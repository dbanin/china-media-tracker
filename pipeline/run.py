"""Orchestrator. Every stage is resumable because every stage writes as it goes.

Usage:
  python -m pipeline.run discover
  python -m pipeline.run fetch --budget-minutes 20
  python -m pipeline.run classify
  python -m pipeline.run all --budget-minutes 45
  python -m pipeline.run status
"""
import argparse
import datetime as dt
import json
import sys
import time
import uuid

from pipeline import config, store


def _run_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def cmd_discover(conn, run_id, args, deadline):
    from pipeline import fetch_feeds
    pruned = store.prune_gated_out(conn)
    counts = fetch_feeds.run(conn, run_id, deadline=deadline)
    counts["pruned_gated_out"] = pruned
    print("discover:", json.dumps(counts))
    return counts


def cmd_fetch(conn, run_id, args, deadline):
    from pipeline import fetch_articles
    counts = fetch_articles.run(conn, run_id, deadline=deadline, limit=args.limit)
    print("fetch:", json.dumps(counts))
    return counts


def cmd_classify(conn, run_id, args, deadline):
    from pipeline import classify_rules
    counts = classify_rules.run(conn, run_id, deadline=deadline)
    print("classify_rules:", json.dumps(counts))
    if not args.no_llm:
        try:
            from pipeline import classify_llm
            llm_counts = classify_llm.run(conn, run_id, deadline=deadline, batch=args.batch)
            print("classify_llm:", json.dumps(llm_counts))
            counts["llm"] = llm_counts
        except ImportError:
            pass
    return counts


def cmd_status(conn, run_id, args, deadline):
    tc = store.table_counts(conn)
    print("tables:", json.dumps(tc))
    rows = conn.execute("SELECT status, COUNT(*) n FROM articles GROUP BY status ORDER BY n DESC").fetchall()
    for r in rows:
        print("  %-16s %d" % (r["status"], r["n"]))
    rows = conn.execute(
        "SELECT country, COUNT(*) n, SUM(gate_relevant) rel FROM articles GROUP BY country ORDER BY n DESC"
    ).fetchall()
    for r in rows:
        print("  %s discovered=%d relevant=%d" % (r["country"], r["n"], r["rel"] or 0))
    return tc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["discover", "fetch", "classify", "all", "status"])
    ap.add_argument("--budget-minutes", type=float, default=45.0)
    ap.add_argument("--limit", type=int, default=None, help="max articles to fetch this run")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--batch", action="store_true", help="use the Message Batches API for LLM calls")
    args = ap.parse_args(argv)

    started = time.time()
    deadline = started + args.budget_minutes * 60
    run_id = _run_id()
    conn = store.connect()
    print("run %s stage=%s budget=%.0fm" % (run_id, args.stage, args.budget_minutes))

    stages = {"discover": cmd_discover, "fetch": cmd_fetch, "classify": cmd_classify, "status": cmd_status}
    order = ["discover", "fetch", "classify"] if args.stage == "all" else [args.stage]
    summary = {}
    for name in order:
        if time.time() > deadline:
            print("budget exhausted before %s" % name)
            break
        summary[name] = stages[name](conn, run_id, args, deadline)
    conn.commit()
    conn.close()
    print("done in %.0fs" % (time.time() - started))
    return summary


if __name__ == "__main__":
    main()
