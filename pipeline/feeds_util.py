"""Shared feed fetching. One place for the user agent, timeouts and parsing."""
import time
from typing import Dict, Optional

import feedparser
import requests

from pipeline import config


def fetch_feed(url: str, timeout: int = config.FEED_TIMEOUT, respect_robots: bool = True) -> Dict:
    """Fetch and parse a feed. Returns a dict with keys ok, entries, status, error, elapsed.

    Feed polling goes through the same robots.txt check and the same per domain rate limiter as
    article fetching. pipeline.fetch_articles calls both non negotiable and the published
    methodology says the crawler honours robots.txt and makes at most one request per domain every
    three seconds; a feed request is a request like any other. It matters most here: feed hosts are
    shared (feeds.bbci.co.uk serves six outlets in the registry) and sixteen workers poll them at
    once, so the unlimited requests.get was the one place the promise was broken."""
    # Imported here: fetch_articles pulls in the extractor, which feed polling does not need.
    from pipeline.fetch_articles import _rate_wait, can_fetch, domain_of
    started = time.time()
    if respect_robots:
        allowed, why = can_fetch(url)
        if not allowed:
            return {"ok": False, "entries": [], "status": None, "error": why, "elapsed": time.time() - started}
    _rate_wait(domain_of(url))
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        return {"ok": False, "entries": [], "status": None, "error": type(exc).__name__, "elapsed": time.time() - started}
    if resp.status_code >= 400:
        return {"ok": False, "entries": [], "status": resp.status_code, "error": "http_%d" % resp.status_code, "elapsed": time.time() - started}
    parsed = feedparser.parse(resp.content)
    entries = parsed.get("entries", []) or []
    if not entries:
        err = "no_entries"
        if parsed.get("bozo") and parsed.get("bozo_exception"):
            err = "parse_error: %s" % type(parsed["bozo_exception"]).__name__
        return {"ok": False, "entries": [], "status": resp.status_code, "error": err, "elapsed": time.time() - started}
    return {"ok": True, "entries": entries, "status": resp.status_code, "error": None, "elapsed": time.time() - started, "feed": parsed.get("feed", {})}
