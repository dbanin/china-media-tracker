"""Re-fetch article bodies for a date range so a reclassification can run.

Bodies live in data/bodies/, gitignored, so a fresh clone has none. This
re-fetches them under the same politeness rules as the original retrieval:
robots.txt, three seconds per domain, identifying user agent, paywalls left
alone. Articles that were paywalled or blocked the first time are not retried
unless --include-blocked is given.

Usage:
  python -m pipeline.rehydrate --since 2026-09-01 --until 2026-09-30
  python -m pipeline.rehydrate --since 2026-09-01 --missing-only
"""
import argparse
import datetime as dt
import json

from pipeline import fetch_articles, store


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="discovery date, inclusive, YYYY-MM-DD")
    ap.add_argument("--until", default=dt.date.today().isoformat())
    ap.add_argument("--missing-only", action="store_true", help="only articles whose body file is absent (default)")
    ap.add_argument("--include-blocked", action="store_true", help="also retry paywalled and robots-blocked articles")
    ap.add_argument("--budget-minutes", type=float, default=40)
    args = ap.parse_args(argv)
    conn = store.connect()
    statuses = ["fetched", "classified", "awaiting_llm", "llm_submitted"]
    if args.include_blocked:
        statuses += ["paywalled", "blocked_robots", "failed"]
    q = "SELECT * FROM articles WHERE gate_relevant=1 AND status IN (%s) AND discovered_at>=? AND discovered_at<? ORDER BY discovered_at" % ",".join("?" * len(statuses))
    rows = conn.execute(q, statuses + [args.since, args.until + "T23:59:59"]).fetchall()
    rows = [r for r in rows if store.load_body(r["url_hash"]) is None]
    print("%d articles need bodies" % len(rows))
    import time
    deadline = time.time() + args.budget_minutes * 60
    counts = {"fetched": 0, "paywalled": 0, "failed": 0, "blocked_robots": 0, "skipped_deadline": 0}
    for r in rows:
        if time.time() > deadline:
            counts["skipped_deadline"] += 1
            continue
        prior_status = r["status"]
        res = fetch_articles.process_article(store.connect, r)
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        # a rehydrated article keeps its classification state; only the body came back
        if res["status"] == "fetched" and prior_status in ("classified", "awaiting_llm", "llm_submitted"):
            store.update_article(conn, r["id"], status=prior_status)
            conn.commit()
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
