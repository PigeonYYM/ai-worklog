#!/usr/bin/env python3
"""Claude Code SessionEnd hook：给被中断的最后一个 turn 补一条流水。

Stop hook 只在 agent 正常答完时触发；用户 Esc 中断接管（比如最后一步需要
手动操作）或会话异常退出时，最后一个 turn 的 prompt 缓存不会被 Stop 消费。
会话结束时缓存还在 = 最后一个 turn 漏记了，在这里补上。

时长取标记链余量：从 prompt 提交到会话结束，包含用户接管后的手动操作时间——
这正是想要的口径；超过 24 小时按未知处理（take_duration 的既有语义）。
正常结束的会话（Stop 已消费缓存）不会重复记录。
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


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return
    main_with_payload(payload)


if __name__ == "__main__":
    main()
