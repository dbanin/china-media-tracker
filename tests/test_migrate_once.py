"""One-time migration steps run once per database, however many connections open it at once.

store._migrate used to seed daily_discovery whenever the table was empty, on every new connection.
Fetch workers each open their own connection, so on a new database several of them checked, found
it empty and seeded together: duplicate rows, or "database is locked". Each step is now checked and
run under BEGIN IMMEDIATE and recorded in schema_migrations in the same transaction (store._once).
"""
import json
import re
import sqlite3
import threading

from pipeline import config, fetch_articles as fa, store


def _article(conn, i, domain="a.test", **extra):
    item = {"url": "https://%s/story-%d" % (domain, i), "outlet_id": "o%d" % (i % 3), "country": "XX",
            "language": "en", "gate_relevant": 1}
    item.update(extra)
    return store.insert_discovered(conn, item)


def _open_at_once(db, n=6):
    """Open n connections on db from n threads released together. Returns the errors raised."""
    barrier = threading.Barrier(n)
    errors = []

    def go():
        barrier.wait()
        try:
            store.connect(db).close()
        except Exception as exc:  # noqa: BLE001  collected for the assertion
            errors.append(repr(exc))

    threads = [threading.Thread(target=go) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not any(t.is_alive() for t in threads), "a connection never returned"
    return errors


def test_concurrent_connections_seed_an_unseeded_database_once(tmp_path):
    # A database made before the discovery tables: articles, empty daily tables, no migration record.
    db = tmp_path / "old.db"
    conn = store.connect(db)
    for i in range(30):
        _article(conn, i)
    conn.execute("DELETE FROM daily_discovery")
    conn.execute("DELETE FROM daily_outlet_discovery")
    conn.execute("DELETE FROM schema_migrations")
    conn.commit()
    conn.close()
    for _ in range(5):   # the race is timing dependent; give it several chances
        assert _open_at_once(db) == []
    conn = store.connect(db)
    assert conn.execute("SELECT SUM(discovered) FROM daily_discovery").fetchone()[0] == 30
    assert conn.execute("SELECT SUM(discovered) FROM daily_outlet_discovery").fetchone()[0] == 30
    names = [r[0] for r in conn.execute("SELECT name FROM schema_migrations")]
    assert names.count("daily_discovery.seed") == 1 and names.count("daily_outlet_discovery.seed") == 1


def test_concurrent_connections_on_a_new_database_do_not_lock_and_the_seed_is_recorded(tmp_path):
    db = tmp_path / "new.db"
    assert _open_at_once(db) == []
    conn = store.connect(db)
    assert {r[0] for r in conn.execute("SELECT name FROM schema_migrations")} >= \
        {"daily_discovery.seed", "daily_outlet_discovery.seed"}
    # Recorded as done, so it never runs again: an article stored without record_discovery is not
    # turned into a discovery count by the next connection.
    _article(conn, 1)
    conn.commit()
    conn.close()
    conn = store.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM daily_discovery").fetchone()[0] == 0


def test_a_column_backfill_runs_once_under_concurrent_connections(tmp_path):
    # An llm_usage table from before cost tracking: the three ALTERs and the pricing must run once,
    # not fail with "duplicate column name" in the connections that lost the race.
    db = tmp_path / "usage.db"
    raw = sqlite3.connect(str(db))
    raw.executescript(store.SCHEMA)
    raw.executescript("""
        DROP TABLE llm_usage;
        CREATE TABLE llm_usage (date TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
            ceiling_hit INTEGER NOT NULL DEFAULT 0);
        INSERT INTO llm_usage VALUES ('2026-09-15', 1000, 1, 1, 0);
    """)
    raw.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version) VALUES (?,?,?,?,?)",
                ("b1", "2026-09-15T01:00:00+00:00", "submitted", json.dumps(list(range(100))), "m"))
    raw.commit()
    raw.close()
    assert _open_at_once(db) == []
    conn = store.connect(db)
    cost = conn.execute("SELECT cost_usd FROM llm_usage").fetchone()[0]
    assert abs(cost - 900 * config.LLM_MEASURED_COST_PER_CALL) < 1e-9
    assert [r[0] for r in conn.execute("SELECT name FROM schema_migrations WHERE name='llm_usage.cost_usd'")] == \
        ["llm_usage.cost_usd"]


PAGE = "<html><body><article><p>%s</p></article></body></html>" % ("Officials from China and the region met on trade. " * 20)


