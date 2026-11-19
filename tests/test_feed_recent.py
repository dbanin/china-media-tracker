"""The share of feed items missed over the last complete days, beside the share over every poll on record.

Feeds were read every four to eight hours until 2026-11-12 and hourly from 2026-11-13. The share over every
estimated poll mixes the two: on 2026-11-18 it was 7.7 percent of items against 1.3 to 2.8 percent on each
day since 2026-11-13, and 8 of the 31 countries warned on it were under the warning level over the seven
days before. The export now
carries both (latest.json feed_recent per country, meta.feed_saturation.recent), the warning states the
recent share, and a country that began to lose items lately is warned on the recent share alone.
"""
import datetime as dt
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import config, export, methodology, store
from tests.test_coverage_warnings import _coverage, _latest, _outlet, _poll, _types, _working

TODAY = dt.datetime.now(dt.timezone.utc).date()
ROOT = Path(__file__).resolve().parents[1]


def _day(back):
    return (TODAY - dt.timedelta(days=back)).isoformat()


def test_the_window_is_the_complete_days_before_the_export_day():
    assert export.feed_recent_window("2026-11-18") == ("2026-11-11", "2026-11-17")
    assert config.FEED_RECENT_DAYS == 7


def test_a_country_that_stopped_losing_items_keeps_its_warning_and_says_so(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    o = _outlet("eg_a", "EGY")
    _working(conn, o)
    _coverage(conn, "EGY")
    _poll(conn, o, 40, 10, 100, 100.0, day=_day(12))       # read every few hours: half its items missed
    _poll(conn, o, 200, 2, 196, 4.0, day=_day(2))          # read hourly: 2 percent
    _poll(conn, o, 10, 5, 10, 90.0, day=_day(0))           # the export day is partial and outside the window
    entry = _latest(conn, [o])["countries"]["EGY"]
    fr = entry["feed_recent"]
    assert (fr["days"], fr["first_day"], fr["last_day"]) == (7, _day(7), _day(1))
    assert (fr["polls"], fr["sat"], fr["miss"], fr["mpolls"], fr["mnew"]) == (200, 2, 4, 200, 196)
    assert fr["miss_share"] == 0.02 and fr["saturation_share"] == 0.01
    text = _types(entry)["feed_saturation"]
    assert text.startswith("an estimated 39 percent of this country's feed items were lost between polls: ")
    assert text.endswith("so its counts understate what its outlets published; over the 7 days to %s the estimate is "
                         "2.0 percent of its items (4 missed against 196 found), under the level at which a country "
                         "is warned, so its recent counts are closer to complete" % _day(1))


def test_a_country_still_losing_items_is_told_the_recent_share_without_the_reassurance(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    o = _outlet("eg_a", "EGY")
    _working(conn, o)
    _coverage(conn, "EGY")
    _poll(conn, o, 100, 20, 100, 100.0, day=_day(3))
    text = _types(_latest(conn, [o])["countries"]["EGY"])["feed_saturation"]
    assert text.endswith("; over the 7 days to %s the estimate is 50 percent of its items (100 missed against 100 found)" % _day(1))


def test_a_country_that_began_losing_items_lately_is_warned_on_the_recent_share(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    o = _outlet("eg_a", "EGY")
    _working(conn, o)
    _coverage(conn, "EGY")
    _poll(conn, o, 2000, 0, 2000, 20.0, day=_day(20))       # 1 percent over three weeks of polls
    _poll(conn, o, 100, 5, 170, 30.0, day=_day(1))          # 15 percent yesterday
    entry = _latest(conn, [o])["countries"]["EGY"]
    assert entry["all_time"]["miss_share"] < config.FEED_MISSED_WARNING_SHARE
    assert _types(entry)["feed_saturation"] == (
        "an estimated 15 percent of this country's feed items were lost between polls over the 7 days to %s "
        "(30 missed against 170 found), so its recent counts understate what its outlets published" % _day(1))


def test_no_warning_and_no_recent_clause_without_enough_recent_polls(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    egy, fra = _outlet("eg_a", "EGY"), _outlet("fr_a", "FRA")
    _working(conn, egy, fra)
    _coverage(conn, "EGY"); _coverage(conn, "FRA")
    _poll(conn, egy, 100, 5, 100, 100.0, day=_day(15))      # nothing in the window: the old wording, unchanged
    _poll(conn, fra, 5, 0, 5, 5.0, day=_day(1))             # five polls are too few to warn on
    latest = _latest(conn, [egy, fra])
    assert _types(latest["countries"]["EGY"])["feed_saturation"].endswith("so its counts understate what its outlets published")
    assert latest["countries"]["EGY"]["feed_recent"]["miss_share"] is None
    assert "feed_saturation" not in _types(latest["countries"]["FRA"])


def test_polls_before_the_current_estimator_are_in_neither_side_of_the_recent_share(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    o = _outlet("eg_a", "EGY")
    _working(conn, o)
    _coverage(conn, "EGY")
    _poll(conn, o, 50, 0, 50, 50.0, estimator="v1", day=_day(2))
    _poll(conn, o, 50, 0, 95, 5.0, day=_day(1))
    fr = _latest(conn, [o])["countries"]["EGY"]["feed_recent"]
    assert (fr["polls"], fr["mpolls"], fr["miss"], fr["mnew"], fr["miss_share"]) == (100, 50, 5, 95, 0.05)


def test_meta_carries_the_recent_window_and_the_methodology_states_it(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    egy, fra = _outlet("eg_a", "EGY"), _outlet("fr_a", "FRA")
    _working(conn, egy, fra)
    _coverage(conn, "EGY"); _coverage(conn, "FRA")
    _poll(conn, egy, 40, 10, 100, 100.0, day=_day(12))
    _poll(conn, egy, 200, 2, 196, 4.0, day=_day(2))
    _poll(conn, fra, 100, 0, 294, 6.0, day=_day(1))
    latest = _latest(conn, [egy, fra])
    fs = export.build_meta(conn, [egy, fra], [], latest)["feed_saturation"]
    assert (fs["polls"], fs["missed_estimate"], fs["estimated_new_items"]) == (340, 110, 590)
    assert fs["recent"] == {"days": 7, "first_day": _day(7), "last_day": _day(1), "polls": 300, "sat": 2, "miss": 10,
                            "mpolls": 300, "mnew": 490, "saturation_share": 0.0067, "miss_share": 0.02}
    # The countries' recent blocks add up to the figure in meta.
    assert sum(c["feed_recent"]["miss"] for c in latest["countries"].values()) == fs["recent"]["miss"]
    assert sum(c["feed_recent"]["mnew"] for c in latest["countries"].values()) == fs["recent"]["mnew"]


def test_the_methodology_page_states_the_recent_share():
    src = (ROOT / "pipeline" / "methodology.py").read_text(encoding="utf-8")
    assert 'fr = fs.get("recent") or {}' in src and "an estimated %.1f percent of items missed (%d against %d found)" in src


def test_the_site_states_the_recent_share_and_both_warning_levels(tmp_path):
    app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    assert "A country carries a warning when an estimated " in app and "percent of its polls came back full." in app
    assert "Countries where this passes" not in app
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    stub = ('var document = {readyState: "loading", addEventListener: function () {}, getElementById: function () { return null; },'
            ' querySelector: function () { return null; }, querySelectorAll: function () { return []; }};')
    files = [ROOT / "docs" / "vendor" / "d3.v7.min.js", ROOT / "docs" / "compute.js", ROOT / "docs" / "app.js", ROOT / "tests" / "smoke_feed_recent.js"]
    shim = tmp_path / "shim.js"
    shim.write_text("const fs = require('fs'), vm = require('vm');\n"
                    "const ctx = {console: console, Date: Date}; ctx.self = ctx; ctx.window = ctx; vm.createContext(ctx);\n"
                    "vm.runInContext(%r, ctx);\n" % stub
                    + "".join("vm.runInContext(fs.readFileSync(%r, 'utf8'), ctx);\n" % str(f) for f in files), encoding="utf-8")
    r = subprocess.run([node, str(shim)], capture_output=True, text=True)
    assert r.returncode == 0 and "feed recent smoke ok" in r.stdout, r.stdout + r.stderr
