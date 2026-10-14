"""
fetch_papers.py — Collect the past week's papers (and a few fusion news items)
for each paper topic.

Sources are listed in config/paper_sources.json, topics in
config/paper_topics.json. Returns a dict mapping topic_id -> list of item dicts.
Each item has: key, title, authors, venue, date, link, abstract, source, kind, score.
"""

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

import feedparser
import requests

from fetch_news import clean_html, parse_published

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; NewsCollectorBot/1.0; "
        "+https://github.com/Linwitness/news_collector)"
    )
}
ARXIV_API = "https://export.arxiv.org/api/query"
OPENALEX_API = "https://api.openalex.org/works"
OSTI_API = "https://www.osti.gov/api/v1/records"


def _get(url: str, params: dict, timeout: int = 60, **headers) -> requests.Response:
    """GET with a few retries on rate limiting (OpenAlex has a per-second limit)."""
    for attempt in range(4):
        resp = requests.get(url, params=params, headers={**HEADERS, **headers}, timeout=timeout)
        wait = int(resp.headers.get("Retry-After", 0) or 0) or 15 * (attempt + 1)
        # A long Retry-After means the daily budget is spent, not a per-second
        # limit: fail now (the source is skipped with a warning) instead of hanging.
        if resp.status_code != 429 or attempt == 3 or wait > 60:
            break
        time.sleep(wait)
    resp.raise_for_status()
    return resp


def load_config(base_dir: str):
    with open(f"{base_dir}/config/paper_sources.json") as f:
        sources = json.load(f)
    with open(f"{base_dir}/config/paper_topics.json") as f:
        topics = json.load(f)
    with open(f"{base_dir}/config/settings.json") as f:
        settings = json.load(f)
    return sources, topics, settings


# Corrections and retraction notices are not new research.
NOT_RESEARCH = re.compile(r"^\s*(corrigendum|erratum|retraction|retracted|correction to|withdrawn|expression of concern)\b", re.I)
STOPWORDS = {"with", "from", "using", "based", "under", "their", "toward", "towards", "into",
             "via", "for", "and", "the", "of", "in", "on", "a", "an", "to", "by", "at", "as"}


def title_key(title: str) -> str:
    """Normalized title, used to dedupe a preprint and its journal version.
    A trailing "[Slides]"/"[Poster]" (OSTI lists talks separately) is ignored."""
    title = re.sub(r"\s*\[[^\]]*\]\s*$", "", title)
    return re.sub(r"[^a-z0-9]", "", title.lower())[:120]


def _title_words(title: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2 and w not in STOPWORDS}


def _same_work(a: set, b: set) -> bool:
    """Titles that share most of their words are versions of one work (e.g. a
    preprint, its dataset and its journal paper under slightly different titles)."""
    if len(a) < 4 or len(b) < 4:
        return False
    return len(a & b) / len(a | b) >= 0.6


def _authors(names: list) -> str:
    names = [n for n in names if n]
    if len(names) > 3:
        return ", ".join(names[:3]) + " et al."
    return ", ".join(names)


def _item(title, authors, venue, date, link, abstract, source, kind="paper"):
    title = clean_html(title)
    return {
        "key": title_key(title),
        "title": title,
        "authors": authors,
        "venue": venue,
        "date": date.strftime("%Y-%m-%d") if date else "unknown",
        "link": link,
        "abstract": clean_html(abstract)[:1200],
        "source": source,
        "kind": kind,
    }


# ---------------------------------------------------------------------------
# Source fetchers. Each returns a list of items; query-based sources also
# record which topic's query found the item (item["query_topic"]).
# ---------------------------------------------------------------------------

def fetch_arxiv(topic: dict, cutoff: datetime) -> list[dict]:
    query = topic.get("arxiv_query")
    if not query:
        return []
    resp = _get(ARXIV_API, {"search_query": query, "sortBy": "submittedDate",
                            "sortOrder": "descending", "max_results": 200})
    items = []
    for e in feedparser.parse(resp.content).entries:
        published = parse_published(e)
        if published and published < cutoff:
            break  # sorted newest first
        cats = ", ".join(t.get("term", "") for t in getattr(e, "tags", [])[:2])
        link = getattr(e, "id", "").replace("http://", "https://")
        link = re.sub(r"v\d+$", "", link)  # always point at the latest version
        items.append(_item(
            e.title, _authors([a.get("name") for a in getattr(e, "authors", [])]),
            f"arXiv preprint ({cats})", published, link, e.get("summary", ""),
            "arXiv", kind="preprint",
        ))
    return items


