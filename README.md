# ai-worklog

[中文文档](README.zh-CN.md)

Cross-tool worklog for AI coding assistants: Claude Code, Codex and OpenCode sessions are captured into a unified stream automatically, then compiled into daily and weekly reports — with optional TAPD requirement linking and work-hour summaries.

## Installation

```bash
git clone git@github.com:PigeonYYM/ai-worklog.git
cd ai-worklog
./install.py
```

The installer is interactive; every step has a default (press Enter to accept):

1. **Which tools to register hooks for** (auto-detected on your machine)
2. **Claude Code recording granularity** (three levels, directly controls record volume):
   - `detailed` — record every turn *and* every completed task-list item (densest)
   - `balanced` — one record per turn
   - `minimal` — only completed task-list items (sparsest; big tasks without a task list won't be recorded)
3. **Data directory** (default `~/daily-report`)
4. **Summarization model** (default empty = use opencode's configured default model; can also be set later via `WORKLOG_MODEL`)
5. **Daily/weekly report times** (default daily 21:07, Friday 21:20)
6. **Enable TAPD requirement linking** (y/n)

Non-interactive install: `./install.py --yes` for all defaults, or use `--data-dir/--model/--daily-time/--weekly-time/--weekly-day/--claude-level/--no-tapd` flags (see `./install.py --help`).

Requirements: macOS (scheduled jobs use launchd; on Linux the installer prints equivalent crontab lines for manual setup), the `opencode` CLI (used for both summarization and session collection), Python 3.9+ (system Python is fine).

## Updating

```bash
git pull && ./install.py
```

The installer is idempotent: stale hook entries are replaced with the new paths, scheduled jobs are reloaded, and every config step can be skipped with Enter to keep the default.

## Uninstalling

```bash
./install.py uninstall
```

Removes hook registrations and scheduled jobs. **Your data directory is left untouched** (stream, daily and weekly reports all remain).

## Data

- `<data-dir>/.raw/YYYY-MM-DD.tsv` — the stream, append-only, **the single source of truth**. Five tab-separated columns: time, project, title, source tool, duration in seconds
- `<data-dir>/YYYY-MM-DD.md` — daily report, a derived artifact, fully rebuilt every night
- `<data-dir>/weekly/YYYY-Www.md` — weekly report, a derived artifact, built weekly
- `<data-dir>/.state/` — watermarks (`opencode.cursor`), launchd logs, TAPD linking state (`tapd_links.json` / `tapd_pending.json`)
- `<data-dir>/.state/marks/` — per-session duration start marks (`<tool>-<session>.mark`)

**The md files are pure derivatives — manual edits are overwritten on the next build. To preserve content, edit the `.raw/` stream, never the md.**

Reports are produced by a model that groups the day's/week's stream by project and compresses it into outcome descriptions. Model output must pass three hard validations (non-empty, covers all stream rows, markdown structure); any failure — or a failed model call — degrades to a plain scripted grouped render. No stream data is ever lost; only the model's summarization ability is.

An empty duration means unknown (hook didn't fire, etc.) and is strictly distinct from `0` — unknown rows are excluded from totals, with a note under the hours table. Durations are recorded honestly with no cap; only orphaned marks older than 24 hours degrade to unknown.

## Collection

| Tool | Mechanism | Granularity |
|---|---|---|
| Claude Code | `UserPromptSubmit` sets mark + caches input (`claude_prompt.py`); `Stop` records turns (`claude_stop.py`) + `TaskCompleted` records task items (`claude_task.py`); granularity set by the install level | per turn / per task |
| Codex | `UserPromptSubmit` + `Stop` pair in `~/.codex/hooks.json` (`codex_prompt.py` / `codex_stop.py`) | per turn |
| OpenCode | polls its local SQLite at summarize time (`collect_opencode.py`), incremental via watermark | per session |

**Why granularity differs per tool**: the ceiling is whatever hook events each tool
exposes. Claude Code offers both Stop (per turn) and TaskCompleted (per task item);
Codex only has the UserPromptSubmit/Stop pair, so per-turn is the finest available;
OpenCode's plugin events (session.idle) fire per turn but carry only a sessionID —
no title — so recording them would just spam duplicate same-title rows. Polling at
session level is a deliberate choice: better coarse than noisy.

Claude's Stop and TaskCompleted share one session mark chain: task items completed mid-turn each take their segment, Stop takes the remainder — the durations within a single turn sum to its true elapsed time, with no double counting. Idle time between turns (reading answers, meals, sleep) is correctly excluded by mark resets.

OpenCode collection has a built-in **self-reference guard**: the summarize job itself runs opencode inside the data directory, and that call lands in opencode's own session table; `collect_opencode.py` excludes records whose project name matches the data directory, otherwise "generating the daily report" would appear in tomorrow's report — infinite self-reference.

## TAPD Requirement Linking

Drop `tapd_my_tasks.md` into `<data-dir>/tapd_task/` to auto-enable (can also be disabled at install time, or via `WORKLOG_TAPD=off`). Items in the "进行中" (in-progress) and "待处理" (pending) groups are injected into the model prompt as candidates: outcomes with a strong association (channel/feature/game-name keyword match) get annotated as `（关联需求：标题 [ID]）`; items the model is unsure about go into the `.state/tapd_pending.json` queue with a footer note in the report. Annotated IDs are validated against the candidate set — hallucinated IDs are stripped.

```bash
/usr/bin/python3 <repo-dir>/summarize.py --link   # interactively resolve the pending queue, then auto-rebuild affected reports
```

Confirmations (including "no association") persist in `.state/tapd_links.json` and are injected into the prompt on rebuild. The degraded fallback render does no linking and doesn't touch the queue.

## Manual Operations

```bash
/usr/bin/python3 summarize.py --daily              # rebuild today's report
/usr/bin/python3 summarize.py --daily --date 2026-09-10   # rebuild a specific date
/usr/bin/python3 summarize.py --weekly             # build this week's report (Monday~today)
/usr/bin/python3 collect_opencode.py               # pull opencode increments manually
/usr/bin/python3 summarize.py --hours --since 2026-09-14  # print hours summary, for timesheets
/usr/bin/python3 summarize.py --link               # resolve pending TAPD links
./install.py                                       # reinstall / reconfigure hooks and jobs
```

`summarize.py` runs `collect_opencode.py` before summarizing, so a manual `--daily`/`--weekly` always includes the latest opencode data.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

All tests redirect via the `WORKLOG_HOME` environment variable and a temporary HOME — real data and configs are never touched.

## Troubleshooting

- **Reports not updating**: check `<data-dir>/.state/daily.err` first, then `launchctl list | grep worklog` to confirm both jobs are loaded with exit code 0.
- **Summarize degraded to grouped render instead of model**: usually the model is unavailable (network/auth) or its output failed the three hard validations. The stream is intact — rerun another day; the md can always be rebuilt from the stream.
- **An opencode session wasn't recorded**: check whether the watermark in `<data-dir>/.state/opencode.cursor` was accidentally advanced (e.g. a manual collect without a summarize). Edit it back to an earlier timestamp; the next collect rescans.
- **Suspect stream data loss**: don't panic — `.raw/*.tsv` is append-only and the single source of truth. Any md can be deleted and regenerated to verify.
- **Pending-link footer stuck in the daily report**: `.state/tapd_pending.json` has unresolved entries. Run `--link` once (or delete the scope from the file). If the model keeps re-flagging the same item, confirm "no association" once to veto it permanently.
- **Self-reference regression** (rows from the data-directory project appear in reports): the exclusion logic in `collect_opencode.py` has broken — re-check its project-name filter.
- **Moved the repo**: after relocating, rerun `./install.py` — stale hook paths are replaced and scheduled jobs are pointed at the new location.
