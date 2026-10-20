"""Full text retrieval.

Non negotiable constraints, all enforced here:
  - robots.txt honored per domain, cached.
  - at most one request per domain every three seconds, with jitter, or slower where the domain's
    robots.txt sets a longer Crawl-delay (capped at config.CRAWL_DELAY_CAP_SECONDS).
  - identifying user agent with contact.
  - paywalled pages recorded as paywalled and left alone.
  - two retries with exponential backoff, then failed with the reason.
"""
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests

from pipeline import config, extract, robots, store

_robots_cache: Dict[str, tuple] = {}
_robots_lock = threading.Lock()
_last_request: Dict[str, float] = {}
_rate_lock = threading.Lock()
# The worker permit the current thread holds, if it runs under paced(): given back while the thread
# sleeps through a long Crawl-delay, so a sleeping thread does not keep a working one out.
_permit = threading.local()
# HTML parsing runs under one lock. Network waits dominate this stage, so parallelism is
# kept for the requests, but lxml under many threads produced heap corruption on the
# GitHub runner (glibc "corrupted size vs. prev_size", exit 134). Parsing is cheap.
_extract_lock = threading.Lock()

# Failures worth another attempt on a later run. Everything else is final.
TRANSIENT_FAIL_PREFIXES = ("http_429", "http_5", "ConnectionError", "ReadTimeout", "ConnectTimeout",
                           "Timeout", "ChunkedEncodingError", "SSLError", "robots_unavailable",
                           "exception:", "unknown", "refetch_")
RETRY_ATTEMPT_LIMIT = 3
RETRY_WINDOW_DAYS = 7


def domain_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def robots_for(domain: str, scheme: str = "https"):
    """Return ('ok', parser) | ('allow_all', None) | ('unavailable', None)."""
    with _robots_lock:
        if domain in _robots_cache:
            return _robots_cache[domain]
    url = "%s://%s/robots.txt" % (scheme, domain)
    result = ("allow_all", None)
    try:
        _rate_wait(domain)
        resp = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=config.FETCH_TIMEOUT)
        if resp.status_code == 200:
            result = ("ok", robots.parse(resp.text.splitlines()))
            asked = result[1].crawl_delay(config.PROJECT_NAME)
            if asked is not None and asked > config.CRAWL_DELAY_CAP_SECONDS:
                print("robots: %s asks Crawl-delay %gs, paced at the %gs cap" % (
                    domain, asked, config.CRAWL_DELAY_CAP_SECONDS), file=sys.stderr)
        elif 400 <= resp.status_code < 500:
            result = ("allow_all", None)
        else:
            result = ("unavailable", None)
    except requests.RequestException:
        result = ("unavailable", None)
    with _robots_lock:
        _robots_cache[domain] = result
    return result


def can_fetch(url: str):
    """Return (allowed: bool, reason: str)."""
    parts = urlsplit(url)
    state, rp = robots_for(parts.netloc.lower(), parts.scheme or "https")
    if state == "unavailable":
        # Treat an unreachable robots.txt as a temporary full disallow, as crawlers conventionally do.
        return False, "robots_unavailable"
    if state == "allow_all" or rp is None:
        return True, "no_robots"
    # RFC 9309: the group naming this crawler governs, or the "*" group when none does, and within
    # it the most specific matching rule wins (see pipeline/robots.py).
    if rp.can_fetch(config.PROJECT_NAME, url):
        return True, "robots_allowed"
    return False, "robots_disallowed"


