"""
run_papers.py — Weekly academic-paper report.

Run manually:
    python3 scripts/run_papers.py            # normal weekly run
    python3 scripts/run_papers.py --fresh    # ignore seen-paper state (testing)

Or weekly via scripts/papers_cron.sh (see README "Scheduling").
Writes output/papers/papers_{YYYYMMDD}.md and sends a Telegram digest.
"""

import json
import os
import sys
import time
from datetime import datetime

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

from fetch_papers import fetch_all_papers, load_config, mark_papers_seen
from analyze_papers import assign_single_topic, triage_topic, write_topic_report


def _with_retries(label: str, fn, *args):
    """Run fn(*args) up to 3 times with backoff; return None if all fail."""
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args)
        except Exception as e:
            if attempt < max_attempts:
                delay = 20 * attempt
                print(f"  WARNING: {label} failed (attempt {attempt}/{max_attempts}): "
                      f"{e} — retrying in {delay}s")
                time.sleep(delay)
            else:
                print(f"  ERROR: {label} failed after {max_attempts} attempts: {e}")
    return None


def main():
    fresh = "--fresh" in sys.argv
    print(f"\n{'='*60}")
    print(f"Paper Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}" + (" (fresh)" if fresh else ""))
    print(f"{'='*60}\n")

    _sources, topics, settings = load_config(BASE_DIR)
    papers_dir = os.path.join(BASE_DIR, settings["output_dir"], "papers")
    os.makedirs(papers_dir, exist_ok=True)
    date_str = datetime.now().strftime("%B %d, %Y")
    date_tag = datetime.now().strftime("%Y%m%d")

    print("Step 1: Fetching papers …")
    candidates = fetch_all_papers(BASE_DIR, fresh=fresh)
    if not any(candidates.values()):
        # Every source failed or returned nothing (e.g. no network). Write no
        # report, so papers_cron.sh's next slot this week tries again.
        print("\nERROR: no items fetched from any source (network?) — no report written.")
        sys.exit(1)

    print("\nStep 2: Selecting relevant papers per topic …")
    selected = {}
    for topic in topics:
        print(f"\n  Topic: {topic['name']}")
        selected[topic["id"]] = _with_retries(
            f"triage for '{topic['name']}'", triage_topic,
            topic, candidates.get(topic["id"], []), settings,
        ) or []
    selected = assign_single_topic(selected)
    print("\n  After keeping each paper in one topic: " +
          ", ".join(f"{tid} {len(items)}" for tid, items in selected.items()))

    print("\nStep 3: Writing reports …")
    sections, reports, used_keys = [], [], []
    for topic in topics:
        items = selected[topic["id"]]
        print(f"\n  Topic: {topic['name']}")
        if not items:
            print("    No relevant papers this week.")
            continue
        result = _with_retries(
            f"report for '{topic['name']}'", write_topic_report,
            topic, items, settings, papers_dir, date_str, date_tag,
        )
        if not result:
            continue
        english, chinese = result
        sections.append(english + "\n\n---\n---\n\n" + chinese)
        reports.append((topic["name"], len(items), english, chinese))
        used_keys += [it["key"] for it in items]

    if not sections:
        print("\nNo paper report generated.")
        return

    out_path = os.path.join(papers_dir, f"papers_{date_tag}.md")
    with open(out_path, "w") as f:
        f.write("\n\n---\n\n".join(sections))
    print(f"\n  Written: {out_path}")
    mark_papers_seen(BASE_DIR, settings, used_keys)

    print("\nStep 4: Sending Telegram digest …")
    try:
        from notify import build_papers_digest, send_telegram
        send_telegram(build_papers_digest(date_str, reports), BASE_DIR)
        print("  Telegram notification sent.")
    except Exception as e:
        print(f"  WARNING: Telegram notification failed: {e}")

    print(f"\n{'='*60}\nDone.\n{'='*60}\n")


if __name__ == "__main__":
    main()
