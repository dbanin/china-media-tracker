"""Agreement study. Draw a stratified random sample of classified articles, blank
the labels, produce a CSV for hand coding, and compute Cohen's kappa between the
machine labels and the hand labels.

Usage:
  python -m pipeline.agreement sample --n 100 --out review/agreement_2026-09-03.csv
  python -m pipeline.agreement compute --csv review/agreement_2026-09-03.csv

The sample CSV contains the article id, headline, URL, an excerpt of the body,
and an empty human_category column. It does not contain the machine label.
The machine labels are frozen in a sidecar JSON at sample time, so a later
reclassification cannot change what the study compares against.

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


def cohens_kappa(pairs: List[Tuple[str, str]]) -> Optional[float]:
    """Cohen's kappa for two raters over the same items. None when undefined."""
    n = len(pairs)
    if n == 0:
        return None
    labels = sorted({a for a, _ in pairs} | {b for _, b in pairs})
    po = sum(1 for a, b in pairs if a == b) / float(n)
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    pe = sum((ca[l] / float(n)) * (cb[l] / float(n)) for l in labels)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


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
    kappa_bc = cohens_kappa(bc_binary)
    confusion = defaultdict(Counter)
    for m, h in pairs:
        confusion[m][h] += 1
    return {"kappa_all": kappa_all, "kappa_bc": kappa_bc, "n": len(pairs), "n_bc": len(bc),
            "agreement": sum(1 for m, h in pairs if m == h) / float(len(pairs)) if pairs else None,
            "confusion": {m: dict(c) for m, c in confusion.items()}}


def cmd_compute(conn, csv_path: Path, reviewer: str, record: bool = True) -> Dict:
    key_path = csv_path.with_suffix(".key.json")
    with open(key_path, "r", encoding="utf-8") as fh:
        key = json.load(fh)
    machine = {it["article_id"]: it for it in key["items"]}
    items = []
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            h = (row.get("human_category") or "").strip()
            if h not in CATEGORIES:
                continue
            aid = int(row["article_id"])
            if aid not in machine:
                continue
            items.append({"article_id": aid, "machine_category": machine[aid]["machine_category"], "human_category": h,
                          "classification_id": machine[aid]["classification_id"], "note": row.get("human_note") or ""})
    res = compute_from_pairs(items)
    if not items:
        print("no coded rows found in %s" % csv_path)
        return res
    print("coded items: %d" % res["n"])
    print("agreement: %.3f" % res["agreement"])
    print("Cohen's kappa, all categories: %s" % ("%.3f" % res["kappa_all"] if res["kappa_all"] is not None else "undefined"))
    print("Cohen's kappa, B versus C (n=%d): %s" % (res["n_bc"], "%.3f" % res["kappa_bc"] if res["kappa_bc"] is not None else "undefined"))
    print("confusion (machine -> human): %s" % json.dumps(res["confusion"]))
    if res["kappa_bc"] is not None and res["kappa_bc"] < config.KAPPA_WARNING_THRESHOLD:
        print("kappa on B versus C is below %.1f. The interface will not display B counts as settled." % config.KAPPA_WARNING_THRESHOLD)
    if record:
        conn.execute(
            "INSERT INTO agreement_studies(computed_at, sample_size, kappa_all, kappa_bc, n_bc, details) VALUES (?,?,?,?,?,?)",
            (store.utcnow(), res["n"], res["kappa_all"], res["kappa_bc"], res["n_bc"],
             json.dumps({"csv": str(csv_path), "confusion": res["confusion"], "agreement": res["agreement"], "key": str(key_path)})),
        )
        for it in items:
            store.insert_human_review(conn, it["article_id"], it["classification_id"], it["human_category"], reviewer,
                                      note="agreement study %s. %s" % (csv_path.name, it["note"]))
        conn.commit()
        print("recorded in agreement_studies and human_reviews")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=100)
    s.add_argument("--out", default="review/agreement_%s.csv" % dt.date.today().isoformat())
    s.add_argument("--seed", type=int, default=None)
    c = sub.add_parser("compute")
    c.add_argument("--csv", required=True)
    c.add_argument("--reviewer", default="hand_coder")
    args = ap.parse_args(argv)
    conn = store.connect()
    if args.cmd == "sample":
        cmd_sample(conn, args.n, Path(args.out), args.seed)
    else:
        cmd_compute(conn, Path(args.csv), args.reviewer)


if __name__ == "__main__":
    main()
