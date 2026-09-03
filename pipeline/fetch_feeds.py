"""Discovery. Poll every active feed, store every item immediately, gate on relevance,
and link near-duplicate titles within a country."""
import datetime as dt
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from pipeline import config, gate, registry, store
from pipeline.feeds_util import fetch_feed

TITLE_JACCARD_THRESHOLD = 0.7


def _entry_time(entry) -> Optional[str]:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        val = entry.get(key)
        if val:
            try:
                return dt.datetime(*val[:6], tzinfo=dt.timezone.utc).isoformat()
            except Exception:
                continue
    return None


def _entry_summary(entry) -> str:
    if entry.get("summary"):
        return entry["summary"]
    content = entry.get("content") or []
    if content and isinstance(content, list) and content[0].get("value"):
        return content[0]["value"]
    return ""


def _entry_author(entry) -> Optional[str]:
    if entry.get("author"):
        return entry["author"]
    if entry.get("dc_creator"):
        return entry["dc_creator"]
    return None


_norm_re = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_title(title: str) -> List[str]:
    t = _norm_re.sub(" ", (title or "").lower())
    toks = [w for w in t.split() if len(w) > 2]
    return toks


def jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))


def link_near_duplicates(conn, article_id: int, title: str, country: str) -> Optional[int]:
    """If a recent article in the same country has a near-identical title, link them.
    Both keep counting as placements. The dup_group_id names the underlying item."""
    toks = normalize_title(title)
    if len(toks) < 4:
        return None
    for row in store.recent_titles(conn, country):
        if row["id"] == article_id:
            continue
        if jaccard(toks, normalize_title(row["title"])) >= TITLE_JACCARD_THRESHOLD:
            group = row["dup_group_id"] or row["id"]
            store.set_dup_group(conn, article_id, group)
            if row["dup_group_id"] is None:
                store.set_dup_group(conn, row["id"], group)
            return group
    return None


def poll_outlet(outlet: Dict) -> List[Dict]:
    out = []
    for url in outlet["feeds"]:
        r = fetch_feed(url)
        out.append({"outlet": outlet, "feed_url": url, "result": r})
    return out


def run(conn, run_id: str, outlets: Optional[List[Dict]] = None, workers: int = 8,
        deadline: Optional[float] = None) -> Dict:
    log_id = store.start_stage(conn, run_id, "discover")
    if outlets is None:
        outlets = registry.active_outlets(registry.load_outlets())
    store.sync_outlets(conn, registry.load_outlets())
    counts = {"feeds": 0, "feeds_ok": 0, "items_seen": 0, "items_new": 0,
              "gate_relevant": 0, "near_duplicates": 0}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for group in pool.map(poll_outlet, outlets):
            for g in group:
                outlet, feed_url, r = g["outlet"], g["feed_url"], g["result"]
                counts["feeds"] += 1
                store.record_feed_health(conn, outlet["id"], feed_url, r["ok"], r["error"], len(r["entries"]))
                if not r["ok"]:
                    continue
                counts["feeds_ok"] += 1
                for entry in r["entries"]:
                    link = entry.get("link")
                    if not link or not link.startswith("http"):
                        continue
                    counts["items_seen"] += 1
                    title = (entry.get("title") or "").strip()
                    summary = _entry_summary(entry)
                    relevant, terms = gate.check(title, summary, outlet["language"])
                    item = {
                        "url": link, "outlet_id": outlet["id"], "country": outlet["country"],
                        "language": outlet["language"], "feed_url": feed_url, "title": title,
                        "summary": gate.strip_html(summary)[:2000], "author": _entry_author(entry),
                        "published_at": _entry_time(entry),
                        "status": "queued" if relevant else "gated_out",
                        "gate_relevant": 1 if relevant else 0, "gate_terms": terms,
                    }
                    new_id = store.insert_discovered(conn, item)
                    if new_id is None:
                        continue
                    counts["items_new"] += 1
                    if relevant:
                        counts["gate_relevant"] += 1
                        if link_near_duplicates(conn, new_id, title, outlet["country"]):
                            counts["near_duplicates"] += 1
                conn.commit()
            if deadline and time.time() > deadline:
                break
    store.finish_stage(conn, log_id, True, counts)
    return counts
