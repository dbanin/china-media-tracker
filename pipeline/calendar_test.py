"""The propaganda calendar: does state origin volume rise around fixed Chinese political dates (measure
"calendar", report only).

The unusual days measure (pipeline/spikes.py) finds days far above a country's own recent level, whatever
the date. This measure asks the opposite question: given a list of dates fixed in advance
(sources/political_calendar.yaml: the Party congress and plenums, the Two Sessions, 1 July, 1 October,
1 August, 3 September, round anniversaries, the summits, forums and expos China hosts, the festivals),
is state origin volume higher around them than on normal days?

Pre-registration (owner decision D9). The calendar was compiled on 28 October 2026 (compiled_on in the
file) from public sources and committed at 19:31 Pacific time that day (02:31 UTC on 29 October), after
the export of 22:18 UTC on 28 October had been published. A window that begins on or before the compile
day is a retrospective test, since its data could already be seen (28 October itself was seen almost
whole); a window that begins after it is a forward test, fixed in advance with this method. An entry
added or changed later (added_on after compiled_on) is reported as added after the pre-registration.
The kinds are reported apart, never pooled.

The series. Evidence settled state origin placements per day of record, exactly as the daily files
count them (export.build_daily, "A"): undetermined labels (pipeline/model_only.py) are in no count, and
the wires count under WLD. Worldwide is the sum over every monitored country and WLD; China is not
monitored and never has a series. Only complete days are used: the export day is partial.

Windows. Every dated event's days plus lead_days before its first day and lag_days after its last
(both 3, stated in the calendar file, the same for every event). An event without dates makes no window.
Days are the tracker's days of record, UTC days; an event's dates are Beijing dates, and a Beijing day
begins at 16:00 UTC the day before, which the lead days cover. Windows of nearby events overlap (207 of
the calendar's 487 days, 43 percent, are in some window), so their results share days and are not independent:
each window lists the windows it overlaps.

Days left out (confounded), on both sides:
  first_day             the first day of record, which is partial
  outage                the day's worldwide discovery was under a quarter of the record's median day
  labelling_incomplete  the day's classified share of relevant items was under three quarters of the
                        record's median share (a labelling backlog leaves state origin uncounted)
  ruleset_change        a classification ruleset took effect on the day or the 2 days before
  gate_change           a relevance gate change took effect on the day or the 2 days before
  backfill              at least a quarter of the day's placements were counted on their publication day
                        by a backfill read (day of record unlike the discovery day); only where the
                        database is read
  label_mix             the day's share of labels made under an older ruleset or model prompt differs by
                        more than 10 points from the median of the days it is compared with, decided for
                        each window, so a window is never compared with days labelled under other rules

Two comparisons for every window:
  normal  complete, unconfounded days outside every window of the calendar, within NORMAL_SPAN_DAYS of
          the window on either side: the test the owner asked for
  other   the same, but outside this window only, shown beside the first while normal days are scarce
          (the autumn calendar is dense: the windows of September 2026 can never have 14 normal days
          within 56 days). Other events' windows are among these days: where those events raise state
          text the ratio is pulled towards 1, where they lower it (a holiday week can) it is pushed away,
          so it is a check, not a cleaner test

Rate ratio. Observed placements on the window's usable days over expected, the expected count being the
sum, over those days, of the mean of the comparison days with the same weekday (the overall comparison
mean where a weekday has fewer than MIN_PER_WEEKDAY comparison days). The 95 percent interval is a
negative binomial score interval: phi is the comparison days' Pearson dispersion around their weekday
means (at least 1), a day's variance is its mean times (1 + (phi - 1) times its mean over the comparison
mean), so the spread grows with the level as in a negative binomial, and the expected count's own
uncertainty (phi times the variance of the weekday means) is added. The interval is every ratio R for
which the observed count lies within t standard deviations of R times the expected count, t the 97.5
percent point of Student's t on the comparison days' degrees of freedom (phi is estimated from them); it
stays sensible at zero (0 to an upper bound) and has no upper bound when the counts are too uncertain.
On synthetic data with known answers (tests/test_calendar_test.py) it covers the true ratio about 95
percent of the time with or without a rise; a window with no rise is called raised about 2 to 5 percent
of the time at the spreads seen so far (dispersion 1 to 3), and up to about 1 in 8 with 14 comparison
days and very uneven counts (a negative binomial shape under 2). Where at least MIN_SHIFTS pseudo windows fit, a permutation p value is
added: the same window shifted to other stretches of comparison days, the share of them with a ratio at
least as high (the shifts overlap one another, so it is coarse). Fewer than MIN_WINDOW_DAYS usable window
days or MIN_COMPARISON_DAYS comparison days: "too few days", and no figure. A window still running is
tested on its days so far and counted in the summary only once it has ended.

Countries. The same test for each country (and WLD) whose comparison days carry at least
COUNTRY_MIN_MEAN placements a day, on the days the country had any discovery; the busiest COUNTRY_MAX.

Mentions. Of the window's state origin placements, the share whose headline carries one of the event's
keywords (in the calendar file), beside the same share on the comparison days. A headline only, so it
is a floor: an item about the event in other words is missed.

Timing, never cause: a rise in a window says state text was more common around the date, not that the
date caused it. With many windows some look raised by chance: about 1 in 40 tested windows where nothing
happens (more where daily counts swing widely), and the site says how many that is.

Command line:
  python -m pipeline.calendar_test validate [path]   check the calendar file
  python -m pipeline.calendar_test site [docs/data]  run the test on the published site files (counts
                                                     from the daily files, headlines from the capped
                                                     article lists) and print the result as JSON
"""
import argparse
import datetime as dt
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from pipeline import config

MEASURE_ID = "calendar"
VERSION = "2"                  # 2: score interval, Pearson dispersion, t quantile, 28 October retrospective

