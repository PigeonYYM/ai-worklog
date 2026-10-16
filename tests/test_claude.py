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


class TestClaudePromptCaching(ClaudeHookTestCase):
    def setUp(self):
        super().setUp()
        self.cache = tempfile.TemporaryDirectory()
        os.environ["WORKLOG_CACHE"] = self.cache.name

    def tearDown(self):
        os.environ.pop("WORKLOG_CACHE", None)
        self.cache.cleanup()
        super().tearDown()

    def test_caches_prompt_for_stop_hook(self):
        import claude_prompt
        result = self.run_hook(
            "claude_prompt.py",
            {"session_id": "abc", "cwd": "/a/repo", "prompt": "帮我写日报"},
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            claude_prompt.cache_path("abc").read_text(encoding="utf-8"), "帮我写日报"
        )

    def test_empty_prompt_writes_no_cache(self):
        import claude_prompt
        self.run_hook("claude_prompt.py", {"session_id": "abc", "prompt": "   "})
        self.assertFalse(claude_prompt.cache_path("abc").exists())


class TestClaudeStop(ClaudeHookTestCase):
    def setUp(self):
        super().setUp()
        self.cache = tempfile.TemporaryDirectory()
        os.environ["WORKLOG_CACHE"] = self.cache.name

    def tearDown(self):
        os.environ.pop("WORKLOG_CACHE", None)
        self.cache.cleanup()
        super().tearDown()

    def test_records_turn_with_cached_prompt_as_title(self):
        import worklog
        self.run_hook(
            "claude_prompt.py",
            {"session_id": "s1", "cwd": "/a/my-repo", "prompt": "修复归因数据拉取"},
        )
        result = self.run_hook("claude_stop.py", {"session_id": "s1", "cwd": "/a/my-repo"})
        self.assertEqual(result.returncode, 0)
        rows = worklog.read_day(datetime.now())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "my-repo")
        self.assertEqual(rows[0][2], "修复归因数据拉取")
        self.assertEqual(rows[0][3], "claude")
        self.assertIsNotNone(rows[0][4])

    def test_consumes_cache(self):
        import claude_prompt
        self.run_hook("claude_prompt.py", {"session_id": "s2", "prompt": "任务一"})
        self.run_hook("claude_stop.py", {"session_id": "s2", "cwd": "/a/repo"})
        self.assertFalse(claude_prompt.cache_path("s2").exists())

    def test_without_cache_is_skipped(self):
        import worklog
        result = self.run_hook("claude_stop.py", {"session_id": "s3", "cwd": "/a/repo"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(worklog.read_day(datetime.now()), [])

    def test_task_then_stop_split_duration_without_overlap(self):
        # 与 TaskCompleted 共用同一条标记链：turn 中段的清单项完成拿走前半段，
        # Stop 只拿剩余尾段，两者相加不重不漏（第二条应接近 0，而不是又一段完整时长）。
        import time
        import worklog
        import claude_stop
        import claude_prompt

        started = datetime.now()
        worklog.set_mark("claude", "s4", started)
        claude_prompt.cache_path("s4").parent.mkdir(parents=True, exist_ok=True)
        claude_prompt.cache_path("s4").write_text("大任务", encoding="utf-8")

        self.run_hook(
            "claude_task.py",
            {"session_id": "s4", "cwd": "/a/repo", "task_subject": "子任务一"},
        )
        claude_stop.main_with_payload({"session_id": "s4", "cwd": "/a/repo"})

        rows = worklog.read_day(datetime.now())
        self.assertEqual(len(rows), 2)
        total = sum(row[4] or 0 for row in rows)
        self.assertLessEqual(total, int(time.time() - started.timestamp()) + 1)

    def test_cache_read_failure_keeps_mark_untouched(self):
        # 标题取不到就不该消耗时长标记，否则这个 turn 的时长会被静默抹掉。
        import worklog
        import claude_prompt
        import claude_stop

        worklog.set_mark("claude", "s5", datetime.now())
        cache = claude_prompt.cache_path("s5")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.mkdir()  # 同名的目录让 read_text 必然 OSError

        claude_stop.main_with_payload({"session_id": "s5", "cwd": "/a/repo"})

        self.assertEqual(worklog.read_day(datetime.now()), [])
        self.assertIsNotNone(worklog.read_mark("claude", "s5"))

