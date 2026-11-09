"""Input watch: after collection, checks that the inputs the findings rest on are still arriving.

Most of the state origin and unchecked state sourcing labels come from a few outlets (on the snapshot of
2026-11-09, 84 percent of the 5,468 current state origin labels came from 20 outlets), and their
failures were silent: on 2026-10-30 a green run hid an 8 day article fetch starvation, the reference
source ref_cgtn_en failed text extraction on most of its items and nothing said so, and id_antara loses
about half its feed items to saturation. This module runs after collection in every run, reads the
database only (no network), and reports what is at risk:

  key inputs   the outlets that carried most of the state origin and unchecked state sourcing labels
               over the last WINDOW_DAYS (key_inputs: the smallest set covering KEY_SHARE of them, at
               most KEY_MAX; papers that share a registry network count once, as one input). For each:
               no successful feed poll in POLL_OK_HOURS, newest feed entry older than
               ENTRY_STALE_HOURS, feed saturation over RECENT_DAYS, China relevant items lost at fetch
               over RECENT_DAYS against its own earlier baseline, and no China relevant item for
               ZERO_DAYS or more when its own rate makes that improbable.
  reference    every reference source: failure share of its items over RECENT_DAYS, and no new item
               in REF_SILENT_HOURS.
  global       the article fetch backlog rising run after run (the 2026-10-30 lesson), no discovery or
               fetch pass finished lately.

Each problem prints one compact line and a GitHub Actions warning annotation titled "Input at risk"
(at most MAX_ANNOTATIONS, the last one saying how many more are in the log), and the run is recorded in
run_log under stage input_watch. Nothing here is a label, a count or a denominator, and nothing is ever
changed but the run_log row. The thresholds below are explained from the snapshot of 2026-11-09.

Usage: python -m pipeline.input_watch [--now 2026-11-09T00:27:00+00:00] [--all]
"""
import argparse
import datetime as dt
import json
import math
import sys
import time
import uuid
from collections import defaultdict
from typing import Dict, List, Optional

from pipeline import config, store

UTC = dt.timezone.utc
STAGE = "input_watch"
TITLE = "Input at risk"

# --- which inputs are key ------------------------------------------------------------------------------
# Eight weeks: long enough that an outlet whose input broke a fortnight ago is still ranked by what it
# carried before (it would drop out of a short window just as it most needs watching), short enough to
# follow the registry. On the snapshot the 56 days hold 6,835 of the 7,964 current A and B labels, 4,501
# distinct items once each input's copies of one title group count once, from 553 inputs.
WINDOW_DAYS = 56
# The smallest set of inputs carrying this share of the window's items, never more than KEY_MAX. With
# unchecked state sourcing included the tail is long: on the snapshot 80 percent would take 117 inputs,
# so the cap binds and the 25 key inputs carry 2,624 of the 4,501 items (58 percent). The summary line
# states the share reached every run. The cap keeps the watch cheap and its output short.
KEY_SHARE = 0.80
KEY_MAX = 25

