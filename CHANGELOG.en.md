# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).
Release flow: `git tag vX.Y.Z` → CI builds and publishes the Release automatically.

> 简体中文版: [CHANGELOG.md](CHANGELOG.md)

## [2.8.2] - 2026-08-23

> Theme: patch fixes — cache concurrency safety, report-chain config flow, pause-state exit semantics. No new features.

### Fixed

- **Module-level cache thread-safety**: dashboard serves requests on multiple threads while `_COLLECT_CACHE`/`_PARSE_CACHE` (ai_sessions), `_agg_cache` and `_aliases_cache` (report), and the days-cache (dashboard_util) were all unlocked — concurrent requests could hit "OrderedDict mutated during iteration" or dirty reads during LRU moves/eviction. All five now guarded by `threading.Lock` (table ops inside locks, parsing/aggregation stays outside; days-cache rescans stay locked to prevent stampedes); new 8-thread hammer regression tests (including direct eviction-loop stress)
- **Report chain config flow break**: the `finalize_day → generate_day_report → generate_consolidated_md` chain never received configuration — report generation silently used global defaults, so settings written under `--data-root`'s config.json or an explicit `--config` did not apply to daily reports (inconsistent with the dashboard). The chain now threads `config_path` end-to-end with unified resolution priority **explicit config_path > `<root>/config.json` > global default** (same semantics as dashboard `_load_config_for_root`; existing callers unchanged). Weekly/monthly reports share the flaw — located, deferred to next batch
- **Pause-state exit semantics**: the daemon loop's pause branch `continue` skipped every exit check at the loop tail, so "pause then quit" left the thread waiting forever. Pause waits now honor stop_event and test_seconds expiry

### Tests (2.8.2)

- New `tests/integration/test_report_config_flow.py` (reversed-priority tripwire), `tests/unit/test_cache_concurrency.py`, `test_stop_while_paused` (negative-proofed: hangs under old code until timeout); conftest dead-code cleanup

## [2.8.1] - 2026-08-23

> Theme: small patch — multi-day AI cost query performance fix (121s→0.95s) + budget endpoint edge-case fix + unified test system (`test_all.py` retired, full-chain E2E). No new features.

### Tests (2.8.1)

- **Two-tier tests merged**: all 47 test functions (336 assertions) of `test_all.py` mechanically ported by domain into four pytest modules — monitor scenarios / report content / dashboard API surface / insights ecosystem — plus a shared support layer (`tests/support/scenario.py`); zero assertion loss (statically verified call-count parity); `test_all.py` deleted, legacy CI step removed, pyproject omit cleaned up
- **Full-chain E2E**: new `tests/e2e/test_full_chain.py` with seven ordered stages over one simulated data world: seeding → SQLite mirror rebuild/verify → day/month reports & CSV exports → dashboard read surface (20+ endpoints) → write cycle (groups add/delete, goals settings persistence) → backup zip → restore into a fresh root with aggregate reconciliation → security spot-checks (token auth 401/200, CSP headers, cross-origin POST rejection) → insights↔query consistency
- **Determinism fix**: legacy wall-clock (`time.time()`) seeding anchors replaced with noon-anchored `_day_noon_ft` (removes the midnight-flake class)
- **Coverage**: gate stays 70%, measured **79%** (was 73%); pytest total 429 → **483** (59 files)

### Fixed (2.8.1)

- **Multi-day AI cost query performance (121s → 0.95s measured)**: fingerprint string was os.walk-order dependent so the v2.7 result cache never matched on real directories (now sorted); `_COLLECT_CACHE_MAX` 8 → 160 (was smaller than query max_days=92, so range results evicted each other); added session-file parse memoization keyed by `(parser, path, mtime_ns, size)` capped at 4096 entries / 256MB source bytes; new `collect_fingerprint_batch()` scope wraps `query.run_query` so directory walks happen once per query instead of once per day
- **`/api/budget` monthly edge-year 500**: month-end arithmetic for `9999-12` (+4 days past date.max) raised an uncaught OverflowError and the handler returned 500 against its own documented "disabled/invalid/error → 200 empty state" contract; `_month_days` now treats it as invalid month and the handler honors 200
- **Test determinism**: `test_pause_resume` rewritten from real-sleep choreography (load-flaky) to a fully fake-clock version; scenario tests no longer scan the developer machine's real AI session directories via `finalize_day`
- **CI flake fix**: conftest `seed_day` now invalidates the days-cache after writing — directory mtime has clock granularity (~10ms), so seeding and reading within the same tick returned a stale day list (reproduced on Windows CI fast disks: goals streak misjudged a met day, failing the build)

### Docs (2.8.1)

- Unified test commands in both READMEs; ROADMAP flips "merge deferred" to done; TEST_WORKFLOW v1.2 records how the migration actually happened; TODO handover commands synced

## [2.8.0] - 2026-08-23

> Theme: engineering wrap-up & test pyramid completion (dashboard split / frontend·e2e tests / coverage gate 70) + Git-side adoption proxy metrics + constrained query template expansion.

### Added (v2.8.0)

- **Git-side adoption proxy metrics** (`adoption.py` converged + `/api/adoption?date=`): per the ADOPTION_SPIKE conclusion, the AI-side per-file attribution is dropped (0% join hit on real data); only read-only Git proxies remain — `retention` = lines_added/(lines_added+lines_deleted), `reworked_ratio` = lines_deleted/(lines_added+lines_deleted). A failing repo is skipped; whole-source failure returns an empty 200 contract, never 500. Mandatory disclaimer; shown collapsed + greyed-out in the insights view; confidence never equals "high"
- **Constrained query template expansion** (`query.py`): added q6 "period output comparison" / q7 "best-focus day" / q8 "cost trend" templates, following the existing regex-whitelist + period-words + empty-200 + notice constrained contract, plus "today/yesterday" period aliases