def test_fetch_with_two_workers_over_two_domains_completes_on_a_new_database(monkeypatch, tmp_path):
    db = tmp_path / "fetch.db"
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "RAW_HTML_DIR", tmp_path / "raw")
    conn = store.connect(db)
    for i, domain in enumerate(["a.test", "b.test", "a.test", "b.test", "a.test", "b.test"]):
        _article(conn, i, domain=domain, status="queued")
        store.record_discovery(conn, "XX", True)
    conn.commit()
    real_connect = store.connect
    monkeypatch.setattr(store, "connect", lambda path=db: real_connect(path))
    monkeypatch.setattr(fa, "can_fetch", lambda url: (True, "robots_allowed"))
    monkeypatch.setattr(fa, "get_page", lambda url: (PAGE, 200, None))
    monkeypatch.setattr(fa, "domain_gap", lambda domain: 0.0)
    counts = fa.run(conn, "t", workers=2)
    assert counts["domains"] == 2 and counts["fetched"] == 6 and counts["failed"] == 0
    assert {r[0] for r in conn.execute("SELECT status FROM articles")} == {"fetched"}
    # The discovery counts are the ones recorded at insert, not seeded again by a worker.
    assert conn.execute("SELECT SUM(discovered) FROM daily_discovery").fetchone()[0] == 6


OLD_COLUMNS = [("articles", "day_of_record"), ("articles", "extract_version"), ("articles", "page_labels"),
               ("articles", "themes"), ("classifications", "route"), ("llm_batches", "kind"),
               ("daily_coverage", "top_discovered"), ("daily_coverage", "top_b")]


def _drop_old_columns(raw, old_columns):
    """Rebuild an old database by removing columns. ALTER TABLE ... DROP COLUMN on the stored table
    fails on the GitHub runner's SQLite (older than the owner's Mac) with "incomplete input", because
    the CREATE TABLE text carries inline "--" comments. So each table is copied into a comment-free
    twin, the columns are dropped there, and the twin replaces the table. Indexes go with the old
    table; store.connect creates them again. Only this test removes columns."""
    by_table = {}
    for table, col in old_columns:
        by_table.setdefault(table, []).append(col)
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("PRAGMA legacy_alter_table=ON")
    for table, cols in by_table.items():
        sql = raw.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        twin = table + "__old_shape"
        sql = re.sub(r"--[^\n]*", "", sql)
        sql = re.sub(r"CREATE TABLE (IF NOT EXISTS )?%s\b" % table, "CREATE TABLE %s" % twin, sql, count=1)
        raw.execute(sql)
        raw.execute("INSERT INTO %s SELECT * FROM %s" % (twin, table))
        for col in cols:
            raw.execute("ALTER TABLE %s DROP COLUMN %s" % (twin, col))
        raw.execute("DROP TABLE %s" % table)
        raw.execute("ALTER TABLE %s RENAME TO %s" % (twin, table))
    raw.commit()
    raw.execute("PRAGMA legacy_alter_table=OFF")
    assert raw.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_concurrent_connections_add_each_missing_column_once(tmp_path):
    # The runner's database before day_of_record and extract_version (and the older plain column
    # adds): the first run after deploy opens several connections at once, and the ones that lose the
    # race must skip the ALTER, not fail with "duplicate column name".
    for attempt in range(3):   # the race is timing dependent; give it several chances
        db = tmp_path / ("cols%d.db" % attempt)
        conn = store.connect(db)
        for i in range(5):
            _article(conn, i)
        conn.commit()
        conn.close()
        raw = sqlite3.connect(str(db))
        _drop_old_columns(raw, OLD_COLUMNS)
        raw.commit()
        raw.close()
        assert _open_at_once(db) == []
        conn = store.connect(db)
        for table, col in OLD_COLUMNS:
            assert [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)].count(col) == 1
        names = [r[0] for r in conn.execute("SELECT name FROM schema_migrations")]
        for table, col in OLD_COLUMNS:
            assert names.count("%s.%s" % (table, col)) == 1
        # The stored articles survive with the new columns empty.
        assert conn.execute("SELECT COUNT(*) FROM articles WHERE day_of_record IS NULL AND extract_version IS NULL").fetchone()[0] == 5
        conn.close()


def test_a_database_that_has_every_column_takes_no_column_step(tmp_path):
    # A new database has every column from the schema script, so no column step is recorded and no
    # connection takes the write lock for one.
    db = tmp_path / "fresh.db"
    store.connect(db).close()
    conn = store.connect(db)
    names = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    assert not names & {"%s.%s" % tc for tc in OLD_COLUMNS}
