"""Agreement study. Draw a fixed random sample of model labelled articles, blank
the labels, produce a CSV for hand coding, and compute Cohen's kappa between the
model's labels and the hand labels.

Unchecked state sourcing counts are published only on the strength of this study
(owner's decision, 12 October 2026, after an outside review): a rerun of the same
model, or a second model, measures whether the judgement is stable, not whether
it is correct, so pipeline/second_rater.py is a stability check and nothing more.

Usage:
  python -m pipeline.agreement sample
      the fixed hand coding sample: about 200 articles for each language with at
      least 300 model labelled articles, stratified by language and, within each
      language, by the model's current category, seeded so the same database gives
      the same draw. Written to data/reviews/human_sample_<date>.csv. Coders work
      blind: the CSV carries no model label. See data/reviews/CODING_GUIDE.md.
  python -m pipeline.agreement sample --n 100 --out review/agreement_2026-09-03.csv
      the older pooled sample, kept for the record
  python -m pipeline.agreement compute --csv data/reviews/human_sample_2026-10-12.csv [--partial] [--dry-run]

The sample is frozen in a sidecar key file at sample time, article id against the
id of the classification row then current, so a later reclassification cannot
change what the study compares against. The human sample's key holds the row ids
only, never the categories: classification rows are never deleted, and compute
reads the category from the row, so the key can sit beside the CSV without
unblinding anyone who opens it.

compute reads the filled CSV, computes kappa across all four categories and
kappa on the B versus C distinction alone (over the articles either coder
placed in B or C), stores the result in agreement_studies, and records every
hand label as a human review so the map's reviewed mode can use it.
"""
import argparse
import csv
import datetime as dt
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pipeline import config, store

CATEGORIES = ["A", "B", "C", "not_relevant"]


# A binary kappa is reported only when both raters placed at least this many items in the class the
# study is about (B, unverified relay). The prompt tells the model to answer C when it is uncertain,
# so a sample with almost no B is the expected case, not a freak one, and on such a sample the
# formula returns a number that says nothing: 300 pairs of notB against notB came back as 1.0, which
# cleared the publication threshold on a study that never tested the distinction, while 299
# agreements and one disagreement came back as 0.0 and withheld counts that were fine. Ten on each
# side is the point below which one item moves the figure across the whole of its range.
MIN_POSITIVE_FOR_KAPPA = 10

# The fixed hand coding sample. About this many articles for each qualifying language, so a
# per language kappa rests on a sample a person can code in a few days, and every language large
# enough to move a published count gets its own check.
HUMAN_SAMPLE_PER_LANGUAGE = 200
# A language qualifies for its own stratum once the model has labelled at least this many of its
# articles. Below it a 200 item draw would take two thirds of the pool or more, and the language is
# covered by the overall kappa only.
HUMAN_SAMPLE_MIN_LABELLED = 300
HUMAN_SAMPLE_SEED = "human-sample-2026-10"
HUMAN_SAMPLE_STRATA = "language, then the model's current category within each language"
REVIEWS_DIR = config.ROOT / "data" / "reviews"
HUMAN_SAMPLE_COLUMNS = ["sample_id", "article_id", "url", "outlet", "language", "title",
                        "human_category", "coder", "note"]
# What coders write in human_category. compute also accepts the codebook's internal codes.
CODER_LABELS = {"state_origin": "A", "unchecked_state_sourcing": "B", "independent_journalism": "C",
                "not_about_china": "not_relevant"}


def cohens_kappa(pairs: List[Tuple[str, str]], min_positive: int = 0, positive=None) -> Optional[float]:
    """Cohen's kappa for two raters over the same items, or None when the sample cannot support one.

    None, never a number, when: there are no pairs; both raters between them used a single label,
    where the formula is 0/0 and perfect agreement on a constant sample is not a finding; or
    min_positive is set and either rater used `positive` fewer than min_positive times."""
    n = len(pairs)
    if n == 0:
        return None
    labels = sorted({a for a, _ in pairs} | {b for _, b in pairs}, key=str)
    if len(labels) < 2:
        return None
    if min_positive:
        if sum(1 for a, _ in pairs if a == positive) < min_positive:
            return None
        if sum(1 for _, b in pairs if b == positive) < min_positive:
            return None
    po = sum(1 for a, b in pairs if a == b) / float(n)
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    pe = sum((ca[l] / float(n)) * (cb[l] / float(n)) for l in labels)
    if pe >= 1.0:       # a marginal wholly inside one label; kappa is undefined
        return None
    return (po - pe) / (1.0 - pe)