### Engineering (v2.8.0 · maintainability & tests)

- **`dashboard.py` split**: HTTP-unrelated pure functions/constants (`_agg_to_csv`, `_backup_zip`, `_safe_extract_zip`, `_available_days`, `_collect_known_apps`, the days-cache group, etc.) moved to the new `dashboard_util.py`; behavior unchanged and re-exported so `dashboard.<name>` stays compatible; dashboard.py 1917→1714 lines; 13 new unit tests
- **Test pyramid completion**: added `tests/frontend/test_frontend_smoke.py` (4 cases: nav↔section↔loader↔TITLES wiring, all 24 frontend `/api/*` calls resolved on backend routes, template structural gaps) and `tests/e2e/test_smoke.py` (2 cases: root HTML + seeded data → `/api/day` → `/api/trend` full-chain smoke)
- **Coverage gate 65 → 70**: coverage source now includes `learn`/`alerts`/`goals`; measured quick-set is 73%; `pyproject.toml` / `ci-fast.yml` / `build.yml` all set to 70 consistently

### Docs (v2.8.0)

- `TODO.md` / `docs/ROADMAP.md` synced to the actual release state (v2.5.x / v2.7.0 released, v2.5.3 noted as tag-less, adoption marked "needs plugin event source"); `.gitignore` now ignores `.agent-teams/` (AgentTeams team state dir)

## [2.7.0] - 2026-08-21

> Theme: alert loop + daily goals + global performance optimization (fingerprint caches for AI sessions / browser history / SQLite) + real-usage-first tokens with weighted estimation.

### Changed (v2.7.0 · Algorithm refinements)

- **Dual-mode token estimation** (`ai_sessions.token_estimation_mode`): new `weighted` (default) weights by character class — CJK 1 token/char, letters 4 chars/token, digits ~3, punctuation 2, whitespace 8 — fixing underestimation on code/JSON; `simple` keeps the legacy formula
- **Real usage fields first**: parses API-returned `usage` in messages (`input_tokens/output_tokens`, `prompt_tokens/completion_tokens`, flat variants); when present, tokens & cost use **actual values** instead of estimation; new `tokens_from_usage` counter
- **"Simple learning" personal baseline (`learn.py` + `insights.baseline_insights`)**
  - Pure-stdlib online statistical learning: sliding-window sample ring (180 days) + z-score anomaly detection; deep learning rejected due to the zero-dependency constraint and tiny per-user sample sizes (rationale in module docstring)
  - Maintains your personal norm for active/coding/sessions; ≥2σ → warn card, ≥3σ → alert (type=trend, reuses existing frontend rendering)
  - Scores before recording (today never pollutes its own baseline); same-day re-calls overwrite; corrupt state self-heals
  - Config `insights.baseline`; wired into `/api/insights` and the daily report

### Performance (v2.7.0 · global slimming)

- **AI session stats fingerprint cache** (`ai_sessions.collect`): keyed by (date, session-file mtime+size fingerprint); hits skip all file reading/parsing — measured 76.9ms → 0.37ms (~208×); new/appended files auto-invalidate; web_ai never cached (varies with args); added `invalidate_collect_cache()`
- **Browser history fingerprint cache** (`browser_history.collect`): keyed by (date, History DB mtime+size), eliminating repeated full-DB copies+parsing (Chrome DBs can exceed 100MB) — measured 6.9ms → 0.05ms (~144×); browser writes auto-invalidate; added `invalidate_visits_cache()`
- **SQLite mirror writes**: per-root shared connections (`_CONN_CACHE`), `init_db` once per connection, **WAL + synchronous=NORMAL** (JSONL is the source of truth; the mirror is rebuildable) — measured 9.9ms → 0.15ms/row (~66×); `rebuild` releases the shared handle first; added `close_connections()`
- **goals day-list TTL cache**: same mtime+5s pattern as dashboard/classifier
- All are behavior-preserving cache speedups: shared read-only objects (per report.aggregate convention), auto-invalidation on fingerprint change

### Tests

- Added `test_learn`, `test_ai_sessions_refined`, `test_baseline_api`, `test_perf_caches`

### Added (v2.7.0 · Action & Goals)

- **Alert loop (`alerts.py`)**: budget warn/exceed and continuous-work rest reminders via tray balloon (auto-converted to Toast on Win10/11)
  - Budget alerts reuse `budget_status` tri-state; warn/exceed fire at most once per day each, re-armed automatically across days
  - Rest reminder: fires after `rest_after_min` minutes of continuous activity without enough idle; idle ≥ `idle_reset_s` counts as a break and resets the accumulator; `cooldown_min` prevents nagging
  - Budget checks are throttled (default 15 min — AI session scanning is expensive); no accumulation/evaluation while monitoring is paused
  - Config section `alerts` with hot reload
- **Daily goals & streak (`goals.py`, optional · off by default)**
  - Two goal types: total active time + coding time (开发工具 + AI编程 categories combined)
  - Overview panel with progress bars and streak counter; settings toggle group (`GET /api/goals` + `POST /api/goals/settings`)
  - Streak is purely derived (no state file): an unmet today doesn't break the chain (counts from yesterday), missing calendar days break it, lookback capped at 90 days; changing goals recomputes against the new targets
  - Config section `goals` (`enabled` default false / `daily_active_min` / `daily_coding_min`)

### Tests

- Added `test_alerts`, `test_goals`, `test_goals_api`

### Fixed (v2.7.0)

