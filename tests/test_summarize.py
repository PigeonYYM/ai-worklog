import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROWS = [
    ("19:02", "overseas-ad-event-processor", "阶段B1：InstallEvent 与去重键", "claude", 3600),
    ("19:05", "overseas-ad-event-processor", "阶段B3：install handlePlatform", "claude", 1800),
    ("16:20", "game-design", "数值策划表结构", "opencode", None),
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


class TestBuildWeeklyPrompt(SummarizeTestCase):
    def test_handles_none_duration_in_weekly_prompt(self):
        """Regression test: build_weekly_prompt must tolerate None in 5-tuple duration column.

        This test would fail before the fix with:
        TypeError: sequence item 4: expected str instance, NoneType found
        """
        import summarize
        # 5-tuples with None duration (which worklog.read_day() now returns)
        rows_with_none_duration = [
            ("19:02", "overseas-ad-event-processor", "阶段B1：InstallEvent", "claude", None),
            ("19:05", "overseas-ad-event-processor", "阶段B3：install handlePlatform", "claude", None),
        ]
        dated_rows = [
            ("09-11", rows_with_none_duration[0]),
            ("09-11", rows_with_none_duration[1]),
        ]
        # This call must not raise TypeError
        prompt = summarize.build_weekly_prompt(dated_rows, datetime(2026, 9, 8), datetime(2026, 9, 14))
        self.assertIn("09-11", prompt)
        self.assertIn("InstallEvent", prompt)
        self.assertIn("overseas-ad-event-processor", prompt)


class TestAtomicWrite(SummarizeTestCase):
    def test_writes_and_leaves_no_tmp(self):
        import summarize
        import worklog
        worklog.report_dir().mkdir(parents=True, exist_ok=True)
        target = worklog.report_dir() / "out.md"
        summarize.atomic_write(target, "内容\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "内容\n")
        self.assertEqual(list(worklog.report_dir().glob("*.tmp")), [])

    def test_leaves_no_tmp_when_replace_fails(self):
        import summarize
        import worklog
        worklog.report_dir().mkdir(parents=True, exist_ok=True)
        # 目标路径本身是一个目录，os.replace 到这里必然失败（IsADirectoryError
        # 是 OSError 的子类），用来触发 atomic_write 里 os.replace 那一步的异常路径。
        target = worklog.report_dir() / "out_dir.md"
        target.mkdir()
        tmp_path = target.with_name(target.name + ".tmp")
        with self.assertRaises(OSError):
            summarize.atomic_write(target, "内容\n")
        self.assertFalse(tmp_path.exists())


def write_stub(path: Path, body: str) -> None:
    """写一个模拟 opencode 的可执行脚本，供 WORKLOG_OPENCODE 指向。"""
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


class TestRunModelStub(SummarizeTestCase):
    """用真实子进程（可执行的 stub 脚本）驱动 run_model 的临时文件机制，
    而不是让它在 opencode_bin() 处提前短路返回 None。这条路径此前完全没有
    测试覆盖：tempfile.mkstemp 创建、以文件方式接收 stdout、读回、清理。
    """

    def setUp(self):
        super().setUp()
        self.stub_dir = tempfile.TemporaryDirectory()
        self.stub_path = Path(self.stub_dir.name) / "fake-opencode"

    def tearDown(self):
        os.environ.pop("WORKLOG_OPENCODE", None)
        self.stub_dir.cleanup()
        super().tearDown()

    def _tmp_leftovers(self):
        # run_model 用 tempfile.mkstemp(suffix=".txt") 创建临时文件，
        # 默认落在系统临时目录（tempfile.gettempdir()），不是 report_dir()。
        return list(Path(tempfile.gettempdir()).glob("tmp*.txt"))

    def test_success_returns_body_and_leaves_no_tmp(self):
        import summarize
        write_stub(
            self.stub_path,
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "print('> build \\u00b7 kimi-for-coding/k3')\n"
            "print()\n"
            "print('## repo')\n"
            "print('- 做了某事')\n",
        )
        os.environ["WORKLOG_OPENCODE"] = str(self.stub_path)
        before = set(self._tmp_leftovers())
        body = summarize.run_model("随便一个 prompt")
        after = set(self._tmp_leftovers())
        self.assertEqual(body, "## repo\n- 做了某事")
        self.assertEqual(after - before, set())

    def test_success_with_large_output_leaves_no_tmp(self):
        import summarize
        # pin 住临时文件方案要保护的确切行为：管道捕获在大输出下会静默截断，
        # 文件方式不会。这里让 stub 输出明显超过 64KB。
        write_stub(
            self.stub_path,
            "#!/usr/bin/env python3\n"
            "print('> build \\u00b7 kimi-for-coding/k3')\n"
            "print()\n"
            "print('## repo')\n"
            "line = '- ' + ('x' * 200)\n"
            "for _ in range(400):\n"
            "    print(line)\n",
        )
        os.environ["WORKLOG_OPENCODE"] = str(self.stub_path)
        before = set(self._tmp_leftovers())
        body = summarize.run_model("prompt")
        after = set(self._tmp_leftovers())
        self.assertIsNotNone(body)
        self.assertGreater(len(body.encode("utf-8")), 64 * 1024)
        self.assertTrue(body.startswith("## repo"))
        self.assertEqual(after - before, set())

    def test_nonzero_exit_returns_none_and_leaves_no_tmp(self):
        import summarize
        write_stub(
            self.stub_path,
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "print('部分输出')\n"
            "sys.exit(1)\n",
        )
        os.environ["WORKLOG_OPENCODE"] = str(self.stub_path)
        before = set(self._tmp_leftovers())
        body = summarize.run_model("prompt")
        after = set(self._tmp_leftovers())
        self.assertIsNone(body)
        self.assertEqual(after - before, set())

    def test_timeout_returns_none_and_leaves_no_tmp(self):
        import summarize
        write_stub(
            self.stub_path,
            "#!/usr/bin/env python3\n"
            "import time\n"
            "time.sleep(5)\n",
        )
        os.environ["WORKLOG_OPENCODE"] = str(self.stub_path)
        original_timeout = summarize.MODEL_TIMEOUT
        summarize.MODEL_TIMEOUT = 1
        try:
            before = set(self._tmp_leftovers())
            body = summarize.run_model("prompt")
            after = set(self._tmp_leftovers())
        finally:
            summarize.MODEL_TIMEOUT = original_timeout
        self.assertIsNone(body)
        self.assertEqual(after - before, set())


class TestDailyFallbackPath(SummarizeTestCase):
    def test_no_model_skips_model_even_when_available(self):
        # 白天每小时毛坯更新：--no-model 必须完全绕开模型调用，
        # 否则每小时一次的定时任务会产生模型成本。
        import summarize
        import worklog
        when = datetime(2026, 9, 11, 19, 2)
        worklog.append_record(when, "repo-a", "做了某事", "claude")

        def should_not_run(prompt):
            raise AssertionError("--no-model 不该调用模型")

        original = summarize.run_model
        summarize.run_model = should_not_run
        os.environ["WORKLOG_SKIP_COLLECT"] = "1"
        try:
            code = summarize.daily(when, no_model=True)
        finally:
            summarize.run_model = original
            os.environ.pop("WORKLOG_SKIP_COLLECT", None)
        self.assertEqual(code, 0)
        text = (worklog.report_dir() / "2026-09-11.md").read_text(encoding="utf-8")
        self.assertIn("做了某事", text)

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

    def test_all_unknown_yields_note_without_table(self):
        import summarize
        rows = [("09:00", "repo", "标题", "claude", None)]
        text = summarize.hours_table(rows)
        self.assertNotIn("## 工时", text)
        self.assertIn("1 条记录无时长，未计入。", text)

    def test_no_rows_at_all_yields_empty_string(self):
        import summarize
        self.assertEqual(summarize.hours_table([]), "")

    def test_grand_total_equals_sum_of_rounded_per_project_rows(self):
        """Regression: displayed total must equal visible per-project sum.

        Before fix: two projects with 20min (1200s) and 25min (1500s) would render
        as 0.3 and 0.4 (visible sum 0.7), but the grand total would be 0.8
        (rounded from unrounded 0.75h), causing a visible discrepancy.

        After fix: grand total must be the sum of displayed rounded per-project values.
        """
        import summarize
        rows = [
            ("10:00", "project-a", "task 1", "claude", 1200),   # 20 min = 0.3333h → 0.3
            ("10:20", "project-b", "task 2", "claude", 1500),   # 25 min = 0.4166h → 0.4
        ]
        text = summarize.hours_table(rows)
        # Each project rounds independently for display
        self.assertIn("| project-a | 0.3 |", text)
        self.assertIn("| project-b | 0.4 |", text)
        # Grand total must be the sum of those displayed rounded values: 0.3 + 0.4 = 0.7
        self.assertIn("**0.7**", text)


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


TAPD_MD = """# TAPD 我的任务同步

## 进行中（1）

| 类型 | ID | 标题 |
|---|---|---|
| 需求 | [111](https://www.tapd.cn/x/stories/view/111) | 【广告系统】AI 智能投放 |
"""


class TapdSummarizeTestCase(SummarizeTestCase):
    def write_tasks(self, content=TAPD_MD):
        import tapd
        tapd.tasks_path().parent.mkdir(parents=True, exist_ok=True)
        tapd.tasks_path().write_text(content, encoding="utf-8")


class TestTapdBlock(TapdSummarizeTestCase):
    def test_no_tasks_file_leaves_prompt_untouched(self):
        import summarize
        prompt = summarize.build_daily_prompt(ROWS, datetime(2026, 9, 11))
        self.assertNotIn("TAPD", prompt)

    def test_candidates_and_rules_in_prompt(self):
        import summarize
        self.write_tasks()
        prompt = summarize.build_daily_prompt(ROWS, datetime(2026, 9, 11))
        self.assertIn("111\t【广告系统】AI 智能投放", prompt)
        self.assertIn("（关联：<ID>）", prompt)
        self.assertIn("TAPD_PENDING", prompt)

    def test_confirmed_and_rejected_links_injected(self):
        import summarize
        import tapd
        self.write_tasks()
        tapd.write_links({"投放策略联调": "111", "杂事": ""})
        prompt = summarize.build_daily_prompt(ROWS, datetime(2026, 9, 11))
        self.assertIn("投放策略联调 → 111", prompt)
        self.assertIn("杂事", prompt)
        self.assertIn(summarize.TAPD_REJECTED_HEADER, prompt)


class TestApplyTapd(TapdSummarizeTestCase):
    def test_annotation_expanded_pending_queued_footer_added(self):
        import summarize
        import tapd
        self.write_tasks()
        body = (
            "# 2026-10-15 周四\n\n"
            "## repo-a\n"
            "- 投放策略联调（关联：111）\n"
            "- 神秘事项\n\n"
            "<!-- TAPD_PENDING: 神秘事项 | 111 -->\n"
        )
        result = summarize.apply_tapd(body, "daily 2026-10-15")
        self.assertIn("（关联需求：【广告系统】AI 智能投放 [111]）", result)
        self.assertNotIn("TAPD_PENDING", result)
        self.assertIn("1 条需求关联待确认", result)
        pending = tapd.read_pending()
        self.assertEqual(pending["daily 2026-10-15"][0]["item"], "神秘事项")

    def test_no_pending_no_footer(self):
        import summarize
        self.write_tasks()
        result = summarize.apply_tapd("## repo-a\n- 做了某事", "daily 2026-10-15")
        self.assertNotIn("待确认", result)

    def test_decided_items_never_reenter_pending(self):
        # 模型不保证记得注入的确认结论；apply_tapd 必须确定性拦下已有结论的条目，
        # 否则 --link 确认完一次，下次重建又把它挂回待确认。
        import summarize
        import tapd
        self.write_tasks()
        tapd.write_links({"神秘事项": "111", "否决项": ""})
        body = "## repo-a\n- 神秘事项\n- 否决项\n<!-- TAPD_PENDING: 神秘事项 | 111 -->\n<!-- TAPD_PENDING: 否决项 | 111 -->"
        summarize.apply_tapd(body, "daily 2026-10-15")
        self.assertEqual(tapd.read_pending(), {})

    def test_no_tasks_file_is_passthrough(self):
        import summarize
        body = "## repo-a\n- 做了某事（关联：111）\n<!-- TAPD_PENDING: x | 111 -->"
        self.assertEqual(summarize.apply_tapd(body, "daily 2026-10-15"), body)


class TestLogRecord(SummarizeTestCase):
    def test_writes_manual_record_with_duration(self):
        import summarize
        import worklog
        code = summarize.log_record("手动部署到生产", 30, "ad-platform")
        self.assertEqual(code, 0)
        rows = worklog.read_day(datetime.now())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "ad-platform")
        self.assertEqual(rows[0][2], "手动部署到生产")
        self.assertEqual(rows[0][3], "manual")
        self.assertEqual(rows[0][4], 1800)

    def test_minutes_omitted_yields_unknown_duration(self):
        import summarize
        import worklog
        summarize.log_record("手动填了 TAPD 工时", None, None)
        rows = worklog.read_day(datetime.now())
        self.assertIsNone(rows[0][4])

    def test_empty_title_rejected(self):
        import summarize
        import worklog
        self.assertEqual(summarize.log_record("   ", 5, None), 1)
        self.assertEqual(worklog.read_day(datetime.now()), [])


class TestLinkMode(TapdSummarizeTestCase):
    def test_confirm_choice_writes_link_clears_pending_rebuilds(self):
        import builtins
        import summarize
        import tapd
        import worklog

        self.write_tasks()
        worklog.append_record(datetime(2026, 10, 15, 10, 0), "repo-a", "做了某事", "claude")
        tapd.add_pending("daily 2026-10-15", [{"item": "做了某事", "candidates": ["111"]}])
        os.environ["WORKLOG_OPENCODE"] = "/nonexistent/opencode"
        os.environ["WORKLOG_SKIP_COLLECT"] = "1"
        original_input = builtins.input
        builtins.input = lambda prompt="": "1"
        try:
            code = summarize.link_mode()
        finally:
            builtins.input = original_input
            os.environ.pop("WORKLOG_OPENCODE", None)
            os.environ.pop("WORKLOG_SKIP_COLLECT", None)
        self.assertEqual(code, 0)
        self.assertEqual(tapd.read_links(), {"做了某事": "111"})
        self.assertEqual(tapd.read_pending(), {})
        # 受影响 scope 已自动重建（模型不可用时退化为分组渲染，文件存在即可）
        self.assertTrue((worklog.report_dir() / "2026-10-15.md").exists())

    def test_zero_marks_rejection(self):
        import builtins
        import summarize
        import tapd
        import worklog

        self.write_tasks()
        worklog.append_record(datetime(2026, 10, 15, 10, 0), "repo-a", "杂事", "claude")
        tapd.add_pending("daily 2026-10-15", [{"item": "杂事", "candidates": ["111"]}])
        os.environ["WORKLOG_SKIP_COLLECT"] = "1"
        original_input = builtins.input
        builtins.input = lambda prompt="": "0"
        try:
            summarize.link_mode()
        finally:
            builtins.input = original_input
            os.environ.pop("WORKLOG_SKIP_COLLECT", None)
        self.assertEqual(tapd.read_links(), {"杂事": ""})
        self.assertEqual(tapd.read_pending(), {})

    def test_empty_pending_is_noop(self):
        import summarize
        self.assertEqual(summarize.link_mode(), 0)



if __name__ == "__main__":
    unittest.main()
