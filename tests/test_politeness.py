"""The crawler's promises, and the deadline that has to hold.

The published methodology says the crawler honours robots.txt and makes at most one request per
domain every three seconds. That covers feed polling as much as article fetching, and the rate
limiter has to queue callers linearly rather than punishing the third one for the second one's wait.
"""
import time

from pipeline import config, feeds_util, fetch_articles as fa, fetch_feeds, registry, store


class _Clock:
    """A clock that does not move while callers sleep: six threads arriving at once on one host,
    each sleeping in parallel."""

    def __init__(self, now=1_000_000.0):
        self.now = now
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))


def test_concurrent_callers_on_one_host_queue_linearly(monkeypatch):
    """The reservation used to be `max(now, last) + wait` where wait already held the backlog, so
    every caller counted it again: 3, 6, 12, 24, 48, 96 seconds instead of 3, 6, 9, 12, 15, 18."""
    clock = _Clock()
    monkeypatch.setattr(fa, "time", clock)
    monkeypatch.setattr(fa.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(config, "MIN_SECONDS_PER_DOMAIN", 3.0)
    monkeypatch.setattr(fa, "_last_request", {"feeds.bbci.co.uk": clock.now})
    for _ in range(6):
        fa._rate_wait("feeds.bbci.co.uk")
    assert clock.slept == [3.0, 6.0, 9.0, 12.0, 15.0, 18.0]
    # the last slot handed out is the sixth one, not the ninety sixth second
    assert fa._last_request["feeds.bbci.co.uk"] == clock.now + 18.0


def test_a_quiet_host_is_not_made_to_wait(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(fa, "time", clock)
    monkeypatch.setattr(fa.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(fa, "_last_request", {})
    fa._rate_wait("first.example")
    assert clock.slept == []


# ---------------------------------------------------------------------------
# Feed polling obeys the same rules as article fetching
# ---------------------------------------------------------------------------

def test_feed_polling_honours_robots(monkeypatch):
    monkeypatch.setattr(fa, "can_fetch", lambda url: (False, "robots_disallowed"))
    monkeypatch.setattr(feeds_util.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("requested anyway")))
    r = feeds_util.fetch_feed("https://blocked.example/rss")
    assert r["ok"] is False and r["error"] == "robots_disallowed" and r["entries"] == []


def test_feed_polling_takes_a_rate_limited_slot(monkeypatch):
    waited = []
    monkeypatch.setattr(fa, "can_fetch", lambda url: (True, "robots_allowed"))
    monkeypatch.setattr(fa, "_rate_wait", lambda domain: waited.append(domain))

    class _Resp:
        status_code = 200
        content = b"<rss><channel><item><title>t</title><link>https://x/1</link></item></channel></rss>"

    monkeypatch.setattr(feeds_util.requests, "get", lambda *a, **k: _Resp())
    r = feeds_util.fetch_feed("https://feeds.bbci.co.uk/news/world/rss.xml")
    assert waited == ["feeds.bbci.co.uk"], "one shared host, one queue"
    assert r["ok"] and len(r["entries"]) == 1


# ---------------------------------------------------------------------------
# The discovery deadline
# ---------------------------------------------------------------------------

def _outlet(i):
    return {"id": "o%d" % i, "name": "O%d" % i, "country": "ITA", "language": "it",
            "feeds": ["https://x.test/%d/feed" % i], "tier": "national", "active": True}


def test_the_deadline_stops_the_polling_not_only_the_writing(monkeypatch, tmp_path):
    """ThreadPoolExecutor.map submitted every outlet up front and the with block joined on exit, so
    a run given 35 minutes could poll for the job's full 55 minute timeout and be killed."""
    polled = []

    def slow(outlet):
        polled.append(outlet["id"])
        time.sleep(0.05)
        return []

    monkeypatch.setattr(fetch_feeds, "poll_outlet", slow)
    conn = store.connect(tmp_path / "d.db")
    counts = fetch_feeds.run(conn, "t", outlets=[_outlet(i) for i in range(20)], workers=1,
                             deadline=time.time() - 1)
    assert counts["stopped_at_deadline"] is True
    assert counts["outlets_polled"] == 1 and counts["outlets_skipped"] == 19
    assert len(polled) < 5, "the outlets not started yet were cancelled, not polled: %s" % polled


def test_without_a_deadline_every_outlet_is_polled(monkeypatch, tmp_path):
    polled = []
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: polled.append(o["id"]) or [])
    conn = store.connect(tmp_path / "d2.db")
    counts = fetch_feeds.run(conn, "t", outlets=[_outlet(i) for i in range(8)], workers=4)
    assert sorted(polled) == ["o%d" % i for i in range(8)]
    assert counts["outlets_polled"] == 8 and counts["outlets_skipped"] == 0
    assert "stopped_at_deadline" not in counts


# ---------------------------------------------------------------------------
# A gated-out item that turns up again on a section feed
# ---------------------------------------------------------------------------

URL = "https://it.test/economia/acme-nuovo-stabilimento"


def _group(kind, feed_url):
    outlet = {"id": "it_test", "name": "IT", "country": "ITA", "language": "it",
              "feeds": [feed_url], "tier": "national", "active": True}
    entry = {"link": URL, "title": "Acme apre un nuovo stabilimento", "summary": "Acme investe in Lombardia."}
    return [{"outlet": outlet, "feed_url": feed_url, "kind": kind,
             "result": {"ok": True, "entries": [entry], "error": None}}]


def test_a_gated_out_item_is_readmitted_on_a_gate_exempt_section_feed(monkeypatch, tmp_path):
    """The editorial feed sees it first and the gate rejects it; the press release feed, which is
    where paid state placements sit, sees the same URL later. INSERT OR IGNORE plus a continue left
    it gated_out until it was pruned three days later."""
    conn = store.connect(tmp_path / "f.db")
    outlet = _group(registry.EDITORIAL, "https://it.test/feed")[0]["outlet"]

    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: _group(registry.EDITORIAL, "https://it.test/feed"))
    first = fetch_feeds.run(conn, "t1", outlets=[outlet])
    row = conn.execute("SELECT * FROM articles").fetchone()
    assert first["items_new"] == 1 and row["status"] == "gated_out" and row["gate_relevant"] == 0
    day = conn.execute("SELECT discovered, gate_relevant FROM daily_discovery").fetchone()
    assert tuple(day) == (1, 0)

    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: _group("press_release", "https://it.test/comunicati/feed"))
    second = fetch_feeds.run(conn, "t2", outlets=[outlet])
    assert second["items_new"] == 0, "not a new item"
    assert second["readmitted_section"] == 1 and second["section_items"] == 1
    row = conn.execute("SELECT * FROM articles").fetchone()
    assert row["status"] == "queued" and row["gate_relevant"] == 1
    assert "section:press_release" in row["gate_terms"]
    assert row["feed_url"] == "https://it.test/comunicati/feed"
    # discovery is not counted twice; only the gate-relevant side of that day is corrected
    day = conn.execute("SELECT discovered, gate_relevant FROM daily_discovery").fetchone()
    assert tuple(day) == (1, 1)
    outlet_day = conn.execute("SELECT discovered, gate_relevant FROM daily_outlet_discovery").fetchone()
    assert tuple(outlet_day) == (1, 1)
    # and polling it a third time changes nothing further
    third = fetch_feeds.run(conn, "t3", outlets=[outlet])
    assert third["readmitted_section"] == 0
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1


def test_an_editorial_repeat_does_not_readmit(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "g.db")
    outlet = _group(registry.EDITORIAL, "https://it.test/feed")[0]["outlet"]
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: _group(registry.EDITORIAL, "https://it.test/feed"))
    fetch_feeds.run(conn, "t1", outlets=[outlet])
    second = fetch_feeds.run(conn, "t2", outlets=[outlet])
    assert second["readmitted_section"] == 0
    assert conn.execute("SELECT status FROM articles").fetchone()[0] == "gated_out"
