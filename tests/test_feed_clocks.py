"""Feed clocks (fetch_feeds.clock_profile, clock_fix, generation_stamps, repair_clocks).

The four feeds read live on 2026-11-09 are the fixtures (tests/fixtures/feed_clocks, item titles and links
replaced, dates and their formats as served, with the poll time beside each): Harian Metro writes Malaysian
local time marked +0000, CTV News the UTC clock marked -0400, Sahafah 24 the feed's generation time in
Yemen local time marked GMT on every item, and Kukmin Ilbo its dates in whitespace and CDATA, about a third
of them a day ahead. The history that teaches each feed its clock is built here from polls of known
publication times."""
import datetime as dt
import random
from pathlib import Path

import feedparser
import pytest

from pipeline import fetch_feeds as ff, lookalike, registry, section_pages, store

UTC = dt.timezone.utc
FIX = Path(__file__).parent / "fixtures" / "feed_clocks"


def _fixture(name):
    parsed = feedparser.parse((FIX / ("%s.xml" % name)).read_bytes())
    polled = dt.datetime.fromisoformat((FIX / ("%s.polled" % name)).read_text().strip())
    return parsed.entries, polled


def _iso(t):
    return t.replace(microsecond=0).isoformat()


def history(end, offset_h=0.0, days=4, gap_h=3.0, per_poll=6, seed=1, late=0.0, round_stamps=False):
    """Rows (discovered_at, feed stamp) of polls every gap_h hours up to end: each poll's new items were
    published between the previous poll and the poll, and the feed stamps them offset_h hours ahead."""
    rnd = random.Random(seed)
    rows, t = [], end - dt.timedelta(days=days)
    while t <= end:
        for i in range(per_poll):
            true = t - dt.timedelta(hours=rnd.uniform(0.05, gap_h + late))
            stamp = true + dt.timedelta(hours=offset_h)
            if round_stamps:
                stamp = stamp.replace(hour=0, minute=0, second=0)
            rows.append((_iso(t + dt.timedelta(seconds=i)), _iso(stamp)))
        t += dt.timedelta(hours=gap_h)
    return rows


def profile(rows):
    return ff.clock_profile(ff.clock_polls(rows))


# --- the four verified feeds --------------------------------------------------------------------------------

def test_local_time_marked_utc_is_learned_and_taken_off():
    entries, polled = _fixture("hmetro")
    stamps = [ff._entry_time(e) for e in entries]
    assert all(dt.datetime.fromisoformat(s) > polled for s in stamps[:5])     # as served: hours in the future
    p = profile(history(polled, offset_h=8.0))
    assert p["offset_hours"] == 8.0 and ff.clock_label(p) == "-8.0h"
    last_poll = polled - dt.timedelta(hours=3)
    fixed = [ff.clock_fix(s, p, set(), last_poll) for s in stamps]
    for (published, raw), stamp in zip(fixed, stamps):
        assert raw == stamp                                        # the feed's stamp is kept beside the fix
        t = dt.datetime.fromisoformat(published)
        assert t == dt.datetime.fromisoformat(stamp) - dt.timedelta(hours=8)
        assert last_poll - dt.timedelta(hours=ff.CLOCK_SLACK_HOURS) <= t <= polled
        assert lookalike.published_ts({"published_at": published, "discovered_at": _iso(polled)}) is not None


def test_utc_clock_marked_minus_four_and_the_dst_change():
    """CTV News: the UTC clock marked -0400, so four hours ahead before and after Canada's change on
    2026-11-01; a window across the change learns four hours throughout."""
    entries, polled = _fixture("ctv")
    stamps = [ff._entry_time(e) for e in entries]
    ahead = [s for s in stamps if dt.datetime.fromisoformat(s) > polled]
    assert len(ahead) == 5          # the sixth was published hours before and listed again
    rows = history(dt.datetime(2026, 11, 3, 12, tzinfo=UTC), offset_h=4.0, days=6)   # spans 2026-11-01
    assert profile(rows)["offset_hours"] == 4.0
    p = profile(history(polled, offset_h=4.0))
    published, raw = ff.clock_fix(stamps[0], p, set(), polled - dt.timedelta(hours=2))
    assert raw == stamps[0] and dt.datetime.fromisoformat(published) <= polled
    # The item listed again from hours before stays as the feed gave it: taking four hours off would put
    # it before the previous poll.
    old = ff.clock_fix(stamps[1], p, set(), polled - dt.timedelta(hours=2))
    assert old == (stamps[1], stamps[1])


