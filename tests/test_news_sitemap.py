"""Google News sitemaps read as editorial feeds (pipeline/news_sitemap.py, registry news_sitemaps).

The fixtures in tests/fixtures/news_sitemaps were fetched once, on 2026-10-12 at about 21:28 UTC,
through news_sitemap.get_xml (robots.txt check, per domain rate limit, the project's user agent), and
are stored gzipped as fetched. fetched.json records each URL, its status and the fetch time. PR
Newswire's sitemap is an index of eight pages; the first three, the ones the reader follows, are kept.
Daily Times (dailytimes.com.pk/sitemap-news.xml) answered 403 and is not registered.
"""
import datetime as dt
import gzip
import json
from pathlib import Path

import pytest

from pipeline import fetch_feeds, gate, news_sitemap as ns, registry, store

FIX = Path(__file__).parent / "fixtures" / "news_sitemaps"
META = json.loads((FIX / "fetched.json").read_text())
PRN_INDEX = "https://www.prnewswire.com/sitemap-news.xml"
PRN_PAGES = {PRN_INDEX + "?page=%d" % i: "us_prnewswire.page%d.xml.gz" % i for i in (1, 2, 3)}


def _body(name):
    return gzip.decompress((FIX / name).read_bytes())


def _when(oid):
    return dt.datetime.fromisoformat(META[oid]["fetched_at"])


def _serve(monkeypatch, files):
    """news_sitemap.get_xml served from the fixtures, recording every URL asked for."""
    asked = []

    def get_xml(url, timeout=None):
        asked.append(url)
        if url not in files:
            return None, 404, "http_404"
        return _body(files[url]), 200, None
    monkeypatch.setattr(ns, "get_xml", get_xml)
    return asked


# ---------------------------------------------------------------------------
# Parsing the saved sitemaps
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("oid,count,hours", [
    ("ar_clarin", 1000, 41.9), ("rs_telegraf", 635, 47.9), ("br_riotimes", 302, 42.2), ("sg_asiaone", 50, 9.3)])
def test_each_saved_sitemap_parses_into_feed_shaped_entries(oid, count, hours):
    p = ns.parse(_body("%s.xml.gz" % oid))
    assert p["kind"] == "urlset" and p["skipped"] == 0
    entries = p["entries"]
    assert len(entries) == count
    for e in entries:
        assert e["link"].startswith("https://") and e["title"] and e["summary"] == ""
        # published_parsed is what feedparser gives and fetch_feeds._entry_time reads, in UTC
        assert fetch_feeds._entry_time(e) == e["published"]
    stamps = sorted(dt.datetime.fromisoformat(e["published"]) for e in entries)
    assert round((stamps[-1] - stamps[0]).total_seconds() / 3600, 1) == hours


def test_clarin_is_capped_at_googles_thousand_entries():
    assert len(ns.parse(_body("ar_clarin.xml.gz"))["entries"]) == ns.MAX_ENTRIES_PER_FILE == 1000


def test_publication_dates_with_offsets_are_read_in_utc():
    first = ns.parse(_body("us_prnewswire.page1.xml.gz"))["entries"][0]
    assert first["published"] == "2026-10-12T21:11:00+00:00"   # 17:11 at -04:00
    assert ns._w3c_time("2026-10-12T08:15:00.1234567+02:00").isoformat() == "2026-10-12T06:15:00+00:00"
    assert ns._w3c_time("2026-10-12").isoformat() == "2026-10-12T00:00:00+00:00"
    assert ns._w3c_time("yesterday") is None


def test_an_index_lists_its_children_and_a_url_without_a_news_title_is_skipped():
    p = ns.parse(_body("us_prnewswire.index.xml.gz"))
    assert p["kind"] == "index" and len(p["children"]) == 8
    body = (b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            b'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
            b'<url><loc>https://x.test/a</loc></url>'
            b'<url><loc>https://x.test/b</loc><news:news><news:publication_date>2026-10-12T10:00:00Z'
            b'</news:publication_date><news:title>China and Chile sign a deal</news:title></news:news></url></urlset>')
    p = ns.parse(body)
    assert [e["link"] for e in p["entries"]] == ["https://x.test/b"] and p["skipped"] == 1
    assert ns.parse(b"<html><body>not a sitemap</body></html>")["kind"] == "error"
    assert ns.parse(b"<<<")["kind"] == "error"


