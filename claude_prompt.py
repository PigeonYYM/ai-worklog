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
    try:
        worklog.set_mark("claude", payload.get("session_id"), datetime.now())
        worklog.cleanup_stale_marks()
    except OSError:
        # 这个 hook 在每次回车时跑，绝不能因为文件系统故障而崩掉编辑器。
        # 标记写失败最坏后果是这一条时长变成未知，未知本来就有合法含义。
        return


if __name__ == "__main__":
    main()
