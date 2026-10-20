"""把工作流水整合成日报 / 周报。

模型输出必须通过三条硬性校验才采纳；校验或调用失败则退化为纯脚本分组渲染，
保证流水里的信息一条不丢。所有 md 都是派生产物，每次从流水全量重建。
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import collect_opencode
import tapd
import worklog

# 整合模型由 WORKLOG_MODEL 环境变量指定；未设置时不传 -m，用 opencode 配置的默认模型。
MODEL = os.environ.get("WORKLOG_MODEL") or None
MODEL_TIMEOUT = 300
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

TAPD_INSTRUCTIONS = """
TAPD 需求关联（候选清单在下方，列为 ID 和标题）：
- 正文里每条成果条目，仅当与某条需求存在强关联（渠道、功能、游戏名等关键词明确对应）时，在条目末尾追加 `（关联：<ID>）`，ID 必须逐字来自候选清单
- 拿不准关联哪条（零条或多条都可能）时，不要追加标注，而是在整个文档末尾输出独立一行：`<!-- TAPD_PENDING: <该条目原文> | <候选ID1>,<候选ID2> -->`
- 既无强关联也无可疑候选的条目，什么都不加
- 关联只标在成果条目后，不要写进工时表，不要单独成节
"""

TAPD_CONFIRMED_HEADER = "以下条目的关联已由用户确认，若对应事项出现在本次正文里，请逐字保留 `（关联：<ID>）` 标注："
TAPD_REJECTED_HEADER = "以下条目已由用户确认不关联任何需求，不要标注，也不要列入 TAPD_PENDING："

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
    """按项目汇总工时。数字由脚本算，绝不交给模型。

    没有任何一行的情况下返回空字符串——这才是真正的「无记录」。
    有记录但全部时长未知时，仍要输出未知条数提示（不带表格），否则一天有
    30 条记录、全部时长未知，会跟没有任何记录长得一模一样，掩盖了正是
    最该被看见的过渡期/hook 未注册场景。
    """
    if not rows:
        return ""
    totals = {}
    unknown = 0
    for row in rows:
        if row[4] is None:
            unknown += 1
            continue
        totals[row[1]] = totals.get(row[1], 0) + row[4]
    if not totals:
        return "> {} 条记录无时长，未计入。\n".format(unknown)
    lines = ["## 工时", "", "| 项目 | 小时 |", "| --- | --- |"]
    rounded_values = []
    for project in sorted(totals, key=lambda name: (-totals[name], name)):
        rounded_hours = round(totals[project] / 3600.0, 1)
        lines.append("| {} | {:.1f} |".format(project, rounded_hours))
        rounded_values.append(rounded_hours)
    grand_total = sum(rounded_values)
    lines.append("| **合计** | **{:.1f}** |".format(grand_total))
    if unknown:
        lines.extend(["", "> {} 条记录无时长，未计入。".format(unknown)])
    return "\n".join(lines) + "\n"


def _draft_annotator():
    """毛坯渲染的关联标注器；功能关闭或无候选时返回 None（零开销）。

    猜测每次重建都重新推导、不固化——改关联随时通过 --link 或直接编辑
    .state/tapd_links.json，下一次渲染即生效。
    """
    if not tapd_enabled():
        return None
    tasks = tapd.load_candidates()
    if not tasks:
        return None
    links = tapd.read_links()
    return lambda title: tapd.draft_annotation(title, tasks, links)


def render_fallback(rows, day: datetime) -> str:
    annotate = _draft_annotator()
    grouped = {}
    for row in rows:
        grouped.setdefault(row[1], []).append(row)
    out = ["# {:%Y-%m-%d} {}".format(day, worklog.WEEKDAYS[day.weekday()]), ""]
    for project in sorted(grouped):
        out.append("## {}".format(project))
        for row in grouped[project]:
            suffix = annotate(row[2]) if annotate else ""
            out.append("- {} {} ({}){}".format(row[0], row[2], record_suffix(row), suffix))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_weekly_fallback(dated_rows, start: datetime, end: datetime) -> str:
    annotate = _draft_annotator()
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
            suffix = annotate(row[2]) if annotate else ""
            out.append("- {} {} ({}){}".format(day_text, row[2], record_suffix(row), suffix))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def build_daily_prompt(rows, day: datetime) -> str:
    flow = "\n".join(flow_row(row) for row in rows)
    return DAILY_PROMPT.format(
        day="{:%Y-%m-%d}".format(day),
        weekday=worklog.WEEKDAYS[day.weekday()],
        flow=flow,
    ) + tapd_block()


def build_weekly_prompt(dated_rows, start: datetime, end: datetime) -> str:
    flow = "\n".join(
        "{}\t{}".format(day_text, flow_row(row)) for day_text, row in dated_rows
    )
    return WEEKLY_PROMPT.format(
        week="{:%G-W%V}".format(start),
        start="{:%Y-%m-%d}".format(start),
        end="{:%Y-%m-%d}".format(end),
        flow=flow,
    ) + tapd_block()


def tapd_enabled() -> bool:
    """install.py 可选择关闭关联功能（往定时任务环境写 WORKLOG_TAPD=off）。"""
    return os.environ.get("WORKLOG_TAPD") != "off"


def tapd_block() -> str:
    """拼进 prompt 的 TAPD 段：候选清单 + 关联规则 + 用户已确认/已否决的结论。

    清单文件缺失或功能被关闭时返回空串，整个关联功能静默关闭。
    """
    if not tapd_enabled():
        return ""
    tasks = tapd.load_candidates()
    if not tasks:
        return ""
    lines = ["", "TAPD 需求候选清单："]
    lines.extend("{}\t{}".format(task["id"], task["title"]) for task in tasks)
    lines.append(TAPD_INSTRUCTIONS)
    links = tapd.read_links()
    confirmed = [(item, tapd_id) for item, tapd_id in links.items() if tapd_id]
    rejected = [item for item, tapd_id in links.items() if not tapd_id]
    if confirmed:
        lines.append(TAPD_CONFIRMED_HEADER)
        lines.extend("- {} → {}".format(item, tapd_id) for item, tapd_id in confirmed)
        lines.append("")
    if rejected:
        lines.append(TAPD_REJECTED_HEADER)
        lines.extend("- {}".format(item) for item in rejected)
        lines.append("")
    return "\n".join(lines)


def apply_tapd(body: str, scope: str) -> str:
    """模型输出的 TAPD 后处理：收待确认、校验展开标注、追加待确认脚注。

    只在模型路径调用；退化渲染不做关联也不动队列——降级模式保持纯脚本。
    """
    if not tapd_enabled():
        return body
    tasks = tapd.load_candidates()
    if not tasks:
        return body
    body, found = tapd.extract_pending(body)
    # 已有结论（确认关联或确认无关联）的条目不进队列——模型不保证每次都记得
    # 注入的指令，这一道是确定性的兜底，保证 `--link` 的结论是终局。
    decided = set(tapd.read_links())
    found = [entry for entry in found if entry.get("item") not in decided]
    if found:
        tapd.add_pending(scope, found)
    body = tapd.expand_annotations(body, tasks)
    pending_count = len(tapd.read_pending().get(scope, []))
    if pending_count:
        link_cmd = "/usr/bin/python3 {} --link".format(Path(__file__).resolve())
        body = body.rstrip() + (
            "\n\n> {} 条需求关联待确认，运行 `{}` 确认\n".format(pending_count, link_cmd)
        )
    return body


def link_mode() -> int:
    """交互确认待关联队列：选编号关联 / 0 无关联 / 回车跳过，然后重建受影响的报告。"""
    pending = tapd.read_pending()
    if not pending:
        print("没有待确认的需求关联")
        return 0
    tasks = {task["id"]: task for task in tapd.load_candidates()}
    links = tapd.read_links()
    affected = []
    for scope in sorted(pending):
        remaining = []
        for entry in pending[scope]:
            item = entry.get("item", "")
            candidates = [c for c in entry.get("candidates", []) if c in tasks]
            print("\n[{}] {}".format(scope, item))
            for index, cid in enumerate(candidates, 1):
                print("  {}. {} {}".format(index, cid, tasks[cid]["title"]))
            choice = input("选择编号关联，0 = 无关联，回车 = 跳过: ").strip()
            if not choice:
                remaining.append(entry)
            elif choice == "0":
                links[item] = ""
            elif choice.isdigit() and 1 <= int(choice) <= len(candidates):
                links[item] = candidates[int(choice) - 1]
            else:
                print("无效输入，跳过")
                remaining.append(entry)
        affected.append(scope)
        if remaining:
            pending[scope] = remaining
        else:
            pending.pop(scope)
    tapd.write_links(links)
    tapd.write_pending(pending)
    for scope in affected:
        rebuild_scope(scope)
    return 0


def rebuild_scope(scope: str) -> None:
    """按 scope 重建对应报告，让刚确认的关联立刻体现在 md 里。"""
    try:
        kind, _, name = scope.partition(" ")
        if kind == "daily":
            daily(datetime.strptime(name, "%Y-%m-%d"))
        elif kind == "weekly":
            monday = datetime.strptime(name + "-1", "%G-W%V-%u")
            weekly(monday)
    except ValueError:
        print("无法识别的 scope：{}，跳过重建".format(scope), file=sys.stderr)


# 各模型 CLI 的无头调用方式（prompt 一律作为最后一个参数追加）。
# 新增后端只需在这里加一行并在 install.py 的 RUNNER_NAMES 里登记。
RUNNER_SPECS = {
    "opencode": lambda b, m: [b, "run", "--pure"] + (["-m", m] if m else []),
    "claude": lambda b, m: [b, "-p"] + (["--model", m] if m else []),
    "codex": lambda b, m: [b, "exec", "-s", "read-only"] + (["-m", m] if m else []),
    "pi": lambda b, m: [b, "-p"] + (["--model", m] if m else []),
    "gemini": lambda b, m: [b, "-p"] + (["-m", m] if m else []),
}
# 自动模式下的优先顺序
RUNNER_PRIORITY = ("opencode", "claude", "codex", "pi", "gemini")


def _runner_binary(name: str):
    """各后端的可执行文件；WORKLOG_<NAME> 环境变量可覆盖（测试与特殊安装用）。"""
    if name == "opencode":
        try:
            return collect_opencode.opencode_bin()
        except RuntimeError:
            return None
    return os.environ.get("WORKLOG_" + name.upper()) or shutil.which(name)


def _backend_command(name: str):
    binary = _runner_binary(name)
    if not binary:
        return None
    return RUNNER_SPECS[name](binary, MODEL)


def model_runner():
    """选一个可用的模型执行后端，返回不含 prompt 的 argv 前缀；都不可用返回 None。

    WORKLOG_RUNNER 可强制指定后端（安装时用户的选择会写入定时任务环境）；
    未指定时按 RUNNER_PRIORITY 顺序取第一个本机可用的。
    """
    forced = os.environ.get("WORKLOG_RUNNER")
    if forced in RUNNER_SPECS:
        command = _backend_command(forced)
        if command:
            return command
    for name in RUNNER_PRIORITY:
        if name == forced:
            continue
        command = _backend_command(name)
        if command:
            return command
    return None


def run_model(prompt: str) -> Optional[str]:
    command = model_runner()
    if command is None:
        return None
    command = command + [prompt]
    workdir = worklog.report_dir()
    workdir.mkdir(parents=True, exist_ok=True)
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
                    command,
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


def daily(day: datetime, no_model: bool = False) -> int:
    with summarize_lock():
        pull_opencode()
        rows = worklog.read_day(day)
        if not rows:
            print("{:%Y-%m-%d} 无记录，跳过".format(day))
            return 0
        body = None if no_model else run_model(build_daily_prompt(rows, day))
        if body and validate(body, rows):
            body = apply_tapd(body, "daily {:%Y-%m-%d}".format(day))
            content = body.rstrip() + "\n"
            source = "模型"
        else:
            content = render_fallback(rows, day)
            source = "脚本渲染" if no_model else "退化渲染"
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
            body = apply_tapd(body, "weekly {:%G-W%V}".format(start))
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


def log_record(title: str, minutes, project) -> int:
    """手动补录一条流水：agent 之外的手工工作（手动部署、手动填报等）。"""
    title = worklog.clean(title)
    if not title:
        print("标题为空，未写入", file=sys.stderr)
        return 1
    duration = minutes * 60 if minutes is not None else None
    project = worklog.clean(project) if project else worklog.project_name(os.getcwd())
    now = datetime.now()
    worklog.append_record(now, project, title, "manual", duration)
    print("已补录到 {}：{}（{}）".format(worklog.raw_path(now), title, project))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="整合工作流水为日报或周报")
    parser.add_argument("--daily", action="store_true", help="生成当天日报")
    parser.add_argument("--weekly", action="store_true", help="生成本周周报")
    parser.add_argument("--date", help="指定日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--hours", action="store_true", help="输出工时汇总，不生成 md")
    parser.add_argument("--since", help="工时汇总起始日期 YYYY-MM-DD，默认与 --date 同天")
    parser.add_argument("--link", action="store_true", help="交互确认待关联的 TAPD 需求，并重建受影响的报告")
    parser.add_argument("--log", help="手动补录一条流水（agent 之外的手工工作），值为标题")
    parser.add_argument("--minutes", type=int, help="补录条目的时长（分钟），默认未知")
    parser.add_argument("--project", help="补录条目的项目名，默认当前目录名")
    parser.add_argument("--no-model", action="store_true",
                        help="跳过模型直接用脚本分组渲染（白天高频毛坯更新用，零模型成本）")
    args = parser.parse_args()

    if args.log is not None:
        return log_record(args.log, args.minutes, args.project)
    if args.link:
        return link_mode()
    day = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    if args.hours:
        start = datetime.strptime(args.since, "%Y-%m-%d") if args.since else day
        return hours_report(start, day)
    if args.weekly:
        return weekly(day)
    return daily(day, no_model=args.no_model)


if __name__ == "__main__":
    sys.exit(main())
