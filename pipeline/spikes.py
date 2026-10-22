"""Unusual days: spike alerts on the daily state origin count (measure "spikes", report only).

For every country, and for the worldwide distribution stratum WLD, the tested series is the number of
distinct underlying items among the state origin placements of each day of record (the placements as
the daily files count them, export.build_daily, with the wires under WLD), each outlet counting at most
OUTLET_CAP of its items: a batch one outlet ran, or one item placed many times, is a lump, not a surge,
and in the first month 13 of the 14 days flagged on the raw placements were one outlet's. Only complete
days are tested: the export day is partial, so the last day tested is the day before it
(export.complete_window_30d). China is not monitored and never has a series.

Baseline. The 28 complete days before the day, the day itself excluded. Only monitored days count, a
monitored day being one on which the country had any discovery at all (disc in the daily files), and
at least 14 of them are needed or the day is not tested. The expected count lambda is the mean of
those days after dropping the top 10 percent of them (the largest int(n / 10) days), with a floor of
0.2, so one earlier surge does not hide the next and a quiet country is not tested against zero.

Spread. The daily counts vary more than a Poisson model allows (their variance was a median 2.1 times
their mean across countries, 22.7 for Australia), so the tail is a negative binomial with mean lambda
and the variance to mean ratio phi of the country's own monitored days in the DISPERSION_DAYS before the
day (the busiest tenth set aside, as for lambda; at least 1, when it is the Poisson model, and 1 with
fewer than 14 such days). A day is flagged when all three hold: the tail P(X >= x) is under 0.001, x is
at least 5, and x is at least 3 lambda. The tail is computed in pure Python on the log scale. The export
also gives the number of tests run and the number of flags the model expects by chance (for each test,
the tail at the smallest count that would have been flagged); real days are lumpier than any such model,
so that number is a rough guide, not a promise, and the site says so.

Each spike carries the rate per discovered item and the distinct underlying items, the outlets and
items behind it (straight from the articles table, so past the 80 per country article file cap), and
the confounders that could make it an artefact of the instrument rather than a surge of state text:

  new_outlet         outlets first seen less than 14 days before the day (their earliest day in
                     daily_outlet_discovery), too new for a baseline of their own, carry at least half of
                     its placements
  backfill           at least a quarter of its placements were counted on their publication day by a
                     backfill read (articles.day_of_record differs from the discovery day)
  outage_recovery    discovery in the country fell below a quarter of its baseline median on one of the
                     3 days before; or outlets with at least half the placements found nothing the day
                     before while they usually do; or at least half the placements were published more
                     than 48 hours before they were found (a collector catching up)
  relay_incomplete   for a country only the collector on the owner's machine collects, that collector ran
                     in full on the day but too few hours on the day before (a catch up) or on 3 or more
                     baseline days
  ruleset_change     the day and its baseline were labelled under different rules: a ruleset took effect
                     inside the day's baseline or on the day, and either it took effect on the day or the 2
                     days before and the day's labels carry an older ruleset or model prompt in a share
                     more than 5 points above its baseline days, or that share differs by more than 10
                     points between the day and its baseline days. A change earlier than the whole
                     baseline is never a confounder, whatever is still waiting to be relabelled, so a
                     change stops blocking new days 28 days after it; the site says that a day is to
                     check because relabelling is not finished
  gate_change        a relevance gate change falls inside the baseline and the country's level after it
                     is at least twice the level before it, or it falls on the day or the 2 days before,
                     or too few baseline days follow it to tell
  saturation         at least the warning share of the country's feed polls on its baseline days came
                     back saturated, so the baseline lost items between polls
  llm_ceiling        model labels are at least a quarter of the state origin count on the day or its
                     baseline, the baseline's China coverage still waiting for the model is at least 10
                     points above the day's, and the daily model cap was hit or candidates were sampled
                     on a run since the baseline began (a candidate judged late still counts on its own
                     day, so only candidates still waiting truncate a day)
  rediscovery        discovery counts before the repeats were fixed (meta.rediscovery) carry repeats:
                     this touches the rate per discovered item only, never the count, so it is noted
                     and does not change the status
  denominator_spike  the day's discovery was at least twice its baseline and the rate per discovered
                     item less than twice its baseline: the whole intake grew, not state text alone

Descriptors (never a confounder): one_item when one underlying item holds more than half the
placements, one_outlet when one outlet does. Status is clean, or check when any confounder touching
the count applies.

The analysis respects the export's deadline: past it, it stops with OutOfTime (nothing is stored, so
there is nothing to resume, and the measure is recorded as failed rather than published half done).
"""
import csv
import datetime as dt
import functools
import json
import math
import random
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

MEASURE_ID = "spikes"
# 2: the tested series is distinct underlying items with each outlet capped, the tail a negative
# binomial on the country's own spread, and a ruleset change counts only inside the baseline.
VERSION = "2"

WINDOW_DAYS = 28
MIN_BASELINE_DAYS = 14
TRIM_SHARE = 0.10
LAMBDA_FLOOR = 0.2
OUTLET_CAP = 3                   # items one outlet can add to a day's tested count
DISPERSION_DAYS = 90             # the country's own history the spread is estimated on
P_THRESHOLD = 0.001
MIN_COUNT = 5
MIN_RATIO = 3.0
MAX_ITEMS = 40
TOP_N = 5
SAMPLE_MAX = 30
PRECISION_THRESHOLD = 0.8
RECENT_DAYS = 30

