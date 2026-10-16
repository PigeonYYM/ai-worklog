---
description: 配置 ai-worklog 的定时任务与个人设置（Claude hooks 已由插件市场注册，本命令完成其余安装）
---

帮助用户完成 ai-worklog 插件的安装配置。严格按顺序执行：

1. 用 AskUserQuestion 一次性询问以下配置（每项都给默认值）：
   - 整合模型：留空 = 用 opencode 配置的默认模型
   - 日报生成时间：默认 21:07
   - 周报生成时间与周几：默认周五 21:20
   - 是否启用 TAPD 需求关联：默认启用（提示用户之后把 tapd_my_tasks.md 放到 ~/daily-report/tapd_task/）
   - 是否也为 codex 注册 hooks：默认是（若用户不用 codex 选否）

   注意（不要询问这些）：
   - 数据目录固定为 `~/daily-report`——插件方式安装的 Claude hooks 运行在插件缓存目录，读不到 install 时写入 launchd 的环境变量
   - 记录粒度固定为 detailed（每 turn + 每清单项）——插件 hooks 是静态注册的；需要 balanced/minimal 的用户应改用 npx 或 clone 安装

2. 把答案翻译成旗标，用 Bash 运行：
   ```
   /usr/bin/python3 "${CLAUDE_PLUGIN_ROOT}/install.py" --yes --no-claude-hooks [--model 模型] [--daily-time HH:MM] [--weekly-time HH:MM] [--weekly-day N] [--no-tapd] [--tools claude,opencode]
   ```
   - 用户选择不为 codex 注册时加 `--tools claude,opencode`
   - install.py 检测到自身运行在插件缓存目录时，会自动把运行脚本物化到 `~/.ai-worklog/` 并让定时任务指向该稳定目录

3. 向用户报告安装输出，并提醒：
   - 更新方式：`/plugin` 里更新本插件后，重跑一次本 setup 命令
   - 卸载：`/usr/bin/python3 ~/.ai-worklog/install.py uninstall`（再到 `/plugin` 卸载插件本体）
