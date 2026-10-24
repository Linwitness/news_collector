"""
alert_news.py — Near-real-time news alerts between daily deep-dive runs.

Polls RSS feeds (cron hourly, 06:00-23:00), filters new high-score articles,
and pushes them to Telegram as one line each: the headline is an HTML hyperlink
and link previews are off, so items stay short. The headlines themselves cost no
LLM calls. The first poll of the day (alert_start_hour) looks back
alert_overnight_lookback_hours so overnight headlines are not missed.

When rednote.enabled is set, the same message also carries the rednote home-feed
news from rednote_digest.collect() (headlines first, then rednote items).

State is persisted in output/state/seen_urls.json so the same article is
never alerted twice. Shares the lock and state file with run_workflow.py.
"""

import argparse
import fcntl
import html
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import feedparser
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))

from fetch_news import load_config, parse_published, score_article, clean_html
from notify import send_telegram


DEFAULTS = {
    "alert_lookback_hours": 2,
    "alert_score_threshold": 2,
    "alert_state_path": "output/state/seen_urls.json",
    "alert_seen_ttl_days": 7,
    "alert_start_hour": 6,
    "alert_overnight_lookback_hours": 8,
}


def _load_dotenv(base_dir: str) -> None:
    env_path = os.path.join(base_dir, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def state_paths(settings: dict):
    rel = settings.get("alert_state_path", DEFAULTS["alert_state_path"])
    state_file = os.path.join(BASE_DIR, rel)
    state_dir = os.path.dirname(state_file)
    lock_file = os.path.join(state_dir, ".lock")
    return state_file, lock_file, state_dir


def load_seen(state_file: str, ttl_days: int) -> dict:
    if not os.path.exists(state_file):
        return {}
    try:
        with open(state_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    pruned = {}
    for url, ts in data.items():
        try:
            seen_at = datetime.fromisoformat(ts)
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if seen_at >= cutoff:
            pruned[url] = ts
    return pruned


def save_seen(state_file: str, seen: dict) -> None:
    tmp = state_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(seen, f)
    os.replace(tmp, state_file)


def mark_urls_seen(urls, settings: dict) -> None:
    """Public helper for run_workflow.py to record URLs it processed."""
    if not urls:
        return
    state_file, lock_file, state_dir = state_paths(settings)
    os.makedirs(state_dir, exist_ok=True)
    ttl = settings.get("alert_seen_ttl_days", DEFAULTS["alert_seen_ttl_days"])
    with open(lock_file, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        seen = load_seen(state_file, ttl)
        now = datetime.now(timezone.utc).isoformat()
        for url in urls:
            if url:
                seen.setdefault(url, now)
        save_seen(state_file, seen)


def collect_candidates(providers, topics, lookback_hours, threshold):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; NewsCollectorBot/1.0; "
            "+https://github.com/Linwitness/news_collector)"
        )
    }
    # Map url -> (topic_name, title, source, link)
    candidates = {}

    for provider in providers:
        name = provider["name"]
        rss_url = provider["rss"]
        try:
            resp = requests.get(rss_url, headers=headers, timeout=15)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"  WARNING: could not fetch {rss_url}: {e}")
            continue

        for entry in feed.entries:
            link = getattr(entry, "link", "") or ""
            if not link:
                continue
            published = parse_published(entry)
            if published and published < cutoff:
                continue

            title = clean_html(getattr(entry, "title", ""))
            summary = clean_html(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            )
            entry_text = f"{title} {summary}"

            # Pick the best-scoring topic for this article.
            best = None
            for topic in topics:
                s = score_article(entry_text, topic["keywords"])
                if s >= threshold and (best is None or s > best[0]):
                    best = (s, topic)
            if not best:
                continue
            _, topic = best
            if link in candidates:
                continue
            candidates[link] = {
                "topic_name": topic["name"],
                "title": title,
                "source": name,
                "link": link,
            }
    return candidates


def format_alert(article: dict) -> str:
    """One line in Telegram HTML: the headline links to the article."""
    return (
        f"🔔 【{html.escape(article['topic_name'])}】"
        f"<a href=\"{html.escape(article['link'], quote=True)}\">{html.escape(article['title'])}</a>"
        f" · {html.escape(article['source'])}"
    )


def run(dry_run: bool = False, rednote: bool = True) -> int:
    _load_dotenv(BASE_DIR)
    providers, topics, settings = load_config(BASE_DIR)

    lookback = settings.get("alert_lookback_hours", DEFAULTS["alert_lookback_hours"])
    # Cron polls only 06:00-23:00; the first poll of the day covers the night.
    if datetime.now().hour == settings.get("alert_start_hour", DEFAULTS["alert_start_hour"]):
        lookback = settings.get("alert_overnight_lookback_hours",
                                DEFAULTS["alert_overnight_lookback_hours"])
    threshold = settings.get("alert_score_threshold", DEFAULTS["alert_score_threshold"])
    ttl = settings.get("alert_seen_ttl_days", DEFAULTS["alert_seen_ttl_days"])

    state_file, lock_file, state_dir = state_paths(settings)
    os.makedirs(state_dir, exist_ok=True)

    with open(lock_file, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another news job is running; exiting.")
            return 0

        print(f"Alert poll @ {datetime.now().strftime('%Y-%m-%d %H:%M')} "
              f"(lookback={lookback}h, threshold={threshold})")

        seen = load_seen(state_file, ttl)
        candidates = collect_candidates(providers, topics, lookback, threshold)
        new_articles = [a for url, a in candidates.items() if url not in seen]
        print(f"  candidates: {len(candidates)}, new: {len(new_articles)}")

        rednote_items, rednote_commit = [], (lambda: None)
        if rednote and settings.get("rednote", {}).get("enabled", False):
            try:
                from rednote_digest import collect as collect_rednote
                rednote_items, rednote_commit = collect_rednote(dry_run=dry_run)
            except Exception as e:
                print(f"  WARNING: rednote digest failed: {e}")

        if not new_articles and not rednote_items:
            rednote_commit()
            return 0

        # Headlines first, then rednote items; send_telegram already chunks if needed.
        message = "\n\n".join([format_alert(a) for a in new_articles] + rednote_items)

        if dry_run:
            print("--- DRY RUN — would send ---")
            print(message)
            print("--- end ---")
            return 0

        try:
            send_telegram(message, BASE_DIR, html_mode=True, preview=False)
        except Exception as e:
            print(f"  ERROR sending Telegram: {e}")
            # Do not mark as seen on failure, so we retry next cycle.
            return 1

        now = datetime.now(timezone.utc).isoformat()
        for a in new_articles:
            seen[a["link"]] = now
        save_seen(state_file, seen)
        rednote_commit()
        print(f"  sent {len(new_articles)} alert(s) + {len(rednote_items)} rednote item(s); "
              f"state saved.")
        return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print would-be alerts without sending or updating state.")
    parser.add_argument("--no-rednote", action="store_true",
                        help="Skip the rednote part (RSS headlines only).")
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run, rednote=not args.no_rednote))


if __name__ == "__main__":
    main()
