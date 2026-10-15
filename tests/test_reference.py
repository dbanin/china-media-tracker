"""Reference sources (pipeline/reference.py, sources/reference.yaml): originals only, never counted.

The centre is the export invariance test: the same database with and without reference items (and
state_copies rows) gives the same site files, apart from wall clock times and meta.measures, and China
stays a gap. Around it: the counted tables are untouched by a poll and a fetch, the ids are apart from
the outlets, the classifier and the lookalike index never see a reference item, the prune leaves them
alone, and each source's time_rule reads T1a's real samples (tests/fixtures/reference/) right.
"""
import datetime as dt
import json
import shutil
from pathlib import Path

import feedparser
import pytest

from pipeline import config, export, fetch_articles, fetch_feeds, news_sitemap, reference, registry, section_pages, store

FIX = Path(__file__).parent / "fixtures" / "reference"
NOW = dt.datetime(2026, 10, 14, 0, 0, tzinfo=dt.timezone.utc)
COUNTED = ("articles", "classifications", "daily_discovery", "daily_outlet_discovery", "daily_counts", "feed_health",
           "feed_polls", "feed_poll_log", "seen_urls", "body_fingerprints", "body_links", "outlets")


def _src(sid):
    return {s["id"]: s for s in reference.load_sources()}[sid]


def _feed(src, url_part):
    return next(f for f in reference._listings(src) if url_part in f["url"])


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

def test_the_registry_validates_and_holds_the_recommended_set():
    srcs = reference.load_sources()
    assert [s["id"] for s in srcs] == ["ref_xinhua_en", "ref_cgtn_en", "ref_global_times_en", "ref_peoples_daily_en",
                                       "ref_china_daily_en", "ref_ecns_en"]
    for s in srcs:
        assert s["id"].startswith("ref_") and "country" not in s
        assert s["collector"] == "hosted" and s["active"] is True
        for f in reference._listings(s):
            assert f["time_rule"] in ("as_published", "bjt_if_unzoned", "bjt", "date_only", "url_date")
    # Xinhua Italian does not exist; CRI English and CGTN Italian are not usable.
    assert not any("italian" in f["url"] or "cri.cn" in f["url"] for s in srcs for f in reference._listings(s))


def test_reference_ids_are_apart_from_the_outlets_and_china_is_not_monitored():
    outlets = registry.load_outlets()
    assert not {o["id"] for o in outlets} & {s["id"] for s in reference.load_sources()}
    assert not any(o["id"].startswith("ref_") for o in outlets)
    assert not [o["id"] for o in outlets if o["country"] == "CHN" and o.get("active")]
    assert any(g["country"] == "CHN" for g in registry.load_gaps())


@pytest.mark.parametrize("bad", [
    [{"id": "xinhua_en", "name": "x", "publisher": "xinhua", "language": "en", "feeds": [], "credits": [], "collector": "hosted",
      "active": True, "news_sitemaps": [{"url": "https://x.test/s.xml", "time_rule": "bjt"}]}],
    [{"id": "ref_x", "name": "x", "publisher": "xinhua", "language": "en", "country": "CHN", "feeds": [], "credits": [],
      "collector": "hosted", "active": True}],
    [{"id": "ref_x", "name": "x", "publisher": "xinhua", "language": "en", "credits": [], "collector": "hosted", "active": True,
      "feeds": [{"url": "https://x.test/f", "type": "rss", "time_rule": "guess"}]}],
    [{"id": "ref_x", "name": "x", "publisher": "xinhua", "language": "en", "credits": [], "collector": "hosted", "active": True,
      "feeds": []}],
])
def test_a_malformed_registry_is_refused(bad):
    with pytest.raises(Exception):
        reference.validate_sources(bad)


# ---------------------------------------------------------------------------
# time_rule, against T1a's real samples
# ---------------------------------------------------------------------------

def _items(sid, url_part, entries):
    src = _src(sid)
    return {i["url"]: i for i in reference.entries_to_items(src, _feed(src, url_part), entries, now=NOW)}


def test_cgtn_rss_times_are_correct_as_published():
    items = _items("ref_cgtn_en", "politics.xml", feedparser.parse((FIX / "cgtn_rss.xml").read_bytes()).entries)
    botswana = next(i for u, i in items.items() if "1RcZzKR0rRK" in u)
    assert botswana["published_at"] == "2026-10-13T08:39:33+00:00"
    # the UTM query strings are gone from the stored address
    assert all("UTM" not in u and "?" not in u for u in items)


