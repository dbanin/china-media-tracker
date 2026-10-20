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
import re
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
    llm_trigger TEXT,
    page_labels TEXT,
    themes TEXT,                      -- JSON {"v": themes version, "t": [theme ids]}
    audit_keep INTEGER,               -- gated-out only: 1 kept permanently for the gate audit, 0 not, NULL undecided
    extract_version TEXT,             -- config.EXTRACT_VERSION the stored body was extracted under; NULL before 2026-10-12
    day_of_record TEXT,               -- publication day of an item a backfill read brought in (backfill_day); NULL means the discovery day
    doctrine TEXT                     -- doctrine term matches (pipeline/doctrine.py), JSON; NULL until tagged. Report only, never a label
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
    route TEXT,                       -- state origin only: how the item arrived (classify_rules.ROUTE_IDS)
    body_refreshed TEXT,              -- model labels only: "<UTC time> <why>" when the body the label was made on, a stub, was since replaced (classify_rules.note_body_refresh)
    issuer_type TEXT,                 -- model labels only: who issued the text (classify_llm.ISSUER_TYPES); NULL before prompt 2026-10-17
    needs_review TEXT,                -- model labels only: why the reply was inconsistent (classify_llm.INCONSISTENCIES), else NULL
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
    top_discovered INTEGER NOT NULL DEFAULT 0,   -- items from the country's top outlet set
    top_target INTEGER NOT NULL DEFAULT 0,       -- state origin, unverified relay and pending among them
    top_china INTEGER NOT NULL DEFAULT 0,        -- all China coverage among them
    top_a INTEGER NOT NULL DEFAULT 0,            -- state origin among them
    top_b INTEGER NOT NULL DEFAULT 0,            -- unverified relay among them
    PRIMARY KEY(date, country)
);

-- Permanent per-day discovery totals, written at insert time. The articles table loses
-- gated-out rows after GATED_OUT_RETENTION_DAYS, so counting rows would shrink history.
CREATE TABLE IF NOT EXISTS daily_discovery (
    date TEXT NOT NULL,
    country TEXT NOT NULL,
    discovered INTEGER NOT NULL DEFAULT 0,
    gate_relevant INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(date, country)
);

-- Same, per outlet, so denominators can be restricted to a country's largest outlets.
CREATE TABLE IF NOT EXISTS daily_outlet_discovery (
    date TEXT NOT NULL,
    outlet_id TEXT NOT NULL,
    country TEXT NOT NULL,
    discovered INTEGER NOT NULL DEFAULT 0,
    gate_relevant INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(date, outlet_id)
);

-- Articles the relay has already delivered, so a bundle that still carries an item the
-- hosted side has since pruned does not insert it, and count it, a second time.
CREATE TABLE IF NOT EXISTS relay_seen (
    url_hash TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);

-- Articles a prune deleted (store.prune_gated_out; on the relay also relay.prune_handed_off), so a feed
-- that still lists one does not insert it, and count it, a second time (config.SEEN_URL_RETENTION_DAYS).
-- h is the first 15 hex digits of the url_hash as an integer (seen_key); days are YYYYMMDD integers.
CREATE TABLE IF NOT EXISTS seen_urls (
    h INTEGER PRIMARY KEY,
    first_day INTEGER NOT NULL,       -- the day of record its discovery was counted on
    last_day INTEGER NOT NULL,        -- the latest day it was seen: its discovery, then each sighting since
    seen_again INTEGER NOT NULL DEFAULT 0, -- 1 once a feed listed it again after the prune
    relevant INTEGER NOT NULL DEFAULT 0    -- 1 when it was in the corpus (the relay's handed-off rows)
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
    total_failures INTEGER NOT NULL DEFAULT 0,
    newest_entry TEXT                 -- the newest entry date the last successful poll listed (frozen feeds)
);

-- Where the shared AAP release listing read got to (pipeline.section_pages): key 'gap' holds the
-- stretch a read cut short at its time budget has still to cover, as JSON {page, oldest, target}.
CREATE TABLE IF NOT EXISTS aap_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
    collected_at TEXT,
    kind TEXT NOT NULL DEFAULT 'classify'   -- classify: first labels; study: the second rater's re-judgements
);

CREATE TABLE IF NOT EXISTS llm_usage (
    date TEXT PRIMARY KEY,
    calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    ceiling_hit INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0     -- estimated at published prices; see pipeline.llm_cost
);

-- Human coding studies live in agreement_studies. A study where a second MODEL re-judges the same
-- articles is a different thing and is kept apart deliberately: it measures whether two raters apply
-- the codebook consistently, not whether they apply it correctly, and two models of one family can
-- share a bias that no amount of agreement between them reveals. Mixing the two in one table would
-- leave a later reader unable to tell which kind of study produced a published number.
CREATE TABLE IF NOT EXISTS model_agreement_studies (
    id INTEGER PRIMARY KEY,
    computed_at TEXT NOT NULL,
    method TEXT NOT NULL,             -- model_vs_model
    model_a TEXT NOT NULL,            -- the model whose labels the site publishes
    model_b TEXT NOT NULL,            -- the second rater
    sample_size INTEGER NOT NULL,
    kappa_all REAL,
    kappa_bc REAL,
    n_bc INTEGER,
    details TEXT                      -- JSON: per category, per language, confusion
);