ONE_SHARE = 0.5
NEW_OUTLET_SHARE = 0.5
BACKFILL_SHARE = 0.25
OUTAGE_LOOKBACK = 3
OUTAGE_FLOOR = 0.25
OUTLET_GAP_SHARE = 0.5
STALE_HOURS = 48
STALE_SHARE = 0.5
RELAY_BASELINE_DAYS = 3
OLDER_LABEL_SHARE = 0.05
OLDER_LABEL_GAP = 0.10
WAITING_GAP = 0.10
RULESET_SETTLE_DAYS = 2
GATE_SETTLE_DAYS = 2
GATE_MIN_SIDE_DAYS = 3
GATE_LEVEL_RATIO = 2.0
SATURATION_SHARE = 0.25          # config.FEED_SATURATION_WARNING_SHARE when meta does not carry it
MODEL_SHARE = 0.25
DENOM_RATIO = 2.0
DENOM_RATE_RATIO = 2.0

UNMONITORED = {"CHN"}            # China stays unmonitored: never a series, never a figure
RATE_ONLY = {"rediscovery"}      # confounders that touch the rate per discovered item, never the count
CONFOUNDERS = ("new_outlet", "backfill", "outage_recovery", "relay_incomplete", "ruleset_change", "gate_change",
               "saturation", "llm_ceiling", "rediscovery", "denominator_spike")
JUDGEMENTS = ("real", "artefact")
ARTEFACT_TYPES = CONFOUNDERS + ("duplicate_items", "misdated", "mislabelled", "other")


# ---------------------------------------------------------------------------
# The Poisson tail and the baseline
# ---------------------------------------------------------------------------

def poisson_log_sf(x: int, lam: float) -> float:
    """log P(X >= x) for X ~ Poisson(lam), on the log scale throughout so a large x never overflows or
    loses the tail to cancellation. -inf when the tail is impossible (lam 0 and x above 0)."""
    x = int(math.ceil(x))
    if x <= 0:
        return 0.0
    if lam <= 0:
        return -math.inf
    log_lam = math.log(lam)

    def logpmf(k: int) -> float:
        return k * log_lam - lam - math.lgamma(k + 1)

    if x <= lam:
        # The tail is large: 1 less the lower sum, whose terms are all of ordinary size.
        lower = math.fsum(math.exp(logpmf(k)) for k in range(x))
        return math.log1p(-lower) if lower < 1.0 else -math.inf
    # x above the mean: the terms from x upward fall geometrically, so sum them relative to the first.
    total, term, k = 1.0, 1.0, x
    while k < x + 1000000:
        k += 1
        term *= lam / k
        total += term
        if term < 1e-17 * total:
            break
    return logpmf(x) + math.log(total)


def poisson_sf(x: int, lam: float) -> float:
    """P(X >= x) for X ~ Poisson(lam); 0.0 when it is below the smallest float."""
    v = poisson_log_sf(x, lam)
    return 0.0 if v == -math.inf else math.exp(v)


class OutOfTime(TimeoutError):
    """The analysis reached the export's deadline."""


def nbinom_log_sf(x: int, mean: float, phi: float) -> float:
    """log P(X >= x) for a negative binomial with the given mean and variance phi * mean. phi at or
    below 1 is the Poisson model. On the log scale throughout, like poisson_log_sf."""
    if phi <= 1.0 + 1e-9:
        return poisson_log_sf(x, mean)
    x = int(math.ceil(x))
    if x <= 0:
        return 0.0
    if mean <= 0:
        return -math.inf
    r = mean / (phi - 1.0)          # the size: variance = mean + mean^2 / r
    log_p, log_q = -math.log(phi), math.log1p(-1.0 / phi)
    lg_r = math.lgamma(r)

    def logpmf(k: int) -> float:
        return math.lgamma(k + r) - lg_r - math.lgamma(k + 1) + r * log_p + k * log_q

    if x <= mean:
        lower = math.fsum(math.exp(logpmf(k)) for k in range(x))
        return math.log1p(-lower) if lower < 1.0 else -math.inf
    # Above the mean every later term is smaller than the one before (the ratio tends to q below 1).
    q = 1.0 - 1.0 / phi
    total, term, k = 1.0, 1.0, x
    while k < x + 1000000:
        term *= (k + r) / (k + 1.0) * q
        k += 1
        total += term
        if term < 1e-17 * total:
            break
    return logpmf(x) + math.log(total)


def tail(x: int, lam: float, phi: float = 1.0) -> float:
    """P(X >= x) under the model: Poisson for phi 1, the negative binomial above."""
    v = nbinom_log_sf(x, lam, phi)
    return 0.0 if v == -math.inf else math.exp(v)


def tested_count(rows: List[Dict], cap: int = OUTLET_CAP) -> int:
    """Distinct underlying items among a day's placements, each outlet adding at most cap of them. An
    item placed by several outlets counts once, under the first outlet (by article id) with room."""
    kept, per = set(), Counter()
    for r in sorted(rows, key=lambda r: r["id"]):
        it, o = r.get("item", r["id"]), r["outlet_id"]
        if it in kept or per[o] >= cap:
            continue
        kept.add(it)
        per[o] += 1
    return len(kept)


def _trimmed(values: List[float]) -> List[float]:
    vals = sorted(values)
    drop = int(math.floor(len(vals) * TRIM_SHARE + 1e-9))
    return vals[:len(vals) - drop] if drop else vals


def trimmed_mean(values: List[float]) -> Optional[float]:
    """The mean after dropping the top TRIM_SHARE of the values (the largest int(n / 10)), with the
    floor LAMBDA_FLOOR; None for no values."""
    if not values:
        return None
    kept = _trimmed(values)
    return max(LAMBDA_FLOOR, sum(kept) / float(len(kept)))


