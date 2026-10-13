"""The correction for items collected from feeds that no longer carried their outlet's news."""
import gzip
import io
import json
import sqlite3

import pytest

from pipeline import export, junk_feeds, registry, relay, store

JUNK = "https://junk.test/feed/"
CLEAN = "https://junk.test/rss.xml"
SINCE = "2026-09-24"


def _outlets(until=None, active=False, collector=None):
    o = {"id": "cf_junk", "name": "Junk", "country": "CAF", "language": "fr", "tier": "national_daily",
         "feeds": [CLEAN] if active else [JUNK], "active": active,
         "junk_feed": {"url": JUNK, "since": SINCE, "reason": "gambling posts"}}
    if until:
        o["junk_feed"]["until"] = until
    if not active:
        o["inactive_reason"] = "feed hijacked"
    if collector:
        o["collector"] = collector
    other = {"id": "cf_other", "name": "Other", "country": "CAF", "language": "fr", "tier": "national_daily",
             "feeds": ["https://other.test/feed/"], "active": True}
    return [o, other]


def _article(conn, url, day, feed=JUNK, outlet="cf_junk", status="gated_out", relevant=0, category=None):
    aid = store.insert_discovered(conn, {
        "url": url, "outlet_id": outlet, "country": "CAF", "language": "fr", "feed_url": feed,
        "title": "PERMAINAN JUDI KLASIK YANG BERASAL DARI CHINA", "summary": "", "status": status,
        "gate_relevant": relevant, "gate_terms": ["CHINA"] if relevant else []})
    conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (day + "T08:00:00+00:00", aid))
    store.record_discovery(conn, "CAF", bool(relevant), outlet, day)
    if category:
        store.insert_classification(conn, aid, "rules", category, 0.6, reasoning="test", signatures_fired=["residual_c"])
    conn.commit()
    return aid


def _count(conn, outlet, day):
    r = conn.execute("SELECT discovered, gate_relevant FROM daily_outlet_discovery WHERE outlet_id=? AND date=?",
                     (outlet, day)).fetchone()
    return tuple(r) if r else None


def _country(conn, day):
    return tuple(conn.execute("SELECT discovered, gate_relevant FROM daily_discovery WHERE country='CAF' AND date=?",
                              (day,)).fetchone())


