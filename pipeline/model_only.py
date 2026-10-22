"""State origin by the model's judgement alone, held apart until a hand check of it passes (owner decision
D1, 2026-10-22).

A state origin label rests on evidence a reader can check when its route is a wire credit line or
dateline, a press release distribution stamp, a sponsored or partner disclosure, a Chinese diplomat's
byline or an uncredited copy of a credited state item (classify_rules.ROUTE_IDS). A label whose route is
"unattributed" rests on nothing of the kind: the model called the text state origin and named no
credit, stamp, disclosure or byline, and the lookalike check matched it to no credited item. The model
is kept blind to outlet, country and address (decision D4), so such a call carries no marker a reader
can point to on the page, and the label accuracy audit found about 390 private company, university and
sports club releases among the weak model calls (classify_llm.RELABEL_PRIORITY). These placements are
therefore counted as their own figure, "model judgement, provisional", and kept out of every state
origin count, share, ranking, map colour and finding, until a hand check of them passes. Stored labels
never change here: the export reads them under another code (CODE, "M") and the audit files keep every
label with its route.

The set is the one meta.route_totals counts as unattributed: a current state origin label whose stored
route is "unattributed" or empty (route or "unattributed"), the model's labels only. A rules label rests
on a signature by construction and is never in it: one made before routes were stored gets the route of
its signatures from classify_rules.ensure_routes at the start of every export. On the committed data of
2026-10-22 no rules label carried that route, so the set is exactly the 1,123 placements the site then
reported as unattributed.

The hand check. `python -m pipeline.model_only sample` draws a seeded random sample of the model-only
labels, stratified by language with proportional allocation, mixed with control rows (current model
labels of independent journalism or not about China, drawn over the same languages) so the coder cannot
tell from the sheet which rows the model called state origin. The sheet,
data/reviews/model_only_a_sample_<day>.csv, carries headline, link, outlet and language and never a
label; the key beside it (.key.json) holds which rows are model-only labels. Coders fill human_category
with the codebook categories, as in data/reviews/CODING_GUIDE.md. The export scores the newest committed
sample: precision is the share of the coded model-only rows the coders placed in state origin.
  passed      precision at least PASS_PRECISION on at least MIN_CODED coded model-only rows: the
              placements rejoin state origin
  withheld    precision below WITHHOLD_BELOW on at least MIN_CODED coded rows: still apart, and the site
              shows no figure for them, only that they are withheld
  provisional anything else (no sample, fewer than MIN_CODED coded, or between the two): still apart,
              shown as their own figure marked provisional
A sample drawn under another classifier prompt (classify_llm.PROMPT_VERSION) is stale and decides nothing.
"""
import argparse
import csv
import datetime as dt
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pipeline import config

ROUTE = "unattributed"
CODE = "M"
CHECK_ID = "model_only_a"
LABEL = "State origin by the model's judgement alone (provisional)"
SHORT_LABEL = "model judgement, provisional"
EVIDENCE_ROUTES = ("wire_credit", "distribution_stamp", "sponsored_disclosure", "diplomatic_byline", "uncredited_copy")
PASS_PRECISION = 0.9
WITHHOLD_BELOW = 0.8
MIN_CODED = 100
DEFAULT_N = 150          # model-only rows: room for unreadable pages and still MIN_CODED coded
DEFAULT_CONTROLS = 50    # control rows mixed in so the sheet does not give the label away
REVIEWS_DIR = config.ROOT / "data" / "reviews"
STATUSES = ("provisional", "passed", "withheld")
FIELDS = ["sample_id", "url", "outlet", "language", "title", "human_category", "coder", "note"]


def is_model_only(category, route, method=None) -> bool:
    """A current state origin label that rests on the model's judgement alone: route unattributed or empty, as
    meta.route_totals counts it. A rules label is never model-only: it rests on a signature by construction,
    and one stored before routes were (route empty) is evidence; ensure_routes gives it its route."""
    return category == "A" and (route or ROUTE) == ROUTE and method != "rules"


def evidence_sql(alias: str = "c", held: Optional[bool] = None) -> str:
    """An SQL condition to AND onto a state origin query so it counts the evidence routes only, or "" once
    the hand check has passed and the model-only labels have rejoined state origin."""
    if held is None:
        held = held_apart()
    return (" AND NOT (COALESCE(%s.route, '%s') = '%s' AND COALESCE(%s.method, '') <> 'rules')"
            % (alias, ROUTE, ROUTE, alias)) if held else ""


