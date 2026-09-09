"""Relay for outlets whose feeds refuse GitHub's runner addresses.

Two halves, one data flow, one database writer.

  bundle  runs on a machine on an ordinary network (the owner's Mac, from launchd).
          It polls and fetches only the outlets marked collector: self_hosted, into its
          own database (TRACKER_DB_PATH), then writes every article discovered in the
          last BUNDLE_DAYS days, bodies included, to one gzipped JSON lines file and
          force-pushes that single file to the relay branch. The branch has no history:
          each push replaces the previous commit, so the repository does not grow.

  ingest  runs on the hosted runner at the start of every collect job. It reads the
          bundle from the relay branch and inserts the articles it does not have yet,
          bodies and discovery counts included. Classification then happens on the
          hosted side like any other article. Nothing here writes the main database from
          two places at once: the relay machine only ever writes its own database and
          the branch.

Usage:
  TRACKER_COLLECTOR=self_hosted TRACKER_DB_PATH=data/relay.db python -m pipeline.relay run
  python -m pipeline.relay ingest
"""
import argparse
import base64
import datetime as dt
import gzip
import io
import json
import os
import subprocess
import sys
import time
from typing import Dict, Iterable, Optional

from pipeline import config, store

BUNDLE_DAYS = 7
RELAY_BRANCH = "relay"
BUNDLE_NAME = "bundle.jsonl.gz"
ARTICLE_FIELDS = ["url", "url_hash", "outlet_id", "country", "language", "feed_url", "title", "summary", "author",
                  "published_at", "discovered_at", "fetched_at", "status", "fail_reason", "http_status", "body_hash",
                  "body_chars", "gate_relevant", "gate_terms", "fetch_attempts", "page_labels"]


def _git(*args, check=True, input_bytes=None):
    return subprocess.run(["git", *args], cwd=str(config.ROOT), check=check, capture_output=True, input=input_bytes)


# ---------------------------------------------------------------------------
# bundle side
# ---------------------------------------------------------------------------

def build_bundle(conn, since_days: int = BUNDLE_DAYS) -> bytes:
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)).isoformat()
    rows = conn.execute("SELECT * FROM articles WHERE discovered_at >= ? ORDER BY id", (since,)).fetchall()
    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8") as fh:
        for r in rows:
            item = {k: r[k] for k in ARTICLE_FIELDS}
            body = store.load_body(r["url_hash"]) if r["status"] in ("fetched", "classified", "awaiting_llm", "paywalled") else None
            if body:
                item["body"] = base64.b64encode(gzip.compress(body.encode("utf-8"))).decode("ascii")
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return buf.getvalue()


def push_bundle(data: bytes, branch: str = RELAY_BRANCH) -> str:
    """Write one file into a history-free commit and force-push it as the relay branch."""
    blob = _git("hash-object", "-w", "--stdin", input_bytes=data).stdout.decode().strip()
    tree = _git("mktree", input_bytes=("100644 blob %s\t%s\n" % (blob, BUNDLE_NAME)).encode()).stdout.decode().strip()
    env = dict(os.environ, GIT_AUTHOR_NAME="tracker-relay", GIT_AUTHOR_EMAIL="tracker-relay@users.noreply.github.com",
               GIT_COMMITTER_NAME="tracker-relay", GIT_COMMITTER_EMAIL="tracker-relay@users.noreply.github.com")
    msg = "relay bundle %s" % store.utcnow()
    commit = subprocess.run(["git", "commit-tree", tree, "-m", msg], cwd=str(config.ROOT), check=True,
                            capture_output=True, env=env).stdout.decode().strip()
    _git("push", "--force", "--quiet", "origin", "%s:refs/heads/%s" % (commit, branch))
    return commit


