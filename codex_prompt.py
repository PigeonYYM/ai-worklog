"""codex UserPromptSubmit hook：把用户输入按 session_id 缓存，供 Stop 取用作标题。

Stop 事件的 payload 不含用户输入，只有 last_assistant_message，
所以「我让它干了什么」必须在这一步抓下来。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import worklog

PREFIX = "worklog-codex-"


def cache_dir() -> Path:
    override = os.environ.get("WORKLOG_CACHE")
    return Path(override) if override else Path(tempfile.gettempdir())


def safe_session_id(raw) -> str:
    return worklog.safe_token(raw)


def cache_path(session_id) -> Path:
    return cache_dir() / "{}{}.txt".format(PREFIX, safe_session_id(session_id))


def cleanup_stale(max_age_seconds: int) -> int:
    removed = 0
    now = time.time()
    for path in cache_dir().glob("{}*.txt".format(PREFIX)):
        try:
            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return
    target = cache_path(payload.get("session_id"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(prompt, encoding="utf-8")


if __name__ == "__main__":
    main()
