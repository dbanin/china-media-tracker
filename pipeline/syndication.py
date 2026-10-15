"""Syndication deals: publicly announced agreements between outlets and Chinese state media, on a
timeline with their sources, and a before and after comparison where collection allows one.

Report only. Nothing here changes a label, a count, a denominator or the outlets monitored, and
China stays unmonitored. The measure is switched off in sources/measures.yaml (min_inputs 10) until
enough sourced, dated entries exist.

The file is sources/syndication.yaml, shaped by sources/syndication_schema.json: a top level
`entries:` list. Each entry names a monitored outlet (outlet_id, an id in sources/outlets.yaml) or an
outlet the tracker does not monitor (unmonitored: true with outlet_name), the partner, the kind of
deal, signed_on with its precision, a public https source and confidence. Validation also checks
that precision matches the form of signed_on, that no two entries share (outlet, partner, kind,
signed_on) or an id, and that ended_on does not come before signed_on.

The comparison. Collection began on config.COLLECTION_START (3 September 2026) and most deals predate
it: those carry predates_collection and appear on the timeline only. A deal signed on a known day or
month whose span lies within [COLLECTION_START + 28 days, last complete day - 28 days] is compared:
the outlet's state origin share of its China coverage, A / (A + B + C) on current labels by day of
record, over the 28 days before the span and the 28 days after it. A side with fewer than 50 China
articles has no share. The difference (after minus before) carries a 95 percent Newcombe interval
built from two Wilson intervals (Newcombe 1998, method 10). Confounders are listed with each row:
ruleset or gate changes inside the window, the share of days the relay collected incompletely (for
an outlet only the owner's machine collects), days on which the outlet discovered nothing, and the
share of saturated feed polls. It shows timing, not cause.

Command line:
  python -m pipeline.syndication validate [path]   check a file (default sources/syndication.yaml)
"""
import argparse
import datetime as dt
import json
import math
import random
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import jsonschema
import yaml

from pipeline import config, measures, registry

MEASURE_ID = "syndication"
VERSION = "1"

SYNDICATION_PATH = config.ROOT / "sources" / "syndication.yaml"
SCHEMA_PATH = config.ROOT / "sources" / "syndication_schema.json"
CHANGELOG_PATH: Optional[Path] = None      # None: CHANGELOG.md, as the export reads it

WINDOW_DAYS = 28
MIN_CHINA_PER_SIDE = 50
Z95 = 1.959963984540054
SATURATION_LIMIT = 0.1      # a side is confounded when at least this share of the outlet's polls saturated
RELAY_LIMIT = 0.1           # or the relay collected incompletely on at least this share of its days
ZERO_DAY_LIMIT = 3          # or the outlet discovered nothing on at least this many of its days
SAMPLE_N = 20
CHECK_THRESHOLD = 0.9
CHECK_STATISTIC = "share of entries the source confirms"

PARTNER_LABELS = {"xinhua": "Xinhua", "cgtn": "CGTN", "china_daily": "China Daily", "global_times": "Global Times",
                  "peoples_daily": "People's Daily", "cri": "China Radio International", "cns": "China News Service",
                  "cmg": "China Media Group", "other": "Other Chinese state media"}
KIND_LABELS = {"content_exchange": "content exchange", "news_exchange": "news exchange", "mou": "memorandum",
               "cooperation_agreement": "cooperation agreement", "supplement": "supplement",
               "paid_placement": "paid placement", "joint_venture": "joint venture",
               "training_partnership": "training partnership", "other": "other agreement"}
PRECISION_OF_LEN = {4: "year", 7: "month", 10: "day"}
SIDE_KEYS = ("before", "after")


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------

def _norm_dates(e: Dict) -> Dict:
    """YAML reads an unquoted 2025-12-17 as a date and 2025 as a number: both become strings."""
    out = dict(e)
    for k in ("signed_on", "effective_from", "ended_on", "accessed_on"):
        v = out.get(k)
        if isinstance(v, (dt.date, dt.datetime)):
            out[k] = v.isoformat()[:10]
        elif isinstance(v, int) and not isinstance(v, bool):
            out[k] = str(v)
    return out


