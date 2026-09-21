# 流水时长记录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每条工作流水自带时长，并能按项目、按天汇总出可直接填报的工时。

**Architecture:** 每个采集工具在会话内维护一个「上次记录时刻」标记，写流水时取差值并把标记推进。TSV 由 4 列扩为 5 列，新增时长秒。日报正文仍由模型归纳，但工时数字由脚本确定性算出并以固定小节追加，模型不参与任何加总。

**Tech Stack:** Python 3（`/usr/bin/python3`，无第三方依赖）、unittest、TSV 文件存储、Claude Code / codex hooks、opencode SQLite。

**Spec:** `docs/superpowers/specs/2026-09-16-task-duration-design.md`

## Global Constraints

- 解释器固定 `/usr/bin/python3`，不引入任何第三方依赖。
- 所有测试通过 `WORKLOG_HOME` 环境变量重定向到临时目录，**绝不触碰真实的 `~/daily-report/`**。
- 流水保持 append-only，是唯一真相源；md 全是派生产物。
- 时长**如实记录，不封顶**。超过 24 小时的标记视为孤儿，该条记为未知（空），这是脏数据识别而非封顶。
- 未知时长写空字符串，**绝不写 0**：`0` 表示确实没花时间，空表示不知道。
- 历史 4 列数据不迁移、不回填。
- hook 脚本必须用 `if __name__ == "__main__":` 保护 `main()`。裸调用会导致被 import 时抢读 stdin（2026-09-11 已踩过，见 task-5-report.md）。
- 每个任务结束时跑全量测试：`cd ~/worklog && /usr/bin/python3 -m unittest discover -s tests -v`

---

### Task 1: worklog.py —— 时长列与标记点读写层

**Files:**
- Modify: `worklog.py`（`append_record` 第 40-44 行、`read_day` 第 47-56 行，并新增标记点相关函数）
- Modify: `tests/test_worklog.py`（`test_roundtrip` 第 56-61 行、`test_reconstructs_timestamp_from_filename_not_clock` 第 105-112 行的元组断言）
- Test: `tests/test_worklog.py`

**Interfaces:**
- Consumes: 无（本任务是基础层）
- Produces:
  - `safe_token(raw) -> str` —— 把任意值清洗成可作文件名的 token，空值回 `"unknown"`
  - `marks_dir() -> Path`
  - `mark_path(tool: str, session_id) -> Path`
  - `set_mark(tool: str, session_id, when: datetime) -> None`
  - `read_mark(tool: str, session_id) -> float | None` —— 返回 epoch 秒
  - `take_duration(tool: str, session_id, now: datetime) -> int | None` —— 返回距上一标记的秒数并把标记推进到 `now`
  - `cleanup_stale_marks(max_age_seconds: int = MARK_STALE_SECONDS) -> int`
  - `parse_duration(text) -> int | None`
  - `append_record(when, project, title, tool, duration_seconds=None) -> None`
  - `read_day(day) -> List[Tuple[str, str, str, str, Optional[int]]]` —— **5 元组**，第 5 位是时长秒或 `None`
  - 常量 `MARK_STALE_SECONDS = 86400`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_worklog.py`（放在 `TestAppendAndRead` 类之后）：

```python
class TestDurationColumn(WorklogTestCase):
    def test_roundtrip_with_duration(self):
        import worklog
        when = datetime(2026, 9, 16, 19, 2)
        worklog.append_record(when, "my-repo", "做了某事", "claude", 4260)
        self.assertEqual(
            worklog.read_day(when),
            [("19:02", "my-repo", "做了某事", "claude", 4260)],
        )

    def test_unknown_duration_is_empty_not_zero(self):
        import worklog
        when = datetime(2026, 9, 16, 19, 2)
        worklog.append_record(when, "my-repo", "做了某事", "claude")
        raw = worklog.raw_path(when).read_text(encoding="utf-8")
        self.assertTrue(raw.rstrip("\n").endswith("\tclaude\t"))
        self.assertIsNone(worklog.read_day(when)[0][4])

    def test_zero_duration_is_preserved_as_zero(self):
        import worklog
        when = datetime(2026, 9, 16, 19, 2)
        worklog.append_record(when, "my-repo", "秒完", "codex", 0)
        self.assertEqual(worklog.read_day(when)[0][4], 0)

    def test_reads_legacy_four_column_rows_as_unknown(self):
        import worklog
        when = datetime(2026, 9, 16, 19, 2)
        worklog.raw_dir().mkdir(parents=True, exist_ok=True)
        worklog.raw_path(when).write_text(
            "19:02\trepo\t老记录\tclaude\n20:10\trepo\t新记录\tclaude\t600\n",
            encoding="utf-8",
        )
        rows = worklog.read_day(when)
        self.assertEqual(rows[0], ("19:02", "repo", "老记录", "claude", None))
        self.assertEqual(rows[1], ("20:10", "repo", "新记录", "claude", 600))

    def test_malformed_duration_becomes_unknown(self):
        import worklog
        when = datetime(2026, 9, 16, 19, 2)
        worklog.raw_dir().mkdir(parents=True, exist_ok=True)
        worklog.raw_path(when).write_text(
            "19:02\trepo\t标题\tclaude\t不是数字\n", encoding="utf-8"
        )
        self.assertIsNone(worklog.read_day(when)[0][4])


