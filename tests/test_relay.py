"""Relay round trip: bundle on one database, ingest into another, nothing lost or doubled."""
import gzip
import base64

from pipeline import relay, store


def _seed(conn, n=3):
    ids = []
    for i in range(n):
        item = {"url": "https://blocked.test/%d" % i, "outlet_id": "in_indianexpress", "country": "IND", "language": "en",
                "title": "China and India talk trade %d" % i, "status": "fetched" if i else "gated_out", "gate_relevant": 1 if i else 0,
                "gate_terms": ["China"] if i else [], "published_at": "2026-09-09T08:00:00+00:00"}
        aid = store.insert_discovered(conn, item)
        h = store.url_hash(item["url"])
        if i:
            store.update_article(conn, aid, body_hash=store.save_body(h, "Body %d about China and trade." % i), body_chars=30,
                                 fetched_at=store.utcnow(), page_labels="section: World")
        store.record_discovery(conn, "IND", bool(i), "in_indianexpress")
        ids.append(aid)
    conn.commit()
    return ids


def test_bundle_and_ingest_round_trip(tmp_path, monkeypatch):
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    src = store.connect(tmp_path / "relay.db")
    _seed(src)
    store.record_feed_health(src, "in_indianexpress", "https://blocked.test/feed", True, None, 40)
    src.commit()
    data = relay.build_bundle(src)
    items = list(relay.iter_bundle(data))
    articles = [it for it in items if it.get("_type") != "feed_health"]
    assert len(articles) == 3 and len(items) == 4
    assert sum(1 for it in articles if it.get("body")) == 2
    assert gzip.decompress(base64.b64decode(articles[1]["body"])).decode() == "Body 1 about China and trade."

    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies_main")
    dst = store.connect(tmp_path / "main.db")
    counts = relay.ingest(dst, data)
    assert counts == {"seen": 3, "inserted": 3, "bodies": 2, "relevant": 2, "feeds": 1}
    assert dst.execute("SELECT consecutive_failures FROM feed_health WHERE feed_url='https://blocked.test/feed'").fetchone()[0] == 0
    rows = dst.execute("SELECT status, gate_relevant, page_labels FROM articles ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("gated_out", 0, None), ("fetched", 1, "section: World"), ("fetched", 1, "section: World")]
    assert store.load_body(rows[1]["page_labels"] and store.url_hash("https://blocked.test/1")) == "Body 1 about China and trade."
    assert dst.execute("SELECT discovered, gate_relevant FROM daily_discovery").fetchone()[:] == (3, 2)
    assert dst.execute("SELECT discovered FROM daily_outlet_discovery").fetchone()[0] == 3
    # a second ingest of the same bundle changes nothing
    again = relay.ingest(dst, data)
    assert again["inserted"] == 0 and dst.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 3
    assert dst.execute("SELECT discovered FROM daily_discovery").fetchone()[0] == 3
