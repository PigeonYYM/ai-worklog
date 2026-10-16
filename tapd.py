"""TAPD 需求清单解析与关联状态存储。

tapd_task/tapd_my_tasks.md 由外部同步生成，只取「进行中」「待处理」两组作为
关联候选——已上线/已取消的需求不该再出现在日报里。

关联决策的持久化（md 是纯派生产物，决策不能放 md 里）：
- .state/tapd_links.json   用户已确认的结论，{条目文本: id}，id 为空字符串
  表示用户确认过「无关联」——重建时注入 prompt，保证确认结果不丢、已否决项
  不会重复进待确认队列。
- .state/tapd_pending.json 模型拿不准、等用户确认的候选，
  {scope: [{"item": 条目文本, "candidates": [id...]}]}，scope 形如
  "daily 2026-10-15" / "weekly 2026-W42"。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

import worklog

ACTIVE_GROUPS = ("进行中", "待处理")
HEADING = re.compile(r"^##\s+(.+?)\s*$")
ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*\[(\d+)\]\([^)]*\)\s*\|\s*([^|]+?)\s*\|")
ANNOTATION = re.compile(r"（关联：(\d+)）")
PENDING_COMMENT = re.compile(r"<!--\s*TAPD_PENDING:\s*(.+?)\s*\|\s*([0-9,\s]*)\s*-->")


def tasks_path() -> Path:
    return worklog.report_dir() / "tapd_task" / "tapd_my_tasks.md"


def load_candidates() -> List[dict]:
    """解析活跃分组的表格行。文件缺失/不可读返回空列表——整个关联功能静默关闭。"""
    try:
        text = tasks_path().read_text(encoding="utf-8")
    except OSError:
        return []
    tasks = []
    active = False
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            active = heading.group(1).startswith(ACTIVE_GROUPS)
            continue
        if not active:
            continue
        row = ROW.match(line)
        if row:
            tasks.append({"kind": row.group(1), "id": row.group(2), "title": row.group(3)})
    return tasks


def pending_path() -> Path:
    return worklog.state_dir() / "tapd_pending.json"


def links_path() -> Path:
    return worklog.state_dir() / "tapd_links.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def read_pending() -> Dict[str, list]:
    return _read_json(pending_path())


def write_pending(pending: Dict[str, list]) -> None:
    _write_json(pending_path(), pending)


def add_pending(scope: str, items: List[dict]) -> None:
    """并入待确认队列，按 (scope, item) 去重——每晚重建不该让队列无限膨胀。"""
    pending = read_pending()
    bucket = pending.setdefault(scope, [])
    known = {entry.get("item") for entry in bucket}
    for item in items:
        if item.get("item") and item["item"] not in known:
            bucket.append({"item": item["item"], "candidates": item.get("candidates", [])})
            known.add(item["item"])
    if not bucket:
        pending.pop(scope, None)
    write_pending(pending)


def read_links() -> Dict[str, str]:
    return _read_json(links_path())


def write_links(links: Dict[str, str]) -> None:
    _write_json(links_path(), links)


def extract_pending(body: str) -> Tuple[str, List[dict]]:
    """从模型输出里提取 TAPD_PENDING 注释行并删除，返回 (清理后正文, 待确认项)。"""
    found = []

    def repl(match) -> str:
        item = worklog.clean(match.group(1))
        candidates = [c for c in re.split(r"[,\s]+", match.group(2)) if c.isdigit()]
        if item:
            found.append({"item": item, "candidates": candidates})
        return ""

    cleaned = PENDING_COMMENT.sub(repl, body)
    return cleaned, found


def expand_annotations(body: str, tasks: List[dict]) -> str:
    """把模型写的 `（关联：<id>）` 校验后展开成带标题的形式。

    id 必须在候选集里，否则剥掉标注——模型可能幻觉出不存在的 id，
    也可能关联到候选范围外（已完成/已取消）的需求，两种情况都不该进日报。
    """
    titles = {task["id"]: task["title"] for task in tasks}

    def repl(match) -> str:
        tapd_id = match.group(1)
        title = titles.get(tapd_id)
        if title is None:
            return ""
        return "（关联需求：{} [{}]）".format(title, tapd_id)

    return ANNOTATION.sub(repl, body)
