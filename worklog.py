"""流水读写与字段清洗。数据根目录可用 WORKLOG_HOME 覆盖（供测试使用）。"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
TOOLS = ("claude", "codex", "opencode")


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


def append_record(when: datetime, project: str, title: str, tool: str) -> None:
    raw_dir().mkdir(parents=True, exist_ok=True)
    line = "\t".join(["{:%H:%M}".format(when), clean(project), clean(title), tool])
    with raw_path(when).open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_day(day: datetime) -> List[Tuple[str, str, str, str]]:
    path = raw_path(day)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            rows.append((parts[0], parts[1], parts[2], parts[3]))
    return rows
