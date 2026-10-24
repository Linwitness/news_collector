# News Collector

Automated daily/weekly news analysis pipeline. Fetches RSS feeds, analyzes with Claude, and outputs bilingual (English + Chinese) markdown reports, with optional Telegram delivery and near-real-time headline alerts.

## Setup

```bash
git clone https://github.com/Linwitness/news_collector.git
cd news_collector
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys
```

`.env` variables:

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | if Anthropic active | Claude API access for analysis |
| `TELEGRAM_BOT_TOKEN` | optional | Telegram bot for digests & alerts |
| `TELEGRAM_CHAT_ID` | optional | Chat that receives the messages |
| `GENERAL_API_KEY` | if DeepSeek active | DeepSeek API key (current active LLM provider) |

Without the Telegram variables the pipeline still runs and writes reports; only the notifications are skipped.

### Switching LLM provider

In `config/settings.json`, set `active_llm` to `"deepseek"` or `"anthropic"`. Each entry under `llm_providers` names its key's env var, `base_url`, analysis `model` and `translation_model`.

## Run Manually

```bash
python3 scripts/run_workflow.py
```

Output:
- `output/daily/{topic_id}_{YYYYMMDD}.md` — one per topic per day
- `output/weekly/weekly_summary_{YYYYMMDD}.md` — generated every Friday
- Telegram: a daily digest after each run, plus a weekly digest on Fridays

## Configure

| File | Purpose |
|---|---|
| `config/topics.json` | Topics and keywords to track |
| `config/providers.json` | RSS feed URLs |
| `config/settings.json` | API key env var, model, lookback windows, alert tuning |
| `config/default_questions.md` | Standing questions answered every day |
| `questions.md` | One-off questions — answered next run, then cleared |

## Add a Topic

Edit `config/topics.json`:
```json
{
  "id": "my_topic",
  "name": "My Topic Name",
  "keywords": ["keyword1", "keyword2", "keyword3"]
}
```

## Add an RSS Feed

Edit `config/providers.json`:
```json
{ "name": "My Source", "rss": "https://example.com/feed.xml" }
```

## Weekly Paper Report

A separate weekly job collects new research papers (plus a few fusion news items) and writes an analysis with predictions. Every item it discusses is linked.

```bash
python3 scripts/run_papers.py            # normal run
python3 scripts/run_papers.py --fresh    # ignore "already reported" state (for testing)
python3 scripts/fetch_papers.py          # fetch only: show candidates per topic, no LLM calls
```

- **Topics** — `config/paper_topics.json`: `name`, a `description` of the scope (used by the LLM), `keywords` (scoring), `min_score`, an `arxiv_query`, `queries` for OpenAlex/OSTI, and optional `require` groups (an item must match one keyword from each group).
- **Sources** — `config/paper_sources.json`: arXiv, OpenAlex (most journals), OSTI (DOE reports), journal RSS feeds (Nuclear Fusion, PPCF, Fusion Eng. Des., Nucl. Mater. Energy, J. Nucl. Mater., Physics of Plasmas), and fusion news feeds limited to the development topic (`"topics"`, `"kind": "news"`). An RSS source can set `core_topics` (its papers count as on-topic even without keyword hits) and `openalex_issn` (fill missing abstracts from OpenAlex — ScienceDirect feeds carry titles only).
- **Pipeline** — fetch the last `papers.lookback_days`, drop corrections/retractions, merge preprint/journal/dataset versions of a work, skip papers already reported (`output/state/seen_papers.json`), keyword-score, then: an LLM triage per topic (thinking on, to screen out pseudo-science) picks up to `papers.max_papers_per_topic`, each paper is kept in a single topic — one whose `milestones` description the triage matched it to (e.g. a record result goes to Fusion Energy Development and into its Highlights), else its best-scoring topic — and a report call writes `papers.highlights_per_topic` Highlights, a Paper Digest of every other selected paper, Research Trends & Analysis, Compared with Last Week, and checkable Predictions. Links are checked against the fetched items; anything else is removed. A topic's optional `report_guidance` is passed to the report prompt.
- **Output** — `output/papers/papers_{YYYYMMDD}.md` (English + Chinese per topic) and a Chinese Telegram digest: per topic, a 2–4 sentence summary of each Highlights paper with its link right after it, then the predictions (trimmed so the summaries are at least ¾ of the text).

### Planned improvements

1. **Read selected papers in detail.** The report currently works from titles and abstracts only (and some journal items have no abstract). Fetch the full text (open-access PDF/HTML, e.g. arXiv or OpenAlex `open_access` links) for the selected papers, at least the Highlights, and base summaries, trends and predictions on methods, results and limitations rather than the abstract alone.

