"""Full text retrieval.

Non negotiable constraints, all enforced here:
  - robots.txt honored per domain, cached.
  - at most one request per domain every three seconds, with jitter.
  - identifying user agent with contact.
  - paywalled pages recorded as paywalled and left alone.
  - two retries with exponential backoff, then failed with the reason.
"""
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional
from urllib import robotparser
from urllib.parse import urlsplit

import requests

from pipeline import config, extract, store

_robots_cache: Dict[str, Optional[robotparser.RobotFileParser]] = {}
_robots_lock = threading.Lock()
_last_request: Dict[str, float] = {}
_rate_lock = threading.Lock()


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
            rp = robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
            result = ("ok", rp)
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
    agent_token = config.PROJECT_NAME
    if rp.can_fetch(agent_token, url) and rp.can_fetch("*", url):
        return True, "robots_allowed"
    return False, "robots_disallowed"


def _rate_wait(domain: str) -> None:
    with _rate_lock:
        last = _last_request.get(domain, 0.0)
        wait = config.MIN_SECONDS_PER_DOMAIN + random.uniform(0, config.JITTER_SECONDS) - (time.time() - last)
        # reserve the slot before sleeping so other threads on the same domain queue behind it
        _last_request[domain] = max(time.time(), last) + max(wait, 0) if wait > 0 else time.time()
    if wait > 0:
        time.sleep(wait)


def get_page(url: str):
    """Fetch with retries. Returns (html, http_status, error)."""
    domain = domain_of(url)
    delay = 2.0
    last_err = None
    status = None
    for attempt in range(config.MAX_RETRIES + 1):
        _rate_wait(domain)
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": config.USER_AGENT,
                         "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                         "Accept-Language": "*"},
                timeout=config.FETCH_TIMEOUT, allow_redirects=True,
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
            else:
                return None, status, "http_%d" % resp.status_code
        except requests.RequestException as exc:
            last_err = type(exc).__name__
        if attempt < config.MAX_RETRIES:
            time.sleep(delay + random.uniform(0, 1))
            delay *= 2
    return None, status, last_err or "unknown"


def process_article(conn_factory, row) -> Dict:
    """Fetch, extract, detect paywall and persist one article. Returns a summary dict."""
    url = row["url"]
    article_id = row["id"]
    out = {"id": article_id, "status": None, "reason": None}
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
        ex = extract.extract(html, url)
        text = ex["text"]
        paywalled, pw_reason = extract.detect_paywall(html, text)
        raw_path = store.save_raw_html(row["url_hash"], html)
        fields = dict(http_status=http_status, fetched_at=store.utcnow(), raw_html_path=raw_path,
                      fetch_attempts=row["fetch_attempts"] + 1)
        if ex["author"] and not row["author"]:
            fields["author"] = ex["author"]
        elif not row["author"]:
            a = extract.jsonld_authors(html)
            if a:
                fields["author"] = a
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
        else:
            fields.update(status="fetched", body_hash=store.save_body(row["url_hash"], text),
                          body_chars=len(text), fail_reason=None)
            out.update(status="fetched")
        store.update_article(conn, article_id, **fields)
        conn.commit()
        return out
    finally:
        conn.close()


def run(conn, run_id: str, deadline: Optional[float] = None, limit: Optional[int] = None,
        workers: int = 24, status: str = "queued") -> Dict:
    log_id = store.start_stage(conn, run_id, "fetch")
    rows = store.articles_by_status(conn, status, limit=limit or 100000)
    # Group by domain so each domain is served sequentially by one worker while domains run in parallel.
    by_domain: Dict[str, List] = {}
    for r in rows:
        by_domain.setdefault(domain_of(r["url"]), []).append(r)
    counts = {"queued": len(rows), "domains": len(by_domain), "fetched": 0, "paywalled": 0,
              "failed": 0, "blocked_robots": 0, "skipped_deadline": 0}
    lock = threading.Lock()

    def worker(items):
        for r in items:
            if deadline and time.time() > deadline:
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

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(worker, by_domain.values()))
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
