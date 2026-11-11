"""The feed validator's deactivation rule, without the network."""
import datetime as dt
import gzip
import io
import json
import sqlite3

import pytest

from pipeline import config, registry
from pipeline import validate_sources as v


def _fail(url, error="http_403"):
    return {"feed_url": url, "ok": False, "error": error}


def test_recent_success_keeps_an_outlet_alive():
    history = {"u": {"last_ok": "2026-09-06T10:00:00+00:00", "total_failures": 40}}
    assert v.is_dead([_fail("u")], history, "2026-09-07") == (False, "2026-09-06")


def test_long_silence_with_enough_failures_is_dead():
    history = {"u": {"last_ok": "2026-08-20T10:00:00+00:00", "total_failures": 5}}
    assert v.is_dead([_fail("u")], history, "2026-09-07") == (True, "2026-08-20")


def test_no_history_is_not_dead_yet():
    assert v.is_dead([_fail("u")], {}, "2026-09-07") == (False, None)


def test_one_working_feed_is_enough():
    history = {"u": {"last_ok": None, "total_failures": 50}}
    assert v.is_dead([_fail("u"), {"feed_url": "w", "ok": True}], history, "2026-09-07", "2026-08-01") == (False, None)


def test_a_new_outlet_is_not_killed_by_one_bad_morning():
    """Three feeds failing once on the first weekly validation is one outage, not a history. The
    run's own failures used to count toward MIN_FAILED_CHECKS, so three feeds reached the
    threshold immediately and the outlet was deactivated with a reason claiming seven days."""
    results = [_fail("a"), _fail("b"), _fail("c")]
    assert v.is_dead(results, {}, "2026-09-07", "2026-08-01") == (False, None)
    fresh = {u: {"last_ok": None, "total_failures": 2, "total_checks": 2} for u in ("a", "b", "c")}
    assert v.is_dead(results, fresh, "2026-09-07", "2026-09-05") == (False, None), "six recorded failures, but only two days of them"


def test_a_feed_that_never_succeeded_is_judged_by_days_since_it_was_added_not_by_checks():
    """168 recorded checks assumed hourly polling; the runner polled a median 2.7 hours apart, so a
    feed that never worked stayed active about three weeks. Days since the outlet entered the
    registry are counted instead, whatever the number of checks."""
    few = {"u": {"last_ok": None, "total_failures": 20, "total_checks": 20}}
    assert v.is_dead([_fail("u")], few, "2026-09-07", "2026-08-30") == (True, None)
    assert v.is_dead([_fail("u")], few, "2026-09-07", "2026-09-01") == (False, None), "six days only"
    many = {"u": {"last_ok": None, "total_failures": 500, "total_checks": 500}}
    assert v.is_dead([_fail("u")], many, "2026-09-07", "2026-09-03") == (False, None)
    assert v.is_dead([_fail("u")], many, "2026-09-07", None) == (False, None), "no known start, never dead"
    assert not hasattr(v, "MIN_CHECKS_WITHOUT_SUCCESS")


def test_silence_is_counted_from_the_move_or_reactivation_at_the_earliest():
    """An outlet moved to the relay, or reactivated, carries old failures and an old last success;
    collector_since restarts the clock so it is not deactivated on the next run."""
    history = {"u": {"last_ok": "2026-08-01T00:00:00+00:00", "total_failures": 300}}
    assert v.is_dead([_fail("u")], history, "2026-10-17", "2026-10-15") == (False, "2026-08-01")
    assert v.is_dead([_fail("u")], history, "2026-10-17", "2026-10-09") == (True, "2026-08-01")


LOG = """commit 2026-09-03T12:16:03-07:00
+- id: it_a
+  feeds:
+  - https://a.test/feed/
+  - 'https://a.test/q?x=1'
+  section_feeds:
+  - url: https://a.test/press
+  rank_source: https://not.a.feed/
commit 2026-09-20T23:30:00-07:00
+- id: it_b
+  - https://b.test/rss
-  - https://a.test/feed/
+  - https://a.test/feed/
commit 2026-09-22T08:38:00Z
+- id: it_z
"""


def test_the_registry_history_dates_each_outlet_and_feed_once_in_utc():
    dates = v.parse_registry_log(LOG)
    assert dates[("id", "it_a")] == "2026-09-03"
    assert dates[("url", "https://a.test/feed/")] == "2026-09-03", "the first appearance counts"
    assert dates[("url", "https://a.test/q?x=1")] == "2026-09-03"
    assert dates[("url", "https://a.test/press")] == "2026-09-03"
    assert dates[("id", "it_b")] == "2026-09-21", "23:30 at UTC-7 is the next day in UTC"
    assert dates[("id", "it_z")] == "2026-09-22"
    assert ("url", "https://not.a.feed/") not in dates


def test_measured_from_takes_the_latest_of_entry_feeds_and_collector_since():
    dates = v.parse_registry_log(LOG)
    a = {"id": "it_a", "feeds": ["https://a.test/feed/"]}
    assert v.measured_from(a, dates, "2026-10-17") == "2026-09-03"
    assert v.measured_from(dict(a, feeds=["https://a.test/feed/", "https://b.test/rss"]), dates, "2026-10-17") == "2026-09-21"
    assert v.measured_from(dict(a, feeds=["https://new.test/rss"]), dates, "2026-10-17") == "2026-10-17", "not committed yet: new"
    assert v.measured_from(dict(a, collector_since="2026-10-01"), dates, "2026-10-17") == "2026-10-01"
    assert v.measured_from(dict(a, collector_since="2026-10-01"), None, "2026-10-17") == "2026-10-01"
    assert v.measured_from(a, None, "2026-10-17") is None


def test_the_registry_history_of_this_repository_can_be_read():
    dates = v.registry_dates()
    if dates is None:
        pytest.skip("no git history here")
    assert ("id", "it_corriere") in dates


OLD = {"u": {"last_ok": "2026-09-01T00:00:00+00:00", "total_failures": 100}}
HOSTED = {"id": "it_h", "feeds": ["u"], "active": True}