def domain_gap(domain: str) -> float:
    """Seconds between two requests to this domain, before jitter: the larger of
    config.MIN_SECONDS_PER_DOMAIN and the Crawl-delay its robots.txt sets for this crawler (or for
    "*" when no group names it), capped at config.CRAWL_DELAY_CAP_SECONDS.

    Reads the cached robots record only and never fetches: every request path checks robots.txt
    (can_fetch) before it paces, so the record is there by the time it matters, and the robots.txt
    request itself is paced at the minimum because nothing is known about the domain yet."""
    with _robots_lock:
        state, rp = _robots_cache.get(domain, (None, None))
    asked = rp.crawl_delay(config.PROJECT_NAME) if state == "ok" and rp is not None else None
    if asked is None:
        return config.MIN_SECONDS_PER_DOMAIN
    return max(config.MIN_SECONDS_PER_DOMAIN, min(asked, config.CRAWL_DELAY_CAP_SECONDS))


def _rate_wait(domain: str) -> None:
    """Take this domain's next slot and sleep until it comes round.

    This is the one rate limiter: article pages, feeds, section pages, section discovery and the
    relay all pace through it, so the gap (domain_gap: the minimum or the site's Crawl-delay) is
    set here and nowhere else.

    The reservation is the slot itself, not now plus the wait. The old line added the wait to the
    last slot, but the wait already contained the backlog (last - now), so every caller queueing on
    a busy domain counted the backlog a second time and the waits doubled per caller: six arrivals
    on one host slept 3, 6, 12, 24, 48 and 96 seconds instead of 3, 6, 9, 12, 15 and 18."""
    base = domain_gap(domain)
    with _rate_lock:
        now = time.time()
        gap = base + random.uniform(0, config.JITTER_SECONDS)
        slot = max(now, _last_request.get(domain, 0.0) + gap)
        _last_request[domain] = slot
        wait = slot - now
    if wait <= 0:
        return
    held = getattr(_permit, "sem", None)
    if held is None or wait <= config.MIN_SECONDS_PER_DOMAIN + config.JITTER_SECONDS:
        time.sleep(wait)
        return
    held.release()
    try:
        time.sleep(wait)
    finally:
        held.acquire()


def slot_in(domain: str) -> float:
    """Seconds until this domain's next slot would come round, without taking it (jitter left out)."""
    gap = domain_gap(domain)
    with _rate_lock:
        return max(0.0, _last_request.get(domain, 0.0) + gap - time.time())


def paced(permits: threading.Semaphore, fn):
    """fn wrapped to run holding one of permits, which _rate_wait hands back while the thread sleeps
    through a wait longer than the ordinary gap.

    A stage runs its pool with `workers` permits and SLOW_DOMAIN_THREADS more threads than that.
    Ordinary domains never wait longer than the gap, so they keep their permit and the stage behaves
    as before; a domain with a long Crawl-delay (AsiaOne asks for 60 seconds, five domains in the
    registry for ten minutes) sleeps without one, and the next domain gets the permit and goes on."""
    def run(*args, **kwargs):
        with permits:
            _permit.sem = permits
            try:
                return fn(*args, **kwargs)
            finally:
                _permit.sem = None
    return run


# Redirects are followed by hand (polite_get), so robots.txt is checked and the pace kept for every
# hop's target, not only for the URL first asked for.
MAX_REDIRECTS = 5
REDIRECT_CODES = (301, 302, 303, 307, 308)
# Retry-After on a 429 or 503 is honoured: a wait up to RETRY_AFTER_CAP_SECONDS is waited out before
# the retry; a longer one holds the whole domain for the rest of the run (at most RETRY_AFTER_HOLD_MAX
# seconds), and its articles are left for a later run, whose retry rules pick them up.
RETRY_AFTER_CODES = (429, 503)
RETRY_AFTER_CAP_SECONDS = 60.0
RETRY_AFTER_HOLD_MAX = 6 * 3600.0
_held: Dict[str, tuple] = {}


class RedirectRefused(requests.RequestException):
    """A redirect led to a URL that may not be fetched: robots.txt disallows it, its robots.txt
    cannot be read, or it is not http(s). reason is the fail reason to record."""

    def __init__(self, reason: str, url: str):
        super().__init__("%s: %s" % (reason, url))
        self.reason = reason
        self.url = url


