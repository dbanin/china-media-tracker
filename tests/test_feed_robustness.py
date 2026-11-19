"""fetch_feeds.run against malformed feed results and entries (the 2026-10-17 robustness audit): a list
summary, a number for a link, a lone surrogate in a title, a result that is not a result. Each feed is
handled alone: one that breaks the loop is rolled back, counted and recorded as a failed poll, and the
stage goes on with the next feed."""
import datetime as dt

import pytest

from pipeline import fetch_feeds, gate, registry, section_pages, store

PP = dt.datetime.now(dt.timezone.utc).utctimetuple()


def E(link, title="China and Australia sign a trade deal", summary="Beijing said on Tuesday", **kw):
    d = {"link": link, "title": title, "summary": summary, "published_parsed": PP}
    d.update(kw)
    return d


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(section_pages, "aap_start", lambda conn, outlets: None)
    c = store.connect(tmp_path / "f.db")
    yield c
    c.close()


OUTLET = {"id": "au_z", "country": "AUS", "language": "en", "feeds": []}


def _run(conn, monkeypatch, feeds):
    """feeds: [(feed_url, result)] polled for OUTLET in that order."""
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": u, "kind": registry.EDITORIAL, "result": r} for u, r in feeds])
    return fetch_feeds.run(conn, "t", outlets=[OUTLET], workers=1)


def _urls(conn):
    return {r["url"]: r for r in conn.execute("SELECT * FROM articles")}


def test_clean_text():
    assert fetch_feeds.clean_text(None) == "" and fetch_feeds.clean_text(["a", "list"]) == ""
    assert fetch_feeds.clean_text({"value": "x"}) == "" and fetch_feeds.clean_text(True) == ""
    assert fetch_feeds.clean_text(42) == "42"
    assert fetch_feeds.clean_text("China \ud800 trade") == "China � trade"
    assert fetch_feeds.clean_text("\x00Beijing\x01\tsaid\n") == "Beijing\tsaid\n"
    assert fetch_feeds.clean_text(b"caf\xc3\xa9") == "café"


def test_malformed_entries_are_skipped_or_cleaned_and_the_feed_still_counts(conn, monkeypatch):
    entries = [
        E("https://z.test/list", summary=["not", "a", "string"]),
        E(12345),
        E({"href": "https://z.test/dict"}),
        E("https://z.test/surrogate", title="China \ud800 trade " + "x" * 5000, summary="\x00Beijing\ud800"),
        E("https://z.test/number", title=42, author=["someone"]),
        E(None),
        E("javascript:alert(1)"),
    ]
    counts = _run(conn, monkeypatch, [("https://z.test/rss", {"ok": True, "error": None, "entries": entries})])
    assert counts["feeds_ok"] == 1 and counts.get("feed_errors", 0) == 0
    assert counts["entries_malformed"] == 2 and counts["items_seen"] == 3
    rows = _urls(conn)
    assert set(rows) == {"https://z.test/list", "https://z.test/surrogate", "https://z.test/number"}
    s = rows["https://z.test/surrogate"]
    assert s["title"].startswith("China � trade") and len(s["title"]) == fetch_feeds.TITLE_MAX_CHARS
    assert s["summary"] == "Beijing�"
    assert rows["https://z.test/list"]["summary"] == "" and rows["https://z.test/number"]["title"] == "42"
    assert rows["https://z.test/number"]["author"] is None


def test_a_broken_result_is_one_failed_feed_and_the_next_feed_is_read(conn, monkeypatch):
    good = {"ok": True, "error": None, "entries": [E("https://z.test/good")]}
    counts = _run(conn, monkeypatch, [
        ("https://z.test/none.xml", None),
        ("https://z.test/garbage.xml", {"ok": True, "error": None, "entries": "garbage"}),
        ("https://z.test/no_entries.xml", {"ok": True, "error": None}),
        ("https://z.test/good.xml", good),
    ])
    assert counts["feed_errors"] == 3 and counts["feeds"] == 4 and counts["feeds_ok"] == 1
    assert "no_entries.xml: KeyError" in counts["last_feed_error"]
    assert set(_urls(conn)) == {"https://z.test/good"}
    health = {r["feed_url"]: r for r in conn.execute("SELECT * FROM feed_health")}
    for u in ("https://z.test/none.xml", "https://z.test/garbage.xml", "https://z.test/no_entries.xml"):
        assert health[u]["last_error"].startswith("feed_error: ") and health[u]["consecutive_failures"] == 1
    assert health["https://z.test/good.xml"]["last_error"] is None


