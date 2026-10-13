"""How far back a run reads the shared AAP release listing (section_pages.aap_start).

The listing is read once per run and shared by the ten ACM mastheads, so the read goes back to the
earliest successful poll among the mastheads polled within AAP_RECENT_DAYS: a run stopped at its
deadline after polling some of them leaves the rest behind, and the next read covers those too. A
masthead failing for longer is left out, so it cannot hold every read back to its old date.
"""
import datetime as dt

from pipeline import fetch_feeds, registry, section_pages, store

UTC = dt.timezone.utc
ACM = ["au_canberratimes", "au_newcastleherald", "au_illawarramercury", "au_examiner", "au_bendigoadvertiser",
       "au_thecourier", "au_bordermail", "au_standard", "au_dailyadvertiser", "au_theadvocate"]


def _outlets():
    by_id = {o["id"]: o for o in registry.load_outlets()}
    return [dict(by_id[i], feeds=[]) for i in ACM]


def _page_url(outlet):
    return [fe["url"] for fe in registry.feed_entries(outlet) if fe.get("type") == section_pages.AAP_WIDGET][0]


def _set_last_ok(conn, outlet, when):
    store.record_feed_health(conn, outlet["id"], _page_url(outlet), True, None, 10)
    conn.execute("UPDATE feed_health SET last_ok=? WHERE feed_url=?",
                 (when.replace(microsecond=0).isoformat(), _page_url(outlet)))
    conn.commit()


def test_a_run_stopped_after_four_mastheads_leaves_the_next_read_covering_the_six_behind(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "a.db")
    outlets = _outlets()
    now = dt.datetime.now(UTC)
    for o in outlets:                                  # the last complete run, 20 hours ago
        _set_last_ok(conn, o, now - dt.timedelta(hours=20))
    releases = [
        {"rkey": "mid", "title": "Xinhua Silk Road: Trade fair in China's Guangzhou draws buyers", "summary": "",
         "published": now - dt.timedelta(hours=10)},
        {"rkey": "new", "title": "Xinhua Silk Road: Port in China's Ningbo opens new berth", "summary": "",
         "published": now - dt.timedelta(minutes=30)},
    ]
    reads = []

    def listing(since, max_pages=section_pages.AAP_MAX_PAGES, **kw):
        reads.append(since)
        return {"ok": True, "releases": [r for r in releases if r["published"] >= since], "status": 200,
                "error": None, "pages": 1, "complete": True}
    monkeypatch.setattr(section_pages, "aap_listing", listing)

    # The run that stopped at its deadline after polling four of the ten.
    fetch_feeds.run(conn, "stopped", outlets=outlets[:4], workers=2)
    # The next run reads back to the six left behind, not to the four polled last.
    fetch_feeds.run(conn, "next", outlets=outlets, workers=2)
    behind = (now - dt.timedelta(hours=20)).replace(microsecond=0)
    assert reads[0] == now.replace(microsecond=0) - dt.timedelta(hours=20 + section_pages.AAP_OVERLAP_HOURS)
    assert reads[-1] == behind - dt.timedelta(hours=section_pages.AAP_OVERLAP_HOURS)
    for rkey in ("mid", "new"):
        rows = conn.execute("SELECT outlet_id FROM articles WHERE url LIKE ?", ("%%/releases/%s?%%" % rkey,)).fetchall()
        assert sorted(r["outlet_id"] for r in rows) == sorted(ACM), rkey   # one copy each, none twice
    # Every masthead is now up to date, so the run after reads back only to this one.
    section_pages.aap_start(conn, outlets)
    assert section_pages._aap["since"] >= now - dt.timedelta(hours=section_pages.AAP_OVERLAP_HOURS, minutes=1)


def test_a_masthead_failing_for_ten_days_does_not_hold_the_read_back(tmp_path):
    conn = store.connect(tmp_path / "b.db")
    outlets = _outlets()
    now = dt.datetime.now(UTC)
    for o in outlets[1:]:
        _set_last_ok(conn, o, now - dt.timedelta(hours=1))
    _set_last_ok(conn, outlets[0], now - dt.timedelta(days=10))
    for _ in range(3):                                 # failing since (recorded in its feed health)
        store.record_feed_health(conn, outlets[0]["id"], _page_url(outlets[0]), False, "http_500", 0)
    section_pages.aap_start(conn, outlets)
    since = section_pages._aap["since"]
    assert now - dt.timedelta(hours=8) < since < now - dt.timedelta(hours=6)
    assert store.feed_health_row(conn, _page_url(outlets[0]))["consecutive_failures"] == 3
    # One that failed for six days still counts: the read goes back to it.
    _set_last_ok(conn, outlets[0], now - dt.timedelta(days=6))
    section_pages.aap_start(conn, outlets)
    assert now - dt.timedelta(days=6, hours=7) < section_pages._aap["since"] < now - dt.timedelta(days=6, hours=5)


def test_after_an_outage_of_every_masthead_the_read_goes_back_to_the_latest_good_poll(tmp_path):
    conn = store.connect(tmp_path / "c.db")
    outlets = _outlets()
    now = dt.datetime.now(UTC)
    for n, o in enumerate(outlets):
        _set_last_ok(conn, o, now - dt.timedelta(days=12 + n))
    section_pages.aap_start(conn, outlets)
    expected = (now - dt.timedelta(days=12)).replace(microsecond=0) - dt.timedelta(hours=section_pages.AAP_OVERLAP_HOURS)
    assert section_pages._aap["since"] == expected


def test_the_first_run_still_reads_back_to_the_backfill_date(tmp_path):
    conn = store.connect(tmp_path / "d.db")
    section_pages.aap_start(conn, _outlets())
    assert section_pages._aap["since"] == dt.datetime(2026, 9, 3, tzinfo=UTC)
