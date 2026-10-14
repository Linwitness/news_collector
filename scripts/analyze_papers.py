"""
analyze_papers.py — Turn a topic's candidate papers into a weekly paper report.

Two LLM calls per topic (plus translation):
  1. triage: pick the genuinely relevant papers from the keyword-scored candidates
     (then assign_single_topic keeps each paper in one topic)
  2. report: highlights, digest, analysis, comparison with last week, predictions
Every link in the report must be one of the input URLs (fix_links).
"""

import glob
import json
import os
import re

from llm import LLM, create, get_llm
from weekly_summary import english_part, read_file

REQUIRED_SECTIONS = ["Highlights", "Paper Digest", "Trends", "Last Week", "Predictions"]
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s?#)]+")


# ---------------------------------------------------------------------------
# 1. Triage
# ---------------------------------------------------------------------------

def build_triage_prompt(topic: dict, candidates: list[dict], limit: int) -> str:
    lines = []
    for i, it in enumerate(candidates):
        abstract = it["abstract"][:300] or "(no abstract)"
        lines.append(f"[{i}] {it['title']} — {it['venue']} ({it['kind']})\n    {abstract}")
    listing = "\n".join(lines)
    reply_format = "Reply with only a JSON array of the selected indices, e.g. [4, 0, 17]."
    if topic.get("milestones"):
        reply_format = f"""Among the selected items, also flag the milestones: {topic['milestones']}
Flag an item as a milestone even if it is mainly a modeling or materials paper.

Reply with only a JSON object, e.g. {{"selected": [4, 0, 17], "milestones": [0]}}."""
    return f"""You are screening this week's new research items for a weekly report.

Topic: {topic['name']}
Scope: {topic['description']}

Candidates:
{listing}

Select the items that are genuinely about this topic's scope and worth an
expert reader's attention. Exclude items that only mention a keyword in passing,
corrigenda/errata, and items that look non-scientific or pseudo-scientific
(e.g. sweeping "unified theory" claims, grandiose titles, or self-published
concepts with no methods or results). Be especially skeptical of items from
self-archiving repositories (Zenodo, figshare, preprints.org, SSRN, ResearchGate):
keep one only if its abstract shows concrete methods, data and results in the
mainstream of the field. Order them most important first and select
at most {limit}; selecting fewer is fine when fewer qualify.

{reply_format}"""


def triage(llm: LLM, topic: dict, candidates: list[dict], limit: int) -> list[dict]:
    """Return the selected candidates, most important first. Items flagged as
    milestones get item["milestone"] = True. Falls back to the top `limit` by
    keyword score if the reply can't be parsed."""
    if len(candidates) <= 3:
        return candidates
    reply = create(llm, build_triage_prompt(topic, candidates, limit))
    indices, milestones = None, []
    try:
        obj = re.search(r"\{.*\}", reply, re.DOTALL)
        if obj:
            parsed = json.loads(obj.group(0))
            indices, milestones = parsed.get("selected"), parsed.get("milestones") or []
        else:
            match = re.search(r"\[[\d,\s]*\]", reply)
            indices = json.loads(match.group(0)) if match else None
    except (json.JSONDecodeError, AttributeError):
        indices = None
    if not indices:
        print("    WARNING: could not parse triage reply; using top candidates by score")
        return candidates[:limit]
    picked, seen = [], set()
    for i in indices:
        if isinstance(i, int) and 0 <= i < len(candidates) and i not in seen:
            seen.add(i)
            picked.append({**candidates[i], "milestone": i in milestones})
    return picked[:limit]


# ---------------------------------------------------------------------------
# 2. Report
# ---------------------------------------------------------------------------

def get_last_paper_report(papers_dir: str, topic_name: str, date_tag: str) -> str:
    """This topic's English section of the most recent earlier paper report, or ""."""
    files = sorted(
        f for f in glob.glob(os.path.join(papers_dir, "papers_*.md"))
        if os.path.basename(f) < f"papers_{date_tag}.md"
    )
    if not files:
        return ""
    content = read_file(files[-1])
    start = content.find(f"# {topic_name}: Weekly Paper Report")
    if start == -1:
        return ""
    return english_part(content[start:])


