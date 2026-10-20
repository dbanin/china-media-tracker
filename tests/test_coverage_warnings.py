"""Coverage warnings and the export stamp (stress test findings of 2026-10-17): the share of feed items
missed, frozen feeds, unreadable articles from every cause together, languages excluded by project rule,
too few items to compare, outlets with nothing working, the wires' feed polls, the gaps, the 30 day
window's end, and schema_version and generated_at in every object data file."""
import datetime as dt
import json
from pathlib import Path

import yaml

from pipeline import config, export, fetch_feeds, relay, store

TODAY = dt.datetime.now(dt.timezone.utc).date()
DAY = (TODAY - dt.timedelta(days=2)).isoformat()
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _outlet(oid, country, language="en", feeds=None, active=True, tier="national", reason=None):
    o = {"id": oid, "name": oid, "country": country, "language": language,
         "feeds": feeds if feeds is not None else ["https://%s.test/feed" % oid], "tier": tier, "active": active}
    if reason:
        o["inactive_reason"] = reason
    return o


def _healthy(conn, outlet, newest=None, failures=0):
    url = outlet["feeds"][0]
    conn.execute("""INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, consecutive_failures, newest_entry)
                    VALUES (?,?,?,?,?,?)""",
                 (url, outlet["id"], NOW.isoformat(), NOW.isoformat(), failures,
                  newest or (NOW - dt.timedelta(hours=3)).isoformat()))


def _delivered(conn, outlet, n=5, day=DAY):
    conn.execute("INSERT INTO daily_outlet_discovery(date, outlet_id, country, discovered) VALUES (?,?,?,?)",
                 (day, outlet["id"], outlet["country"], n))


def _coverage(conn, country, rel=100, fetched=100, paywalled=0, failed=0, blocked=0, day=DAY):
    conn.execute("""INSERT INTO daily_coverage(date, country, discovered, gate_relevant, fetched, paywalled, failed,
                    blocked_robots, classified, llm_pending, computed_at) VALUES (?,?,?,?,?,?,?,?,0,0,?)""",
                 (day, country, rel * 10, rel, fetched, paywalled, failed, blocked, NOW.isoformat()))


def _poll(conn, outlet, polls, saturated, new_items, missed, estimator=None, day=DAY):
    conn.execute("""INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, saturated, entries, new_items,
                    missed_estimate, collector, estimator) VALUES (?,?,?,?,?,?,?,?,?,'hosted',?)""",
                 (day, outlet["feeds"][0], outlet["id"], outlet["country"], polls, saturated, new_items, new_items,
                  missed, estimator or config.MISSED_ESTIMATOR_VERSION))


def _working(conn, *outlets):
    for o in outlets:
        _healthy(conn, o)
        _delivered(conn, o)


def _types(entry):
    return {w["type"]: w["text"] for w in entry["warnings"]}


def _latest(conn, outlets, gaps=()):
    conn.commit()
    return export.build_latest(conn, outlets, list(gaps), population={})


# --- (1) the share of items missed --------------------------------------------------------------------

