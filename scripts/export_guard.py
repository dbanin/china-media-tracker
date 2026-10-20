"""Refuse an export whose discovery total has fallen below the one the site already shows.

On a cache miss the runner once restored the database from the 2026-09-04 seed without a word, and
the export then published totals weeks behind the site's. This check runs before the export step
(.github/workflows/pipeline.yml): it reads articles_discovered from docs/data/meta.json at the tip of
the branch the export will push to (git show origin/<branch>:docs/data/meta.json, falling back to
the checked out file) and compares it with what the database holds.

The database figure is the sum of the per outlet-day discovery table, which the export adds up into
articles_discovered (store.rebuild_daily_discovery), plus the junk feed ledger (junk_discovery): a
junk feed correction takes discoveries off the outlet-day rows and adds the same amount to the ledger
(pipeline/junk_feeds.py), so the sum of the two never goes down between exports of one database.
A relay bundle replaces its outlets' rows wholesale and can move the figure down a little, so the
check allows a drop of at most --max-drop (a share, default 0.005) and refuses anything larger.

Usage:
  python scripts/export_guard.py [--db data/tracker.db] [--ref origin/main] [--meta docs/data/meta.json]
Exit status 0 lets the export run, 1 refuses it.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

META = "docs/data/meta.json"
DEFAULT_MAX_DROP = 0.005


def committed_total(ref: Optional[str], meta_path: str = META) -> Tuple[Optional[int], str]:
    """articles_discovered on the branch tip (ref), else in the checked out file; (None, where) when
    neither has it."""
    if ref:
        r = subprocess.run(["git", "show", "%s:%s" % (ref, meta_path)], capture_output=True)
        if r.returncode == 0:
            try:
                return int(json.loads(r.stdout.decode("utf-8"))["articles_discovered"]), "%s:%s" % (ref, meta_path)
            except (ValueError, KeyError, TypeError):
                pass
    try:
        return int(json.loads(Path(meta_path).read_text(encoding="utf-8"))["articles_discovered"]), meta_path
    except (OSError, ValueError, KeyError, TypeError):
        return None, meta_path


def database_total(db_path: str) -> int:
    """Discoveries the database accounts for: outlet-day rows plus the junk feed ledger. 0 when the
    database or the table is missing."""
    if not Path(db_path).exists():
        return 0
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        total = 0
        if "daily_outlet_discovery" in tables:
            total += conn.execute("SELECT COALESCE(SUM(discovered), 0) FROM daily_outlet_discovery").fetchone()[0]
        elif "daily_discovery" in tables:
            total += conn.execute("SELECT COALESCE(SUM(discovered), 0) FROM daily_discovery").fetchone()[0]
        if "junk_discovery" in tables:
            total += conn.execute("SELECT COALESCE(SUM(discovered), 0) FROM junk_discovery").fetchone()[0]
        return int(total)
    finally:
        conn.close()


def check(db_path: str, ref: Optional[str], meta_path: str = META, max_drop: float = DEFAULT_MAX_DROP) -> Tuple[bool, str]:
    committed, where = committed_total(ref, meta_path)
    have = database_total(db_path)
    if committed is None:
        return True, "no committed articles_discovered in %s; nothing to compare (database %d)" % (where, have)
    floor = int(committed * (1 - max_drop))
    if have < floor:
        return False, ("the database accounts for %d discoveries, below the %d the site shows (%s); "
                       "a drop of more than %.1f%% means it was restored from an old snapshot or seed, "
                       "so the export is refused" % (have, committed, where, max_drop * 100))
    note = " (%d fewer, within the allowance)" % (committed - have) if have < committed else ""
    return True, "database %d discoveries, site %d (%s)%s" % (have, committed, where, note)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("TRACKER_DB_PATH", "data/tracker.db"))
    ap.add_argument("--ref", default=None, help="git ref whose docs/data/meta.json is the site's, e.g. origin/main")
    ap.add_argument("--meta", default=META)
    ap.add_argument("--max-drop", type=float,
                    default=float(os.environ.get("TRACKER_EXPORT_MAX_DROP") or DEFAULT_MAX_DROP))
    args = ap.parse_args(argv)
    ok, msg = check(args.db, args.ref, args.meta, args.max_drop)
    print(("export guard: " if ok else "::error::export guard: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