-- Both raters' labels for every sampled article, so a disagreement can be read rather than inferred
-- from a coefficient. The disagreeing pairs are the closest thing to validation available without a
-- human, and they are worth keeping even when the kappa is comfortable.
CREATE TABLE IF NOT EXISTS model_agreement_labels (
    study_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    category_a TEXT NOT NULL,
    confidence_a REAL,
    evidence_a TEXT,
    category_b TEXT NOT NULL,
    confidence_b REAL,
    evidence_b TEXT,
    PRIMARY KEY(study_id, article_id),
    FOREIGN KEY(study_id) REFERENCES model_agreement_studies(id)
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

-- When the daily model call ceiling binds, the stratified draw's allocation per day and country:
-- how many articles were eligible for the verification judgement and how many were sent. The
-- ratio is the sampling fraction, so counts can be reweighted instead of silently undercounting.
CREATE TABLE IF NOT EXISTS llm_sampling (
    date TEXT NOT NULL,
    country TEXT NOT NULL,
    eligible INTEGER NOT NULL DEFAULT 0,
    drawn INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(date, country)
);

-- Per feed per day and per collector: polls, polls that came back as a full window with nothing
-- seen before (saturated), and the estimated number of items lost between polls. collector is the
-- process that made the polls (config.COLLECTOR: hosted, or self_hosted for the relay on the
-- owner's machine, whose rows arrive in its bundle), so a feed polled by both keeps one row each.
-- estimator is the missed-items estimator the row's estimates were made by
-- (config.MISSED_ESTIMATOR_VERSION; v1 for rows made before 2026-10-12, mixed for a row that holds
-- polls estimated by two versions); the export reports the estimate only from rows of the current one.
CREATE TABLE IF NOT EXISTS feed_polls (
    date TEXT NOT NULL,
    feed_url TEXT NOT NULL,
    outlet_id TEXT NOT NULL,
    country TEXT NOT NULL,
    polls INTEGER NOT NULL DEFAULT 0,
    saturated INTEGER NOT NULL DEFAULT 0,
    entries INTEGER NOT NULL DEFAULT 0,
    new_items INTEGER NOT NULL DEFAULT 0,
    missed_estimate REAL NOT NULL DEFAULT 0,
    collector TEXT NOT NULL DEFAULT 'hosted',
    estimator TEXT NOT NULL DEFAULT 'v1',
    PRIMARY KEY(date, feed_url, collector)
);

-- Every poll of a feed that both collectors poll (registry collector: both), one row each, kept for
-- POLL_LOG_DAYS. A poll is saturated only when its window misses the previous poll by either
-- collector, so each collector's polls are judged against the other's too: the runner's at poll
-- time, against the relay polls already ingested, and the relay's when its bundle is ingested
-- (pipeline.relay.reconcile_poll), which also rejudges a runner poll the late relay poll preceded.
-- saturated_own and missed_own are the verdict the collector reached alone; saturated and
-- missed_estimate the verdict against both, which is what feed_polls holds. times is the window's
-- entry dates (JSON), so a poll can be judged again; prev_poll_at the poll it was judged against;
-- estimator the missed-items estimator missed_estimate was made by, as in feed_polls.
CREATE TABLE IF NOT EXISTS feed_poll_log (
    feed_url TEXT NOT NULL,
    collector TEXT NOT NULL,
    polled_at TEXT NOT NULL,
    outlet_id TEXT NOT NULL,
    country TEXT NOT NULL,
    entries INTEGER NOT NULL DEFAULT 0,
    new_items INTEGER NOT NULL DEFAULT 0,
    times TEXT,
    prev_poll_at TEXT,
    saturated_own INTEGER NOT NULL DEFAULT 0,
    missed_own REAL NOT NULL DEFAULT 0,
    saturated INTEGER NOT NULL DEFAULT 0,
    missed_estimate REAL NOT NULL DEFAULT 0,
    estimator TEXT NOT NULL DEFAULT 'v1',
    PRIMARY KEY(feed_url, polled_at, collector)
);

-- Discovery passes the relay collector on the owner's machine completed, from its bundle.
CREATE TABLE IF NOT EXISTS relay_runs (
    started_at TEXT PRIMARY KEY,
    finished_at TEXT,
    ok INTEGER
);

-- Articles whose byline, or whose cited organisation, matches a persona or false front named in a
-- published investigation (sources/personas.yaml, read by pipeline.planted). A flag means the name
-- matches a listed persona, cited there; it is never a finding, never a category and never changes
-- a count. The one of persona_id and front_id that does not apply is stored as '' rather than NULL
-- so that the UNIQUE constraint holds: SQLite treats two NULLs as distinct.
CREATE TABLE IF NOT EXISTS planted_flags (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL,
    detector TEXT NOT NULL,           -- author_field | byline_head | front_cited
    persona_id TEXT,
    front_id TEXT,
    operation_id TEXT NOT NULL,
    evidence TEXT,
    flagged_at TEXT NOT NULL,
    list_version TEXT NOT NULL,
    UNIQUE(article_id, detector, persona_id, front_id)
);
CREATE INDEX IF NOT EXISTS idx_planted_article ON planted_flags(article_id);

-- Where the planted scan got to: the last article id and fetch time it looked at and the persona
-- list version it looked with, so a changed list triggers a full rescan.
CREATE TABLE IF NOT EXISTS planted_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Body text fingerprints and the near-duplicate links found from them (pipeline.body_dups).
-- shingles is -1 when the body file was missing; sig is NULL when the body is too short to compare.
CREATE TABLE IF NOT EXISTS body_fingerprints (
    article_id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL,
    shingles INTEGER NOT NULL,
    sig BLOB,                         -- 64 x uint32 one permutation MinHash, little endian
    discovered_at TEXT NOT NULL,
    outlet_id TEXT NOT NULL,
    template INTEGER NOT NULL DEFAULT 0,  -- matches its own outlet's text under another headline
    checked_at TEXT,                  -- NULL until the linking pass has compared it
    computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bodyfp_discovered ON body_fingerprints(discovered_at);

-- One row per pair of articles (a < b) from different outlets whose bodies overlap. They join title
-- groups for the underlying item counts; articles.dup_group_id stays the title group.
CREATE TABLE IF NOT EXISTS body_links (
    a INTEGER NOT NULL,
    b INTEGER NOT NULL,
    similarity REAL NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (a, b)
);
CREATE INDEX IF NOT EXISTS idx_bodylinks_b ON body_links(b);

-- New research measures (pipeline/measures.py), added 2026-10-13. Every table from here down is
-- report only: nothing in it is a label, a count, a denominator or a monitored outlet, and the prune
-- (prune_gated_out, expire_seen) never touches it.

-- Items from the reference sources (sources/reference.yaml, ids ref_*): the English and other
-- editions of Chinese state outlets, read as originals to compare state copy against. Never
-- classified, never counted, never an outlet; China stays unmonitored. url_hash is url_hash(url), in
-- a namespace of its own (an item may also exist in articles). sig and shingles are the body_dups
-- fingerprint of the body, kept after the body itself is deleted (body_pruned_at).
CREATE TABLE IF NOT EXISTS reference_items (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,          -- sources/reference.yaml id, never an outlets.yaml id
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL UNIQUE,
    language TEXT,
    title TEXT,
    summary TEXT,
    author TEXT,
    published_at TEXT,
    discovered_at TEXT NOT NULL,
    fetched_at TEXT,
    status TEXT NOT NULL,             -- set by pipeline/reference.py (discovered, fetched, failed, blocked_robots, ...)
    fail_reason TEXT,
    body_hash TEXT,
    body_chars INTEGER,
    extract_version TEXT,             -- config.EXTRACT_VERSION the body was extracted under
    sig BLOB,                         -- body_dups signature (64 x uint32, little endian); NULL when too short
    shingles INTEGER,                 -- body_dups shingle count; -1 when the body was missing
    body_pruned_at TEXT,              -- when the stored body of an unpaired item was deleted; the row stays
    collector TEXT,                   -- hosted | self_hosted (config.COLLECTOR of the process that collected it)
    also_monitored TEXT               -- set by pipeline/reference.py when the same item is also in the monitored corpus; NULL otherwise
);
CREATE INDEX IF NOT EXISTS idx_refitems_source_pub ON reference_items(source_id, published_at);
CREATE INDEX IF NOT EXISTS idx_refitems_discovered ON reference_items(discovered_at);
CREATE INDEX IF NOT EXISTS idx_refitems_status ON reference_items(status);

-- Feed health of the reference sources, the same columns as feed_health with source_id for outlet_id.
CREATE TABLE IF NOT EXISTS reference_feed_health (
    feed_url TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    last_checked TEXT NOT NULL,
    last_ok TEXT,
    last_error TEXT,
    last_entries INTEGER,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    total_checks INTEGER NOT NULL DEFAULT 0,
    total_failures INTEGER NOT NULL DEFAULT 0,
    newest_entry TEXT
);

-- One row per state origin copy (copy_id is its articles.id): the original it was paired with, if
-- any, and the alteration and republication speed metrics (pipeline/copies.py, pipeline/speed.py).
-- current is 0 once the copy no longer carries a current state origin label. No foreign keys, so a
-- row never blocks a delete elsewhere.
CREATE TABLE IF NOT EXISTS state_copies (
    copy_id INTEGER PRIMARY KEY,
    version TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    current INTEGER NOT NULL DEFAULT 1,
    original_kind TEXT NOT NULL,      -- reference | monitored | none
    ref_id INTEGER,                   -- reference_items.id when original_kind is reference
    original_article_id INTEGER,      -- articles.id when original_kind is monitored
    original_source TEXT,             -- the reference source id or the outlet id of the original
    match_method TEXT,
    match_similarity REAL,
    credit_consistent INTEGER,        -- 1 when the copy's credit names the original's publisher
    none_reason TEXT,                 -- why no original was found, when original_kind is none
    copy_published_at TEXT,
    original_published_at TEXT,
    copy_ts_res TEXT,                 -- resolution of the copy's time: second, minute, hour, date (no time of day), none
    orig_ts_res TEXT,
    lag_seconds INTEGER,
    body_state TEXT,                  -- which bodies were available when the metrics were computed
    headline_similarity REAL,
    kept_share REAL,
    novel_share REAL,
    rewritten_share REAL,
    added_paragraphs INTEGER,
    added_words INTEGER,
    cue_local INTEGER,
    cue_counterpoint INTEGER,
    band TEXT,                        -- verbatim | light | moderate | substantial, provisional
    details TEXT                      -- JSON
);
CREATE INDEX IF NOT EXISTS idx_copies_kind ON state_copies(original_kind, current);
CREATE INDEX IF NOT EXISTS idx_copies_ref ON state_copies(ref_id);
CREATE INDEX IF NOT EXISTS idx_copies_orig_article ON state_copies(original_article_id);
CREATE INDEX IF NOT EXISTS idx_copies_band ON state_copies(band);

-- Aggregate engagement counts the owner looked up by hand in the Meta Content Library and imported
-- (pipeline/engagement.py). Counts only: no post, no account, no link to a platform. key_type is url
-- (a state origin article URL) or outlet_month (an outlet id and a YYYY-MM).
CREATE TABLE IF NOT EXISTS engagement_aggregates (
    id INTEGER PRIMARY KEY,
    key_type TEXT NOT NULL,
    key TEXT NOT NULL,
    platform TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    posts_n INTEGER,
    reactions INTEGER,
    shares INTEGER,
    comments INTEGER,
    views INTEGER,
    suppressed INTEGER NOT NULL DEFAULT 0,  -- 1 when the cell was too small to publish
    query_date TEXT NOT NULL,
    mcl_query_ref TEXT,
    file_sha256 TEXT NOT NULL,        -- the imported file, so an import can be traced and repeated
    imported_at TEXT NOT NULL,
    UNIQUE(key_type, key, platform, period_start, period_end, query_date)
);
CREATE INDEX IF NOT EXISTS idx_engagement_key ON engagement_aggregates(key_type, key);
"""

# The research measure tables, in SCHEMA order (pipeline/measures.py). Report only: prune_gated_out and
# expire_seen never touch them.
MEASURE_TABLES = ("reference_items", "reference_feed_health", "state_copies", "engagement_aggregates")


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
    # Adnkronos serves one Immediapress release at /immediapress/<distributor>/<slug>_<id> and at
    # /immediapress/<slug>_<id>; the item id that ends both is the same, so the segment between is dropped.
    if netloc == "adnkronos.com":
        m = _ADNKRONOS_IMMEDIAPRESS.match(path)
        if m:
            path = "/immediapress/" + m.group(1)
    return urlunsplit((parts.scheme.lower() or "https", netloc, path, urlencode(query), ""))


_ADNKRONOS_IMMEDIAPRESS = re.compile(r"^/immediapress/[^/]+/([^/]+_[A-Za-z0-9]{16,26})$")


def connect(path: Path = config.DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}


def _once(conn: sqlite3.Connection, name: str, needed=None) -> bool:
    """Whether this connection is to run the one-time migration step `name` now.

    Each step runs once per database and is recorded in schema_migrations. The check and the run
    happen under BEGIN IMMEDIATE, so of several connections opened at once on one database (the
    fetch workers each open their own) exactly one runs the step; the others wait for the write
    lock, then find the row and skip it. When this returns True the row is already written in that
    open transaction and the caller's work joins it: the next commit stores both, and a failure
    before it leaves neither. `needed(conn)`, checked under the lock, says whether this database
    has anything for the step to do; when it has not (a new database, or one an earlier version
    already migrated) the step is recorded as run and skipped for good."""
    if conn.execute("SELECT 1 FROM schema_migrations WHERE name=?", (name,)).fetchall():
        return False
    conn.commit()   # the previous step's work, with its row
    conn.execute("BEGIN IMMEDIATE")
    if conn.execute("SELECT 1 FROM schema_migrations WHERE name=?", (name,)).fetchall():
        conn.commit()
        return False
    run = needed is None or bool(needed(conn))
    conn.execute("INSERT INTO schema_migrations(name, applied_at) VALUES (?,?)", (name, utcnow()))
    if not run:
        conn.commit()
    return run


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """ALTER TABLE table ADD COLUMN column decl, as a one-time step (_once) so that of several
    connections opened at once on a database without the column, one adds it and the rest skip it
    instead of failing with "duplicate column name". The caller checks the column first, so a
    database that already has it never takes the write lock."""
    if _once(conn, "%s.%s" % (table, column), lambda c: column not in _columns(c, table)):
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created by earlier schema versions. A step that changes
    data (a seed or a backfill) is guarded by _once, so it runs once per database however many
    connections are opened at the same time; so is every column it adds (_add_column), since a
    second ALTER TABLE ADD COLUMN for the same column fails with "duplicate column name"."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)")}
    if "page_labels" not in cols:
        _add_column(conn, "articles", "page_labels", "TEXT")
    if "themes" not in cols:
        _add_column(conn, "articles", "themes", "TEXT")
    if "audit_keep" not in cols and _once(conn, "articles.audit_keep", lambda c: "audit_keep" not in _columns(c, "articles")):
        conn.execute("ALTER TABLE articles ADD COLUMN audit_keep INTEGER")
        mark_gate_audit_keep(conn, commit=False)   # the rejections still stored; later ones at prune time
    if "day_of_record" not in cols:
        # The publication day of an item a backfill read brought in (backfill_day); NULL means the
        # discovery day, which is every item inserted before 2026-10-12.
        _add_column(conn, "articles", "day_of_record", "TEXT")
    if "extract_version" not in cols:
        # The extraction version a stored body was made under (config.EXTRACT_VERSION). NULL for every
        # body stored before 2026-10-12; extract.stale_extraction says which of those are stale.
        _add_column(conn, "articles", "extract_version", "TEXT")
    if "doctrine" not in cols:
        # Doctrine term matches (pipeline/doctrine.py), added 2026-10-13; NULL until an article is tagged.
        _add_column(conn, "articles", "doctrine", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_audit_keep ON articles(discovered_at) WHERE audit_keep=1")
    od = {r[1] for r in conn.execute("PRAGMA table_info(daily_outlet_discovery)")}
    if "gate_relevant" not in od and _once(conn, "daily_outlet_discovery.gate_relevant",
                                           lambda c: "gate_relevant" not in _columns(c, "daily_outlet_discovery")):
        conn.execute("ALTER TABLE daily_outlet_discovery ADD COLUMN gate_relevant INTEGER NOT NULL DEFAULT 0")
        # Relevant articles are never pruned, so their per outlet-day counts are exactly recoverable.
        conn.execute(
            """UPDATE daily_outlet_discovery SET gate_relevant = (
                 SELECT COUNT(*) FROM articles a WHERE a.gate_relevant=1 AND a.outlet_id=daily_outlet_discovery.outlet_id
                   AND substr(a.discovered_at, 1, 10)=daily_outlet_discovery.date)"""
        )
    cls = {r[1] for r in conn.execute("PRAGMA table_info(classifications)")}
    if "route" not in cls:
        _add_column(conn, "classifications", "route", "TEXT")   # filled by classify_rules.ensure_routes
    if "body_refreshed" not in cls:
        # Set on a current model label when the stub it was made on is replaced (classify_rules.note_body_refresh),
        # which queues the article for the model again (classify_llm.relabel_candidates). NULL on every older row.
        _add_column(conn, "classifications", "body_refreshed", "TEXT")
    if "issuer_type" not in cls:
        # Who issued the text a model label judged (classify_llm.ISSUER_TYPES), from prompt 2026-10-17.
        _add_column(conn, "classifications", "issuer_type", "TEXT")
    if "needs_review" not in cls:
        # Why a model reply was inconsistent (classify_llm.inconsistency_of), from prompt 2026-10-17.
        _add_column(conn, "classifications", "needs_review", "TEXT")
    cov = {r[1] for r in conn.execute("PRAGMA table_info(daily_coverage)")}
    batches = {r[1] for r in conn.execute("PRAGMA table_info(llm_batches)")}
    if "kind" not in batches:
        _add_column(conn, "llm_batches", "kind", "TEXT NOT NULL DEFAULT 'classify'")
    for col in ("top_discovered", "top_target", "top_china", "top_a", "top_b"):
        if col not in cov:
            _add_column(conn, "daily_coverage", col, "INTEGER NOT NULL DEFAULT 0")
    usage = {r[1] for r in conn.execute("PRAGMA table_info(llm_usage)")}
    if "cost_usd" not in usage and _once(conn, "llm_usage.cost_usd", lambda c: "cost_usd" not in _columns(c, "llm_usage")):
        conn.execute("ALTER TABLE llm_usage ADD COLUMN cache_creation_tokens INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE llm_usage ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE llm_usage ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0")
        # Days recorded before cost tracking are priced at the measured average per call. Batch
        # requests still outstanding on those days are left out: their cost lands when they are
        # collected, on the day they were submitted.
        outstanding = {}
        for b in conn.execute("SELECT submitted_at, article_ids FROM llm_batches WHERE status='submitted'"):
            day = (b["submitted_at"] or "")[:10]
            outstanding[day] = outstanding.get(day, 0) + len(json.loads(b["article_ids"] or "[]"))
        for r in conn.execute("SELECT date, calls FROM llm_usage").fetchall():
            priced = max(0, r["calls"] - outstanding.get(r["date"], 0))
            conn.execute("UPDATE llm_usage SET cost_usd=? WHERE date=?", (priced * config.LLM_MEASURED_COST_PER_CALL, r["date"]))
        conn.commit()
    polls = {r[1] for r in conn.execute("PRAGMA table_info(feed_polls)")}
    if polls and "collector" not in polls and _once(conn, "feed_polls.collector",
                                                    lambda c: "collector" not in _columns(c, "feed_polls")):
        # feed_polls gained collector on 2026-10-12, inside its primary key, so the relay's polls of a
        # feed (from its bundle) and the runner's own polls of the same feed are kept apart. SQLite
        # cannot change a primary key in place, so the table is rebuilt. Every row recorded before
        # then was made by the process that owns this database: the runner for the main database,
        # the relay for its own.
        conn.execute("ALTER TABLE feed_polls RENAME TO feed_polls_pre_collector")
        start = SCHEMA.index("CREATE TABLE IF NOT EXISTS feed_polls")
        conn.execute(SCHEMA[start:SCHEMA.index(");", start) + 2])
        conn.execute(
            """INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, saturated, entries, new_items,
                                     missed_estimate, collector)
               SELECT date, feed_url, outlet_id, country, polls, saturated, entries, new_items, missed_estimate, ?
               FROM feed_polls_pre_collector""", (config.COLLECTOR,))
        conn.execute("DROP TABLE feed_polls_pre_collector")
        conn.commit()
    # The missed-items estimator a row's estimates were made by, added 2026-10-12. Every row already
    # recorded was made by the earlier estimator and takes the column's default, v1.
    if polls and "estimator" not in _columns(conn, "feed_polls"):
        _add_column(conn, "feed_polls", "estimator", "TEXT NOT NULL DEFAULT 'v1'")
    if "estimator" not in _columns(conn, "feed_poll_log"):
        _add_column(conn, "feed_poll_log", "estimator", "TEXT NOT NULL DEFAULT 'v1'")
    # The newest entry date a feed's last successful poll listed, added 2026-10-17, so a feed that answers
    # with old items is told from a working one (export, config.FEED_FROZEN_DAYS). NULL until polled again.
    if "newest_entry" not in _columns(conn, "feed_health"):
        _add_column(conn, "feed_health", "newest_entry", "TEXT")
    if "reference_feed_health" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} \
            and "newest_entry" not in _columns(conn, "reference_feed_health"):
        _add_column(conn, "reference_feed_health", "newest_entry", "TEXT")   # mirrors feed_health
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "planted_flags" not in tables or "planted_state" not in tables:
        # Databases opened without the schema script. Both statements are CREATE IF NOT EXISTS.
        start = SCHEMA.index("CREATE TABLE IF NOT EXISTS planted_flags")
        conn.executescript(SCHEMA[start:])
    if "body_fingerprints" not in tables or "body_links" not in tables:
        # Body near-duplicate tables (pipeline.body_dups), added 2026-10-12. CREATE IF NOT EXISTS only.
        start = SCHEMA.index("CREATE TABLE IF NOT EXISTS body_fingerprints")
        conn.executescript(SCHEMA[start:])
    if not set(MEASURE_TABLES) <= tables:
        # Research measure tables (pipeline/measures.py), added 2026-10-13. CREATE IF NOT EXISTS only.
        start = SCHEMA.index("CREATE TABLE IF NOT EXISTS reference_items")
        conn.executescript(SCHEMA[start:])
    # Nothing to do, only a record: its applied_at is when this database was first opened by code that
    # remembers pruned addresses (seen_urls), so the export can say from which day the discovery counts
    # stop counting a pruned item again (export.rediscovery, config.REDISCOVERY_FIXED_FROM).
    _once(conn, "seen_urls.remembering", lambda c: False)
    # The two seeds below are for a database made before the discovery tables existed. They run once
    # (on a new database they find no articles and are recorded as done); every later discovery is
    # counted at insert time by record_discovery.
    if _once(conn, "daily_discovery.seed", lambda c: c.execute("SELECT COUNT(*) FROM daily_discovery").fetchone()[0] == 0
             and c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]):
        # Seed from whatever rows survive. Days already pruned are undercounted and stay so.
        conn.execute(
            """INSERT INTO daily_discovery(date, country, discovered, gate_relevant)
               SELECT COALESCE(day_of_record, substr(discovered_at, 1, 10)), country, COUNT(*),
                      COALESCE(SUM(gate_relevant), 0)
               FROM articles GROUP BY 1, 2"""
        )
    if _once(conn, "daily_outlet_discovery.seed", lambda c: c.execute("SELECT COUNT(*) FROM daily_outlet_discovery").fetchone()[0] == 0
             and c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]):
        conn.execute(
            """INSERT INTO daily_outlet_discovery(date, outlet_id, country, discovered)
               SELECT COALESCE(day_of_record, substr(discovered_at, 1, 10)), outlet_id, country, COUNT(*)
               FROM articles GROUP BY 1, 2, 3"""
        )
    # Since 2026-10-17 an Adnkronos Immediapress address with a distributor segment hashes as the address
    # without it (canonical_url). A row stored before takes the new hash, so a feed listing the page again
    # finds it instead of storing it twice. A row whose twin at the other address is stored as well keeps
    # its hash, which is unique, and the export counts the two once (pipeline/placements.py).
    if _once(conn, "articles.url_hash.adnkronos_immediapress", lambda c: c.execute(
            "SELECT 1 FROM articles WHERE url LIKE '%adnkronos.com/immediapress/%/%' LIMIT 1").fetchone()):
        rehash_moved_addresses(conn, "%adnkronos.com/immediapress/%/%")
    conn.commit()


def rehash_moved_addresses(conn: sqlite3.Connection, like: str) -> Dict[str, int]:
    """Give the stored rows whose url matches `like` the hash canonical_url now gives their address, after
    a change to canonical_url. The body file is copied to the new hash's name (the old file is left, so a
    failure before the commit loses nothing). A row whose new hash another row already holds keeps its own
    and is counted in "kept"."""
    import shutil
    moved = kept = 0
    for r in conn.execute("SELECT id, url, url_hash FROM articles WHERE url LIKE ?", (like,)).fetchall():
        h = url_hash(r["url"])
        if h == r["url_hash"]:
            continue
        if conn.execute("SELECT 1 FROM articles WHERE url_hash=?", (h,)).fetchall():
            kept += 1
            continue
        conn.execute("UPDATE articles SET url_hash=? WHERE id=?", (h, r["id"]))
        old, new = body_path(r["url_hash"]), body_path(h)
        if old.exists() and not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old, new)
        moved += 1
    return {"moved": moved, "kept": kept}


def backfill_day(published_at: Optional[str], now: Optional[dt.datetime] = None) -> Optional[str]:
    """The day of record of an item a backfill read brings in: its publication day (UTC) when it is
    discovered more than config.BACKFILL_LAG_HOURS after it was published and was published on or
    after config.COLLECTION_START; otherwise None, which means the discovery day. A published time
    that cannot be read, or that lies after discovery, is not credible and leaves the discovery day.
    The caller stores the result in articles.day_of_record and passes it to record_discovery, so the
    item's count and its share of the discovery denominator move together."""
    if not published_at:
        return None
    try:
        pub = dt.datetime.fromisoformat(published_at.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    pub = pub if pub.tzinfo else pub.replace(tzinfo=dt.timezone.utc)
    pub = pub.astimezone(dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    if pub.date().isoformat() < config.COLLECTION_START or pub > now:
        return None
    if (now - pub).total_seconds() <= config.BACKFILL_LAG_HOURS * 3600:
        return None
    return pub.date().isoformat()


def record_discovery(conn: sqlite3.Connection, country: str, relevant: bool, outlet_id: Optional[str] = None,
                     day: Optional[str] = None) -> None:
    """Count one newly discovered item on its day of record: `day` (backfill_day) or today."""
    today = day or dt.datetime.now(dt.timezone.utc).date().isoformat()
    conn.execute(
        """INSERT INTO daily_discovery(date, country, discovered, gate_relevant) VALUES (?,?,1,?)
           ON CONFLICT(date, country) DO UPDATE SET discovered=discovered+1,
             gate_relevant=gate_relevant+excluded.gate_relevant""",
        (today, country, 1 if relevant else 0),
    )
    if outlet_id:
        conn.execute(
            """INSERT INTO daily_outlet_discovery(date, outlet_id, country, discovered, gate_relevant) VALUES (?,?,?,1,?)
               ON CONFLICT(date, outlet_id) DO UPDATE SET discovered=discovered+1,
                 gate_relevant=gate_relevant+excluded.gate_relevant""",
            (today, outlet_id, country, 1 if relevant else 0),
        )


def rebuild_daily_discovery(conn: sqlite3.Connection) -> int:
    """daily_discovery is the per country sum of the per outlet-day table. The outlet table is
    the source of truth because the relay replaces its outlets' rows wholesale."""
    conn.execute("DELETE FROM daily_discovery")
    cur = conn.execute(
        """INSERT INTO daily_discovery(date, country, discovered, gate_relevant)
           SELECT date, country, SUM(discovered), SUM(gate_relevant) FROM daily_outlet_discovery GROUP BY date, country"""
    )
    conn.commit()
    return cur.rowcount


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

def seen_key(h: str) -> int:
    """The seen_urls key of a url_hash: its first 15 hex digits as an integer (60 bits; among ten million
    addresses the chance that any two share a key is about one in twenty thousand)."""
    try:
        return int(h[:15], 16)
    except (TypeError, ValueError):
        return int(hashlib.sha256(str(h).encode("utf-8")).hexdigest()[:15], 16)


def _day_int(day: Optional[str] = None) -> int:
    """A YYYY-MM-DD day (or the start of an ISO time; today when None) as the integer YYYYMMDD."""
    return int((day or dt.datetime.now(dt.timezone.utc).date().isoformat())[:10].replace("-", ""))


def seen_before(conn: sqlite3.Connection, h: str) -> Optional[tuple]:
    """For a pruned article with this url_hash: (the day of record, YYYY-MM-DD, its discovery was counted
    on, whether it was gate relevant then); None for any other."""
    r = conn.execute("SELECT first_day, relevant FROM seen_urls WHERE h=?", (seen_key(h),)).fetchone()
    if r is None:
        return None
    s = "%08d" % r[0]
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:]), bool(r[1])


def touch_seen(conn: sqlite3.Connection, h: str) -> None:
    """A feed lists a pruned article again: it is remembered from today (config.SEEN_URL_RETENTION_DAYS).
    At most one write per article a day."""
    today = _day_int()
    conn.execute("UPDATE seen_urls SET last_day=?, seen_again=1 WHERE h=? AND (last_day<? OR seen_again=0)",
                 (today, seen_key(h), today))


def forget_seen(conn: sqlite3.Connection, h: str) -> None:
    conn.execute("DELETE FROM seen_urls WHERE h=?", (seen_key(h),))


def remember_pruned(conn: sqlite3.Connection, where: str, params) -> int:
    """Before a `DELETE FROM articles WHERE <where>`: remember every row it will delete in seen_urls, on
    the day of record its discovery was counted on, last seen on its discovery day. Returns the rows."""
    rows = conn.execute("SELECT url_hash, day_of_record, discovered_at, gate_relevant FROM articles WHERE " + where,
                        params).fetchall()
    conn.executemany("INSERT OR IGNORE INTO seen_urls(h, first_day, last_day, seen_again, relevant) VALUES (?,?,?,0,?)",
                     [(seen_key(r[0]), _day_int(r[1] or r[2]), _day_int(r[2]), 1 if r[3] else 0) for r in rows])
    return len(rows)


def expire_seen(conn: sqlite3.Connection, now: Optional[dt.datetime] = None) -> int:
    """Forget what no feed has listed for config.SEEN_URL_RETENTION_DAYS, and what no feed listed again
    within config.SEEN_URL_UNSEEN_DAYS of its discovery."""
    today = (now or dt.datetime.now(dt.timezone.utc)).date()
    again = _day_int((today - dt.timedelta(days=config.SEEN_URL_RETENTION_DAYS)).isoformat())
    once = _day_int((today - dt.timedelta(days=config.SEEN_URL_UNSEEN_DAYS)).isoformat())
    return conn.execute("DELETE FROM seen_urls WHERE last_day < (CASE seen_again WHEN 1 THEN ? ELSE ? END)",
                        (again, once)).rowcount


def record_relevant_again(conn: sqlite3.Connection, country: str, outlet_id: Optional[str], day: str) -> None:
    """A pruned rejected article back in the corpus: its discovery was counted on `day` when it was first
    seen, so only the gate relevant side of that day's counters moves (as fetch_feeds.readmit_gated_out)."""
    conn.execute("UPDATE daily_discovery SET gate_relevant=gate_relevant+1 WHERE date=? AND country=?", (day, country))
    if outlet_id:
        conn.execute("UPDATE daily_outlet_discovery SET gate_relevant=gate_relevant+1 WHERE date=? AND outlet_id=?",
                     (day, outlet_id))


def insert_discovered(conn: sqlite3.Connection, item: Dict) -> Optional[int]:
    """Insert a discovered item. Returns the new id, or None if already known.

    An article a prune deleted (seen_urls) is known too. Still outside the gate, it is not inserted
    again, and the sighting keeps it remembered. Now inside it (a gate-exempt section feed, or a summary
    its first sighting lacked), it is inserted on the day of record it was first counted on, and
    item["seen_day"] names that day: the caller counts no discovery for it (record_relevant_again). One
    pruned while relevant (the relay's handed-off rows) is never inserted again."""
    h = url_hash(item["url"])
    seen = seen_before(conn, h)
    if seen is not None:
        if seen[1] or not item.get("gate_relevant"):
            touch_seen(conn, h)
            return None
        seen = seen[0]
        item["day_of_record"] = seen
    # One stamp: the caller counts the discovery on this day (item["discovered_at"]), not on the clock
    # read after the insert, which a lock wait can carry past midnight.
    stamp = utcnow()
    cur = conn.execute(
        """INSERT OR IGNORE INTO articles
           (url, url_hash, outlet_id, country, language, feed_url, title, summary, author,
            published_at, discovered_at, status, gate_relevant, gate_terms, day_of_record)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item["url"], h, item["outlet_id"], item["country"], item["language"], item.get("feed_url"),
         item.get("title"), item.get("summary"), item.get("author"), item.get("published_at"),
         stamp, item.get("status", "discovered"), item.get("gate_relevant"),
         json.dumps(item.get("gate_terms") or []), item.get("day_of_record")),
    )
    if cur.rowcount == 0:
        return None
    item["discovered_at"] = stamp
    if seen is not None:
        forget_seen(conn, h)        # stored again, as relevant, and relevant articles are never pruned
        item["seen_day"] = seen
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


def _write_atomic(p: Path, data: bytes) -> None:
    """Write `data` to `p` through a temporary file in the same directory and os.replace: a reader sees
    the old file or the whole new one, never a half written one (a killed run, a full disk)."""
    import os
    import tempfile
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), 0o644)       # as the plain open it replaces; mkstemp makes 0600
            fh.write(data)
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_body(h: str, text: str) -> str:
    import gzip
    data = text.encode("utf-8")
    _write_atomic(body_path(h), gzip.compress(data))
    return hashlib.sha256(data).hexdigest()[:32]


# Body files that exist but cannot be read (truncated, not gzip, not UTF-8), by url_hash, this process.
UNREADABLE_BODIES: Dict[str, str] = {}


def unreadable_body_count() -> int:
    return len(UNREADABLE_BODIES)


def load_body(h: str) -> Optional[str]:
    """The stored body, or None when there is none or its file cannot be read. An unreadable file is
    logged once and counted in UNREADABLE_BODIES; every caller treats it as a missing body (fetched
    again where that is allowed), so one damaged file never stops a stage."""
    import gzip
    import sys
    import zlib
    p = body_path(h)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, EOFError, ValueError, zlib.error) as exc:
        if h not in UNREADABLE_BODIES:
            print("unreadable body %s: %s: %s" % (h, type(exc).__name__, str(exc)[:120]), file=sys.stderr)
        UNREADABLE_BODIES[h] = type(exc).__name__
        return None


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
                          ruleset_version: str = config.RULESET_VERSION,
                          route: Optional[str] = None, issuer_type: Optional[str] = None,
                          needs_review: Optional[str] = None) -> int:
    """Insert a new current classification, marking earlier ones for the article as not current.
    Earlier rows are never deleted. route is set on state origin labels only, issuer_type on model labels."""
    conn.execute("UPDATE classifications SET is_current=0 WHERE article_id=?", (article_id,))
    cur = conn.execute(
        """INSERT INTO classifications
           (article_id, method, category, confidence, evidence_quote, reasoning, signatures_fired,
            china_sources_cited, independent_confirmation_present, confirmation_evidence,
            model_version, ruleset_version, classified_at, is_current, raw_response, route, issuer_type, needs_review)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)""",
        (article_id, method, category, confidence, evidence_quote, reasoning,
         json.dumps(signatures_fired or []), json.dumps(china_sources_cited or []),
         None if independent_confirmation_present is None else int(independent_confirmation_present),
         confirmation_evidence, model_version, ruleset_version, utcnow(), raw_response, route, issuer_type, needs_review),
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
                       error: Optional[str], entries: int, newest_entry: Optional[str] = None) -> None:
    """newest_entry is the newest entry date the poll listed; None (a failed poll, undated entries, or
    a caller that does not know) keeps the date on record."""
    now = utcnow()
    row = conn.execute("SELECT consecutive_failures, total_checks, total_failures FROM feed_health WHERE feed_url=?",
                       (feed_url,)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, last_error, last_entries,
               consecutive_failures, total_checks, total_failures, newest_entry) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (feed_url, outlet_id, now, now if ok else None, error, entries, 0 if ok else 1, 1, 0 if ok else 1,
             newest_entry if ok else None),
        )
    else:
        cf = 0 if ok else row["consecutive_failures"] + 1
        conn.execute(
            """UPDATE feed_health SET outlet_id=?, last_checked=?, last_ok=COALESCE(?, last_ok), last_error=?,
               last_entries=?, consecutive_failures=?, total_checks=total_checks+1,
               total_failures=total_failures+?, newest_entry=COALESCE(?, newest_entry) WHERE feed_url=?""",
            (outlet_id, now, now if ok else None, error, entries, cf, 0 if ok else 1,
             newest_entry if ok else None, feed_url),
        )


def feed_health_row(conn: sqlite3.Connection, feed_url: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM feed_health WHERE feed_url=?", (feed_url,)).fetchone()


def record_reference_feed_health(conn: sqlite3.Connection, source_id: str, feed_url: str, ok: bool,
                                 error: Optional[str], entries: int) -> None:
    """record_feed_health for a reference source feed (pipeline/reference.py), into reference_feed_health.
    Kept apart so a reference feed never appears among the monitored outlets' feed health."""
    now = utcnow()
    row = conn.execute("SELECT consecutive_failures FROM reference_feed_health WHERE feed_url=?", (feed_url,)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO reference_feed_health(feed_url, source_id, last_checked, last_ok, last_error, last_entries,
               consecutive_failures, total_checks, total_failures) VALUES (?,?,?,?,?,?,?,?,?)""",
            (feed_url, source_id, now, now if ok else None, error, entries, 0 if ok else 1, 1, 0 if ok else 1),
        )
    else:
        cf = 0 if ok else row["consecutive_failures"] + 1
        conn.execute(
            """UPDATE reference_feed_health SET source_id=?, last_checked=?, last_ok=COALESCE(?, last_ok), last_error=?,
               last_entries=?, consecutive_failures=?, total_checks=total_checks+1,
               total_failures=total_failures+? WHERE feed_url=?""",
            (source_id, now, now if ok else None, error, entries, cf, 0 if ok else 1, feed_url),
        )


# A feed_polls row whose polls were estimated by more than one estimator version (the day an
# estimator changed): its estimate belongs to neither, so the export leaves it out. ESTIMATOR_MERGE is
# the upsert clause that keeps a row's estimator while every poll added agrees with it.
ESTIMATOR_MIXED = "mixed"
ESTIMATOR_MERGE = ("estimator=CASE WHEN estimator=excluded.estimator THEN estimator ELSE '%s' END"
                   % ESTIMATOR_MIXED)

# How long feed_poll_log keeps a poll. Longer than the relay bundle carries polls
# (pipeline.relay.POLL_EVENT_DAYS), so a poll sent again is recognised and never counted twice.
POLL_LOG_DAYS = 5


def last_poll_at(conn: sqlite3.Connection, feed_url: str, before: Optional[str] = None) -> Optional[str]:
    """The latest logged successful poll of the feed by either collector, before `before` if given."""
    if before:
        r = conn.execute("SELECT MAX(polled_at) FROM feed_poll_log WHERE feed_url=? AND polled_at<?",
                         (feed_url, before)).fetchone()
    else:
        r = conn.execute("SELECT MAX(polled_at) FROM feed_poll_log WHERE feed_url=?", (feed_url,)).fetchone()
    return r[0] if r else None


def log_feed_poll(conn: sqlite3.Connection, feed_url: str, collector: str, polled_at: str, outlet_id: str,
                  country: str, entries: int, new_items: int, times: List[str], prev_poll_at: Optional[str],
                  saturated_own: bool, missed_own: float, saturated: Optional[bool] = None,
                  missed_estimate: Optional[float] = None, estimator: Optional[str] = None) -> bool:
    """Log one poll of a shared feed. Returns False when it is logged already. estimator is the
    missed-items estimator the estimate was made by (config.MISSED_ESTIMATOR_VERSION unless given)."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO feed_poll_log(feed_url, collector, polled_at, outlet_id, country, entries, new_items,
             times, prev_poll_at, saturated_own, missed_own, saturated, missed_estimate, estimator)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (feed_url, collector, polled_at, outlet_id, country, entries, new_items, json.dumps(sorted(times or [])),
         prev_poll_at, 1 if saturated_own else 0, missed_own or 0.0,
         1 if (saturated_own if saturated is None else saturated) else 0,
         (missed_own if missed_estimate is None else missed_estimate) or 0.0,
         estimator or config.MISSED_ESTIMATOR_VERSION))
    return cur.rowcount == 1


