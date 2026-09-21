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
