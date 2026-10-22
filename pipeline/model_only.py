"""Undetermined items (code U): state origin calls whose state authorship or commissioning the evidence
cannot settle, excluded from every category count (owner decision D1 as revised on 2026-10-22, after the
original study).

The original study (the freeze-8 study) codes an item whose authorship or commissioning cannot be settled,
and could be state, as undetermined, and leaves it out of every category count. The tracker does the same.
A state origin label is counted only when its state authorship is settled by evidence a reader can check:
a wire credit line or dateline, a press release distribution stamp, a sponsored or partner disclosure the
rules find on the article itself, a Chinese diplomat's byline, or a close match with credited state text
(classify_rules.ROUTE_IDS). Two kinds of current state origin label are undetermined instead:

  (a) a model label whose route is "unattributed" or empty: the model called the text state origin and
      named no credit, stamp, disclosure or byline, and the lookalike check matched it to no credited item;
  (b) a model label whose route is "sponsored_disclosure": the model named a sponsored or partner
      disclosure the rules did not find. The rules look for a disclosure on the article itself and
      deliberately ignore sidebar and menu advertisements, and classify_llm.WEAK_MODEL_ROUTES already
      lists this route as weak;
  (c) a model label whose route is "uncredited_copy" when the item it nearly repeats is not settled
      state origin (unsettled_copy_sql): the lookalike reference recorded for it (llm_trigger
      "lookalike", else the match snapshotted in state_copies.details) no longer has a current state
      origin label that may stand as a reference (reference_sql), because it is undetermined itself,
      was relabelled, or rests on another uncredited copy. As in the original study, a copy counts
      only when the text it repeats is a known state original.

A rules label is never undetermined: it rests on a signature by construction, and one stored before
routes were (route empty) gets the route of its signatures from classify_rules.ensure_routes at the start
of every export. Model labels with the routes wire_credit, distribution_stamp and diplomatic_byline stay
state origin, and so does an uncredited_copy label whose recorded reference is settled state origin. The model is kept blind to outlet, country and address until after
23 October 2026 (decision D4), so a model call of either kind carries no marker a reader can point to.

Undetermined items are in no count, share or denominator: not in state origin, not in China coverage (the
China total is state origin, unchecked state sourcing and independent journalism only), not in the map, the
ranked list, the Top 10, the themes, the measures or the findings. They are shown only as their own figure,
and they never rejoin state origin. Stored labels never change here: the export reads them under another
code (CODE, "U") and the audit files keep every label with its route.

The hand check is information only. `python -m pipeline.model_only sample` draws a seeded random sample of
the undetermined labels, stratified by language with proportional allocation, mixed with control rows
(current model labels of independent journalism or not about China, drawn over the same languages) so the
coder cannot tell from the sheet which rows are undetermined. The sheet,
data/reviews/undetermined_a_sample_<day>.csv, carries headline, link, outlet and language and never a
label; the key beside it (.key.json) holds which rows are undetermined. Coders fill human_category with the
codebook categories, as in data/reviews/CODING_GUIDE.md, or with "undetermined" where they too cannot
settle who wrote or commissioned the text. The export scores the newest committed sample and reports what
the coders found: how many they settled as state origin, how many they also left undetermined, how many
they placed elsewhere, and the two shares (confirmed state, and could be state). Nothing it finds moves an
item into a count.
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
SPONSORED_ROUTE = "sponsored_disclosure"
# The stored routes of a model state origin label that leave its state authorship unsettled.
UNDETERMINED_ROUTES = (ROUTE, SPONSORED_ROUTE)
CODE = "U"
CHECK_ID = "undetermined_a"
LABEL = "Undetermined: who wrote or commissioned it could not be settled"
SHORT_LABEL = "Undetermined"
# The routes a model state origin label may carry and still be counted in state origin (an uncredited copy
# only while the item it repeats is settled state origin, unsettled_copy_sql).
EVIDENCE_ROUTES = ("wire_credit", "distribution_stamp", "diplomatic_byline", "uncredited_copy")
COPY_ROUTE = "uncredited_copy"
# The route the export reads (route_sql) for a model uncredited copy whose recorded reference is not settled
# state origin. Never stored: the stored route stays uncredited_copy.
COPY_OF_UNDETERMINED = "copy_of_undetermined"
MIN_CODED = 100          # coded undetermined rows before the check reports its shares as complete
DEFAULT_N = 150          # undetermined rows: room for unreadable pages and still MIN_CODED coded
DEFAULT_CONTROLS = 50    # control rows mixed in so the sheet does not give the label away
REVIEWS_DIR = config.ROOT / "data" / "reviews"
FIELDS = ["sample_id", "url", "outlet", "language", "title", "human_category", "coder", "note"]
# What a coder writes when the page does not settle who wrote or commissioned the text.
CODER_UNDETERMINED = ("undetermined", "u", "cannot_settle", "could_be_state")


def is_undetermined(category, route, method=None) -> bool:
    """A current state origin label the export counts as undetermined: a model label (any method but
    rules) whose stored route is unattributed or empty, or sponsored_disclosure, or whose route as the
    export reads it (route_sql) is COPY_OF_UNDETERMINED. A rules label never is. A caller that judges an
    uncredited copy must read its route through route_sql: the stored route alone does not say whether
    the item it repeats is settled."""
    return category == "A" and method != "rules" and (
        (route or ROUTE) in UNDETERMINED_ROUTES or route == COPY_OF_UNDETERMINED)


def _no_evidence_sql(alias: str) -> str:
    """Kinds (a) and (b): a model label whose stored route is unattributed, empty or sponsored_disclosure."""
    return ("(COALESCE(%s.method, '') <> 'rules' AND COALESCE(%s.route, '%s') IN (%s))"
            % (alias, alias, ROUTE, ", ".join("'%s'" % r for r in UNDETERMINED_ROUTES)))


def reference_sql(alias: str = "c") -> str:
    """The SQL condition on a current state origin label `alias` that may stand as a lookalike reference
    (pipeline/lookalike.py, credited_reference_sql): its route is one a credit supports
    (copies.CREDITED_ROUTES) or it is a rules label from before routes were stored, and it is not
    undetermined. A credited route is never uncredited_copy, so of the undetermined kinds only (a) and (b)
    can apply to it, and the test reads those alone."""
    from pipeline.copies import CREDITED_ROUTES  # copies imports lookalike, which imports this lazily
    routes = ", ".join("'%s'" % r for r in sorted(CREDITED_ROUTES))
    return ("((%s.route IN (%s) OR (%s.method = 'rules' AND %s.route IS NULL)) AND NOT %s)"
            % (alias, routes, alias, alias, _no_evidence_sql(alias)))


def reference_id_sql(alias: str = "c") -> str:
    """The SQL expression for the article id of the lookalike reference recorded for label `alias`: the
    match in its article's llm_trigger, else the one pipeline/copies.py snapshotted in state_copies (kept
    there because a later rules pass can rewrite llm_trigger), else NULL. Text that is not JSON is NULL."""
    return ("COALESCE((SELECT CASE WHEN json_valid(_ula.llm_trigger) THEN json_extract(_ula.llm_trigger, '$.lookalike.article_id') END "
            "FROM articles _ula WHERE _ula.id = {c}.article_id), "
            "(SELECT CASE WHEN json_valid(_usc.details) THEN json_extract(_usc.details, '$.lookalike.article_id') END "
            "FROM state_copies _usc WHERE _usc.copy_id = {c}.article_id))").format(c=alias)


def unsettled_copy_sql(alias: str = "c") -> str:
    """Kind (c), true for a model uncredited_copy label whose recorded reference (reference_id_sql) has no
    current state origin label that may stand as a reference (reference_sql). A copy with no recorded
    reference at all is left as stored: the route checks the match when the label is made
    (classify_llm._model_route), so none should exist. The reference is looked up only for uncredited
    copies."""
    ref = reference_id_sql(alias)
    return ("(CASE WHEN {c}.route = '{copy}' AND COALESCE({c}.method, '') <> 'rules' THEN "
            "CASE WHEN {ref} IS NULL THEN 0 ELSE NOT EXISTS (SELECT 1 FROM classifications _urc "
            "WHERE _urc.article_id = {ref} AND _urc.is_current = 1 AND _urc.category = 'A' AND {settled}) END "
            "ELSE 0 END)").format(c=alias, copy=COPY_ROUTE, ref=ref, settled=reference_sql("_urc"))


def undetermined_sql(alias: str = "c") -> str:
    """The SQL condition that is true for an undetermined label, given category A: kinds (a), (b) and (c)."""
    return "(%s OR %s)" % (_no_evidence_sql(alias), unsettled_copy_sql(alias))


def evidence_sql(alias: str = "c") -> str:
    """An SQL condition to AND onto a state origin query so it counts the evidence routes only."""
    return " AND NOT " + undetermined_sql(alias)


def route_sql(alias: str = "c") -> str:
    """The SQL expression for a label's route as the export reads it: COPY_OF_UNDETERMINED for an
    uncredited copy of an item that is not settled state origin (unsettled_copy_sql), the stored route
    otherwise. Every query whose rows go to is_undetermined, headline_category or counted_route selects
    its route through this."""
    return "(CASE WHEN %s THEN '%s' ELSE %s.route END)" % (unsettled_copy_sql(alias), COPY_OF_UNDETERMINED, alias)


def headline_category(category, route, method=None):
    """The category a placement is counted under: CODE for an undetermined label, the stored one otherwise."""
    return CODE if is_undetermined(category, route, method) else category


def counted_route(route, method=None) -> str:
    """The route a current state origin label is tallied under in the daily files and meta.route_totals:
    every undetermined label under "unattributed", the route filter's undetermined layer, so the counted
    routes add up to state origin."""
    if method != "rules" and ((route or ROUTE) in UNDETERMINED_ROUTES or route == COPY_OF_UNDETERMINED):
        return ROUTE
    return route or ROUTE


# ---------------------------------------------------------------------------
# The hand check (information only)
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


def _coded_as(value: str) -> Optional[str]:
    """A human_category cell as "A", "U", another codebook code, or None when blank or not a category."""
    from pipeline import agreement
    raw = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if raw in CODER_UNDETERMINED:
        return CODE
    return agreement.coder_category(value or "")


def _share(k: int, n: int) -> Optional[float]:
    return round(k / float(n), 4) if n else None


def score(rows: List[Dict], key: Dict) -> Dict:
    """Scores one coded sheet against its key: n undetermined rows, coded (a category or undetermined
    entered), confirmed (coded state origin), undetermined (the coders could not settle it either),
    not_state (coded anything else), controls coded, confirmed_share (confirmed over coded) and
    could_be_state_share (confirmed plus undetermined over coded). Rows not in the key are ignored."""
    items = key.get("items") or {}
    n = sum(1 for v in items.values() if v.get("role") == "undetermined")
    coded = confirmed = unsettled = controls = 0
    for r in rows:
        it = items.get((r.get("sample_id") or "").strip())
        if not it:
            continue
        cat = _coded_as(r.get("human_category") or "")
        if cat is None:
            continue
        if it.get("role") == "undetermined":
            coded += 1
            confirmed += 1 if cat == "A" else 0
            unsettled += 1 if cat == CODE else 0
        else:
            controls += 1
    return {"n": n, "coded": coded, "confirmed": confirmed, "undetermined": unsettled,
            "not_state": coded - confirmed - unsettled, "controls_coded": controls,
            "confirmed_share": _share(confirmed, coded), "could_be_state_share": _share(confirmed + unsettled, coded)}


def _empty_check() -> Dict:
    return {"state": "none", "file": None, "drawn_on": None, "checked_on": None, "prompt_version": None,
            "n": 0, "coded": 0, "confirmed": 0, "undetermined": 0, "not_state": 0, "controls_coded": 0,
            "confirmed_share": None, "could_be_state_share": None}


def _check_of(csv_path: Path, key_path: Path) -> Dict:
    from pipeline import measures
    with open(key_path, encoding="utf-8") as fh:
        key = json.load(fh)
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    m = measures._DATE.search(csv_path.name)
    check = _empty_check()
    check.update({"file": measures._rel(csv_path), "drawn_on": m.group(1) if m else None,
                  "checked_on": key.get("checked_on"), "prompt_version": key.get("prompt_version")})
    current = _prompt_version()
    if key.get("prompt_version") and current and key["prompt_version"] != current:
        check["state"] = "stale"
        return check
    check.update(score(rows, key))
    check["state"] = "reported" if (check["coded"] or 0) >= MIN_CODED else "under_way"
    return check


def _pct(v) -> str:
    return "n/a" if v is None else "%d percent" % round(100.0 * v)


def findings_text(c: Dict) -> str:
    """What the coders found, in words, for one scored check."""
    return ("of %s coded, the coders settled %s as state origin (%s), left %s undetermined and placed %s elsewhere; "
            "%s could be state" % ("{:,}".format(c.get("coded") or 0), "{:,}".format(c.get("confirmed") or 0),
                                   _pct(c.get("confirmed_share")), "{:,}".format(c.get("undetermined") or 0),
                                   "{:,}".format(c.get("not_state") or 0), _pct(c.get("could_be_state_share"))))


def assess(reviews_dir: Optional[Path] = None) -> Dict:
    """{check, summary} from the committed samples: the newest sample drawn under the current classifier
    prompt, scored, and what it found in words. It decides nothing. A sample that cannot be read is
    reported, never a stop."""
    try:
        checks = [_check_of(c, k) for c, k in sample_files(reviews_dir)]
    except Exception as exc:  # noqa: BLE001
        chk = _empty_check()
        chk.update({"state": "error", "error": "%s: %s" % (type(exc).__name__, exc)})
        return {"check": chk, "summary": "the hand check could not be scored (%s)" % chk["error"]}
    if not checks:
        return {"check": _empty_check(), "summary": "no hand check yet"}
    current = [c for c in checks if c["state"] != "stale"]
    if not current:
        return {"check": checks[0], "summary": "the latest hand check was drawn under classifier prompt %s; a new one is needed"
                % checks[0].get("prompt_version")}
    c = current[0]
    if c["state"] == "reported":
        return {"check": c, "summary": "hand check: " + findings_text(c)}
    return {"check": c, "summary": "hand check under way, %d of %d coded%s" % (
        c.get("coded") or 0, c.get("n") or 0, ("; so far " + findings_text(c)) if c.get("coded") else "")}


# ---------------------------------------------------------------------------
# Counting and the meta block
# ---------------------------------------------------------------------------

def _pool_sql() -> str:
    return """SELECT a.id, a.country, a.outlet_id, a.language, a.title, a.url, a.discovered_at, a.day_of_record,
                     c.id AS cid, c.category, c.route, c.method, c.model_version
              FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
              WHERE a.gate_relevant=1 AND c.category='A' AND %s ORDER BY a.id""" % undetermined_sql("c")


def pool(conn) -> List[Dict]:
    """The undetermined placements: current, gate relevant, a copy another row stands for left out
    (pipeline/placements.py), as every count on the site leaves it out."""
    from pipeline import placements
    shadow = placements.shadowed(conn)
    return [dict(r) for r in conn.execute(_pool_sql()) if r["id"] not in shadow]


def _in(r: Dict, start: Optional[str], end: Optional[str]) -> bool:
    d = r["day_of_record"] or (r["discovered_at"] or "")[:10]
    return not ((start and d < start) or (end and d >= end))


def counts(conn, start: Optional[str] = None, end: Optional[str] = None) -> int:
    """Undetermined placements on the days start <= day < end (every day when both are None)."""
    return sum(1 for r in pool(conn) if _in(r, start, end))


def by_route(conn, start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, int]:
    """Undetermined placements by the stored route, every route of kinds (a) and (b) listed, and
    uncredited_copy (kind (c)) when any copy of an item that is not settled state origin is among them."""
    out = dict.fromkeys(UNDETERMINED_ROUTES, 0)
    for r in pool(conn):
        if _in(r, start, end):
            k = r["route"] or ROUTE
            out[k] = out.get(k, 0) + 1
    return out


def meta_block(conn, start30: Optional[str] = None, end30: Optional[str] = None,
               reviews_dir: Optional[Path] = None) -> Dict:
    """meta.undetermined: the count all time and over the 30 complete days the map opens on, by stored
    route, what the hand check found (information only), and the rule."""
    a = assess(reviews_dir)
    routes_all = by_route(conn)
    return {"count": sum(routes_all.values()),
            "count_last_30d": counts(conn, start30, end30) if start30 else None,
            "window_start": start30, "window_end": (dt.date.fromisoformat(end30) - dt.timedelta(days=1)).isoformat() if end30 else None,
            "by_route": routes_all, "excluded_from_counts": True, "rejoins_state_origin": False,
            "check": a["check"], "summary": a["summary"], "label": LABEL, "short_label": SHORT_LABEL, "code": CODE,
            "rule": {"method_not": "rules", "routes": list(UNDETERMINED_ROUTES), "min_coded": MIN_CODED,
                     "statistics": ["confirmed_share", "could_be_state_share"]}}


def methodology_text(block: Optional[Dict]) -> str:
    """The paragraph for METHODOLOGY.md and the site's method section, from meta.undetermined."""
    b = block or {}
    n = b.get("count") or 0
    routes = b.get("by_route") or {}
    chk = b.get("check") or {}
    lead = ("Undetermined items. As in the original study, an item whose authorship or commissioning cannot be "
            "settled, and could be state, is undetermined (code U) and excluded from every category count. In the "
            "tracker the undetermined items are the model's state origin calls with no marker a reader can check "
            "(no credit line, distribution stamp, sponsored disclosure or diplomat's byline named as evidence, and "
            "no near copy of a credited state item behind them), its sponsored disclosure calls that the rules "
            "did not confirm, and its uncredited copy calls whose credited item is not settled state origin itself; "
            "the rules look for a sponsored or partner disclosure on the article itself and "
            "deliberately ignore sidebar and menu advertisements, and a copy counts only when the item it repeats is "
            "settled state origin. The model is kept blind to outlet, country and "
            "address until after 23 October 2026, so that who published a text cannot sway its judgement, and that "
            "blindness is also why such a call cannot be checked against anything on the page. The label accuracy "
            "audit of October 2026 found private company, university and sports club releases among these weak "
            "calls. Undetermined items are in no count, share or denominator: not in state origin, not in the China "
            "coverage total, not in the map, the rankings, the Top 10, the themes, the measures or the findings. "
            "They are shown only as their own figure, and they never rejoin state origin. Stored labels are "
            "unchanged, and the audit files keep every label with its route. At export time there were %s "
            "undetermined placements" % "{:,}".format(n))
    if routes:
        lead += " (%s with no marker, %s sponsored disclosures named by the model alone%s)" % (
            "{:,}".format(routes.get(ROUTE, 0)), "{:,}".format(routes.get(SPONSORED_ROUTE, 0)),
            ", %s uncredited copies of an item that is not settled state origin" % "{:,}".format(routes[COPY_ROUTE])
            if routes.get(COPY_ROUTE) else "")
    if b.get("count_last_30d") is not None and b.get("window_end"):
        lead += ", %s of them in the 30 complete days ending %s" % ("{:,}".format(b["count_last_30d"]), b["window_end"])
    lead += "."
    check = (" A blind hand check reports what they are, as information: a seeded random sample of them, stratified "
             "by language and mixed with control rows the model judged independent journalism or not about China, "
             "so the sheet does not reveal which rows are undetermined, is coded by the codebook, with undetermined "
             "allowed (data/reviews/undetermined_a_sample_*.csv, with its key beside it). It reports how many the "
             "coders settled as state origin and how many could be state; it moves nothing into a count.")
    if chk.get("coded"):
        now = " Hand check so far: %s." % findings_text(chk)
    else:
        now = " Hand check: %s." % (b.get("summary") or "no hand check yet")
    return lead + check + now


