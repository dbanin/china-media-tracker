"""Recall audit of the candidate gate: how much state origin and unchecked state sourcing the rules
settle without ever asking the model.

The model sees an article only when a weak state origin signature fires or an official sourcing
trigger does (pipeline/classify_rules.py, classify_article; the triggers are llm_triggers in
pipeline/signatures.yaml: a citation of Xinhua, CGTN, Global Times, China Daily, CCTV, People's
Daily or China Media Group, a Chinese foreign ministry spokesperson, a Chinese embassy, a named
official spokesperson, or a "state media reported" construction). Every other article that passed
the relevance gate is labelled independent journalism or not relevant by rules, with no model call.
Agency copy that runs without a credit line names none of those things, so the gate cannot see
uncredited relay at all, and the outlets that run the most of it are where it is blind.

This module measures that blind spot by hand. `sample` draws a seeded random sample of the articles
the rules labelled independent journalism or not relevant without a model call, stratified by
language and, inside each language, oversampling the articles whose headline or feed summary nearly
repeats a state origin item already in the database (lookalikes) and the outlets with a known wire
copy habit.
It writes a CSV for coders with no rule label in it, and a sidecar key that freezes the strata,
their sizes and the rule labels, so a later reclassification cannot change what the audit measured.
`compute` reads the filled CSV and reports the share the coders placed in state origin or unchecked
state sourcing (the gate's miss rate), weighted back to the population, with a Wilson interval on
the Kish effective sample size, per language and overall, and stores it in recall_audits.

Coders fill human_category with one of: state origin, unchecked state sourcing, independent
journalism, not about China (the codes A, B, C and not_relevant are accepted too). Rows left
blank are not counted.

  python -m pipeline.recall_audit sample --n 300 --seed 20261012
  python -m pipeline.recall_audit compute --csv data/reviews/recall_audit_2026-10-12.csv
  python -m pipeline.recall_audit describe          # the population and the lookalikes, no sample
"""
import argparse
import csv
import datetime as dt
import json
import math
import random
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from pipeline import config
from pipeline.lookalike import (BODY_CONTAINMENT, BODY_MIN_SHINGLES, SHINGLE, SUMMARY_CONTAINMENT,  # noqa: F401
                                SUMMARY_MIN_SHINGLES, TITLE_JACCARD, TITLE_MIN_TOKENS, _Index, shingles,
                                tie_rank, title_set, tokens)

REVIEWS_DIR = config.ROOT / "data" / "reviews"
DEFAULT_N = 300
# Each article in the wire habit stratum of a language is this many times as likely to be drawn as
# one in the rest of that language. The weights in the key undo it when the rate is computed.
OVERSAMPLE = 3.0
# Every language with at least MIN_LANGUAGE_POP articles in the population gets at least this many
# draws, so no language with a real volume is left out by proportional allocation.
MIN_PER_LANGUAGE = 3
MIN_LANGUAGE_POP = 200
# The share of the draws given to lookalikes, the articles whose headline or summary nearly repeats a
# state origin item: the likeliest misses, too few to turn up by chance in a sample of this size.
LOOKALIKE_SHARE = 0.1
# Outlets known to carry state agency copy, named because the habit is documented outside the
# database: AsiaOne runs agency copy without a credit line (the owner's paper found its relay the
# highest of the outlets it read); it was registered as sg_asiaone on 2026-10-12. The three
# Italian agencies carry Xinhua material under agreements (sources/outlets.yaml notes).
WIRE_HABIT_NAMED = {"sg_asiaone", "it_ansa", "it_italpress", "it_adnkronos"}
# An outlet also has the habit when it is a news agency (registry tier agency), when at least this
# many of its current labels are state origin, or when at least LOOKALIKE_HABIT_MIN of its rules
# labelled articles nearly repeat a state origin item.
WIRE_HABIT_MIN_STATE_ORIGIN = 5
LOOKALIKE_HABIT_MIN = 3
# Lookalike thresholds and similarity helpers live in pipeline/lookalike.py, shared with the rules
# stage, which sends lookalikes to the model (classify_rules.classify_article).
Z95 = 1.959964

