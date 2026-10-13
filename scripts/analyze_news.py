"""
analyze_news.py — Call Claude API to analyze news articles for a topic.

Returns a markdown string that is written to output/daily/{TOPIC}_{DATE}.md
"""

import json
import os
from datetime import datetime

from llm import LLM, get_llm, response_text


def load_questions(questions_path: str) -> str:
    """Read one-off user questions from questions.md."""
    try:
        with open(questions_path) as f:
            content = f.read()
        # Extract lines that are actual questions (non-empty, non-comment, non-header)
        lines = [
            ln.strip()
            for ln in content.splitlines()
            if ln.strip()
            and not ln.strip().startswith("#")
            and not ln.strip().startswith("<!--")
        ]
        return "\n".join(lines)
    except FileNotFoundError:
        return ""


def load_standing_questions(default_questions_path: str, topic_id: str = "") -> str:
    """Read standing questions from config/default_questions.md for a given topic."""
    try:
        with open(default_questions_path) as f:
            content = f.read()
    except FileNotFoundError:
        return ""

    current_section = None
    collected = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current_section = stripped[3:].strip().lower()
        elif stripped.startswith("#") or stripped.startswith("<!--"):
            continue
        elif stripped and current_section in ("global", topic_id.lower()):
            collected.append(stripped)
    return "\n".join(collected)


def build_prompt(
    topic: dict,
    articles: list[dict],
    standing_questions: str,
    user_questions: str,
    date_str: str,
) -> str:
    articles_block = ""
    for i, art in enumerate(articles, 1):
        articles_block += (
            f"{i}. **{art['title']}**\n"
            f"   Source: {art['source']} | Published: {art['published']}\n"
            f"   URL: {art['link']}\n"
            f"   Summary: {art['summary']}\n\n"
        )

    standing_q_block = ""
    if standing_questions:
        standing_q_block = f"""
STANDING QUESTIONS (answer every day in the context of today's news):
{standing_questions}
"""

    user_q_block = ""
    if user_questions:
        user_q_block = f"""
ONE-OFF USER QUESTIONS (answer these specifically today):
{user_questions}
"""

    return f"""You are an expert geopolitical and news analyst. Today is {date_str}.

Topic: **{topic['name']}**

Here are the latest news articles (last 24 hours):

{articles_block}
{standing_q_block}
{user_q_block}

Produce a daily news summary report in the following EXACT markdown format (English only). Be analytical, precise, and cite sources with markdown links.

---

# {topic['name']}: Daily News Summary — {date_str}

## Standing Questions Analysis
*(Answered every day from standing questions)*

[For each standing question, write:
**Q: [the question]**
[2-4 sentence answer in context of today's news]]

---

## User Q&A
*(One-off questions answered today)*

[If there are one-off user questions, for each write:
> *Your question:* [question]
**Answer:** [detailed answer]

If there are no one-off questions, write: *No questions submitted for today.*]

---

## Top News (Last 24 Hours)

[For each significant article/story, write a numbered section:
### N. [Story Title]
[2-4 sentence summary paragraph with key facts]
**Source:** [Provider Name](URL) | Published: [time]]

---

## Analysis
[3-5 paragraph analytical synthesis of the day's news: what changed, what it means, key actors]

## Trend Prediction
[Bullet-point predictions: Short-term (next 7-14 days), Medium-term (1-2 months), Wildcard risks]

---

## Leave Your Questions for Tomorrow
> *(Edit questions.md to ask one-off questions — they will be answered in tomorrow's report)*
"""


def _translate_to_chinese(llm: LLM, english_report: str, topic_name: str) -> str:
    """Translate an English report to Chinese using the provider's translation model."""
    prompt = f"""Translate the following English news report into Chinese.
Keep all markdown formatting, section structure, and source URLs intact.
Translate section headers too. Keep URL hrefs as-is but translate anchor text.

---

{english_report}"""

    message = llm.client.messages.create(
        model=llm.translation_model,
        max_tokens=llm.translation_max_tokens,
        messages=[{"role": "user", "content": prompt}],
        **llm.extra,
    )
    return response_text(message)


def analyze_topic(
    topic: dict,
    articles: list[dict],
    standing_questions: str,
    user_questions: str,
    settings: dict,
    date_str: str,
) -> str:
    """Call the active LLM and return the full markdown report string."""
    llm = get_llm(settings)

    if not articles:
        no_news_note = (
            f"No articles were found for topic '{topic['name']}' in the last 24 hours."
        )
        articles_for_prompt = []
        # Still run analysis so standing questions get answered
        print(f"  WARNING: {no_news_note}")
    else:
        articles_for_prompt = articles

    prompt = build_prompt(
        topic, articles_for_prompt, standing_questions, user_questions, date_str
    )

    print(f"  Calling {llm.provider} ({llm.model}) for topic '{topic['name']}' …")
    message = llm.client.messages.create(
        model=llm.model,
        max_tokens=llm.max_tokens,
        messages=[{"role": "user", "content": prompt}],
        **llm.extra,
    )
    english_report = response_text(message)

    print(f"  Translating to Chinese ({llm.translation_model}) …")
    chinese_report = _translate_to_chinese(llm, english_report, topic["name"])

    return english_report + "\n\n---\n---\n\n" + chinese_report


if __name__ == "__main__":
    import sys, os

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(f"{base}/config/settings.json") as f:
        settings = json.load(f)
    topic = {"id": "test", "name": "Test Topic", "keywords": []}
    articles = []
    sq = "- What is happening today?"
    uq = ""
    date_str = datetime.now().strftime("%B %d, %Y")
    result = analyze_topic(topic, articles, sq, uq, settings, date_str)
    print(result[:500])