def polite_get(url: str, headers: Dict, timeout):
    """requests.get following at most MAX_REDIRECTS redirects by hand. The first URL is the caller's
    to check (can_fetch) and pace (_rate_wait); each redirect target is checked against its own
    robots.txt and paced here before it is requested. Raises RedirectRefused for a target that may
    not be fetched and requests.TooManyRedirects past the limit (both requests.RequestException)."""
    cur = url
    for _ in range(MAX_REDIRECTS + 1):
        resp = requests.get(cur, headers=headers, timeout=timeout, allow_redirects=False)
        loc = (getattr(resp, "headers", None) or {}).get("Location")
        if getattr(resp, "status_code", None) not in REDIRECT_CODES or not loc:
            return resp
        nxt = urljoin(cur, loc.strip())
        if urlsplit(nxt).scheme not in ("http", "https"):
            raise RedirectRefused("redirect_bad_scheme", nxt)
        allowed, why = can_fetch(nxt)
        if not allowed:
            raise RedirectRefused(why if why == "robots_unavailable" else "redirect_" + why, nxt)
        close = getattr(resp, "close", None)
        if close:
            close()
        _rate_wait(domain_of(nxt))
        cur = nxt
    raise requests.TooManyRedirects("more than %d redirects from %s" % (MAX_REDIRECTS, url))


def retry_after_seconds(resp) -> Optional[float]:
    """The wait a response's Retry-After asks for, in seconds (a number or an HTTP date), or None."""
    value = ((getattr(resp, "headers", None) or {}).get("Retry-After") or "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    import datetime as _dt
    from email.utils import parsedate_to_datetime
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return max(0.0, (when - _dt.datetime.now(_dt.timezone.utc)).total_seconds())


def hold_domain(domain: str, seconds: float, code: int) -> None:
    """Make no request to this domain for seconds (capped at RETRY_AFTER_HOLD_MAX), as it asked."""
    until = time.time() + min(max(seconds, 0.0), RETRY_AFTER_HOLD_MAX)
    with _rate_lock:
        if until > _held.get(domain, (0.0, code))[0]:
            _held[domain] = (until, code)


def held_reason(domain: str) -> Optional[str]:
    """The fail reason while a domain is held after a long Retry-After ("http_429_retry_after"), or
    None. It starts like the status, so the retry rules treat it as the transient failure it is."""
    with _rate_lock:
        until, code = _held.get(domain, (0.0, 0))
    return "http_%d_retry_after" % code if until > time.time() else None


def note_retry_after(domain: str, resp) -> Optional[float]:
    """After a 429 or 503: the short wait to sleep before retrying (the domain's next slot is moved
    past it, so every caller waits), or None when there is no Retry-After. A wait longer than
    RETRY_AFTER_CAP_SECONDS holds the domain (hold_domain) and returns float("inf"): no retry now."""
    if getattr(resp, "status_code", None) not in RETRY_AFTER_CODES:
        return None
    wait = retry_after_seconds(resp)
    if wait is None:
        return None
    if wait > RETRY_AFTER_CAP_SECONDS:
        hold_domain(domain, wait, resp.status_code)
        return float("inf")
    with _rate_lock:
        _last_request[domain] = max(_last_request.get(domain, 0.0), time.time() + wait)
    return wait


def get_page(url: str):
    """Fetch with retries. Returns (html, http_status, error). Redirects are followed by polite_get
    (robots.txt and pacing for every hop) and a Retry-After on a 429 or 503 is honoured."""
    domain = domain_of(url)
    delay = 2.0
    last_err = None
    status = None
    for attempt in range(config.MAX_RETRIES + 1):
        held = held_reason(domain)
        if held:
            return None, status, held
        _rate_wait(domain)
        asked = None
        try:
            resp = polite_get(
                url,
                headers={"User-Agent": config.USER_AGENT,
                         "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                         "Accept-Language": "*"},
                timeout=config.FETCH_TIMEOUT,
            )
            status = resp.status_code
            ctype = resp.headers.get("Content-Type", "")
            if resp.status_code == 200:
                if "html" not in ctype and "xml" not in ctype and not resp.text.lstrip().lower().startswith("<"):
                    return None, status, "not_html:%s" % ctype.split(";")[0]
                if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding or "utf-8"
                return resp.text, status, None
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = "http_%d" % resp.status_code
                asked = note_retry_after(domain, resp)
                if asked == float("inf"):
                    break          # a long Retry-After: the domain is held, the article waits for a later run
            else:
                return None, status, "http_%d" % resp.status_code
        except RedirectRefused as exc:
            return None, status, exc.reason
        except requests.RequestException as exc:
            last_err = type(exc).__name__
        if domain_gap(domain) > config.MIN_SECONDS_PER_DOMAIN:
            # A site that asks for a Crawl-delay and is failing is not retried within the run: each
            # retry would wait out another delay (ten minutes at most). The reason is transient, so
            # retry_candidates picks the article up on a later run.
            break
        if attempt < config.MAX_RETRIES and asked is None:
            time.sleep(delay + random.uniform(0, 1))
            delay *= 2
        # With a short Retry-After the next slot was moved past it: _rate_wait waits it out.
    return None, status, last_err or "unknown"


# Some URLs are copies of one page: each Australian Community Media masthead gets its own row for an
# AAP release (section_pages.aap_entries), at the release's URL with a "masthead" parameter the AAP
# service ignores. FETCH_KEY_RULES maps such a URL to the key of the page it copies, (URL prefix,
# query parameter dropped from the key); a URL no rule matches has no key and is fetched on its own.
# The first row of a key is fetched; the others get its body and outcome without a request
# (copy_from_sibling), and each is still classified on its own. Only a row whose body was saved
# lends it: after a failure the next sibling fetches again, so one bad response is not copied ten times.
FETCH_KEY_RULES = (
    ("https://newsroom.aap.com.au/releases/", "masthead"),
)
# Fetch key -> id of a row whose body is saved. A run fills it once from the database
# (prime_fetch_keys) and adds every body it saves; outside a run a miss is looked up in the database.
_fetch_keys: Dict[str, int] = {}
_fetch_keys_state = {"primed": False}
_fetch_keys_lock = threading.Lock()


def fetch_key(url: str) -> Optional[str]:
    """The key of the page this URL copies, or None when it is fetched on its own."""
    for prefix, param in FETCH_KEY_RULES:
        if url.startswith(prefix):
            parts = urlsplit(url)
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != param]
            return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))
    return None


