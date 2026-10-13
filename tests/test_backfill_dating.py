"""Day of record for items a backfill read brings in (store.backfill_day, config.BACKFILL_LAG_HOURS).

Every figure counts an item on its discovery day. The AAP release reader's first run reads weeks of
releases at once, so an item it brings in that was published more than BACKFILL_LAG_HOURS before it
was seen counts on its publication day instead, in the counts and in the discovery denominators
alike. Items from ordinary feeds keep the discovery day whatever their published time says.
"""
import datetime as dt

from pipeline import config, export, fetch_feeds, methodology, registry, relay, section_pages, store

UTC = dt.timezone.utc


def _iso(t):
    return t.isoformat()


def test_backfill_day_takes_the_publication_day_only_past_the_lag():
    now = dt.datetime(2026, 10, 13, 6, 0, tzinfo=UTC)
    assert store.backfill_day(_iso(now - dt.timedelta(days=10)), now) == "2026-10-03"
    assert store.backfill_day(_iso(now - dt.timedelta(hours=config.BACKFILL_LAG_HOURS + 1)), now) == "2026-10-11"
    # Within the lag: an ordinary late poll, counted on the discovery day like everything else.
    assert store.backfill_day(_iso(now - dt.timedelta(hours=config.BACKFILL_LAG_HOURS - 1)), now) is None
    # Not credible: before collection began, after discovery, unreadable or missing.
    assert store.backfill_day("2026-09-02T23:00:00+00:00", now) is None
    assert store.backfill_day(_iso(now + dt.timedelta(hours=2)), now) is None
    assert store.backfill_day("yesterday", now) is None
    assert store.backfill_day(None, now) is None
    # A published time with an offset is placed on its UTC day.
    assert store.backfill_day("2026-10-05T08:00:00+10:00", now) == "2026-10-04"


def test_export_day_prefers_the_day_of_record():
    assert export._day({"discovered_at": "2026-10-13T01:00:00+00:00", "day_of_record": "2026-09-20"}) == "2026-09-20"
    assert export._day({"discovered_at": "2026-10-13T01:00:00+00:00", "day_of_record": None}) == "2026-10-13"
    assert export._day({"discovered_at": "2026-10-13T01:00:00+00:00"}) == "2026-10-13"


def _aap_run(monkeypatch, conn, releases):
    def listing(since, max_pages=section_pages.AAP_MAX_PAGES, start_page=1, stop_by=None, resume_oldest=None):
        stamps = [r["published"] for r in releases if r["published"]]
        return {"ok": True, "releases": releases, "status": 200, "error": None, "pages": 1, "complete": True,
                "oldest": min(stamps) if stamps else None, "last_page": 1}
    monkeypatch.setattr(section_pages, "aap_listing", listing)
    by_id = {o["id"]: o for o in registry.load_outlets()}
    outlets = [dict(by_id["au_canberratimes"], feeds=[])]
    fetch_feeds.run(conn, "t", outlets=outlets, workers=1)
    return outlets


