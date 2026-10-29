"""The propaganda calendar (sources/political_calendar.yaml, pipeline/calendar_test.py): the validator, the
pre-registration fields, and the test on synthetic data: a known rise is found, none is found where there
is none, and windows are kept out of normal days."""
import copy
import datetime as dt
import math
import random
import re
from pathlib import Path

import pytest
import yaml

from pipeline import calendar_test as ct
from pipeline import measures

ROOT = Path(__file__).resolve().parent.parent
DASHES = ("\u2013", "\u2014")


def _event(eid, start, end=None, **kw):
    e = {"id": eid, "name": "Test event %s" % eid, "start": start, "end": end or start, "category": "state",
         "recurrence": "annual_fixed", "status": "fixed", "known_before_compiled": True, "added_on": "2026-10-28",
         "keywords": ["national day", "国庆"], "source_url": "https://example.org/source",
         "source_title": "A public source for the test"}
    e.update(kw)
    return e


def _cal(events, lead=3, lag=3, compiled="2026-10-28"):
    return {"compiled_on": compiled, "window": {"lead_days": lead, "lag_days": lag}, "events": events}


def _poisson(rng, lam):
    # Knuth: small lambdas only, which is all the tests need
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def _daily(start, n_days, base=10.0, rises=None, country="ITA", seed=7, override=None):
    """{month: {"days": {...}}} in the daily files' shape: one country, discovery and labelling complete."""
    rng = random.Random(seed)
    rises = rises or {}
    out = {}
    d0 = dt.date.fromisoformat(start)
    for i in range(n_days):
        d = (d0 + dt.timedelta(days=i)).isoformat()
        lam = rises.get(d, base)
        a = _poisson(rng, lam)
        e = {"countries": {country: {"A": a, "disc": 1000, "rel": 100, "cls": 80}},
             "labels_on_older_ruleset": 0, "labels_on_older_prompt": 0}
        if override and d in override:
            override[d](e)
        out.setdefault(d[:7], {"days": {}})["days"][d] = e
    return out


def _span(a, b):
    return ct._span(a, b)


# ---------------------------------------------------------------------------
# The calendar file
# ---------------------------------------------------------------------------

def test_committed_calendar_validates():
    data = ct.load_file()
    assert ct.validate(data) == []
    assert data["compiled_on"] == "2026-10-28"
    assert data["window"] == {"lead_days": 3, "lag_days": 3}
    ids = {e["id"] for e in data["events"]}
    for must in ("victory_day_2026", "national_day_2026", "long_march_90", "fifth_plenum_20th_cc", "ciie_2026",
                 "apec_2026_shenzhen", "two_sessions_2027", "cpc_founding_2027", "pla_centenary_2027",
                 "spring_festival_2027", "mid_autumn_2026", "party_congress_21"):
        assert must in ids, must


def test_header_says_it_is_the_preregistration():
    text = (ROOT / "sources" / "political_calendar.yaml").read_text(encoding="utf-8")
    head = text.split("compiled_on:")[0]
    assert "pre-registration" in head and "retrospective" in head and "forward test" in head


@pytest.mark.parametrize("change, needle", [
    (lambda d: d["events"].append(copy.deepcopy(d["events"][0])), "listed twice"),
    (lambda d: d["events"][0].update(status="expected"), "expected entry"),
    (lambda d: d["events"][0].update(start="2026-10-05", end="2026-10-01"), "end is before start"),
    (lambda d: d["events"][0].update(name="Victory Day \u2014 a test"), "dash"),
    (lambda d: d["events"][0].update(start=None, end=None), "expected_period"),
    (lambda d: d["events"][0].update(start="2026-08-01", end="2026-08-01"), "outside the dates"),
    (lambda d: d["events"][0].update(added_on="2026-10-01"), "before compiled_on"),
    (lambda d: d["events"][0].update(keywords=["Victory Day"]), "lower case"),
    (lambda d: d["events"][0].update(source_url="http://example.org/x"), "source_url"),
    (lambda d: d["events"][0].update(category="party_line"), "category"),
    (lambda d: d["events"][0].pop("known_before_compiled"), "known_before_compiled"),
])
def test_validator_catches(change, needle):
    data = ct.load_file()
    change(data)
    errs = ct.validate(data)
    assert errs and any(needle in e for e in errs), errs