# ---------------------------------------------------------------------------
# Reading a sitemap: index, children, age window, the request rules
# ---------------------------------------------------------------------------

def test_prnewswire_index_follows_the_three_newest_pages_only(monkeypatch):
    asked = _serve(monkeypatch, dict(PRN_PAGES, **{PRN_INDEX: "us_prnewswire.index.xml.gz"}))
    r = ns.fetch_entries(PRN_INDEX, now=_when("us_prnewswire"))
    assert r["ok"] and r["files"] == 4 and r["backfill"] is True
    # Eight pages all carry the same lastmod: document order, page 1 (the newest) first.
    assert asked == [PRN_INDEX] + list(PRN_PAGES)
    assert len(r["entries"]) == 283
    times = [e["published"] for e in r["entries"]]
    assert times == sorted(times, reverse=True)
    assert (times[0], times[-1]) == ("2026-10-12T21:11:00+00:00", "2026-10-12T13:26:00+00:00")
    # All of prnewswire.com's English release list, not a US-only list: non-US issuers and the
    # German and Spanish versions of a Chinese municipal release are on it.
    assert all(e["link"].startswith("https://www.prnewswire.com/news-releases/") for e in r["entries"])
    assert sum("Luoyang" in e["title"] for e in r["entries"]) == 3


def test_an_index_inside_an_index_is_not_followed(monkeypatch):
    asked = _serve(monkeypatch, {"https://x.test/news.xml": "us_prnewswire.index.xml.gz",
                                 PRN_INDEX + "?page=1": "us_prnewswire.index.xml.gz"})
    r = ns.fetch_entries("https://x.test/news.xml", now=_when("us_prnewswire"))
    assert not r["ok"] and r["error"] == "nested_index"
    assert asked.count(PRN_INDEX + "?page=1") == 1 and len(asked) == 1 + ns.MAX_CHILD_SITEMAPS


def test_entries_older_than_two_days_are_dropped(monkeypatch):
    _serve(monkeypatch, {"https://www.telegraf.rs/xml/news": "rs_telegraf.xml.gz"})
    r = ns.fetch_entries("https://www.telegraf.rs/xml/news", now=_when("rs_telegraf") + dt.timedelta(hours=12))
    cutoff = (_when("rs_telegraf") + dt.timedelta(hours=12 - ns.MAX_AGE_HOURS)).isoformat()
    assert r["ok"] and 0 < len(r["entries"]) < 635
    assert min(e["published"] for e in r["entries"]) >= cutoff
    # A sitemap of nothing but old items is a failed read, not an empty success.
    r = ns.fetch_entries("https://www.telegraf.rs/xml/news", now=_when("rs_telegraf") + dt.timedelta(days=5))
    assert not r["ok"] and r["error"] == "no_entries"


def test_get_xml_honours_robots_and_takes_a_paced_slot_with_the_declared_user_agent(monkeypatch):
    from pipeline import config, fetch_articles as fa
    monkeypatch.setattr(fa, "can_fetch", lambda url: (False, "robots_disallowed"))
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("requested anyway")))
    assert ns.get_xml("https://blocked.test/news.xml") == (None, None, "robots_disallowed")

    waited, seen = [], {}
    monkeypatch.setattr(fa, "can_fetch", lambda url: (True, "robots_allowed"))
    monkeypatch.setattr(fa, "_rate_wait", lambda domain: waited.append(domain))

    class _Resp:
        status_code = 200
        content = gzip.compress(_body("sg_asiaone.xml.gz"))     # a .xml.gz file is unpacked

    def get(url, headers=None, **k):
        seen.update(headers)
        return _Resp()
    monkeypatch.setattr(requests, "get", get)
    body, status, err = ns.get_xml("https://www.asiaone.com/googlenews.xml")
    assert waited == ["www.asiaone.com"] and seen["User-Agent"] == config.USER_AGENT
    assert status == 200 and err is None and ns.parse(body)["kind"] == "urlset"


# ---------------------------------------------------------------------------
# The keyword gate on a title alone
# ---------------------------------------------------------------------------

