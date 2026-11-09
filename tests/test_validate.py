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

    def save_then_die(conn, oid, group):
        if len(calls) == 3:
            raise KeyboardInterrupt("the step's timeout")
        calls.append(oid)
        real(conn, oid, group)

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
