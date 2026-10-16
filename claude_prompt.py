#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook：打时长起点标记，并缓存用户输入供 Stop 取用作标题。

Stop 事件的 payload 不含用户输入，「我让它干了什么」必须在这一步抓下来——
这一点和 codex 的 UserPromptSubmit/Stop 配对完全一致，缓存也复用同一套实现。

这个 hook 在每次回车时执行，必须足够轻，且绝不能因为文件系统故障崩掉编辑器。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_prompt
import worklog

PREFIX = "worklog-claude-"
STALE_SECONDS = 86400


def cache_path(session_id) -> Path:
    return codex_prompt.cache_path(session_id, PREFIX)


def cleanup_stale(max_age_seconds: int = STALE_SECONDS) -> int:
    return codex_prompt.cleanup_stale(max_age_seconds, PREFIX)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return
    try:
        worklog.set_mark("claude", payload.get("session_id"), datetime.now())
        prompt = str(payload.get("prompt") or "").strip()
        if prompt:
            target = cache_path(payload.get("session_id"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(prompt, encoding="utf-8")
        worklog.cleanup_stale_marks()
        cleanup_stale()
    except OSError:
        # 标记/缓存写失败的最坏后果是这一条时长变成未知或缺一条 turn 记录，
        # 都远轻于把编辑器搞崩。
        return


if __name__ == "__main__":
    main()
