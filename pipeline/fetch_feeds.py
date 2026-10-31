"""Discovery. Poll every active feed, store every item immediately, gate on relevance,
link near-duplicate titles within a country, and record for every poll whether the
feed's window reached back to the previous poll.

An outlet is polled on its editorial feeds and on the press release, sponsored and
partner sections found by pipeline/discover_sections.py (registry.feed_entries). Items
from those sections skip the relevance gate, because that is where paid state placements
sit and they often name no China term in the headline; classification decides. A section feed
that floods the queue with items classification keeps rejecting loses the exemption until it
earns it back (section_feed_flooding).
"""
import datetime as dt
import json
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Dict, List, Optional, Tuple

from pipeline import boilerplate, extract, gate, registry, store
from pipeline.feeds_util import fetch_feed

TITLE_JACCARD_THRESHOLD = 0.7
# One poll's estimate of items lost between polls is capped, so a feed with nonsense dates cannot
# dominate a country's total.
MAX_MISSED_ESTIMATE = 500.0
# Every feed_polls row records the estimator version that made it (config.MISSED_ESTIMATOR_VERSION,
# which a change of meaning here must bump), and the export reports only the current version's estimate.
# The estimate is guarded three ways (saturation, feed_history). Up to 2026-10-12 it was the window's
# own rate times the gap, and a publisher that replaces a short feed wholesale once a day made every
# batch look saturated with a window a few minutes wide: Newsmada delivered 118 new items in 30 days
# and was booked 7,953 missed, and five such feeds about 15,000 between them.
# The rate is taken over at least the median span of the feed's full windows seen over the last
# MISSED_SPAN_DAYS (never under MISSED_SPAN_MIN_HOURS), or MISSED_MIN_SPAN_HOURS when that is shorter
# or fewer than three full windows are on record, so a window stamped within minutes cannot imply
# hundreds of items an hour while a busy feed whose windows always span under an hour keeps its rate.
MISSED_MIN_SPAN_HOURS = 2.0
MISSED_SPAN_DAYS = 3
MISSED_SPAN_MIN_HOURS = 0.25
# One poll's estimate is at most MISSED_DAILY_MULTIPLE times the items the feed delivered per day
# over the trailing MISSED_HISTORY_DAYS of the feed_polls ledger (or this poll's own count when
# larger), times the gap in days when the gap is longer than a day.
MISSED_HISTORY_DAYS = 14
MISSED_DAILY_MULTIPLE = 2.0
# A batch publisher: over the trailing MISSED_HISTORY_DAYS it was polled at least
# BATCH_MIN_POLLS_PER_DAY times a day yet delivered no more than BATCH_MAX_WINDOWS_PER_DAY full
# windows a day, so its new items come in one or two bursts. A poll of such a feed whose dated entries
# are mostly (BATCH_SHARE) within BATCH_SPREAD_HOURS of one another is a batch; it misses nothing
# when the previous successful poll came less than one batch interval (a day divided by the windows
# delivered per day) before it, and otherwise one window for each whole interval skipped.
BATCH_MIN_POLLS_PER_DAY = 3.0
BATCH_MAX_WINDOWS_PER_DAY = 2.5
BATCH_SPREAD_HOURS = 1.0
BATCH_SHARE = 0.8

# Per section feed relevance guard. The gate exemption assumes a section feed is small and that what
# it carries is placements. Discovery sometimes registers a whole editorial desk instead: Index.hr's
# sport section was recorded as "sponsored" because its header reads "Sport sponsored by", and the
# Mirror's site-wide rss.xml as "partner". In the 30 days to 2026-10-11 the Index.hr sport page alone
# admitted 3,650 items, 4.5% of every article fetched, and classification found 2 of them relevant
# (both residual_c sports stories, no state origin, no official sourcing). Ten section feeds carried
# more than 500 items each with under 2% relevant; together they were a sixth of all fetches.
# When a section feed's resolved items over the trailing window exceed SECTION_GUARD_MIN_ITEMS and
# fewer than SECTION_GUARD_MAX_RELEVANT_SHARE of them were classified anything other than
# not_relevant, the keyword gate applies to that feed again: an item naming a China term in its
# headline or summary is still admitted, the rest is gated out like an editorial item, and nothing
# already queued is touched. Delivered items are the larger of two counts: the feed_polls ledger
# (every new item the feed delivered, which survives the pruning of gated out rows, so a guarded
# feed stays guarded while it stays busy) and the article rows still present for the feed (which
# include items re-admitted from an editorial feed, never in the ledger, and cover feeds the ledger
# has no rows for). Items still awaiting a fetch or a verdict are subtracted, so a backlog is never
# read as irrelevance. A feed that quietens below the threshold is exempt again; a quiet feed cannot
# flood.
SECTION_GUARD_DAYS = 30
SECTION_GUARD_MIN_ITEMS = 500
SECTION_GUARD_MAX_RELEVANT_SHARE = 0.02
# Statuses with no verdict yet: not fetched, or fetched and waiting for the model.
SECTION_GUARD_PENDING = ("discovered", "queued", "fetched", "awaiting_llm", "llm_submitted")


