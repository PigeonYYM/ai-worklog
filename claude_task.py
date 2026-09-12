#!/usr/bin/env python3
"""Claude Code TaskCompleted hook：把完成的任务写入工作流水。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import worklog


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return

    subject = worklog.clean(payload.get("task_subject") or "")
    if not subject:
        return

    worklog.append_record(
        datetime.now(),
        worklog.project_name(payload.get("cwd")),
        subject,
        "claude",
    )


main()
