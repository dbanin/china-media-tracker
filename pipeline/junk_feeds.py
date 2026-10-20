"""Correction for items collected from feeds that no longer carried their outlet's news.

On 2026-10-12 a title count over the last 30 days found seven feeds serving something other than their
outlet's news: gambling posts (Centrafrique Presse), advertorials (La Voix de Djibouti), injected travel
posts (Guatevision), casino posts (ORTC), machine-generated titles (BelaPAN), a "No Feed Access"
placeholder (Pacific Islands News Association) and shoe advertisements (the FeedBurner feed of Les
Nouvelles Caledoniennes). Six outlets were set inactive and Les Nouvelles Caledoniennes moved to its
site's own feed. That stops new items only: the stored items kept their labels (a gambling post counted
as independent journalism for the Central African Republic on 2026-09-24), and every item those feeds
brought in stayed in the permanent per outlet-day discovery counts, so each country's discovered
denominator was inflated by them (BelaPAN alone added 1,163 in 30 days).

The registry records each such feed on its outlet as junk_feed (sources/outlets_schema.json): its url,
since (the first day of record its items were not the outlet's news, read from the stored titles), an
optional until (the day the outlet stopped polling it, required while the outlet is active) and the
reason. From since to the day before until (or for good, on an inactive outlet) the junk feed was the
outlet's only feed, so the outlet's whole discovery on those days is the feed's ("whole days"). On and
after until only the feed's own stored items are its ("partial days").

For every outlet with a junk_feed:
  - items: every stored article of the outlet whose day of record is on or after since, and that came
    from the feed or falls on a whole day, is withdrawn. Its current labels are marked not current,
    never deleted; its status becomes junk_feed, its gate_relevant 0, llm_pending 0, and fail_reason
    records the feed's start date and the status it had. No stage selects the junk_feed status: the
    fetcher, the rules stage, reclassification, the model stage, the gate re-admission, the gate audit
    and the gated-out prune all leave it alone, and the export counts only gate relevant rows. A label
    that arrives later (a model batch submitted before the correction) is withdrawn on the next run.
  - discovery on whole days: the outlet-day row in daily_outlet_discovery is set to zero, the same
    amount is taken off the country-day row in daily_discovery, and the amount is added to the ledger
    junk_discovery, keyed by day and outlet. Gated-out items are pruned after three days, so most of
    what these feeds brought in is no longer stored: the outlet-day row is the only record of it.
  - discovery on partial days: each withdrawn item takes one discovery (and one gate relevant, when it
    was) off its outlet-day and country-day rows, and adds it to the ledger.

It is idempotent: a withdrawn item carries the junk_feed status and is skipped, and a whole day row is
zero after the first run. It runs at the start of every discover stage (before gated-out items are
pruned) and of every export. A relay bundle cannot bring the items back (pipeline/relay.py): an
existing junk_feed row is never brought forward, a relay outlet-day row for a whole day is not taken
in, and one for a partial day is taken in less what the ledger records for it, since the relay's own
row still counts the withdrawn items. An item the relay delivers that the main database had pruned
before the correction ran is inserted, counted as any relay item is, and withdrawn on the next run.
"""
import json
from typing import Dict, List, Optional

STATUS = "junk_feed"
REASON = "junk_feed"     # fail_reason: "junk_feed:<since>: was <status>[ (<earlier fail_reason>)]"

LEDGER = """CREATE TABLE IF NOT EXISTS junk_discovery (
    date TEXT NOT NULL,
    outlet_id TEXT NOT NULL,
    country TEXT NOT NULL,
    discovered INTEGER NOT NULL DEFAULT 0,
    gate_relevant INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(date, outlet_id)
)"""


def windows(outlets: Optional[List[Dict]] = None) -> Dict[str, Dict]:
    """Outlet id: {url, since, until, reason, country} for every outlet the registry gives a junk_feed."""
    if outlets is None:
        from pipeline import registry
        outlets = registry.load_outlets()
    out = {}
    for o in outlets:
        j = o.get("junk_feed")
        if j:
            out[o["id"]] = {"url": j["url"], "since": j["since"], "until": j.get("until"),
                            "reason": j.get("reason") or "", "country": o["country"]}
    return out


def whole_day(w: Dict, day: Optional[str]) -> bool:
    """Whether the outlet's whole discovery on `day` came from its junk feed."""
    return bool(day) and day >= w["since"] and (not w.get("until") or day < w["until"])


