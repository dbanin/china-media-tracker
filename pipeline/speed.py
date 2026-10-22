"""Republication speed: how soon a state origin copy runs after the state original it reproduces.

Report only. It reads the pairs pipeline/copies.py stores in state_copies and writes nothing to the
database: no label, count, denominator or outlet figure changes, and China stays unmonitored.

The lag is recomputed here from the stored publication times, never taken from state_copies.lag_seconds,
and each time counts only when lookalike.published_ts finds it credible against that item's own
discovery (readable, not after discovery, not before collection began, not more than two weeks before
discovery unless a backfill read brought it in). Discovery time is never used as a lag.

Two kinds of lag, never pooled:
  absolute  the original is a reference item (pipeline/reference.py): the state outlet's own page
  relative  the original is the first monitored copy: "behind the first monitored copy"

Buckets. Timed pairs (both times carry a time of day): same hour (under 1 h, including a copy up to
1 h ahead, which is clock noise), same day (1 to 24 h), 1 to 3 days (24 to 72 h), later. Date pairs
(either time is a date only): same date, 1 to 3 days, later, by calendar date, counted in one zone:
the zone of the date (a reference source's dates are Beijing dates, except the Global Times sitemap's
UTC dates, reference.date_zone_minutes; a copy's date is in the zone its stamp wrote, else UTC), the
timed side converted into it. Outside every distribution: copy_precedes_original (the copy is more
than 1 h, or a calendar day, ahead of its original) and no_credible_time (a time is missing,
unreadable or not credible).

Distinct originals. One press release run by ten sister mastheads is ten pairs and one original, so
every group counts its distinct originals (originals, originals_timed) beside its pairs, and a median
is given only from MIN_GROUP_MEDIAN distinct timed originals (MIN_OUTLET_PAIRS for an outlet).

Diagnostics. Reference times are normalised per source by pipeline/reference.py, and some listings
label their zones wrongly (a Beijing time written as UTC reads 8 h late). For each reference source
the export gives the median of discovered_at minus published_at, the share where publication comes
after discovery (negative), and the share that are a date only, with a flag when either is high
(from DIAG_NEGATIVE_MIN negative items, or DIAG_DATE_ONLY_MIN dated items). Two more come from
pipeline/reference.py: a source whose items still carry a date only after their first day
(reference.source_status), and a source whose two listings disagree on an item's time
(reference.time_checks: the CGTN sitemap reading earlier than the RSS is what a switch from Beijing
time written as "Z" to true UTC looks like, which discovery alone would never show). The export also
carries each source's status counts (fetched, expired, failed, paywalled).

Hand check: 60 reference pairs; the coder reads the publication time and zone shown on each page,
and the bucket those times give is compared with the measure's. It passes at 85 percent agreement.
data/reviews/SPEED_GUIDE.md is the guide.
"""
import datetime as dt
import random
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from pipeline import lookalike, measures

MEASURE_ID = "speed"
VERSION = "2"

HOUR = 3600
DAY = 86400
PRECEDES_TOLERANCE = HOUR          # a copy up to 1 h ahead of its original is clock noise, same hour
MIN_OUTLET_PAIRS = 10              # an outlet is listed, with a median, from 10 timed pairs
MIN_GROUP_MEDIAN = 5               # route, country and source medians need 5 timed pairs
CHECK_THRESHOLD = 0.85
SAMPLE_N = 60
DIAG_NEGATIVE_FLAG = 0.05          # share of items published after discovery that flags a source
DIAG_NEGATIVE_MIN = 3              # and at least this many such items, so one stray time flags nothing
DIAG_DATE_ONLY_FLAG = 0.25         # share of dated items with a date only that flags a source (observed: 0.40
DIAG_DATE_ONLY_MIN = 10            # to 0.49 at three sources, 0.06 at Xinhua), from this many dated items

TIMED_BUCKETS = ("same_hour", "same_day", "days_1_3", "later")
DATE_BUCKETS = ("same_date", "date_1_3", "date_later")
OUTSIDE_BUCKETS = ("copy_precedes_original", "no_credible_time")
BUCKETS = TIMED_BUCKETS + DATE_BUCKETS + OUTSIDE_BUCKETS
BUCKET_LABELS = {
    "same_hour": "Same hour", "same_day": "Same day (1 to 24 h)", "days_1_3": "1 to 3 days", "later": "Later",
    "same_date": "Same date (date only)", "date_1_3": "1 to 3 days (date only)", "date_later": "Later (date only)",
    "copy_precedes_original": "Copy ahead of its original", "no_credible_time": "No credible time",
}
KINDS = {"reference": "absolute", "monitored": "relative"}
TIMED_RES = ("second", "minute", "hour")
DATE_RES = ("day", "date")


