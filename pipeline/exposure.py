"""Exposure: where an outlet placed each state origin item, from a cheap record of how it was found and
whether its homepage linked to it. Report only (sources/measures.yaml): nothing here changes a label, a
count, a denominator or the outlets monitored, and China stays unmonitored.

A release can be published and still have almost no readers, if it sits only on a press release page. So
for each state origin placement this measure records where the outlet put it, separating "published" from
"placed where readers see it". The levels, from most to least exposed:

  homepage      the outlet's homepage linked to it in at least one read (the collector step below)
  section_page  a main section page the registry names (main_sections) linked to it in at least one read
  news_feed     it was discovered through one of the outlet's editorial feeds: an RSS feed or a Google News
                sitemap (feeds, news_sitemaps), or a site-wide feed once registered as a section by mistake
                (release_sections rejected_feed, which pipeline/section_retag.py reads the same way)
  release_only  it was discovered only through a press release, sponsored or partner section, its feed or
                its index page (section_feeds, release_sections, the AAP release widget of
                pipeline/section_pages.py), or on a distribution wire, which carries nothing but releases

The discovery level needs no request: articles.feed_url, matched to the registry by registry.feed_key.
An item whose feed is no longer in the registry is read by its arrival marker (classify_rules.arrival_of:
a section feed stores the term section:<kind>), and counted as such (basis arrival_marker). A placement is
discovered once: articles.feed_url is the first feed that listed it, so a release seen first on the
release page and listed later in the general feed still reads release_only, and the general feed's
listing is not recorded. The homepage reads are what catch it in that case.

The collector step (collect, run_stage): in each run, after the article fetch (pipeline/run.py cmd_fetch),
the homepage of every outlet that carried settled state origin in the last LOOKBACK_DAYS days, and each
main section page the registry names for it, is read once, under the rules of every fetch of the tracker
(fetch_articles.can_fetch for robots.txt, its _rate_wait pacing with the site's Crawl-delay, polite_get for
redirects, the crawler's own user agent) and inside STAGE_SECONDS that cannot delay collection: it starts
only once the article queue is done, never runs past the stage's own deadline, starts no read within
READ_RESERVE_SECONDS of its end, and skips a site whose next polite slot is not due. One attempt per page:
a page that fails waits for the next run. Distribution wires and the shared AAP release widget are not
read (a wire's homepage is a release list, and the widget's links are scripts that match nothing).
Every same-site link the page carries is recorded in exposure_sightings under its canonical address
(store.canonical_url, hashed as store.url_hash, as articles are) with its first and last seen times, the
number of reads that saw it and the best rank it had on the page; each read is logged in exposure_reads.
A link that matches no stored article is kept UNMATCHED_KEEP_HOURS (its article may be discovered by a
later run) and then dropped; reads are kept READS_KEEP_DAYS. The step runs only while the measure is
switched on in sources/measures.yaml.

Hourly sampling misses short stays: a release that was on the homepage for twenty minutes between two
reads is never seen, and a stay is known only to the hour. A placement is "checked" when its outlet's
homepage was read successfully at least once from CHECK_BEFORE_HOURS before its discovery to
CHECK_AFTER_HOURS after it; only checked placements are in the homepage share's denominator.
"""
import datetime as dt
import random
import re
import sqlite3
import sys
import time
from bisect import bisect_left
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from pipeline import config, measures, model_only, registry, store

MEASURE_ID = "exposure"
VERSION = "1"

LEVELS = ("homepage", "section_page", "news_feed", "release_only")
RELEASE_KINDS = ("press_release", "sponsored", "partner", "distribution_wire")

# The collector step.
LOOKBACK_DAYS = 14            # outlets that carried settled state origin on these last days have their pages read
STAGE_SECONDS = 90            # the step's whole budget in one run
READ_RESERVE_SECONDS = 25     # no read starts this close to the step's end (one request's timeout and a margin)
WORKERS = 4                   # pages read at once, each on its own site
MAX_PAGES_PER_RUN = 80        # longest-unread first, so a cut-short run rotates
MAX_LINKS = 600               # links kept from one page, in page order
UNMATCHED_KEEP_HOURS = 48
READS_KEEP_DAYS = 90
HOMEPAGE_SAMPLE_ROWS = 300    # recent articles of the outlet read to find its site's host

# Which placements count as checked by the homepage reads.
CHECK_BEFORE_HOURS = 6
CHECK_AFTER_HOURS = 12

# The hand check.
SAMPLE_N = 40
CHECK_THRESHOLD = 0.8
CHECK_STATISTIC = "placement agreement"
SAMPLE_FIELDS = ["sample_id", "url", "outlet", "country", "headline", "where_placed", "coder", "note"]
CODED_VALUES = {"release": "release_only", "news": "news_feed", "unclear": None}