def test_a_labelled_junk_item_is_withdrawn_and_its_days_leave_the_denominator(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    labelled = _article(conn, "https://junk.test/judi", SINCE, status="classified", relevant=1, category="C")
    gated = [_article(conn, "https://junk.test/slot-%d" % i, "2026-09-27") for i in range(3)]
    before = _article(conn, "https://junk.test/news", "2026-09-20", status="classified", relevant=1, category="C")
    other = _article(conn, "https://other.test/a", SINCE, feed="https://other.test/feed/", outlet="cf_other")
    # Gated-out copies already pruned: only the outlet-day row still counts them.
    store.record_discovery(conn, "CAF", False, "cf_junk", "2026-09-30")
    conn.commit()

    out = junk_feeds.run(conn, _outlets())
    t = out["cf_junk"]
    assert (t["items_withdrawn"], t["relevant_items"], t["labels_withdrawn"]) == (4, 1, {"C": 1})
    assert (t["outlet_days"], t["discovered_withdrawn"], t["gate_relevant_withdrawn"]) == (3, 5, 1)
    r = conn.execute("SELECT * FROM articles WHERE id=?", (labelled,)).fetchone()
    assert (r["status"], r["gate_relevant"], r["llm_pending"]) == ("junk_feed", 0, 0)
    assert r["fail_reason"] == "junk_feed:%s: was classified" % SINCE
    assert [tuple(x) for x in conn.execute("SELECT category, is_current FROM classifications WHERE article_id=?",
                                           (labelled,))] == [("C", 0)], "withdrawn, never deleted"
    assert all(conn.execute("SELECT status FROM articles WHERE id=?", (g,)).fetchone()[0] == "junk_feed" for g in gated)
    # Items from before the feed turned, and the country's other outlets, are left alone.
    assert conn.execute("SELECT status FROM articles WHERE id=?", (before,)).fetchone()[0] == "classified"
    assert conn.execute("SELECT status FROM articles WHERE id=?", (other,)).fetchone()[0] == "gated_out"
    assert _count(conn, "cf_junk", "2026-09-20") == (1, 1)
    for day in (SINCE, "2026-09-27", "2026-09-30"):
        assert _count(conn, "cf_junk", day) == (0, 0)
    assert _count(conn, "cf_other", SINCE) == (1, 0)
    assert _country(conn, SINCE) == (1, 0), "the other outlet's item stays in the country's denominator"
    assert junk_feeds.withdrawn(conn)["cf_junk"] == {"country": "CAF", "discovered": 5, "gate_relevant": 1, "days": 3}
    assert [a["id"] for a in export.article_rows(conn)] == [before]

    # Idempotent: a second run finds nothing and moves no count.
    assert junk_feeds.run(conn, _outlets()) == {}
    assert _count(conn, "cf_junk", SINCE) == (0, 0) and _country(conn, SINCE) == (1, 0)
    assert junk_feeds.withdrawn(conn)["cf_junk"]["discovered"] == 5
    store.rebuild_daily_discovery(conn)
    assert _country(conn, SINCE) == (1, 0)


def test_a_withdrawn_item_is_never_requeued_or_pruned(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, "https://junk.test/judi", SINCE, status="llm_submitted", relevant=1)
    junk_feeds.run(conn, _outlets())
    # The model batch it was in comes back with a label and marks it classified.
    store.insert_classification(conn, aid, "llm", "C", 0.8, reasoning="late")
    conn.execute("UPDATE articles SET status='classified' WHERE id=?", (aid,))
    conn.commit()
    out = junk_feeds.run(conn, _outlets())
    assert out["cf_junk"]["late_labels_withdrawn"] == 1 and out["cf_junk"]["discovered_withdrawn"] == 0
    assert conn.execute("SELECT status FROM articles WHERE id=?", (aid,)).fetchone()[0] == "junk_feed"
    assert conn.execute("SELECT COUNT(*) FROM classifications WHERE article_id=? AND is_current=1", (aid,)).fetchone()[0] == 0
    conn.execute("UPDATE articles SET discovered_at='2026-01-01T00:00:00+00:00' WHERE id=?", (aid,))
    conn.commit()
    store.prune_gated_out(conn)
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE id=?", (aid,)).fetchone()[0] == 1
    assert junk_feeds.run(conn, _outlets()) == {}


def test_after_the_feed_is_replaced_only_its_own_items_are_withdrawn(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    old = _article(conn, "https://junk.test/shoe", "2026-10-09")
    junk = _article(conn, "https://junk.test/shoe-2", "2026-10-12")
    news = _article(conn, "https://junk.test/article/1", "2026-10-12", feed=CLEAN, status="queued", relevant=1)
    outlets = _outlets(until="2026-10-12", active=True)
    out = junk_feeds.run(conn, outlets)
    assert out["cf_junk"]["items_withdrawn"] == 2 and out["cf_junk"]["discovered_withdrawn"] == 2
    assert _count(conn, "cf_junk", "2026-10-09") == (0, 0)
    assert _count(conn, "cf_junk", "2026-10-12") == (1, 1), "the new feed's item stays counted"
    assert conn.execute("SELECT status FROM articles WHERE id=?", (news,)).fetchone()[0] == "queued"
    assert {conn.execute("SELECT status FROM articles WHERE id=?", (i,)).fetchone()[0] for i in (old, junk)} == {"junk_feed"}
    assert junk_feeds.run(conn, outlets) == {}
    assert _count(conn, "cf_junk", "2026-10-12") == (1, 1)


def _bundle(*records):
    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return buf.getvalue()


def test_a_relay_bundle_cannot_bring_the_junk_back(tmp_path, monkeypatch):
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, "https://junk.test/judi", SINCE, status="classified", relevant=1, category="C")
    junk = _article(conn, "https://junk.test/shoe", "2026-10-12")
    store.record_discovery(conn, "CAF", False, "cf_junk", "2026-10-12")     # a clean feed item, pruned since
    conn.commit()
    outlets = _outlets(until="2026-10-12", active=True, collector="self_hosted")
    junk_feeds.run(conn, outlets)
    assert _count(conn, "cf_junk", "2026-10-12") == (1, 0)
    h = conn.execute("SELECT url_hash FROM articles WHERE id=?", (aid,)).fetchone()[0]
    data = _bundle(
        {"_type": "outlet_discovery", "date": SINCE, "outlet_id": "cf_junk", "country": "CAF", "discovered": 1, "gate_relevant": 1},
        {"_type": "outlet_discovery", "date": "2026-10-12", "outlet_id": "cf_junk", "country": "CAF", "discovered": 3, "gate_relevant": 0},
        {"url": "https://junk.test/judi", "url_hash": h, "outlet_id": "cf_junk", "country": "CAF", "language": "fr",
         "feed_url": JUNK, "title": "PERMAINAN JUDI", "discovered_at": SINCE + "T08:00:00+00:00", "status": "fetched",
         "gate_relevant": 1, "gate_terms": "[\"CHINA\"]"})
    for _ in range(2):    # sent again, the same rows count the same
        counts = relay.ingest(conn, data, outlets=outlets)
        assert counts["junk_outlet_days"] == 1 and counts["updated"] == 0
        assert _count(conn, "cf_junk", SINCE) == (0, 0), "a whole day stays withdrawn"
        assert _count(conn, "cf_junk", "2026-10-12") == (2, 0), "the relay's 3 less the 1 withdrawn"
        r = conn.execute("SELECT status, gate_relevant FROM articles WHERE id=?", (aid,)).fetchone()
        assert tuple(r) == ("junk_feed", 0)
    assert conn.execute("SELECT status FROM articles WHERE id=?", (junk,)).fetchone()[0] == "junk_feed"


def test_the_dry_run_writes_nothing(tmp_path):
    path = tmp_path / "t.db"
    conn = store.connect(path)
    _article(conn, "https://junk.test/judi", SINCE, status="classified", relevant=1, category="C")
    conn.close()
    ro = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    ro.row_factory = sqlite3.Row
    out = junk_feeds.run(ro, _outlets(), dry_run=True)
    assert out["cf_junk"]["labels_withdrawn"] == {"C": 1} and out["cf_junk"]["discovered_withdrawn"] == 1
    assert junk_feeds.withdrawn(ro) == {}
    assert ro.execute("SELECT status FROM articles").fetchone()[0] == "classified"


def test_the_registry_refuses_a_junk_feed_an_active_outlet_still_polls():
    o = _outlets(active=True)[0]
    with pytest.raises(ValueError, match="needs junk_feed until"):
        registry.validate_outlets([o])
    o["junk_feed"]["until"] = "2026-10-12"
    o["feeds"] = [JUNK]
    with pytest.raises(ValueError, match="still polls"):
        registry.validate_outlets([o])
    o["feeds"] = [CLEAN]
    o["junk_feed"]["until"] = SINCE
    with pytest.raises(ValueError, match="after since"):
        registry.validate_outlets([o])


def test_the_registry_records_the_seven_junk_feeds():
    ws = junk_feeds.windows(registry.load_outlets())
    assert {k: (w["since"], w.get("until")) for k, w in ws.items()} == {
        "cf_centrafriquepresse": ("2026-09-24", None), "by_belapan": ("2026-09-24", None),
        "dj_lavoixdedjibouti": ("2026-09-03", None), "gt_guatevision": ("2026-09-03", None),
        "km_ortc": ("2026-09-03", None), "fj_pina": ("2026-09-03", None),
        "nc_lnc": ("2026-09-24", "2026-10-12")}
    assert ws["nc_lnc"]["url"] == "https://feeds.feedburner.com/LesNouvellesCaledoniennes"
