"""Read a press release, sponsored or partner section index page as if it were a feed.

Most such sections publish no RSS, so the collector reads the section page itself and
treats every same-site link that looks like an article as a feed entry. The page is
fetched under the same rules as article pages (robots.txt, one request per domain every
three seconds, identifying user agent). The return value has the shape of
feeds_util.fetch_feed, so the collector handles both the same way.
"""
import datetime as dt
import json
import re
import threading
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlsplit

MAX_ENTRIES = 60
MIN_TITLE_CHARS = 20
_SKIP_PATH = re.compile(r"/(tag|tags|topic|topics|author|authors|category|categories|page|search|login|subscribe|about|contact|privacy|terms)(/|$)", re.I)


def _same_site(a: str, b: str) -> bool:
    ha = urlsplit(a).netloc.lower().replace("www.", "")
    hb = urlsplit(b).netloc.lower().replace("www.", "")
    return bool(ha) and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha))


def _stem(path: str) -> str:
    first = next((s for s in path.split("/") if s), "")
    return re.sub(r"s$", "", first.lower())


def article_links(html: str, section_url: str) -> List[Dict]:
    """Links under the section that look like articles: same site, deeper than the section
    path or carrying a story slug, with a headline length anchor text. First occurrence wins."""
    from lxml import html as lhtml
    try:
        doc = lhtml.fromstring(html)
    except Exception:
        return []
    section_path = urlsplit(section_url).path.rstrip("/")
    out, seen = [], set()
    for a in doc.iter("a"):
        href = a.get("href")
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = urljoin(section_url, href).split("#")[0]
        if not url.startswith("http") or not _same_site(url, section_url) or url in seen:
            continue
        path = urlsplit(url).path.rstrip("/")
        if not path or path == section_path or _SKIP_PATH.search(path):
            continue
        last = path.rsplit("/", 1)[-1]
        # The link must sit under the section: its path, or a first segment that is a close variant of the
        # section's ("/media-release/story" under "/media-releases"). Site-wide navigation does not count.
        # Tag, topic and category listings link stories that live elsewhere on the site.
        listing = bool(re.match(r"^/(tag|tags|topic|topics|category|categories|rubrique|categoria|kategorie|seccion|section)/", section_path + "/", re.I))
        under = path.startswith(section_path + "/") or _stem(path) == _stem(section_path) or \
            (listing and (last.count("-") >= 3 or bool(re.search(r"\d{5,}", last))))
        looks_like_story = under and (path.startswith(section_path + "/") or last.count("-") >= 3 or re.search(r"\d{5,}", last))
        title = " ".join((a.text_content() or "").split())
        if not looks_like_story or len(title) < MIN_TITLE_CHARS:
            continue
        seen.add(url)
        out.append({"link": url, "title": title[:300], "summary": "", "published_parsed": None})
        if len(out) >= MAX_ENTRIES:
            break
    return out


def fetch_entries(url: str, type_: Optional[str] = "page") -> Dict:
    """Fetch a section page and return {ok, entries, status, error}, like feeds_util.fetch_feed.
    A section of type aap_widget is read from the AAP release listing instead (aap_entries)."""
    if type_ == AAP_WIDGET:
        return aap_entries(url)
    from pipeline import fetch_articles
    allowed, why = fetch_articles.can_fetch(url)
    if not allowed:
        return {"ok": False, "entries": [], "status": None, "error": why}
    html, status, err = fetch_articles.get_page(url)
    if html is None:
        return {"ok": False, "entries": [], "status": status, "error": err}
    entries = article_links(html, url)
    if not entries:
        return {"ok": False, "entries": [], "status": status, "error": "no_article_links"}
    return {"ok": True, "entries": entries, "status": status, "error": None}


