"""codex Stop hook：取出缓存的用户输入作为标题，写入工作流水。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_prompt
import worklog

STALE_SECONDS = 86400


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return

    cache = codex_prompt.cache_path(payload.get("session_id"))
    if not cache.exists():
        codex_prompt.cleanup_stale(STALE_SECONDS)
        return

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
            datetime.now(),
            worklog.project_name(payload.get("cwd")),
            title,
            "codex",
        )
    codex_prompt.cleanup_stale(STALE_SECONDS)


if __name__ == "__main__":
    main()