# --- per input checks -----------------------------------------------------------------------------------
# Every feed is polled in every run, and the runs come a median 2.7 hours apart (registry.py); a key
# input with no successful poll in a day has failed about eight polls in a row. On the snapshot every
# key input's last good poll is under 40 minutes old.
POLL_OK_HOURS = 24
# A feed can answer and still serve old items (CityNews served 2022 items). The key inputs are wires and
# dailies: on the snapshot their newest entries are all 13 hours old or newer, so three days without a
# new entry is no weekend lull. The site's own frozen feed rule (config.FEED_FROZEN_DAYS) waits 14 days.
ENTRY_STALE_HOURS = 72
# The days every recent share is taken over.
RECENT_DAYS = 7
# Share of a key input's feed items lost between polls (feed_polls.missed_estimate, current estimator
# only) over RECENT_DAYS, out of the items seen plus those missed. The site warns at
# config.FEED_SATURATION_WARNING_SHARE (25 percent) already; the watch speaks only from 40 percent, since
# on the snapshot 11 of the 25 key inputs sit between 10 and 39 percent (median of all 25: 14 percent),
# a loss the site states and a warning every run would bury. At 40 percent it flags id_antara (61),
# my_utusan (53), za_iol (41) and my_astroawani (41).
SATURATION_SHARE = 0.40
SATURATION_MIN_ITEMS = 100     # items seen plus missed over RECENT_DAYS, below which a share says little
# China relevant items lost at fetch (failed, extraction_empty among them, paywalled, blocked_robots),
# out of those with a fetch outcome, over RECENT_DAYS against the same input's share in the rest of the
# window. A rise is flagged when it is LOSS_RISE or more, the recent share is LOSS_SHARE or more, there
# are LOSS_MIN_ITEMS recent outcomes on each side, and so many losses would be improbable at the
# baseline share (binomial tail below LOSS_P). A steady loss, a paywall the site already reports, is not
# news; a change is. On the snapshot one key input rose from 35 to 99 percent (the Postmedia papers);
# no other rose by more than 8 points (es_europapress, 0 to 8).
LOSS_RISE = 0.20
LOSS_SHARE = 0.30
LOSS_MIN_ITEMS = 10
LOSS_P = 0.001
# No China relevant item for at least ZERO_DAYS, and for long enough that the input's own rate (its
# relevant items from the window's start to its newest one) expected ZERO_EXPECTED or more in the
# silence: a Poisson chance of exp(-7), under 0.001, that a working input shows nothing. The silence a
# key input needs grows as its rate falls: on the snapshot from 3 days for most of them to about 9 days
# for the quietest two (gy_villagevoice, 44 relevant items in 56 days; sv_diariocolatino, 41 in 53).
ZERO_DAYS = 3
ZERO_EXPECTED = 7.0

# --- reference sources ----------------------------------------------------------------------------------
# Share of a reference source's items over RECENT_DAYS whose fetch failed (failed, blocked_robots,
# paywalled), out of those fetched or failed; items that expired unfetched are not counted. On the
# snapshot ref_cgtn_en fails on 762 of 1,394 (55 percent) and every other source on 20 percent or less
# (ref_ecns_en 20 of 98), most under 10.
REF_FAIL_SHARE = 0.30
REF_MIN_ITEMS = 20
# A reference source with no new item for two days: every source lists news daily (each added items on
# 9 or more of the 11 days to the snapshot) and the oldest newest item on the snapshot is 17 hours old.
REF_SILENT_HOURS = 48

# --- global ---------------------------------------------------------------------------------------------
# The backlog of China relevant articles the fetch left queued at its deadline (run_log fetch
# skipped_deadline, retries included) rising in each of the last BACKLOG_RUNS fetch runs to
# BACKLOG_FLOOR or more. Over the snapshot's 488 fetch runs this fires only at the start of the
# starvation of 2026-10-22: at 18:40 (3, 8, 326 left queued), 13 hours before the workflow's own warning
# at BACKLOG_WORKFLOW_WARNS (07:52 on 2026-10-23), which the watch leaves to the workflow so as not to say
# it twice. Single runs that leave a few hundred queued are common (up to 744 since 2026-11-02, 2,949
# once on 2026-10-13) and do not fire it, since the next run clears them.
BACKLOG_FLOOR = 250
BACKLOG_RUNS = 3
BACKLOG_WORKFLOW_WARNS = 1000
# No discovery or fetch pass finished in this long: the input as a whole has stopped.
STAGE_STALE_HOURS = 24

# GitHub shows at most 10 warning annotations per step; the tenth says how many more the log holds.
MAX_ANNOTATIONS = 10

LOST = ("failed", "paywalled", "blocked_robots")
DONE = ("classified", "awaiting_llm", "llm_submitted", "fetched")


def _iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).replace(microsecond=0).isoformat()


def _parse(ts) -> Optional[dt.datetime]:
    if not ts:
        return None
    try:
        t = dt.datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _hours(now: dt.datetime, ts) -> Optional[float]:
    t = _parse(ts)
    return None if t is None else (now - t).total_seconds() / 3600


def _pct(n, d) -> str:
    return "%d%%" % round(100.0 * n / d) if d else "n/a"


