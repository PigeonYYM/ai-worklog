"""流水读写与字段清洗。数据根目录可用 WORKLOG_HOME 覆盖（供测试使用）。"""
from __future__ import annotations

import os
import re
import time
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


def take_duration(tool: str, session_id, now: datetime) -> Optional[int]:
    """返回距上一标记点的秒数，并把标记推进到 now。

    标记缺失、时钟回拨、或标记已过期（孤儿）时返回 None——未知必须与 0 区分。
    无论哪种情况标记都会被推进，好让同会话的下一条有可用基线。
    """
    previous = read_mark(tool, session_id)
    set_mark(tool, session_id, now)
    if previous is None:
        return None
    elapsed = now.timestamp() - previous
    if elapsed < 0 or elapsed > MARK_STALE_SECONDS:
        return None
    return int(elapsed)


def cleanup_stale_marks(max_age_seconds: int = MARK_STALE_SECONDS) -> int:
    directory = marks_dir()
    if not directory.exists():
        return 0
    removed = 0
    now = time.time()
    for path in directory.glob("*.mark"):
        try:
            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
