# ai-worklog

[English README](README.md)

跨工具 AI 编码工作日志：Claude Code / codex / opencode 完成工作时自动记入统一流水，定时整合成日报与周报，可关联 TAPD 需求、汇总工时。

## 安装

三种方式任选，都会引导你完成同样的交互式配置（工具选择、Claude 记录粒度、数据目录、模型、日报/周报时间、TAPD 开关）：

> ⚠️ **三种方式互斥，不要混装**（比如同时用插件市场和 clone 安装）——Claude hooks 会被注册两次，同一事件记录两条流水。切换方式前先用旧方式的 uninstall 清理。

### 方式 A：npx 一键（推荐，免 clone）

```bash
npx -y git+https://github.com/PigeonYYM/ai-worklog.git
```

前提：本机有 node/npm（用 Claude Code 必有）、你有这个私有仓库的访问权限（走 GitHub HTTPS 凭据，如 osxkeychain 里存的账号或 PAT）。运行脚本会被物化到 `~/.ai-worklog/`，hooks 与定时任务都指向这个稳定目录。非交互可加 `--yes` 及 `--data-dir/--model/--daily-time/--weekly-time/--weekly-day/--claude-level/--no-tapd/--tools` 旗标。

### 方式 B：Claude Code 插件市场（hooks 随插件版本自动更新）

在 Claude Code 里执行：

```
/plugin marketplace add PigeonYYM/ai-worklog
/plugin install ai-worklog@ai-worklog
```

然后运行 `/ai-worklog:setup`，由它引导完成定时任务与其余配置。
约束：数据目录固定 `~/daily-report`、记录粒度固定 `detailed`（插件 hooks 运行在版本化缓存目录、读不到自定义环境变量）；需要自定义请用方式 A 或 C。

### 方式 C：git clone（开发者/维护者）

```bash
git clone https://github.com/PigeonYYM/ai-worklog.git
cd ai-worklog
./install.py
```

原地注册，改代码即时生效。

要求：macOS（定时任务用 launchd；Linux 会打印等效 crontab 示例自行安装）、`opencode` CLI（整合与采集都依赖它）、Python 3.9+（系统自带即可）。

## 更新

- 方式 A：重跑同一条 npx 命令（物化覆盖 `~/.ai-worklog/`，hooks 指向不变）
- 方式 B：`/plugin` 里更新插件后重跑一次 `/ai-worklog:setup`
- 方式 C：`git pull && ./install.py`

安装脚本幂等：旧 hook 条目会被替换，定时任务重新加载，配置逐项回车即保持默认。

## 卸载

```bash
/usr/bin/python3 ~/.ai-worklog/install.py uninstall   # 方式 A/B（插件本体再到 /plugin 卸载）
./install.py uninstall                                # 方式 C
```

移除 hooks 注册与定时任务，**不动数据目录**（流水、日报、周报都保留）。

## 数据

- `<数据目录>/.raw/YYYY-MM-DD.tsv` — 流水，append-only，**唯一真相源**。五列：时间、项目、标题、来源工具、时长秒（tab 分隔）
- `<数据目录>/YYYY-MM-DD.md` — 日报，派生产物，每晚全量重建
- `<数据目录>/weekly/YYYY-Www.md` — 周报，派生产物，每周生成
- `<数据目录>/.state/` — 水位线（`opencode.cursor`）、launchd 日志、TAPD 关联状态（`tapd_links.json` / `tapd_pending.json`）
- `<数据目录>/.state/marks/` — 各工具会话的时长起点标记（`<工具>-<会话>.mark`）

**md 是纯派生文件，手工编辑会在下次整合时被覆盖。要保留内容请改 `.raw/` 流水，不要改 md。**

日报/周报由模型把当天/当周流水按项目分组、压缩成成果描述；模型输出必须通过三条硬性校验（非空、覆盖流水条数、markdown 结构），任一条不过或调用失败就退化为纯脚本分组渲染——流水信息不会丢，只是失去模型的归纳能力。

时长为空表示未知（hook 未触发等），与 `0` 严格区分——汇总时未知条目不计入，并在工时表下方标注条数。时长如实记录、不封顶；只有距今超过 24 小时的孤儿标记才记为未知。

## 采集

| 工具 | 机制 | 粒度 |
|---|---|---|
| Claude Code | `UserPromptSubmit` 打标记+缓存输入（`claude_prompt.py`）；`Stop` 记 turn（`claude_stop.py`）+ `TaskCompleted` 记清单项（`claude_task.py`），粒度由安装档位决定；`SessionEnd` 兜底被 Esc 中断的最后一个 turn（`claude_session_end.py`） | 单个 turn / 单个任务 |
| codex | `~/.codex/hooks.json` 的 `UserPromptSubmit` + `Stop` 配对（`codex_prompt.py` / `codex_stop.py`） | 单个 turn |
| opencode | 整合时轮询其本地 SQLite（`collect_opencode.py`），按水位线增量拉取 | 整个会话 |

