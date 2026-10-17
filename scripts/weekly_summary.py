"""
weekly_summary.py — Generate a weekly synthesis from the past 7 daily MDs.

Called every Friday by run_workflow.py.
Outputs output/weekly/weekly_summary_{YYYYMMDD}.md
"""

import json
import os
import glob
import time
from datetime import datetime

from llm import LLM, create, get_llm


def get_recent_daily_files(daily_dir: str, topic_id: str, n: int = 7) -> list[str]:
    """Return up to n most recent daily MD files for a topic, newest first."""
    pattern = os.path.join(daily_dir, f"{topic_id}_*.md")
    files = sorted(glob.glob(pattern), reverse=True)
    return files[:n]


REPORT_SEPARATOR = "\n---\n---\n"  # between the English and Chinese halves

# Keywords for the five required H2 sections (models sometimes reword a
# header slightly, e.g. "Compared" for "Compares").
REQUIRED_SECTIONS = ["Key Events", "Last Week", "Evolving Trends", "Trend Predictions", "Sources"]


def english_part(report: str) -> str:
    """Return the English half of a bilingual report."""
    return report.split(REPORT_SEPARATOR, 1)[0].strip()


def get_last_weekly(weekly_dir: str, topic_name: str) -> str:
    """Return this topic's English section of the most recent weekly summary, or ""."""
    pattern = os.path.join(weekly_dir, "weekly_summary_*.md")
    files = sorted(glob.glob(pattern), reverse=True)
    if not files:
        return ""
    content = read_file(files[0])
    start = content.find(f"# {topic_name}: Weekly Summary")
    if start == -1:
        return ""
    return english_part(content[start:])


def check_weekly(report: str) -> tuple[str, list[str]]:
    """Drop any preamble before the title; return (report, missing section names)."""
    start = report.find("# ")
    if start > 0:
        report = report[start:]
    headers = [ln for ln in report.splitlines() if ln.startswith("## ")]
    missing = [s for s in REQUIRED_SECTIONS if not any(s.lower() in h.lower() for h in headers)]
    return report, missing


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

{last_weekly}

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


def _translate_to_chinese(llm: LLM, english_report: str) -> str:
    """Translate an English weekly report to Chinese using the provider's translation model."""
    prompt = f"""Translate the following English news report into Chinese.
Keep all markdown formatting, section structure, and source URLs intact.
Translate section headers too. Keep URL hrefs as-is but translate anchor text.

---

{english_report}"""

    return create(llm, prompt, translation=True)


def _weekly_for_topic(llm: LLM, prompt: str) -> tuple[str, str]:
    """Return (english_report, chinese_report) for one topic's weekly prompt."""
    print(f"    Calling {llm.provider} ({llm.model}) …")
    # One retry if sections are missing; after that keep it with a warning
    # (raising here would drop this topic's weekly summary).
    for attempt in (1, 2):
        english_report, missing = check_weekly(create(llm, prompt))
        if not missing:
            break
        print(f"    WARNING: missing sections {missing} (attempt {attempt}/2)")

    print(f"    Translating to Chinese ({llm.translation_model}) …")
    return english_report, _translate_to_chinese(llm, english_report)


def generate_weekly(base_dir: str, topics: list[dict], settings: dict) -> list[tuple]:
    """Generate weekly summaries for all topics.

    Returns a list of (topic_name, english_report, chinese_report) tuples so the
    caller can build a Telegram digest. Empty list if nothing was generated.
    """
    llm = get_llm(settings, task="weekly")

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

        daily_contents = [(f, english_part(read_file(f))) for f in daily_files]
        last_weekly = get_last_weekly(weekly_dir, topic["name"])

        prompt = build_weekly_prompt(topic, daily_contents, last_weekly, date_str)

        # Retry transient failures (a connection reset on 2026-10-16 lost the
        # whole weekly); if a topic still fails, skip it and keep the others.
        result = None
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                result = _weekly_for_topic(llm, prompt)
                break
            except Exception as e:
                if attempt < max_attempts:
                    delay = 20 * attempt  # 20s, 40s
                    print(f"    WARNING: weekly for '{topic['name']}' failed "
                          f"(attempt {attempt}/{max_attempts}): {e} — retrying in {delay}s")
                    time.sleep(delay)
                else:
                    print(f"    ERROR: weekly for '{topic['name']}' failed "
                          f"after {max_attempts} attempts: {e} — skipping this topic")
        if result is None:
            continue
        english_report, chinese_report = result

        combined_sections.append(english_report + "\n\n---\n---\n\n" + chinese_report)
        weekly_reports.append((topic["name"], english_report, chinese_report))

    if combined_sections:
        output_path = os.path.join(weekly_dir, f"weekly_summary_{date_tag}.md")
        with open(output_path, "w") as f:
            f.write("\n\n---\n\n".join(combined_sections))
        print(f"  Weekly summary written: {output_path}")
    else:
        print("  No weekly summary generated (no daily files, or every topic failed).")

    return weekly_reports


if __name__ == "__main__":
    import sys

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(f"{base}/config/settings.json") as f:
        settings = json.load(f)
    with open(f"{base}/config/topics.json") as f:
        topics = json.load(f)
    generate_weekly(base, topics, settings)
