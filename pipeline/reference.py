"""Reference sources: editions of Chinese state outlets read as originals (sources/reference.yaml).

The research measures compare state copy in monitored outlets against the text it came from, so the
tracker reads the English editions of Xinhua, CGTN, Global Times, People's Daily, China Daily and ECNS.
They are originals only. A reference source is never an outlet: it has no country, its id is always
ref_*, its items are never classified, never counted, never in a denominator and never in the export's
figures, and China stays unmonitored. Everything here writes reference_items and
reference_feed_health, and the body files, and nothing else: no articles row, no discovery count, no
feed health or feed poll of a monitored outlet, no seen_urls entry. There is no relevance gate, since
every item is state text.

Polling (poll). Each active source of this collector (config.COLLECTOR) is read on its listings:
RSS feeds and index pages through fetch_feeds.poll_outlet (so robots.txt, the per domain rate limiter
and the user agent are the collector's own), Google News sitemaps through news_sitemap.get_xml, read
here so the raw publication date survives, and Xinhua's static list pages through list_entries. Each
listing's entry times are normalised by its time_rule (the notes in sources/reference.yaml give the
evidence for each). A new item is stored as discovered; an item seen again gains a time of day when it
had only a date. Feed health goes to reference_feed_health (store.record_reference_feed_health).

Times. A stored published_at is UTC ISO with a time of day, or a bare YYYY-MM-DD when only the date is
known (lookalike.published_ts and tie_rank read that as day resolution). A date read from a URL is a
Beijing date; the Global Times sitemap date is its UTC date. Either is replaced at fetch by the page's
own time when the source has a page_time rule and the page time is within a day of the date.

Fetching (fetch). Up to config.REFERENCE_FETCH_PER_RUN items a run, one worker per host so the hosts
are read side by side, each request under fetch_articles.can_fetch and get_page. Candidates come
first: items whose headline nearly repeats the headline of a current state origin copy that has no
reference original yet (CANDIDATE_TITLE_JACCARD, over copies first seen in the last
CANDIDATE_COPY_DAYS days), taken while they are up to CANDIDATE_MAX_AGE_DAYS old, even once expired.
Then the newest discovered items of the last FETCH_MAX_AGE_DAYS days. The fetch stage has a slice of
its own (FETCH_SLICE_SECONDS), taken even when the run's article fetch has used the run's budget, so
reference pages are read on every run. extract.extract gives the text; the source's cleaner strips its
template lines (the CGTN language menu and footer, the People's Daily related links); the cleaned text is
stored with store.save_body under body_key(url_hash), a key of its own, so a reference body never
overwrites or deletes the body of a monitored article at the same address; and body_dups.fingerprint
gives the sig and shingle count kept on the row. A paywalled page is marked paywalled and never retried
(no body is kept: a teaser is not an original). A refusal by robots.txt is final; a transient failure
leaves the item to a later run until it is FETCH_MAX_AGE_DAYS old, after which it is marked expired
(a candidate is not expired while it is a candidate).

Status (source_status). Per source: items by status (fetched, expired, failed, paywalled, discovered,
blocked by robots.txt), bodies kept, the share of dated items that carry a date only, and that share
among items more than a day old, which should be near zero once fetch has read the page times; a
source where it stays at DATE_ONLY_AFTER_DAY_FLAG or more is flagged. The research measures export
it (pipeline/copies.py, pipeline/speed.py).

Time checks (time_checks). An item listed by two listings of one source with different time rules
(CGTN's RSS and its latest news sitemap) is a check on the rules: the time each listing gives is
recorded in reference_time_checks, and a source where the second listing reads more than an hour
earlier (or later) than the first for TIME_CHECK_FLAG of its items is flagged. CGTN's sitemap writes
Beijing time with a "Z"; if CGTN starts writing true UTC, the sitemap times read 8 hours early and
this check says so.

Retention (prune_bodies). The body of an item no state copy was paired with (no state_copies row
names it) is deleted config.REFERENCE_UNPAIRED_BODY_DAYS after it was fetched, and body_pruned_at is
set. The row and its fingerprint are kept for good.

also_monitored. When a reference item's url_hash is also an articles url_hash (a monitored outlet
carries the same page), the article id is recorded in also_monitored. Nothing else follows from it.

Relay. A source with collector: self_hosted is polled and fetched by the relay machine
(relay.run_relay calls run_stage there, where config.COLLECTOR is self_hosted), and its rows travel in
the bundle as reference_item records with the body inline (bundle_records); ingest writes them into
reference_items only (ingest_record). Every source starts hosted, so the path is wired but idle.

validate() is a report only: it never deactivates or moves a source.

Usage:
  python -m pipeline.reference poll  [--budget-minutes N] [--db PATH] [--bodies-dir DIR] [--source ID]
  python -m pipeline.reference fetch [--budget-minutes N] [--limit N] [--db PATH] [--bodies-dir DIR] [--source ID]
  python -m pipeline.reference validate
  python -m pipeline.reference status
"""
import argparse
import base64
import datetime as dt
import gzip
import html as htmllib
import json
import queue
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import yaml

from pipeline import config, store

REFERENCE_PATH = config.ROOT / "sources" / "reference.yaml"
SCHEMA_PATH = config.ROOT / "sources" / "reference_schema.json"
BJT = dt.timezone(dt.timedelta(hours=8))
UTC = dt.timezone.utc
# The time one stage may take within a pipeline run (run_stage); the run's own deadline may be earlier.
POLL_SECONDS = 150
FETCH_SECONDS = 240
# The fetch stage's own slice: it may take this long even when the run's deadline leaves less (the
# article fetch before it can use the whole budget), so reference pages are read on every run. The
# workflow's job timeout leaves far more room than this.
FETCH_SLICE_SECONDS = 90
# Listing entries older than this are not stored: index pages carry evergreen and topic links.
MAX_ENTRY_AGE_DAYS = 30
# Items still not fetched this many days after discovery are marked expired and left alone.
FETCH_MAX_AGE_DAYS = 3
# Fetch candidates: reference items whose headline nearly repeats that of a current state origin copy
# with no reference original, copies first seen in the last CANDIDATE_COPY_DAYS days; a candidate is
# fetched while it is up to CANDIDATE_MAX_AGE_DAYS old, even once expired (pairing looks up to eleven
# days back, pipeline/copies.py WINDOW_AFTER_DAYS).
CANDIDATE_TITLE_JACCARD = 0.4
CANDIDATE_COPY_DAYS = 14
CANDIDATE_MAX_AGE_DAYS = 10
# Status: an item more than a day old should carry the page's time; a source where at least this share
# of such items (from DATE_ONLY_MIN_ITEMS of them) still has a date only is flagged.
DATE_ONLY_AFTER_DAY_FLAG = 0.25
DATE_ONLY_MIN_ITEMS = 10
# Time checks between two listings of one source: a difference over TIME_CHECK_TOLERANCE seconds is
# earlier or later than expected; a source is flagged when TIME_CHECK_FLAG of at least TIME_CHECK_MIN
# compared items of the last TIME_CHECK_DAYS days differ one way. Rows older than TIME_CHECK_KEEP_DAYS go.
TIME_CHECK_TOLERANCE = 3600
TIME_CHECK_FLAG = 0.2
TIME_CHECK_MIN = 10
TIME_CHECK_DAYS = 7
TIME_CHECK_KEEP_DAYS = 14
# A host that fails this many times with a transient error in one fetch stage is left for the next run.
HOST_TRANSIENT_LIMIT = 3
MIN_BODY_CHARS = 200
MAX_SUMMARY_CHARS = 1000
# A page time is taken only when it lies within this many days of the date already known.
PAGE_TIME_AGREE_DAYS = 1
# How far an item has got, so a relay record never moves a row back.
STATUS_RANK = {"discovered": 0, "expired": 1, "failed": 1, "blocked_robots": 1, "paywalled": 2, "fetched": 3}
RECORD_TYPE = "reference_item"
RELAY_FIELDS = ["url", "url_hash", "source_id", "language", "title", "summary", "author", "published_at",
                "discovered_at", "fetched_at", "status", "fail_reason", "body_hash", "body_chars",
                "extract_version", "shingles", "body_pruned_at", "collector"]


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr)


