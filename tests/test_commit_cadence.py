"""The export's long correction passes commit in steps, so the hourly run's other writers are not locked
out for the whole pass (the 2026-10-17 robustness audit measured the write lock held for up to 55 s).
Each step is a whole correction of its own: the results are those of a single commit, and a second run
changes nothing."""
from tests import test_copies as tc
from tests import test_junk_feeds as tj
from tests import test_section_retag as ts

from pipeline import copies, junk_feeds, section_retag, store


def _commits(conn):
    seen = []
    conn.set_trace_callback(lambda sql: seen.append(sql) if sql.strip().upper() == "COMMIT" else None)
    return seen


def test_junk_feeds_commits_in_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(junk_feeds, "COMMIT_EVERY", 2)
    conn = store.connect(tmp_path / "t.db")
    for i in range(7):
        tj._article(conn, "https://junk.test/slot-%d" % i, "2026-09-27")
    seen = _commits(conn)
    out = junk_feeds.run(conn, tj._outlets())
    conn.set_trace_callback(None)
    assert out["cf_junk"]["items_withdrawn"] == 7 and out["cf_junk"]["discovered_withdrawn"] == 7
    assert len(seen) >= 4, "a commit every two items and one for the outlet"
    assert tj._count(conn, "cf_junk", "2026-09-27") == (0, 0)
    assert junk_feeds.run(conn, tj._outlets()) == {}


def test_section_retag_commits_in_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(section_retag, "COMMIT_EVERY", 2)
    conn = store.connect(tmp_path / "t.db")
    ids = [ts._article(conn, "https://www.index.hr/sport/%d" % i, ts.SPORT, "Dinamo pobijedio Hajduk %d" % i,
                       ["section:sponsored"]) for i in range(7)]
    seen = _commits(conn)
    out = section_retag.run(conn)
    conn.set_trace_callback(None)
    assert out[ts.SPORT]["regated_out"] == 7 and len(seen) >= 4
    assert all(ts._row(conn, a)["status"] == "gated_out" for a in ids)
    assert section_retag.run(conn) == {}


def test_the_alteration_metrics_pass_commits_in_steps(tmp_path, monkeypatch):
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "COLLECTION_START", "2000-01-01")
    monkeypatch.setattr(copies, "COMMIT_EVERY", 2)
    conn = store.connect(tmp_path / "c.db")
    ids = tc._scored_corpus(conn, n=6)
    seen = _commits(conn)
    out = copies.compute(conn, {}, None, sources=tc.SOURCES)
    conn.set_trace_callback(None)
    assert out["finished"] == 6 and out["remaining"] == 0
    assert len(seen) >= 3 + 3, "pairing and metrics each commit every two copies"
    assert all(tc._row(conn, a)["body_state"] == "both" for a in ids)