def prune_poll_log(conn: sqlite3.Connection, days: int = POLL_LOG_DAYS) -> int:
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).replace(microsecond=0).isoformat()
    return conn.execute("DELETE FROM feed_poll_log WHERE polled_at<?", (cutoff,)).rowcount


def record_feed_poll(conn: sqlite3.Connection, outlet_id: str, country: str, feed_url: str, entries: int,
                     new_items: int, saturated: bool, missed_estimate: float, collector: Optional[str] = None,
                     estimator: Optional[str] = None) -> None:
    """One poll of a feed, added to today's row for this feed and the collector that made it
    (config.COLLECTOR unless given). estimator is the missed-items estimator the estimate was made by
    (config.MISSED_ESTIMATOR_VERSION unless given); a row that already holds polls estimated by
    another version becomes mixed (ESTIMATOR_MIXED), which the export leaves out of the estimate."""
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    conn.execute(
        """INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, saturated, entries, new_items, missed_estimate,
                                  collector, estimator)
           VALUES (?,?,?,?,1,?,?,?,?,?,?)
           ON CONFLICT(date, feed_url, collector) DO UPDATE SET outlet_id=excluded.outlet_id, country=excluded.country,
             polls=polls+1, saturated=saturated+excluded.saturated, entries=entries+excluded.entries,
             new_items=new_items+excluded.new_items, missed_estimate=missed_estimate+excluded.missed_estimate,
             """ + ESTIMATOR_MERGE,
        (today, feed_url, outlet_id, country, 1 if saturated else 0, entries, new_items, missed_estimate or 0.0,
         collector or config.COLLECTOR, estimator or config.MISSED_ESTIMATOR_VERSION),
    )


