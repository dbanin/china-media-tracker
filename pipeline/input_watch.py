"""Input watch: after collection, checks that the inputs the findings rest on are still arriving.

The state origin and unchecked state sourcing labels come from hundreds of outlets, and their failures
were silent: on 2026-10-30 a green run hid an 8 day article fetch starvation, the reference source
ref_cgtn_en failed text extraction on most of its items and nothing said so, and id_antara loses about
half its feed items to saturation. This module runs after collection in every run, reads the database
only (no network), and reports what is at risk:

  inputs       every input this collector polls that carried a current state origin or unchecked state
               sourcing item over the last WINDOW_DAYS (an input is an outlet, or a registry network of
               outlets counted once). On the snapshot of 2026-11-09 that is 519 inputs carrying 4,415 of
               the window's 4,501 items (98 percent); the other 86 came from outlets no longer polled.
               For each: no successful feed poll in POLL_OK_HOURS, feed entries stale for longer than its
               own entry rate allows, feed saturation over RECENT_DAYS, China relevant items lost at fetch
               (nearly all of them, or a rise against its own earlier share), and a silence of China
               relevant items its own rate makes improbable.
  groups       inputs too small to be judged alone, pooled by country, on the rules that need volume.
  reference    every reference source: failure share of its items over RECENT_DAYS, and no new item
               in REF_SILENT_HOURS.
  global       the article fetch backlog rising run after run (the 2026-10-30 lesson), no discovery or
               fetch pass finished lately.

Every rule judges only what the input's own volume can support: a rule that cannot be judged at an
input's volume says nothing for it. A problem is new when the same input showed no problem of its kind
from NEW_HOURS to NEW_LOOKBACK days earlier, judged on the data as it stood then; otherwise it is ongoing
(an outlet dark for days behind HTTP 403, a robots.txt refusal or a bot challenge, a feed saturated for
weeks), and the notice says how many such inputs there are, what they carried, why and since when. New
problems get a GitHub Actions warning titled "Input at risk", one per input, ranked by the items at stake
(at most MAX_ANNOTATIONS, the last saying how many more the log holds); ongoing ones are counted in one
notice and one log line, so a new risk is never hidden behind old ones. Every problem prints one log line, and the
run is recorded in run_log under stage input_watch. Nothing here is a label, a count or a denominator,
and nothing is ever changed but the run_log row. The thresholds below are explained from the snapshot of
2026-11-09.

Usage: python -m pipeline.input_watch [--now 2026-11-09T00:27:00+00:00] [--all]
"""
import argparse
import bisect
import datetime as dt
import json
import math
import sys
import time
import uuid
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from pipeline import config, store

UTC = dt.timezone.utc
STAGE = "input_watch"
TITLE = "Input at risk"
NOTICE_TITLE = "Inputs at risk, ongoing"

# --- which inputs are watched ---------------------------------------------------------------------------
# Eight weeks: long enough that an outlet whose input broke a fortnight ago is still watched and weighed by
# what it carried before (it would drop out of a short window just as it most needs watching), short
# enough to follow the registry. On the snapshot the 56 days hold 6,835 of the 7,964 current A and B
# labels, 4,501 distinct items once each input's copies of one title group count once, from 553 inputs, 519
# of them polled by this collector. Every polled one is watched: 183 carried a single item, and the 456
# that carried ten or fewer hold 1,216 items (27 percent), too many to leave out.
WINDOW_DAYS = 56
# A problem the same input already showed this long ago (judged on the data as it stood then) is ongoing:
# it is counted in the notice, not warned again. Runs come a median 2.7 hours apart (registry.py), so a new
# problem is warned on about nine runs, a day's worth, before it joins the ongoing count.
NEW_HOURS = 24
# And a problem that comes and goes is warned once a week at most: it is new only if the input showed none
# of its kind at any checkpoint from NEW_HOURS before now back NEW_LOOKBACK days more, CHECK_EVERY_HOURS
# apart. Over the snapshot's fetch runs since 2026-10-13 the day before alone gave 164 new problems, 24 of
# them new more than once (a paywall share or a saturated feed hovering at its line); the week gives 126,
# 9 of them more than once (before the saturation rule of _mark_new, which leaves 80).
NEW_LOOKBACK = 7
CHECK_EVERY_HOURS = 6

# --- per input checks -----------------------------------------------------------------------------------
# Every feed is polled in every run; an input with no successful poll in a day has failed about eight
# polls in a row. A poll failure needs no volume to judge, so this holds for every input.
POLL_OK_HOURS = 24
# A feed can answer and still serve old items (CityNews served 2022 items). The newest entry must be at
# least ENTRY_STALE_HOURS old, and so old that the input's own habit (its days with a new item discovered,
# from the window's start to that entry) makes the gap improbable, by the same test as the silence below: a
# weekly paper is not stale after four days, a daily is.
ENTRY_STALE_HOURS = 72
# The days every recent share is taken over.
RECENT_DAYS = 7
# Share of an input's feed items lost between polls (feed_polls.missed_estimate, current estimator only)
# over RECENT_DAYS, out of the items seen plus those missed. The site warns at
# config.FEED_SATURATION_WARNING_SHARE (25 percent) already; the watch speaks only from 40 percent, on
# SATURATION_MIN_ITEMS or more items. On the snapshot 49 of the 519 inputs are at 40 percent or more, most
# of them for weeks: those are ongoing, and only a jump from under the site's own line a day before is
# warned (_mark_new). Over the snapshot's runs since 2026-10-13 that happened once (mu_defimedia).
SATURATION_SHARE = 0.40
SATURATION_MIN_ITEMS = 100     # items seen plus missed over RECENT_DAYS, below which a share says little
# China relevant items lost at fetch (failed, extraction_empty among them, paywalled, blocked_robots), out
# of those with a fetch outcome, over RECENT_DAYS. Two rules:
#   fetch_dark  DARK_LOSS_SHARE or more of LOSS_MIN_ITEMS or more recent outcomes lost: nothing gets
#               through (HTTP 403 to the runner, a bot challenge, a new paywall). It needs no baseline, so
#               an outlet blocked from its first day is seen; it is new only when the share is a change
#               against the input's own earlier share (one sided Fisher test below LOSS_P), or the input
#               has no earlier share to compare with.
#   fetch_loss  a rise: the recent share LOSS_SHARE or more and LOSS_RISE above the input's share in the
#               rest of the window, LOSS_MIN_ITEMS outcomes on each side, and the split improbable if both
#               parts shared one rate (one sided Fisher exact test below LOSS_P; the test treats the
#               earlier share as an estimate, so ten items at a baseline of nought cannot fire it alone).
# On the snapshot the Postmedia papers rose from 35 to 99 percent; tr_ekonomim and tr_dunya went to HTTP 403.
DARK_LOSS_SHARE = 0.90
LOSS_RISE = 0.20
LOSS_SHARE = 0.30
LOSS_MIN_ITEMS = 10
LOSS_P = 0.001
# No China relevant item for at least ZERO_DAYS, and for long enough that a working input would almost
# surely have shown one. The input's habit is known only from its own record: the calendar days with a China
# relevant item, out of the days from the window's start (or its first discovery, if later) to its newest
# such item. Days, not items, since items come in bursts (fr_lacroix had 107 items in 52 days and silences
# of up to 5.4 days; a count of items would have called its 3.6 day silence on the snapshot improbable).
# With that share uncertain (silence_score), the rule fires when the chance of the silence is under
# exp(-ZERO_EXPECTED), below 0.001, so it is sound at any volume: a small input needs a long silence, and
# one with fewer than ZERO_MIN_DAYS active days is never judged.
ZERO_DAYS = 3
ZERO_EXPECTED = 7.0
# Days with an item below which no silence is judged at all: with a uniform prior, one active day of one
# would make 55 silent days look improbable, and a few days are no record of an input's habits.
ZERO_MIN_DAYS = 5
# An input is "judged alone" when a silence of RECENT_DAYS from now would fire the rule above on its own
# items (about one China relevant item a day). Smaller inputs are pooled by country into one group per
# country, judged on the summed items by the silence and fetch rules; a group can be judged where its
# members cannot.

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
# Ongoing problems named in the notice, the largest first.
NOTICE_NAMES = 5