PATH = config.ROOT / "sources" / "political_calendar.yaml"
SCHEMA_PATH = config.ROOT / "sources" / "political_calendar_schema.json"
COVERS = ("2026-09-01", "2027-12-31")   # the dates the calendar covers

NORMAL_SPAN_DAYS = 56          # comparison days come from this many days either side of the window
MIN_WINDOW_DAYS = 3            # usable window days needed for a figure
MIN_COMPARISON_DAYS = 14       # usable comparison days needed for a figure
MIN_PER_WEEKDAY = 2            # comparison days of a weekday before its own mean is used
MIN_SHIFTS = 19                # pseudo windows needed before a permutation p value is given
Z95 = 1.959964
CHANCE_RAISED = 0.025          # the share of windows without a rise called raised by chance (one tail)
COUNTRY_MIN_MEAN = 1.0         # placements per comparison day for a country's own test
COUNTRY_MAX = 8
OUTAGE_FLOOR = 0.25
LABELLED_FLOOR = 0.75
RULE_SETTLE_DAYS = 2           # as the unusual days measure (spikes.RULESET_SETTLE_DAYS)
BACKFILL_SHARE = 0.25
LABEL_MIX_GAP = 0.10
SAMPLE_MENTIONS = 40           # hand check: headlines the keywords matched
SAMPLE_MISSES = 20             # and window headlines they did not
CHECK_THRESHOLD = 0.9
UNMONITORED = {"CHN"}
WORLD = "ALL"                  # the worldwide series' key in the side file
REASONS = ("first_day", "outage", "labelling_incomplete", "ruleset_change", "gate_change", "backfill", "label_mix")
TEXT_FIELDS = ("name", "source_title", "note", "expected_period")
_DASHES = ("\u2013", "\u2014", "\u2012", "\u2015")


class OutOfTime(TimeoutError):
    """The analysis reached the export's deadline."""


# ---------------------------------------------------------------------------
# The calendar file
# ---------------------------------------------------------------------------

def _plain(v):
    """YAML reads an unquoted YYYY-MM-DD as a date: every date back to its ISO string."""
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    return v


def load_file(path: Optional[Path] = None) -> Dict:
    with open(Path(path or PATH), encoding="utf-8") as fh:
        return _plain(yaml.safe_load(fh) or {})


def _date(s) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def text_errors(where: str, text: str) -> List[str]:
    errs = []
    if any(d in text for d in _DASHES):
        errs.append("%s: an en or em dash" % where)
    if " - " in text or " -- " in text:
        errs.append("%s: a spaced hyphen used as a dash" % where)
    return errs


def validate(data) -> List[str]:
    """Schema errors first; then the dates, the pre-registration fields and the wording."""
    import jsonschema
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    errs = ["%s: %s" % ("/".join(str(p) for p in e.absolute_path) or "(top)", e.message)
            for e in jsonschema.Draft202012Validator(schema).iter_errors(data)]
    if errs:
        return errs
    compiled = _date(data["compiled_on"])
    if not compiled:
        return ["compiled_on is not a real date"]
    lo, hi = _date(COVERS[0]), _date(COVERS[1])
    seen = set()
    for e in data["events"]:
        k = e["id"]
        if k in seen:
            errs.append("%s: listed twice" % k)
        seen.add(k)
        for f in TEXT_FIELDS:
            if isinstance(e.get(f), str):
                errs += text_errors("%s %s" % (k, f), e[f])
        for kw in e["keywords"]:
            if kw != kw.lower() or kw != kw.strip():
                errs.append("%s: keyword %r is not lower case and trimmed" % (k, kw))
            errs += text_errors("%s keyword" % k, kw)
        added = _date(e["added_on"])
        if not added:
            errs.append("%s: added_on is not a real date" % k)
        elif added < compiled:
            errs.append("%s: added_on is before compiled_on" % k)
        if e["status"] == "expected" and e["known_before_compiled"]:
            errs.append("%s: an expected entry's dates were not known before compiled_on" % k)
        if e["start"] is None or e["end"] is None:
            if not (e["start"] is None and e["end"] is None):
                errs.append("%s: start and end are both dates or both null" % k)
            elif e["status"] != "expected" or not e.get("expected_period"):
                errs.append("%s: an event without dates is expected and says when (expected_period)" % k)
            continue
        a, b = _date(e["start"]), _date(e["end"])
        if not a or not b:
            errs.append("%s: start or end is not a real date" % k)
            continue
        if b < a:
            errs.append("%s: end is before start" % k)
        if a < lo or b > hi:
            errs.append("%s: outside the dates the calendar covers, %s to %s" % (k, COVERS[0], COVERS[1]))
        if (b - a).days > 31:
            errs.append("%s: an event of more than a month makes no useful window" % k)
    return errs


def validate_file(path: Optional[Path] = None) -> List[str]:
    try:
        data = load_file(path)
    except (OSError, yaml.YAMLError) as exc:
        return ["the calendar file cannot be read: %s" % exc]
    return validate(data)


def load_calendar(path: Optional[Path] = None) -> Dict:
    """The validated calendar; raises ValueError naming every error."""
    data = load_file(path)
    errs = validate(data)
    if errs:
        raise ValueError("sources/political_calendar.yaml: " + "; ".join(errs[:10]))
    return data


# ---------------------------------------------------------------------------
# Days and windows
# ---------------------------------------------------------------------------

def _shift(day: str, n: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=n)).isoformat()


def _span(a: str, b: str) -> List[str]:
    out, d = [], a
    while d <= b:
        out.append(d)
        d = _shift(d, 1)
    return out


_WEEKDAYS: Dict[str, int] = {}


def _weekday(day: str) -> int:
    w = _WEEKDAYS.get(day)
    if w is None:
        w = _WEEKDAYS[day] = dt.date.fromisoformat(day).weekday()
    return w


