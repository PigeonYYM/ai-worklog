"""从 opencode 的 SQLite 增量拉取会话，写入工作流水。

不使用 opencode plugin：session.idle 每回合触发、payload 只有 sessionID、
同一会话会重复写同一标题；而 session 表天然按会话去重且 title 是最终版。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List

import worklog

CURSOR_NAME = "opencode.cursor"
QUERY_TIMEOUT = 60


def opencode_bin() -> str:
    override = os.environ.get("WORKLOG_OPENCODE")
    if override:
        return override
    for candidate in (
        "/opt/homebrew/bin/opencode",
        "/usr/local/bin/opencode",
        str(Path.home() / ".opencode" / "bin" / "opencode"),
    ):
        if os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("opencode")
    if found:
        return found
    raise RuntimeError("找不到 opencode 可执行文件")


def cursor_path() -> Path:
    return worklog.state_dir() / CURSOR_NAME


def read_cursor() -> int:
    path = cursor_path()
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return 0


def write_cursor(value: int) -> None:
    worklog.state_dir().mkdir(parents=True, exist_ok=True)
    cursor_path().write_text(str(int(value)), encoding="utf-8")


def fetch_sessions(since_ms: int) -> List[dict]:
    query = (
        "SELECT id, directory, title, time_updated FROM session "
        "WHERE time_updated > {} ORDER BY time_updated".format(int(since_ms))
    )
    # 用临时文件接收 stdout，而不是 capture_output 的管道：
    # 实测 opencode（Bun 打包的二进制）在写大量 JSON 到管道后立即退出，
    # 会在管道缓冲区超过 64KB 时截断输出（真实数据库几百个会话时必现）。
    # 重定向到文件规避了这个管道竞态。
    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".json", delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with tmp_path.open("wb") as out_handle:
            result = subprocess.run(
                [opencode_bin(), "db", "--format", "json", query],
                stdout=out_handle,
                stderr=subprocess.PIPE,
                timeout=QUERY_TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
        if result.returncode != 0:
            return []
        try:
            payload = json.loads(tmp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            return []
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    if isinstance(payload, dict):
        payload = payload.get("rows") or payload.get("data") or []
    return payload if isinstance(payload, list) else []


def collect(rows: List[dict]) -> int:
    own = str(worklog.report_dir())
    written = 0
    for row in rows:
        title = worklog.clean(row.get("title") or "")
        directory = row.get("directory") or ""
        if not title:
            continue
        if directory == own:
            continue
        epoch_ms = row.get("time_updated") or 0
        when = datetime.fromtimestamp(int(epoch_ms) / 1000.0)
        worklog.append_record(when, worklog.project_name(directory), title, "opencode")
        written += 1
    return written


def main() -> int:
    since = read_cursor()
    try:
        rows = fetch_sessions(since)
    except (subprocess.TimeoutExpired, OSError, RuntimeError) as exc:
        print("opencode 查询失败: {}".format(exc), file=sys.stderr)
        return 1
    written = collect(rows)
    newest = since
    for row in rows:
        newest = max(newest, int(row.get("time_updated") or 0))
    if newest > since:
        write_cursor(newest)
    print("opencode 采集 {} 条，水位线 {}".format(written, newest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