LOST = ("failed", "paywalled", "blocked_robots")
DONE = ("classified", "awaiting_llm", "llm_submitted", "fetched")
DARK = ("no_poll", "stale_entries", "no_relevant", "fetch_dark")
VOLUME_CHECKS = ("no_relevant", "fetch_dark", "fetch_loss")
CHECK_NAMES = {"no_poll": "no successful feed poll", "stale_entries": "feed entries stale",
               "no_relevant": "no China relevant item", "fetch_dark": "nearly all lost at fetch",
               "saturation": "feed saturation", "fetch_loss": "fetch loss rising",
               "ref_failing": "reference failing", "ref_silent": "reference silent",
               "stage_stale": "stage stale", "backlog_rising": "backlog rising"}


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


def _days(a: dt.datetime, b: dt.datetime) -> float:
    return (b - a).total_seconds() / 86400


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


def _lchoose(a: int, b: int) -> float:
    return math.lgamma(a + 1) - math.lgamma(b + 1) - math.lgamma(a - b + 1)


def fisher_tail(k_r: int, n_r: int, k_b: int, n_b: int) -> float:
    """One sided Fisher exact test: the chance that k_r or more of the k_r + k_b losses fall among the n_r
    recent outcomes if recent and earlier shared one loss rate (hypergeometric upper tail). Unlike a
    binomial against the earlier share, it allows for that share being an estimate, so it stays sound
    when the earlier part is small."""
    if k_r <= 0:
        return 1.0
    big_n, k = n_r + n_b, k_r + k_b
    denom = _lchoose(big_n, n_r)
    total = 0.0
    for i in range(k_r, min(n_r, k) + 1):
        if n_r - i > big_n - k:
            continue
        total += math.exp(_lchoose(k, i) + _lchoose(big_n - k, n_r - i) - denom)
    return min(1.0, total)


def silence_score(active: int, days: int, silent_days: float) -> float:
    """-log of the chance that an input which had an item on `active` of `days` calendar days shows none
    for `silent_days` more. Days, not items: a burst of forty items in one day (a sitemap catching up)
    says nothing about tomorrow, a day with an item does. The share of days with an item is uncertain
    (a Beta posterior from a uniform prior), so the chance is the Beta geometric predictive
    B(a, b + s) / B(a, b), a = active + 1, b = days - active + 1. Compared with ZERO_EXPECTED. An input with
    an item every day for a month fires after 2.3 silent days, one with an item on half its days after 12.5,
    one with an item on 5 days of 30 only after 63, so never within the window; fewer than ZERO_MIN_DAYS
    active days never fire it."""
    if active < ZERO_MIN_DAYS or days < 1 or silent_days <= 0:
        return 0.0
    a, b, s = active + 1.0, max(0, days - active) + 1.0, float(silent_days)
    return math.lgamma(b) + math.lgamma(a + b + s) - math.lgamma(a + b) - math.lgamma(b + s)


def _count_days(dates: List[str], lo: str, hi: str) -> int:
    """Dates (sorted, YYYY-MM-DD) from lo to hi inclusive."""
    return bisect.bisect_right(dates, hi) - bisect.bisect_left(dates, lo)


def _span(lo: dt.datetime, hi: dt.datetime) -> int:
    """Calendar days from lo's to hi's, both counted."""
    return max(0, (hi.date() - lo.date()).days + 1)


def _marks(ids) -> str:
    return ",".join("?" * len(ids))


def _reason(status: str, fail_reason) -> str:
    """A short why for an item lost at fetch: paywall, robots.txt, HTTP 403, bot challenge, extraction."""
    if status == "paywalled":
        return "paywall"
    if status == "blocked_robots":
        return "robots.txt"
    r = str(fail_reason or "failed").strip()
    if r.startswith("unreadable:"):
        r = r[len("unreadable:"):].strip()
    r = r.split(":")[0].strip() or "failed"
    return r[:40]


def _count(n: int, noun: str) -> str:
    return "%s %s%s" % (_num(n), noun, "" if n == 1 else "s")


def _items(n: int) -> str:
    return _count(n, "item")


def _first(counter: Counter, default: str, prefix: str = "feed ") -> str:
    """The most common key of a counter, as a short why."""
    if not counter:
        return default
    return prefix + sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _feed_errors(counter: Counter) -> str:
    return ", ".join("%s on %d feed%s" % (k, v, "" if v == 1 else "s")
                     for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:2])


def _top(counter: Counter, n: int = 2) -> str:
    return ", ".join("%s %s" % (k, _num(v)) for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n])


# ---------------------------------------------------------------------------------------------------------
# What the checks read, loaded once
# ---------------------------------------------------------------------------------------------------------

def _collectable(outlets: List[Dict]) -> List[Dict]:
    from pipeline import registry
    return registry.collectable(outlets)