def _now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


def _iso(d: dt.datetime) -> str:
    return d.astimezone(UTC).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

def validate_sources(sources: List[Dict], schema_path: Path = SCHEMA_PATH) -> None:
    import jsonschema
    with open(schema_path, encoding="utf-8") as fh:
        jsonschema.validate(sources, json.load(fh))
    ids = [s["id"] for s in sources]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError("duplicate reference ids: %s" % ", ".join(dupes))
    for s in sources:
        if not s.get("feeds") and not s.get("news_sitemaps"):
            raise ValueError("reference source %s has no feed and no news sitemap" % s["id"])
        for f in list(s.get("feeds") or []) + list(s.get("news_sitemaps") or []):
            if f.get("link_pattern"):
                re.compile(f["link_pattern"])


def load_sources(path: Path = REFERENCE_PATH, validate: bool = True) -> List[Dict]:
    """sources/reference.yaml as a list of sources, validated against reference_schema.json."""
    p = Path(path)
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as fh:
        sources = yaml.safe_load(fh) or []
    if validate:
        validate_sources(sources)
    for s in sources:
        s.setdefault("news_sitemaps", [])
        s.setdefault("page_time", "none")
        s.setdefault("cleaner", "none")
    return sources


def collectable(sources: List[Dict], collector: Optional[str] = None) -> List[Dict]:
    """The active sources this collector polls and fetches."""
    c = collector or config.COLLECTOR
    return [s for s in sources if s.get("active") and s.get("collector", "hosted") == c]


# ---------------------------------------------------------------------------
# Addresses and bodies
# ---------------------------------------------------------------------------

def clean_url(url: str) -> str:
    """The address without its fragment and tracking parameters (CGTN's RSS adds UTM_Source and so on).
    Unlike store.canonical_url it keeps the host and scheme as written, so the page is fetched as listed."""
    u = (url or "").strip()
    try:
        parts = urlsplit(u)
    except ValueError:
        return u
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def body_key(url_hash: str) -> str:
    """Where a reference item's body is stored (store.save_body / load_body / body_path). "r" is not a
    hex digit, so the key never equals an article's url_hash: a reference body never overwrites, and
    its retention never deletes, the body of a monitored article at the same address."""
    return "r" + url_hash


def load_body(row_or_hash) -> Optional[str]:
    """The stored (cleaned) body of a reference item, given its row or its url_hash; None once pruned."""
    h = row_or_hash if isinstance(row_or_hash, str) else row_or_hash["url_hash"]
    return store.load_body(body_key(h))


# ---------------------------------------------------------------------------
# Times
# ---------------------------------------------------------------------------

_STAMP = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?\s*"
                    r"(Z|z|[+-]\d{2}:?\d{2})?\s*$")
_URL_DATES = (
    re.compile(r"/(20\d{2})(\d{2})(\d{2})/[0-9a-f]{32}/"),           # Xinhua /20261013/<hash>/c.html
    re.compile(r"/n3/(20\d{2})/(\d{2})(\d{2})/"),                     # People's Daily /n3/2026/1013/
    re.compile(r"/a/(20\d{2})(\d{2})/(\d{2})/"),                      # China Daily /a/202610/13/
    re.compile(r"/(20\d{2})-(\d{2})-(\d{2})/"),                       # CGTN /news/2026-10-13/, ECNS /2026-10-13/
)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"], 1)}


def parse_stamp(text: Optional[str]) -> Optional[Tuple[dt.datetime, Optional[str], bool]]:
    """(naive datetime as written, zone text or None, has a time of day) for an ISO or W3C style stamp
    ("2026-10-13 18:10:06", "2026-10-14T01:27Z", "2026-10-13T04:30:03+08:00", "2026-10-13"), else None."""
    m = _STAMP.match(text or "")
    if not m:
        return None
    y, mo, d, hh, mi, ss, zone = m.groups()
    try:
        naive = dt.datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0))
    except ValueError:
        return None
    return naive, zone, hh is not None


def _with_zone(naive: dt.datetime, zone: Optional[str]) -> dt.datetime:
    if zone in (None, "Z", "z"):
        return naive.replace(tzinfo=UTC)
    z = zone.replace(":", "")
    sign = 1 if z[0] == "+" else -1
    return naive.replace(tzinfo=dt.timezone(sign * dt.timedelta(hours=int(z[1:3]), minutes=int(z[3:5]))))


def url_date(link: Optional[str]) -> Optional[str]:
    """The date an article URL carries (a Beijing date at these sites), as YYYY-MM-DD, or None."""
    for pat in _URL_DATES:
        m = pat.search(link or "")
        if m:
            try:
                return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                return None
    return None


def normalise_time(raw: Optional[str], rule: str, parsed=None, link: Optional[str] = None) -> Optional[str]:
    """A listing entry's publication time under its feed's time_rule (sources/reference.yaml): UTC ISO
    with a time of day, a bare YYYY-MM-DD when only the date is known, or None.
      as_published    the parsed time as written (feedparser's published_parsed, or the stamp's own zone)
      bjt_if_unzoned  no zone or "Z": Beijing time; an explicit other offset is trusted
      bjt             Beijing time, whatever zone is written
      date_only       the date as written, without its time
      url_date        the date in the article URL"""
    if rule == "url_date":
        return url_date(link)
    st = parse_stamp(raw)
    if rule == "date_only":
        if st is not None:
            return st[0].date().isoformat()
        return url_date(link)
    if st is None:
        if parsed and rule in ("as_published", "bjt_if_unzoned"):
            try:
                return _iso(dt.datetime(*parsed[:6], tzinfo=UTC))
            except (TypeError, ValueError):
                return None
        return url_date(link)
    naive, zone, timed = st
    if not timed:
        return naive.date().isoformat()
    if rule == "as_published":
        if parsed:
            try:
                return _iso(dt.datetime(*parsed[:6], tzinfo=UTC))
            except (TypeError, ValueError):
                pass
        return _iso(_with_zone(naive, zone))
    if rule == "bjt_if_unzoned":
        return _iso(naive.replace(tzinfo=BJT) if zone in (None, "Z", "z") else _with_zone(naive, zone))
    if rule == "bjt":
        return _iso(naive.replace(tzinfo=BJT))
    raise ValueError("unknown time_rule %r" % rule)


# The zone of the calendar dates each time_rule stores when a value has no time of day, in minutes east
# of UTC: a URL date, and a Beijing stamp without its time, is a Beijing date; date_only keeps the date
# the listing wrote, which for the Global Times sitemap (its only user) is the UTC date (see its
# evidence in sources/reference.yaml); an RSS date as published is read as UTC.
RULE_DATE_ZONES = {"url_date": 480, "bjt": 480, "bjt_if_unzoned": 480, "date_only": 0, "as_published": 0}
BJT_MINUTES = 480


def date_zone_minutes(src: Dict) -> Optional[int]:
    """Minutes east of UTC of the dates a source stores without a time of day: the zone of its
    date giving listings (url_date and date_only) when they agree, else of all its listings when
    they agree, else None (pipeline/speed.py then takes a Beijing date)."""
    rules = [f.get("time_rule") for f in _listings(src)]
    for pick in ([r for r in rules if r in ("url_date", "date_only")], rules):
        zones = {RULE_DATE_ZONES[r] for r in pick if r in RULE_DATE_ZONES}
        if len(zones) == 1:
            return zones.pop()
        if zones:
            return None
    return None


def date_zones(sources: Optional[List[Dict]] = None) -> Dict[str, Optional[int]]:
    """{source id: date_zone_minutes} for every source in the registry (or those given)."""
    srcs = load_sources(validate=False) if sources is None else sources
    return {s["id"]: date_zone_minutes(s) for s in srcs}