CATEGORY_ALIASES = {
    "a": "A", "state origin": "A",
    "b": "B", "unchecked state sourcing": "B", "unverified relay": "B",
    "c": "C", "independent journalism": "C",
    "not_relevant": "not_relevant", "not relevant": "not_relevant", "not about china": "not_relevant", "n": "not_relevant",
}
MISS = {"A", "B"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS recall_audits (
    id INTEGER PRIMARY KEY,
    computed_at TEXT NOT NULL,
    sample_file TEXT NOT NULL,
    sampled_at TEXT,
    seed INTEGER,
    ruleset_version TEXT,
    population INTEGER NOT NULL,
    n_coded INTEGER NOT NULL,
    n_missed INTEGER NOT NULL,
    miss_rate REAL,
    ci_low REAL,
    ci_high REAL,
    details TEXT
);
"""


def ensure_table(conn) -> None:
    conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# the population
# ---------------------------------------------------------------------------

def population(conn) -> List[Dict]:
    """Articles whose current label is independent journalism or not relevant by rules and that were
    never routed to the model: no trigger was ever recorded and no model label exists for them."""
    rows = conn.execute(
        """SELECT a.id AS article_id, a.url, a.url_hash, a.outlet_id, a.feed_url, a.language, a.country, a.title, a.summary,
                  c.id AS classification_id, c.category, c.ruleset_version, o.tier
           FROM classifications c
           JOIN articles a ON a.id = c.article_id
           LEFT JOIN outlets o ON o.id = a.outlet_id
           WHERE c.is_current = 1 AND c.method = 'rules' AND c.category IN ('C', 'not_relevant')
             AND (a.llm_trigger IS NULL OR a.llm_trigger = '')
             AND NOT EXISTS (SELECT 1 FROM classifications l WHERE l.article_id = a.id AND l.method = 'llm')
           ORDER BY a.id"""
    ).fetchall()
    return [dict(r) for r in rows]


def state_origin_items(conn) -> List[Dict]:
    # The times lookalike.tie_rank orders equally similar items by; day_of_record only where the
    # database has it (the audit opens a snapshot read only, without store.connect's migrations).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)")}
    times = "".join(", a.%s" % c for c in ("published_at", "discovered_at", "day_of_record") if c in cols)
    rows = conn.execute(
        """SELECT a.id AS article_id, a.url_hash, a.outlet_id, a.feed_url, a.language, a.title, a.summary, c.route%s
           FROM classifications c JOIN articles a ON a.id = c.article_id
           WHERE c.is_current = 1 AND c.category = 'A' ORDER BY a.id""" % times
    ).fetchall()
    return [dict(r) for r in rows]


def lookalikes(rows: List[Dict], refs: List[Dict], bodies=None, boiler=None) -> Dict[int, Dict]:
    """Articles in rows whose headline, feed summary or (when `bodies` returns one for both) body nearly
    repeats a state origin item that is a different article. Headlines are compared within a language.
    With `boiler` (a pipeline/boilerplate.FeedBoilerplate), feed summaries are compared without the
    boilerplate their feed repeats in most of its items, as the rules stage's route compares them.
    Returns {article_id: {"by": "title"|"summary"|"body", "score", "match_article_id", "match_outlet_id", "match_route"}}."""
    def summary(r):
        return boiler.summary(r, r["summary"]) if boiler is not None and r["summary"] else r["summary"]

    by_id = {r["article_id"]: r for r in refs}
    ranks = {k: tie_rank(r) for k, r in by_id.items()}   # equally similar items: the earliest, then the lowest id
    titles = defaultdict(lambda: _Index(ranks.get))
    summ = _Index(ranks.get)
    for r in refs:
        ts = title_set(r["title"])
        if ts:
            titles[r["language"]].add(r["article_id"], ts)
        sh = shingles(summary(r))
        if len(sh) >= SUMMARY_MIN_SHINGLES:
            summ.add(r["article_id"], sh)
    body_idx = None
    if bodies:
        body_idx = _Index(ranks.get)
        for r in refs:
            b = bodies(r["url_hash"])
            if b:
                body_idx.add(r["article_id"], shingles(b))
    out = {}

    def hit(r, by, key, score):
        m = by_id[key]
        out[r["article_id"]] = {"by": by, "score": round(score, 3), "match_article_id": key,
                                "match_outlet_id": m["outlet_id"], "match_route": m["route"]}

    for r in rows:
        ts = title_set(r["title"])
        if len(ts) >= TITLE_MIN_TOKENS and r["language"] in titles:
            b = titles[r["language"]].best_jaccard(ts)
            if b and b[0] != r["article_id"] and b[1] >= TITLE_JACCARD:
                hit(r, "title", b[0], b[1])
                continue
        sh = shingles(summary(r))
        if len(sh) >= SUMMARY_MIN_SHINGLES:
            b = summ.best(sh)
            if b and b[0] != r["article_id"] and b[1] / float(len(sh)) >= SUMMARY_CONTAINMENT:
                hit(r, "summary", b[0], b[1] / float(len(sh)))
                continue
        if body_idx is not None and body_idx.sizes:
            body = bodies(r["url_hash"])
            if body:
                sh = shingles(body)
                if len(sh) >= BODY_MIN_SHINGLES:
                    b = body_idx.best(sh)
                    if b and b[0] != r["article_id"] and b[1] / float(len(sh)) >= BODY_CONTAINMENT:
                        hit(r, "body", b[0], b[1] / float(len(sh)))
    return out


def feed_boilerplate(conn):
    """The per feed summary boilerplate (pipeline/boilerplate.py) the audit strips before comparing summaries."""
    from pipeline import boilerplate
    return boilerplate.FeedBoilerplate(conn)


def wire_habit_outlets(conn, rows: List[Dict], looks: Dict[int, Dict]) -> Dict[str, str]:
    """Outlets with a known wire copy habit, each with the reason it qualifies."""
    habit = {o: "named" for o in WIRE_HABIT_NAMED}
    for r in rows:
        if r.get("tier") == "agency":
            habit.setdefault(r["outlet_id"], "agency")
    for o, n in conn.execute(
            """SELECT a.outlet_id, COUNT(*) FROM classifications c JOIN articles a ON a.id = c.article_id
               WHERE c.is_current = 1 AND c.category = 'A' GROUP BY 1"""):
        if n >= WIRE_HABIT_MIN_STATE_ORIGIN:
            habit.setdefault(o, "state_origin_labels")
    per_outlet = Counter(r["outlet_id"] for r in rows if r["article_id"] in looks)
    for o, n in per_outlet.items():
        if n >= LOOKALIKE_HABIT_MIN:
            habit.setdefault(o, "lookalikes")
    return habit


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------

def allocate(sizes: Dict[str, int], n: int, floor: int = MIN_PER_LANGUAGE, floor_min_pop: int = MIN_LANGUAGE_POP) -> Dict[str, int]:
    """Proportional allocation by largest remainder, with a floor for every stratum of at least
    floor_min_pop items, never more than a stratum holds."""
    total = sum(sizes.values())
    if n >= total:
        return dict(sizes)
    alloc = {k: 0 for k in sizes}
    floors = {k: min(floor, v) for k, v in sizes.items() if v >= floor_min_pop}
    if sum(floors.values()) <= n:
        alloc.update(floors)
    left = n - sum(alloc.values())
    while left > 0:
        room = {k: sizes[k] - alloc[k] for k in sizes if sizes[k] > alloc[k]}
        if not room:
            break
        rt = float(sum(room.values()))
        quotas = {k: left * v / rt for k, v in room.items()}
        given = 0
        for k, q in quotas.items():
            add = min(room[k], int(math.floor(q)))
            alloc[k] += add
            given += add
        rest = left - given
        if rest > 0:
            for k in sorted(room, key=lambda k: (-(quotas[k] - math.floor(quotas[k])), -sizes[k], k)):
                if rest == 0:
                    break
                if alloc[k] < sizes[k]:
                    alloc[k] += 1
                    rest -= 1
        if given == 0 and rest == left:
            break
        left = n - sum(alloc.values())
    return alloc


def split_language(n_lang: int, n_habit: int, n_other: int, oversample: float = OVERSAMPLE) -> Tuple[int, int]:
    """Split one language's draws between its wire habit and other substrata, the first weighted by oversample."""
    if n_habit == 0:
        return 0, min(n_lang, n_other)
    if n_other == 0:
        return min(n_lang, n_habit), 0
    share = oversample * n_habit / (oversample * n_habit + n_other)
    h = int(round(n_lang * share))
    if n_lang >= 2:
        h = max(1, min(h, n_lang - 1))
    h = min(h, n_habit)
    o = min(n_other, n_lang - h)
    h = min(n_habit, n_lang - o)
    return h, o


def draw(rows: List[Dict], habit: Dict[str, str], looks: Dict[int, Dict], n: int, seed: int,
         oversample: float = OVERSAMPLE, lookalike_share: float = LOOKALIKE_SHARE) -> Tuple[List[Dict], Dict[str, Dict]]:
    """The stratified sample and the strata it came from. Each language has up to three strata:
    language|lookalike (the article nearly repeats a state origin item), language|habit (its outlet has
    a wire copy habit) and language|other. The lookalikes get lookalike_share of the draws, spread over
    languages in proportion; the rest is allocated by language and split between habit and other."""
    strata = defaultdict(list)
    for r in rows:
        if r["article_id"] in looks:
            sub = "lookalike"
        elif r["outlet_id"] in habit:
            sub = "habit"
        else:
            sub = "other"
        strata["%s|%s" % (r["language"], sub)].append(r)
    look_by_lang = Counter(r["language"] for r in rows if r["article_id"] in looks)
    n_look = min(sum(look_by_lang.values()), int(round(n * lookalike_share)))
    look_alloc = allocate(dict(look_by_lang), n_look, floor=0)
    rest_by_lang = Counter(r["language"] for r in rows if r["article_id"] not in looks)
    lang_alloc = allocate(dict(rest_by_lang), n - n_look)
    rng = random.Random(seed)
    picked, info = [], {}
    for lang in sorted(set(lang_alloc) | set(look_alloc)):
        hk, ok, lk = "%s|habit" % lang, "%s|other" % lang, "%s|lookalike" % lang
        nh, no = split_language(lang_alloc.get(lang, 0), len(strata.get(hk, [])), len(strata.get(ok, [])), oversample)
        for key, k in ((lk, look_alloc.get(lang, 0)), (hk, nh), (ok, no)):
            pop = strata.get(key, [])
            if not pop:
                continue
            info[key] = {"population": len(pop), "drawn": k}
            if k:
                for r in rng.sample(pop, k):
                    picked.append(dict(r, stratum=key))
    rng.shuffle(picked)
    return picked, info


def cmd_sample(conn, n: int = DEFAULT_N, seed: int = 20261012, out: Optional[Path] = None,
               bodies=None) -> Dict:
    rows = population(conn)
    refs = state_origin_items(conn)
    looks = lookalikes(rows, refs, bodies=bodies, boiler=feed_boilerplate(conn))
    habit = wire_habit_outlets(conn, rows, looks)
    picked, strata = draw(rows, habit, looks, n, seed)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    out = Path(out) if out else REVIEWS_DIR / ("recall_audit_%s.csv" % today)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "url", "outlet", "language", "title", "human_category", "note"])
        for i, r in enumerate(picked, 1):
            w.writerow([i, r["url"], r["outlet_id"], r["language"], (r["title"] or "").strip(), "", ""])
    key = {
        "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "seed": seed, "n": len(picked), "oversample": oversample_note(),
        "ruleset_version": config.RULESET_VERSION,
        "population": len(rows),
        "population_by_category": dict(Counter(r["category"] for r in rows)),
        "lookalikes_in_population": len(looks),
        "wire_habit_outlets": dict(sorted(habit.items())),
        "strata": strata,
        "items": [{"sample_id": i, "article_id": r["article_id"], "url": r["url"], "stratum": r["stratum"],
                   "rule_category": r["category"], "classification_id": r["classification_id"],
                   "rule_ruleset_version": r["ruleset_version"], "lookalike": looks.get(r["article_id"])}
                  for i, r in enumerate(picked, 1)],
    }
    with open(key_path(out), "w", encoding="utf-8") as fh:
        json.dump(key, fh, indent=1, ensure_ascii=False)
    print("wrote %d articles to %s and the key to %s. Fill human_category with state origin, unchecked state "
          "sourcing, independent journalism or not about China." % (len(picked), out, key_path(out)))
    return key


