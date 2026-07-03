"""
run_workflow.py — Main orchestrator for the news collector pipeline.

Run manually:
    python3 scripts/run_workflow.py

Or on a schedule via scripts/run_cron.sh (see README "Scheduling").
"""

import json
import os
import sys
import time
from datetime import datetime

# Ensure the project root is on sys.path so sibling scripts are importable
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))


def _load_dotenv(base_dir: str) -> None:
    """Load .env file into os.environ without overriding existing vars."""
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

_load_dotenv(BASE_DIR)

from fetch_news import fetch_all
from analyze_news import analyze_topic, load_questions, load_standing_questions
from weekly_summary import generate_weekly


QUESTIONS_TEMPLATE = """# User Questions
<!-- Add your one-off questions below. They will be answered in the next run and then cleared. -->

"""


def load_settings(base_dir: str) -> dict:
    with open(os.path.join(base_dir, "config", "settings.json")) as f:
        return json.load(f)


def load_topics(base_dir: str) -> list[dict]:
    with open(os.path.join(base_dir, "config", "topics.json")) as f:
        return json.load(f)


def clear_questions(questions_path: str) -> None:
    with open(questions_path, "w") as f:
        f.write(QUESTIONS_TEMPLATE)


def is_friday() -> bool:
    return datetime.now().weekday() == 4  # Monday=0, Friday=4


def main():
    print(f"\n{'='*60}")
    print(f"News Collector — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    settings = load_settings(BASE_DIR)
    topics = load_topics(BASE_DIR)

    output_dir = os.path.join(BASE_DIR, settings["output_dir"], "daily")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

    questions_path = os.path.join(BASE_DIR, "questions.md")
    default_questions_path = os.path.join(BASE_DIR, "config", "default_questions.md")

    # 1. Fetch news
    print("Step 1: Fetching news from RSS feeds …")
    articles_by_topic = fetch_all(BASE_DIR)

    # 2. Load questions
    user_questions = load_questions(questions_path)

    if user_questions:
        print(f"\nOne-off questions found:\n{user_questions}\n")
    else:
        print("\nNo one-off questions today.\n")

    # 3. Analyze each topic and write daily MD
    print("Step 2: Analyzing topics with Claude …")
    date_str = datetime.now().strftime("%B %d, %Y")
    date_tag = datetime.now().strftime("%Y%m%d")

    reports = []
    for topic in topics:
        tid = topic["id"]
        articles = articles_by_topic.get(tid, [])
        standing_questions = load_standing_questions(default_questions_path, tid)
        print(f"\n  Topic: {topic['name']} ({len(articles)} articles)")

        # Retry on transient failures (e.g. network blips) so a single error
        # doesn't silently drop a whole topic for the day.
        md_content = None
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                md_content = analyze_topic(
                    topic=topic,
                    articles=articles,
                    standing_questions=standing_questions,
                    user_questions=user_questions,
                    settings=settings,
                    date_str=date_str,
                )
                break
            except Exception as e:
                if attempt < max_attempts:
                    delay = 20 * attempt  # 20s, 40s
                    print(f"  WARNING: analyzing '{topic['name']}' failed "
                          f"(attempt {attempt}/{max_attempts}): {e} — retrying in {delay}s")
                    time.sleep(delay)
                else:
                    print(f"  ERROR analyzing topic '{topic['name']}' "
                          f"after {max_attempts} attempts: {e}")
        if md_content is None:
            continue

        out_path = os.path.join(output_dir, f"{tid}_{date_tag}.md")
        with open(out_path, "w") as f:
            f.write(md_content)
        print(f"  Written: {out_path}")
        reports.append((topic["name"], len(articles), md_content))

    # Step 2a. Mark processed URLs as seen so the alert poller skips them.
    try:
        from alert_news import mark_urls_seen
        processed_urls = [
            art["link"]
            for arts in articles_by_topic.values()
            for art in arts
            if art.get("link")
        ]
        mark_urls_seen(processed_urls, settings)
    except Exception as e:
        print(f"  WARNING: could not update seen-state: {e}")

    # Step 2b. Send Telegram digest
    print("\nStep 2b: Sending Telegram digest …")
    try:
        from notify import build_digest, send_telegram
        digest = build_digest(date_str, reports)
        send_telegram(digest, BASE_DIR)
        print("  Telegram notification sent.")
    except Exception as e:
        print(f"  WARNING: Telegram notification failed: {e}")

    # 4. Clear one-off questions
    if user_questions:
        print("\nStep 3: Clearing answered questions …")
        clear_questions(questions_path)
        print("  questions.md reset to empty template.")
    else:
        print("\nStep 3: No questions to clear.")

    # 5. Weekly summary (Fridays only)
    if is_friday():
        print("\nStep 4: Today is Friday — generating weekly summary …")
        try:
            weekly_reports = generate_weekly(BASE_DIR, topics, settings)
            if weekly_reports:
                try:
                    from notify import build_weekly_digest, send_telegram
                    weekly_digest = build_weekly_digest(date_str, weekly_reports)
                    send_telegram(weekly_digest, BASE_DIR)
                    print("  Weekly Telegram digest sent.")
                except Exception as e:
                    print(f"  WARNING: weekly Telegram digest failed: {e}")
        except Exception as e:
            print(f"  ERROR generating weekly summary: {e}")
    else:
        day_name = datetime.now().strftime("%A")
        print(f"\nStep 4: Skipping weekly summary (today is {day_name}, not Friday).")

    print(f"\n{'='*60}")
    print("Done.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