class Facts:
    """Everything the per input checks read, loaded in a few grouped queries from `start` on, so that any
    input can be judged at any time from then to now (now, a day before now for the new or ongoing test,
    and past runs in a replay) without another query. Times are ISO strings in UTC, compared as strings,
    as the database stores them."""

    def __init__(self, conn, start: dt.datetime, asof: Optional[dt.datetime] = None):
        self.start = start
        self.asof = (asof or dt.datetime.now(UTC)).astimezone(UTC)     # when the latest state was read
        s = _iso(start)
        # current state origin and unchecked state sourcing items: (time, title group) per outlet
        self.ab = defaultdict(list)
        for oid, g, t in conn.execute(
                """SELECT a.outlet_id, COALESCE(a.dup_group_id, -a.id), a.discovered_at FROM classifications c
                   JOIN articles a ON a.id = c.article_id
                   WHERE c.is_current = 1 AND c.category IN ('A', 'B') AND a.discovered_at >= ?""", (s,)):
            self.ab[oid].append((t, g))
        for v in self.ab.values():
            v.sort()
        # China relevant articles: sorted times with prefix counts of lost and done outcomes
        rows = defaultdict(list)
        for oid, t, status in conn.execute(
                "SELECT outlet_id, discovered_at, status FROM articles WHERE gate_relevant = 1 AND discovered_at >= ?",
                (s,)):
            rows[oid].append((t, 1 if status in LOST else (2 if status in DONE else 0)))
        self.rel = {}
        for oid, v in rows.items():
            v.sort()
            times, lost, done, done_times = [], [0], [0], []
            for t, k in v:
                times.append(t)
                lost.append(lost[-1] + (k == 1))
                done.append(done[-1] + (k == 2))
                if k == 2:
                    done_times.append(t)
            self.rel[oid] = (times, lost, done, done_times)
        self.rel_dates = {oid: sorted({t[:10] for t in r[0]}) for oid, r in self.rel.items()}
        # feed polls under the current missed items estimator, per outlet and day
        self.polls = defaultdict(list)
        for oid, d, new, missed, polls, sat in conn.execute(
                """SELECT outlet_id, date, SUM(new_items), SUM(missed_estimate), SUM(polls), SUM(saturated)
                   FROM feed_polls WHERE date >= ? AND estimator = ? GROUP BY 1, 2""",
                ((start - dt.timedelta(days=1)).date().isoformat(), config.MISSED_ESTIMATOR_VERSION)):
            self.polls[oid].append((d, new or 0, missed or 0.0, polls or 0, sat or 0))
        for v in self.polls.values():
            v.sort()
        # items discovered per outlet and day (all items, relevant or not), and each outlet's first day
        self.first_day = {oid: d for oid, d in conn.execute(
            "SELECT outlet_id, MIN(date) FROM daily_outlet_discovery WHERE discovered > 0 GROUP BY 1")}
        self.disc = defaultdict(list)
        for oid, d, n in conn.execute(
                "SELECT outlet_id, date, discovered FROM daily_outlet_discovery WHERE discovered > 0 AND date >= ?",
                (start.date().isoformat(),)):
            self.disc[oid].append((d, n))
        for v in self.disc.values():
            v.sort()
        self.disc_dates = {oid: [d for d, _ in v] for oid, v in self.disc.items()}
        # feed health as it stands now (the table keeps no history)
        self.health = defaultdict(list)
        for oid, ok, newest, err, fails in conn.execute(
                "SELECT outlet_id, last_ok, newest_entry, last_error, consecutive_failures FROM feed_health"):
            self.health[oid].append({"last_ok": _parse(ok), "newest": _parse(newest), "error": err,
                                     "fails": int(fails or 0)})
        # finished discovery passes, newest first: a feed never polled successfully has failed since the
        # pass its run of consecutive failures began
        self.discover_runs = [_parse(r[0]) for r in conn.execute(
            """SELECT finished_at FROM run_log WHERE stage = 'discover' AND finished_at IS NOT NULL
               ORDER BY finished_at DESC LIMIT 2000""")]
        self.discover_runs = [t for t in self.discover_runs if t]

    def _streak_start(self, fails: int) -> Optional[dt.datetime]:
        if fails <= 0 or not self.discover_runs:
            return None
        return self.discover_runs[min(fails, len(self.discover_runs)) - 1]

    def weights(self, outlets: List[Dict], now: dt.datetime) -> Dict[str, set]:
        """Title groups of current state origin and unchecked state sourcing items per input over the
        window to `now` (an input is an outlet, or a registry network counted once)."""
        by_id = {o["id"]: o for o in outlets}
        lo, hi = _iso(now - dt.timedelta(days=WINDOW_DAYS)), _iso(now)
        out = defaultdict(set)
        for oid, v in self.ab.items():
            i, j = bisect.bisect_left(v, (lo,)), bisect.bisect_right(v, (hi + "￿",))
            if j > i:
                out[unit_of(oid, by_id)].update(g for _, g in v[i:j])
        return out

    def metrics(self, members: List[str], now: dt.datetime) -> Dict:
        """Everything the per input checks read, for the outlets of one input (or group), as of `now`."""
        m = {"feeds": 0, "last_ok": None, "never_ok": False, "errors": Counter(), "newest_entry": None,
             "entries": 0, "entry_span": 0, "sat_new": 0, "sat_missed": 0.0, "sat_polls": 0,
             "sat_saturated": 0, "lost_recent": 0, "done_recent": 0, "lost_base": 0, "done_base": 0,
             "relevant": 0, "rel_days": 0, "rel_span": 0, "rel_days_all": 0, "span_all": 0, "newest_relevant": None, "newest_done": None,
             "base_days": 0.0, "silent_days": None}
        if not members:
            return m
        horizon = self.asof + dt.timedelta(hours=1)
        good, from_streak = [], []
        for oid in members:
            for f in self.health.get(oid, ()):
                m["feeds"] += 1
                # The table holds the latest state only. Judged at a past time (the day before, a replay),
                # a feed that has polled well since is taken as well then too, so the past is never
                # judged worse than the data shows.
                if f["last_ok"] is not None:
                    good.append(min(f["last_ok"], now))
                else:
                    since = self._streak_start(f["fails"])    # never polled successfully: failing since
                    if since is not None and since <= now:
                        from_streak.append(since)
                    elif since is not None:
                        good.append(now)
                if f["error"] and f["fails"]:
                    m["errors"][f["error"]] += 1
                # An entry dated in the future (a feed's wrong zone) would hide a stale feed: it is skipped.
                # One newer than `now` but not in the future (judged at a past time) was fresh then.
                newest = f["newest"]
                if newest and newest > horizon:
                    newest = None
                if newest and newest > now:
                    newest = now
                if newest and (m["newest_entry"] is None or newest > m["newest_entry"]):
                    m["newest_entry"] = newest
        m["last_ok"] = max(good + from_streak) if (good or from_streak) else None
        m["never_ok"] = bool(from_streak) and not good
        first = min((self.first_day[o] for o in members if o in self.first_day), default=None)
        start = now - dt.timedelta(days=WINDOW_DAYS)
        f = _parse(first + "T00:00:00+00:00") if first else None
        if f and f > start:
            start = f
        # feed polls over the recent days (by day, as feed_polls is kept)
        first_day = (now - dt.timedelta(days=RECENT_DAYS)).date().isoformat()
        last_day = now.date().isoformat()
        for oid in members:
            for d, new, missed, polls, sat in self.polls.get(oid, ()):
                if first_day < d <= last_day:
                    m["sat_new"] += new
                    m["sat_missed"] += missed
                    m["sat_polls"] += polls
                    m["sat_saturated"] += sat
        # China relevant items: outcomes recent and before, the newest one and the newest that got through
        lo, rec, hi = _iso(now - dt.timedelta(days=WINDOW_DAYS)), _iso(now - dt.timedelta(days=RECENT_DAYS)), _iso(now)
        newest = newest_done = None
        for oid in members:
            r = self.rel.get(oid)
            if not r:
                continue
            times, lost, done, done_times = r
            i, k, j = bisect.bisect_left(times, lo), bisect.bisect_left(times, rec), bisect.bisect_right(times, hi)
            k = max(i, min(k, j))
            m["lost_recent"] += lost[j] - lost[k]
            m["done_recent"] += done[j] - done[k]
            m["lost_base"] += lost[k] - lost[i]
            m["done_base"] += done[k] - done[i]
            m["relevant"] += j - i
            if j > i and (newest is None or times[j - 1] > newest):
                newest = times[j - 1]
            d = bisect.bisect_right(done_times, hi)
            if d and (newest_done is None or done_times[d - 1] > newest_done):
                newest_done = done_times[d - 1]
        newest, newest_done = _parse(newest), _parse(newest_done)
        m.update(newest_relevant=newest, newest_done=newest_done,
                 base_days=max(0.0, _days(start, newest or start)),
                 silent_days=_days(newest, now) if newest else None)
        # days with a China relevant item, from the window's start to the newest one and to now
        a = start.date().isoformat()
        if newest is not None:
            m["rel_days"] = self._active(self.rel_dates, members, a, newest.date().isoformat())
            m["rel_span"] = _span(start, newest)
        m["rel_days_all"] = self._active(self.rel_dates, members, a, now.date().isoformat())
        m["span_all"] = _span(start, now)
        # days with a new item discovered (relevant or not) from the window's start to the newest feed
        # entry: the input's entry rate
        if m["newest_entry"] is not None:
            m["entries"] = self._active(self.disc_dates, members, a, m["newest_entry"].date().isoformat())
            m["entry_span"] = _span(start, m["newest_entry"])
        return m

    @staticmethod
    def _active(table: Dict[str, List[str]], members: List[str], lo: str, hi: str) -> int:
        if len(members) == 1:
            return _count_days(table.get(members[0], []), lo, hi)
        days = set()
        for oid in members:
            v = table.get(oid, [])
            days.update(v[bisect.bisect_left(v, lo):bisect.bisect_right(v, hi)])
        return len(days)

    def lost_reasons(self, conn, members: List[str], now: dt.datetime) -> Counter:
        """Why the recent China relevant items of these outlets were lost at fetch (only for the few
        inputs that need a reason)."""
        out = Counter()
        rec, hi = _iso(now - dt.timedelta(days=RECENT_DAYS)), _iso(now)
        for status, reason, n in conn.execute(
                """SELECT status, fail_reason, COUNT(*) FROM articles WHERE outlet_id IN (%s) AND gate_relevant = 1
                   AND discovered_at >= ? AND discovered_at <= ? AND status IN ('failed', 'paywalled', 'blocked_robots')
                   GROUP BY 1, 2""" % _marks(members), list(members) + [rec, hi + "￿"]):
            out[_reason(status, reason)] += n
        return out