def oversample_note() -> Dict:
    return {"factor": OVERSAMPLE, "lookalike_share": LOOKALIKE_SHARE, "min_per_language": MIN_PER_LANGUAGE, "min_language_population": MIN_LANGUAGE_POP,
            "named_outlets": sorted(WIRE_HABIT_NAMED), "min_state_origin_labels": WIRE_HABIT_MIN_STATE_ORIGIN,
            "min_lookalikes": LOOKALIKE_HABIT_MIN}


def key_path(csv_path: Path) -> Path:
    csv_path = Path(csv_path)
    return csv_path.with_name(csv_path.stem + ".key.json")


# ---------------------------------------------------------------------------
# compute
# ---------------------------------------------------------------------------

def normalise_category(v: str) -> Optional[str]:
    v = re.sub(r"\s+", " ", (v or "").strip().lower().replace("_", " "))
    if not v:
        return None
    return CATEGORY_ALIASES.get(v) or CATEGORY_ALIASES.get(v.replace(" ", "_"))


def wilson(p: float, n_eff: float, z: float = Z95) -> Tuple[Optional[float], Optional[float]]:
    if n_eff <= 0:
        return None, None
    d = 1 + z * z / n_eff
    centre = (p + z * z / (2 * n_eff)) / d
    half = z * math.sqrt(p * (1 - p) / n_eff + z * z / (4 * n_eff * n_eff)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def estimate(items: List[Dict], strata: Dict[str, Dict]) -> Optional[Dict]:
    """Weighted miss rate over coded items. Each coded item stands for population/coded items of its
    stratum. The interval is Wilson's on the Kish effective sample size."""
    coded = Counter(i["stratum"] for i in items)
    if not items:
        return None
    ws, ys = [], []
    for i in items:
        ws.append(strata[i["stratum"]]["population"] / float(coded[i["stratum"]]))
        ys.append(1.0 if i["human_category"] in MISS else 0.0)
    sw = sum(ws)
    p = sum(w * y for w, y in zip(ws, ys)) / sw
    n_eff = sw * sw / sum(w * w for w in ws)
    lo, hi = wilson(p, n_eff)
    pop = sum(strata[s]["population"] for s in coded)
    return {"n_coded": len(items), "n_missed": int(sum(ys)), "rate": p, "ci_low": lo, "ci_high": hi,
            "n_eff": round(n_eff, 1), "population_covered": pop, "estimated_missed": int(round(p * pop)),
            "by_human_category": dict(Counter(i["human_category"] for i in items))}


def compute_from_items(items: List[Dict], strata: Dict[str, Dict]) -> Dict:
    overall = estimate(items, strata)
    langs = defaultdict(list)
    subs = defaultdict(list)
    for i in items:
        lang, sub = i["stratum"].rsplit("|", 1)
        langs[lang].append(i)
        subs[sub].append(i)
    return {"overall": overall,
            "by_language": {l: estimate(v, strata) for l, v in sorted(langs.items())},
            "by_substratum": {s: estimate(v, strata) for s, v in sorted(subs.items())}}


def read_coded(csv_path: Path, key: Dict) -> List[Dict]:
    by_id = {str(i["sample_id"]): i for i in key["items"]}
    items = []
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            k = by_id.get(str(row.get("sample_id", "")).strip())
            if not k:
                continue
            if (row.get("url") or "").strip() != k["url"]:
                raise ValueError("sample %s: the url in the CSV does not match the key" % k["sample_id"])
            cat = normalise_category(row.get("human_category"))
            if cat is None:
                if (row.get("human_category") or "").strip():
                    raise ValueError("sample %s: unknown human_category %r" % (k["sample_id"], row["human_category"]))
                continue
            items.append(dict(k, human_category=cat, note=(row.get("note") or "").strip()))
    return items


def cmd_compute(conn, csv_path: Path) -> Dict:
    csv_path = Path(csv_path)
    with open(key_path(csv_path), "r", encoding="utf-8") as fh:
        key = json.load(fh)
    items = read_coded(csv_path, key)
    res = compute_from_items(items, key["strata"])
    ov = res["overall"]
    if not ov:
        print("no coded rows")
        return res
    print("coded %d of %d sampled articles the rules settled without the model; %d placed in state origin or "
          "unchecked state sourcing. Weighted miss rate %.1f percent (95 percent interval %.1f to %.1f), about %d of "
          "%d articles." % (ov["n_coded"], key["n"], ov["n_missed"], 100 * ov["rate"], 100 * ov["ci_low"],
                            100 * ov["ci_high"], ov["estimated_missed"], ov["population_covered"]))
    for lang, e in res["by_language"].items():
        print("  %s: %d of %d, %.1f percent (%.1f to %.1f)" % (lang, e["n_missed"], e["n_coded"], 100 * e["rate"],
                                                            100 * e["ci_low"], 100 * e["ci_high"]))
    ensure_table(conn)
    try:
        sample_file = str(csv_path.resolve().relative_to(config.ROOT))
    except ValueError:
        sample_file = str(csv_path)
    conn.execute(
        """INSERT INTO recall_audits(computed_at, sample_file, sampled_at, seed, ruleset_version, population,
               n_coded, n_missed, miss_rate, ci_low, ci_high, details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), sample_file, key.get("created_at"),
         key.get("seed"), key.get("ruleset_version"), key.get("population") or ov["population_covered"],
         ov["n_coded"], ov["n_missed"], ov["rate"], ov["ci_low"], ov["ci_high"], json.dumps(res)))
    conn.commit()
    return res


def latest(conn) -> Optional[Dict]:
    """The most recent audit, for the export's meta and METHODOLOGY.md, or None when none has run."""
    try:
        r = conn.execute("SELECT * FROM recall_audits ORDER BY computed_at DESC, id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    if not r:
        return None
    details = json.loads(r["details"] or "{}")
    by_lang = {l: {"n": e["n_coded"], "missed": e["n_missed"], "rate": e["rate"], "ci_low": e["ci_low"], "ci_high": e["ci_high"]}
               for l, e in (details.get("by_language") or {}).items() if e}
    return {"computed_at": r["computed_at"], "sample_file": r["sample_file"], "sampled_at": r["sampled_at"],
            "ruleset_version": r["ruleset_version"], "population": r["population"], "n_coded": r["n_coded"],
            "n_missed": r["n_missed"], "rate": r["miss_rate"], "ci_low": r["ci_low"], "ci_high": r["ci_high"],
            "by_language": by_lang}


def latest_quietly(conn) -> Optional[Dict]:
    try:
        return latest(conn)
    except Exception as exc:  # noqa: BLE001
        print("recall audit meta failed: %s: %s" % (type(exc).__name__, exc))
        return None


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------

def cmd_describe(conn, bodies=None, top: int = 20) -> Dict:
    rows = population(conn)
    refs = state_origin_items(conn)
    looks = lookalikes(rows, refs, bodies=bodies, boiler=feed_boilerplate(conn))
    habit = wire_habit_outlets(conn, rows, looks)
    routed = Counter(r[0] for r in conn.execute(
        "SELECT a.outlet_id FROM classifications c JOIN articles a ON a.id=c.article_id WHERE c.is_current=1 AND c.method='llm'"))
    labelled = Counter(r[0] for r in conn.execute(
        "SELECT a.outlet_id FROM classifications c JOIN articles a ON a.id=c.article_id WHERE c.is_current=1"))
    print("articles labelled independent journalism or not relevant by rules, never sent to the model: %d" % len(rows))
    print("  by category: %s" % dict(Counter(r["category"] for r in rows)))
    print("  by language: %s" % ", ".join("%s %d" % kv for kv in Counter(r["language"] for r in rows).most_common(top)))
    print("outlets with the most, with the share of each outlet's labels that came from the model:")
    for o, n in Counter(r["outlet_id"] for r in rows).most_common(top):
        print("  %-24s %6d  model %4.1f%%%s" % (o, n, 100.0 * routed[o] / max(1, labelled[o]), "  habit:" + habit[o] if o in habit else ""))
    print("lookalikes of a state origin item: %d (%s)" % (len(looks), dict(Counter(v["by"] for v in looks.values()))))
    for o, n in Counter(r["outlet_id"] for r in rows if r["article_id"] in looks).most_common(top):
        print("  %-24s %4d" % (o, n))
    return {"population": len(rows), "lookalikes": looks, "habit": habit}


def _bodies_loader():
    from pipeline import store
    return store.load_body


def _open_readonly(path: Path):
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main(argv=None):
    ap = argparse.ArgumentParser(description="Recall audit of the candidate gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=DEFAULT_N)
    s.add_argument("--seed", type=int, default=20261012)
    s.add_argument("--out")
    s.add_argument("--bodies", action="store_true", help="also compare bodies where they are stored locally")
    c = sub.add_parser("compute")
    c.add_argument("--csv", required=True)
    d = sub.add_parser("describe")
    d.add_argument("--bodies", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "compute":
        from pipeline import store
        cmd_compute(store.connect(), Path(args.csv))
        return
    conn = _open_readonly(config.DB_PATH)
    loader = _bodies_loader() if args.bodies else None
    if args.cmd == "sample":
        cmd_sample(conn, args.n, args.seed, Path(args.out) if args.out else None, bodies=loader)
    else:
        cmd_describe(conn, bodies=loader)


if __name__ == "__main__":
    main()
