#!/bin/bash
# 重新加载工作日志的两个 launchd 定时任务
set -euo pipefail

AGENTS="$HOME/Library/LaunchAgents"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/daily-report/.state"

for label in daily weekly; do
    plist="$AGENTS/com.pigeonyang.worklog.$label.plist"
    repo_plist="$SCRIPT_DIR/com.pigeonyang.worklog.$label.plist"

    # Copy the plist from the repo to the LaunchAgents directory
    if [[ ! -f "$repo_plist" ]]; then
        echo "错误：找不到仓库内的 plist 副本：$repo_plist" >&2
        exit 1
    fi
    cp "$repo_plist" "$plist"

    plutil -lint "$plist"
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
    echo "已加载 com.pigeonyang.worklog.$label"
done

# Non-fatal status check
launchctl list | grep worklog || true
