"""SQLite storage layer.

Principles:
  - Never delete. Corrections are new rows.
  - Every stage writes its result immediately so a killed runner loses nothing
    that had finished.
  - Machine labels and human labels live in separate tables so disagreement is
    preserved and measurable.
"""
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from pipeline import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS outlets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    language TEXT NOT NULL,
    tier TEXT NOT NULL,
    active INTEGER NOT NULL,
    notes TEXT,
    feeds TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL UNIQUE,
    outlet_id TEXT NOT NULL,
    country TEXT NOT NULL,
    language TEXT NOT NULL,
    feed_url TEXT,
    title TEXT,
    summary TEXT,
    author TEXT,
    published_at TEXT,
    discovered_at TEXT NOT NULL,
    fetched_at TEXT,
    status TEXT NOT NULL,
    fail_reason TEXT,
    http_status INTEGER,
    body_hash TEXT,
    body_chars INTEGER,
    raw_html_path TEXT,
    gate_relevant INTEGER,
    gate_terms TEXT,
    dup_group_id INTEGER,
    fetch_attempts INTEGER NOT NULL DEFAULT 0,
    llm_pending INTEGER NOT NULL DEFAULT 0,
    llm_trigger TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_country_date ON articles(country, published_at);
CREATE INDEX IF NOT EXISTS idx_articles_outlet ON articles(outlet_id);
CREATE INDEX IF NOT EXISTS idx_articles_discovered ON articles(discovered_at);

CREATE TABLE IF NOT EXISTS classifications (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL,
    method TEXT NOT NULL,             -- rules | llm
    category TEXT NOT NULL,           -- A | B | C | not_relevant
    confidence REAL NOT NULL,
    evidence_quote TEXT,
    reasoning TEXT,
    signatures_fired TEXT,            -- JSON list of pattern ids (rules)
    china_sources_cited TEXT,         -- JSON list (llm)
    independent_confirmation_present INTEGER,
    confirmation_evidence TEXT,
    model_version TEXT,
    ruleset_version TEXT NOT NULL,
    classified_at TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    raw_response TEXT,
    FOREIGN KEY(article_id) REFERENCES articles(id)
);
CREATE INDEX IF NOT EXISTS idx_class_article ON classifications(article_id, is_current);

CREATE TABLE IF NOT EXISTS human_reviews (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL,
    classification_id INTEGER,
    human_category TEXT NOT NULL,     -- A | B | C | not_relevant
    reviewer TEXT,
    note TEXT,
    reviewed_at TEXT NOT NULL,
    FOREIGN KEY(article_id) REFERENCES articles(id)
);
CREATE INDEX IF NOT EXISTS idx_reviews_article ON human_reviews(article_id);

CREATE TABLE IF NOT EXISTS daily_counts (
    date TEXT NOT NULL,
    country TEXT NOT NULL,
    category TEXT NOT NULL,           -- A | B | C | not_relevant
    scope TEXT NOT NULL,              -- all | reviewed
    n INTEGER NOT NULL,
    n_rules INTEGER NOT NULL DEFAULT 0,
    n_llm INTEGER NOT NULL DEFAULT 0,
    n_reviewed INTEGER NOT NULL DEFAULT 0,
    n_unique_items INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL,
    PRIMARY KEY(date, country, category, scope)
);

CREATE TABLE IF NOT EXISTS daily_coverage (
    date TEXT NOT NULL,
    country TEXT NOT NULL,
    discovered INTEGER NOT NULL DEFAULT 0,
    gate_relevant INTEGER NOT NULL DEFAULT 0,
    fetched INTEGER NOT NULL DEFAULT 0,
    paywalled INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    blocked_robots INTEGER NOT NULL DEFAULT 0,
    classified INTEGER NOT NULL DEFAULT 0,
    llm_pending INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL,
    PRIMARY KEY(date, country)
);