def _num(n) -> str:
    return "{:,}".format(int(round(n)))


def binomial_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), in log space so a large n cannot overflow."""
    if k <= 0:
        return 1.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lp, lq = math.log(p), math.log1p(-p)
    total = 0.0
    for i in range(k, n + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(1.0, total)


def _marks(ids) -> str:
    return ",".join("?" * len(ids))


# ---------------------------------------------------------------------------------------------------------
# Which inputs are key
# ---------------------------------------------------------------------------------------------------------

def _collectable(outlets: List[Dict]) -> List[Dict]:
    from pipeline import registry
    return registry.collectable(outlets)


def key_inputs(conn, outlets: List[Dict], now: dt.datetime) -> Dict:
    """The smallest set of inputs that carried KEY_SHARE of the current state origin and unchecked state
    sourcing labels over WINDOW_DAYS, at most KEY_MAX. An input is an outlet, or a registry network of
    outlets counted once; its weight is its distinct items (title groups, articles.dup_group_id), so ten
    papers carrying one widget copy weigh one item, not ten. Only inputs this collector polls (an active
    outlet, or a network with one) can be key; the others still count in the total, so the share
    reached is honest about what the watch does not see.

    Returns {"units": [{"id", "members", "weight", "rank"}], "total", "covered", "share", "window_days"}."""
    since = _iso(now - dt.timedelta(days=WINDOW_DAYS))
    by_id = {o["id"]: o for o in outlets}
    polled = {o["id"] for o in _collectable(outlets)}

    def unit_of(oid):
        net = (by_id.get(oid) or {}).get("network")
        return "network:" + net if net else oid

    items = defaultdict(set)
    for oid, g in conn.execute(
            """SELECT a.outlet_id, COALESCE(a.dup_group_id, -a.id) FROM classifications c
               JOIN articles a ON a.id = c.article_id
               WHERE c.is_current = 1 AND c.category IN ('A', 'B') AND a.discovered_at >= ?""", (since,)):
        items[unit_of(oid)].add(g)
    members = defaultdict(list)
    for oid in sorted(polled):
        members[unit_of(oid)].append(oid)
    total = sum(len(s) for s in items.values())
    ranked = sorted(items.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    units, covered, last = [], 0, None
    for uid, groups in ranked:
        if len(units) >= KEY_MAX:
            break
        if total and covered >= KEY_SHARE * total and len(groups) != last:
            break        # reached, and not tied with the last input taken
        if uid not in members:
            continue     # not polled by this collector: it counts in the total but cannot be watched
        units.append({"id": uid, "members": members[uid], "weight": len(groups), "rank": len(units) + 1})
        covered += len(groups)
        last = len(groups)
    return {"units": units, "total": total, "covered": covered,
            "share": (covered / total) if total else 0.0, "window_days": WINDOW_DAYS}


# ---------------------------------------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------------------------------------

def unit_metrics(conn, members: List[str], now: dt.datetime) -> Dict:
    """Everything the per input checks read, for the outlets of one input."""
    m = {"feeds": 0, "last_ok": None, "newest_entry": None, "sat_new": 0, "sat_missed": 0.0, "sat_polls": 0,
         "sat_saturated": 0, "lost_recent": 0, "done_recent": 0, "lost_base": 0, "done_base": 0, "relevant": 0,
         "newest_relevant": None, "base_days": 0.0, "silent_days": None}
    if not members:
        return m
    mk = _marks(members)
    horizon = now + dt.timedelta(hours=1)
    for r in conn.execute("SELECT last_ok, newest_entry FROM feed_health WHERE outlet_id IN (%s)" % mk, members):
        m["feeds"] += 1
        ok, newest = _parse(r[0]), _parse(r[1])
        if ok and (m["last_ok"] is None or ok > m["last_ok"]):
            m["last_ok"] = ok
        # An entry dated in the future (a feed's wrong zone) would hide a stale feed: it is skipped.
        if newest and newest <= horizon and (m["newest_entry"] is None or newest > m["newest_entry"]):
            m["newest_entry"] = newest
    first_day = (now - dt.timedelta(days=RECENT_DAYS)).date().isoformat()
    r = conn.execute(
        """SELECT COALESCE(SUM(new_items), 0), COALESCE(SUM(missed_estimate), 0), COALESCE(SUM(polls), 0),
                  COALESCE(SUM(saturated), 0)
           FROM feed_polls WHERE outlet_id IN (%s) AND date > ? AND estimator = ?""" % mk,
        members + [first_day, config.MISSED_ESTIMATOR_VERSION]).fetchone()
    m.update(sat_new=r[0], sat_missed=r[1], sat_polls=r[2], sat_saturated=r[3])
    since = _iso(now - dt.timedelta(days=WINDOW_DAYS))
    recent = _iso(now - dt.timedelta(days=RECENT_DAYS))
    lost_r = done_r = lost_b = done_b = relevant = 0
    newest = None
    for status, is_recent, n, last in conn.execute(
            """SELECT status, discovered_at >= ?, COUNT(*), MAX(discovered_at) FROM articles
               WHERE outlet_id IN (%s) AND gate_relevant = 1 AND discovered_at >= ?
               GROUP BY 1, 2""" % mk, [recent] + members + [since]):
        relevant += n
        t = _parse(last)
        if t and (newest is None or t > newest):
            newest = t
        if status in LOST:
            if is_recent:
                lost_r += n
            else:
                lost_b += n
        elif status in DONE:
            if is_recent:
                done_r += n
            else:
                done_b += n
    # The input's rate of China relevant items runs from the window's start, or from its first discovery if
    # later (an outlet added a month ago has a month of baseline, not eight weeks of zeros), to its newest
    # relevant item; the silence runs from that item to now.
    first = conn.execute("SELECT MIN(date) FROM daily_outlet_discovery WHERE outlet_id IN (%s) AND discovered > 0" % mk,
                         members).fetchone()[0]
    start = now - dt.timedelta(days=WINDOW_DAYS)
    f = _parse(first + "T00:00:00+00:00") if first else None
    if f and f > start:
        start = f
    m.update(lost_recent=lost_r, done_recent=done_r, lost_base=lost_b, done_base=done_b, relevant=relevant,
             newest_relevant=newest,
             base_days=max(0.0, ((newest or start) - start).total_seconds() / 86400),
             silent_days=((now - newest).total_seconds() / 86400) if newest else None)
    return m


def check_unit(unit: Dict, m: Dict, now: dt.datetime, polls_meaningful: bool = True) -> List[Dict]:
    """The problems of one key input, each {"check", "input", "line"}."""
    out = []
    name = unit["id"].replace("network:", "network ")
    if len(unit["members"]) > 1:
        name += " (%d outlets)" % len(unit["members"])
    tag = "%s, key input %d" % (name, unit["rank"])

    def add(check, text):
        out.append({"check": check, "input": unit["id"], "line": "%s: %s" % (tag, text)})

    if polls_meaningful:
        if not m["feeds"]:
            add("no_poll", "no feed of it has ever been polled")
        elif m["last_ok"] is None:
            add("no_poll", "no successful feed poll on record")
        else:
            h = (now - m["last_ok"]).total_seconds() / 3600
            if h > POLL_OK_HOURS:
                add("no_poll", "no successful feed poll in %.0f hours (limit %d)" % (h, POLL_OK_HOURS))
    if m["newest_entry"] is not None:
        h = (now - m["newest_entry"]).total_seconds() / 3600
        if h > ENTRY_STALE_HOURS:
            add("stale_entries", "newest feed entry is %.0f hours old (limit %d)" % (h, ENTRY_STALE_HOURS))
    flow = m["sat_new"] + m["sat_missed"]
    if flow >= SATURATION_MIN_ITEMS and m["sat_missed"] / flow >= SATURATION_SHARE:
        add("saturation", "an estimated %s of %s feed items (%s) missed to feed saturation over %d days, "
            "%d of %d polls saturated" % (_num(m["sat_missed"]), _num(flow), _pct(m["sat_missed"], flow),
                                          RECENT_DAYS, m["sat_saturated"], m["sat_polls"]))
    n_r = m["lost_recent"] + m["done_recent"]
    n_b = m["lost_base"] + m["done_base"]
    if n_r >= LOSS_MIN_ITEMS and n_b >= LOSS_MIN_ITEMS:
        s_r, s_b = m["lost_recent"] / n_r, m["lost_base"] / n_b
        if s_r >= LOSS_SHARE and s_r - s_b >= LOSS_RISE and binomial_tail(m["lost_recent"], n_r, s_b) < LOSS_P:
            add("fetch_loss", "%s of %s China relevant items (%s) lost at fetch over %d days, against %s of %s (%s) "
                "in the %d days before" % (_num(m["lost_recent"]), _num(n_r), _pct(m["lost_recent"], n_r), RECENT_DAYS,
                                           _num(m["lost_base"]), _num(n_b), _pct(m["lost_base"], n_b),
                                           WINDOW_DAYS - RECENT_DAYS))
    silent = m.get("silent_days")
    if silent is not None and silent >= ZERO_DAYS and m["base_days"] >= ZERO_DAYS:
        rate = m["relevant"] / m["base_days"]
        if rate * silent >= ZERO_EXPECTED:
            add("no_relevant", "no China relevant item in %.1f days, against %s in the %.0f days before (%.1f a day, "
                "%.0f expected in the silence)" % (silent, _num(m["relevant"]), m["base_days"], rate, rate * silent))
    return out


def _reference_ids(conn, sources: Optional[List[Dict]]) -> List[str]:
    """The reference sources this collector polls (sources/reference.yaml), or, when the file cannot be
    read, every source with feed health on record."""
    if sources is None:
        try:
            from pipeline import reference
            sources = reference.collectable(reference.load_sources())
        except Exception as exc:  # noqa: BLE001  a broken side file must not silence the other checks
            print("input watch: reference sources unreadable (%s: %s); checking those on record"
                  % (type(exc).__name__, exc), file=sys.stderr)
            return sorted(r[0] for r in conn.execute("SELECT DISTINCT source_id FROM reference_feed_health"))
    return sorted(s["id"] for s in sources)


def reference_metrics(conn, source_ids: List[str], now: dt.datetime) -> Dict[str, Dict]:
    out = {s: {"lost": 0, "fetched": 0, "expired": 0, "newest": None} for s in source_ids}
    if not source_ids:
        return out
    recent = _iso(now - dt.timedelta(days=RECENT_DAYS))
    mk = _marks(source_ids)
    # A page the reference fetch found to carry no article at all (a breaking news alert, a video with a
    # short caption: fail_reason no_text or short_text, pipeline/reference.py) is not a lost item: 3,174 of
    # CGTN's 4,056 failures on the 2026-11-09 snapshot are such alerts. It is counted in neither part.
    for sid, status, n in conn.execute(
            """SELECT source_id, status, COUNT(*) FROM reference_items
               WHERE source_id IN (%s) AND discovered_at >= ?
                 AND NOT (status = 'failed' AND (COALESCE(fail_reason, '') LIKE 'no_text:%%' OR COALESCE(fail_reason, '') LIKE 'short_text:%%'))
               GROUP BY 1, 2""" % mk, source_ids + [recent]):
        if status in ("failed", "blocked_robots", "paywalled"):
            out[sid]["lost"] += n
        elif status == "fetched":
            out[sid]["fetched"] += n
        elif status == "expired":
            out[sid]["expired"] += n
    for sid, newest in conn.execute(
            "SELECT source_id, MAX(discovered_at) FROM reference_items WHERE source_id IN (%s) GROUP BY 1" % mk,
            source_ids):
        out[sid]["newest"] = _parse(newest)
    return out


def check_reference(sid: str, m: Dict, now: dt.datetime) -> List[Dict]:
    out = []
    n = m["lost"] + m["fetched"]
    if n >= REF_MIN_ITEMS and m["lost"] / n >= REF_FAIL_SHARE:
        out.append({"check": "ref_failing", "input": sid,
                    "line": "reference %s: %s of %s items (%s) failed to fetch or extract over %d days"
                            % (sid, _num(m["lost"]), _num(n), _pct(m["lost"], n), RECENT_DAYS)})
    if m["newest"] is None:
        out.append({"check": "ref_silent", "input": sid, "line": "reference %s: no item on record" % sid})
    else:
        h = (now - m["newest"]).total_seconds() / 3600
        if h > REF_SILENT_HOURS:
            out.append({"check": "ref_silent", "input": sid,
                        "line": "reference %s: no new item in %.0f hours (limit %d)" % (sid, h, REF_SILENT_HOURS)})
    return out


def _stage_finished(conn, stage: str) -> Optional[dt.datetime]:
    r = conn.execute("SELECT MAX(finished_at) FROM run_log WHERE stage = ? AND ok = 1", (stage,)).fetchone()
    return _parse(r[0]) if r else None


def backlog_series(conn, runs: int = BACKLOG_RUNS) -> List[int]:
    """What the last `runs` finished fetch runs left queued at their deadline, oldest first."""
    vals = []
    for (counts,) in conn.execute(
            "SELECT counts FROM run_log WHERE stage = 'fetch' AND finished_at IS NOT NULL ORDER BY id DESC LIMIT ?",
            (runs,)):
        try:
            vals.append(int((json.loads(counts or "{}") or {}).get("skipped_deadline") or 0))
        except (ValueError, TypeError, AttributeError):
            vals.append(0)
    return list(reversed(vals))


def queued_now(conn, outlets: List[Dict]) -> int:
    """China relevant articles waiting for a first fetch from outlets this collector fetches (items of
    outlets dropped with the relay stay queued for good and are left out)."""
    from pipeline import registry
    mine = {o["id"] for o in outlets if o.get("active") and registry.is_mine(o["id"], outlets)}
    return sum(n for oid, n in conn.execute(
        "SELECT outlet_id, COUNT(*) FROM articles WHERE status = 'queued' AND gate_relevant = 1 GROUP BY 1")
        if oid in mine)


def check_global(conn, outlets: List[Dict], now: dt.datetime) -> List[Dict]:
    out = []
    for stage in ("discover", "fetch"):
        t = _stage_finished(conn, stage)
        h = None if t is None else (now - t).total_seconds() / 3600
        if h is None or h > STAGE_STALE_HOURS:
            out.append({"check": "stage_stale", "input": stage,
                        "line": "no %s pass has finished %s" % (
                            stage, "on record" if h is None else "in %.0f hours (limit %d)" % (h, STAGE_STALE_HOURS))})
    s = backlog_series(conn)
    if len(s) >= BACKLOG_RUNS and s[-1] >= BACKLOG_FLOOR and all(a < b for a, b in zip(s, s[1:])):
        if s[-1] < BACKLOG_WORKFLOW_WARNS:     # from there on the workflow's own warning says it
            out.append({"check": "backlog_rising", "input": "fetch",
                        "line": "article fetch backlog rising: the last %d fetch runs left %s China relevant "
                                "items queued, %s queued now" % (len(s), ", ".join(_num(v) for v in s),
                                                                 _num(queued_now(conn, outlets)))})
    return out


# ---------------------------------------------------------------------------------------------------------
# The watch
# ---------------------------------------------------------------------------------------------------------

def check(conn, outlets: List[Dict], now: Optional[dt.datetime] = None, sources: Optional[List[Dict]] = None,
          detail: bool = False) -> Dict:
    """Every check, read only. Returns {"problems": [...], "key": key_inputs(...), "reference": [...],
    "metrics": {input: metrics} when detail}."""
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    problems = check_global(conn, outlets, now)
    discover_stale = any(p["check"] == "stage_stale" and p["input"] == "discover" for p in problems)
    ref_ids = _reference_ids(conn, sources)
    ref = reference_metrics(conn, ref_ids, now)
    for sid in ref_ids:
        problems += check_reference(sid, ref[sid], now)
    key = key_inputs(conn, outlets, now)
    metrics = {}
    for unit in key["units"]:
        m = unit_metrics(conn, unit["members"], now)
        metrics[unit["id"]] = m
        # With no discovery pass in a day every input looks unpolled: the global line says it once.
        problems += check_unit(unit, m, now, polls_meaningful=not discover_stale)
    out = {"problems": problems, "key": key, "reference": ref_ids, "now": _iso(now)}
    if detail:
        out["metrics"] = metrics
        out["reference_metrics"] = ref
    return out


def annotations(problems: List[Dict]) -> List[str]:
    """The ::warning lines, at most MAX_ANNOTATIONS; the last says how many more the log holds."""
    lines = ["::warning title=%s::%s" % (TITLE, _escape(p["line"])) for p in problems]
    if len(lines) > MAX_ANNOTATIONS:
        more = len(lines) - (MAX_ANNOTATIONS - 1)
        lines = lines[:MAX_ANNOTATIONS - 1] + ["::warning title=%s::%d more inputs at risk; see the input watch lines "
                                               "in this step's log" % (TITLE, more)]
    return lines


def _escape(text: str) -> str:
    """GitHub workflow command data: % and line breaks are encoded."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def summary_counts(result: Dict, seconds: float) -> Dict:
    key = result["key"]
    by_check = defaultdict(int)
    for p in result["problems"]:
        by_check[p["check"]] += 1
    return {"key_inputs": len(key["units"]), "key_labels": key["covered"], "window_labels": key["total"],
            "window_days": key["window_days"], "reference_sources": len(result["reference"]),
            "problems": len(result["problems"]), "by_check": dict(sorted(by_check.items())),
            "flags": [p["line"] for p in result["problems"]], "seconds": round(seconds, 2)}