def test_window_kinds_follow_the_compile_day():
    cal = _cal([_event("a", "2026-10-01"), _event("b", "2026-10-31"), _event("c", "2026-10-29"),
                _event("d", "2026-12-01", added_on="2026-11-15"), _event("e", None, None, status="expected",
                known_before_compiled=False, expected_period="2027")])
    by = {w["id"]: w for w in ct.windows(cal)}
    assert by["a"]["kind"] == "retrospective" and by["a"]["window_start"] == "2026-09-28" and by["a"]["window_end"] == "2026-10-04"
    assert by["b"]["kind"] == "forward" and by["b"]["window_start"] == "2026-10-28"
    assert by["c"]["kind"] == "retrospective"    # 29 October less 3 days begins before the compile day
    assert by["d"]["kind"] == "added_later"
    assert "e" not in by and [u["id"] for u in ct.undated(cal)] == ["e"]


# ---------------------------------------------------------------------------
# The test on synthetic data
# ---------------------------------------------------------------------------

def test_a_known_rise_is_found():
    rises = {d: 30.0 for d in _span("2026-11-07", "2026-11-13")}
    daily = _daily("2026-09-01", 150, rises=rises)
    res = ct.analyse(daily, "2026-12-31", _cal([_event("x", "2026-11-10")]), {}, None)
    w = res["windows"][0]
    assert w["kind"] == "forward" and w["state"] == "tested"
    for name in ("normal", "other"):
        t = w["comparisons"][name]
        assert t["state"] == "tested" and t["window_days"] == 7
        assert 2.2 < t["ratio"] < 3.8, t
        assert t["ci_low"] > 1.0
        assert t["p_shift"] is not None and t["p_shift"] <= 0.06
    assert ct.summarise(res)["forward"]["raised_normal"] == 1


def test_no_rise_where_there_is_none():
    daily = _daily("2026-09-01", 150)
    res = ct.analyse(daily, "2026-12-31", _cal([_event("x", "2026-11-10")]), {}, None)
    t = res["windows"][0]["comparisons"]["normal"]
    assert t["state"] == "tested" and t["ci_low"] < 1.0 < t["ci_high"], t
    assert ct.summarise(res)["forward"]["raised_normal"] == 0


def test_windows_are_kept_out_of_normal_days():
    # A big rise around event b must not lift the normal days event a is compared with.
    rises = {d: 40.0 for d in _span("2026-10-22", "2026-10-28")}
    daily = _daily("2026-09-01", 150, rises=rises)
    cal = _cal([_event("a", "2026-10-10"), _event("b", "2026-10-25")])
    res = ct.analyse(daily, "2026-12-31", cal, {}, None)
    a = next(w for w in res["windows"] if w["id"] == "a")
    n, o = a["comparisons"]["normal"], a["comparisons"]["other"]
    # normal days leave out both windows; other days only a's own
    assert o["comparison_days"] - n["comparison_days"] == 7
    assert n["comparison_mean"] < o["comparison_mean"]
    assert n["ci_low"] < 1.0 < n["ci_high"]
    assert res["normal_days"] == 121 - 14 - 1     # complete days before until; the first day of record is partial


def test_confounded_days_are_left_out():
    def backlog(e):
        e["countries"]["ITA"]["cls"] = 10            # labelling well behind
    override = {"2026-11-09": backlog}
    rises = {d: 30.0 for d in _span("2026-11-07", "2026-11-13")}
    daily = _daily("2026-09-01", 150, rises=rises, override=override)
    meta = {"ruleset_changes": [{"date": "2026-11-11", "version": "x"}], "gate_changes": []}
    res = ct.analyse(daily, "2026-12-31", _cal([_event("x", "2026-11-10")]), meta, None)
    w = res["windows"][0]
    assert w["excluded"] == {"labelling_incomplete": 1, "ruleset_change": 3}
    assert w["comparisons"]["normal"]["window_days"] == 3
    assert set(res["confounded"]["2026-11-11"]) == {"ruleset_change"}
    assert res["confounded"]["2026-09-01"] == ["first_day"]


def test_label_mix_days_are_left_out():
    def newer(e):
        e["labels_on_older_ruleset"] = 0
    def older(e):
        e["labels_on_older_ruleset"] = 80
    days = _span("2026-09-01", "2027-01-28")
    override = {d: older for d in days}
    for d in _span("2026-11-09", "2026-11-11"):
        override[d] = newer
    daily = _daily("2026-09-01", 150, override=override)
    res = ct.analyse(daily, "2026-12-31", _cal([_event("x", "2026-11-10")]), {}, None)
    t = res["windows"][0]["comparisons"]["normal"]
    assert t["label_mix_days"] == 3 and t["window_days"] == 4