class TestMarks(WorklogTestCase):
    def test_take_duration_without_mark_returns_none(self):
        import worklog
        self.assertIsNone(
            worklog.take_duration("claude", "s1", datetime(2026, 9, 16, 10, 0))
        )

    def test_take_duration_measures_from_previous_mark(self):
        import worklog
        worklog.set_mark("claude", "s1", datetime(2026, 9, 16, 10, 0))
        seconds = worklog.take_duration("claude", "s1", datetime(2026, 9, 16, 10, 30))
        self.assertEqual(seconds, 1800)

    def test_take_duration_advances_the_mark(self):
        import worklog
        worklog.set_mark("claude", "s1", datetime(2026, 9, 16, 10, 0))
        worklog.take_duration("claude", "s1", datetime(2026, 9, 16, 10, 30))
        second = worklog.take_duration("claude", "s1", datetime(2026, 9, 16, 10, 45))
        self.assertEqual(second, 900)

    def test_consecutive_durations_sum_to_total_elapsed(self):
        import worklog
        worklog.set_mark("claude", "s1", datetime(2026, 9, 16, 10, 0))
        first = worklog.take_duration("claude", "s1", datetime(2026, 9, 16, 10, 30))
        second = worklog.take_duration("claude", "s1", datetime(2026, 9, 16, 11, 15))
        self.assertEqual(first + second, 75 * 60)

    def test_stale_mark_returns_none_but_still_advances(self):
        import worklog
        worklog.set_mark("claude", "s1", datetime(2026, 9, 14, 10, 0))
        self.assertIsNone(
            worklog.take_duration("claude", "s1", datetime(2026, 9, 16, 10, 0))
        )
        self.assertEqual(
            worklog.take_duration("claude", "s1", datetime(2026, 9, 16, 10, 5)), 300
        )

    def test_backwards_clock_returns_none(self):
        import worklog
        worklog.set_mark("claude", "s1", datetime(2026, 9, 16, 10, 0))
        self.assertIsNone(
            worklog.take_duration("claude", "s1", datetime(2026, 9, 16, 9, 0))
        )

    def test_marks_are_isolated_per_tool_and_session(self):
        import worklog
        worklog.set_mark("claude", "s1", datetime(2026, 9, 16, 10, 0))
        self.assertIsNone(
            worklog.take_duration("codex", "s1", datetime(2026, 9, 16, 10, 5))
        )
        self.assertIsNone(
            worklog.take_duration("claude", "s2", datetime(2026, 9, 16, 10, 5))
        )

    def test_session_id_is_sanitised_into_filename(self):
        import worklog
        path = worklog.mark_path("claude", "../../etc/passwd")
        self.assertEqual(path.parent, worklog.marks_dir())
        self.assertEqual(path.name, "claude-etcpasswd.mark")

    def test_empty_session_id_falls_back_to_unknown(self):
        import worklog
        self.assertEqual(worklog.mark_path("claude", None).name, "claude-unknown.mark")

    def test_cleanup_removes_only_stale_marks(self):
        import os as os_module
        import time as time_module
        import worklog
        worklog.set_mark("claude", "old", datetime(2026, 9, 16, 10, 0))
        worklog.set_mark("claude", "fresh", datetime(2026, 9, 16, 10, 0))
        stale = worklog.mark_path("claude", "old")
        long_ago = time_module.time() - 200000
        os_module.utime(str(stale), (long_ago, long_ago))
        self.assertEqual(worklog.cleanup_stale_marks(86400), 1)
        self.assertFalse(stale.exists())
        self.assertTrue(worklog.mark_path("claude", "fresh").exists())

    def test_cleanup_on_missing_dir_returns_zero(self):
        import worklog
        self.assertEqual(worklog.cleanup_stale_marks(86400), 0)
```

同时把两处既有断言改成 5 元组。`tests/test_worklog.py` 第 61 行：

```python
        self.assertEqual(rows, [("19:02", "my-repo", "做了某事", "claude", None)])
