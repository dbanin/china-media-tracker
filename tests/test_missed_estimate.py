"""The estimate of items lost between polls (fetch_feeds.saturation, feed_history), on synthetic polls.

Up to 2026-10-12 the estimate was the window's own publishing rate times the gap since the previous
successful poll, capped at 500. A publisher that replaces a short feed wholesale once a day (Newsmada:
five items stamped within minutes, every morning) made every batch look saturated with a rate of
hundreds an hour, and was booked 7,953 missed items against 118 delivered. The rate now runs over a
minimum span, the estimate is capped by what the feed delivers per day, and a batch poll of a batch
publisher misses nothing unless a whole batch interval passed unseen.
"""
import datetime as dt
import time

from pipeline import fetch_feeds as ff, store

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
FEED = "https://batch.test/feed/"


def _stamps(start, minutes):
    return [(start + dt.timedelta(minutes=m)).isoformat() for m in minutes]


def _ledger(conn, feed, polls, entries, new, days=14, now=NOW, outlet="o_test"):
    for d in range(1, days + 1):
        day = (now - dt.timedelta(days=d)).date().isoformat()
        conn.execute("INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, entries, new_items) VALUES (?,?,?,?,?,?,?)",
                     (day, feed, outlet, "MDG", polls, entries, new))
    conn.commit()


def _old(prev_ok, times):
    """The estimator as it stood before 2026-10-12, for comparison."""
    s = sorted(dt.datetime.fromisoformat(t) for t in times)
    span, gap = (s[-1] - s[0]).total_seconds(), (s[0] - prev_ok).total_seconds()
    return min(ff.MAX_MISSED_ESTIMATE, (len(s) - 1) / span * gap)


def test_a_morning_batch_of_a_batch_publisher_misses_nothing(tmp_path):
    conn = store.connect(tmp_path / "a.db")
    _ledger(conn, FEED, polls=6, entries=30, new=5)        # five items a day, polled six times a day
    h = ff.feed_history(conn, FEED, now=NOW)
    assert h["batch"] and h["per_day"] == 5 and h["window"] == 5
    times = _stamps(NOW - dt.timedelta(hours=1), [0, 1, 2, 3, 4])
    prev_ok = NOW - dt.timedelta(hours=4)
    assert _old(prev_ok, times) == 180.0                       # one item a minute for three hours
    assert ff.saturation({"last_ok": prev_ok.isoformat()}, 5, 5, times, now=NOW, history=h) == (True, 0.0)


def test_a_batch_publisher_left_unpolled_past_a_batch_interval_misses_whole_batches(tmp_path):
    conn = store.connect(tmp_path / "b.db")
    _ledger(conn, FEED, polls=6, entries=30, new=5)
    h = ff.feed_history(conn, FEED, now=NOW)
    times = _stamps(NOW - dt.timedelta(hours=1), [0, 1, 2, 3, 4])
    # 51 hours since the previous poll with one batch a day: two batches went unseen.
    prev_ok = NOW - dt.timedelta(hours=52)
    assert ff.saturation({"last_ok": prev_ok.isoformat()}, 5, 5, times, now=NOW, history=h) == (True, 10.0)


def test_a_busy_feed_is_never_a_batch_publisher(tmp_path):
    conn = store.connect(tmp_path / "c.db")
    # Twenty items a poll, a hundred new a day: five windows a day, a continuous publisher whose
    # minute-rounded stamps can still cluster (PR Newswire stamps several releases 08:00:00).
    _ledger(conn, FEED, polls=6, entries=120, new=100)
    h = ff.feed_history(conn, FEED, now=NOW)
    assert not h["batch"]
    times = _stamps(NOW - dt.timedelta(minutes=50), [0] * 6 + list(range(1, 15)))
    sat, missed = ff.saturation({"last_ok": (NOW - dt.timedelta(hours=3)).isoformat()}, 20, 20, times, now=NOW, history=h)
    assert sat and missed > 0


