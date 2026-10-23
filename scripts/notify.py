"""
notify.py — Build a short news digest and send it via Telegram.

No third-party packages required (uses urllib.request from stdlib).
"""

import html
import os
import re
import urllib.error
import urllib.request
import urllib.parse
import json


def _get_chinese_section(md_content: str) -> str:
    """Return the Chinese half of a bilingual report."""
    marker = "\n---\n---\n"
    idx = md_content.find(marker)
    if idx == -1:
        return md_content
    return md_content[idx + len(marker):]


def build_digest(date_str: str, topic_reports: list[tuple]) -> str:
    """
    Build a detailed plain-text digest in Chinese with top stories per topic.

    Args:
        date_str: Human-readable date, e.g. "March 15, 2026"
        topic_reports: List of (topic_name, article_count, md_content) tuples

    Returns:
        Formatted digest string.
    """
    lines = [f"\U0001f4f0 每日新闻摘要 · {date_str}", ""]

    for topic_name, article_count, md_content in topic_reports:
        chinese = _get_chinese_section(md_content)
        stories = _extract_top_stories(chinese, n=3)
        trend = _extract_trend(chinese)

        lines.append(f"【{topic_name}】({article_count} 篇)")
        for i, (title, summary) in enumerate(stories, 1):
            lines.append(f"{i}. {title}")
            if summary:
                lines.append(f"   {summary}")
        if trend:
            lines.append(f"📈 展望: {trend}")
        lines.append("")

    return "\n".join(lines).strip()


def _split_h2_sections(md_content: str) -> list[tuple[str, str]]:
    """Split a markdown report into (header_text, body) pairs by H2 (`## `) headers.

    Order is preserved. The H1 title and any preamble before the first H2 are
    dropped (only `## ` sections are returned).
    """
    sections = []
    parts = re.split(r"(?m)^##\s+(?!#)(.+)$", md_content)
    # parts = [preamble, header1, body1, header2, body2, ...]
    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        sections.append((header, body))
    return sections


def _trim_lines(text: str, max_lines: int) -> str:
    """Keep at most `max_lines` non-empty lines of `text`, joined by newlines."""
    kept = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(kept[:max_lines])


def build_weekly_digest(date_str: str, weekly_reports: list[tuple]) -> str:
    """
    Build a concise Chinese weekly digest for Telegram.

    For each topic we surface three sections by H2 *position* (robust to how the
    translator renders the header text). The weekly template emits H2 sections in
    a fixed order:
        0: Key Events This Week
        1: How This Week Compares to Last Week
        2: Evolving Trends
        3: Updated Trend Predictions
        4: Sources Referenced This Week

    Args:
        date_str: Human-readable date, e.g. "June 19, 2026"
        weekly_reports: List of (topic_name, english_report, chinese_report) tuples.

    Returns:
        Formatted digest string.
    """
    lines = [f"\U0001f4c5 每周新闻综述 · {date_str}", ""]

    for topic_name, _english, chinese in weekly_reports:
        sections = _split_h2_sections(chinese)
        lines.append(f"【{topic_name}】")

        # 0: Key Events — top few lines only
        if len(sections) > 0:
            header, body = sections[0]
            lines.append(f"■ {header}")
            lines.append(_trim_lines(body, 5))

        # 1: Comparison to last week
        if len(sections) > 1:
            header, body = sections[1]
            lines.append(f"■ {header}")
            lines.append(_trim_lines(body, 4))

        # 3: Updated trend predictions
        if len(sections) > 3:
            header, body = sections[3]
            lines.append(f"■ {header}")
            lines.append(_trim_lines(body, 5))

        lines.append("")

    return "\n".join(lines).strip()


_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _plain(text: str) -> str:
    """Markdown to plain text: links become their text, bold/italic markers go."""
    return _MD_LINK.sub(r"\1", text).replace("**", "").replace("*", "").strip()


def _first_sentence(line: str, limit: int = 150) -> str:
    end = re.search(r"[。！？]|[.!?](?=\s|$)", line)
    return line[: end.end()] if end else line[:limit]