def test_cgtn_sitemap_z_times_are_beijing_time():
    entries, err = reference.sitemap_entries((FIX / "cgtn_latestnews.xml").read_bytes())
    assert err is None
    items = _items("ref_cgtn_en", "sitemap_latestnews", entries)
    by = {k.split("-")[-1].split("/")[0]: v["published_at"] for k, v in items.items()}
    # 2026-10-13T16:39Z in the sitemap is the item the RSS gives as 08:39:33 GMT
    assert by["1RcZzKR0rRK"] == "2026-10-13T08:39:00+00:00"
    # 2026-10-14T01:27Z is shown on the page as datePublished 2026-10-14T01:27:17+08:00
    assert by["1RdxyyECXmM"] == "2026-10-13T17:27:00+00:00"
    assert reference.page_time((FIX / "cgtn_page_time.html").read_text(), "jsonld") == "2026-10-13T17:27:17+00:00"
    assert by["1RbLjN2vbZ6"] == "2026-10-12T16:25:00+00:00"


def test_ecns_unzoned_times_are_beijing_time():
    items = _items("ref_ecns_en", "ecns.cn/rss", feedparser.parse((FIX / "ecns_rss.xml").read_bytes()).entries)
    got = sorted(i["published_at"] for i in items.values())
    # pubDate "2026-10-13 18:10:06", which feedparser alone reads as 18:10 UTC
    assert got == ["2026-10-13T10:03:58+00:00", "2026-10-13T10:10:06+00:00"]


def test_global_times_keeps_the_sitemap_date_and_takes_the_time_from_the_page():
    entries, _ = reference.sitemap_entries((FIX / "gt_sitemap.xml").read_bytes())
    items = _items("ref_global_times_en", "globaltimes", entries)
    editorial = next(i for u, i in items.items() if u.endswith("1372221.shtml"))
    # written 2026-10-13T04:30:03+08:00, a 12 hour UTC clock: only the date is kept
    assert editorial["published_at"] == "2026-10-13"
    # "Published: Oct 14, 2026 12:30 AM" on the page, Beijing time
    assert reference.page_time((FIX / "gt_page_time.html").read_text(), "published_line") == "2026-10-13T16:30:00+00:00"


def test_xinhua_list_times_are_beijing_time_and_match_the_page():
    entries = reference.list_entries((FIX / "xinhua_list.html").read_text(), "https://english.news.cn/list/china-business.htm")
    assert len(entries) == 4 and entries[0]["link"] == "https://english.news.cn/20261013/47d14df2c9eb49e4a5403c5486386250/c.html"
    items = _items("ref_xinhua_en", "china-business", entries)
    assert items[entries[0]["link"]]["published_at"] == "2026-10-13T13:51:45+00:00"
    # the article page prints the same Beijing stamp as the list (2026-10-14 00:42:00 for this item)
    assert reference.page_time((FIX / "xinhua_page_time.html").read_text(), "bjt_stamp") == "2026-10-13T16:42:00+00:00"


def test_homepage_items_carry_the_url_date_and_the_page_time():
    pd = _src("ref_peoples_daily_en")
    got = reference.entries_to_items(pd, _feed(pd, "en.people.cn"), [
        {"link": "https://en.people.cn/n3/2026/1013/c90000-20507791.html", "title": "Chinese vice premier meets"},
        {"link": "https://en.people.cn/90780/index.html", "title": "A section page, not an article"}], now=NOW)
    assert [(i["url"], i["published_at"]) for i in got] == [("https://en.people.cn/n3/2026/1013/c90000-20507791.html", "2026-10-13")]
    assert reference.page_time((FIX / "pd_page_time.html").read_text(), "hhmm_month_day_year") == "2026-10-13T06:05:00+00:00"
    cd = _src("ref_china_daily_en")
    got = reference.entries_to_items(cd, _feed(cd, "chinadaily"), [
        {"link": "https://www.chinadaily.com.cn/a/202610/13/WS6ace4cd3e4b06d4aa0562994.html", "title": "Shanghai wins award"},
        {"link": "http://epaper.chinadaily.com.cn/global", "title": "China Daily Global E-paper"},
        {"link": "https://www.chinadaily.com.cn/a/202608/03/WS6a7000f7a310986e2b468af1.html", "title": "An evergreen page"}], now=NOW)
    assert [i["published_at"] for i in got] == ["2026-10-13"]
    assert reference.page_time((FIX / "cd_page_time.html").read_text(), "jsonld") == "2026-10-13T15:22:59+00:00"


