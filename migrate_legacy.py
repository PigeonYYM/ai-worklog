"""一次性迁移：把旧格式 md（hook 直接追加）里的记录转成 TSV 流水。

幂等保护：目标 TSV 已存在且非空时拒绝执行，避免重复写入。
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import worklog

RECORD = re.compile(r"^-\s+(\d{2}:\d{2})\s+\[([^\]]+)\]\s+(.+)$")


def parse_legacy(md_text: str) -> List[Tuple[str, str, str]]:
    rows = []
    for line in md_text.splitlines():
        match = RECORD.match(line.strip())
        if match:
            rows.append((match.group(1), match.group(2), match.group(3).strip()))
    return rows


def migrate(md_path: Path) -> int:
    day = datetime.strptime(md_path.stem, "%Y-%m-%d")
    target = worklog.raw_path(day)
    if target.exists() and target.stat().st_size > 0:
        print("拒绝执行：{} 已存在且非空".format(target))
        return -1

    rows = parse_legacy(md_path.read_text(encoding="utf-8"))
    for time_text, project, title in rows:
        hour, minute = time_text.split(":")
        when = day.replace(hour=int(hour), minute=int(minute))
        worklog.append_record(when, project, title, "claude")
    return len(rows)


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: migrate_legacy.py <YYYY-MM-DD.md 路径>")
        return 2
    md_path = Path(sys.argv[1])
    if not md_path.exists():
        print("文件不存在: {}".format(md_path))
        return 2
    count = migrate(md_path)
    if count < 0:
        return 1
    print("已迁移 {} 条到 {}".format(count, worklog.raw_path(datetime.strptime(md_path.stem, "%Y-%m-%d"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
