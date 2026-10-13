"""Read a Google News sitemap as if it were a feed.

A short RSS feed overflows between hourly polls at a busy site: at peak, the ten items of the Rio
Times feed spanned 0.19 hours and those of Clarin's 0.34, and PR Newswire's twenty item feed lost an
estimated 431 China items in 30 days, about 103 of them state origin. Many such sites list a Google
News sitemap in robots.txt. By Google's rules it lists every article of the last two days (up to
1,000 per file) with its title and publication date, so one request per poll covers the whole window
between polls, however busy the site.

A feed of type news_sitemap (registry field news_sitemaps, an editorial feed beside the outlet's RSS,
never in its place) is read here. Both shapes are accepted: a urlset whose <url> entries carry
<news:news> with <news:title> and <news:publication_date>, and a sitemap index pointing at such
files, of which the MAX_CHILD_SITEMAPS most recently modified are read (one level only). Every
request goes through the robots.txt check, the per domain rate limiter and the declared user agent,
like every other request the collector makes. The return value has the shape of
feeds_util.fetch_feed, each entry the shape feedparser gives an RSS item (link, title, summary,
published_parsed), so the collector handles it like any feed. The summary is empty: a sitemap has
none, so the keyword gate reads the title alone, with the title_only terms of keywords.yaml (Chinese
cities, companies, fairs and state media brands that a headline names where a summary says China).
"""
import calendar
import datetime as dt
import gzip
import time
from typing import Dict, List, Optional, Tuple

NEWS_SITEMAP = "news_sitemap"
# A news sitemap index at the sites registered so far points at one or two current files; older or
# section files beyond the few most recent add requests and no news.
MAX_CHILD_SITEMAPS = 3
# Google allows 1,000 <url> entries a file; a file read past that is not a news sitemap.
MAX_ENTRIES_PER_FILE = 1000
# Entries published more than this before the read are dropped. A news sitemap should carry two days;
# a site that leaves older articles in it would otherwise feed weeks of old items into the counts. It
# stays a day inside config.GATED_OUT_RETENTION_DAYS (3), so an item the gate rejected is still on
# record, and known, for as long as the sitemap can list it; it is never inserted and counted twice.
MAX_AGE_HOURS = 48
# A larger body is not a news sitemap (1,000 entries run to about 0.5 MB).
MAX_BYTES = 20 * 1024 * 1024

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
NEWS_NS = "http://www.google.com/schemas/sitemap-news/0.9"


def get_xml(url: str, timeout: Optional[int] = None) -> Tuple[Optional[bytes], Optional[int], Optional[str]]:
    """One request for a sitemap file, under the robots.txt check, the per domain rate limiter and the
    declared user agent. Returns (body, status, error); a gzip body is unpacked."""
    import requests
    from pipeline import config
    from pipeline.fetch_articles import _rate_wait, can_fetch, domain_of
    allowed, why = can_fetch(url)
    if not allowed:
        return None, None, why
    _rate_wait(domain_of(url))
    try:
        resp = requests.get(url, headers={"User-Agent": config.USER_AGENT,
                                          "Accept": "application/xml, text/xml, */*"},
                            timeout=timeout or config.FEED_TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:
        return None, None, type(exc).__name__
    if resp.status_code >= 400:
        return None, resp.status_code, "http_%d" % resp.status_code
    body = resp.content or b""
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError):
            return None, resp.status_code, "bad_gzip"
    if len(body) > MAX_BYTES:
        return None, resp.status_code, "too_large"
    return body, resp.status_code, None


def _w3c_time(text: Optional[str]) -> Optional[dt.datetime]:
    """A W3C datetime (2026-10-12T08:15:00+02:00, 2026-10-12T06:15Z, 2026-10-12) in UTC, or None."""
    t = (text or "").strip()
    if not t:
        return None
    t = t.replace("Z", "+00:00").replace("z", "+00:00")
    if len(t) == 10:
        t += "T00:00:00+00:00"
    # Python 3.9 reads fractions of 3 or 6 digits only, and some sites write 1 or 7.
    if "." in t[19:]:
        head, rest = t[:19], t[19:]
        frac = rest[1:]
        digits = len(frac) - len(frac.lstrip("0123456789"))
        t = head + rest[1 + digits:]
    try:
        d = dt.datetime.fromisoformat(t)
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)


def _text(el, path: str) -> str:
    found = el.find(path)
    return " ".join((found.text or "").split()) if found is not None and found.text else ""