def test_an_explicit_offset_is_trusted_where_the_rule_allows():
    assert reference.normalise_time("2026-10-13T16:39+08:00", "bjt_if_unzoned") == "2026-10-13T08:39:00+00:00"
    assert reference.normalise_time("2026-10-13T16:39+00:00", "bjt_if_unzoned") == "2026-10-13T16:39:00+00:00"
    assert reference.normalise_time(None, "bjt_if_unzoned", link="https://x.test/news/2026-10-12/a") == "2026-10-12"
    assert reference.normalise_time("nonsense", "bjt") is None


# ---------------------------------------------------------------------------
# Cleaners
# ---------------------------------------------------------------------------

def test_the_cgtn_cleaner_strips_the_language_menu_and_footer():
    raw = (FIX / "cgtn_extracted.txt").read_text()
    assert raw.count("\n") > 100
    lines = reference.clean_body(raw, "cgtn").split("\n")
    assert lines[0] == "CGTN" and len(lines) == 2
    assert lines[1].startswith("President of the Republic of Botswana")


def test_the_peoples_daily_cleaner_strips_the_related_links():
    out = reference.clean_body((FIX / "pd_extracted.txt").read_text(), "peoples_daily")
    assert "Related Stories" not in out and "Photos" not in out and "Copyright" not in out
    assert out.split("\n")[1].startswith("BEIJING, Oct. 13 (Xinhua) --") and len(out.split("\n")) == 6
    assert reference.clean_body("a\nb", "none") == "a\nb"


def test_video_page_and_china_daily_template_lines_are_stripped():
    # lines seen on the live check of 2026-10-14
    cgtn = "Asia;China\n00:29\nChina's Zheng Qinwen made history on home soil.\n" \
           "For more, check out our exclusive content on CGTN Now and subscribe to our weekly newsletter."
    assert reference.clean_body(cgtn, "cgtn") == "China's Zheng Qinwen made history on home soil."
    cd = "Athletes compete in Qingdao\nShare - WeChat\nThe championships concluded on Tuesday.\nMost Popular\n" \
         "- Zheng crowned China Open champion in historic triumph"
    assert reference.clean_body(cd, "china_daily") == "Athletes compete in Qingdao\nThe championships concluded on Tuesday."
    menu = "Multilateralism a shield\nShare - WeChat\n- Search\n- HOME\n- CHINA\nPhoto\nTop\nCopyright 1994 -\nvar oTime"
    assert reference.clean_body(menu, "china_daily") == "Multilateralism a shield"


# ---------------------------------------------------------------------------
# Poll and fetch on fixture listings, with the network replaced
# ---------------------------------------------------------------------------

def _paras(seed, n=6):
    words = ["state", "council", "province", "trade", "officials", "delegation", "agreement", "cooperation", "port",
             "summit", "energy", "market", "farmers", "festival", "railway", "museum", "drones", "harvest"]
    out = []
    for p in range(n):
        out.append(" ".join("%s%d" % (words[(seed * 7 + p * 5 + i) % len(words)], (seed * 31 + p * 11 + i) % 97)
                            for i in range(45)) + ".")
    return out


def _html(title, paras, head=""):
    body = "".join("<p>%s</p>" % p for p in paras)
    return "<html><head><title>%s</title>%s</head><body><article><h1>%s</h1>%s</article></body></html>" % (title, head, title, body)


SHARED_URL = "https://en.people.cn/n3/2026/1013/c90000-20507791.html"
PAYWALL_URL = "https://www.chinadaily.com.cn/a/202610/13/WS6ace4cd3e4b06d4aa0562994.html"


