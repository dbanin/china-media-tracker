"""relay.ingest against a damaged bundle (the 2026-10-17 robustness audit): a line that is not JSON, an
outlet_discovery record without its date, a value SQLite will not bind, a body that does not decode, a
truncated gzip. Each bad record is skipped and counted in skipped_malformed, the rest is taken in, and
the acknowledgement is held so the next bundle sends the window again."""
import base64
import gzip
import json
import os

import pytest

from pipeline import config, relay, store


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies_relay")
    src = store.connect(tmp_path / "relay.db")
    for i in range(40):
        url = "https://blocked.test/%d" % i
        aid = store.insert_discovered(src, {"url": url, "outlet_id": "in_indianexpress", "country": "IND",
                                            "language": "en", "title": "China and India talk trade %d" % i,
                                            "status": "fetched", "gate_relevant": 1, "gate_terms": ["China"]})
        store.update_article(src, aid, body_hash=store.save_body(store.url_hash(url), os.urandom(400).hex()),
                             body_chars=800, fetched_at=store.utcnow())
        store.record_discovery(src, "IND", True, "in_indianexpress")
    src.commit()
    data = relay.build_bundle(src)
    src.close()
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies_main")
    return [json.loads(l) for l in gzip.decompress(data).decode("utf-8").splitlines() if l.strip()]


def _pack(records):
    lines = [json.dumps(r) for r in records]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


def _dst(tmp_path):
    return store.connect(tmp_path / "main.db")


def _acked(conn):
    return relay._ack_through(conn)


def test_a_clean_bundle_still_advances_the_ack(bundle, tmp_path):
    conn = _dst(tmp_path)
    counts = relay.ingest(conn, _pack(bundle))
    assert counts["inserted"] == 40 and "skipped_malformed" not in counts and "ack_held" not in counts
    assert _acked(conn) == bundle[0]["built_at"]


def test_bad_json_and_a_record_missing_its_date_are_skipped(bundle, tmp_path):
    conn = _dst(tmp_path)
    recs = [dict(r) for r in bundle]
    day = next(r for r in recs if r.get("_type") == "outlet_discovery")
    del day["date"]
    lines = [json.dumps(r) for r in recs[:5]] + ['{"url_hash": "x", "title": "Invalid \x01"}', "[1, 2]", "not json"] + \
        [json.dumps(r) for r in recs[5:]]
    data = gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))
    counts = relay.ingest(conn, data)
    assert counts["inserted"] == 40 and counts["bodies"] == 40
    assert counts["skipped_malformed"] == 4 and counts["ack_held"] is True
    assert "outlet_discovery: KeyError" in counts["last_malformed"]
    assert conn.execute("SELECT COUNT(*) FROM daily_outlet_discovery").fetchone()[0] == 0
    assert _acked(conn) is None
    # the next, whole bundle takes the outlet day in and moves the acknowledgement on
    again = relay.ingest(conn, _pack(bundle))
    assert again["inserted"] == 0 and again["outlet_days"] == 1 and _acked(conn) == bundle[0]["built_at"]


def test_a_record_that_fails_part_way_is_rolled_back_alone(bundle, tmp_path):
    conn = _dst(tmp_path)
    recs = [dict(r) for r in bundle]
    arts = [r for r in recs if not r.get("_type")]
    arts[3]["title"] = {"not": "text"}               # SQLite will not bind it
    arts[7]["body"] = base64.b64encode(b"\x1f\x8b truncated").decode()   # a body that does not decode
    counts = relay.ingest(conn, _pack(recs))
    assert counts["inserted"] == 39 and counts["skipped_malformed"] == 1 and counts["body_malformed"] == 1
    assert counts["bodies"] == 38
    assert "article: InterfaceError" in counts["last_malformed"]
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE url_hash=?", (arts[3]["url_hash"],)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM relay_seen WHERE url_hash=?", (arts[3]["url_hash"],)).fetchone()[0] == 0
    assert store.load_body(arts[7]["url_hash"]) is None
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE url_hash=?", (arts[7]["url_hash"],)).fetchone()[0] == 1


def test_a_truncated_bundle_keeps_what_was_read_and_holds_the_ack(bundle, tmp_path):
    conn = _dst(tmp_path)
    data = _pack(bundle)
    counts = relay.ingest(conn, data[: len(data) // 2])
    assert 0 < counts["inserted"] < 40
    assert counts["bundle_truncated"].startswith("EOFError") and counts["ack_held"] is True
    assert _acked(conn) is None
    assert relay.ingest(conn, data)["inserted"] == 40 - counts["inserted"]
    assert _acked(conn) == bundle[0]["built_at"]


def test_iter_bundle_without_problems_still_raises():
    with pytest.raises(ValueError):
        list(relay.iter_bundle(gzip.compress(b"not json\n")))


def test_a_body_withheld_earlier_is_stored_when_it_arrives_for_an_unchanged_row(bundle, tmp_path):
    """Once the bundle key exists, a body arrives for rows taken in earlier without it. Their status does
    not change, and update_existing reports nothing changed: the body is still stored (fill_body)."""
    conn = _dst(tmp_path)
    withheld = [{k: v for k, v in r.items() if k != "body"} for r in bundle]
    assert relay.ingest(conn, _pack(withheld))["bodies"] == 0
    arts = [r for r in bundle if not r.get("_type")]
    other, blank = arts[0]["url_hash"], arts[1]["url_hash"]
    conn.execute("UPDATE articles SET body_hash='another text' WHERE url_hash=?", (other,))
    conn.execute("UPDATE articles SET body_hash=NULL, body_chars=NULL WHERE url_hash=?", (blank,))
    conn.commit()
    counts = relay.ingest(conn, _pack(bundle))
    assert counts["updated"] == 0 and counts["bodies_filled"] == 39 and counts["bodies"] == 39
    assert store.load_body(arts[5]["url_hash"]) is not None
    assert store.load_body(other) is None, "a row whose label rests on another text keeps it"
    row = conn.execute("SELECT body_hash, body_chars FROM articles WHERE url_hash=?", (blank,)).fetchone()
    assert tuple(row) == (arts[1]["body_hash"], arts[1]["body_chars"]) and store.load_body(blank)
    assert relay.ingest(conn, _pack(bundle))["bodies"] == 0          # on disk now: nothing to do
