"""The public database snapshot, scrubbed (scratchpad DECISIONS 7).

The db-snapshot release is public. Before it is uploaded (scripts/db_snapshot.sh), a copy of the
working database is made and these are taken out of the copy, never out of the working database:
  - articles.author and reference_items.author: journalists' bylines;
  - articles.summary and reference_items.summary: the feeds' excerpts of the articles;
  - classifications.raw_response: the model's raw output. A session label keeps only its labelled_at
    field, which is how pipeline.labels.session_label_counts tells it from an API label;
  - human_reviews.reviewer: a pseudonymous coder id (c1, c2 ..., data/reviews/CODING_GUIDE.md) is kept;
    anything else, a name, becomes coder_1, coder_2 ... in order of first review, so agreement between
    coders can still be computed; and human_reviews.note, free text that may name people;
  - the doctrine_snippets table (pipeline/doctrine.py), dropped: its matches are 30 word excerpts of
    article bodies. A doctrine draw on a database restored from the snapshot finds no stored snippet
    and reproduces the matches from the text it holds, as for any article without one;
  - every email address in any other text column, replaced by EMAIL_MARK. Columns holding a URL or a
    hash are left alone, so url_hash still matches url.
Then the copy is vacuumed, so the removed text is not left in free pages of the file.

The runner restores from this snapshot only when the Actions cache has no database (scripts/
db_restore.sh); the cache keeps the full database and is not public. A database restored from the
snapshot works: the counts, labels, flags and the relay acknowledgement are all there. What it lacks
is the bylines and summaries of the rows it holds (the planted bylines author detector cannot rescan
those rows, and the gate cannot be run again on their summaries) and the raw model output. New rows
carry their own. The snapshot_info table says when the copy was scrubbed.

Usage:
  python -m pipeline.snapshot_scrub data/tracker.db data/tracker.snapshot.db
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional

SCRUB_VERSION = "1"
EMAIL_MARK = "[email removed]"
CODER_ID_RE = re.compile(r"[A-Za-z]{1,4}[0-9]{1,3}")
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
# Tables left out of the public copy altogether.
DROP_TABLES = ("doctrine_snippets",)
# table -> columns emptied (set to NULL)
BLANK = {
    "articles": ("author", "summary"),
    "reference_items": ("author", "summary"),
    "human_reviews": ("note",),
}


def _tables(conn) -> Dict[str, Dict[str, str]]:
    out = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        out[name] = {r[1]: (r[2] or "").upper() for r in conn.execute('PRAGMA table_info("%s")' % name)}
    return out


def scrub_emails(value):
    if not isinstance(value, str) or "@" not in value:
        return value
    return EMAIL_RE.sub(EMAIL_MARK, value)


def _session_mark(raw: Optional[str]) -> Optional[str]:
    """What is kept of a raw model response: {"labelled_at": ...} for a session label, else nothing."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("labelled_at") is not None:
        return json.dumps({"labelled_at": data["labelled_at"]})
    return None


def scrub(src_path, dst_path) -> Dict:
    """Copy src_path (opened read only) to dst_path, scrub the copy as the module says, vacuum it.
    Returns the counts of what was changed. dst_path is replaced if it exists."""
    src_path, dst_path = Path(src_path), Path(dst_path)
    if src_path.resolve() == dst_path.resolve():
        raise ValueError("the scrubbed copy must not be the working database")
    if dst_path.exists():
        dst_path.unlink()
    src = sqlite3.connect(src_path.resolve().as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)
    finally:
        src.close()
    counts: Dict[str, int] = {}
    try:
        dst.create_function("scrub_emails", 1, scrub_emails, deterministic=True)
        dst.create_function("session_mark", 1, _session_mark, deterministic=True)
        tables = _tables(dst)
        for table in DROP_TABLES:
            if table in tables:
                counts["dropped." + table] = dst.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
                dst.execute('DROP TABLE "%s"' % table)
                del tables[table]
        for table, cols in BLANK.items():
            for col in cols:
                if col in tables.get(table, {}):
                    cur = dst.execute('UPDATE "%s" SET "%s"=NULL WHERE "%s" IS NOT NULL' % (table, col, col))
                    counts["%s.%s" % (table, col)] = cur.rowcount
        if "raw_response" in tables.get("classifications", {}):
            cur = dst.execute("UPDATE classifications SET raw_response=session_mark(raw_response) "
                              "WHERE raw_response IS NOT NULL")
            counts["classifications.raw_response"] = cur.rowcount
        if "reviewer" in tables.get("human_reviews", {}):
            names = {}
            for (name,) in dst.execute("SELECT reviewer FROM human_reviews WHERE reviewer IS NOT NULL ORDER BY id"):
                if not CODER_ID_RE.fullmatch(name):
                    names.setdefault(name, "coder_%d" % (len(names) + 1))
            for name, alias in names.items():
                dst.execute("UPDATE human_reviews SET reviewer=? WHERE reviewer=?", (alias, name))
            counts["human_reviews.reviewer"] = len(names)
        emails = 0
        for table, cols in tables.items():
            for col, ctype in cols.items():
                if col.endswith("url") or col.endswith("hash") or not (ctype == "" or "TEXT" in ctype or "CHAR" in ctype):
                    continue
                cur = dst.execute('UPDATE "%s" SET "%s"=scrub_emails("%s") WHERE "%s" LIKE \'%%@%%\' '
                                  'AND scrub_emails("%s") IS NOT "%s"' % (table, col, col, col, col, col))
                emails += max(cur.rowcount, 0)
        counts["email_fields"] = emails
        dst.execute("CREATE TABLE IF NOT EXISTS snapshot_info (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        dst.execute("INSERT OR REPLACE INTO snapshot_info(key, value) VALUES ('scrubbed_at', strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))")
        dst.execute("INSERT OR REPLACE INTO snapshot_info(key, value) VALUES ('scrub_version', ?)", (SCRUB_VERSION,))
        dst.commit()
        dst.execute("VACUUM")
    finally:
        dst.close()
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write a scrubbed copy of the database for the public snapshot")
    ap.add_argument("src", help="the working database, read only")
    ap.add_argument("dst", help="the scrubbed copy to write")
    args = ap.parse_args(argv)
    counts = scrub(args.src, args.dst)
    print("snapshot scrub:", json.dumps(counts, sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
