#!/usr/bin/env python3
"""Claude Code Stop hook：取出缓存的用户输入作为标题，连同 turn 耗时写入工作流水。

与 claude_task.py（TaskCompleted）共用同一条会话标记链：turn 内完成的清单项
各自分段计时，Stop 拿走剩余尾段——同一个 turn 里所有记录的时长之和等于该
turn 的真实耗时，不重不漏。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import claude_prompt
import worklog


def main_with_payload(payload: dict) -> None:
    cache = claude_prompt.cache_path(payload.get("session_id"))
    if not cache.exists():
        claude_prompt.cleanup_stale()
        return

    now = datetime.now()
    try:
        title = worklog.clean(cache.read_text(encoding="utf-8"))
    except OSError:
        return
    finally:
        try:
            cache.unlink()
        except OSError:
            pass
    if not title:
        return

    # 确定要记录了再消耗标记——标题取不到就推进标记会把这个 turn 的时长
    # 从流水里静默抹掉。
    try:
        duration = worklog.take_duration("claude", payload.get("session_id"), now)
    except OSError:
        duration = None
    worklog.append_record(
        now,
        worklog.project_name(payload.get("cwd")),
        title,
        "claude",
        duration,
    )
    claude_prompt.cleanup_stale()


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return
    main_with_payload(payload)


if __name__ == "__main__":
    main()