def test_a_feed_that_fails_part_way_is_rolled_back_whole(conn, monkeypatch):
    real = gate.check

    def check(title, *a, **k):
        if "explodes" in title:
            raise RuntimeError("a bug met half way through a feed")
        return real(title, *a, **k)
    monkeypatch.setattr(gate, "check", check)
    counts = _run(conn, monkeypatch, [
        ("https://z.test/a.xml", {"ok": True, "error": None, "entries": [
            E("https://z.test/a1"), E("https://z.test/a2", title="China trade explodes")]}),
        ("https://z.test/b.xml", {"ok": True, "error": None, "entries": [E("https://z.test/b1")]}),
    ])
    assert counts["feed_errors"] == 1 and "RuntimeError" in counts["last_feed_error"]
    # a1 was inserted and counted before a2 broke the feed: both went with the rollback
    assert set(_urls(conn)) == {"https://z.test/b1"}
    assert counts["items_new"] == 1 and counts["items_seen"] == 1 and counts["feeds_ok"] == 1
    assert conn.execute("SELECT SUM(discovered) FROM daily_discovery").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM feed_polls WHERE feed_url='https://z.test/a.xml'").fetchone()[0] == 0


def test_an_item_inserted_just_before_midnight_counts_on_its_own_day(conn, monkeypatch):
    """insert_discovered stamped discovered_at, then record_discovery read the clock again: a lock wait
    between the two (up to 60 s) put the article in one month's file and its discovery in the next."""
    import types
    real = dt.datetime
    clock = {"t": real(2026, 10, 31, 23, 59, 59, 900000, tzinfo=dt.timezone.utc)}

    class FakeDT(real):
        @classmethod
        def now(cls, tz=None):
            return clock["t"]
    fake = types.SimpleNamespace(**{k: getattr(dt, k) for k in dir(dt) if not k.startswith("__")})
    fake.datetime = FakeDT
    monkeypatch.setattr(store, "dt", fake)
    insert = store.insert_discovered

    def insert_then_wait(conn, item):
        out = insert(conn, item)
        clock["t"] = real(2026, 11, 1, 0, 0, 30, tzinfo=dt.timezone.utc)     # the write lock came free late
        return out
    monkeypatch.setattr(store, "insert_discovered", insert_then_wait)
    _run(conn, monkeypatch, [("https://z.test/m.xml", {"ok": True, "error": None, "entries": [E("https://z.test/m1", published_parsed=None)]})])
    row = conn.execute("SELECT discovered_at, day_of_record FROM articles").fetchone()
    assert row["discovered_at"].startswith("2026-10-31") and row["day_of_record"] is None
    assert [tuple(r) for r in conn.execute("SELECT date, discovered FROM daily_discovery")] == [("2026-10-31", 1)]
    assert [tuple(r) for r in conn.execute("SELECT date FROM daily_outlet_discovery")] == [("2026-10-31",)]


HOSTILE = (b'<?xml version="1.0"?><rss><channel><item><title>China &#xD800; test</title>'
           b'<link>https://fp.test/1</link></item></channel></rss>')
GOOD = (b'<?xml version="1.0"?><rss><channel><item><title>China and Australia sign a trade deal</title>'
        b'<link>https://fp.test/2</link></item></channel></rss>')


def _serve(monkeypatch, bodies):
    from pipeline import fetch_articles as fa

    class _Resp:
        status_code = 200
        headers = {}

        def __init__(self, content):
            self.content = content
    monkeypatch.setattr(fa, "can_fetch", lambda url: (True, "robots_allowed"))
    monkeypatch.setattr(fa, "_rate_wait", lambda domain: None)
    monkeypatch.setattr(fa, "held_reason", lambda domain: None)
    monkeypatch.setattr(fa, "polite_get", lambda url, headers=None, timeout=None: _Resp(bodies[url]))


