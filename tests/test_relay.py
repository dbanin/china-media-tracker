"""Relay round trip: bundle on one database, ingest into another, nothing lost or doubled."""
import gzip
import base64
import datetime as dt

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
    articles = [it for it in items if not it.get("_type")]
    # plus the bundle_meta record first, one feed health and one outlet-day record
    assert len(articles) == 3 and len(items) == 6 and items[0]["_type"] == "bundle_meta"
    assert sum(1 for it in articles if it.get("body")) == 2
    assert gzip.decompress(base64.b64decode(articles[1]["body"])).decode() == "Body 1 about China and trade."

    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies_main")
    dst = store.connect(tmp_path / "main.db")
    counts = relay.ingest(dst, data)
    acked = counts.pop("acked_through")
    assert counts == {"seen": 3, "inserted": 3, "bodies": 2, "relevant": 2, "already_delivered": 0, "updated": 0,
                      "feeds": 1, "outlet_days": 1, "bundle": "window"}
    assert acked == items[0]["built_at"]
    assert dst.execute("SELECT consecutive_failures FROM feed_health WHERE feed_url='https://blocked.test/feed'").fetchone()[0] == 0
    rows = dst.execute("SELECT status, gate_relevant, page_labels FROM articles ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("gated_out", 0, None), ("fetched", 1, "section: World"), ("fetched", 1, "section: World")]
    assert store.load_body(rows[1]["page_labels"] and store.url_hash("https://blocked.test/1")) == "Body 1 about China and trade."
    # per outlet-day counts come from the bundle, exactly, and the country table is derived from them
    assert dst.execute("SELECT discovered, gate_relevant FROM daily_outlet_discovery").fetchone()[:] == (3, 2)
    store.rebuild_daily_discovery(dst)
    assert dst.execute("SELECT discovered, gate_relevant FROM daily_discovery").fetchone()[:] == (3, 2)
    # a second ingest of the same bundle changes nothing
    again = relay.ingest(dst, data)
    assert again["inserted"] == 0 and dst.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 3
    store.rebuild_daily_discovery(dst)
    assert dst.execute("SELECT discovered FROM daily_discovery").fetchone()[0] == 3


def test_pruned_items_are_not_delivered_twice(tmp_path, monkeypatch):
    """The bundle carries seven days; the hosted side prunes gated-out items after three. The
    same item must not come back, and must not be counted again."""
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    src = store.connect(tmp_path / "relay.db")
    _seed(src)
    data = relay.build_bundle(src)
    dst = store.connect(tmp_path / "main.db")
    first = relay.ingest(dst, data)
    assert first["inserted"] == 3
    dst.execute("UPDATE articles SET discovered_at='2020-01-01T00:00:00+00:00' WHERE status='gated_out'")
    dst.commit()
    assert store.prune_gated_out(dst) == 1
    second = relay.ingest(dst, data)
    assert second["inserted"] == 0 and second["already_delivered"] == 1
    assert dst.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 2
    assert dst.execute("SELECT discovered FROM daily_outlet_discovery").fetchone()[0] == 3


def test_relay_decisions_reach_the_hosted_row_it_already_has(tmp_path, monkeypatch):
    """The relay runs the gate and the fetcher for outlets the hosted runner cannot reach, so its
    copy of an article it already delivered is often further along. Skipping every url_hash the
    hosted side had left those decisions stranded: gated_out articles stayed gated_out even when
    the relay had them as gate-relevant with section terms, and recoveries never landed."""
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    src = store.connect(tmp_path / "relay.db")
    _seed(src, n=3)
    dst = store.connect(tmp_path / "main.db")
    relay.ingest(dst, relay.build_bundle(src))
    # the hosted side has them; now the relay moves each one on
    src.execute("UPDATE articles SET status='queued', gate_relevant=1, gate_terms='[\"section:press_release\"]', "
                "feed_url='https://blocked.test/pr/feed' WHERE status='gated_out'")
    src.execute("UPDATE articles SET status='failed', fail_reason='http_503' WHERE id=2")
    src.commit()
    dst.execute("UPDATE articles SET status='failed', fail_reason='ConnectionError' WHERE id=3")
    dst.commit()
    src.execute("UPDATE articles SET status='fetched', fail_reason=NULL WHERE id=3")
    src.commit()
    counts = relay.ingest(dst, relay.build_bundle(src))
    assert counts["inserted"] == 0 and counts["updated"] == 2 and counts["promoted_relevant"] == 1
    rows = {r["id"]: r for r in dst.execute("SELECT * FROM articles")}
    assert rows[1]["status"] == "queued" and rows[1]["gate_relevant"] == 1
    assert "section:press_release" in rows[1]["gate_terms"] and rows[1]["feed_url"] == "https://blocked.test/pr/feed"
    assert rows[3]["status"] == "fetched", "a transient failure the relay recovered from"
    # never backwards: the relay says failed, the hosted side already has it fetched
    assert rows[2]["status"] == "fetched"
    # and discovery is not counted twice
    assert dst.execute("SELECT discovered FROM daily_outlet_discovery").fetchone()[0] == 3


def test_a_hosted_classification_is_never_overwritten(tmp_path, monkeypatch):
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    src = store.connect(tmp_path / "relay.db")
    _seed(src, n=2)
    dst = store.connect(tmp_path / "main.db")
    relay.ingest(dst, relay.build_bundle(src))
    store.insert_classification(dst, 2, "llm", "B", 0.8, model_version="claude-sonnet-5")
    store.update_article(dst, 2, status="classified")
    dst.commit()
    src.execute("UPDATE articles SET status='queued', gate_relevant=0 WHERE id=2")
    src.commit()
    counts = relay.ingest(dst, relay.build_bundle(src))
    assert counts["updated"] == 0
    row = dst.execute("SELECT status FROM articles WHERE id=2").fetchone()
    assert row[0] == "classified"
    assert store.current_classification(dst, 2)["category"] == "B"


def test_unknown_record_types_are_skipped_not_fatal(tmp_path, monkeypatch):
    import gzip, io, json
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"_type": "something_newer", "x": 1}) + "\n")
        fh.write(json.dumps({"no_url_hash": True}) + "\n")
    dst = store.connect(tmp_path / "m.db")
    counts = relay.ingest(dst, buf.getvalue())
    assert counts["skipped_unknown"] == 2 and counts["inserted"] == 0


