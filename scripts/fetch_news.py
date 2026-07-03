"""
fetch_news.py — Fetch and filter articles from RSS feeds.

Returns a dict mapping topic_id -> list of article dicts.
Each article dict has: title, summary, link, source, published, score.
"""

import feedparser
import requests
import json
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup


def load_config(base_dir: str):
    with open(f"{base_dir}/config/providers.json") as f:
        providers = json.load(f)
    with open(f"{base_dir}/config/topics.json") as f:
        topics = json.load(f)
    with open(f"{base_dir}/config/settings.json") as f:
        settings = json.load(f)
    return providers, topics, settings


def parse_published(entry):
    """Parse published date from a feedparser entry, returning UTC datetime."""
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    # Try raw string fields
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc)
            except Exception:
                pass
    return None


def score_article(entry_text: str, keywords: list[str]) -> int:
    """Return number of keyword matches in the text (case-insensitive)."""
    text_lower = entry_text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def clean_html(raw: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def fetch_all(base_dir: str):
    """
    Fetch articles from all RSS providers, score against each topic,
    and return top-N articles per topic.
    """
    providers, topics, settings = load_config(base_dir)
    hours_lookback = settings.get("hours_lookback", 24)
    max_articles = settings.get("max_articles_per_topic", 20)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_lookback)

    # Accumulate scored articles per topic
    topic_articles = {t["id"]: [] for t in topics}
    seen_urls = set()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; NewsCollectorBot/1.0; "
            "+https://github.com/Linwitness/news_collector)"
        )
    }

    for provider in providers:
        name = provider["name"]
        rss_url = provider["rss"]
        print(f"  Fetching {name} …")
        try:
            resp = requests.get(rss_url, headers=headers, timeout=15)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"    WARNING: could not fetch {rss_url}: {e}")
            continue

        for entry in feed.entries:
            link = getattr(entry, "link", "") or ""
            if not link or link in seen_urls:
                continue

            published = parse_published(entry)
            # If no date, accept article (don't skip — some feeds omit dates)
            if published and published < cutoff:
                continue

            title = clean_html(getattr(entry, "title", ""))
            summary = clean_html(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            )
            entry_text = f"{title} {summary}"

            for topic in topics:
                score = score_article(entry_text, topic["keywords"])
                if score == 0:
                    continue
                topic_articles[topic["id"]].append(
                    {
                        "title": title,
                        "summary": summary[:800],  # truncate for prompt size
                        "link": link,
                        "source": name,
                        "published": published.strftime("%Y-%m-%d %H:%M UTC")
                        if published
                        else "unknown",
                        "score": score,
                    }
                )
                seen_urls.add(link)

    # Sort by score desc, deduplicate by URL per topic, take top N
    result = {}
    for topic in topics:
        tid = topic["id"]
        seen = set()
        deduped = []
        for art in sorted(topic_articles[tid], key=lambda a: a["score"], reverse=True):
            if art["link"] not in seen:
                seen.add(art["link"])
                deduped.append(art)
        result[tid] = deduped[:max_articles]
        print(f"  Topic '{topic['name']}': {len(result[tid])} articles found")

    return result


if __name__ == "__main__":
    import sys, os

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    articles = fetch_all(base)
    for tid, arts in articles.items():
        print(f"\n=== {tid} ===")
        for a in arts[:3]:
            print(f"  [{a['score']}] {a['title']} ({a['source']})")
