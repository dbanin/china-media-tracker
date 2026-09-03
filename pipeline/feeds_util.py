"""Shared feed fetching. One place for the user agent, timeouts and parsing."""
import time
from typing import Dict, Optional

import feedparser
import requests

from pipeline import config


def fetch_feed(url: str, timeout: int = config.FEED_TIMEOUT) -> Dict:
    """Fetch and parse a feed. Returns a dict with keys ok, entries, status, error, elapsed."""
    started = time.time()
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