def test_the_gate_reads_a_title_with_an_empty_summary():
    assert gate.check("China and Argentina sign a currency swap", "", "en", "ARG")[0]
    assert gate.check("China y Argentina firman un acuerdo", "", "es", "ARG")[0]
    assert not gate.check("Boca Juniors wins the derby", "", "en", "ARG")[0]
    assert gate.check("", "", "en", "USA") == (False, [])
    assert gate.check(None, None, "en", "USA") == (False, [])


@pytest.mark.parametrize("oid,language,country,passed", [
    ("ar_clarin", "es", "ARG", 4), ("br_riotimes", "en", "BRA", 5), ("rs_telegraf", "sr", "SRB", 4),
    ("sg_asiaone", "en", "SGP", 3)])
def test_few_sitemap_titles_pass_the_gate(oid, language, country, passed):
    """Only the China share of a sitemap reaches the fetch queue."""
    entries = ns.parse(_body("%s.xml.gz" % oid))["entries"]
    assert sum(gate.check(e["title"], "", language, country)[0] for e in entries) == passed


@pytest.mark.parametrize("oid,language,country,passed", [
    ("ar_clarin", "es", "ARG", 4), ("br_riotimes", "en", "BRA", 5), ("rs_telegraf", "sr", "SRB", 4),
    ("sg_asiaone", "en", "SGP", 3), ("us_prnewswire", "en", "USA", 11)])
def test_title_only_terms_add_few_sitemap_titles(oid, language, country, passed):
    """The title_only terms (keywords.yaml) add 8 of PR Newswire's 283 saved entries, all of them
    Chinese issuers (GAC at the Paris Motor Show, the Canton Fair), and nothing at the other four."""
    names = PRN_PAGES.values() if oid == "us_prnewswire" else ["%s.xml.gz" % oid]
    entries = [e for n in names for e in ns.parse(_body(n))["entries"]]
    assert sum(gate.check(e["title"], "", language, country, title_only=True)[0] for e in entries) == passed


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_sitemaps_are_editorial_feeds_after_the_rss_and_never_replace_it():
    outlet = {"feeds": ["https://x.test/rss"], "news_sitemaps": ["https://x.test/news.xml", "https://x.test/rss"],
              "section_feeds": [{"url": "https://x.test/press", "kind": "press_release", "type": "page"}]}
    assert registry.feed_entries(outlet) == [
        {"url": "https://x.test/rss", "kind": "editorial", "type": "rss"},
        {"url": "https://x.test/news.xml", "kind": "editorial", "type": "news_sitemap"},
        {"url": "https://x.test/press", "kind": "press_release", "type": "page"}]


REGISTERED = {"us_prnewswire": "https://www.prnewswire.com/sitemap-news.xml",
              "br_riotimes": "https://www.riotimesonline.com/news-sitemap.xml",
              "ar_clarin": "https://www.clarin.com/sitemaps/sitemap_google_news.xml",
              "rs_telegraf": "https://www.telegraf.rs/xml/news",
              "sg_asiaone": "https://www.asiaone.com/googlenews.xml"}


def test_registered_sitemaps_sit_beside_the_rss_feeds():
    by_id = {o["id"]: o for o in registry.load_outlets()}
    for oid, url in REGISTERED.items():
        assert by_id[oid]["news_sitemaps"] == [url] and by_id[oid]["feeds"], oid
        assert url == META[oid]["url"] and META[oid]["status"] == 200, oid
    assert "news_sitemaps" not in by_id["pk_dailytimes"]       # 403 to the crawler's user agent
    assert registry.is_distribution_wire(by_id["us_prnewswire"])


def test_save_outlets_keeps_the_field(tmp_path):
    outlets = [o for o in registry.load_outlets() if o["id"] in REGISTERED]
    path = tmp_path / "o.yaml"
    registry.save_outlets(outlets, path)
    again = registry.load_outlets(path)
    assert {o["id"]: o["news_sitemaps"] for o in again} == {o["id"]: o["news_sitemaps"] for o in outlets}


# ---------------------------------------------------------------------------
# Discovery with a sitemap beside the RSS
# ---------------------------------------------------------------------------