def windows(cal: Dict) -> List[Dict]:
    """Every dated event's window, in date order, with its kind: retrospective (the window begins on or
    before compiled_on), forward (after it) or added_later (added_on after compiled_on), and the ids of
    the windows it overlaps."""
    lead, lag = int(cal["window"]["lead_days"]), int(cal["window"]["lag_days"])
    compiled = cal["compiled_on"]
    out = []
    for e in cal["events"]:
        if not e.get("start"):
            continue
        ws, we = _shift(e["start"], -lead), _shift(e["end"], lag)
        kind = "added_later" if e["added_on"] > compiled else ("retrospective" if ws <= compiled else "forward")
        out.append({"id": e["id"], "name": e["name"], "category": e["category"], "status": e["status"],
                    "recurrence": e["recurrence"], "known_before_compiled": e["known_before_compiled"],
                    "added_on": e["added_on"], "start": e["start"], "end": e["end"], "window_start": ws,
                    "window_end": we, "kind": kind, "keywords": list(e["keywords"]),
                    "source_url": e["source_url"], "source_title": e["source_title"], "note": e.get("note")})
    out.sort(key=lambda w: (w["window_start"], w["id"]))
    for w in out:
        w["overlaps"] = [x["id"] for x in out if x["id"] != w["id"] and x["window_start"] <= w["window_end"]
                         and w["window_start"] <= x["window_end"]]
    return out


def undated(cal: Dict) -> List[Dict]:
    return [{"id": e["id"], "name": e["name"], "category": e["category"], "expected_period": e.get("expected_period"),
             "source_url": e["source_url"], "source_title": e["source_title"]}
            for e in cal["events"] if not e.get("start")]


def window_days(wins: List[Dict]) -> set:
    out = set()
    for w in wins:
        out.update(_span(w["window_start"], w["window_end"]))
    return out


def day_entries(daily: Dict) -> Dict[str, Dict]:
    out = {}
    for m in (daily or {}).values():
        for d, e in ((m or {}).get("days") or {}).items():
            out[d] = e or {}
    return out


def _sum(entry: Dict, field: str, country: Optional[str] = None) -> int:
    cs = (entry or {}).get("countries") or {}
    if country is not None:
        return int((cs.get(country) or {}).get(field) or 0)
    return int(sum((v or {}).get(field) or 0 for c, v in cs.items() if c not in UNMONITORED))


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if not n:
        return 0.0
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def older_shares(entries: Dict[str, Dict]) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """{day: (share of labels on an older ruleset, share on an older model prompt)}, None without labels."""
    out = {}
    for d, e in entries.items():
        cls = _sum(e, "cls")
        if cls <= 0:
            out[d] = (None, None)
            continue
        r, p = e.get("labels_on_older_ruleset"), e.get("labels_on_older_prompt")
        out[d] = (None if r is None else min(1.0, float(r) / cls), None if p is None else min(1.0, float(p) / cls))
    return out


def confounded_days(entries: Dict[str, Dict], days: List[str], meta: Optional[Dict] = None,
                    rows_by: Optional[Dict] = None) -> Dict[str, List[str]]:
    """{day: [reasons]} for the record's complete days that are left out, label_mix aside (decided for
    each window)."""
    meta = meta or {}
    out: Dict[str, List[str]] = defaultdict(list)
    if not days:
        return {}
    out[days[0]].append("first_day")
    disc = {d: _sum(entries.get(d), "disc") for d in days}
    med_disc = _median([v for v in disc.values() if v > 0])
    ratios = {}
    for d in days:
        rel = _sum(entries.get(d), "rel")
        if rel > 0:
            ratios[d] = _sum(entries.get(d), "cls") / float(rel)
    med_ratio = _median(list(ratios.values()))
    changes = [("ruleset_change", str(c.get("date"))[:10]) for c in (meta.get("ruleset_changes") or []) if c.get("date")]
    changes += [("gate_change", str(c.get("date"))[:10]) for c in (meta.get("gate_changes") or []) if c.get("date")]
    by_day: Dict[str, List[Dict]] = defaultdict(list)
    for (d, _c), rows in (rows_by or {}).items():
        by_day[d].extend(rows)
    for d in days:
        if med_disc and disc[d] < OUTAGE_FLOOR * med_disc:
            out[d].append("outage")
        if med_ratio and d in ratios and ratios[d] < LABELLED_FLOOR * med_ratio:
            out[d].append("labelling_incomplete")
        for reason, c in changes:
            if c <= d <= _shift(c, RULE_SETTLE_DAYS) and reason not in out[d]:
                out[d].append(reason)
        rows = by_day.get(d) or []
        if rows:
            back = sum(1 for r in rows if r.get("day_of_record") and r.get("discovered_at")
                       and str(r["day_of_record"])[:10] != str(r["discovered_at"])[:10])
            if back >= BACKFILL_SHARE * len(rows):
                out[d].append("backfill")
    return {d: v for d, v in out.items() if v}


def label_mixed(days: List[str], pool: List[str], shares: Dict[str, Tuple]) -> set:
    """The days among days and pool whose older label shares differ by more than LABEL_MIX_GAP from the
    pool's median."""
    out = set()
    for i in (0, 1):
        vals = [shares[d][i] for d in pool if d in shares and shares[d][i] is not None]
        if not vals:
            continue
        m = _median(vals)
        for d in set(days) | set(pool):
            v = (shares.get(d) or (None, None))[i]
            if v is not None and abs(v - m) > LABEL_MIX_GAP:
                out.add(d)
    return out


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

def _mean_var(vals: List[float]) -> Tuple[float, float]:
    n = len(vals)
    m = sum(vals) / float(n) if n else 0.0
    v = sum((x - m) ** 2 for x in vals) / float(n - 1) if n > 1 else 0.0
    return m, v


