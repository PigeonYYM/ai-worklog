#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook：给会话打起点标记，供 TaskCompleted 算时长。

只写一个时间戳文件——它在每次回车时执行，必须足够轻。
"""
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
    worklog.set_mark("claude", payload.get("session_id"), datetime.now())
    worklog.cleanup_stale_marks()


if __name__ == "__main__":
    main()