def _prn():
    return next(o for o in registry.load_outlets() if o["id"] == "us_prnewswire")


RSS = "https://www.prnewswire.com/rss/news-releases-list.rss"


def _group(outlet, rss_entries, sitemap):
    return [{"outlet": outlet, "feed_url": RSS, "kind": "editorial", "type": "rss",
             "result": {"ok": True, "entries": rss_entries, "error": None, "status": 200}},
            {"outlet": outlet, "feed_url": PRN_INDEX, "kind": "editorial", "type": "news_sitemap",
             "result": sitemap}]


def _sitemap(monkeypatch):
    _serve(monkeypatch, dict(PRN_PAGES, **{PRN_INDEX: "us_prnewswire.index.xml.gz"}))
    return ns.fetch_entries(PRN_INDEX, now=_when("us_prnewswire"))


def test_sitemap_items_keep_the_wire_outlet_and_are_gated_on_title(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "a.db")
    sm = _sitemap(monkeypatch)
    outlet = _prn()
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: _group(o, sm["entries"][:2], sm))
    counts = fetch_feeds.run(conn, "t1", outlets=[outlet])
    rows = conn.execute("SELECT outlet_id, country, feed_url, summary, status, gate_terms, title FROM articles").fetchall()
    assert len(rows) == 283 and counts["items_new"] == 283
    assert {r["outlet_id"] for r in rows} == {"us_prnewswire"} and {r["country"] for r in rows} == {"USA"}
    assert sum(r["feed_url"] == PRN_INDEX for r in rows) == 281          # two came first by the RSS
    assert all(r["summary"] == "" for r in rows if r["feed_url"] == PRN_INDEX)
    queued = [r for r in rows if r["status"] == "queued"]
    assert {r["title"] for r in queued} >= {t for t in (e["title"] for e in sm["entries"]) if "Luoyang" in t}
    # 3 on the main lists; 8 more on the title_only terms (GAC at the Paris Motor Show, the Canton Fair)
    assert len(queued) == 11
    assert sum(all(t.startswith("title_only:") for t in json.loads(r["gate_terms"])) for r in queued) == 8

    # The next poll lists the same items: all known, none gated or counted again.
    counts = fetch_feeds.run(conn, "t2", outlets=[outlet])
    assert counts["items_new"] == 0 and conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 283


def test_the_same_article_under_another_host_is_not_inserted_twice(monkeypatch, tmp_path):
    """Telegraf's RSS links www.telegraf.rs/izbori-2026/4412441-... where its sitemap has
    politika.telegraf.rs/izbori-2026/4412441-...: same path, same title, one article."""
    conn = store.connect(tmp_path / "b.db")
    sm = _sitemap(monkeypatch)
    outlet = _prn()
    first = sm["entries"][0]
    elsewhere = dict(first, link=first["link"].replace("https://www.", "https://politika."))
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: _group(o, [elsewhere], sm))
    counts = fetch_feeds.run(conn, "t1", outlets=[outlet])
    assert counts["sitemap_same_title"] == 1 and counts["items_new"] == 283
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE title=?", (first["title"],)).fetchone()[0] == 1


def test_two_releases_with_one_title_at_two_addresses_both_count():
    """PR Newswire issued some releases twice under two release numbers (8 pairs in the saved pages);
    the RSS counts both, so the sitemap does too."""
    titles = {}
    entries = ns.parse(_body("us_prnewswire.page3.xml.gz"))["entries"]
    assert not any(ns.duplicate_title(titles, e["title"], e["link"]) for e in entries)
    a = entries[0]
    assert ns.duplicate_title(titles, a["title"], a["link"].replace("https://www.", "https://amp.")) is True
    assert ns.duplicate_title(titles, a["title"], a["link"]) is False