# ---------------------------------------------------------------------------
# Times and buckets
# ---------------------------------------------------------------------------

def _date_only(s: Optional[str]) -> bool:
    return bool(s) and "T" not in s.strip() and " " not in s.strip()


def resolution(stored_res: Optional[str], published_at: Optional[str]) -> str:
    """second, minute, hour, day or none: the stored resolution when it says (day and date both mean a
    date only, even when the string carries a midnight time), else read from the string."""
    if not (published_at or "").strip():
        return "none"
    r = (stored_res or "").strip().lower()
    if r == "none":
        return r
    if r in DATE_RES or _date_only(published_at):
        return "day"
    return r if r in TIMED_RES else "second"


def credible(published_at: Optional[str], discovered_at: Optional[str], day_of_record: Optional[str] = None) -> Optional[float]:
    """The publication time as a timestamp when lookalike.published_ts finds it credible, else None."""
    return lookalike.published_ts({"published_at": published_at, "discovered_at": discovered_at,
                                   "day_of_record": day_of_record})


_WRITTEN = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})(?:[T ][\d:.]+)?\s*(Z|z|[+-]\d{2}:?\d{2})?\s*$")


def _written_date(item: Dict) -> Tuple[Optional[dt.date], Optional[int]]:
    """The calendar date a date only item carries as written (raw, the source row's own string, since a
    stored UTC midnight of a local date can fall on the day before), and its zone in minutes east of
    UTC: the stamp's own offset when it writes one, else the item's date_zone (a reference source's,
    reference.date_zone_minutes), else None."""
    for s in (item.get("raw"), item.get("published_at")):
        m = _WRITTEN.match(s or "")
        if not m:
            continue
        try:
            day = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        z = m.group(2)
        if z in ("Z", "z"):
            return day, 0
        if z:
            z = z.replace(":", "")
            mins = int(z[1:3]) * 60 + int(z[3:5])
            return day, (-mins if z[0] == "-" else mins)
        return day, item.get("date_zone")
    return None, item.get("date_zone")


def _date_in(ts: float, zone: Optional[int]) -> dt.date:
    return dt.datetime.fromtimestamp(ts, dt.timezone(dt.timedelta(minutes=zone or 0))).date()


def date_days(copy: Dict, cts: float, cres: str, orig: Dict, ots: float, ores: str) -> int:
    """Calendar days from the original to the copy, counted in one zone. A date only side keeps the
    date it wrote, and the timed side is converted into that date's zone (UTC when the zone is not
    known). Two dates in two zones are compared by their middays in UTC, rounded to whole days."""
    if cres == "day" and ores == "day":
        cd, cz = _written_date(copy)
        od, oz = _written_date(orig)
        cd, od = cd or _date_in(cts, cz), od or _date_in(ots, oz)
        if (cz or 0) == (oz or 0):
            return (cd - od).days
        mid = lambda d, z: (dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.timezone.utc).timestamp()  # noqa: E731
                            - (z or 0) * 60)
        return int(round((mid(cd, cz) - mid(od, oz)) / DAY))
    if cres == "day":
        cd, cz = _written_date(copy)
        return ((cd or _date_in(cts, cz)) - _date_in(ots, cz)).days
    od, oz = _written_date(orig)
    return (_date_in(cts, oz) - (od or _date_in(ots, oz))).days


