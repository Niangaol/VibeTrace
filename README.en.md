# VibeTrace · 电脑使用情况监控

> Pure vibe coding artifact · Local-first · Python standard library + ctypes · Zero third-party runtime dependencies

[VibeTrace](https://github.com/Niangaol/VibeTrace) is a local Windows usage monitoring tool. It runs as a resident daemon, samples the foreground window, records software / social contacts / browser / AI coding usage, and produces daily, weekly, and monthly reports plus a local web dashboard.

No build step, no framework, no bundler — pure Python standard library + vanilla JS, with `ctypes` calling Win32 directly. Data stays local by default; no screenshots, no screen recording, no keyboard logging, no chat content reading.

| Web Dashboard | Daily Report | Desktop Shell |
|---|---|---|
| <img src="docs/screenshots/dashboard.png" width="480" alt="Dashboard"> | <img src="docs/screenshots/report.png" width="480" alt="Report"> | <img src="docs/screenshots/desktop_app.png" width="300" alt="Desktop shell"> |

---

## Contents

- [Why VibeTrace](#why-usagemonitor) — what it is and how it compares
- [Quick start](#quick-start) — clone + `python monitor.py`
- [Features](#features) — monitoring / reports / insights / adaptation / updates / security
- [Configuration & access](#configuration--access) — config discovery, env vars, access
- [Architecture](#architecture) — backend module layout
- [Roadmap](#roadmap) — AI coding deep-tracking plan
- [Running tests](#running-tests)
- [Docs](#docs)

---

## Why VibeTrace

Most usage-tracking tools sync data to the cloud, depend on paid services, or miss two dimensions this project targets: AI coding and URL-level browser history.

In most comparable open-source projects, tracking AI coding is either limited to token/session counts in a standalone tool, or handled as a small editor/plugin dimension that is not part of overall computer-usage analysis. VibeTrace treats AI coding time as a dimension alongside software, social contacts, and browsers — tracked within the same monitoring and reporting system, covering both foreground-window / process-tree AI tool timing and optional local AI session deep stats.

Key differences:

- **Local by default, no upload** — data lives under `data_root`; the dashboard listens on 127.0.0.1 only
- **Zero third-party runtime deps** — no psutil / pywin32 / browser extensions / cloud services
- **AI coding monitoring** — process-tree detection of opencode / pi agent / claude in terminals, not just the foreground window
- **URL-level browser history** — Chromium + Firefox visit details with classification and dwell time
- **Optional AI session deep stats** — reads local AI tool session files and counts turns / generated output
- **Open source, free (MIT)**

**vs. the field**:

| | VibeTrace | RescueTime | ManicTime | WakaTime | ActivityWatch |
|---|---|---|---|---|---|
| Local by default, no upload | Yes | Cloud | Partial | Cloud | Yes |
| Open source, free | Yes (MIT) | No | No | Partial | Yes (MPL) |
| Zero third-party runtime deps | Yes | No | No | No | Partial |
| AI coding monitoring (process tree) | Yes | No | Partial | Partial | Partial |
| URL-level browser history | Yes | Partial | Partial | Editor-focused | Partial |
| Local web dashboard | Yes | No | No | No | Dashboard |
| In-app update | Yes | — | — | — | — |

> Note: competitor capability boundaries change with versions; the table reflects general feature scopes.

---

## Quick start

```powershell
git clone https://github.com/Niangaol/VibeTrace.git
cd VibeTrace

# Test run for 30 seconds
python monitor.py --test 30

# Run in foreground
python monitor.py --foreground

# Tray daemon
python monitor.py --tray
```

GUI install / uninstall:

```powershell
powershell -ExecutionPolicy Bypass -File installer.ps1
powershell -ExecutionPolicy Bypass -File uninstaller.ps1
```

To read window titles of elevated (admin) processes:

```powershell
python monitor.py --admin   # auto UAC elevation when not running as admin
```

> **Stopping the daemon**: tray menu "Exit"; Ctrl-C for `--foreground`; managed by Task Scheduler when launched as a scheduled task.

---

## Features

### Monitoring

- Foreground app timing: 5s polling by default, writes only on state change
- No timing while idle/locked: sessions cut off after 3 minutes without input by default
- Software inventory scan: registry uninstall entries, Start Menu shortcuts, running processes
- Social contact recognition: WeChat / QQ / DingTalk / WeCom / Feishu / Slack / Teams, etc.
- Browser activity classification: title keywords → video / coding / studying / other
- URL-level browser history: Chromium (Chrome/Edge/Brave/Opera/Vivaldi, etc.) and Firefox
- AI coding monitoring: process-tree detection of AI CLI tools in terminal/integrated terminal

### Reports & dashboard

- Daily, weekly, monthly reports (Markdown + CSV)
- Local web dashboard with thirteen views: Overview / Trends / Report / Week / Month / Sessions / Timeline / Growth / Compare / Logs / Groups / Insights / Settings
- Export (CSV / JSON), backup / restore

### Insights & AI

- Offline rule engine: study / game / health / efficiency / balance / trend advice
- Optional AI insights: OpenAI-compatible endpoint, privacy-filtered aggregates, off by default
- AI session deep stats: reads local session files from opencode / ChatGPT / Claude / Cursor / Windsurf / Trae / DeepSeek / Pi Agent / DSH / ZCode / Codex (per-tool support matrix and extension guide: [docs/HARNESSES.md](docs/HARNESSES.md), Chinese)
- Alert loop (v2.7): AI cost budget warn/exceed + continuous-work rest reminders via tray balloon, configurable thresholds/cooldown
- Daily goals (v2.7 · optional): total active / coding time targets with streak counter, overview progress panel, off by default
- Adoption proxy (v2.8 · reference only): Git-side retention / rework rough proxies (`/api/adoption`), shown collapsed & greyed-out with a mandatory disclaimer; confidence never "high"; AI-side per-file attribution cut per the spike conclusion
- Constrained queries expanded (v2.8): new templates for period-over-period output comparison, best-focus day, and cost trend, with dual-period parsing and period aliases
- Degradation observability (v2.9.12): 34 intentionally-swallowed exceptions now land in `logs/app.log` and are visible in the dashboard's Log view — when the numbers don't add up you can find out why (behavior unchanged: still no raise, still degraded)

### Adaptation

- Custom app grouping: overlay config, effective immediately
- Common app display names / classification: Obsidian / Notion / Slack / Teams / Steam / Spotify / VLC / PowerToys, etc.
- Browser adaptation: Vivaldi / Yandex / Chromium / Opera GX / Arc / Cent / Sogou / Maxthon / Slimjet, etc.
- AI tool recognition: Codex / Goose / Amazon Q / DSH / Claude Code / Gemini CLI / Continue / Bamboo / Augment / Warp / ZCode, etc.
- Terminal TUI tools: tmux / btop / lazygit / k9s / lazydocker / kubectl / fzf / rg / ncdu / tig, etc.

### Updates & packaging

- Update check: on startup, tray menu, dashboard settings
- In-app update: SHA256 verified download → graceful exit → replace EXE → auto-restart
- Update supply-chain security: download URL allowlist — only GitHub official domains or the `update.api_base` host
- PyInstaller single-file EXE; CI builds a Release with `sha256` on tag push

### Security & privacy

- Dashboard listens on 127.0.0.1 only; all `/api/*` validate Origin / Referer
- Optional access token (`dashboard_token`, HMAC constant-time comparison)
- Title privacy blacklist — matches recorded as `[hidden]`
- AI insights off by default; aggregates only (no titles / URLs / contacts)
- UWP/Store app recognition (`uwp_app_names`)

### Optional backends

- SQLite backend `usage.db`: mirror/index beside JSONL; backfill / rebuild / consistency check
- GitHub Pages docs site: https://niangaol.github.io/VibeTrace/

---

## Configuration & access

### Config discovery

| Item | How it is found |
|---|---|
| Config file | `config.json` (falls back to `config.default.json`) |
| Data root | `data_root`; empty means the program directory |
| Hot reload | monitor re-reads `config.json` each loop (`data_root` keeps the startup value) |
| Alias table | `<data_root>/aliases.json` (not committed) |

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `USAGEMON_PROJECT_DIR` | script directory | project root override |
| `DATA_ROOT` | `config.json` `data_root` | data root override |
| `PORT` | `8765` | dashboard port |
| `PYTHON` | auto-detected | Python used by Electron shell / launcher |
| `USAGEMON_USE_BROWSER` | unset | `=1` forces the system browser (default prefers the Electron desktop shell; falls back to the browser when the shell is missing or fails) |

### Access

```powershell
python dashboard.py --open            # open http://127.0.0.1:8765
python dashboard.py --port 9000       # custom port
VibeTrace.exe --dashboard --open   # via EXE
```

Tray menu: Today's Overview / Open Dashboard / Check for Updates / Pause · Resume / Exit.

---

## Architecture

No build step, no framework — Python standard-library `http.server` + vanilla JS. Core modules:

```
monitor.py         daemon (foreground polling, tray, cross-day aggregation, --admin)
win32core.py       Win32 API (ctypes): foreground window / processes / idle / UWP / admin check
classifier.py      classification, contacts, AI tools, terminal tools, config loading
inventory.py       software inventory scan & auto-classification
report.py          daily/weekly/monthly aggregation, reclassify, verify/repair (SQLite fast path)
dashboard.py       local web dashboard + all /api/* routes
dashboard_util.py  pure functions/utilities for dashboard.py
browser_history.py Chromium + Firefox history parsing (direct read of source DB, no copy)
insights.py        smart insights (offline rules + optional AI)
ai_sessions.py     AI session deep stats (tokens / cost / quality score)
git_insights.py    Git change analysis (range-batched git log)
derived.py         shared data-access framework for derived metrics (day_bundle / series, v2.9.8)
metrics_util.py    single source of truth for stats helpers (entropy / HHI / switch count / merge / format)
tool_registry.py   tool registry (single source of truth: default dirs / parsers / cache exemptions / web domains)
tool_compare.py    multi-tool comparison
growth.py          growth curve / weekly snapshots
query.py           constrained template queries (regex allowlist, no LLM)
advice.py          overview "Advice" column (optional, off by default)
adoption.py        Git-side adoption proxy metrics (read-only, disclaimer + collapsed)
budget.py          cost budget alerts
alerts.py          alert scheduling (budget / break reminders, tray balloon loop)
goals.py           daily goals & streaks
learn.py           online statistical baseline (sliding window + z-score)
timeline.py        Vibe timeline replay
sqlite_store.py    optional SQLite backend + consistency check
updater.py         update check, in-app update, download URL allowlist
tray.py            tray icon
paths.py / applog.py / version.py   path resolution / rolling logs + degradation notes / unified version
```

State lives outside the repo in a runtime directory (date folders + `usage.jsonl`).

> Performance: AI session stats, browser history and SQLite mirror writes all use
> **fingerprint caches / shared connections** (auto-invalidate on mtime+size change,
> behavior unchanged), and the dashboard computes repeated multi-endpoint aggregation
> only once — AI session and browser-history hot paths are ~200× / ~144× faster,
> SQLite writes ~66×. Browser URL association now **reads the source DB directly with
> no full copy** (6.1ms → 0.49ms per call, ~12×); multi-day Git analysis is
> **range-batched** (one `git log` bucketed by committer date; 44 days ≈9.2s → ≈1.4s);
> derived metrics go through the shared `derived.py` framework (13 per-day loops
> converged into one implementation).

---

## Roadmap

AI coding deep-tracking plan:

- **Phase 1 (high priority)**: session-level fine tracking — conversation turns, token estimation, per-model / per-project breakdown
- **Phase 2 (medium-high)**: quality & efficiency — acceptance / retention rate (requires IDE plugins)
- **Phase 3 (medium)**: cost & ROI — model pricing, per-project cost allocation, automated expense reports
- **Phase 4 (medium-low)**: behavior insights — death-loop detection, focus score, Vibe Coding persona analysis

Each phase can be delivered independently. Full plan: [docs/ROADMAP.md](docs/ROADMAP.md) (Chinese).

---

## Running tests

```powershell
python -m pytest tests -q   # ~890 test cases (unit / integration / API / frontend / security / performance / full-chain E2E; measured 882 passed / 7 skipped)
coverage run -m pytest tests/unit tests/integration tests/api tests/security tests/performance tests/e2e -q
coverage report --fail-under=70
ruff check .                # 0 violations
```

CI: tests → coverage (incl. insights/updater/sqlite_store/ai_sessions) → PyInstaller build → EXE smoke → Release on tag.

> If Windows temp-directory permissions cause test failures, clean `%TEMP%\usagemon_hist_*` / `dsh-*` first.

---

## Docs

- [CHANGELOG.md](CHANGELOG.md)（[English](CHANGELOG.en.md)）
- [简体中文 README](README.md)
- [HARNESSES.md](docs/HARNESSES.md)（AI harness support matrix: which tools get timing / session deep-stats / web tracking, and how to extend — Chinese）
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [TODO.md](TODO.md)（handover / todo list）
- [ROADMAP.md](docs/ROADMAP.md)（AI coding deep-tracking plan, Chinese）
- [Requirements](项目需求与开发文档.md)
- GitHub Pages: https://niangaol.github.io/VibeTrace/
