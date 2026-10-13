"""Outlets polled by both collectors (registry collector: both).

GitHub drops many scheduled runs, so the hosted runner reached each feed a median 2.7 hours apart and
short feeds rolled past items between polls. A shared outlet is polled by the runner and by the relay
on the owner's machine; its pages are fetched by the runner only. The relay's items reach the main
database by url_hash like any relay item, discovery is counted once, and a poll is saturated only
when its window misses the previous poll by either collector.
"""
import datetime as dt
import json

import pytest

from pipeline import config, fetch_feeds, registry, relay, store

FEED = "https://short.test/rss"
SHARED = {"id": "it_short", "name": "Short", "country": "ITA", "language": "it", "feeds": [FEED],
          "tier": "national", "active": True, "collector": "both"}
OUTLETS = [SHARED,
           {"id": "it_runner", "name": "R", "country": "ITA", "language": "it", "feeds": [], "tier": "national", "active": True},
           {"id": "it_relay", "name": "M", "country": "ITA", "language": "it", "feeds": [], "tier": "national", "active": True,
            "collector": "self_hosted"},
           {"id": "it_off", "name": "O", "country": "ITA", "language": "it", "feeds": [], "tier": "national", "active": False,
            "inactive_reason": "test", "collector": "both"}]


def _iso(t):
    return t.replace(microsecond=0).isoformat()


NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _h(hours):
    """A time `hours` before NOW, as stored."""
    return _iso(NOW - dt.timedelta(hours=hours))


# ---------------------------------------------------------------------------
# registry selection
# ---------------------------------------------------------------------------

def test_both_is_polled_by_each_collector_and_fetched_by_the_runner_only():
    assert [o["id"] for o in registry.collectable(OUTLETS, "hosted")] == ["it_short", "it_runner"]
    assert [o["id"] for o in registry.collectable(OUTLETS, "self_hosted")] == ["it_short", "it_relay"]
    assert registry.fetcher_of(SHARED) == "hosted" and registry.is_shared(SHARED)
    assert registry.is_mine("it_short", OUTLETS, "hosted") and not registry.is_mine("it_short", OUTLETS, "self_hosted")
    assert registry.is_mine("it_relay", OUTLETS, "self_hosted") and not registry.is_mine("it_relay", OUTLETS, "hosted")
    assert not registry.is_shared(OUTLETS[1]) and not registry.is_shared(OUTLETS[2])


def test_the_schema_accepts_both_and_nothing_else_new():
    import jsonschema
    o = dict(SHARED, id="it_short2", tier="national_daily", feeds=["https://short.test/rss2"])
    registry.validate_outlets([o])
    with pytest.raises(jsonschema.ValidationError):
        registry.validate_outlets([dict(o, collector="either")])


def test_the_registry_has_shared_outlets_the_runner_fetches():
    shared = [o for o in registry.load_outlets() if registry.is_shared(o)]
    assert {"us_prnewswire", "us_globenewswire"} <= {o["id"] for o in shared}
    assert all(registry.fetcher_of(o) == "hosted" for o in shared)


def test_the_relay_does_not_fetch_pages_of_a_shared_outlet(tmp_path, monkeypatch):
    from pipeline import fetch_articles
    monkeypatch.setattr(config, "COLLECTOR", "self_hosted")
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: OUTLETS)
    conn = store.connect(tmp_path / "relay.db")
    store.insert_discovered(conn, {"url": "https://short.test/a", "outlet_id": "it_short", "country": "ITA",
                                   "language": "it", "status": "queued", "gate_relevant": 1})
    conn.commit()
    counts = fetch_articles.run(conn, "t")
    assert counts["queued"] == 0
    assert conn.execute("SELECT status FROM articles").fetchone()[0] == "queued"


def test_the_relay_drops_handed_off_rows_after_the_bundle_window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COLLECTOR", "self_hosted")
    conn = store.connect(tmp_path / "relay.db")
    for i, (outlet, age) in enumerate([("it_short", 9), ("it_short", 2), ("it_relay", 9)]):
        aid = store.insert_discovered(conn, {"url": "https://x.test/%d" % i, "outlet_id": outlet, "country": "ITA",
                                             "language": "it", "status": "queued", "gate_relevant": 1})
        store.update_article(conn, aid, discovered_at=_iso(NOW - dt.timedelta(days=age)))
    conn.commit()
    assert relay.prune_handed_off(conn, OUTLETS) == 1
    left = {r[0] for r in conn.execute("SELECT url FROM articles")}
    assert left == {"https://x.test/1", "https://x.test/2"}