def _credible_of(item: Dict, res: str) -> Optional[float]:
    """credible() of an item's time; a date only is read as the start of that date in its own zone
    (_written_date), so a Beijing date found in the first eight Beijing hours is not taken for a
    time after its discovery."""
    pub = item.get("published_at")
    if res == "day":
        day, zone = _written_date(item)
        if day is not None and zone:
            sign = "-" if zone < 0 else "+"
            pub = "%sT00:00:00%s%02d:%02d" % (day.isoformat(), sign, abs(zone) // 60, abs(zone) % 60)
    return credible(pub, item.get("discovered_at"), item.get("day_of_record"))


def timed_bucket(lag_seconds: float) -> str:
    if lag_seconds < -PRECEDES_TOLERANCE:
        return "copy_precedes_original"
    if lag_seconds < HOUR:
        return "same_hour"
    if lag_seconds < DAY:
        return "same_day"
    if lag_seconds < 3 * DAY:
        return "days_1_3"
    return "later"


def date_bucket(days: int) -> str:
    if days < 0:
        return "copy_precedes_original"
    if days == 0:
        return "same_date"
    if days <= 3:
        return "date_1_3"
    return "date_later"


def pair_lag(copy: Dict, orig: Dict) -> Dict:
    """{bucket, lag_seconds, days, resolution} for one pair. copy and orig are dicts with published_at,
    discovered_at, day_of_record (optional), res (the stored resolution, optional), raw (the source
    row's published_at as written, optional) and date_zone (minutes east of UTC of a bare date,
    optional; a reference source's)."""
    cres = resolution(copy.get("res"), copy.get("published_at"))
    ores = resolution(orig.get("res"), orig.get("published_at"))
    cts = _credible_of(copy, cres)
    ots = _credible_of(orig, ores)
    if cres == "none" or ores == "none" or cts is None or ots is None:
        return {"bucket": "no_credible_time", "lag_seconds": None, "days": None, "resolution": None}
    if cres in TIMED_RES and ores in TIMED_RES:
        lag = int(round(cts - ots))
        return {"bucket": timed_bucket(lag), "lag_seconds": lag, "days": None, "resolution": "time"}
    days = date_days(copy, cts, cres, orig, ots, ores)
    return {"bucket": date_bucket(days), "lag_seconds": None, "days": days, "resolution": "date"}


def quantile(values: List[float], q: float) -> Optional[float]:
    """Linear interpolation between closest ranks (the inclusive method), None for no values."""
    v = sorted(values)
    if not v:
        return None
    pos = (len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def _hours(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x / HOUR, 2)


def _orig_key(it: Dict):
    k = it.get("orig_key")
    return k if k is not None else ("pair", it.get("copy_id"), id(it))


def summarise(items: Iterable[Dict], min_median: int = MIN_GROUP_MEDIAN) -> Dict:
    """n (pairs inside the distributions), n_timed, n_date, the distinct originals behind them
    (originals, originals_timed), median/p25/p75 hours over the timed lags (when there are at least
    min_median distinct timed originals, so one release run by many sister outlets is not a median),
    and the count in every bucket."""
    counts = {b: 0 for b in BUCKETS}
    lags = []
    origs, origs_timed = set(), set()
    for it in items:
        counts[it["bucket"]] += 1
        if it["bucket"] in TIMED_BUCKETS:
            lags.append(it["lag_seconds"])
            origs_timed.add(_orig_key(it))
        if it["bucket"] not in OUTSIDE_BUCKETS:
            origs.add(_orig_key(it))
    n_timed = sum(counts[b] for b in TIMED_BUCKETS)
    n_date = sum(counts[b] for b in DATE_BUCKETS)
    enough = len(origs_timed) >= min_median
    return {"n": n_timed + n_date, "n_timed": n_timed, "n_date": n_date,
            "originals": len(origs), "originals_timed": len(origs_timed),
            "outside": sum(counts[b] for b in OUTSIDE_BUCKETS),
            "median_hours": _hours(quantile(lags, 0.5)) if enough else None,
            "p25_hours": _hours(quantile(lags, 0.25)) if enough else None,
            "p75_hours": _hours(quantile(lags, 0.75)) if enough else None,
            "buckets": counts}


# ---------------------------------------------------------------------------
# Reading the pairs
# ---------------------------------------------------------------------------

def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def inputs_present(conn) -> int:
    """Current pairs with an original, reference or monitored."""
    if not _table_exists(conn, "state_copies"):
        return 0
    return conn.execute("SELECT COUNT(*) FROM state_copies WHERE current=1 AND original_kind IN ('reference','monitored')").fetchone()[0]


BJT_MINUTES = 480


def _date_zones() -> Dict[str, Optional[int]]:
    """{reference source: minutes east of UTC of its bare dates} (pipeline/reference.py), {} when the
    registry cannot be read; a source not in it takes a Beijing date."""
    try:
        from pipeline import reference
        return reference.date_zones()
    except Exception:  # noqa: BLE001
        return {}


def _outlet_maps(ctx: Optional[Dict]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """{outlet id: export country (WLD for a distribution wire)}, {outlet id: name}."""
    from pipeline import registry
    outlets = (ctx or {}).get("outlets")
    if outlets is None:
        outlets = registry.load_outlets()
    return ({o["id"]: registry.export_country(o) for o in outlets}, {o["id"]: o.get("name") or o["id"] for o in outlets})


def _pick(stored: Optional[str], fallback: Optional[str]) -> Optional[str]:
    return stored if (stored or "").strip() else fallback


def load_pairs(conn, ctx: Optional[Dict] = None) -> List[Dict]:
    """Every current pair with an original, each with its kind (absolute or relative), bucket and lag,
    and the copy's outlet, export country, route and the original's source."""
    if not _table_exists(conn, "state_copies"):
        return []
    country_of, _ = _outlet_maps(ctx)
    has_ref = _table_exists(conn, "reference_items")
    zones = _date_zones()
    from pipeline import model_only
    rows = conn.execute(
        """SELECT s.copy_id, s.original_kind, s.ref_id, s.original_article_id, s.original_source,
                  s.copy_published_at, s.original_published_at, s.copy_ts_res, s.orig_ts_res,
                  a.outlet_id, a.country, a.published_at AS a_pub, a.discovered_at AS a_disc, a.day_of_record AS a_dor,
                  %s AS route, c.category AS c_category, c.method AS c_method,
                  o.published_at AS o_pub, o.discovered_at AS o_disc, o.day_of_record AS o_dor
           FROM state_copies s
           JOIN articles a ON a.id = s.copy_id
           LEFT JOIN classifications c ON c.article_id = s.copy_id AND c.is_current = 1
           LEFT JOIN articles o ON o.id = s.original_article_id AND s.original_kind = 'monitored'
           WHERE s.current = 1 AND s.original_kind IN ('reference', 'monitored')""" % model_only.route_sql("c")).fetchall()
    refs = {}
    if has_ref:
        ids = [r["ref_id"] for r in rows if r["original_kind"] == "reference" and r["ref_id"] is not None]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in conn.execute("SELECT id, source_id, published_at, discovered_at FROM reference_items WHERE id IN (%s)"
                                  % ",".join("?" * len(chunk)), chunk):
                refs[r["id"]] = r
    out = []
    from pipeline import model_only
    for r in rows:
        # A copy whose state origin label is undetermined (pipeline/model_only.py) is in no count.
        if model_only.is_undetermined(r["c_category"], r["route"], r["c_method"]):
            continue
        kind = KINDS[r["original_kind"]]
        copy = {"published_at": _pick(r["copy_published_at"], r["a_pub"]), "discovered_at": r["a_disc"],
                "day_of_record": r["a_dor"], "res": r["copy_ts_res"], "raw": r["a_pub"]}
        if kind == "absolute":
            ref = refs.get(r["ref_id"])
            source = r["original_source"] or (ref["source_id"] if ref else None)
            orig = {"published_at": _pick(r["original_published_at"], ref["published_at"] if ref else None),
                    "discovered_at": ref["discovered_at"] if ref else None, "res": r["orig_ts_res"],
                    "raw": ref["published_at"] if ref else None,
                    "date_zone": BJT_MINUTES if zones.get(source) is None else zones[source]}
        else:
            orig = {"published_at": _pick(r["original_published_at"], r["o_pub"]), "discovered_at": r["o_disc"],
                    "day_of_record": r["o_dor"], "res": r["orig_ts_res"], "raw": r["o_pub"]}
            source = r["original_source"]
        lag = pair_lag(copy, orig)
        okey = ("r%s" % r["ref_id"]) if kind == "absolute" and r["ref_id"] is not None else (
            ("a%s" % r["original_article_id"]) if r["original_article_id"] is not None else None)
        out.append({"copy_id": r["copy_id"], "kind": kind, "ref_id": r["ref_id"], "orig_key": okey,
                    "original_article_id": r["original_article_id"], "source": source or "unknown",
                    "outlet_id": r["outlet_id"], "country": country_of.get(r["outlet_id"], r["country"]),
                    "route": r["route"] or "unattributed", **lag})
    return out


# ---------------------------------------------------------------------------
# Diagnostics of the reference times
# ---------------------------------------------------------------------------

def _reference_reports(conn) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
    """reference.source_status by source, and reference.time_checks; empty when unavailable."""
    try:
        from pipeline import reference
        return {d["source"]: d for d in reference.source_status(conn)}, reference.time_checks(conn)
    except Exception:  # noqa: BLE001  the diagnostics never stop the export
        return {}, {}


def diagnostics(conn) -> List[Dict]:
    """Per reference source: items with a time, the median hours of discovered_at minus published_at,
    the share negative (published after discovery: a zone labelled wrongly, or a clock running
    ahead), the median of those negative gaps, the share that are a date only, the share still date
    only after day one (reference.source_status), the cross listing time check (reference.time_checks:
    an item's time on a second listing against the first, which catches a zone reading early), and
    flags."""
    if not _table_exists(conn, "reference_items"):
        return []
    status_by, checks = _reference_reports(conn)
    by = defaultdict(lambda: {"items": 0, "no_time": 0, "date_only": 0, "gaps": []})
    for r in conn.execute("SELECT source_id, published_at, discovered_at FROM reference_items"):
        d = by[r["source_id"]]
        d["items"] += 1
        p = (r["published_at"] or "").strip()
        pt, dt_ = lookalike._ts(p), lookalike._ts(r["discovered_at"])
        if not p or pt is None or dt_ is None:
            d["no_time"] += 1
            continue
        if _date_only(p):
            d["date_only"] += 1
            continue
        d["gaps"].append(dt_ - pt)
    out = []
    for sid in sorted(by):
        d = by[sid]
        timed = len(d["gaps"])
        neg = [g for g in d["gaps"] if g < 0]
        dated = timed + d["date_only"]
        st = status_by.get(sid) or {}
        ck = checks.get(sid)
        row = {"source": sid, "items": d["items"], "timed": timed, "date_only": d["date_only"], "no_time": d["no_time"],
               "median_discovery_gap_hours": _hours(quantile(d["gaps"], 0.5)),
               "share_negative": round(len(neg) / timed, 3) if timed else None,
               "median_negative_hours": _hours(quantile(neg, 0.5)),
               "share_date_only": round(d["date_only"] / dated, 3) if dated else None,
               "share_date_only_after_day_one": st.get("share_date_only_after_day_one"),
               "time_check": ck,
               "flags": []}
        if timed and len(neg) >= DIAG_NEGATIVE_MIN and len(neg) / timed >= DIAG_NEGATIVE_FLAG:
            row["flags"].append("published after discovery")
        if dated >= DIAG_DATE_ONLY_MIN and d["date_only"] / dated >= DIAG_DATE_ONLY_FLAG:
            row["flags"].append("mostly date only")
        row["flags"] += [f for f in st.get("flags") or [] if f not in row["flags"]]
        row["flags"] += [f for f in (ck or {}).get("flags") or [] if f not in row["flags"]]
        out.append(row)
    return out


def reference_sources(conn) -> List[Dict]:
    """The status of every reference source (reference.source_status): items by status (fetched,
    expired, failed, paywalled, discovered, blocked by robots.txt), bodies kept, the date only shares
    and flags. [] when unavailable."""
    status_by, _ = _reference_reports(conn)
    return [status_by[k] for k in sorted(status_by)]


# ---------------------------------------------------------------------------
# The module interface
# ---------------------------------------------------------------------------

def compute(conn, ctx, deadline) -> Dict:
    """Speed writes nothing: it is computed from state_copies at export time. Returns counts."""
    pairs = load_pairs(conn, ctx)
    out = {"pairs": len(pairs)}
    for kind in ("absolute", "relative"):
        sub = [p for p in pairs if p["kind"] == kind]
        out[kind] = len(sub)
        out[kind + "_in_distribution"] = sum(1 for p in sub if p["bucket"] not in OUTSIDE_BUCKETS)
    return out


def _groups(pairs: List[Dict], key: str, label=None, min_median: int = MIN_GROUP_MEDIAN,
            min_pairs: int = 0) -> List[Dict]:
    by = defaultdict(list)
    for p in pairs:
        by[p[key]].append(p)
    out = []
    for k, items in by.items():
        s = summarise(items, min_median)
        if s["originals_timed"] < min_pairs:
            continue
        row = {"key": k}
        if label:
            row["label"] = label(k)
        row.update(s)
        out.append(row)
    out.sort(key=lambda r: (-r["n"], str(r["key"])))
    return out


def _block(pairs: List[Dict], names: Dict[str, str], country_of: Dict[str, str]) -> Dict:
    from pipeline.export import ROUTE_LABELS
    outlets = _groups(pairs, "outlet_id", min_median=MIN_OUTLET_PAIRS, min_pairs=MIN_OUTLET_PAIRS)
    for r in outlets:
        r["name"] = names.get(r["key"], r["key"])
        r["country"] = country_of.get(r["key"])
    outlets.sort(key=lambda r: (r["median_hours"] if r["median_hours"] is not None else float("inf"), -r["n"], r["key"]))
    return {"overall": summarise(pairs), "by_route": _groups(pairs, "route", lambda k: ROUTE_LABELS.get(k, k)),
            "by_country": _groups(pairs, "country"), "by_source": _groups(pairs, "source"),
            "by_outlet": outlets}


def coverage(conn) -> Dict:
    """Copies with a current row, by kind of original, and the reasons none was found."""
    out = {"copies": 0, "reference": 0, "monitored": 0, "none": 0, "none_reasons": {}}
    if not _table_exists(conn, "state_copies"):
        return out
    for r in conn.execute("SELECT original_kind k, COUNT(*) n FROM state_copies WHERE current=1 GROUP BY 1"):
        out["copies"] += r["n"]
        if r["k"] in out:
            out[r["k"]] = r["n"]
    out["none_reasons"] = {r["why"] or "unrecorded": r["n"] for r in conn.execute(
        "SELECT none_reason why, COUNT(*) n FROM state_copies WHERE current=1 AND original_kind='none' GROUP BY 1 ORDER BY 2 DESC")}
    return out


def build_export(conn, ctx) -> Dict:
    pairs = load_pairs(conn, ctx)
    country_of, names = _outlet_maps(ctx)
    return {"measure": MEASURE_ID, "version": VERSION,
            "buckets": {"timed": list(TIMED_BUCKETS), "date": list(DATE_BUCKETS), "outside": list(OUTSIDE_BUCKETS),
                        "labels": BUCKET_LABELS},
            "min_outlet_pairs": MIN_OUTLET_PAIRS, "min_group_median": MIN_GROUP_MEDIAN,
            "absolute": _block([p for p in pairs if p["kind"] == "absolute"], names, country_of),
            "relative": _block([p for p in pairs if p["kind"] == "relative"], names, country_of),
            "coverage": coverage(conn), "diagnostics": diagnostics(conn), "reference_sources": reference_sources(conn)}


def meta_summary(conn, ctx) -> Dict:
    pairs = load_pairs(conn, ctx)
    out = {}
    for kind in ("absolute", "relative"):
        s = summarise([p for p in pairs if p["kind"] == kind])
        out[kind] = {"pairs": s["n"] + s["outside"], "n": s["n"], "originals": s["originals"],
                     "originals_timed": s["originals_timed"], "median_hours": s["median_hours"]}
    diag = diagnostics(conn)
    out["flagged_sources"] = [d["source"] for d in diag if d["flags"]]
    out["source_flags"] = {d["source"]: d["flags"] for d in diag if d["flags"]}
    return out


def methodology_text(block) -> str:
    summ = (block or {}).get("summary") or {}
    a = (summ.get("absolute") or {}).get("n")
    ao = (summ.get("absolute") or {}).get("originals")
    lead = ("Republication speed is the time between a state original and a state origin copy of it in a "
            "monitored outlet. It is measured only for copies paired with their original (see the alteration "
            "score), and only when both publication times are credible: readable, not after the item was "
            "discovered, and not before collection began. The time the tracker discovered an item is never "
            "used as a lag. Where the original is the state outlet's own page, collected as a reference "
            "source, the lag is absolute. Where no such page was found and the original is the first "
            "monitored copy, the lag is relative, behind the first monitored copy, and the two are never "
            "pooled. Lags fall into four buckets: the same hour, the same day (1 to 24 hours), 1 to 3 days, "
            "and later. A copy up to an hour ahead of its original counts as the same hour, since clocks "
            "differ by minutes. When either page gives only a date, the pair is bucketed by calendar date "
            "instead (same date, 1 to 3 days, later) and kept out of the medians. The days are counted in one "
            "zone, the zone of the date: the state outlets' dates are Beijing dates (the Global Times sitemap's "
            "are UTC dates), and the other page's time is converted into that zone before the days are "
            "counted. Copies more than an hour "
            "ahead of their original, and pairs without a credible time, are counted but kept outside every "
            "distribution. One state text run by many outlets of one group is many pairs but one original, "
            "so every figure gives the distinct originals beside the pairs, and a median needs distinct "
            "originals, not pairs: an outlet gets a median from %d timed pairs with %d distinct originals; a "
            "route, country or source from %d. An earlier copy at a sister outlet of the same media group "
            "is never the first monitored copy. "
            "Wires are counted under WLD." % (MIN_OUTLET_PAIRS, MIN_OUTLET_PAIRS, MIN_GROUP_MEDIAN))
    if a is not None:
        lead += " At the last export, %d absolute pairs fell inside the distributions" % a
        lead += (", with %d distinct originals." % ao) if ao is not None else "."
    caveats = ("The times are what the pages say, and they are not always what they seem. Many sites give "
               "the time a page was last updated as its publication time: on the copy that makes it look "
               "slower than it was, and on the original it can put a copy ahead of its source. Some state listings label their time zones wrongly: a Beijing time written as "
               "UTC reads eight hours late. Times are corrected per source when they are collected, and the "
               "export checks every reference source for this by comparing publication with discovery: the "
               "median gap, the share published after it was discovered, and the share that give only a "
               "date, overall and among items more than a day old, by when the page's own time should have "
               "been read. Discovery can only show a time that reads late. A time that reads early is caught "
               "where a source lists an item twice: CGTN's sitemap writes Beijing time marked as UTC, so its "
               "times are read as Beijing time, and every item it shares with CGTN's RSS feed is compared; "
               "if CGTN began writing true UTC, the sitemap would read eight hours earlier than the feed and "
               "the source would be flagged. Each source's counts of pages fetched, expired, failed and "
               "paywalled are given beside it. Pages that give only a date can say no more than the calendar day. State text also "
               "reaches outlets by channels the tracker cannot see, such as wire terminals, email and "
               "agreements, so a lag is the time between two public pages, not the time it took the text to "
               "travel. Coverage is partial: only copies paired with an original count, the reference "
               "sources reach back only a few days, and most copies have no original found. The hand check "
               "compares the bucket of %d reference pairs, read by a person from the times shown on both "
               "pages, with the measure's bucket, and passes at %d percent agreement."
               % (SAMPLE_N, int(CHECK_THRESHOLD * 100)))
    return lead + "\n\n" + caveats


# ---------------------------------------------------------------------------
# Hand check
# ---------------------------------------------------------------------------

SAMPLE_FIELDS = ["pair", "copy_outlet", "copy_title", "copy_url", "original_source", "original_title", "original_url",
                 "copy_time_shown", "copy_zone_shown", "original_time_shown", "original_zone_shown", "note"]
CODED_FIELDS = ("copy_time_shown", "original_time_shown")
NONE_WORDS = ("none", "no time", "not shown", "n/a", "na", "-")


def draw_sample(conn, n=SAMPLE_N, seed=None, out_dir=None):
    """n reference pairs at random (seeded), blind: the coder sees both links and enters the time and
    zone each page shows. The key holds the measure's bucket for each pair."""
    pairs = [p for p in load_pairs(conn) if p["kind"] == "absolute"]
    rng = random.Random(str(seed))
    chosen = rng.sample(pairs, min(int(n), len(pairs)))
    chosen.sort(key=lambda p: p["copy_id"])
    rows, answers = [], {}
    for i, p in enumerate(chosen, 1):
        a = conn.execute("SELECT title, url FROM articles WHERE id=?", (p["copy_id"],)).fetchone()
        ref = conn.execute("SELECT title, url FROM reference_items WHERE id=?", (p["ref_id"],)).fetchone() \
            if p["ref_id"] is not None and _table_exists(conn, "reference_items") else None
        pid = "S%03d" % i
        rows.append({"pair": pid, "copy_outlet": p["outlet_id"], "copy_title": a["title"] if a else "",
                     "copy_url": a["url"] if a else "", "original_source": p["source"],
                     "original_title": ref["title"] if ref else "", "original_url": ref["url"] if ref else "",
                     "copy_time_shown": "", "copy_zone_shown": "", "original_time_shown": "",
                     "original_zone_shown": "", "note": ""})
        answers[pid] = {"copy_id": p["copy_id"], "ref_id": p["ref_id"], "bucket": p["bucket"],
                        "lag_seconds": p["lag_seconds"], "days": p["days"]}
    key = {"pairs": answers, "statistic": "bucket agreement", "threshold": CHECK_THRESHOLD}
    return measures.write_sample(Path(out_dir or measures.REVIEWS_DIR), MEASURE_ID, rows, SAMPLE_FIELDS, key,
                                 version=VERSION, seed=seed)


ZONE_NAMES = {"UTC": 0, "GMT": 0, "Z": 0, "WET": 0, "BST": 60, "CET": 60, "CEST": 120, "EET": 120, "EEST": 180,
              "MSK": 180, "IST": 330, "WIB": 420, "ICT": 420, "BJT": 480, "CST": 480, "HKT": 480, "SGT": 480,
              "PHT": 480, "MYT": 480, "AWST": 480, "JST": 540, "KST": 540, "AEST": 600, "AEDT": 660,
              "NZST": 720, "NZDT": 780, "EST": -300, "EDT": -240, "CDT": -300, "MST": -420, "PST": -480,
              "PDT": -420, "ART": -180, "BRT": -180}
_OFFSET = re.compile(r"^(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)
_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")


def parse_zone(text: Optional[str]) -> Optional[int]:
    """Minutes east of UTC for a zone as a coder writes it (+08:00, UTC+8, GMT-5, BJT, CET), else None."""
    t = (text or "").strip()
    if not t:
        return None
    if t.upper() in ZONE_NAMES:
        return ZONE_NAMES[t.upper()]
    m = _OFFSET.match(t)
    if not m:
        return None
    mins = int(m.group(2)) * 60 + int(m.group(3) or 0)
    return -mins if m.group(1) == "-" else mins


def coded_time(time_text: Optional[str], zone_text: Optional[str]) -> Optional[Tuple[str, Optional[float], dt.date]]:
    """What the coder read off a page: ('time', timestamp, UTC date), ('date', None, date), ('none', None,
    None), or None when the cell is blank or cannot be read. A time without a readable zone counts as
    its date only."""
    t = (time_text or "").strip()
    if not t:
        return None
    if t.lower() in NONE_WORDS:
        return ("none", None, None)
    m = _TIME.match(t)
    if not m:
        return None
    try:
        day = dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None
    zone = parse_zone(zone_text)
    if m.group(2) is None or zone is None:
        return ("date", None, day)
    try:
        local = dt.datetime(day.year, day.month, day.day, int(m.group(2)), int(m.group(3)), int(m.group(4) or 0))
    except ValueError:
        return None
    utc = local.replace(tzinfo=dt.timezone(dt.timedelta(minutes=zone)))
    return ("time", utc.timestamp(), utc.astimezone(dt.timezone.utc).date())


def coded_bucket(row: Dict) -> Optional[str]:
    """The bucket the coder's times give, or None when the row is not coded or cannot be read."""
    c = coded_time(row.get("copy_time_shown"), row.get("copy_zone_shown"))
    o = coded_time(row.get("original_time_shown"), row.get("original_zone_shown"))
    if c is None or o is None:
        return None
    if c[0] == "none" or o[0] == "none":
        return "no_credible_time"
    if c[0] == "time" and o[0] == "time":
        return timed_bucket(c[1] - o[1])
    # One zone, as the measure counts it: the date side's zone (as the coder wrote it; else Beijing for
    # the state outlet's page and UTC for the copy), the timed side converted into it.
    cz = parse_zone(row.get("copy_zone_shown"))
    oz = parse_zone(row.get("original_zone_shown"))
    cz = 0 if cz is None else cz
    oz = BJT_MINUTES if oz is None else oz
    if c[0] == "date" and o[0] == "time":
        return date_bucket((c[2] - _date_in(o[1], cz)).days)
    if c[0] == "time" and o[0] == "date":
        return date_bucket((_date_in(c[1], oz) - o[2]).days)
    return date_bucket((c[2] - o[2]).days)


def score_check(rows: List[Dict], key: Dict) -> Dict:
    """Bucket agreement: the share of coded pairs whose coder bucket equals the measure's. A row is
    coded when both time cells hold a time, a date or "none"; a row whose cells cannot be read is not
    coded. Passes at CHECK_THRESHOLD once every pair is coded."""
    answers = (key or {}).get("pairs") or {}
    n = len(answers)
    coded = agree = 0
    for r in rows:
        want = answers.get((r.get("pair") or "").strip())
        if want is None:
            continue
        got = coded_bucket(r)
        if got is None:
            continue
        coded += 1
        agree += got == want.get("bucket")
    value = round(agree / coded, 4) if coded else None
    return measures.check_result("bucket agreement", value, CHECK_THRESHOLD, n, coded, agree=agree)