```

第 106-112 行：

```python
        self.assertEqual(
            rows,
            [
                ("17:41", "ad-platform-frontend", "配置周报 hook", "claude", None),
                ("19:02", "repo", "阶段B2：Properties 构建", "claude", None),
            ],
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/worklog && /usr/bin/python3 -m unittest tests.test_worklog -v`
Expected: FAIL —— `AttributeError: module 'worklog' has no attribute 'take_duration'`，以及两处元组断言的长度不匹配。

- [ ] **Step 3: 实现**

`worklog.py` 顶部 import 与常量改成：

```python
"""流水读写与字段清洗。数据根目录可用 WORKLOG_HOME 覆盖（供测试使用）。"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
TOOLS = ("claude", "codex", "opencode")
UNSAFE_TOKEN = re.compile(r"[^A-Za-z0-9_-]")
MARK_STALE_SECONDS = 86400
```

把 `append_record` 与 `read_day` 替换为：

```python
def parse_duration(text) -> Optional[int]:
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def append_record(
    when: datetime,
    project: str,
    title: str,
    tool: str,
    duration_seconds: Optional[int] = None,
) -> None:
    raw_dir().mkdir(parents=True, exist_ok=True)
    line = "\t".join([
        "{:%H:%M}".format(when),
        clean(project),
        clean(title),
        tool,
        "" if duration_seconds is None else str(int(duration_seconds)),
    ])
    with raw_path(when).open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_day(day: datetime) -> List[Tuple[str, str, str, str, Optional[int]]]:
    path = raw_path(day)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        # 4 列是本功能上线前的历史流水，时长按未知处理，不回填。
        if len(parts) == 4:
            rows.append((parts[0], parts[1], parts[2], parts[3], None))
        elif len(parts) == 5:
            rows.append(
                (parts[0], parts[1], parts[2], parts[3], parse_duration(parts[4]))
            )
    return rows
```

在文件末尾追加标记点层：

```python
def safe_token(raw) -> str:
    cleaned = UNSAFE_TOKEN.sub("", str(raw or ""))
    return cleaned or "unknown"


def marks_dir() -> Path:
    return state_dir() / "marks"


def mark_path(tool: str, session_id) -> Path:
    return marks_dir() / "{}-{}.mark".format(safe_token(tool), safe_token(session_id))


def set_mark(tool: str, session_id, when: datetime) -> None:
    marks_dir().mkdir(parents=True, exist_ok=True)
    mark_path(tool, session_id).write_text(repr(when.timestamp()), encoding="utf-8")


def read_mark(tool: str, session_id) -> Optional[float]:
    try:
        return float(mark_path(tool, session_id).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def take_duration(tool: str, session_id, now: datetime) -> Optional[int]:
    """返回距上一标记点的秒数，并把标记推进到 now。

    标记缺失、时钟回拨、或标记已过期（孤儿）时返回 None——未知必须与 0 区分。
    无论哪种情况标记都会被推进，好让同会话的下一条有可用基线。
    """
    previous = read_mark(tool, session_id)
    set_mark(tool, session_id, now)
    if previous is None:
        return None
    elapsed = now.timestamp() - previous
    if elapsed < 0 or elapsed > MARK_STALE_SECONDS:
        return None
    return int(elapsed)


def cleanup_stale_marks(max_age_seconds: int = MARK_STALE_SECONDS) -> int:
    directory = marks_dir()
    if not directory.exists():
        return 0
    removed = 0
    now = time.time()
    for path in directory.glob("*.mark"):
        try:
            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/worklog && /usr/bin/python3 -m unittest discover -s tests -v`
Expected: 全部 PASS。`tests/test_collect_opencode.py` 与 `tests/test_codex.py` 此时仍应通过，因为 `append_record` 的新参数有默认值。

- [ ] **Step 5: 提交**

```bash
cd ~/worklog
git add worklog.py tests/test_worklog.py
git commit -m "feat(worklog): 流水加时长列与会话标记点读写层"
```

---

### Task 2: Claude 采集 —— UserPromptSubmit 标记 + TaskCompleted 取差值

**Files:**
- Create: `claude_prompt.py`
- Modify: `claude_task.py`（整文件重写，33 行）
- Modify: `~/.claude/settings.json`（**仅** `hooks.UserPromptSubmit`，追加而非替换）
- Test: `tests/test_claude.py`（新建）

**Interfaces:**
- Consumes: `worklog.set_mark`、`worklog.take_duration`、`worklog.cleanup_stale_marks`、`worklog.append_record`、`worklog.clean`、`worklog.project_name`（均来自 Task 1）
- Produces: `claude_prompt.main()`、`claude_task.main()`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_claude.py`：

```python
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class ClaudeHookTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["WORKLOG_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("WORKLOG_HOME", None)
        self.tmp.cleanup()

    def run_hook(self, script: str, payload: dict):
        env = dict(os.environ)
        env["WORKLOG_HOME"] = self.tmp.name
        return subprocess.run(
            ["/usr/bin/python3", str(REPO / script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )


class TestClaudePrompt(ClaudeHookTestCase):
    def test_writes_mark_for_session(self):
        import worklog
        result = self.run_hook("claude_prompt.py", {"session_id": "abc", "cwd": "/a/repo"})
        self.assertEqual(result.returncode, 0)
        self.assertIsNotNone(worklog.read_mark("claude", "abc"))

    def test_broken_stdin_is_silent(self):
        result = subprocess.run(
            ["/usr/bin/python3", str(REPO / "claude_prompt.py")],
            input="not json",
            capture_output=True,
            text=True,
            env=dict(os.environ, WORKLOG_HOME=self.tmp.name),
            timeout=30,
        )
        self.assertEqual(result.returncode, 0)


class TestClaudeTask(ClaudeHookTestCase):
    def test_records_duration_since_mark(self):
        import worklog
        worklog.set_mark("claude", "abc", datetime.now())
        result = self.run_hook(
            "claude_task.py",
            {"session_id": "abc", "cwd": "/a/my-repo", "task_subject": "修好了登录"},
        )
        self.assertEqual(result.returncode, 0)
        rows = worklog.read_day(datetime.now())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "my-repo")
        self.assertEqual(rows[0][2], "修好了登录")
        self.assertEqual(rows[0][3], "claude")
        self.assertIsNotNone(rows[0][4])
        self.assertLess(rows[0][4], 60)

    def test_without_mark_duration_is_unknown(self):
        import worklog
        result = self.run_hook(
            "claude_task.py",
            {"session_id": "no-mark", "cwd": "/a/my-repo", "task_subject": "干了点事"},
        )
        self.assertEqual(result.returncode, 0)
        rows = worklog.read_day(datetime.now())
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][4])

    def test_empty_subject_writes_nothing(self):
        import worklog
        self.run_hook("claude_task.py", {"session_id": "abc", "cwd": "/a/b", "task_subject": "  "})
        self.assertEqual(worklog.read_day(datetime.now()), [])

    def test_importing_task_module_does_not_consume_stdin(self):
        # 2026-09-11 踩过：hook 脚本裸调用 main() 会在被 import 时抢读 stdin。
        source = (REPO / "claude_task.py").read_text(encoding="utf-8")
        self.assertIn('if __name__ == "__main__":', source)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/worklog && /usr/bin/python3 -m unittest tests.test_claude -v`
Expected: FAIL —— `claude_prompt.py` 不存在（`can't open file`），且 `test_records_duration_since_mark` 因当前 `claude_task.py` 不写时长而拿到 `None`。

- [ ] **Step 3: 实现**

新建 `claude_prompt.py`：

```python
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
    worklog.set_mark("claude", payload.get("session_id"), datetime.now())
    worklog.cleanup_stale_marks()


if __name__ == "__main__":
    main()
```

把 `claude_task.py` 整体替换为：

```python
#!/usr/bin/env python3
"""Claude Code TaskCompleted hook：把完成的任务连同耗时写入工作流水。"""
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

    subject = worklog.clean(payload.get("task_subject") or "")
    if not subject:
        return

    now = datetime.now()
    worklog.append_record(
        now,
        worklog.project_name(payload.get("cwd")),
        subject,
        "claude",
        worklog.take_duration("claude", payload.get("session_id"), now),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/worklog && /usr/bin/python3 -m unittest discover -s tests -v`
Expected: 全部 PASS。

- [ ] **Step 5: 备份并修改 settings.json**

`hooks.UserPromptSubmit` **已有** claude-island-state 那一项，必须追加新元素，不能覆盖：

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.bak-duration
/usr/bin/python3 - <<'PY'
import json, pathlib
path = pathlib.Path.home() / ".claude" / "settings.json"
data = json.loads(path.read_text(encoding="utf-8"))
entry = {
    "hooks": [
        {
            "type": "command",
            "command": "/usr/bin/python3 '/Users/pigeonyang/worklog/claude_prompt.py' 2>/dev/null || true",
            "async": True,
        }
    ]
}
groups = data["hooks"]["UserPromptSubmit"]
assert not any(
    "claude_prompt.py" in h.get("command", "")
    for g in groups for h in g.get("hooks", [])
), "已经配置过，勿重复追加"
groups.append(entry)
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("UserPromptSubmit 现有分组数:", len(groups))
PY
```

- [ ] **Step 6: 校验 settings.json 只动了这一个键**

```bash
/usr/bin/python3 - <<'PY'
import json, pathlib
home = pathlib.Path.home() / ".claude"
old = json.loads((home / "settings.json.bak-duration").read_text(encoding="utf-8"))
new = json.loads((home / "settings.json").read_text(encoding="utf-8"))
old_hooks = old.pop("hooks"); new_hooks = new.pop("hooks")
assert old == new, "hooks 之外的顶层键被改动了"
for key in set(old_hooks) | set(new_hooks):
    if key != "UserPromptSubmit":
        assert old_hooks[key] == new_hooks[key], "误改了 hook 事件: " + key
assert len(new_hooks["UserPromptSubmit"]) == len(old_hooks["UserPromptSubmit"]) + 1
assert new_hooks["UserPromptSubmit"][0] == old_hooks["UserPromptSubmit"][0], "原有项被改动"
print("校验通过：仅 UserPromptSubmit 追加了一项")
PY
```

Expected: 打印「校验通过」。任何 assert 失败都要用 `settings.json.bak-duration` 还原后重来。

- [ ] **Step 7: 端到端验证真实 hook**

在 Claude Code 里用 `TaskCreate` 建一个任务再 `TaskUpdate` 标记 completed，然后确认今天的流水尾行是 5 列且时长非空：

```bash
/usr/bin/python3 -c "
import sys; sys.path.insert(0, '/Users/pigeonyang/worklog')
import worklog
from datetime import datetime
rows = worklog.read_day(datetime.now())
print(rows[-1] if rows else '无记录')
"
```

Expected: 末行形如 `('14:32', 'worklog', '...', 'claude', 812)`，第 5 位是整数而非 `None`。

- [ ] **Step 8: 提交**

```bash
cd ~/worklog
git add claude_prompt.py claude_task.py tests/test_claude.py
git commit -m "feat(claude): UserPromptSubmit 打标记，TaskCompleted 记录任务耗时"
```

---

### Task 3: codex 采集 —— 由缓存文件 mtime 算 turn 耗时

**Files:**
- Modify: `codex_stop.py`（第 17-45 行 `main`）
- Modify: `codex_prompt.py`（第 16 行与 25-27 行，改为复用 `worklog.safe_token`）
- Test: `tests/test_codex.py`

**Interfaces:**
- Consumes: `worklog.safe_token`、`worklog.append_record`（Task 1）
- Produces: 无新公开接口；`codex_prompt.safe_session_id` 保持同名同行为（既有测试依赖它）

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_codex.py`：

```python
class TestCodexDuration(CodexTestCase):
    def test_duration_measured_from_cache_mtime(self):
        import os as os_module
        import time as time_module
        import codex_prompt
        import codex_stop
        import worklog
        from datetime import datetime

        payload = {"session_id": "sess-1", "cwd": "/a/my-repo"}
        cache = codex_prompt.cache_path("sess-1")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("重构转化回传", encoding="utf-8")
        started = time_module.time() - 900
        os_module.utime(str(cache), (started, started))

        codex_stop.main_with_payload(payload)

        rows = worklog.read_day(datetime.now())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], "codex")
        self.assertGreaterEqual(rows[0][4], 890)
        self.assertLessEqual(rows[0][4], 910)

    def test_stale_cache_yields_unknown_duration(self):
        import os as os_module
        import time as time_module
        import codex_prompt
        import codex_stop
        import worklog
        from datetime import datetime

        cache = codex_prompt.cache_path("sess-2")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("很久以前的输入", encoding="utf-8")
        long_ago = time_module.time() - 200000
        os_module.utime(str(cache), (long_ago, long_ago))

        codex_stop.main_with_payload({"session_id": "sess-2", "cwd": "/a/my-repo"})

        rows = worklog.read_day(datetime.now())
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][4])

    def test_safe_session_id_delegates_to_worklog(self):
        import codex_prompt
        import worklog
        self.assertEqual(
            codex_prompt.safe_session_id("../../etc/passwd"),
            worklog.safe_token("../../etc/passwd"),
        )
```

> `CodexTestCase` 是 `tests/test_codex.py` 里既有的基类（它在 setUp 里设置 `WORKLOG_HOME` 与 `WORKLOG_CACHE`）。若实际类名不同，用该文件里现有的基类名替换。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/worklog && /usr/bin/python3 -m unittest tests.test_codex -v`
Expected: FAIL —— `AttributeError: module 'codex_stop' has no attribute 'main_with_payload'`。

- [ ] **Step 3: 实现**

`codex_stop.py` 整体替换为：

```python
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
```

`codex_prompt.py` 里删掉本地的 `UNSAFE` 常量与正则实现，改为复用共享实现。把第 16 行的 `UNSAFE = re.compile(...)` 删除、`import re` 删除，并把 `safe_session_id` 换成：

```python
def safe_session_id(raw) -> str:
    return worklog.safe_token(raw)
```

同时在 `codex_prompt.py` 的 import 区加入：

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))

import worklog
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/worklog && /usr/bin/python3 -m unittest discover -s tests -v`
Expected: 全部 PASS，包括既有的 `safe_session_id` 三条断言。

- [ ] **Step 5: 提交**

```bash
cd ~/worklog
git add codex_stop.py codex_prompt.py tests/test_codex.py
git commit -m "feat(codex): 由 prompt 缓存 mtime 记录 turn 耗时"
```

---

### Task 4: opencode 采集 —— 增量跨度

**Files:**
- Modify: `collect_opencode.py`（`fetch_sessions` 第 60-95 行的 SQL、`collect` 第 98-112 行、`main` 第 115-129 行）
- Test: `tests/test_collect_opencode.py`

**Interfaces:**
- Consumes: `worklog.append_record`（Task 1）
- Produces: `collect_opencode.session_duration(row, since_ms) -> int | None`、`collect(rows, since_ms=0) -> int`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_collect_opencode.py`：

```python
class TestSessionDuration(CollectTestCase):
    def test_first_collection_uses_full_span(self):
        import collect_opencode
        row = {"time_created": 1_000_000, "time_updated": 1_900_000}
        self.assertEqual(collect_opencode.session_duration(row, 0), 900)

    def test_resumed_session_only_counts_new_span(self):
        import collect_opencode
        row = {"time_created": 1_000_000, "time_updated": 1_900_000}
        self.assertEqual(collect_opencode.session_duration(row, 1_600_000), 300)

    def test_two_collections_sum_to_full_span(self):
        import collect_opencode
        created, first_seen, final = 1_000_000, 1_600_000, 1_900_000
        first = collect_opencode.session_duration(
            {"time_created": created, "time_updated": first_seen}, 0
        )
        second = collect_opencode.session_duration(
            {"time_created": created, "time_updated": final}, first_seen
        )
        self.assertEqual(first + second, (final - created) // 1000)

    def test_missing_timestamps_are_unknown(self):
        import collect_opencode
        self.assertIsNone(collect_opencode.session_duration({"time_updated": 5}, 0))
        self.assertIsNone(collect_opencode.session_duration({"time_created": 5}, 0))

    def test_cursor_ahead_of_update_yields_zero_not_negative(self):
        import collect_opencode
        row = {"time_created": 1_000_000, "time_updated": 1_200_000}
        self.assertEqual(collect_opencode.session_duration(row, 1_500_000), 0)

    def test_collect_writes_duration_into_flow(self):
        import collect_opencode
        import worklog
        from datetime import datetime
        when_ms = int(datetime(2026, 9, 16, 15, 0).timestamp() * 1000)
        row = {
            "id": "s1",
            "directory": "/a/game-design",
            "title": "数值表结构",
            "time_created": when_ms - 1_800_000,
            "time_updated": when_ms,
        }
        self.assertEqual(collect_opencode.collect([row], 0), 1)
        rows = worklog.read_day(datetime(2026, 9, 16))
        self.assertEqual(rows[0][4], 1800)
```

> `CollectTestCase` 是该文件里既有的基类，`_row` 辅助方法也已存在；新测试直接构造 dict 是为了显式控制 `time_created`。若既有 `_row` 未产出 `time_created`，需同步给它补上该字段，否则既有用例会走到「时长未知」分支——这是预期行为，不必改断言。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/worklog && /usr/bin/python3 -m unittest tests.test_collect_opencode -v`
Expected: FAIL —— `AttributeError: module 'collect_opencode' has no attribute 'session_duration'`。

- [ ] **Step 3: 实现**

`fetch_sessions` 里的 SQL 补一列：

```python
    query = (
        "SELECT id, directory, title, time_created, time_updated FROM session "
        "WHERE time_updated > {} ORDER BY time_updated".format(int(since_ms))
    )
```

新增 `session_duration`，并让 `collect` 接收水位线：

```python
def session_duration(row: dict, since_ms: int):
    """本次采集新增的那段跨度。

    水位线走 time_updated，续用旧会话会被再次采集；若按完整跨度计算，同一段
    时间会被重复计入工时。用水位线截断起点后，多次采集的累加值仍等于会话总跨度。
    """
    created = int(row.get("time_created") or 0)
    updated = int(row.get("time_updated") or 0)
    if not created or not updated:
        return None
    start = max(created, int(since_ms))
    if updated <= start:
        return 0
    return int((updated - start) // 1000)


def collect(rows: List[dict], since_ms: int = 0) -> int:
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
        worklog.append_record(
            when,
            worklog.project_name(directory),
            title,
            "opencode",
            session_duration(row, since_ms),
        )
        written += 1
    return written
```

`main` 里把水位线传下去：

```python
    written = collect(rows, since)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/worklog && /usr/bin/python3 -m unittest discover -s tests -v`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
cd ~/worklog
git add collect_opencode.py tests/test_collect_opencode.py
git commit -m "feat(opencode): 按水位线截断的增量跨度记录会话耗时"
```

---

### Task 5: summarize —— 工时小节、--hours 模式与文档

**Files:**
- Modify: `summarize.py`（`DAILY_PROMPT` 第 27-39 行、`WEEKLY_PROMPT` 第 41-53 行、`render_fallback` 第 82-92 行、`render_weekly_fallback` 第 95-109 行、`build_daily_prompt` 第 112-118 行、`build_weekly_prompt` 第 121-128 行、`daily` 第 208-225 行、`weekly` 第 233-257 行、`main` 第 260-270 行）
- Modify: `README.md`
- Test: `tests/test_summarize.py`

**Interfaces:**
- Consumes: `worklog.read_day`（返回 5 元组）、`worklog.WEEKDAYS`
- Produces: `format_hours(seconds) -> str`、`flow_row(row) -> str`、`record_suffix(row) -> str`、`hours_table(rows) -> str`、`hours_report(start, end) -> int`

- [ ] **Step 1: 写失败的测试**

把 `tests/test_summarize.py` 顶部的 `ROWS` 改成 5 元组（含一条未知时长）：

```python
ROWS = [
    ("19:02", "overseas-ad-event-processor", "阶段B1：InstallEvent 与去重键", "claude", 3600),
    ("19:05", "overseas-ad-event-processor", "阶段B3：install handlePlatform", "claude", 1800),
    ("16:20", "game-design", "数值策划表结构", "opencode", None),
]
```

并把 `test_marks_source_tool` 第 89 行的断言改为：

```python
        self.assertIn("(opencode)", text)
```

（`game-design` 那条时长为 `None`，后缀仍是纯工具名，断言不变。）

追加新测试类：

```python
class TestFormatHours(SummarizeTestCase):
    def test_converts_seconds_to_hours(self):
        import summarize
        self.assertEqual(summarize.format_hours(3600), "1.0h")
        self.assertEqual(summarize.format_hours(5400), "1.5h")

    def test_unknown_renders_dash(self):
        import summarize
        self.assertEqual(summarize.format_hours(None), "-")

    def test_zero_is_not_unknown(self):
        import summarize
        self.assertEqual(summarize.format_hours(0), "0.0h")


class TestHoursTable(SummarizeTestCase):
    def test_totals_per_project(self):
        import summarize
        text = summarize.hours_table(ROWS)
        self.assertIn("| overseas-ad-event-processor | 1.5 |", text)

    def test_grand_total_sums_known_durations(self):
        import summarize
        text = summarize.hours_table(ROWS)
        self.assertIn("**1.5**", text)

    def test_unknown_rows_are_reported_not_counted_as_zero(self):
        import summarize
        text = summarize.hours_table(ROWS)
        self.assertIn("1 条记录无时长", text)
        self.assertNotIn("game-design", text)

    def test_all_unknown_yields_empty_table(self):
        import summarize
        rows = [("09:00", "repo", "标题", "claude", None)]
        self.assertEqual(summarize.hours_table(rows), "")


class TestRenderFallbackDuration(SummarizeTestCase):
    def test_shows_duration_when_known(self):
        import summarize
        text = summarize.render_fallback(ROWS, datetime(2026, 9, 16))
        self.assertIn("(claude, 1.0h)", text)

    def test_omits_duration_when_unknown(self):
        import summarize
        text = summarize.render_fallback(ROWS, datetime(2026, 9, 16))
        self.assertIn("(opencode)", text)


class TestPromptCarriesDuration(SummarizeTestCase):
    def test_flow_includes_hours_column(self):
        import summarize
        prompt = summarize.build_daily_prompt(ROWS, datetime(2026, 9, 16))
        self.assertIn("1.0h", prompt)
        self.assertIn("时长", prompt)

    def test_forbids_model_aggregation(self):
        import summarize
        prompt = summarize.build_daily_prompt(ROWS, datetime(2026, 9, 16))
        self.assertIn("不要加总", prompt)


class TestDailyAppendsHours(SummarizeTestCase):
    def test_hours_section_present_even_when_model_unavailable(self):
        import summarize
        import worklog
        when = datetime(2026, 9, 16, 19, 2)
        worklog.append_record(when, "repo-a", "做了某事", "claude", 3600)
        os.environ["WORKLOG_OPENCODE"] = "/nonexistent/opencode"
        os.environ["WORKLOG_SKIP_COLLECT"] = "1"
        try:
            summarize.daily(when)
        finally:
            os.environ.pop("WORKLOG_OPENCODE", None)
            os.environ.pop("WORKLOG_SKIP_COLLECT", None)
        text = (worklog.report_dir() / "2026-09-16.md").read_text(encoding="utf-8")
        self.assertIn("## 工时", text)
        self.assertIn("| repo-a | 1.0 |", text)


class TestHoursReport(SummarizeTestCase):
    def test_prints_table_per_day(self):
        import contextlib
        import io
        import summarize
        import worklog
        worklog.append_record(datetime(2026, 9, 15, 10, 0), "repo-a", "一", "claude", 3600)
        worklog.append_record(datetime(2026, 9, 16, 10, 0), "repo-b", "二", "claude", 1800)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = summarize.hours_report(datetime(2026, 9, 15), datetime(2026, 9, 16))
        out = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("2026-09-15", out)
        self.assertIn("| repo-a | 1.0 |", out)
        self.assertIn("| repo-b | 0.5 |", out)

    def test_empty_range_reports_no_records(self):
        import contextlib
        import io
        import summarize
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            summarize.hours_report(datetime(2020, 1, 1), datetime(2020, 1, 2))
        self.assertIn("无记录", buffer.getvalue())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ~/worklog && /usr/bin/python3 -m unittest tests.test_summarize -v`
Expected: FAIL —— `AttributeError: module 'summarize' has no attribute 'format_hours'`。

- [ ] **Step 3: 实现**

两个 prompt 模板的末尾说明改为：

```python
流水（列依次为 时间、项目、标题、来源工具、时长）：
{flow}
```

并在两个模板的规则列表里各加一行：

```
- 时长列原样保留，不要加总、不要换算、不要编造——工时汇总由脚本另行生成
```

新增辅助函数（放在 `render_fallback` 之前）：

```python
def format_hours(seconds) -> str:
    if seconds is None:
        return "-"
    return "{:.1f}h".format(seconds / 3600.0)


def record_suffix(row) -> str:
    if row[4] is None:
        return row[3]
    return "{}, {}".format(row[3], format_hours(row[4]))


def flow_row(row) -> str:
    return "\t".join([row[0], row[1], row[2], row[3], format_hours(row[4])])


def hours_table(rows) -> str:
    """按项目汇总工时。数字由脚本算，绝不交给模型。"""
    totals = {}
    unknown = 0
    for row in rows:
        if row[4] is None:
            unknown += 1
            continue
        totals[row[1]] = totals.get(row[1], 0) + row[4]
    if not totals:
        return ""
    lines = ["## 工时", "", "| 项目 | 小时 |", "| --- | --- |"]
    for project in sorted(totals, key=lambda name: (-totals[name], name)):
        lines.append("| {} | {:.1f} |".format(project, totals[project] / 3600.0))
    lines.append("| **合计** | **{:.1f}** |".format(sum(totals.values()) / 3600.0))
    if unknown:
        lines.extend(["", "> {} 条记录无时长，未计入。".format(unknown)])
    return "\n".join(lines) + "\n"
```

`render_fallback` 与 `render_weekly_fallback` 的记录行改用 `record_suffix`：

```python
            out.append("- {} {} ({})".format(row[0], row[2], record_suffix(row)))
```

```python
            out.append("- {} {} ({})".format(day_text, row[2], record_suffix(row)))
```

两个 prompt 构造函数改用 `flow_row`：

```python
def build_daily_prompt(rows, day: datetime) -> str:
    flow = "\n".join(flow_row(row) for row in rows)
    ...


def build_weekly_prompt(dated_rows, start: datetime, end: datetime) -> str:
    flow = "\n".join(
        "{}\t{}".format(day_text, flow_row(row)) for day_text, row in dated_rows
    )
    ...
```

`daily` 与 `weekly` 在写盘前追加工时小节。`daily` 里把 `atomic_write` 之前改成：

```python
        table = hours_table(rows)
        if table:
            content = content.rstrip() + "\n\n" + table
        target = worklog.report_dir() / "{:%Y-%m-%d}.md".format(day)
```

`weekly` 里同样：

```python
        table = hours_table(rows)
        if table:
            content = content.rstrip() + "\n\n" + table
        target = worklog.report_dir() / "weekly" / "{:%G-W%V}.md".format(start)
```

新增 `hours_report`（放在 `weekly` 之后）：

```python
def hours_report(start: datetime, end: datetime) -> int:
    cursor = start
    found = False
    while cursor <= end:
        rows = worklog.read_day(cursor)
        table = hours_table(rows)
        if table:
            found = True
            print("## {:%Y-%m-%d} {}".format(cursor, worklog.WEEKDAYS[cursor.weekday()]))
            print(table)
        cursor += timedelta(days=1)
    if not found:
        print("区间内无记录")
    return 0
```

`main` 增加参数与分支：

```python
    parser.add_argument("--hours", action="store_true", help="输出工时汇总，不生成 md")
    parser.add_argument("--since", help="工时汇总起始日期 YYYY-MM-DD，默认与 --date 同天")
    args = parser.parse_args()

    day = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    if args.hours:
        start = datetime.strptime(args.since, "%Y-%m-%d") if args.since else day
        return hours_report(start, day)
    if args.weekly:
        return weekly(day)
    return daily(day)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ~/worklog && /usr/bin/python3 -m unittest discover -s tests -v`
Expected: 全部 PASS。

- [ ] **Step 5: 真实数据冒烟**

```bash
/usr/bin/python3 ~/worklog/summarize.py --hours --since 2026-09-14
```

Expected: 按天打印工时表；历史条目因无时长会显示「N 条记录无时长，未计入」，这是预期的。

- [ ] **Step 6: 更新 README**

`## 数据` 一节第 7 行改为：

```markdown
- `~/daily-report/.raw/YYYY-MM-DD.tsv` — 流水，append-only，**唯一真相源**。五列：时间、项目、标题、来源工具、时长秒（tab 分隔）
```

在该节末尾追加：

```markdown
时长为空表示未知（本功能上线前的历史流水、或 hook 未触发），与 `0` 严格区分——
汇总时未知条目不计入，并在工时表下方标注条数。时长如实记录、不封顶；只有距今
超过 24 小时的孤儿标记才记为未知。
```

`## 采集` 表格下方追加：

```markdown
时长口径是「会话内上一标记点到本次记录」的差值：Claude 由 `UserPromptSubmit`
（`claude_prompt.py`）打标记，codex 用 prompt 缓存文件的 mtime，opencode 用
`time_created` 并按采集水位线截断。同一会话内各条时长之和等于真实流逝时间。
```

`## 手动操作` 代码块里追加一行：

```bash
/usr/bin/python3 ~/worklog/summarize.py --hours --since 2026-09-14  # 输出工时汇总，用于填报
```

并把测试数量说明（第 45 行）里的「54 个测试」改为实际数量（跑一次 `discover` 看结尾输出的 `Ran N tests`）。

- [ ] **Step 7: 提交**

```bash
cd ~/worklog
git add summarize.py tests/test_summarize.py README.md
git commit -m "feat(summarize): 脚本确定性生成工时小节，新增 --hours 填报模式"
```

---

## Self-Review

**Spec 覆盖检查：**

| Spec 要求 | 对应任务 |
|---|---|
| TSV 扩 5 列、存整数秒 | Task 1 |
| 未知写空不写 0 | Task 1（`test_unknown_duration_is_empty_not_zero`、`test_zero_duration_is_preserved_as_zero`） |
| 标记点层放 `.state/marks/` | Task 1 |
| 24 小时孤儿判定 | Task 1（`test_stale_mark_returns_none_but_still_advances`）、Task 3（`test_stale_cache_yields_unknown_duration`） |
| Claude `UserPromptSubmit` 打标记 | Task 2 |
| codex 用缓存 mtime | Task 3 |
| opencode 增量跨度 | Task 4（`test_two_collections_sum_to_full_span`） |
| 数字不交给模型 | Task 5（`hours_table` 为脚本渲染，`test_forbids_model_aggregation`） |
| `--hours` 模式 | Task 5 |
| 4/5 列混读兼容 | Task 1（`test_reads_legacy_four_column_rows_as_unknown`） |
| 历史不回填 | 全程未引入迁移脚本 |
| settings.json 备份 + 全字段比对 | Task 2 Step 5-6 |

**类型一致性：** `read_day` 在 Task 1 定为 5 元组，Task 5 的 `record_suffix`/`flow_row`/`hours_table` 一律按 `row[4]` 取时长；`session_duration` 与 `take_duration` 均返回 `int | None`，与 `append_record` 的 `duration_seconds` 参数类型一致。

**已知的跨任务影响：** Task 1 改 `read_day` 返回值后，`tests/test_summarize.py` 顶部的 `ROWS` 常量仍是 4 元组，但它是测试内部的字面量、不经过 `read_day`，因此 Task 1 结束时不会红；Task 5 Step 1 会把它改成 5 元组。`migrate_legacy.py` 以 4 参数调用 `append_record`，靠默认值继续工作，无需改动。
