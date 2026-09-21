"""流水读写与字段清洗。数据根目录可用 WORKLOG_HOME 覆盖（供测试使用）。"""
from __future__ import annotations

import fcntl
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
TOOLS = ("claude", "codex", "opencode")
UNSAFE_TOKEN = re.compile(r"[^A-Za-z0-9_-]")
MARK_STALE_SECONDS = 86400


def report_dir() -> Path:
    override = os.environ.get("WORKLOG_HOME")
    if override:
        return Path(override)
    return Path.home() / "daily-report"


def raw_dir() -> Path:
    return report_dir() / ".raw"


def state_dir() -> Path:
    return report_dir() / ".state"


def clean(text) -> str:
    return " ".join(str(text).split())


def project_name(cwd) -> str:
    return Path(cwd or ".").name or "-"


def raw_path(day: datetime) -> Path:
    return raw_dir() / "{:%Y-%m-%d}.tsv".format(day)


def parse_duration(text) -> Optional[int]:
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def append_record(
    when: datetime,
    project: str,
    title: str,
    tool: str,
    duration_seconds: Optional[int] = None,
) -> None:
    raw_dir().mkdir(parents=True, exist_ok=True)
    line = "\t".join([
        "{:%H:%M}".format(when),
        clean(project),
        clean(title),
        tool,
        "" if duration_seconds is None else str(int(duration_seconds)),
    ])
    with raw_path(when).open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_day(day: datetime) -> List[Tuple[str, str, str, str, Optional[int]]]:
    path = raw_path(day)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        # 4 列是本功能上线前的历史流水，时长按未知处理，不回填。
        if len(parts) == 4:
            rows.append((parts[0], parts[1], parts[2], parts[3], None))
        elif len(parts) == 5:
            rows.append(
                (parts[0], parts[1], parts[2], parts[3], parse_duration(parts[4]))
            )
    return rows


def safe_token(raw) -> str:
    cleaned = UNSAFE_TOKEN.sub("", str(raw or ""))
    return cleaned or "unknown"


def marks_dir() -> Path:
    return state_dir() / "marks"


def mark_path(tool: str, session_id) -> Path:
    return marks_dir() / "{}-{}.mark".format(safe_token(tool), safe_token(session_id))


def set_mark(tool: str, session_id, when: datetime) -> None:
    marks_dir().mkdir(parents=True, exist_ok=True)
    mark_path(tool, session_id).write_text(repr(when.timestamp()), encoding="utf-8")


def read_mark(tool: str, session_id) -> Optional[float]:
    try:
        return float(mark_path(tool, session_id).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


@contextmanager
def _mark_lock(tool: str, session_id):
    """对单个标记文件的读-改-写加互斥锁，避免并行 hook 竞态读到同一基线。

    Claude Code 并行跑多个 Task 子代理时，同一批完成的任务会在几十毫秒内
    各自起一个 claude_task.py 进程；不加锁的话两边都可能在对方写入前读到
    同一个 previous，两条记录都测出同一段完整区间，日总时长超过实际流逝
    的墙钟时间。锁文件是标记文件的兄弟文件，与 summarize.py 的
    summarize_lock 用同一套 fcntl.flock 模式。
    """
    marks_dir().mkdir(parents=True, exist_ok=True)
    lock_path = mark_path(tool, session_id).with_suffix(".lock")
    with lock_path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def take_duration(tool: str, session_id, now: datetime) -> Optional[int]:
    """返回距上一标记点的秒数，并把标记推进到 now。

    会话 id 缺失/空白：不可测量，直接返回 None，不碰任何标记文件。

    标记缺失（这是第一条记录，或起点 hook 从未跑过）：返回 None，且**不创建**
    标记——否则第一条会凭空产生一个基线，把它之后的记录错误地拴到上面，
    跨过用户离开电脑的时间。宁可这次未知，也不要编一个假基线。

    标记存在但已过期（孤儿，超过 MARK_STALE_SECONDS）：返回 None，并把标记
    推进到 now，好让同会话的下一条有可用基线。

    标记存在且新鲜：返回距上一标记点的秒数，并把标记推进到 now。

    读-改-写在同一把每标记文件的排他锁内完成，防止并行 hook 竞态读到
    同一个 previous（见 `_mark_lock`）。
    """
    if not session_id:
        return None
    with _mark_lock(tool, session_id):
        previous = read_mark(tool, session_id)
        if previous is None:
            return None
        set_mark(tool, session_id, now)
        elapsed = now.timestamp() - previous
    if elapsed < 0 or elapsed > MARK_STALE_SECONDS:
        return None
    return int(elapsed)


def cleanup_stale_marks(max_age_seconds: int = MARK_STALE_SECONDS) -> int:
    """清理过期的 .mark 与 .lock 文件。

    .lock 文件由 `_mark_lock` 为每个曾经调用过 `take_duration` 的
    (tool, session_id) 创建，即便当时没有标记可读（例如 Fix1 之后，
    hook 未注册期间每次调用都是这种情况）。不清理的话这些空锁文件会
    随会话数量无限堆积。
    """
    directory = marks_dir()
    if not directory.exists():
        return 0
    removed = 0
    now = time.time()
    for pattern in ("*.mark", "*.lock"):
        for path in directory.glob(pattern):
            try:
                if now - path.stat().st_mtime > max_age_seconds:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    return removed