def headline_category(category, route, held: Optional[bool] = None, method=None):
    """The category a placement is counted under: CODE for a model-only state origin label while it is held
    apart, the stored category otherwise."""
    if is_model_only(category, route, method) and (held_apart() if held is None else held):
        return CODE
    return category


# ---------------------------------------------------------------------------
# The hand check
# ---------------------------------------------------------------------------

def _prompt_version() -> Optional[str]:
    try:
        from pipeline import classify_llm
        return classify_llm.PROMPT_VERSION
    except Exception:  # noqa: BLE001
        return None


def sample_files(reviews_dir: Optional[Path] = None) -> List[Tuple[Path, Path]]:
    """(csv, key) pairs of the committed samples, newest first, in the measures' house order."""
    from pipeline import measures
    return measures.sample_files(CHECK_ID, Path(reviews_dir or REVIEWS_DIR))


def score(rows: List[Dict], key: Dict) -> Dict:
    """Scores one coded sheet against its key: n model-only rows, coded (a codebook category entered),
    confirmed (coded state origin), precision, and controls coded. Rows not in the key are ignored."""
    from pipeline import agreement
    items = key.get("items") or {}
    n = sum(1 for v in items.values() if v.get("role") == "model_only")
    coded = confirmed = controls = 0
    for r in rows:
        it = items.get((r.get("sample_id") or "").strip())
        if not it:
            continue
        cat = agreement.coder_category(r.get("human_category") or "")
        if cat is None:
            continue
        if it.get("role") == "model_only":
            coded += 1
            confirmed += 1 if cat == "A" else 0
        else:
            controls += 1
    return {"n": n, "coded": coded, "confirmed": confirmed, "controls_coded": controls,
            "precision": round(confirmed / float(coded), 4) if coded else None}


def verdict(sc: Dict) -> str:
    """passed, withheld or provisional for one scored sheet."""
    if (sc.get("coded") or 0) < MIN_CODED or sc.get("precision") is None:
        return "provisional"
    if sc["precision"] >= PASS_PRECISION:
        return "passed"
    if sc["precision"] < WITHHOLD_BELOW:
        return "withheld"
    return "provisional"


def _check_of(csv_path: Path, key_path: Path) -> Dict:
    from pipeline import measures
    with open(key_path, encoding="utf-8") as fh:
        key = json.load(fh)
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    m = measures._DATE.search(csv_path.name)
    check = {"state": "none", "file": measures._rel(csv_path), "drawn_on": m.group(1) if m else None,
             "checked_on": key.get("checked_on"), "prompt_version": key.get("prompt_version"),
             "n": 0, "coded": 0, "confirmed": 0, "precision": None}
    current = _prompt_version()
    if key.get("prompt_version") and current and key["prompt_version"] != current:
        check["state"] = "stale"
        return check
    sc = score(rows, key)
    check.update(sc)
    v = verdict(sc)
    check["state"] = v if v != "provisional" else ("under_way" if (sc["coded"] or 0) < MIN_CODED else "inconclusive")
    return check


def _empty_check() -> Dict:
    return {"state": "none", "file": None, "drawn_on": None, "checked_on": None, "prompt_version": None,
            "n": 0, "coded": 0, "confirmed": 0, "precision": None}


def _pct(v) -> str:
    return "n/a" if v is None else "%d percent" % round(100.0 * v)