def test_a_feed_polled_rarely_is_not_called_a_batch_publisher(tmp_path):
    conn = store.connect(tmp_path / "d.db")
    _ledger(conn, FEED, polls=1, entries=20, new=20)        # one poll a day cannot tell a burst from a day
    assert not ff.feed_history(conn, FEED, now=NOW)["batch"]


def test_the_rate_runs_over_at_least_two_hours_without_history():
    times = _stamps(NOW - dt.timedelta(hours=1), [0, 3, 6])
    prev_ok = NOW - dt.timedelta(hours=4)
    assert round(_old(prev_ok, times)) == 60                 # 2 items in 6 minutes, 3 hours
    # 2 items over two hours, times three hours.
    assert ff.saturation({"last_ok": prev_ok.isoformat()}, 3, 3, times, now=NOW) == (True, 3.0)


def test_the_estimate_is_capped_by_what_the_feed_delivers_per_day(tmp_path):
    conn = store.connect(tmp_path / "e.db")
    _ledger(conn, FEED, polls=2, entries=20, new=10)       # ten new items a day
    h = ff.feed_history(conn, FEED, now=NOW)
    times = _stamps(NOW - dt.timedelta(hours=3), [0, 20, 40, 60, 80, 100, 120, 140, 160, 180])
    prev_ok = NOW - dt.timedelta(hours=23)
    # 9 items in 3 hours times 20 hours is 60; twice ten a day is 20.
    assert ff.saturation({"last_ok": prev_ok.isoformat()}, 10, 10, times, now=NOW) == (True, 60.0)
    assert ff.saturation({"last_ok": prev_ok.isoformat()}, 10, 10, times, now=NOW, history=h) == (True, 20.0)
    # A gap of several days scales the cap: three days of twice ten a day.
    prev_ok = NOW - dt.timedelta(hours=75)
    assert ff.saturation({"last_ok": prev_ok.isoformat()}, 10, 10, times, now=NOW, history=h) == (True, 60.0)


def _poll_rows(conn, feed, polls, size, span_min, outlet="o_test", now=NOW):
    """`polls` full windows of `size` new items each, discovered an hour apart, published over span_min."""
    for p in range(polls):
        disc = now - dt.timedelta(hours=p + 1)
        for i in range(size):
            aid = store.insert_discovered(conn, {"url": "%sw%d-%d" % (feed, p, i), "outlet_id": outlet, "country": "MDG",
                                                 "language": "fr", "feed_url": feed, "title": "Item %d %d" % (p, i),
                                                 "status": "gated_out", "gate_relevant": 0, "gate_terms": [],
                                                 "published_at": (disc - dt.timedelta(minutes=span_min * i / (size - 1))).isoformat()})
            conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (disc.isoformat(), aid))
    conn.commit()


def test_a_feed_whose_windows_always_span_half_an_hour_keeps_its_rate(tmp_path):
    conn = store.connect(tmp_path / "f.db")
    _ledger(conn, FEED, polls=8, entries=80, new=400)       # a busy feed, never capped here
    _poll_rows(conn, FEED, polls=4, size=10, span_min=30)
    h = ff.feed_history(conn, FEED, now=NOW, outlet_id="o_test")
    assert h["min_span_h"] == 0.5 and not h["batch"]
    assert ff.feed_history(conn, FEED, now=NOW)["min_span_h"] == 0.5   # the same without the outlet narrowing
    times = _stamps(NOW - dt.timedelta(minutes=30), [0, 3, 6, 10, 13, 16, 20, 23, 26, 30])
    prev_ok = NOW - dt.timedelta(hours=2, minutes=30)
    # 9 items in half an hour times two hours: 36, where a two hour floor would give 9.
    assert ff.saturation({"last_ok": prev_ok.isoformat()}, 10, 10, times, now=NOW, history=h) == (True, 36.0)
    # A window of a few minutes from the same feed is held to its usual half hour.
    times = _stamps(NOW - dt.timedelta(minutes=5), [0, 0, 1, 1, 2, 2, 3, 3, 4, 5])
    prev_ok = NOW - dt.timedelta(hours=2, minutes=5)
    assert ff.saturation({"last_ok": prev_ok.isoformat()}, 10, 10, times, now=NOW, history=h) == (True, 36.0)