_SKIP_PATH = re.compile(r"/(tag|tags|author|authors|search|login|logout|register|subscribe|account|privacy|terms|"
                        r"cookie|cookies|contact|about|newsletter|newsletters)(/|$)", re.I)


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr)


def _outlets(ctx_outlets=None) -> List[Dict]:
    if ctx_outlets is not None:
        return ctx_outlets
    try:
        return registry.load_outlets()
    except Exception as exc:  # noqa: BLE001  a registry problem leaves the measure without outlets, never a stop
        _warn("exposure: outlets not loaded (%s)" % exc)
        return []


def _day_sql(a: str = "a") -> str:
    return "COALESCE({a}.day_of_record, substr({a}.discovered_at, 1, 10))".format(a=a)


def _parse(ts: Optional[str]) -> Optional[dt.datetime]:
    if not ts:
        return None
    try:
        d = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _iso(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# The discovery level: no request
# ---------------------------------------------------------------------------

def feed_index(outlets: List[Dict]) -> Dict[str, List[Tuple[str, str, Optional[str], str]]]:
    """{registry.feed_key(url): [(outlet_id, level, kind, basis)]} for every feed and section page the
    registry names, active or not: editorial feeds and sitemaps (news_feed), section feeds and pages and
    the sections recorded in release_sections (release_only, with their kind), and the site-wide feeds
    once registered as a section and rejected (news_feed, basis site_wide_feed)."""
    idx: Dict[str, List] = defaultdict(list)

    def add(url, entry):
        if isinstance(url, str) and url.strip():
            idx[registry.feed_key(url)].append(entry)
    for o in outlets:
        oid = o["id"]
        for u in o.get("feeds") or []:
            add(u, (oid, "news_feed", None, "rss"))
        for u in o.get("news_sitemaps") or []:
            add(u, (oid, "news_feed", None, "news_sitemap"))
        for f in o.get("section_feeds") or []:
            add(f.get("url"), (oid, "release_only", f.get("kind"), "section_" + (f.get("type") or "rss")))
        for s in (o.get("release_sections") or {}).get("sections") or []:
            if s.get("rejected_feed"):
                add(s["rejected_feed"], (oid, "news_feed", None, "site_wide_feed"))
            if not s.get("rejected"):
                add(s.get("feed"), (oid, "release_only", s.get("kind"), "section_rss"))
                add(s.get("url"), (oid, "release_only", s.get("kind"), "section_page"))
    return dict(idx)


def discovery_level(outlet_id: str, feed_url: Optional[str], gate_terms, idx: Dict, wires: set) -> Tuple[str, Optional[str], str]:
    """(level, kind, basis) of one placement from how it was found. kind is the release kind for
    release_only, None otherwise."""
    if outlet_id in wires:
        return "release_only", "distribution_wire", "distribution_wire"
    hits = idx.get(registry.feed_key(feed_url)) if feed_url else None
    if hits:
        mine = [h for h in hits if h[0] == outlet_id] or hits
        rel = [h for h in mine if h[1] == "release_only"]
        if rel:
            return "release_only", rel[0][2] or "press_release", rel[0][3]
        return "news_feed", None, mine[0][3]
    from pipeline import classify_rules
    arrival = classify_rules.arrival_of(gate_terms)
    if arrival != "editorial_feed":
        kind = next((k for k, v in classify_rules.SECTION_ARRIVALS.items() if v == arrival), "press_release")
        return "release_only", kind, "arrival_marker"
    return "news_feed", None, "arrival_marker"


def _settled_sql(where: str = "") -> str:
    return ("""SELECT a.id, a.url, a.url_hash, a.feed_url, a.gate_terms, a.outlet_id, a.country, a.title,
                      a.discovered_at, %s AS day
               FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
               WHERE a.gate_relevant=1 AND c.category='A'%s%s""" % (_day_sql(), model_only.evidence_sql(), where))


def placement_rows(conn, outlets: Optional[List[Dict]] = None) -> List[Dict]:
    """Every settled state origin placement (undetermined labels left out, pipeline/model_only.py; one row
    per placement, pipeline/placements.py) with its country as the export counts it and its discovery
    level."""
    outlets = _outlets(outlets)
    by_id = {o["id"]: o for o in outlets}
    idx = feed_index(outlets)
    wires = registry.distribution_wire_ids(outlets)
    from pipeline import placements
    shadow = placements.shadowed(conn)
    out = []
    for r in conn.execute(_settled_sql()):
        if r["id"] in shadow:
            continue
        level, kind, basis = discovery_level(r["outlet_id"], r["feed_url"], r["gate_terms"], idx, wires)
        o = by_id.get(r["outlet_id"])
        out.append({"id": r["id"], "url": r["url"], "url_hash": r["url_hash"], "outlet_id": r["outlet_id"],
                    "country": registry.export_country(o) if o else r["country"], "title": r["title"],
                    "discovered_at": r["discovered_at"], "day": r["day"], "level": level, "kind": kind,
                    "basis": basis, "widget": bool(placements.widget_of(r["url"]))})
    return out


# ---------------------------------------------------------------------------
# The collector step: homepage and main section pages
# ---------------------------------------------------------------------------

def collector_on(config_path: Optional[Path] = None) -> bool:
    """Whether the step may read pages: the measure is switched on in sources/measures.yaml, and this is
    the hosted collector. An unreadable measures file reads as off."""
    if config.COLLECTOR != registry.HOSTED:
        return False
    try:
        spec = next((m for m in measures.load_config(config_path) if m["id"] == MEASURE_ID), None)
    except Exception:  # noqa: BLE001
        return False
    return bool(spec and spec["enabled"])


def homepage_of(conn, outlet: Dict) -> Optional[str]:
    """The outlet's homepage: its registry homepage, else the root of the host most of its recent stored
    articles live on (shared widget addresses left out), or None."""
    if outlet.get("homepage"):
        return outlet["homepage"]
    from pipeline import placements
    hosts = Counter()
    for (url,) in conn.execute("SELECT url FROM articles WHERE outlet_id=? ORDER BY id DESC LIMIT ?",
                               (outlet["id"], HOMEPAGE_SAMPLE_ROWS)):
        if not url or placements.widget_of(url):
            continue
        p = urlsplit(url.strip())
        if p.scheme in ("http", "https") and p.hostname:
            hosts[(p.scheme, p.netloc.lower())] += 1
    if not hosts:
        return None
    (scheme, host), _n = sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return "%s://%s/" % (scheme, host)


def pages_due(conn, outlets: Optional[List[Dict]] = None, now: Optional[dt.datetime] = None,
              lookback_days: int = LOOKBACK_DAYS) -> List[Dict]:
    """The pages to read this run: the homepage and named main section pages of each outlet (not a
    distribution wire) with a settled state origin placement outside the shared widget on the last
    lookback_days days, longest unread first, at most MAX_PAGES_PER_RUN."""
    outlets = _outlets(outlets)
    by_id = {o["id"]: o for o in outlets if not registry.is_distribution_wire(o)}
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=lookback_days)).date().isoformat()
    from pipeline import placements
    carried = set()
    for r in conn.execute(_settled_sql(" AND %s >= ?" % _day_sql()), (since,)):
        if r["outlet_id"] in by_id and not placements.widget_of(r["url"]):
            carried.add(r["outlet_id"])
    last = {r[0]: r[1] for r in conn.execute("SELECT page_url, MAX(read_at) FROM exposure_reads GROUP BY page_url")}
    pages = []
    for oid in sorted(carried):
        o = by_id[oid]
        home = homepage_of(conn, o)
        seen = set()
        for url, kind in ([(home, "homepage")] if home else []) + [(u, "section") for u in o.get("main_sections") or []]:
            if url and url not in seen:
                seen.add(url)
                pages.append({"outlet_id": oid, "page_url": url, "page_kind": kind, "last_read": last.get(url) or ""})
    pages.sort(key=lambda p: (p["last_read"], p["page_kind"] != "homepage", p["page_url"]))
    return pages[:MAX_PAGES_PER_RUN]


