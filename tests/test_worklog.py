import contextlib
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class WorklogTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["WORKLOG_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("WORKLOG_HOME", None)
        self.tmp.cleanup()


class TestClean(WorklogTestCase):
    def test_collapses_tabs_and_newlines(self):
        import worklog
        self.assertEqual(worklog.clean("a\tb\nc"), "a b c")

    def test_collapses_repeated_spaces(self):
        import worklog
        self.assertEqual(worklog.clean("a   b"), "a b")

    def test_accepts_non_string(self):
        import worklog
        self.assertEqual(worklog.clean(None), "None")


class TestProjectName(WorklogTestCase):
    def test_takes_basename(self):
        import worklog
        self.assertEqual(worklog.project_name("/a/b/my-repo"), "my-repo")

    def test_falls_back_on_empty(self):
        import worklog
        self.assertEqual(worklog.project_name(""), "-")

    def test_falls_back_on_none(self):
        import worklog
        self.assertEqual(worklog.project_name(None), "-")

    def test_falls_back_on_root(self):
        import worklog
        self.assertEqual(worklog.project_name("/"), "-")


class TestAppendAndRead(WorklogTestCase):
    def test_roundtrip(self):
        import worklog
        when = datetime(2026, 9, 11, 19, 2)
        worklog.append_record(when, "my-repo", "做了某事", "claude")
        rows = worklog.read_day(when)
        self.assertEqual(rows, [("19:02", "my-repo", "做了某事", "claude", None)])

    def test_title_with_tab_does_not_break_columns(self):
        import worklog
        when = datetime(2026, 9, 11, 19, 2)
        worklog.append_record(when, "my-repo", "标题\t含制表符", "codex")
        rows = worklog.read_day(when)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], "标题 含制表符")

    def test_read_missing_day_returns_empty(self):
        import worklog
        self.assertEqual(worklog.read_day(datetime(2020, 1, 1)), [])

    def test_writes_into_day_of_given_timestamp(self):
        import worklog
        worklog.append_record(datetime(2026, 9, 10, 8, 0), "r", "昨天的事", "opencode")
        worklog.append_record(datetime(2026, 9, 11, 8, 0), "r", "今天的事", "opencode")
        self.assertEqual(len(worklog.read_day(datetime(2026, 9, 10))), 1)
        self.assertEqual(len(worklog.read_day(datetime(2026, 9, 11))), 1)


class TestMigrate(WorklogTestCase):
    def _write_md(self, dirpath, day_str, lines):
        path = Path(dirpath) / (day_str + ".md")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_reconstructs_timestamp_from_filename_not_clock(self):
        import migrate_legacy
        import worklog
        # 用一个明显不等于「今天」的日期，证明写入的日期/时间来自文件名与 md
        # 行内容，而非运行测试时的系统时钟。
        day_str = "2024-03-04"
        with tempfile.TemporaryDirectory() as src:
            md_path = self._write_md(src, day_str, [
                "# 2024-03-04 周一",
                "",
                "- 17:41 [ad-platform-frontend] 配置周报 hook",
                "- 19:02 [repo] 阶段B2：Properties 构建",
            ])
            count = migrate_legacy.migrate(md_path)

        self.assertEqual(count, 2)
        rows = worklog.read_day(datetime(2024, 3, 4))
        self.assertEqual(
            rows,
            [
                ("17:41", "ad-platform-frontend", "配置周报 hook", "claude", None),
                ("19:02", "repo", "阶段B2：Properties 构建", "claude", None),
            ],
        )

    def test_returned_count_matches_source_record_lines(self):
        import migrate_legacy
        day_str = "2026-09-11"
        lines = [
            "# 2026-09-11 周五",
            "",
            "- 17:41 [repo-a] 事情一",
            "- 18:00 [repo-b] 事情二",
            "- 20:04 [repo-c] 事情三",
        ]
        with tempfile.TemporaryDirectory() as src:
            md_path = self._write_md(src, day_str, lines)
            count = migrate_legacy.migrate(md_path)

        record_line_count = len(
            [l for l in lines if l.startswith("- ") and ":" in l[2:7] and "[" in l]
        )
        self.assertEqual(record_line_count, 3)
        self.assertEqual(count, record_line_count)

    def test_second_migrate_on_populated_target_is_rejected_and_does_not_grow_tsv(self):
        import migrate_legacy
        import worklog
        day_str = "2026-09-11"
        with tempfile.TemporaryDirectory() as src:
            md_path = self._write_md(src, day_str, [
                "- 17:41 [repo] 事情一",
                "- 18:00 [repo] 事情二",
            ])
            first_count = migrate_legacy.migrate(md_path)
            self.assertEqual(first_count, 2)

            target = worklog.raw_path(datetime(2026, 9, 11))
            lines_after_first = target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines_after_first), 2)

            with contextlib.redirect_stdout(io.StringIO()):
                second_count = migrate_legacy.migrate(md_path)

        self.assertEqual(second_count, -1)
        lines_after_second = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines_after_second, lines_after_first)
        self.assertEqual(len(lines_after_second), 2)


class TestParseLegacy(WorklogTestCase):
    def test_parses_record_line(self):
        import migrate_legacy
        rows = migrate_legacy.parse_legacy("- 17:41 [ad-platform-frontend] 配置周报 hook")
        self.assertEqual(rows, [("17:41", "ad-platform-frontend", "配置周报 hook")])

    def test_skips_header_and_blank(self):
        import migrate_legacy
        text = "# 2026-09-11 周五\n\n- 19:02 [repo] 阶段B2：Properties 构建\n"
        rows = migrate_legacy.parse_legacy(text)
        self.assertEqual(len(rows), 1)

    def test_keeps_brackets_inside_title(self):
        import migrate_legacy
        rows = migrate_legacy.parse_legacy("- 09:05 [repo] 配置 hook（TaskCompleted → ~/daily-report）")
        self.assertEqual(rows[0][2], "配置 hook（TaskCompleted → ~/daily-report）")


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


if __name__ == "__main__":
    unittest.main()
