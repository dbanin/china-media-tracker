import sqlite3
from pathlib import Path

from pipeline import store
from pipeline.fetch_feeds import jaccard, normalize_title


def _mem():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    return conn


def test_canonical_url_strips_tracking():
    a = store.canonical_url("https://www.example.com/a/b/?utm_source=rss&x=1")
    b = store.canonical_url("https://example.com/a/b?x=1")
    assert a == b
    assert store.url_hash(a) == store.url_hash("https://WWW.example.com/a/b?x=1#frag")


def test_insert_dedupes():
    conn = _mem()
    item = {"url": "https://example.com/x", "outlet_id": "it_x", "country": "ITA", "language": "it",
            "title": "t", "status": "queued", "gate_relevant": 1}
    assert store.insert_discovered(conn, item) is not None
    item["url"] = "https://www.example.com/x/?utm_medium=feed"
    assert store.insert_discovered(conn, item) is None


def test_classification_never_deletes():
    conn = _mem()
    aid = store.insert_discovered(conn, {"url": "https://e.com/1", "outlet_id": "o", "country": "ITA",
                                         "language": "it", "title": "t"})
    c1 = store.insert_classification(conn, aid, "rules", "A", 1.0, signatures_fired=["xinhua_paren"])
    c2 = store.insert_classification(conn, aid, "llm", "B", 0.7)
    rows = conn.execute("SELECT id, is_current FROM classifications WHERE article_id=? ORDER BY id", (aid,)).fetchall()
    assert [(r["id"], r["is_current"]) for r in rows] == [(c1, 0), (c2, 1)]
    assert store.current_classification(conn, aid)["category"] == "B"


def test_human_review_separate_from_machine():
    conn = _mem()
    aid = store.insert_discovered(conn, {"url": "https://e.com/2", "outlet_id": "o", "country": "ITA",
                                         "language": "it", "title": "t"})
    cid = store.insert_classification(conn, aid, "llm", "B", 0.6)
    store.insert_human_review(conn, aid, cid, "C", "tester")
    assert store.current_classification(conn, aid)["category"] == "B"
    assert store.latest_human_review(conn, aid)["human_category"] == "C"


def test_title_similarity():
    a = normalize_title("China's Xi meets Putin in Beijing for talks on trade")
    b = normalize_title("China's Xi meets Putin in Beijing for talks on trade and Ukraine")
    assert jaccard(a, b) >= 0.7
    c = normalize_title("Storms sweep across Ontario")
    assert jaccard(a, c) < 0.2


def test_feed_health_consecutive_failures():
    conn = _mem()
    store.record_feed_health(conn, "o", "https://f", False, "http_404", 0)
    store.record_feed_health(conn, "o", "https://f", False, "http_404", 0)
    assert conn.execute("SELECT consecutive_failures FROM feed_health").fetchone()[0] == 2
    store.record_feed_health(conn, "o", "https://f", True, None, 10)
    assert conn.execute("SELECT consecutive_failures FROM feed_health").fetchone()[0] == 0


def test_prune_gated_out_keeps_relevant():
    conn = _mem()
    old = "2020-01-01T00:00:00+00:00"
    for i in range(3):
        conn.execute("INSERT INTO articles(url, url_hash, outlet_id, country, language, discovered_at, status, gate_relevant) VALUES (?,?,?,?,?,?,?,?)",
                     ("https://e.com/%d" % i, "h%d" % i, "o", "ITA", "it", old, "gated_out" if i < 2 else "classified", 0 if i < 2 else 1))
    conn.execute("INSERT INTO articles(url, url_hash, outlet_id, country, language, discovered_at, status, gate_relevant) VALUES (?,?,?,?,?,?,?,?)",
                 ("https://e.com/new", "hnew", "o", "ITA", "it", store.utcnow(), "gated_out", 0))
    assert store.prune_gated_out(conn, days=3) == 2
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 2
