import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import install


class InstallTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.script_dir = Path("/repo/ai-worklog")

    def tearDown(self):
        self.tmp.cleanup()

    def claude_settings(self):
        return self.home / ".claude" / "settings.json"

    def codex_hooks(self):
        return self.home / ".codex" / "hooks.json"

    def write_foreign_hooks(self):
        """预置含他人 hook 的配置，模拟使用者已有的环境。"""
        self.claude_settings().parent.mkdir(parents=True, exist_ok=True)
        self.claude_settings().write_text(json.dumps({
            "hooks": {
                "Stop": [{"hooks": [{"command": "node /other/hook.mjs", "type": "command"}]}],
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "rtk hook claude", "type": "command"}]}],
            },
            "env": {"KEEP": "me"},
        }), encoding="utf-8")
        self.codex_hooks().parent.mkdir(parents=True, exist_ok=True)
        self.codex_hooks().write_text(json.dumps({
            "hooks": {
                "Stop": [{"hooks": [{"command": "node /other/hook.mjs", "type": "command"}]}],
            },
        }), encoding="utf-8")

    def make_config(self, **overrides):
        base = dict(
            data_dir=self.home / "daily-report", model="", daily_time=(21, 7),
            weekly_time=(21, 20), weekly_day=5, tools=["claude", "codex"],
            claude_level="detailed", tapd_enabled=True,
        )
        base.update(overrides)
        return install.Config(**base)

    def commands(self, path, event):
        data = json.loads(path.read_text(encoding="utf-8"))
        return [
            h["command"]
            for group in data["hooks"].get(event, [])
            for h in group["hooks"]
        ]


class TestMergeHooks(InstallTestCase):
    def test_preserves_foreign_hooks_and_settings(self):
        self.write_foreign_hooks()
        install.install(self.make_config(), self.home, load_launchd=False, out=lambda *a: None)
        data = json.loads(self.claude_settings().read_text(encoding="utf-8"))
        self.assertEqual(data["env"], {"KEEP": "me"})
        self.assertIn("rtk hook claude", self.commands(self.claude_settings(), "PreToolUse")[0])
        self.assertTrue(any("node /other/hook.mjs" in c for c in self.commands(self.claude_settings(), "Stop")))

    def test_registers_claude_events_per_level(self):
        self.write_foreign_hooks()
        install.install(self.make_config(), self.home, load_launchd=False, out=lambda *a: None)
        self.assertTrue(any("claude_prompt.py" in c for c in self.commands(self.claude_settings(), "UserPromptSubmit")))
        self.assertTrue(any("claude_stop.py" in c for c in self.commands(self.claude_settings(), "Stop")))
        self.assertTrue(any("claude_task.py" in c for c in self.commands(self.claude_settings(), "TaskCompleted")))

    def test_balanced_level_skips_task_completed(self):
        self.write_foreign_hooks()
        install.install(self.make_config(claude_level="balanced"), self.home,
                        load_launchd=False, out=lambda *a: None)
        self.assertTrue(any("claude_stop.py" in c for c in self.commands(self.claude_settings(), "Stop")))
        self.assertEqual(self.commands(self.claude_settings(), "TaskCompleted"), [])

    def test_minimal_level_skips_stop(self):
        self.write_foreign_hooks()
        install.install(self.make_config(claude_level="minimal"), self.home,
                        load_launchd=False, out=lambda *a: None)
        self.assertTrue(any("claude_task.py" in c for c in self.commands(self.claude_settings(), "TaskCompleted")))
        self.assertFalse(any("claude_stop.py" in c for c in self.commands(self.claude_settings(), "Stop")))

    def test_idempotent_on_repeat_install(self):
        self.write_foreign_hooks()
        config = self.make_config()
        for _ in range(2):
            install.install(config, self.home, load_launchd=False, out=lambda *a: None)
        ours = [c for c in self.commands(self.claude_settings(), "Stop") if "claude_stop.py" in c]
        self.assertEqual(len(ours), 1)

    def test_replaces_stale_paths_on_reinstall(self):
        self.write_foreign_hooks()
        data = json.loads(self.claude_settings().read_text(encoding="utf-8"))
        data["hooks"]["Stop"].append({"hooks": [{
            "command": "/usr/bin/python3 '/old/location/claude_stop.py' 2>/dev/null || true",
            "type": "command"}]})
        self.claude_settings().write_text(json.dumps(data), encoding="utf-8")
        install.install(self.make_config(), self.home, load_launchd=False, out=lambda *a: None)
        ours = [c for c in self.commands(self.claude_settings(), "Stop") if "claude_stop.py" in c]
        self.assertEqual(len(ours), 1)
        self.assertNotIn("/old/location/", ours[0])

    def test_missing_settings_file_is_created(self):
        install.install(self.make_config(), self.home, load_launchd=False, out=lambda *a: None)
        self.assertTrue(self.claude_settings().exists())
        self.assertTrue(any("claude_stop.py" in c for c in self.commands(self.claude_settings(), "Stop")))

    def test_backup_written_before_change(self):
        self.write_foreign_hooks()
        install.install(self.make_config(), self.home, load_launchd=False, out=lambda *a: None)
        backup = Path(str(self.claude_settings()) + ".worklog-bak")
        self.assertTrue(backup.exists())
        self.assertIn("node /other/hook.mjs", backup.read_text(encoding="utf-8"))

    def test_codex_hooks_registered_without_async(self):
        self.write_foreign_hooks()
        install.install(self.make_config(), self.home, load_launchd=False, out=lambda *a: None)
        data = json.loads(self.codex_hooks().read_text(encoding="utf-8"))
        ours = [h for g in data["hooks"]["Stop"] for h in g["hooks"] if "codex_stop.py" in h["command"]]
        self.assertEqual(len(ours), 1)
        self.assertNotIn("async", ours[0])


