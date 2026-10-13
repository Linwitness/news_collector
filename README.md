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

## Project Structure

```
news_collector/
├── config/
│   ├── topics.json          # Topics + keywords (user-editable)
│   ├── providers.json       # RSS feed URLs (user-editable)
│   ├── settings.json        # API/model settings + alert tuning
│   └── default_questions.md # Standing questions (answered every day)
├── scripts/
│   ├── fetch_news.py        # RSS fetching + keyword filtering
│   ├── analyze_news.py      # Claude API analysis
│   ├── weekly_summary.py    # Friday weekly synthesis
│   ├── notify.py            # Telegram digests (daily + weekly)
│   ├── alert_news.py        # Near-real-time headline alerts (no API calls)
│   ├── run_workflow.py      # Main orchestrator
│   ├── run_cron.sh          # Daily wrapper (idempotent)
│   └── alert_cron.sh        # Alert poller wrapper
├── output/
│   ├── daily/               # {topic_id}_{YYYYMMDD}.md
│   ├── weekly/              # weekly_summary_{YYYYMMDD}.md
│   └── state/               # Alert dedup state
├── questions.md             # One-off Q&A (cleared after each run)
├── requirements.txt
└── BACKLOG.txt              # Improvement ideas
```