def dispersion(counts: Dict[str, int], disc: Dict[str, int], day: str) -> float:
    """The variance to mean ratio of the country's monitored days in the DISPERSION_DAYS before day,
    the busiest tenth set aside; at least 1, and 1 when fewer than MIN_BASELINE_DAYS days were
    monitored or the mean is 0."""
    vals = [counts.get(d, 0) for d in (_shift(day, -k) for k in range(DISPERSION_DAYS, 0, -1)) if disc.get(d, 0) > 0]
    if len(vals) < MIN_BASELINE_DAYS:
        return 1.0
    kept = _trimmed(vals)
    n = len(kept)
    m = sum(kept) / float(n)
    if m <= 0 or n < 2:
        return 1.0
    var = sum((v - m) ** 2 for v in kept) / float(n - 1)
    return max(1.0, var / m)


def baseline(counts: Dict[str, int], disc: Dict[str, int], day: str) -> Optional[Dict]:
    """The baseline of day: the WINDOW_DAYS days before it, monitored days only (disc above 0). None
    when fewer than MIN_BASELINE_DAYS of them were monitored."""
    days = [_shift(day, -k) for k in range(WINDOW_DAYS, 0, -1)]
    monitored = [d for d in days if disc.get(d, 0) > 0]
    if len(monitored) < MIN_BASELINE_DAYS:
        return None
    return {"days": days, "monitored": monitored, "lambda": trimmed_mean([counts.get(d, 0) for d in monitored])}


def flag_threshold(lam: float, phi: float = 1.0) -> Tuple[int, float]:
    """The smallest count that would be flagged against lam (spread phi), and its tail under the
    model: the chance a day with no surge is flagged, for the expected false flag rate."""
    x = max(MIN_COUNT, int(math.ceil(MIN_RATIO * lam - 1e-9)))
    p = tail(x, lam, phi)
    while p >= P_THRESHOLD:
        x += 1
        p = tail(x, lam, phi)
    return x, p


def is_flagged(x: int, lam: float, phi: float = 1.0) -> Tuple[bool, float]:
    p = tail(x, lam, phi)
    return (x >= MIN_COUNT and x >= MIN_RATIO * lam and p < P_THRESHOLD), p


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=65536)
def _shift(day: str, n: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=n)).isoformat()


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def last_tested_day_bound(latest: Optional[Dict]) -> str:
    """The first day not tested: the export day, which is partial (export.complete_window_30d). Today in
    UTC when there is no latest.json to read it from."""
    if latest and latest.get("window_start_30d"):
        from pipeline import export
        return export.complete_window_30d(latest)[1]
    return _today()


def day_entries(daily: Dict) -> Dict[str, Dict]:
    """{day: the daily file's day entry} over every month."""
    out = {}
    for m in (daily or {}).values():
        for d, e in ((m or {}).get("days") or {}).items():
            out[d] = e
    return out


def load_rows(conn, outlets: List[Dict]) -> Dict[Tuple[str, str], List[Dict]]:
    """The current state origin placements, by (day of record, export country), wires under WLD, with
    the underlying item each counts under, exactly as the daily files count them."""
    from pipeline import body_dups, model_only, placements, registry
    wire_ids = registry.distribution_wire_ids(outlets)
    items = body_dups.item_groups(conn)
    shadow = placements.shadowed(conn)   # copies counted through another row, as in the daily files
    out: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    for r in conn.execute(
            """SELECT a.id, a.country, a.outlet_id, a.title, a.url, a.published_at, a.discovered_at, a.day_of_record,
                      a.dup_group_id, c.route, c.method
               FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
               WHERE a.gate_relevant=1 AND c.category='A'%s ORDER BY a.id""" % model_only.evidence_sql()):
        # Undetermined labels (pipeline/model_only.py) are in no count, as in the daily files.
        if r["id"] in shadow:
            continue
        country = registry.WORLDWIDE if r["outlet_id"] in wire_ids else r["country"]
        day = r["day_of_record"] or (r["discovered_at"] or "")[:10]
        g = r["dup_group_id"] or r["id"]
        out[(day, country)].append({
            "id": r["id"], "outlet_id": r["outlet_id"], "title": r["title"], "url": r["url"], "route": r["route"],
            "date": day, "discovered_at": r["discovered_at"], "published_at": r["published_at"],
            "day_of_record": r["day_of_record"], "method": r["method"], "item": items.get(g, g)})
    return out


def outlet_discovery(conn, outlets: List[Dict]) -> Tuple[Dict[str, str], Dict[Tuple[str, str], int]]:
    """(first day each outlet was seen, {(outlet, day): items discovered}) from daily_outlet_discovery."""
    first, per = {}, {}
    for r in conn.execute("SELECT date, outlet_id, discovered FROM daily_outlet_discovery"):
        o, d = r["outlet_id"], r["date"]
        if (r["discovered"] or 0) > 0 and (o not in first or d < first[o]):
            first[o] = d
        per[(o, d)] = per.get((o, d), 0) + (r["discovered"] or 0)
    return first, per


def _meta_fallback(conn, outlets: List[Dict]) -> Dict:
    """What the analysis reads from meta, built from the database when there is no export context (the
    hand check sample is drawn from the command line)."""
    from pipeline import config, export
    first = conn.execute("SELECT MIN(date) FROM daily_discovery").fetchone()[0]
    return {"ruleset_changes": export.ruleset_changes(), "gate_changes": export.gate_changes(),
            "rediscovery": export.rediscovery(conn), "relay_collector": export.relay_status(conn, outlets),
            "feed_saturation": {"warning_share": config.FEED_SATURATION_WARNING_SHARE}, "first_discovered": first}


# ---------------------------------------------------------------------------
# Confounders
# ---------------------------------------------------------------------------