# ---------------------------------------------------------------------------
# ingest: one article, one discovery count, one near-duplicate link
# ---------------------------------------------------------------------------

def _article(url, title, relevant=True, discovered=None):
    return {"url": url, "outlet_id": "it_short", "country": "ITA", "language": "it", "feed_url": FEED,
            "title": title, "status": "queued" if relevant else "gated_out", "gate_relevant": 1 if relevant else 0,
            "gate_terms": ["Cina"] if relevant else [], "discovered": discovered}


def _relay_db(path, articles):
    src = store.connect(path)
    for a in articles:
        aid = store.insert_discovered(src, a)
        if a.get("discovered"):
            store.update_article(src, aid, discovered_at=a["discovered"])
        store.record_discovery(src, "ITA", bool(a["gate_relevant"]), "it_short")
    store.record_feed_health(src, "it_short", FEED, False, "http_500", 0)
    store.record_feed_poll(src, "it_short", "ITA", FEED, 20, 20, True, 9.0)
    src.commit()
    return src


def test_shared_articles_are_inserted_once_and_counted_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    # the runner found the first article itself and counted it
    dst = store.connect(tmp_path / "main.db")
    first = _article("https://short.test/1", "La Cina apre un nuovo porto commerciale nel Mediterraneo")
    aid = store.insert_discovered(dst, first)
    store.record_discovery(dst, "ITA", True, "it_short")
    store.record_feed_health(dst, "it_short", FEED, True, None, 20)
    dst.commit()
    src = _relay_db(tmp_path / "relay.db", [
        first,
        _article("https://short.test/2", "La Cina apre un nuovo porto commerciale nel Mediterraneo oggi"),
        _article("https://short.test/3", "Sport: risultati della giornata", relevant=False),
        _article("https://short.test/4", "Meteo: pioggia al nord", relevant=False, discovered=_h(24 * 2.5)),
    ])
    data = relay.build_bundle(src)
    counts = relay.ingest(dst, data, outlets=OUTLETS)
    assert counts["inserted"] == 2 and counts["seen"] == 4 and counts["shared_stale_gated"] == 1
    # the relay's outlet-day rows, feed health and daily poll totals do not replace the runner's
    assert counts["shared_skipped"] == 3
    od = dst.execute("SELECT SUM(discovered), SUM(gate_relevant) FROM daily_outlet_discovery").fetchone()
    assert tuple(od) == (3, 2)        # the runner's article, the relay's relevant one and its gated-out one
    assert dst.execute("SELECT consecutive_failures FROM feed_health WHERE feed_url=?", (FEED,)).fetchone()[0] == 0
    assert dst.execute("SELECT COUNT(*) FROM feed_polls").fetchone()[0] == 0
    # the near-identical headline from the relay is linked to the runner's article, once
    second = dst.execute("SELECT id, dup_group_id FROM articles WHERE url='https://short.test/2'").fetchone()
    assert second["dup_group_id"] == aid
    # the same bundle again changes nothing
    again = relay.ingest(dst, data, outlets=OUTLETS)
    assert again["inserted"] == 0
    assert tuple(dst.execute("SELECT SUM(discovered), SUM(gate_relevant) FROM daily_outlet_discovery").fetchone()) == (3, 2)
    assert dst.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 3


def test_a_relay_only_outlet_still_takes_the_bundles_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    src = store.connect(tmp_path / "relay.db")
    store.insert_discovered(src, dict(_article("https://relay.test/1", "La Cina e il commercio"), outlet_id="it_relay"))
    store.record_discovery(src, "ITA", True, "it_relay")
    store.record_feed_health(src, "it_relay", "https://relay.test/rss", True, None, 10)
    src.commit()
    dst = store.connect(tmp_path / "main.db")
    counts = relay.ingest(dst, relay.build_bundle(src), outlets=OUTLETS)
    assert counts["inserted"] == 1 and counts.get("shared_skipped") is None
    assert counts["outlet_days"] == 1 and counts["feeds"] == 1


# ---------------------------------------------------------------------------
# saturation against the previous poll by either collector
# ---------------------------------------------------------------------------

def _window(newest_h, oldest_h, n=20):
    """n entry dates spread evenly from oldest_h to newest_h hours before NOW."""
    step = (oldest_h - newest_h) / float(n - 1)
    return [_h(newest_h + i * step) for i in range(n)]


