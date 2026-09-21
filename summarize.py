"""把工作流水整合成日报 / 周报。

模型输出必须通过三条硬性校验才采纳；校验或调用失败则退化为纯脚本分组渲染，
保证流水里的信息一条不丢。所有 md 都是派生产物，每次从流水全量重建。
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import collect_opencode
import worklog

MODEL = "kimi-for-coding/k3"
MODEL_TIMEOUT = 300
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

DAILY_PROMPT = """把下面这份工作流水整合成当天日报。

规则：
- 按项目分组，每个项目一个 `## 项目名` 小标题
- 同一项目里属于同一件事的多条任务压成一条成果描述，阶段明细收进括号
- 不同的事各自独立成条，不要过度归纳
- 不要发明流水里没有的内容，不要补充推测
- 直接输出 markdown 正文，不要前言、不要解释、不要代码块包裹
- 第一行必须是：# {day} {weekday}
- 时长列原样保留，不要加总、不要换算、不要编造——工时汇总由脚本另行生成

流水（列依次为 时间、项目、标题、来源工具、时长）：
{flow}
"""

WEEKLY_PROMPT = """把下面这份一周的工作流水整合成周报。

规则：
- 按项目分组，突出本周产出与进展
- 同一件事跨多天的多条记录合并成一条，体现最终成果而非过程
- 不同的事各自独立成条，不要过度归纳
- 不要发明流水里没有的内容，不要补充推测
- 直接输出 markdown 正文，不要前言、不要解释、不要代码块包裹
- 第一行必须是：# {week} 周报（{start} ~ {end}）
- 时长列原样保留，不要加总、不要换算、不要编造——工时汇总由脚本另行生成

