"""Audit the relevance gate's false negative rate.

The gate is deliberately loose and its decision is recorded on every item.
This draws a random sample of items the gate rejected, writes a CSV for hand
checking, and computes the false negative rate from the filled CSV.

  python -m pipeline.gate_audit sample --n 200 --out review/gate_audit.csv
  python -m pipeline.gate_audit compute --csv review/gate_audit.csv
"""
import argparse
import csv
import random
from pathlib import Path

from pipeline import store


def cmd_sample(conn, n, out, seed=None):
    rows = conn.execute(
        "SELECT id, outlet_id, country, language, title, summary, url FROM articles WHERE gate_relevant=0"
    ).fetchall()
    rng = random.Random(seed)
    picked = rng.sample(rows, min(n, len(rows)))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["article_id", "outlet_id", "country", "language", "title", "summary", "url", "concerns_china"])
        for r in picked:
            w.writerow([r["id"], r["outlet_id"], r["country"], r["language"], r["title"] or "", (r["summary"] or "")[:400], r["url"], ""])
    print("wrote %d gated-out items to %s. Fill concerns_china with yes or no." % (len(picked), out))


def cmd_compute(csv_path):
    n = yes = 0
    by_lang = {}
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            v = (row.get("concerns_china") or "").strip().lower()
            if v not in ("yes", "no"):
                continue
            n += 1
            by_lang.setdefault(row["language"], [0, 0])
            by_lang[row["language"]][1] += 1
            if v == "yes":
                yes += 1
                by_lang[row["language"]][0] += 1
    if not n:
        print("no coded rows")
        return
    print("coded %d gated-out items; %d concerned China; false negative rate among rejected items %.1f percent" % (n, yes, 100.0 * yes / n))
    for lang, (y, t) in sorted(by_lang.items(), key=lambda kv: -kv[1][1]):
        if t >= 5:
            print("  %s: %d of %d" % (lang, y, t))


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample"); s.add_argument("--n", type=int, default=200); s.add_argument("--out", default="review/gate_audit.csv"); s.add_argument("--seed", type=int)
    c = sub.add_parser("compute"); c.add_argument("--csv", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "sample":
        cmd_sample(store.connect(), args.n, Path(args.out), args.seed)
    else:
        cmd_compute(Path(args.csv))


if __name__ == "__main__":
    main()