def _keyed_donors(conn, prefix: str) -> Dict[str, int]:
    """Fetch key -> id of a row under this prefix whose body is saved (one scan of the table)."""
    found: Dict[str, int] = {}
    for r in conn.execute("SELECT id, url FROM articles WHERE substr(url, 1, ?)=? AND body_hash IS NOT NULL "
                          "AND status IN ('fetched','paywalled','classified','awaiting_llm') ORDER BY id",
                          (len(prefix), prefix)):
        found.setdefault(fetch_key(r["url"]), r["id"])
    return found


def prime_fetch_keys(conn, rows) -> None:
    """Load, in one scan per rule, the saved bodies the queued keyed rows can take."""
    keys = {fetch_key(r["url"]) for r in rows} - {None}
    found: Dict[str, int] = {}
    for prefix, _param in FETCH_KEY_RULES:
        if any(k.startswith(prefix) for k in keys):
            found.update({k: v for k, v in _keyed_donors(conn, prefix).items() if k in keys})
    with _fetch_keys_lock:
        _fetch_keys.clear()
        _fetch_keys.update(found)
        _fetch_keys_state["primed"] = True


def _release_fetch_keys() -> None:
    with _fetch_keys_lock:
        _fetch_keys.clear()
        _fetch_keys_state["primed"] = False