def _highlight_items(body: str) -> list[tuple[str, str, str]]:
    """(title, summary, url) for each `### N. [Title](URL)` item of a Highlights
    section. The title prefers the translator's Chinese rendering in
    parentheses after the link; the summary is the text below the author line."""
    items = []
    for block in re.split(r"(?m)^###\s+", body)[1:]:
        lines = block.strip().splitlines()
        link = _MD_LINK.search(lines[0])
        if not link:
            continue
        rest = lines[0][link.end():].strip()
        zh_title = re.match(r"^[（(](.+)[)）]$", rest)
        title = zh_title.group(1) if zh_title else link.group(1)
        text = [ln for ln in lines[1:] if ln.strip() and not ln.strip().startswith("*")]
        items.append((title, _plain(" ".join(text)), link.group(2)))
    return items


def build_papers_digest(date_str: str, paper_reports: list[tuple]) -> str:
    """
    Build the Chinese Telegram digest of the weekly paper report: for each topic,
    a short summary of every Highlights paper with its link right after it, then
    the predictions. Predictions are capped so the paper summaries make up at
    least 3/4 of the text.

    The paper template emits H2 sections in a fixed order (found by position,
    so translated headers don't matter):
        0: Highlights  1: Paper Digest  2: Research Trends & Analysis
        3: Compared with Last Week  4: Predictions & What to Watch

    Args:
        paper_reports: List of (topic_name, item_count, english_report, chinese_report).
    """
    lines = [f"\U0001f4da 每周论文速递 · {date_str}", ""]

    for topic_name, count, english, chinese in paper_reports:
        sections = _split_h2_sections(chinese)
        english_sections = _split_h2_sections(english)
        en_items = _highlight_items(english_sections[0][1]) if english_sections else []
        items = _highlight_items(sections[0][1]) if sections else []
        if not items:
            items = en_items  # translation lost the structure: fall back to English
        elif len(items) == len(en_items):
            # The English report's links are the checked ones (fix_links).
            items = [(t, s, en[2]) for (t, s, _), en in zip(items, en_items)]

        lines.append(f"【{topic_name}】({count} 篇)")
        summary_chars = 0
        for i, (title, summary, url) in enumerate(items, 1):
            lines.append(f"{i}. {title}")
            if summary:
                lines.append(summary)
            lines.append(f"🔗 {url}")
            lines.append("")
            summary_chars += len(title) + len(summary)

        # 4: Predictions — first sentence of each bullet, within a budget of
        # 1/3 of the summary text (so summaries are >= 3/4 of the total).
        if len(sections) > 4:
            header, body = sections[4]
            budget = summary_chars // 3
            kept = []
            for line in _plain(body).splitlines():
                line = line.strip().lstrip("-•").strip()
                if not line:
                    continue
                sentence = _first_sentence(line)
                if kept and sum(map(len, kept)) + len(sentence) > budget:
                    break
                kept.append(sentence)
            if kept:
                lines.append(f"🔭 {header}")
                lines += [f"- {k}" for k in kept]

        lines.append("")

    return "\n".join(lines).strip()