def test_a_feed_that_changes_its_clock_is_left_alone_until_the_window_agrees():
    """Prensa Latina: right until 2026-10-31, an hour ahead from 2026-11-01 (Cuba left daylight time).
    Two days after the change the window still mostly holds the old clock: no correction, nothing wrong
    corrected. Once the window holds the new clock, an hour."""
    change = dt.datetime(2026, 11, 1, tzinfo=UTC)
    before = history(change, offset_h=0.0, days=5, seed=2)
    after2 = history(change + dt.timedelta(days=2), offset_h=1.0, days=2, seed=3)
    mixed = profile(before + after2)
    assert mixed["offset_hours"] is None
    settled = profile(history(change + dt.timedelta(days=7), offset_h=1.0, days=6, seed=4))
    assert settled["offset_hours"] == 1.0
    # A feed that just fixed its clock: the old offset still fits the window, but not the last day's polls.
    end = change + dt.timedelta(days=12)
    fixed_now = history(end - dt.timedelta(days=1), offset_h=3.0, days=11, seed=5) + history(end, offset_h=0.0, days=1,
                                                                                                seed=6)
    p = profile(fixed_now)
    assert p["fit"] >= ff.CLOCK_MIN_FIT and p["recent_fit"] < ff.CLOCK_MIN_FIT and p["offset_hours"] is None


def test_generation_stamp_is_no_publication_time():
    """Sahafah 24 stamps every item with the feed's generation time, Yemen local time marked GMT."""
    entries, polled = _fixture("sa24")
    stamps = [ff._entry_time(e) for e in entries]
    assert len(set(stamps)) == 1
    rel = (dt.datetime.fromisoformat(stamps[0]) - polled).total_seconds() / 3600
    assert 2.9 < rel < 3.1
    # Without history the one stamp three hours ahead could be anything: left as given (and not credible).
    assert ff.generation_stamps(stamps, None, polled) == set()
    # The history: every poll's items share one stamp 2.8 to 3.0 hours after the poll.
    rows, t = [], polled - dt.timedelta(days=3)
    while t < polled:
        stamp = _iso(t + dt.timedelta(hours=3, seconds=-random.Random(t.hour).randint(10, 600)))
        rows += [(_iso(t + dt.timedelta(seconds=i)), stamp) for i in range(5)]
        t += dt.timedelta(hours=4)
    p = profile(rows)
    assert p["generation"] and p["offset_hours"] is None and ff.clock_label(p) == "generation"
    gen = ff.generation_stamps(stamps, p, polled)
    assert gen == {stamps[0]}
    assert ff.clock_fix(stamps[0], p, gen, polled - dt.timedelta(hours=4)) == (None, stamps[0])


def test_kmib_dates_in_whitespace_and_cdata_are_read_and_its_day_ahead_items_are_not_a_clock():
    """Kukmin Ilbo's pubDate is "<![CDATA[ 09 Nov 2026 15:06:13 GMT ]]>" between newlines: feedparser reads
    it. About a third of its items carry the next day's date (the edition date), a day ahead, which no
    clock offset explains: nothing is corrected and those items stay not credible."""
    entries, polled = _fixture("kmib")
    stamps = [ff._entry_time(e) for e in entries]
    assert all(stamps) and stamps[0] == "2026-11-09T15:06:13+00:00"
    assert any(dt.datetime.fromisoformat(s) - polled > dt.timedelta(hours=12) for s in stamps)
    right = history(polled, offset_h=0.0, per_poll=4, seed=7)
    a_day = history(polled, offset_h=24.0, per_poll=2, seed=8)
    p = profile(sorted(right + a_day))
    assert p["offset_hours"] is None and not p["generation"]


# --- feeds that must not be touched -------------------------------------------------------------------------

def test_a_feed_with_a_correct_clock_is_left_alone():
    end = dt.datetime(2026, 11, 9, 0, tzinfo=UTC)
    for gap in (1.0, 3.0, 8.0):
        p = profile(history(end, offset_h=0.0, gap_h=gap, seed=int(gap)))
        assert p["offset_hours"] is None and not p["generation"] and ff.clock_label(p) is None
    # Items listed late (published before the previous poll) say nothing about the clock.
    assert profile(history(end, offset_h=0.0, late=4.0, seed=9))["offset_hours"] is None
    assert ff.clock_fix("2026-11-08T23:00:00+00:00", None, set(), None) == ("2026-11-08T23:00:00+00:00", None)