def load_file(path: Optional[Path] = None) -> Dict:
    """The parsed file, {"entries": [...]}, with dates as strings. A missing file has no entries."""
    p = Path(path or SYNDICATION_PATH)
    if not p.exists():
        return {"entries": []}
    with open(p, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if raw is None:
        return {"entries": []}
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        raw = dict(raw)
        raw["entries"] = [_norm_dates(e) if isinstance(e, dict) else e for e in raw["entries"]]
    return raw


def _schema() -> Dict:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _valid_date(s: str) -> bool:
    """A real calendar date, month or year in one of the three forms."""
    try:
        if len(s) == 10:
            dt.date.fromisoformat(s)
        elif len(s) == 7:
            dt.date(int(s[:4]), int(s[5:7]), 1)
        elif len(s) == 4:
            int(s)
        else:
            return False
    except ValueError:
        return False
    return True


def span(date_str: str) -> Tuple[dt.date, dt.date]:
    """The first and last day a date of any precision can mean."""
    if len(date_str) == 10:
        d = dt.date.fromisoformat(date_str)
        return d, d
    if len(date_str) == 7:
        y, m = int(date_str[:4]), int(date_str[5:7])
        first = dt.date(y, m, 1)
        nxt = dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        return first, nxt - dt.timedelta(days=1)
    y = int(date_str[:4])
    return dt.date(y, 1, 1), dt.date(y, 12, 31)


def outlet_key(e: Dict) -> str:
    """The outlet half of the duplicate key: its id, or the name of an unmonitored outlet."""
    if e.get("unmonitored"):
        return "unmonitored:" + " ".join(str(e.get("outlet_name") or "").lower().split())
    return str(e.get("outlet_id") or "")


def validate(data, outlet_ids) -> Tuple[List[Dict], List[str]]:
    """(entries that pass, errors). Each error names the entry's id (or its position) and the rule it
    breaks. An entry with any error is left out of the entries that pass; a duplicate drops only the
    later entry. A file that is not {"entries": [...]} gives no entries and one error."""
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return [], ["the file needs a top level entries: list"]
    entries = data["entries"]
    errs_by = {i: [] for i in range(len(entries))}
    general = []

    def name(i):
        e = entries[i]
        return (e.get("id") if isinstance(e, dict) and e.get("id") else "entry %d" % (i + 1))

    for err in jsonschema.Draft202012Validator(_schema()).iter_errors(data):
        path = list(err.absolute_path)
        if len(path) >= 2 and path[0] == "entries" and isinstance(path[1], int):
            field = ".".join(str(x) for x in path[2:])
            errs_by[path[1]].append(("%s: %s" % (field, err.message)) if field else err.message)
        else:
            general.append(err.message)
    outlet_ids = set(outlet_ids or ())
    seen_ids, seen_keys = {}, {}
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        errs = errs_by[i]
        if e.get("unmonitored"):
            if e.get("outlet_id"):
                errs.append("an unmonitored entry names outlet_name, not outlet_id")
            if not e.get("outlet_name"):
                errs.append("an unmonitored entry needs outlet_name")
        elif not e.get("outlet_id"):
            errs.append("needs outlet_id, or unmonitored: true with outlet_name")
        elif e["outlet_id"] not in outlet_ids:
            errs.append("outlet_id %s is not in sources/outlets.yaml (mark it unmonitored: true with outlet_name)"
                        % e["outlet_id"])
        s, prec = e.get("signed_on"), e.get("precision")
        if isinstance(s, str) and len(s) in PRECISION_OF_LEN:
            if not _valid_date(s):
                errs.append("signed_on %s is not a real date" % s)
            elif prec in ("day", "month", "year") and PRECISION_OF_LEN[len(s)] != prec:
                errs.append("precision %s does not match signed_on %s, which is %s precision"
                            % (prec, s, PRECISION_OF_LEN[len(s)]))
        for k in ("effective_from", "ended_on", "accessed_on"):
            v = e.get(k)
            if isinstance(v, str) and len(v) in PRECISION_OF_LEN and not _valid_date(v):
                errs.append("%s %s is not a real date" % (k, v))
        if not str(e.get("source_url") or "").strip():
            errs.append("a source is required: source_url")
        ok_dates = not errs and isinstance(s, str) and _valid_date(s)
        if ok_dates and isinstance(e.get("ended_on"), str) and _valid_date(e["ended_on"]):
            if span(e["ended_on"])[1] < span(s)[0]:
                errs.append("ended_on %s comes before signed_on %s" % (e["ended_on"], s))
        if ok_dates and isinstance(e.get("accessed_on"), str) and _valid_date(e["accessed_on"]):
            if span(s)[0] > dt.date.fromisoformat(e["accessed_on"]):
                errs.append("signed_on %s comes after accessed_on %s" % (s, e["accessed_on"]))
        eid = e.get("id")
        if eid:
            if eid in seen_ids:
                errs.append("duplicate id, also entry %d" % (seen_ids[eid] + 1))
            else:
                seen_ids[eid] = i
        key = (outlet_key(e), e.get("partner"), e.get("kind"), s)
        if all(key):
            if key in seen_keys:
                errs.append("duplicate of %s: same outlet, partner, kind and signed_on" % name(seen_keys[key]))
            else:
                seen_keys[key] = i
    errors = list(general)
    passed = []
    for i, e in enumerate(entries):
        if errs_by[i]:
            errors.extend("%s: %s" % (name(i), m) for m in errs_by[i])
        elif isinstance(e, dict):
            passed.append(e)
    return passed, errors


def _outlets(outlets=None) -> List[Dict]:
    return outlets if outlets is not None else registry.load_outlets()


def validate_file(path: Optional[Path] = None, outlets=None) -> Tuple[List[Dict], List[str]]:
    try:
        data = load_file(path)
    except yaml.YAMLError as exc:
        return [], ["the file is not valid YAML: %s" % exc]
    return validate(data, {o["id"] for o in _outlets(outlets)})


def monitored(entries: List[Dict]) -> List[Dict]:
    return [e for e in entries if not e.get("unmonitored") and e.get("outlet_id")]


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

def _d(s) -> dt.date:
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s))


