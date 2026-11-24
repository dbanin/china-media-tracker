"""How old a copy of the tracker's data is, told by its newest discovery, and the guard every local tool
that draws a sample or reports findings puts in front of it.

The runner collects every hour; a local database is as old as its last pull. On 2026-11-24 a paper
analysis drawn from the local data/tracker.db (newest discovery 2026-10-12) concluded that the tracker
had stopped collecting on 12 October, when the runner had collected hourly throughout and nothing had
warned the person drawing from the stale copy. The age of a copy is the age of its newest discovery
(articles.discovered_at): every run discovers items, so a copy whose newest is days old is a copy days
old, or a collector that stopped, and either way findings drawn from it describe the tracker as it stood
then. (The newest model label is no measure: the model stage stops when the provider's usage limit is
reached, as from 2026-11-19 to 2026-12-01, while collection goes on.)

An export (docs/data, data/export) is aged the same way, from meta.last_discovered, or, in an export
made before that field, from meta.generated_at, which is never earlier than its newest discovery.

Standard library only, so the stand alone scripts (scripts/numbers_sheet.py) can import it.
"""
import datetime as dt
import sqlite3
import sys
from typing import Dict, Optional

UTC = dt.timezone.utc
# A runner snapshot is published by every export (scripts/db_snapshot.sh), at least once a day, so a
# current copy's newest discovery is under a day old. Two days, as pipeline/agreement.py has refused
# since 2026-10-17, leaves a day for a pull made just before an export.
MAX_AGE_DAYS = 2
PULL_HOW = ("scripts/db_pull.sh downloads the runner's scrubbed snapshot (tracker.db.gz from the db-snapshot "
            "release, published by every export) and puts it in place of data/tracker.db, keeping the old file "
            "as data/tracker.db.local-<time>.bak")
EXPORT_HOW = ("git pull brings the runner's latest export (docs/data and data/export are committed by every "
              "export run)")


def _parse(ts) -> Optional[dt.datetime]:
    if not ts:
        return None
    try:
        t = dt.datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _now(now: Optional[dt.datetime]) -> dt.datetime:
    return (now or dt.datetime.now(UTC)).astimezone(UTC)


def _info(newest: Optional[dt.datetime], now: Optional[dt.datetime], max_age_days: float, source: str,
          basis: str) -> Dict:
    now = _now(now)
    age = None if newest is None else (now - newest).total_seconds() / 86400.0
    return {"newest": newest.replace(microsecond=0).isoformat() if newest else None, "age_days": age,
            "stale": age is not None and age > max_age_days, "max_age_days": max_age_days, "source": source,
            "basis": basis, "now": now.replace(microsecond=0).isoformat()}


def newest_discovery(conn) -> Optional[dt.datetime]:
    """The newest articles.discovered_at in this database, or None when it holds no article or cannot be
    read as a tracker database."""
    try:
        row = conn.execute("SELECT MAX(discovered_at) FROM articles").fetchone()
    except (sqlite3.Error, AttributeError):
        return None
    return _parse(row[0]) if row else None


def db_path(conn) -> str:
    """The file this connection reads, for the message."""
    try:
        for row in conn.execute("PRAGMA database_list"):
            if row[1] == "main":
                return row[2] or "an in-memory database"
    except (sqlite3.Error, AttributeError):
        pass
    return "this database"


def database_age(conn, now: Optional[dt.datetime] = None, max_age_days: float = MAX_AGE_DAYS) -> Dict:
    """{"newest", "age_days", "stale", ...} for a database: the age of its newest discovery."""
    return _info(newest_discovery(conn), now, max_age_days, db_path(conn), "newest discovery")


def export_age(meta: Optional[Dict], now: Optional[dt.datetime] = None, max_age_days: float = MAX_AGE_DAYS,
               source: str = "this export") -> Dict:
    """The same for an export, from its meta.json: last_discovered, or generated_at in an export made before
    that field (an export's newest discovery is never later than its generation)."""
    meta = meta or {}
    newest = _parse(meta.get("last_discovered"))
    basis = "newest discovery"
    if newest is None:
        newest = _parse(meta.get("generated_at") or meta.get("generated"))
        basis = "export time"
    return _info(newest, now, max_age_days, source, basis)


def _when(info: Dict) -> str:
    return str(info["newest"])[:16].replace("T", " ") + " UTC"


def _age(days: float) -> str:
    return "%.1f hours" % (days * 24) if days < 2 else "%.1f days" % days


def message(info: Dict, purpose: str, refused: bool = False, export: bool = False) -> str:
    """The warning (or refusal) for a stale copy: what is old, since when, what that means, how to get the
    current data. Several lines, meant for standard error."""
    head = ("NOT DRAWN: %s refuses a stale copy" % purpose) if refused else ("WARNING: STALE COPY, %s" % purpose)
    lines = [head,
             "  %s: %s %s, %s before now (the limit is %d days)" % (
                 info["source"], info["basis"], _when(info), _age(info["age_days"]), info["max_age_days"]),
             "  The runner collects every hour. Anything drawn from this copy describes the tracker as it stood on "
             "%s, and a silence after that date is this copy's age, not a pause in collection." % info["newest"][:10],
             "  To get the current data: %s." % (EXPORT_HOW if export else PULL_HOW)]
    if refused:
        lines.append("  Nothing was written. Pull the current snapshot and run this again.")
    return "\n".join(lines)


def guard(conn, purpose: str, refuse: bool = False, now: Optional[dt.datetime] = None, out=None,
          max_age_days: float = MAX_AGE_DAYS) -> bool:
    """Check a database before a tool draws from it. A copy whose newest discovery is over max_age_days
    old is warned about loudly on standard error, or, with refuse (a sample is about to be drawn),
    refused: the return is then False and the caller writes nothing. A database with no article, or one
    that cannot be read, is let through: there is nothing to draw from it, and the tool says so itself."""
    info = database_age(conn, now, max_age_days)
    if not info["stale"]:
        return True
    print(message(info, purpose, refused=refuse), file=out or sys.stderr)
    return not refuse


def guard_export(meta: Optional[Dict], purpose: str, source: str = "this export", now: Optional[dt.datetime] = None,
                 out=None, max_age_days: float = MAX_AGE_DAYS) -> Dict:
    """Warn on standard error when an export's newest discovery is over max_age_days old. Returns the age."""
    info = export_age(meta, now, max_age_days, source)
    if info["stale"]:
        print(message(info, purpose, export=True), file=out or sys.stderr)
    return info
