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


def _planted_scan(conn, counts, deadline=None):
    """Flag new rows against the persona list (pipeline.planted) after a stage that added rows or
    bodies. A failure is recorded in the stage's counts and never stops the run. deadline is the
    stage's wall clock deadline (time.time based); the scan compares against time.monotonic, so it
    is converted here, and a scan that stops at the deadline resumes on the next run."""
    try:
        from pipeline import planted
        mono = None if deadline is None else time.monotonic() + (deadline - time.time())
        counts["planted"] = planted.scan_quietly(conn, deadline=mono)
    except Exception as exc:  # noqa: BLE001  the detector is a side channel, never a reason to stop
        counts["planted"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        print("planted scan failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)


def _body_links(conn, counts, deadline=None):
    """Link this stage's new bodies to near-duplicates from other outlets (pipeline.body_dups), and
    fingerprint older stored bodies that have none, for at most body_dups.STAGE_SECONDS. A failure
    is recorded in the stage's counts and never stops the run."""
    try:
        from pipeline import body_dups
        counts["body_links"] = body_dups.run(conn, deadline=deadline)
    except Exception as exc:  # noqa: BLE001  the underlying item figures can wait for the next run
        counts["body_links"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        print("body link pass failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)


def _language_retag(conn):
    """Articles stored under another language than their feed's are set right before the prune, so a
    gated-out item the gate now passes in its language is re-admitted before it is deleted, and their
    residual rules labels are judged again by this run's rules stage (pipeline/language_retag.py). A
    failure is recorded and never stops the run."""
    try:
        from pipeline import language_retag
        return language_retag.run(conn)
    except Exception as exc:  # noqa: BLE001  the correction waits for the next run
        conn.rollback()
        print("language retag failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def _junk_feeds(conn):
    """Items from feeds that stopped carrying their outlet's news (registry junk_feed) are withdrawn
    with their labels, and their discovery taken off the per day counts, before the prune, so a junk
    item the gate rejected is withdrawn and kept rather than pruned (pipeline/junk_feeds.py). A
    failure is recorded and never stops the run."""
    try:
        from pipeline import junk_feeds
        return junk_feeds.run(conn)
    except Exception as exc:  # noqa: BLE001  the correction waits for the next run
        conn.rollback()
        print("junk feed correction failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def cmd_discover(conn, run_id, args, deadline):
    from pipeline import fetch_feeds
    retagged = _language_retag(conn)
    junk = _junk_feeds(conn)
    pruned = store.prune_gated_out(conn)
    counts = fetch_feeds.run(conn, run_id, deadline=deadline)
    counts["pruned_gated_out"] = pruned
    if retagged:
        counts["language_retag"] = retagged
    if junk:
        counts["junk_feeds"] = junk
    _planted_scan(conn, counts, deadline)
    print("discover:", json.dumps(counts))
    return counts


def cmd_fetch(conn, run_id, args, deadline):
    from pipeline import fetch_articles
    counts = fetch_articles.run(conn, run_id, deadline=deadline, limit=args.limit)
    _planted_scan(conn, counts, deadline)
    _body_links(conn, counts, deadline)
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