def test_feed_warning_rests_on_the_share_of_items_missed(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    egy, fra = _outlet("eg_a", "EGY"), _outlet("fr_a", "FRA")
    _working(conn, egy, fra)
    _coverage(conn, "EGY"); _coverage(conn, "FRA")
    # Egypt: 5 percent of polls saturated, yet half its items missed. France: 1 in 20 missed.
    _poll(conn, egy, 100, 5, 100, 100.0)
    _poll(conn, fra, 100, 5, 190, 10.0)
    latest = _latest(conn, [egy, fra])
    eg, fr = latest["countries"]["EGY"], latest["countries"]["FRA"]
    assert eg["all_time"]["miss_share"] == 0.5 and fr["all_time"]["miss_share"] == 0.05
    text = _types(eg)["feed_saturation"]
    assert text.startswith("an estimated 50 percent of this country's feed items were lost between polls: "
                           "an estimated 100 were missed (estimated since %s), against 100 found; 5 percent of its feed polls" % DAY)
    assert "feed_saturation" not in _types(fr)


def test_older_estimator_rows_count_in_neither_side_of_the_missed_share(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    o = _outlet("eg_a", "EGY")
    _working(conn, o)
    _coverage(conn, "EGY")
    _poll(conn, o, 50, 0, 50, 50.0, estimator="v1")
    _poll(conn, o, 50, 0, 95, 5.0, day=(TODAY - dt.timedelta(days=1)).isoformat())
    at = _latest(conn, [o])["countries"]["EGY"]["all_time"]
    assert (at["miss"], at["mnew"], at["mpolls"], at["polls"]) == (5, 95, 50, 100)
    assert at["miss_share"] == 0.05


def test_high_saturation_still_warns_without_an_estimate(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    o = _outlet("eg_a", "EGY")
    _working(conn, o)
    _coverage(conn, "EGY")
    _poll(conn, o, 40, 20, 40, 0.0, estimator="v1")
    text = _types(_latest(conn, [o])["countries"]["EGY"])["feed_saturation"]
    assert text.startswith("50 percent of feed polls came back as a full window")


# --- (6) the wires' polls belong to the worldwide stratum -----------------------------------------------

def test_wire_feed_polls_are_counted_under_wld_not_the_registry_country(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    us, wire = _outlet("us_a", "USA"), _outlet("us_wire", "USA", tier="distribution_wire")
    _working(conn, us, wire)
    _coverage(conn, "USA")
    _poll(conn, us, 10, 1, 30, 3.0)
    _poll(conn, wire, 90, 9, 300, 30.0)
    latest = _latest(conn, [us, wire])
    assert latest["countries"]["USA"]["all_time"]["polls"] == 10
    assert latest["countries"]["WLD"]["all_time"]["polls"] == 90
    daily = export.build_daily(conn, [us, wire])
    day = daily[DAY[:7]]["days"][DAY]["countries"]
    assert (day["USA"]["polls"], day["WLD"]["polls"], day["WLD"]["mnew"]) == (10, 90, 300)


# --- (2) frozen feeds ---------------------------------------------------------------------------------

def test_a_frozen_feed_counts_as_failing(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    old, fresh = _outlet("ca_city", "CAN"), _outlet("ca_ok", "CAN")
    _healthy(conn, old, newest="2022-05-01T10:00:00+00:00")
    _healthy(conn, fresh)
    _delivered(conn, old); _delivered(conn, fresh)
    _coverage(conn, "CAN")
    can = _latest(conn, [old, fresh])["countries"]["CAN"]
    assert (can["feeds_total"], can["feeds_ok"], can["feeds_frozen"], can["outlets_working"]) == (2, 1, 1, 1)
    text = _types(can)["feeds_failing"]
    assert text == "1 of 2 feeds are failing, 1 of them frozen, answering with no item newer than %d days" % config.FEED_FROZEN_DAYS
    feeds = {o["id"]: o["feeds"][0] for o in export.build_outlets(conn, [old, fresh])["outlets"]}
    assert feeds["ca_city"]["ok"] is False and feeds["ca_city"]["frozen"] is True
    assert feeds["ca_city"]["newest_entry"] == "2022-05-01T10:00:00+00:00"
    assert feeds["ca_ok"]["ok"] is True and feeds["ca_ok"]["frozen"] is False


def test_a_feed_with_no_newest_entry_on_record_is_not_frozen():
    cutoff = export.frozen_cutoff_now()
    assert not export.feed_frozen({"consecutive_failures": 0, "newest_entry": None}, cutoff)
    assert export.feed_working({"consecutive_failures": 0, "newest_entry": None}, cutoff)
    assert not export.feed_working({"consecutive_failures": 2, "newest_entry": NOW.isoformat()}, cutoff)


def test_record_feed_health_keeps_the_newest_entry_through_a_failed_poll(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    url = "https://x.test/feed"
    store.record_feed_health(conn, "x", url, True, None, 10, "2026-10-01T08:00:00+00:00")
    store.record_feed_health(conn, "x", url, False, "http_500", 0, None)
    assert store.feed_health_row(conn, url)["newest_entry"] == "2026-10-01T08:00:00+00:00"
    store.record_feed_health(conn, "x", url, True, None, 10, "2026-10-02T08:00:00+00:00")
    store.record_feed_health(conn, "x", url, True, None, 10)   # a caller that does not know keeps it
    assert store.feed_health_row(conn, url)["newest_entry"] == "2026-10-02T08:00:00+00:00"


def test_newest_entry_time_ignores_dates_in_the_future():
    now = dt.datetime(2026, 10, 17, 12, tzinfo=dt.timezone.utc)
    entries = [{"published_parsed": (2022, 3, 1, 9, 0, 0)}, {"published_parsed": (2030, 1, 1, 0, 0, 0)},
               {"title": "undated"}]
    assert fetch_feeds.newest_entry_time(entries, now=now) == "2022-03-01T09:00:00+00:00"
    assert fetch_feeds.newest_entry_time([{"title": "undated"}], now=now) is None


def test_relay_ingest_carries_the_newest_entry(tmp_path):
    src = store.connect(tmp_path / "relay.db")
    store.record_feed_health(src, "in_x", "https://blocked.test/feed", True, None, 40, "2026-10-10T08:00:00+00:00")
    src.commit()
    dst = store.connect(tmp_path / "main.db")
    relay.ingest(dst, relay.build_bundle(src))
    assert store.feed_health_row(dst, "https://blocked.test/feed")["newest_entry"] == "2026-10-10T08:00:00+00:00"


# --- (3) unreadable from every cause ------------------------------------------------------------------

def test_mixed_losses_get_the_combined_unreadable_warning(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    irl, fra = _outlet("ie_a", "IRL"), _outlet("fr_a", "FRA")
    _working(conn, irl, fra)
    # Ireland: 25 percent paywalled and 25 percent failed or blocked, neither at the single cause threshold.
    _coverage(conn, "IRL", fetched=50, paywalled=25, failed=15, blocked=10)
    _coverage(conn, "FRA", fetched=90, paywalled=5, failed=5)
    latest = _latest(conn, [irl, fra])
    ie = _types(latest["countries"]["IRL"])
    assert "paywalled" not in ie and "fetch_failing" not in ie
    assert ie["unreadable"].startswith("50 percent of this country's China relevant articles could not be read "
                                       "(25 paywalled, 15 failed, 10 blocked by robots.txt, of 100 attempted)")
    assert "unreadable" not in _types(latest["countries"]["FRA"])


# --- (4) languages excluded by project rule -----------------------------------------------------------

RULE = ("publishes primarily in Chinese; excluded by project rule because the owner does not read Chinese and "
        "machine translation would miss the coding distinctions")


def test_an_excluded_press_language_makes_support_partial(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    en = _outlet("tw_en", "TWN")
    zh = [_outlet("tw_zh%d" % i, "TWN", language="zh", active=False, reason=RULE) for i in range(2)]
    gr_en = _outlet("gr_en", "GRC", active=False, reason="feed returned HTTP 403 to identified crawler on 2026-09-02")
    gr_el = _outlet("gr_el", "GRC", language="el")
    _working(conn, en, gr_el)
    _coverage(conn, "TWN"); _coverage(conn, "GRC")
    latest = _latest(conn, [en, gr_el, gr_en] + zh)
    tw, gr = latest["countries"]["TWN"], latest["countries"]["GRC"]
    assert tw["language_support"] == "partial" and tw["theme_language_support"] == "partial"
    assert tw["excluded_languages"] == {"zh": 2}
    assert _types(tw)["language_excluded"].startswith("2 registered outlets publishing in zh are excluded by project rule")
    # An outlet inactive because its feed failed is not an excluded language.
    assert gr["excluded_languages"] == {} and "language_excluded" not in _types(gr)


# --- (5) too few items --------------------------------------------------------------------------------

def test_too_few_relevant_items_to_compare(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    sdn, fra = _outlet("sd_a", "SDN"), _outlet("fr_a", "FRA")
    _working(conn, sdn, fra)
    _coverage(conn, "SDN", rel=2, fetched=2)
    _coverage(conn, "FRA", rel=config.MIN_RELEVANT_ITEMS_30D, fetched=50)
    # Items older than 30 days do not count towards the window.
    _coverage(conn, "SDN", rel=500, fetched=500, day=(TODAY - dt.timedelta(days=60)).isoformat())
    latest = _latest(conn, [sdn, fra])
    assert _types(latest["countries"]["SDN"])["few_items"].startswith("only 2 China relevant items in the last 30 days")
    assert "few_items" not in _types(latest["countries"]["FRA"])
    assert "few_items" not in _types(latest["countries"].get("WLD", {"warnings": []}))


# --- (8) nothing working ------------------------------------------------------------------------------

def test_a_monitored_territory_with_nothing_working_is_flagged(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    blm, niu, fra = _outlet("bl_a", "BLM"), _outlet("nu_a", "NIU"), _outlet("fr_a", "FRA")
    _healthy(conn, blm)                       # answers, delivered nothing in 30 days
    _delivered(conn, blm, day=(TODAY - dt.timedelta(days=45)).isoformat())
    _delivered(conn, niu)                     # delivered, but no feed health: never answered
    _working(conn, fra)
    _coverage(conn, "FRA")
    latest = _latest(conn, [blm, niu, fra])
    for c in ("BLM", "NIU"):
        e = latest["countries"][c]
        assert e["coverage"] == "monitored" and e["no_working_outlet"] is True and e["outlets_working"] == 0
        assert _types(e)["no_working_outlet"].startswith("none of the 1 active outlets delivered an item through a working feed")
        assert "few_items" not in _types(e)
    assert latest["countries"]["FRA"]["no_working_outlet"] is False


# --- (7) gaps -----------------------------------------------------------------------------------------

def test_gaps_follow_coverage_and_keep_the_reason_without_an_active_outlet(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    esh, bvt = _outlet("eh_a", "ESH"), _outlet("bv_a", "BVT", active=False, reason="feed did not respond")
    _working(conn, esh)
    gaps = [{"country": "ESH", "name": "Western Sahara", "reason": "Sahara Press Service feeds did not respond."},
            {"country": "BVT", "name": "Bouvet Island", "reason": "Bouvet Island is uninhabited."},
            {"country": "ATA", "name": "Antarctica", "reason": "Antarctica has research stations and no news outlet."}]
    outlets = [esh, bvt]
    latest = _latest(conn, outlets, gaps)
    c = latest["countries"]
    assert c["ESH"]["coverage"] == "monitored" and "gap_reason" not in c["ESH"]
    assert (c["BVT"]["coverage"], c["BVT"]["gap_reason"], c["BVT"]["outlets_total"]) == ("gap", "Bouvet Island is uninhabited.", 1)
    assert c["ATA"]["coverage"] == "gap"
    meta = export.build_meta(conn, outlets, gaps, latest)
    assert meta["countries_in_gaps"] == 2
    assert [g["country"] for g in meta["gaps"]] == ["BVT", "ATA"]


def test_no_country_in_the_gaps_file_has_an_active_outlet():
    gaps = yaml.safe_load(config.GAPS_PATH.read_text(encoding="utf-8")) or []
    outlets = yaml.safe_load(config.OUTLETS_PATH.read_text(encoding="utf-8")) or []
    active = {o["country"] for o in outlets if o.get("active")}
    assert sorted(g["country"] for g in gaps if g["country"] in active) == []


# --- (9) the 30 day window, (10) the stamp, (11) staleness ---------------------------------------------

def test_latest_states_where_its_30_day_window_ends(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    latest = _latest(conn, [])
    assert latest["window_end_30d"] == TODAY.isoformat() and latest["window_end_30d_partial"] is True
    start, until = export.complete_window_30d(latest)
    assert start == latest["window_start_30d"] and until == latest["window_end_30d"]


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def test_every_object_data_file_carries_the_export_stamp(tmp_path, monkeypatch):
    db = tmp_path / "empty.db"
    conn = store.connect(db)
    monkeypatch.setattr(config, "DB_PATH", db)
    out = tmp_path / "out" / "data"
    export.run(conn, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M.md")
    meta = _load(out / "meta.json")
    assert meta["stale_after_hours"] == config.DATA_STALE_HOURS == 24
    stamp = (meta["schema_version"], meta["generated_at"])
    assert stamp[0] == config.SCHEMA_VERSION
    files = ["latest.json", "outlets.json", "examples.json", "meta.json"] + ["daily/" + p.name for p in (out / "daily").glob("*.json")]
    for name in files:
        data = _load(out / name)
        assert (data.get("schema_version"), data.get("generated_at")) == stamp, name
    # Arrays keep their shape and carry no stamp.
    assert isinstance(_load(out / "global_series.json"), list)
    assert all(isinstance(_load(p), list) for p in (out / "articles").glob("*.json"))


def test_stamp_data_leaves_arrays_and_empty_objects_alone():
    stamp = {"schema_version": 1, "generated_at": "t"}
    src = {"a": 1}
    assert export.stamp_data(src, stamp) == {"a": 1, "schema_version": 1, "generated_at": "t"} and src == {"a": 1}
    assert export.stamp_data([1], stamp) == [1] and export.stamp_data({}, stamp) == {}