def run(conn, outlets: Optional[List[Dict]] = None, now: Optional[dt.datetime] = None,
        sources: Optional[List[Dict]] = None, out=None, show_all: bool = False) -> Dict:
    """Run every check, print the lines and annotations, record a run_log row. Never raises: a failure of
    the watch is itself printed as a warning and recorded with ok 0."""
    out = out or sys.stdout
    started = time.monotonic()
    run_id = dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    log_id = store.start_stage(conn, run_id, STAGE)
    try:
        if outlets is None:
            from pipeline import registry
            outlets = registry.load_outlets()
        result = check(conn, outlets, now=now, sources=sources, detail=show_all)
    except Exception as exc:  # noqa: BLE001  the watch must never stop the run it watches
        conn.rollback()
        msg = "%s: %s" % (type(exc).__name__, exc)
        print("::warning title=Input watch failed::%s" % _escape(msg), file=out)
        store.finish_stage(conn, log_id, False, {"error": msg}, notes=msg[:500])
        return {"error": msg}
    seconds = time.monotonic() - started
    key = result["key"]
    print("input watch: %d key inputs carried %s of %s state origin and unchecked state sourcing items (%s) over %d "
          "days; %d reference sources; %d problems; %.1fs" % (
              len(key["units"]), _num(key["covered"]), _num(key["total"]), _pct(key["covered"], key["total"]),
              key["window_days"], len(result["reference"]), len(result["problems"]), seconds), file=out)
    if show_all:
        for u in key["units"]:
            print("  key %2d %-28s %5d items  %s" % (u["rank"], u["id"], u["weight"],
                                                    json.dumps(result["metrics"][u["id"]], default=str)), file=out)
        for sid in result["reference"]:
            print("  ref    %-28s %s" % (sid, json.dumps(result["reference_metrics"][sid], default=str)), file=out)
    for p in result["problems"]:
        print("input watch: " + p["line"], file=out)
    for line in annotations(result["problems"]):
        print(line, file=out)
    counts = summary_counts(result, seconds)
    store.finish_stage(conn, log_id, True, counts, notes="; ".join(counts["flags"])[:2000])
    result["counts"] = counts
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--now", help="the time to judge against, ISO 8601 (default: now); for reading a snapshot")
    ap.add_argument("--all", action="store_true", help="also print every key input's and reference source's figures")
    args = ap.parse_args(argv)
    now = _parse(args.now) if args.now else None
    if args.now and now is None:
        ap.error("--now is not an ISO 8601 time: %s" % args.now)
    conn = store.connect()
    try:
        run(conn, now=now, show_all=args.all)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