# ---------------------------------------------------------------------------
# Drawing the sample
# ---------------------------------------------------------------------------

def _allocate(sizes: Dict[str, int], n: int) -> Dict[str, int]:
    """Proportional allocation of n over strata of the given sizes, by largest remainder, never more than
    a stratum holds, so the sample's shares need no weights."""
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
    """(sheet rows, key) for a sample of n undetermined labels and up to `controls` control rows, both
    stratified by language with proportional allocation (the controls over the languages of the
    undetermined draw), shuffled together so the order gives nothing away."""
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
        picked += [dict(r, role="undetermined") for r in rows[:alloc[lang]]]
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
        sid = "ud-%03d" % i
        sheet.append({"sample_id": sid, "url": r["url"], "outlet": names.get(r["outlet_id"], r["outlet_id"]),
                      "language": r["language"], "title": r["title"], "human_category": "", "coder": "", "note": ""})
        items[sid] = {"article_id": r["id"], "classification_id": r["cid"], "role": r["role"],
                      "category": r["category"], "route": r["route"] or (ROUTE if r["role"] == "undetermined" else None)}
    key = {"kind": CHECK_ID, "seed": seed, "prompt_version": _prompt_version(), "ruleset_version": config.RULESET_VERSION,
           "pool": len(base), "undetermined_n": sum(1 for v in items.values() if v["role"] == "undetermined"),
           "controls_n": sum(1 for v in items.values() if v["role"] == "control"),
           "stratified_on": "language, proportional allocation; controls over the same languages",
           "languages": {k: {"pool": len(by_lang[k]), "sampled": alloc[k]} for k in sorted(by_lang)},
           "rule": {"min_coded": MIN_CODED, "decides": "nothing; information only"},
           "items": items}
    return sheet, key


def write_sample(conn, n: int = DEFAULT_N, controls: int = DEFAULT_CONTROLS, seed: Optional[str] = None,
                 out_dir: Optional[Path] = None, day: Optional[str] = None) -> Tuple[Path, Path]:
    """Draws a sample and writes data/reviews/undetermined_a_sample_<day>.csv (what coders see, no label)
    and its .key.json (measures.write_sample: never an empty draw, never over a sample of the same day)."""
    from pipeline import measures
    sheet, key = draw(conn, n, controls, seed)
    if not any(v["role"] == "undetermined" for v in key["items"].values()):
        raise ValueError("refusing an empty %s sample: no undetermined label to draw" % CHECK_ID)
    return measures.write_sample(Path(out_dir or REVIEWS_DIR), CHECK_ID, sheet, FIELDS, key, seed=seed, day=day)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Undetermined items: hand check sample and what it found")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="what the latest hand check found (information only)")
    s = sub.add_parser("sample", help="draw a blind hand check sample into data/reviews")
    s.add_argument("--n", type=int, default=DEFAULT_N, help="undetermined rows (default %d)" % DEFAULT_N)
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
