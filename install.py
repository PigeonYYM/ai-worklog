#!/usr/bin/env python3
"""ai-worklog 交互式安装 / 更新 / 卸载。

幂等：重复运行安全，所以「更新」就是 `git pull && ./install.py` 再跑一遍。

做的事：
1. 探测本机的 Claude Code / codex / opencode，按用户选择注册 hooks
   （合并进 ~/.claude/settings.json 与 ~/.codex/hooks.json，保留他人条目，
   写前备份为 *.worklog-bak）
2. macOS 下渲染并加载两个 launchd 定时任务（日报/周报）；Linux 打印 crontab 示例
3. 发现旧版 label（com.<用户名>.worklog.*）时自动迁移

卸载（--uninstall）：移除 hooks 注册与定时任务，不动数据目录。
"""
from __future__ import annotations

import argparse
import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

# UserPromptSubmit 始终注册：它负责打时长标记和缓存输入，是另外两档事件的公共地基。
CLAUDE_BASE_HOOKS = {"UserPromptSubmit": "claude_prompt.py"}
# 三档记录粒度，与流水产出量强相关：
#   detailed —— 每个 turn 结束记一条 + 每个任务清单项完成记一条（最密）
#   balanced —— 只在每个 turn 结束记一条（适中）
#   minimal  —— 只在任务清单项完成时记一条（最精简，不用清单的大任务会漏记）
CLAUDE_LEVEL_HOOKS = {
    "detailed": {"Stop": "claude_stop.py", "TaskCompleted": "claude_task.py"},
    "balanced": {"Stop": "claude_stop.py"},
    "minimal": {"TaskCompleted": "claude_task.py"},
}
CODEX_HOOKS = {
    "UserPromptSubmit": "codex_prompt.py",
    "Stop": "codex_stop.py",
}
SCRIPT_NAMES = set(CLAUDE_BASE_HOOKS.values()) | set(CODEX_HOOKS.values()) | {
    script for hooks in CLAUDE_LEVEL_HOOKS.values() for script in hooks.values()
}
LEGACY_LABELS = ("com.pigeonyang.worklog.daily", "com.pigeonyang.worklog.weekly")
PLIST_PATH = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

SCRIPT_DIR = Path(__file__).resolve().parent


class Config:
    def __init__(self, data_dir, model, daily_time, weekly_time, weekly_day, tools,
                 claude_level="detailed", tapd_enabled=True):
        self.data_dir = data_dir          # Path，已展开 ~
        self.model = model                # str，可为空（用 opencode 默认模型）
        self.daily_time = daily_time      # (hour, minute)
        self.weekly_time = weekly_time    # (hour, minute)
        self.weekly_day = weekly_day      # int，launchd Weekday（0/7=周日，5=周五）
        self.tools = tools                # ["claude", "codex", ...]
        self.claude_level = claude_level  # detailed / balanced / minimal
        self.tapd_enabled = tapd_enabled  # 关闭时往 plist 写 WORKLOG_TAPD=off


# ---------- hooks 合并 ----------

def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(str(path), str(path) + ".worklog-bak")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _strip_ours(settings: dict) -> dict:
    """移除所有 command 引用本仓库脚本的 hook 条目，清理空组和空事件。"""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            entries = [
                h for h in group.get("hooks", [])
                if not any(name in str(h.get("command", "")) for name in SCRIPT_NAMES)
            ]
            if entries:
                group["hooks"] = entries
                kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    return settings


def merge_hooks(settings_path: Path, hook_spec: dict, script_dir: Path, async_hooks: bool) -> None:
    """把 hook_spec（事件 -> 脚本名）幂等合并进 settings 文件。"""
    settings = _strip_ours(_load_json(settings_path))
    hooks = settings.setdefault("hooks", {})
    for event, script in hook_spec.items():
        entry = {
            "command": "/usr/bin/python3 '{}' 2>/dev/null || true".format(script_dir / script),
            "type": "command",
        }
        if async_hooks:
            entry = {"async": True, **entry}
        hooks.setdefault(event, []).append({"hooks": [entry]})
    _write_json(settings_path, settings)


def remove_hooks(settings_path: Path) -> None:
    if not settings_path.exists():
        return
    _write_json(settings_path, _strip_ours(_load_json(settings_path)))


# ---------- launchd ----------

