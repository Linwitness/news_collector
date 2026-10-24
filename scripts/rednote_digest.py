"""
rednote_digest.py — rednote (Xiaohongshu) home-feed news for the hourly alert.

Opens the logged-in home feed in Chromium (headless, persistent profile), screens
up to rednote.screen_per_run unseen notes by title in one LLM call, opens only the
promising ones (at most rednote.notes_per_run) the way a user would (click, read,
go back), and asks the LLM which are timely news/events. Each significant note
becomes a Telegram item: a hyperlinked title, then a short Chinese paragraph.

alert_news.py calls collect() every hour and sends these items in the same
message as the news headlines. Run manually:
    python3 scripts/rednote_digest.py --login    # one-time: scan the QR code in the browser window
    python3 scripts/rednote_digest.py --dry-run  # print notes + message; don't send or update state
    python3 scripts/rednote_digest.py            # standalone run that sends its own message
    python3 scripts/rednote_digest.py --export-state FILE  # save the login (cookies) to move it
    python3 scripts/rednote_digest.py --import-state FILE  # load it on another machine (e.g. a Pi)

Login lives in a browser profile outside the repo (rednote.profile_dir). When the
session expires or rednote shows a verification page, one Telegram warning is
sent per day and the rednote part is skipped; it never tries to solve a captcha.
"""

import argparse
import fcntl
import html
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))

from alert_news import _load_dotenv, load_seen, save_seen
from llm import create, get_llm
from notify import send_telegram


DEFAULTS = {
    "url": "https://www.rednote.com/explore",
    "screen_per_run": 30,
    "notes_per_run": 10,
    "profile_dir": "~/.news_collector/rednote_profile",
    "state_path": "output/state/rednote_seen.json",
    "seen_ttl_days": 7,
    "headless": True,
    "browser_executable": "",
    "significance": (
        "Timely news and events: real-world happenings, breaking or developing stories, "
        "policy and regulation changes, economic, market and price moves, public safety, "
        "notable announcements or launches. Not lifestyle, shopping or recommendation "
        "posts, ads, tutorials, celebrity gossip, games, memes or personal diaries."
    ),
}

NOTE_LINK = "https://www.rednote.com/discovery/item/{id}"

# The page keeps its data in window.__INITIAL_STATE__ (Vue refs: value in ._value).
FEED_JS = """() => {
  const v = r => (r && typeof r === 'object' && '__v_isRef' in r) ? r._value : r;
  const s = window.__INITIAL_STATE__;
  if (!s || !s.feed) return null;
  const feeds = v(s.feed.feeds) || [];
  return {
    loggedIn: !!v(s.user && s.user.loggedIn),
    notes: feeds.filter(f => f && f.modelType === 'note' && f.noteCard).map(f => {
      const c = f.noteCard, u = c.user || {};
      return {id: f.id, title: c.displayTitle || '', type: c.type || '',
              author: u.nickname || u.nickName || ''};
    }),
  };
}"""

NOTE_JS = """id => {
  const v = r => (r && typeof r === 'object' && '__v_isRef' in r) ? r._value : r;
  const map = v(window.__INITIAL_STATE__.note.noteDetailMap) || {};
  const n = map[id] && map[id].note;
  // The card data lands first; `time` only arrives with the full note.
  if (!n || !n.noteId || !n.time) return null;
  const i = n.interactInfo || {};
  return {title: n.title || '', desc: n.desc || '', time: n.time || 0,
          ip: n.ipLocation || '', type: n.type || '',
          author: (n.user || {}).nickname || '',
          tags: (n.tagList || []).map(t => t.name).filter(Boolean),
          likes: i.likedCount || '', comments: i.commentCount || ''};
}"""

FEED_READY_JS = """() => {
  const s = window.__INITIAL_STATE__;
  const f = s && s.feed && s.feed.feeds;
  const a = f && (f._value || f);
  return Array.isArray(a) && a.length > 0;
}"""