## rednote Digest

Every hour (06:00–23:00) the news alert also reads new notes from your rednote (Xiaohongshu) home feed. It adds the timely news among them to the same Telegram message, after the headlines: a hyperlinked title with the author and post time (e.g. `10-23 14:05（3小时前）`, so stale posts stand out), then a 1–3 sentence Chinese summary. It adds nothing in an hour with no rednote news.

```bash
python3 -m pip install playwright
python3 -m playwright install --no-shell chromium   # Playwright's own browser, ~150 MB
python3 scripts/rednote_digest.py --login            # one-time: scan the QR code with the rednote app
python3 scripts/rednote_digest.py --dry-run          # rednote part alone: print the notes and the message; send nothing
```

- **Login** — rednote's web login is QR code or SMS only, so there's no password to configure. `--login` opens a visible browser; after you scan, the session is kept in a browser profile outside the repo (`rednote.profile_dir`, default `~/.news_collector/rednote_profile`). It lasts a few weeks. When it expires, or rednote shows a verification page, the job sends one Telegram warning per day asking you to run `--login` again. It never tries to solve a captcha.
- **Each run** loads the home feed headless and screens up to `rednote.screen_per_run` (30) unseen notes by title in one LLM call. It opens only the promising ones (at most `rednote.notes_per_run`, 10), the way a user would. A second LLM call picks the significant ones (`rednote.significance`) and summarizes them from the note text only. Screened notes are tracked in `output/state/rednote_seen.json`. Set `rednote.enabled` to `false` to turn the rednote part off.
- **Caveat** — automated access is against rednote's terms. The job is low-volume, but there is some risk of a verification prompt or an account restriction.

## Run on a Raspberry Pi

An always-on Raspberry Pi avoids the laptop's sleep and charging gaps. `deploy/pi/` sets one up from a single SD card, with no monitor or keyboard:

1. Flash Raspberry Pi OS Lite (64-bit) onto the card.
2. Build a bundle: `news_collector/` (the working tree with `.env` and `output/`), plus `rednote_state.json` from `python3 scripts/rednote_digest.py --export-state rednote_state.json`.
3. Run `WIFI_SSID=… WIFI_PASSWORD=… python3 deploy/pi/prepare_bootfs.py /Volumes/bootfs bundle.tar.gz`. This writes the cloud-init `user-data` (hostname `newspi`, user `lin` with your SSH key, key-only SSH) and `network-config`, and copies the setup files.
4. Boot the Pi.

On first boot, `newspi-setup.service` runs `deploy/pi/setup.sh`, retrying every 5 min until it succeeds. It:
- installs packages and a venv;
- installs Playwright Chromium, falling back to the system Chromium via `REDNOTE_BROWSER_EXECUTABLE`;
- restores `.env`, `output/` and the rednote login, then deletes the bundle from the card;
- installs `deploy/pi/crontab.txt`;
- self-tests the LLM and feeds;
- reports ✅ or ❌ to Telegram. The log is `/var/log/newspi-setup.log`, also copied to the card's boot partition.

Then `ssh lin@newspi.local` (or `ssh lin@newspi` from anywhere once both machines are on Tailscale). Turn the Mac's jobs off (crontab line, `launchctl bootout` of the agents) so only one machine sends.

To read the reports on the Mac, `scripts/sync_reports.sh` mirrors the Pi's `output/` into this checkout with rsync over SSH. It tries `newspi` (Tailscale) first, then `newspi.local`. A LaunchAgent runs it every 60 minutes, with the same plist layout as above, using `StartInterval` 3600 and `RunAtLoad`.

## Ask a One-Off Question

Edit `questions.md` and add your question. It will be answered in the next run and then the file will be reset.

## Scheduling

Two wrapper scripts handle scheduling. Both locate the repo from their own path and default to `python3` on `PATH`; set `PYTHON_BIN` to point at a specific interpreter (conda/venv).

- **`scripts/run_cron.sh`** — the daily pipeline. Idempotent: it skips if every topic already has a report for today (and, on Fridays, the weekly summary exists), fills in missing topics otherwise, and runs `run_workflow.py --weekly-only` when only Friday's weekly summary is missing — so re-runs never double-spend on API calls.