def _entry_time(entry) -> Optional[str]:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        val = entry.get(key)
        if val:
            try:
                return dt.datetime(*val[:6], tzinfo=dt.timezone.utc).isoformat()
            except Exception:
                continue
    return None


def newest_entry_time(entries, now: Optional[dt.datetime] = None) -> Optional[str]:
    """The newest entry date a poll listed, for feed_health.newest_entry (a frozen feed answers with old
    items). A date more than a day ahead is a feed's error and is left out, so it cannot hide a freeze."""
    limit = (now or dt.datetime.now(dt.timezone.utc)) + dt.timedelta(days=1)
    stamps = [t for t in (_entry_time(e) for e in entries or []) if t and dt.datetime.fromisoformat(t) <= limit]
    return max(stamps) if stamps else None


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


_SURROGATES = re.compile("[\ud800-\udfff]")
_CONTROLS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
TITLE_MAX_CHARS = 1000


def clean_text(value) -> str:
    """A feed field as storable text: "" for what is not text (a list summary, a dict), a number as its
    digits, lone surrogates (which UTF-8 cannot encode, so SQLite and the body files cannot store them)
    replaced by U+FFFD, and NUL and the other control characters but tab and newlines left out."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    elif isinstance(value, (int, float)):
        value = str(value)
    elif not isinstance(value, str):
        return ""
    return _CONTROLS.sub("", _SURROGATES.sub("\ufffd", value))


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


def feed_history(conn, feed_url: str, now: Optional[dt.datetime] = None,
                 outlet_id: Optional[str] = None) -> Optional[Dict]:
    """The feed's delivery over the trailing MISSED_HISTORY_DAYS whole days of the feed_polls ledger
    (today, still being polled, is left out): days with a poll, new items delivered per such day,
    polls per day, entries per poll, whether it is a batch publisher (see the note above the
    constants), and the span floor for its rate (min_span_h). None when the ledger has no polls
    for it. outlet_id, when given, only narrows the articles lookup to the outlet's index."""
    now = now or dt.datetime.now(dt.timezone.utc)
    day = now.date()
    # A feed both collectors poll has a ledger row per collector and day: days are distinct dates, polls
    # and entries add up, and new items are the larger collector's, since an item new to one
    # collector's database is often new to the other's too (as in section_feed_flooding).
    days, polls, entries, new = conn.execute(
        """SELECT COUNT(DISTINCT date), COALESCE(SUM(polls), 0), COALESCE(SUM(entries), 0),
                  (SELECT COALESCE(MAX(n), 0) FROM (SELECT SUM(new_items) n FROM feed_polls
                     WHERE feed_url=:f AND date>=:a AND date<:b GROUP BY collector))
           FROM feed_polls WHERE feed_url=:f AND date>=:a AND date<:b""",
        {"f": feed_url, "a": (day - dt.timedelta(days=MISSED_HISTORY_DAYS)).isoformat(), "b": day.isoformat()}).fetchone()
    if not days or not polls:
        return None
    per_day, window, polls_per_day = new / float(days), entries / float(polls), polls / float(days)
    batch = (days >= 3 and polls_per_day >= BATCH_MIN_POLLS_PER_DAY and 0 < per_day
             and per_day <= BATCH_MAX_WINDOWS_PER_DAY * window)
    return {"days": days, "per_day": per_day, "polls_per_day": polls_per_day, "window": window, "batch": batch,
            "min_span_h": _span_floor(conn, feed_url, window, now, outlet_id)}