class FakeNet:
    """The listings and pages the poll and fetch read, from the fixtures; records every request."""

    def __init__(self):
        self.requests = []
        self.pages = {}

    def install(self, monkeypatch):
        monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (True, "no_robots"))
        monkeypatch.setattr(fetch_feeds, "fetch_feed", self.feed)
        monkeypatch.setattr(news_sitemap, "get_xml", self.xml)
        monkeypatch.setattr(section_pages, "fetch_entries", self.section)
        monkeypatch.setattr(fetch_articles, "get_page", self.page)

    def feed(self, url, *a, **k):
        self.requests.append(url)
        name = "ecns_rss.xml" if "ecns" in url else "cgtn_rss.xml"
        parsed = feedparser.parse((FIX / name).read_bytes())
        return {"ok": True, "entries": parsed.entries, "status": 200, "error": None}

    def xml(self, url, *a, **k):
        self.requests.append(url)
        return (FIX / ("gt_sitemap.xml" if "globaltimes" in url else "cgtn_latestnews.xml")).read_bytes(), 200, None

    def section(self, url, type_="page"):
        self.requests.append(url)
        links = {"english.news.cn": ["https://english.news.cn/20261013/47d14df2c9eb49e4a5403c5486386250/c.html",
                                     "https://english.news.cn/20261013/0123456789abcdef0123456789abcdef/c.html"],
                 "en.people.cn": [SHARED_URL],
                 "chinadaily": [PAYWALL_URL, "http://epaper.chinadaily.com.cn/global"]}
        host = next(k for k in links if k in url)
        return {"ok": True, "status": 200, "error": None,
                "entries": [{"link": u, "title": "Homepage link %d" % i, "summary": "", "published_parsed": None}
                            for i, u in enumerate(links[host])]}

    def page(self, url):
        self.requests.append(url)
        if "list" in url or url.endswith("index.htm"):
            return (FIX / "xinhua_list.html").read_text(), 200, None
        if url == PAYWALL_URL:
            return _html("Locked", _paras(99, 1), head='<script type="application/ld+json">{"isAccessibleForFree": false}</script>'), 200, None
        head = ""
        if "globaltimes" in url:
            head = (FIX / "gt_page_time.html").read_text()
        elif "people.cn" in url:
            head = (FIX / "pd_page_time.html").read_text()
        seed = sum(map(ord, url)) % 50
        return _html("Article %d" % seed, _paras(seed), head="<meta name='x' content='y'>") .replace(
            "</article>", "</article><div>%s</div>" % head), 200, None


def _seed_counted(conn):
    """A few monitored articles, counted, one of them at the address a People's Daily item also has."""
    ids = []
    for i, url in enumerate([SHARED_URL, "https://x.test/it/1", "https://x.test/it/2"]):
        aid = store.insert_discovered(conn, {"url": url, "outlet_id": "it_big", "country": "ITA", "language": "it",
                                             "title": "Monitored story %d" % i, "status": "classified", "gate_relevant": 1})
        store.record_discovery(conn, "ITA", True, "it_big")
        h = store.url_hash(url)
        store.update_article(conn, aid, body_hash=store.save_body(h, "The monitored outlet's own body %d." % i))
        ids.append(aid)
    store.record_feed_health(conn, "it_big", "https://x.test/feed", True, None, 3)
    conn.commit()
    return ids


def _counts(conn):
    return {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in COUNTED}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "t.db")
    return conn


