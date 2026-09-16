"""A label whose article can never be re-read must not hold the completion flag false forever."""
from pipeline import config, export, store


def _article(conn, url, attempts, ruleset):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "it_x", "country": "ITA", "language": "it",
                                         "title": "t", "gate_relevant": 1, "status": "classified"})
    store.insert_classification(conn, aid, "rules", "C", 0.8, ruleset_version=ruleset)
    conn.execute("UPDATE articles SET fetch_attempts=? WHERE id=?", (attempts, aid))
    conn.commit()
    return aid


def test_unreclassifiable_labels_are_counted_and_complete_when_only_they_remain(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "x.db")
    _article(conn, "https://e.com/current", 0, config.RULESET_VERSION)
    _article(conn, "https://e.com/stuck", 3, "2026.09.4")       # cannot be re-read
    meta = export.build_meta(conn, [], [], export.build_latest(conn, [], [], population={}))
    assert meta["labels_unreclassifiable"] == 1
    assert meta["reclassification_complete"] is True
    _article(conn, "https://e.com/movable", 0, "2026.09.4")      # still retrievable
    meta = export.build_meta(conn, [], [], export.build_latest(conn, [], [], population={}))
    assert meta["labels_unreclassifiable"] == 1
    assert meta["reclassification_complete"] is False