def _hours_between(later: Optional[str], earlier: Optional[str]) -> Optional[float]:
    try:
        a = dt.datetime.fromisoformat(str(later).replace("Z", "+00:00"))
        b = dt.datetime.fromisoformat(str(earlier).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    a = a if a.tzinfo else a.replace(tzinfo=dt.timezone.utc)
    b = b if b.tzinfo else b.replace(tzinfo=dt.timezone.utc)
    return (a - b).total_seconds() / 3600.0


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if not n:
        return 0.0
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _pct(v: float) -> str:
    return "%d percent" % int(round(100.0 * v))


def find_confounders(s: Dict) -> List[Dict]:
    """The confounders of one candidate day. s holds: day, country, x, base (baseline()), series {day:
    country entry}, entries {day: day entry}, rows (placements), meta, first_seen, outlet_disc, names."""
    out: List[Dict] = []
    day, c, x, base = s["day"], s["country"], s["x"], s["base"]
    series, entries, rows, meta = s["series"], s["entries"], s["rows"], s["meta"] or {}
    names = s.get("names") or {}
    bdays, mon = base["days"], base["monitored"]
    base_start = bdays[0]
    n_rows = len(rows) or x or 1

    def add(reason, detail, **extra):
        c = {"reason": reason, "detail": detail, "affects": "rate" if reason in RATE_ONLY else "count"}
        c.update(extra)
        out.append(c)

    def cv(d, k):
        return (series.get(d) or {}).get(k, 0) or 0

    by_outlet = Counter(r["outlet_id"] for r in rows)

    # new_outlet
    first_seen = s.get("first_seen") or {}
    new = {o: n for o, n in by_outlet.items() if first_seen.get(o) and first_seen[o] > _shift(day, -MIN_BASELINE_DAYS)}
    if new and sum(new.values()) >= NEW_OUTLET_SHARE * n_rows:
        add("new_outlet", "%s, first seen less than %d days before, carried %d of the %d placements" % (
            ", ".join("%s (first seen %s)" % (names.get(o, o), first_seen[o]) for o in sorted(new)),
            MIN_BASELINE_DAYS, sum(new.values()), n_rows))

    # backfill
    bf = sum(1 for r in rows if r.get("day_of_record") and r["day_of_record"] != (r.get("discovered_at") or "")[:10])
    if rows and bf >= BACKFILL_SHARE * n_rows:
        add("backfill", "%d of the %d placements were counted on their publication day by a backfill read" % (bf, n_rows))

    # outage_recovery
    reasons = []
    med = _median([cv(d, "disc") for d in mon])
    for k in range(1, OUTAGE_LOOKBACK + 1):
        d = _shift(day, -k)
        if med > 0 and cv(d, "disc") < OUTAGE_FLOOR * med:
            reasons.append("discovery fell to %d on %s against a baseline median of %d" % (cv(d, "disc"), d, med))
            break
    odisc = s.get("outlet_disc") or {}
    if odisc and rows:
        prev = _shift(day, -1)
        gap = {o: n for o, n in by_outlet.items()
               if odisc.get((o, prev), 0) == 0
               and sum(1 for d in bdays if odisc.get((o, d), 0) > 0) >= 0.5 * len(bdays)}
        if gap and sum(gap.values()) >= OUTLET_GAP_SHARE * n_rows:
            reasons.append("%s found nothing on %s but usually do, and carried %d of the %d placements" % (
                ", ".join(names.get(o, o) for o in sorted(gap)), prev, sum(gap.values()), n_rows))
    stale = sum(1 for r in rows if not (r.get("day_of_record") and r["day_of_record"] != (r.get("discovered_at") or "")[:10])
                and (_hours_between(r.get("discovered_at"), r.get("published_at")) or 0) > STALE_HOURS)
    if rows and stale >= STALE_SHARE * n_rows:
        reasons.append("%d of the %d placements were published more than %d hours before they were found" % (
            stale, n_rows, STALE_HOURS))
    if reasons:
        add("outage_recovery", "; ".join(reasons))

    # relay_incomplete
    rc = meta.get("relay_collector") or {}
    inc = set(rc.get("incomplete_days") or []) | {d for d, e in entries.items() if (e or {}).get("relay_incomplete")}
    # Only the countries that collector alone collects; the hosted runner still polls the shared ones. A day
    # that is itself incomplete is if anything understated, so only a complete day can be an artefact: one
    # that follows an incomplete day (the collector catching up) or one set against an incomplete baseline.
    # Since the relay was retired its outlets are inactive, so the countries it left (dropped_by_country) still
    # explain the days before then.
    relay_only = set(rc.get("countries") or []) | set(rc.get("dropped_by_country") or {})
    if inc and (not rc or c in relay_only) and day not in inc:
        prev = _shift(day, -1)
        in_base = [d for d in bdays if d in inc]
        if prev in inc:
            add("relay_incomplete", "the collector on the owner's machine ran too few hours on %s, the day before, "
                "and in full on the day, so the day may carry its catch up" % prev)
        elif len(in_base) >= RELAY_BASELINE_DAYS:
            add("relay_incomplete", "the collector on the owner's machine ran too few hours on %d of the baseline days "
                "and in full on the day" % len(in_base))

    # ruleset_change
    # Relabelling is retroactive, so a change only confounds where the day and its baseline were labelled
    # under different rules: older labels in a clearly different share on the day and on the baseline
    # days, or a somewhat higher share on the day when a change took effect on it or just before. When
    # every label is older, both sides were labelled alike.
    def older(ds):
        o = t = 0
        for d in ds:
            e = entries.get(d) or {}
            # a model label can be on an older ruleset and an older prompt at once: the larger, not the sum
            o += max(e.get("labels_on_older_ruleset") or 0, e.get("labels_on_older_prompt") or 0)
            t += sum((v or {}).get("cls", 0) or 0 for v in (e.get("countries") or {}).values())
        return min(1.0, o / float(t)) if t else 0.0
    # Only a change inside the baseline or on the day can have labelled the two sides differently: one
    # before the whole baseline came before every label the comparison reads, so a change stops blocking
    # new days WINDOW_DAYS after it, however long the relabelling of older days takes.
    od, ob = older([day]), older(bdays)
    changes = meta.get("ruleset_changes") or []
    inside = sorted((ch for ch in changes if base_start <= ch.get("date", "") <= day), key=lambda ch: ch.get("date", ""))
    recent = [ch for ch in inside if _shift(day, -RULESET_SETTLE_DAYS) <= ch.get("date", "") <= day]
    if inside and ((recent and od - ob > OLDER_LABEL_SHARE) or abs(od - ob) > OLDER_LABEL_GAP):
        last = inside[-1]
        add("ruleset_change", "ruleset %s took effect on %s, inside the %d days this day is compared with, and "
            "relabelling under it is not finished: %s of the labels on the day and %s on its baseline days still "
            "carry an older ruleset or prompt, so the two were labelled under different rules" % (
                last.get("version"), last["date"], WINDOW_DAYS, _pct(od), _pct(ob)),
            ruleset_date=last["date"], older_day=round(od, 3), older_baseline=round(ob, 3))

    # gate_change
    for ch in meta.get("gate_changes") or []:
        g = ch.get("date", "")
        if not (base_start < g <= day):
            continue
        if g >= _shift(day, -GATE_SETTLE_DAYS):
            add("gate_change", "relevance gate %s took effect on %s, just before the day" % (ch.get("version"), g))
            break
        pre = [cv(d, "A") for d in mon if d < g]
        post = [cv(d, "A") for d in mon if d >= g]
        if len(post) < GATE_MIN_SIDE_DAYS and pre:
            add("gate_change", "relevance gate %s took effect on %s, too few days since to tell whether the level moved" % (
                ch.get("version"), g))
            break
        if len(pre) >= GATE_MIN_SIDE_DAYS and len(post) >= GATE_MIN_SIDE_DAYS:
            mpre, mpost = sum(pre) / float(len(pre)), sum(post) / float(len(post))
            if mpost >= GATE_LEVEL_RATIO * max(mpre, LAMBDA_FLOOR):
                add("gate_change", "relevance gate %s took effect on %s and the level after it (%.1f a day) is at least twice the level before (%.1f)" % (
                    ch.get("version"), g, mpost, mpre))
                break

    # saturation
    warn = ((meta.get("feed_saturation") or {}).get("warning_share")) or SATURATION_SHARE
    polls = sum(cv(d, "polls") for d in bdays)
    sat = sum(cv(d, "sat") for d in bdays)
    if polls and sat / float(polls) >= warn:
        add("saturation", "%s of the feed polls on the baseline days came back saturated, so items were lost between polls" % _pct(sat / float(polls)))

    # llm_ceiling
    # The cap and the sampling are recorded on the day the model ran, and a candidate judged later still
    # counts on its own day of record. What truncates a day is candidates still waiting: so this needs the
    # cap or sampling on record, model labels making up a real part of the count, and clearly more of the
    # baseline's China coverage still waiting than of the day's.
    a_day, al_day = cv(day, "A"), cv(day, "Al")
    a_base, al_base = sum(cv(d, "A") for d in bdays), sum(cv(d, "Al") for d in bdays)
    share = max(al_day / float(a_day) if a_day else 0.0, al_base / float(a_base) if a_base else 0.0)

    def waiting(ds):
        w = t = 0
        for d in ds:
            v = series.get(d) or {}
            w += v.get("pending", 0) or 0
            t += sum(v.get(k, 0) or 0 for k in ("A", "B", "C", "pending"))
        return (w / float(t)) if t else 0.0
    wd, wb = waiting([day]), waiting(bdays)
    if share >= MODEL_SHARE and wb - wd >= WAITING_GAP:
        capped = []
        for d in sorted(entries):
            if d < base_start:
                continue
            e = entries.get(d) or {}
            samp = (e.get("llm_sampling") or {}).get(c)
            if e.get("llm_ceiling_hit") or (samp and len(samp) >= 2 and (samp[1] or 0) < (samp[0] or 0)):
                capped.append(d)
        if capped:
            add("llm_ceiling", "model labels are %s of the count; %s of the China coverage on the baseline days still waits "
                "for the model against %s on the day, and the daily model cap was hit or candidates sampled on %d days since %s" % (
                    _pct(share), _pct(wb), _pct(wd), len(capped), base_start))

    # rediscovery (the rate only)
    red = meta.get("rediscovery")
    if red and red.get("fixed_from") and base_start < red["fixed_from"]:
        add("rediscovery", "discovery counts before %s carry repeats, so the rate per discovered item is understated there; the count is not affected" % red["fixed_from"])

    # denominator_spike
    discs = [cv(d, "disc") for d in mon]
    mdisc = sum(discs) / float(len(discs)) if discs else 0.0
    if mdisc and cv(day, "disc") >= DENOM_RATIO * mdisc:
        rr = s.get("rate_ratio")
        if rr is None or rr < DENOM_RATE_RATIO:
            add("denominator_spike", "discovery was %.1f times its baseline and the rate per discovered item %s" % (
                cv(day, "disc") / mdisc, "%.1f times its baseline" % rr if rr is not None else "has no baseline"))
    return out


# ---------------------------------------------------------------------------
# The analysis
# ---------------------------------------------------------------------------

def analyse(daily: Dict, until: str, meta: Optional[Dict] = None,
            rows_by: Optional[Dict[Tuple[str, str], List[Dict]]] = None, first_seen: Optional[Dict[str, str]] = None,
            outlet_disc: Optional[Dict[Tuple[str, str], int]] = None, names: Optional[Dict[str, str]] = None,
            deadline: Optional[float] = None) -> Dict:
    """Every test and every spike, from the daily files' {month: {...}} and the export bound until (the
    partial export day, never tested). Pure: everything from the database comes in as arguments. A
    day's tested count is tested_count of its placements in rows_by; a day with no rows there (only in
    tests: load_rows carries every placement) falls back to the daily file's state origin count. Raises
    OutOfTime once time.monotonic() passes deadline."""
    meta = meta or {}
    rows_by = rows_by or {}
    names = names or {}
    entries = day_entries(daily)
    series: Dict[str, Dict[str, Dict]] = defaultdict(dict)
    for d, e in entries.items():
        for c, v in ((e or {}).get("countries") or {}).items():
            if c not in UNMONITORED:
                series[c][d] = v or {}
    all_days = sorted(d for d in entries if d < until)
    first = max([str(d)[:10] for d in (meta.get("first_discovered"), all_days[0] if all_days else None) if d] or [until])
    tests, expected, spikes = 0, 0.0, []
    for c in sorted(series):
        if deadline is not None and time.monotonic() >= deadline:
            raise OutOfTime("unusual days: the export's deadline came before every country was tested")
        sc = series[c]
        counts = {d: (tested_count(rows_by[(d, c)]) if (d, c) in rows_by else (v.get("A") or 0)) for d, v in sc.items()}
        disc = {d: (v.get("disc") or 0) for d, v in sc.items()}
        d = _shift(first, MIN_BASELINE_DAYS)
        while d < until:
            base = baseline(counts, disc, d)
            if base is not None:
                lam = base["lambda"]
                phi = dispersion(counts, disc, d)
                tests += 1
                expected += flag_threshold(lam, phi)[1]
                x = counts.get(d, 0)
                hit, p = is_flagged(x, lam, phi)
                if hit:
                    spikes.append(_spike(c, d, x, lam, p, base, sc, entries, rows_by.get((d, c), []), meta,
                                         first_seen, outlet_disc, names, phi))
            d = _shift(d, 1)
    spikes.sort(key=lambda s: (s["date"], s["count"], s["country"]), reverse=True)
    return {"tests": tests, "expected_false_flags": expected, "spikes": spikes,
            "first_tested": _shift(first, MIN_BASELINE_DAYS) if tests else None,
            "last_complete_day": _shift(until, -1)}


def _spike(c, d, x, lam, p, base, sc, entries, rows, meta, first_seen, outlet_disc, names, phi=1.0) -> Dict:
    v = sc.get(d) or {}
    disc = v.get("disc") or 0
    placements = v.get("A") or len(rows) or x
    bA = sum((sc.get(k) or {}).get("A", 0) or 0 for k in base["monitored"])
    bD = sum((sc.get(k) or {}).get("disc", 0) or 0 for k in base["monitored"])
    rate = (placements / float(disc)) if disc else None
    rate_exp = (bA / float(bD)) if bD else None
    rate_ratio = (rate / rate_exp) if (rate is not None and rate_exp) else None
    ui = ((entries.get(d) or {}).get("underlying_items") or {}).get(c) or {}
    uniq = len(ui.get("A") or []) if ui else len({r["item"] for r in rows})
    by_outlet = Counter(r["outlet_id"] for r in rows)
    by_item: Dict = defaultdict(list)
    for r in rows:
        by_item[r["item"]].append(r)
    item_order = sorted(by_item, key=lambda k: (-len(by_item[k]), min(r["id"] for r in by_item[k])))
    top_items = [{"item": k, "placements": len(by_item[k]), "title": by_item[k][0]["title"],
                  "outlets": sorted({r["outlet_id"] for r in by_item[k]})} for k in item_order[:TOP_N]]
    top_outlets = [{"id": o, "name": names.get(o, o), "placements": n}
                   for o, n in sorted(by_outlet.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]]
    items = [{"id": r["id"], "outlet_id": r["outlet_id"], "title": r["title"], "url": r["url"], "route": r["route"],
              "date": r["date"]} for k in item_order for r in by_item[k]][:MAX_ITEMS]
    n = len(rows) or placements
    top_item_share = (len(by_item[item_order[0]]) / float(n)) if item_order else (1.0 if uniq == 1 else None)
    top_outlet_share = (top_outlets[0]["placements"] / float(n)) if top_outlets else None
    descriptors = []
    # "mostly" on the site, so only above half (a tie at half is not mostly anything)
    if top_item_share is not None and top_item_share > ONE_SHARE:
        descriptors.append("one_item")
    if top_outlet_share is not None and top_outlet_share > ONE_SHARE:
        descriptors.append("one_outlet")
    conf = find_confounders({"day": d, "country": c, "x": placements, "base": base, "series": sc, "entries": entries,
                             "rows": rows, "meta": meta, "first_seen": first_seen, "outlet_disc": outlet_disc,
                             "names": names, "rate_ratio": rate_ratio})
    return {"date": d, "country": c, "count": placements, "uniq": uniq, "tested": x, "expected": round(lam, 2),
            "dispersion": round(phi, 2), "outlets": len(by_outlet) if rows else None,
            "baseline_days": len(base["monitored"]), "p": float("%.3g" % p), "ratio": round(x / lam, 1),
            "rate": None if rate is None else round(rate, 5), "rate_expected": None if rate_exp is None else round(rate_exp, 5),
            "top_outlets": top_outlets, "top_items": top_items,
            "top_item_share": None if top_item_share is None else round(top_item_share, 3),
            "top_outlet_share": None if top_outlet_share is None else round(top_outlet_share, 3),
            "confounders": [dict(k) for k in conf],
            "descriptors": descriptors,
            "status": "check" if any(k["affects"] == "count" for k in conf) else "clean",
            "items": items}


def run_analysis(conn, ctx: Optional[Dict] = None, deadline: Optional[float] = None) -> Dict:
    """The analysis for the export (ctx from pipeline.measures) or, without ctx, from the database alone.
    Cached in ctx, so compute, build_export and meta_summary share one run. Raises OutOfTime past
    deadline."""
    ctx = ctx if ctx is not None else {}
    if "_spikes" in ctx:
        return ctx["_spikes"]
    from pipeline import export, registry
    outlets = ctx.get("outlets")
    if outlets is None:
        outlets = registry.load_outlets()
    daily = ctx.get("daily")
    if daily is None:
        daily = export.build_daily(conn, outlets)
    meta = ctx.get("meta") or _meta_fallback(conn, outlets)
    until = last_tested_day_bound(ctx.get("latest"))
    first_seen, odisc = outlet_discovery(conn, outlets)
    names = {o["id"]: o.get("name") or o["id"] for o in outlets}
    if deadline is not None and time.monotonic() >= deadline:
        raise OutOfTime("unusual days: the export's deadline came before the analysis began")
    res = analyse(daily, until, meta, load_rows(conn, outlets), first_seen, odisc, names, deadline)
    ctx["_spikes"] = res
    return res


# ---------------------------------------------------------------------------
# The measure interface (pipeline/measures.py)
# ---------------------------------------------------------------------------

def inputs_present(conn) -> int:
    """Days with discovery on record: at least MIN_BASELINE_DAYS + 1 are needed for one test."""
    return int(conn.execute("SELECT COUNT(DISTINCT date) FROM daily_discovery").fetchone()[0] or 0)


def compute(conn, ctx, deadline) -> Dict:
    """Nothing is stored: the analysis reads the daily files and the articles table and is cached in ctx.
    It stops at the deadline (OutOfTime), and the measure is then recorded as failed for this export."""
    res = run_analysis(conn, ctx, deadline)
    return {"tests": res["tests"], "spikes": len(res["spikes"])}


def _rate(res: Dict) -> Optional[float]:
    return (res["expected_false_flags"] / res["tests"]) if res["tests"] else None


def build_export(conn, ctx) -> Dict:
    res = run_analysis(conn, ctx)
    from pipeline import store
    clean = sum(1 for s in res["spikes"] if s["status"] == "clean")
    return {"generated_at": store.utcnow(), "version": VERSION,
            "method": {"window_days": WINDOW_DAYS, "min_baseline_days": MIN_BASELINE_DAYS, "trim_share": TRIM_SHARE,
                       "lambda_floor": LAMBDA_FLOOR, "p_threshold": P_THRESHOLD, "min_count": MIN_COUNT,
                       "min_ratio": MIN_RATIO, "outlet_cap": OUTLET_CAP, "dispersion_days": DISPERSION_DAYS,
                       "model": "negative binomial on the country's own variance to mean ratio (Poisson at 1)",
                       "series": "distinct underlying state origin items per day of record, each outlet counting "
                                 "at most %d" % OUTLET_CAP},
            "first_tested": res["first_tested"], "last_complete_day": res["last_complete_day"],
            "tests": res["tests"], "expected_false_flags": round(res["expected_false_flags"], 3),
            "expected_false_flag_rate": None if _rate(res) is None else float("%.3g" % _rate(res)),
            "counts": {"spikes": len(res["spikes"]), "clean": clean, "check": len(res["spikes"]) - clean,
                       "check_relabelling_only": sum(1 for s in res["spikes"] if _relabelling_only(s))},
            "spikes": res["spikes"]}


def _relabelling_only(s: Dict) -> bool:
    """A day to check only because relabelling after a ruleset change is not finished."""
    count = {c["reason"] for c in s.get("confounders") or [] if c.get("affects") == "count"}
    return count == {"ruleset_change"}


def meta_summary(conn, ctx) -> Dict:
    res = run_analysis(conn, ctx)
    clean = sum(1 for s in res["spikes"] if s["status"] == "clean")
    last = res["last_complete_day"]
    recent = [s for s in res["spikes"] if s["date"] > _shift(last, -RECENT_DAYS)]
    return {"tests": res["tests"], "spikes": len(res["spikes"]), "clean": clean,
            "recent_30d": len(recent), "recent_30d_clean": sum(1 for s in recent if s["status"] == "clean"),
            "expected_false_flags": round(res["expected_false_flags"], 3),
            "expected_false_flag_rate": None if _rate(res) is None else float("%.3g" % _rate(res)),
            "last_complete_day": last}


def methodology_text(block) -> str:
    summ = (block or {}).get("summary") or {}
    tests = summ.get("tests")
    exp = summ.get("expected_false_flags")
    if tests:
        flagged = summ.get("spikes") or 0
        chance = ("Over the %s country days tested so far, %d %s flagged. The model puts the number it would flag "
                  "by chance alone at about %s, but it assumes each country's days vary as they did before, and "
                  "real days are lumpier, so that figure is a rough guide and not a promise." % (
                      format(tests, ","), flagged, "was" if flagged == 1 else "were",
                      ("%.1f" % exp) if exp is not None else "n/a"))
    else:
        chance = "No country has enough monitored days to be tested yet, so nothing has been flagged."
    return (
        "Unusual days are days on which a country, or the worldwide press release wires, carried far more state "
        "origin text than usual. What is counted is the day's distinct underlying items, each outlet adding at most "
        "%d of them, so one outlet's batch or one item placed many times is a lump and never a surge on its own. "
        "Each complete day is compared with the 28 complete days before it, counting only the days on which the "
        "country's outlets were collected at all, and at least 14 of them are needed. The expected count is the mean "
        "of those days after the busiest tenth of them is set aside, and never less than 0.2. Daily counts vary "
        "more than chance alone would make them, so the spread is taken from the country's own last %d days (a "
        "negative binomial model). A day is flagged when it counts at least 5, at least three times the expected "
        "count, with a chance under 1 in 1,000 of reaching that count on an ordinary day. The day the export runs "
        "is partial and is never tested. China is not monitored and has no series. " % (OUTLET_CAP, DISPERSION_DAYS)
        + chance
        + "\n\n"
        "Each flagged day lists the outlets and underlying items behind it, and the checks that could make it an "
        "artefact of the instrument rather than a surge of state text: a newly added outlet, a backfill read, a "
        "collector recovering from an outage, an incomplete day for the relay on the owner's machine (stopped on 22 October 2026), a ruleset "
        "or relevance gate change, saturated feeds on the baseline days, the daily model cap, and a surge in "
        "everything discovered rather than in state text alone. A day with any of these is shown muted with its "
        "reason, as one to check, and a day with none is shown as clean. A ruleset change counts only when it took "
        "effect inside the 28 days a day is compared with and the labels on the two sides still differ in age: until "
        "the older days are labelled again under the new rules, those days stay to check, and the site says so; a "
        "change stops affecting new days 28 days after it. The repeats in the discovery counts before they were "
        "fixed are noted too, but they touch only the rate per discovered item, not the count. Unusual days are "
        "report only: they change no count, and a hand check of up to 30 flagged days, each judged a real surge or "
        "an artefact, has to find at least 80 percent real before the marking provisional comes off.")


# ---------------------------------------------------------------------------
# The hand check
# ---------------------------------------------------------------------------

SAMPLE_FIELDS = ["sample_id", "date", "country", "placements", "underlying_items", "tested", "expected",
                 "top_outlets", "top_items", "item_urls", "judgement", "artefact_type", "notes"]


def draw_sample(conn, n=SAMPLE_MAX, seed=None, out_dir=None):
    """Every spike, up to SAMPLE_MAX (a seeded random draw when there are more), for a coder to judge a
    real surge of state text or an artefact, with its type. The status and confounders stay in the key."""
    from pipeline import measures
    res = run_analysis(conn, {})
    spikes = list(res["spikes"])
    k = max(0, min(int(n or SAMPLE_MAX), SAMPLE_MAX, len(spikes)))
    if len(spikes) > k:
        spikes = random.Random(str(seed)).sample(spikes, k)
    spikes.sort(key=lambda s: (s["date"], s["country"]))
    rows, key = [], {}
    for i, s in enumerate(spikes, 1):
        sid = "S%02d" % i
        rows.append({"sample_id": sid, "date": s["date"], "country": s["country"], "placements": s["count"],
                     "expected": s["expected"], "underlying_items": s["uniq"], "tested": s.get("tested", s["count"]),
                     "top_outlets": "; ".join("%s (%d)" % (o["name"], o["placements"]) for o in s["top_outlets"]),
                     "top_items": " | ".join("%s (%d)" % (t["title"], t["placements"]) for t in s["top_items"]),
                     "item_urls": " ".join(it["url"] for it in s["items"][:10] if it.get("url")),
                     "judgement": "", "artefact_type": "", "notes": ""})
        key[sid] = {"date": s["date"], "country": s["country"], "status": s["status"],
                    "confounders": [c["reason"] for c in s["confounders"]], "descriptors": s["descriptors"]}
    return measures.write_sample(Path(out_dir or measures.REVIEWS_DIR), MEASURE_ID, rows, SAMPLE_FIELDS,
                                 {"spikes": key, "judgements": list(JUDGEMENTS), "artefact_types": list(ARTEFACT_TYPES),
                                  "guide": "data/reviews/SPIKES_GUIDE.md", "threshold": PRECISION_THRESHOLD},
                                 version=VERSION, seed=seed)


def _judgement(v) -> Optional[str]:
    v = str(v or "").strip().lower()
    if v in ("real", "surge", "real surge"):
        return "real"
    if v in ("artefact", "artifact"):
        return "artefact"
    return None


def score_check(rows, key):
    """Precision: the share of the coded spikes judged a real surge of state text. Also reported: the
    precision among clean spikes, and how often a coded artefact's type is one of the confounders the
    spike carried."""
    from pipeline import measures
    spikes = (key or {}).get("spikes") or {}
    ids = set(spikes) if spikes else {r.get("sample_id") for r in rows}
    n = len(ids)
    coded = real = clean_coded = clean_real = art = art_matched = 0
    for r in rows:
        sid = r.get("sample_id")
        if sid not in ids:
            continue
        j = _judgement(r.get("judgement"))
        if j is None:
            continue
        coded += 1
        is_clean = (spikes.get(sid) or {}).get("status") == "clean"
        if j == "real":
            real += 1
            clean_real += 1 if is_clean else 0
        else:
            t = str(r.get("artefact_type") or "").strip().lower()
            if t:
                art += 1
                art_matched += 1 if t in ((spikes.get(sid) or {}).get("confounders") or []) else 0
        clean_coded += 1 if is_clean else 0
    value = (real / float(coded)) if coded else None
    return measures.check_result("precision", value, PRECISION_THRESHOLD, n, coded,
                                 clean_precision=(clean_real / float(clean_coded)) if clean_coded else None,
                                 artefacts_typed=art,
                                 artefacts_matching_a_confounder=art_matched)