def test_poll_and_fetch_write_reference_items_only(db, monkeypatch):
    net = FakeNet()
    net.install(monkeypatch)
    ids = _seed_counted(db)
    before = _counts(db)
    shared_body = store.load_body(store.url_hash(SHARED_URL))
    got = reference.poll(db, now=NOW)
    assert got["new"] > 0 and got["feeds_ok"] == got["feeds"]
    rows = {r["url"]: r for r in db.execute("SELECT * FROM reference_items")}
    assert {r["source_id"] for r in rows.values()} == {s["id"] for s in reference.load_sources()}
    assert all(r["status"] == "discovered" and r["collector"] == "hosted" for r in rows.values())
    # the CGTN item listed by the RSS and the sitemap is stored once, with the RSS time
    botswana = [r for u, r in rows.items() if "1RcZzKR0rRK" in u]
    assert len(botswana) == 1 and botswana[0]["published_at"] == "2026-10-13T08:39:33+00:00"
    # the Xinhua item on a list and on the homepage keeps the list's time
    xh = rows["https://english.news.cn/20261013/47d14df2c9eb49e4a5403c5486386250/c.html"]
    assert xh["published_at"] == "2026-10-13T13:51:45+00:00"
    assert rows[SHARED_URL]["also_monitored"] == str(ids[0])
    assert "http://epaper.chinadaily.com.cn/global" not in rows
    health = db.execute("SELECT COUNT(*) FROM reference_feed_health").fetchone()[0]
    assert health == sum(len(reference._listings(s)) for s in reference.load_sources())

    got = reference.fetch(db, now=NOW)
    assert got["fetched"] > 0 and got.get("paywalled") == 1
    assert _counts(db) == before
    # the monitored article's body at the shared address is untouched
    assert store.load_body(store.url_hash(SHARED_URL)) == shared_body
    after = {r["url"]: r for r in db.execute("SELECT * FROM reference_items")}
    pd = after[SHARED_URL]
    assert pd["status"] == "fetched" and pd["sig"] is not None and pd["shingles"] >= 25
    assert reference.load_body(pd) and pd["body_hash"]
    # the People's Daily page time replaced the URL date
    assert pd["published_at"] == "2026-10-13T06:05:00+00:00"
    gt = next(r for u, r in after.items() if u.endswith("1372221.shtml"))
    assert gt["published_at"] == "2026-10-13T16:30:00+00:00"
    pw = after[PAYWALL_URL]
    assert pw["status"] == "paywalled" and pw["body_hash"] is None
    # paywalled, fetched and failed items are never fetched again
    n = len(net.requests)
    again = reference.fetch(db, now=NOW)
    assert again["candidates"] == 0 and len(net.requests) == n


def test_the_cleaner_runs_before_the_body_is_stored_and_fingerprinted(db, monkeypatch):
    net = FakeNet()
    net.install(monkeypatch)
    from pipeline import extract, body_dups
    raw = (FIX / "cgtn_extracted.txt").read_text().replace(
        "President of the Republic of Botswana", " ".join(_paras(3)) + "\nPresident of the Republic of Botswana")
    monkeypatch.setattr(extract, "extract", lambda html, url=None: {"text": raw, "title": None, "author": None, "date": None})
    src = _src("ref_cgtn_en")
    db.execute("""INSERT INTO reference_items(source_id, url, url_hash, language, published_at, discovered_at, status)
                  VALUES ('ref_cgtn_en', 'https://news.cgtn.com/news/2026-10-13/x-1/p.html', 'h1', 'en', '2026-10-13T08:39:33+00:00', ?, 'discovered')""",
               (NOW.isoformat(),))
    reference.fetch(db, sources=[src], now=NOW)
    row = db.execute("SELECT * FROM reference_items").fetchone()
    body = reference.load_body(row)
    assert row["status"] == "fetched" and "Choose your language" not in body and "Albanian" not in body
    assert "Search Trends" not in body and body.startswith("CGTN")
    assert row["shingles"] == body_dups.fingerprint(body)[0]


def test_classifier_and_lookalike_index_never_see_reference_items(db, monkeypatch):
    from pipeline import classify_rules, lookalike
    net = FakeNet()
    net.install(monkeypatch)
    reference.poll(db, now=NOW)
    reference.fetch(db, now=NOW)
    text = "BEIJING, Oct. 13 (Xinhua) -- Chinese Vice Premier He Lifeng met business leaders on trade. " * 5
    aid = store.insert_discovered(db, {"url": "https://x.test/it/copy", "outlet_id": "it_big", "country": "ITA", "language": "en",
                                       "title": "Chinese vice premier meets executive vice chair", "status": "fetched", "gate_relevant": 1,
                                       "published_at": "2026-10-13T08:00:00+00:00"})
    store.update_article(db, aid, body_hash=store.save_body(store.url_hash("https://x.test/it/copy"), text),
                         body_chars=len(text), extract_version=config.EXTRACT_VERSION)
    db.commit()
    snapshot = [tuple(r) for r in db.execute("SELECT * FROM reference_items ORDER BY id")]
    classify_rules.run(db, "t")
    assert [r[0] for r in db.execute("SELECT DISTINCT article_id FROM classifications")] == [aid]
    assert [tuple(r) for r in db.execute("SELECT * FROM reference_items ORDER BY id")] == snapshot
    row = dict(db.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone())
    index = lookalike.build_index(db, [row])
    ref_titles = {r["title"] for r in db.execute("SELECT title FROM reference_items")}
    assert set(index.refs) <= {aid} and not ref_titles & {r.get("title") for r in index.refs.values()}


