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
    history = {"u": {"last_ok": None, "total_failures": 10}}
    assert v.is_dead([_fail("u")], history, "2026-09-07") == (True, None)