class TestUninstall(InstallTestCase):
    def test_removes_only_ours(self):
        self.write_foreign_hooks()
        install.install(self.make_config(), self.home, load_launchd=False, out=lambda *a: None)
        install.uninstall(self.home, out=lambda *a: None)
        data = json.loads(self.claude_settings().read_text(encoding="utf-8"))
        all_commands = [c for ev in data["hooks"].values() for g in ev for c in
                        [h["command"] for h in g["hooks"]]]
        self.assertFalse(any("ai-worklog" in c or "claude_stop.py" in c for c in all_commands))
        self.assertTrue(any("node /other/hook.mjs" in c for c in all_commands))
        self.assertEqual(data["env"], {"KEEP": "me"})


class TestRenderPlist(InstallTestCase):
    def test_daily_plist_contents(self):
        config = self.make_config(model="kimi-for-coding/k3")
        plist = install.render_plist("com.worklog.daily", ["--daily"],
                                     {"Hour": 21, "Minute": 7}, config,
                                     self.script_dir, self.home)
        self.assertEqual(plist["ProgramArguments"],
                         ["/usr/bin/python3", "/repo/ai-worklog/summarize.py", "--daily"])
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 21, "Minute": 7})
        env = plist["EnvironmentVariables"]
        self.assertEqual(env["WORKLOG_HOME"], str(self.home / "daily-report"))
        self.assertEqual(env["WORKLOG_MODEL"], "kimi-for-coding/k3")
        self.assertNotIn("WORKLOG_TAPD", env)

    def test_weekly_job_has_weekday(self):
        jobs = dict((name, (args, interval)) for name, args, interval
                    in install.scheduled_jobs(self.make_config()))
        self.assertEqual(jobs["weekly"][1]["Weekday"], 5)

    def test_hourly_job_uses_no_model(self):
        jobs = dict((name, (args, interval)) for name, args, interval
                    in install.scheduled_jobs(self.make_config()))
        self.assertEqual(jobs["hourly"][0], ["--daily", "--no-model"])
        self.assertEqual(jobs["hourly"][1], {"Minute": 53})

    def test_hourly_job_absent_when_disabled(self):
        names = [name for name, _, _ in install.scheduled_jobs(
            self.make_config(hourly_intraday=False))]
        self.assertNotIn("hourly", names)

    def test_tapd_off_writes_env(self):
        plist = install.render_plist("com.worklog.daily", ["--daily"],
                                     {"Hour": 21, "Minute": 7},
                                     self.make_config(tapd_enabled=False),
                                     self.script_dir, self.home)
        self.assertEqual(plist["EnvironmentVariables"]["WORKLOG_TAPD"], "off")


