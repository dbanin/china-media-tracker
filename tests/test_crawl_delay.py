"""Crawl-delay: read from robots.txt like the Allow and Disallow rules, paced in the one rate limiter,
and kept from blocking the rest of a run.

RFC 9309 has no Crawl-delay, but a site that sets one is asking to be fetched more slowly. AsiaOne
asks for 60 seconds, and 147 of the 1,996 domains behind the active outlets set a value (2026-10-12).
"""
import threading
import time

import pytest

from pipeline import config, fetch_articles as fa, fetch_feeds, registry, robots, store

UA = "ChinaStateMediaTracker"


def _rb(text):
    return robots.parse(text.strip().splitlines())


# ---------------------------------------------------------------------------
# Parsing: the same group selection as Allow and Disallow
# ---------------------------------------------------------------------------

def test_asiaone_style_star_group_delay_applies_to_us():
    rb = _rb("User-agent: *\nCrawl-delay: 60\nDisallow: /search/\nDisallow: /*?q=\n")
    assert rb.crawl_delay(UA) == 60.0
    assert not rb.can_fetch(UA, "https://www.asiaone.com/search/china")      # rules unchanged
    assert rb.can_fetch(UA, "https://www.asiaone.com/asia/china-story")


def test_a_group_naming_us_replaces_the_star_delay():
    rb = _rb("User-agent: *\nDisallow: /a\nCrawl-delay: 30\n\n"
             "User-agent: ChinaStateMediaTracker\nAllow: /\nCrawl-delay: 5\n")
    assert rb.crawl_delay(UA) == 5.0
    assert rb.crawl_delay("OtherBot") == 30.0


def test_a_group_naming_us_without_a_delay_does_not_fall_back_to_star():
    rb = _rb("User-agent: ChinaStateMediaTracker\nAllow: /\n\nUser-agent: *\nDisallow: /p\nCrawl-delay: 20\n")
    assert rb.crawl_delay(UA) is None


def test_another_crawlers_delay_is_not_ours():
    rb = _rb("User-agent: Bingbot\nCrawl-delay: 100\nDisallow: /x\n\nUser-agent: *\nDisallow: /y\n")
    assert rb.crawl_delay(UA) is None
    assert rb.crawl_delay("bingbot") == 100.0


def test_a_delay_only_star_record_runs_into_the_next_and_errs_slow():
    """A Crawl-delay line does not end a group, so the Allow/Disallow grouping is what it was; a "*"
    record holding only a delay shares a group with the record after it, and we take the delay."""
    rb = _rb("User-agent: *\nCrawl-delay: 10\nUser-agent: ChinaStateMediaTracker\nDisallow: /private/\n")
    assert rb.crawl_delay(UA) == 10.0
    assert not rb.can_fetch(UA, "https://a.example/private/x")


@pytest.mark.parametrize("value,expected", [("0.5", 0.5), ("10 seconds", 10.0), (" 7 ", 7.0),
                                            ("abc", None), ("", None), ("-5", None)])
def test_delay_values(value, expected):
    assert _rb("User-agent: *\nCrawl-delay: %s\nDisallow: /x\n" % value).crawl_delay(UA) == expected


def test_the_larger_of_two_values_in_one_group_and_none_outside_a_group():
    assert _rb("User-agent: *\nCrawl-delay: 5\nCrawl-delay: 12\nDisallow: /x\n").crawl_delay(UA) == 12.0
    assert _rb("Crawl-delay: 30\nUser-agent: *\nDisallow: /x\n").crawl_delay(UA) is None


def test_no_delay_anywhere():
    assert _rb("User-agent: *\nDisallow: /x\n").crawl_delay(UA) is None
    assert robots.Robots([(["*"], [])]).crawl_delay(UA) is None


# ---------------------------------------------------------------------------
# The gap the rate limiter uses
# ---------------------------------------------------------------------------

@pytest.fixture
def cache(monkeypatch):
    c = {}
    monkeypatch.setattr(fa, "_robots_cache", c)
    monkeypatch.setattr(config, "MIN_SECONDS_PER_DOMAIN", 3.0)
    monkeypatch.setattr(config, "CRAWL_DELAY_CAP_SECONDS", 600.0)
    return c


def test_domain_gap_is_the_larger_of_the_minimum_and_the_delay_capped(cache):
    cache["slow.example"] = ("ok", _rb("User-agent: *\nCrawl-delay: 60\nDisallow: /x"))
    cache["quick.example"] = ("ok", _rb("User-agent: *\nCrawl-delay: 1\nDisallow: /x"))
    cache["absurd.example"] = ("ok", _rb("User-agent: *\nCrawl-delay: 86400\nDisallow: /x"))
    cache["none.example"] = ("allow_all", None)
    assert fa.domain_gap("slow.example") == 60.0
    assert fa.domain_gap("quick.example") == 3.0
    assert fa.domain_gap("absurd.example") == 600.0
    assert fa.domain_gap("none.example") == 3.0
    assert fa.domain_gap("never.fetched.example") == 3.0      # reads the cache only, never fetches


