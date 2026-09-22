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

# How far an article has got. An existing hosted row takes the relay's status only when the relay's
# is strictly further along this ladder, so a status never regresses; a hosted row that carries a
# classification is never touched at all. Discovery is not counted again on either path: for relay
# outlets the per outlet-day counts come from the bundle's own rows.
STATUS_RANK = {
    "gated_out": 0,
    "blocked_robots": 1, "failed": 1,
    "discovered": 2, "queued": 2,
    "paywalled": 3,
    "fetched": 4,
    "awaiting_llm": 5,
    "llm_submitted": 6,
    "classified": 7,
}
# Fields worth carrying over when the relay's copy is further along. The hosted row's own
# discovery fields (url, outlet, country, discovered_at) are never rewritten.
ADVANCE_FIELDS = ["status", "fail_reason", "http_status", "body_hash", "body_chars", "fetched_at",
                  "fetch_attempts", "page_labels", "author", "published_at", "title", "summary"]


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
        # Feed health travels too, so the site shows the relayed feeds as healthy rather than
        # as the hosted runner sees them.
        for h in conn.execute("SELECT * FROM feed_health"):
            fh.write(json.dumps({"_type": "feed_health", **{k: h[k] for k in h.keys()}}, ensure_ascii=False) + "\n")
        # The relay database counts every item it discovers at insert time, so its per outlet-day
        # rows are exact; the hosted side replaces its rows for these outlets with them.
        for d in conn.execute("SELECT date, outlet_id, country, discovered, gate_relevant FROM daily_outlet_discovery"):
            fh.write(json.dumps({"_type": "outlet_discovery", **{k: d[k] for k in d.keys()}}) + "\n")
        # Every discovery pass the relay ran, so the hosted side can tell a quiet day in the relayed
        # countries from a day on which the machine was asleep.
        for run in conn.execute("SELECT started_at, finished_at, ok FROM run_log WHERE stage='discover' AND started_at >= ?", (since,)):
            fh.write(json.dumps({"_type": "relay_run", **{k: run[k] for k in run.keys()}}) + "\n")
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


def _save_body(item: Dict) -> bool:
    if not item.get("body"):
        return False
    store.save_body(item["url_hash"], gzip.decompress(base64.b64decode(item["body"])).decode("utf-8"))
    return True


def update_existing(conn, row, item: Dict) -> Optional[str]:
    """Bring a hosted row forward when the relay's copy of the same article is strictly more
    advanced. Returns what changed, or None when nothing did.

    The relay machine runs the gate and the fetcher on outlets the hosted runner cannot reach, so
    its copy is often further along: an item the hosted side has as gated_out can be gate-relevant
    on the relay because it arrived there on a press release section feed, and an item the hosted
    side failed to fetch can be fetched there. Skipping every url_hash it already had left those
    decisions stranded on the relay machine forever.

    Two things are never done: a hosted classification is never overwritten, and a status never
    goes backwards. Nothing here counts discovery: the row was counted when it was first seen."""
    if row["status"] == "classified" or conn.execute(
            "SELECT 1 FROM classifications WHERE article_id=? AND is_current=1", (row["id"],)).fetchone():
        return None
    if row["status"] == "gated_out" and not item.get("gate_relevant"):
        return None      # still outside the corpus on both sides; a status alone would not make it in
    fields = {}
    changed = []
    if item.get("gate_relevant") and not row["gate_relevant"]:
        # The gate decision the relay made wins: it saw the item on a feed the hosted side does not poll.
        fields["gate_relevant"] = 1
        fields["gate_terms"] = item.get("gate_terms") or "[]"
        if item.get("feed_url"):
            fields["feed_url"] = item["feed_url"]
        changed.append("gate_relevant")
    relay_rank = STATUS_RANK.get(item.get("status"), -1)
    hosted_rank = STATUS_RANK.get(row["status"], -1)
    if relay_rank > hosted_rank:
        for k in ADVANCE_FIELDS:
            if k in item and item[k] is not None:
                fields[k] = item[k]
        # A row promoted out of gated_out goes to the relay's status, which is at least queued.
        changed.append("status")
    elif "gate_relevant" in changed and row["status"] == "gated_out":
        fields["status"] = "queued"
        changed.append("status")
    if not fields:
        return None
    store.update_article(conn, row["id"], **fields)
    return ",".join(changed)