def _expected(counts: Dict[str, float], wdays: List[str], pool: List[str]):
    """(expected count over wdays, its variance before phi, overall pool mean, days on the overall mean)."""
    by_wd: Dict[int, List[float]] = defaultdict(list)
    for d in pool:
        by_wd[_weekday(d)].append(counts.get(d, 0))
    m_all = sum(counts.get(d, 0) for d in pool) / float(len(pool))
    c = Counter(_weekday(d) for d in wdays)
    e, var, fallback = 0.0, 0.0, 0
    for w, k in c.items():
        vals = by_wd.get(w) or []
        if len(vals) >= MIN_PER_WEEKDAY:
            mw = sum(vals) / float(len(vals))
            e += k * mw
            var += k * k * mw / float(len(vals))
        else:
            e += k * m_all
            var += k * k * m_all / float(len(pool))
            fallback += k
    return e, var, m_all, fallback


def ratio_of(counts: Dict[str, float], wdays: List[str], pool: List[str]) -> Optional[float]:
    if not wdays or not pool:
        return None
    e = _expected(counts, wdays, pool)[0]
    return (sum(counts.get(d, 0) for d in wdays) / e) if e > 0 else None


def t975(dof: int) -> float:
    """The 97.5 percent point of Student's t with dof degrees of freedom (Cornish Fisher expansion, within
    0.01 of the exact value from 7 degrees of freedom up, the fewest a 14 day pool has): the spread is estimated from the comparison days,
    so a short pool widens the interval."""
    z = Z95
    n = float(max(int(dof), 1))
    return z + (z ** 3 + z) / (4 * n) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * n * n)


def dispersion_dof(pool: List[str]) -> int:
    """The degrees of freedom of dispersion(): the pool's days less one per weekday mean it uses."""
    c = Counter(_weekday(d) for d in pool)
    groups = len([w for w, k in c.items() if k >= MIN_PER_WEEKDAY])
    return len(pool) - max(groups, 1)


def dispersion(counts: Dict[str, float], pool: List[str]) -> float:
    """The pool's Pearson dispersion around its weekday means (the overall mean for a weekday with fewer
    than MIN_PER_WEEKDAY days), at least 1. A weekday rhythm is not counted as spread."""
    by_wd: Dict[int, List[float]] = defaultdict(list)
    for d in pool:
        by_wd[_weekday(d)].append(counts.get(d, 0))
    m_all = sum(counts.get(d, 0) for d in pool) / float(len(pool)) if pool else 0.0
    ss, groups = 0.0, 0
    for vals in by_wd.values():
        if len(vals) >= MIN_PER_WEEKDAY:
            m = sum(vals) / float(len(vals))
            groups += 1
        else:
            m = m_all
        if m > 0:
            ss += sum((x - m) ** 2 for x in vals) / m
    dof = len(pool) - max(groups, 1)
    return max(1.0, ss / dof) if dof > 0 else 1.0


def score_interval(o: float, e: float, n_days: int, phi: float, m_pool: float, var_e0: float, z: float = Z95):
    """(low, high) of the ratio R: every R for which o lies within z standard deviations of R e, where
    var(O | R) = R e (1 + (phi - 1) R e / (n_days m_pool)) + R^2 phi var_e0. high is None when the expected
    count is too uncertain for an upper bound."""
    inv_k = (phi - 1.0) / m_pool if m_pool > 0 else 0.0
    z2 = z * z
    quad = inv_k * e * e / float(max(n_days, 1)) + phi * var_e0
    a = e * e - z2 * quad
    b = -(2.0 * o * e + z2 * e)
    c = float(o) * o
    disc = math.sqrt(max(0.0, b * b - 4.0 * a * c))
    if a <= 0:
        # the upper root runs off: only the lower bound is defined
        low = c / (-b) if b < 0 and o > 0 else 0.0
        return max(0.0, low), None
    low = (-b - disc) / (2.0 * a) if o > 0 else 0.0
    return max(0.0, low), (-b + disc) / (2.0 * a)


def rate_test(counts: Dict[str, float], wdays: List[str], pool: List[str], wrange: Optional[List[str]] = None) -> Dict:
    """The window's rate ratio against the pool: observed, expected, ratio, the 95 percent negative
    binomial score interval and, where enough pseudo windows fit, the permutation p value. state is
    "tested", "no_comparison_volume" or "too_few_days"."""
    wdays, pool = sorted(wdays), sorted(pool)
    out = {"state": "too_few_days", "window_days": len(wdays), "comparison_days": len(pool),
           "observed": int(round(sum(counts.get(d, 0) for d in wdays))), "expected": None, "ratio": None,
           "ci_low": None, "ci_high": None, "dispersion": None, "p_shift": None, "shifts": 0, "weekday_fallback": 0}
    if len(wdays) < MIN_WINDOW_DAYS or len(pool) < MIN_COMPARISON_DAYS:
        return out
    e, var_e0, m_all, fallback = _expected(counts, wdays, pool)
    phi = dispersion(counts, pool)
    o = sum(counts.get(d, 0) for d in wdays)
    out.update({"state": "tested", "expected": round(e, 2), "dispersion": round(phi, 2), "weekday_fallback": fallback,
                "comparison_mean": round(m_all, 3), "window_mean": round(o / float(len(wdays)), 3)})
    if e <= 0:
        out["state"] = "no_comparison_volume"
        return out
    r = o / e
    lo, hi = score_interval(o, e, len(wdays), phi, m_all, var_e0, t975(dispersion_dof(pool)))
    out.update({"ratio": round(r, 3), "ci_low": round(lo, 3), "ci_high": None if hi is None else round(hi, 3)})
    # Pseudo windows: the window's span moved onto comparison days, never overlapping the real one. The
    # pool's weekday sums are kept, and each pseudo window's own days taken out of them.
    wrange = wrange or wdays
    length = len(wrange)
    pset = set(pool)
    sums: Dict[int, float] = defaultdict(float)
    ns: Dict[int, int] = defaultdict(int)
    for d in pool:
        sums[_weekday(d)] += counts.get(d, 0)
        ns[_weekday(d)] += 1
    total, n_pool = sum(sums.values()), len(pool)
    ratios = []
    for s in range(-NORMAL_SPAN_DAYS - length, NORMAL_SPAN_DAYS + length + 1):
        if abs(s) < length:
            continue
        full = [x for x in (_shift(d, s) for d in wrange) if x in pset]
        if len(full) < MIN_WINDOW_DAYS or len(full) < len(wdays) or n_pool - len(full) < MIN_COMPARISON_DAYS:
            continue
        use = full[:len(wdays)]
        # the rest is the pool less every pseudo window day; use is tested against it
        out_s, out_n = defaultdict(float), defaultdict(int)
        for d in full:
            out_s[_weekday(d)] += counts.get(d, 0)
            out_n[_weekday(d)] += 1
        rest_n = n_pool - len(full)
        m_rest = (total - sum(out_s.values())) / float(rest_n)
        exp_s = 0.0
        for w, k in Counter(_weekday(d) for d in use).items():
            nw = ns[w] - out_n[w]
            exp_s += k * ((sums[w] - out_s[w]) / float(nw) if nw >= MIN_PER_WEEKDAY else m_rest)
        if exp_s > 0:
            ratios.append(sum(counts.get(d, 0) for d in use) / exp_s)
    out["shifts"] = len(ratios)
    if len(ratios) >= MIN_SHIFTS:
        out["p_shift"] = round((1 + sum(1 for x in ratios if x >= r - 1e-12)) / float(1 + len(ratios)), 3)
    return out