def last_complete_day(ctx: Optional[Dict] = None) -> dt.date:
    """The day before the export's own UTC day, which is still partial. ctx["today"] overrides today."""
    today = (ctx or {}).get("today") or dt.datetime.now(dt.timezone.utc).date().isoformat()
    return _d(today) - dt.timedelta(days=1)


def eligible_range(last_complete, collection_start=None) -> Tuple[dt.date, dt.date]:
    """The days a deal's span must lie within to be compared."""
    cs = _d(collection_start or config.COLLECTION_START)
    return cs + dt.timedelta(days=WINDOW_DAYS), _d(last_complete) - dt.timedelta(days=WINDOW_DAYS)


def predates_collection(e: Dict, collection_start=None) -> bool:
    """The deal was signed, at its precision, wholly before collection began."""
    return span(e["signed_on"])[1] < _d(collection_start or config.COLLECTION_START)


def window(e: Dict, last_complete, collection_start=None) -> Dict:
    """{eligible, reason, before: [first, last], after: [first, last]}. reason is one of eligible,
    unmonitored, predates_collection, imprecise (year precision), too_early, too_recent. The sides are
    the 28 days before the first day the date can mean and the 28 days after the last."""
    first, last = span(e["signed_on"])
    out = {"eligible": False, "reason": "eligible",
           "before": [(first - dt.timedelta(days=WINDOW_DAYS)).isoformat(), (first - dt.timedelta(days=1)).isoformat()],
           "after": [(last + dt.timedelta(days=1)).isoformat(), (last + dt.timedelta(days=WINDOW_DAYS)).isoformat()]}
    lo, hi = eligible_range(last_complete, collection_start)
    if e.get("unmonitored") or not e.get("outlet_id"):
        out["reason"] = "unmonitored"
    elif predates_collection(e, collection_start):
        out["reason"] = "predates_collection"
    elif e.get("precision") not in ("day", "month"):
        out["reason"] = "imprecise"
    elif first < lo:
        out["reason"] = "too_early"
    elif last > hi:
        out["reason"] = "too_recent"
    else:
        out["eligible"] = True
    return out


# ---------------------------------------------------------------------------
# Intervals
# ---------------------------------------------------------------------------

def wilson(x: int, n: int, z: float = Z95) -> Optional[Tuple[float, float]]:
    """The Wilson score interval for x of n, or None when n is 0."""
    if not n:
        return None
    p = x / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe(x1: int, n1: int, x2: int, n2: int, z: float = Z95) -> Optional[Tuple[float, float, float]]:
    """(difference, lower, upper) for p1 minus p2, by Newcombe's hybrid score method from two Wilson
    intervals (Newcombe 1998, method 10). None when either side is empty."""
    w1, w2 = wilson(x1, n1, z), wilson(x2, n2, z)
    if w1 is None or w2 is None:
        return None
    p1, p2 = x1 / n1, x2 / n2
    d = p1 - p2
    lower = d - math.sqrt((p1 - w1[0]) ** 2 + (w2[1] - p2) ** 2)
    upper = d + math.sqrt((w1[1] - p1) ** 2 + (p2 - w2[0]) ** 2)
    return d, lower, upper