class SessionProblem(Exception):
    """Logged out or blocked by a verification page: needs the user, not a retry."""


def load_settings():
    """Return (all settings, rednote settings merged over DEFAULTS)."""
    with open(os.path.join(BASE_DIR, "config", "settings.json")) as f:
        settings = json.load(f)
    cfg = {**DEFAULTS, **settings.get("rednote", {})}
    # Per-machine override (set in the Pi's crontab when it uses the system Chromium).
    if os.environ.get("REDNOTE_BROWSER_EXECUTABLE"):
        cfg["browser_executable"] = os.environ["REDNOTE_BROWSER_EXECUTABLE"]
    return settings, cfg


def state_dir_for(cfg: dict) -> str:
    state_dir = os.path.dirname(os.path.join(BASE_DIR, cfg["state_path"]))
    os.makedirs(state_dir, exist_ok=True)
    return state_dir


def acquire_lock(state_dir: str):
    """Own lock: the browser profile can't be opened by two runs (or --login) at once.

    Returns the open lock file, or None if another run holds it.
    """
    lf = open(os.path.join(state_dir, ".rednote.lock"), "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lf.close()
        return None
    return lf


def open_browser(p, cfg: dict, headless: bool):
    # Playwright's own Chromium (`python -m playwright install --no-shell chromium`),
    # not the system Chrome: Chrome auto-updates past what the installed Playwright
    # supports (Python 3.9 caps it at 1.60, which hangs on Chrome 156).
    # rednote.browser_executable overrides it (e.g. /usr/bin/chromium on a Pi).
    profile = os.path.expanduser(cfg["profile_dir"])
    os.makedirs(profile, mode=0o700, exist_ok=True)
    browser = ({"executable_path": cfg["browser_executable"]} if cfg.get("browser_executable")
               else {"channel": "chromium"})
    return p.chromium.launch_persistent_context(
        profile,
        **browser,
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 900},
        locale="zh-CN",
    )