def _openalex_abstract(inverted: dict) -> str:
    if not inverted:
        return ""
    words = sorted((pos, w) for w, positions in inverted.items() for pos in positions)
    return " ".join(w for _, w in words)


def _openalex_search(query: str, cutoff: datetime, max_results: int) -> list[dict]:
    """Every work matching `query` since the cutoff, up to max_results (paged)."""
    results, cursor = [], "*"
    while cursor and len(results) < max_results:
        time.sleep(1)
        data = _get(OPENALEX_API, {
            "filter": f"from_publication_date:{cutoff:%Y-%m-%d},"
                      f"title_and_abstract.search:{query}",
            "per-page": 100,
            "cursor": cursor,
            "select": "doi,title,publication_date,authorships,"
                      "primary_location,abstract_inverted_index,type",
        }).json()
        page = data.get("results", [])
        results += page
        cursor = data.get("meta", {}).get("next_cursor") if page else None
    return results[:max_results]


def fetch_openalex(topic: dict, cutoff: datetime, max_per_query: int = 200) -> list[dict]:
    items = []
    for query in topic.get("queries", []):
        for r in _openalex_search(query, cutoff, max_per_query):
            link = r.get("doi") or ((r.get("primary_location") or {}).get("landing_page_url"))
            if not link or not r.get("title"):
                continue
            src = ((r.get("primary_location") or {}).get("source") or {})
            date = None
            if r.get("publication_date"):
                date = datetime.strptime(r["publication_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            items.append(_item(
                r["title"],
                _authors([(a.get("author") or {}).get("display_name") for a in r.get("authorships", [])]),
                src.get("display_name") or r.get("type", ""), date, link,
                _openalex_abstract(r.get("abstract_inverted_index")), "OpenAlex",
                kind="preprint" if r.get("type") == "preprint" else "paper",
            ))
    return items