_JSONLD_DATE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')
_PUBLISHED_LINE = re.compile(r"Published:\s*([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4}) (\d{1,2}):(\d{2})\s*([AaPp][Mm])")
_BJT_STAMP = re.compile(r"(20\d{2})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})")
_HHMM_MDY = re.compile(r"(\d{1,2}):(\d{2}), ([A-Z][a-z]+) (\d{1,2}), (20\d{2})")


def page_time(html: Optional[str], rule: str) -> Optional[str]:
    """The publication time an article page states, under the source's page_time rule, as UTC ISO."""
    if not html or rule in (None, "none"):
        return None
    try:
        if rule == "jsonld":
            m = _JSONLD_DATE.search(html)
            st = parse_stamp(m.group(1)) if m else None
            if st is None or not st[2]:
                return None
            return _iso(st[0].replace(tzinfo=BJT) if st[1] is None else _with_zone(st[0], st[1]))
        if rule == "published_line":
            m = _PUBLISHED_LINE.search(html)
            if not m:
                return None
            mon, day, year, hh, mi, ampm = m.groups()
            h = int(hh) % 12 + (12 if ampm.lower() == "pm" else 0)
            return _iso(dt.datetime(int(year), _MONTHS[mon.lower()], int(day), h, int(mi), tzinfo=BJT))
        if rule == "bjt_stamp":
            m = _BJT_STAMP.search(html)
            return _iso(dt.datetime(*map(int, m.groups()), tzinfo=BJT)) if m else None
        if rule == "hhmm_month_day_year":
            m = _HHMM_MDY.search(html)
            if not m:
                return None
            hh, mi, mon, day, year = m.groups()
            month = _MONTHS.get(mon[:3].lower())
            return _iso(dt.datetime(int(year), month, int(day), int(hh), int(mi), tzinfo=BJT)) if month else None
    except (ValueError, KeyError):
        return None
    raise ValueError("unknown page_time rule %r" % rule)


def _timed(published: Optional[str]) -> bool:
    return bool(published) and "T" in published


def _day(published: Optional[str]) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat((published or "")[:10])
    except ValueError:
        return None


def _better_time(current: Optional[str], new: Optional[str]) -> Optional[str]:
    """new when it adds a time of day (or any time) to what is stored; otherwise None."""
    if not new:
        return None
    if not current:
        return new
    if not _timed(current) and _timed(new):
        return new
    return None


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