def test_a_feed_the_parser_raises_on_is_a_failed_poll_of_that_feed_only(monkeypatch):
    """feedparser itself raised UnicodeEncodeError on &#xD800;, out of fetch_feed and out of the
    outlet's poll task, so the outlet's other feeds were lost for that poll too."""
    from pipeline import feeds_util
    _serve(monkeypatch, {"https://fp.test/bad.xml": HOSTILE, "https://fp.test/good.xml": GOOD})
    r = feeds_util.fetch_feed("https://fp.test/bad.xml")
    assert r["ok"] is False and r["entries"] == [] and r["status"] == 200
    assert r["error"].startswith("parse_error: ")
    outlet = {"id": "au_fp", "country": "AUS", "language": "en",
              "feeds": ["https://fp.test/bad.xml", "https://fp.test/good.xml"]}
    group = fetch_feeds.poll_outlet(outlet)
    by_url = {g["feed_url"]: g["result"] for g in group}
    assert by_url["https://fp.test/bad.xml"]["error"].startswith("parse_error: ")
    assert by_url["https://fp.test/good.xml"]["ok"] and by_url["https://fp.test/good.xml"]["entries"][0]["link"] == "https://fp.test/2"


def test_a_parser_exception_of_any_kind_is_caught(monkeypatch):
    from pipeline import feeds_util
    _serve(monkeypatch, {"https://fp.test/x.xml": GOOD})

    def explode(data):
        raise RuntimeError("parser bug")
    monkeypatch.setattr(feeds_util.feedparser, "parse", explode)
    assert feeds_util.fetch_feed("https://fp.test/x.xml")["error"] == "parse_error: RuntimeError"


PATHS = (b'<?xml version="1.0"?><rss><channel>'
         b'<item><title>China one</title><link>/news/1449484</link></item>'
         b'<item><title>China two</title><link> //cdn.fp.test/two </link></item>'
         b'<item><title>China three</title><link>https://fp.test/three</link></item>'
         b'<item><title>China four</title><link>www.fp.test/four</link></item>'
         b'<item><title>China five</title><link>mailto:desk@fp.test</link></item>'
         b'<item><title>China six</title></item>'
         b'</channel></rss>')


def test_item_links_that_are_paths_alone_are_resolved_against_the_feed(monkeypatch):
    """srna.rs/rss and workpointtoday.com/feed/ list every item as a path. feedparser parses bytes and has
    no address to resolve against, discovery keeps only http links, and so both feeds were read for weeks
    without one item. Only a link that starts with a slash is touched."""
    from pipeline import feeds_util
    _serve(monkeypatch, {"https://www.fp.test/en/rss": PATHS})
    r = feeds_util.fetch_feed("https://www.fp.test/en/rss")
    assert r["ok"]
    assert [e.get("link") for e in r["entries"]] == [
        "https://www.fp.test/news/1449484", "https://cdn.fp.test/two", "https://fp.test/three",
        "www.fp.test/four", "mailto:desk@fp.test", None]


def test_a_path_link_is_resolved_against_the_address_that_answered(monkeypatch):
    from pipeline import feeds_util, fetch_articles as fa
    _serve(monkeypatch, {"http://fp.test/rss": PATHS})

    class _Moved:
        status_code, headers, content, url = 200, {}, PATHS, "https://news.fp.test/feeds/all.xml"
    monkeypatch.setattr(fa, "polite_get", lambda url, headers=None, timeout=None: _Moved())
    r = feeds_util.fetch_feed("http://fp.test/rss")
    assert r["entries"][0]["link"] == "https://news.fp.test/news/1449484"


SRNA = (b'<?xml version="1.0"?><rss><channel><link></link>'
        b'<item><title>China one</title><link>/news/1449761</link><guid>/news/1449761</guid></item>'
        b'<item><title>China two</title><link>https://www.srna.rs/news/1449728/</link></item>'
        b'<item><title>China three</title><link>https://www.srna.rs/en/novost/1449484/a-headline</link></item>'
        b'<item><title>China four</title><link>/newsroom/12</link></item>'
        b'</channel></rss>')