def render_plist(label: str, mode: str, config: Config, script_dir: Path, home: Path) -> dict:
    hour, minute = config.daily_time if mode == "--daily" else config.weekly_time
    interval = {"Hour": hour, "Minute": minute}
    if mode == "--weekly":
        interval["Weekday"] = config.weekly_day
    env = {
        "HOME": str(home),
        "PATH": PLIST_PATH,
        "WORKLOG_HOME": str(config.data_dir),
    }
    if config.model:
        env["WORKLOG_MODEL"] = config.model
    if not config.tapd_enabled:
        env["WORKLOG_TAPD"] = "off"
    return {
        "Label": label,
        "ProgramArguments": ["/usr/bin/python3", str(script_dir / "summarize.py"), mode],
        "WorkingDirectory": str(script_dir),
        "StartCalendarInterval": interval,
        "EnvironmentVariables": env,
        "StandardOutPath": str(config.data_dir / ".state" / "{}.log".format(mode[2:])),
        "StandardErrorPath": str(config.data_dir / ".state" / "{}.err".format(mode[2:])),
    }


def install_launchd(config: Config, script_dir: Path, home: Path, out=print) -> None:
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    (config.data_dir / ".state").mkdir(parents=True, exist_ok=True)
    for legacy in LEGACY_LABELS:
        legacy_plist = agents / "{}.plist".format(legacy)
        if legacy_plist.exists():
            subprocess.run(["launchctl", "unload", str(legacy_plist)],
                           capture_output=True)
            legacy_plist.unlink()
            out("已移除旧版定时任务 {}".format(legacy))
    for name, mode in (("daily", "--daily"), ("weekly", "--weekly")):
        label = "com.worklog.{}".format(name)
        target = agents / "{}.plist".format(label)
        with target.open("wb") as handle:
            plistlib.dump(render_plist(label, mode, config, script_dir, home), handle)
        subprocess.run(["launchctl", "unload", str(target)], capture_output=True)
        result = subprocess.run(["launchctl", "load", str(target)], capture_output=True)
        if result.returncode == 0:
            out("已加载 {}".format(label))
        else:
            out("加载 {} 失败：{}".format(label, result.stderr.decode("utf-8", "replace")))


def uninstall_launchd(home: Path, out=print) -> None:
    agents = home / "Library" / "LaunchAgents"
    for name in ("daily", "weekly"):
        target = agents / "com.worklog.{}.plist".format(name)
        if target.exists():
            subprocess.run(["launchctl", "unload", str(target)], capture_output=True)
            target.unlink()
            out("已移除 {}".format(target.name))


def print_cron_hint(config: Config, script_dir: Path, out=print) -> None:
    dh, dm = config.daily_time
    wh, wm = config.weekly_time
    out("非 macOS 系统，请自行把下面两行加入 crontab（crontab -e）：")
    out("  {} {} * * * WORKLOG_HOME={} /usr/bin/python3 {} --daily".format(
        dm, dh, config.data_dir, script_dir / "summarize.py"))
    out("  {} {} * * {} WORKLOG_HOME={} /usr/bin/python3 {} --weekly".format(
        wm, wh, config.weekly_day, config.data_dir, script_dir / "summarize.py"))


# ---------- 交互配置 ----------

def _parse_time(text: str):
    parts = text.strip().split(":")
    if len(parts) != 2:
        raise ValueError(text)
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(text)
    return hour, minute


def _ask(input_fn, prompt: str, default: str) -> str:
    answer = input_fn("{} [{}]: ".format(prompt, default)).strip()
    return answer or default


LEVEL_LABELS = {
    "detailed": "详细 —— 每个 turn + 每个任务清单项都记录（流水最多）",
    "balanced": "适中 —— 每个 turn 记录一条",
    "minimal": "精简 —— 仅任务清单项完成时记录（流水最少）",
}