def build_report_prompt(topic: dict, papers: list[dict], last_report: str, date_str: str,
                        highlights: int = 5) -> str:
    block = ""
    for i, it in enumerate(papers, 1):
        label = {"preprint": "Preprint", "report": "Technical report", "news": "News"}.get(it["kind"], "Journal article")
        block += (
            f"[P{i}] {it['title']}\n"
            f"   Authors: {it['authors'] or 'n/a'} | Venue: {it['venue']} | Date: {it['date']} | Type: {label}"
            f"{' | Milestone' if it.get('milestone') else ''}\n"
            f"   URL: {it['link']}\n"
            f"   Abstract: {it['abstract'] or '(not available — use the title only, do not guess results)'}\n\n"
        )

    prior = ""
    if last_report:
        prior = f"""
## Last week's report on this topic (for continuity):

{last_report}

---
"""

    guidance = f"\nTopic-specific guidance: {topic['report_guidance']}\n" if topic.get("report_guidance") else ""

    return f"""You are a senior research analyst writing a weekly literature report for an
expert reader. Today is {date_str}.

Topic: **{topic['name']}**
Scope: {topic['description']}
{guidance}{prior}
## This week's new items ({len(papers)}):

{block}
---

Rules:
- Use only the items above. Base claims about an item on its abstract; if there is
  no abstract, describe it from its title only and do not invent results.
- Every time you mention an item, link it as [Title](URL) using its exact URL from
  the list (a shortened title is fine in running text). Never write any other URL,
  and never show the [P#] labels — they are only for your reference.
- Mark preprints "(preprint)", technical reports "(report)" and news items "[News]".
- Mention every item at least once (in Highlights or in the Paper Digest).
- Items marked "Milestone" mark a step forward for a fusion program; put them in
  Highlights unless there are more than {highlights}.
- Items with Date "unknown" come from journal feeds that release a whole issue at
  once. Do not read the number of items from one journal as a trend in the field.

Produce the report in this EXACT markdown format (English only):

---

# {topic['name']}: Weekly Paper Report — Week of {date_str}

## Highlights
[The {highlights} most significant items (fewer if fewer stand out). Prefer items
with an abstract. For each:
### N. [Title](URL)
*Authors — Venue, Date*
2-4 sentences: what they did, the key result, and why it matters for this field.]

## Paper Digest
[All remaining items, grouped under short **bold sub-theme** labels. One line each:
- [Title](URL) — one-sentence takeaway.]

## Research Trends & Analysis
[2-4 paragraphs of synthesis, not a list of papers: what this week's work says
about where the field is moving — recurring methods, maturing vs. emerging
directions, disagreements, gaps, and notable groups or facilities. Make an
argument in each paragraph and support it with the 2-4 most telling items; do
not string many titles together.]

## Compared with Last Week
[If last week's report is given: which of its predictions and trends this week's
items support or contradict, and what is new. Otherwise write that this is the
first weekly paper report.]

## Predictions & What to Watch
[Field-level predictions that a reader could check later, not "expect follow-ups
to paper X". Each states what will happen, roughly when, and what would show it
was wrong; name the evidence from this week that supports it. Say how confident
you are (low / medium / high).]
- **Next 1-3 months:** [1-2 predictions]
- **Next 6-12 months:** [1-2 predictions]
- **Open questions / wildcards:** [what could change the picture]

---

*Paper report compiled: {date_str} · {len(papers)} items reviewed*
"""