def test_prune_and_expire_seen_leave_reference_items_alone(db):
    old = "2026-01-01T00:00:00+00:00"
    db.execute("""INSERT INTO reference_items(source_id, url, url_hash, discovered_at, status) VALUES
                  ('ref_ecns_en', 'http://www.ecns.cn/a', 'h-old', ?, 'failed')""", (old,))
    db.commit()
    store.prune_gated_out(db)
    store.expire_seen(db)
    assert db.execute("SELECT COUNT(*) FROM reference_items").fetchone()[0] == 1


def test_unpaired_bodies_are_deleted_after_thirty_days_and_the_rows_kept(db):
    old = (NOW - dt.timedelta(days=31)).isoformat()
    for i, h in enumerate(("aa01", "aa02", "aa03")):
        db.execute("""INSERT INTO reference_items(source_id, url, url_hash, discovered_at, fetched_at, status, body_hash, sig, shingles)
                      VALUES ('ref_ecns_en', ?, ?, ?, ?, 'fetched', ?, x'00', 40)""",
                   ("http://www.ecns.cn/%d" % i, h, old, old if i < 2 else NOW.isoformat(),
                    store.save_body(reference.body_key(h), "body %d" % i)))
    paired = db.execute("SELECT id FROM reference_items WHERE url_hash='aa02'").fetchone()[0]
    db.execute("""INSERT INTO state_copies(copy_id, version, computed_at, original_kind, ref_id) VALUES (1, 't', ?, 'reference', ?)""",
               (NOW.isoformat(), paired))
    assert reference.prune_bodies(db, now=NOW) == 1
    rows = {r["url_hash"]: r for r in db.execute("SELECT * FROM reference_items")}
    assert rows["aa01"]["body_pruned_at"] and reference.load_body("aa01") is None and rows["aa01"]["sig"] is not None
    assert rows["aa02"]["body_pruned_at"] is None and reference.load_body("aa02") == "body 1"
    assert rows["aa03"]["body_pruned_at"] is None and reference.load_body("aa03") == "body 2"


def test_validate_is_a_report_that_changes_nothing(db, monkeypatch):
    net = FakeNet()
    net.install(monkeypatch)
    reference.poll(db, now=NOW)
    snap = [tuple(r) for r in db.execute("SELECT * FROM reference_items ORDER BY id")]
    rep = reference.validate(db)
    assert set(rep["sources"]) == {s["id"] for s in reference.load_sources()}
    assert all(e["active"] for e in rep["sources"].values())
    assert [tuple(r) for r in db.execute("SELECT * FROM reference_items ORDER BY id")] == snap
    assert reference.load_sources() == reference.load_sources()


def test_run_stage_gives_up_when_the_run_has_no_time_left(db):
    import time
    assert reference.run_stage(db, "poll", time.time() - 1) == {"skipped": "no time left"}


# ---------------------------------------------------------------------------
# Export invariance
# ---------------------------------------------------------------------------

def _fixture_db(path):
    conn = store.connect(path)
    day = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).strftime("%Y-%m-%d")
    for i, (outlet, country, cat) in enumerate([("it_big", "ITA", "A"), ("it_big", "ITA", "C"), ("fr_state", "FRA", "A"),
                                                ("fr_state", "FRA", "B"), ("it_wire", "ITA", "A")]):
        url = "https://x.test/%s/%d" % (outlet, i)
        aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": country, "language": "it",
                                             "title": "Story %d about China and Xinhua" % i, "status": "classified",
                                             "gate_relevant": 1, "published_at": day + "T08:00:00+00:00"})
        conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (day + "T09:00:00+00:00", aid))
        store.record_discovery(conn, country, True, outlet, day)
        store.insert_classification(conn, aid, "rules", cat, 1.0, evidence_quote="ROME, Oct. 12 (Xinhua) --",
                                    signatures_fired=["xinhua_dateline"] if cat == "A" else [], route="wire_credit" if cat == "A" else None)
    conn.commit()
    return conn


