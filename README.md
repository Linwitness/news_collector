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
- **Output** — `output/papers/papers_{YYYYMMDD}.md` (English + Chinese per topic) and a Chinese Telegram digest with the highlight links.

### Planned improvements

1. **Read selected papers in detail.** The report currently works from titles and abstracts only (and some journal items have no abstract). Fetch the full text (open-access PDF/HTML, e.g. arXiv or OpenAlex `open_access` links) for the selected papers, at least the Highlights, and base summaries, trends and predictions on methods, results and limitations rather than the abstract alone.

## Ask a One-Off Question

Edit `questions.md` and add your question. It will be answered in the next run and then the file will be reset.

## Scheduling

Two wrapper scripts handle scheduling. Both locate the repo from their own path and default to `python3` on `PATH`; set `PYTHON_BIN` to point at a specific interpreter (conda/venv).

- **`scripts/run_cron.sh`** — the daily pipeline. Idempotent: it skips if every topic already has a report for today, and fills in missing topics otherwise, so re-runs never double-spend on API calls.
- **`scripts/alert_cron.sh`** — near-real-time headline alerts pushed to Telegram (no Claude API calls). Deduplicates via `output/state/seen_urls.json`.

### Linux / cron

```
PYTHON_BIN=/path/to/python3
25 17 * * * /path/to/news_collector/scripts/run_cron.sh
0 * * * *   /path/to/news_collector/scripts/alert_cron.sh
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
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>17</integer>
        <key>Minute</key>
        <integer>25</integer>
    </dict>
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

`scripts/papers_cron.sh` runs `run_papers.py` and skips if a report already exists for the current ISO week. Use the plist above with a new `Label` (e.g. `com.example.newscollector.papers`), `scripts/papers_cron.sh` in `ProgramArguments`, `logs/papers.launchd.log` for output, and a weekly `StartCalendarInterval`:
```xml
<key>StartCalendarInterval</key>
<dict>
    <key>Weekday</key><integer>6</integer>   <!-- Saturday -->
    <key>Hour</key><integer>9</integer>
    <key>Minute</key><integer>0</integer>
</dict>
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
│   └── alert_cron.sh        # Alert poller wrapper
├── output/
│   ├── daily/               # {topic_id}_{YYYYMMDD}.md
│   ├── weekly/              # weekly_summary_{YYYYMMDD}.md
│   ├── papers/              # papers_{YYYYMMDD}.md
│   └── state/               # Alert and paper dedup state
├── questions.md             # One-off Q&A (cleared after each run)
├── requirements.txt
└── BACKLOG.txt              # Improvement ideas
```