def fetch_osti(topic: dict, cutoff: datetime) -> list[dict]:
    """DOE reports and articles. OSTI's own date filter is unreliable, so
    sort by entry date and stop at the cutoff."""
    items = []
    for query in topic.get("queries", []):
        resp = _get(OSTI_API, {"q": query, "sort": "entry_date desc", "rows": 50},
                    Accept="application/json")
        for r in resp.json():
            entered = datetime.strptime(r["entry_date"][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if entered < cutoff:
                break
            # OSTI also posts old papers as new records; skip ones published long ago.
            published = (r.get("publication_date") or "")[:10]
            if published and published < (cutoff - timedelta(days=180)).strftime("%Y-%m-%d"):
                continue
            link = next((l["href"] for l in r.get("links", []) if l.get("rel") == "citation"), "")
            if r.get("doi"):
                link = f"https://doi.org/{r['doi']}"
            if not link:
                continue
            authors = [re.sub(r"\s*[\[(].*$", "", a) for a in r.get("authors", [])]
            items.append(_item(
                r.get("title", ""), _authors(authors),
                r.get("journal_name") or f"OSTI {r.get('product_type', 'record')}",
                entered, link, r.get("description", ""), "OSTI",
                kind="report" if r.get("product_type") == "Technical Report" else "paper",
            ))
    return items


def openalex_abstracts(issn: str) -> dict:
    """Recent works of one journal from OpenAlex, as {title_key: (abstract, authors)}.
    Used to fill in abstracts for feeds that only carry titles (ScienceDirect).
    A journal listing without a search costs 1 OpenAlex credit per page."""
    since = (datetime.now() - timedelta(days=150)).strftime("%Y-%m-%d")
    found = {}
    for page in (1, 2):
        time.sleep(1)
        results = _get(OPENALEX_API, {
            "filter": f"primary_location.source.issn:{issn},from_publication_date:{since}",
            "sort": "publication_date:desc", "per-page": 200, "page": page,
            "select": "title,authorships,abstract_inverted_index",
        }).json().get("results", [])
        for r in results:
            if r.get("title"):
                found[title_key(clean_html(r["title"]))] = (
                    _openalex_abstract(r.get("abstract_inverted_index")),
                    _authors([(a.get("author") or {}).get("display_name") for a in r.get("authorships", [])]),
                )
        if len(results) < 200:
            break
    return found


def fetch_rss(source: dict, cutoff: datetime) -> list[dict]:
    resp = _get(source["rss"], {}, timeout=30)
    items = []
    for e in feedparser.parse(resp.content).entries:
        link = getattr(e, "link", "") or ""
        published = parse_published(e)
        if not link or (published and published < cutoff):
            continue
        authors = [a.get("name") for a in getattr(e, "authors", [])] or [getattr(e, "author", "")]
        abstract = clean_html(getattr(e, "summary", "") or getattr(e, "description", ""))
        # ScienceDirect feeds carry no abstract or date, only
        # "Publication date: … Source: … Author(s): …".
        m = re.search(r"Author\(s\):\s*(.+)$", abstract)
        if m:
            authors = [a.strip() for a in m.group(1).split(",")]
            abstract = ""
        items.append(_item(
            getattr(e, "title", ""), _authors(authors), source["name"], published, link,
            abstract, source["name"], kind=source.get("kind", "paper"),
        ))

    if source.get("openalex_issn"):
        try:
            extra = openalex_abstracts(source["openalex_issn"])
        except Exception as e:
            print(f"    WARNING: could not look up abstracts for {source['name']}: {e}")
            extra = {}
        filled = 0
        for it in items:
            abstract, authors = extra.get(it["key"], ("", ""))
            if abstract and not it["abstract"]:
                it["abstract"] = clean_html(abstract)[:1200]
                filled += 1
            if authors and not it["authors"]:
                it["authors"] = authors
        print(f"    abstracts filled from OpenAlex: {filled}/{len(items)}")
    return items


# ---------------------------------------------------------------------------
# Scoring, dedup and seen-state
# ---------------------------------------------------------------------------

def _keyword_pattern(kw: str) -> re.Pattern:
    """Whole-word match (plural allowed). Short all-caps acronyms (ITER, MHD,
    LLM) are case-sensitive so they don't match "iteration" or "mhd"-like words."""
    flags = 0 if (kw.isupper() and len(kw) <= 6) else re.IGNORECASE
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(kw) + r"s?(?![A-Za-z0-9])", flags)


def score_item(item: dict, patterns: list) -> int:
    """Title matches count double."""
    title, abstract = item["title"], item["abstract"]
    return sum(2 * bool(p.search(title)) + bool(p.search(abstract)) for p in patterns)


def meets_requirements(item: dict, groups: list) -> bool:
    """A topic's optional "require" groups: the item must match at least one
    keyword from every group (e.g. an AI term AND a physics term)."""
    text = f"{item['title']} {item['abstract']}"
    return all(any(p.search(text) for p in group) for group in groups)


def _state_path(base_dir: str, cfg: dict) -> str:
    return os.path.join(base_dir, cfg.get("state_path", "output/state/seen_papers.json"))


def drop_stale_undated(base_dir: str, cfg: dict, items: list, cutoff: datetime) -> list:
    """Some feeds (ScienceDirect) give no dates and keep items listed for weeks.
    Remember when each undated item was first fetched and drop it once that is
    older than the lookback window."""
    path = _state_path(base_dir, cfg).replace(".json", "_first_seen.json")
    first_seen = load_seen(path, cfg.get("seen_ttl_days", 90))
    today = datetime.now().strftime("%Y-%m-%d")
    keep = []
    for entry in items:
        item = entry[0]
        if item["date"] == "unknown":
            first = first_seen.setdefault(item["key"], today)
            if first < cutoff.strftime("%Y-%m-%d"):
                continue
        keep.append(entry)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(first_seen, f, indent=0)
    return keep


def load_seen(state_path: str, ttl_days: int) -> dict:
    try:
        with open(state_path) as f:
            seen = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    cutoff = (datetime.now() - timedelta(days=ttl_days)).strftime("%Y-%m-%d")
    return {k: d for k, d in seen.items() if d >= cutoff}


def mark_papers_seen(base_dir: str, settings: dict, keys: list) -> None:
    """Record papers that made it into a written report, so they aren't repeated."""
    cfg = settings.get("papers", {})
    state_path = _state_path(base_dir, cfg)
    seen = load_seen(state_path, cfg.get("seen_ttl_days", 90))
    today = datetime.now().strftime("%Y-%m-%d")
    seen.update({k: today for k in keys})
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(seen, f, indent=0)


def _merge(entry: dict, item: dict, allowed) -> None:
    """Fold another version of the same work into a merged entry."""
    current = entry["item"]
    if current["kind"] == "preprint" and item["kind"] == "paper":
        if not item["abstract"]:
            item = {**item, "abstract": current["abstract"]}
        entry["item"] = item
    elif not current["abstract"] and item["abstract"]:
        entry["item"] = {**current, "abstract": item["abstract"]}
    entry["allowed"] |= set(allowed)


def fetch_all_papers(base_dir: str, fresh: bool = False) -> dict:
    """
    Fetch every source, dedupe across sources, drop already-reported papers,
    and return the top candidates per topic.
    """
    sources, topics, settings = load_config(base_dir)
    cfg = settings.get("papers", {})
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.get("lookback_days", 7))
    max_candidates = cfg.get("candidates_per_topic", 60)

    seen = {}
    if not fresh:
        seen = load_seen(_state_path(base_dir, cfg), cfg.get("seen_ttl_days", 90))

    # (item, topics it may go to, topic whose query found it, core topics of its journal)
    collected = []
    all_topic_ids = [t["id"] for t in topics]
    for source in sources:
        name, stype = source["name"], source["type"]
        print(f"  Fetching {name} …")
        if stype == "rss":
            try:
                found = [(it, source.get("topics", all_topic_ids), None, source.get("core_topics", []))
                         for it in fetch_rss(source, cutoff)]
            except Exception as e:
                print(f"    WARNING: could not fetch {name}: {e}")
                continue
        else:
            # Query each topic separately, so one failed query doesn't lose the source.
            fetcher = {"arxiv": fetch_arxiv, "openalex": fetch_openalex, "osti": fetch_osti}[stype]
            found = []
            for topic in topics:
                try:
                    found += [(it, all_topic_ids, topic["id"], []) for it in fetcher(topic, cutoff)]
                except Exception as e:
                    print(f"    WARNING: could not fetch {name} for '{topic['id']}': {e}")
                if stype == "arxiv":
                    time.sleep(3)  # arXiv API asks for 3s between requests
        print(f"    {len(found)} items")
        collected += found

    if not fresh:
        collected = drop_stale_undated(base_dir, cfg, collected, cutoff)

    # Dedupe by normalized title, then merge near-identical titles. Prefer a
    # journal version over a preprint, keep an abstract if any version has one,
    # and remember every topic query / core journal that found the item.
    merged = {}
    for item, allowed, query_topic, core in collected:
        if not item["key"] or item["key"] in seen or NOT_RESEARCH.match(item["title"]):
            continue
        entry = merged.get(item["key"])
        if entry is None:
            entry = merged[item["key"]] = {"item": item, "allowed": set(allowed),
                                           "query_topics": set(), "core_topics": set()}
        else:
            _merge(entry, item, allowed)
        if query_topic:
            entry["query_topics"].add(query_topic)
        entry["core_topics"].update(core)

    entries = list(merged.values())
    words = [_title_words(e["item"]["title"]) for e in entries]
    kept = []
    for i, entry in enumerate(entries):
        twin = next((k for k in kept if _same_work(words[i], words[k])), None)
        if twin is None:
            kept.append(i)
            continue
        other = entries[twin]
        _merge(other, entry["item"], entry["allowed"])
        other["query_topics"] |= entry["query_topics"]
        other["core_topics"] |= entry["core_topics"]
    merged = {entries[i]["item"]["key"]: entries[i] for i in kept}
    print(f"  {len(entries)} unique titles, {len(merged)} after merging versions")

    result = {}
    for topic in topics:
        tid = topic["id"]
        patterns = [_keyword_pattern(kw) for kw in topic["keywords"]]
        required = [[_keyword_pattern(kw) for kw in group] for group in topic.get("require", [])]
        scored = []
        for entry in merged.values():
            if tid not in entry["allowed"] or not meets_requirements(entry["item"], required):
                continue
            score = score_item(entry["item"], patterns)
            if tid in entry["query_topics"]:
                score += 2  # this topic's own search query found it
            if tid in entry["core_topics"]:
                score += topic.get("min_score", 2)  # a core journal for this topic
            if score >= topic.get("min_score", 2):
                scored.append({**entry["item"], "score": score})
        scored.sort(key=lambda it: it["score"], reverse=True)
        result[tid] = scored[:max_candidates]
        print(f"  Topic '{topic['name']}': {len(scored)} matches, keeping {len(result[tid])}")

    return result


if __name__ == "__main__":
    import sys

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    papers = fetch_all_papers(base, fresh="--fresh" in sys.argv)
    for tid, items in papers.items():
        print(f"\n=== {tid} ===")
        for it in items[:10]:
            print(f"  [{it['score']}] {it['title'][:100]} ({it['source']}, {it['kind']}, {it['date']})")