def copy_from_sibling(conn, row, key: str) -> Optional[Dict]:
    """Give this row the saved body and fetch outcome of a sibling with the same fetch key. None when
    no sibling has a body (or its body file is gone or no longer matches its hash): fetch it then."""
    import hashlib
    with _fetch_keys_lock:
        donor_id = _fetch_keys.get(key)
        primed = _fetch_keys_state["primed"]
    if donor_id is None and not primed:
        prefix = next(p for p, _q in FETCH_KEY_RULES if key.startswith(p))
        donor_id = _keyed_donors(conn, prefix).get(key)
    if donor_id is None or donor_id == row["id"]:
        return None
    # fetchall, not fetchone: an unfinished read would hold a shared lock into the write below.
    donor = next(iter(conn.execute("SELECT * FROM articles WHERE id=?", (donor_id,)).fetchall()), None)
    text = store.load_body(donor["url_hash"]) if donor is not None and donor["body_hash"] else None
    if not text or hashlib.sha256(text.encode("utf-8")).hexdigest()[:32] != donor["body_hash"]:
        with _fetch_keys_lock:
            if _fetch_keys.get(key) == donor_id:
                del _fetch_keys[key]
        return None
    paywalled = donor["status"] == "paywalled"
    fields = dict(status="paywalled" if paywalled else "fetched", fail_reason=donor["fail_reason"] if paywalled else None,
                  http_status=donor["http_status"], fetched_at=store.utcnow(), raw_html_path=donor["raw_html_path"],
                  fetch_attempts=row["fetch_attempts"] + 1, page_labels=donor["page_labels"],
                  body_hash=store.save_body(row["url_hash"], text), body_chars=len(text))
    if donor["author"] and not row["author"]:
        fields["author"] = donor["author"]
    if donor["published_at"] and not row["published_at"]:
        fields["published_at"] = donor["published_at"]
    store.update_article(conn, row["id"], **fields)
    try:
        from pipeline import body_dups
        body_dups.record(conn, row, text)
    except Exception:  # noqa: BLE001  as in process_article: a missing fingerprint is backfilled
        pass
    conn.commit()
    return {"id": row["id"], "status": fields["status"], "reason": fields["fail_reason"],
            "text_hash": fields["body_hash"], "copied_from": donor_id}


def _lend_fetch_key(key: Optional[str], article_id: int, saved: bool) -> None:
    """After a fetch: a saved body becomes the one its siblings take."""
    if key and saved:
        with _fetch_keys_lock:
            _fetch_keys.setdefault(key, article_id)