def read_page(url: str, latest_start: Optional[float] = None) -> Dict:
    """One polite attempt at a page, with the tracker's own helpers: robots.txt (can_fetch), the domain's
    pacing and Crawl-delay (_rate_wait), redirects checked hop by hop (polite_get), the crawler's user
    agent, and a Retry-After honoured by holding the domain. Never retried here: a failure waits for the
    next run. When the site's next polite slot comes after latest_start (a time.monotonic value), nothing
    is requested and the result is {skipped: True}: the page waits for the next run. Returns {html, status,
    error}."""
    import requests
    from pipeline import fetch_articles as fa
    allowed, why = fa.can_fetch(url)
    if not allowed:
        return {"html": None, "status": None, "error": why}
    domain = fa.domain_of(url)
    held = fa.held_reason(domain)
    if held:
        return {"html": None, "status": None, "error": held}
    if latest_start is not None and fa.slot_in(domain) + config.JITTER_SECONDS > latest_start - time.monotonic():
        return {"html": None, "status": None, "error": "slot_not_due", "skipped": True}
    fa._rate_wait(domain)
    try:
        resp = fa.polite_get(url, headers={"User-Agent": config.USER_AGENT,
                                           "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                                           "Accept-Language": "*"}, timeout=config.FETCH_TIMEOUT)
    except fa.RedirectRefused as exc:
        return {"html": None, "status": None, "error": exc.reason}
    except requests.RequestException as exc:
        return {"html": None, "status": None, "error": type(exc).__name__}
    status = resp.status_code
    if status != 200:
        fa.note_retry_after(domain, resp)
        return {"html": None, "status": status, "error": "http_%d" % status}
    ctype = resp.headers.get("Content-Type", "")
    if "html" not in ctype and not resp.text.lstrip().lower().startswith("<"):
        return {"html": None, "status": status, "error": "not_html:%s" % ctype.split(";")[0]}
    if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return {"html": resp.text, "status": status, "error": None, "final_url": getattr(resp, "url", None) or url}


def page_links(html: str, page_url: str) -> List[str]:
    """The page's same-site links in page order, each once, as canonical addresses (store.canonical_url),
    at most MAX_LINKS. The page itself, its root and account, search and similar utility pages are left
    out; navigation to sections stays in and simply matches no article."""
    from lxml import html as lhtml
    from pipeline.section_pages import _same_site
    try:
        doc = lhtml.fromstring(html)
    except Exception:  # noqa: BLE001  an unparseable page has no links
        return []
    own = store.canonical_url(page_url)
    out, seen = [], set()
    for a in doc.iter("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = urljoin(page_url, href).split("#")[0]
        if not url.startswith(("http://", "https://")) or not _same_site(url, page_url):
            continue
        path = urlsplit(url).path
        if not path.strip("/") or _SKIP_PATH.search(path):
            continue
        canon = store.canonical_url(url)
        if canon == own or canon in seen:
            continue
        seen.add(canon)
        out.append(canon)
        if len(out) >= MAX_LINKS:
            break
    return out


def other_scheme(url: str) -> str:
    """The same address under the other of http and https: canonical_url keeps the scheme, and a page may
    link an article over http that is stored over https, or the reverse."""
    if url.startswith("https://"):
        return "http://" + url[8:]
    if url.startswith("http://"):
        return "https://" + url[7:]
    return url


def _stored_form(conn, link: str) -> str:
    """The link as a stored article has it: as given, or under the other scheme when only that one is
    stored."""
    if conn.execute("SELECT 1 FROM articles WHERE url_hash=?", (store.url_hash(link),)).fetchone():
        return link
    alt = other_scheme(link)
    if alt != link and conn.execute("SELECT 1 FROM articles WHERE url_hash=?", (store.url_hash(alt),)).fetchone():
        return alt
    return link


def record_read(conn, page: Dict, at: str, result: Dict, links: List[str]) -> int:
    """Log one read and upsert the links it saw. Returns the number of links recorded."""
    conn.execute("""INSERT INTO exposure_reads(outlet_id, page_kind, page_url, read_at, ok, http_status, error, links)
                    VALUES (?,?,?,?,?,?,?,?)""",
                 (page["outlet_id"], page["page_kind"], page["page_url"], at, 1 if result.get("html") is not None else 0,
                  result.get("status"), result.get("error"), len(links)))
    for rank, link in enumerate(links, start=1):
        link = _stored_form(conn, link)
        conn.execute("""INSERT INTO exposure_sightings(page_url, url_hash, outlet_id, page_kind, url, first_seen, last_seen,
                                                       reads, best_rank)
                        VALUES (?,?,?,?,?,?,?,1,?)
                        ON CONFLICT(page_url, url_hash) DO UPDATE SET last_seen=excluded.last_seen,
                            reads=exposure_sightings.reads + 1,
                            best_rank=MIN(COALESCE(exposure_sightings.best_rank, excluded.best_rank), excluded.best_rank)""",
                     (page["page_url"], store.url_hash(link), page["outlet_id"], page["page_kind"], link, at, at, rank))
    return len(links)


def prune(conn, now: Optional[dt.datetime] = None) -> Dict[str, int]:
    """Drop sightings that match no stored article once they are UNMATCHED_KEEP_HOURS old, and reads
    older than READS_KEEP_DAYS."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cut = _iso(now - dt.timedelta(hours=UNMATCHED_KEEP_HOURS))
    a = conn.execute("""DELETE FROM exposure_sightings WHERE last_seen < ?
                        AND url_hash NOT IN (SELECT url_hash FROM articles)""", (cut,)).rowcount
    b = conn.execute("DELETE FROM exposure_reads WHERE read_at < ?",
                     (_iso(now - dt.timedelta(days=READS_KEEP_DAYS)),)).rowcount
    return {"sightings_pruned": a, "reads_pruned": b}


def collect(conn, deadline: float, outlets: Optional[List[Dict]] = None, now: Optional[dt.datetime] = None,
            reader: Optional[Callable[[str, Optional[float]], Dict]] = None, workers: int = WORKERS) -> Dict:
    """Read the pages due (pages_due) until deadline (a time.monotonic value), WORKERS at a time, each site
    once. Network in the worker threads, parsing and every write here, in the caller's thread. No read
    starts within READ_RESERVE_SECONDS of the deadline, nor on a site whose next polite slot is further off
    than the time left. Commits what it writes."""
    from pipeline import fetch_articles as fa
    reader = reader or read_page
    counts = {"pages_due": 0, "read": 0, "ok": 0, "failed": 0, "skipped_slot": 0, "not_reached": 0, "links": 0}
    pages = pages_due(conn, outlets, now)
    counts["pages_due"] = len(pages)
    queue = list(pages)
    busy_domains = set()
    latest_start = deadline - READ_RESERVE_SECONDS
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    running = {}
    try:
        while queue or running:
            while queue and len(running) < max(1, workers) and time.monotonic() < latest_start:
                page = None
                for i, p in enumerate(queue):
                    if fa.domain_of(p["page_url"]) not in busy_domains:
                        page = queue.pop(i)
                        break
                if page is None:
                    break
                dom = fa.domain_of(page["page_url"])
                if fa.slot_in(dom) > latest_start - time.monotonic():
                    counts["skipped_slot"] += 1
                    continue
                busy_domains.add(dom)
                running[pool.submit(reader, page["page_url"], latest_start)] = page
            if not running:
                break
            done, _ = wait(list(running), timeout=max(0.1, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
            if not done:
                if time.monotonic() >= deadline:
                    break
                continue
            for fut in done:
                page = running.pop(fut)
                busy_domains.discard(fa.domain_of(page["page_url"]))
                try:
                    result = fut.result()
                except Exception as exc:  # noqa: BLE001  one page's failure is recorded, never a stop
                    result = {"html": None, "status": None, "error": "exception: %s" % type(exc).__name__}
                if result.get("skipped"):
                    counts["skipped_slot"] += 1      # nothing was requested: the page waits for the next run
                    continue
                at = store.utcnow() if now is None else _iso(now)
                links = page_links(result["html"], result.get("final_url") or page["page_url"]) \
                    if result.get("html") is not None else []
                counts["links"] += record_read(conn, page, at, result, links)
                counts["read"] += 1
                counts["ok" if result.get("html") is not None else "failed"] += 1
                conn.commit()
    finally:
        # A read still running at the deadline is left to finish on its own (its request timeout bounds it)
        # and is not recorded; nothing here waits for it.
        pool.shutdown(wait=False)
    counts["not_reached"] = len(queue)
    counts["abandoned"] = len(running)
    counts.update(prune(conn, now))
    conn.commit()
    return counts


def run_stage(conn, deadline: Optional[float] = None, config_path: Optional[Path] = None, **kw) -> Dict:
    """The collector step for pipeline/run.py: STAGE_SECONDS at most and never past the stage's own deadline
    (time.time based, as run.py keeps it). Off while the measure is off. A failure is recorded and rolled
    back, never a stop."""
    if not collector_on(config_path):
        return {"skipped": "measure off"}
    cap = time.monotonic() + STAGE_SECONDS
    if deadline is not None:
        cap = min(cap, time.monotonic() + (deadline - time.time()))
    if cap - time.monotonic() < READ_RESERVE_SECONDS:
        return {"skipped": "no time left"}
    started = time.monotonic()
    try:
        out = collect(conn, cap, **kw)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


# ---------------------------------------------------------------------------
# Joining the reads to the placements
# ---------------------------------------------------------------------------

def _reads_by_outlet(conn) -> Dict[Tuple[str, str], List[dt.datetime]]:
    out = defaultdict(list)
    for r in conn.execute("SELECT outlet_id, page_kind, read_at FROM exposure_reads WHERE ok=1"):
        d = _parse(r["read_at"])
        if d:
            out[(r["outlet_id"], r["page_kind"])].append(d)
    for v in out.values():
        v.sort()
    return out


def _checked(times: List[dt.datetime], at: Optional[dt.datetime]) -> bool:
    if not times or at is None:
        return False
    lo, hi = at - dt.timedelta(hours=CHECK_BEFORE_HOURS), at + dt.timedelta(hours=CHECK_AFTER_HOURS)
    i = bisect_left(times, lo)
    return i < len(times) and times[i] <= hi


def _sightings(conn) -> Dict[Tuple[str, str, str], Dict]:
    """{(outlet_id, page_kind, url_hash): {first, last, reads}} over every page of that kind."""
    out = {}
    for r in conn.execute("""SELECT outlet_id, page_kind, url_hash, MIN(first_seen) AS f, MAX(last_seen) AS l,
                                    SUM(reads) AS n FROM exposure_sightings GROUP BY outlet_id, page_kind, url_hash"""):
        out[(r["outlet_id"], r["page_kind"], r["url_hash"])] = {"first": r["f"], "last": r["l"], "reads": r["n"]}
    return out


def assess_placements(conn, outlets: Optional[List[Dict]] = None) -> List[Dict]:
    """placement_rows with the reads joined: checked (the outlet's homepage was read around its discovery),
    on_homepage, on_section_page, and the exposure level (the highest of the three sources)."""
    rows = placement_rows(conn, outlets)
    try:
        reads = _reads_by_outlet(conn)
        seen = _sightings(conn)
    except sqlite3.OperationalError:
        reads, seen = {}, {}
    for p in rows:
        at = _parse(p["discovered_at"])
        canon = store.canonical_url(p["url"] or "")
        keys = {p["url_hash"], store.url_hash(canon), store.url_hash(other_scheme(canon))}
        p["checked"] = _checked(reads.get((p["outlet_id"], "homepage"), []), at)
        p["section_checked"] = _checked(reads.get((p["outlet_id"], "section"), []), at)
        home = [seen[(p["outlet_id"], "homepage", k)] for k in keys if (p["outlet_id"], "homepage", k) in seen]
        sec = [seen[(p["outlet_id"], "section", k)] for k in keys if (p["outlet_id"], "section", k) in seen]
        p["on_homepage"] = bool(home)
        p["on_section_page"] = bool(sec)
        p["home_reads"] = sum(h["reads"] for h in home)
        if home:
            f, l = _parse(min(h["first"] for h in home)), _parse(max(h["last"] for h in home))
            p["home_hours"] = round((l - f).total_seconds() / 3600.0, 1) if f and l else None
        p["exposure"] = ("homepage" if p["on_homepage"] else "section_page" if p["on_section_page"] else p["level"])
    return rows


# ---------------------------------------------------------------------------
# The measure
# ---------------------------------------------------------------------------

def inputs_present(conn) -> int:
    """Settled state origin labels on record (placements before shared copies are joined). Never writes."""
    try:
        return conn.execute("""SELECT COUNT(*) FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
                               WHERE a.gate_relevant=1 AND c.category='A'%s""" % model_only.evidence_sql()).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def compute(conn, ctx, deadline) -> Dict:
    """Nothing to write: the collector step stores the reads, and the levels are read at export time."""
    try:
        reads = conn.execute("SELECT COUNT(*), COALESCE(SUM(ok), 0) FROM exposure_reads").fetchone()
    except sqlite3.OperationalError:
        reads = (0, 0)
    return {"reads": reads[0], "reads_ok": reads[1]}


def _share(a: int, b: int) -> Optional[float]:
    return round(a / b, 4) if b else None


def _median(xs: List[float]) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else round((xs[m - 1] + xs[m]) / 2.0, 1)


def _group() -> Dict:
    return {"placements": 0, "news_feed": 0, "release_only": 0, "checked": 0, "on_homepage": 0, "on_section_page": 0,
            "release_only_checked": 0, "release_only_on_homepage": 0}


def _add(g: Dict, p: Dict) -> None:
    g["placements"] += 1
    g[p["level"]] += 1
    if p["checked"]:
        g["checked"] += 1
        if p["on_homepage"]:
            g["on_homepage"] += 1
        if p["level"] == "release_only":
            g["release_only_checked"] += 1
            if p["on_homepage"]:
                g["release_only_on_homepage"] += 1
    if p["on_section_page"]:
        g["on_section_page"] += 1


def build_export(conn, ctx) -> Dict:
    """exposure.json: counts by discovery level and by homepage presence, in total, for newsrooms (the
    distribution wires left out), by release kind, by country and by outlet. Never an address, a headline
    or any article text."""
    ctx = ctx or {}
    outlets = _outlets(ctx.get("outlets"))
    names = {o["id"]: o["name"] for o in outlets}
    rows = assess_placements(conn, outlets)
    total, newsrooms = _group(), _group()
    by_country, by_outlet = defaultdict(_group), defaultdict(_group)
    kinds, basis = Counter(), Counter()
    stays, one_read = [], 0
    for p in rows:
        _add(total, p)
        if p["kind"] != "distribution_wire":
            _add(newsrooms, p)
        _add(by_country[p["country"]], p)
        _add(by_outlet[p["outlet_id"]], p)
        if p["level"] == "release_only":
            kinds[p["kind"]] += 1
        basis[p["basis"]] += 1
        if p["checked"] and p["on_homepage"]:
            stays.append(p.get("home_hours"))
            one_read += 1 if p.get("home_reads") == 1 else 0
    for g in [total, newsrooms] + list(by_country.values()) + list(by_outlet.values()):
        g["share_release_only"] = _share(g["release_only"], g["placements"])
        g["share_on_homepage"] = _share(g["on_homepage"], g["checked"])
    try:
        r = conn.execute("""SELECT COUNT(*) AS n, COALESCE(SUM(ok), 0) AS ok, MIN(read_at) AS first, MAX(read_at) AS last,
                                   COUNT(DISTINCT CASE WHEN ok=1 THEN outlet_id END) AS outlets,
                                   COUNT(DISTINCT CASE WHEN page_kind='section' THEN page_url END) AS sections
                            FROM exposure_reads""").fetchone()
        reads = {"reads": r["n"], "reads_ok": r["ok"], "first": r["first"], "last": r["last"], "outlets_read": r["outlets"],
                 "section_pages": r["sections"]}
    except sqlite3.OperationalError:
        reads = {"reads": 0, "reads_ok": 0, "first": None, "last": None, "outlets_read": 0, "section_pages": 0}
    days = sorted(p["day"] for p in rows if p["day"])
    country_rows = [dict(v, id=k) for k, v in by_country.items()]
    country_rows.sort(key=lambda e: (-e["placements"], str(e["id"])))
    outlet_rows = [dict(v, id=k, name=names.get(k, k),
                        country=next((p["country"] for p in rows if p["outlet_id"] == k), None))
                   for k, v in by_outlet.items()]
    outlet_rows.sort(key=lambda e: (-e["placements"], str(e["id"])))
    return {
        "generated_at": store.utcnow(),
        "version": VERSION,
        "window": {"first": days[0] if days else None, "last": days[-1] if days else None},
        "levels": list(LEVELS),
        "totals": total,
        "newsrooms": newsrooms,
        "release_kinds": {k: kinds.get(k, 0) for k in RELEASE_KINDS},
        "basis": dict(sorted(basis.items())),
        "homepage": dict(reads, lookback_days=LOOKBACK_DAYS, check_before_hours=CHECK_BEFORE_HOURS,
                         check_after_hours=CHECK_AFTER_HOURS, median_hours_seen=_median(stays), seen_in_one_read=one_read),
        "by_country": country_rows,
        "by_outlet": outlet_rows,
    }


def meta_summary(conn, ctx) -> Dict:
    d = build_export(conn, ctx)
    n = d["newsrooms"]
    return {"placements": d["totals"]["placements"], "newsroom_placements": n["placements"],
            "release_only": n["release_only"], "share_release_only": n["share_release_only"],
            "checked": n["checked"], "on_homepage": n["on_homepage"], "reads": d["homepage"]["reads"],
            "window": d["window"]}


def methodology_text(block) -> str:
    paras = [
        "This measure asks where an outlet placed each state origin item, because a release can be published "
        "and still have almost no readers if it sits only on a press release page. It separates published from "
        "placed where readers see it, on four levels: linked from the outlet's homepage, linked from a main "
        "section page the registry names, listed in the outlet's general news feed, or found only in a press "
        "release, sponsored or partner section or its feed. A distribution wire carries nothing but releases, "
        "so its placements are on the last level and are shown apart from the newsrooms.",
        "The feed level costs no request. Each stored article keeps the feed or section page that first listed "
        "it, and the registry says which of those are editorial feeds and which are release, sponsored or "
        "partner sections. An item is discovered once, so a release that first appeared on the release page and "
        "was listed in the general feed later still counts as found only in the release section; the homepage "
        "reads are what catch it in that case. An item whose feed has since left the registry is read by the "
        "section marker stored when it arrived.",
        "The homepage level comes from a small reading step in each collection run. Once the article pages are "
        "fetched, the homepage of every outlet that carried state origin in the previous %d days, and any main "
        "section page the registry names for it, is read once, under the same rules as every other page the "
        "tracker reads: robots.txt honoured, the site's own pace and crawl delay kept, redirects checked, the "
        "crawler's own user agent, paywalls never bypassed. The step has a fixed time limit of %d seconds, starts "
        "only after the article fetch and never runs past the run's own deadline, so it cannot delay collection; "
        "a page it does not reach is read first in the next run. Each link the page carries is matched to the "
        "stored articles by its canonical address, and its first and last seen times are kept. A placement "
        "counts toward the homepage share only when its outlet's homepage was read at least once from %d hours "
        "before its discovery to %d hours after it." % (LOOKBACK_DAYS, STAGE_SECONDS, CHECK_BEFORE_HOURS,
                                                        CHECK_AFTER_HOURS),
        "The limits matter. Runs come about once an hour, so a link that stayed on the homepage for less than an "
        "hour between two reads is missed, and how long a link stayed is known only to the hour; the homepage "
        "share is therefore a lower bound. A link anywhere on the page counts, at the top or at the foot, and "
        "the page as read by the crawler may differ from what a reader in another country, or a logged in "
        "reader, sees. Pages that build their links in the browser show none to the crawler. The measure "
        "inherits the tracker's outlet list and classification, and it changes no label, count or share on "
        "the map.",
        "The hand check draws up to %d placements, half found only in a release section and half from a general "
        "news feed, without saying which. A coder opens each article and records whether the outlet presents it "
        "as a release, sponsored or partner item or as ordinary news, from the section, label and address the "
        "page shows. It passes when at least %d percent of the decided rows agree with the stored level."
        % (SAMPLE_N, int(CHECK_THRESHOLD * 100)),
    ]
    return "\n\n".join(paras)


# ---------------------------------------------------------------------------
# The hand check
# ---------------------------------------------------------------------------

def draw_sample(conn, n=SAMPLE_N, seed=None, out_dir=None, outlets=None):
    """Up to n newsroom placements (distribution wires and the shared widget left out, which are releases by
    construction), half found only in a release section and half from a general news feed, shuffled. The
    coder writes where_placed: release, news or unclear. The key holds the stored levels."""
    n = max(2, min(int(n or SAMPLE_N), SAMPLE_N))
    outlets = _outlets(outlets)
    names = {o["id"]: o["name"] for o in outlets}
    rows = [p for p in placement_rows(conn, outlets) if p["kind"] != "distribution_wire" and not p["widget"]]
    rng = random.Random(str(seed))
    rel = sorted((p for p in rows if p["level"] == "release_only"), key=lambda p: p["id"])
    news = sorted((p for p in rows if p["level"] == "news_feed"), key=lambda p: p["id"])
    k_rel = min(len(rel), n // 2)
    picked = rng.sample(rel, k_rel) + rng.sample(news, min(len(news), n - k_rel))
    rng.shuffle(picked)
    out, answers = [], {}
    for i, p in enumerate(picked, start=1):
        sid = "x%02d" % i
        out.append({"sample_id": sid, "url": (p["url"] or "").strip(), "outlet": names.get(p["outlet_id"], p["outlet_id"]),
                    "country": p["country"], "headline": " ".join((p["title"] or "").split()), "where_placed": "",
                    "coder": "", "note": ""})
        answers[sid] = p["level"]
    key = {"answers": answers, "threshold": CHECK_THRESHOLD, "statistic": CHECK_STATISTIC,
           "values": sorted(CODED_VALUES),
           "instructions": "Open each article and write in where_placed whether the outlet presents it as a release, "
                           "sponsored or partner item (release), as ordinary news (news), or cannot be told (unclear), "
                           "from the section, label and address the page shows."}
    return measures.write_sample(Path(out_dir or measures.REVIEWS_DIR), MEASURE_ID, out, SAMPLE_FIELDS, key,
                                 version=VERSION, seed=seed)


def score_check(rows, key) -> Dict:
    """Agreement between the coders' where_placed and the stored level, over the decided rows (release or
    news). An unclear row is coded but decides nothing."""
    answers = (key or {}).get("answers") or {}
    by_sid = {(r.get("sample_id") or "").strip(): r for r in rows or []}
    coded = decided = agree = 0
    for sid, level in answers.items():
        v = ((by_sid.get(sid) or {}).get("where_placed") or "").strip().lower()
        if v not in CODED_VALUES:
            continue
        coded += 1
        if CODED_VALUES[v] is None:
            continue
        decided += 1
        agree += 1 if CODED_VALUES[v] == level else 0
    return measures.check_result(CHECK_STATISTIC, round(agree / decided, 4) if decided else None,
                                 float((key or {}).get("threshold") or CHECK_THRESHOLD), len(answers), coded,
                                 decided=decided)
