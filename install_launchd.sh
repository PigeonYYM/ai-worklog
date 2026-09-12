#!/bin/bash
# 重新加载工作日志的两个 launchd 定时任务
set -euo pipefail

AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$HOME/daily-report/.state"

for label in daily weekly; do
    plist="$AGENTS/com.pigeonyang.worklog.$label.plist"
    plutil -lint "$plist"
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
    echo "已加载 com.pigeonyang.worklog.$label"
done

launchctl list | grep worklog