_XINHUA_ITEM = re.compile(
    r"""<a\s[^>]*?href=["']([^"']*?/(\d{8})/[0-9a-f]{32}/c\.html)["'][^>]*>(.*?)</a>\s*"""
    r"""<span\s+class=["']time["']\s*>\s*([^<]+?)\s*</span>""", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")


def list_entries(html: str, list_url: str) -> List[Dict]:
    """The timed items of a Xinhua English list page: <a href="../YYYYMMDD/<32 hex>/c.html">title</a>
    followed by <span class="time">YYYY-MM-DD HH:MM:SS</span> (Beijing time, no zone written). Entries in
    the shape of a feedparser item, the raw time in "published"; untimed links are left out."""
    out, seen = [], set()
    for href, _, title, stamp in _XINHUA_ITEM.findall(html or ""):
        url = urljoin(list_url, href.strip())
        text = " ".join(htmllib.unescape(_TAG.sub("", title)).split())
        if url in seen or not text:
            continue
        seen.add(url)
        out.append({"link": url, "title": text[:500], "summary": "", "published": stamp.strip(),
                    "published_parsed": None})
    return out


def fetch_list(url: str) -> Dict:
    """Read one Xinhua list page, as feeds_util.fetch_feed returns a feed."""
    from pipeline import fetch_articles
    started = time.time()
    allowed, why = fetch_articles.can_fetch(url)
    if not allowed:
        return {"ok": False, "entries": [], "status": None, "error": why, "elapsed": time.time() - started}
    html, status, err = fetch_articles.get_page(url)
    if html is None:
        return {"ok": False, "entries": [], "status": status, "error": err, "elapsed": time.time() - started}
    entries = list_entries(html, url)
    return {"ok": bool(entries), "entries": entries, "status": status, "error": None if entries else "no_timed_items",
            "elapsed": time.time() - started}


def sitemap_entries(body: bytes) -> Tuple[List[Dict], Optional[str]]:
    """The <url> records of a Google News sitemap urlset, each with its raw news:publication_date
    (news_sitemap.parse converts it to UTC, which is exactly the reading CGTN's and Global Times's
    mislabelled stamps must not get). (entries, error)."""
    from lxml import etree
    from pipeline import news_sitemap
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
    try:
        root = etree.fromstring(body, parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        return [], "parse_error: %s" % type(exc).__name__
    if not isinstance(root.tag, str) or etree.QName(root).localname != "urlset":
        return [], "not_a_urlset"
    sm, news = "{%s}" % news_sitemap.SITEMAP_NS, "{%s}" % news_sitemap.NEWS_NS
    out = []
    for u in root.iter(sm + "url"):
        if len(out) >= news_sitemap.MAX_ENTRIES_PER_FILE:
            break
        loc = (u.findtext(sm + "loc") or "").strip()
        n = u.find(news + "news")
        title = " ".join((n.findtext(news + "title") or "").split()) if n is not None else ""
        if not loc.startswith("http") or not title:
            continue
        raw = (n.findtext(news + "publication_date") or "").strip() or None
        out.append({"link": loc, "title": title[:500], "summary": "", "published": raw, "published_parsed": None})
    return out, None


def fetch_sitemap(url: str) -> Dict:
    from pipeline import news_sitemap
    started = time.time()
    body, status, err = news_sitemap.get_xml(url)
    if body is None:
        return {"ok": False, "entries": [], "status": status, "error": err, "elapsed": time.time() - started}
    entries, perr = sitemap_entries(body)
    if perr or not entries:
        return {"ok": False, "entries": [], "status": status, "error": perr or "no_entries", "elapsed": time.time() - started}
    return {"ok": True, "entries": entries, "status": status, "error": None, "elapsed": time.time() - started}


def _listings(src: Dict) -> List[Dict]:
    """Every listing of a source, as {url, type, time_rule, link_pattern}: Xinhua lists first (they carry
    times), then RSS, then sitemaps, then index pages, so an item listed twice is stored with its time."""
    feeds = [dict(f) for f in src.get("feeds") or []]
    maps = [dict(f, type="news_sitemap") for f in src.get("news_sitemaps") or []]
    order = {"xinhua_list": 0, "rss": 1, "news_sitemap": 2, "page": 3}
    return sorted(feeds + maps, key=lambda f: order.get(f["type"], 9))


def read_listing(src: Dict, feed: Dict) -> Optional[Dict]:
    """One request for one listing. RSS and index pages go through fetch_feeds.poll_outlet with the source
    dressed as a one-feed outlet; None when poll_outlet deferred the feed (a slow host, within a run)."""
    t = feed["type"]
    if t == "xinhua_list":
        return fetch_list(feed["url"])
    if t == "news_sitemap":
        return fetch_sitemap(feed["url"])
    from pipeline import fetch_feeds, registry
    if t == "rss":
        shell = {"id": src["id"], "feeds": [feed["url"]]}
    else:
        shell = {"id": src["id"], "feeds": [], "section_feeds": [{"url": feed["url"], "kind": registry.EDITORIAL,
                                                                  "type": "page"}]}
    got = fetch_feeds.poll_outlet(shell)
    return got[0]["result"] if got else None


def _summary(entry) -> str:
    from pipeline import fetch_feeds
    text = fetch_feeds._entry_summary(entry) or ""
    return " ".join(htmllib.unescape(_TAG.sub(" ", text)).split())[:MAX_SUMMARY_CHARS]


def entries_to_items(src: Dict, feed: Dict, entries: Iterable, now: Optional[dt.datetime] = None) -> List[Dict]:
    """A listing's entries as reference items: address cleaned, link_pattern applied, time normalised,
    entries older than MAX_ENTRY_AGE_DAYS left out."""
    from pipeline import fetch_feeds
    now = now or _now()
    oldest = (now - dt.timedelta(days=MAX_ENTRY_AGE_DAYS)).date()
    pat = re.compile(feed["link_pattern"]) if feed.get("link_pattern") else None
    out = []
    for e in entries:
        link = e.get("link")
        if not link or not str(link).startswith("http"):
            continue
        url = clean_url(link)
        if pat and not pat.search(url):
            continue
        raw = e.get("published") or e.get("updated")
        parsed = e.get("published_parsed") or e.get("updated_parsed")
        published = normalise_time(raw, feed["time_rule"], parsed, url)
        day = _day(published)
        if day is not None and day < oldest:
            continue
        title = " ".join(htmllib.unescape(_TAG.sub(" ", e.get("title") or "")).split())[:500] or None
        out.append({"url": url, "title": title, "summary": _summary(e) or None,
                    "author": fetch_feeds._entry_author(e), "published_at": published})
    return out


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------

def article_id_for(conn, url_hash: str) -> Optional[str]:
    """The id of the monitored article at the same address, read only, as text, or None."""
    row = conn.execute("SELECT id FROM articles WHERE url_hash=?", (url_hash,)).fetchone()
    return str(row[0]) if row else None


def store_item(conn, src: Dict, item: Dict, now: Optional[str] = None) -> Optional[str]:
    """Insert a listing item into reference_items, or improve the row it already has. Returns "new",
    "upgraded" or None. Writes reference_items only."""
    h = store.url_hash(item["url"])
    row = conn.execute("SELECT id, published_at, title, summary, author FROM reference_items WHERE url_hash=?",
                       (h,)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO reference_items(source_id, url, url_hash, language, title, summary, author, published_at,
                   discovered_at, status, collector, also_monitored) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (src["id"], item["url"], h, src.get("language"), item.get("title"), item.get("summary"),
             item.get("author"), item.get("published_at"), now or store.utcnow(), "discovered", config.COLLECTOR,
             article_id_for(conn, h)))
        return "new"
    fields = {}
    better = _better_time(row["published_at"], item.get("published_at"))
    if better:
        fields["published_at"] = better
    for k in ("title", "summary", "author"):
        if not row[k] and item.get(k):
            fields[k] = item[k]
    if not fields:
        return None
    conn.execute("UPDATE reference_items SET %s WHERE id=?" % ", ".join("%s=?" % k for k in fields),
                 list(fields.values()) + [row["id"]])
    return "upgraded" if "published_at" in fields else None


def refresh_also_monitored(conn, days: int = 14) -> int:
    """Record, on recent reference items, the monitored article at the same address (read only)."""
    since = (_now() - dt.timedelta(days=days)).isoformat()
    cur = conn.execute(
        """UPDATE reference_items SET also_monitored =
               (SELECT CAST(a.id AS TEXT) FROM articles a WHERE a.url_hash = reference_items.url_hash)
           WHERE also_monitored IS NULL AND discovered_at >= ?
             AND EXISTS (SELECT 1 FROM articles a WHERE a.url_hash = reference_items.url_hash)""", (since,))
    return cur.rowcount


# ---------------------------------------------------------------------------
# Poll
# ---------------------------------------------------------------------------

def _poll_source(src: Dict, deadline: Optional[float]) -> List[Tuple[Dict, Optional[Dict]]]:
    """Read every listing of one source in turn (network only, no database), until the deadline."""
    out = []
    for feed in _listings(src):
        if deadline is not None and time.monotonic() >= deadline:
            out.append((feed, None))
            continue
        try:
            out.append((feed, read_listing(src, feed)))
        except Exception as exc:  # noqa: BLE001  one listing must not take the source down
            out.append((feed, {"ok": False, "entries": [], "status": None,
                               "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}))
    return out


def poll(conn, deadline: Optional[float] = None, sources: Optional[List[Dict]] = None,
         now: Optional[dt.datetime] = None) -> Dict:
    """Poll this collector's reference sources, one thread per source (the per domain rate limiter keeps
    each host polite), and store their items. deadline is a time.monotonic() value: a listing not
    started by then is skipped and read on the next run."""
    srcs = collectable(load_sources()) if sources is None else sources
    counts = {"sources": len(srcs), "feeds": 0, "feeds_ok": 0, "feeds_skipped": 0, "entries": 0, "new": 0,
              "upgraded": 0}
    if not srcs:
        return counts
    now = now or _now()
    stamp = _iso(now)
    pool = ThreadPoolExecutor(max_workers=min(8, len(srcs)))
    futures = {pool.submit(_poll_source, s, deadline): s for s in srcs}
    try:
        for fut, src in futures.items():
            try:
                group = fut.result()
            except Exception as exc:  # noqa: BLE001
                counts["source_errors"] = counts.get("source_errors", 0) + 1
                _warn("reference poll of %s failed: %s: %s" % (src["id"], type(exc).__name__, exc))
                continue
            per = counts.setdefault("by_source", {}).setdefault(src["id"], {"new": 0, "entries": 0, "feeds_ok": 0})
            first_times: Dict[str, Tuple[Dict, str]] = {}
            checks: List[Tuple] = []
            for feed, r in group:
                if r is None:
                    counts["feeds_skipped"] += 1
                    continue
                counts["feeds"] += 1
                store.record_reference_feed_health(conn, src["id"], feed["url"], bool(r.get("ok")), r.get("error"),
                                                   len(r.get("entries") or []))
                if not r.get("ok"):
                    continue
                counts["feeds_ok"] += 1
                per["feeds_ok"] += 1
                for item in entries_to_items(src, feed, r["entries"], now):
                    counts["entries"] += 1
                    per["entries"] += 1
                    check = _cross_check(first_times, feed, item)
                    if check:
                        checks.append(check)
                    done = store_item(conn, src, item, stamp)
                    if done == "new":
                        counts["new"] += 1
                        per["new"] += 1
                    elif done == "upgraded":
                        counts["upgraded"] += 1
            if checks:
                counts["time_checks"] = counts.get("time_checks", 0) + record_time_checks(conn, src["id"], checks, stamp)
            conn.commit()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    counts["also_monitored"] = refresh_also_monitored(conn)
    conn.commit()
    return counts


# ---------------------------------------------------------------------------
# Time checks between listings
# ---------------------------------------------------------------------------

_TIME_CHECKS_SQL = """CREATE TABLE IF NOT EXISTS reference_time_checks (
    source_id TEXT NOT NULL,
    url_hash TEXT NOT NULL,
    first_listing TEXT NOT NULL,
    first_rule TEXT NOT NULL,
    first_time TEXT NOT NULL,
    other_listing TEXT NOT NULL,
    other_rule TEXT NOT NULL,
    other_time TEXT NOT NULL,
    diff_seconds INTEGER NOT NULL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (source_id, url_hash, other_listing)
)"""


def _ts(iso: Optional[str]) -> Optional[float]:
    try:
        return dt.datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _cross_check(first_times: Dict, feed: Dict, item: Dict) -> Optional[Tuple]:
    """An item's time from this listing against the time the first listing that gave it a time of day
    gave, when the two listings read times by different rules (CGTN's RSS and sitemap); None otherwise.
    Records the first timed sighting in first_times."""
    t = item.get("published_at")
    if not _timed(t):
        return None
    url = item["url"]
    seen = first_times.get(url)
    if seen is None:
        first_times[url] = (feed, t)
        return None
    f0, t0 = seen
    if f0["url"] == feed["url"] or f0.get("time_rule") == feed.get("time_rule"):
        return None
    a, b = _ts(t0), _ts(t)
    if a is None or b is None:
        return None
    return (store.url_hash(url), f0["url"], f0.get("time_rule"), t0, feed["url"], feed.get("time_rule"), t, int(round(b - a)))


def record_time_checks(conn, source_id: str, checks: List[Tuple], stamp: Optional[str] = None) -> int:
    """Store this poll's cross listing time checks of one source in reference_time_checks (a table of
    this module's own, created here) and drop rows older than TIME_CHECK_KEEP_DAYS."""
    conn.execute(_TIME_CHECKS_SQL)
    stamp = stamp or store.utcnow()
    conn.executemany(
        """INSERT OR REPLACE INTO reference_time_checks(source_id, url_hash, first_listing, first_rule, first_time,
               other_listing, other_rule, other_time, diff_seconds, checked_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [(source_id,) + tuple(c) + (stamp,) for c in checks])
    base = _ts(stamp)
    ref_time = dt.datetime.fromtimestamp(base, UTC) if base is not None else _now()
    old = _iso(ref_time - dt.timedelta(days=TIME_CHECK_KEEP_DAYS))
    conn.execute("DELETE FROM reference_time_checks WHERE checked_at < ?", (old,))
    return len(checks)


def time_checks(conn, now: Optional[dt.datetime] = None, days: int = TIME_CHECK_DAYS) -> Dict[str, Dict]:
    """Per source with cross listing checks in the last `days` days: compared, how many read earlier
    or later than the first listing by more than TIME_CHECK_TOLERANCE, the shares, the median
    difference in hours (other minus first), and flags: "earlier than expected" (the CGTN sitemap
    reading 8 h early is what a switch to true UTC looks like) or "later than expected"."""
    try:
        rows = conn.execute("SELECT source_id, diff_seconds FROM reference_time_checks WHERE checked_at >= ?",
                            (_iso((now or _now()) - dt.timedelta(days=days)),)).fetchall()
    except Exception:  # noqa: BLE001  no table yet: no check has run
        return {}
    by: Dict[str, List[int]] = {}
    for r in rows:
        by.setdefault(r[0], []).append(int(r[1]))
    out = {}
    for sid, diffs in sorted(by.items()):
        n = len(diffs)
        early = sum(1 for d in diffs if d < -TIME_CHECK_TOLERANCE)
        late = sum(1 for d in diffs if d > TIME_CHECK_TOLERANCE)
        ds = sorted(diffs)
        mid = ds[n // 2] if n % 2 else (ds[n // 2 - 1] + ds[n // 2]) / 2.0
        flags = []
        if n >= TIME_CHECK_MIN and early / float(n) >= TIME_CHECK_FLAG:
            flags.append("earlier than expected")
        if n >= TIME_CHECK_MIN and late / float(n) >= TIME_CHECK_FLAG:
            flags.append("later than expected")
        out[sid] = {"compared": n, "earlier": early, "later": late, "share_earlier": round(early / float(n), 3),
                    "share_later": round(late / float(n), 3), "median_diff_hours": round(mid / 3600.0, 2), "flags": flags}
    return out


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

_CGTN_TIME_LINE = re.compile(r"^\d{1,2}:\d{2}, \d{1,2}-[A-Z][a-z]{2}-\d{4}$")
_CGTN_TAIL = {"search trends", "choose your language", "download our app"}
_CGTN_DROP = {"breaking news", "subscribe", "get timely updates on major events from around the globe with cgtn breaking news."}
_CGTN_DURATION = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_CGTN_LABELS = re.compile(r"^[A-Za-z&' ]+(;[A-Za-z&' ]+)+$")
_CD_DROP = {"share - wechat", "- search", "photo", "top"}
# The CGTN head: the language menu (each language in English and in its own script) and the cookie
# notice. Its accept line has been "I agree"; other wordings are taken too, and when there is no accept
# line the head ends after the cookie notice, or after the run of short menu lines.
_CGTN_ACCEPT = {"i agree", "agree", "i accept", "accept", "accept all", "accept cookies", "got it", "ok", "okay"}
_CGTN_MENU_HEAD = "choose your language"
_CD_NAV = re.compile(r"^- [A-Z]+$")


def clean_body(text: Optional[str], cleaner: Optional[str]) -> str:
    """The extracted body without the source's template lines.
      cgtn           the head up to the cookie notice's "I agree" (the "Choose your language" menu, about
                     90 lines of language names; _cgtn_head, which also copes with another accept line
                     or none), the section label and "16:39, 13-Oct-2026" time line
                     after it, the breaking news banner lines, and everything from "Search Trends",
                     "CHOOSE YOUR LANGUAGE", "DOWNLOAD OUR APP" or the "Copyright ... CGTN" line on. The
                     bare "CGTN" credit line and "Source(s): ..." lines are kept.
                     Video pages: the clip duration line ("02:58"), a section label line such as
                     "Asia;China", and the "For more, check out our exclusive content on CGTN Now" line.
      peoples_daily  everything from the "Related Stories" link list on (with the "Photos" line before
                     it) and the "Copyright ... People's Daily Online" line.
      china_daily    everything from the "Most Popular" link list or the "Copyright 1994" footer on, the
                     "Share - WeChat", "Photo" and "Top" lines and the "- HOME" style menu lines.
    Seen on every sample T1a extracted (4 CGTN pages, 3 People's Daily pages) and on the live check of
    2026-10-14 (3 pages a source)."""
    lines = (text or "").split("\n")
    if cleaner == "cgtn":
        lines = _cgtn_head(lines)
        for i, line in enumerate(lines):
            s = line.strip()
            low = s.lower()
            if low in _CGTN_TAIL or (low.startswith(("copyright", "\u00a9")) and "cgtn" in low):
                lines = lines[:i]
                break
        for i, line in enumerate(lines[:4]):
            if _CGTN_TIME_LINE.match(line.strip()):
                head = lines[:i]
                # the section label just before the time line ("Politics", "Asia", "Europe")
                if head and len(head[-1].split()) <= 3 and not head[-1].strip().endswith((".", "?", "!")):
                    head = head[:-1]
                lines = head + lines[i + 1:]
                break
        lines = [line for line in lines if line.strip().lower() not in _CGTN_DROP
                 and not _CGTN_DURATION.match(line.strip()) and not _CGTN_LABELS.match(line.strip())
                 and not line.strip().startswith("For more, check out our exclusive content on CGTN")]
    elif cleaner == "peoples_daily":
        for i, line in enumerate(lines):
            if line.strip() == "Related Stories":
                cut = i - 1 if i > 0 and lines[i - 1].strip() == "Photos" else i
                lines = lines[:cut]
                break
        lines = [line for line in lines
                 if not (line.strip().startswith("Copyright") and "People's Daily" in line)]
    elif cleaner == "china_daily":
        for i, line in enumerate(lines):
            if line.strip() == "Most Popular" or line.strip().startswith("Copyright 1994"):
                lines = lines[:i]
                break
        lines = [line for line in lines if line.strip().lower() not in _CD_DROP and not _CD_NAV.match(line.strip())]
    return "\n".join(lines).strip()


def _cgtn_head(lines: List[str]) -> List[str]:
    """The CGTN body without its head: the "Choose your language" menu (it opens the page, or one of its
    first five lines) through the cookie notice's accept line. Without an accept line, through the
    cookie notice ("cookies" in a line); without either, through the run of short lines (three words
    or fewer) that follows the menu heading, stopping before the section label and time line. Text with
    no menu heading is returned as it is."""
    head = [i for i, line in enumerate(lines[:5]) if line.strip().lower() == _CGTN_MENU_HEAD]
    if not head:
        return lines
    window = lines[:250]
    # The head ends before the first long line that is not the cookie notice: the body has begun.
    for j, line in enumerate(window):
        if len(line.split()) > 12 and "cookie" not in line.lower():
            window = window[:j]
            break
    for i, line in enumerate(window):
        if line.strip().lower() in _CGTN_ACCEPT:
            return lines[i + 1:]
    for i, line in enumerate(window):
        if "cookie" in line.lower():
            return lines[i + 1:]
    i = head[-1] + 1
    while i < len(lines) and len(lines[i].split()) <= 3 and not _CGTN_TIME_LINE.match(lines[i].strip()):
        i += 1
    if i < len(lines) and _CGTN_TIME_LINE.match(lines[i].strip()) and i - 1 > head[-1]:
        i -= 1       # keep the section label before the time line; the time line rule removes both
    return lines[i:]


_extract_lock = threading.Lock()


def _fetch_one(row, src: Dict) -> Dict:
    """Request and extract one reference page (network and extraction only, no database)."""
    from pipeline import extract, fetch_articles
    url = row["url"]
    allowed, why = fetch_articles.can_fetch(url)
    if not allowed:
        return {"row": row, "kind": "transient" if why == "robots_unavailable" else "blocked", "reason": why}
    html, status, err = fetch_articles.get_page(url)
    if html is None:
        transient = bool(err) and err.startswith(fetch_articles.TRANSIENT_FAIL_PREFIXES)
        return {"row": row, "kind": "transient" if transient else "failed", "reason": err, "http_status": status}
    with _extract_lock:
        ex = extract.extract(html, url)
        text = ex.get("text")
        paywalled, pw_reason = extract.detect_paywall(html, text)
        unreadable, un_reason = extract.detect_unreadable(html, text, row["title"])
    return {"row": row, "kind": "page", "html_time": page_time(html, src.get("page_time")), "text": text,
            "paywalled": paywalled, "pw_reason": pw_reason, "unreadable": unreadable, "un_reason": un_reason,
            "author": ex.get("author"), "title": ex.get("title")}


def _host_worker(rows: List, sources: Dict[str, Dict], deadline: Optional[float], out: "queue.Queue") -> None:
    transient = 0
    for row in rows:
        if deadline is not None and time.monotonic() >= deadline:
            break
        if transient >= HOST_TRANSIENT_LIMIT:
            break
        try:
            res = _fetch_one(row, sources[row["source_id"]])
        except Exception as exc:  # noqa: BLE001  one page must not stop the host
            res = {"row": row, "kind": "transient", "reason": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
        if res["kind"] == "transient":
            transient += 1
        out.put(res)


def _record_fetch(conn, res: Dict, src: Dict, counts: Dict) -> None:
    from pipeline import body_dups
    row, now = res["row"], store.utcnow()
    kind = res["kind"]
    if kind == "transient":
        conn.execute("UPDATE reference_items SET fail_reason=? WHERE id=?", (res["reason"], row["id"]))
        counts["transient"] += 1
        return
    if kind in ("blocked", "failed"):
        status = "blocked_robots" if kind == "blocked" else "failed"
        conn.execute("UPDATE reference_items SET status=?, fail_reason=?, fetched_at=? WHERE id=?",
                     (status, res["reason"], now, row["id"]))
        counts[status] = counts.get(status, 0) + 1
        return
    text = res["text"]
    fields = {"fetched_at": now}
    if res["paywalled"]:
        fields.update(status="paywalled", fail_reason=res["pw_reason"])
    elif not text or len(text) < MIN_BODY_CHARS:
        fields.update(status="failed", fail_reason="extraction_empty")
    elif res["unreadable"]:
        fields.update(status="failed", fail_reason="unreadable: %s" % res["un_reason"])
    else:
        body = clean_body(text, src.get("cleaner"))
        if len(body) < MIN_BODY_CHARS:
            fields.update(status="failed", fail_reason="extraction_empty_after_cleaning")
        else:
            n, sig = body_dups.fingerprint(body)
            fields.update(status="fetched", fail_reason=None, body_hash=store.save_body(body_key(row["url_hash"]), body),
                          body_chars=len(body), extract_version=config.EXTRACT_VERSION, sig=sig, shingles=n,
                          body_pruned_at=None)
    # The page's own time, when the listing gave a date only and the page agrees with that date.
    pt = res.get("html_time")
    if pt and not _timed(row["published_at"]):
        known = _day(row["published_at"])
        if known is None or abs((_day(pt) - known).days) <= PAGE_TIME_AGREE_DAYS:
            fields["published_at"] = pt
            counts["page_times"] += 1
    if res.get("author") and not row["author"]:
        fields["author"] = res["author"]
    if res.get("title") and not row["title"]:
        fields["title"] = res["title"]
    conn.execute("UPDATE reference_items SET %s WHERE id=?" % ", ".join("%s=?" % k for k in fields),
                 list(fields.values()) + [row["id"]])
    counts[fields.get("status", "fetched")] = counts.get(fields.get("status", "fetched"), 0) + 1


def fetch_candidates(conn, source_ids: List[str], now: Optional[dt.datetime] = None) -> Dict[int, float]:
    """{reference item id: best headline similarity} for the items not yet fetched (discovered, or
    expired while up to CANDIDATE_MAX_AGE_DAYS old) whose headline nearly repeats (Jaccard of title
    words at least CANDIDATE_TITLE_JACCARD) that of a current state origin copy in the same language,
    first seen in the last CANDIDATE_COPY_DAYS days, with no reference original yet. Read only."""
    from pipeline import lookalike
    if not source_ids:
        return {}
    now = now or _now()
    since = _iso(now - dt.timedelta(days=CANDIDATE_COPY_DAYS))
    try:
        copies = conn.execute(
            """SELECT a.id, a.title, a.language FROM classifications c JOIN articles a ON a.id = c.article_id
               WHERE c.is_current = 1 AND c.category = 'A' AND a.discovered_at >= ?
                 AND NOT EXISTS (SELECT 1 FROM state_copies s WHERE s.copy_id = a.id AND s.current = 1
                                 AND s.original_kind = 'reference')""", (since,)).fetchall()
    except Exception:  # noqa: BLE001  a database without the tables: no candidates
        return {}
    idx: Dict[str, "lookalike._Index"] = {}
    for r in copies:
        ts = lookalike.title_set(r["title"] or "")
        if len(ts) >= lookalike.TITLE_MIN_TOKENS:
            idx.setdefault(r["language"], lookalike._Index()).add(r["id"], ts)
    if not idx:
        return {}
    marks = ",".join("?" * len(source_ids))
    oldest = _iso(now - dt.timedelta(days=CANDIDATE_MAX_AGE_DAYS))
    out = {}
    for r in conn.execute(
            """SELECT id, title, language FROM reference_items
               WHERE source_id IN (%s) AND discovered_at >= ? AND status IN ('discovered', 'expired')""" % marks,
            list(source_ids) + [oldest]):
        ix = idx.get(r["language"])
        ts = lookalike.title_set(r["title"] or "")
        if ix is None or len(ts) < lookalike.TITLE_MIN_TOKENS:
            continue
        best = ix.best_jaccard(ts)
        if best and best[1] >= CANDIDATE_TITLE_JACCARD:
            out[r["id"]] = round(best[1], 4)
    return out


def fetch(conn, deadline: Optional[float] = None, limit: Optional[int] = None, sources: Optional[List[Dict]] = None,
          now: Optional[dt.datetime] = None) -> Dict:
    """Fetch this collector's reference items, up to config.REFERENCE_FETCH_PER_RUN: first the
    candidates for current copies (fetch_candidates, most similar first), then the newest discovered
    items of the last FETCH_MAX_AGE_DAYS days; one worker per host. Then prune the bodies of unpaired
    items (prune_bodies). deadline is a time.monotonic() value."""
    from pipeline import fetch_articles
    srcs = collectable(load_sources()) if sources is None else sources
    counts = {"candidates": 0, "copy_candidates": 0, "fetched": 0, "transient": 0, "page_times": 0, "expired": 0}
    by_id = {s["id"]: s for s in srcs}
    if by_id:
        now = now or _now()
        marks = ",".join("?" * len(by_id))
        wanted = fetch_candidates(conn, list(by_id), now)
        cutoff = _iso(now - dt.timedelta(days=FETCH_MAX_AGE_DAYS))
        # A candidate is not expired while it is one.
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _ref_keep(id INTEGER PRIMARY KEY)")
        conn.execute("DELETE FROM _ref_keep")
        conn.executemany("INSERT OR IGNORE INTO _ref_keep(id) VALUES (?)", [(i,) for i in wanted])
        counts["expired"] = conn.execute(
            "UPDATE reference_items SET status='expired' WHERE status='discovered' AND discovered_at < ? "
            "AND source_id IN (%s) AND id NOT IN (SELECT id FROM _ref_keep)" % marks, [cutoff] + list(by_id)).rowcount
        conn.execute("DROP TABLE _ref_keep")
        conn.commit()
        n = config.REFERENCE_FETCH_PER_RUN if limit is None else limit
        cols = "id, source_id, url, url_hash, title, author, published_at, discovered_at"
        rows = []
        if wanted:
            ids = sorted(wanted, key=lambda i: (-wanted[i], -i))[:n]
            got = {}
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                for r in conn.execute("SELECT %s FROM reference_items WHERE id IN (%s) AND status IN ('discovered', 'expired')"
                                      % (cols, ",".join("?" * len(part))), part):
                    got[r["id"]] = r
            rows = [got[i] for i in ids if i in got]
        counts["copy_candidates"] = len(rows)
        if len(rows) < n:
            have = {r["id"] for r in rows}
            newest = conn.execute(
                """SELECT %s FROM reference_items WHERE status='discovered' AND source_id IN (%s)
                   ORDER BY COALESCE(published_at, discovered_at) DESC, id DESC LIMIT ?""" % (cols, marks),
                list(by_id) + [n]).fetchall()
            rows += [r for r in newest if r["id"] not in have][:n - len(rows)]
        counts["candidates"] = len(rows)
        hosts: Dict[str, List] = {}
        for r in rows:
            hosts.setdefault(fetch_articles.domain_of(r["url"]), []).append(r)
        if hosts:
            out: "queue.Queue" = queue.Queue()
            workers = [threading.Thread(target=_host_worker, args=(rs, by_id, deadline, out), daemon=True)
                       for rs in hosts.values()]
            for w in workers:
                w.start()
            done = 0
            while any(w.is_alive() for w in workers) or not out.empty():
                try:
                    res = out.get(timeout=0.5)
                except queue.Empty:
                    if deadline is not None and time.monotonic() >= deadline + 30:
                        break       # a request still in flight long past the deadline is left behind
                    continue
                _record_fetch(conn, res, by_id[res["row"]["source_id"]], counts)
                done += 1
                if done % 20 == 0:
                    conn.commit()
            conn.commit()
    counts["bodies_pruned"] = prune_bodies(conn)
    conn.commit()
    return counts


def prune_bodies(conn, now: Optional[dt.datetime] = None, days: Optional[int] = None) -> int:
    """Delete the stored bodies of items no state copy was paired with (no state_copies row names them)
    more than config.REFERENCE_UNPAIRED_BODY_DAYS after they were fetched; set body_pruned_at. The row,
    its sig and its shingle count stay."""
    days = config.REFERENCE_UNPAIRED_BODY_DAYS if days is None else days
    now = now or _now()
    cutoff = _iso(now - dt.timedelta(days=days))
    rows = conn.execute(
        """SELECT r.id, r.url_hash FROM reference_items r
           WHERE r.body_hash IS NOT NULL AND r.body_pruned_at IS NULL
             AND COALESCE(r.fetched_at, r.discovered_at) < ?
             AND NOT EXISTS (SELECT 1 FROM state_copies s WHERE s.ref_id = r.id)""", (cutoff,)).fetchall()
    stamp = _iso(now)
    for r in rows:
        p = store.body_path(body_key(r["url_hash"]))
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        conn.execute("UPDATE reference_items SET body_pruned_at=? WHERE id=?", (stamp, r["id"]))
    return len(rows)


# ---------------------------------------------------------------------------
# Pipeline and relay hooks
# ---------------------------------------------------------------------------

def run_stage(conn, stage: str, deadline: Optional[float] = None) -> Dict:
    """poll or fetch within its own cap (POLL_SECONDS, FETCH_SECONDS) and the run's wall clock deadline
    (time.time based, as pipeline/run.py and relay.run_relay keep it). fetch has a slice of its own,
    FETCH_SLICE_SECONDS, which it takes even when the run's deadline leaves less or has passed: the
    article fetch before it may use the whole budget, and the reference pages would then never be read."""
    cap = POLL_SECONDS if stage == "poll" else FETCH_SECONDS
    mono = time.monotonic() + cap
    if deadline is not None:
        left = deadline - time.time()
        if stage == "fetch":
            left = max(left, FETCH_SLICE_SECONDS)
        mono = min(mono, time.monotonic() + left)
    if mono <= time.monotonic():
        return {"skipped": "no time left"}
    if stage == "poll":
        return poll(conn, mono)
    if stage == "fetch":
        return fetch(conn, mono)
    raise ValueError("unknown reference stage %r" % stage)


def bundle_records(conn, since: str) -> Iterable[Dict]:
    """The relay's reference items (collector self_hosted) discovered or fetched since `since`, as bundle
    records with the body inline (base64 of gzip, like an article's) and the fingerprint."""
    for r in conn.execute(
            """SELECT * FROM reference_items WHERE collector='self_hosted' AND (discovered_at >= ? OR fetched_at >= ?)
               ORDER BY id""", (since, since)):
        rec = {"_type": RECORD_TYPE, **{k: r[k] for k in RELAY_FIELDS}}
        if r["sig"] is not None:
            rec["sig"] = base64.b64encode(bytes(r["sig"])).decode("ascii")
        if r["body_hash"] and not r["body_pruned_at"]:
            body = store.load_body(body_key(r["url_hash"]))
            if body:
                rec["body"] = base64.b64encode(gzip.compress(body.encode("utf-8"))).decode("ascii")
        yield rec


def ingest_record(conn, rec: Dict) -> str:
    """One reference_item record from the relay bundle into reference_items (and its body file), and
    nothing else. A row is never moved back: it takes the record's fetch fields only when the record is
    further along (STATUS_RANK), and a time of day when it had a date only. Returns inserted, updated
    or unchanged."""
    h = rec.get("url_hash")
    if not h or not rec.get("url") or not str(rec.get("source_id") or "").startswith("ref_"):
        return "unchanged"
    sig = base64.b64decode(rec["sig"]) if rec.get("sig") else None
    body = gzip.decompress(base64.b64decode(rec["body"])).decode("utf-8") if rec.get("body") else None
    row = conn.execute("SELECT id, status, published_at, title, summary, author FROM reference_items WHERE url_hash=?",
                       (h,)).fetchone()
    if row is None:
        vals = {k: rec.get(k) for k in RELAY_FIELDS}
        vals["collector"] = vals.get("collector") or "self_hosted"
        vals["status"] = vals.get("status") or "discovered"
        vals["discovered_at"] = vals.get("discovered_at") or store.utcnow()
        vals["sig"] = sig
        vals["also_monitored"] = article_id_for(conn, h)
        if not body:
            vals["body_hash"] = None if not vals.get("body_pruned_at") else vals.get("body_hash")
        cols = list(vals)
        conn.execute("INSERT INTO reference_items (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" * len(cols))),
                     [vals[k] for k in cols])
        if body:
            store.save_body(body_key(h), body)
        return "inserted"
    fields = {}
    if STATUS_RANK.get(rec.get("status"), -1) > STATUS_RANK.get(row["status"], -1):
        for k in ("status", "fail_reason", "fetched_at", "body_hash", "body_chars", "extract_version", "shingles",
                  "body_pruned_at"):
            fields[k] = rec.get(k)
        fields["sig"] = sig
        if rec.get("status") == "fetched" and not body:
            fields["body_hash"] = None if not rec.get("body_pruned_at") else rec.get("body_hash")
    better = _better_time(row["published_at"], rec.get("published_at"))
    if better:
        fields["published_at"] = better
    for k in ("title", "summary", "author"):
        if not row[k] and rec.get(k):
            fields[k] = rec[k]
    if not fields:
        return "unchanged"
    conn.execute("UPDATE reference_items SET %s WHERE id=?" % ", ".join("%s=?" % k for k in fields),
                 list(fields.values()) + [row["id"]])
    if body and "status" in fields:
        store.save_body(body_key(h), body)
    return "updated"


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

# Hourly runs: this many consecutive failures of every listing of a source is about 3 days of refusals,
# the point at which the plan moves a source to the relay (by hand, in sources/reference.yaml).
RELAY_SUGGEST_FAILURES = 72


def validate(conn=None, sources: Optional[List[Dict]] = None, days: int = 7) -> Dict:
    """A report on every source: the registry checks, each listing's health, and what the last `days`
    days of items look like (statuses, how many carry a time of day or a date only, how many are dated
    after their discovery, how many have a body). It never deactivates or moves a source."""
    report = {"problems": [], "sources": {}}
    try:
        srcs = load_sources() if sources is None else sources
        if sources is not None:
            validate_sources(srcs)
    except Exception as exc:  # noqa: BLE001
        report["problems"].append("registry: %s: %s" % (type(exc).__name__, exc))
        return report
    from pipeline import registry
    outlet_ids = {o["id"] for o in registry.load_outlets(validate=False)}
    clash = sorted(outlet_ids & {s["id"] for s in srcs})
    if clash:
        report["problems"].append("ids also in outlets.yaml: %s" % ", ".join(clash))
    since = (_now() - dt.timedelta(days=days)).isoformat()
    for s in srcs:
        entry = {"active": s.get("active"), "collector": s.get("collector"), "listings": []}
        all_failing = True
        for feed in _listings(s):
            h = conn.execute("SELECT * FROM reference_feed_health WHERE feed_url=?", (feed["url"],)).fetchone() \
                if conn is not None else None
            entry["listings"].append({"url": feed["url"], "type": feed["type"], "time_rule": feed["time_rule"],
                                      "last_ok": h["last_ok"] if h else None, "last_error": h["last_error"] if h else None,
                                      "last_entries": h["last_entries"] if h else None,
                                      "consecutive_failures": h["consecutive_failures"] if h else None})
            if not h or h["consecutive_failures"] < RELAY_SUGGEST_FAILURES:
                all_failing = False
        if conn is not None:
            rows = conn.execute("SELECT status, published_at, discovered_at, body_hash, body_pruned_at FROM reference_items "
                                "WHERE source_id=? AND discovered_at >= ?", (s["id"], since)).fetchall()
            st: Dict[str, int] = {}
            timed = dated = future = bodies = 0
            for r in rows:
                st[r["status"]] = st.get(r["status"], 0) + 1
                if _timed(r["published_at"]):
                    timed += 1
                    if r["published_at"] > r["discovered_at"]:
                        future += 1
                elif r["published_at"]:
                    dated += 1
                if r["body_hash"] and not r["body_pruned_at"]:
                    bodies += 1
            entry.update(items=len(rows), statuses=st, timed=timed, date_only=dated, no_time=len(rows) - timed - dated,
                         dated_after_discovery=future, bodies=bodies)
            if rows and future > len(rows) * 0.1:
                report["problems"].append("%s: %d of %d items dated after their discovery; check the time_rule"
                                          % (s["id"], future, len(rows)))
        if all_failing and entry["listings"] and s.get("collector") == "hosted":
            report["problems"].append("%s: every listing has failed %d times in a row; the relay was retired on 2026-10-22, so check the listing or mark the source inactive"
                                      % (s["id"], RELAY_SUGGEST_FAILURES))
        report["sources"][s["id"]] = entry
    return report


SOURCE_STATUSES = ("fetched", "expired", "failed", "paywalled", "discovered", "blocked_robots")


def source_status(conn, now: Optional[dt.datetime] = None) -> List[Dict]:
    """Per reference source, for the research measures' exports: items, the count in each status
    (SOURCE_STATUSES), bodies kept, the share of dated items with a date only, and that share among
    items discovered more than a day ago (by then fetch should have read the page's time), with the
    flag "date only after day one" when it is DATE_ONLY_AFTER_DAY_FLAG or more over at least
    DATE_ONLY_MIN_ITEMS items. Read only; [] for a database without the table."""
    now = now or _now()
    day_ago = _iso(now - dt.timedelta(days=1))
    try:
        rows = conn.execute("SELECT source_id, status, published_at, discovered_at, body_hash, body_pruned_at "
                            "FROM reference_items").fetchall()
    except Exception:  # noqa: BLE001
        return []
    by: Dict[str, Dict] = {}
    for r in rows:
        d = by.setdefault(r["source_id"], {"items": 0, "statuses": {k: 0 for k in SOURCE_STATUSES}, "bodies": 0,
                                           "dated": 0, "date_only": 0, "old_dated": 0, "old_date_only": 0})
        d["items"] += 1
        st = r["status"] or "discovered"
        d["statuses"][st] = d["statuses"].get(st, 0) + 1
        if r["body_hash"] and not r["body_pruned_at"]:
            d["bodies"] += 1
        p = (r["published_at"] or "").strip()
        if not p:
            continue
        only = not _timed(p)
        d["dated"] += 1
        d["date_only"] += only
        if (r["discovered_at"] or "") < day_ago:
            d["old_dated"] += 1
            d["old_date_only"] += only
    out = []
    for sid in sorted(by):
        d = by[sid]
        share = round(d["date_only"] / float(d["dated"]), 3) if d["dated"] else None
        late = round(d["old_date_only"] / float(d["old_dated"]), 3) if d["old_dated"] else None
        flags = []
        if d["old_dated"] >= DATE_ONLY_MIN_ITEMS and late is not None and late >= DATE_ONLY_AFTER_DAY_FLAG:
            flags.append("date only after day one")
        out.append({"source": sid, "items": d["items"], "statuses": d["statuses"], "bodies": d["bodies"],
                    "share_date_only": share, "items_after_day_one": d["old_dated"],
                    "share_date_only_after_day_one": late, "flags": flags})
    return out


def status(conn) -> Dict:
    out = {}
    for r in conn.execute("""SELECT source_id, status, COUNT(*) n, SUM(body_hash IS NOT NULL AND body_pruned_at IS NULL) b,
                                    MAX(published_at) newest FROM reference_items GROUP BY source_id, status
                             ORDER BY source_id, status"""):
        s = out.setdefault(r["source_id"], {"statuses": {}, "bodies": 0, "newest": None})
        s["statuses"][r["status"]] = r["n"]
        s["bodies"] += r["b"] or 0
        if r["newest"] and (s["newest"] is None or r["newest"] > s["newest"]):
            s["newest"] = r["newest"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reference sources: poll, fetch, validate (report only), status.")
    ap.add_argument("cmd", choices=["poll", "fetch", "validate", "status"])
    ap.add_argument("--budget-minutes", type=float, default=None)
    ap.add_argument("--limit", type=int, default=None, help="fetch: at most this many pages")
    ap.add_argument("--source", action="append", default=None, help="only this source id (repeatable)")
    ap.add_argument("--db", default=None, help="database path (default: config.DB_PATH)")
    ap.add_argument("--bodies-dir", default=None, help="where bodies are written (default: data/bodies)")
    args = ap.parse_args(argv)
    if args.bodies_dir:
        config.BODIES_DIR = Path(args.bodies_dir)
    conn = store.connect(Path(args.db)) if args.db else store.connect()
    deadline = None if args.budget_minutes is None else time.monotonic() + args.budget_minutes * 60
    srcs = None
    if args.source:
        srcs = [s for s in load_sources() if s["id"] in set(args.source)]
    if args.cmd == "poll":
        out = poll(conn, deadline, sources=srcs if srcs is not None else None)
    elif args.cmd == "fetch":
        out = fetch(conn, deadline, limit=args.limit, sources=srcs)
    elif args.cmd == "validate":
        out = validate(conn, sources=srcs)
    else:
        out = status(conn)
    conn.commit()
    conn.close()
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return out


if __name__ == "__main__":
    main()