def run_relay(conn, budget_minutes: float = 30.0, push: bool = True) -> Dict:
    """Discover and fetch the relay outlets, then bundle and push."""
    from pipeline import fetch_articles, fetch_feeds
    run_id = "relay-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    deadline = time.time() + budget_minutes * 60
    store.prune_gated_out(conn)
    counts = {"discover": fetch_feeds.run(conn, run_id, deadline=deadline)}
    counts["fetch"] = fetch_articles.run(conn, run_id, deadline=deadline)
    data = build_bundle(conn)
    counts["bundle_bytes"] = len(data)
    if push:
        counts["commit"] = push_bundle(data)
    else:
        out = config.ROOT / "data" / BUNDLE_NAME
        out.write_bytes(data)
        counts["written"] = str(out)
    return counts


# ---------------------------------------------------------------------------
# ingest side
# ---------------------------------------------------------------------------

def fetch_bundle(branch: str = RELAY_BRANCH) -> Optional[bytes]:
    r = _git("fetch", "--quiet", "origin", "%s:refs/remotes/origin/%s" % (branch, branch), check=False)
    if r.returncode != 0:
        return None
    r = _git("show", "origin/%s:%s" % (branch, BUNDLE_NAME), check=False)
    return r.stdout if r.returncode == 0 else None


def iter_bundle(data: bytes) -> Iterable[Dict]:
    with gzip.open(io.BytesIO(data), "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def ingest(conn, data: bytes) -> Dict:
    """Insert every article the main database does not know. Existing rows are left alone."""
    from pipeline.fetch_feeds import link_near_duplicates
    counts = {"seen": 0, "inserted": 0, "bodies": 0, "relevant": 0}
    for item in iter_bundle(data):
        counts["seen"] += 1
        if conn.execute("SELECT 1 FROM articles WHERE url_hash=?", (item["url_hash"],)).fetchone():
            continue
        cols = [k for k in ARTICLE_FIELDS if k in item]
        conn.execute("INSERT INTO articles (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" * len(cols))),
                     [item[k] for k in cols])
        new_id = conn.execute("SELECT id FROM articles WHERE url_hash=?", (item["url_hash"],)).fetchone()[0]
        counts["inserted"] += 1
        relevant = bool(item.get("gate_relevant"))
        # Discovery totals count on the day the relay found the item, not today.
        day = (item.get("discovered_at") or store.utcnow())[:10]
        conn.execute(
            """INSERT INTO daily_discovery(date, country, discovered, gate_relevant) VALUES (?,?,1,?)
               ON CONFLICT(date, country) DO UPDATE SET discovered=discovered+1, gate_relevant=gate_relevant+excluded.gate_relevant""",
            (day, item["country"], 1 if relevant else 0))
        conn.execute(
            """INSERT INTO daily_outlet_discovery(date, outlet_id, country, discovered) VALUES (?,?,?,1)
               ON CONFLICT(date, outlet_id) DO UPDATE SET discovered=discovered+1""",
            (day, item["outlet_id"], item["country"]))
        if item.get("body"):
            text = gzip.decompress(base64.b64decode(item["body"])).decode("utf-8")
            store.save_body(item["url_hash"], text)
            counts["bodies"] += 1
        if relevant:
            counts["relevant"] += 1
            link_near_duplicates(conn, new_id, item.get("title") or "", item["country"])
        if counts["inserted"] % 200 == 0:
            conn.commit()
    conn.commit()
    return counts


def cmd_ingest(conn) -> Dict:
    log_id = store.start_stage(conn, "ingest", "relay_ingest")
    data = fetch_bundle()
    if data is None:
        counts = {"bundle": "absent"}
    else:
        counts = ingest(conn, data)
    store.finish_stage(conn, log_id, True, counts)
    print("relay ingest:", json.dumps(counts))
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "bundle", "ingest"])
    ap.add_argument("--budget-minutes", type=float, default=30.0)
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)
    conn = store.connect()
    if args.cmd == "run":
        print(json.dumps(run_relay(conn, args.budget_minutes, push=not args.no_push)))
    elif args.cmd == "bundle":
        data = build_bundle(conn)
        print(json.dumps({"commit": push_bundle(data)} if not args.no_push else {"bytes": len(data)}))
    else:
        cmd_ingest(conn)


if __name__ == "__main__":
    main()