# Australian Community Media mastheads (The Canberra Times, Newcastle Herald and the rest) carry a
# "Press Releases from AAP" page. The page is an empty shell that aaprwidget.js fills in the reader's
# browser from the listing of the AAP release service (newsroom.aap.com.au/releases?from=N, ten
# releases a page, newest first), and its links are javascript:window.open calls, so article_links
# finds nothing on it. A section of type aap_widget is read from that listing instead. The releases
# appear on each masthead's own site, so they count as that masthead's output, like any press release
# section; AAP is an Australian distribution service, not a worldwide wire, and is not registered as
# one. Every masthead shows the same list, so the listing is read once per run and shared. Each
# masthead gets its own copy of each release: the link is the release's page on the AAP service (the
# readable copy the widget itself loads) with the masthead's host in a "masthead" parameter the
# service ignores, because articles are unique by URL and without it only the first masthead polled
# would keep the release. The copies carry the release's own title, so the near-duplicate detector
# links them into one underlying item.
#
# AAP carries every release it distributes, about 37 a day (1,459 from 2026-09-03 to 2026-10-12), and
# few concern China: 49 of those were Chinese state releases. Exempt from the keyword gate, each
# masthead's page would cross the section guard's 500 item floor (fetch_feeds.SECTION_GUARD_MIN_ITEMS)
# in under a fortnight; the guard itself would probably not trip, since the state releases alone are
# 3.4% against its 2% line, but ten mastheads would queue about 11,000 fetches a month of releases
# about nothing to do with China, all from one host at one request every three seconds. So the
# keyword gate applies to these items from the start (keyword_gate in the result). It loses nothing
# found by hand: the Chinese issuers name themselves or a Chinese place in the headline or abstract,
# and all 49 pass it, among 352 of the 1,459 (24%).
AAP_WIDGET = "aap_widget"
AAP_LISTING = "https://newsroom.aap.com.au/releases"
AAP_TZ = "Australia/Sydney"            # the listing's "Published" times are Sydney local time
# The first poll reads back to this date, the start of the releases found by hand; later polls read
# back to the earliest successful poll among the mastheads polled within AAP_RECENT_DAYS, less
# AAP_OVERLAP_HOURS (aap_start). AAP_MAX_PAGES caps one run's reading.
AAP_BACKFILL_SINCE = "2026-09-03"
AAP_OVERLAP_HOURS = 6
AAP_RECENT_DAYS = 7
AAP_MAX_PAGES = 200
# The listing is read once, before the discovery workers start (aap_preread), so no worker waits on
# it. Read lazily under the lock instead, the first backfill (about 146 pages, 8.5 minutes at one
# request every three seconds) held about ten of the sixteen workers on the lock. The read may take at
# most AAP_PREREAD_SHARE of the time the stage has left. A read cut short there (or at AAP_MAX_PAGES)
# keeps what it read, which the mastheads use, and records the stretch still to cover in aap_state:
# the page it stopped on, the oldest release it reached and the date it was reading back to. The next
# run reads the new releases from page 1 as usual, then resumes at that page. Pages only move back as
# releases are added, so the stored page then starts at or above the oldest release reached; a page
# that starts below it (releases withdrawn) sends the read AAP_RESUME_BACKSTEP pages back first.
AAP_PREREAD_SHARE = 0.3
AAP_RESUME_BACKSTEP = 10
_RKEY = re.compile(r"rkey=([A-Za-z0-9_.-]+)")
_PUBLISHED = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})")
_aap_lock = threading.Lock()
_aap = {"since": None, "listing": None}
_clock = time.time                    # the stage deadline is time.time based; tests replace this


def _aap_time(text: str) -> Optional[dt.datetime]:
    m = _PUBLISHED.search(text or "")
    if not m:
        return None
    day, month, year, hour, minute = (int(x) for x in m.groups())
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(AAP_TZ)
    except Exception:
        tz = dt.timezone(dt.timedelta(hours=10))
    try:
        return dt.datetime(year, month, day, hour, minute, tzinfo=tz).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def aap_releases(html: str) -> List[Dict]:
    """The releases on one listing page, newest first: {rkey, title, summary, published (UTC or None)}."""
    from lxml import html as lhtml
    try:
        doc = lhtml.fromstring(html)
    except Exception:
        return []
    out = []
    for li in doc.xpath('//li[contains(concat(" ", normalize-space(@class), " "), " aapr-widget_release ")]'):
        a = li.xpath('.//span[contains(@class, "aapr-widget_title")]//a')
        m = _RKEY.search(a[0].get("href") or "") if a else None
        if not m:
            continue
        out.append({
            "rkey": m.group(1),
            "title": " ".join(a[0].text_content().split())[:300],
            "summary": " ".join(li.xpath('string(.//span[contains(@class, "aapr-widget_abstract")])').split())[:2000],
            "published": _aap_time(li.xpath('string(.//span[contains(@class, "aapr-widget_datetime")])')),
        })
    return out