def test_srna_item_links_are_rewritten_to_the_address_the_site_serves(conn, monkeypatch):
    """srna.rs/rss lists each item as /news/<number>, which the site answers with 404; the story is at
    /en/novost/<number>/<any words>. Only that form of link, on that host, is rewritten."""
    from pipeline import feeds_util
    _serve(monkeypatch, {"https://www.srna.rs/rss": SRNA, "https://www.fp.test/rss": SRNA})
    r = feeds_util.fetch_feed("https://www.srna.rs/rss")
    assert [e.get("link") for e in r["entries"]] == [
        "https://www.srna.rs/en/novost/1449761/news", "https://www.srna.rs/en/novost/1449728/news",
        "https://www.srna.rs/en/novost/1449484/a-headline", "https://www.srna.rs/newsroom/12"]
    other = feeds_util.fetch_feed("https://www.fp.test/rss")
    assert other["entries"][0]["link"] == "https://www.fp.test/news/1449761"
    counts = _run(conn, monkeypatch, [("https://www.srna.rs/rss", r)])
    assert "https://www.srna.rs/en/novost/1449761/news" in _urls(conn) and counts["items_new"] == 4


def test_srna_rows_stored_under_the_dead_address_take_the_served_one_once(conn, monkeypatch):
    """The rows of 2026-11-18 and 19: the feed listing an item again must find its row, and a row that
    failed with the site's 404 is fetched again at the address that answers."""
    def add(url, status, reason=None, relevant=1, attempts=0):
        conn.execute("INSERT INTO articles(url, url_hash, outlet_id, country, language, discovered_at, status, "
                     "fail_reason, http_status, gate_relevant, fetch_attempts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (url, store.url_hash(url), "ba_srna", "BIH", "sr", store.utcnow(), status, reason,
                      404 if reason == "http_404" else None, relevant, attempts))
    add("https://www.srna.rs/news/1449761", "failed", "http_404", attempts=2)
    add("https://www.srna.rs/news/1449728", "gated_out", relevant=0)
    add("https://www.srna.rs/news/1449484", "failed", "timeout", attempts=1)
    add("https://www.srna.rs/news/1449485", "failed", "http_404", attempts=1)
    add("https://www.srna.rs/en/novost/1449485/news", "fetched")          # already stored where it is served
    add("https://www.fp.test/news/7", "failed", "http_404", attempts=1)
    conn.commit()
    counts = _run(conn, monkeypatch, [])
    assert counts["srna_readdressed"] == {"moved": 3, "queued": 1}
    rows = _urls(conn)
    a = rows["https://www.srna.rs/en/novost/1449761/news"]
    assert (a["status"], a["fail_reason"], a["http_status"], a["fetch_attempts"]) == ("queued", None, None, 0)
    assert a["url_hash"] == store.url_hash("https://www.srna.rs/en/novost/1449761/news")
    assert rows["https://www.srna.rs/en/novost/1449728/news"]["status"] == "gated_out"
    b = rows["https://www.srna.rs/en/novost/1449484/news"]
    assert (b["status"], b["fail_reason"], b["fetch_attempts"]) == ("failed", "timeout", 1)
    assert rows["https://www.srna.rs/news/1449485"]["status"] == "failed"
    assert rows["https://www.fp.test/news/7"]["status"] == "failed" and len(rows) == 6
    # the feed lists the first two again: both rows are found, neither is stored twice
    _serve(monkeypatch, {"https://www.srna.rs/rss": SRNA})
    from pipeline import feeds_util
    again = _run(conn, monkeypatch, [("https://www.srna.rs/rss", feeds_util.fetch_feed("https://www.srna.rs/rss"))])
    assert "srna_readdressed" not in again
    assert again["items_new"] == 2 and len(_urls(conn)) == 8       # the feed's other two items alone


def test_resolved_links_reach_discovery(conn, monkeypatch):
    from pipeline import feeds_util
    _serve(monkeypatch, {"https://www.fp.test/en/rss": PATHS})
    r = feeds_util.fetch_feed("https://www.fp.test/en/rss")
    counts = _run(conn, monkeypatch, [("https://www.fp.test/en/rss", r)])
    assert set(_urls(conn)) == {"https://www.fp.test/news/1449484", "https://cdn.fp.test/two", "https://fp.test/three"}
    assert counts["items_new"] == 3
