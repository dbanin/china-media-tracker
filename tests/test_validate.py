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