def parse(body: bytes) -> Dict:
    """Parse one sitemap file: {"kind": "urlset", "entries": [...], "skipped": n} with entries in the
    shape of a feedparser item, {"kind": "index", "children": [(url, lastmod or None), ...]}, or
    {"kind": "error", "error": reason}. An <url> without a <news:news> title is skipped: the gate has
    nothing else to read."""
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
    try:
        root = etree.fromstring(body, parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        return {"kind": "error", "error": "parse_error: %s" % type(exc).__name__}
    tag = etree.QName(root).localname if isinstance(root.tag, str) else ""
    sm, news = "{%s}" % SITEMAP_NS, "{%s}" % NEWS_NS
    if tag == "sitemapindex":
        children = []
        for s in root.iter(sm + "sitemap"):
            loc = _text(s, sm + "loc")
            if loc.startswith("http"):
                children.append((loc, _w3c_time(_text(s, sm + "lastmod"))))
        return {"kind": "index", "children": children}
    if tag != "urlset":
        return {"kind": "error", "error": "not_a_sitemap"}
    entries, skipped = [], 0
    for u in root.iter(sm + "url"):
        if len(entries) >= MAX_ENTRIES_PER_FILE:
            break
        loc = _text(u, sm + "loc")
        n = u.find(news + "news")
        title = _text(n, news + "title") if n is not None else ""
        if not loc.startswith("http") or not title:
            skipped += 1
            continue
        pub = _w3c_time(_text(n, news + "publication_date"))
        entries.append({"link": loc, "title": title[:500], "summary": "",
                        "published": pub.isoformat() if pub else None,
                        "published_parsed": pub.utctimetuple() if pub else None})
    return {"kind": "urlset", "entries": entries, "skipped": skipped}


def _recent(entries: List[Dict], now: dt.datetime) -> List[Dict]:
    """Entries no older than MAX_AGE_HOURS; undated ones are kept and count on the day they are seen."""
    cutoff = now - dt.timedelta(hours=MAX_AGE_HOURS)
    out = []
    for e in entries:
        p = e.get("published_parsed")
        if p is not None and dt.datetime.fromtimestamp(calendar.timegm(p), dt.timezone.utc) < cutoff:
            continue
        out.append(e)
    return out


def fetch_entries(url: str, now: Optional[dt.datetime] = None) -> Dict:
    """Read a news sitemap (or an index of them): {ok, entries, status, error, elapsed, files, backfill},
    like feeds_util.fetch_feed. Entries are unique by link, newest first.

    backfill: the read reaches back up to two days, so an item first seen more than
    config.BACKFILL_LAG_HOURS after it was published (the first read of a sitemap) counts on its
    publication day, not the day of the read (fetch_feeds, store.backfill_day)."""
    started = time.time()
    now = now or dt.datetime.now(dt.timezone.utc)

    def done(ok, entries, status, error, files):
        return {"ok": ok, "entries": entries, "status": status, "error": error,
                "elapsed": time.time() - started, "files": files, "backfill": True}

    body, status, err = get_xml(url)
    if body is None:
        return done(False, [], status, err, 1)
    top = parse(body)
    files = 1
    if top["kind"] == "error":
        return done(False, [], status, top["error"], files)
    if top["kind"] == "index":
        oldest = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        children = sorted(top["children"], key=lambda c: c[1] or oldest, reverse=True)[:MAX_CHILD_SITEMAPS]
        entries, errors = [], []
        for child, _ in children:
            cbody, cstatus, cerr = get_xml(child)
            files += 1
            if cbody is None:
                errors.append(cerr)
                continue
            sub = parse(cbody)
            if sub["kind"] != "urlset":      # one level only: an index inside an index is not followed
                errors.append(sub.get("error") or "nested_index")
                continue
            entries.extend(sub["entries"])
        if not children:
            return done(False, [], status, "empty_index", files)
        if not entries and errors:
            return done(False, [], status, errors[0], files)
    else:
        entries = top["entries"]
    seen, unique = set(), []
    for e in _recent(entries, now):
        if e["link"] not in seen:
            seen.add(e["link"])
            unique.append(e)
    unique.sort(key=lambda e: e["published"] or "", reverse=True)
    if not unique:
        return done(False, [], status, "no_entries", files)
    return done(True, unique, status, None, files)


# How the collector uses a sitemap read beside the outlet's RSS (fetch_feeds.run).
#
# Known links are skipped before the keyword gate: a sitemap repeats up to two days of items on every
# poll, and gating 1,000 titles at about 4 ms each, to find them known, would cost seconds a run.
#
# The same article under two hosts: Telegraf's RSS links some politics stories at www.telegraf.rs and
# its sitemap at politika.telegraf.rs, same path and title (4 of the 170 RSS items in one sitemap's
# window). Both would be inserted and counted. A new sitemap item whose path and title (case and
# spacing aside) match an item of the same outlet discovered in the last DUPLICATE_TITLE_HOURS at
# another address is taken as the same article and not inserted again. The title alone is not
# enough: PR Newswire issues some releases twice under two release numbers and Clarin runs a story
# and its video under one headline (8 and 6 such pairs in the saved sitemaps), and the RSS counts
# both, so the sitemap does too.
#
# Saturation. A saturated RSS poll lost the items published between polls that it no longer carried.
# When the outlet's sitemap was read in the same run and reached back to the previous poll, it listed
# those items too, except for the part of the stream it does not carry (Telegraf's sitemap leaves out
# its ona. and biznis. subdomains, which its RSS carries; PR Newswire's left out 5 of 36 releases in
# one window). The loss is scaled by that part: the share of the RSS poll's links missing from the
# sitemap. A poll fully covered is recorded as not saturated, with nothing missed.
DUPLICATE_TITLE_HOURS = 96


def title_key(title: Optional[str], link: str) -> Optional[Tuple[str, str]]:
    """(title, path and query) with case and spacing set aside; None without a title."""
    from urllib.parse import urlsplit
    t = " ".join((title or "").lower().split())
    if not t:
        return None
    parts = urlsplit(link or "")
    return t, parts.path.rstrip("/").lower() + ("?" + parts.query if parts.query else "")


def known_titles(conn, outlet_id: str, now: Optional[dt.datetime] = None) -> Dict[Tuple[str, str], str]:
    """title_key -> url_hash for the outlet's items discovered in the last DUPLICATE_TITLE_HOURS."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(hours=DUPLICATE_TITLE_HOURS)).isoformat()
    out = {}
    for h, t, u in conn.execute("SELECT url_hash, title, url FROM articles WHERE outlet_id=? AND discovered_at>=? "
                                "AND title IS NOT NULL ORDER BY id", (outlet_id, since)):
        k = title_key(t, u)
        if k and k not in out:
            out[k] = h
    return out


def duplicate_title(titles: Optional[Dict], title: str, link: str) -> bool:
    """Whether a new link has the path and title of an item of the outlet already on record at
    another address. Records the link when it is new, so the check holds within one poll too."""
    if titles is None:
        return False
    from pipeline import store
    k, h = title_key(title, link), store.url_hash(link)
    if not k:
        return False
    if k in titles:
        return titles[k] != h
    titles[k] = h
    return False


def known_hashes(conn, links: List[str]) -> set:
    """The url_hashes among these links already on record."""
    from pipeline import store
    hashes = list({store.url_hash(u) for u in links if u})
    known = set()
    for i in range(0, len(hashes), 500):
        part = hashes[i:i + 500]
        known.update(r[0] for r in conn.execute(
            "SELECT url_hash FROM articles WHERE url_hash IN (%s)" % ",".join("?" * len(part)), part))
    return known


def cover(results: List[Dict]) -> Optional[Dict]:
    """What the outlet's sitemap reads in this run listed: {"oldest": UTC datetime, "hashes": set}, or
    None when no sitemap was read successfully. Each result is a fetch_entries return value."""
    from pipeline import store
    ok = [r for r in results if r and r.get("ok")]
    if not ok:
        return None
    hashes, stamps = set(), []
    for r in ok:
        for e in r["entries"]:
            hashes.add(store.url_hash(e["link"]))
            if e.get("published"):
                stamps.append(dt.datetime.fromisoformat(e["published"]))
    return {"oldest": min(stamps) if stamps else None, "hashes": hashes}


def covered_saturation(prev, saturated: bool, missed: float, links: List[str],
                       covered: Optional[Dict]) -> Tuple[bool, float, bool]:
    """(saturated, missed, scaled) for an editorial RSS poll, given the outlet's sitemap cover for the
    run (see the note above DUPLICATE_TITLE_HOURS). Unchanged unless the poll was saturated and the
    sitemap's oldest entry reaches back to the previous successful poll."""
    if not saturated or not covered or covered["oldest"] is None or not links or prev is None or not prev["last_ok"]:
        return saturated, missed, False
    from pipeline import store
    try:
        last_ok = dt.datetime.fromisoformat(prev["last_ok"])
    except (TypeError, ValueError):
        return saturated, missed, False
    last_ok = last_ok if last_ok.tzinfo else last_ok.replace(tzinfo=dt.timezone.utc)
    if covered["oldest"] > last_ok:
        return saturated, missed, False
    left = sum(1 for u in links if store.url_hash(u) not in covered["hashes"]) / float(len(links))
    return left > 0, round((missed or 0.0) * left, 2), True