# ---------------------------------------------------------------------------
# Counts and confounders
# ---------------------------------------------------------------------------

def side_counts(conn: sqlite3.Connection, outlet_id: str, first: str, last: str) -> Dict:
    """State origin and China coverage (state origin, unchecked state sourcing, independent journalism)
    of one outlet on its current labels, by day of record, first to last inclusive."""
    a = china = 0
    for r in conn.execute(
        """SELECT c.category cat, COUNT(*) n FROM articles a
           JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.outlet_id=? AND a.gate_relevant=1 AND c.category IN ('A','B','C')
             AND COALESCE(NULLIF(a.day_of_record, ''), substr(a.discovered_at, 1, 10)) BETWEEN ? AND ?
           GROUP BY c.category""", (outlet_id, first, last)):
        china += r[1]
        if r[0] == "A":
            a += r[1]
    return {"start": first, "end": last, "state_origin": a, "china": china}


def _days(first: str, last: str) -> List[str]:
    d, end, out = _d(first), _d(last), []
    while d <= end:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _changes() -> Dict[str, List[Dict]]:
    from pipeline import export
    return {"ruleset": export.ruleset_changes(CHANGELOG_PATH), "gate": export.gate_changes(CHANGELOG_PATH)}


def _relay_incomplete(conn, last_complete: dt.date) -> set:
    from pipeline import export
    return set(export.relay_incomplete_days(conn, (last_complete + dt.timedelta(days=1)).isoformat()))


def side_checks(conn, outlet: Optional[Dict], first: str, last: str, relay_incomplete: set) -> Dict:
    """For one side: the share of its days the relay collected incompletely (only for an outlet that
    only the owner's machine collects; None otherwise), the days the outlet discovered nothing, and
    the share of its feed polls that were saturated (None without polls)."""
    days = _days(first, last)
    oid = (outlet or {}).get("id")
    relay_only = bool(outlet) and registry.collector_of(outlet) == registry.SELF_HOSTED
    relay_share = round(sum(1 for d in days if d in relay_incomplete) / len(days), 4) if relay_only else None
    found = {r[0] for r in conn.execute(
        "SELECT date FROM daily_outlet_discovery WHERE outlet_id=? AND discovered>0 AND date BETWEEN ? AND ?",
        (oid, first, last))}
    polls, sat = conn.execute(
        "SELECT COALESCE(SUM(polls),0), COALESCE(SUM(saturated),0) FROM feed_polls WHERE outlet_id=? AND date BETWEEN ? AND ?",
        (oid, first, last)).fetchone()
    return {"relay_incomplete_share": relay_share, "zero_discovery_days": sum(1 for d in days if d not in found),
            "saturation_share": round(sat / polls, 4) if polls else None, "polls": polls}


def confounders(checks: Dict[str, Dict], first: str, last: str, changes: Dict[str, List[Dict]]) -> List[Dict]:
    """What else changed in the window, first to last: each {type, text, ...}."""
    out = []
    for kind, word in (("ruleset", "signature ruleset"), ("gate", "relevance gate")):
        for c in changes.get(kind) or []:
            if first <= c["date"] <= last:
                out.append({"type": "%s_change" % kind, "date": c["date"], "version": c["version"],
                            "text": "the %s changed to %s on %s, inside the window" % (word, c["version"], c["date"])})
    for side in SIDE_KEYS:
        ch = checks[side]
        if ch["relay_incomplete_share"] is not None and ch["relay_incomplete_share"] >= RELAY_LIMIT:
            out.append({"type": "relay_incomplete", "side": side, "value": ch["relay_incomplete_share"],
                        "text": "the owner's machine collected this outlet incompletely on %d percent of the days %s"
                                % (round(ch["relay_incomplete_share"] * 100), side)})
        if ch["zero_discovery_days"] >= ZERO_DAY_LIMIT:
            out.append({"type": "zero_discovery", "side": side, "value": ch["zero_discovery_days"],
                        "text": "the outlet discovered nothing on %d of the %d days %s"
                                % (ch["zero_discovery_days"], WINDOW_DAYS, side)})
        if ch["saturation_share"] is not None and ch["saturation_share"] >= SATURATION_LIMIT:
            out.append({"type": "saturation", "side": side, "value": ch["saturation_share"],
                        "text": "%d percent of the outlet's feed polls %s were saturated, so items may have been missed"
                                % (round(ch["saturation_share"] * 100), side)})
    return out