class TestCollectConfig(InstallTestCase):
    def make_args(self, **overrides):
        import argparse
        base = dict(yes=True, data_dir=None, model=None, daily_time=None,
                    weekly_time=None, weekly_day=None, claude_level=None,
                    no_tapd=False, no_hourly=False, tools=None, runner=None, uninstall=False,
                    action=None)
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_yes_mode_uses_defaults(self):
        config = install.collect_config(self.make_args(), input_fn=lambda p: "")
        self.assertEqual(config.daily_time, (21, 0))
        self.assertEqual(config.weekly_day, 5)
        self.assertEqual(config.claude_level, "detailed")
        self.assertTrue(config.tapd_enabled)

    def test_interactive_answers(self):
        answers = iter(["1", "2", "2", "/tmp/data", "1", "2", "my-model",
                        "4", "22:00", "4", "20:30", "4", "1", "2", "2"])
        args = self.make_args(yes=False)
        original_models, original_current = install.detect_models, install.detect_current_model
        install.detect_models = lambda: []
        install.detect_current_model = lambda: ""
        try:
            config = install.collect_config(args, input_fn=lambda p: next(answers))
        finally:
            install.detect_models = original_models
            install.detect_current_model = original_current
        self.assertEqual(config.tools, ["claude"])
        self.assertEqual(config.claude_level, "balanced")
        self.assertEqual(config.data_dir, Path("/tmp/data"))
        self.assertEqual(config.model, "my-model")
        self.assertEqual(config.daily_time, (22, 0))
        self.assertEqual(config.weekly_day, 1)
        self.assertFalse(config.tapd_enabled)
        self.assertFalse(config.hourly_intraday)

    def test_interactive_can_pick_detected_model(self):
        # 模型选项来自本机 opencode 已配置模型：第 2 项即探测到的第一个模型
        answers = iter(["1", "1", "1", "1", "2", "1", "1", "1", "1", "1"])
        args = self.make_args(yes=False)
        original_models, original_current = install.detect_models, install.detect_current_model
        install.detect_models = lambda: ["kimi-code-plan-cn/k3"]
        install.detect_current_model = lambda: ""
        try:
            config = install.collect_config(args, input_fn=lambda p: next(answers))
        finally:
            install.detect_models = original_models
            install.detect_current_model = original_current
        self.assertEqual(config.model, "kimi-code-plan-cn/k3")

    def test_current_model_is_default_choice(self):
        # 模型步骤直接回车（取默认）应选中 opencode 当前正在使用的模型
        answers = iter(["1", "1", "1", "1", "", "1", "1", "1", "1", "1"])
        args = self.make_args(yes=False)
        original_models, original_current = install.detect_models, install.detect_current_model
        install.detect_models = lambda: ["other-provider/m1"]
        install.detect_current_model = lambda: "kimi-code-plan-cn/k3"
        try:
            config = install.collect_config(args, input_fn=lambda p: next(answers))
        finally:
            install.detect_models = original_models
            install.detect_current_model = original_current
        self.assertEqual(config.model, "kimi-code-plan-cn/k3")

    def test_detect_current_model_reads_recent_first(self):
        home = Path(self.tmp.name)
        state = home / ".local" / "state" / "opencode"
        state.mkdir(parents=True)
        (state / "model.json").write_text(json.dumps({
            "recent": [{"providerID": "kimi-code-plan-cn", "modelID": "k3"}],
        }), encoding="utf-8")
        self.assertEqual(install.detect_current_model(home), "kimi-code-plan-cn/k3")

    def test_detect_current_model_missing_file_returns_empty(self):
        self.assertEqual(install.detect_current_model(Path(self.tmp.name)), "")

    def test_detect_models_parses_opencode_output(self):
        import subprocess as sp
        original_run = install.subprocess.run
        original_which = install.shutil.which

        class FakeResult:
            returncode = 0
            stdout = "opencode/beam-free\nkimi-code-plan-cn/k3\n\n"

        install.shutil.which = lambda name: "/fake/opencode"
        install.subprocess.run = lambda *a, **kw: FakeResult()
        try:
            self.assertEqual(install.detect_models(),
                             ["opencode/beam-free", "kimi-code-plan-cn/k3"])
        finally:
            install.subprocess.run = original_run
            install.shutil.which = original_which

    def test_detect_models_failure_returns_empty(self):
        original_which = install.shutil.which
        install.shutil.which = lambda name: None
        try:
            self.assertEqual(install.detect_models(), [])
        finally:
            install.shutil.which = original_which

    def test_no_model_runner_skips_model_question(self):
        # opencode/claude 都没装：模型选择无意义，应跳过该问题并提示
        answers = iter(["1", "1", "1", "1", "1", "1"])  # 数据目录/日报/周报时间/周几/TAPD/hourly
        args = self.make_args(yes=False)
        original = install.shutil.which
        install.shutil.which = lambda name: None
        try:
            config = install.collect_config(args, input_fn=lambda p: next(answers))
        finally:
            install.shutil.which = original
        self.assertEqual(config.tools, [])
        self.assertEqual(config.model, "")

    def test_runner_choice_claude_skips_opencode_model_list(self):
        # 用户选 claude 作后端：不该再去读 opencode 的模型列表
        answers = iter(["1", "1", "1", "2", "1", "1", "1", "1", "1", "1"])
        args = self.make_args(yes=False)

        def forbid_detect():
            raise AssertionError("选了 claude 后端不该读取 opencode 模型列表")

        original = install.detect_models
        install.detect_models = forbid_detect
        try:
            config = install.collect_config(args, input_fn=lambda p: next(answers))
        finally:
            install.detect_models = original
        self.assertEqual(config.runner, "claude")
        self.assertEqual(config.model, "")

    def test_runner_written_to_plist_env(self):
        plist = install.render_plist("com.worklog.daily", ["--daily"],
                                     {"Hour": 21, "Minute": 7},
                                     self.make_config(runner="claude"),
                                     self.script_dir, self.home)
        self.assertEqual(plist["EnvironmentVariables"]["WORKLOG_RUNNER"], "claude")

    def test_bad_time_rejected(self):
        args = self.make_args(daily_time="25:00")
        with self.assertRaises(ValueError):
            install.collect_config(args, input_fn=lambda p: "")

    def test_tools_flag_limits_registration(self):
        config = install.collect_config(self.make_args(tools="claude"), input_fn=lambda p: "")
        self.assertEqual(config.tools, ["claude"])

    def test_tools_flag_rejects_unknown(self):
        with self.assertRaises(ValueError):
            install.collect_config(self.make_args(tools="gemini"), input_fn=lambda p: "")


