"""The permanent gate audit sample: a random share of rejections survives pruning."""
import csv
import sqlite3

from pipeline import config, gate_audit, store

OLD = "2020-01-01T00:00:00+00:00"


def _hashes(keep, n):
    """n url hashes that are (keep=True) or are not in the audit set."""
    out, i = [], 0
    while len(out) < n:
        h = store.url_hash("https://e.test/%s/%d" % (keep, i))
        if store.gate_audit_keep(h) == keep:
            out.append(h)
        i += 1
    return out


def _add(conn, h, lang="it", discovered=OLD, status="gated_out", relevant=0, outlet="o"):
    conn.execute(
        """INSERT INTO articles(url, url_hash, outlet_id, country, language, title, summary, discovered_at, status,
             gate_relevant, gate_terms) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("https://e.test/" + h, h, outlet, "ITA", lang, "title " + h, "summary " + h, discovered, status, relevant, '["cina"]'))


def test_retention_is_deterministic_and_close_to_the_share():
    hashes = [store.url_hash("https://outlet%d.test/story/%d" % (i % 50, i)) for i in range(100000)]
    first = [store.gate_audit_keep(h) for h in hashes]
    assert first == [store.gate_audit_keep(h) for h in hashes]
    share = sum(first) / len(first)
    assert abs(share - config.GATE_AUDIT_KEEP_SHARE) < 0.002
    # Blind to outlet: every outlet's share is near the overall one (2,000 items each, so a loose bound).
    for k in range(50):
        sub = first[k::50]
        assert sum(sub) / len(sub) < 0.045
    assert store.gate_audit_keep(hashes[0], share=0.0) is False
    assert store.gate_audit_keep(hashes[0], share=1.0) is True


def test_prune_keeps_retained_rows(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    kept, gone = _hashes(True, 2), _hashes(False, 3)
    for h in kept + gone:
        _add(conn, h)
    _add(conn, "recent", discovered=store.utcnow())
    conn.commit()
    assert store.prune_gated_out(conn, days=3) == 3
    rows = {r["url_hash"]: r for r in conn.execute("SELECT * FROM articles")}
    assert set(rows) == set(kept) | {"recent"}
    r = rows[kept[0]]
    assert (r["title"], r["summary"], r["url"], r["outlet_id"], r["country"], r["language"], r["gate_terms"]) == \
        ("title " + kept[0], "summary " + kept[0], "https://e.test/" + kept[0], "o", "ITA", "it", '["cina"]')
    assert r["audit_keep"] == 1
    # A second prune, later, still leaves them.
    assert store.prune_gated_out(conn, days=0) == (0 if store.gate_audit_keep("recent") else 1)
    assert set(kept) <= {x[0] for x in conn.execute("SELECT url_hash FROM articles")}


def test_relevant_rows_are_not_marked(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    h = _hashes(True, 1)[0]
    _add(conn, h, status="queued", relevant=1)
    store.mark_gate_audit_keep(conn)
    assert conn.execute("SELECT audit_keep FROM articles").fetchone()[0] is None


def test_migration_on_an_old_database(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.execute("""CREATE TABLE articles (id INTEGER PRIMARY KEY, url TEXT NOT NULL, url_hash TEXT NOT NULL UNIQUE,
        outlet_id TEXT NOT NULL, country TEXT NOT NULL, language TEXT NOT NULL, feed_url TEXT, title TEXT, summary TEXT,
        author TEXT, published_at TEXT, discovered_at TEXT NOT NULL, fetched_at TEXT, status TEXT NOT NULL,
        fail_reason TEXT, http_status INTEGER, body_hash TEXT, body_chars INTEGER, raw_html_path TEXT,
        gate_relevant INTEGER, gate_terms TEXT, dup_group_id INTEGER, fetch_attempts INTEGER NOT NULL DEFAULT 0,
        llm_pending INTEGER NOT NULL DEFAULT 0, llm_trigger TEXT)""")
    kept, gone = _hashes(True, 2), _hashes(False, 2)
    for h in kept + gone:
        old.execute("INSERT INTO articles(url, url_hash, outlet_id, country, language, discovered_at, status, gate_relevant)"
                    " VALUES (?,?,?,?,?,?,?,?)", ("https://e.test/" + h, h, "o", "ITA", "it", OLD, "gated_out", 0))
    old.commit()
    old.close()
    conn = store.connect(path)
    marks = {r[0]: r[1] for r in conn.execute("SELECT url_hash, audit_keep FROM articles")}
    assert marks == {**{h: 1 for h in kept}, **{h: 0 for h in gone}}
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='idx_articles_audit_keep'").fetchone()
    assert store.prune_gated_out(conn) == 2
    conn.close()
    store.connect(path).close()   # opening again is a no-op


def test_allocate_is_proportional_with_a_floor():
    assert gate_audit.allocate({"en": 900, "fr": 100}, 100) == {"en": 90, "fr": 10}
    assert gate_audit.allocate({"en": 990, "fr": 10}, 100, min_per_stratum=5) == {"en": 99, "fr": 5}
    assert gate_audit.allocate({"en": 3, "fr": 2}, 100, min_per_stratum=5) == {"en": 3, "fr": 2}


def test_sample_draws_from_retained_rows_by_language_and_date(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    kept = _hashes(True, 40)
    for i, h in enumerate(kept):
        _add(conn, h, lang="en" if i % 4 else "fr", discovered="2026-09-%02dT12:00:00+00:00" % (1 + i % 20))
    for h in _hashes(False, 40):
        _add(conn, h, lang="en", discovered="2026-09-05T12:00:00+00:00")
    _add(conn, "rel", lang="en", discovered="2026-09-05T12:00:00+00:00", status="classified", relevant=1)
    conn.commit()
    out = tmp_path / "audit.csv"
    n = gate_audit.cmd_sample(conn, 10, out, seed=1, since="2026-09-01", until="2026-09-11", min_per_language=3)
    rows = list(csv.DictReader(open(out, encoding="utf-8")))
    assert n == len(rows) == 10
    assert {r["url"].rsplit("/", 1)[1] for r in rows} <= set(kept)
    assert all("2026-09-01" <= r["discovered_at"] < "2026-09-11" for r in rows)
    assert [r["language"] for r in rows] == sorted(r["language"] for r in rows)
    by_lang = {}
    for r in rows:
        by_lang.setdefault(r["language"], []).append(r)
    in_window = {"en": sum(1 for i in range(40) if i % 20 < 10 and i % 4), "fr": sum(1 for i in range(40) if i % 20 < 10 and not i % 4)}
    assert in_window == {"en": 14, "fr": 6} and {k: len(v) for k, v in by_lang.items()} == {"en": 7, "fr": 3}
    for lang, rs in by_lang.items():
        assert int(rs[0]["stratum_size"]) == in_window[lang]
        assert abs(float(rs[0]["weight"]) - in_window[lang] / len(rs)) < 1e-3
    assert rows[0]["gate_terms"] == '["cina"]' and rows[0]["title"].startswith("title ")


def test_compute_weights_by_language(tmp_path, capsys):
    path = tmp_path / "coded.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(gate_audit.CSV_FIELDS)
        w.writerow([1, "o", "ITA", "en", "", "", "t", "", "u1", 90, "9.0", "no"])
        w.writerow([2, "o", "ITA", "fr", "", "", "t", "", "u2", 10, "1.0", "yes"])
    gate_audit.cmd_compute(path)
    out = capsys.readouterr().out
    assert "50.0 percent" in out and "weighted to each language's share of the retained rejections: 10.0 percent" in out
