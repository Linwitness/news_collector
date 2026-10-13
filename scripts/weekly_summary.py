"""
weekly_summary.py — Generate a weekly synthesis from the past 7 daily MDs.

Called every Friday by run_workflow.py.
Outputs output/weekly/weekly_summary_{YYYYMMDD}.md
"""

import json
import os
import glob
from datetime import datetime

from llm import get_llm, response_text


def get_recent_daily_files(daily_dir: str, topic_id: str, n: int = 7) -> list[str]:
    """Return up to n most recent daily MD files for a topic, newest first."""
    pattern = os.path.join(daily_dir, f"{topic_id}_*.md")
    files = sorted(glob.glob(pattern), reverse=True)
    return files[:n]


def get_last_weekly(weekly_dir: str) -> str:
    """Return the content of the most recent weekly summary, or empty string."""
    pattern = os.path.join(weekly_dir, "weekly_summary_*.md")
    files = sorted(glob.glob(pattern), reverse=True)
    if not files:
        return ""
    try:
        with open(files[0]) as f:
            return f.read()
    except Exception:
        return ""


def read_file(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


def build_weekly_prompt(
    topic: dict,
    daily_contents: list[tuple[str, str]],  # (filename, content)
    last_weekly: str,
    date_str: str,
) -> str:
    daily_block = ""
    for fname, content in daily_contents:
        daily_block += f"\n\n### File: {os.path.basename(fname)}\n\n{content}\n\n---"

    prior_weekly_block = ""
    if last_weekly:
        prior_weekly_block = f"""
## Prior Weekly Summary (for continuity reference):

{last_weekly[:4000]}

---
"""

    return f"""You are an expert geopolitical analyst producing a weekly synthesis report.

Today is {date_str} (Friday). Topic: **{topic['name']}**

{prior_weekly_block}

## This Week's Daily Reports:
{daily_block}

---

Produce a comprehensive weekly synthesis in the following EXACT markdown format (English only):

---

# {topic['name']}: Weekly Summary — Week of {date_str}

## Key Events This Week
[Numbered list of the 5-8 most significant events/developments this week, with brief explanations]

## How This Week Compares to Last Week
[If a prior weekly summary exists: compare this week's developments to last week's analysis and predictions — what was predicted correctly, what changed unexpectedly, what trends continued]
[If no prior weekly: note this is the first weekly summary]

## Evolving Trends
[3-5 paragraphs on how the situation is evolving — key shifts in dynamics, new actors, changing power balances]

## Updated Trend Predictions
- **Next 7-14 days:** [prediction]
- **Next 1-2 months:** [prediction]
- **Wildcard risks:** [risks that could dramatically change the situation]

## Sources Referenced This Week
[Bullet list of key sources cited across this week's daily reports]

---

*Weekly report compiled: {date_str}*
"""


def _translate_to_chinese(client, model: str, english_report: str) -> str:
    """Translate an English weekly report to Chinese using the provider's translation model."""
    prompt = f"""Translate the following English news report into Chinese.
Keep all markdown formatting, section structure, and source URLs intact.
Translate section headers too. Keep URL hrefs as-is but translate anchor text.

---

{english_report}"""

    message = client.messages.create(
        model=model,
        max_tokens=12000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response_text(message)


def generate_weekly(base_dir: str, topics: list[dict], settings: dict) -> list[tuple]:
    """Generate weekly summaries for all topics.

    Returns a list of (topic_name, english_report, chinese_report) tuples so the
    caller can build a Telegram digest. Empty list if nothing was generated.
    """
    client, model, translation_model, provider = get_llm(settings)

    output_dir = os.path.join(base_dir, settings["output_dir"])
    daily_dir = os.path.join(output_dir, "daily")
    weekly_dir = os.path.join(output_dir, "weekly")
    os.makedirs(weekly_dir, exist_ok=True)

    date_str = datetime.now().strftime("%B %d, %Y")
    date_tag = datetime.now().strftime("%Y%m%d")

    combined_sections = []
    weekly_reports = []

    for topic in topics:
        print(f"  Generating weekly summary for '{topic['name']}' …")
        daily_files = get_recent_daily_files(daily_dir, topic["id"], n=7)

        if not daily_files:
            print(f"    No daily files found for {topic['id']} — skipping.")
            continue

        daily_contents = [(f, read_file(f)) for f in daily_files]
        last_weekly = get_last_weekly(weekly_dir)

        prompt = build_weekly_prompt(topic, daily_contents, last_weekly, date_str)

        print(f"    Calling {provider} ({model}) …")
        message = client.messages.create(
            model=model,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        english_report = response_text(message)

        print(f"    Translating to Chinese ({translation_model}) …")
        chinese_report = _translate_to_chinese(client, translation_model, english_report)

        combined_sections.append(english_report + "\n\n---\n---\n\n" + chinese_report)
        weekly_reports.append((topic["name"], english_report, chinese_report))

    if combined_sections:
        output_path = os.path.join(weekly_dir, f"weekly_summary_{date_tag}.md")
        with open(output_path, "w") as f:
            f.write("\n\n---\n\n".join(combined_sections))
        print(f"  Weekly summary written: {output_path}")
    else:
        print("  No weekly summary generated (no daily files found).")

    return weekly_reports


if __name__ == "__main__":
    import sys

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(f"{base}/config/settings.json") as f:
        settings = json.load(f)
    with open(f"{base}/config/topics.json") as f:
        topics = json.load(f)
    generate_weekly(base, topics, settings)