def test_an_absurd_delay_is_capped_and_logged(cache, monkeypatch, capsys):
    class _Resp:
        status_code = 200
        text = "User-agent: *\nCrawl-delay: 1200\nDisallow: /wp-admin/\n"
    monkeypatch.setattr(fa.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(fa, "_rate_wait", lambda d: None)
    state, rp = fa.robots_for("almarsad.example")
    assert state == "ok" and rp.crawl_delay(UA) == 1200.0
    assert fa.domain_gap("almarsad.example") == 600.0
    assert "almarsad.example asks Crawl-delay 1200s, paced at the 600s cap" in capsys.readouterr().err


class _Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))


def test_the_rate_limiter_paces_a_crawl_delayed_domain_at_its_delay(cache, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(fa, "time", clock)
    monkeypatch.setattr(fa.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(fa, "_last_request", {"www.asiaone.com": clock.now, "bbc.example": clock.now})
    cache["www.asiaone.com"] = ("ok", _rb("User-agent: *\nCrawl-delay: 60\nDisallow: /search/"))
    for _ in range(3):
        fa._rate_wait("www.asiaone.com")
    fa._rate_wait("bbc.example")
    assert clock.slept == [60.0, 120.0, 180.0, 3.0]


def test_a_long_wait_gives_the_permit_back_and_a_short_one_keeps_it(cache, monkeypatch):
    permits = threading.BoundedSemaphore(1)
    seen = []

    class _C(_Clock):
        def sleep(self, seconds):
            free = permits.acquire(blocking=False)
            if free:
                permits.release()
            seen.append((seconds, free))

    clock = _C()
    monkeypatch.setattr(fa, "time", clock)
    monkeypatch.setattr(fa.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(fa, "_last_request", {"slow.example": clock.now, "quick.example": clock.now})
    cache["slow.example"] = ("ok", _rb("User-agent: *\nCrawl-delay: 60\nDisallow: /x"))
    fa.paced(permits, lambda: (fa._rate_wait("slow.example"), fa._rate_wait("quick.example")))()
    assert seen == [(60.0, True), (3.0, False)]
    assert permits.acquire(blocking=False), "the permit is back after the call"


# ---------------------------------------------------------------------------
# The fetch stage moves on to other domains
# ---------------------------------------------------------------------------

def test_a_slow_domain_does_not_hold_the_only_worker(cache, monkeypatch, tmp_path):
    """One worker, one domain with a 60 second Crawl-delay first in the queue and six ordinary
    domains behind it, two articles each (the second waits the ordinary three seconds). The clock is fake: the slow domain's sleep returns only once every ordinary
    domain is done, which can happen only if the sleep gave the one permit away. The deadline then
    cuts the slow domain short, leaving its last article queued for the next run."""
    fast_done = threading.Event()
    clock_lock = threading.Lock()

    class _Shared(_Clock):
        def sleep(self, seconds):
            if seconds <= config.MIN_SECONDS_PER_DOMAIN + config.JITTER_SECONDS:
                return                                  # ordinary gaps: no time needs to pass here
            start = self.now
            fast_done.wait(5)                           # a safety net only; never reached when it works
            with clock_lock:
                self.now = max(self.now, start + seconds)

    clock = _Shared()
    monkeypatch.setattr(fa, "time", clock)
    monkeypatch.setattr(fa.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(config, "JITTER_SECONDS", 0.0)
    monkeypatch.setattr(fa, "_last_request", {})
    cache["slow.example"] = ("ok", _rb("User-agent: *\nCrawl-delay: 60\nDisallow: /x"))
    rows = [{"id": i, "url": "https://slow.example/%d" % i, "fetch_attempts": 0, "outlet_id": "o"} for i in range(4)]
    for d in range(6):
        rows += [{"id": 100 + 10 * d + i, "url": "https://fast%d.example/%d" % (d, i), "fetch_attempts": 0,
                  "outlet_id": "o"} for i in range(2)]
    done = []

    def fake_process(conn_factory, row):
        fa._rate_wait(fa.domain_of(row["url"]))
        done.append(row["url"])
        if sum(1 for u in done if "fast" in u) == 12:
            fast_done.set()
        return {"status": "fetched"}

    monkeypatch.setattr(fa.store, "articles_by_status", lambda conn, status, limit=0: rows)
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: [])
    monkeypatch.setattr(registry, "is_mine", lambda *a, **k: True)
    monkeypatch.setattr(fa, "process_article", fake_process)
    conn = store.connect(tmp_path / "f.db")
    counts = fa.run(conn, "t", workers=1, deadline=clock.now + 130)
    slow = [i for i, u in enumerate(done) if "slow" in u]
    fast = [i for i, u in enumerate(done) if "fast" in u]
    assert len(fast) == 12, "every ordinary domain was fetched"
    assert max(fast) < slow[1], "the ordinary domains went while the slow one slept"
    # slots at 0, 60 and 120 seconds; the fourth, at 180, falls past the 130 second deadline
    assert len(slow) == 3 and counts["skipped_deadline"] == 1 and counts["fetched"] == 15
    assert counts["slow_domains"] == {"slow.example": 60.0}


def test_a_failing_crawl_delayed_site_is_not_retried_within_the_run(cache, monkeypatch):
    calls = []

    class _Resp:
        status_code = 503
        headers = {}
    monkeypatch.setattr(fa.requests, "get", lambda *a, **k: calls.append(1) or _Resp())
    monkeypatch.setattr(fa, "_rate_wait", lambda d: None)
    monkeypatch.setattr(fa, "time", _Clock())          # the retry backoff does not sleep for real
    cache["slow.example"] = ("ok", _rb("User-agent: *\nCrawl-delay: 60\nDisallow: /x"))
    html, status, err = fa.get_page("https://slow.example/a")
    assert html is None and err == "http_503" and len(calls) == 1
    calls.clear()
    fa.get_page("https://quick.example/a")
    assert len(calls) == config.MAX_RETRIES + 1


# ---------------------------------------------------------------------------
# Feed polling: the first feed always, further ones while they fit
# ---------------------------------------------------------------------------

@pytest.fixture
def feed_state(cache, monkeypatch):
    now = time.time()
    monkeypatch.setattr(fetch_feeds, "_slow_feeds", {"started": now, "deadline": None, "polled": {}})
    monkeypatch.setattr(fa, "_last_request", {"lovinmalta.example": now})   # its robots.txt, just now
    cache["lovinmalta.example"] = ("ok", _rb("User-agent: *\nCrawl-delay: 600\nDisallow: /wp-admin/"))
    cache["quick.example"] = ("ok", _rb("User-agent: *\nDisallow: /wp-admin/"))
    return now


def test_a_ten_minute_host_gets_one_feed_per_run(feed_state, monkeypatch):
    assert not fetch_feeds._defer_feed("https://lovinmalta.example/feed")       # waits out the delay
    fa._last_request["lovinmalta.example"] = feed_state + 600                   # that slot is taken
    assert fetch_feeds._defer_feed("https://lovinmalta.example/news/feed")      # the next one would be at 20 min
    assert not fetch_feeds._defer_feed("https://quick.example/feed")            # ordinary hosts never deferred


def test_a_sixty_second_host_gets_every_feed_that_fits(feed_state, monkeypatch):
    fa._robots_cache["www.asiaone.com"] = ("ok", _rb("User-agent: *\nCrawl-delay: 60\nDisallow: /search/"))
    fa._last_request["www.asiaone.com"] = feed_state
    for i in range(3):
        assert not fetch_feeds._defer_feed("https://www.asiaone.com/rss/%d.xml" % i)
        fa._last_request["www.asiaone.com"] += 60


def test_no_slow_feed_is_started_past_the_deadline(feed_state):
    fetch_feeds._slow_feeds["deadline"] = feed_state + 300
    assert fetch_feeds._defer_feed("https://lovinmalta.example/feed")


def test_outside_a_discovery_run_nothing_is_deferred(feed_state):
    fetch_feeds._slow_feeds["started"] = None
    fa._last_request["lovinmalta.example"] = feed_state + 6000
    assert not fetch_feeds._defer_feed("https://lovinmalta.example/feed")


def test_feeds_on_a_slow_host_take_turns_by_the_hour(feed_state):
    entries = [{"url": "https://lovinmalta.example/feed", "type": "rss"},
               {"url": "https://quick.example/feed", "type": "rss"},
               {"url": "https://lovinmalta.example/news/feed", "type": "rss"}]
    fetch_feeds._slow_feeds["started"] = 3600.0 * 10
    assert fetch_feeds._take_turns(entries) == entries
    fetch_feeds._slow_feeds["started"] = 3600.0 * 11
    assert [e["url"] for e in fetch_feeds._take_turns(entries)] == [
        "https://lovinmalta.example/news/feed", "https://quick.example/feed", "https://lovinmalta.example/feed"]


def test_a_deferred_feed_is_counted_and_leaves_no_health_record(monkeypatch, tmp_path):
    outlet = {"id": "mt_x", "name": "X", "country": "MLT", "language": "en",
              "feeds": ["https://lovinmalta.example/feed"], "tier": "national", "active": True}
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": o["feeds"][0], "kind": registry.EDITORIAL, "result": None, "deferred": True}])
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: [outlet])   # skips the full registry sync
    conn = store.connect(tmp_path / "d.db")
    counts = fetch_feeds.run(conn, "t", outlets=[outlet])
    assert counts["feeds_deferred"] == 1 and counts["feeds"] == 0 and counts["outlets_polled"] == 1
    assert store.feed_health_row(conn, "https://lovinmalta.example/feed") is None