def _matcher(keywords: List[str]):
    pats = []
    for k in keywords:
        k = k.lower()
        if all(ord(ch) < 0x250 for ch in k):
            pats.append(re.compile(r"(?<!\w)%s(?!\w)" % re.escape(k)))
        else:
            pats.append(re.compile(re.escape(k)))
    return lambda title: any(p.search((title or "").lower()) for p in pats)


_BY_DAY = ("_by_day", "")


def _rows_by_day(rows_by: Dict) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = defaultdict(list)
    for key, rows in (rows_by or {}).items():
        if key != _BY_DAY:
            out[key[0]].extend(rows)
    return out


def mentions(rows_by: Dict, wdays: List[str], pool: List[str], keywords: List[str]) -> Optional[Dict]:
    """The share of the window's state origin placements whose headline carries a keyword, and the same
    share on the comparison days. None when no headlines are at hand."""
    if not rows_by:
        return None
    hit = _matcher(keywords)
    by_day = rows_by.get(_BY_DAY) if _BY_DAY in rows_by else _rows_by_day(rows_by)
    w_n = w_k = p_n = p_k = 0
    by_outlet: Counter = Counter()
    for d in set(wdays):
        for r in by_day.get(d, ()):
            w_n += 1
            by_outlet[r.get("outlet_id")] += 1
            w_k += 1 if hit(r.get("title")) else 0
    for d in set(pool) - set(wdays):
        for r in by_day.get(d, ()):
            p_n += 1
            p_k += 1 if hit(r.get("title")) else 0
    return {"items": w_n, "mentioning": w_k, "share": round(w_k / float(w_n), 4) if w_n else None,
            "comparison_items": p_n, "comparison_mentioning": p_k,
            "comparison_share": round(p_k / float(p_n), 4) if p_n else None,
            "top_outlet_share": round(by_outlet.most_common(1)[0][1] / float(w_n), 3) if w_n else None}


def _country_series(entries: Dict[str, Dict], days: List[str]) -> Dict[str, Tuple[Dict[str, int], set]]:
    """{country: ({day: placements}, monitored days)} and the worldwide series under WORLD."""
    out: Dict[str, Tuple[Dict[str, int], set]] = {}
    world: Dict[str, int] = {}
    for d in days:
        e = entries.get(d) or {}
        world[d] = _sum(e, "A")
        for c, v in ((e.get("countries") or {}).items()):
            if c in UNMONITORED:
                continue
            s = out.setdefault(c, ({}, set()))
            s[0][d] = int((v or {}).get("A") or 0)
            if int((v or {}).get("disc") or 0) > 0:
                s[1].add(d)
    out[WORLD] = (world, set(d for d in days if _sum(entries.get(d), "disc") > 0))
    return out