def _r(v, k=4):
    return None if v is None else round(v, k)


def compare(conn, e: Dict, w: Dict, outlet: Optional[Dict], relay_incomplete: set, changes: Dict) -> Dict:
    """One deal's before and after row."""
    sides, checks = {}, {}
    for side in SIDE_KEYS:
        c = side_counts(conn, e["outlet_id"], w[side][0], w[side][1])
        c["sufficient"] = c["china"] >= MIN_CHINA_PER_SIDE
        ci = wilson(c["state_origin"], c["china"]) if c["sufficient"] else None
        c["share"] = _r(c["state_origin"] / c["china"]) if c["sufficient"] else None
        c["ci"] = [_r(ci[0]), _r(ci[1])] if ci else None
        sides[side] = c
        checks[side] = side_checks(conn, outlet, w[side][0], w[side][1], relay_incomplete)
    b, a = sides["before"], sides["after"]
    sufficient = b["sufficient"] and a["sufficient"]
    diff = newcombe(a["state_origin"], a["china"], b["state_origin"], b["china"]) if sufficient else None
    reason = ""
    if not sufficient:
        short = [s for s in SIDE_KEYS if not sides[s]["sufficient"]]
        reason = "fewer than %d China articles %s" % (MIN_CHINA_PER_SIDE, " and ".join(short))
    conf = confounders(checks, w["before"][0], w["after"][1], changes)
    return {"entry_id": e["id"], "outlet_id": e["outlet_id"],
            "outlet_name": (outlet or {}).get("name") or e["outlet_id"],
            "country": registry.export_country(outlet) if outlet else None,
            "partner": e["partner"], "kind": e["kind"], "signed_on": e["signed_on"], "precision": e["precision"],
            "before": b, "after": a, "sufficient": sufficient, "reason": reason,
            "difference": _r(diff[0]) if diff else None, "difference_ci": [_r(diff[1]), _r(diff[2])] if diff else None,
            "checks": checks, "confounders": conf, "confounded": bool(conf)}


# ---------------------------------------------------------------------------
# The measure interface (pipeline/measures.py)
# ---------------------------------------------------------------------------

def _ctx_outlets(ctx: Optional[Dict]) -> List[Dict]:
    o = (ctx or {}).get("outlets")
    return o if o is not None else registry.load_outlets()


def inputs_present(conn) -> int:
    """Sourced, dated entries that pass validation and name a monitored outlet. Reads only the file."""
    entries, _ = validate_file()
    return len(monitored(entries))


def compute(conn, ctx, deadline) -> Dict:
    """Nothing is stored: the comparison is read from the labels at export time. Returns counts."""
    entries, errors = validate_file(outlets=_ctx_outlets(ctx))
    return {"entries": len(entries), "invalid": len(errors)}


def _entry_row(e: Dict, w: Dict, outlet: Optional[Dict]) -> Dict:
    monitored_ = not e.get("unmonitored") and bool(e.get("outlet_id"))
    source = {"url": e["source_url"], "title": e.get("source_title"), "publisher": e.get("source_publisher"),
              "kind": e.get("source_kind"), "accessed_on": e.get("accessed_on")}
    return {"id": e["id"], "outlet_id": e.get("outlet_id") if monitored_ else None,
            "outlet_name": (outlet or {}).get("name") or e.get("outlet_name") or e.get("outlet_id"),
            "country": registry.export_country(outlet) if outlet else None, "monitored": monitored_,
            "partner": e["partner"], "partner_label": PARTNER_LABELS.get(e["partner"], e["partner"]),
            "kind": e["kind"], "kind_label": KIND_LABELS.get(e["kind"], e["kind"]),
            "signed_on": e["signed_on"], "precision": e["precision"],
            "effective_from": e.get("effective_from"), "ended_on": e.get("ended_on"),
            "confidence": e["confidence"], "notes": e.get("notes") or "",
            "predates_collection": predates_collection(e), "comparison": w["reason"], "source": source,
            "links": [{"label": e.get("source_title") or e.get("source_publisher") or e["source_url"],
                       "publisher": e.get("source_publisher"), "url": e["source_url"]}]}


