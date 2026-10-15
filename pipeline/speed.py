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
(either time is a date only): same date, 1 to 3 days, later, by calendar date. Outside every
distribution: copy_precedes_original (the copy is more than 1 h, or a calendar day, ahead of its
original) and no_credible_time (a time is missing, unreadable or not credible).

Diagnostics. Reference times are normalised per source by pipeline/reference.py, and some listings
label their zones wrongly (a Beijing time written as UTC reads 8 h late). For each reference source
the export gives the median of discovered_at minus published_at, the share where publication comes
after discovery (negative), and the share that are a date only, with a flag when either is high.

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
VERSION = "1"

HOUR = 3600
DAY = 86400
PRECEDES_TOLERANCE = HOUR          # a copy up to 1 h ahead of its original is clock noise, same hour
MIN_OUTLET_PAIRS = 10              # an outlet is listed, with a median, from 10 timed pairs
MIN_GROUP_MEDIAN = 5               # route, country and source medians need 5 timed pairs
CHECK_THRESHOLD = 0.85
SAMPLE_N = 60
DIAG_NEGATIVE_FLAG = 0.05          # share of items published after discovery that flags a source
DIAG_DATE_ONLY_FLAG = 0.5

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


def _utc_date(item: Dict, ts: float, res: str) -> dt.date:
    """The calendar date of a time: for a date only, the date as the page wrote it (raw, the source
    row's own string, since a stored UTC midnight of a local date can fall on the day before), else
    the UTC date."""
    if res == "day":
        for s in (item.get("raw"), item.get("published_at")):
            try:
                return dt.date.fromisoformat((s or "").strip()[:10])
            except ValueError:
                continue
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()


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
    discovered_at, day_of_record (optional), res (the stored resolution, optional) and raw (the source
    row's published_at as written, optional)."""
    cres = resolution(copy.get("res"), copy.get("published_at"))
    ores = resolution(orig.get("res"), orig.get("published_at"))
    cts = credible(copy.get("published_at"), copy.get("discovered_at"), copy.get("day_of_record"))
    ots = credible(orig.get("published_at"), orig.get("discovered_at"), orig.get("day_of_record"))
    if cres == "none" or ores == "none" or cts is None or ots is None:
        return {"bucket": "no_credible_time", "lag_seconds": None, "days": None, "resolution": None}
    if cres in TIMED_RES and ores in TIMED_RES:
        lag = int(round(cts - ots))
        return {"bucket": timed_bucket(lag), "lag_seconds": lag, "days": None, "resolution": "time"}
    days = (_utc_date(copy, cts, cres) - _utc_date(orig, ots, ores)).days
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


def summarise(items: Iterable[Dict], min_median: int = MIN_GROUP_MEDIAN) -> Dict:
    """n (pairs inside the distributions), n_timed, n_date, median/p25/p75 hours over the timed lags
    (when there are at least min_median), and the count in every bucket."""
    counts = {b: 0 for b in BUCKETS}
    lags = []
    for it in items:
        counts[it["bucket"]] += 1
        if it["bucket"] in TIMED_BUCKETS:
            lags.append(it["lag_seconds"])
    n_timed = sum(counts[b] for b in TIMED_BUCKETS)
    n_date = sum(counts[b] for b in DATE_BUCKETS)
    enough = n_timed >= min_median
    return {"n": n_timed + n_date, "n_timed": n_timed, "n_date": n_date,
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
    rows = conn.execute(
        """SELECT s.copy_id, s.original_kind, s.ref_id, s.original_article_id, s.original_source,
                  s.copy_published_at, s.original_published_at, s.copy_ts_res, s.orig_ts_res,
                  a.outlet_id, a.country, a.published_at AS a_pub, a.discovered_at AS a_disc, a.day_of_record AS a_dor,
                  c.route,
                  o.published_at AS o_pub, o.discovered_at AS o_disc, o.day_of_record AS o_dor
           FROM state_copies s
           JOIN articles a ON a.id = s.copy_id
           LEFT JOIN classifications c ON c.article_id = s.copy_id AND c.is_current = 1
           LEFT JOIN articles o ON o.id = s.original_article_id AND s.original_kind = 'monitored'
           WHERE s.current = 1 AND s.original_kind IN ('reference', 'monitored')""").fetchall()
    refs = {}
    if has_ref:
        ids = [r["ref_id"] for r in rows if r["original_kind"] == "reference" and r["ref_id"] is not None]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in conn.execute("SELECT id, source_id, published_at, discovered_at FROM reference_items WHERE id IN (%s)"
                                  % ",".join("?" * len(chunk)), chunk):
                refs[r["id"]] = r
    out = []
    for r in rows:
        kind = KINDS[r["original_kind"]]
        copy = {"published_at": _pick(r["copy_published_at"], r["a_pub"]), "discovered_at": r["a_disc"],
                "day_of_record": r["a_dor"], "res": r["copy_ts_res"], "raw": r["a_pub"]}
        if kind == "absolute":
            ref = refs.get(r["ref_id"])
            orig = {"published_at": _pick(r["original_published_at"], ref["published_at"] if ref else None),
                    "discovered_at": ref["discovered_at"] if ref else None, "res": r["orig_ts_res"],
                    "raw": ref["published_at"] if ref else None}
            source = r["original_source"] or (ref["source_id"] if ref else None)
        else:
            orig = {"published_at": _pick(r["original_published_at"], r["o_pub"]), "discovered_at": r["o_disc"],
                    "day_of_record": r["o_dor"], "res": r["orig_ts_res"], "raw": r["o_pub"]}
            source = r["original_source"]
        lag = pair_lag(copy, orig)
        out.append({"copy_id": r["copy_id"], "kind": kind, "ref_id": r["ref_id"],
                    "original_article_id": r["original_article_id"], "source": source or "unknown",
                    "outlet_id": r["outlet_id"], "country": country_of.get(r["outlet_id"], r["country"]),
                    "route": r["route"] or "unattributed", **lag})
    return out


# ---------------------------------------------------------------------------
# Diagnostics of the reference times
# ---------------------------------------------------------------------------

def diagnostics(conn) -> List[Dict]:
    """Per reference source: items with a time, the median hours of discovered_at minus published_at,
    the share negative (published after discovery: a zone labelled wrongly, or a clock running
    ahead), the median of those negative gaps, the share that are a date only, and flags."""
    if not _table_exists(conn, "reference_items"):
        return []
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
        row = {"source": sid, "items": d["items"], "timed": timed, "date_only": d["date_only"], "no_time": d["no_time"],
               "median_discovery_gap_hours": _hours(quantile(d["gaps"], 0.5)),
               "share_negative": round(len(neg) / timed, 3) if timed else None,
               "median_negative_hours": _hours(quantile(neg, 0.5)),
               "share_date_only": round(d["date_only"] / dated, 3) if dated else None,
               "flags": []}
        if timed and len(neg) / timed >= DIAG_NEGATIVE_FLAG:
            row["flags"].append("published after discovery")
        if dated and d["date_only"] / dated >= DIAG_DATE_ONLY_FLAG:
            row["flags"].append("mostly date only")
        out.append(row)
    return out


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
        if s["n_timed"] < min_pairs:
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
            "coverage": coverage(conn), "diagnostics": diagnostics(conn)}


def meta_summary(conn, ctx) -> Dict:
    pairs = load_pairs(conn, ctx)
    out = {}
    for kind in ("absolute", "relative"):
        s = summarise([p for p in pairs if p["kind"] == kind])
        out[kind] = {"pairs": s["n"] + s["outside"], "n": s["n"], "median_hours": s["median_hours"]}
    out["flagged_sources"] = [d["source"] for d in diagnostics(conn) if d["flags"]]
    return out


def methodology_text(block) -> str:
    summ = (block or {}).get("summary") or {}
    a = (summ.get("absolute") or {}).get("n")
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
            "instead (same date, 1 to 3 days, later) and kept out of the medians. Copies more than an hour "
            "ahead of their original, and pairs without a credible time, are counted but kept outside every "
            "distribution. An outlet gets a median from %d timed pairs; a route, country or source from %d. "
            "Wires are counted under WLD." % (MIN_OUTLET_PAIRS, MIN_GROUP_MEDIAN))
    if a is not None:
        lead += " At the last export, %d absolute pairs fell inside the distributions." % a
    caveats = ("The times are what the pages say, and they are not always what they seem. Many sites give "
               "the time a page was last updated as its publication time: on the copy that makes it look "
               "slower than it was, and on the original it can put a copy ahead of its source. Some state listings label their time zones wrongly: a Beijing time written as "
               "UTC reads eight hours late. Times are corrected per source when they are collected, and the "
               "export checks every reference source for this by comparing publication with discovery: the "
               "median gap, the share published after it was discovered, and the share that give only a "
               "date. Pages that give only a date can say no more than the calendar day. State text also "
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
