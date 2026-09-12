import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class CodexTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = tempfile.TemporaryDirectory()
        os.environ["WORKLOG_HOME"] = self.tmp.name
        os.environ["WORKLOG_CACHE"] = self.cache.name

    def tearDown(self):
        os.environ.pop("WORKLOG_HOME", None)
        os.environ.pop("WORKLOG_CACHE", None)
        self.tmp.cleanup()
        self.cache.cleanup()

    def run_hook(self, script, payload):
        env = dict(os.environ)
        return subprocess.run(
            ["/usr/bin/python3", str(ROOT / script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
        )


class TestSafeSessionId(CodexTestCase):
    def test_strips_path_traversal(self):
        import codex_prompt
        self.assertEqual(codex_prompt.safe_session_id("../../etc/passwd"), "etcpasswd")

    def test_keeps_uuid_shape(self):
        import codex_prompt
        sid = "01a09009-aaaa-bbbb-cccc-ddddeeeeffff"
        self.assertEqual(codex_prompt.safe_session_id(sid), sid)

    def test_empty_becomes_placeholder(self):
        import codex_prompt
        self.assertEqual(codex_prompt.safe_session_id(""), "unknown")


class TestPromptCaching(CodexTestCase):
    def test_caches_prompt(self):
        import codex_prompt
        result = self.run_hook("codex_prompt.py", {"session_id": "s1", "prompt": "帮我修一下登录"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(codex_prompt.cache_path("s1").read_text(encoding="utf-8"), "帮我修一下登录")

    def test_bad_json_exits_clean(self):
        result = subprocess.run(
            ["/usr/bin/python3", str(ROOT / "codex_prompt.py")],
            input="not json",
            capture_output=True,
            text=True,
            env=dict(os.environ),
        )
        self.assertEqual(result.returncode, 0)


class TestStopCollection(CodexTestCase):
    def test_uses_cached_prompt_as_title(self):
        import worklog
        self.run_hook("codex_prompt.py", {"session_id": "s2", "prompt": "修复埋点上报"})
        result = self.run_hook("codex_stop.py", {
            "session_id": "s2",
            "cwd": "/a/b/ad-platform-frontend",
            "last_assistant_message": "已完成",
        })
        self.assertEqual(result.returncode, 0)
        rows = worklog.read_day(datetime.now())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "ad-platform-frontend")
        self.assertEqual(rows[0][2], "修复埋点上报")
        self.assertEqual(rows[0][3], "codex")

    def test_consumes_cache(self):
        import codex_prompt
        self.run_hook("codex_prompt.py", {"session_id": "s3", "prompt": "任务一"})
        self.run_hook("codex_stop.py", {"session_id": "s3", "cwd": "/a/repo"})
        self.assertFalse(codex_prompt.cache_path("s3").exists())

    def test_without_cache_is_skipped(self):
        import worklog
        result = self.run_hook("codex_stop.py", {"session_id": "s4", "cwd": "/a/repo"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(worklog.read_day(datetime.now()), [])


class TestCleanupStale(CodexTestCase):
    def test_removes_old_cache(self):
        import codex_prompt
        stale = codex_prompt.cache_path("old")
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("旧内容", encoding="utf-8")
        os.utime(str(stale), (time.time() - 90000, time.time() - 90000))
        removed = codex_prompt.cleanup_stale(86400)
        self.assertEqual(removed, 1)
        self.assertFalse(stale.exists())

    def test_keeps_fresh_cache(self):
        import codex_prompt
        fresh = codex_prompt.cache_path("new")
        fresh.parent.mkdir(parents=True, exist_ok=True)
        fresh.write_text("新内容", encoding="utf-8")
        self.assertEqual(codex_prompt.cleanup_stale(86400), 0)
        self.assertTrue(fresh.exists())


if __name__ == "__main__":
    unittest.main()
