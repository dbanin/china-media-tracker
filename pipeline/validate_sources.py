"""Check every feed in the registry. Mark dead ones inactive and log the change.

Usage:
  python -m pipeline.validate_sources            # report only
  python -m pipeline.validate_sources --apply    # mark dead feeds inactive and rewrite outlets.yaml
  python -m pipeline.validate_sources --json out.json

A feed is dead when it fails on this run and has failed on the previous two
recorded validation runs, so a single bad hour does not remove an outlet. The
first run has no history, so --apply on a fresh clone only reports.
"""
import argparse
import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from pipeline import config, registry
from pipeline.feeds_util import fetch_feed

CONSECUTIVE_FAILURES_TO_DEACTIVATE = 3


def check_outlet(outlet):
    results = []
    for url in outlet["feeds"]:
        r = fetch_feed(url)
        results.append({
            "outlet_id": outlet["id"],
            "feed_url": url,
            "ok": r["ok"],
            "status": r["status"],
            "error": r["error"],
            "entries": len(r["entries"]),
            "elapsed": round(r["elapsed"], 2),
        })
    return results


def run(apply=False, json_out=None, workers=12):
    outlets = registry.load_outlets()
    today = dt.date.today().isoformat()
    to_check = [o for o in outlets if o["active"]]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        nested = list(pool.map(check_outlet, to_check))
    results = [r for group in nested for r in group]

    # Feed health history lives in the database when it exists, so the
    # consecutive failure rule can be applied. Without history, report only.
    history = {}
    try:
        from pipeline import store
        conn = store.connect()
        for row in conn.execute(
            "SELECT feed_url, consecutive_failures FROM feed_health"
        ):
            history[row[0]] = row[1]
        for r in results:
            store.record_feed_health(conn, r["outlet_id"], r["feed_url"], r["ok"], r["error"], r["entries"])
        conn.commit()
    except Exception as exc:  # store may not exist yet on a fresh checkout
        print("feed health history unavailable (%s); reporting only" % exc, file=sys.stderr)

    dead_outlets = []
    by_outlet = {}
    for r in results:
        by_outlet.setdefault(r["outlet_id"], []).append(r)
    for oid, rs in by_outlet.items():
        all_failed = all(not r["ok"] for r in rs)
        if not all_failed:
            continue
        prior = max((history.get(r["feed_url"], 0) for r in rs), default=0)
        if prior + 1 >= CONSECUTIVE_FAILURES_TO_DEACTIVATE:
            dead_outlets.append(oid)

    ok = sum(1 for r in results if r["ok"])
    print("checked %d feeds across %d outlets: %d ok, %d failing" % (
        len(results), len(to_check), ok, len(results) - ok))
    for r in results:
        if not r["ok"]:
            print("  FAIL %-24s %s  (%s)" % (r["outlet_id"], r["feed_url"], r["error"]))

    changed = []
    if apply and dead_outlets:
        for o in outlets:
            if o["id"] in dead_outlets and o["active"]:
                o["active"] = False
                o["inactive_reason"] = "all feeds failed on %d consecutive validation runs (auto)" % CONSECUTIVE_FAILURES_TO_DEACTIVATE
                o["inactive_since"] = today
                changed.append(o["id"])
        if changed:
            registry.save_outlets(outlets)
            print("marked inactive: %s" % ", ".join(changed))
    elif dead_outlets:
        print("would mark inactive with --apply: %s" % ", ".join(dead_outlets))

    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump({"date": today, "results": results, "newly_inactive": changed,
                       "dead_candidates": dead_outlets}, fh, indent=1)
    return results, changed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    run(apply=args.apply, json_out=args.json_out, workers=args.workers)