def aap_listing(since: dt.datetime, max_pages: int = AAP_MAX_PAGES, start_page: int = 1,
                stop_by: Optional[float] = None, resume_oldest: Optional[dt.datetime] = None) -> Dict:
    """Read the listing from page start_page back to `since`: {ok, releases, status, error, pages,
    complete, oldest, last_page}. One request a page, under the same robots.txt check and per domain
    rate limit as every other request. A page that fails fails the whole read, so the next run reads
    the same stretch again. A read that reaches stop_by (a _clock time) or max_pages before `since`
    stops with what it read (complete False, at least one page): oldest is the oldest release time it
    reached and last_page the last page it read. resume_oldest, when resuming at start_page, is the
    oldest release the earlier read reached; a start page beginning below it steps back first."""
    from pipeline import fetch_articles
    allowed, why = fetch_articles.can_fetch(AAP_LISTING + "?from=1")
    if not allowed:
        return {"ok": False, "releases": [], "status": None, "error": why, "pages": 0, "complete": False}
    releases, seen, status, pages, page = [], set(), None, 0, max(1, start_page)
    oldest, last_page, complete = None, page, False
    while pages < max_pages:
        if pages and stop_by is not None and _clock() >= stop_by:
            break
        html, status, err = fetch_articles.get_page("%s?from=%d" % (AAP_LISTING, page))
        pages += 1
        if html is None:
            return {"ok": False, "releases": [], "status": status, "error": err, "pages": pages, "complete": False}
        listed = aap_releases(html)
        if resume_oldest is not None:
            stamps = [r["published"] for r in listed if r["published"]]
            if page > 1 and stamps and max(stamps) < resume_oldest:
                page = max(1, page - AAP_RESUME_BACKSTEP)   # releases withdrawn: the stretch moved up
                continue
            resume_oldest = None
        fresh = [r for r in listed if r["rkey"] not in seen]
        if not fresh:
            complete = True
            break                                  # past the last page, or the same page again
        seen.update(r["rkey"] for r in fresh)
        releases.extend(r for r in fresh if r["published"] is None or r["published"] >= since)
        last_page = page
        stamps = [r["published"] for r in fresh if r["published"]]
        if stamps:
            oldest = min(stamps) if oldest is None else min(oldest, min(stamps))
            if min(stamps) < since:
                complete = True
                break
        page += 1
    if not releases and start_page <= 1:
        return {"ok": False, "releases": [], "status": status, "error": "no_releases", "pages": pages,
                "complete": complete}
    return {"ok": True, "releases": releases, "status": status, "error": None, "pages": pages,
            "complete": complete, "oldest": oldest, "last_page": last_page}


def aap_start(conn, outlets: List[Dict]) -> None:
    """Set where this run's listing read stops and drop the previous run's listing. The listing is
    shared and read once, so the stop is the earliest successful poll among the aap_widget sections
    that succeeded within AAP_RECENT_DAYS, less the overlap: a run stopped at its deadline after
    polling only some mastheads leaves the others behind, and the next read covers them too. A
    masthead failing for longer is left out (its failures are in its feed health) so it cannot hold
    every read back to its old date; when every masthead is, the read goes back to the latest
    successful poll, as after an outage. When none has ever succeeded, AAP_BACKFILL_SINCE."""
    from pipeline import registry
    urls = [fe["url"] for o in outlets for fe in registry.feed_entries(o) if fe.get("type") == AAP_WIDGET]
    since = dt.datetime.fromisoformat(AAP_BACKFILL_SINCE).replace(tzinfo=dt.timezone.utc)
    if urls:
        stamps = []
        for (text,) in conn.execute("SELECT last_ok FROM feed_health WHERE feed_url IN (%s) AND last_ok IS NOT NULL"
                                    % ",".join("?" * len(urls)), urls).fetchall():
            try:
                stamps.append(_utc(text))
            except (ValueError, TypeError):
                continue
        if stamps:
            recent = [t for t in stamps if t >= dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=AAP_RECENT_DAYS)]
            last = min(recent) if recent else max(stamps)
            since = last - dt.timedelta(hours=AAP_OVERLAP_HOURS)
    from pipeline import store
    with _aap_lock:
        _aap.update(since=since, listing=None, urls=urls, started_at=store.utcnow(), gap=None, gap_changed=False)