- **Backup restore endpoint**: `/api/backup/restore` rejected an oversized body without reading it, so keep-alive parsed the leftover bytes as a new request and the client saw a connection reset; it now drains a bounded prefix and closes the connection before returning a clean 400 (covered by new `test_restore_reject_bad_bodies`)

## [2.5.3] - 2026-08-21

> Theme: AI pricing settings usable + export progress feedback.

### Added

- **AI model pricing settings page can now edit built-in prices directly**: the Settings → "💲 AI model pricing" panel now renders all 60 built-in model prices as editable rows; changing a unit price writes an override to `<data root>/ai_pricing.json` (pure diff layer; unmodified built-ins aren't written), and "重置" restores the default. No need to retype the model name. Added a backend assertion that the full built-in price table is returned (count matches the in-code table).

### Fixed

- **Month report export stuck on "导出中…" with no follow-up**: root cause was slow server-side month aggregation (uncached first run ~10s+) and a frontend `fetch` with no timeout, so any SQLite-lock contention blocked it forever. Export now **streams the response**: an indeterminate sliding progress bar during generation ("正在生成报表…"), then a determinate `Content-Length`-based bar during download ("正在下载…"); plus a **120s client timeout** that surfaces a "导出超时" alert and resets the button instead of hanging.

## [2.5.2] - 2026-08-20

> Theme: refine AI session model recognition — timeline/compare/depth panels no longer drowned by "unrecognized".

### Fixed

- **AI session model still biased toward "unrecognized"**: conversation-level model used to take the most frequent model across ALL messages, but Claude-style user messages carry no model field (recorded as "unrecognized"), and when they outnumber assistant messages they displaced the real model. Now **conversation-level model is computed from assistant messages' known models only** (real models live on assistant turns); unrecognized conversations dropped from 14/20 to 4/20. The `by_model` dimension still keeps the "unrecognized" key for backward compatibility. Cost estimation and multi-tool discovery unaffected (still ~$0.70/day).

## [2.5.1] - 2026-08-20

> Theme: fix a batch of frontend/data-layer defects found in real use, plus add an "AI model pricing" settings entry. Fully offline-derived, zero third-party runtime dependencies.

### Fixed

- **Export button always returned 400**: the frontend `doExport` argument order was reversed versus the backend contract (`scope`/`type` swapped), so `type` was rejected as invalid. Corrected the argument order and added a loading state; the backend now strictly validates `type`/`scope` and returns 400 on mismatch (no more silent empty files).
- **Growth/Compare 4/8/12/24-week buttons dead**: the old `$$('.controls [data-gw]')` selector couldn't bind buttons outside the view container. Switched to event delegation (`#view-growth [data-gw]`) with a `primary` highlight.
- **AI sessions only saw claude, models all "unrecognized", cost all 0**: `ai_sessions` discovery previously only covered claude. Added **opencode (SQLite, real modelID/cost)** and **pi agent (`~/.pi/agent/sessions` dedicated parser with model_change context backfill)** parsers — `model` is mostly session-level and must be backfilled, otherwise every message reads "unrecognized". After the fix, tools cover `claude / opencode / pi_agent`, and model recognition + cost estimation recover.
- **Quick-ask input couldn't be typed + should show only after AI is wired**: the panel now defaults to hidden and appears only when AI insights are enabled; confirmed no overlay/`readonly`/`preventDefault` blocks input.
- **Week/Month report "never generates"**: month aggregation takes ~12s on real data with no feedback. Added an explicit loading state ("Aggregating this month's data… please wait") to avoid appearing frozen.
- **Bottom-left version hardcoded v1.0.0**: now injected from backend `version.VERSION` to match the release version.
- **Contact identification empty**: verified by-design — only recorded when WeChat/QQ/DingTalk foreground window titles contain a contact; the user had no such windows recently, so empty is expected, not a bug. The AI-tool part recovered with the data-layer fix above.

### Added

- **AI model pricing settings UI** (Settings → "💲 AI model pricing"): built-in common model price table (USD per million tokens, magnitude reference); override unlisted/changed models here; saved to `<data root>/ai_pricing.json` and immediately used by timeline/compare/cost stats. New `/api/pricing` GET/POST endpoints.

### Tests

- Added `tests/api/test_regression_bugs.py`: locks the export param contract (correct order 200, wrong order 400), `/api/pricing` read/write round-trip, and multi-tool discovery structure.

## [2.5.0] - 2026-08-20

> Theme: evolve from "how long you used it" to "understand your AI-coding process, cost and growth". All offline-derived, zero third-party runtime deps, raw `usage.jsonl` never mutated.

### Added (Vibe Coding analytics platform · v2.5/v2.6 main line)

- **AI session quality scoring** (`ai_sessions`): 0–100 weighted score from four factors (question value / rework / stability / context health) with grade (great/good/fair/needs-work); each conversation gets `quality_score`, `quality_factors`, `quality_notice`; daily report "AI session depth" section gains a quality summary and column, dashboard AI panel adds an average-quality card and sorts by quality desc. Pure derivation, not persisted, explicitly labeled as NOT acceptance rate
- **Vibe Coding timeline replay** (`timeline.py` + `/api/timeline`): merges three sources — foreground sessions in `usage.jsonl`, AI session depth, Git commits — into a time-ordered event stream (`session` / `ai_session` / `git_commit`); new "Timeline" view replays the day's coding narrative with a summary (AI minutes / commits / churn / cost)
- **Cost budget alerts** (`budget.py` + `/api/budget`): set daily/monthly AI cost budgets, three states ok / warn (≥80%) / exceed; overview banner turns red on overspend, weekly/monthly reports append a budget summary. Off by default (`insights.budget`)
- **Multi-tool comparison** (`tool_compare.py` + `/api/ai-compare`): compare sessions / rounds / minutes / tokens / cost / chars-per-dollar / avg quality / cost share across AI tools over a 1–90 day window, with project filtering; new "Compare" view
- **Capability growth curve** (`growth.py` + `/api/trend`): ISO-week aggregation of dependency / efficiency / quality / focus weekly averages with up/flat/down trend; weekly snapshot written atomically (`tmp + os.replace`), idempotent, self-healing on corruption; new "Growth" view
- **Constrained template query** (`query.py` + `/api/query`): 5 fixed templates (AI cost / cost ranking / focus trend / AI vs Git output / AI activity overview) with period words (today/yesterday/this week/last week/this month/last N days); overview "Quick Ask" panel. **No LLM embedded**, strict regex allow-list matching for injection safety

### Engineering (ROADMAP §9)

- **Externalized page template**: the 2405-line inline `PAGE_TEMPLATE` moved to `assets/dashboard.html`, loaded at runtime with `mtime/size` cache; three-level path fallback (`sys._MEIPASS` → program dir → source dir) plus an inline fallback page so it never goes blank; `dashboard.py` shrinks from 3957 to 1616 lines (-59%)
- **`_available_days` cache**: date-folder list cached by data-root mtime + 5s TTL, returns a shallow copy, avoids repeated `os.listdir` within a request and on long histories (hundreds of date folders)
- **`applog.read_recent` streaming tail**: `deque(maxlen=n)` line iteration instead of `readlines()`, memory independent of total log size (verified on a 200k-line log)

### Fixed

- **Missing log section**: externalizing the template dropped the `<section id="view-log">` open tag, breaking the log view DOM (restored, guarded by a wiring test)
- **Feature without entry**: `/api/ai-compare` and `/api/query` had backends but no nav entry in the main dashboard — views and calls now wired up
- **Missing config section**: `config.default.json` gains a `query` section (`enabled` / `max_days`), old `config.json` picks it up via deep-merge
- **Coverage source gap**: `pyproject.toml` coverage source adds the `query` module

### Tests

- **pytest 85 → 290** (unit / integration / api / security / performance), `test_all.py` 334 LEGACY still green; coverage 56% → 59% (`timeline` 89% / `budget` 96% / `tool_compare` 96%)
- Added `test_ai_quality`, `test_timeline`, `test_budget`, `test_tool_compare`, `test_growth`, `test_query`, `test_adoption`, `test_applog`, `test_days_cache`, `test_dashboard_template`, `test_frontend_wiring`, etc.
- **Wiring guard**: `test_frontend_wiring` asserts nav ↔ section ↔ loader ↔ TITLES consistency and that every `/api/*` the frontend calls exists in the backend, preventing "backend done, frontend not wired" and this release's section-drop regression

### Known limitations (honest disclosure)

- **Acceptance / retention attribution NOT adopted**: `adoption.py` and `docs/ADOPTION_SPIKE.md` record a heuristic spike based on Git numstat × AI session time windows × file mtime; real-data hit rate was 0% (sessions at dawn, writes at noon, commits in the afternoon), far below the 30% acceptance bar, so it is **not wired into the dashboard**, kept only as documentation of why it's not done
- Token / cost / time-saved / quality are all **offline estimates**, not official bills or real acceptance rates; UI and reports carry disclaimers

## [2.4.0] - 2026-08-20

### Tests

- **Test pyramid, 85 tests**: tests organized in layers `unit / security / integration / api / frontend / performance / e2e`; full `pytest tests` run is green (85 passed)
- **56% line coverage**: overall line coverage 56% across monitor / insights / report / dashboard contract / updater / security boundaries, serving as the regression baseline

### Added

- **time_saved offline estimate** (`insights.time_saved`, Phase 3): estimates time saved from the day's AI-coding activity × efficiency factor (saved = AI time × (factor−1)); computed offline, never stored or uploaded. New "Time saved estimate" card on the dashboard overview; factor (1.0–5.0) and minimum AI-active minutes configurable via `factor` / `min_ai_min`

### Frontend details

- Unified hover / focus / active states and transitions for form controls (select / input / textarea / file / button, incl. `:focus-visible` ring)
- Heatmap legend hint (less → more)
- Mobile hamburger menu with drawer open/close (syncs `aria-expanded`)
- Auth overlay: Enter to unlock, Esc to close, auto-focus on open
- Debounced view refresh on window resize
- "Last 14 days activity trend": per-day aggregation failures fall back to 0; overview trend shows a degraded hint instead of a 500 / blank chart

### Fixed

- **Config drift**: new keys such as `insights.time_saved` are merged into defaults via `_merge_dict`, so old config.json files get missing keys filled in automatically and behavior no longer drifts
- **Update whitelist**: `updater._is_allowed_asset_url` now allows custom `api_base` mirrors while rejecting any non-whitelisted domain (covered by `test_update_whitelist_rejects_evil`)

### Backfilled (v2.7.0 housekeeping)

> These capabilities actually shipped with v2.4.0 but were omitted from its release notes; recorded here for accuracy.

- **Focus score** (offline rule engine): 0–100 score from longest focus segment, coding/dev share and hourly switch frequency, graded high/mid/low; **death-loop detection** flags dense short-session rapid switching; behavior panel + daily report; `/api/insights` returns `behavior`; thresholds via `insights.behavior`
- **Vibe coding persona analysis** (fun · offline): weighted scoring over the day's activity distribution picks a persona; persona card on the behavior panel + daily hint; `/api/insights` returns `persona`; configurable via `insights.persona`
- **Git code-change analysis** (Phase 2 · read-only local commits): `git log --numstat` per-day commits/added/deleted/changed files, `modify_ratio` as a rework proxy; Git output panel + daily report; `/api/insights` returns `git`; read-only with timeout, graceful degrade without git / not configured / not a repo; thresholds via `insights.git`
- **AI cost ledger** (Phase 3 · weekly/monthly spend reports): per-day AI session depth aggregated to messages/rounds/tokens/cost by model/project/tool; auto-appended to weekly/monthly reports; read-only, offline, omitted when no data
- **Fixes**: prune orphan app groups pointing to unknown categories (classification/list/import only trust registered categories, built-in ∪ custom); `/api/heatmap` falls back to 0 per-day instead of 500ing the whole chart

## [2.3.0] - 2026-08-18

### Added (ROADMAP Phase 1 · AI coding deep tracking v1.5)

- **Turn tracking** (`ai_sessions.rounds`): counts Q/A pairs (user→assistant) inside local session files; also deep-parses browser visits into Web AI conversations (ChatGPT/Claude/Gemini chat pages grouped by conversation; returns/refreshes ≈ turns, best-effort)
- **Token estimation** (`ai_sessions.token_estimation`, default on): CJK ≈ 1 token/char, other ≈ 1 token/4 chars, split into input/output tokens per tool and per conversation
- **Breakdown by model** (`by_model`): extracts model names from message `model` fields or content patterns (Claude/GPT/DeepSeek/Qwen, etc.), aggregated to tool/total/conversation detail
- **Breakdown by project** (`by_project`): extracts from cwd/project/repo fields, attributed conversation-level to avoid tool-dir noise, aggregated to tool/total/conversation detail
- **AI session depth on by default**: `ai_sessions.enabled` now defaults to `true` (no opt-in needed; can be disabled in config)
- **Dashboard "AI session details" panel**: fixed at the bottom of **Overview** (new `/api/ai-sessions` endpoint, always shown): summary cards (messages/turns/tokens in·out) + model/project distribution + local conversation table + Web AI sessions table
- **Frontend restructure**: removed the separate session-depth panel/page; "AI Insights" is now its own feature and its **sidebar item is hidden when `insights.ai.enabled=false`** (rules stay in that page)
- **Daily report "AI session depth" section**: summary + model/project distribution + local/Web conversation tables (on by default; shown whenever data exists)
- **New `ai_sessions --web` CLI** to include browser-side Web AI conversations
- **Config**: `ai_sessions.enabled` defaults to `true`; new `ai_sessions.token_estimation` (default `true`) and `ai_sessions.web_ai.enabled` (default `true`)

### Added (ROADMAP Phase 3 · Cost & ROI)

- **Per-model cost estimation**: built-in pricing table updated to the latest generations (GPT-5.x/4.1/o3/o4-mini, Claude Fable 5/Opus 5/Sonnet 5/Haiku 4.5, DeepSeek V4, Gemini 3.x/2.5, Qwen3/GLM-5/Kimi/Doubao/Grok-4, etc.); cost = model price × tokens (input/output split)
- **Per-project cost allocation**: cost rolls up to projects through `by_project` (conversation-level), showing how much each project spent
- **Cost data everywhere**: `tools` / `total` / `by_model` / `by_project` / conversation details all carry `cost_in` / `cost_out` / `cost_total`
- **Overview panel**: new "cost estimate" card; cost columns added to model/project distribution and conversation table
- **Daily report "AI session depth" section**: cost summary and per-model/per-project cost display
- **CLI shows cost** in `ai_sessions --json` and text output
- **New config**: `ai_sessions.costs.enabled` (default `true`), `ai_sessions.costs.model_pricing` (empty by default)
- **Two ways to override a price**: ① config `ai_sessions.costs.model_pricing` (`{"gpt-5": [1.25, 10]}` or `{"...": {"input":..,"output":..}}`); ② drop an `ai_pricing.json` in the data directory (same format, highest priority, easy to maintain without touching config). Since prices drift, users are encouraged to maintain their own.

### Testing

- New `test_ai_sessions_costs`: per-model pricing / per-project allocation / custom price override / `costs.enabled=false` off path

### Testing

- Extended `test_ai_sessions` with rounds/tokens/by_model/by_project assertions
- Added `test_ai_sessions_phase1`: multi-turn conversation, model/project breakdown, conversation details, Web AI sessions (including disabled-switch paths)
## [2.2.0] - 2026-08-17

### Added
- **UWP/Store app recognition**: detects WindowsApps packages from process path and maps display names (`config.uwp_app_names`; Calculator/Store/Photos/Terminal, etc.)
- **Admin privilege mode**: `python monitor.py --admin` requests a UAC elevation restart when not running as admin
- **Firefox dwell-time estimation**: estimates dwell time from the interval to the next visit (`config.firefox_dwell_max_s`, default 600s)
- **Update supply-chain security**: update asset download URLs are allowlist-validated (GitHub official domains / `update.api_base` domain); arbitrary third-party URLs are rejected
- **More app adaptations**:
  - Common app display names/classification (Obsidian/Notion/Slack/Teams/WeCom/Feishu/WhatsApp/LINE/Skype/Steam/Epic/Spotify/VLC/PowerToys/uTools, etc.)
  - Social app recognition additions (WeCom/Feishu/Slack/Teams/WhatsApp/LINE/Skype)
  - Browser adaptations (Vivaldi/Yandex/Chromium/Opera GX/Arc/Cent/2345/Sogou/Maxthon/Slimjet)
  - AI tool recognition additions (Codex/Goose/Amazon Q/DSH/pi/Claude Code/Gemini CLI/Continue/Bamboo/Augment/Warp)
  - Terminal TUI tool additions (tmux/screen/btop/k9s/lazydocker/kubectl/ssh/curl/fzf/rg/ncdu/tig, etc.)

## [2.1.1] - 2026-08-17

### Added
- **SQLite consistency check**: `sqlite_store.py --verify` compares JSONL and usage.db record counts; use `--rebuild` to fix differences
- **SQLite fast path for weekly aggregation**: `report.aggregate_days()` queries a date range in one pass; weekly report/dashboard week view no longer scans JSONL day by day
- **Updater tests**: added version compare, check, download verification, script generation, and signal file tests
- **Dashboard update API tests**: covers `/api/update/status|check|download|apply` error states
- **Release asset**: CI now generates and uploads `UsageMonitor.exe.sha256`
- **Coverage scope**: CI coverage now includes `insights/updater/sqlite_store/ai_sessions`

### Fixed
- Fixed several test assertions that depended on JSON whitespace formatting

## [2.1.0] - 2026-08-17

### Added
- **AI session deep stats now supports more tools**:
  - Added default local session directory detection for Cursor / Windsurf / Trae / DeepSeek / Pi Agent (π) / DSH
  - Parser enhanced to handle nested `conversations` / `sessions` / `threads` / `entries` and other common formats
  - Paths such as DSH can still be customized via `ai_sessions.paths`; common directories are auto-detected when not configured

## [2.0.0] - 2026-08-17

### Added
- **AI session deep stats** (§6.4.3, off by default):
  - New `ai_sessions.py`: reads local session files (JSON / JSONL) from opencode / ChatGPT / Claude, etc.,
    and counts AI interaction turns, user/assistant messages, generated lines/chars for a day
  - New "AI Session Depth" panel on the dashboard Insights view; CLI via `python ai_sessions.py --day ...` or
    `python insights.py --ai-sessions`
  - `config.default.json` adds the `ai_sessions` section (`enabled` defaults to false; `paths` is customizable,
    otherwise common directories are auto-detected)
- **SQLite backend usage.db** (§6.5, optional high-performance queries):
  - New `sqlite_store.py`: maintains `usage.db` under `data_root` as an extra mirror/index beside the JSONL raw logs
  - The monitor best-effort writes to SQLite after appending JSONL;
    `python sqlite_store.py --backfill / --rebuild / --query / --status` can backfill and query
  - `config.default.json` adds `sqlite.enabled` (default true; failures degrade silently and never affect JSONL)
- **GitHub Pages docs site** (P2 #7):
  - New `docs/index.md` and `.github/workflows/pages.yml`; pushes to master automatically publish the docs site
- **Review fixes**:
  - Removed the unused `datetime` import in `updater.py` (ruff: 0 violations)
  - Fixed the README Firefox support statement (Firefox places.sqlite has been supported since v1.1.0)

### Changed
- `UsageMonitor.spec` hiddenimports now include `sqlite_store` and `ai_sessions`
- Version bumped to 2.0.0

## [1.6.0] - 2026-08-17

### Added
- **New-version detection**:
  - Automatically checks the latest GitHub Release after startup and shows a tray balloon when a new version is available
    (configurable via `update.check_on_startup`; `update.api_base` can override the check source for testing/mirrors)
  - New "Check for Updates" tray menu item that opens the dashboard Settings page and checks automatically
  - The dashboard "Settings → Software Update" page supports manual checks and shows the latest version, release date, release notes, and size
- **In-app updates**:
  - One-click download of the latest EXE (background thread + progress bar; verifies Content-Length size and the SHA256 digest provided by GitHub; aborts automatically on verification failure)
  - Applying an update: writes an update signal so the daemon exits gracefully → a PowerShell script waits for all processes to exit
    (60-second timeout with a force-kill fallback) → replaces the EXE → restarts automatically → cleans up
  - Dev mode (running from source) only supports checking; in-app installation clearly reports that it is unavailable
  - New `/api/update/check`, `/api/update/status`, `/api/update/download`, and `/api/update/apply`
    (`apply` supports `dryrun` for preview/testing)

## [1.5.0] - 2026-08-17

### Added
- **Expanded AI insight content** (new aggregate dimensions sent to the AI, all numbers only — no private data):
  - Weekday/weekend, first and last active time, average session length, morning/afternoon/evening/late-night distribution
  - Work/study share (AI coding + dev tools + work & study + design/creation)
  - Top 5 subcategories, top 3 terminal tools, and a 7-day comparison of daily active time and session count
- **Customizable AI insights module** (same pattern as app groups, persisted in the data root as `ai_custom.json`):
  - Custom provider presets: add/remove any OpenAI-compatible endpoint; they appear in the Settings → Provider presets dropdown and take priority over built-in presets
  - Prompt customization: toggle which data sections are sent to the AI, adjust the insight count range (1–10), and add a custom instruction (up to 500 chars, appended to the end of the prompt)
  - New `GET/POST /api/ai/module`, `GET /api/ai/module/export`, and `POST /api/ai/module/import`; the Insights page can export/import the whole module config (migration/backup)

## [1.4.0] - 2026-08-16

### Added
- **Graphical install wizard** (`installer.ps1`, zero dependencies): a familiar installer experience — choose the install directory,
  register login auto-start and the daily report scheduled task, create Start Menu/desktop shortcuts, and register in "Add or Remove Programs";
  supports `-Silent` for automation/CI.
- **Graphical uninstaller** (`uninstaller.ps1`): launch from "Add or Remove Programs" or the command line; stops running instances and
  cleans up scheduled tasks/shortcuts/registry entries/program files, with an option to delete recorded data too.
- AI insights support **Ollama local models**:
  - New "Ollama local" provider preset (default `http://127.0.0.1:11434/v1`; API key can be left blank)
  - Selecting Ollama on the Settings page auto-fills the endpoint/model, with a one-click "Refresh Ollama model list"
    (reads locally installed models into a dropdown; shows a clear message when Ollama is not installed/started)
  - New `GET /api/insights/ollama/models` (proxied by the dashboard to local Ollama `/api/tags`, avoiding CORS issues)

## [1.3.1] - 2026-08-16

### Fixed
- Fixed the dashboard "Groups" import button: clicking "Import Config" now opens a file picker instead of leaving a native file input exposed on the page;
  shows progress during import, and clears the selection after a failed import so the same file can be selected again.
- The "Settings → Data Restore" native file input is likewise hidden; added a "Choose backup file" button that shows the selected file name.

## [1.3.0] - 2026-08-16

### Added
- More granular app-group customization:
  - `app_groups.json` adds `app_names` (custom display name per exe) and `group_meta` (group metadata)
  - The dashboard "Groups" view adds a "Display name" editable column; renames take effect immediately for new sessions and the dashboard
  - New `/api/groups/rename`, `/api/groups/export`, and `/api/groups/import`
  - The Groups view adds "Export Config / Import Config" buttons to back up/migrate the whole grouping config
- `classifier.resolve_app_name()` now lets user-defined display names take priority over the `apps` mapping in `config.json`.

## [1.2.1] - 2026-08-16

### Fixed
- Fixed the packaged EXE still falling back to the browser when clicking the tray "Open Dashboard": `_find_electron_shell()`
  now uses `paths.script_dir()` and probes the parent directory (when the EXE is in `dist/`, the project root is the parent),
  and removes the `ELECTRON_RUN_AS_NODE` environment variable that made Electron run in Node mode.

## [1.2.0] - 2026-08-16

A major smart-insights release: offline rule suggestions + optional AI insights (built-in provider presets / custom endpoints / settings-page toggle).

### Added
- Smart insights module (v1.2.0 candidate): new `insights.py` (pure standard library), an offline rule engine that generates
  structured study/game/health/efficiency/balance/trend advice from `report.aggregate()`;
  optional AI suggestions (OpenAI-compatible `chat/completions`, zero dependencies via `urllib`, off by default, privacy-filtered
  aggregate statistics, successful results cached at `<data_root>/YYYY-MM-DD/insights.json`, thread-safe single-flight lock).
- Dashboard "Insights" view (new sidebar entry): `GET /api/insights` (rules on demand + AI from cache) and
  `GET /api/insights/ai?date=…&refresh=1` (force regeneration); rule-card severity colors, AI panel status/error states.
- Dashboard "Settings" page adds an AI optional-feature panel: **enable/disable switch**, built-in provider presets
  (OpenCode Go / OpenAI / DeepSeek / Moonshot / OpenRouter / Zhipu GLM / Qwen / Custom),
  Base URL / API Key / Model / timeout / raw-title sample switch, saved to `config.json`;
  new `GET /api/insights/settings` and `POST /api/insights/settings` (API key is never echoed; leaving it blank preserves the existing one).
- Daily report `report.md` appends a "📌 Today's suggestions" section (offline rule insights only, when `insights.enabled &&
  insights.in_report` is enabled; never makes network requests).
- `config.default.json` adds the `insights` section (rule thresholds + AI endpoint + built-in provider presets);
  the local and repository default is `ai.enabled=false` (optional feature off by default; users can enable it in one click on the Settings page).
- CLI: `python insights.py --day YYYY-MM-DD [--ai] [--json] [--data-root …]`.
- Tests: 8 new smart-insight test groups (rules / AI prompt privacy / AI call / provider presets / cache /
  dashboard API / AI settings API / report section).

### Changed
- `UsageMonitor.spec` `hiddenimports` now includes `insights`.
- Added `ruff.toml` with a baseline lint rule set (E4/E7/E9/F) and cleaned up existing E/F violations.
- Documentation synced: README (Chinese/English) adds a "Smart Insights" section and privacy statement; TODO tracks execution status.

## [1.1.0] - 2026-08-15

Feature-enhancement major release: custom app grouping (P0) + nine feature enhancements (P1) + engineering/quality items (P2).

### Added
- Custom app grouping (P0): `classifier` supports `load/save_app_groups` (TTL 5s cache + atomic writes),
  `all_categories`, and `classify_category` with user overrides taking priority; the dashboard adds GET `/api/groups`,
  POST `/api/groups/set|add|delete` (with Origin validation) and the "Groups" view (6th sidebar item).
- Feature enhancements P1 (nine items): weekly/monthly report views (`/api/week`, `/api/month`), data export
  (`/api/export`, CSV/JSON with injection protection), backup download and restore upload (`/api/backup[/restore]`,
  allowlist + path-traversal protection), light theme toggle, optional access token (`dashboard_token`, hmac
  constant-time comparison, off by default), `classifier` config hot reload (mtime + TTL 3s + shallow copy to avoid pollution),
  per-loop config re-read in the `monitor` loop (`data_root` stays at the startup value), tray balloon notifications (`show_balloon`,
  NIF_INFO + click event opens the report view), and Firefox history support (auto profile discovery, PRTime conversion,
  unified output structure).
- Electron desktop shell (standalone app window instead of the default browser): electron-app/ (Electron 33 shell, ~1280x820 window),
  auto-detects/starts the Python dashboard service, cleans up a self-started service when the window closes, and reuses a tray-kept service;
  `--smoke` mode (start → screenshot → exit, for CI); `monitor.open_dashboard` prefers the Electron shell (packaged EXE > dev mode),
  falls back to the default browser, and `USAGEMON_USE_BROWSER=1` forces the browser fallback; paths environment variables
  `USAGEMON_PROJECT_DIR`/`DATA_ROOT`/`PORT`/`PYTHON`.
- English README (`README.en.md`) with bilingual cross-links.
- Engineering/quality (P2): CHANGELOG.md (keep-a-changelog format), CONTRIBUTING.md contribution guide,
  Issue/PR templates (bug_report / feature_request / pull request), README CI/Release badges,
  antivirus false-positive guidance; CI adds version↔git tag sync validation and coverage reports (uploaded as artifacts).

### Fixed
- Fixed the `do_POST` unknown-path 405 regression; `/api/groups` classification now uses the service `data_root`.
- Fixed a historical `report.py` missing `import re` bug (NameError on the verify path).

### Changed
- gitignore now covers sandbox test/runtime temp directories (`.tmp_*/`).
- Added handover documents (app-group feature live demo + full feature/engineering todo list).
- Tests: all 152 assertions pass (9 new tray-scheduler tests, 14 `test_app_groups` assertions).

## [1.0.0] - 2026-08-13

First official release: a local Windows usage monitoring tool (Phase 1-3 + refined monitoring dimensions).
Pure standard library with zero third-party dependencies; static CPU < 0.1%, memory < 25 MB.

### Added
- Monitoring core (win32core.py, 5s polling, writes only on state change, cross-day isolation, idle truncation, zero writes while static).
- Foreground-window session timing; software inventory scan (registry / Start Menu / processes) + automatic classification + daily auto-refresh;
  social contact recognition (WeChat / QQ / DingTalk) + alias table; browser site classification + URL-level history parsing
  (lock-safe, dwell time, cross-day apportionment); vibe coding monitoring (opencode / pi agent(π) / ChatGPT, etc.,
  process tree + title dual recognition); terminal TUI tool recognition / secondary subcategories / window state / session URL association.
- Daily Markdown summary (overview + hourly distribution + categories + contacts + AI + browser details + inventory summary);
  weekly/monthly reports / JSON export / reclassification / local web dashboard / tray / auto-start.
- Dashboard frontend rebuilt: fixed left sidebar (Overview / Trends / Report / Sessions / Logs 5 views), warm gray dark
  design system (#101318 + amber single accent #e0a53c), restrained rounded corners / thin borders / monospaced numbers, no AI-generated
  look (no purple gradients / glassmorphism / emoji), animations (view switching / number roll / bars / heatmap entry /
  hover feedback / skeleton screens / prefers-reduced-motion), trend heatmap (24h × days), report page Markdown smooth progress rendering,
  session page filtering/search, compact duration formatting, and unified label styles.
- Unified logging: applog.py rolling logs (1 MB × 5), integrated into monitor / report / dashboard;
  `/api/log` endpoint + log view (runtime logs + error logs, auto-refresh every 15s).
- Icon assets (assets/icon.png / icon.ico / tray.ico) + project screenshots + branded README + custom tray
  icon (tray.py loads it first, falls back to the system icon); make_demo_data.py fake demo-data generator.
- Single source of truth for config: config.default.json (DEFAULT_CONFIG loaded from file),
  classifier.py `--sync-config` to validate differences, and completes `editor_exes`.
- Portability: new paths.py (frozen-aware), removing all 13 hardcoded `D:` paths;
  UsageMonitor.spec (EXE uses icon.ico + embedded icon resources).
- CI auto-build: .github/workflows/build.yml (Windows EXE build + auto Release on tag,
  Release permission / idempotent allowUpdates / action-gh-release v2 parameter fixes).
- Unified version number: version.py = 1.0.0; monitor / report / dashboard all support `--version`.

### Fixed
- Security: dashboard `/api/*` validates Origin/Referer must point to `127.0.0.1:<port>`; malicious sites get 403;
  pages add `X-Frame-Options: DENY` + CSP.
- Reliability: usage.jsonl writes use flush + fsync; report.py `--verify`/`--repair`
  (removes broken lines with auto-backup and rebuilds missing daily reports).
- Portability: fixed the hidden bug where the packaged EXE wrote data into the `_MEIPASS` temp directory.
- Frontend: fixed DATA_ROOT double replacement / JSON.parse pre-decoding / double-quote nesting three template-injection bugs;
  HTML responses add `Cache-Control: no-store`; fixed heatmap opacity transition not visible under virtual time.
- Tests: test_all adds 11 dashboard API tests (endpoints / 403 / security headers / error codes / path traversal);
  post-build `UsageMonitor.exe --version` smoke test; all 125 assertions pass the gate.

[2.8.2]: https://github.com/Niangaol/VibeTrace/releases/tag/v2.8.2
[2.8.1]: https://github.com/Niangaol/VibeTrace/releases/tag/v2.8.1
[2.8.0]: https://github.com/Niangaol/VibeTrace/releases/tag/v2.8.0
[2.7.0]: https://github.com/Niangaol/VibeTrace/releases/tag/v2.7.0
[2.2.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.2.0
[2.1.1]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.1.1
[2.1.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.1.0
[2.0.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v2.0.0
[1.6.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.6.0
[1.5.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.5.0
[1.4.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.4.0
[1.3.1]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.3.1
[1.3.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.3.0
[1.2.1]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.2.1
[1.2.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.2.0
[1.1.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.1.0
[1.0.0]: https://github.com/Niangaol/UsageMonitor/releases/tag/v1.0.0