def record_llm_sampling(conn: sqlite3.Connection, date: str, allocation: Dict[str, List[int]]) -> None:
    """allocation: {country: [eligible, drawn]}. Eligible keeps the day's largest pool and drawn
    accumulates over the day's runs.

    The two are NOT a sampling fraction and nothing reweights by them. Eligible is a high water mark
    of a backlog that grows during the day while drawn is a running total, the synchronous and batch
    paths record different things in the drawn column (calls made versus the planned quota), and the
    row is keyed by the run day while the counts it would correct are keyed by the discovery day.
    Treat the pair as a diagnostic of how bound a day's draw was, which is what METHODOLOGY.md says."""
    for country, (eligible, drawn) in allocation.items():
        conn.execute(
            """INSERT INTO llm_sampling(date, country, eligible, drawn) VALUES (?,?,?,?)
               ON CONFLICT(date, country) DO UPDATE SET eligible=MAX(eligible, excluded.eligible), drawn=drawn+excluded.drawn""",
            (date, country, eligible, drawn),
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
                     ceiling_hit: bool = False, cache_creation_tokens: int = 0, cache_read_tokens: int = 0,
                     cost_usd: float = 0.0, date: Optional[str] = None) -> None:
    """Add to a day's usage row. date defaults to today; a batch result collected later is booked
    on the day its request was submitted, so a day's calls and their cost sit on the same row."""
    day = date or dt.datetime.now(dt.timezone.utc).date().isoformat()
    conn.execute(
        """INSERT INTO llm_usage(date, calls, input_tokens, output_tokens, ceiling_hit,
                                 cache_creation_tokens, cache_read_tokens, cost_usd) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET calls=calls+excluded.calls,
             input_tokens=input_tokens+excluded.input_tokens,
             output_tokens=output_tokens+excluded.output_tokens,
             ceiling_hit=MAX(ceiling_hit, excluded.ceiling_hit),
             cache_creation_tokens=cache_creation_tokens+excluded.cache_creation_tokens,
             cache_read_tokens=cache_read_tokens+excluded.cache_read_tokens,
             cost_usd=cost_usd+excluded.cost_usd""",
        (day, calls, input_tokens, output_tokens, 1 if ceiling_hit else 0,
         cache_creation_tokens, cache_read_tokens, cost_usd),
    )