_RELAY_OUTLETS = [{"id": "au_moved", "active": True, "collector": "self_hosted"},
                  {"id": "au_refusedtoo", "active": True, "collector": "self_hosted"},
                  {"id": "au_hosted", "active": True}]


def _main_with_refusals(path):
    """A main database as the runner leaves it for an outlet whose pages refuse it."""
    main = store.connect(path)
    old = "2026-01-04T00:00:00+00:00"
    def add(url, outlet, status, reason, relevant=1, discovered=None):
        aid = store.insert_discovered(main, {"url": url, "outlet_id": outlet, "country": "AUS", "language": "en",
                                             "title": "China " + url, "status": status, "gate_relevant": relevant,
                                             "gate_terms": ["China"]})
        store.update_article(main, aid, fail_reason=reason, fetch_attempts=3,
                             discovered_at=discovered or (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=20)).isoformat())
        return aid
    ids = {
        "refused": add("https://moved.test/a", "au_moved", "failed", "http_403"),
        "limited": add("https://moved.test/b", "au_moved", "failed", "http_429"),
        "label": add("https://moved.test/c", "au_moved", "classified", "refetch_failed:http_403"),
        "other_failure": add("https://moved.test/d", "au_moved", "failed", "extraction_empty"),
        "not_relevant": add("https://moved.test/e", "au_moved", "gated_out", None, relevant=0),
        "too_old": add("https://moved.test/f", "au_moved", "failed", "http_403", discovered=old),
        "hosted_outlet": add("https://hosted.test/a", "au_hosted", "failed", "http_403"),
        "refused_too": add("https://refusedtoo.test/a", "au_refusedtoo", "failed", "http_403"),
    }
    store.insert_classification(main, ids["label"], "rules", "C", 0.9)
    main.commit()
    return main, ids