def test_a_title_only_rejection_is_readmitted_when_the_rss_brings_a_summary(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "c.db")
    sm = _sitemap(monkeypatch)
    outlet = _prn()
    release = next(e for e in sm["entries"] if not gate.check(e["title"], "", "en", "USA")[0])
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: _group(o, [], sm))
    fetch_feeds.run(conn, "t1", outlets=[outlet])
    assert conn.execute("SELECT status FROM articles WHERE url=?", (release["link"],)).fetchone()[0] == "gated_out"
    with_summary = dict(release, summary="BEIJING, Oct. 12, 2026 /PRNewswire/ -- Xinhua Silk Road: the forum in Beijing")
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: _group(o, [with_summary], sm))
    counts = fetch_feeds.run(conn, "t2", outlets=[outlet])
    assert counts["readmitted_editorial"] == 1
    row = conn.execute("SELECT status, gate_relevant FROM articles WHERE url=?", (release["link"],)).fetchone()
    assert row["status"] == "queued" and row["gate_relevant"] == 1


def _saturated_rss(conn, outlet, sm, extra=0):
    """Three RSS items published after the previous poll, all new: a saturated poll. extra of them
    are not on the sitemap."""
    last_ok = "2026-10-12T20:00:00+00:00"
    store.record_feed_health(conn, outlet["id"], RSS, True, None, 20)
    conn.execute("UPDATE feed_health SET last_ok=? WHERE feed_url=?", (last_ok, RSS))
    conn.commit()
    items = [e for e in sm["entries"] if e["published"] > last_ok][:3]
    for i in range(extra):
        items[i] = dict(items[i], link="https://www.prnewswire.com/news-releases/not-on-the-sitemap-%d.html" % i,
                        title="Not on the sitemap %d" % i)
    return items


@pytest.mark.parametrize("extra", [0, 1])
def test_a_saturated_rss_poll_is_scaled_by_what_the_sitemap_left_out(monkeypatch, tmp_path, extra):
    conn = store.connect(tmp_path / "d.db")
    sm = _sitemap(monkeypatch)
    outlet = _prn()
    rss = _saturated_rss(conn, outlet, sm, extra)
    # The same poll without the sitemap: saturated, with an estimate.
    plain, plain_missed = fetch_feeds.saturation({"last_ok": "2026-10-12T20:00:00+00:00"}, 3, 3,
                                                 [fetch_feeds._entry_time(e) for e in rss])
    assert plain and plain_missed > 0
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: _group(o, rss, sm))
    counts = fetch_feeds.run(conn, "t1", outlets=[outlet])
    assert counts["saturation_covered"] == 1
    sat, missed = conn.execute("SELECT saturated, missed_estimate FROM feed_polls WHERE feed_url=?", (RSS,)).fetchone()
    if extra == 0:
        assert (sat, missed) == (0, 0.0)
    else:
        assert sat == 1 and 0 < missed < plain_missed + 1e-9


def test_a_sitemap_that_does_not_reach_the_previous_poll_leaves_the_estimate():
    covered = {"oldest": dt.datetime(2026, 10, 12, 21, tzinfo=dt.timezone.utc), "hashes": set()}
    assert ns.covered_saturation({"last_ok": "2026-10-12T20:00:00+00:00"}, True, 12.5, ["https://x.test/a"], covered) \
        == (True, 12.5, False)
    assert ns.covered_saturation({"last_ok": "2026-10-12T20:00:00+00:00"}, False, 0.0, ["https://x.test/a"], covered) \
        == (False, 0.0, False)
    assert ns.covered_saturation(None, True, 3.0, ["https://x.test/a"], covered) == (True, 3.0, False)


def test_poll_outlet_reads_a_sitemap_feed_with_the_sitemap_reader(monkeypatch):
    monkeypatch.setitem(fetch_feeds._slow_feeds, "started", None)     # outside a run: nothing deferred
    called = []
    monkeypatch.setattr(fetch_feeds, "fetch_feed", lambda url: called.append(("rss", url)) or {"ok": False, "entries": [], "error": "x", "status": None})
    monkeypatch.setattr(ns, "fetch_entries", lambda url: called.append(("map", url)) or {"ok": False, "entries": [], "error": "x", "status": None})
    outlet = {"id": "br_x", "feeds": ["https://x.test/feed/"], "news_sitemaps": ["https://x.test/news-sitemap.xml"]}
    out = fetch_feeds.poll_outlet(outlet)
    assert called == [("rss", "https://x.test/feed/"), ("map", "https://x.test/news-sitemap.xml")]
    assert [g["type"] for g in out] == ["rss", "news_sitemap"] and all(g["kind"] == "editorial" for g in out)