def _add_reference_rows(conn):
    now = store.utcnow()
    for i in range(4):
        conn.execute("""INSERT INTO reference_items(source_id, url, url_hash, language, title, published_at, discovered_at, fetched_at,
                            status, body_hash, body_chars, sig, shingles, collector) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     ("ref_xinhua_en", "https://english.news.cn/2026101%d/%032x/c.html" % (i, i), "ref%d" % i, "en",
                      "Story %d about China and Xinhua" % i, "2026-10-12T01:00:00+00:00", now, now, "fetched", "b%d" % i, 900,
                      b"\x00" * 256, 80, "hosted"))
    conn.execute("""INSERT INTO reference_feed_health(feed_url, source_id, last_checked, last_ok, last_entries, consecutive_failures,
                        total_checks, total_failures) VALUES ('https://english.news.cn/list/latestnews.htm', 'ref_xinhua_en', ?, ?, 20, 0, 1, 0)""",
                 (now, now))
    for aid, rid in ((1, 1), (3, 2)):
        conn.execute("""INSERT INTO state_copies(copy_id, version, computed_at, original_kind, ref_id, original_source, band,
                            novel_share) VALUES (?, 't1', ?, 'reference', ?, 'ref_xinhua_en', 'light', 0.1)""", (aid, now, rid))
    conn.execute("""INSERT INTO state_copies(copy_id, version, computed_at, original_kind, none_reason) VALUES (5, 't1', ?, 'none', 'no candidate')""",
                 (now,))
    conn.commit()


# The wall clock times an export writes, which differ between two exports of the same database a
# second apart: every generated_at (latest, outlets, examples, meta and meta.findings), meta's
# last_successful_run and last_runs (the run log's stage times), and planted.json's generated. Found by
# diffing two exports of the fixture database taken seconds apart; nothing else differed.
CLOCK_KEYS = {"generated_at"}
TOP_CLOCK_KEYS = {"meta.json": ("last_successful_run", "last_runs"), "planted.json": ("generated",)}


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in CLOCK_KEYS}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def _site_files(out, audit):
    files = {}
    for p in sorted(out.rglob("*.json")):
        rel = str(p.relative_to(out))
        if rel in ("latest.json", "global_series.json", "outlets.json", "examples.json", "meta.json", "planted.json") \
                or rel.startswith(("daily/", "articles/")):
            data = json.loads(p.read_text())
            if rel == "meta.json":
                data.pop("measures", None)
            for k in TOP_CLOCK_KEYS.get(rel, ()):
                assert k in data, (rel, k)     # still written, so dropping it hides nothing else
                data.pop(k)
            files[rel] = _strip(data)
    for p in sorted(audit.rglob("*")):
        if p.is_file():
            files["audit/" + str(p.relative_to(audit))] = p.read_text()
    return files


def test_export_is_the_same_with_and_without_reference_items(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    outlets = [{"id": "it_big", "name": "Big Italian Outlet", "country": "ITA", "language": "it", "feeds": ["https://x.test/f1"],
                "tier": "national", "active": True, "ownership": "private"},
               {"id": "it_wire", "name": "A Release Wire", "country": "ITA", "language": "it", "feeds": ["https://x.test/f2"],
                "tier": "distribution_wire", "active": True, "ownership": "state"},
               {"id": "fr_state", "name": "French State Outlet", "country": "FRA", "language": "fr", "feeds": ["https://x.test/f3"],
                "tier": "national", "active": True, "ownership": "state"}]
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: [dict(o) for o in outlets])
    base = tmp_path / "base.db"
    _fixture_db(base).close()
    with_ref = tmp_path / "with_ref.db"
    shutil.copy(base, with_ref)
    conn = store.connect(with_ref)
    _add_reference_rows(conn)
    conn.close()
    results = {}
    for name, path in (("base", base), ("ref", with_ref)):
        monkeypatch.setattr(config, "DB_PATH", path)
        out, audit = tmp_path / name / "data", tmp_path / name / "audit"
        export.run(store.connect(path), "test", export_dir=out, audit_dir=audit,
                   methodology_path=tmp_path / name / "M.md", prune=False)
        results[name] = _site_files(out, audit)
    assert set(results["base"]) == set(results["ref"])
    assert any(k.startswith("daily/") for k in results["base"]) and any(k.startswith("audit/") for k in results["base"])
    for k in results["base"]:
        assert results["base"][k] == results["ref"][k], k
    latest = results["ref"]["latest.json"]
    assert latest["countries"]["CHN"]["coverage"] == "gap"
    assert results["ref"]["articles/CHN.json"] == []
    # the reference rows were there throughout
    assert store.connect(with_ref).execute("SELECT COUNT(*) FROM reference_items").fetchone()[0] == 4