def test_relay_takes_over_articles_the_runner_was_refused(tmp_path, monkeypatch):
    """An outlet moved to the relay because its pages refuse the runner left its refused articles
    behind: the relay only fetched what its own feeds discovered. It now queues them from the main
    database's local copy, for its own outlets only, once each, and not where it is refused too."""
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    main, ids = _main_with_refusals(tmp_path / "main.db")
    main.close()
    rel = store.connect(tmp_path / "relay.db")
    # the relay machine is refused by this outlet too: its last fetch here was a 403
    aid = store.insert_discovered(rel, {"url": "https://refusedtoo.test/new", "outlet_id": "au_refusedtoo",
                                        "country": "AUS", "language": "en", "status": "failed", "gate_relevant": 1})
    store.update_article(rel, aid, fail_reason="http_403", fetched_at=store.utcnow())
    rel.commit()
    counts = relay.seed_refused(rel, tmp_path / "main.db", outlets=_RELAY_OUTLETS)
    assert counts["seeded"] == 3 and counts["skipped_refused_here"] == 1
    queued = {r["url"]: r for r in rel.execute("SELECT * FROM articles WHERE status='queued'")}
    assert set(queued) == {"https://moved.test/a", "https://moved.test/b", "https://moved.test/c"}
    assert queued["https://moved.test/a"]["fetch_attempts"] == 0
    # the runner's discovery is kept, not counted again
    assert rel.execute("SELECT COUNT(*) FROM daily_outlet_discovery").fetchone()[0] == 0
    # once each: a second run adds nothing, whatever the relay's outcome was
    rel.execute("UPDATE articles SET status='failed', fail_reason='extraction_empty' WHERE url='https://moved.test/b'")
    rel.commit()
    assert relay.seed_refused(rel, tmp_path / "main.db", outlets=_RELAY_OUTLETS)["seeded"] == 0
    # a cap per outlet and run, newest first
    rel2 = store.connect(tmp_path / "relay2.db")
    assert relay.seed_refused(rel2, tmp_path / "main.db", outlets=_RELAY_OUTLETS, per_outlet=2)["seeded"] == 3
    # no copy of the main database on this machine: nothing to do, nothing fails
    assert relay.seed_refused(rel2, tmp_path / "absent.db", outlets=_RELAY_OUTLETS)["main_db"] == "absent"


def test_refused_articles_fetched_on_the_relay_reach_the_hosted_rows(tmp_path, monkeypatch):
    """Fetched weeks after discovery, they still travel in the bundle, and the hosted rows take them:
    a failed row moves on to fetched, and a classified row whose re-fetch was refused gets its body
    back with its label, status and attempt count untouched, so it can be classified again."""
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    main, ids = _main_with_refusals(tmp_path / "main.db")
    rel = store.connect(tmp_path / "relay.db")
    relay.seed_refused(rel, tmp_path / "main.db", outlets=_RELAY_OUTLETS)
    for r in rel.execute("SELECT * FROM articles WHERE status='queued'").fetchall():
        h = store.url_hash(r["url"])
        store.update_article(rel, r["id"], status="fetched", fail_reason=None, http_status=200, fetched_at=store.utcnow(),
                             fetch_attempts=1, body_hash=store.save_body(h, "Body of %s about China." % r["url"]), body_chars=40)
    rel.commit()
    data = relay.build_bundle(rel)
    # the three refused articles of au_moved and the one of au_refusedtoo (not refused on this relay yet)
    assert sum(1 for it in relay.iter_bundle(data) if not it.get("_type")) == 4
    counts = relay.ingest(main, data)
    assert counts["inserted"] == 0 and counts["updated"] == 4
    rows = {r["id"]: r for r in main.execute("SELECT * FROM articles")}
    assert rows[ids["refused"]]["status"] == "fetched" and rows[ids["refused"]]["fail_reason"] is None
    assert rows[ids["limited"]]["status"] == "fetched"
    label = rows[ids["label"]]
    assert label["status"] == "classified" and label["fail_reason"] is None and label["fetch_attempts"] == 3
    assert store.current_classification(main, ids["label"])["category"] == "C"
    assert store.load_body(label["url_hash"]) == "Body of https://moved.test/c about China."
    # the hosted outlet's refusal is not the relay's business
    assert rows[ids["hosted_outlet"]]["status"] == "failed"


# ---------------------------------------------------------------------------
# feed polls travel in the bundle (2026-10-12)
# ---------------------------------------------------------------------------

def _relay_polls(conn, n, saturated, miss=4.0, feed="https://relay.test/feed", outlet="o_relay", country="ITA"):
    for i in range(n):
        sat = i < saturated
        store.record_feed_poll(conn, outlet, country, feed, 20, 20 if sat else 3, sat, miss if sat else 0.0,
                               collector="self_hosted")
    conn.commit()