def _span_floor(conn, feed_url: str, window: float, now: dt.datetime, outlet_id: Optional[str] = None) -> float:
    """MISSED_MIN_SPAN_HOURS, or the median span in hours of the feed's full windows over the last
    MISSED_SPAN_DAYS when that is shorter (never under MISSED_SPAN_MIN_HOURS). A full window is a poll
    whose new items (articles discovered within five minutes of one another) number the feed's usual
    entries per poll; gated out rows are kept three days, so every such poll is still on record."""
    size = max(2, int(round(window)))
    where, args = "feed_url=? AND discovered_at>=? AND discovered_at<?", [feed_url]
    if outlet_id:
        where, args = "outlet_id=? AND " + where, [outlet_id, feed_url]
    args += [(now - dt.timedelta(days=MISSED_SPAN_DAYS)).isoformat(), now.isoformat()]
    polls, last = [], None
    for disc, pub in conn.execute("SELECT discovered_at, published_at FROM articles WHERE %s ORDER BY discovered_at" % where,
                                  args):
        d = _aware(disc)
        if d is None:
            continue
        if last is None or (d - last).total_seconds() > 300:
            polls.append([])
        last = d
        polls[-1].append(_aware(pub) if pub else None)
    spans = []
    for p in polls:
        dated = sorted(s for s in p if s is not None)
        if len(p) >= size and len(dated) >= 2:
            spans.append((dated[-1] - dated[0]).total_seconds() / 3600.0)
    if len(spans) < 3:
        return MISSED_MIN_SPAN_HOURS
    spans.sort()
    mid = len(spans) // 2
    median = spans[mid] if len(spans) % 2 else (spans[mid - 1] + spans[mid]) / 2.0
    return min(MISSED_MIN_SPAN_HOURS, max(MISSED_SPAN_MIN_HOURS, median))


def _burst(stamps: List[dt.datetime]) -> bool:
    """Whether at least BATCH_SHARE of the (sorted) stamps fall within BATCH_SPREAD_HOURS of one another."""
    spread, best, j = BATCH_SPREAD_HOURS * 3600, 0, 0
    for i, s in enumerate(stamps):
        while (s - stamps[j]).total_seconds() > spread:
            j += 1
        best = max(best, i - j + 1)
    return len(stamps) >= 3 and best >= BATCH_SHARE * len(stamps)


