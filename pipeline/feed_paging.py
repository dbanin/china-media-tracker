"""Feed paging: reading back past a saturated feed's first page.

A feed lists only its latest items. The workflow's cron is hourly but GitHub drops scheduled runs: in the
week to 2026-11-09 each feed was polled 41 times, about once every four hours, and a busy feed's first
page covered less than that. Antara's terkini feed (50 items, about 3.6 hours at peak) was saturated on 21
of the 41 polls, with an estimated 2,117 items missed beside 1,440 new; Utusan's ten item WordPress feed
on 25, with 586 missed. The input watch (pipeline/input_watch.py) warned for both.

So when a poll's first page is saturated, the collector reads the feed's next pages until the stream
reaches back to the previous successful poll of the feed, within a small cap. How a feed pages is known
by evidence, never guessed:

  WordPress    a feed whose generator is wordpress.org takes ?paged=2, ?paged=3 (core WordPress), so it is
               paged without a record (form "auto", AUTO_MAX_PAGES pages).
  recorded     sources/feed_paging.yaml records the other forms, each checked live: the feed's own next
               pages in the same format (type rss, NSTP's Drupal feeds count ?paged= from 1), or the
               outlet's numbered HTML listing of its latest items (type page, read by
               section_pages.listing_entries with the listing's link pattern) where the feed takes no page
               parameter (Antara's /terkini/N). An entry with pages: null turns WordPress paging off.

Every page is checked again as it is read, so a site that changes is caught the same day: the page must
parse and list entries, must not repeat what was read already (REPEAT_SHARE of its links or more: a site
that ignores the parameter serves page 1 again), and a dated page must be older than the page before it
(its median entry) with no gap between them wider than GAP_SPACINGS times the spacing between items of
everything read so far, or GAP_FLOOR_HOURS when that is longer: Adnkronos answers ?page=2 with twelve day
old items. The floor is wide because a quiet night is a real gap (Village Voice News, ten items a page,
had one of 14.6 hours, Sunday night in Guyana, between its third and fourth pages on 2026-11-09); a form
counted from the wrong number, which skips a page of a busy feed, is caught when the form is checked and
recorded. A listing's first page must share a link with what the feed
gave, which is the proof the two are one stream; its later pages follow it. Reading stops when the stream
reaches back to the previous poll (an entry dated at or before it, the feed's clock offset applied, or for
an undated listing a link on record before the run), at the page cap, at a failed or refused request
(robots.txt is checked for every page URL, query string included), when the feed's paging has taken
FEED_MAX_SECONDS, when the host's Crawl-delay is longer than MAX_GAP_SECONDS, within
DEADLINE_MARGIN_SECONDS of the stage deadline, or when the run's RUN_MAX_REQUESTS page requests are
spent. Every request goes through the same helpers as the first page: fetch_feed or get_page, the
robots.txt check, the per domain rate limiter and Crawl-delay, the crawler's own user agent.

The pages read are one poll of the feed: their items are inserted like the first page's, and the
saturation judgement and the estimate of items missed (fetch_feeds.saturation) take the whole window read.
"""
import datetime as dt
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PAGING_PATH = Path(__file__).resolve().parent.parent / "sources" / "feed_paging.yaml"
RSS, PAGE, AUTO = "rss", "page", "auto"

# Pages after the first a WordPress feed may be read for in one poll. Ten item pages: Utusan's poll
# missed a median of about 20 items when it was saturated.
AUTO_MAX_PAGES = 5
# Ceiling for a recorded form's max_pages (default AUTO_MAX_PAGES).
MAX_PAGES_LIMIT = 12
# Page requests one discovery run may make in all. On the snapshot of 2026-11-09 a run had 24 to 200
# saturated feeds (median 53) and only some of them page; the cap keeps a run where everything overflowed
# (after an outage) from spending minutes on it.
RUN_MAX_REQUESTS = 300
# Seconds one feed's paging may take, its rate limiter waits included: no page is requested when the pages
# so far, at their average time, would take it past this. Antara's listing took 75 seconds for 12 pages on
# 2026-11-09 (about 6 seconds a page: the 3 second gap and a slow answer).
FEED_MAX_SECONDS = 90.0
# A host asking a longer Crawl-delay is not paged: each page would hold a worker that long.
MAX_GAP_SECONDS = 10.0
# No page is requested this close to the discovery stage's deadline.
DEADLINE_MARGIN_SECONDS = 300.0
# A page whose links were already read in this poll at this share or more repeats what came before.
REPEAT_SHARE = 0.5
# The widest gap allowed between a dated page's newest entry and the previous page's oldest.
GAP_FLOOR_HOURS = 24.0
GAP_SPACINGS = 5.0

