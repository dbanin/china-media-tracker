"""Terminal review tool for low-confidence cases.

Queue membership: every article whose current label is B with confidence below
config.REVIEW_CONFIDENCE_THRESHOLD, and every A candidate resolved by the LLM
rather than by rules, that has no human review yet. The human label goes to a
separate table and never overwrites the machine label.

Usage:
  python -m review.queue                 # review the queue
  python -m review.queue --all           # any classified article without a review
  python -m review.queue --category B    # restrict to one machine category
  python -m review.queue --reviewer name
  python -m review.queue --list          # print the queue and exit

Keys: a accept, A B C set label, n not relevant, s skip, o open article in browser, q quit.
"""
import argparse
import json
import os
import sys
import textwrap
import webbrowser

from pipeline import config, store

KEYS = {"a": "accept", "A": "A", "B": "B", "C": "C", "n": "not_relevant", "s": "skip", "o": "open", "q": "quit"}


def read_key(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write(ch + "\n")
        return ch
    except Exception:
        return (sys.stdin.readline() or "q").strip()[:1] or "q"


def queue_rows(conn, everything: bool = False, category: str = None, limit: int = 500):
    q = """SELECT a.id, a.title, a.url, a.outlet_id, a.country, a.llm_trigger, a.url_hash,
                  c.id AS cid, c.category, c.confidence, c.method, c.evidence_quote, c.reasoning, c.signatures_fired,
                  c.china_sources_cited, c.independent_confirmation_present, c.confirmation_evidence
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE NOT EXISTS (SELECT 1 FROM human_reviews h WHERE h.article_id=a.id)"""
    params = []
    if category:
        q += " AND c.category=?"
        params.append(category)
    if not everything:
        q += """ AND ((c.category='B' AND c.confidence < ?)
                      OR (c.method='llm' AND a.llm_trigger LIKE '%"a_candidate": ["%'))"""
        params.append(config.REVIEW_CONFIDENCE_THRESHOLD)
    q += " ORDER BY c.category='B' DESC, c.confidence ASC, a.id ASC LIMIT ?"
    params.append(limit)
    return conn.execute(q, params).fetchall()


def show(row, index, total):
    outlet = row["outlet_id"]
    print("\n" + "=" * 78)
    print("[%d/%d] article %d  outlet %s  country %s" % (index, total, row["id"], outlet, row["country"]))
    print("-" * 78)
    print(textwrap.fill(row["title"] or "(no title)", 78))
    print(row["url"])
    print("-" * 78)
    print("proposed: %s  confidence %.2f  method %s" % (row["category"], row["confidence"], row["method"]))
    if row["signatures_fired"] and row["signatures_fired"] != "[]":
        print("signatures: %s" % row["signatures_fired"])
    if row["china_sources_cited"] and row["china_sources_cited"] != "[]":
        print("china sources cited: %s" % row["china_sources_cited"])
    if row["independent_confirmation_present"] is not None:
        print("independent confirmation: %s" % ("yes" if row["independent_confirmation_present"] else "no"))
    if row["evidence_quote"]:
        print("evidence: " + textwrap.fill(row["evidence_quote"], 78, subsequent_indent="          "))
    if row["confirmation_evidence"]:
        print("confirmation: " + textwrap.fill(row["confirmation_evidence"], 78, subsequent_indent="              "))
    if row["reasoning"]:
        print("reasoning: " + textwrap.fill(row["reasoning"], 78, subsequent_indent="           "))
    if row["llm_trigger"]:
        try:
            t = json.loads(row["llm_trigger"])
            print("routed by: %s" % ", ".join(t.get("triggers", []) + ["A candidate: " + x for x in t.get("a_candidate", [])]))
        except ValueError:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="any classified article without a human review")
    ap.add_argument("--category", choices=["A", "B", "C", "not_relevant"])
    ap.add_argument("--reviewer", default=os.environ.get("TRACKER_REVIEWER") or os.environ.get("USER") or "reviewer")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args(argv)
    conn = store.connect()
    rows = queue_rows(conn, everything=args.all, category=args.category, limit=args.limit)
    if args.list:
        for r in rows:
            print("%6d  %s  %.2f  %-18s  %s" % (r["id"], r["category"], r["confidence"], r["outlet_id"], (r["title"] or "")[:60]))
        print("%d in queue" % len(rows))
        return
    if not rows:
        print("queue is empty")
        return
    print("%d articles in the review queue. Keys: a accept, A/B/C set label, n not relevant, s skip, o open, q quit." % len(rows))
    done = 0
    i = 0
    while i < len(rows):
        r = rows[i]
        show(r, i + 1, len(rows))
        key = read_key("> ")
        action = KEYS.get(key)
        if action == "quit":
            break
        if action == "skip":
            i += 1
            continue
        if action == "open":
            webbrowser.open(r["url"])
            continue
        if action is None:
            print("unknown key")
            continue
        label = r["category"] if action == "accept" else action
        store.insert_human_review(conn, r["id"], r["cid"], label, args.reviewer,
                                  note="queue" if action == "accept" else "queue: changed from %s" % r["category"])
        conn.commit()
        done += 1
        i += 1
    print("\nrecorded %d human labels. Machine labels are untouched." % done)


if __name__ == "__main__":
    main()