def test_too_few_items_or_polls_teach_nothing():
    end = dt.datetime(2026, 11, 9, 0, tzinfo=UTC)
    sparse = history(end, offset_h=8.0, days=1, gap_h=6.0, per_poll=3)        # 5 polls, 12 items with a prev
    p = profile(sparse)
    assert p["items"] < ff.CLOCK_MIN_ITEMS and p["offset_hours"] is None
    few_polls = history(end, offset_h=8.0, days=0.5, gap_h=4.0, per_poll=20)
    assert profile(few_polls)["offset_hours"] is None


def test_dates_without_a_time_are_no_clock():
    """Taipei Times writes the Taipei date at midnight UTC: up to eight hours ahead, and no clock."""
    end = dt.datetime(2026, 11, 9, 0, tzinfo=UTC)
    p = profile(history(end, offset_h=8.0, round_stamps=True))
    assert p["offset_hours"] is None and p["items"] == 0
    # A date-only stamp is never a generation stamp either.
    assert ff.generation_stamps(["2026-11-09T00:00:00+00:00"] * 5, None, end) == set()


def test_single_poll_generation_stamp_and_its_guards():
    now = dt.datetime(2026, 11, 9, 7, 40, tzinfo=UTC)
    stamp = _iso(now - dt.timedelta(minutes=4, seconds=17))
    assert ff.generation_stamps([stamp] * 4 + [None], None, now) == {stamp}
    # Written to the minute: several releases in one minute share it (PR Newswire); not a generation stamp.
    minute = _iso((now - dt.timedelta(minutes=4)).replace(second=0))
    assert ff.generation_stamps([minute] * 4, None, now) == set()
    # Two entries, one stamp hours old, or entries with their own times: left alone.
    assert ff.generation_stamps([stamp] * 2, None, now) == set()
    old = _iso(now - dt.timedelta(hours=3, seconds=17))
    assert ff.generation_stamps([old] * 5, None, now) == set()
    assert ff.generation_stamps([stamp, stamp, stamp, old], None, now) == set()


def test_a_correction_never_lands_before_the_previous_poll():
    p = {"offset_hours": 8.0, "generation": None}
    last = dt.datetime(2026, 11, 9, 6, tzinfo=UTC)
    early = _iso(last + dt.timedelta(hours=7))          # 7 hours ahead of the previous poll: 1 hour before it
    assert ff.clock_fix(early, p, set(), last) == (early, early)
    fine = _iso(last + dt.timedelta(hours=9))
    assert ff.clock_fix(fine, p, set(), last) == (_iso(last + dt.timedelta(hours=1)), fine)
    assert ff.clock_fix(None, p, set(), last) == (None, None)


# --- discovery and repair ------------------------------------------------------------------------------------

OUTLET = {"id": "my_z", "country": "MYS", "language": "en", "feeds": []}
FEED = "https://z.test/feed"


