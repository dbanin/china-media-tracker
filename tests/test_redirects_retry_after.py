"""Every redirect hop is checked against its own robots.txt and paced, and a Retry-After on a 429 or
503 is honoured, for article pages and feeds alike."""
import email.utils

import pytest
import requests

from pipeline import config, feeds_util, fetch_articles as fa

RSS = b"<rss><channel><item><title>t</title><link>https://x/1</link></item></channel></rss>"


class _Resp:
    def __init__(self, status=200, headers=None, text="<html><p>ok</p></html>", content=RSS):
        self.status_code = status
        self.headers = dict({"Content-Type": "text/html"}, **(headers or {}))
        self.text = text
        self.content = content
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


class _Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


@pytest.fixture
def net(monkeypatch):
    """Scripted responses by URL, a robots.txt that disallows /private/, and a recorded pace."""
    script, calls, paced = {}, [], []

    def get(url, headers=None, timeout=None, allow_redirects=True):
        assert allow_redirects is False, "redirects are followed by hand"
        calls.append(url)
        seq = script[url]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(fa, "can_fetch", lambda url: (False, "robots_disallowed") if "/private/" in url else
                        ((False, "robots_unavailable") if "down.example" in url else (True, "robots_allowed")))
    monkeypatch.setattr(fa, "_rate_wait", lambda domain: paced.append(domain))
    monkeypatch.setattr(fa, "_held", {})
    monkeypatch.setattr(fa, "_last_request", {})
    monkeypatch.setattr(fa.random, "uniform", lambda a, b: 0.0)
    return script, calls, paced


def test_a_redirect_is_followed_only_where_robots_allows(net):
    script, calls, paced = net
    script["https://a.example/story"] = [_Resp(301, {"Location": "https://b.example/story"})]
    script["https://b.example/story"] = [_Resp(302, {"Location": "/private/story"})]
    html, status, err = fa.get_page("https://a.example/story")
    assert html is None and err == "redirect_robots_disallowed"
    # the disallowed target is never requested; the allowed hop was paced on its own domain
    assert calls == ["https://a.example/story", "https://b.example/story"]
    assert paced == ["a.example", "b.example"]


def test_an_allowed_redirect_chain_arrives(net):
    script, calls, paced = net
    script["http://a.example/x"] = [_Resp(301, {"Location": "https://a.example/x"})]
    script["https://a.example/x"] = [_Resp(200)]
    html, status, err = fa.get_page("http://a.example/x")
    assert err is None and status == 200 and "ok" in html
    assert calls == ["http://a.example/x", "https://a.example/x"]


def test_a_redirect_to_a_host_whose_robots_cannot_be_read_is_transient(net):
    script, _, _ = net
    script["https://a.example/y"] = [_Resp(302, {"Location": "https://down.example/y"})]
    assert fa.get_page("https://a.example/y")[2] == "robots_unavailable"
    assert fa.get_page("https://a.example/y")[2].startswith(fa.TRANSIENT_FAIL_PREFIXES)


def test_redirect_loops_and_odd_schemes_stop(net, monkeypatch):
    script, calls, _ = net
    monkeypatch.setattr(fa, "time", _Clock())
    script["https://a.example/loop"] = [_Resp(302, {"Location": "https://a.example/loop"})]
    assert fa.get_page("https://a.example/loop")[2] == "TooManyRedirects"
    script["https://a.example/ftp"] = [_Resp(302, {"Location": "ftp://a.example/file"})]
    assert fa.get_page("https://a.example/ftp")[2] == "redirect_bad_scheme"


def test_a_feed_redirect_is_checked_too(net):
    script, calls, _ = net
    script["https://feeds.example/rss"] = [_Resp(301, {"Location": "https://feeds.example/private/rss"})]
    r = feeds_util.fetch_feed("https://feeds.example/rss")
    assert r["ok"] is False and r["error"] == "redirect_robots_disallowed"
    assert calls == ["https://feeds.example/rss"]
    script["https://feeds.example/moved"] = [_Resp(308, {"Location": "https://feeds.example/rss2"})]
    script["https://feeds.example/rss2"] = [_Resp(200)]
    assert feeds_util.fetch_feed("https://feeds.example/moved")["ok"]


def test_a_short_retry_after_is_waited_out(net, monkeypatch):
    script, calls, _ = net
    clock = _Clock()
    monkeypatch.setattr(fa, "time", clock)
    script["https://a.example/busy"] = [_Resp(429, {"Retry-After": "20"}), _Resp(200)]
    html, status, err = fa.get_page("https://a.example/busy")
    assert err is None and len(calls) == 2
    # no blind 2 second backoff: the domain's next slot was moved past the 20 seconds asked for
    assert clock.slept == []
    assert fa._last_request["a.example"] == pytest.approx(1_000_000.0 + 20)


def test_a_long_retry_after_holds_the_domain_for_the_run(net, monkeypatch):
    script, calls, _ = net
    clock = _Clock()
    monkeypatch.setattr(fa, "time", clock)
    script["https://a.example/one"] = [_Resp(429, {"Retry-After": "3600"})]
    script["https://a.example/two"] = [_Resp(200)]
    assert fa.get_page("https://a.example/one")[2] == "http_429"
    assert calls == ["https://a.example/one"], "no retry inside the hour asked for"
    # another page and a feed on the same host are not requested while it is held
    assert fa.get_page("https://a.example/two")[2] == "http_429_retry_after"
    assert feeds_util.fetch_feed("https://a.example/feed")["error"] == "http_429_retry_after"
    assert calls == ["https://a.example/one"]
    assert "http_429_retry_after".startswith(fa.TRANSIENT_FAIL_PREFIXES)
    clock.now += 3601
    assert fa.get_page("https://a.example/two")[2] is None


def test_retry_after_as_an_http_date(net, monkeypatch):
    import datetime as dt
    when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
    r = _Resp(503, {"Retry-After": email.utils.format_datetime(when, usegmt=True)})
    assert 7000 < fa.retry_after_seconds(r) <= 7200
    assert fa.retry_after_seconds(_Resp(503, {"Retry-After": "soon"})) is None
    assert fa.retry_after_seconds(_Resp(503)) is None


def test_a_feed_429_with_retry_after_holds_the_host(net, monkeypatch):
    script, calls, _ = net
    clock = _Clock()
    monkeypatch.setattr(fa, "time", clock)
    script["https://feeds.example/a"] = [_Resp(429, {"Retry-After": "900"})]
    assert feeds_util.fetch_feed("https://feeds.example/a")["error"] == "http_429"
    assert feeds_util.fetch_feed("https://feeds.example/b")["error"] == "http_429_retry_after"
    assert calls == ["https://feeds.example/a"]


def test_config_retries_still_back_off_without_retry_after(net, monkeypatch):
    script, calls, _ = net
    clock = _Clock()
    monkeypatch.setattr(fa, "time", clock)
    monkeypatch.setattr(config, "MAX_RETRIES", 1)
    script["https://a.example/flaky"] = [_Resp(502), _Resp(200)]
    assert fa.get_page("https://a.example/flaky")[2] is None
    assert clock.slept == [2.0]