def ingest(conn, data: bytes) -> Dict:
    """Insert every article the main database does not know, and bring forward the ones whose relay
    copy is further along. Discovery is never counted twice."""
    from pipeline.fetch_feeds import link_near_duplicates
    counts = {"seen": 0, "inserted": 0, "bodies": 0, "relevant": 0, "already_delivered": 0, "updated": 0}
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)).isoformat()
    conn.execute("DELETE FROM relay_seen WHERE seen_at < ?", (cutoff,))
    for item in iter_bundle(data):
        if item.get("_type") == "outlet_discovery":
            conn.execute(
                """INSERT INTO daily_outlet_discovery(date, outlet_id, country, discovered, gate_relevant) VALUES (?,?,?,?,?)
                   ON CONFLICT(date, outlet_id) DO UPDATE SET country=excluded.country, discovered=excluded.discovered,
                     gate_relevant=excluded.gate_relevant""",
                (item["date"], item["outlet_id"], item["country"], item["discovered"], item.get("gate_relevant", 0)))
            counts["outlet_days"] = counts.get("outlet_days", 0) + 1
            continue
        if item.get("_type") == "relay_run":
            conn.execute("INSERT OR IGNORE INTO relay_runs(started_at, finished_at, ok) VALUES (?,?,?)",
                         (item["started_at"], item.get("finished_at"), item.get("ok")))
            conn.execute("UPDATE relay_runs SET finished_at=?, ok=? WHERE started_at=? AND finished_at IS NULL",
                         (item.get("finished_at"), item.get("ok"), item["started_at"]))
            counts["relay_runs"] = counts.get("relay_runs", 0) + 1
            continue
        if item.get("_type") == "feed_health":
            conn.execute(
                """INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, last_error, last_entries,
                   consecutive_failures, total_checks, total_failures) VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(feed_url) DO UPDATE SET outlet_id=excluded.outlet_id, last_checked=excluded.last_checked,
                     last_ok=excluded.last_ok, last_error=excluded.last_error, last_entries=excluded.last_entries,
                     consecutive_failures=excluded.consecutive_failures, total_checks=excluded.total_checks,
                     total_failures=excluded.total_failures""",
                (item["feed_url"], item["outlet_id"], item["last_checked"], item["last_ok"], item["last_error"],
                 item["last_entries"], item["consecutive_failures"], item["total_checks"], item["total_failures"]))
            counts["feeds"] = counts.get("feeds", 0) + 1
            continue
        if item.get("_type") or "url_hash" not in item:
            # A record type this version does not know, from a newer relay. Skip it rather than fail
            # the whole run: on 2026-09-14 a bundle with a new record type stopped two hourly runs.
            counts["skipped_unknown"] = counts.get("skipped_unknown", 0) + 1
            continue
        counts["seen"] += 1
        existing = conn.execute(
            "SELECT id, status, gate_relevant, country, title FROM articles WHERE url_hash=?", (item["url_hash"],)).fetchone()
        if existing:
            changed = update_existing(conn, existing, item)
            if changed:
                counts["updated"] += 1
                if "gate_relevant" in changed:
                    counts["promoted_relevant"] = counts.get("promoted_relevant", 0) + 1
                    link_near_duplicates(conn, existing["id"], item.get("title") or existing["title"] or "",
                                         item.get("country") or existing["country"])
                if _save_body(item):
                    counts["bodies"] += 1
                conn.commit()
            continue
        if conn.execute("SELECT 1 FROM relay_seen WHERE url_hash=?", (item["url_hash"],)).fetchone():
            counts["already_delivered"] += 1   # delivered earlier and pruned since; not a new item
            continue
        cols = [k for k in ARTICLE_FIELDS if k in item]
        conn.execute("INSERT INTO articles (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" * len(cols))),
                     [item[k] for k in cols])
        new_id = conn.execute("SELECT id FROM articles WHERE url_hash=?", (item["url_hash"],)).fetchone()[0]
        conn.execute("INSERT OR REPLACE INTO relay_seen(url_hash, seen_at) VALUES (?,?)", (item["url_hash"], store.utcnow()))
        counts["inserted"] += 1
        relevant = bool(item.get("gate_relevant"))
        # Discovery counts for relay outlets come from the bundle's own per outlet-day rows above.
        if _save_body(item):
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
