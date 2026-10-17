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

4. **Weekly summary** (`scripts/weekly_summary.py`) — Runs only on Fridays. Reads the English half of the 7 most recent daily reports per topic, plus that topic's own section of the previous weekly file (`get_last_weekly`), synthesizes a weekly digest into `output/weekly/weekly_summary_{YYYYMMDD}.md`, and sends a concise weekly Telegram digest (`build_weekly_digest` in `notify.py`, which extracts sections by H2 position so it is robust to translated headers). `check_weekly()` verifies the 5 required sections and retries a topic once before keeping it with a warning. Each topic's weekly is also retried 3 times on exceptions (20 s/40 s backoff) and then skipped, so one failure no longer loses the whole file (a connection reset did on 2026-10-16). `run_weekly()` holds this step; `run_workflow.py --weekly-only` runs just it.

**Weekly paper report** (separate entry point `scripts/run_papers.py`, scheduled via `scripts/papers_cron.sh`; independent of the news run). `fetch_papers.py` pulls the last 7 days from `config/paper_sources.json` (arXiv API, OpenAlex paged with `page=` up to 200 results per query — `cursor=` paging returns 401 without an API key since 2026-10, OSTI, journal RSS, plus fusion news feeds restricted to `fusion_development`) for the topics in `config/paper_topics.json`. It drops corrections/retractions, fills missing abstracts for title-only feeds from OpenAlex (`openalex_issn`), gives papers from a source's `core_topics` journals a passing score, dedupes by normalized title and then merges near-identical titles (word-set Jaccard ≥ 0.6), skips papers already reported (`output/state/seen_papers.json`) and stale undated feed items (ScienceDirect feeds have no dates), then keyword-scores with whole-word matching. `run_papers.py` triages every topic first (`task="papers"`, thinking on — thinking off let pseudo-scientific Zenodo preprints through; up to `max_papers_per_topic`). A topic with a `milestones` description gets a JSON object back from triage that also flags milestone items. `assign_single_topic` then keeps each paper in one topic: a topic that flagged it as a milestone wins, else its highest-scoring topic. Milestone items are marked in the report prompt and go to Highlights. Then per topic a report call (`get_llm(settings, task="papers")`, thinking on) writes 5 sections, then translation. `fix_links()` maps every link back to an input URL (matching by DOI, since the model sometimes rewrites publisher URLs as doi.org) and removes any other link, so no invented URLs survive. `_get` retries per-second 429s but fails fast when Retry-After exceeds 60 s: that means the anonymous OpenAlex daily budget (1000 credits, 10 per search page, shared per IP) is spent, and the source is skipped with a warning. Repeated test fetches in one day can exhaust it. Output: `output/papers/papers_{YYYYMMDD}.md` and `build_papers_digest` in `notify.py`.

Separately, **`scripts/alert_news.py`** (run hourly via `scripts/alert_cron.sh`) polls the same feeds and pushes new high-scoring headlines to Telegram with zero Claude API calls. It deduplicates via `output/state/seen_urls.json` (shared with the daily run through `mark_urls_seen`) and uses an fcntl lock to avoid overlapping runs.

## Scheduling wrappers

- `scripts/run_cron.sh` — daily pipeline wrapper. Locates the repo from its own path; interpreter from `$PYTHON_BIN` (default `python3`). Idempotent: skips only when **all** topics already have today's report and, on Fridays, `weekly_summary_{today}.md` exists; if only the weekly is missing it runs `run_workflow.py --weekly-only`.
- `scripts/alert_cron.sh` — alert poller wrapper, same conventions.
- `scripts/papers_cron.sh` — weekly paper-report wrapper, same conventions; skips if a `papers_*.md` exists for the current ISO week. `run_papers.py` exits 1 without writing a report if no source returned anything.
- `scripts/wait_for_network.sh` — sourced by `run_cron.sh` and `papers_cron.sh`; polls for up to 30 min before spending API calls (on 2026-10-17 the network was down after wake and every paper source failed DNS).
- Installed launchd jobs have retry slots: daily 17:25 + 20:25; papers Sat 09:00, Sat 15:00, Sun 09:00. The idempotency guards make retries no-ops after a success.
- On macOS prefer launchd over cron for the daily job (cron never fires missed jobs when the machine is asleep; launchd's `StartCalendarInterval` catches up on wake). See README for a plist template.

## Configuration

All configuration is in `config/`:
- **`settings.json`** — `active_llm` (selects an entry in `llm_providers`: env var for the key, `base_url`, `model`, `translation_model`), output dir, `max_articles_per_topic`, `hours_lookback`, plus alert tuning (`alert_lookback_hours`, `alert_score_threshold`, `alert_state_path`, `alert_seen_ttl_days`)
- **`topics.json`** — Topic `id`, `name`, and `keywords` array. The `id` field is the filename prefix and the key used in `default_questions.md`.
- **`providers.json`** — RSS feed URLs and source names
- **`paper_topics.json`** / **`paper_sources.json`** — paper-report topics (`description` for the LLM, `keywords`, `min_score`, `arxiv_query`, `queries`, optional `require` keyword groups) and sources (`type`: `arxiv`/`openalex`/`osti`/`rss`; RSS entries may set `topics` and `"kind": "news"`). Tuning lives in the `papers` block of `settings.json`.
- **`config/default_questions.md`** — Standing questions answered in every report. Uses `## Global` for all topics and `## {topic_id}` for topic-specific questions. Parsed by `load_standing_questions()` in `analyze_news.py`.
- **`questions.md`** (repo root) — User-editable one-off questions. Auto-cleared after each pipeline run.

## Key Design Decisions

- **Topic IDs are the canonical key** — The `id` in `topics.json` must match the `##` section headers in `default_questions.md` and is used as the output filename prefix.
- **Single LLM call per topic per day** (plus one translation call) — `analyze_news.py` bundles all articles and questions into one prompt. Output caps are per-provider in settings.json (default 8000 / 12000 for translation; DeepSeek 16000 / 20000).
- **Provider-agnostic via the anthropic SDK** — DeepSeek is reached through its Anthropic-compatible endpoint, so every call goes through `create(llm, prompt, translation=...)` in `scripts/llm.py`. It streams, so there's no non-streaming `max_tokens` ceiling. `get_llm(settings, task=None)` returns an `LLM` object. `task="weekly"` merges the provider's `"weekly"` override block into the analysis settings; translation always uses the base settings. `"thinking"` in a provider config is `false` (disabled), a dict sent as-is (e.g. `{"type": "enabled"}`), or absent (provider default). DeepSeek thinks by default, and thinking counts against `max_tokens` (which once produced empty daily reports). So DeepSeek runs thinking off for daily reports and translation, and thinking on for the weekly synthesis. In testing, thinking off invented an event there and graded last week's predictions too generously. `response_text()` raises on an empty reply so the retry logic catches it.
- **Friday detection** — Weekly summaries trigger automatically via `datetime.now().weekday() == 4` in `run_workflow.py`.
- **questions.md is ephemeral** — It's overwritten with an empty template after each run if questions were present.
- **Alerts make no API calls** — the alert poller ships raw headlines; only the daily/weekly paths spend Claude tokens.
