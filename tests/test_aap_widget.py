"""The AAP release widget on Australian Community Media mastheads (section_pages, type aap_widget).

The mastheads' "Press Releases from AAP" pages are filled in the browser from the AAP release
listing, so the collector reads that listing: once per run, shared by every masthead, back to the
previous poll (or to AAP_BACKFILL_SINCE on the first one). Each masthead keeps its own copy of a
release, the copies are linked as one underlying item, and the keyword gate applies.
"""
import datetime as dt
import json
from pathlib import Path

from pipeline import classify_rules, extract, fetch_feeds, registry, section_pages, store

FIXTURES = Path(__file__).parent / "fixtures"
UTC = dt.timezone.utc
ACM = ["au_canberratimes", "au_newcastleherald", "au_illawarramercury", "au_examiner", "au_bendigoadvertiser",
       "au_thecourier", "au_bordermail", "au_standard", "au_dailyadvertiser", "au_theadvocate"]


def _page(items):
    """A listing page in the service's markup: items are (rkey, title, abstract, 'dd/mm/yyyy HH:MM')."""
    lis = "".join(
        '<li class="aapr-widget_release"><span class="aapr-widget_title"><a href="javascript:window.open('
        "window.location.href.split('?')[0] + '?rkey=%s', '_self');\">%s</a></span>"
        '<span class="aapr-widget_datetime"><p>Published: %s</p></span>'
        '<span class="aapr-widget_abstract"><p>%s</p></span></li>' % (k, t, d, a) for k, t, a, d in items)
    return "<div><ul>%s</ul></div>" % lis


def _serve(monkeypatch, pages):
    """Serve listing pages by their from= number; record every request."""
    from pipeline import fetch_articles
    calls = []
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (True, "no_robots"))

    def get_page(url):
        calls.append(url)
        n = int(url.rsplit("from=", 1)[1])
        return (pages[n - 1] if n <= len(pages) else _page([])), 200, None
    monkeypatch.setattr(fetch_articles, "get_page", get_page)
    return calls


def test_the_saved_listing_page_parses():
    rel = section_pages.aap_releases((FIXTURES / "aap_listing.html").read_text())
    assert len(rel) == 10
    assert len({r["rkey"] for r in rel}) == 10
    assert all(r["title"] and r["published"] and r["published"].tzinfo for r in rel)
    assert rel[0]["rkey"] == "cision20261012AE70142" and rel[0]["title"].startswith("HCLTech delivers")
    assert [r["published"] for r in rel] == sorted((r["published"] for r in rel), reverse=True)


def test_published_times_are_sydney_local():
    # AEDT (UTC+11) in October, AEST (UTC+10) in September.
    assert section_pages._aap_time("Published: 13/10/2026 06:29") == dt.datetime(2026, 10, 12, 19, 29, tzinfo=UTC)
    assert section_pages._aap_time("Published: 08/09/2026 14:22") == dt.datetime(2026, 9, 8, 4, 22, tzinfo=UTC)
    assert section_pages._aap_time("no date") is None


def test_the_saved_release_page_is_readable():
    out = extract.extract((FIXTURES / "aap_release.html").read_text(),
                          "https://newsroom.aap.com.au/releases/cision20260908AE42174")
    assert out["title"].startswith("Xinhua Silk Road: City marathon")
    assert "Shenyang Marathon" in out["text"] and len(out["text"]) > 1000


def test_the_listing_is_read_back_to_the_date_and_no_further(monkeypatch):
    pages = [_page([("r%d%d" % (p, i), "Release %d %d" % (p, i), "", "%02d/09/2026 12:00" % (20 - 3 * p)) for i in range(3)])
             for p in range(5)]
    calls = _serve(monkeypatch, pages)
    since = dt.datetime(2026, 9, 12, tzinfo=UTC)          # page 3 (14/09) is the last one inside
    r = section_pages.aap_listing(since)
    assert r["ok"] and len(calls) == 4 and r["pages"] == 4   # page 4 (11/09) crosses the date and stops the read
    assert {x["rkey"][:2] for x in r["releases"]} == {"r0", "r1", "r2"}


def test_the_read_ends_at_the_last_page_and_a_failed_page_fails_it(monkeypatch):
    pages = [_page([("a%d" % i, "Release %d" % i, "", "20/09/2026 12:00") for i in range(3)])]
    calls = _serve(monkeypatch, pages)
    r = section_pages.aap_listing(dt.datetime(2026, 9, 1, tzinfo=UTC))
    assert r["ok"] and len(r["releases"]) == 3 and len(calls) == 2
    from pipeline import fetch_articles
    monkeypatch.setattr(fetch_articles, "get_page", lambda url: (None, 503, "http_503"))
    r = section_pages.aap_listing(dt.datetime(2026, 9, 1, tzinfo=UTC))
    assert not r["ok"] and r["error"] == "http_503" and r["releases"] == []


