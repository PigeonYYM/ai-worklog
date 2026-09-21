"""codex Stop hook：取出缓存的用户输入作为标题，连同 turn 耗时写入工作流水。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_prompt
import worklog

STALE_SECONDS = 86400


def cache_age_seconds(cache: Path, now: datetime):
    """缓存文件的 mtime 就是本 turn 的起点——它在 UserPromptSubmit 时被写入。"""
    try:
        started = cache.stat().st_mtime
    except OSError:
        return None
    elapsed = now.timestamp() - started
    if elapsed < 0 or elapsed > STALE_SECONDS:
        return None
    return int(elapsed)


def main_with_payload(payload: dict) -> None:
    cache = codex_prompt.cache_path(payload.get("session_id"))
    if not cache.exists():
        codex_prompt.cleanup_stale(STALE_SECONDS)
        return

    now = datetime.now()
    duration = cache_age_seconds(cache, now)

    try:
        title = worklog.clean(cache.read_text(encoding="utf-8"))
    except OSError:
        return
    finally:
        try:
            cache.unlink()
        except OSError:
            pass

    if title:
        worklog.append_record(
            now,
            worklog.project_name(payload.get("cwd")),
            title,
            "codex",
            duration,
        )
    codex_prompt.cleanup_stale(STALE_SECONDS)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return
    main_with_payload(payload)


if __name__ == "__main__":
    main()
