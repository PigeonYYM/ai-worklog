import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROWS = [
    ("19:02", "overseas-ad-event-processor", "阶段B1：InstallEvent 与去重键", "claude"),
    ("19:05", "overseas-ad-event-processor", "阶段B3：install handlePlatform", "claude"),
    ("16:20", "game-design", "数值策划表结构", "opencode"),
]


class SummarizeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["WORKLOG_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("WORKLOG_HOME", None)
        self.tmp.cleanup()


class TestStripAnsi(SummarizeTestCase):
    def test_removes_color_codes(self):
        import summarize
        self.assertEqual(summarize.strip_ansi("\x1b[0mhello\x1b[31m"), "hello")


class TestExtractBody(SummarizeTestCase):
    def test_drops_opencode_header(self):
        import summarize
        raw = "\x1b[0m\n> build · kimi-for-coding/k3\n\x1b[0m\n## repo\n- 做了某事\n"
        self.assertEqual(summarize.extract_body(raw), "## repo\n- 做了某事")

    def test_keeps_quote_inside_body(self):
        import summarize
        raw = "> build · m\n\n## repo\n- 一条\n> 引用不该被吃掉\n"
        body = summarize.extract_body(raw)
        self.assertIn("> 引用不该被吃掉", body)


class TestValidate(SummarizeTestCase):
    def test_rejects_empty(self):
        import summarize
        self.assertFalse(summarize.validate("   \n  ", ROWS))

    def test_rejects_missing_project(self):
        import summarize
        body = "## overseas-ad-event-processor\n- 重构 install 链路"
        self.assertFalse(summarize.validate(body, ROWS))

    def test_accepts_complete_output(self):
        import summarize
        body = (
            "## overseas-ad-event-processor\n"
            "- 重构 install 事件处理链路（去重键、handlePlatform）\n"
            "## game-design\n"
            "- 整理数值策划表结构\n"
        )
        self.assertTrue(summarize.validate(body, ROWS))

    def test_rejects_fewer_lines_than_projects(self):
        import summarize
        body = "overseas-ad-event-processor game-design"
        self.assertFalse(summarize.validate(body, ROWS))


class TestRenderFallback(SummarizeTestCase):
    def test_groups_by_project(self):
        import summarize
        text = summarize.render_fallback(ROWS, datetime(2026, 9, 11))
        self.assertIn("# 2026-09-11 周五", text)
        self.assertIn("## game-design", text)
        self.assertIn("## overseas-ad-event-processor", text)

    def test_keeps_every_record(self):
        import summarize
        text = summarize.render_fallback(ROWS, datetime(2026, 9, 11))
        for row in ROWS:
            self.assertIn(row[2], text)

    def test_marks_source_tool(self):
        import summarize
        text = summarize.render_fallback(ROWS, datetime(2026, 9, 11))
        self.assertIn("(opencode)", text)


class TestBuildPrompt(SummarizeTestCase):
    def test_includes_all_records(self):
        import summarize
        prompt = summarize.build_daily_prompt(ROWS, datetime(2026, 9, 11))
        for row in ROWS:
            self.assertIn(row[2], prompt)

    def test_states_merge_rule(self):
        import summarize
        prompt = summarize.build_daily_prompt(ROWS, datetime(2026, 9, 11))
        self.assertIn("括号", prompt)
        self.assertIn("不要发明", prompt)


class TestAtomicWrite(SummarizeTestCase):
    def test_writes_and_leaves_no_tmp(self):
        import summarize
        import worklog
        worklog.report_dir().mkdir(parents=True, exist_ok=True)
        target = worklog.report_dir() / "out.md"
        summarize.atomic_write(target, "内容\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "内容\n")
        self.assertEqual(list(worklog.report_dir().glob("*.tmp")), [])


class TestDailyFallbackPath(SummarizeTestCase):
    def test_falls_back_when_model_unavailable(self):
        import summarize
        import worklog
        when = datetime(2026, 9, 11, 19, 2)
        worklog.append_record(when, "repo-a", "做了某事", "claude")
        os.environ["WORKLOG_OPENCODE"] = "/nonexistent/opencode"
        os.environ["WORKLOG_SKIP_COLLECT"] = "1"
        try:
            code = summarize.daily(when)
        finally:
            os.environ.pop("WORKLOG_OPENCODE", None)
            os.environ.pop("WORKLOG_SKIP_COLLECT", None)
        self.assertEqual(code, 0)
        text = (worklog.report_dir() / "2026-09-11.md").read_text(encoding="utf-8")
        self.assertIn("做了某事", text)
        self.assertIn("## repo-a", text)

    def test_no_records_writes_nothing(self):
        import summarize
        import worklog
        os.environ["WORKLOG_SKIP_COLLECT"] = "1"
        try:
            code = summarize.daily(datetime(2026, 9, 11))
        finally:
            os.environ.pop("WORKLOG_SKIP_COLLECT", None)
        self.assertEqual(code, 0)
        self.assertFalse((worklog.report_dir() / "2026-09-11.md").exists())


if __name__ == "__main__":
    unittest.main()