def analyse(daily: Dict, until: str, cal: Dict, meta: Optional[Dict] = None, rows_by: Optional[Dict] = None,
            deadline: Optional[float] = None) -> Dict:
    """Every window tested against both comparisons, from the daily files' {month: {...}} and the export
    bound until (the partial export day, never used). Pure: everything comes in as arguments. rows_by is
    {(day, country): [placements with title, outlet_id, day_of_record, discovered_at]} or None."""
    entries = day_entries(daily)
    days = sorted(d for d in entries if d < until)
    wins = windows(cal)
    all_win = window_days(wins)
    conf = confounded_days(entries, days, meta, rows_by)
    shares = older_shares(entries)
    series = _country_series(entries, days)
    dayset = set(days)
    last = days[-1] if days else None
    first = days[0] if days else None
    if rows_by:
        rows_by = dict(rows_by)
        rows_by[_BY_DAY] = _rows_by_day(rows_by)
    results = []
    for w in wins:
        if deadline is not None and time.monotonic() >= deadline:
            raise OutOfTime("propaganda calendar: the export's deadline came before every window was tested")
        wrange = _span(w["window_start"], w["window_end"])
        r = dict(w)
        r.pop("keywords", None)
        r["keywords"] = w["keywords"]
        if not days or w["window_start"] > last:
            r["state"] = "upcoming"
            results.append(r)
            continue
        if w["window_end"] > last:
            r["state"] = "in_progress"
        in_rec = [d for d in wrange if d in dayset]
        excluded = Counter()
        for d in wrange:
            if d not in dayset:
                excluded["outside_record"] += 1
        lo, hi = _shift(w["window_start"], -NORMAL_SPAN_DAYS), _shift(w["window_end"], NORMAL_SPAN_DAYS)
        near = [d for d in days if lo <= d <= hi and d not in conf]
        # the most normal days this window can ever have: every day of the span from the first of record
        # outside every window, as if all were complete and unconfounded
        r["normal_possible"] = len([d for d in _span(max(lo, _shift(first, 1)), hi) if d not in all_win])
        wset = set(wrange)
        pools = {"normal": [d for d in near if d not in all_win], "other": [d for d in near if d not in wset]}
        used_w = [d for d in in_rec if d not in conf]
        for d in in_rec:
            for reason in conf.get(d, [])[:1]:
                excluded[reason] += 1
        r["comparisons"] = {}
        world = series[WORLD]
        for name, pool in pools.items():
            mixed = label_mixed(used_w, pool, shares)
            wd = [d for d in used_w if d not in mixed and d in world[1]]
            pl = [d for d in pool if d not in mixed and d in world[1]]
            t = rate_test(world[0], wd, pl, wrange)
            t["label_mix_days"] = len([d for d in used_w if d in mixed])
            t["mentions"] = mentions(rows_by, wd, pl, w["keywords"]) if t["window_days"] else None
            # the busiest COUNTRY_MAX countries with a test, busiest first: tested in that order, so the
            # quiet ones are never tested
            cand = []
            for c, (cnt, mon) in series.items():
                if c == WORLD:
                    continue
                cw, cp = [d for d in wd if d in mon], [d for d in pl if d in mon]
                if not cp:
                    continue
                mean = sum(cnt.get(d, 0) for d in cp) / float(len(cp))
                if mean >= COUNTRY_MIN_MEAN:
                    cand.append((-mean, c, cw, cp))
            countries = []
            for _m, c, cw, cp in sorted(cand, key=lambda x: (x[0], x[1])):
                if len(countries) >= COUNTRY_MAX:
                    break
                ct = rate_test(series[c][0], cw, cp, wrange)
                if ct["state"] != "tested":
                    continue
                ct["country"] = c
                countries.append(ct)
            t["countries"] = countries
            r["comparisons"][name] = t
        r["days_in_record"] = len(in_rec)
        r["days_usable"] = len(used_w)
        r["excluded"] = dict(excluded)
        r["state"] = r.get("state") or ("tested" if r["comparisons"]["normal"]["state"] == "tested"
                                       or r["comparisons"]["other"]["state"] == "tested" else "too_few_days")
        results.append(r)
    in_cal = [d for d in days if d >= COVERS[0]]
    return {"windows": results, "undated": undated(cal), "compiled_on": cal["compiled_on"],
            "lead_days": cal["window"]["lead_days"], "lag_days": cal["window"]["lag_days"],
            "first_day": days[0] if days else None, "last_complete_day": last,
            "record_days": len(days), "confounded": {d: v for d, v in sorted(conf.items())},
            "confounded_by_reason": dict(Counter(v[0] for v in conf.values())),
            "days_in_any_window": len([d for d in in_cal if d in all_win]),
            "normal_days": len([d for d in in_cal if d not in all_win and d not in conf]),
            "calendar_days_in_windows": round(len([d for d in _span(*COVERS) if d in all_win]) / float(len(_span(*COVERS))), 3)}


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def load_rows(conn, outlets: List[Dict]) -> Dict:
    """The evidence settled state origin placements by (day of record, country), with their headline,
    as the unusual days measure reads them (spikes.load_rows: the daily files' own count)."""
    from pipeline import spikes
    return spikes.load_rows(conn, outlets)


def run_analysis(conn, ctx: Optional[Dict] = None, deadline: Optional[float] = None) -> Dict:
    """The analysis for the export (ctx from pipeline.measures), cached in ctx so compute, build_export
    and meta_summary share one run."""
    ctx = ctx if ctx is not None else {}
    if "_calendar" in ctx:
        return ctx["_calendar"]
    from pipeline import export, registry, spikes
    cal = load_calendar()
    outlets = ctx.get("outlets")
    if outlets is None:
        outlets = registry.load_outlets()
    daily = ctx.get("daily")
    if daily is None:
        daily = export.build_daily(conn, outlets)
    meta = ctx.get("meta") or {"ruleset_changes": export.ruleset_changes(), "gate_changes": export.gate_changes()}
    until = spikes.last_tested_day_bound(ctx.get("latest"))
    rows_by = ctx.get("_spike_rows")
    if rows_by is None:
        rows_by = load_rows(conn, outlets)
    res = analyse(daily, until, cal, meta, rows_by, deadline)
    ctx["_calendar"] = res
    return res


def inputs_present(conn) -> int:
    """Days with discovery on record."""
    return int(conn.execute("SELECT COUNT(DISTINCT date) FROM daily_discovery").fetchone()[0] or 0)


def compute(conn, ctx, deadline) -> Dict:
    res = run_analysis(conn, ctx, deadline)
    st = Counter((w["kind"], w["state"]) for w in res["windows"])
    return {"windows": len(res["windows"]), "states": {"%s %s" % k: v for k, v in sorted(st.items())}}