def assess(reviews_dir: Optional[Path] = None) -> Dict:
    """{status, check, reason} from the committed samples. The newest sample of the current prompt decides;
    while it is still being coded, an earlier sample that withheld them keeps them withheld, and one that
    passed leaves them provisional until the new one reports. A sample that cannot be read is reported and
    leaves them provisional, never a stop."""
    try:
        checks = []
        for c, k in sample_files(reviews_dir):
            checks.append(_check_of(c, k))
            if checks[-1]["state"] in ("passed", "withheld"):
                break
    except Exception as exc:  # noqa: BLE001
        chk = _empty_check()
        chk.update({"state": "error", "error": "%s: %s" % (type(exc).__name__, exc)})
        return {"status": "provisional", "check": chk, "reason": "the hand check could not be scored (%s)" % chk["error"]}
    current = [c for c in checks if c["state"] != "stale"]
    if not checks:
        return {"status": "provisional", "check": _empty_check(), "reason": "no hand check yet"}
    if not current:
        return {"status": "provisional", "check": checks[0],
                "reason": "the latest hand check was drawn under classifier prompt %s; a new one is needed" % checks[0].get("prompt_version")}
    newest = current[0]
    done = next((c for c in current if c["state"] in ("passed", "withheld")), None)
    stat = lambda c: "%s of %d coded model only labels confirmed as state origin (%s)" % (
        "{:,}".format(c.get("confirmed") or 0), c.get("coded") or 0, _pct(c.get("precision")))
    if newest["state"] == "passed":
        return {"status": "passed", "check": newest, "reason": "hand check passed: %s" % stat(newest)}
    if newest["state"] == "withheld":
        return {"status": "withheld", "check": newest, "reason": "hand check failed: %s, below %s" % (stat(newest), _pct(WITHHOLD_BELOW))}
    if done and done["state"] == "withheld":
        return {"status": "withheld", "check": done, "reason": "the latest completed hand check failed (%s); a new one is under way, %d of %d coded"
                % (stat(done), newest.get("coded") or 0, newest.get("n") or 0)}
    if newest["state"] == "inconclusive":
        return {"status": "provisional", "check": newest, "reason": "hand check inconclusive: %s, between %s and %s" % (
            stat(newest), _pct(WITHHOLD_BELOW), _pct(PASS_PRECISION))}
    return {"status": "provisional", "check": newest, "reason": "hand check under way, %d of %d coded" % (
        newest.get("coded") or 0, newest.get("n") or 0)}


def status(reviews_dir: Optional[Path] = None) -> str:
    return assess(reviews_dir)["status"]


def held_apart(reviews_dir: Optional[Path] = None) -> bool:
    """True until the hand check passes: the model-only labels stay out of state origin."""
    return status(reviews_dir) != "passed"


# ---------------------------------------------------------------------------
# Counting and the meta block
# ---------------------------------------------------------------------------

def _pool_sql() -> str:
    return """SELECT a.id, a.country, a.outlet_id, a.language, a.title, a.url, a.discovered_at, a.day_of_record,
                     c.id AS cid, c.category, c.route, c.method, c.model_version
              FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
              WHERE a.gate_relevant=1 AND c.category='A' AND COALESCE(c.route, '%s')='%s'
                    AND COALESCE(c.method, '') <> 'rules' ORDER BY a.id""" % (ROUTE, ROUTE)


def pool(conn) -> List[Dict]:
    """The model-only placements: current, gate relevant, a copy another row stands for left out
    (pipeline/placements.py), as every count on the site leaves it out."""
    from pipeline import placements
    shadow = placements.shadowed(conn)
    return [dict(r) for r in conn.execute(_pool_sql()) if r["id"] not in shadow]


def counts(conn, start: Optional[str] = None, end: Optional[str] = None) -> int:
    """Model-only placements on the days start <= day < end (every day when both are None)."""
    n = 0
    for r in pool(conn):
        d = r["day_of_record"] or (r["discovered_at"] or "")[:10]
        if (start and d < start) or (end and d >= end):
            continue
        n += 1
    return n


def meta_block(conn, start30: Optional[str] = None, end30: Optional[str] = None,
               reviews_dir: Optional[Path] = None) -> Dict:
    """meta.model_only_state_origin: the count all time and over the 30 complete days the map opens on,
    the hand check's status, check and reason, and the rule. held_apart says whether they are out of
    state origin in this export (every state but passed)."""
    a = assess(reviews_dir)
    st = a["status"]
    return {"count": counts(conn), "count_last_30d": counts(conn, start30, end30) if start30 else None,
            "window_start": start30, "window_end": (dt.date.fromisoformat(end30) - dt.timedelta(days=1)).isoformat() if end30 else None,
            "status": st, "passed": st == "passed", "held_apart": st != "passed", "check": a["check"], "reason": a["reason"],
            "label": LABEL, "route": ROUTE, "code": CODE,
            "rule": {"pass_precision": PASS_PRECISION, "withhold_below": WITHHOLD_BELOW, "min_coded": MIN_CODED,
                     "statistic": "precision"}}