def check_papers(report: str, allowed_urls: set, expected_urls: set = frozenset()) -> tuple[str, list[str]]:
    """Validate a report that has been through fix_links. Returns (report, problems).

    Drops any preamble before the title, and requires the 5 sections, a link on
    every Highlights item, and (nearly) every expected item to be mentioned.
    """
    start = report.find("# ")
    if start > 0:
        report = report[start:]
    problems = []

    headers = [ln for ln in report.splitlines() if ln.startswith("## ")]
    missing = [s for s in REQUIRED_SECTIONS if not any(s.lower() in h.lower() for h in headers)]
    if missing:
        problems.append(f"missing sections {missing}")

    # fix_links has already removed unknown links; count what it dropped.
    linked = {url for _, url in LINK_RE.findall(report)}
    if not linked:
        problems.append("no links")
    unmentioned = expected_urls - linked
    if len(unmentioned) > max(2, len(expected_urls) // 10):
        problems.append(f"{len(unmentioned)} of {len(expected_urls)} items not mentioned")

    m = re.search(r"(?ms)^## Highlights\s*$(.*?)(?=^## )", report)
    if m:
        items = re.split(r"(?m)^### ", m.group(1))[1:]
        unlinked = [it.splitlines()[0][:60] for it in items if not LINK_RE.search(it)]
        if not items:
            problems.append("no Highlights items")
        if unlinked:
            problems.append(f"Highlights items without a link: {unlinked}")

    return report, problems


def _canonical(url: str) -> str:
    """A link's DOI if it has one (so doi.org/X and publisher/X match), else the URL."""
    m = DOI_RE.search(url)
    return m.group(0).lower().rstrip(".") if m else url


def fix_links(report: str, allowed_urls: set) -> str:
    """Point every link at its input URL (the model sometimes rewrites a
    publisher URL as doi.org/...), and turn links to unknown URLs into plain text."""
    by_canonical = {_canonical(u): u for u in allowed_urls}

    def repl(m):
        url = m.group(2) if m.group(2) in allowed_urls else by_canonical.get(_canonical(m.group(2)))
        return f"[{m.group(1)}]({url})" if url else m.group(1)

    return LINK_RE.sub(repl, report)


def _translate_to_chinese(llm: LLM, english_report: str) -> str:
    """Translate an English paper report to Chinese using the provider's translation model."""
    prompt = f"""Translate the following English research report into Chinese.
Keep all markdown formatting, section structure, and URLs intact.
Translate section headers too. Keep URL hrefs as-is but translate anchor text.
Keep paper titles in their original English inside the link text, followed by
a short Chinese rendering in parentheses.

---

{english_report}"""

    return create(llm, prompt, translation=True)


def triage_topic(topic: dict, candidates: list[dict], settings: dict) -> list[dict]:
    """Pick this topic's relevant items, most important first."""
    limit = settings.get("papers", {}).get("max_papers_per_topic", 50)
    # Thinking on: with it off, the screen let through pseudo-scientific preprints.
    llm = get_llm(settings, task="papers")
    print(f"    Triage: {len(candidates)} candidates ({llm.provider}, {llm.model}) …")
    selected = triage(llm, topic, candidates, limit)
    flagged = sum(it.get("milestone", False) for it in selected)
    print(f"    Selected {len(selected)} items" + (f" ({flagged} milestones)" if flagged else ""))
    return selected


def assign_single_topic(selected: dict) -> dict:
    """Keep each item in only one topic. A topic that flagged the item as a
    milestone wins; otherwise the one where it scored highest (ties go to the
    topic listed first). selected maps topic_id -> items."""
    best = {}
    for tid, items in selected.items():
        for it in items:
            rank = (it.get("milestone", False), it["score"])
            if it["key"] not in best or rank > best[it["key"]][1]:
                best[it["key"]] = (tid, rank)
    return {tid: [it for it in items if best[it["key"]][0] == tid] for tid, items in selected.items()}


def write_topic_report(topic: dict, selected: list[dict], settings: dict,
                       papers_dir: str, date_str: str, date_tag: str) -> tuple:
    """Return (english_report, chinese_report) for one topic's selected items."""
    llm = get_llm(settings, task="papers")
    highlights = settings.get("papers", {}).get("highlights_per_topic", 5)
    last_report = get_last_paper_report(papers_dir, topic["name"], date_tag)
    expected = {it["link"] for it in selected}
    # Links may point at this week's items or at papers in last week's report.
    allowed = expected | {url for _, url in LINK_RE.findall(last_report)}
    prompt = build_report_prompt(topic, selected, last_report, date_str, highlights)

    print(f"    Writing report on {len(selected)} items ({llm.provider}, {llm.model}) …")
    # One retry on a failed check; after that keep it with a warning (raising
    # would drop the topic) but never keep a link that isn't a real input.
    for attempt in (1, 2):
        raw = create(llm, prompt)
        english, problems = check_papers(fix_links(raw, allowed), allowed, expected)
        dropped = len(LINK_RE.findall(raw)) - len(LINK_RE.findall(english))
        if dropped > 0:
            print(f"    WARNING: removed {dropped} link(s) to URLs not in the inputs")
        if not problems:
            break
        print(f"    WARNING: {'; '.join(problems)} (attempt {attempt}/2)")

    print(f"    Translating to Chinese ({llm.translation_model}) …")
    chinese = _translate_to_chinese(llm, english)
    return english, chinese