def is_junk(w: Dict, feed_url: Optional[str], day: Optional[str]) -> bool:
    """Whether an article of the outlet, from `feed_url` with day of record `day`, is a junk feed item."""
    return bool(day) and day >= w["since"] and (feed_url == w["url"] or whole_day(w, day))


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _day_sql(conn) -> str:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)")}
    return ("COALESCE(day_of_record, substr(discovered_at, 1, 10))" if "day_of_record" in cols
            else "substr(discovered_at, 1, 10)")


def _ledger_add(conn, day: str, outlet_id: str, country: str, discovered: int, relevant: int) -> None:
    from pipeline import store
    conn.execute(
        """INSERT INTO junk_discovery(date, outlet_id, country, discovered, gate_relevant, updated_at) VALUES (?,?,?,?,?,?)
           ON CONFLICT(date, outlet_id) DO UPDATE SET discovered=discovered+excluded.discovered,
             gate_relevant=gate_relevant+excluded.gate_relevant, updated_at=excluded.updated_at""",
        (day, outlet_id, country, discovered, relevant, store.utcnow()))


def _subtract(conn, day: str, outlet_id: str, country: str, discovered: int, relevant: int) -> None:
    conn.execute("""UPDATE daily_outlet_discovery SET discovered=MAX(discovered-?, 0), gate_relevant=MAX(gate_relevant-?, 0)
                    WHERE date=? AND outlet_id=?""", (discovered, relevant, day, outlet_id))
    conn.execute("""UPDATE daily_discovery SET discovered=MAX(discovered-?, 0), gate_relevant=MAX(gate_relevant-?, 0)
                    WHERE date=? AND country=?""", (discovered, relevant, day, country))
    _ledger_add(conn, day, outlet_id, country, discovered, relevant)


def _withdraw(conn, article_id: int, tally: Dict, dry_run: bool) -> int:
    cats = [r[0] for r in conn.execute(
        "SELECT category FROM classifications WHERE article_id=? AND is_current=1", (article_id,))]
    for c in cats:
        tally[c] = tally.get(c, 0) + 1
    if cats and not dry_run:
        conn.execute("UPDATE classifications SET is_current=0 WHERE article_id=? AND is_current=1", (article_id,))
    return len(cats)


def _reason(w: Dict, row) -> str:
    why = "%s:%s: was %s" % (REASON, w["since"], row["status"])
    if row["fail_reason"]:
        why += " (%s)" % row["fail_reason"]
    return why


def _empty(w: Dict) -> Dict:
    return {"country": w["country"], "since": w["since"], "items_withdrawn": 0, "relevant_items": 0,
            "labels_withdrawn": {}, "late_labels_withdrawn": 0, "outlet_days": 0,
            "discovered_withdrawn": 0, "gate_relevant_withdrawn": 0}


# Items corrected between commits, and a commit after each outlet. Each item's correction (and each outlet
# day's) is whole on its own and is not applied twice, so committing in steps keeps the write lock free
# for the hourly run's other writers (the 2026-10-17 robustness audit measured 55 s held by one commit).
COMMIT_EVERY = 500


