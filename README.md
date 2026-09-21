# 跨工具工作日志

三个 AI 编码工具（Claude Code、codex、opencode）完成工作时自动记入统一流水，定时整合成日报与周报。

## 数据

- `~/daily-report/.raw/YYYY-MM-DD.tsv` — 流水，append-only，**唯一真相源**。五列：时间、项目、标题、来源工具、时长秒（tab 分隔）
- `~/daily-report/YYYY-MM-DD.md` — 日报，派生产物，每晚 21:07 全量重建
- `~/daily-report/weekly/YYYY-Www.md` — 周报，派生产物，每周五 21:20 全量生成
- `~/daily-report/.state/` — 水位线（`opencode.cursor`）与 launchd 日志（`daily.log`/`daily.err`）
- `~/daily-report/.state/marks/` — 各工具会话的时长起点标记（`<工具>-<会话>.mark`），放这里而不是 `/tmp` 是为了排障时可直接查看
- `~/daily-report/.backup/` — 改造前的原始 md 备份，系统稳定运行几天后可自行删除，**目前不要动**

**md 是纯派生文件，手工编辑会在下次整合时被覆盖。要保留内容请改 `.raw/` 流水，不要改 md。**

日报/周报由模型（`kimi-for-coding/k3`）把当天/当周流水按项目分组、压缩成成果描述；模型输出必须通过三条硬性校验（非空、覆盖流水条数、markdown 结构），任一条不过或调用失败就退化为纯脚本分组渲染——流水信息不会丢，只是失去模型的归纳能力。生成的 md 开头会标注走的是「模型」还是「退化渲染」。

时长为空表示未知（本功能上线前的历史流水、或 hook 未触发），与 `0` 严格区分——
汇总时未知条目不计入，并在工时表下方标注条数。时长如实记录、不封顶；只有距今
超过 24 小时的孤儿标记才记为未知。

## 采集

| 工具 | 机制 | 粒度 |
|---|---|---|
| Claude Code | `UserPromptSubmit` 打标记（`claude_prompt.py`）+ `TaskCompleted` → `~/worklog/claude_task.py` | 单个任务 |
| codex | `~/.codex/hooks.json` 的 `UserPromptSubmit` + `Stop` 配对（`codex_prompt.py` / `codex_stop.py`） | 单个 turn |
| opencode | 整合时轮询其本地 SQLite（`collect_opencode.py`），按水位线增量拉取 | 整个会话 |

时长口径是「会话内上一标记点到本次记录」的差值：Claude 由 `UserPromptSubmit`
（`claude_prompt.py`）打标记，codex 用 prompt 缓存文件的 mtime，opencode 用
`time_created` 并按采集水位线截断。同一个 turn 内各条时长之和等于该 turn
的真实耗时（跨 turn 的空闲——用户读回答、吃饭、睡觉——被标记重置正确排除，
不计入总和）。

opencode 的采集内建**自指防护**：整合任务本身也是在 `daily-report` 目录下调用 opencode，这次调用会被 opencode 记进它自己的 session 表；`collect_opencode.py` 会排除项目名为 `daily-report` 的记录，否则「生成日报」这件事会被记进第二天的日报，造成无限自指。验收时曾人为跑过一轮整合+采集，确认流水里自指行数为 0。

## 手动操作

```bash
/usr/bin/python3 ~/worklog/summarize.py --daily              # 重建今天日报
/usr/bin/python3 ~/worklog/summarize.py --daily --date 2026-09-10   # 重建指定日期
/usr/bin/python3 ~/worklog/summarize.py --weekly             # 生成本周周报（周一~今天/周日）
/usr/bin/python3 ~/worklog/collect_opencode.py               # 手动拉一次 opencode 增量
/usr/bin/python3 ~/worklog/summarize.py --hours --since 2026-09-14  # 输出工时汇总，用于填报
bash ~/worklog/install_launchd.sh                             # 重装两个 launchd 定时任务
```

`summarize.py` 内部会先调用一次 `collect_opencode.py` 再整合，所以单独手动跑 `--daily`/`--weekly` 已经包含最新的 opencode 数据。

## 测试

```bash
cd ~/worklog && /usr/bin/python3 -m unittest discover -s tests -v
```

115 个测试，全部通过 `WORKLOG_HOME` 环境变量重定向到临时目录，不会碰真实的 `~/daily-report/`。

## 排障

- **日报/周报没更新**：先看 `~/daily-report/.state/daily.err`，再用 `launchctl list | grep worklog` 确认两个任务已加载、上次退出码是 0。
- **整合退化成分组渲染而非模型**：生成的 md 首行标注会写「退化渲染」。原因通常是模型不可用（网络/鉴权）或输出没通过三条硬性校验。流水本身完好，改天/重跑一次即可，不用管 md——它随时可从流水重建。
- **opencode 漏记某次会话**：检查 `~/daily-report/.state/opencode.cursor` 这个水位线数值是否被意外提前推进过（比如手动跑过一次采集又没让它整合），必要时可以直接编辑这个文件回退时间戳，下次采集会重新扫描。
- **怀疑流水本身丢数据**：不要慌，`.raw/*.tsv` 是唯一真相源、只增不改。任何 md 都能删了重新生成来验证——这是本系统的核心设计前提，验收时实测过（删日报 → 重建 → 内容与删除前一致，仅整合方式可能因流水增量而略有不同）。
- **自指风险复发**（日报里出现 `daily-report` 项目的条目）：说明 `collect_opencode.py` 里的排除逻辑失效了，需要重新检查该脚本对项目名的过滤条件。