def test_a_runner_only_failure_deactivates_the_outlet_and_never_moves_it():
    """Until 2026-10-22 an outlet only the runner could not reach went to the relay on the owner's
    network. The relay was retired that day, so the outlet is deactivated with the runner's reason."""
    action, why = v.decide(HOSTED, [_fail("u")], OLD, "2026-10-17", "2026-09-01")
    assert action == "deactivate" and why == ("every feed failed on every check from the GitHub Actions runner "
                                              "for more than 7 days; last success 2026-09-01 (auto)")
    assert "relay" not in why and "owner" not in why
    never = {"u": {"last_ok": None, "total_failures": 100}}
    action, why = v.decide(dict(HOSTED, collector="both"), [_fail("u")], never, "2026-10-17", "2026-09-01")
    assert action == "deactivate" and "no feed has succeeded since 2026-09-01" in why
    assert v.decide(HOSTED, [_fail("u")], OLD, "2026-10-17", "2026-10-12") is None, "added five days ago"


def test_robots_blocked_outlets_are_deactivated():
    action, why = v.decide(HOSTED, [_fail("u", "robots_disallowed")], OLD, "2026-10-17", "2026-09-01")
    assert action == "deactivate" and "robots.txt" in why and "(auto)" in why


def test_the_validator_no_longer_reads_the_relay():
    import inspect
    src = inspect.getsource(v)
    assert "relay.fetch_bundle" not in src and "iter_bundle" not in src and "RELAY_BUNDLE_KEY" not in src
    for gone in ("load_relay_health", "relay_results", "relay_reaches", "RELAY_FRESH_DAYS"):
        assert not hasattr(v, gone), gone


def test_apply_only_deactivates_and_never_moves_or_reactivates():
    outlets = [dict(HOSTED, notes="Verified 2026-09-02."), {"id": "it_d", "feeds": ["d"], "active": True},
               {"id": "it_off", "feeds": ["o"], "active": False, "collector": "self_hosted",
                "inactive_reason": "relay retired", "inactive_since": "2026-10-22"}]
    off = v.apply_decisions(outlets, {"it_d": ("deactivate", "dead (auto)"), "it_off": ("deactivate", "x (auto)")},
                            "2026-10-29")
    assert off == ["it_d"]
    assert outlets[0]["active"] is True and "collector" not in outlets[0]
    assert outlets[1]["active"] is False and outlets[1]["inactive_since"] == "2026-10-29"
    assert outlets[2]["inactive_reason"] == "relay retired" and outlets[2]["inactive_since"] == "2026-10-22"
    with pytest.raises(ValueError):
        v.apply_decisions([dict(HOSTED)], {"it_h": ("move", "to the relay")}, "2026-10-29")