def test_a_backfill_read_counts_old_items_on_their_publication_day_in_numerator_and_denominator(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COLLECTION_START", "2000-01-01")
    conn = store.connect(tmp_path / "a.db")
    now = dt.datetime.now(UTC)
    old = (now - dt.timedelta(days=10)).replace(hour=12, minute=0, second=0, microsecond=0)
    old_day, today = old.date().isoformat(), now.date().isoformat()
    releases = [
        {"rkey": "cn_old", "title": "Xinhua Silk Road: trade fair opens in China's Xiamen", "summary": "",
         "published": old},
        {"rkey": "us_old", "title": "Acme Widgets reports record third quarter revenue growth", "summary": "",
         "published": old},
        {"rkey": "cn_new", "title": "Xinhua Silk Road: marathon in NE China's Shenyang draws runners", "summary": "",
         "published": now},
    ]
    outlets = _aap_run(monkeypatch, conn, releases)
    rows = {r["url"].split("/releases/")[1].split("?")[0]: r for r in
            conn.execute("SELECT id, url, day_of_record, discovered_at, gate_relevant FROM articles")}
    assert rows["cn_old"]["day_of_record"] == old_day and rows["us_old"]["day_of_record"] == old_day
    assert rows["cn_new"]["day_of_record"] is None
    assert rows["cn_old"]["discovered_at"][:10] == today      # discovery time itself is kept as observed
    disc = {r["date"]: (r["discovered"], r["gate_relevant"]) for r in
            conn.execute("SELECT date, discovered, gate_relevant FROM daily_outlet_discovery WHERE outlet_id='au_canberratimes'")}
    assert disc == {old_day: (2, 1), today: (1, 1)}
    for key in ("cn_old", "cn_new"):
        store.insert_classification(conn, rows[key]["id"], "rules", "A", 1.0)
    export.rebuild_rollups(conn, outlets=registry.load_outlets())
    counts = {r["date"]: r["n"] for r in conn.execute(
        "SELECT date, n FROM daily_counts WHERE country='AUS' AND category='A' AND scope='all'")}
    assert counts == {old_day: 1, today: 1}
    cov = {r["date"]: r["discovered"] for r in conn.execute("SELECT date, discovered FROM daily_coverage WHERE country='AUS'")}
    assert cov[old_day] == 2 and cov[today] == 1
    assert {r["date"]: r["discovered"] for r in conn.execute("SELECT date, discovered FROM daily_discovery")} == \
        {old_day: 2, today: 1}


def test_an_ordinary_feed_keeps_the_discovery_day_whatever_its_published_time(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COLLECTION_START", "2000-01-01")
    conn = store.connect(tmp_path / "b.db")
    old = dt.datetime.now(UTC) - dt.timedelta(days=10)
    outlet = {"id": "au_x", "country": "AUS", "language": "en", "feeds": ["https://x.example/rss"]}
    result = {"ok": True, "error": None, "entries": [
        {"link": "https://x.example/a", "title": "China and Australia sign a trade deal", "summary": "",
         "published_parsed": old.timetuple()}]}
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": "https://x.example/rss", "kind": registry.EDITORIAL, "result": result}])
    monkeypatch.setattr(section_pages, "aap_start", lambda conn, outlets: None)
    # Polled successfully earlier today, so this is an ordinary poll, not the feed's first (backfill_poll).
    store.record_feed_health(conn, "au_x", "https://x.example/rss", True, None, 1)
    store.record_feed_poll(conn, "au_x", "AUS", "https://x.example/rss", 1, 0, False, 0.0)
    counts = fetch_feeds.run(conn, "t", outlets=[outlet], workers=1)
    row = conn.execute("SELECT day_of_record, discovered_at FROM articles").fetchone()
    assert row["day_of_record"] is None
    assert [r["date"] for r in conn.execute("SELECT date FROM daily_outlet_discovery")] == [row["discovered_at"][:10]]
    assert not any(k.startswith("feeds_backfill_") for k in counts)


FEED = "https://y.example/rss"


def _feed_run(monkeypatch, conn, ages_hours):
    """One poll of FEED delivering an item per age (hours before now); {link: day_of_record}, counts."""
    now = dt.datetime.now(UTC)
    outlet = {"id": "au_y", "country": "AUS", "language": "en", "feeds": [FEED]}
    entries = [{"link": "https://y.example/%d" % h, "title": "China and Australia sign trade deal %d" % h,
                "summary": "", "published_parsed": (now - dt.timedelta(hours=h)).utctimetuple()} for h in ages_hours]
    result = {"ok": True, "error": None, "entries": entries}
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": FEED, "kind": registry.EDITORIAL, "result": result}])
    monkeypatch.setattr(section_pages, "aap_start", lambda conn, outlets: None)
    counts = fetch_feeds.run(conn, "t", outlets=[outlet], workers=1)
    days = {r["url"].rsplit("/", 1)[1]: r["day_of_record"] for r in conn.execute("SELECT url, day_of_record FROM articles")}
    return days, counts


def _day(hours_ago):
    return (dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago)).date().isoformat()


def test_a_new_feeds_first_poll_is_a_backfill_read(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COLLECTION_START", "2000-01-01")
    conn = store.connect(tmp_path / "f.db")
    # validate_sources records last_ok for a feed it checks without collecting from it: still a first poll.
    store.record_feed_health(conn, "au_y", FEED, True, None, 3)
    days, counts = _feed_run(monkeypatch, conn, [240, 72, 2])
    assert days == {"240": _day(240), "72": _day(72), "2": None}
    assert counts["feeds_backfill_first"] == 1
    disc = {r["date"]: r["discovered"] for r in conn.execute("SELECT date, discovered FROM daily_outlet_discovery")}
    assert disc[_day(240)] == 1 and disc[_day(72)] == 1 and sum(disc.values()) == 3
    # The next poll is an ordinary one: an old item it brings for the first time counts on the discovery day.
    days, counts = _feed_run(monkeypatch, conn, [240, 72, 2, 100])
    assert days["100"] is None and not any(k.startswith("feeds_backfill_") for k in counts)


def test_a_feed_back_after_five_days_is_a_backfill_read(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COLLECTION_START", "2000-01-01")
    conn = store.connect(tmp_path / "g.db")
    then = dt.datetime.now(UTC) - dt.timedelta(days=5)
    conn.execute("""INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, entries, new_items)
                    VALUES (?,?,?,?,1,3,3)""", (then.date().isoformat(), FEED, "au_y", "AUS"))
    store.record_feed_health(conn, "au_y", FEED, True, None, 3)
    conn.execute("UPDATE feed_health SET last_ok=? WHERE feed_url=?", (_iso(then), FEED))
    days, counts = _feed_run(monkeypatch, conn, [96, 2])
    assert days == {"96": _day(96), "2": None}
    assert counts["feeds_backfill_gap"] == 1


def test_a_poll_within_the_gap_is_ordinary(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COLLECTION_START", "2000-01-01")
    conn = store.connect(tmp_path / "h.db")
    then = dt.datetime.now(UTC) - dt.timedelta(days=config.BACKFILL_GAP_DAYS - 1)
    conn.execute("""INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, entries, new_items)
                    VALUES (?,?,?,?,1,3,3)""", (then.date().isoformat(), FEED, "au_y", "AUS"))
    days, counts = _feed_run(monkeypatch, conn, [96, 2])
    assert days == {"96": None, "2": None}
    assert not any(k.startswith("feeds_backfill_") for k in counts)


def test_backfill_poll_judges_the_last_poll_by_either_collector(tmp_path):
    conn = store.connect(tmp_path / "k.db")
    now = dt.datetime.now(UTC)
    assert fetch_feeds.backfill_poll(conn, FEED, None, now) == "first"
    # A shared feed's poll by the other collector, logged in feed_poll_log, is a previous poll.
    store.log_feed_poll(conn, FEED, "self_hosted", _iso(now - dt.timedelta(hours=6)), "au_y", "AUS", 3, 3, [],
                        None, False, 0.0)
    assert fetch_feeds.backfill_poll(conn, FEED, None, now) is None
    later = now + dt.timedelta(days=config.BACKFILL_GAP_DAYS, hours=1)
    assert fetch_feeds.backfill_poll(conn, FEED, None, later) == "gap"
    # This collector's own last_ok, when later than the ledger, decides.
    assert fetch_feeds.backfill_poll(conn, FEED, {"last_ok": _iso(later - dt.timedelta(hours=2))}, later) is None


def test_a_readmitted_backfilled_item_is_corrected_on_its_day_of_record(tmp_path):
    conn = store.connect(tmp_path / "c.db")
    item = {"url": "https://x.example/b", "outlet_id": "au_x", "country": "AUS", "language": "en",
            "title": "Something", "published_at": "2026-09-20T03:00:00+00:00", "status": "gated_out",
            "gate_relevant": 0, "gate_terms": [], "day_of_record": "2026-09-20"}
    assert store.insert_discovered(conn, item)
    store.record_discovery(conn, "AUS", False, "au_x", item["day_of_record"])
    assert fetch_feeds.readmit_gated_out(conn, dict(item, gate_terms=["section:press_release"]))
    row = conn.execute("SELECT date, discovered, gate_relevant FROM daily_outlet_discovery").fetchone()
    assert (row["date"], row["discovered"], row["gate_relevant"]) == ("2026-09-20", 1, 1)


def test_the_relay_bundle_carries_the_day_of_record():
    assert "day_of_record" in relay.ARTICLE_FIELDS


def test_the_methodology_states_the_backfill_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BACKFILL_LAG_HOURS", 48)
    monkeypatch.setattr(config, "COLLECTION_START", "2026-09-04")
    monkeypatch.setattr(config, "BACKFILL_GAP_DAYS", 5, raising=False)
    conn = store.connect(tmp_path / "m.db")
    export.run(conn, "test", export_dir=tmp_path / "out", audit_dir=tmp_path / "audit",
               methodology_path=tmp_path / "M.md")
    text = " ".join((tmp_path / "M.md").read_text(encoding="utf-8").split())
    assert "Every figure counts an item on the UTC day it was discovered" in text
    assert "discovered more than 48 hours after it was published, which counts on its publication day" in text
    assert "a feed's first poll, and its first poll after more than 5 days without one" in text
    assert "on or after 2026-09-04" in text and "put weeks of items on one day" in text
    assert "was admitted by the gate in force when it was read (version %s at this export)" % config.GATE_VERSION in text
    own = methodology.backfill_day_text({})
    assert own in text and "\u2014" not in own and "\u2013" not in own
