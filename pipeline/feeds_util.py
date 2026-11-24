"""Shared feed fetching. One place for the user agent, timeouts and parsing."""
import re
import time
from typing import Dict, Optional
from urllib.parse import urljoin, urlsplit

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


# SRNA of Bosnia lists every item of srna.rs/rss as /news/<number>, an address its own site answers with 404:
# the nine pages fetched after the links were first resolved (2026-11-18) all failed that way. The site serves
# the story at /en/novost/<number>/<any words>, where the words are the headline in its own links and are not
# checked (checked 2026-11-19: the page answers 200 under any words and 404 under none). The link is rewritten
# to that address with the one word "news", so an item keeps one address when its headline is edited.
_SRNA_ITEM_RE = re.compile(r"^https?://(?:www\.)?srna\.rs/news/(\d+)/?$")
_SRNA_PAGE = "https://www.srna.rs/en/novost/%s/news"


def _repair_links(entries) -> None:
    """Rewrite an item link the publisher's own site does not answer to the address it serves the item at."""
    for entry in entries:
        try:
            link = entry.get("link")
            m = _SRNA_ITEM_RE.match(link.strip()) if isinstance(link, str) else None
            if m:
                entry["link"] = _SRNA_PAGE % m.group(1)
        except Exception:  # noqa: BLE001  an entry that is not a mapping is discovery's to skip
            continue


# A link whose last path segment is the word "undefined" is a script's unset value printed into an address,
# not a page. Libero's old rss.xml listed, beside items such as the "pillole" videos at
# /news/pillole/<id>/<slug>/, one to five more entries under the same headline at /news/<another id>/undefined/,
# with no date: 129 such rows under 88 headlines were stored from 2026-09-03 to 2026-11-12, and every one
# fetched (108) failed with 404. Rechecked 2026-11-24: that address and the pillole one both answer 404 since
# the site moved to a new system on 2026-11-12 (rss.xml answers 404 too), and the ids differ from the pillole
# ids (49146852 beside 49146469), so no rewrite reaches a page. Such an entry is dropped before discovery: the
# item it repeats is stored from its own entry, and a broken address is not an item.
BROKEN_SEGMENTS = ("undefined",)


def broken_link(link) -> bool:
    """Whether an item link ends in a template placeholder segment (BROKEN_SEGMENTS)."""
    if not isinstance(link, str):
        return False
    try:
        path = urlsplit(link.strip()).path
    except ValueError:
        return False
    segments = [s for s in path.split("/") if s]
    return bool(segments) and segments[-1] in BROKEN_SEGMENTS


def _drop_broken(entries) -> list:
    out = []
    for entry in entries:
        try:
            if broken_link(entry.get("link")):
                continue
        except Exception:  # noqa: BLE001  an entry that is not a mapping is discovery's to skip
            pass
        out.append(entry)
    return out


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
    _repair_links(entries)
    entries = _drop_broken(entries)
    if not entries:
        return {"ok": False, "entries": [], "status": resp.status_code, "error": "no_entries: every item link broken",
                "elapsed": time.time() - started}
    return {"ok": True, "entries": entries, "status": resp.status_code, "error": None, "elapsed": time.time() - started, "feed": parsed.get("feed", {})}