def process_article(conn_factory, row) -> Dict:
    """Fetch, extract, detect paywall and persist one article. Returns a summary dict."""
    url = row["url"]
    article_id = row["id"]
    out = {"id": article_id, "status": None, "reason": None}
    key = fetch_key(url)
    if key:
        conn = conn_factory()
        try:
            copied = copy_from_sibling(conn, row, key)
        finally:
            conn.close()
        if copied:
            return copied
    allowed, why = can_fetch(url)
    conn = conn_factory()
    try:
        if not allowed:
            new_status = "failed" if why == "robots_unavailable" else "blocked_robots"
            store.update_article(conn, article_id, status=new_status, fail_reason=why,
                                 fetched_at=store.utcnow(), fetch_attempts=row["fetch_attempts"] + 1)
            conn.commit()
            out.update(status=new_status, reason=why)
            return out
        html, http_status, err = get_page(url)
        if html is None:
            store.update_article(conn, article_id, status="failed", fail_reason=err, http_status=http_status,
                                 fetched_at=store.utcnow(), fetch_attempts=row["fetch_attempts"] + 1)
            conn.commit()
            out.update(status="failed", reason=err)
            return out
        with _extract_lock:
            ex = extract.extract(html, url)
            text = ex["text"]
            paywalled, pw_reason = extract.detect_paywall(html, text)
            unreadable, un_reason = extract.detect_unreadable(html, text, row["title"])
            labels = extract.page_labels(html, url)
            jsonld_author = None if (ex["author"] or row["author"]) else extract.jsonld_authors(html)
            wire = extract.embedded_source(html, url)
        raw_path = store.save_raw_html(row["url_hash"], html)
        fields = dict(http_status=http_status, fetched_at=store.utcnow(), raw_html_path=raw_path,
                      fetch_attempts=row["fetch_attempts"] + 1, page_labels=labels or None)
        if ex["author"] and not row["author"]:
            fields["author"] = ex["author"]
        elif jsonld_author:
            fields["author"] = extract.clean_author(jsonld_author)
        if wire and not wire[1]:
            # A wire source the page data credits (extract.embedded_source) beside a desk byline, so the
            # author scoped credit signatures see it: "Staff Writers; Xinhua", or "Xinhua" with no byline.
            current = fields["author"] if "author" in fields else row["author"]
            credited = extract.wire_author(current, wire[0])
            if credited != current:
                fields["author"] = credited
        if ex["date"] and not row["published_at"]:
            fields["published_at"] = ex["date"]
        if paywalled:
            fields.update(status="paywalled", fail_reason=pw_reason)
            if text:
                fields["body_hash"] = store.save_body(row["url_hash"], text)
                fields["body_chars"] = len(text)
            out.update(status="paywalled", reason=pw_reason)
        elif not text or len(text) < 200:
            fields.update(status="failed", fail_reason="extraction_empty")
            out.update(status="failed", reason="extraction_empty")
        elif unreadable:
            # A challenge page or menus with no running text: not the article, so never classified.
            # The reason is final, not transient, so the article is not retried.
            fields.update(status="failed", fail_reason="unreadable: %s" % un_reason)
            out.update(status="failed", reason="unreadable: %s" % un_reason)
        else:
            fields.update(status="fetched", body_hash=store.save_body(row["url_hash"], text),
                          body_chars=len(text), fail_reason=None)
            out.update(status="fetched")
        if text:
            # The hash a saved body would carry (store.save_body), so a re-fetch can tell whether an
            # existing label was made on this very text (classify_rules.ensure_body).
            import hashlib
            out["text_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        if fields.get("body_hash"):
            # The extraction this body was made under (extract.stale_extraction).
            fields["extract_version"] = config.EXTRACT_VERSION
        store.update_article(conn, article_id, **fields)
        if fields.get("body_hash"):
            # Body fingerprint for near-duplicate linking (pipeline.body_dups), computed here where the
            # body is saved; the stage links the new fingerprints once every fetch has finished.
            try:
                from pipeline import body_dups
                body_dups.record(conn, row, text)
            except Exception:  # noqa: BLE001  a missing fingerprint is backfilled; never lose a fetch over it
                pass
        conn.commit()
        _lend_fetch_key(key, article_id, bool(fields.get("body_hash")))
        return out
    finally:
        conn.close()


def retry_candidates(conn) -> List:
    """Recently discovered articles whose last failure looks transient and that have attempts left."""
    since = (time.time() - RETRY_WINDOW_DAYS * 86400)
    import datetime as dt
    since_iso = dt.datetime.fromtimestamp(since, dt.timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT * FROM articles WHERE status='failed' AND gate_relevant=1 AND fetch_attempts<? AND discovered_at>=? ORDER BY discovered_at",
        (RETRY_ATTEMPT_LIMIT, since_iso),
    ).fetchall()
    return [r for r in rows if r["fail_reason"] and r["fail_reason"].startswith(TRANSIENT_FAIL_PREFIXES)]


def run(conn, run_id: str, deadline: Optional[float] = None, limit: Optional[int] = None,
        workers: int = 12, status: str = "queued") -> Dict:
    log_id = store.start_stage(conn, run_id, "fetch")
    rows = list(store.articles_by_status(conn, status, limit=limit or 100000))
    retries = retry_candidates(conn) if status == "queued" else []
    rows += retries
    # Only articles from outlets this collector is responsible for; the other collector's
    # articles wait for it, rather than failing here with a 403 and burning an attempt.
    from pipeline import registry
    outlets = registry.load_outlets()
    rows = [r for r in rows if registry.is_mine(r["outlet_id"], outlets)]
    retries = [r for r in retries if registry.is_mine(r["outlet_id"], outlets)]
    # Group by domain so each domain is served sequentially by one worker while domains run in parallel.
    by_domain: Dict[str, List] = {}
    for r in rows:
        by_domain.setdefault(domain_of(r["url"]), []).append(r)
    # Copies of one page (fetch_key) are moved up behind the first of them, so it is fetched once
    # and the rest take its body in turn (copy_from_sibling); every other row keeps its place.
    for items in by_domain.values():
        first: Dict[str, int] = {}
        for i, r in enumerate(items):
            first.setdefault(fetch_key(r["url"]) or r["url"], i)
        items.sort(key=lambda r: first[fetch_key(r["url"]) or r["url"]])
    prime_fetch_keys(conn, rows)
    counts = {"queued": len(rows), "retried": len(retries), "domains": len(by_domain), "fetched": 0, "paywalled": 0,
              "failed": 0, "blocked_robots": 0, "skipped_deadline": 0, "slow_domains": {}}
    lock = threading.Lock()
    # A domain whose robots.txt sets a Crawl-delay above the minimum (AsiaOne asks for 60 seconds)
    # used to hold its worker asleep for the whole run, and a handful of them would starve every
    # other domain. Under paced() the sleeping worker gives its permit to the next domain (see
    # _rate_wait). A slow domain gets what fits in the budget (about 35 fetches in 35 minutes at 60
    # seconds); the remainder stays queued, without spending an attempt, for the next run.
    permits = threading.BoundedSemaphore(workers)

    def worker(items):
        for r in items:
            gap = domain_gap(domain_of(r["url"]))
            if gap > config.MIN_SECONDS_PER_DOMAIN:
                with lock:
                    counts["slow_domains"][domain_of(r["url"])] = gap
            # On a slow domain the next slot may be a minute away: stop if it falls past the deadline.
            lead = slot_in(domain_of(r["url"])) if gap > config.MIN_SECONDS_PER_DOMAIN else 0.0
            if deadline and time.time() + lead > deadline:
                with lock:
                    counts["skipped_deadline"] += 1
                continue
            try:
                res = process_article(store.connect, r)
            except Exception as exc:  # never let one page kill the run
                c2 = store.connect()
                store.update_article(c2, r["id"], status="failed", fail_reason="exception:%s" % type(exc).__name__,
                                     fetched_at=store.utcnow(), fetch_attempts=r["fetch_attempts"] + 1)
                c2.commit(); c2.close()
                res = {"status": "failed"}
            with lock:
                counts[res["status"]] = counts.get(res["status"], 0) + 1
                if res.get("copied_from"):
                    counts["copied"] = counts.get("copied", 0) + 1

    with ThreadPoolExecutor(max_workers=workers + config.SLOW_DOMAIN_THREADS) as pool:
        list(pool.map(paced(permits, worker), by_domain.values()))
    _release_fetch_keys()
    store.finish_stage(conn, log_id, True, counts)
    return counts


def coverage_report(conn) -> List[Dict]:
    rows = conn.execute(
        """SELECT country,
                  SUM(gate_relevant) relevant,
                  SUM(CASE WHEN status IN ('fetched','classified') THEN 1 ELSE 0 END) fetched,
                  SUM(CASE WHEN status='paywalled' THEN 1 ELSE 0 END) paywalled,
                  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
                  SUM(CASE WHEN status='blocked_robots' THEN 1 ELSE 0 END) blocked,
                  SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) pending
           FROM articles WHERE gate_relevant=1 GROUP BY country ORDER BY relevant DESC"""
    ).fetchall()
    return [dict(r) for r in rows]
