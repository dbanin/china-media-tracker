"""Discovery. Poll every active feed, store every item immediately, gate on relevance,
link near-duplicate titles within a country, and record for every poll whether the
feed's window reached back to the previous poll.

An outlet is polled on its editorial feeds and on the press release, sponsored and
partner sections found by pipeline/discover_sections.py (registry.feed_entries). Items
from those sections skip the relevance gate, because that is where paid state placements
sit and they often name no China term in the headline; classification decides.
"""
import datetime as dt
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from pipeline import extract, gate, registry, store
from pipeline.feeds_util import fetch_feed

TITLE_JACCARD_THRESHOLD = 0.7
# One poll's estimate of items lost between polls is capped, so a feed with nonsense dates cannot
# dominate a country's total.
MAX_MISSED_ESTIMATE = 500.0


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


def _aware(t: str) -> Optional[dt.datetime]:
    try:
        d = dt.datetime.fromisoformat(t)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def saturation(prev, linked: int, new_here: int, times: List[str],
               now: Optional[dt.datetime] = None) -> Tuple[bool, float]:
    """Whether a poll came back as a full window with nothing seen before, and an estimate of
    the items lost between this poll and the previous successful one.

    A feed carries only its most recent entries. When every entry of a poll is new, the window
    did not reach back to the previous successful poll, so whatever was published in between
    was never seen; a skipped run costs a busy feed more than a quiet one. The estimate is the
    feed's own publishing rate across the returned window times the stretch between the previous
    successful poll and the oldest entry returned. The first successful poll of a feed has
    nothing to overlap with and is never saturated. A window with fewer than two dated entries is
    saturated with no estimate."""
    if prev is None or not prev["last_ok"] or linked == 0 or new_here < linked:
        return False, 0.0
    last_ok = _aware(prev["last_ok"])
    now = now or dt.datetime.now(dt.timezone.utc)
    stamps = sorted(min(s, now) for s in (_aware(t) for t in times) if s is not None)
    if last_ok is None or len(stamps) < 2:
        return True, 0.0
    span = (stamps[-1] - stamps[0]).total_seconds()
    gap = (stamps[0] - last_ok).total_seconds()
    if span <= 0 or gap <= 0:
        return True, 0.0
    return True, round(min(MAX_MISSED_ESTIMATE, (len(stamps) - 1) / span * gap), 2)


def poll_outlet(outlet: Dict) -> List[Dict]:
    out = []
    for fe in registry.feed_entries(outlet):
        if fe.get("type", "rss") == "rss":
            r = fetch_feed(fe["url"])
        else:
            from pipeline import section_pages  # section index pages without a feed
            r = section_pages.fetch_entries(fe["url"])
        out.append({"outlet": outlet, "feed_url": fe["url"], "kind": fe.get("kind", registry.EDITORIAL), "result": r})
    return out


def run(conn, run_id: str, outlets: Optional[List[Dict]] = None, workers: int = 16,
        deadline: Optional[float] = None) -> Dict:
    log_id = store.start_stage(conn, run_id, "discover")
    if outlets is None:
        outlets = registry.collectable(registry.load_outlets())
    store.sync_outlets(conn, registry.load_outlets())
    counts = {"feeds": 0, "feeds_ok": 0, "items_seen": 0, "items_new": 0,
              "gate_relevant": 0, "near_duplicates": 0, "feeds_saturated": 0, "section_items": 0}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for group in pool.map(poll_outlet, outlets):
            for g in group:
                outlet, feed_url, kind, r = g["outlet"], g["feed_url"], g["kind"], g["result"]
                counts["feeds"] += 1
                prev = store.feed_health_row(conn, feed_url)
                store.record_feed_health(conn, outlet["id"], feed_url, r["ok"], r["error"], len(r["entries"]))
                if not r["ok"]:
                    continue
                counts["feeds_ok"] += 1
                section = kind in registry.GATE_EXEMPT_KINDS
                linked, new_here, times = 0, 0, []
                for entry in r["entries"]:
                    link = entry.get("link")
                    if not link or not link.startswith("http"):
                        continue
                    linked += 1
                    counts["items_seen"] += 1
                    title = (entry.get("title") or "").strip()
                    summary = _entry_summary(entry)
                    if section:
                        relevant, terms = True, ["section:%s" % kind]
                    else:
                        relevant, terms = gate.check(title, summary, outlet["language"], outlet["country"])
                    item = {
                        "url": link, "outlet_id": outlet["id"], "country": outlet["country"],
                        "language": outlet["language"], "feed_url": feed_url, "title": title,
                        "summary": gate.strip_html(summary)[:2000], "author": extract.clean_author(_entry_author(entry)),
                        "published_at": _entry_time(entry),
                        "status": "queued" if relevant else "gated_out",
                        "gate_relevant": 1 if relevant else 0, "gate_terms": terms,
                    }
                    if item["published_at"]:
                        times.append(item["published_at"])
                    new_id = store.insert_discovered(conn, item)
                    if new_id is None:
                        continue
                    new_here += 1
                    counts["items_new"] += 1
                    store.record_discovery(conn, outlet["country"], relevant, outlet["id"])
                    if section:
                        counts["section_items"] += 1
                    if relevant:
                        counts["gate_relevant"] += 1
                        if link_near_duplicates(conn, new_id, title, outlet["country"]):
                            counts["near_duplicates"] += 1
                saturated, missed = saturation(prev, linked, new_here, times)
                counts["feeds_saturated"] += 1 if saturated else 0
                store.record_feed_poll(conn, outlet["id"], outlet["country"], feed_url, linked, new_here, saturated, missed)
                conn.commit()
            if deadline and time.time() > deadline:
                break
    store.finish_stage(conn, log_id, True, counts)
    return counts