def test_the_span_floor_needs_three_full_windows_and_never_drops_under_a_quarter_hour(tmp_path):
    conn = store.connect(tmp_path / "g.db")
    _ledger(conn, FEED, polls=8, entries=80, new=400)
    _poll_rows(conn, FEED, polls=2, size=10, span_min=30)
    assert ff.feed_history(conn, FEED, now=NOW)["min_span_h"] == ff.MISSED_MIN_SPAN_HOURS
    other = "https://busy.test/feed/"
    _ledger(conn, other, polls=8, entries=80, new=400)
    _poll_rows(conn, other, polls=3, size=10, span_min=2)
    assert ff.feed_history(conn, other, now=NOW)["min_span_h"] == ff.MISSED_SPAN_MIN_HOURS


def test_history_leaves_today_out_and_needs_a_ledger(tmp_path):
    conn = store.connect(tmp_path / "h.db")
    assert ff.feed_history(conn, FEED, now=NOW) is None
    conn.execute("INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, entries, new_items) VALUES (?,?,?,?,?,?,?)",
                 (NOW.date().isoformat(), FEED, "o_test", "MDG", 3, 15, 15))
    assert ff.feed_history(conn, FEED, now=NOW) is None
    _ledger(conn, FEED, polls=6, entries=30, new=5, days=2)
    h = ff.feed_history(conn, FEED, now=NOW)
    assert h["days"] == 2 and not h["batch"]              # two days are too few to call it a batch publisher


def test_burst():
    t = lambda m: NOW + dt.timedelta(minutes=m)
    assert ff._burst([t(0), t(2), t(5), t(9), t(30)])
    assert not ff._burst([t(0), t(50), t(100), t(150), t(200)])
    assert not ff._burst([t(0), t(1)])                      # two stamps are no batch


OUTLET = {"id": "mg_batch_test", "name": "Batch", "country": "MDG", "language": "fr",
          "feeds": [FEED], "tier": "national_daily", "active": True}


def _entries(start, minutes, tag):
    return [{"link": "%s%s-%d" % (FEED, tag, i), "title": "Vaovao %s %d" % (tag, i), "summary": "",
             "published_parsed": time.gmtime((start + dt.timedelta(minutes=m)).timestamp())} for i, m in enumerate(minutes)]


def test_the_poll_loop_books_no_misses_for_a_batch(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "i.db")
    now = dt.datetime.now(UTC)
    _ledger(conn, FEED, polls=6, entries=30, new=5, now=now, outlet=OUTLET["id"])
    batch = lambda tag, start: [{"outlet": OUTLET, "feed_url": FEED, "kind": "editorial",
                                 "result": {"ok": True, "entries": _entries(start, [0, 1, 2, 3, 4], tag), "error": None, "status": 200}}]
    monkeypatch.setattr(ff, "poll_outlet", lambda o: batch("y", now - dt.timedelta(hours=26)))
    ff.run(conn, "t1", outlets=[OUTLET])
    conn.execute("UPDATE feed_health SET last_ok=? WHERE feed_url=?", ((now - dt.timedelta(hours=4)).isoformat(), FEED))
    monkeypatch.setattr(ff, "poll_outlet", lambda o: batch("t", now - dt.timedelta(minutes=30)))
    counts = ff.run(conn, "t2", outlets=[OUTLET])
    assert counts["feeds_saturated"] == 1
    row = conn.execute("SELECT polls, saturated, missed_estimate FROM feed_polls WHERE feed_url=? AND date=?",
                       (FEED, dt.datetime.now(UTC).date().isoformat())).fetchone()
    assert (row["polls"], row["saturated"], row["missed_estimate"]) == (2, 1, 0.0)