def methodology_text(block: Optional[Dict]) -> str:
    """The paragraph for METHODOLOGY.md and the site's method section, from meta.model_only_state_origin."""
    b = block or {}
    n = b.get("count") or 0
    st = b.get("status") or "provisional"
    chk = b.get("check") or {}
    lead = ("State origin by the model's judgement alone. A state origin label the model gave with no credit line, "
            "distribution stamp, sponsored disclosure or diplomat's byline named as its evidence, and with no near "
            "copy of a credited state item behind it (the route listed as unattributed), rests on the model's call "
            "and on nothing a reader can check. The model is kept blind to outlet, country and address, so that its "
            "judgement of the text is not swayed by who published it, and that blindness, kept unchanged until after "
            "23 October 2026, is also why such a call carries no visible marker. The label accuracy audit of "
            "October 2026 found private company, university and sports club releases among these weak calls, about "
            "390 placements. Since 22 October 2026 these labels are held apart: they are counted as their own figure, "
            "model judgement, provisional, and kept out of every state origin count, share, ranking, map colour and "
            "finding. Stored labels are unchanged, and the audit files keep every label with its route. At export "
            "time there were %s such placements" % "{:,}".format(n))
    if b.get("count_last_30d") is not None and b.get("window_end"):
        lead += ", %s of them in the 30 complete days ending %s" % ("{:,}".format(b["count_last_30d"]), b["window_end"])
    lead += "."
    rule = (" They rejoin state origin once a hand check confirms them: a seeded random sample of them, stratified "
            "by language and mixed with control rows the model judged independent journalism or not about China, "
            "so the sheet does not reveal which rows the model called state origin, is coded blind by the codebook "
            "(data/reviews/model_only_a_sample_*.csv, with its key beside it). They rejoin when at least %d percent "
            "of at least %d coded model only rows are confirmed as state origin, and the figure is withheld when "
            "fewer than %d percent are." % (round(PASS_PRECISION * 100), MIN_CODED, round(WITHHOLD_BELOW * 100)))
    if st == "passed":
        now = (" The hand check has passed (%s of %d coded rows confirmed, %s), so these placements are counted in "
               "state origin again." % ("{:,}".format(chk.get("confirmed") or 0), chk.get("coded") or 0, _pct(chk.get("precision"))))
    elif st == "withheld":
        now = (" The hand check failed (%s of %d coded rows confirmed, %s), so they stay out of state origin and "
               "their figure is withheld." % ("{:,}".format(chk.get("confirmed") or 0), chk.get("coded") or 0, _pct(chk.get("precision"))))
    else:
        now = " Status: provisional, %s." % (b.get("reason") or "no hand check yet")
    return lead + rule + now


# ---------------------------------------------------------------------------
# Drawing the sample
# ---------------------------------------------------------------------------

def _allocate(sizes: Dict[str, int], n: int) -> Dict[str, int]:
    """Proportional allocation of n over strata of the given sizes, by largest remainder, never more than
    a stratum holds, so the sample's precision needs no weights."""
    total = sum(sizes.values())
    if not total or n <= 0:
        return {k: 0 for k in sizes}
    n = min(n, total)
    raw = {k: n * v / float(total) for k, v in sizes.items()}
    out = {k: min(sizes[k], int(raw[k])) for k in sizes}
    left = n - sum(out.values())
    for k in sorted(sizes, key=lambda k: (-(raw[k] - int(raw[k])), k)):
        if left <= 0:
            break
        if out[k] < sizes[k]:
            out[k] += 1
            left -= 1
    return out


def _controls_pool(conn, languages) -> List[Dict]:
    from pipeline import placements
    shadow = placements.shadowed(conn)
    rows = conn.execute(
        """SELECT a.id, a.country, a.outlet_id, a.language, a.title, a.url, c.id AS cid, c.category, c.route
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 AND c.method='llm' AND c.category IN ('C', 'not_relevant') ORDER BY a.id""")
    return [dict(r) for r in rows if r["id"] not in shadow and r["language"] in languages]