def run(conn, outlets: Optional[List[Dict]] = None, dry_run: bool = False) -> Dict:
    """Apply the correction (or, with dry_run, only count it; nothing is written, so it works on a read
    only connection). Returns per outlet counts; an empty dict when there is nothing left to correct.
    discovered_withdrawn and gate_relevant_withdrawn are what this run takes off the denominators."""
    ws = windows(outlets)
    if not ws:
        return {}
    if not dry_run:
        conn.execute(LEDGER)
    day_sql = _day_sql(conn)
    out: Dict[str, Dict] = {}
    for oid, w in ws.items():
        t = _empty(w)
        rows = conn.execute(
            "SELECT id, status, gate_relevant, fail_reason, feed_url, country, %s AS day FROM articles "
            "WHERE outlet_id=? AND %s>=? ORDER BY id" % (day_sql, day_sql), (oid, w["since"])).fetchall()
        for i, r in enumerate(rows):
            if i and i % COMMIT_EVERY == 0 and not dry_run:
                conn.commit()
            if not is_junk(w, r["feed_url"], r["day"]):
                continue
            if (r["fail_reason"] or "").startswith(REASON + ":"):
                # Withdrawn before. A model label that arrived since, or a batch error that set it to
                # await the model again, is undone; its discovery was taken off the first time.
                late = conn.execute("SELECT COUNT(*) FROM classifications WHERE article_id=? AND is_current=1",
                                    (r["id"],)).fetchone()[0]
                if r["status"] == STATUS and not late and not r["gate_relevant"]:
                    continue
                t["late_labels_withdrawn"] += _withdraw(conn, r["id"], {}, dry_run)
                if not dry_run:
                    conn.execute("UPDATE articles SET status=?, gate_relevant=0, llm_pending=0 WHERE id=?", (STATUS, r["id"]))
                continue
            rel = 1 if r["gate_relevant"] else 0
            t["items_withdrawn"] += 1
            t["relevant_items"] += rel
            _withdraw(conn, r["id"], t["labels_withdrawn"], dry_run)
            if not whole_day(w, r["day"]):
                t["discovered_withdrawn"] += 1
                t["gate_relevant_withdrawn"] += rel
            if dry_run:
                continue
            conn.execute("UPDATE articles SET status=?, gate_relevant=0, llm_pending=0, fail_reason=? WHERE id=?",
                         (STATUS, _reason(w, r), r["id"]))
            if not whole_day(w, r["day"]):
                _subtract(conn, r["day"], oid, r["country"], 1, rel)
        q = ("SELECT date, country, discovered, gate_relevant FROM daily_outlet_discovery WHERE outlet_id=? AND date>=?"
             " AND (discovered>0 OR gate_relevant>0)")
        params = [oid, w["since"]]
        if w.get("until"):
            q += " AND date<?"
            params.append(w["until"])
        for d in conn.execute(q + " ORDER BY date", params).fetchall():
            t["outlet_days"] += 1
            t["discovered_withdrawn"] += d["discovered"]
            t["gate_relevant_withdrawn"] += d["gate_relevant"]
            if not dry_run:
                _subtract(conn, d["date"], oid, d["country"], d["discovered"], d["gate_relevant"])
        if t["items_withdrawn"] or t["late_labels_withdrawn"] or t["outlet_days"] or t["discovered_withdrawn"]:
            out[oid] = t
        if not dry_run:
            conn.commit()
    return out


def withdrawn(conn) -> Dict[str, Dict]:
    """Per outlet, what the ledger records as taken off the discovery counts: {outlet: {country,
    discovered, gate_relevant, days}}."""
    if not _has_table(conn, "junk_discovery"):
        return {}
    return {r[0]: {"country": r[1], "discovered": r[2], "gate_relevant": r[3], "days": r[4]} for r in conn.execute(
        """SELECT outlet_id, country, SUM(discovered), SUM(gate_relevant), COUNT(*) FROM junk_discovery
           GROUP BY outlet_id, country ORDER BY outlet_id""")}


def relay_outlet_day(conn, item: Dict, ws: Dict[str, Dict]) -> Optional[Dict]:
    """A relay bundle's outlet-day discovery row as it is to be taken in: unchanged for an outlet day no
    junk feed touches, None (not taken in) on a whole day, whose row the correction set to zero, and on
    a partial day less what the ledger records for that day. The relay's row counts every item it
    discovered, the withdrawn ones included, and replaces the main database's row."""
    w = ws.get(item.get("outlet_id"))
    if not w or not item.get("date") or item["date"] < w["since"]:
        return item
    if whole_day(w, item["date"]):
        return None
    if not _has_table(conn, "junk_discovery"):
        return item
    led = conn.execute("SELECT discovered, gate_relevant FROM junk_discovery WHERE date=? AND outlet_id=?",
                       (item["date"], item["outlet_id"])).fetchone()
    if not led:
        return item
    return dict(item, discovered=max(0, int(item.get("discovered") or 0) - led[0]),
                gate_relevant=max(0, int(item.get("gate_relevant") or 0) - led[1]))


def dry_run_report(conn, outlets: Optional[List[Dict]] = None) -> str:
    """The dry run as JSON, for the command line."""
    return json.dumps({"would_correct": run(conn, outlets, dry_run=True), "ledger": withdrawn(conn)}, indent=1)


if __name__ == "__main__":
    import argparse
    import sqlite3
    from pipeline import config
    ap = argparse.ArgumentParser(description="Count (--dry-run, read only) or apply the junk feed correction.")
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        c = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
        c.row_factory = sqlite3.Row
        print(dry_run_report(c))
    else:
        from pathlib import Path
        from pipeline import store
        c = store.connect(Path(args.db))
        print(json.dumps(run(c), indent=1))
