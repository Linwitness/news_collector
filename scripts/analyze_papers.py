"""
analyze_papers.py — Turn a topic's candidate papers into a paper report
(sent Monday, Wednesday and Saturday).

Two LLM calls per topic (plus translation):
  1. triage: pick the genuinely relevant papers from the keyword-scored candidates
     (then assign_single_topic keeps each paper in one topic)
  2. report: highlights, digest, trends, gaps & implications. Each report stands
     alone: no comparison with the previous one and no predictions.
Every link in the report must be one of the input URLs (fix_links).
"""

import json
import re

from llm import LLM, create, get_llm

REQUIRED_SECTIONS = ["Highlights", "Paper Digest", "Trends", "Gaps"]
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
    return f"""You are screening new research items for a paper report.

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


def triage(llm: LLM, topic: dict, candidates: list[dict], limit: int) -> tuple[list[dict], bool]:
    """Return (selected candidates, most important first; judged). Items flagged
    as milestones get item["milestone"] = True. If the reply can't be parsed it
    falls back to the top `limit` by keyword score with judged=False: the rest
    were not actually rejected."""
    if len(candidates) <= 3:
        return candidates, True
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
        return candidates[:limit], False
    picked, seen = [], set()
    for i in indices:
        if isinstance(i, int) and 0 <= i < len(candidates) and i not in seen:
            seen.add(i)
            picked.append({**candidates[i], "milestone": i in milestones})
    return picked[:limit], True


# ---------------------------------------------------------------------------
# 2. Report
# ---------------------------------------------------------------------------

def build_report_prompt(topic: dict, papers: list[dict], date_str: str, highlights: int = 5) -> str:
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

    guidance = f"\nTopic-specific guidance: {topic['report_guidance']}\n" if topic.get("report_guidance") else ""

    return f"""You are a senior research analyst writing a literature report for an expert
reader. Reports go out on Monday, Wednesday and Saturday, and each covers the items
that are new since the previous one. Today is {date_str}.

Topic: **{topic['name']}**
Scope: {topic['description']}
{guidance}
## New items in this report ({len(papers)}):

{block}
---

Rules:
- Use only the items above. Base claims about an item on its abstract; if there is
  no abstract, describe it from its title only and do not invent results.
- Every time you mention an item, link it using its exact URL from the list. Never
  write any other URL, and never show the [P#] labels — they are only for your
  reference. In Highlights headings and the Paper Digest the link text is the full
  title; in Research Trends & Analysis and Gaps & Implications it is a short label
  of 2-6 words instead, e.g. [ST-E1 gyrokinetics](URL).
- Mark preprints "(preprint)", technical reports "(report)" and news items "[News]".
- Mention every item at least once (in Highlights or in the Paper Digest).
- Items marked "Milestone" mark a step forward for a fusion program; put them in
  Highlights unless there are more than {highlights}.
- Items with Date "unknown" come from journal feeds that release a whole issue at
  once. Do not read the number of items from one journal as a trend in the field.
- Analyze what these items show now. Do not predict future results, timelines or
  what the field will do next.

Produce the report in this EXACT markdown format (English only):

---

# {topic['name']}: Paper Report — {date_str}

## Highlights
[The {highlights} most significant items (fewer if fewer stand out). Prefer items
with an abstract. For each:
### N. [Title](URL)
*Authors — Venue, Date*
One paragraph of 4-6 sentences, in prose (no sub-headings, lists or bold labels):
the question and the method or setup; the key result, with the numbers the
abstract gives (sizes, errors, temperatures, speed-ups, conditions); the
limitations or caveats (e.g. simulation only, no validation, small sample, model
material or proxy isotope, narrow parameter range, abstract cut off); and why it
matters for this field. Do not add numbers the abstract does not give.]

## Paper Digest
[All remaining items, grouped under short **bold sub-theme** labels. One line each:
- [Title](URL) — one-sentence takeaway.]

## Research Trends & Analysis
[2-4 paragraphs of synthesis, not a list of papers: what this batch says about
where the field is moving — recurring methods, maturing vs. emerging directions,
disagreements, and notable groups or facilities. Make an argument in each
paragraph and support it with the 2-4 most telling items; do not string many
titles together.]

## Gaps & Implications
[Based only on the items above. Exactly these two bold labels, each on its own
line and followed by 2-4 bullets:
**Gaps**
- What these items leave open (missing validation, untested regimes or
  materials, unavailable data, methods that do not yet connect), naming the
  items that show it.
**Implications**
- What these results mean now for design choices, codes and models,
  experiments or facilities in this field, naming the items behind it.]

---

*Paper report compiled: {date_str} · {len(papers)} items reviewed*
"""


def check_papers(report: str, allowed_urls: set, expected_urls: set = frozenset()) -> tuple[str, list[str]]:
    """Validate a report that has been through fix_links. Returns (report, problems).

    Drops any preamble before the title, and requires the 4 sections, a link on
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
In the Highlights headings and the Paper Digest, keep paper titles in their
original English inside the link text, followed by a short Chinese rendering in
parentheses. Elsewhere the links have short labels: translate those into Chinese.

---

{english_report}"""

    return create(llm, prompt, translation=True)


def triage_topic(topic: dict, candidates: list[dict], settings: dict) -> tuple[list[dict], bool]:
    """Pick this topic's relevant items, most important first. Returns
    (selected, judged); see triage()."""
    limit = settings.get("papers", {}).get("max_papers_per_topic", 50)
    # Thinking on: with it off, the screen let through pseudo-scientific preprints.
    llm = get_llm(settings, task="papers")
    print(f"    Triage: {len(candidates)} candidates ({llm.provider}, {llm.model}) …")
    selected, judged = triage(llm, topic, candidates, limit)
    flagged = sum(it.get("milestone", False) for it in selected)
    print(f"    Selected {len(selected)} items" + (f" ({flagged} milestones)" if flagged else ""))
    return selected, judged


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


def write_topic_report(topic: dict, selected: list[dict], settings: dict, date_str: str) -> tuple:
    """Return (english_report, chinese_report) for one topic's selected items."""
    llm = get_llm(settings, task="papers")
    highlights = settings.get("papers", {}).get("highlights_per_topic", 5)
    expected = {it["link"] for it in selected}
    allowed = expected  # links may only point at this report's items
    prompt = build_report_prompt(topic, selected, date_str, highlights)

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