def _event(polled_h, prev_h, times, saturated, missed):
    return {"_type": "feed_poll_event", "feed_url": FEED, "polled_at": _h(polled_h), "outlet_id": "it_short",
            "country": "ITA", "entries": len(times), "new_items": len(times) if saturated else 3,
            "times": json.dumps(sorted(times)), "prev_poll_at": _h(prev_h) if prev_h is not None else None,
            "saturated_own": 1 if saturated else 0, "missed_own": missed}


def _log_runner_poll(conn, polled_h, prev_h, times, saturated, missed, new_items=None):
    """A runner poll of the shared feed, recorded the way the discovery loop records it."""
    store.log_feed_poll(conn, FEED, "hosted", _h(polled_h), "it_short", "ITA", len(times),
                        len(times) if new_items is None else new_items, times, _h(prev_h), saturated, missed)
    conn.execute(
        """INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, saturated, entries, new_items, missed_estimate, collector)
           VALUES (?,?,?,?,1,?,?,?,?, 'hosted')
           ON CONFLICT(date, feed_url, collector) DO UPDATE SET polls=polls+1, saturated=saturated+excluded.saturated,
             missed_estimate=missed_estimate+excluded.missed_estimate""",
        (_h(polled_h)[:10], FEED, "it_short", "ITA", 1 if saturated else 0, len(times), len(times), missed))
    conn.commit()


def _row(conn, collector):
    r = conn.execute("SELECT SUM(polls), SUM(saturated), SUM(missed_estimate) FROM feed_polls WHERE collector=?",
                     (collector,)).fetchone()
    return (r[0] or 0, r[1] or 0, round(r[2] or 0, 2))


def test_the_runner_judges_its_poll_against_the_relays_later_poll(tmp_path):
    conn = store.connect(tmp_path / "main.db")
    store.record_feed_health(conn, "it_short", FEED, True, None, 20)
    conn.execute("UPDATE feed_health SET last_ok=?", (_h(4),))
    store.log_feed_poll(conn, FEED, "self_hosted", _h(1), "it_short", "ITA", 20, 5, [], _h(2), False, 0.0)
    own = store.feed_health_row(conn, FEED)
    prev = fetch_feeds.previous_poll(conn, FEED, own)
    assert prev["last_ok"] == _h(1)
    # a full window, nothing seen before, its oldest entry 30 minutes old: against its own poll four
    # hours ago that is three and a half hours lost; against the relay's an hour ago, half an hour
    times = _window(0.0, 0.5)
    alone = fetch_feeds.saturation(own, 20, 20, times, now=NOW)
    both = fetch_feeds.saturation(prev, 20, 20, times, now=NOW)
    assert alone[0] and both[0] and both[1] < alone[1] / 5


def test_a_relay_poll_the_runner_polled_inside_is_not_saturated(tmp_path):
    """The relay saw a full window of new items against its own previous poll three hours earlier,
    but the runner polled two hours ago and the window reaches back past that."""
    conn = store.connect(tmp_path / "main.db")
    _log_runner_poll(conn, 2.0, 5.0, _window(2.0, 6.0), False, 0.0, new_items=4)
    out = relay.reconcile_poll(conn, _event(1.0, 4.0, _window(1.0, 2.5), True, 14.0))
    assert out == {"saturated_own": True, "saturated": False, "rejudged": 0}
    assert _row(conn, "self_hosted") == (1, 0, 0.0)
    log = conn.execute("SELECT prev_poll_at, saturated_own, missed_own, saturated FROM feed_poll_log "
                       "WHERE collector='self_hosted'").fetchone()
    assert tuple(log) == (_h(2.0), 1, 14.0, 0)


def test_a_relay_poll_still_saturated_is_estimated_from_the_runners_poll(tmp_path):
    conn = store.connect(tmp_path / "main.db")
    _log_runner_poll(conn, 3.0, 6.0, _window(3.0, 7.0), False, 0.0, new_items=4)
    times = _window(0.5, 2.5)       # oldest entry 2.5 hours old: half an hour after the runner's poll
    out = relay.reconcile_poll(conn, _event(0.5, 6.0, times, True, 50.0))
    assert out["saturated"]
    polls, sat, miss = _row(conn, "self_hosted")
    assert (polls, sat) == (1, 1) and 0 < miss < 50.0


