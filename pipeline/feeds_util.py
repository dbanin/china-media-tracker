"""Shared feed fetching. One place for the user agent, timeouts and parsing."""
import time
from typing import Dict, Optional
from urllib.parse import urljoin

import feedparser
import requests

from pipeline import config


def _resolve_links(entries, base: str) -> None:
    """Make an item link that is a path alone (/news/1449484, //host/item) a full address, against the
    address that answered. feedparser is given the feed's bytes and not its address, so it leaves such a link
    as it is, and discovery keeps only links that start with http: every item of a feed that lists its items
    this way was dropped on every poll (srna.rs/rss and workpointtoday.com/feed/, polled from September to
    2026-11-18 without one item). Only a link that starts with a slash is resolved; anything else that is not
    an address (a bare host name, mailto:, javascript:) is left for discovery to drop, as before."""
    if not isinstance(base, str) or not base.startswith("http"):
        return
    for entry in entries:
        try:
            link = entry.get("link")
            if isinstance(link, str) and link.strip().startswith("/"):
                entry["link"] = urljoin(base, link.strip())
        except Exception:  # noqa: BLE001  an entry that is not a mapping is discovery's to skip
            continue


def fetch_feed(url: str, timeout: int = config.FEED_TIMEOUT, respect_robots: bool = True) -> Dict:
    """Fetch and parse a feed. Returns a dict with keys ok, entries, status, error, elapsed.

    Feed polling goes through the same robots.txt check and the same per domain rate limiter as
    article fetching. pipeline.fetch_articles calls both non negotiable and the published
    methodology says the crawler honours robots.txt and makes at most one request per domain every
    three seconds; a feed request is a request like any other. It matters most here: feed hosts are
    shared (feeds.bbci.co.uk serves six outlets in the registry) and sixteen workers poll them at
    once, so the unlimited requests.get was the one place the promise was broken.

    Redirects go through fetch_articles.polite_get, so a feed that redirects is fetched only where
    robots.txt allows it, and a Retry-After on a 429 or 503 holds the host (fetch_articles.note_retry_after)
    so no other feed on it is polled before the time it asked for."""
    # Imported here: fetch_articles pulls in the extractor, which feed polling does not need.
    from pipeline.fetch_articles import (RedirectRefused, _rate_wait, can_fetch, domain_of, held_reason,
                                         note_retry_after, polite_get)
    started = time.time()
    if respect_robots:
        allowed, why = can_fetch(url)
        if not allowed:
            return {"ok": False, "entries": [], "status": None, "error": why, "elapsed": time.time() - started}
    domain = domain_of(url)
    held = held_reason(domain)
    if held:
        return {"ok": False, "entries": [], "status": None, "error": held, "elapsed": time.time() - started}
    _rate_wait(domain)
    try:
        resp = polite_get(
            url,
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
            timeout=timeout,
        )
    except RedirectRefused as exc:
        return {"ok": False, "entries": [], "status": None, "error": exc.reason, "elapsed": time.time() - started}
    except requests.RequestException as exc:
        return {"ok": False, "entries": [], "status": None, "error": type(exc).__name__, "elapsed": time.time() - started}
    if resp.status_code in (429, 503):
        note_retry_after(domain, resp)
    if resp.status_code >= 400:
        return {"ok": False, "entries": [], "status": resp.status_code, "error": "http_%d" % resp.status_code, "elapsed": time.time() - started}
    try:
        parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001  feedparser itself raises on some hostile bytes (&#xD800;)
        # One feed the parser cannot read is a failed poll of that feed, never the loss of its outlet's
        # other feeds (fetch_feeds.poll_outlet polls them in one task).
        return {"ok": False, "entries": [], "status": resp.status_code, "error": "parse_error: %s" % type(exc).__name__,
                "elapsed": time.time() - started}
    entries = parsed.get("entries", []) or []
    if not entries:
        err = "no_entries"
        if parsed.get("bozo") and parsed.get("bozo_exception"):
            err = "parse_error: %s" % type(parsed["bozo_exception"]).__name__
        return {"ok": False, "entries": [], "status": resp.status_code, "error": err, "elapsed": time.time() - started}
    _resolve_links(entries, getattr(resp, "url", None) or url)
    return {"ok": True, "entries": entries, "status": resp.status_code, "error": None, "elapsed": time.time() - started, "feed": parsed.get("feed", {})}