def build_export(conn, ctx) -> Dict:
    """docs/data/syndication.json: the timeline (every valid entry, newest first, with its source
    link) and one before and after row per eligible deal. No article text."""
    outlets = _ctx_outlets(ctx)
    by_id = {o["id"]: o for o in outlets}
    entries, errors = validate_file(outlets=outlets)
    last = last_complete_day(ctx)
    lo, hi = eligible_range(last)
    relay_inc = _relay_incomplete(conn, last)
    changes = _changes()
    timeline, comparisons = [], []
    for e in sorted(entries, key=lambda x: (x["signed_on"], x["id"]), reverse=True):
        w = window(e, last)
        outlet = by_id.get(e.get("outlet_id")) if not e.get("unmonitored") else None
        timeline.append(_entry_row(e, w, outlet))
        if w["eligible"]:
            comparisons.append(compare(conn, e, w, outlet, relay_inc, changes))
    counts = {"entries": len(timeline), "monitored": sum(1 for t in timeline if t["monitored"]),
              "unmonitored": sum(1 for t in timeline if not t["monitored"]),
              "predates_collection": sum(1 for t in timeline if t["predates_collection"]),
              "eligible": len(comparisons), "compared": sum(1 for c in comparisons if c["sufficient"]),
              "confounded": sum(1 for c in comparisons if c["sufficient"] and c["confounded"]),
              "invalid": len(errors)}
    out = {"measure": MEASURE_ID, "version": VERSION, "collection_start": config.COLLECTION_START,
           "last_complete_day": last.isoformat(), "window_days": WINDOW_DAYS,
           "min_china_per_side": MIN_CHINA_PER_SIDE, "eligible_from": lo.isoformat(), "eligible_until": hi.isoformat(),
           "counts": counts, "timeline": timeline, "comparisons": comparisons}
    if ctx is not None:
        ctx["_syndication_counts"] = counts
    return out


def meta_summary(conn, ctx) -> Dict:
    counts = (ctx or {}).get("_syndication_counts")
    if counts is None:
        counts = build_export(conn, ctx)["counts"]
    return dict(counts)


def _long_date(s: str) -> str:
    d = _d(s)
    return "%d %s %d" % (d.day, d.strftime("%B"), d.year)


def methodology_text(block) -> str:
    start = _long_date(config.COLLECTION_START)
    s = (block or {}).get("summary") or {}
    tally = ""
    if s.get("entries"):
        tally = (" Of the %d entries now listed, %d were signed before collection began."
                 % (s["entries"], s.get("predates_collection", 0)))
    return (
        "This measure lists publicly announced agreements between outlets and Chinese state media, such as "
        "content exchanges, memorandums and supplements, each with the public page that states the deal and "
        "the date it gives. An entry goes in only when such a page exists; an entry whose page is a third "
        "party's report is marked reported rather than stated. Most of these deals were signed before "
        "collection began on %s, so they appear on the timeline only, with no figure beside them.%s"
        "\n\n"
        "Where a deal was signed on a known day or month at least %d days after collection began and at "
        "least %d days before the last complete day, the outlet's state origin share of its China coverage is "
        "compared over the %d days before the deal and the %d days after it, on the same labels as every "
        "other figure. A side with fewer than %d China articles shows no share. The difference carries a 95 "
        "percent interval by Newcombe's method, built from two Wilson intervals. A comparison is flagged when "
        "a change to the signature ruleset or the relevance gate falls inside its window, when the owner's "
        "machine collected the outlet incompletely on at least a tenth of the days of a side, when the outlet "
        "discovered nothing on %d or more days of a side, or when at least a tenth of its feed polls on a side "
        "were saturated. The comparison shows timing, not cause: a change after a deal may come from the news, "
        "from other agreements or from the instrument, and no change does not show that a deal had no effect."
        "\n\n"
        "The hand check rereads the source page of %d entries drawn at random, without the recorded date, and "
        "notes whether the page states the deal and what date it gives. It passes when at least %s of the "
        "entries checked agree with their page at the precision recorded."
        % (start, tally, WINDOW_DAYS, WINDOW_DAYS, WINDOW_DAYS, WINDOW_DAYS, MIN_CHINA_PER_SIDE, ZERO_DAY_LIMIT,
           SAMPLE_N, CHECK_THRESHOLD))