流水（列依次为 日期、时间、项目、标题、来源工具、时长）：
{flow}
"""


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def extract_body(raw: str) -> str:
    lines = strip_ansi(raw).splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped and not stripped.startswith("> build"):
            break
        index += 1
    return "\n".join(lines[index:]).strip()


def validate(body: str, rows) -> bool:
    if not body.strip():
        return False
    projects = set(row[1] for row in rows)
    for project in projects:
        if project not in body:
            return False
    content_lines = [line for line in body.splitlines() if line.strip()]
    return len(content_lines) >= len(projects)


def format_hours(seconds) -> str:
    if seconds is None:
        return "-"
    return "{:.1f}h".format(seconds / 3600.0)


def record_suffix(row) -> str:
    if row[4] is None:
        return row[3]
    return "{}, {}".format(row[3], format_hours(row[4]))


def flow_row(row) -> str:
    return "\t".join([row[0], row[1], row[2], row[3], format_hours(row[4])])


def hours_table(rows) -> str:
    """按项目汇总工时。数字由脚本算，绝不交给模型。"""
    totals = {}
    unknown = 0
    for row in rows:
        if row[4] is None:
            unknown += 1
            continue
        totals[row[1]] = totals.get(row[1], 0) + row[4]
    if not totals:
        return ""
    lines = ["## 工时", "", "| 项目 | 小时 |", "| --- | --- |"]
    for project in sorted(totals, key=lambda name: (-totals[name], name)):
        lines.append("| {} | {:.1f} |".format(project, totals[project] / 3600.0))
    lines.append("| **合计** | **{:.1f}** |".format(sum(totals.values()) / 3600.0))
    if unknown:
        lines.extend(["", "> {} 条记录无时长，未计入。".format(unknown)])
    return "\n".join(lines) + "\n"


def render_fallback(rows, day: datetime) -> str:
    grouped = {}
    for row in rows:
        grouped.setdefault(row[1], []).append(row)
    out = ["# {:%Y-%m-%d} {}".format(day, worklog.WEEKDAYS[day.weekday()]), ""]
    for project in sorted(grouped):
        out.append("## {}".format(project))
        for row in grouped[project]:
            out.append("- {} {} ({})".format(row[0], row[2], record_suffix(row)))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_weekly_fallback(dated_rows, start: datetime, end: datetime) -> str:
    grouped = {}
    for day_text, row in dated_rows:
        grouped.setdefault(row[1], []).append((day_text, row))
    week = "{:%G-W%V}".format(start)
    out = [
        "# {} 周报（{:%Y-%m-%d} ~ {:%Y-%m-%d}）".format(week, start, end),
        "",
    ]
    for project in sorted(grouped):
        out.append("## {}".format(project))
        for day_text, row in grouped[project]:
            out.append("- {} {} ({})".format(day_text, row[2], record_suffix(row)))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def build_daily_prompt(rows, day: datetime) -> str:
    flow = "\n".join(flow_row(row) for row in rows)
    return DAILY_PROMPT.format(
        day="{:%Y-%m-%d}".format(day),
        weekday=worklog.WEEKDAYS[day.weekday()],
        flow=flow,
    )


def build_weekly_prompt(dated_rows, start: datetime, end: datetime) -> str:
    flow = "\n".join(
        "{}\t{}".format(day_text, flow_row(row)) for day_text, row in dated_rows
    )
    return WEEKLY_PROMPT.format(
        week="{:%G-W%V}".format(start),
        start="{:%Y-%m-%d}".format(start),
        end="{:%Y-%m-%d}".format(end),
        flow=flow,
    )


def run_model(prompt: str) -> Optional[str]:
    workdir = worklog.report_dir()
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        binary = collect_opencode.opencode_bin()
    except RuntimeError:
        return None
    # 用临时文件接收 stdout，而不是 capture_output 的管道：
    # 实测同一条 opencode 查询，capture_output=True 管道捕获得到 63352 字节，
    # 重定向到普通文件得到 79642 字节，两次 returncode 都是 0——
    # 管道捕获会静默丢数据且不报错。日报流水少风险不高，周报聚合整周、
    # 输出更长，截断后果是静默写入一份残缺报告，用户不会发现。
    fd, tmp_name = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with tmp_path.open("wb") as out_handle:
            try:
                result = subprocess.run(
                    [binary, "run", "--pure", "-m", MODEL, prompt],
                    cwd=str(workdir),
                    stdout=out_handle,
                    stderr=subprocess.PIPE,
                    timeout=MODEL_TIMEOUT,
                    stdin=subprocess.DEVNULL,
                )
            except (subprocess.TimeoutExpired, OSError):
                return None
        if result.returncode != 0:
            return None
        try:
            stdout_text = tmp_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return extract_body(stdout_text)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


@contextmanager
def summarize_lock():
    worklog.state_dir().mkdir(parents=True, exist_ok=True)
    lock_file = worklog.state_dir() / "summarize.lock"
    with lock_file.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def pull_opencode() -> None:
    if os.environ.get("WORKLOG_SKIP_COLLECT"):
        return
    try:
        collect_opencode.main()
    except Exception as exc:  # 采集失败不该阻断整合
        print("opencode 采集跳过: {}".format(exc), file=sys.stderr)


def daily(day: datetime) -> int:
    with summarize_lock():
        pull_opencode()
        rows = worklog.read_day(day)
        if not rows:
            print("{:%Y-%m-%d} 无记录，跳过".format(day))
            return 0
        body = run_model(build_daily_prompt(rows, day))
        if body and validate(body, rows):
            content = body.rstrip() + "\n"
            source = "模型"
        else:
            content = render_fallback(rows, day)
            source = "退化渲染"
        target = worklog.report_dir() / "{:%Y-%m-%d}.md".format(day)
        table = hours_table(rows)
        if table:
            content = content.rstrip() + "\n\n" + table
        atomic_write(target, content)
        print("日报已写入 {}（{}，{} 条流水）".format(target, source, len(rows)))
        return 0


def week_range(day: datetime) -> Tuple[datetime, datetime]:
    start = day - timedelta(days=day.weekday())
    return start, day


def weekly(day: datetime) -> int:
    with summarize_lock():
        pull_opencode()
        start, end = week_range(day)
        dated_rows: List[Tuple[str, tuple]] = []
        cursor = start
        while cursor <= end:
            for row in worklog.read_day(cursor):
                dated_rows.append(("{:%m-%d}".format(cursor), row))
            cursor += timedelta(days=1)
        if not dated_rows:
            print("本周无记录，跳过")
            return 0
        rows = [row for _, row in dated_rows]
        body = run_model(build_weekly_prompt(dated_rows, start, end))
        if body and validate(body, rows):
            content = body.rstrip() + "\n"
            source = "模型"
        else:
            content = render_weekly_fallback(dated_rows, start, end)
            source = "退化渲染"
        target = worklog.report_dir() / "weekly" / "{:%G-W%V}.md".format(start)
        table = hours_table(rows)
        if table:
            content = content.rstrip() + "\n\n" + table
        atomic_write(target, content)
        print("周报已写入 {}（{}，{} 条流水）".format(target, source, len(rows)))
        return 0


def hours_report(start: datetime, end: datetime) -> int:
    cursor = start
    found = False
    while cursor <= end:
        rows = worklog.read_day(cursor)
        table = hours_table(rows)
        if table:
            found = True
            print("## {:%Y-%m-%d} {}".format(cursor, worklog.WEEKDAYS[cursor.weekday()]))
            print(table)
        cursor += timedelta(days=1)
    if not found:
        print("区间内无记录")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="整合工作流水为日报或周报")
    parser.add_argument("--daily", action="store_true", help="生成当天日报")
    parser.add_argument("--weekly", action="store_true", help="生成本周周报")
    parser.add_argument("--date", help="指定日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--hours", action="store_true", help="输出工时汇总，不生成 md")
    parser.add_argument("--since", help="工时汇总起始日期 YYYY-MM-DD，默认与 --date 同天")
    args = parser.parse_args()

    day = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    if args.hours:
        start = datetime.strptime(args.since, "%Y-%m-%d") if args.since else day
        return hours_report(start, day)
    if args.weekly:
        return weekly(day)
    return daily(day)


if __name__ == "__main__":
    sys.exit(main())