class TestNoClaudeHooks(InstallTestCase):
    def test_skips_claude_settings_but_keeps_codex(self):
        # Claude 插件市场安装的路径：hooks 由插件注册，install.py 只补 codex 与定时任务
        self.write_foreign_hooks()
        install.install(self.make_config(), self.home, load_launchd=False,
                        register_claude=False, out=lambda *a: None)
        self.assertFalse(any("claude_stop.py" in c for c in self.commands(self.claude_settings(), "Stop")))
        self.assertTrue(any("codex_stop.py" in c for c in self.commands(self.codex_hooks(), "Stop")))


class TestEphemeralRuntime(InstallTestCase):
    def test_detects_npx_and_plugin_caches(self):
        self.assertTrue(install.is_ephemeral(Path("/Users/x/.npm/_npx/abc123/node_modules/ai-worklog")))
        self.assertTrue(install.is_ephemeral(Path("/Users/x/.claude/plugins/cache/m/ai-worklog/1.0.0")))
        self.assertFalse(install.is_ephemeral(Path("/Users/x/worklog")))

    def test_materialize_copies_runtime_and_writes_version(self):
        runtime = self.home / ".ai-worklog"
        install.materialize(runtime, out=lambda *a: None)
        self.assertTrue((runtime / "install.py").exists())
        self.assertTrue((runtime / "worklog.py").exists())
        self.assertIn("from", (runtime / ".version").read_text(encoding="utf-8"))

    def test_main_from_npx_materializes_and_registers_stable_paths(self):
        fake_pkg = self.home / ".npm" / "_npx" / "abc" / "node_modules" / "ai-worklog"
        fake_pkg.mkdir(parents=True)
        (fake_pkg / "dummy.py").write_text("# dummy", encoding="utf-8")
        runtime = self.home / ".ai-worklog"
        original = install.SCRIPT_DIR
        install.SCRIPT_DIR = fake_pkg
        try:
            code = install.main(
                ["--yes", "--skip-launchd", "--runtime-dir", str(runtime)],
                home=self.home, input_fn=lambda p: "",
            )
        finally:
            install.SCRIPT_DIR = original
        self.assertEqual(code, 0)
        self.assertTrue((runtime / "dummy.py").exists())
        ours = [c for c in self.commands(self.claude_settings(), "Stop") if "claude_stop.py" in c]
        self.assertEqual(len(ours), 1)
        self.assertIn(str(runtime), ours[0])
        self.assertNotIn("_npx", ours[0])


if __name__ == "__main__":
    unittest.main()
