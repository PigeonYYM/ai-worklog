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

    def test_mark_write_failure_does_not_crash_the_hook(self):
        # 这个 hook 在每次回车时跑，是最不该崩的地方。用 .state/marks 被一个
        # 普通文件占位来制造 set_mark 里 mkdir 必然失败的场景（marks_dir()
        # 无条件调用 mkdir，不像 take_duration 那样受 Fix1 的存在性短路保护）。
        import worklog
        worklog.state_dir().mkdir(parents=True, exist_ok=True)
        (worklog.state_dir() / "marks").write_text("不是目录", encoding="utf-8")

        result = self.run_hook("claude_prompt.py", {"session_id": "abc", "cwd": "/a/repo"})

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")


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

    def test_missing_session_id_still_writes_record_with_unknown_duration(self):
        # session_id 缺失不该变成所有会话共享的 claude-unknown.mark 桶
        # （Fix3），但更重要的是：这条记录本身必须照样写进流水。
        import worklog
        result = self.run_hook(
            "claude_task.py",
            {"cwd": "/a/my-repo", "task_subject": "没有 session_id 也要记"},
        )
        self.assertEqual(result.returncode, 0)
        rows = worklog.read_day(datetime.now())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], "没有 session_id 也要记")
        self.assertIsNone(rows[0][4])

    def test_mark_write_failure_does_not_destroy_the_record(self):
        # take_duration 内部对标记文件的 mkdir/写入可能因 OSError 失败
        # （权限、磁盘满、.state/marks 被一个同名文件占位）。这是次要的时长
        # 功能，绝不能连累主功能——流水记录本身必须照样写入。用 .state/marks
        # 是一个普通文件来制造 mkdir 必然失败的场景。
        import worklog
        worklog.state_dir().mkdir(parents=True, exist_ok=True)
        (worklog.state_dir() / "marks").write_text("不是目录", encoding="utf-8")

        result = self.run_hook(
            "claude_task.py",
            {"session_id": "abc", "cwd": "/a/my-repo", "task_subject": "标记层故障也要记"},
        )

        self.assertEqual(result.returncode, 0)
        rows = worklog.read_day(datetime.now())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], "标记层故障也要记")
        self.assertIsNone(rows[0][4])

    def test_empty_subject_writes_nothing(self):
        import worklog
        self.run_hook("claude_task.py", {"session_id": "abc", "cwd": "/a/b", "task_subject": "  "})
        self.assertEqual(worklog.read_day(datetime.now()), [])

    def test_importing_task_module_does_not_consume_stdin(self):
        # 2026-09-11 踩过：hook 脚本裸调用 main() 会在被 import 时抢读 stdin。
        source = (REPO / "claude_task.py").read_text(encoding="utf-8")
        self.assertIn('if __name__ == "__main__":', source)