def test_a_late_relay_poll_clears_a_runner_poll_it_came_before(tmp_path):
    """The runner judged its poll saturated against its own poll six hours earlier; the relay's poll
    two hours before it reached the bundle only afterwards, and the runner's window reaches back past it."""
    conn = store.connect(tmp_path / "main.db")
    _log_runner_poll(conn, 1.0, 7.0, _window(1.0, 2.5), True, 30.0)
    assert _row(conn, "hosted") == (1, 1, 30.0)
    out = relay.reconcile_poll(conn, _event(2.0, 3.0, _window(2.0, 4.0), False, 0.0))
    assert out["rejudged"] == 1
    assert _row(conn, "hosted") == (1, 0, 0.0)
    assert _row(conn, "self_hosted") == (1, 0, 0.0)
    row = conn.execute("SELECT prev_poll_at, saturated, saturated_own FROM feed_poll_log WHERE collector='hosted'").fetchone()
    assert tuple(row) == (_h(2.0), 0, 1)


def test_relay_polls_sent_again_count_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    src = store.connect(tmp_path / "relay.db")
    for h in (3.0, 2.0, 1.0):
        store.log_feed_poll(src, FEED, "self_hosted", _h(h), "it_short", "ITA", 20, 4, _window(h, h + 3), _h(h + 1),
                            False, 0.0)
    store.log_feed_poll(src, FEED, "self_hosted", _h(24 * 4), "it_short", "ITA", 20, 4, [], _h(24 * 4 + 1), False, 0.0)
    src.commit()
    data = relay.build_bundle(src)
    events = [it for it in relay.iter_bundle(data) if it.get("_type") == "feed_poll_event"]
    assert len(events) == 3          # POLL_EVENT_DAYS: the four-day-old poll stays home
    dst = store.connect(tmp_path / "main.db")
    assert relay.ingest(dst, data, outlets=OUTLETS)["shared_polls"] == 3
    assert relay.ingest(dst, data, outlets=OUTLETS).get("shared_polls") is None
    assert _row(dst, "self_hosted") == (3, 0, 0.0)
    assert relay.POLL_EVENT_DAYS < store.POLL_LOG_DAYS


def _entries(hours):
    out = []
    for i, h in enumerate(hours):
        t = NOW - dt.timedelta(hours=h)
        out.append({"link": "https://short.test/n/%s" % t.strftime("%H%M%S%f"), "title": "Notizia %d" % i,
                    "summary": "", "published_parsed": t.utctimetuple()})
    return out


def test_the_discovery_loop_logs_a_shared_feed_and_uses_the_relays_poll(tmp_path, monkeypatch):
    conn = store.connect(tmp_path / "main.db")
    # the runner's own last poll was five hours ago; the relay's, ingested, one hour ago
    store.record_feed_health(conn, "it_short", FEED, True, None, 20)
    conn.execute("UPDATE feed_health SET last_ok=?", (_h(5),))
    store.log_feed_poll(conn, FEED, "self_hosted", _h(1), "it_short", "ITA", 20, 2, [], _h(2), False, 0.0)
    conn.commit()
    entries = _entries([0.1 * i for i in range(1, 9)])    # newest six minutes old, oldest 48 minutes
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": FEED, "kind": "editorial", "result": {"ok": True, "entries": entries, "error": None}}])
    counts = fetch_feeds.run(conn, "t", outlets=[SHARED])
    assert counts["items_new"] == 8 and counts["feeds_saturated"] == 1
    # a full window of new items whose oldest is 48 minutes old: what was lost is the 12 minutes since
    # the relay's poll, under one item, not the four hours and more since the runner's own poll
    alone = fetch_feeds.saturation({"last_ok": _h(5)}, 8, 8, [e for e in conn.execute(
        "SELECT published_at FROM articles").fetchall() for e in e], now=NOW)
    assert alone[1] > 10
    assert conn.execute("SELECT missed_estimate FROM feed_polls WHERE collector='hosted'").fetchone()[0] < 1
    row = conn.execute("SELECT collector, prev_poll_at, saturated FROM feed_poll_log WHERE collector='hosted'").fetchone()
    assert tuple(row) == ("hosted", _h(1), 1)
    # a runner-only outlet is not logged
    conn.execute("DELETE FROM feed_poll_log")
    other = dict(SHARED, id="it_runner", collector="hosted")
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": "https://runner.test/rss", "kind": "editorial",
         "result": {"ok": True, "entries": _entries([0.2]), "error": None}}])
    fetch_feeds.run(conn, "t2", outlets=[other])
    assert conn.execute("SELECT COUNT(*) FROM feed_poll_log").fetchone()[0] == 0