Both `run_cron.sh` and `papers_cron.sh` first wait up to 30 minutes for the network (`scripts/wait_for_network.sh`; right after a wake it is often not up yet) and exit without API calls if it stays down. Inside a run, a topic that fails (e.g. a dropped connection) is retried 3 times and then skipped, keeping the others. Because the wrappers are idempotent, a second launchd slot later in the day is a cheap safety net: it only does work if the first run failed.
- **`scripts/alert_cron.sh`** — near-real-time headline alerts pushed to Telegram (no LLM API calls), hourly 06:00–23:00. Each alert is one line, `🔔 【Topic】Headline · Source`, where the headline is a hyperlink and link previews are off. The 06:00 poll looks back `alert_overnight_lookback_hours` (8) to catch overnight news. Deduplicates via `output/state/seen_urls.json`.
  When `rednote.enabled` is set, the same hourly message also carries the rednote news (see "rednote Digest"); `alert_news.py --no-rednote` skips it.

### Linux / cron

```
PYTHON_BIN=/path/to/python3
25 17 * * * /path/to/news_collector/scripts/run_cron.sh
0 6-23 * * * /path/to/news_collector/scripts/alert_cron.sh
```

### macOS / launchd (recommended for the daily job)

macOS `cron` silently skips jobs if the machine is asleep at the scheduled minute. A launchd LaunchAgent with `StartCalendarInterval` runs the missed job on next wake instead. Create `~/Library/LaunchAgents/com.example.newscollector.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.example.newscollector.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/path/to/news_collector/scripts/run_cron.sh</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHON_BIN</key>
        <string>/path/to/python3</string>
    </dict>
    <!-- 17:25 plus a 20:25 retry slot (a no-op when 17:25 succeeded) -->
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key><integer>17</integer>
            <key>Minute</key><integer>25</integer>
        </dict>
        <dict>
            <key>Hour</key><integer>20</integer>
            <key>Minute</key><integer>25</integer>
        </dict>
    </array>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

Load it with:
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.newscollector.daily.plist
```

### Weekly paper report (launchd)

`scripts/papers_cron.sh` runs `run_papers.py` and skips if a report already exists for the current ISO week. Use the plist above with a new `Label` (e.g. `com.example.newscollector.papers`), `scripts/papers_cron.sh` in `ProgramArguments`, `logs/papers.launchd.log` for output, and a weekly `StartCalendarInterval` with retry slots (skipped once the week's report exists; `run_papers.py` writes no report and exits 1 if no source returned anything):
```xml
<key>StartCalendarInterval</key>
<array>
    <dict>
        <key>Weekday</key><integer>6</integer>   <!-- Saturday 09:00 -->
        <key>Hour</key><integer>9</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <dict>
        <key>Weekday</key><integer>6</integer>   <!-- Saturday 15:00 retry -->
        <key>Hour</key><integer>15</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <dict>
        <key>Weekday</key><integer>0</integer>   <!-- Sunday 09:00 retry -->
        <key>Hour</key><integer>9</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
</array>
```

## Project Structure

```
news_collector/
├── config/
│   ├── topics.json          # Topics + keywords (user-editable)
│   ├── providers.json       # RSS feed URLs (user-editable)
│   ├── paper_topics.json    # Paper-report topics, keywords, search queries
│   ├── paper_sources.json   # Paper sources (arXiv, OpenAlex, OSTI, journal/news RSS)
│   ├── settings.json        # API/model settings + alert tuning
│   └── default_questions.md # Standing questions (answered every day)
├── scripts/
│   ├── fetch_news.py        # RSS fetching + keyword filtering
│   ├── analyze_news.py      # Claude API analysis
│   ├── weekly_summary.py    # Friday weekly synthesis
│   ├── notify.py            # Telegram digests (daily + weekly)
│   ├── alert_news.py        # Near-real-time headline alerts (no API calls)
│   ├── fetch_papers.py      # Paper fetching, dedup, scoring
│   ├── analyze_papers.py    # Paper triage + weekly paper report
│   ├── run_papers.py        # Weekly paper-report entry point
│   ├── papers_cron.sh       # Weekly paper wrapper (idempotent per ISO week)
│   ├── run_workflow.py      # Main orchestrator
│   ├── run_cron.sh          # Daily wrapper (idempotent)
│   ├── alert_cron.sh        # Alert poller wrapper (also runs the rednote part)
│   └── rednote_digest.py    # rednote home-feed news for the hourly alert (Playwright + LLM)
├── output/
│   ├── daily/               # {topic_id}_{YYYYMMDD}.md
│   ├── weekly/              # weekly_summary_{YYYYMMDD}.md
│   ├── papers/              # papers_{YYYYMMDD}.md
│   └── state/               # Alert and paper dedup state
├── questions.md             # One-off Q&A (cleared after each run)
├── requirements.txt
└── BACKLOG.txt              # Improvement ideas
```