CREATE TABLE IF NOT EXISTS feed_health (
    feed_url TEXT PRIMARY KEY,
    outlet_id TEXT NOT NULL,
    last_checked TEXT NOT NULL,
    last_ok TEXT,
    last_error TEXT,
    last_entries INTEGER,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    total_checks INTEGER NOT NULL DEFAULT 0,
    total_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    ok INTEGER,
    counts TEXT,
    notes TEXT,
    ceiling_hit INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS llm_batches (
    batch_id TEXT PRIMARY KEY,
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    article_ids TEXT NOT NULL,
    model_version TEXT NOT NULL,
    collected_at TEXT
);

CREATE TABLE IF NOT EXISTS llm_usage (
    date TEXT PRIMARY KEY,
    calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    ceiling_hit INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agreement_studies (
    id INTEGER PRIMARY KEY,
    computed_at TEXT NOT NULL,
    sample_size INTEGER NOT NULL,
    kappa_all REAL,
    kappa_bc REAL,
    n_bc INTEGER,
    details TEXT
);
"""


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:32]


def canonical_url(url: str) -> str:
    """Strip tracking parameters and fragments so the same article hashes the same."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    drop = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "fbclid", "gclid", "ref", "source", "cmpid", "ncid", "ito", "rss", "from"}
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in drop]
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((parts.scheme.lower() or "https", netloc, path, urlencode(query), ""))


def connect(path: Path = config.DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Outlets
# ---------------------------------------------------------------------------

def sync_outlets(conn: sqlite3.Connection, outlets: List[Dict]) -> None:
    now = utcnow()
    for o in outlets:
        conn.execute(
            """INSERT INTO outlets(id, name, country, language, tier, active, notes, feeds, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, country=excluded.country,
                 language=excluded.language, tier=excluded.tier, active=excluded.active,
                 notes=excluded.notes, feeds=excluded.feeds, updated_at=excluded.updated_at""",
            (o["id"], o["name"], o["country"], o["language"], o["tier"], 1 if o["active"] else 0,
             o.get("notes"), json.dumps(o["feeds"]), now),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------

def insert_discovered(conn: sqlite3.Connection, item: Dict) -> Optional[int]:
    """Insert a discovered item. Returns the new id, or None if already known."""
    h = url_hash(item["url"])
    cur = conn.execute(
        """INSERT OR IGNORE INTO articles
           (url, url_hash, outlet_id, country, language, feed_url, title, summary, author,
            published_at, discovered_at, status, gate_relevant, gate_terms)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item["url"], h, item["outlet_id"], item["country"], item["language"], item.get("feed_url"),
         item.get("title"), item.get("summary"), item.get("author"), item.get("published_at"),
         utcnow(), item.get("status", "discovered"), item.get("gate_relevant"),
         json.dumps(item.get("gate_terms") or [])),
    )
    if cur.rowcount == 0:
        return None
    return cur.lastrowid


def recent_titles(conn: sqlite3.Connection, country: str, days: int = 3) -> List[sqlite3.Row]:
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    return conn.execute(
        "SELECT id, title, dup_group_id FROM articles WHERE country=? AND discovered_at>=? AND title IS NOT NULL",
        (country, since),
    ).fetchall()


def set_dup_group(conn: sqlite3.Connection, article_id: int, group_id: int) -> None:
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (group_id, article_id))


def articles_by_status(conn: sqlite3.Connection, status: str, limit: int = 1000) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM articles WHERE status=? ORDER BY discovered_at ASC LIMIT ?", (status, limit)
    ).fetchall()


def update_article(conn: sqlite3.Connection, article_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join("%s=?" % k for k in fields)
    conn.execute("UPDATE articles SET %s WHERE id=?" % cols, list(fields.values()) + [article_id])


def get_article(conn: sqlite3.Connection, article_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()


# ---------------------------------------------------------------------------
# Bodies (gzipped on disk, keyed by url_hash, gitignored)
# ---------------------------------------------------------------------------

def body_path(h: str) -> Path:
    return config.BODIES_DIR / h[:2] / ("%s.txt.gz" % h)


def save_body(h: str, text: str) -> str:
    import gzip
    p = body_path(h)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def load_body(h: str) -> Optional[str]:
    import gzip
    p = body_path(h)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return fh.read()


def raw_html_path(h: str) -> Path:
    return config.RAW_HTML_DIR / h[:2] / ("%s.html.gz" % h)


def save_raw_html(h: str, html: str) -> str:
    import gzip
    p = raw_html_path(h)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write(html)
    try:
        return str(p.relative_to(config.ROOT))
    except ValueError:  # outside the repository, for example a dry run in a temp directory
        return str(p)


# ---------------------------------------------------------------------------
# Classifications and reviews
# ---------------------------------------------------------------------------

def insert_classification(conn: sqlite3.Connection, article_id: int, method: str, category: str,
                          confidence: float, evidence_quote: Optional[str] = None,
                          reasoning: Optional[str] = None, signatures_fired: Optional[List[str]] = None,
                          china_sources_cited: Optional[List[str]] = None,
                          independent_confirmation_present: Optional[bool] = None,
                          confirmation_evidence: Optional[str] = None,
                          model_version: Optional[str] = None,
                          raw_response: Optional[str] = None,
                          ruleset_version: str = config.RULESET_VERSION) -> int:
    """Insert a new current classification, marking earlier ones for the article as not current.
    Earlier rows are never deleted."""
    conn.execute("UPDATE classifications SET is_current=0 WHERE article_id=?", (article_id,))
    cur = conn.execute(
        """INSERT INTO classifications
           (article_id, method, category, confidence, evidence_quote, reasoning, signatures_fired,
            china_sources_cited, independent_confirmation_present, confirmation_evidence,
            model_version, ruleset_version, classified_at, is_current, raw_response)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
        (article_id, method, category, confidence, evidence_quote, reasoning,
         json.dumps(signatures_fired or []), json.dumps(china_sources_cited or []),
         None if independent_confirmation_present is None else int(independent_confirmation_present),
         confirmation_evidence, model_version, ruleset_version, utcnow(), raw_response),
    )
    conn.execute("UPDATE articles SET status='classified', llm_pending=0 WHERE id=?", (article_id,))
    return cur.lastrowid


def current_classification(conn: sqlite3.Connection, article_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM classifications WHERE article_id=? AND is_current=1 ORDER BY id DESC LIMIT 1",
        (article_id,),
    ).fetchone()


def insert_human_review(conn: sqlite3.Connection, article_id: int, classification_id: Optional[int],
                        human_category: str, reviewer: Optional[str], note: Optional[str] = None) -> int:
    cur = conn.execute(
        """INSERT INTO human_reviews(article_id, classification_id, human_category, reviewer, note, reviewed_at)
           VALUES (?,?,?,?,?,?)""",
        (article_id, classification_id, human_category, reviewer, note, utcnow()),
    )
    return cur.lastrowid


def latest_human_review(conn: sqlite3.Connection, article_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM human_reviews WHERE article_id=? ORDER BY id DESC LIMIT 1", (article_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Feed health, run log, LLM usage
# ---------------------------------------------------------------------------

def record_feed_health(conn: sqlite3.Connection, outlet_id: str, feed_url: str, ok: bool,
                       error: Optional[str], entries: int) -> None:
    now = utcnow()
    row = conn.execute("SELECT consecutive_failures, total_checks, total_failures FROM feed_health WHERE feed_url=?",
                       (feed_url,)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, last_error, last_entries,
               consecutive_failures, total_checks, total_failures) VALUES (?,?,?,?,?,?,?,?,?)""",
            (feed_url, outlet_id, now, now if ok else None, error, entries, 0 if ok else 1, 1, 0 if ok else 1),
        )
    else:
        cf = 0 if ok else row["consecutive_failures"] + 1
        conn.execute(
            """UPDATE feed_health SET outlet_id=?, last_checked=?, last_ok=COALESCE(?, last_ok), last_error=?,
               last_entries=?, consecutive_failures=?, total_checks=total_checks+1,
               total_failures=total_failures+? WHERE feed_url=?""",
            (outlet_id, now, now if ok else None, error, entries, cf, 0 if ok else 1, feed_url),
        )


def start_stage(conn: sqlite3.Connection, run_id: str, stage: str) -> int:
    cur = conn.execute("INSERT INTO run_log(run_id, stage, started_at) VALUES (?,?,?)",
                       (run_id, stage, utcnow()))
    conn.commit()
    return cur.lastrowid


def finish_stage(conn: sqlite3.Connection, log_id: int, ok: bool, counts: Dict, notes: str = "",
                 ceiling_hit: bool = False) -> None:
    conn.execute("UPDATE run_log SET finished_at=?, ok=?, counts=?, notes=?, ceiling_hit=? WHERE id=?",
                 (utcnow(), 1 if ok else 0, json.dumps(counts), notes, 1 if ceiling_hit else 0, log_id))
    conn.commit()


def llm_calls_today(conn: sqlite3.Connection) -> int:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    row = conn.execute("SELECT calls FROM llm_usage WHERE date=?", (today,)).fetchone()
    return row["calls"] if row else 0


def record_llm_usage(conn: sqlite3.Connection, calls: int, input_tokens: int, output_tokens: int,
                     ceiling_hit: bool = False) -> None:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    conn.execute(
        """INSERT INTO llm_usage(date, calls, input_tokens, output_tokens, ceiling_hit) VALUES (?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET calls=calls+excluded.calls,
             input_tokens=input_tokens+excluded.input_tokens,
             output_tokens=output_tokens+excluded.output_tokens,
             ceiling_hit=MAX(ceiling_hit, excluded.ceiling_hit)""",
        (today, calls, input_tokens, output_tokens, 1 if ceiling_hit else 0),
    )


def table_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    out = {}
    for t in ("outlets", "articles", "classifications", "human_reviews", "daily_counts", "feed_health", "run_log"):
        out[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    return out