def test_too_few_days():
    daily = _daily("2026-10-01", 12)
    res = ct.analyse(daily, "2026-10-13", _cal([_event("x", "2026-10-06")]), {}, None)
    w = res["windows"][0]
    assert w["state"] == "too_few_days"
    assert w["comparisons"]["normal"]["state"] == "too_few_days" and w["comparisons"]["normal"]["ratio"] is None
    up = ct.analyse(daily, "2026-10-13", _cal([_event("y", "2026-12-06")]), {}, None)["windows"][0]
    assert up["state"] == "upcoming" and "comparisons" not in up


def test_countries_and_mentions():
    rises = {d: 30.0 for d in _span("2026-11-07", "2026-11-13")}
    daily = _daily("2026-09-01", 150, rises=rises)
    rows = {("2026-11-10", "ITA"): [{"id": 1, "title": "Celebrating National Day with friends", "outlet_id": "o1"},
                                    {"id": 2, "title": "Trade figures rise", "outlet_id": "o2"}],
            ("2026-11-11", "ITA"): [{"id": 3, "title": "国庆 观察", "outlet_id": "o1"}],
            ("2026-10-01", "ITA"): [{"id": 4, "title": "Nationaldayish is not a word", "outlet_id": "o1"}]}
    res = ct.analyse(daily, "2026-12-31", _cal([_event("x", "2026-11-10")]), {}, rows)
    t = res["windows"][0]["comparisons"]["normal"]
    assert t["mentions"]["items"] == 3 and t["mentions"]["mentioning"] == 2
    assert t["mentions"]["comparison_items"] == 1 and t["mentions"]["comparison_mentioning"] == 0
    assert [c["country"] for c in t["countries"]] == ["ITA"] and t["countries"][0]["ci_low"] > 1.0


def test_rate_test_interval_is_sane():
    counts = {d: 10 for d in _span("2026-09-01", "2026-10-30")}
    pool = _span("2026-09-01", "2026-10-15")
    win = _span("2026-10-20", "2026-10-26")
    t = ct.rate_test(counts, win, pool)
    assert t["ratio"] == 1.0 and t["ci_low"] < 1.0 < t["ci_high"] and t["dispersion"] == 1.0


# ---------------------------------------------------------------------------
# The measure's interface and wording
# ---------------------------------------------------------------------------

def test_measure_is_registered_and_complete():
    spec = next(m for m in measures.load_config() if m["id"] == "calendar")
    assert spec["module"] == "pipeline.calendar_test" and spec["enabled"] is True
    for f in measures.REQUIRED:
        assert callable(getattr(ct, f)), f
    assert ct.MEASURE_ID == "calendar"


def test_score_check():
    key = {"rows": {"a": {"expected": "yes"}, "b": {"expected": "no"}, "c": {"expected": "yes"}}}
    rows = [{"sample_id": "a", "human": "yes"}, {"sample_id": "b", "human": "yes"}, {"sample_id": "c", "human": ""}]
    r = ct.score_check(rows, key)
    assert r["n"] == 3 and r["coded"] == 2 and r["value"] == 0.5 and r["passed"] is None
    rows[2]["human"] = "Y"
    r = ct.score_check(rows, key)
    assert r["complete"] and r["passed"] is False


def test_no_dashes_anywhere():
    block = {"summary": ct.summarise(ct.analyse(_daily("2026-09-01", 60), "2026-11-01", ct.load_calendar(), {}, None))}
    texts = [ct.methodology_text(block), ct.__doc__,
             (ROOT / "pipeline" / "calendar_test.py").read_text(encoding="utf-8"),
             (ROOT / "sources" / "political_calendar.yaml").read_text(encoding="utf-8"),
             (ROOT / "docs" / "m_calendar.js").read_text(encoding="utf-8")]
    for t in texts:
        assert not any(d in t for d in DASHES)
    assert " - " not in ct.methodology_text(block)


def test_site_slot_sits_under_the_unusual_days():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    themes = re.search(r'<section id="themes"[^>]*>.*?</section>', html, re.S).group(0)
    assert themes.index('id="findings-spikes"') < themes.index('id="findings-calendar-wrap"') < themes.index('id="findings-calendar"')
    assert re.search(r'<div id="findings-calendar-wrap"[^>]*\shidden', html)
    assert html.index('src="measures.js') < html.index('src="m_calendar.js') < html.index('src="app.js')
    js = (ROOT / "docs" / "measures.js").read_text(encoding="utf-8")
    assert '{id: "findings-calendar-wrap", measures: ["calendar"]}' in js and 'calendar: "findings-calendar"' in js
