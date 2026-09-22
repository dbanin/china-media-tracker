"""The feed validator's deactivation rule, without the network."""
from pipeline import validate_sources as v


def _fail(url):
    return {"feed_url": url, "ok": False}


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
    assert v.is_dead([_fail("u"), {"feed_url": "w", "ok": True}], history, "2026-09-07") == (False, None)


def test_never_successful_feed_dies_after_enough_failures():
    history = {"u": {"last_ok": None, "total_failures": 10, "total_checks": v.MIN_CHECKS_WITHOUT_SUCCESS}}
    assert v.is_dead([_fail("u")], history, "2026-09-07") == (True, None)


def test_a_new_outlet_is_not_killed_by_one_bad_morning():
    """Three feeds failing once on the first weekly validation is one outage, not a history. The
    run's own failures used to count toward MIN_FAILED_CHECKS, so three feeds reached the
    threshold immediately and the outlet was deactivated with a reason claiming seven days."""
    results = [_fail("a"), _fail("b"), _fail("c")]
    assert v.is_dead(results, {}, "2026-09-07") == (False, None)
    fresh = {u: {"last_ok": None, "total_failures": 2, "total_checks": 2} for u in ("a", "b", "c")}
    assert v.is_dead(results, fresh, "2026-09-07") == (False, None), "six recorded failures, but only two days of them"


def test_a_feed_that_never_succeeded_needs_enough_checks_behind_it():
    few = {"u": {"last_ok": None, "total_failures": 20, "total_checks": v.MIN_CHECKS_WITHOUT_SUCCESS - 1}}
    assert v.is_dead([_fail("u")], few, "2026-09-07") == (False, None)
    enough = {"u": {"last_ok": None, "total_failures": 20, "total_checks": v.MIN_CHECKS_WITHOUT_SUCCESS}}
    assert v.is_dead([_fail("u")], enough, "2026-09-07") == (True, None)


def test_the_validation_date_is_utc():
    """last_ok is written in UTC; the runner's local date moved the seven day boundary."""
    import datetime as dt
    import inspect
    src = inspect.getsource(v.run)
    assert "dt.date.today()" not in src
    assert "dt.datetime.now(dt.timezone.utc).date()" in src