def unit_of(oid: str, by_id: Dict[str, Dict]) -> str:
    net = (by_id.get(oid) or {}).get("network")
    return "network:" + net if net else oid


# ---------------------------------------------------------------------------------------------------------
# Which inputs are watched
# ---------------------------------------------------------------------------------------------------------

def watched_inputs(conn, outlets: List[Dict], now: dt.datetime, facts: Optional[Facts] = None) -> Dict:
    """Every input this collector polls that carried a current state origin or unchecked state sourcing
    item over WINDOW_DAYS, the largest first. An input is an outlet, or a registry network of outlets
    counted once; its weight (the items at stake) is its distinct items (title groups,
    articles.dup_group_id), so ten papers carrying one widget copy weigh one item, not ten. Inputs this
    collector does not poll (inactive, or another collector's) still count in the total, so the share
    covered is honest about what the watch does not see.

    Returns {"units": [{"id", "members", "weight", "rank", "country"}], "total", "covered", "share",
    "unpolled", "window_days"}."""
    facts = facts or Facts(conn, now - dt.timedelta(days=WINDOW_DAYS), asof=now)
    by_id = {o["id"]: o for o in outlets}
    members = defaultdict(list)
    for o in sorted(_collectable(outlets), key=lambda o: o["id"]):
        members[unit_of(o["id"], by_id)].append(o["id"])
    items = facts.weights(outlets, now)
    total = sum(len(s) for s in items.values())
    units, covered = [], 0
    for uid, groups in sorted(items.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if uid not in members:
            continue     # not polled by this collector: it counts in the total but cannot be watched
        countries = Counter((by_id.get(o) or {}).get("country") or "" for o in members[uid])
        units.append({"id": uid, "members": members[uid], "weight": len(groups), "rank": len(units) + 1,
                      "country": sorted(countries.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]})
        covered += len(groups)
    return {"units": units, "total": total, "covered": covered, "unpolled": total - covered,
            "share": (covered / total) if total else 0.0, "window_days": WINDOW_DAYS}


def judged_alone(m: Dict) -> bool:
    """Whether a silence of RECENT_DAYS from now would fire the silence rule on the input's own items."""
    return silence_score(m["rel_days_all"], m["span_all"], RECENT_DAYS) >= ZERO_EXPECTED


# ---------------------------------------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------------------------------------

def _name(unit: Dict) -> str:
    if unit.get("group"):
        return "%s, %d small inputs" % (unit["id"].replace("group:", "country group "), len(unit["units"]))
    name = unit["id"].replace("network:", "network ")
    if len(unit["members"]) > 1:
        name += " (%d outlets)" % len(unit["members"])
    return name


def check_unit(unit: Dict, m: Dict, now: dt.datetime, polls_meaningful: bool = True,
               only: Optional[tuple] = None) -> List[Dict]:
    """The problems of one input, each {"check", "input", "line", "stake", "category", "since", "why"}.
    `only` limits the rules run (a country group runs the volume rules only)."""
    out = []
    tag = "%s, %s at stake" % (_name(unit), _items(unit["weight"]))

    def add(check, text, since=None, why="", **extra):
        p = {"check": check, "input": unit["id"], "line": "%s: %s" % (tag, text), "stake": unit["weight"],
             "category": "dark" if check in DARK else "degraded", "since": since, "why": why}
        p.update(extra)
        out.append(p)

    def runs(check):
        return only is None or check in only

    if polls_meaningful and runs("no_poll"):
        if not m["feeds"]:
            add("no_poll", "no feed of it has ever been polled")
        elif m["last_ok"] is None:
            add("no_poll", "no successful feed poll on record", why=_first(m["errors"], "feed failing"))
        else:
            h = (now - m["last_ok"]).total_seconds() / 3600
            if h > POLL_OK_HOURS:
                add("no_poll", "no successful feed poll in %.0f hours (limit %d)%s%s" % (
                    h, POLL_OK_HOURS, ", none on record" if m["never_ok"] else "",
                    ", last error %s" % _feed_errors(m["errors"]) if m["errors"] else ""),
                    since=m["last_ok"], why=_first(m["errors"], "feed failing"))
    if m["newest_entry"] is not None and runs("stale_entries"):
        s = _days(m["newest_entry"], now)
        if s * 24 > ENTRY_STALE_HOURS and silence_score(m["entries"], m["entry_span"], s) >= ZERO_EXPECTED:
            add("stale_entries", "newest feed entry is %.0f hours old, against new items on %d of the %d days "
                "before it" % (s * 24, m["entries"], m["entry_span"]),
                since=m["newest_entry"], why="feed entries stale")
    flow = m["sat_new"] + m["sat_missed"]
    if runs("saturation") and flow >= SATURATION_MIN_ITEMS and m["sat_missed"] / flow >= SATURATION_SHARE:
        add("saturation", "an estimated %s of %s feed items (%s) missed to feed saturation over %d days, "
            "%d of %d polls saturated" % (_num(m["sat_missed"]), _num(flow), _pct(m["sat_missed"], flow),
                                          RECENT_DAYS, m["sat_saturated"], m["sat_polls"]), why="saturation")
    n_r = m["lost_recent"] + m["done_recent"]
    n_b = m["lost_base"] + m["done_base"]
    if n_r >= LOSS_MIN_ITEMS:
        s_r = m["lost_recent"] / n_r
        s_b = m["lost_base"] / n_b if n_b else None
        p = fisher_tail(m["lost_recent"], n_r, m["lost_base"], n_b) if n_b >= LOSS_MIN_ITEMS else None
        before = (", against %s of %s (%s) in the %d days before" % (
            _num(m["lost_base"]), _num(n_b), _pct(m["lost_base"], n_b), WINDOW_DAYS - RECENT_DAYS)
                  if n_b else ", none before")
        if s_r >= DARK_LOSS_SHARE and runs("fetch_dark"):
            add("fetch_dark", "%s of %s China relevant items (%s) lost at fetch over %d days%s" % (
                _num(m["lost_recent"]), _num(n_r), _pct(m["lost_recent"], n_r), RECENT_DAYS, before),
                why="lost at fetch",
                steady=(p is not None and p >= LOSS_P))
        elif (runs("fetch_loss") and n_b >= LOSS_MIN_ITEMS and s_r >= LOSS_SHARE and s_r - s_b >= LOSS_RISE
              and p < LOSS_P):
            add("fetch_loss", "%s of %s China relevant items (%s) lost at fetch over %d days%s" % (
                _num(m["lost_recent"]), _num(n_r), _pct(m["lost_recent"], n_r), RECENT_DAYS, before),
                why="lost at fetch")
    silent = m.get("silent_days")
    if runs("no_relevant") and silent is not None and silent >= ZERO_DAYS and m["base_days"] >= ZERO_DAYS:
        if silence_score(m["rel_days"], m["rel_span"], silent) >= ZERO_EXPECTED:
            add("no_relevant", "no China relevant item in %.1f days, against %s in the %.0f days before, on %d of "
                "%d days" % (silent, _num(m["relevant"]), m["base_days"], m["rel_days"], m["rel_span"]),
                since=m["newest_relevant"], why="no China relevant item")
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
    hi = _iso(now) + "￿"
    mk = _marks(source_ids)
    # A page the reference fetch found to carry no article at all (a breaking news alert, a video with a
    # short caption: fail_reason no_text or short_text, pipeline/reference.py) is not a lost item: 3,174 of
    # CGTN's 4,056 failures on the 2026-11-09 snapshot are such alerts. It is counted in neither part.
    for sid, status, n in conn.execute(
            """SELECT source_id, status, COUNT(*) FROM reference_items
               WHERE source_id IN (%s) AND discovered_at >= ? AND discovered_at <= ?
                 AND NOT (status = 'failed' AND (COALESCE(fail_reason, '') LIKE 'no_text:%%' OR COALESCE(fail_reason, '') LIKE 'short_text:%%'))
               GROUP BY 1, 2""" % mk, source_ids + [recent, hi]):
        if status in ("failed", "blocked_robots", "paywalled"):
            out[sid]["lost"] += n
        elif status == "fetched":
            out[sid]["fetched"] += n
        elif status == "expired":
            out[sid]["expired"] += n
    for sid, newest in conn.execute(
            "SELECT source_id, MAX(discovered_at) FROM reference_items WHERE source_id IN (%s) AND discovered_at <= ? "
            "GROUP BY 1" % mk, source_ids + [hi]):
        out[sid]["newest"] = _parse(newest)
    return out


def reference_stakes(conn, now: dt.datetime) -> Dict[str, int]:
    """Items at stake for each reference source: the window's state origin copies paired with an
    original of that source (state_copies), title groups counted once."""
    try:
        return {sid: n for sid, n in conn.execute(
            """SELECT s.original_source, COUNT(DISTINCT COALESCE(a.dup_group_id, -a.id)) FROM state_copies s
               JOIN articles a ON a.id = s.copy_id
               WHERE s.current = 1 AND s.original_kind = 'reference' AND a.discovered_at >= ? AND a.discovered_at <= ?
               GROUP BY 1""", (_iso(now - dt.timedelta(days=WINDOW_DAYS)), _iso(now) + "￿"))}
    except Exception:  # noqa: BLE001  an older database without the table: no stake known
        return {}


def check_reference(sid: str, m: Dict, now: dt.datetime, stake: int = 0) -> List[Dict]:
    out = []
    tag = "reference %s, %s at stake" % (sid, _items(stake))
    n = m["lost"] + m["fetched"]
    if n >= REF_MIN_ITEMS and m["lost"] / n >= REF_FAIL_SHARE:
        out.append({"check": "ref_failing", "input": sid, "stake": stake, "category": "reference", "since": None,
                    "why": "reference failing",
                    "line": "%s: %s of %s items (%s) failed to fetch or extract over %d days"
                            % (tag, _num(m["lost"]), _num(n), _pct(m["lost"], n), RECENT_DAYS)})
    if m["newest"] is None:
        out.append({"check": "ref_silent", "input": sid, "stake": stake, "category": "reference", "since": None,
                    "why": "no item on record", "line": "%s: no item on record" % tag})
    else:
        h = (now - m["newest"]).total_seconds() / 3600
        if h > REF_SILENT_HOURS:
            out.append({"check": "ref_silent", "input": sid, "stake": stake, "category": "reference",
                        "since": m["newest"], "why": "reference silent",
                        "line": "%s: no new item in %.0f hours (limit %d)" % (tag, h, REF_SILENT_HOURS)})
    return out


def _stage_finished(conn, stage: str, now: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    if now is None:
        r = conn.execute("SELECT MAX(finished_at) FROM run_log WHERE stage = ? AND ok = 1", (stage,)).fetchone()
    else:
        r = conn.execute("SELECT MAX(finished_at) FROM run_log WHERE stage = ? AND ok = 1 AND finished_at <= ?",
                         (stage, _iso(now) + "￿")).fetchone()
    return _parse(r[0]) if r else None


def backlog_series(conn, runs: int = BACKLOG_RUNS, now: Optional[dt.datetime] = None) -> List[int]:
    """What the last `runs` finished fetch runs (to `now`, when given) left queued at their deadline,
    oldest first."""
    vals = []
    sql = "SELECT counts FROM run_log WHERE stage = 'fetch' AND finished_at IS NOT NULL"
    args = []
    if now is not None:
        sql += " AND finished_at <= ?"
        args.append(_iso(now) + "￿")
    for (counts,) in conn.execute(sql + " ORDER BY id DESC LIMIT ?", args + [runs]):
        try:
            vals.append(int((json.loads(counts or "{}") or {}).get("skipped_deadline") or 0))
        except (ValueError, TypeError, AttributeError):
            vals.append(0)
    return list(reversed(vals))


def queued_now(conn, outlets: List[Dict]) -> int:
    """China relevant articles waiting for a first fetch from outlets this collector fetches (items of
    outlets dropped with the relay stay queued for good and are left out)."""
    from pipeline import registry
    owners = {o["id"]: registry.fetcher_of(o) for o in outlets}
    mine = {o["id"] for o in outlets if o.get("active") and owners.get(o["id"], config.COLLECTOR) == config.COLLECTOR}
    return sum(n for oid, n in conn.execute(
        "SELECT outlet_id, COUNT(*) FROM articles WHERE status = 'queued' AND gate_relevant = 1 GROUP BY 1")
        if oid in mine)


def check_global(conn, outlets: List[Dict], now: dt.datetime, stake: int = 0, replay: bool = False) -> List[Dict]:
    out = []
    asof = now if replay else None
    for stage in ("discover", "fetch"):
        t = _stage_finished(conn, stage, asof)
        h = None if t is None else (now - t).total_seconds() / 3600
        if h is None or h > STAGE_STALE_HOURS:
            out.append({"check": "stage_stale", "input": stage, "stake": stake, "category": "global", "since": t,
                        "why": "stage stale",
                        "line": "no %s pass has finished %s" % (
                            stage, "on record" if h is None else "in %.0f hours (limit %d)" % (h, STAGE_STALE_HOURS))})
    s = backlog_series(conn, now=asof)
    if len(s) >= BACKLOG_RUNS and s[-1] >= BACKLOG_FLOOR and all(a < b for a, b in zip(s, s[1:])):
        if s[-1] < BACKLOG_WORKFLOW_WARNS:     # from there on the workflow's own warning says it
            out.append({"check": "backlog_rising", "input": "fetch", "stake": stake, "category": "global",
                        "since": None, "why": "backlog rising",
                        "line": "article fetch backlog rising: the last %d fetch runs left %s China relevant "
                                "items queued%s" % (len(s), ", ".join(_num(v) for v in s),
                                                    "" if replay else ", %s queued now" % _num(queued_now(conn, outlets)))})
    return out


# ---------------------------------------------------------------------------------------------------------
# The watch
# ---------------------------------------------------------------------------------------------------------

def _checkpoints(now: dt.datetime) -> List[dt.datetime]:
    """The earlier times a problem is looked for: from NEW_HOURS before now back NEW_LOOKBACK days more,
    every CHECK_EVERY_HOURS (about every other run)."""
    first = now - dt.timedelta(hours=NEW_HOURS)
    return [first - dt.timedelta(hours=CHECK_EVERY_HOURS * k)
            for k in range(NEW_LOOKBACK * 24 // CHECK_EVERY_HOURS + 1)]


def _mark_new(now_problems: List[Dict], before: List[Dict], sat_share_before: Optional[float] = 0.0) -> None:
    """A problem is new unless the same input showed one of its kind at an earlier checkpoint: any dark
    problem for a dark one (an input dark for days that now also stops polling is not news), the same
    check otherwise. Nearly all lost at fetch is new only if it is a change against the input's own
    earlier share: a paywall that has held for weeks is ongoing. Saturation is new only if a day before
    the input's share could be judged and was under the site's own warning line
    (config.FEED_SATURATION_WARNING_SHARE): a jump, not a feed drifting across SATURATION_SHARE (37 of the
    snapshot's 126 new problems since 2026-10-13 were such drifts), and not the day after a change of the
    missed items estimator, when every saturated feed reaches the minimum at once."""
    dark_before = {p["input"] for p in before if p["category"] == "dark"}
    checks_before = {(p["input"], p["check"]) for p in before}
    for p in now_problems:
        if p["category"] == "dark":
            p["new"] = p["input"] not in dark_before and not p.get("steady")
        else:
            p["new"] = (p["input"], p["check"]) not in checks_before
            if p["check"] == "saturation" and (sat_share_before is None
                                               or sat_share_before >= config.FEED_SATURATION_WARNING_SHARE):
                p["new"] = False


def _sat_share(m: Dict) -> Optional[float]:
    """The share of feed items missed to saturation, or None when too few items to judge it."""
    flow = m["sat_new"] + m["sat_missed"]
    return m["sat_missed"] / flow if flow >= SATURATION_MIN_ITEMS else None


def _groups(units: List[Dict], metrics: Dict[str, Dict], dark: set = frozenset()) -> List[Dict]:
    """The inputs too small to be judged alone, pooled by country (two or more per group). An input
    already dark by its own rules is left out: its past items would make the group's silence look like
    news, and its own line says it."""
    by_country = defaultdict(list)
    for u in units:
        if not judged_alone(metrics[u["id"]]) and u["id"] not in dark:
            by_country[u["country"] or "unknown"].append(u)
    out = []
    for country, us in sorted(by_country.items()):
        if len(us) < 2:
            continue
        out.append({"id": "group:" + country, "group": True, "units": [u["id"] for u in us],
                    "members": sorted({o for u in us for o in u["members"]}),
                    "weight": sum(u["weight"] for u in us), "country": country})
    return out


def evaluate(conn, facts: Facts, outlets: List[Dict], now: dt.datetime, sources: Optional[List[Dict]] = None,
             ref_ids: Optional[List[str]] = None, replay: bool = False, detail: bool = False,
             reasons: bool = True) -> Dict:
    """Every check at `now` and, for the inputs with a problem, at each earlier checkpoint, on facts
    loaded once."""
    points = _checkpoints(now)
    before = points[0]
    watch = watched_inputs(conn, outlets, now, facts)
    problems = check_global(conn, outlets, now, stake=watch["total"], replay=replay)
    for p in problems:
        p["new"] = True
    discover_stale = any(p["check"] == "stage_stale" and p["input"] == "discover" for p in problems)
    # reference sources
    if ref_ids is None:
        ref_ids = _reference_ids(conn, sources)
    stakes = reference_stakes(conn, now)
    ref_now = reference_metrics(conn, ref_ids, now)
    ref_problems = [p for sid in ref_ids for p in check_reference(sid, ref_now[sid], now, stakes.get(sid, 0))]
    if ref_problems:
        past = []
        for t in points:
            ref_then = reference_metrics(conn, sorted({p["input"] for p in ref_problems}), t)
            past += [q for sid, m in ref_then.items() for q in check_reference(sid, m, t)]
        _mark_new(ref_problems, past)
    # inputs, then the country groups of the small ones
    metrics, judged, unit_problems = {}, {}, []
    for unit in watch["units"]:
        m = facts.metrics(unit["members"], now)
        metrics[unit["id"]] = m
        judged[unit["id"]] = judged_alone(m)
        # With no discovery pass in a day every input looks unpolled: the global line says it once.
        cur = check_unit(unit, m, now, polls_meaningful=not discover_stale)
        if cur:
            past = [q for t in points for q in check_unit(unit, facts.metrics(unit["members"], t), t,
                                                           polls_meaningful=not discover_stale)]
            _mark_new(cur, past, _sat_share(facts.metrics(unit["members"], before)))
            unit_problems += cur
    groups = _groups(watch["units"], metrics, {p["input"] for p in unit_problems if p["category"] == "dark"})
    group_judged = set()
    for g in groups:
        m = facts.metrics(g["members"], now)
        metrics[g["id"]] = m
        if judged_alone(m):
            group_judged.update(g["units"])
        cur = check_unit(g, m, now, only=VOLUME_CHECKS)
        if cur:
            _mark_new(cur, [q for t in points for q in check_unit(g, facts.metrics(g["members"], t), t,
                                                                   only=VOLUME_CHECKS)])
            unit_problems += cur
    if reasons:
        for p in unit_problems:
            if p["check"] in ("fetch_dark", "fetch_loss"):
                members = next((u["members"] for u in watch["units"] + groups if u["id"] == p["input"]), [])
                why = facts.lost_reasons(conn, members, now) if members else Counter()
                if why:
                    p["why"] = _first(why, "lost at fetch", prefix="fetch ")
                    p["line"] += "; mostly %s" % _top(why)
    problems += sorted(unit_problems + ref_problems, key=lambda p: (-p["stake"], p["input"], p["check"]))
    weight = {u["id"]: u["weight"] for u in watch["units"]}
    watch["judged_alone"] = sum(w for uid, w in weight.items() if judged[uid])
    watch["judged_group"] = sum(w for uid, w in weight.items() if not judged[uid] and uid in group_judged)
    watch["groups"] = len(groups)
    out = {"problems": problems, "key": watch, "reference": ref_ids, "now": _iso(now), "groups": groups}
    if detail:
        out["metrics"] = metrics
        out["reference_metrics"] = ref_now
        out["judged"] = judged
    return out


def check(conn, outlets: List[Dict], now: Optional[dt.datetime] = None, sources: Optional[List[Dict]] = None,
          detail: bool = False) -> Dict:
    """Every check, read only. Returns {"problems": [...], "key": watched_inputs(...) with the shares
    judged, "reference": [...], "groups": [...], "metrics": {input: metrics} when detail}. Each problem
    carries "new" (warned) or not (ongoing, counted in the notice)."""
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    facts = Facts(conn, now - dt.timedelta(days=WINDOW_DAYS + NEW_LOOKBACK, hours=NEW_HOURS), asof=now)
    return evaluate(conn, facts, outlets, now, sources=sources, detail=detail)


def unit_metrics(conn, members: List[str], now: dt.datetime) -> Dict:
    """The figures the per input checks read, for the outlets of one input."""
    return Facts(conn, now - dt.timedelta(days=WINDOW_DAYS), asof=now).metrics(members, now)


def _by_input(problems: List[Dict]) -> List[List[Dict]]:
    """Problems grouped by input, in the order of their first problem."""
    order, groups = [], {}
    for p in problems:
        k = (p.get("category") == "global", p["input"])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(p)
    return [groups[k] for k in order]


def _annotation_text(ps: List[Dict]) -> str:
    if len(ps) == 1:
        return ps[0]["line"]
    head, _, first = ps[0]["line"].partition(": ")
    return head + ": " + "; ".join([first] + [p["line"].partition(": ")[2] for p in ps[1:]])


def ongoing_summary(problems: List[Dict]) -> Optional[str]:
    """One line for every ongoing problem: the dark inputs and their items by why, the degraded ones, the
    reference sources, and the largest few by name."""
    old = [p for p in problems if not p.get("new")]
    if not old:
        return None
    parts = []
    dark = defaultdict(list)
    for p in old:
        if p["category"] == "dark":
            dark[p["input"]].append(p)
    if dark:
        why = Counter()
        items = 0
        ages = []
        for inp, ps in dark.items():
            items += ps[0]["stake"]
            first = sorted(ps, key=lambda p: DARK.index(p["check"]))[0]
            why[first.get("why") or CHECK_NAMES.get(first["check"], first["check"])] += 1
            since = [p["since"] for p in ps if p.get("since")]
            if since:
                ages.append(min(since))
        median = ""
        if ages:
            ages.sort()
            median = "; median onset %s" % _iso(ages[len(ages) // 2])[:10]
        parts.append("%s dark for over %d hours carrying %s (%s%s)" % (
            _count(len(dark), "input"), NEW_HOURS, _items(items), _top(why, 4), median))
    deg = defaultdict(list)
    for p in old:
        if p["category"] == "degraded":
            deg[p["input"]].append(p)
    if deg:
        kinds = Counter(p["check"] for ps in deg.values() for p in ps)
        parts.append("%s degraded carrying %s (%s)" % (
            _count(len(deg), "input"), _items(sum(ps[0]["stake"] for ps in deg.values())),
            ", ".join("%s %d" % (CHECK_NAMES[k], v) for k, v in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])))))
    refs = {p["input"] for p in old if p["category"] == "reference"}
    if refs:
        parts.append(_count(len(refs), "reference source"))
    largest = []
    seen = set()
    for p in sorted(old, key=lambda p: (-p["stake"], p["input"])):
        if p["input"] in seen or p["category"] == "reference":
            continue
        seen.add(p["input"])
        largest.append("%s %s (%s)" % (p["input"].replace("network:", "network "), _num(p["stake"]),
                                       CHECK_NAMES.get(p["check"], p["check"])))
        if len(largest) >= NOTICE_NAMES:
            break
    return "ongoing, not warned again: %s%s" % ("; ".join(parts), "; largest: " + ", ".join(largest) if largest else "")


def annotations(problems: List[Dict]) -> List[str]:
    """The ::warning lines for new problems, one per input in the order given (the most items at stake
    first), at most MAX_ANNOTATIONS; the last says how many more the log holds. Ongoing problems are left
    to the notice."""
    new = [ps for ps in _by_input([p for p in problems if p.get("new", True)])]
    lines = ["::warning title=%s::%s" % (TITLE, _escape(_annotation_text(ps))) for ps in new]
    if len(lines) > MAX_ANNOTATIONS:
        more = len(lines) - (MAX_ANNOTATIONS - 1)
        lines = lines[:MAX_ANNOTATIONS - 1] + ["::warning title=%s::%d more inputs at risk; see the input watch lines "
                                               "in this step's log" % (TITLE, more)]
    return lines


def _escape(text: str) -> str:
    """GitHub workflow command data: % and line breaks are encoded."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def coverage_line(result: Dict, seconds: float) -> str:
    key = result["key"]
    total = key["total"]
    new = sum(1 for p in result["problems"] if p.get("new", True))
    judged = key.get("judged_alone", 0) + key.get("judged_group", 0)
    return ("input watch: watching %s that carried %s of %s state origin and unchecked state sourcing items "
            "(%s) over %d days, %s (%s) on inputs not polled here; could judge %s (%s), %s alone and %s in %s; "
            "%s; %s, %d new; %.1fs" % (
                _count(len(key["units"]), "input"), _num(key["covered"]), _num(total), _pct(key["covered"], total),
                key["window_days"], _num(key["unpolled"]), _pct(key["unpolled"], total), _num(judged),
                _pct(judged, total), _num(key.get("judged_alone", 0)), _num(key.get("judged_group", 0)),
                _count(key.get("groups", 0), "country group"), _count(len(result["reference"]), "reference source"),
                _count(len(result["problems"]), "problem"), new, seconds))


def summary_counts(result: Dict, seconds: float) -> Dict:
    key = result["key"]
    by_check, new_by_check = defaultdict(int), defaultdict(int)
    for p in result["problems"]:
        by_check[p["check"]] += 1
        if p.get("new", True):
            new_by_check[p["check"]] += 1
    dark = {p["input"]: p["stake"] for p in result["problems"] if p["category"] == "dark" and not p.get("new", True)}
    return {"inputs_watched": len(key["units"]), "watched_items": key["covered"], "window_items": key["total"],
            "unpolled_items": key["unpolled"], "judged_alone_items": key.get("judged_alone", 0),
            "judged_group_items": key.get("judged_group", 0), "country_groups": key.get("groups", 0),
            "window_days": key["window_days"], "reference_sources": len(result["reference"]),
            "problems": len(result["problems"]), "new": sum(new_by_check.values()),
            "by_check": dict(sorted(by_check.items())), "new_by_check": dict(sorted(new_by_check.items())),
            "dark_ongoing_inputs": len(dark), "dark_ongoing_items": sum(dark.values()),
            "estimator": config.MISSED_ESTIMATOR_VERSION,
            "flags": [p["line"] for p in result["problems"] if p.get("new", True)],
            # every problem, compactly: input, check, items at stake, new (1) or ongoing (0)
            "all": [[p["input"], p["check"], p["stake"], 1 if p.get("new", True) else 0] for p in result["problems"]],
            "seconds": round(seconds, 2)}


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
    print(coverage_line(result, seconds), file=out)
    summary = ongoing_summary(result["problems"])
    if summary:
        print("input watch: " + summary, file=out)
    if show_all:
        for u in result["key"]["units"]:
            print("  input %3d %-28s %5d items %s  %s" % (
                u["rank"], u["id"], u["weight"], "alone" if result["judged"][u["id"]] else "small",
                json.dumps(result["metrics"][u["id"]], default=str)), file=out)
        for g in result["groups"]:
            print("  group %-30s %5d items %d inputs  %s" % (g["id"], g["weight"], len(g["units"]),
                                                             json.dumps(result["metrics"][g["id"]], default=str)),
                  file=out)
        for sid in result["reference"]:
            print("  ref    %-28s %s" % (sid, json.dumps(result["reference_metrics"][sid], default=str)), file=out)
    for p in result["problems"]:
        when = "new" if p.get("new", True) else (
            "ongoing since %s" % _iso(p["since"])[:10] if p.get("since") else "ongoing")
        print("input watch: [%s] %s" % (when, p["line"]), file=out)
    for line in annotations(result["problems"]):
        print(line, file=out)
    if summary:
        print("::notice title=%s::%s" % (NOTICE_TITLE, _escape(summary)), file=out)
    counts = summary_counts(result, seconds)
    notes = "; ".join(counts["flags"]) or (summary or "")
    store.finish_stage(conn, log_id, True, counts, notes=notes[:2000])
    result["counts"] = counts
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--now", help="the time to judge against, ISO 8601 (default: now); for reading a snapshot")
    ap.add_argument("--all", action="store_true", help="also print every input's and reference source's figures")
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