def _polls(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT feed_url, collector, polls, saturated, new_items, missed_estimate FROM feed_polls ORDER BY feed_url, collector")]


def test_feed_polls_round_trip_and_ingest_is_idempotent(tmp_path, monkeypatch):
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    src = store.connect(tmp_path / "relay.db")
    _relay_polls(src, 3, 1)
    polls = [it for it in relay.iter_bundle(relay.build_bundle(src)) if it.get("_type") == "feed_poll"]
    assert len(polls) == 1 and polls[0]["collector"] == "self_hosted"
    assert (polls[0]["polls"], polls[0]["saturated"], polls[0]["new_items"]) == (3, 1, 26)

    dst = store.connect(tmp_path / "main.db")
    # the runner's own polls of the same feed on the same day stay in their own row
    store.record_feed_poll(dst, "o_relay", "ITA", "https://relay.test/feed", 20, 2, False, 0.0)
    dst.commit()
    assert relay.ingest(dst, relay.build_bundle(src))["feed_poll_days"] == 1
    expected = [("https://relay.test/feed", "hosted", 1, 0, 2, 0.0), ("https://relay.test/feed", "self_hosted", 3, 1, 26, 4.0)]
    assert _polls(dst) == expected
    # the same bundle again counts nothing twice
    relay.ingest(dst, relay.build_bundle(src))
    assert _polls(dst) == expected
    # a later bundle carries the day's running total, which replaces the row rather than adding to it
    _relay_polls(src, 2, 2)
    relay.ingest(dst, relay.build_bundle(src))
    assert _polls(dst)[1] == ("https://relay.test/feed", "self_hosted", 5, 3, 66, 12.0)


def test_relay_bundle_polls_are_stored_as_the_relays_whatever_its_database_says(tmp_path, monkeypatch):
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    src = store.connect(tmp_path / "relay.db")
    store.record_feed_poll(src, "o_relay", "ITA", "https://relay.test/feed", 20, 20, True, 1.0, collector="hosted")
    src.commit()
    dst = store.connect(tmp_path / "main.db")
    store.record_feed_poll(dst, "o_relay", "ITA", "https://relay.test/feed", 20, 2, False, 0.0)
    dst.commit()
    relay.ingest(dst, relay.build_bundle(src))
    # the runner's own row is never overwritten by a bundle
    assert _polls(dst) == [("https://relay.test/feed", "hosted", 1, 0, 2, 0.0),
                           ("https://relay.test/feed", "self_hosted", 1, 1, 20, 1.0)]


def test_saturation_of_a_relay_only_feed_reaches_the_export_once(tmp_path, monkeypatch):
    from pipeline import config, export
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    src = store.connect(tmp_path / "relay.db")
    _relay_polls(src, 12, 6)
    data = relay.build_bundle(src)
    dst = store.connect(tmp_path / "main.db")
    relay.ingest(dst, data)
    relay.ingest(dst, data)      # re-sent bundle
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    day = export.build_daily(dst)[today[:7]]["days"][today]["countries"]["ITA"]
    assert (day["polls"], day["sat"], day["miss"]) == (12, 6, 24)
    outlets = [{"id": "o_relay", "name": "O", "country": "ITA", "language": "it", "feeds": ["https://relay.test/feed"],
                "tier": "regional", "active": True, "collector": "self_hosted"}]
    latest = export.build_latest(dst, outlets, [], population={})
    warn = [w for w in latest["countries"]["ITA"]["warnings"] if w["type"] == "feed_saturation"]
    assert warn and "50 percent" in warn[0]["text"] and "an estimated 24 were missed" in warn[0]["text"]
    fs = export.build_meta(dst, [], [], export.build_latest(dst, [], [], population={}))["feed_saturation"]
    assert (fs["polls"], fs["saturated"], fs["missed_estimate"]) == (12, 6, 24)


def test_feed_polls_gain_a_collector_without_losing_rows(tmp_path, monkeypatch):
    import sqlite3
    from pipeline import config
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.execute("""CREATE TABLE feed_polls (date TEXT NOT NULL, feed_url TEXT NOT NULL, outlet_id TEXT NOT NULL,
                   country TEXT NOT NULL, polls INTEGER NOT NULL DEFAULT 0, saturated INTEGER NOT NULL DEFAULT 0,
                   entries INTEGER NOT NULL DEFAULT 0, new_items INTEGER NOT NULL DEFAULT 0,
                   missed_estimate REAL NOT NULL DEFAULT 0, PRIMARY KEY(date, feed_url))""")
    old.execute("INSERT INTO feed_polls VALUES ('2026-10-01', 'https://relay.test/feed', 'o', 'ITA', 4, 1, 80, 30, 2.5)")
    old.commit()
    old.close()
    monkeypatch.setattr(config, "COLLECTOR", "self_hosted")   # the relay opening its own database
    conn = store.connect(path)
    assert [tuple(r) for r in conn.execute("SELECT date, polls, saturated, new_items, missed_estimate, collector FROM feed_polls")] == \
        [("2026-10-01", 4, 1, 30, 2.5, "self_hosted")]
    pk = [r[1] for r in sorted(conn.execute("PRAGMA table_info(feed_polls)"), key=lambda r: r[5]) if r[5]]
    assert pk == ["date", "feed_url", "collector"]
    conn.close()
    assert store.connect(path).execute("SELECT COUNT(*) FROM feed_polls").fetchone()[0] == 1