def build_export(conn, ctx) -> Dict:
    res = run_analysis(conn, ctx)
    return {"version": VERSION, "compiled_on": res["compiled_on"], "lead_days": res["lead_days"],
            "lag_days": res["lag_days"], "first_day": res["first_day"], "last_complete_day": res["last_complete_day"],
            "record_days": res["record_days"], "normal_days": res["normal_days"],
            "days_in_any_window": res["days_in_any_window"], "calendar_days_in_windows": res["calendar_days_in_windows"],
            "confounded_by_reason": res["confounded_by_reason"], "confounded": res["confounded"],
            "windows": res["windows"], "undated": res["undated"],
            "settings": {"normal_span_days": NORMAL_SPAN_DAYS, "min_window_days": MIN_WINDOW_DAYS,
                         "min_comparison_days": MIN_COMPARISON_DAYS, "min_shifts": MIN_SHIFTS,
                         "country_min_mean": COUNTRY_MIN_MEAN}}


def _raised(t: Optional[Dict]) -> bool:
    return bool(t and t.get("state") == "tested" and (t.get("ci_low") or 0) > 1.0)


def summarise(res: Dict) -> Dict:
    out = {"windows": len(res["windows"]), "undated": len(res["undated"]), "normal_days": res["normal_days"]}
    for kind in ("retrospective", "forward", "added_later"):
        ws = [w for w in res["windows"] if w["kind"] == kind]
        # a window still running is left out until it has ended: its figures are interim
        tested = [w for w in ws if w.get("state") == "tested"]
        out[kind] = {"windows": len(ws), "tested_normal": len([w for w in tested if w["comparisons"]["normal"]["state"] == "tested"]),
                     "tested_other": len([w for w in tested if w["comparisons"]["other"]["state"] == "tested"]),
                     "raised_normal": len([w for w in tested if _raised(w["comparisons"]["normal"])]),
                     "raised_other": len([w for w in tested if _raised(w["comparisons"]["other"])]),
                     "in_progress": len([w for w in ws if w.get("state") == "in_progress"]),
                     "upcoming": len([w for w in ws if w.get("state") == "upcoming"])}
    return out


def by_chance(n_tested: int) -> float:
    """How many of n_tested windows would be called raised by chance alone if no window had a rise."""
    return round(n_tested * CHANCE_RAISED, 1)


def meta_summary(conn, ctx) -> Dict:
    return summarise(run_analysis(conn, ctx))


def methodology_text(block) -> str:
    s = (block or {}).get("summary") or {}
    retro, fwd = s.get("retrospective") or {}, s.get("forward") or {}
    p1 = ("The propaganda calendar asks whether state origin volume rises around Chinese political dates fixed "
          "in advance. The dates are in sources/political_calendar.yaml: the Party congress and plenums once "
          "announced, the Two Sessions, 1 July, 1 August, 3 September, National Day and Golden Week, round "
          "anniversaries such as the 90th of the end of the Long March on 22 October 2026, the summits, forums "
          "and expos China hosts, and the Spring and Mid-Autumn festivals, each with its category, how it "
          "recurs, whether it is fixed, announced or only expected, whether its dates were public before 28 "
          "October 2026, and a public source. The file was compiled on 28 October 2026 and the committed file "
          "is the pre-registration: it was committed that evening, after the day's data had been published, so a "
          "window that begins on or before 28 October is a retrospective test, chosen while its data could be "
          "seen, and a window that begins after it is a forward test fixed in advance. An entry added or changed "
          "later is reported as added after the pre-registration. The kinds are reported apart and never pooled. "
          "The interval was revised on 29 October 2026, before any forward window had begun.")
    p2 = ("Each window is an event's days with 3 days before and 3 after. The series is the evidence settled "
          "state origin placements of each complete day, as the daily files count them, worldwide and for each "
          "country with at least %s placement a day on its comparison days. Days are left out on both sides "
          "when the record's first day is partial, discovery fell under a quarter of the median day, the day's "
          "labelling was well behind, a ruleset or relevance gate change took effect on the day or the 2 days "
          "before, a quarter of the day's placements came from a backfill read, or the day's labels were made "
          "under other rules than the days it is compared with. A window is compared with normal days, outside "
          "every window of the calendar and within %d days of it, and, while normal days are scarce, "
          "with every other usable day near it; other events' windows are among those days, so "
          "it is a check, not a cleaner test. Days are UTC days of record and the events' dates Beijing dates; "
          "the lead days cover the 8 hours between them. The rate ratio is the window's placements over "
          "the count expected from comparison days of the same weekday, with a 95 percent negative binomial "
          "score interval and, where at least %d shifted windows fit, a permutation p value. With fewer than %d usable "
          "window days or %d comparison days the window says too few days. The share of the window's state "
          "origin headlines that carry one of the event's keywords in the file is given beside the same share "
          "on the comparison days; a headline about the event in other words is missed, and a keyword such as "
          "a city name can match a headline about something else." % ("%g" % COUNTRY_MIN_MEAN, NORMAL_SPAN_DAYS, MIN_SHIFTS, MIN_WINDOW_DAYS,
                                  MIN_COMPARISON_DAYS))
    rt_n, rt_o = int(retro.get("tested_normal") or 0), int(retro.get("tested_other") or 0)
    ft_n, ft_o = int(fwd.get("tested_normal") or 0), int(fwd.get("tested_other") or 0)
    p3 = ("So far, of the windows that have ended, %d retrospective windows had enough days against normal days "
          "(%d raised) and %d against other days (%d raised); %d forward windows had enough days against normal "
          "days (%d raised) and %d against other days (%d raised). A raised window shows timing, not cause: state "
          "text was more common around the date, which the date need not explain. With many windows some look "
          "raised by chance: where nothing happens, about 1 in 40 tested windows would (on synthetic data 2 to 5 "
          "in 100, the more as daily counts swing), and overlapping windows share days, so they rise and fall "
          "together. The calendar is report only and changes no count. A hand check codes whether a sample of "
          "headlines the keywords matched, and missed, are about the event, and whether each window's dates "
          "match its source; it has to agree at least %d percent of the time before the marking provisional "
          "comes off." % (rt_n, int(retro.get("raised_normal") or 0), rt_o, int(retro.get("raised_other") or 0),
                          ft_n, int(fwd.get("raised_normal") or 0), ft_o, int(fwd.get("raised_other") or 0),
                          int(round(CHECK_THRESHOLD * 100))))
    return "\n\n".join((p1, p2, p3))