def record_model_agreement(conn: sqlite3.Connection, study: Dict, pairs: Iterable[Dict]) -> int:
    """Store a second rater study and every pair of labels behind it.

    study carries method, model_a, model_b, sample_size, kappa_all, kappa_bc, n_bc and a details
    dict. Each pair carries article_id and both raters' category, confidence and evidence quote."""
    cur = conn.execute(
        """INSERT INTO model_agreement_studies(computed_at, method, model_a, model_b, sample_size,
           kappa_all, kappa_bc, n_bc, details) VALUES (?,?,?,?,?,?,?,?,?)""",
        (utcnow(), study.get("method", "model_vs_model"), study["model_a"], study["model_b"],
         study["sample_size"], study.get("kappa_all"), study.get("kappa_bc"), study.get("n_bc"),
         json.dumps(study.get("details") or {})),
    )
    study_id = cur.lastrowid
    for p in pairs:
        conn.execute(
            """INSERT OR REPLACE INTO model_agreement_labels(study_id, article_id, category_a, confidence_a,
               evidence_a, category_b, confidence_b, evidence_b) VALUES (?,?,?,?,?,?,?,?)""",
            (study_id, p["article_id"], p["category_a"], p.get("confidence_a"), p.get("evidence_a"),
             p["category_b"], p.get("confidence_b"), p.get("evidence_b")),
        )
    conn.commit()
    return study_id