def _utc(text: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(text)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def aap_gap(conn) -> Optional[Dict]:
    """The stretch an earlier read cut short has still to cover: {page, oldest, target} or None."""
    row = conn.execute("SELECT value FROM aap_state WHERE key='gap'").fetchone()
    if row is None:
        return None
    try:
        g = json.loads(row[0])
        return {"page": max(1, int(g["page"])), "oldest": _utc(g["oldest"]) if g.get("oldest") else None,
                "target": _utc(g["target"])}
    except (ValueError, KeyError, TypeError):
        return None


def aap_preread(conn, deadline: Optional[float] = None) -> Optional[Dict]:
    """Read this run's shared listing before the discovery workers start, so none of them waits on it
    (see the note above AAP_PREREAD_SHARE). Within AAP_PREREAD_SHARE of the time left to `deadline`
    (a _clock time; None for no limit) it reads the new releases from page 1 back to aap_start's date,
    then the stretch an earlier read left (aap_gap). Where it got to is kept for aap_finish to record.
    Returns a summary for the stage counts, or None when the run polls no aap_widget section."""
    if not _aap.get("urls"):
        return None
    began = _clock()
    stop_by = None if deadline is None else began + max(0.0, deadline - began) * AAP_PREREAD_SHARE
    since, gap, tail = _aap["since"], aap_gap(conn), None
    try:
        # Without a deadline the call is the plain aap_listing(since) it always was (tests replace it).
        head = aap_listing(since) if stop_by is None else aap_listing(since, stop_by=stop_by)
        listing, new_gap = head, gap
        if head["ok"] and not head.get("complete", True):
            # Cut short: what lies below the last page read is still to read, back to the older date.
            new_gap = {"page": head["last_page"], "oldest": head.get("oldest"),
                       "target": since if gap is None else min(since, gap["target"])}
        elif head["ok"] and gap is not None:
            left = AAP_MAX_PAGES - head["pages"]
            if gap["target"] >= since:
                new_gap = None                     # this read went back past the stretch anyway
            elif left > 0 and (stop_by is None or _clock() < stop_by):
                tail = aap_listing(gap["target"], max_pages=left, start_page=gap["page"], stop_by=stop_by,
                                   resume_oldest=gap["oldest"])
                if tail["ok"]:                     # a failed page leaves the stretch for the next run
                    known = {r["rkey"] for r in head["releases"]}
                    listing = dict(head, releases=head["releases"] + [r for r in tail["releases"] if r["rkey"] not in known])
                    new_gap = None if tail["complete"] else {
                        "page": tail["last_page"], "oldest": tail.get("oldest") or gap["oldest"], "target": gap["target"]}
    except Exception as exc:  # noqa: BLE001  the mastheads report it; the next run reads the stretch again
        listing, new_gap = {"ok": False, "releases": [], "status": None,
                            "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}, gap
        head = {"pages": 0}
    with _aap_lock:
        _aap.update(listing=listing, gap=new_gap, gap_changed=new_gap != gap)
    return {"pages": head["pages"] + (tail["pages"] if tail else 0), "releases": len(listing["releases"]),
            "seconds": round(_clock() - began, 1), "error": listing["error"] or (tail["error"] if tail else None),
            "resumed_from_page": gap["page"] if tail else None,
            "left_to_read_back_to": new_gap["target"].isoformat() if new_gap else None}


def aap_finish(conn) -> None:
    """Record where this run's listing read got to (aap_state). Only once the read has reached a
    masthead (an aap_widget section polled successfully since aap_start): a run stopped before any was
    polled leaves the earlier record, so the next run reads the stretch again, as the date aap_start
    takes from feed_health.last_ok does."""
    urls = _aap.get("urls")
    if not urls or not _aap.get("gap_changed"):
        return
    polled = conn.execute("SELECT COUNT(*) FROM feed_health WHERE feed_url IN (%s) AND last_ok >= ?"
                          % ",".join("?" * len(urls)), list(urls) + [_aap["started_at"]]).fetchone()[0]
    if not polled:
        return
    gap = _aap["gap"]
    if gap is None:
        conn.execute("DELETE FROM aap_state WHERE key='gap'")
    else:
        value = json.dumps({"page": gap["page"], "oldest": gap["oldest"].isoformat() if gap["oldest"] else None,
                            "target": gap["target"].isoformat()})
        conn.execute("INSERT INTO aap_state(key, value) VALUES ('gap', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (value,))
    conn.commit()
    _aap["gap_changed"] = False


def aap_entries(section_url: str) -> Dict:
    """One masthead's entries from the shared listing, read by aap_preread before the run's workers
    start. Without it (a check outside a run) the listing is read on first use, back to aap_start's
    date or, without aap_start, one day."""
    with _aap_lock:
        if _aap["listing"] is None:
            since = _aap["since"] or dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
            _aap["listing"] = aap_listing(since)
        listing = _aap["listing"]
    if not listing["ok"]:
        return {"ok": False, "entries": [], "status": listing["status"], "error": listing["error"]}
    host = urlsplit(section_url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    # Every masthead is offered every release read, not only those after its own last poll: the read
    # goes back to the earliest recent poll (aap_start), and a release a masthead already holds is a
    # known URL that insert_discovered ignores.
    entries = [{"link": "%s/%s?masthead=%s" % (AAP_LISTING, r["rkey"], host), "title": r["title"],
                "summary": r["summary"], "published_parsed": r["published"].timetuple() if r["published"] else None}
               for r in listing["releases"]]
    # backfill: the read reaches back to a past date (AAP_BACKFILL_SINCE on the first run, or the last
    # good poll after an outage), so an item published more than config.BACKFILL_LAG_HOURS before it was
    # seen counts on its publication day, not the day of this run (fetch_feeds, store.backfill_day).
    return {"ok": True, "entries": entries, "status": listing["status"], "error": None, "keyword_gate": True,
            "backfill": True}