# ---------------------------------------------------------------------------
# The hand check
# ---------------------------------------------------------------------------

FIELDS = ["sample_id", "kind", "event", "question", "start", "end", "source_url", "title", "url", "human", "coder", "note"]
YES = ("yes", "y", "1", "true")
NO = ("no", "n", "0", "false")


def draw_sample(conn, n=SAMPLE_MENTIONS, seed=None, out_dir=None):
    """Rows of two kinds: every window that has begun, to check its dates against its source (expected
    yes), and up to n headlines from those windows that the keywords matched plus SAMPLE_MISSES they did
    not, to judge whether each is about the event (expected: the keyword verdict, in the key only)."""
    import random
    from pipeline import measures
    res = run_analysis(conn, {})
    rows_by = load_rows(conn, __import__("pipeline.registry", fromlist=["x"]).load_outlets())
    rng = random.Random(str(seed))
    rows, key = [], {}
    begun = [w for w in res["windows"] if w.get("state") != "upcoming"]
    for w in begun:
        sid = "d_%s" % w["id"]
        rows.append({"sample_id": sid, "kind": "dates", "event": w["name"], "start": w["start"], "end": w["end"],
                     "source_url": w["source_url"], "note": w.get("note") or "",
                     "question": "Does the source give these dates for the event? yes or no"})
        key[sid] = {"expected": "yes"}
    hits, misses = [], []
    for w in begun:
        m = _matcher(w["keywords"])
        wset = set(_span(w["window_start"], w["window_end"]))
        for (d, _c), rs in rows_by.items():
            if d in wset:
                for r in rs:
                    (hits if m(r.get("title")) else misses).append((w, r))
    rng.shuffle(hits)
    rng.shuffle(misses)
    for kind, pick in (("yes", hits[:int(n or SAMPLE_MENTIONS)]), ("no", misses[:SAMPLE_MISSES])):
        for w, r in pick:
            sid = "m_%s_%s" % (w["id"], r["id"])
            if sid in key:
                continue
            rows.append({"sample_id": sid, "kind": "headline", "event": w["name"], "title": r.get("title"),
                         "url": r.get("url"), "question": "Is this headline about the event? yes or no"})
            key[sid] = {"expected": kind}
    rng.shuffle(rows)
    return measures.write_sample(out_dir or measures.REVIEWS_DIR, MEASURE_ID, rows, FIELDS, {"rows": key},
                                 version=VERSION, seed=seed)


def score_check(rows, key):
    from pipeline import measures
    answers = (key or {}).get("rows") or {}
    n = coded = agree = 0
    for r in rows:
        k = answers.get(r.get("sample_id"))
        if not k:
            continue
        n += 1
        h = (r.get("human") or "").strip().lower()
        if h in YES or h in NO:
            coded += 1
            agree += 1 if (("yes" if h in YES else "no") == k["expected"]) else 0
    value = (agree / float(coded)) if coded else None
    return measures.check_result("agreement", value, CHECK_THRESHOLD, n, coded)


# ---------------------------------------------------------------------------
# The published site files, for a reader without the database
# ---------------------------------------------------------------------------

def site_inputs(data_dir: Path) -> Tuple[Dict, str, Dict, Dict]:
    """(daily, until, meta, rows_by) from a published docs/data directory: the daily files, latest.json's
    export day, meta.json's rule changes, and the headlines of the capped article lists."""
    data_dir = Path(data_dir)
    daily = {}
    for f in sorted((data_dir / "daily").glob("*.json")):
        with open(f, encoding="utf-8") as fh:
            daily[f.stem] = json.load(fh)
    with open(data_dir / "meta.json", encoding="utf-8") as fh:
        meta = json.load(fh)
    until = None
    try:
        with open(data_dir / "latest.json", encoding="utf-8") as fh:
            latest = json.load(fh)
        from pipeline import spikes
        until = spikes.last_tested_day_bound(latest)
    except (OSError, ValueError, KeyError):
        until = max(day_entries(daily)) if daily else dt.date.today().isoformat()
    rows_by: Dict = defaultdict(list)
    for f in sorted((data_dir / "articles").glob("*.json")):
        with open(f, encoding="utf-8") as fh:
            items = json.load(fh) or []
        for a in items:
            if a.get("category") == "A" and a.get("date"):
                rows_by[(a["date"][:10], f.stem)].append({"id": a.get("id"), "title": a.get("title"), "url": a.get("url"),
                                                          "outlet_id": a.get("outlet_id")})
    return daily, until, meta, dict(rows_by)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="The propaganda calendar and its test")
    sub = ap.add_subparsers(dest="cmd")
    v = sub.add_parser("validate", help="check the calendar file")
    v.add_argument("path", nargs="?", default=None)
    s = sub.add_parser("site", help="run the test on published site files")
    s.add_argument("data_dir", nargs="?", default=str(config.EXPORT_DIR))
    args = ap.parse_args(argv)
    if args.cmd == "validate":
        errs = validate_file(Path(args.path) if args.path else None)
        for e in errs:
            print(e)
        if errs:
            raise SystemExit(1)
        print("ok: %d events" % len(load_file(Path(args.path) if args.path else None)["events"]))
    elif args.cmd == "site":
        daily, until, meta, rows_by = site_inputs(Path(args.data_dir))
        res = analyse(daily, until, load_calendar(), meta, rows_by)
        res["summary"] = summarise(res)
        print(json.dumps(res, indent=1, ensure_ascii=False, sort_keys=True))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