def _seed(conn, rows, feed=FEED, day_of_record=None):
    for i, (disc, stamp) in enumerate(rows):
        url = "%s/old/%s/%d" % (feed, disc, i)
        conn.execute(
            """INSERT INTO articles(url, url_hash, outlet_id, country, language, feed_url, title, published_at,
                                    discovered_at, status, gate_relevant, day_of_record)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (url, store.url_hash(url), OUTLET["id"], OUTLET["country"], "en", feed, "Old item %d" % i, stamp, disc,
             "gated_out", 0, day_of_record))
    conn.commit()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(section_pages, "aap_start", lambda conn, outlets: None)
    c = store.connect(tmp_path / "c.db")
    yield c
    c.close()


def test_published_raw_column_is_migrated(conn):
    assert "published_raw" in store._columns(conn, "articles")


def test_discovery_corrects_new_items_and_reports_the_feed_clock(conn, monkeypatch):
    now = dt.datetime.now(UTC)
    _seed(conn, history(now - dt.timedelta(minutes=30), offset_h=8.0, days=3))
    true = now - dt.timedelta(minutes=50)
    entries = [{"link": "https://z.test/new/%d" % i, "title": "China and Malaysia sign a trade deal %d" % i,
                "summary": "Beijing said", "published_parsed": (true + dt.timedelta(hours=8, minutes=i)).utctimetuple()}
               for i in range(3)]
    monkeypatch.setattr(ff, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": FEED, "kind": registry.EDITORIAL, "polled_at": now,
         "result": {"ok": True, "entries": entries, "error": None}}])
    counts = ff.run(conn, "t", outlets=[OUTLET], workers=1)
    assert counts["feed_clocks"] == {FEED: "-8.0h"}
    assert counts["clock_offset"] == 3
    rows = conn.execute("SELECT published_at, published_raw, discovered_at FROM articles WHERE url LIKE 'https://z.test/new/%'").fetchall()
    for r in rows:
        assert dt.datetime.fromisoformat(r["published_raw"]) - dt.datetime.fromisoformat(r["published_at"]) == dt.timedelta(hours=8)
        assert lookalike.published_ts(r) is not None


def test_discovery_clears_a_generation_stamp_without_history(conn, monkeypatch):
    now = dt.datetime.now(UTC)
    stamp = (now - dt.timedelta(minutes=3)).replace(microsecond=0)
    stamp = stamp.replace(second=stamp.second or 7)
    entries = [{"link": "https://z.test/g/%d" % i, "title": "China opens a consulate %d" % i, "summary": "Beijing",
                "published_parsed": stamp.utctimetuple()} for i in range(4)]
    monkeypatch.setattr(ff, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": FEED, "kind": registry.EDITORIAL, "polled_at": now,
         "result": {"ok": True, "entries": entries, "error": None}}])
    counts = ff.run(conn, "t", outlets=[OUTLET], workers=1)
    assert counts["clock_generation"] == 4
    rows = conn.execute("SELECT published_at, published_raw FROM articles").fetchall()
    assert all(r["published_at"] is None and r["published_raw"] == stamp.isoformat() for r in rows)


def test_repair_corrects_stored_rows_once_and_leaves_backfill_rows(conn):
    end = dt.datetime.now(UTC) - dt.timedelta(hours=1)
    _seed(conn, history(end, offset_h=8.0, days=5))
    _seed(conn, history(end, offset_h=0.0, days=5, seed=3), feed="https://ok.test/feed")
    backfill = [(_iso(end), _iso(end - dt.timedelta(days=2)))]
    _seed(conn, backfill, feed=FEED, day_of_record=(end - dt.timedelta(days=2)).date().isoformat())
    stored = conn.execute("SELECT COUNT(*) FROM articles WHERE feed_url=? AND day_of_record IS NULL", (FEED,)).fetchone()[0]
    first = ff.repair_clocks(conn, days=None)
    assert first["generation"] == 0 and first["offset"] + first["kept"] > 0
    assert list(first["feeds"]) == [FEED]                       # the correct feed is not touched
    # The first days of the window teach nothing yet; from then on every item is judged.
    judged = conn.execute("SELECT COUNT(*) FROM articles WHERE published_raw IS NOT NULL").fetchone()[0]
    assert judged == first["offset"] + first["kept"] and judged >= stored // 2
    for r in conn.execute("SELECT published_at, published_raw, discovered_at FROM articles WHERE published_raw IS NOT NULL"
                          " AND published_at != published_raw"):
        assert r["published_at"] <= r["discovered_at"]
    assert conn.execute("SELECT published_raw FROM articles WHERE day_of_record IS NOT NULL").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE feed_url='https://ok.test/feed' AND published_raw IS NOT NULL"
                        ).fetchone()[0] == 0
    again = ff.repair_clocks(conn, days=None)
    assert (again["offset"], again["generation"], again["kept"]) == (0, 0, 0)


def test_repair_clears_generation_stamps(conn):
    end = dt.datetime.now(UTC) - dt.timedelta(hours=1)
    rows, t = [], end - dt.timedelta(days=3)
    while t < end:
        stamp = _iso(t + dt.timedelta(hours=3, seconds=-41))
        rows += [(_iso(t + dt.timedelta(seconds=i)), stamp) for i in range(4)]
        t += dt.timedelta(hours=4)
    _seed(conn, rows)
    out = ff.repair_clocks(conn, days=None)
    assert out["generation"] > 0 and out["offset"] == 0
    left = conn.execute("SELECT COUNT(*) FROM articles WHERE published_at IS NOT NULL").fetchone()[0]
    assert left == len(rows) - out["generation"]
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE published_at IS NULL AND published_raw IS NULL").fetchone()[0] == 0