**为什么各工具粒度不同**：粒度上限取决于工具暴露的 hook 事件。Claude Code
同时有 Stop（每 turn）和 TaskCompleted（每清单项）可用；codex 只有
UserPromptSubmit/Stop 配对，turn 级即最细；opencode 的插件事件（session.idle）
虽每回合触发但 payload 只有 sessionID、拿不到标题，硬记只会产生同标题重复
流水，所以主动选择会话级轮询——宁可粗一点，也不写垃圾进流水。

Claude 的 Stop 与 TaskCompleted 共用同一条会话标记链：turn 内完成的清单项
各自分段计时，Stop 拿走剩余尾段，同一个 turn 里所有记录的时长之和等于该
turn 的真实耗时，不重不漏。跨 turn 的空闲（读回答、吃饭、睡觉）被标记重置
正确排除，不计入总和。

opencode 的采集内建**自指防护**：整合任务本身也是在数据目录下调用 opencode，这次调用会被 opencode 记进它自己的 session 表；`collect_opencode.py` 会排除项目名为数据目录名的记录，否则「生成日报」这件事会被记进第二天的日报，造成无限自指。

## TAPD 需求关联

把 `tapd_my_tasks.md` 放到 `<数据目录>/tapd_task/` 即自动启用（安装时也可整体关闭，或设 `WORKLOG_TAPD=off`）。清单中「进行中」「待处理」两组的条目作为候选注入模型 prompt：强关联（渠道/功能/游戏名明确对应）的成果条目后标注 `（关联需求：标题 [ID]）`；模型拿不准的条目进入 `.state/tapd_pending.json` 待确认队列，并在 md 末尾加脚注提示。标注的 ID 会按候选集校验，幻觉 ID 一律剥掉。

```bash
/usr/bin/python3 <仓库目录>/summarize.py --link   # 交互确认待关联队列，确认后自动重建受影响的日报/周报
```

确认结果（含「无关联」）存在 `.state/tapd_links.json`，重建时注入 prompt，结论持久有效。模型不可用走退化渲染时不做关联、不动队列。

## 手动操作

```bash
/usr/bin/python3 summarize.py --daily              # 重建今天日报
/usr/bin/python3 summarize.py --daily --date 2026-09-10   # 重建指定日期
/usr/bin/python3 summarize.py --weekly             # 生成本周周报（周一~今天）
/usr/bin/python3 collect_opencode.py               # 手动拉一次 opencode 增量
/usr/bin/python3 summarize.py --hours --since 2026-09-14  # 输出工时汇总，用于填报
/usr/bin/python3 summarize.py --link               # 确认待关联的 TAPD 需求
/usr/bin/python3 summarize.py --log "手动部署到生产" --minutes 25   # 补录 agent 之外的手工工作
./install.py                                       # 重装/重配 hooks 与定时任务
```

**中断与手动工作的兜底**：Esc 中断的 turn（Stop 不触发）会在会话结束时由
SessionEnd hook 补录，时长含接管后的手动操作时间；完全在 agent 之外做的
手工工作用 `--log` 自己记一条（工具列 `manual`，`--minutes` 可省略，项目名
默认当前目录）。在 Claude 会话里也可以直接说「记一条工作日志：xxx」让
agent 替你执行。

`summarize.py` 内部会先调用一次 `collect_opencode.py` 再整合，所以单独手动跑 `--daily`/`--weekly` 已经包含最新的 opencode 数据。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

全部测试通过 `WORKLOG_HOME` 环境变量与临时 HOME 重定向到临时目录，不会碰真实数据与配置。

## 排障

- **日报/周报没更新**：先看 `<数据目录>/.state/daily.err`，再用 `launchctl list | grep worklog` 确认两个任务已加载、上次退出码是 0。
- **整合退化成分组渲染而非模型**：原因通常是模型不可用（网络/鉴权）或输出没通过三条硬性校验。流水本身完好，改天/重跑一次即可，不用管 md——它随时可从流水重建。
- **opencode 漏记某次会话**：检查 `<数据目录>/.state/opencode.cursor` 水位线是否被意外提前推进过（比如手动跑过一次采集又没让它整合），必要时直接编辑回退时间戳，下次采集会重新扫描。
- **怀疑流水本身丢数据**：不要慌，`.raw/*.tsv` 是唯一真相源、只增不改。任何 md 都能删了重新生成来验证。
- **待确认关联一直挂在日报里**：说明 `.state/tapd_pending.json` 里有没处理的条目，跑一次 `--link` 逐条确认（或把对应 scope 从文件里删掉）后脚注就会消失。若怀疑是模型反复把同一条目标成待确认，确认一次「无关联」即可永久否决。
- **自指风险复发**（日报里出现数据目录项目的条目）：说明 `collect_opencode.py` 里的排除逻辑失效了，需要重新检查该脚本对项目名的过滤条件。
- **Esc 中断的 turn 没有记录**：确认 SessionEnd hook 已注册（旧版本安装的没有这条，重跑一次安装命令即可）；它只兜底「会话结束时尚未记录的最后一个 turn」，中断后继续在原会话工作不受影响。
- **换了仓库位置**：`git pull` 移动目录后重跑一次 `./install.py`，hooks 里的旧路径会被替换，定时任务会指向新位置。