def kappa_shortfall(pairs: List[Tuple[str, str]], positive="B", min_positive: int = MIN_POSITIVE_FOR_KAPPA) -> Optional[str]:
    """Why a binary kappa cannot be reported on these pairs, in a sentence, or None when it can."""
    if not pairs:
        return "no pairs in the comparison"
    a = sum(1 for x, _ in pairs if x == positive)
    b = sum(1 for _, y in pairs if y == positive)
    if a < min_positive or b < min_positive:
        return ("%d of %d pairs carry %s from the first rater and %d from the second; at least %d on each side "
                "are needed before a kappa on that distinction means anything" % (a, len(pairs), positive, b, min_positive))
    if len({x for x, _ in pairs} | {y for _, y in pairs}) < 2:
        return "both raters used a single label, so there is no distinction to measure"
    return None


def stratified_sample(rows: List[Dict], n: int, seed: Optional[int] = None) -> List[Dict]:
    """Roughly equal allocation across machine categories, remainder filled proportionally.
    Categories with too few items contribute everything they have."""
    rng = random.Random(seed)
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    for v in by_cat.values():
        rng.shuffle(v)
    cats = [c for c in CATEGORIES if by_cat.get(c)]
    if not cats:
        return []
    picked = []
    quota = {c: n // len(cats) for c in cats}
    for c in cats:
        picked.extend(by_cat[c][:quota[c]])
        by_cat[c] = by_cat[c][quota[c]:]
    leftovers = [r for c in cats for r in by_cat[c]]
    rng.shuffle(leftovers)
    picked.extend(leftovers[:max(0, n - len(picked))])
    rng.shuffle(picked)
    return picked


def classified_rows(conn) -> List[Dict]:
    rows = conn.execute(
        """SELECT a.id, a.title, a.url, a.url_hash, a.country, a.outlet_id, c.category, c.method, c.confidence, c.id AS cid
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 AND a.status='classified'"""
    ).fetchall()
    return [dict(r) for r in rows]


def human_pool(conn) -> List[Dict]:
    """What the hand coding study checks: articles carrying a current model label and an extracted
    body. Labels the rules gave are out, because the question is whether the model's judgement holds."""
    rows = conn.execute(
        """SELECT a.id, a.title, a.url, a.language, a.outlet_id, o.name AS outlet, c.category, c.id AS cid
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           LEFT JOIN outlets o ON o.id=a.outlet_id
           WHERE c.method='llm' AND a.gate_relevant=1 AND COALESCE(a.body_chars, 0) > 0
           ORDER BY a.id"""
    ).fetchall()
    return [dict(r) for r in rows]


def qualifying_languages(rows: List[Dict], min_labelled: int = HUMAN_SAMPLE_MIN_LABELLED) -> Dict[str, int]:
    """Languages with at least min_labelled model labelled articles, largest first, with their pool size."""
    counts = Counter(r["language"] for r in rows if r.get("language"))
    return {lang: n for lang, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n >= min_labelled}


def human_sample(rows: List[Dict], per_language: int = HUMAN_SAMPLE_PER_LANGUAGE,
                 min_labelled: int = HUMAN_SAMPLE_MIN_LABELLED, seed: str = HUMAN_SAMPLE_SEED) -> List[Dict]:
    """The fixed sample: each qualifying language is a stratum of its own, and within it the draw is
    stratified by the model's current category, equal allocation with the remainder filled at random,
    so unchecked state sourcing, rare in most languages, is present in numbers that can test the
    distinction. Every language has its own seed, so adding a language leaves the others unchanged."""
    out = []
    for lang in qualifying_languages(rows, min_labelled):
        pool = sorted((r for r in rows if r.get("language") == lang), key=lambda r: r["id"])
        out.extend(stratified_sample(pool, per_language, seed="%s:%s" % (seed, lang)))
    return out


def cmd_human_sample(conn, out: Path, per_language: int = HUMAN_SAMPLE_PER_LANGUAGE,
                     min_labelled: int = HUMAN_SAMPLE_MIN_LABELLED, seed: str = HUMAN_SAMPLE_SEED) -> Dict:
    """Write the blind coding CSV and its key. Prints sizes per language and nothing about the model's
    labels, because the person running it is one of the coders."""
    rows = human_pool(conn)
    langs = qualifying_languages(rows, min_labelled)
    picked = human_sample(rows, per_language, min_labelled, seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    sampled = Counter()
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HUMAN_SAMPLE_COLUMNS)
        for r in picked:
            sampled[r["language"]] += 1
            w.writerow(["%s-%03d" % (r["language"], sampled[r["language"]]), r["id"], r["url"],
                        r.get("outlet") or r.get("outlet_id") or "", r["language"], r["title"] or "", "", "", ""])
    key = {"kind": "human_sample", "created_at": store.utcnow(), "seed": seed, "per_language": per_language,
           "min_labelled": min_labelled, "stratified_on": HUMAN_SAMPLE_STRATA,
           "ruleset_version": config.RULESET_VERSION, "llm_model": config.LLM_MODEL, "n": len(picked),
           "languages": {lang: {"pool": langs[lang], "sampled": sampled[lang]} for lang in langs},
           # Ids only. The category is read from the classification row when the study is computed.
           "items": [{"article_id": r["id"], "classification_id": r["cid"], "language": r["language"]} for r in picked]}
    key_path = out.with_suffix(".key.json")
    with open(key_path, "w", encoding="utf-8") as fh:
        json.dump(key, fh, indent=1)
    print("wrote %d items to %s (classification ids frozen in %s)" % (len(picked), out, key_path))
    for lang in langs:
        print("  %s: %d sampled from %d model labelled articles" % (lang, sampled[lang], langs[lang]))
    print("Code human_category blind, following data/reviews/CODING_GUIDE.md, then run compute.")
    return {"n": len(picked), "languages": key["languages"], "out": str(out), "key": str(key_path)}


def latest_sample_info(reviews_dir: Optional[Path] = None) -> Optional[Dict]:
    """The newest human sample key in data/reviews, summarised for the site, or None."""
    keys = sorted((reviews_dir or REVIEWS_DIR).glob("human_sample_*.key.json"))
    if not keys:
        return None
    try:
        key = json.loads(keys[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {"file": keys[-1].name.replace(".key.json", ".csv"), "created_at": key.get("created_at"),
            "n": key.get("n"), "per_language": key.get("per_language"), "min_labelled": key.get("min_labelled"),
            "stratified_on": key.get("stratified_on"),
            "languages": {lang: (v or {}).get("sampled") for lang, v in (key.get("languages") or {}).items()}}


def human_study_status(conn, reviews_dir: Optional[Path] = None) -> Dict:
    """Where the hand coding study stands. passed is the only state that publishes unchecked state
    sourcing: kappa on unchecked sourcing versus independent journalism at or above the threshold,
    with at least MIN_POSITIVE_FOR_KAPPA on each side, on a sample coded to the end."""
    row = conn.execute("SELECT * FROM agreement_studies ORDER BY id DESC LIMIT 1").fetchone()
    out = {"threshold": config.KAPPA_WARNING_THRESHOLD, "min_positive": MIN_POSITIVE_FOR_KAPPA,
           "per_language": HUMAN_SAMPLE_PER_LANGUAGE, "min_labelled": HUMAN_SAMPLE_MIN_LABELLED,
           "stratified_on": HUMAN_SAMPLE_STRATA, "sample": latest_sample_info(reviews_dir),
           "n": None, "kappa_bc": None, "n_bc": None, "computed_at": None, "coded": None, "sampled": None}
    if not row:
        out["state"] = "sample_drawn" if out["sample"] else "not_started"
    else:
        try:
            details = json.loads(row["details"] or "{}") or {}
        except ValueError:
            details = {}
        out.update(n=row["sample_size"], kappa_bc=row["kappa_bc"], n_bc=row["n_bc"], computed_at=row["computed_at"],
                   coded=details.get("coded"), sampled=details.get("sampled"))
        if details.get("sample_complete") is False:
            out["state"] = "incomplete"
        elif row["kappa_bc"] is None:
            out["state"] = "inconclusive"
            out["inconclusive"] = details.get("kappa_bc_inconclusive")
        elif row["kappa_bc"] >= config.KAPPA_WARNING_THRESHOLD:
            out["state"] = "passed"
        else:
            out["state"] = "failed"
    out["passed"] = out["state"] == "passed"
    return out


def provisional_reason(status: Dict) -> str:
    """Why unchecked state sourcing is still provisional, in one or two sentences for the site."""
    rule = ("The counts stay provisional until the owner and two research assistants have hand coded a fixed random "
            "sample, about %d articles in each language with at least %d model labelled articles, stratified by "
            "language and, within each language, by the model's current category, and Cohen's kappa between their "
            "codes and the model's on unchecked state sourcing versus independent journalism is at least %.1f, with "
            "at least %d articles on each side, overall and in every language with enough items."
            % (status["per_language"], status["min_labelled"], status["threshold"], status["min_positive"]))
    state = status.get("state")
    if state == "incomplete":
        return ("The hand coding study is under way: %s of %s sampled articles are coded. %s"
                % (status.get("coded") or 0, status.get("sampled") or "the", rule))
    if state == "inconclusive":
        return ("The hand coding study reported no kappa on that distinction: %s. %s"
                % (status.get("inconclusive") or "the sample held too few items on one side", rule))
    if state == "sample_drawn":
        return "No hand coding study has reported yet; the sample is drawn and being coded. " + rule
    return "No hand coding study has reported yet. " + rule


def cmd_sample(conn, n: int, out: Path, seed: Optional[int]) -> Dict:
    rows = classified_rows(conn)
    picked = stratified_sample(rows, n, seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "article_id", "headline", "url", "excerpt", "human_category", "human_note"])
        for i, r in enumerate(picked, 1):
            body = store.load_body(r["url_hash"]) or ""
            w.writerow([i, r["id"], r["title"] or "", r["url"], body[:1500].replace("\n", " "), "", ""])
    key = {"created_at": store.utcnow(), "n": len(picked), "seed": seed, "ruleset_version": config.RULESET_VERSION,
           "items": [{"article_id": r["id"], "machine_category": r["category"], "method": r["method"],
                      "confidence": r["confidence"], "classification_id": r["cid"]} for r in picked]}
    key_path = out.with_suffix(".key.json")
    with open(key_path, "w", encoding="utf-8") as fh:
        json.dump(key, fh, indent=1)
    dist = Counter(r["category"] for r in picked)
    print("wrote %d items to %s (machine labels frozen in %s)" % (len(picked), out, key_path))
    print("machine label distribution in sample: %s" % dict(dist))
    print("Hand code the human_category column with A, B, C or not_relevant, then run compute.")
    return {"n": len(picked), "distribution": dict(dist)}


def compute_from_pairs(items: List[Dict]) -> Dict:
    pairs = [(it["machine_category"], it["human_category"]) for it in items]
    kappa_all = cohens_kappa(pairs)
    bc = [(m, h) for m, h in pairs if m in ("B", "C") or h in ("B", "C")]
    bc_binary = [("B" if m == "B" else "notB", "B" if h == "B" else "notB") for m, h in bc]
    kappa_bc = cohens_kappa(bc_binary, min_positive=MIN_POSITIVE_FOR_KAPPA, positive="B")
    kappa_bc_inconclusive = kappa_shortfall(bc_binary) if kappa_bc is None else None
    confusion = defaultdict(Counter)
    for m, h in pairs:
        confusion[m][h] += 1
    # One category against the rest, for every category either coder used. A single kappa across
    # four categories hides where the disagreement sits.
    by_category = {}
    for cat in CATEGORIES:
        binary = [(m == cat, h == cat) for m, h in pairs]
        if any(m or h for m, h in binary):
            by_category[cat] = {"kappa": cohens_kappa(binary), "n_machine": sum(1 for m, _ in binary if m),
                                "n_human": sum(1 for _, h in binary if h)}
    # The unverified relay versus independent journalism judgement, per language of the article.
    bc_by_language = {}
    for lang in sorted({it.get("language") for it in items if it.get("language")}):
        sub = [("B" if it["machine_category"] == "B" else "notB", "B" if it["human_category"] == "B" else "notB")
               for it in items if it.get("language") == lang
               and (it["machine_category"] in ("B", "C") or it["human_category"] in ("B", "C"))]
        bc_by_language[lang] = {"kappa": cohens_kappa(sub, min_positive=MIN_POSITIVE_FOR_KAPPA, positive="B"),
                                "n": len(sub), "n_b": sum(1 for a, _ in sub if a == "B")}
    return {"kappa_all": kappa_all, "kappa_bc": kappa_bc, "kappa_bc_inconclusive": kappa_bc_inconclusive,
            "kappa_min_positive": MIN_POSITIVE_FOR_KAPPA, "n": len(pairs), "n_bc": len(bc),
            "agreement": sum(1 for m, h in pairs if m == h) / float(len(pairs)) if pairs else None,
            "confusion": {m: dict(c) for m, c in confusion.items()},
            "kappa_by_category": by_category, "bc_by_language": bc_by_language}


def coder_category(value: str) -> Optional[str]:
    """A human_category cell as a codebook code, or None when it is blank or not a category."""
    raw = (value or "").strip()
    if raw in CATEGORIES:
        return raw
    norm = raw.lower().replace(" ", "_").replace("-", "_")
    if norm in CODER_LABELS:
        return CODER_LABELS[norm]
    return raw.upper() if raw.upper() in ("A", "B", "C") else None


def _machine_category(conn, item: Dict, against: str = "frozen") -> Optional[str]:
    """The model's label as frozen at sample time. The human sample's key holds only the classification
    row id, and rows are never deleted, so the label is read from that row. against="current" compares
    with the article's current model label instead, for a sample drawn before a codebook change whose
    reclassification has since finished: the coders apply the codebook as it stands now."""
    if against == "current":
        row = conn.execute("SELECT category FROM classifications WHERE article_id=? AND is_current=1 AND method='llm'",
                           (item["article_id"],)).fetchone()
        return row[0] if row else None
    if item.get("machine_category"):
        return item["machine_category"]
    row = conn.execute("SELECT category FROM classifications WHERE id=?", (item.get("classification_id"),)).fetchone()
    return row[0] if row else None


def cmd_compute(conn, csv_path: Path, reviewer: str, record: bool = True, partial: bool = False,
                against: str = "frozen") -> Dict:
    """Read the coded CSV, compute kappa against the frozen model labels, and record one agreement_studies
    row, whose details carry the per language results the export's language veto reads, plus one
    human_reviews row per coded article. A sample not yet coded to the end is not recorded unless partial
    is set, and a partial study never publishes: export treats it as incomplete."""
    key_path = csv_path.with_suffix(".key.json")
    with open(key_path, "r", encoding="utf-8") as fh:
        key = json.load(fh)
    machine = {it["article_id"]: it for it in key["items"]}
    items, accounted, coders = [], set(), set()
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                aid = int(row.get("article_id") or "")
            except ValueError:
                continue
            if aid not in machine or aid in accounted:
                continue
            note = (row.get("note") or row.get("human_note") or "").strip()
            h = coder_category(row.get("human_category"))
            if h is None:
                if note:        # left blank with a reason, such as a page that no longer loads
                    accounted.add(aid)
                continue
            m = _machine_category(conn, machine[aid], against)
            if m is None:
                continue
            accounted.add(aid)
            language = machine[aid].get("language")
            if not language:   # key files from before per-language kappa carry no language
                found = conn.execute("SELECT language FROM articles WHERE id=?", (aid,)).fetchone()
                language = found[0] if found else None
            coder = (row.get("coder") or "").strip() or reviewer
            coders.add(coder)
            items.append({"article_id": aid, "machine_category": m, "human_category": h,
                          "classification_id": machine[aid].get("classification_id"), "note": note,
                          "language": language, "coder": coder})
    res = compute_from_pairs(items)
    res["sampled"], res["coded"] = len(machine), len(items)
    res["complete"] = len(accounted) >= len(machine)
    coverage = defaultdict(lambda: {"coded": 0, "sampled": 0})
    for it in machine.values():
        coverage[it.get("language") or "unknown"]["sampled"] += 1
    for it in items:
        coverage[it.get("language") or "unknown"]["coded"] += 1
    res["coded_by_language"] = dict(coverage)
    if not items:
        print("no coded rows found in %s" % csv_path)
        return res
    print("coded items: %d of %d sampled%s" % (len(items), len(machine), "" if res["complete"] else " (sample not finished)"))
    for lang, v in sorted(coverage.items()):
        k = (res["bc_by_language"].get(lang) or {}).get("kappa")
        print("  %s: %d of %d coded, kappa on unchecked sourcing versus independent %s"
              % (lang, v["coded"], v["sampled"], "%.3f" % k if k is not None else "undefined"))
    print("coded items: %d" % res["n"])
    print("agreement: %.3f" % res["agreement"])
    print("Cohen's kappa, all categories: %s" % ("%.3f" % res["kappa_all"] if res["kappa_all"] is not None else "undefined"))
    print("Cohen's kappa, B versus C (n=%d): %s" % (res["n_bc"], "%.3f" % res["kappa_bc"] if res["kappa_bc"] is not None else "undefined"))
    if res["kappa_bc"] is None:
        print("  inconclusive: %s" % res["kappa_bc_inconclusive"])
    print("confusion (machine -> human): %s" % json.dumps(res["confusion"]))
    if res["kappa_bc"] is not None and res["kappa_bc"] < config.KAPPA_WARNING_THRESHOLD:
        print("kappa on B versus C is below %.1f. Unchecked state sourcing counts will be withheld." % config.KAPPA_WARNING_THRESHOLD)
    elif res["kappa_bc"] is not None and res["complete"]:
        print("kappa on B versus C is at or above %.1f. Unchecked state sourcing counts will be published, "
              "except in any language whose own kappa is below it." % config.KAPPA_WARNING_THRESHOLD)
    if record and not res["complete"] and not partial:
        print("not recorded: the sample is not finished. Every row needs a category, or a note saying why it has none. "
              "Pass --partial to record an interim study; it will not publish anything.")
        record = False
    if record:
        conn.execute(
            "INSERT INTO agreement_studies(computed_at, sample_size, kappa_all, kappa_bc, n_bc, details) VALUES (?,?,?,?,?,?)",
            (store.utcnow(), res["n"], res["kappa_all"], res["kappa_bc"], res["n_bc"],
             json.dumps({"method": "human_coding", "csv": str(csv_path), "confusion": res["confusion"], "agreement": res["agreement"],
                         "key": str(key_path), "kappa_bc_inconclusive": res["kappa_bc_inconclusive"],
                         "kappa_min_positive": res["kappa_min_positive"],
                         # export.build_meta reads these two for the per language veto and the category table.
                         "bc_by_language": res["bc_by_language"], "kappa_by_category": res["kappa_by_category"],
                         "sample_complete": res["complete"], "sampled": res["sampled"], "coded": res["coded"],
                         "coded_by_language": res["coded_by_language"], "coders": sorted(coders),
                         "stratified_on": key.get("stratified_on"), "seed": key.get("seed"),
                         "compared_against": against})),
        )
        for it in items:
            store.insert_human_review(conn, it["article_id"], it["classification_id"], it["human_category"], it.get("coder") or reviewer,
                                      note="agreement study %s. %s" % (csv_path.name, it["note"]))
        conn.commit()
        print("recorded in agreement_studies and human_reviews")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=None, help="the older pooled sample of this size instead of the human sample")
    s.add_argument("--out", default=None)
    s.add_argument("--seed", default=None, help="default %s for the human sample" % HUMAN_SAMPLE_SEED)
    s.add_argument("--per-language", type=int, default=HUMAN_SAMPLE_PER_LANGUAGE)
    s.add_argument("--min-labelled", type=int, default=HUMAN_SAMPLE_MIN_LABELLED)
    c = sub.add_parser("compute")
    c.add_argument("--csv", required=True)
    c.add_argument("--reviewer", default="hand_coder", help="used for rows whose coder column is blank")
    c.add_argument("--partial", action="store_true", help="record a study on a sample not yet coded to the end")
    c.add_argument("--dry-run", action="store_true", help="print the figures and record nothing")
    c.add_argument("--against", choices=["frozen", "current"], default="frozen",
                   help="compare with the label frozen at sample time (default) or the article's current model label")
    args = ap.parse_args(argv)
    conn = store.connect()
    today = dt.date.today().isoformat()
    if args.cmd == "sample" and args.n:
        cmd_sample(conn, args.n, Path(args.out or "review/agreement_%s.csv" % today),
                   int(args.seed) if args.seed is not None else None)
    elif args.cmd == "sample":
        cmd_human_sample(conn, Path(args.out or REVIEWS_DIR / ("human_sample_%s.csv" % today)),
                         args.per_language, args.min_labelled, args.seed or HUMAN_SAMPLE_SEED)
    else:
        cmd_compute(conn, Path(args.csv), args.reviewer, record=not args.dry_run, partial=args.partial, against=args.against)


if __name__ == "__main__":
    main()