def saturation(prev, linked: int, new_here: int, times: List[str],
               now: Optional[dt.datetime] = None, history: Optional[Dict] = None) -> Tuple[bool, float]:
    """Whether a poll came back as a full window with nothing seen before, and an estimate of
    the items lost between this poll and the previous successful one.

    A feed carries only its most recent entries. When every entry of a poll is new, the window
    did not reach back to the previous successful poll, so whatever was published in between
    was never seen; a skipped run costs a busy feed more than a quiet one. The estimate is the
    feed's own publishing rate across the returned window, taken over at least
    MISSED_MIN_SPAN_HOURS (or the feed's own median window span when shorter, from feed_history),
    times the stretch between the previous successful poll and the oldest entry returned. With the
    feed's ledger history (feed_history) the estimate is capped by what the feed delivers per day,
    and a batch poll of a batch publisher counts only the batches that fell wholly between the two
    polls. The first successful poll of a feed has nothing to overlap
    with and is never saturated. A window with fewer than two dated entries, or with every entry
    dated the same instant, is saturated with no estimate."""
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
    cap = MAX_MISSED_ESTIMATE
    if history:
        cap = min(cap, MISSED_DAILY_MULTIPLE * max(history["per_day"], linked) * max(1.0, gap / 86400.0))
        if history["batch"] and _burst(stamps):
            interval = 86400.0 * history["window"] / history["per_day"]
            return True, round(min(cap, (gap // interval) * linked), 2)
    floor_h = history.get("min_span_h", MISSED_MIN_SPAN_HOURS) if history else MISSED_MIN_SPAN_HOURS
    rate = (len(stamps) - 1) / max(span, floor_h * 3600)
    return True, round(min(cap, rate * gap), 2)


def previous_poll(conn, feed_url: str, prev):
    """The previous successful poll of a feed polled by both collectors, as saturation() takes it:
    this collector's own (feed_health.last_ok) or the latest logged poll by either collector,
    whichever is later. Judged against its own last poll alone, a collector that polls a feed
    every few hours would book as lost what the other collector picked up in between."""
    logged = store.last_poll_at(conn, feed_url)
    own = prev["last_ok"] if prev is not None else None
    if logged and (not own or (_aware(own) and _aware(logged) and _aware(logged) > _aware(own))):
        return {"last_ok": logged}
    return prev


def backfill_poll(conn, feed_url: str, prev, now: Optional[dt.datetime] = None) -> Optional[str]:
    """Whether this successful poll of a feed is a backfill read, whose items published more than
    config.BACKFILL_LAG_HOURS ago count on their publication day (store.backfill_day): "first" for the
    feed's first successful poll ever, "gap" for its first after more than config.BACKFILL_GAP_DAYS
    without one, None for an ordinary poll, whose items count on the discovery day. prev is the feed's
    feed_health row as it stood before this poll was recorded.

    The first poll is judged by the polls ledger alone (feed_polls, which only successful polls write,
    and feed_poll_log for a shared feed's polls by either collector), not by feed_health.last_ok:
    validate_sources records feed health, last_ok included, for every feed it checks without collecting
    anything, so a feed added and validated before its first discovery poll already has a last_ok. The
    last successful poll is the latest of the three; a ledger day counts as polled to its end."""
    from pipeline import config
    now = now or dt.datetime.now(dt.timezone.utc)
    day = conn.execute("SELECT MAX(date) FROM feed_polls WHERE feed_url=?", (feed_url,)).fetchone()[0]
    logged = store.last_poll_at(conn, feed_url)
    if not day and not logged:
        return "first"
    stamps = [_aware(logged), _aware(prev["last_ok"]) if prev is not None and prev["last_ok"] else None]
    if day:
        stamps.append(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1))
    last = max((s for s in stamps if s is not None), default=None)
    if last is None or (now - last).total_seconds() > config.BACKFILL_GAP_DAYS * 86400:
        return "gap"
    return None


def rejudge(conn, row, prev_at: str) -> Tuple[bool, float]:
    """A logged saturated poll judged again against a poll by the other collector at prev_at that came
    to light later (it arrived in a relay bundle after this poll was judged). When the window's oldest
    entry is no newer than that poll, the window reached it and nothing was lost; otherwise the poll
    stays saturated and the estimate runs from that poll, with the same guards as saturation()."""
    times = json.loads(row["times"] or "[]")
    at = _aware(row["polled_at"])
    stamps = sorted(min(s, at) for s in (_aware(t) for t in times) if s is not None)
    p = _aware(prev_at)
    if stamps and p and stamps[0] <= p:
        return False, 0.0
    history = feed_history(conn, row["feed_url"], now=at)
    return saturation({"last_ok": prev_at}, row["entries"], row["entries"], times, now=at, history=history)


def readmit_gated_out(conn, item: Dict) -> Optional[int]:
    """Re-admit an item the gate rejected when it turns up again on a gate-exempt section feed.

    The same URL is often on an editorial feed, where the gate rejects a headline naming no China
    term, and on the press release or sponsored section feed where paid state placements actually
    sit. insert_discovered is INSERT OR IGNORE and the caller moves on, so the exemption never
    applied to an item seen editorially first and the item was pruned three days later.

    Returns the article id when it was re-admitted. Discovery is not counted again: the item was
    counted when it was first seen, on its day of record, and only the gate-relevant side of that day's counters is
    corrected, so the denominator does not move."""
    row = conn.execute(
        "SELECT id, status, gate_relevant, discovered_at, day_of_record, country, outlet_id FROM articles WHERE url_hash=?",
        (store.url_hash(item["url"]),)).fetchone()
    if row is None or row["gate_relevant"] or row["status"] != "gated_out":
        return None
    conn.execute("UPDATE articles SET status='queued', gate_relevant=1, gate_terms=?, feed_url=? WHERE id=?",
                 (json.dumps(item.get("gate_terms") or []), item.get("feed_url"), row["id"]))
    day = row["day_of_record"] or (row["discovered_at"] or "")[:10]
    if day:
        conn.execute("UPDATE daily_discovery SET gate_relevant=gate_relevant+1 WHERE date=? AND country=?",
                     (day, row["country"]))
        conn.execute("UPDATE daily_outlet_discovery SET gate_relevant=gate_relevant+1 WHERE date=? AND outlet_id=?",
                     (day, row["outlet_id"]))
    return row["id"]


def section_feed_flooding(conn, feed_url: str, now: Optional[dt.datetime] = None) -> Optional[Dict]:
    """Whether a section feed has lost the gate exemption: {resolved, relevant} when its resolved items
    over the trailing SECTION_GUARD_DAYS exceed SECTION_GUARD_MIN_ITEMS and under
    SECTION_GUARD_MAX_RELEVANT_SHARE of them were classified anything other than not_relevant,
    otherwise None. See the note above the constants."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=SECTION_GUARD_DAYS)).isoformat()
    # One collector's ledger, the larger: a feed polled by both the runner and the relay has a row for
    # each, and an item new to one collector's database is new to the other's too.
    ledger = conn.execute(
        """SELECT COALESCE(MAX(n), 0) FROM (SELECT SUM(new_items) n FROM feed_polls WHERE feed_url=? AND date>=?
           GROUP BY collector)""", (feed_url, since[:10])).fetchone()[0]
    marks = ",".join("?" * len(SECTION_GUARD_PENDING))
    present, pending = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(status IN (%s)), 0) FROM articles WHERE feed_url=? AND discovered_at>=?" % marks,
        tuple(SECTION_GUARD_PENDING) + (feed_url, since)).fetchone()
    resolved = max(0, max(ledger, present) - pending)
    if resolved <= SECTION_GUARD_MIN_ITEMS:
        return None
    relevant = conn.execute(
        """SELECT COUNT(*) FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.feed_url=? AND a.discovered_at>=? AND c.category!='not_relevant'""",
        (feed_url, since)).fetchone()[0]
    if relevant >= resolved * SECTION_GUARD_MAX_RELEVANT_SHARE:
        return None
    return {"resolved": resolved, "relevant": relevant}


# Crawl-delay in feed polling. A feed host whose robots.txt asks for a long Crawl-delay (AsiaOne 60
# seconds; five hosts in the registry ask for ten minutes or more) gets its first feed of the run
# however long the wait, and further feeds only while their slots fall within
# SLOW_FEED_WINDOW_SECONDS of the stage start and before the deadline. The others are deferred to a
# later run (feeds_deferred, no health record either way) and an outlet's feeds on such a host take
# turns by the hour, so none is left out for good. The waits are slept without a worker permit
# (fetch_articles.paced), so the other workers keep polling everything else.
SLOW_FEED_WINDOW_SECONDS = 600
# Discovery does not hold the run for those sleeps. From 2026-10-20 it polled everything else in 11 to 22
# minutes and then slept to minute 28 for teledoce.com (ten minutes between requests, and its feed
# redirects), and from 2026-10-22, when the rules stage got its reserve, that left the article fetch
# a minute of the run: 12,470 relevant articles were queued by 2026-10-30. So past
# SLOW_FEED_PATIENCE_SECONDS, once every outlet left is asleep for more than SLOW_FEED_NEAR_SECONDS, the
# stage moves on (outlets_left_asleep, no health record either way). One clock hour in
# SLOW_FEED_PATIENT_EVERY_HOURS it waits them out as before, so none is left out for good.
SLOW_FEED_PATIENCE_SECONDS = 900
SLOW_FEED_NEAR_SECONDS = 60
SLOW_FEED_PATIENT_EVERY_HOURS = 6
_slow_feeds: Dict = {"started": None, "deadline": None, "polled": {}}
_slow_lock = threading.Lock()


def _slow_host(url: str) -> bool:
    """Whether the feed's host asks for a Crawl-delay above the minimum. Loads its robots.txt (once
    per host and run, as fetch_feed would) so the delay is known before the first request."""
    from pipeline import config, fetch_articles as fa
    fa.can_fetch(url)
    return fa.domain_gap(fa.domain_of(url)) > config.MIN_SECONDS_PER_DOMAIN


def _defer_feed(url: str) -> bool:
    """Whether this run leaves the feed for a later one (see the note above). Only within run()."""
    from pipeline import fetch_articles as fa
    if _slow_feeds["started"] is None or not _slow_host(url):
        return False
    domain = fa.domain_of(url)
    at = time.time() + fa.slot_in(domain)
    with _slow_lock:
        if _slow_feeds["deadline"] and at > _slow_feeds["deadline"]:
            return True
        if _slow_feeds["polled"].get(domain) and at - _slow_feeds["started"] > SLOW_FEED_WINDOW_SECONDS:
            return True
        _slow_feeds["polled"][domain] = _slow_feeds["polled"].get(domain, 0) + 1
    return False


def _patience_ends(started: float) -> Optional[float]:
    """When discovery stops waiting for outlets that only sleep, or None in an hour that waits them out."""
    if int(started // 3600) % SLOW_FEED_PATIENT_EVERY_HOURS == 0:
        return None
    return started + SLOW_FEED_PATIENCE_SECONDS


def _only_sleeps_left(left: int, patience_ends: Optional[float], now: Optional[float] = None) -> bool:
    """Whether all the outlets left are asleep on a long Crawl-delay, past the stage's patience, with none
    about to wake. An outlet not started yet, or polling, is not asleep, so it is always waited for."""
    from pipeline import fetch_articles as fa
    now = time.time() if now is None else now
    if patience_ends is None or now < patience_ends:
        return False
    wakes = fa.long_sleeps()
    return len(wakes) >= left and all(w - now > SLOW_FEED_NEAR_SECONDS for w in wakes)


def _arrivals(futures, counts: Dict, patience_ends: Optional[float], deadline: Optional[float]):
    """The futures as they finish, like as_completed, until only sleeps or a passed deadline are left."""
    pending = set(futures)
    while pending:
        done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
        yield from done
        if done:
            continue
        if deadline and time.time() > deadline:
            counts["stopped_at_deadline"] = True
            return
        if _only_sleeps_left(len(pending), patience_ends):
            counts["outlets_left_asleep"] = len(pending)
            return


def _take_turns(entries: List[Dict]) -> List[Dict]:
    """The outlet's feeds on slow hosts rotated by the hour among their own places in the list."""
    if _slow_feeds["started"] is None:
        return entries
    slow = [i for i, fe in enumerate(entries) if fe.get("type", "rss") == "rss" and _slow_host(fe["url"])]
    if len(slow) < 2:
        return entries
    k = int(_slow_feeds["started"] // 3600) % len(slow)
    turned = [entries[i] for i in slow[k:] + slow[:k]]
    out = list(entries)
    for i, fe in zip(slow, turned):
        out[i] = fe
    return out


def poll_outlet(outlet: Dict) -> List[Dict]:
    out = []
    for fe in _take_turns(registry.feed_entries(outlet)):
        if fe.get("type", "rss") in ("rss", "news_sitemap"):
            if _defer_feed(fe["url"]):
                out.append({"outlet": outlet, "feed_url": fe["url"], "kind": fe.get("kind", registry.EDITORIAL),
                            "result": None, "deferred": True})
                continue
            if fe.get("type") == "news_sitemap":
                from pipeline import news_sitemap  # a Google News sitemap read as a feed
                r = news_sitemap.fetch_entries(fe["url"])
            else:
                r = fetch_feed(fe["url"])
        else:
            from pipeline import section_pages  # section index pages without a feed
            r = section_pages.fetch_entries(fe["url"], fe.get("type"))
        out.append({"outlet": outlet, "feed_url": fe["url"], "kind": fe.get("kind", registry.EDITORIAL), "result": r,
                    "type": fe.get("type", "rss")})
    return out


def run(conn, run_id: str, outlets: Optional[List[Dict]] = None, workers: int = 16,
        deadline: Optional[float] = None) -> Dict:
    log_id = store.start_stage(conn, run_id, "discover")
    if outlets is None:
        outlets = registry.collectable(registry.load_outlets())
    store.sync_outlets(conn, registry.load_outlets())
    store.prune_poll_log(conn)
    from pipeline import section_pages
    section_pages.aap_start(conn, outlets)   # how far back this run reads the shared AAP release listing
    counts = {"feeds": 0, "feeds_ok": 0, "items_seen": 0, "items_new": 0,
              "gate_relevant": 0, "near_duplicates": 0, "feeds_saturated": 0, "section_items": 0,
              "outlets": len(outlets), "outlets_polled": 0, "readmitted_section": 0,
              "section_feeds_guarded": 0, "feeds_deferred": 0}
    # The shared AAP release listing is read once here, before any worker starts, within a share of the
    # time left (section_pages.AAP_PREREAD_SHARE); read lazily under a lock, its first backfill held
    # most of the workers for minutes. aap_finish records where it got to once a masthead has used it.
    aap = section_pages.aap_preread(conn, deadline)
    if aap is not None:
        counts["aap_listing"] = aap
    # ThreadPoolExecutor.map submits every outlet up front and the with block joins on exit, so
    # breaking out of the consumer loop at the deadline stopped the database writes but not the
    # polling: a run given 35 minutes could overshoot into the job's 55 minute timeout and be
    # killed, losing the fetch and classify stages with it. shutdown(cancel_futures=True) drops
    # the outlets not started yet, and wait=False leaves only the polls already in flight.
    # Hosts with a long Crawl-delay sleep without a permit (fetch_articles.paced), so the pool has
    # SLOW_DOMAIN_THREADS threads beyond the `workers` that poll at any one time; `stopped` keeps a
    # thread that only gets its permit after the deadline from polling at all.
    from pipeline import config, fetch_articles
    _slow_feeds.update(started=time.time(), deadline=deadline, polled={})
    permits = threading.BoundedSemaphore(workers)
    # Each feed's summary boilerplate (pipeline/boilerplate.py), which the keyword gate does not read.
    boiler = boilerplate.FeedBoilerplate(conn)
    stopped = threading.Event()

    def poll(o):
        return [] if stopped.is_set() else poll_outlet(o)

    conn.commit()        # a feed that fails is rolled back to the end of the feed before it, and no further
    pool = ThreadPoolExecutor(max_workers=workers + config.SLOW_DOMAIN_THREADS)
    futures = [pool.submit(fetch_articles.paced(permits, poll), o) for o in outlets]
    try:
        for fut in _arrivals(futures, counts, _patience_ends(_slow_feeds["started"]), deadline):
            try:
                group = fut.result()
            except Exception as exc:       # one outlet must not take the discovery stage down
                counts["outlet_errors"] = counts.get("outlet_errors", 0) + 1
                counts["last_outlet_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
                continue
            counts["outlets_polled"] += 1
            # What the outlet's Google News sitemap listed this run, for the RSS saturation estimate, and the
            # titles on record, loaded at the sitemap's turn (pipeline/news_sitemap.py, the note there).
            from pipeline import news_sitemap
            covered = news_sitemap.cover([g["result"] for g in group if g.get("type") == news_sitemap.NEWS_SITEMAP])
            titles = None
            for g in group:
                if g.get("deferred"):
                    counts["feeds_deferred"] += 1
                    continue
                outlet, feed_url, kind, r = g["outlet"], g["feed_url"], g["kind"], g["result"]
                counts["feeds"] += 1
                counted = dict(counts)
                try:
                    prev = store.feed_health_row(conn, feed_url)
                    store.record_feed_health(conn, outlet["id"], feed_url, r["ok"], r["error"], len(r["entries"]),
                                             newest_entry_time(r["entries"]))
                    if not r["ok"]:
                        continue
                    counts["feeds_ok"] += 1
                    # The feed's first successful poll ever, or its first after a long outage, is a backfill
                    # read whatever the reader (backfill_poll, config.BACKFILL_GAP_DAYS).
                    reread = backfill_poll(conn, feed_url, prev)
                    if reread:
                        counts["feeds_backfill_" + reread] = counts.get("feeds_backfill_" + reread, 0) + 1
                    backfill = bool(r.get("backfill") or reread)
                    # A reader that sets keyword_gate (the AAP release listing, section_pages) keeps the gate.
                    section = kind in registry.GATE_EXEMPT_KINDS and not r.get("keyword_gate")
                    if section and section_feed_flooding(conn, feed_url):
                        # Flooding: the keyword gate applies to this feed until it earns the exemption back.
                        section = False
                        counts["section_feeds_guarded"] += 1
                    linked, new_here, times = 0, 0, []
                    is_map, known, links = g.get("type") == news_sitemap.NEWS_SITEMAP, (), []
                    if is_map:
                        known = news_sitemap.known_hashes(conn, [e.get("link") for e in r["entries"]])
                        titles = news_sitemap.known_titles(conn, outlet["id"]) if titles is None else titles
                    for entry in r["entries"]:
                        link = entry.get("link")
                        if link is not None and not isinstance(link, str):
                            counts["entries_malformed"] = counts.get("entries_malformed", 0) + 1
                            continue
                        link = clean_text(link).strip()
                        if not link or not link.startswith("http"):
                            continue
                        linked += 1
                        counts["items_seen"] += 1
                        links.append(link)
                        if is_map:
                            if store.url_hash(link) in known:
                                continue            # on record: a sitemap lists two days of items on every poll
                            if news_sitemap.duplicate_title(titles, clean_text(entry.get("title")), link):
                                counts["sitemap_same_title"] = counts.get("sitemap_same_title", 0) + 1
                                continue            # the same article under another address
                        title = clean_text(entry.get("title")).strip()[:TITLE_MAX_CHARS]
                        summary = clean_text(_entry_summary(entry))
                        if section:
                            relevant, terms = True, ["section:%s" % kind]
                        else:
                            # A sitemap entry has a title and nothing else: the gate also reads its title_only terms.
                            relevant, terms = gate.check(title, summary, outlet["language"], outlet["country"], url=link,
                                                         feed_boilerplate=boiler.shingles(outlet["id"], feed_url),
                                                         title_only=is_map)
                            if relevant and r.get("keyword_gate") and kind in registry.GATE_EXEMPT_KINDS:
                                terms = terms + ["section:%s" % kind]   # it still arrived by the section
                        item = {
                            "url": link, "outlet_id": outlet["id"], "country": outlet["country"],
                            "language": outlet["language"], "feed_url": feed_url, "title": title,
                            "summary": gate.strip_html(summary)[:2000], "author": extract.clean_author(clean_text(_entry_author(entry)) or None),
                            "published_at": _entry_time(entry),
                            "status": "queued" if relevant else "gated_out",
                            "gate_relevant": 1 if relevant else 0, "gate_terms": terms,
                        }
                        # A backfill read (the result's "backfill", set by the AAP release reader, or a feed's
                        # first poll or first after an outage, backfill_poll) dates an item published well
                        # before it was seen by its publication day, in the counts and the discovery totals
                        # alike (store.backfill_day, config.BACKFILL_LAG_HOURS).
                        item["day_of_record"] = store.backfill_day(item["published_at"]) if backfill else None
                        if item["published_at"]:
                            times.append(item["published_at"])
                        new_id = store.insert_discovered(conn, item)
                        if new_id is not None and item.get("seen_day"):
                            # Rejected and pruned before, relevant now: back in the corpus on the day its
                            # discovery was counted, which is not counted again (store.insert_discovered).
                            store.record_relevant_again(conn, outlet["country"], outlet["id"], item["seen_day"])
                            counts["readmitted_after_prune"] = counts.get("readmitted_after_prune", 0) + 1
                            if section:
                                counts["section_items"] += 1
                            if link_near_duplicates(conn, new_id, title, outlet["country"]):
                                counts["near_duplicates"] += 1
                            continue
                        if new_id is None:
                            # Known URL. On a gate-exempt section feed it may be one the gate rejected
                            # when it arrived on an editorial feed; re-admit it there and then.
                            readmitted = readmit_gated_out(conn, item) if section else None
                            if readmitted:
                                counts["readmitted_section"] += 1
                                counts["section_items"] += 1
                                if link_near_duplicates(conn, readmitted, title, outlet["country"]):
                                    counts["near_duplicates"] += 1
                            elif relevant and not is_map and outlet.get("news_sitemaps"):
                                # The outlet's news sitemap brought it first and the gate read its title alone;
                                # an RSS feed now brings its summary too, and the gate passes.
                                again = readmit_gated_out(conn, item)
                                if again:
                                    counts["readmitted_editorial"] = counts.get("readmitted_editorial", 0) + 1
                                    if link_near_duplicates(conn, again, title, outlet["country"]):
                                        counts["near_duplicates"] += 1
                            continue
                        new_here += 1
                        counts["items_new"] += 1
                        store.record_discovery(conn, outlet["country"], relevant, outlet["id"],
                                               item["day_of_record"] or item["discovered_at"][:10])
                        if section:
                            counts["section_items"] += 1
                        if relevant:
                            counts["gate_relevant"] += 1
                            if link_near_duplicates(conn, new_id, title, outlet["country"]):
                                counts["near_duplicates"] += 1
                    # A feed both collectors poll (registry collector: both) is judged against the previous
                    # poll by either of them, and every poll of it is logged so the other side can do the same.
                    shared = registry.is_shared(outlet)
                    if shared:
                        prev = previous_poll(conn, feed_url, prev)
                    history = feed_history(conn, feed_url, outlet_id=outlet["id"]) if linked and new_here >= linked else None
                    saturated, missed = saturation(prev, linked, new_here, times, history=history)
                    if covered is not None and not is_map and kind == registry.EDITORIAL:
                        # The sitemap listed what this RSS poll lost, except the part of the stream it leaves out.
                        saturated, missed, scaled = news_sitemap.covered_saturation(prev, saturated, missed, links, covered)
                        counts["saturation_covered"] = counts.get("saturation_covered", 0) + (1 if scaled else 0)
                    counts["feeds_saturated"] += 1 if saturated else 0
                    store.record_feed_poll(conn, outlet["id"], outlet["country"], feed_url, linked, new_here, saturated, missed)
                    if shared:
                        store.log_feed_poll(conn, feed_url, config.COLLECTOR, store.utcnow(), outlet["id"], outlet["country"],
                                            linked, new_here, times, prev["last_ok"] if prev is not None else None,
                                            saturated, missed)
                    conn.commit()
                except Exception as exc:
                    # One feed whose result or entries break the loop (a list summary, a number for a link,
                    # text SQLite cannot store) is rolled back alone, counted, and recorded as a failed
                    # poll in feed_health; the stage goes on with the next feed.
                    conn.rollback()
                    counts.clear()
                    counts.update(counted)      # the feed's items went with the rollback
                    counts["feed_errors"] = counts.get("feed_errors", 0) + 1
                    counts["last_feed_error"] = "%s: %s: %s" % (feed_url, type(exc).__name__, str(exc)[:200])
                    try:
                        store.record_feed_health(conn, outlet["id"], feed_url, False,
                                                 "feed_error: %s" % type(exc).__name__, 0)
                        conn.commit()
                    except Exception:
                        conn.rollback()
            if deadline and time.time() > deadline:
                counts["stopped_at_deadline"] = True
                break
    finally:
        stopped.set()
        pool.shutdown(wait=False, cancel_futures=True)
    section_pages.aap_finish(conn)
    counts["outlets_skipped"] = counts["outlets"] - counts["outlets_polled"]
    store.finish_stage(conn, log_id, True, counts)
    return counts