def test_run_deactivates_runner_failures_and_leaves_retired_relay_outlets_alone(tmp_path, monkeypatch):
    from pipeline import store
    outlets = [
        {"id": "it_runner", "name": "R", "country": "ITA", "language": "it", "feeds": ["https://r.test/rss"],
         "tier": "national_daily", "active": True},
        {"id": "it_robots", "name": "B", "country": "ITA", "language": "it", "feeds": ["https://b.test/rss"],
         "tier": "national_daily", "active": True},
        {"id": "it_relay", "name": "S", "country": "ITA", "language": "it", "feeds": ["https://s.test/rss"],
         "tier": "national_daily", "collector": "self_hosted", "active": False,
         "inactive_reason": "relay retired", "inactive_since": "2026-10-22"},
    ]
    saved = {}
    monkeypatch.setattr(config, "COLLECTOR", "hosted")
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: outlets)
    monkeypatch.setattr(registry, "save_outlets", lambda os_, *a, **k: saved.setdefault("o", os_))
    errors = {"https://r.test/rss": "http_403", "https://b.test/rss": "robots_disallowed"}
    checked = []
    def fake_fetch(url):
        checked.append(url)
        return {"ok": False, "entries": [], "status": None, "error": errors[url], "elapsed": 0.1}
    monkeypatch.setattr(v, "fetch_feed", fake_fetch)
    conn = store.connect(tmp_path / "t.db")
    for url, oid in (("https://r.test/rss", "it_runner"), ("https://b.test/rss", "it_robots")):
        conn.execute("INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, total_checks, total_failures) "
                     "VALUES (?,?,?,?,?,?)", (url, oid, "2026-10-16T00:00:00+00:00", "2026-09-01T00:00:00+00:00", 90, 80))
    conn.commit()
    monkeypatch.setattr(store, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(v, "registry_dates", lambda *a, **k: {})
    monkeypatch.setattr(v, "measured_from", lambda o, dates, t: o.get("collector_since") or "2026-08-01")
    out = tmp_path / "v.json"
    v.run(apply=True, json_out=str(out))
    by_id = {o["id"]: o for o in saved["o"]}
    assert by_id["it_runner"]["active"] is False and "collector" not in by_id["it_runner"]
    assert "GitHub Actions runner" in by_id["it_runner"]["inactive_reason"]
    assert by_id["it_robots"]["active"] is False and "robots.txt" in by_id["it_robots"]["inactive_reason"]
    assert by_id["it_relay"]["active"] is False and by_id["it_relay"]["inactive_reason"] == "relay retired"
    assert "https://s.test/rss" not in checked, "a retired relay outlet is not polled"
    report = json.loads(out.read_text())
    assert sorted(report["newly_inactive"]) == ["it_robots", "it_runner"]
    assert not any(k.startswith(("relay", "moved")) for k in report)


def test_the_validation_date_is_utc():
    """last_ok is written in UTC; the runner's local date moved the seven day boundary."""
    import inspect
    src = inspect.getsource(v.run)
    assert "dt.date.today()" not in src
    assert "dt.datetime.now(dt.timezone.utc).date()" in src


# ---------------------------------------------------------------------------
# the weekly check finishes inside its step and keeps what it did (2026-11-09)
# ---------------------------------------------------------------------------

import threading
import time

from pipeline import fetch_articles as fa


def _outlet(oid, feeds=None):
    return {"id": oid, "name": oid, "country": "ITA", "language": "it", "tier": "national_daily", "active": True,
            "feeds": feeds or ["https://%s.test/rss" % oid]}


def _harness(tmp_path, monkeypatch, outlets, fetch, dead_history=True):
    """run() over these outlets with fetch as fetch_feed, a database in tmp_path, and every feed on record
    as long dead, so any outlet that is judged is deactivated."""
    from pipeline import store
    saved = {}
    monkeypatch.setattr(config, "COLLECTOR", "hosted")
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: outlets)
    monkeypatch.setattr(registry, "save_outlets", lambda os_, *a, **k: saved.setdefault("o", os_))
    monkeypatch.setattr(v, "fetch_feed", fetch)
    db = tmp_path / "t.db"
    conn = store.connect(db)
    if dead_history:
        for o in outlets:
            for url in o["feeds"]:
                conn.execute("INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, total_checks, "
                             "total_failures) VALUES (?,?,?,?,?,?)",
                             (url, o["id"], "2026-11-08T00:00:00+00:00", "2026-09-01T00:00:00+00:00", 90, 80))
    conn.commit()
    monkeypatch.setattr(store, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(v, "registry_dates", lambda *a, **k: {})
    monkeypatch.setattr(v, "measured_from", lambda o, dates, t: "2026-08-01")
    return db, saved


def _failing(delay=0.0, starts=None):
    def fetch(url):
        if starts is not None:
            starts.append((time.time(), url))
        time.sleep(delay)
        return {"ok": False, "entries": [], "status": 403, "error": "http_403", "elapsed": delay}
    return fetch


def _ro(db):
    return sqlite3.connect("file:%s?mode=ro" % db, uri=True)


def test_the_check_order_is_least_recently_validated_then_least_recently_checked():
    a, b, c, d = _outlet("a"), _outlet("b"), _outlet("c"), _outlet("d", ["https://d1.test/", "https://d2.test/"])
    validated = {"a": "2026-11-02T03:50:00+00:00", "b": "2026-10-26T03:50:00+00:00"}
    checked = {"https://c.test/rss": "2026-11-08T10:00:00+00:00", "https://d1.test/": "2026-11-08T11:00:00+00:00",
               "https://d2.test/": "2026-10-31T00:00:00+00:00", "https://a.test/rss": "2026-09-01T00:00:00+00:00"}
    order = [o["id"] for o in v.check_order([a, b, c, d], validated, checked)]
    assert order == ["d", "c", "b", "a"], "never validated first (by oldest feed), then the oldest validation"
    assert [o["id"] for o in v.check_order([c, a], {}, {})] == ["c", "a"], "no record at all: registry order"


def test_the_deadline_starts_no_outlet_and_judges_none_it_left(tmp_path, monkeypatch):
    """The 2026-11-02 check ran into the step's 15 minutes and lost everything. Past the deadline no outlet
    starts; those left are reported, saved nowhere and never deactivated, however dead their history."""
    outlets = [_outlet("o%d" % i) for i in range(8)]
    starts = []
    db, saved = _harness(tmp_path, monkeypatch, outlets, _failing(0.5, starts))
    out = tmp_path / "v.json"
    t0 = time.time()
    v.run(apply=True, json_out=str(out), workers=1, deadline_minutes=1.0 / 60)
    assert time.time() - t0 < 8
    assert starts and all(t - t0 < 1.0 + 0.3 for t, _ in starts), "no feed is fetched after the deadline"
    report = json.loads(out.read_text())
    n = report["outlets_checked"]
    assert 1 <= n < 8 and report["outlets_to_check"] == 8
    assert report["outlets_skipped_deadline"] == 8 - n and report["outlets_left_asleep"] == 0
    left = set(report["unchecked"]["deadline"])
    judged = {r["outlet_id"] for r in report["results"]}
    assert len(judged) == n and not judged & left
    assert set(report["dead_candidates"]) == judged == set(report["newly_inactive"])
    by_id = {o["id"]: o for o in saved["o"]}
    assert all(by_id[i]["active"] for i in left), "an outlet left for the deadline is never judged"
    ro = _ro(db)
    assert {r[0] for r in ro.execute("SELECT outlet_id FROM outlet_validation")} == judged
    stamped = {r[0] for r in ro.execute("SELECT outlet_id FROM feed_health WHERE total_checks = 91")}
    assert stamped == judged, "only checked outlets get a feed health record from this run"


def test_each_outlet_is_saved_as_it_arrives_so_a_killed_run_keeps_its_work(tmp_path, monkeypatch):
    outlets = [_outlet("o%d" % i) for i in range(5)]
    db, saved = _harness(tmp_path, monkeypatch, outlets, _failing())
    real = v.save_outlet
    calls = []

    def save_then_die(conn, oid, group, **kw):
        if len(calls) == 3:
            raise KeyboardInterrupt("the step's timeout")
        calls.append(oid)
        real(conn, oid, group, **kw)

    monkeypatch.setattr(v, "save_outlet", save_then_die)
    out = tmp_path / "v.json"
    with pytest.raises(KeyboardInterrupt):
        v.run(apply=True, json_out=str(out), workers=2, deadline_minutes=1)
    ro = _ro(db)
    assert {r[0] for r in ro.execute("SELECT outlet_id FROM outlet_validation")} == set(calls)
    assert ro.execute("SELECT COUNT(*) FROM feed_health WHERE total_checks = 91").fetchone()[0] == 3
    assert "o" not in saved and not out.exists(), "a killed run deactivates nothing"


def test_an_outlet_that_only_sleeps_through_a_crawl_delay_does_not_hold_the_check(tmp_path, monkeypatch):
    """teledoce.com asks ten minutes between requests. Its outlet sleeps without a worker permit
    (fetch_articles.paced), the others go on, and once only that sleep is left the check ends."""
    release = threading.Event()

    class Clock:
        time = staticmethod(time.time)
        sleep = staticmethod(lambda s: release.wait(s))

    monkeypatch.setattr(fa, "time", Clock)
    monkeypatch.setitem(fa._last_request, "sleepy.test", time.time() + 600)
    fast = _failing()

    def fetch(url):
        if "sleepy.test" in url:
            fa._rate_wait("sleepy.test")
        return fast(url)

    outlets = [_outlet("sleepy", ["https://sleepy.test/rss"])] + [_outlet("o%d" % i) for i in range(4)]
    db, saved = _harness(tmp_path, monkeypatch, outlets, fetch)
    monkeypatch.setattr(v, "SLEEP_PATIENCE_SECONDS", 0)
    out = tmp_path / "v.json"
    t0 = time.time()
    try:
        v.run(apply=True, json_out=str(out), workers=1, deadline_minutes=0.5)
        took = time.time() - t0
    finally:
        release.set()
    assert took < 10, "it did not wait for the sleep, nor for the 30 second deadline"
    report = json.loads(out.read_text())
    assert report["unchecked"]["asleep"] == ["sleepy"] and report["outlets_left_asleep"] == 1
    assert report["outlets_checked"] == 4 and report["outlets_skipped_deadline"] == 0
    assert "sleepy" not in report["dead_candidates"]
    assert {o["id"]: o for o in saved["o"]}["sleepy"]["active"] is True
    assert _ro(db).execute("SELECT 1 FROM outlet_validation WHERE outlet_id='sleepy'").fetchone() is None


def test_consecutive_partial_weeks_cover_the_whole_registry(tmp_path, monkeypatch):
    outlets = [_outlet("o%d" % i) for i in range(6)]
    db, _ = _harness(tmp_path, monkeypatch, outlets, _failing(0.5), dead_history=False)
    seen = []
    for week in range(10):
        out = tmp_path / ("v%d.json" % week)
        v.run(json_out=str(out), workers=1, deadline_minutes=1.2 / 60)
        report = json.loads(out.read_text())
        assert report["outlets_checked"] < 6
        now = {r["outlet_id"] for r in report["results"]}
        left = {o["id"] for o in outlets} - set().union(*seen)
        assert now <= left or left <= now, "a week checks what the weeks before left before anything again"
        seen.append(now)
        if set().union(*seen) == {o["id"] for o in outlets}:
            break
    assert set().union(*seen) == {o["id"] for o in outlets}


def test_the_report_results_follow_the_check_order(tmp_path, monkeypatch):
    outlets = [_outlet("a"), _outlet("b"), _outlet("c")]
    db, _ = _harness(tmp_path, monkeypatch, outlets, _failing(), dead_history=False)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS outlet_validation (outlet_id TEXT PRIMARY KEY, last_validated TEXT NOT NULL)")
    conn.executemany("INSERT INTO outlet_validation VALUES (?,?)", [("a", "2026-11-02"), ("c", "2026-10-26")])
    conn.commit()
    out = tmp_path / "v.json"
    v.run(json_out=str(out), workers=3, deadline_minutes=1)
    report = json.loads(out.read_text())
    assert [r["outlet_id"] for r in report["results"]] == ["b", "c", "a"]
    assert report["outlets_checked"] == 3 and report["unchecked"] == {"deadline": [], "asleep": [], "error": []}


def test_an_outlet_whose_check_breaks_is_reported_and_not_judged(tmp_path, monkeypatch):
    def fetch(url):
        if "bad" in url:
            raise RuntimeError("boom")
        return _failing()(url)
    outlets = [_outlet("bad"), _outlet("ok")]
    db, saved = _harness(tmp_path, monkeypatch, outlets, fetch)
    out = tmp_path / "v.json"
    v.run(apply=True, json_out=str(out), workers=2, deadline_minutes=1)
    report = json.loads(out.read_text())
    assert report["unchecked"]["error"] == ["bad"] and report["outlet_errors"] == 1
    assert report["newly_inactive"] == ["ok"]


def test_the_command_has_the_deadline_flag_and_does_not_wait_for_sleeping_threads():
    """The pool's threads are joined at interpreter exit, so a thread asleep for ten minutes would carry the
    step past its limit after the deadline; the command leaves with os._exit once everything is written."""
    import inspect
    src = inspect.getsource(v)
    assert "--deadline-minutes" in src and "os._exit(0)" in src
    assert 0 < v.DEADLINE_MINUTES <= 12, "well inside the step's 15 minutes"
    assert v.SLEEP_PATIENCE_SECONDS < v.DEADLINE_MINUTES * 60


def test_outlets_start_in_check_order_however_the_workers_contend(tmp_path, monkeypatch):
    """One task per outlet let a thread that had just finished take the worker permit back ahead of threads
    waiting since the start, so on the snapshot 64 outlets never checked were passed over for 1,292 checked
    after them. A task now takes its permit before it takes the next outlet."""
    outlets = [_outlet("o%02d" % i) for i in range(40)]
    starts = []
    _harness(tmp_path, monkeypatch, outlets, _failing(0.01, starts), dead_history=False)
    v.run(json_out=str(tmp_path / "v.json"), workers=2, deadline_minutes=1)
    got = [url.split("//")[1].split(".")[0] for _, url in sorted(starts)]
    want = [o["id"] for o in outlets]
    assert sorted(got) == want
    assert all(abs(got.index(oid) - i) <= 2 for i, oid in enumerate(want)), got


def test_an_outlet_read_through_its_news_sitemap_is_judged_on_it_too(tmp_path, monkeypatch):
    """Korea Times, CNN Brasil, Philenews and ARY are read through their sitemaps alone, their RSS refused by
    robots.txt; judged on the RSS only they would be retired while the sitemap worked."""
    sitemap_only = dict(_outlet("kr_sm", ["https://sm.test/rss"]), news_sitemaps=["https://sm.test/news.xml"])
    both_dead = dict(_outlet("kr_dead", ["https://dead.test/rss"]), news_sitemaps=["https://dead.test/news.xml"])
    db, saved = _harness(tmp_path, monkeypatch, [sitemap_only, both_dead], _failing())
    maps = []
    def sitemap(url):
        maps.append(url)
        ok = url == "https://sm.test/news.xml"
        return {"ok": ok, "entries": [{}] * (40 if ok else 0), "status": 200 if ok else 404,
                "error": None if ok else "http_404", "elapsed": 0.1}
    monkeypatch.setattr(v.news_sitemap, "fetch_entries", sitemap)
    out = tmp_path / "v.json"
    v.run(apply=True, json_out=str(out))
    assert sorted(maps) == ["https://dead.test/news.xml", "https://sm.test/news.xml"]
    assert json.loads(out.read_text())["newly_inactive"] == ["kr_dead"]
    by_id = {o["id"]: o for o in saved["o"]}
    assert by_id["kr_sm"]["active"] is True and by_id["kr_dead"]["active"] is False
    rows = dict(_ro(db).execute("SELECT feed_url, last_entries FROM feed_health WHERE feed_url LIKE '%news.xml'"))
    assert rows["https://sm.test/news.xml"] == 40


# ---------------------------------------------------------------------------
# outlets whose feeds cannot all be fetched inside the deadline (2026-11-09, evening)
# ---------------------------------------------------------------------------

NOW = dt.datetime(2026, 11, 16, 3, 30, tzinfo=dt.timezone.utc)


def _ago(hours, now=None):
    return ((now or NOW) - dt.timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _health(last_checked, ok, last_ok=None, failures=80):
    """A feed_health row as load_state reads it."""
    return {"last_checked": last_checked, "last_ok": last_checked if ok else last_ok,
            "consecutive_failures": 0 if ok else 5, "total_failures": failures, "total_checks": 200,
            "last_error": None if ok else "http_403"}


TELE = {"id": "uy_t", "feeds": ["https://slow.test/rss", "https://slow.test/feed"], "active": True}


def _ok(url):
    return {"ok": True, "entries": [{}] * 10, "status": 200, "error": None, "elapsed": 0.0}


def test_feeds_go_least_recently_checked_first_and_a_manual_check_keeps_registry_order():
    """Discovery reached only teledoce.com/rss from 2026-10-31 to 2026-11-09, so the weekly check starts with /feed;
    a feed never checked comes before both, and ties keep registry order."""
    o = {"id": "x", "feeds": ["https://a.test/1", "https://a.test/2", "https://a.test/3"]}
    seen = {"https://a.test/1": "2026-11-08T18:59:10+00:00", "https://a.test/2": "2026-10-31T00:23:21+00:00"}
    assert [u for u, _ in v.feed_order(o, seen)] == ["https://a.test/3", "https://a.test/2", "https://a.test/1"]
    assert [u for u, _ in v.feed_order(o, {})] == o["feeds"], "no record at all: registry order"
    assert [u for u, _ in v.feed_order(o)] == o["feeds"]


def test_a_manual_check_still_fetches_every_feed(monkeypatch):
    got = []
    monkeypatch.setattr(v, "fetch_feed", lambda url: got.append(url) or _ok(url))
    rows = v.check_outlet(TELE)
    assert got == TELE["feeds"] and [r["fetched"] for r in rows] == [True, True]


def test_the_weekly_check_stops_at_the_first_feed_that_answers(monkeypatch):
    """One working feed keeps an outlet alive, so a second request proves nothing more; the other feeds are
    reported as not fetched, never given a result."""
    got = []
    monkeypatch.setattr(v, "fetch_feed", lambda url: got.append(url) or _ok(url))
    seen = {"https://slow.test/rss": "2026-11-08T18:59:10+00:00", "https://slow.test/feed": "2026-10-31T00:23:21+00:00"}
    rows = v.check_outlet(TELE, last_checked=seen, until_ok=True)
    assert got == ["https://slow.test/feed"]
    assert rows[1] == {"outlet_id": "uy_t", "feed_url": "https://slow.test/rss", "fetched": False, "reason": "outlet_alive"}
    assert v.fetched_rows(rows) == rows[:1]
    verdict = v.judge(TELE, rows, {}, NOW)
    assert verdict["verdict"] == "alive" and verdict["decided_on"] == ["https://slow.test/feed"]
    rss = [e for e in verdict["evidence"] if e["feed_url"] == "https://slow.test/rss"][0]
    assert rss["source"] == "not_fetched" and rss["why_not_fetched"] == "outlet_alive"


def test_a_failing_feed_does_not_stop_the_check_and_a_dead_outlet_is_still_found_dead(tmp_path, monkeypatch):
    """Stopping at the first answer must not stop at a failure: an outlet whose feeds all fail has every feed
    fetched, and with a long silence on record it is deactivated as before."""
    dead = _outlet("it_dead", ["https://dead.test/a", "https://dead.test/b", "https://dead.test/c"])
    alive = _outlet("it_alive", ["https://alive.test/a", "https://alive.test/b"])
    got = []

    def fetch(url):
        got.append(url)
        return _ok(url) if "alive.test" in url else _failing()(url)

    db, saved = _harness(tmp_path, monkeypatch, [dead, alive], fetch)
    out = tmp_path / "v.json"
    v.run(apply=True, json_out=str(out))
    report = json.loads(out.read_text())
    assert sorted(u for u in got if "dead.test" in u) == dead["feeds"]
    assert len([u for u in got if "alive.test" in u]) == 1
    assert report["newly_inactive"] == ["it_dead"] and report["outlets"]["it_dead"]["verdict"] == "dead"
    assert all(e["source"] == "fetched" for e in report["outlets"]["it_dead"]["evidence"])
    assert report["outlets"]["it_alive"]["verdict"] == "alive"
    assert {o["id"]: o for o in saved["o"]}["it_alive"]["active"] is True
    stamped = {r[0] for r in _ro(db).execute("SELECT outlet_id FROM outlet_validation")}
    assert stamped == {"it_dead", "it_alive"}, "both judged on this run's fetches"
    total = dict(_ro(db).execute("SELECT feed_url, total_checks FROM feed_health WHERE feed_url LIKE '%alive.test%'"))
    assert sorted(total.values()) == [90, 91], "only the feed fetched gets a health record from this run"


class _RP:
    def __init__(self, delay):
        self.delay = delay

    def crawl_delay(self, name):
        return self.delay

    def can_fetch(self, name, url):
        return True


def test_a_feed_whose_crawl_delay_slot_falls_past_the_deadline_is_not_fetched(monkeypatch):
    """teledoce.com asks 600 s between requests: once one request is made, the next cannot come before an 11 minute
    deadline that is closer than that, so the check does not start it (it would sleep past the run's end)."""
    monkeypatch.setitem(fa._robots_cache, "slow.test", ("ok", _RP(600)))
    monkeypatch.setitem(fa._last_request, "slow.test", time.time())
    got = []
    monkeypatch.setattr(v, "fetch_feed", lambda url: got.append(url) or _failing()(url))
    rows = v.check_outlet(TELE, until_ok=True, deadline=time.time() + 60)
    assert got == [] and [r["reason"] for r in rows] == ["crawl_delay_past_deadline"] * 2
    rows = v.check_outlet(TELE, until_ok=True, deadline=time.time() + 700)
    assert got == TELE["feeds"], "a slot before the deadline is waited for"
    monkeypatch.setitem(fa._robots_cache, "fast.test", ("allow_all", None))
    assert v._slot_past("https://fast.test/rss", time.time() - 1) is False, "an ordinary host is never past it"


def test_a_fresh_recorded_success_judges_an_outlet_the_run_could_not_fetch():
    history = {"https://slow.test/rss": _health(_ago(8.5), True),
               "https://slow.test/feed": _health("2026-10-31T00:23:21+00:00", True)}
    verdict = v.judge(TELE, [], history, NOW, "2026-09-01", unfetched_reason="run_ended")
    assert verdict["verdict"] == "alive_recorded" and verdict["decided_on"] == ["https://slow.test/rss"]
    by = {e["feed_url"]: e for e in verdict["evidence"]}
    assert by["https://slow.test/rss"]["source"] == "recorded" and by["https://slow.test/rss"]["age_hours"] == 8.5
    stale = by["https://slow.test/feed"]
    assert stale["source"] == "not_fetched" and stale["record_ok"] is True and stale["record_age_hours"] > 24 * 7
    assert verdict["decision"] is None


def test_an_outlet_whose_only_evidence_is_old_is_not_judged_alive_on_it():
    """A success recorded 30 hours ago, or six days ago, is not taken as current: the outlet is neither alive nor
    dead on it, and nothing is decided."""
    for hours in (24.5, 30, 6 * 24):
        history = {u: _health(_ago(hours), True) for u in TELE["feeds"]}
        verdict = v.judge(TELE, [], history, NOW, "2026-09-01")
        assert verdict["verdict"] == "not_judged" and verdict["decision"] is None, hours
        assert all(e["source"] == "not_fetched" and e["record_ok"] for e in verdict["evidence"])
    assert v.judge(TELE, [], {u: _health(_ago(24), True) for u in TELE["feeds"]}, NOW)["verdict"] == "alive_recorded"


def test_a_record_dated_after_the_run_or_unreadable_is_no_evidence():
    for stamp in (_ago(-2), "garbage", None):
        history = {u: _health(stamp, True) for u in TELE["feeds"]}
        assert v.judge(TELE, [], history, NOW)["verdict"] == "not_judged", stamp


def test_a_dead_outlet_the_run_could_not_fetch_is_found_dead_on_fresh_records():
    """Every feed failing on a check at most RECORDED_FRESH_HOURS old, with a month of silence and enough failures
    on record, is dead; the reason says which records it rests on."""
    history = {u: _health(_ago(3), False, last_ok="2026-10-10T00:00:00+00:00") for u in TELE["feeds"]}
    verdict = v.judge(TELE, [], history, NOW, "2026-09-01", unfetched_reason="run_ended")
    assert verdict["verdict"] == "dead" and verdict["as_of"] == "2026-11-16"
    action, why = verdict["decision"]
    assert action == "deactivate" and why.endswith(" (auto)")
    assert "not fetched this run, judged on the check on record: https://slow.test/rss %s" % _ago(3) in why
    assert "last success 2026-10-10" in why


def test_a_failure_on_record_measures_the_silence_to_its_own_day_not_to_today():
    """A record from yesterday shows the silence up to yesterday only. last_ok eight days before today is seven
    before the record: not yet past DEAD_AFTER_DAYS on the evidence, so not dead."""
    now = dt.datetime(2026, 11, 16, 2, 0, tzinfo=dt.timezone.utc)
    history = {u: _health("2026-11-15T22:00:00+00:00", False, last_ok="2026-11-08T12:00:00+00:00") for u in TELE["feeds"]}
    verdict = v.judge(TELE, [], history, now, "2026-09-01")
    assert verdict["verdict"] == "not_dead" and verdict["as_of"] == "2026-11-15" and verdict["decision"] is None
    fetched = [{"feed_url": u, "fetched": True, "ok": False, "error": "http_403"} for u in TELE["feeds"]]
    assert v.judge(TELE, fetched, history, now, "2026-09-01")["verdict"] == "dead", "fetched today, it is past"


def test_missing_or_stale_evidence_never_kills_an_outlet():
    """One feed failed this run and the other has no fresh result: however long the recorded silence, the outlet
    is not judged, because the other feed may be working."""
    rows = [{"feed_url": "https://slow.test/feed", "fetched": True, "ok": False, "error": "http_403"},
            {"feed_url": "https://slow.test/rss", "fetched": False, "reason": "crawl_delay_past_deadline"}]
    dead = _health(_ago(1), False, last_ok="2026-09-01T00:00:00+00:00")
    for other in (None, _health(_ago(30), False, last_ok="2026-09-01T00:00:00+00:00")):
        history = {"https://slow.test/feed": dead}
        if other:
            history["https://slow.test/rss"] = other
        verdict = v.judge(TELE, rows, history, NOW, "2026-09-01")
        assert verdict["verdict"] == "not_judged" and verdict["decision"] is None
        rss = [e for e in verdict["evidence"] if e["feed_url"] == "https://slow.test/rss"][0]
        assert rss["source"] == "not_fetched" and rss["why_not_fetched"] == "crawl_delay_past_deadline"
    history = {"https://slow.test/feed": dead, "https://slow.test/rss": _health(_ago(2), True)}
    assert v.judge(TELE, rows, history, NOW, "2026-09-01")["verdict"] == "alive_recorded", \
        "a failure this run and a fresh success on the other feed: alive on the record"


def test_only_a_verdict_on_this_runs_fetches_stamps_the_validation():
    fetched = {"feed_url": "u", "source": "fetched", "ok": False}
    recorded = {"feed_url": "w", "source": "recorded", "ok": True}
    assert v.stamps_validation({"verdict": "alive", "evidence": [fetched, recorded]})
    assert v.stamps_validation({"verdict": "dead", "evidence": [fetched]})
    assert not v.stamps_validation({"verdict": "alive_recorded", "evidence": [fetched, recorded]})
    assert not v.stamps_validation({"verdict": "dead", "evidence": [fetched, dict(recorded, ok=False)]})
    assert not v.stamps_validation({"verdict": "not_judged", "evidence": [fetched]})


def _slow_harness(tmp_path, monkeypatch, outlets, rows):
    """run() over these outlets with sleepy.test asleep on a ten minute Crawl-delay until release is set, and
    feed_health rows {url: (last_checked, last_ok, consecutive_failures, total_failures)} stamped from the clock."""
    from pipeline import store
    release = threading.Event()

    class Clock:
        time = staticmethod(time.time)
        sleep = staticmethod(lambda s: release.wait(s))

    monkeypatch.setattr(fa, "time", Clock)
    monkeypatch.setitem(fa._last_request, "sleepy.test", time.time() + 600)
    got = []

    def fetch(url):
        got.append(url)
        if "sleepy.test" in url:
            fa._rate_wait("sleepy.test")
        return _failing()(url)

    saved = {}
    monkeypatch.setattr(config, "COLLECTOR", "hosted")
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: outlets)
    monkeypatch.setattr(registry, "save_outlets", lambda os_, *a, **k: saved.setdefault("o", os_))
    monkeypatch.setattr(v, "fetch_feed", fetch)
    db = tmp_path / "t.db"
    conn = store.connect(db)
    for url, (checked, last_ok, cf, tf) in rows.items():
        oid = [o["id"] for o in outlets if url in o["feeds"]][0]
        conn.execute("INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, consecutive_failures, "
                     "total_checks, total_failures, last_error) VALUES (?,?,?,?,?,?,?,?)",
                     (url, oid, checked, last_ok, cf, 200, tf, None if cf == 0 else "http_403"))
    conn.commit()
    monkeypatch.setattr(store, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(v, "registry_dates", lambda *a, **k: {})
    monkeypatch.setattr(v, "measured_from", lambda o, dates, t: "2026-08-01")
    monkeypatch.setattr(v, "SLEEP_PATIENCE_SECONDS", 0)
    return db, saved, got, release


def test_a_slow_host_outlet_asleep_at_the_end_is_judged_on_its_fresh_record(tmp_path, monkeypatch):
    """uy_telemundo: two feeds on a host asking ten minutes, every week left asleep and never judged. Now the feed
    checked longest ago is tried first, and when the run ends with it asleep, the other feed's success recorded
    by discovery hours ago judges the outlet alive, said so in the report with its age. It keeps its place first
    in the check order for a live attempt next week."""
    now = dt.datetime.now(dt.timezone.utc)
    slow = _outlet("uy_slow", ["https://sleepy.test/rss", "https://sleepy.test/feed"])
    rows = {"https://sleepy.test/rss": (_ago(8.5, now), _ago(8.5, now), 0, 0),
            "https://sleepy.test/feed": (_ago(9 * 24, now), _ago(9 * 24, now), 0, 0)}
    others = [_outlet("o%d" % i) for i in range(3)]
    db, saved, got, release = _slow_harness(tmp_path, monkeypatch, [slow] + others, rows)
    out = tmp_path / "v.json"
    try:
        v.run(apply=True, json_out=str(out), workers=1, deadline_minutes=0.5)
    finally:
        release.set()
    report = json.loads(out.read_text())
    assert [u for u in got if "sleepy" in u] == ["https://sleepy.test/feed"], "the feed checked longest ago goes first"
    entry = report["outlets"]["uy_slow"]
    assert entry["verdict"] == "alive_recorded" and entry["decided_on"] == ["https://sleepy.test/rss"]
    by = {e["feed_url"]: e for e in entry["evidence"]}
    assert by["https://sleepy.test/rss"]["source"] == "recorded" and 8.4 <= by["https://sleepy.test/rss"]["age_hours"] <= 8.6
    assert by["https://sleepy.test/feed"]["source"] == "not_fetched" and by["https://sleepy.test/feed"]["why_not_fetched"] == "run_ended"
    assert report["outlets_judged_on_record"] == 1 and report["verdicts"]["alive_recorded"] == 1
    assert report["outlets_checked"] == 4 and report["unchecked"]["asleep"] == [] and "uy_slow" not in report["dead_candidates"]
    assert report["newly_inactive"] == [] and "o" not in saved, "nothing deactivated, the registry not rewritten"
    ro = _ro(db)
    assert ro.execute("SELECT 1 FROM outlet_validation WHERE outlet_id='uy_slow'").fetchone() is None
    assert ro.execute("SELECT total_checks FROM feed_health WHERE feed_url='https://sleepy.test/rss'").fetchone()[0] == 200, \
        "a recorded result is read, never written again as this run's"


def test_a_dead_slow_host_outlet_is_found_dead_on_fresh_records_and_a_stale_one_is_left(tmp_path, monkeypatch):
    now = dt.datetime.now(dt.timezone.utc)
    dead = _outlet("sl_dead", ["https://sleepy.test/a", "https://sleepy.test/b"])
    stale = _outlet("sl_stale", ["https://sleepy.test/c"])
    long_ago = "2026-09-01T00:00:00+00:00"
    rows = {"https://sleepy.test/a": (_ago(2, now), long_ago, 40, 80), "https://sleepy.test/b": (_ago(3, now), long_ago, 40, 80),
            "https://sleepy.test/c": (_ago(30, now), long_ago, 40, 80)}
    db, saved, got, release = _slow_harness(tmp_path, monkeypatch, [dead, stale], rows)
    out = tmp_path / "v.json"
    try:
        v.run(apply=True, json_out=str(out), workers=2, deadline_minutes=0.5)
    finally:
        release.set()
    report = json.loads(out.read_text())
    assert report["newly_inactive"] == ["sl_dead"]
    why = {o["id"]: o for o in saved["o"]}["sl_dead"]["inactive_reason"]
    assert "judged on the check on record" in why and "https://sleepy.test/b" in why
    assert report["outlets"]["sl_stale"]["verdict"] == "not_judged"
    assert report["outlets"]["sl_stale"]["evidence"][0]["record_age_hours"] >= 29.9
    assert {o["id"]: o for o in saved["o"]}["sl_stale"]["active"] is True
    assert report["unchecked"]["asleep"] == ["sl_stale"]


def test_the_report_names_the_evidence_of_every_outlet(tmp_path, monkeypatch):
    outlets = [_outlet("o%d" % i) for i in range(8)]
    db, saved = _harness(tmp_path, monkeypatch, outlets, _failing(0.5), dead_history=False)
    out = tmp_path / "v.json"
    v.run(json_out=str(out), workers=1, deadline_minutes=1.0 / 60)
    report = json.loads(out.read_text())
    assert list(report["outlets"]) == [o["id"] for o in outlets]
    for oid, entry in report["outlets"].items():
        assert entry["verdict"] in v.JUDGED + ("not_judged",)
        assert [e["feed_url"] for e in entry["evidence"]] == ["https://%s.test/rss" % oid]
        assert all(e["source"] in ("fetched", "recorded", "not_fetched") for e in entry["evidence"])
    never = [oid for oid, e in report["outlets"].items() if e["evidence"][0].get("why_not_fetched") == "not_started"]
    assert never and all(report["outlets"][oid]["verdict"] == "not_judged" for oid in never)
    assert set(never) <= set(report["unchecked"]["deadline"])
    assert report["recorded_fresh_hours"] == v.RECORDED_FRESH_HOURS


# ---------------------------------------------------------------------------
# When the weekly check is due (due(), read by the workflow before a scheduled collection)
# ---------------------------------------------------------------------------

def _utc(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc)


def test_the_check_week_begins_on_monday_at_0330_utc():
    assert v.week_start(_utc("2026-11-11T12:00:00")) == _utc("2026-11-09T03:30:00")      # a Wednesday
    assert v.week_start(_utc("2026-11-09T03:30:00")) == _utc("2026-11-09T03:30:00")      # the minute it begins
    assert v.week_start(_utc("2026-11-09T03:29:00")) == _utc("2026-11-02T03:30:00")      # Monday, a minute before
    assert v.week_start(_utc("2026-11-15T23:59:00")) == _utc("2026-11-09T03:30:00")      # Sunday night


def _due_db(tmp_path, stamps=(), runs=()):
    from pipeline import store
    conn = store.connect(tmp_path / "d.db")
    v.load_state(conn)
    for oid, at in stamps:
        conn.execute("INSERT INTO %s(outlet_id, last_validated) VALUES (?, ?)" % v.CHECKS_TABLE, (oid, at))
    for started, ok in runs:
        conn.execute("INSERT INTO run_log(run_id, stage, started_at, ok) VALUES ('r', ?, ?, ?)", (v.RUN_STAGE, started, ok))
    conn.commit()
    return conn


def test_the_check_is_due_once_a_week_has_begun_with_no_outlet_validated(tmp_path):
    conn = _due_db(tmp_path, stamps=[("a", "2026-11-02T03:50:00+00:00"), ("b", "2026-11-08T22:00:00+00:00")])
    assert v.due(conn, _utc("2026-11-09T03:29:00"))[0] is False        # still last week, which has its check
    is_due, why = v.due(conn, _utc("2026-11-09T03:52:00"))
    assert is_due and "2026-11-09T03:30:00" in why and "2026-11-08T22:00:00" in why
    assert v.due(conn, _utc("2026-11-14T18:00:00"))[0] is True         # every trigger was dropped until Saturday


def test_the_check_is_not_due_again_in_a_week_that_has_one(tmp_path):
    conn = _due_db(tmp_path, stamps=[("a", "2026-11-09T04:10:00+00:00")])
    for now in ("2026-11-09T04:11:00", "2026-11-12T00:00:00", "2026-11-16T03:29:00"):
        is_due, why = v.due(conn, _utc(now))
        assert not is_due and "an outlet was validated" in why
    assert v.due(conn, _utc("2026-11-16T03:30:00"))[0] is True


def test_a_database_with_no_check_on_record_is_due(tmp_path):
    from pipeline import store
    conn = store.connect(tmp_path / "fresh.db")                       # outlet_validation not made yet
    is_due, why = v.due(conn, _utc("2026-11-11T12:00:00"))
    assert is_due and "never" in why
    assert v.due(sqlite3.connect(":memory:"), _utc("2026-11-11T12:00:00"))[0] is True     # no run_log either


def test_a_check_that_broke_before_it_validated_anything_is_tried_again_after_a_day_not_every_run(tmp_path):
    conn = _due_db(tmp_path, stamps=[("a", "2026-11-02T04:00:00+00:00")], runs=[("2026-11-09T04:00:00+00:00", None)])
    is_due, why = v.due(conn, _utc("2026-11-09T05:00:00"))
    assert not is_due and "stamped nothing" in why
    assert v.due(conn, _utc("2026-11-09T23:59:00"))[0] is False
    assert v.due(conn, _utc("2026-11-10T00:00:00"))[0] is True         # RETRY_AFTER_HOURS later


def test_a_check_that_finished_counts_for_its_week_even_with_no_outlet_stamped(tmp_path):
    conn = _due_db(tmp_path, runs=[("2026-11-09T04:00:00+00:00", 1)])
    is_due, why = v.due(conn, _utc("2026-11-12T00:00:00"))
    assert not is_due and "a check finished" in why
    assert v.due(conn, _utc("2026-11-16T04:00:00"))[0] is True


def test_stamps_that_cannot_be_read_or_lie_in_the_future_count_as_none(tmp_path):
    conn = _due_db(tmp_path, stamps=[("a", "not a date")], runs=[("2027-01-01T00:00:00+00:00", 1)])
    assert v.due(conn, _utc("2026-11-11T12:00:00"))[0] is True


def test_a_run_records_itself_so_the_week_has_its_check(tmp_path, monkeypatch):
    outlets = [_outlet("o%d" % i) for i in range(3)]
    db, saved = _harness(tmp_path, monkeypatch, outlets, _failing(0.0), dead_history=False)
    v.run(json_out=str(tmp_path / "v.json"), workers=1)
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT stage, ok, started_at, finished_at, counts FROM run_log WHERE stage=?", (v.RUN_STAGE,)).fetchall()
    assert len(rows) == 1 and rows[0][1] == 1 and rows[0][3]
    counts = json.loads(rows[0][4])
    assert counts["outlets_to_check"] == 3 and counts["applied"] is False
    now = dt.datetime.now(dt.timezone.utc)
    assert v.due(conn, now)[0] is False and v.due(conn, now + dt.timedelta(days=7, minutes=1))[0] is True
