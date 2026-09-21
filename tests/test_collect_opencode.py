import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class CollectTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["WORKLOG_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("WORKLOG_HOME", None)
        self.tmp.cleanup()

    def _row(self, title, directory, when):
        epoch_ms = int(when.timestamp() * 1000)
        return {"id": "s1", "directory": directory, "title": title, "time_updated": epoch_ms}


class TestCollect(CollectTestCase):
    def test_writes_row_into_flow(self):
        import collect_opencode
        import worklog
        when = datetime(2026, 9, 11, 16, 20)
        count = collect_opencode.collect([self._row("数值表结构", "/a/game-design", when)])
        self.assertEqual(count, 1)
        rows = worklog.read_day(when)
        self.assertEqual(rows, [("16:20", "game-design", "数值表结构", "opencode", None)])

    def test_excludes_own_report_dir(self):
        import collect_opencode
        import worklog
        when = datetime(2026, 9, 11, 21, 7)
        own = str(worklog.report_dir())
        count = collect_opencode.collect([self._row("生成日报", own, when)])
        self.assertEqual(count, 0)
        self.assertEqual(worklog.read_day(when), [])

    def test_skips_empty_title(self):
        import collect_opencode
        when = datetime(2026, 9, 11, 10, 0)
        self.assertEqual(collect_opencode.collect([self._row("", "/a/repo", when)]), 0)

    def test_uses_session_timestamp_not_now(self):
        import collect_opencode
        import worklog
        yesterday = datetime(2026, 9, 10, 8, 30)
        collect_opencode.collect([self._row("昨天的会话", "/a/repo", yesterday)])
        self.assertEqual(len(worklog.read_day(yesterday)), 1)
        self.assertEqual(worklog.read_day(datetime(2026, 9, 11)), [])


class TestCursor(CollectTestCase):
    def test_defaults_to_zero(self):
        import collect_opencode
        self.assertEqual(collect_opencode.read_cursor(), 0)

    def test_roundtrip(self):
        import collect_opencode
        collect_opencode.write_cursor(1757600000000)
        self.assertEqual(collect_opencode.read_cursor(), 1757600000000)

    def test_ignores_corrupt_cursor(self):
        import collect_opencode
        import worklog
        worklog.state_dir().mkdir(parents=True, exist_ok=True)
        (worklog.state_dir() / "opencode.cursor").write_text("垃圾内容", encoding="utf-8")
        self.assertEqual(collect_opencode.read_cursor(), 0)


if __name__ == "__main__":
    unittest.main()