def test_every_masthead_shares_one_read_and_keeps_its_own_copy(monkeypatch, tmp_path):
    now = dt.datetime.now(UTC) + dt.timedelta(hours=11)
    stamp = now.strftime("%d/%m/%Y %H:%M")
    calls = _serve(monkeypatch, [_page([("k1", "Xinhua Silk Road: something in China", "", stamp)])])
    section_pages.aap_start(store.connect(tmp_path / "a.db"), [])
    section_pages._aap["since"] = dt.datetime.now(UTC) - dt.timedelta(days=1)
    a = section_pages.fetch_entries("https://www.canberratimes.com.au/press-releases", section_pages.AAP_WIDGET)
    b = section_pages.fetch_entries("https://www.examiner.com.au/press-releases", section_pages.AAP_WIDGET)
    assert len(calls) == 2                       # one read of the listing (page 1, then the empty page 2)
    assert a["keyword_gate"] and b["keyword_gate"]
    assert a["entries"][0]["link"] == "https://newsroom.aap.com.au/releases/k1?masthead=canberratimes.com.au"
    assert b["entries"][0]["link"] == "https://newsroom.aap.com.au/releases/k1?masthead=examiner.com.au"
    assert store.url_hash(a["entries"][0]["link"]) != store.url_hash(b["entries"][0]["link"])


def test_the_first_poll_reads_back_to_the_backfill_date_and_later_ones_to_the_last_poll(tmp_path):
    conn = store.connect(tmp_path / "b.db")
    outlets = [{"id": "au_x", "feeds": [], "section_feeds": [
        {"url": "https://www.x.com.au/press-releases", "kind": "press_release", "type": "aap_widget"}]}]
    section_pages.aap_start(conn, outlets)
    assert section_pages._aap["since"] == dt.datetime(2026, 9, 3, tzinfo=UTC)
    store.record_feed_health(conn, "au_x", "https://www.x.com.au/press-releases", True, None, 10)
    last = dt.datetime.fromisoformat(store.feed_health_row(conn, "https://www.x.com.au/press-releases")["last_ok"])
    section_pages.aap_start(conn, outlets)
    assert section_pages._aap["since"] == last - dt.timedelta(hours=section_pages.AAP_OVERLAP_HOURS)
    assert section_pages._aap["listing"] is None


def test_discovery_gates_aap_items_keeps_the_section_arrival_and_links_the_copies(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "c.db")
    releases = [
        {"rkey": "cn1", "title": "Xinhua Silk Road: City marathon in NE China's Shenyang draws runners",
         "summary": "", "published": dt.datetime.now(UTC)},
        {"rkey": "us1", "title": "Acme Widgets reports record third quarter revenue growth",
         "summary": "", "published": dt.datetime.now(UTC)},
    ]
    reads = []

    def listing(since, max_pages=section_pages.AAP_MAX_PAGES, **kw):
        reads.append(since)
        return {"ok": True, "releases": releases, "status": 200, "error": None, "pages": 1, "complete": True}
    monkeypatch.setattr(section_pages, "aap_listing", listing)
    by_id = {o["id"]: o for o in registry.load_outlets()}
    outlets = [dict(by_id[i], feeds=[]) for i in ("au_canberratimes", "au_newcastleherald")]
    counts = fetch_feeds.run(conn, "t", outlets=outlets, workers=2)
    assert len(reads) == 1 and counts["section_items"] == 0
    rows = conn.execute("SELECT url, outlet_id, status, gate_terms, dup_group_id FROM articles ORDER BY id").fetchall()
    assert len(rows) == 4
    china = [r for r in rows if "/cn1?" in r["url"]]
    other = [r for r in rows if "/us1?" in r["url"]]
    assert {r["outlet_id"] for r in china} == {"au_canberratimes", "au_newcastleherald"}
    assert all(r["status"] == "queued" for r in china) and all(r["status"] == "gated_out" for r in other)
    assert all(classify_rules.arrival_of(r["gate_terms"]) == "press_release_section" for r in china)
    assert "China" in json.loads(china[0]["gate_terms"])
    assert china[0]["dup_group_id"] and china[0]["dup_group_id"] == china[1]["dup_group_id"]


def test_the_acm_mastheads_read_the_aap_widget():
    by_id = {o["id"]: o for o in registry.load_outlets()}
    for oid in ACM:
        o = by_id[oid]
        page = "https://www.%s/press-releases" % o["feeds"][0].split("//www.", 1)[1].split("/", 1)[0]
        assert {"url": page, "kind": "press_release", "type": "aap_widget"} in registry.feed_entries(o), oid
        sec = [s for s in o["release_sections"]["sections"] if s["url"] == page]
        assert sec and sec[0]["readable"] is True, oid
        assert not registry.is_distribution_wire(o)
