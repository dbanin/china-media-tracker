"""Body files on disk: written atomically (store.save_body) and read defensively (store.load_body), so
one truncated, garbled or wrongly encoded file never stops a stage. The reclassify loop, the body
link pass and the planted bylines scan each go on past a damaged body."""
import errno
import gzip
import os

import pytest

from pipeline import body_dups, classify_rules, config, fetch_articles, planted, store


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "RAW_HTML_DIR", tmp_path / "raw")
    monkeypatch.setattr(store, "UNREADABLE_BODIES", {})
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


def _damage(h: str, kind: str) -> None:
    p = store.body_path(h)
    raw = p.read_bytes()
    if kind == "truncated":
        p.write_bytes(raw[: len(raw) // 2])
    elif kind == "garbage":
        p.write_bytes(b"\x00not gzip at all" * 20)
    elif kind == "latin1":
        p.write_bytes(gzip.compress("caf\xe9 \xff".encode("latin-1")))
    elif kind == "bad_crc":
        p.write_bytes(raw[:-8] + b"\x00" * 8)


def _article(conn, url, body=None, title="Ministry statement", status="fetched", outlet="o1", country="ITA"):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": country, "language": "en",
                                         "title": title, "status": status, "gate_relevant": 1})
    if body is not None:
        h = store.url_hash(url)
        store.update_article(conn, aid, body_hash=store.save_body(h, body), body_chars=len(body), fetched_at=store.utcnow())
    conn.commit()
    return aid


def test_save_body_round_trip_leaves_no_temporary_file(db):
    h = store.url_hash("https://b.test/1")
    digest = store.save_body(h, "Body text about trade.")
    assert store.load_body(h) == "Body text about trade."
    assert len(digest) == 32
    assert [p.name for p in store.body_path(h).parent.iterdir()] == [store.body_path(h).name]
    assert oct(store.body_path(h).stat().st_mode & 0o777) == oct(0o644)


def test_a_failed_write_keeps_the_old_body_whole(db, monkeypatch):
    h = store.url_hash("https://b.test/2")
    store.save_body(h, "The first body.")

    def full_disk(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", full_disk)
        with pytest.raises(OSError):
            store.save_body(h, "The second body, never stored.")
    assert store.load_body(h) == "The first body."
    assert [p.name for p in store.body_path(h).parent.iterdir()] == [store.body_path(h).name]


@pytest.mark.parametrize("kind", ["truncated", "garbage", "latin1", "bad_crc"])
def test_a_damaged_body_reads_as_missing_and_is_counted(db, kind, capsys):
    h = store.url_hash("https://b.test/%s" % kind)
    store.save_body(h, "A body long enough to be compressed into more than a few bytes. " * 20)
    _damage(h, kind)
    assert store.load_body(h) is None
    assert store.load_body(h) is None
    assert store.UNREADABLE_BODIES == {h: store.UNREADABLE_BODIES[h]} and store.unreadable_body_count() == 1
    assert capsys.readouterr().err.count("unreadable body %s" % h) == 1      # logged once, however often read
    assert store.load_body(store.url_hash("https://b.test/absent")) is None and store.unreadable_body_count() == 1


def test_reclassify_goes_past_a_damaged_body_and_keeps_the_model_label(db, monkeypatch):
    """The 2026-10-17 audit: one truncated body crashed classify_rules.run's reclassify loop on every
    run (EOFError), which stopped every export after it."""
    monkeypatch.setattr(fetch_articles, "process_article",
                        lambda *a, **k: pytest.fail("a kept model label is not fetched again"))
    ids = []
    for i in range(3):
        aid = _article(db, "https://r.test/%d" % i, body="Officials met on Tuesday to discuss the harbour. " * 10,
                       status="classified")
        store.insert_classification(db, aid, "llm", "C", 0.8, ruleset_version="2000.01.0")
        ids.append(aid)
    db.commit()
    _damage(store.url_hash("https://r.test/0"), "truncated")
    counts = classify_rules.reclassify(db, "t")
    assert counts["input"] == 3
    for aid in ids:
        cur = store.current_classification(db, aid)
        assert cur["method"] == "llm" and cur["category"] == "C"
    assert store.unreadable_body_count() == 1


WIRE = ("ministry officials said the agreement would expand trade between both countries while analysts warned "
        "that tariffs could rise again before the summit next month in the capital where leaders plan to sign "
        "several memoranda covering energy ports railways and digital infrastructure projects ") * 4


def test_body_link_pass_reaches_linking_past_a_damaged_body(db):
    """backfill_fingerprints died on one corrupt body, so link_pending never ran again."""
    a = _article(db, "https://l.test/a", body=WIRE, outlet="o1", country="ITA")
    b = _article(db, "https://l.test/b", body=WIRE + " And one closing line.", outlet="o2", country="FRA")
    bad = _article(db, "https://l.test/bad", body=WIRE, outlet="o3", country="DEU")
    _damage(store.url_hash("https://l.test/bad"), "truncated")
    out = body_dups.run(db)
    assert out["backfill"]["unreadable_body"] == 1 and out["backfill"]["missing_body"] == 0
    assert out["backfill"]["fingerprinted"] == 2
    links = {(r["a"], r["b"]) for r in db.execute("SELECT a, b FROM body_links")}
    assert (min(a, b), max(a, b)) in links and not any(bad in pair for pair in links)
    # marked, so the next run does not read it again
    assert body_dups.run(db)["backfill"]["todo"] == 0


def test_planted_scan_goes_past_a_damaged_body(db, tmp_path, monkeypatch):
    p = tmp_path / "personas.yaml"
    p.write_text("""version: "2026.10.1"
operations:
  - {id: op_x, name: Op X, origin: IR, reported_by: Lab, report_url: "https://example.test/r", reported_on: "2026-10-08", summary: s}
personas:
  - {id: ervin_b_hoskins, name: Ervin B. Hoskins, operation: op_x, kind: journalist}
fronts: []
""", encoding="utf-8")
    monkeypatch.setattr(config, "PERSONAS_PATH", p)
    _article(db, "https://p.test/1", body="By Someone\n\nA first story. " * 5)
    later = _article(db, "https://p.test/2", body="By Ervin B. Hoskins\n\nA second story.")
    _damage(store.url_hash("https://p.test/1"), "garbage")
    counts = planted.scan(db)
    assert counts["unreadable_bodies"] == 1 and counts["bodies"] == 1 and counts["flagged"] == 1
    assert [r["article_id"] for r in db.execute("SELECT article_id FROM planted_flags")] == [later]
