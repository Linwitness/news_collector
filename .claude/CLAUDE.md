# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Pipeline

```bash
# Run manually from the repo root (requires ANTHROPIC_API_KEY in env or .env)
python3 scripts/run_workflow.py

# Install dependencies
pip install -r requirements.txt
```

No tests exist yet. The pipeline is verified by inspecting generated output files in `output/daily/` and `output/weekly/`.

## Architecture

The pipeline runs in this sequence (all orchestrated by `scripts/run_workflow.py`):

1. **Fetch** (`scripts/fetch_news.py`) — Pulls RSS feeds from `config/providers.json`, scores articles by keyword match against topics in `config/topics.json`, deduplicates by URL, returns top N articles per topic.

2. **Analyze** (`scripts/analyze_news.py`) — For each topic, builds a prompt with the scored articles + standing questions, calls the active LLM (via `get_llm` in `scripts/llm.py`), writes a bilingual (EN + Chinese) markdown report to `output/daily/{topic_id}_{YYYYMMDD}.md`. Retried up to 3 times per topic with backoff in `run_workflow.py`.

3. **Notify** (`scripts/notify.py`) — Builds a concise Chinese digest from the daily reports and sends it via Telegram (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`; skipped if unset). Messages are auto-chunked under Telegram's 4096-char limit.

4. **Weekly summary** (`scripts/weekly_summary.py`) — Runs only on Fridays. Reads the 7 most recent daily reports per topic, synthesizes a weekly digest into `output/weekly/weekly_summary_{YYYYMMDD}.md`, and sends a concise weekly Telegram digest (`build_weekly_digest` in `notify.py`, which extracts sections by H2 position so it is robust to translated headers).

Separately, **`scripts/alert_news.py`** (run hourly via `scripts/alert_cron.sh`) polls the same feeds and pushes new high-scoring headlines to Telegram with zero Claude API calls. It deduplicates via `output/state/seen_urls.json` (shared with the daily run through `mark_urls_seen`) and uses an fcntl lock to avoid overlapping runs.

## Scheduling wrappers

- `scripts/run_cron.sh` — daily pipeline wrapper. Locates the repo from its own path; interpreter from `$PYTHON_BIN` (default `python3`). Idempotent: skips only when **all** topics already have today's report, so a re-run fills in topics that failed transiently.
- `scripts/alert_cron.sh` — alert poller wrapper, same conventions.
- On macOS prefer launchd over cron for the daily job (cron never fires missed jobs when the machine is asleep; launchd's `StartCalendarInterval` catches up on wake). See README for a plist template.

## Configuration

All configuration is in `config/`:
- **`settings.json`** — `active_llm` (selects an entry in `llm_providers`: env var for the key, `base_url`, `model`, `translation_model`), output dir, `max_articles_per_topic`, `hours_lookback`, plus alert tuning (`alert_lookback_hours`, `alert_score_threshold`, `alert_state_path`, `alert_seen_ttl_days`)
- **`topics.json`** — Topic `id`, `name`, and `keywords` array. The `id` field is the filename prefix and the key used in `default_questions.md`.
- **`providers.json`** — RSS feed URLs and source names
- **`config/default_questions.md`** — Standing questions answered in every report. Uses `## Global` for all topics and `## {topic_id}` for topic-specific questions. Parsed by `load_standing_questions()` in `analyze_news.py`.
- **`questions.md`** (repo root) — User-editable one-off questions. Auto-cleared after each pipeline run.

## Key Design Decisions

- **Topic IDs are the canonical key** — The `id` in `topics.json` must match the `##` section headers in `default_questions.md` and is used as the output filename prefix.
- **Single LLM call per topic per day** (plus one translation call) — `analyze_news.py` bundles all articles and questions into one prompt (max 8000 tokens).
- **Provider-agnostic via the anthropic SDK** — DeepSeek is reached through its Anthropic-compatible endpoint, so every provider uses `client.messages.create`. Always read responses with `response_text()` (DeepSeek returns a thinking block before the text).
- **Friday detection** — Weekly summaries trigger automatically via `datetime.now().weekday() == 4` in `run_workflow.py`.
- **questions.md is ephemeral** — It's overwritten with an empty template after each run if questions were present.
- **Alerts make no API calls** — the alert poller ships raw headlines; only the daily/weekly paths spend Claude tokens.