def collect_config(args, input_fn=input) -> Config:
    detected = []
    for tool in ("claude", "codex", "opencode"):
        if shutil.which(tool):
            detected.append(tool)
    if args.yes:
        tools = list(detected)
    else:
        print("探测到的工具：{}".format("、".join(detected) or "（无）"))
        answer = _ask(input_fn, "为哪些工具注册 hook（逗号分隔）", ",".join(detected))
        tools = [t.strip() for t in answer.split(",") if t.strip() in ("claude", "codex", "opencode")]

    def pick(flag, prompt, default):
        if flag is not None:
            return flag
        if args.yes:
            return default
        return _ask(input_fn, prompt, default)

    claude_level = args.claude_level or "detailed"
    if "claude" in tools and not args.yes and not args.claude_level:
        print("Claude Code 记录粒度：")
        for key in ("detailed", "balanced", "minimal"):
            print("  {}. {}".format(key, LEVEL_LABELS[key]))
        claude_level = _ask(input_fn, "选择粒度（detailed/balanced/minimal）", "detailed")
    if claude_level not in CLAUDE_LEVEL_HOOKS:
        raise ValueError("claude 粒度必须是 {}".format("/".join(CLAUDE_LEVEL_HOOKS)))

    data_dir = Path(pick(args.data_dir, "数据目录（流水/日报/周报）", "~/daily-report")).expanduser()
    model = pick(args.model, "整合模型（留空用 opencode 默认模型）", "")
    daily_time = _parse_time(pick(args.daily_time, "日报生成时间 HH:MM", "21:07"))
    weekly_time = _parse_time(pick(args.weekly_time, "周报生成时间 HH:MM", "21:20"))
    weekly_day = int(pick(args.weekly_day, "周报在周几生成（0=周日 … 5=周五）", "5"))
    if not 0 <= weekly_day <= 7:
        raise ValueError("weekly_day 必须在 0-7 之间")
    if args.no_tapd:
        tapd_enabled = False
    elif args.yes:
        tapd_enabled = True
    else:
        tapd_enabled = _ask(input_fn, "启用 TAPD 需求关联（需要后续放入需求清单文件）？(y/n)", "y").lower() != "n"
    return Config(data_dir, model, daily_time, weekly_time, weekly_day, tools,
                  claude_level, tapd_enabled)


# ---------- 安装 / 卸载 ----------

def install(config: Config, home: Path, load_launchd: bool = True, out=print) -> None:
    if "claude" in config.tools:
        spec = dict(CLAUDE_BASE_HOOKS)
        spec.update(CLAUDE_LEVEL_HOOKS[config.claude_level])
        merge_hooks(home / ".claude" / "settings.json", spec, SCRIPT_DIR, async_hooks=True)
        events = " / ".join(spec)
        out("已注册 Claude Code hooks（{}，粒度 {}）".format(events, config.claude_level))
    if "codex" in config.tools:
        merge_hooks(home / ".codex" / "hooks.json", CODEX_HOOKS, SCRIPT_DIR, async_hooks=False)
        out("已注册 codex hooks（UserPromptSubmit / Stop）")
    if "opencode" in config.tools:
        if shutil.which("opencode") or (home / ".opencode" / "bin" / "opencode").exists():
            out("opencode 无需注册 hook，由定时任务轮询采集")
        else:
            out("警告：未找到 opencode 可执行文件，会话采集会失败")
    if sys.platform == "darwin":
        if load_launchd:
            install_launchd(config, SCRIPT_DIR, home, out)
        else:
            out("（跳过 launchd 加载）")
    else:
        print_cron_hint(config, SCRIPT_DIR, out)


def uninstall(home: Path, out=print) -> None:
    remove_hooks(home / ".claude" / "settings.json")
    remove_hooks(home / ".codex" / "hooks.json")
    if sys.platform == "darwin":
        uninstall_launchd(home, out)
    out("已移除 hooks 注册与定时任务（数据目录未动）")


def main(argv=None, home: Path = None, input_fn=input) -> int:
    parser = argparse.ArgumentParser(description="ai-worklog 安装/更新/卸载")
    parser.add_argument("--yes", action="store_true", help="全部接受默认值，不交互")
    parser.add_argument("--data-dir", help="数据目录，默认 ~/daily-report")
    parser.add_argument("--model", help="整合模型，默认空=用 opencode 默认模型")
    parser.add_argument("--daily-time", help="日报时间 HH:MM，默认 21:07")
    parser.add_argument("--weekly-time", help="周报时间 HH:MM，默认 21:20")
    parser.add_argument("--weekly-day", help="周报在周几生成，0=周日…5=周五，默认 5")
    parser.add_argument("--claude-level", choices=list(CLAUDE_LEVEL_HOOKS),
                        help="claude 记录粒度：detailed=每turn+每清单项，balanced=每turn，minimal=仅清单项")
    parser.add_argument("--no-tapd", action="store_true", help="关闭 TAPD 需求关联")
    parser.add_argument("--uninstall", action="store_true", help="移除注册与定时任务")
    parser.add_argument("action", nargs="?", choices=["uninstall"], help="同 --uninstall")
    args = parser.parse_args(argv)
    home = home or Path.home()

    if args.uninstall or args.action == "uninstall":
        uninstall(home)
        return 0
    try:
        config = collect_config(args, input_fn)
    except (ValueError, EOFError) as exc:
        print("配置无效：{}".format(exc), file=sys.stderr)
        return 1
    install(config, home)
    print()
    print("完成。数据目录：{}".format(config.data_dir))
    print("启用 TAPD 关联：把 tapd_my_tasks.md 放到 {}/tapd_task/ 即可（缺失时自动关闭该功能）".format(config.data_dir))
    print("更新方式：git pull && ./install.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