def login(cfg: dict) -> int:
    """Open a visible browser on rednote and wait until the QR login completes."""
    from playwright.sync_api import sync_playwright

    lf = acquire_lock(state_dir_for(cfg))
    if lf is None:
        print("A rednote run is in progress; try again in a minute.")
        return 1
    with lf, sync_playwright() as p:
        ctx = open_browser(p, cfg, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(cfg["url"], wait_until="domcontentloaded")
        print("In the browser window: open the login dialog if it isn't shown, then scan the "
              "QR code with the rednote app. Waiting up to 10 minutes …")
        deadline = time.time() + 600
        while time.time() < deadline:
            time.sleep(3)
            try:
                state = page.evaluate(FEED_JS)
            except Exception:
                continue  # page navigating after login
            if state and state["loggedIn"]:
                print("Logged in; session saved to the profile.")
                time.sleep(3)
                ctx.close()
                return 0
        ctx.close()
    print("Timed out waiting for login.")
    return 1


def _logged_in(page, cfg: dict) -> bool:
    page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_function(FEED_READY_JS, timeout=30000)
    return bool(page.evaluate(FEED_JS)["loggedIn"])


def export_state(cfg: dict, path: str) -> int:
    """Save the profile's rednote login (cookies + localStorage) as Playwright storage state.

    Browser profiles don't move between machines (macOS encrypts cookies with the
    Keychain); this JSON does. It is a credential: keep it private.
    """
    from playwright.sync_api import sync_playwright

    lf = acquire_lock(state_dir_for(cfg))
    if lf is None:
        print("A rednote run is in progress; try again in a minute.")
        return 1
    with lf, sync_playwright() as p:
        ctx = open_browser(p, cfg, headless=True)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if not _logged_in(page, cfg):
                print("Not logged in; run --login first.")
                return 1
            ctx.storage_state(path=path)
        finally:
            ctx.close()
    os.chmod(path, 0o600)
    print(f"Saved login state to {path}.")
    return 0


def import_state(cfg: dict, path: str) -> int:
    """Load a storage state saved by --export-state into this machine's profile."""
    from playwright.sync_api import sync_playwright

    with open(path) as f:
        state = json.load(f)
    lf = acquire_lock(state_dir_for(cfg))
    if lf is None:
        print("A rednote run is in progress; try again in a minute.")
        return 1
    with lf, sync_playwright() as p:
        ctx = open_browser(p, cfg, headless=True)
        try:
            ctx.add_cookies(state.get("cookies", []))
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for origin in state.get("origins", []):
                page.goto(origin["origin"], wait_until="domcontentloaded", timeout=60000)
                page.evaluate("items => items.forEach(i => localStorage.setItem(i.name, i.value))",
                              origin.get("localStorage", []))
            ok = _logged_in(page, cfg)
        finally:
            ctx.close()
    print("Imported login state; logged in." if ok else
          "Imported login state, but rednote does not accept it; run --login on this machine.")
    return 0 if ok else 1


def _clean_desc(desc: str) -> str:
    return desc.replace("[话题]#", "#").strip()


def _json_array(text: str) -> list:
    text = text.strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError(f"no JSON array in LLM reply: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def screen_titles(cards: list, settings: dict, significance: str) -> list:
    """One cheap LLM pass over feed titles: ids worth opening, most promising first."""
    rows = "\n".join(f"{c['id']} | {c.get('type', '')} | {c.get('author', '')} | "
                     f"{c.get('title') or '(no title)'}" for c in cards)
    prompt = f"""You are pre-screening a user's rednote (Xiaohongshu) home feed by title only, to decide which notes are worth opening.

What we are looking for:
{significance}

Pick the notes whose title suggests they may be about that; include a note when unsure. Skip notes the title clearly rules out.

Reply with only a JSON array of ids, most promising first, e.g. ["<id>", "<id>"]. Reply [] if none.

id | type | author | title
{rows}
"""
    known = {c["id"] for c in cards}
    ids = []
    for nid in _json_array(create(get_llm(settings), prompt)):
        nid = str(nid).strip()
        if nid in known and nid not in ids:
            ids.append(nid)
    return ids


def _open_notes(page, cards: list) -> list:
    """Open each card like a user (click, read, go back); return the full notes."""
    from playwright.sync_api import TimeoutError as PWTimeout

    notes = []
    for card in cards:
        time.sleep(random.uniform(2, 5))
        nid = card["id"]
        try:
            # Only the first ~18 cards are rendered; scroll down until this one is.
            link = page.locator(f'section.note-item a.cover[href*="{nid}"]').first
            for _ in range(10):
                if link.count():
                    break
                page.mouse.wheel(0, 800)
                time.sleep(random.uniform(0.8, 1.5))
            link.click(timeout=10000)
            page.wait_for_function(NOTE_JS, arg=nid, timeout=15000)
            detail = page.evaluate(NOTE_JS, nid)
            notes.append({**card, **{k: v for k, v in detail.items() if v},
                          "desc": _clean_desc(detail["desc"])})
        except PWTimeout:
            print(f"  WARNING: could not open note {nid}; skipping")
        # The note opens as an overlay on its own URL; going back closes it.
        try:
            if "/discovery/item/" in page.url or "/explore/" in page.url:
                page.go_back(wait_until="domcontentloaded")
            page.wait_for_selector("section.note-item", timeout=15000)
        except PWTimeout:
            print("  WARNING: feed did not come back after a note; stopping early")
            break
    return notes


def fetch_notes(cfg: dict, settings: dict, seen: dict, headless: bool):
    """Screen unseen feed cards by title and open the promising ones.

    Returns (screened cards, opened notes).
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    with sync_playwright() as p:
        ctx = open_browser(p, cfg, headless=headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_function(FEED_READY_JS, timeout=30000)
            except PWTimeout:
                page.screenshot(path=os.path.join(BASE_DIR, "logs", "rednote_last_error.png"))
                raise SessionProblem(f"home feed did not load (page: {page.url}); "
                                     "possibly a verification page")
            state = page.evaluate(FEED_JS)
            if not state["loggedIn"]:
                raise SessionProblem("not logged in (session expired)")

            fresh = [n for n in state["notes"] if n["id"] not in seen]
            cards = fresh[:cfg["screen_per_run"]]
            print(f"  rednote feed: {len(state['notes'])} notes, unseen: {len(fresh)}, "
                  f"screening {len(cards)}")
            if not cards:
                return [], []
            ids = screen_titles(cards, settings, cfg["significance"])[:cfg["notes_per_run"]]
            print(f"  rednote title screen: opening {len(ids)}")
            # Open in feed order so finding the next card only needs scrolling down.
            return cards, _open_notes(page, [c for c in cards if c["id"] in ids])
        finally:
            ctx.close()


def build_prompt(notes: list, significance: str) -> str:
    blocks = []
    for n in notes:
        posted = (datetime.fromtimestamp(n["time"] / 1000).strftime("%Y-%m-%d %H:%M")
                  if n.get("time") else "unknown")
        blocks.append(
            f"### id: {n['id']}\n"
            f"Title: {n.get('title') or '(none)'}\n"
            f"Author: {n.get('author', '')} | Posted: {posted} | IP: {n.get('ip', '')} | "
            f"Likes: {n.get('likes', '')} | Type: {n.get('type', '')}\n"
            f"Tags: {', '.join(n.get('tags', []))}\n"
            f"Text:\n{n.get('desc', '')[:1500]}"
        )
    return f"""You are screening a user's rednote (Xiaohongshu) home feed for timely news.

What counts as significant:
{significance}

Below are {len(notes)} notes, each with an id. For each SIGNIFICANT note, write a short paragraph in Chinese (1-3 sentences, at most about 100 characters) saying what happened or what the key information is. Use only the note's own text: do not add facts, numbers or background that are not in it. If it is an opinion or an unverified claim, say so (e.g. "博主称……"). Skip every other note.

Reply with only a JSON array, most important first:
[{{"id": "<note id>", "summary": "<Chinese paragraph>"}}]
Reply [] if no note qualifies.

{chr(10).join(blocks)}
"""


def pick_significant(notes: list, settings: dict, significance: str) -> list:
    """Return [(note, summary)] for the notes the LLM judged significant."""
    by_id = {n["id"]: n for n in notes}
    picks, used = [], set()
    for item in _json_array(create(get_llm(settings), build_prompt(notes, significance))):
        nid = str(item.get("id", "")).strip()
        summary = str(item.get("summary", "")).strip()
        if nid in by_id and nid not in used and summary:
            picks.append((by_id[nid], summary))
            used.add(nid)
    return picks


def format_item(note: dict, summary: str) -> str:
    """Telegram HTML: hyperlinked title · author, then the paragraph."""
    title = note.get("title") or note.get("desc", "")[:30] or "（无标题）"
    link = NOTE_LINK.format(id=note["id"])
    return (f"📕 <a href=\"{html.escape(link, quote=True)}\">{html.escape(title)}</a>"
            f" · {html.escape(note.get('author', ''))}\n{html.escape(summary)}")


def warn_once(reason: str, state_dir: str, dry_run: bool) -> None:
    """Send one Telegram warning per day about a session problem."""
    marker = os.path.join(state_dir, "rednote_warned")
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(marker) and open(marker).read().strip() == today:
        return
    message = (f"⚠️ rednote 摘要暂停：{reason}。\n"
               f"请运行 python3 scripts/rednote_digest.py --login 重新扫码登录。")
    if dry_run:
        print(f"--- DRY RUN — would warn ---\n{message}")
        return
    try:
        send_telegram(message, BASE_DIR)
        with open(marker, "w") as f:
            f.write(today)
    except Exception as e:
        print(f"  ERROR sending warning: {e}")


def collect(dry_run: bool = False, headed: bool = False):
    """One rednote pass without sending.

    Returns (items, commit): Telegram HTML items for the significant notes, and a
    function that marks every screened note seen — call it once the items are
    delivered (or when there are none). Never raises: on any problem it logs and
    returns no items with a no-op commit, so the caller's news alerts still go out
    and the notes are screened again next hour.
    """
    nothing = ([], lambda: None)
    _load_dotenv(BASE_DIR)
    settings, cfg = load_settings()
    state_file = os.path.join(BASE_DIR, cfg["state_path"])
    state_dir = state_dir_for(cfg)
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

    lf = acquire_lock(state_dir)
    if lf is None:
        print("  rednote: another rednote job is running; skipping.")
        return nothing
    with lf:
        seen = load_seen(state_file, cfg["seen_ttl_days"])
        try:
            cards, notes = fetch_notes(cfg, settings, seen, headless=cfg["headless"] and not headed)
            picks = pick_significant(notes, settings, cfg["significance"]) if notes else []
        except SessionProblem as e:
            print(f"  rednote SESSION PROBLEM: {e}")
            warn_once(str(e), state_dir, dry_run)
            return nothing
        except Exception as e:
            print(f"  rednote ERROR: {e}")
            return nothing
    if not cards:
        print("  rednote: no new notes.")
        return nothing

    if dry_run:
        for n in notes:
            print(f"    - {n['id']} | {n.get('title', '')[:40]} | {n.get('author', '')} | "
                  f"{len(n.get('desc', ''))} chars")
    print(f"  rednote: screened {len(cards)}, opened {len(notes)}, significant {len(picks)}")
    for n, _ in picks:
        print(f"    · {n.get('title', '')[:50]} ({n['id']})")

    def commit():
        if dry_run:
            return
        with open(os.path.join(state_dir, ".rednote.lock"), "w") as lf2:
            fcntl.flock(lf2, fcntl.LOCK_EX)
            seen_now = load_seen(state_file, cfg["seen_ttl_days"])
            now = datetime.now(timezone.utc).isoformat()
            for c in cards:
                seen_now[c["id"]] = now
            save_seen(state_file, seen_now)
        print(f"  rednote: {len(cards)} note(s) marked seen.")

    return [format_item(n, s) for n, s in picks], commit


def run(dry_run: bool = False, headed: bool = False) -> int:
    """Standalone run (manual tests): send the rednote items as their own message."""
    print(f"rednote digest @ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    items, commit = collect(dry_run=dry_run, headed=headed)
    if items:
        message = "\n\n".join(items)
        if dry_run:
            print("--- DRY RUN — would send ---")
            print(message)
            print("--- end ---")
            return 0
        try:
            send_telegram(message, BASE_DIR, html_mode=True, preview=False)
        except Exception as e:
            print(f"  ERROR sending Telegram: {e}")
            return 1
    commit()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true",
                        help="Open a visible browser to log in by QR code (one-time).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print notes and the message without sending or updating state.")
    parser.add_argument("--headed", action="store_true",
                        help="Show the browser window during a run (debugging).")
    parser.add_argument("--export-state", metavar="FILE",
                        help="Save the rednote login (cookies) to FILE, to move it to another machine.")
    parser.add_argument("--import-state", metavar="FILE",
                        help="Load a login saved by --export-state into this machine's profile.")
    args = parser.parse_args()
    if args.login or args.export_state or args.import_state:
        _, cfg = load_settings()
        if args.export_state:
            sys.exit(export_state(cfg, args.export_state))
        if args.import_state:
            sys.exit(import_state(cfg, args.import_state))
        sys.exit(login(cfg))
    sys.exit(run(dry_run=args.dry_run, headed=args.headed))


if __name__ == "__main__":
    main()
