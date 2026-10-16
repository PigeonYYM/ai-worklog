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
        plist = install.render_plist("com.worklog.daily", "--daily", config,
                                     self.script_dir, self.home)
        self.assertEqual(plist["ProgramArguments"],
                         ["/usr/bin/python3", "/repo/ai-worklog/summarize.py", "--daily"])
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 21, "Minute": 7})
        env = plist["EnvironmentVariables"]
        self.assertEqual(env["WORKLOG_HOME"], str(self.home / "daily-report"))
        self.assertEqual(env["WORKLOG_MODEL"], "kimi-for-coding/k3")
        self.assertNotIn("WORKLOG_TAPD", env)

    def test_weekly_plist_has_weekday(self):
        plist = install.render_plist("com.worklog.weekly", "--weekly", self.make_config(),
                                     self.script_dir, self.home)
        self.assertEqual(plist["StartCalendarInterval"]["Weekday"], 5)

    def test_tapd_off_writes_env(self):
        plist = install.render_plist("com.worklog.daily", "--daily",
                                     self.make_config(tapd_enabled=False),
                                     self.script_dir, self.home)
        self.assertEqual(plist["EnvironmentVariables"]["WORKLOG_TAPD"], "off")


class TestCollectConfig(InstallTestCase):
    def make_args(self, **overrides):
        import argparse
        base = dict(yes=True, data_dir=None, model=None, daily_time=None,
                    weekly_time=None, weekly_day=None, claude_level=None,
                    no_tapd=False, uninstall=False, action=None)
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_yes_mode_uses_defaults(self):
        config = install.collect_config(self.make_args(), input_fn=lambda p: "")
        self.assertEqual(config.daily_time, (21, 7))
        self.assertEqual(config.weekly_day, 5)
        self.assertEqual(config.claude_level, "detailed")
        self.assertTrue(config.tapd_enabled)

    def test_interactive_answers(self):
        answers = iter(["claude", "balanced", "/tmp/data", "my-model", "22:00", "20:30", "1", "n"])
        args = self.make_args(yes=False)
        config = install.collect_config(args, input_fn=lambda p: next(answers))
        self.assertEqual(config.tools, ["claude"])
        self.assertEqual(config.claude_level, "balanced")
        self.assertEqual(config.data_dir, Path("/tmp/data"))
        self.assertEqual(config.model, "my-model")
        self.assertEqual(config.daily_time, (22, 0))
        self.assertEqual(config.weekly_day, 1)
        self.assertFalse(config.tapd_enabled)

    def test_bad_time_rejected(self):
        args = self.make_args(daily_time="25:00")
        with self.assertRaises(ValueError):
            install.collect_config(args, input_fn=lambda p: "")


if __name__ == "__main__":
    unittest.main()
