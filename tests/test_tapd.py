import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TAPD_MD = """# TAPD 我的任务同步

## 进行中（2）

| 类型 | ID | 标题 | 状态 |
|---|---|---|---|
| 需求 | [111](https://www.tapd.cn/x/stories/view/111) | 【广告系统】AI 智能投放 | 开发中 |
| 任务 | [222](https://www.tapd.cn/x/prong/tasks/view/222) | 账号管家功能实现 | 进行中 |

## 待处理（1）

| 类型 | ID | 标题 | 状态 |
|---|---|---|---|
| 需求 | [333](https://www.tapd.cn/x/stories/view/333) | 素材报表补充GG指标 | 规划中 |

## 已完成（1）

| 类型 | ID | 标题 | 状态 |
|---|---|---|---|
| 需求 | [444](https://www.tapd.cn/x/stories/view/444) | 渠道子包权限控制 | 已上线 |

## 其他（1）

| 类型 | ID | 标题 | 状态 |
|---|---|---|---|
| 需求 | [555](https://www.tapd.cn/x/stories/view/555) | 业务学习 | 需求取消 |
"""


class TapdTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["WORKLOG_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("WORKLOG_HOME", None)
        self.tmp.cleanup()

    def write_tasks(self, content=TAPD_MD):
        import tapd
        tapd.tasks_path().parent.mkdir(parents=True, exist_ok=True)
        tapd.tasks_path().write_text(content, encoding="utf-8")


class TestLoadCandidates(TapdTestCase):
    def test_missing_file_returns_empty(self):
        import tapd
        self.assertEqual(tapd.load_candidates(), [])

    def test_only_active_groups_are_candidates(self):
        import tapd
        self.write_tasks()
        tasks = tapd.load_candidates()
        ids = [task["id"] for task in tasks]
        self.assertEqual(ids, ["111", "222", "333"])

    def test_extracts_id_and_title_from_link_row(self):
        import tapd
        self.write_tasks()
        tasks = tapd.load_candidates()
        self.assertEqual(tasks[0]["title"], "【广告系统】AI 智能投放")
        self.assertEqual(tasks[0]["kind"], "需求")


class TestPending(TapdTestCase):
    def test_add_and_read_roundtrip(self):
        import tapd
        tapd.add_pending("daily 2026-10-15", [{"item": "做了某事", "candidates": ["111"]}])
        pending = tapd.read_pending()
        self.assertEqual(pending["daily 2026-10-15"][0]["item"], "做了某事")
        self.assertEqual(pending["daily 2026-10-15"][0]["candidates"], ["111"])

    def test_dedupes_by_scope_and_item(self):
        import tapd
        entry = {"item": "做了某事", "candidates": ["111"]}
        tapd.add_pending("daily 2026-10-15", [entry])
        tapd.add_pending("daily 2026-10-15", [dict(entry)])
        self.assertEqual(len(tapd.read_pending()["daily 2026-10-15"]), 1)

    def test_corrupt_file_reads_as_empty(self):
        import tapd
        tapd.pending_path().parent.mkdir(parents=True, exist_ok=True)
        tapd.pending_path().write_text("不是 json", encoding="utf-8")
        self.assertEqual(tapd.read_pending(), {})


class TestLinks(TapdTestCase):
    def test_roundtrip_including_rejection(self):
        import tapd
        links = {"做了某事": "111", "另一件事": ""}
        tapd.write_links(links)
        self.assertEqual(tapd.read_links(), links)


class TestExtractPending(TapdTestCase):
    def test_extracts_and_removes_comment(self):
        import tapd
        body = "# 日报\n\n- 做了某事\n\n<!-- TAPD_PENDING: 做了某事 | 111,222 -->\n"
        cleaned, found = tapd.extract_pending(body)
        self.assertNotIn("TAPD_PENDING", cleaned)
        self.assertIn("- 做了某事", cleaned)
        self.assertEqual(found, [{"item": "做了某事", "candidates": ["111", "222"]}])

    def test_no_comment_returns_body_unchanged(self):
        import tapd
        cleaned, found = tapd.extract_pending("# 日报\n- 普通条目\n")
        self.assertEqual(found, [])
        self.assertIn("普通条目", cleaned)


class TestExpandAnnotations(TapdTestCase):
    def test_valid_id_expands_with_title(self):
        import tapd
        tasks = [{"id": "111", "title": "【广告系统】AI 智能投放", "kind": "需求"}]
        body = "- 投放策略联调（关联：111）"
        self.assertEqual(
            tapd.expand_annotations(body, tasks),
            "- 投放策略联调（关联需求：【广告系统】AI 智能投放 [111]）",
        )

    def test_unknown_id_is_stripped(self):
        import tapd
        tasks = [{"id": "111", "title": "某需求", "kind": "需求"}]
        body = "- 做了某事（关联：999）"
        self.assertEqual(tapd.expand_annotations(body, tasks), "- 做了某事")


TASKS = [
    {"id": "111", "title": "【Trickcal】- 广告投放pLTV预测并回传-中R和大R模型", "kind": "需求"},
    {"id": "222", "title": "【三谋韩国】- 广告平台接入GMO渠道的api", "kind": "需求"},
    {"id": "333", "title": "【广告系统】AI 智能投放", "kind": "需求"},
]


class TestSuggest(TapdTestCase):
    def test_tag_match_wins(self):
        import tapd
        self.assertEqual(tapd.suggest("Trickcal pLTV 事件联调", []), [])
        self.assertEqual(
            tapd.suggest("【Trickcal】回传联调", TASKS), ["111"]
        )

    def test_long_common_substring_unique(self):
        import tapd
        self.assertEqual(
            tapd.suggest("三国韩服 S2S 离线回传事件洗数", TASKS + [
                {"id": "x", "title": "无关需求", "kind": "需求"},
            ]),
            [],
        )
        self.assertEqual(
            tapd.suggest("广告平台接入GMO渠道联调", TASKS), ["222"]
        )

    def test_short_overlap_is_not_evidence(self):
        import tapd
        self.assertEqual(tapd.suggest("广告日常维护", TASKS), [])  # 「广告」只有 2 字重合

    def test_tie_returns_nothing(self):
        import tapd
        dup = TASKS + [{"id": "999", "title": "【Trickcal】另一条", "kind": "需求"}]
        self.assertEqual(tapd.suggest("【Trickcal】联调", dup), [])
        same_lcs = TASKS + [{"id": "888", "title": "【其他】广告投放优化", "kind": "需求"}]
        self.assertEqual(tapd.suggest("广告投放联调", same_lcs), [])  # 「广告投放」撞两条


class TestDraftAnnotation(TapdTestCase):
    def test_confirmed_link_takes_precedence(self):
        import tapd
        links = {"模型压缩后的条目文本": "111"}
        result = tapd.draft_annotation("模型压缩后的条目文本（阶段一）", TASKS, links)
        self.assertIn("关联需求", result)
        self.assertIn("111", result)

    def test_guess_has_question_mark(self):
        import tapd
        result = tapd.draft_annotation("广告平台接入GMO渠道联调", TASKS, {})
        self.assertIn("（关联？：", result)
        self.assertIn("222", result)

    def test_no_match_no_annotation(self):
        import tapd
        self.assertEqual(tapd.draft_annotation("完全无关的事情", TASKS, {}), "")

    def test_rejected_link_is_not_applied(self):
        import tapd
        links = {"广告平台接入GMO渠道联调": ""}  # 用户确认过无关联
        result = tapd.draft_annotation("广告平台接入GMO渠道联调", TASKS, links)
        # 已否决的条目不该再贴猜测标注
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