def draw(conn, n: int = DEFAULT_N, controls: int = DEFAULT_CONTROLS, seed: Optional[str] = None) -> Tuple[List[Dict], Dict]:
    """(sheet rows, key) for a sample of n model-only labels and up to `controls` control rows, both
    stratified by language with proportional allocation (the controls over the languages of the
    model-only draw), shuffled together so the order gives nothing away."""
    from pipeline import registry
    rng = random.Random(seed)
    names = {o["id"]: o["name"] for o in registry.load_outlets()}
    base = pool(conn)
    by_lang = defaultdict(list)
    for r in base:
        by_lang[r["language"] or "und"].append(r)
    alloc = _allocate({k: len(v) for k, v in by_lang.items()}, n)
    picked = []
    for lang in sorted(by_lang):
        rows = by_lang[lang][:]
        rng.shuffle(rows)
        picked += [dict(r, role="model_only") for r in rows[:alloc[lang]]]
    drawn_langs = {}
    for r in picked:
        drawn_langs[r["language"] or "und"] = drawn_langs.get(r["language"] or "und", 0) + 1
    ctrl_by = defaultdict(list)
    for r in _controls_pool(conn, set(drawn_langs)):
        ctrl_by[r["language"] or "und"].append(r)
    calloc = _allocate({k: drawn_langs[k] for k in drawn_langs if ctrl_by.get(k)}, controls)
    for lang in sorted(calloc):
        rows = ctrl_by[lang][:]
        rng.shuffle(rows)
        picked += [dict(r, role="control") for r in rows[:calloc[lang]]]
    rng.shuffle(picked)
    sheet, items = [], {}
    for i, r in enumerate(picked, 1):
        sid = "mo-%03d" % i
        sheet.append({"sample_id": sid, "url": r["url"], "outlet": names.get(r["outlet_id"], r["outlet_id"]),
                      "language": r["language"], "title": r["title"], "human_category": "", "coder": "", "note": ""})
        items[sid] = {"article_id": r["id"], "classification_id": r["cid"], "role": r["role"],
                      "category": r["category"], "route": r["route"] or (ROUTE if r["role"] == "model_only" else None)}
    key = {"kind": CHECK_ID, "seed": seed, "prompt_version": _prompt_version(), "ruleset_version": config.RULESET_VERSION,
           "pool": len(base), "model_only_n": sum(1 for v in items.values() if v["role"] == "model_only"),
           "controls_n": sum(1 for v in items.values() if v["role"] == "control"),
           "stratified_on": "language, proportional allocation; controls over the same languages",
           "languages": {k: {"pool": len(by_lang[k]), "sampled": alloc[k]} for k in sorted(by_lang)},
           "rule": {"pass_precision": PASS_PRECISION, "withhold_below": WITHHOLD_BELOW, "min_coded": MIN_CODED},
           "items": items}
    return sheet, key


def write_sample(conn, n: int = DEFAULT_N, controls: int = DEFAULT_CONTROLS, seed: Optional[str] = None,
                 out_dir: Optional[Path] = None, day: Optional[str] = None) -> Tuple[Path, Path]:
    """Draws a sample and writes data/reviews/model_only_a_sample_<day>.csv (what coders see, no label)
    and its .key.json (measures.write_sample: never an empty draw, never over a sample of the same day)."""
    from pipeline import measures
    sheet, key = draw(conn, n, controls, seed)
    if not any(v["role"] == "model_only" for v in key["items"].values()):
        raise ValueError("refusing an empty %s sample: no model-only state origin label to draw" % CHECK_ID)
    return measures.write_sample(Path(out_dir or REVIEWS_DIR), CHECK_ID, sheet, FIELDS, key, seed=seed, day=day)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="State origin by the model's judgement alone: hand check sample and status")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="the hand check status the export would use")
    s = sub.add_parser("sample", help="draw a blind hand check sample into data/reviews")
    s.add_argument("--n", type=int, default=DEFAULT_N, help="model-only rows (default %d)" % DEFAULT_N)
    s.add_argument("--controls", type=int, default=DEFAULT_CONTROLS, help="control rows mixed in (default %d)" % DEFAULT_CONTROLS)
    s.add_argument("--seed", default=None)
    s.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "sample":
        from pipeline import store
        conn = store.connect()
        seed = args.seed or "%s-%s" % (CHECK_ID, dt.datetime.now(dt.timezone.utc).date().isoformat())
        try:
            paths = write_sample(conn, args.n, args.controls, seed, Path(args.out) if args.out else None)
        except ValueError as exc:
            raise SystemExit("no sample written: %s" % exc)
        print("wrote %s" % ", ".join(str(p) for p in paths))
    else:
        print(json.dumps(assess(), indent=1, sort_keys=True, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
