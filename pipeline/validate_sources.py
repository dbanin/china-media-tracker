"""Check every feed in the registry. Mark dead ones inactive and log the change.

Usage:
  python -m pipeline.validate_sources            # report only
  python -m pipeline.validate_sources --apply    # mark dead feeds inactive and rewrite outlets.yaml
  python -m pipeline.validate_sources --json out.json

An outlet is dead when every feed fails on this run, no feed has succeeded in
the last DEAD_AFTER_DAYS days of recorded checks, and at least
MIN_FAILED_CHECKS failures are on record. The hourly collect job also writes
feed health, so a count of consecutive failures alone would deactivate an
outlet after a three hour outage; the rule is about elapsed time since the
last success instead. The first run has no history, so --apply on a fresh
clone only reports.
"""
import argparse
import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from pipeline import config, registry
from pipeline.feeds_util import fetch_feed

DEAD_AFTER_DAYS = 7
MIN_FAILED_CHECKS = 3


def is_dead(results, history, today):
    """results: this run's per feed results for one outlet. history: feed_url -> row with
    last_ok, total_failures. today: ISO date. Returns (dead, last_ok_date or None)."""
    if not results or not all(not r["ok"] for r in results):
        return False, None
    last_ok = max((history.get(r["feed_url"], {}).get("last_ok") or "" for r in results), default="")
    failures = sum(history.get(r["feed_url"], {}).get("total_failures", 0) for r in results) + len(results)
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=DEAD_AFTER_DAYS)).isoformat()
    if failures < MIN_FAILED_CHECKS:
        return False, last_ok[:10] or None
    if last_ok and last_ok[:10] >= cutoff:
        return False, last_ok[:10]
    return True, last_ok[:10] or None


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
            "SELECT feed_url, last_ok, total_failures FROM feed_health"
        ):
            history[row[0]] = {"last_ok": row[1], "total_failures": row[2]}
        for r in results:
            store.record_feed_health(conn, r["outlet_id"], r["feed_url"], r["ok"], r["error"], r["entries"])
        conn.commit()
    except Exception as exc:  # store may not exist yet on a fresh checkout
        print("feed health history unavailable (%s); reporting only" % exc, file=sys.stderr)

    dead_outlets = []
    last_success = {}
    by_outlet = {}
    for r in results:
        by_outlet.setdefault(r["outlet_id"], []).append(r)
    for oid, rs in by_outlet.items():
        dead, last_ok = is_dead(rs, history, today)
        if dead:
            dead_outlets.append(oid)
            last_success[oid] = last_ok

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
                o["inactive_reason"] = ("every feed failed on every check from the GitHub Actions runner for more than %d days; last success %s (auto)"
                                        % (DEAD_AFTER_DAYS, last_success.get(o["id"]) or "none on record"))
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