# ---------------------------------------------------------------------------
# Hand check: re-verify each sampled entry's source and date
# ---------------------------------------------------------------------------

SAMPLE_FIELDS = ["sample_id", "entry_id", "outlet", "partner", "kind", "source_url", "source_title",
                 "page_states_deal", "page_date", "coder_notes"]
YES, NO = {"yes", "y", "1", "true"}, {"no", "n", "0", "false"}


def draw_sample(conn, n, seed, out_dir, path: Optional[Path] = None, outlets=None):
    """Up to 20 valid entries at random. Coders open source_url and fill page_states_deal (yes or no)
    and page_date (the signing date the page gives, YYYY-MM-DD, YYYY-MM or YYYY). The recorded date is
    in the key only, so the check is blind to it."""
    entries, _ = validate_file(path, outlets)
    by_id = {o["id"]: o for o in _outlets(outlets)}
    size = min(int(n or SAMPLE_N), SAMPLE_N, len(entries))
    picked = random.Random(str(seed)).sample(sorted(entries, key=lambda e: e["id"]), size)
    rows, key = [], {}
    for i, e in enumerate(picked, 1):
        sid = "s%02d" % i
        name = e.get("outlet_name") if e.get("unmonitored") else (by_id.get(e["outlet_id"], {}).get("name") or e["outlet_id"])
        rows.append({"sample_id": sid, "entry_id": e["id"], "outlet": name,
                     "partner": PARTNER_LABELS.get(e["partner"], e["partner"]),
                     "kind": KIND_LABELS.get(e["kind"], e["kind"]), "source_url": e["source_url"],
                     "source_title": e.get("source_title") or "", "page_states_deal": "", "page_date": "",
                     "coder_notes": ""})
        key[sid] = {"entry_id": e["id"], "signed_on": e["signed_on"], "precision": e["precision"]}
    return measures.write_sample(out_dir, MEASURE_ID, rows, SAMPLE_FIELDS,
                                 {"answers": key, "threshold": CHECK_THRESHOLD, "statistic": CHECK_STATISTIC},
                                 version=VERSION, seed=seed)


def date_agrees(page_date: str, signed_on: str) -> bool:
    """The page's date supports the recorded one at the recorded precision: a page giving a day
    supports that day, its month and its year; a page giving only a month does not support a day."""
    p = (page_date or "").strip()
    if not p or len(p) not in PRECISION_OF_LEN or not _valid_date(p):
        return False
    return len(p) >= len(signed_on) and p[:len(signed_on)] == signed_on


def score_check(rows, key) -> Dict:
    answers = (key or {}).get("answers") or {}
    by_sid = {r.get("sample_id"): r for r in rows or []}
    coded = agree = 0
    for sid, ans in answers.items():
        r = by_sid.get(sid) or {}
        v = (r.get("page_states_deal") or "").strip().lower()
        if v not in YES | NO:
            continue
        coded += 1
        if v in YES and date_agrees(r.get("page_date"), ans["signed_on"]):
            agree += 1
    return measures.check_result(CHECK_STATISTIC, round(agree / coded, 4) if coded else None, CHECK_THRESHOLD,
                                 len(answers), coded)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Syndication deals: check the entries file")
    sub = ap.add_subparsers(dest="cmd")
    v = sub.add_parser("validate", help="check a syndication file against the schema and the rules")
    v.add_argument("path", nargs="?", default=None)
    args = ap.parse_args(argv)
    if args.cmd != "validate":
        ap.print_help()
        return 2
    path = Path(args.path) if args.path else SYNDICATION_PATH
    entries, errors = validate_file(path)
    for e in errors:
        print(e)
    try:
        raw = load_file(path)
        total = len(raw.get("entries") or []) if isinstance(raw, dict) else 0
    except yaml.YAMLError:
        total = 0
    last = last_complete_day()
    elig = sum(1 for e in monitored(entries) if window(e, last)["eligible"])
    print("%s: %d entries, %d pass (%d monitored, %d predate collection, %d eligible for a comparison now), %d errors"
          % (path, total, len(entries), len(monitored(entries)), sum(1 for e in entries if predates_collection(e)),
             elig, len(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
