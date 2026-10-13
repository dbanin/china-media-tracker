"""Audit the relevance gate's false negative rate.

The gate is deliberately loose and its decision is recorded on every item.
Rejected items are pruned after config.GATED_OUT_RETENTION_DAYS, except a
random share (config.GATE_AUDIT_KEEP_SHARE, decided by a hash of the url) that
is kept permanently for this audit. `sample` draws from that retained set,
stratified by language, over any date range, and writes a CSV for hand
checking; `compute` reads the filled CSV and reports the false negative rate,
weighted back to each language's share of the rejections.

  python -m pipeline.gate_audit sample --n 200 --since 2026-09-01 --until 2026-10-01 --out review/gate_audit.csv
  python -m pipeline.gate_audit compute --csv review/gate_audit.csv
"""
import argparse
import csv
import random
from pathlib import Path
from typing import Dict, List, Optional

from pipeline import store

CSV_FIELDS = ["article_id", "outlet_id", "country", "language", "discovered_at", "gate_terms", "title", "summary",
              "url", "stratum_size", "weight", "concerns_china"]


def allocate(sizes: Dict[str, int], n: int, min_per_stratum: int = 0) -> Dict[str, int]:
    """Split n draws across strata in proportion to their size (largest remainder), then raise
    each stratum to min_per_stratum where it has that many rows. The weights written with each
    row undo the over-sampling of small strata."""
    total = sum(sizes.values())
    if not total or n <= 0:
        return {k: 0 for k in sizes}
    n = min(n, total)
    exact = {k: n * v / total for k, v in sizes.items()}
    alloc = {k: int(x) for k, x in exact.items()}
    for k in sorted(sizes, key=lambda k: (-(exact[k] - alloc[k]), k))[: n - sum(alloc.values())]:
        alloc[k] += 1
    return {k: min(sizes[k], max(a, min_per_stratum)) for k, a in alloc.items()}


def retained_rows(conn, since: Optional[str] = None, until: Optional[str] = None) -> List:
    """Gated-out items in the permanent audit set discovered in [since, until)."""
    store.mark_gate_audit_keep(conn)   # rejections from the last few days, not yet decided by a prune
    q = ("SELECT id, outlet_id, country, language, discovered_at, gate_terms, title, summary, url FROM articles "
         "WHERE gate_relevant=0 AND status='gated_out' AND audit_keep=1")
    args = []
    if since:
        q += " AND discovered_at>=?"
        args.append(since)
    if until:
        q += " AND discovered_at<?"
        args.append(until)
    return conn.execute(q + " ORDER BY id", args).fetchall()


def cmd_sample(conn, n, out, seed=None, since=None, until=None, min_per_language=5):
    rows = retained_rows(conn, since, until)
    by_lang: Dict[str, List] = {}
    for r in rows:
        by_lang.setdefault(r["language"] or "", []).append(r)
    alloc = allocate({k: len(v) for k, v in by_lang.items()}, n, min_per_language)
    rng = random.Random(seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_FIELDS)
        for lang in sorted(by_lang):
            k = alloc.get(lang, 0)
            if not k:
                continue
            size = len(by_lang[lang])
            for r in sorted(rng.sample(by_lang[lang], k), key=lambda r: r["id"]):
                w.writerow([r["id"], r["outlet_id"], r["country"], r["language"], r["discovered_at"], r["gate_terms"] or "",
                            r["title"] or "", (r["summary"] or "")[:400], r["url"], size, "%.4f" % (size / k), ""])
                written += 1
    print("wrote %d of %d retained gated-out items (%s to %s, %d languages) to %s. Fill concerns_china with yes or no."
          % (written, len(rows), since or "start", until or "now", sum(1 for v in alloc.values() if v), out))
    return written


def cmd_compute(csv_path):
    n = yes = 0
    wn = wyes = 0.0
    by_lang = {}
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            v = (row.get("concerns_china") or "").strip().lower()
            if v not in ("yes", "no"):
                continue
            n += 1
            wt = float(row.get("weight") or 1)   # files written before stratification carry no weight
            wn += wt
            wyes += wt if v == "yes" else 0
            by_lang.setdefault(row["language"], [0, 0])
            by_lang[row["language"]][1] += 1
            if v == "yes":
                yes += 1
                by_lang[row["language"]][0] += 1
    if not n:
        print("no coded rows")
        return
    print("coded %d gated-out items; %d concerned China; false negative rate among rejected items %.1f percent" % (n, yes, 100.0 * yes / n))
    print("weighted to each language's share of the retained rejections: %.1f percent" % (100.0 * wyes / wn))
    for lang, (y, t) in sorted(by_lang.items(), key=lambda kv: -kv[1][1]):
        if t >= 5:
            print("  %s: %d of %d" % (lang, y, t))


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample"); s.add_argument("--n", type=int, default=200); s.add_argument("--out", default="review/gate_audit.csv"); s.add_argument("--seed", type=int)
    s.add_argument("--since", help="first discovery date included, YYYY-MM-DD"); s.add_argument("--until", help="discovery date excluded, YYYY-MM-DD")
    s.add_argument("--min-per-language", type=int, default=5, help="draw at least this many from each language that has them")
    c = sub.add_parser("compute"); c.add_argument("--csv", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "sample":
        cmd_sample(store.connect(), args.n, Path(args.out), args.seed, args.since, args.until, args.min_per_language)
    else:
        cmd_compute(Path(args.csv))


if __name__ == "__main__":
    main()