_forms_cache: Dict = {"key": None, "forms": {}}
_forms_lock = threading.Lock()


def load_forms(path: Path = PAGING_PATH) -> Dict[str, Dict]:
    """The recorded paging forms by feed URL, read once per version of the file. A malformed entry is
    skipped (the feed keeps its first page), never fatal to discovery."""
    import yaml
    try:
        st = os.stat(str(path))
    except OSError:
        return {}
    key = (str(path), st.st_mtime, st.st_size)
    with _forms_lock:
        if _forms_cache["key"] == key:
            return _forms_cache["forms"]
    with open(str(path), "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or []
    forms = {}
    for f in raw if isinstance(raw, list) else []:
        form = valid_form(f)
        if form is not None:
            forms[form["feed"]] = form
    with _forms_lock:
        _forms_cache.update(key=key, forms=forms)
    return forms


def valid_form(f) -> Optional[Dict]:
    """A recorded form as the reader uses it, or None when it is malformed. pages: null is kept (it turns
    WordPress paging off for the feed)."""
    if not isinstance(f, dict) or not isinstance(f.get("feed"), str) or not f["feed"].startswith("http"):
        return None
    pages = f.get("pages")
    if pages is None:
        return {"feed": f["feed"], "pages": None}
    kind = f.get("type", RSS)
    if not isinstance(pages, str) or "{n}" not in pages or not pages.startswith("http") or kind not in (RSS, PAGE):
        return None
    first = f.get("first", 2)
    max_pages = f.get("max_pages", AUTO_MAX_PAGES)
    if not isinstance(first, int) or first < 0 or not isinstance(max_pages, int) or max_pages < 1:
        return None
    form = {"feed": f["feed"], "pages": pages, "type": kind, "first": first,
            "max_pages": min(max_pages, MAX_PAGES_LIMIT), "page1": f.get("page1"), "source": "recorded"}
    if kind == PAGE:
        try:
            re.compile(f.get("links") or "")
        except (re.error, TypeError):
            return None
        if not f.get("links"):
            return None
        form["links"] = f["links"]
    return form


def wordpress(result: Dict) -> bool:
    """Whether a feed's own generator element names WordPress (https://wordpress.org/?v=6.9)."""
    feed = (result or {}).get("feed") or {}
    gen = feed.get("generator") if hasattr(feed, "get") else None
    detail = feed.get("generator_detail") if hasattr(feed, "get") else None
    text = " ".join(str(x) for x in (gen, (detail or {}).get("href") if hasattr(detail, "get") else None) if x)
    return "wordpress.org" in text.lower()


def form_for(feed_url: str, result: Dict, forms: Optional[Dict[str, Dict]] = None) -> Optional[Dict]:
    """How the feed pages: its recorded form, the WordPress form when its generator says WordPress, or
    None (no paging)."""
    forms = load_forms() if forms is None else forms
    if feed_url in forms:
        f = forms[feed_url]
        return f if f.get("pages") else None
    if wordpress(result):
        return {"feed": feed_url, "pages": with_query(feed_url, "paged", "{n}"), "type": RSS, "first": 2,
                "max_pages": AUTO_MAX_PAGES, "page1": None, "source": AUTO}
    return None


def with_query(url: str, name: str, value: str) -> str:
    """url with the query parameter name set to value, other parameters kept in order."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != name]
    query.append((name, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, safe="{}"), parts.fragment))


def page_url(form: Dict, n: int) -> str:
    if n == 1 and form.get("page1"):
        return form["page1"]
    return form["pages"].replace("{n}", str(n))


class Budget:
    """The run's page requests, shared by every worker."""

    def __init__(self, limit: int = RUN_MAX_REQUESTS):
        self.limit, self.used = limit, 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            return True


def _stamp(entry, offset_hours: float = 0.0) -> Optional[dt.datetime]:
    from pipeline.fetch_feeds import _entry_time
    t = _entry_time(entry)
    if not t:
        return None
    d = dt.datetime.fromisoformat(t)
    return d - dt.timedelta(hours=offset_hours) if offset_hours else d


def _links(entries: Iterable) -> List[str]:
    out = []
    for e in entries:
        link = e.get("link") if hasattr(e, "get") else None
        if isinstance(link, str) and link.strip().startswith("http"):
            out.append(link.strip())
    return out


def reached(entries, since: dt.datetime, offset_hours: float = 0.0, known: Optional[Set[str]] = None) -> bool:
    """Whether these entries reach back to the previous poll: one dated at or before it, or one on record
    before the run (known: url hashes)."""
    if known:
        from pipeline import store
        if any(store.url_hash(u) in known for u in _links(entries)):
            return True
    stamps = [s for s in (_stamp(e, offset_hours) for e in entries) if s is not None]
    return bool(stamps) and min(stamps) <= since


def _median(stamps: List[dt.datetime]) -> dt.datetime:
    s = sorted(stamps)
    return s[len(s) // 2]


def _not_following(page: List, before: List, offset_hours: float, window: Optional[List] = None) -> Optional[str]:
    """Why a dated page does not follow the dated page before it, or None when it does. window is
    everything read so far, for the spacing between items (the page before it when not given)."""
    a = [s for s in (_stamp(e, offset_hours) for e in before) if s is not None]
    b = [s for s in (_stamp(e, offset_hours) for e in page) if s is not None]
    if not a or not b:
        return None
    if _median(b) >= _median(a):
        return "not_older"
    w = [s for s in (_stamp(e, offset_hours) for e in (window or before)) if s is not None]
    spacing = (max(w) - min(w)).total_seconds() / (len(w) - 1) if len(w) >= 2 else 0.0
    allowed = max(GAP_FLOOR_HOURS * 3600, GAP_SPACINGS * spacing)
    if (min(a) - max(b)).total_seconds() > allowed:
        return "gap"
    return None


def read_back(feed_url: str, result: Dict, since: Optional[dt.datetime], form: Optional[Dict],
              offset_hours: float = 0.0, known: Optional[Set[str]] = None, deadline: Optional[float] = None,
              budget: Optional[Budget] = None, fetch: Optional[Callable] = None,
              listing: Optional[Callable] = None) -> Dict:
    """The poll's result with the entries of the feed's next pages added (see the module note), or the
    result itself when there is nothing to page: no form, a failed first page, no previous poll, or a
    first page that already reaches back. A paged result carries first_entries (the first page's count)
    and paging: {source, pages, added, reached, stop, undated}."""
    if not form or not result or not result.get("ok") or since is None:
        return result
    page1 = list(result.get("entries") or [])
    if not page1 or reached(page1, since, offset_hours, known):
        return result
    dated = any(_stamp(e, offset_hours) is not None for e in page1)
    if not dated and not known:
        return result            # nothing to tell whether it reached back
    if fetch is None:
        from pipeline.feeds_util import fetch_feed as fetch
    if listing is None:
        from pipeline.section_pages import listing_entries as listing
    from pipeline import config, fetch_articles
    from pipeline import store
    started = time.time()
    key = store.url_hash          # links compared as the database compares them (tracking parameters, www.)
    seen = {key(u) for u in _links(page1)}
    combined, previous = page1, page1
    info = {"source": form.get("source", "recorded"), "pages": 0, "added": 0, "reached": False, "stop": "cap",
            "undated": 0}
    n, first_listing = form["first"], form["type"] == PAGE
    while info["pages"] < form["max_pages"]:
        url = page_url(form, n)
        if deadline is not None and time.time() > deadline - DEADLINE_MARGIN_SECONDS:
            info["stop"] = "deadline"
            break
        spent = time.time() - started
        if info["pages"] and spent + spent / info["pages"] > FEED_MAX_SECONDS:
            info["stop"] = "time"
            break
        allowed, why = fetch_articles.can_fetch(url)
        if not allowed:
            info["stop"] = why
            break
        if fetch_articles.domain_gap(fetch_articles.domain_of(url)) > max(MAX_GAP_SECONDS, config.MIN_SECONDS_PER_DOMAIN):
            info["stop"] = "slow_host"
            break
        if budget is not None and not budget.take():
            info["stop"] = "run_budget"
            break
        r = listing(url, form["links"]) if form["type"] == PAGE else fetch(url)
        info["pages"] += 1
        if not r or not r.get("ok") or not r.get("entries"):
            info["stop"] = "error:%s" % ((r or {}).get("error") or "no_entries")
            break
        entries = [e for e in r["entries"] if _links([e])]
        links = _links(entries)
        repeat = sum(1 for u in links if key(u) in seen)
        if first_listing:
            if not repeat:
                info["stop"] = "no_overlap"       # the listing is not shown to continue the feed
                break
            first_listing = False
        elif not links or repeat >= REPEAT_SHARE * len(links):
            info["stop"] = "repeat"
            break
        else:
            why = _not_following(entries, previous, offset_hours, combined)
            if why:
                info["stop"] = why
                break
        fresh = []
        for e, u in zip(entries, links):
            if key(u) not in seen:
                seen.add(key(u))
                fresh.append(e)
        combined = combined + fresh
        info["added"] += len(fresh)
        info["undated"] += sum(1 for e in fresh if _stamp(e) is None)
        previous = entries
        if reached(entries, since, offset_hours, known):
            info["reached"], info["stop"] = True, "reached"
            break
        n += 1
    out = dict(result)
    out["entries"], out["first_entries"], out["paging"] = combined, len(page1), info
    return out


def tally(counts: Dict, info: Optional[Dict]) -> None:
    """Add one feed's paging to the stage counts (feed_paging: feeds, pages, items, reached, stops)."""
    if not info:
        return
    # A new dict each time: the collector restores a copy of its counts when a feed fails, and a shared
    # nested dict would keep the failed feed's paging.
    old = counts.get("feed_paging") or {"feeds": 0, "pages": 0, "items": 0, "reached": 0, "stops": {}}
    t = dict(old, stops=dict(old["stops"]))
    counts["feed_paging"] = t
    t["feeds"] += 1
    t["pages"] += info["pages"]
    t["items"] += info["added"]
    t["reached"] += 1 if info["reached"] else 0
    stop = info["stop"].split(":")[0] if info["stop"].startswith("error:") else info["stop"]
    t["stops"][stop] = t["stops"].get(stop, 0) + 1


# Coverage by the outlet's other feeds. A saturated poll of an editorial feed lost the items published
# between the previous poll and its window's oldest entry. Another feed of the same outlet read in the same
# run whose window reaches back past that previous poll (a section feed with a longer window, the outlet's
# sitemap) listed those of the lost items that belong to its own part of the stream, and the collector
# took them. So the loss is scaled as news_sitemap.covered_saturation scales it for a sitemap: by the share
# of the saturated poll's links that none of the covering feeds carries. Only feeds that reach back count,
# each by its own dated entries (its clock offset applied); an undated feed or one that does not reach
# back covers nothing, so the scaling never claims items the collector did not take.
def sources(group: List[Dict], offsets: Optional[Dict[str, float]] = None) -> List[Dict]:
    """Each successful feed read of one outlet's poll as {feed, oldest, hashes}; oldest is None when the
    read has no dated entry."""
    from pipeline import store
    offsets = offsets or {}
    out = []
    for g in group:
        r = g.get("result")
        if g.get("deferred") or not r or not r.get("ok"):
            continue
        try:
            # A malformed read (a string for an entry, a number for a date) covers nothing; the collector
            # records it as a failed poll of its own feed.
            entries = [e for e in (r.get("entries") or []) if hasattr(e, "get")]
            if g.get("type") == "news_sitemap":
                stamps = [dt.datetime.fromisoformat(e["published"]) for e in entries if e.get("published")]
            else:
                off = offsets.get(g["feed_url"], 0.0)
                stamps = [s for s in (_stamp(e, off) for e in entries) if s is not None]
            stamps = [s if s.tzinfo else s.replace(tzinfo=dt.timezone.utc) for s in stamps]
            out.append({"feed": g["feed_url"], "oldest": min(stamps) if stamps else None,
                        "hashes": {store.url_hash(u) for u in _links(entries)}})
        except Exception:  # noqa: BLE001
            continue
    return out


def cover_for(feed_url: str, last_ok: Optional[dt.datetime], srcs: List[Dict]) -> Optional[Dict]:
    """The cover (news_sitemap.cover's shape) of the outlet's other feeds that reach back to last_ok, or
    None when none does."""
    if last_ok is None:
        return None
    reach = [s for s in srcs if s["feed"] != feed_url and s["oldest"] is not None and s["oldest"] <= last_ok]
    if not reach:
        return None
    hashes: Set[str] = set()
    for s in reach:
        hashes |= s["hashes"]
    return {"oldest": max(s["oldest"] for s in reach), "hashes": hashes}