def latest_model_agreement(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM model_agreement_studies ORDER BY id DESC LIMIT 1").fetchone()


def gate_audit_keep(h: str, share: Optional[float] = None) -> bool:
    """Whether the rejected item with url_hash `h` belongs to the permanent gate audit sample.
    A salted SHA-256 of the url_hash, read as a uniform number in [0, 1), is compared with the
    share: reproducible, the same on every machine, and blind to outlet, country and language."""
    share = config.GATE_AUDIT_KEEP_SHARE if share is None else share
    d = hashlib.sha256(("gate_audit_keep:" + (h or "")).encode("utf-8")).digest()
    return int.from_bytes(d[:8], "big") / 2.0 ** 64 < share


def mark_gate_audit_keep(conn: sqlite3.Connection, commit: bool = True) -> int:
    """Decide audit_keep for every gated-out item not yet decided. Rows reach the table by more
    than one path (discovery, the relay), so the decision is taken here rather than at insert.
    Returns the number newly marked as kept."""
    rows = conn.execute(
        "SELECT id, url_hash FROM articles WHERE status='gated_out' AND gate_relevant=0 AND audit_keep IS NULL").fetchall()
    if not rows:
        return 0
    marks = [(1 if gate_audit_keep(r[1]) else 0, r[0]) for r in rows]
    conn.executemany("UPDATE articles SET audit_keep=? WHERE id=?", marks)
    if commit:
        conn.commit()
    return sum(m for m, _ in marks)


def prune_gated_out(conn: sqlite3.Connection, days: int = config.GATED_OUT_RETENTION_DAYS) -> int:
    """Delete items the gate rejected more than `days` ago. Relevant items are never deleted, and
    neither is a rejected item that carries a planted byline flag: the flag is about the byline,
    not about China, and the row it points at has to stay readable. Nor is the random share
    (config.GATE_AUDIT_KEEP_SHARE) kept for the gate audit, with every field it was rejected with.
    Nor is a rejected item that holds a label or a human review, current or withdrawn: an item
    gated out after it was classified (pipeline/section_retag.py) keeps its labels, which are never
    deleted, and the foreign keys would refuse the whole statement.
    Each deleted item's address is remembered (seen_urls), so a feed that still lists it does not
    bring it back and count it again; addresses no feed lists any more are forgotten (expire_seen)."""
    mark_gate_audit_keep(conn, commit=False)
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    where = """gate_relevant=0 AND status='gated_out' AND discovered_at<?
           AND COALESCE(audit_keep, 0)=0
           AND id NOT IN (SELECT article_id FROM planted_flags)
           AND id NOT IN (SELECT article_id FROM classifications)
           AND id NOT IN (SELECT article_id FROM human_reviews)"""
    remember_pruned(conn, where, (cutoff,))
    cur = conn.execute("DELETE FROM articles WHERE " + where, (cutoff,))
    expire_seen(conn)
    conn.commit()
    return cur.rowcount


def vacuum(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.execute("VACUUM")


def table_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    out = {}
    for t in ("outlets", "articles", "classifications", "human_reviews", "daily_counts", "daily_discovery", "feed_health", "run_log"):
        out[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    return out