def _extract_top_stories(md_content: str, n: int = 3) -> list[tuple[str, str]]:
    """Extract top N stories (title, first-sentence summary) from the Top News section."""
    match = re.search(
        r"##\s+(?:Top News|头条新闻|最新新闻)\b.*?\n(.*?)(?=\n##[^#]|\Z)",
        md_content, re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return []

    section = match.group(1)
    stories = []

    # Split by ### headers: "### 1. Title", "### 2. Title", etc.
    parts = re.split(r"^\s*###\s+\d+\.\s+", section, flags=re.MULTILINE)
    # First element is text before ### 1, skip it
    for part in parts[1: n + 1]:
        lines = part.strip().splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        # Get first non-empty body line as summary, strip markdown
        summary = ""
        for line in lines[1:]:
            line = line.strip()
            if not line or line.startswith("**来源") or line.startswith("**Source"):
                continue
            # Strip markdown links: [text](url) -> text
            line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
            # Truncate to ~100 chars at sentence boundary
            sent = re.search(r"[。.!?！？]", line)
            if sent:
                summary = line[: sent.end()]
            else:
                summary = line[:100]
            break
        stories.append((title, summary))

    return stories


def _extract_trend(md_content: str) -> str:
    """Extract the first prediction bullet from the ## Trend Prediction section."""
    match = re.search(r"##\s+(?:Trend Prediction|趋势预测)\b.*?\n(.*?)(?=\n##[^#]|\Z)", md_content, re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    section = match.group(1).strip()

    for line in section.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip subheader lines like "**Short-Term (Next 7–14 Days):**"
        if re.match(r"^\*\*[^*]+\*\*:?\s*$", line):
            continue
        # Strip leading list markers
        line = re.sub(r"^[-*•]\s*", "", line)
        # Strip emoji
        line = re.sub(r"[\U0001F000-\U0001FFFF\U00002600-\U000027BF\U0001FA00-\U0001FA9F]", "", line).strip()
        # Strip bold markers
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        if not line:
            continue
        # Return up to first sentence boundary
        sentence_end = re.search(r"[.!?]", line)
        if sentence_end:
            return line[: sentence_end.end()].strip()
        return line[:120].strip()

    return ""


def _chunk_message(text: str, limit: int = 4000) -> list[str]:
    """
    Split `text` into chunks no larger than `limit` characters.

    Prefers blank-line (topic) boundaries, then single newlines, and falls back
    to a hard slice for any single line longer than the limit.
    """
    if len(text) <= limit:
        return [text] if text else [""]

    chunks: list[str] = []
    # Split into topic blocks separated by blank lines (build_digest emits these).
    blocks = text.split("\n\n")
    current = ""

    def flush():
        nonlocal current
        if current:
            chunks.append(current.rstrip())
            current = ""

    for block in blocks:
        # If a single block is still too big, split it by lines / hard slice.
        if len(block) > limit:
            flush()
            line_buf = ""
            for line in block.split("\n"):
                # Hard-slice an over-long line.
                while len(line) > limit:
                    if line_buf:
                        chunks.append(line_buf.rstrip())
                        line_buf = ""
                    chunks.append(line[:limit])
                    line = line[limit:]
                candidate = line if not line_buf else line_buf + "\n" + line
                if len(candidate) > limit:
                    chunks.append(line_buf.rstrip())
                    line_buf = line
                else:
                    line_buf = candidate
            if line_buf:
                chunks.append(line_buf.rstrip())
            continue

        candidate = block if not current else current + "\n\n" + block
        if len(candidate) > limit:
            flush()
            current = block
        else:
            current = candidate

    flush()
    return [c for c in chunks if c]


def _html_to_plain(text: str) -> str:
    """Telegram-HTML message to plain text: <a href="u">t</a> -> "t u", entities unescaped."""
    text = re.sub(r'<a href="([^"]*)">(.*?)</a>',
                  lambda m: f"{m.group(2)} {m.group(1)}", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def send_telegram(message: str, base_dir: str = None, html_mode: bool = False,
                  preview: bool = True) -> None:
    """
    Send a message via Telegram bot.

    Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment variables.
    Long messages are split into multiple sends to stay under Telegram's
    4096-character per-message limit.

    html_mode=True sends with parse_mode HTML (e.g. <a href="...">title</a>
    hyperlinks); if Telegram rejects a chunk's markup (HTTP 400), that chunk is
    resent as plain text so nothing is lost. preview=False turns off link
    preview cards.
    Raises an exception if credentials are missing or any request fails.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set")
    if not chat_id:
        raise ValueError("TELEGRAM_CHAT_ID environment variable is not set")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = _chunk_message(message)
    total = len(chunks)

    def post(fields: dict, idx: int) -> None:
        payload = {"chat_id": chat_id, **fields}
        if not preview:
            payload["link_preview_options"] = {"is_disabled": True}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if not result.get("ok"):
                raise RuntimeError(f"Telegram API error (chunk {idx}/{total}): {result}")

    for idx, chunk in enumerate(chunks, 1):
        text = chunk if total == 1 else (chunk if idx == 1 else f"(续 {idx}/{total})\n{chunk}")
        if not html_mode:
            post({"text": text}, idx)
            continue
        try:
            post({"text": text, "parse_mode": "HTML"}, idx)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            print(f"  WARNING: Telegram rejected HTML (chunk {idx}/{total}); resending as plain text")
            post({"text": _html_to_plain(text)}, idx)
