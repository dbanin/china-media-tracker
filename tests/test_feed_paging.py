"""Feed paging (pipeline/feed_paging.py): a saturated poll reads the feed's next pages until the stream reaches
back to the previous poll, within a cap, under the same robots.txt, rate limiter and user agent as the first
page; and the estimate of items missed counts what the pages and the outlet's other feeds brought in."""
import datetime as dt
from pathlib import Path

import pytest

from pipeline import feed_paging as fp
from pipeline import fetch_articles as fa
from pipeline import fetch_feeds, feeds_util, registry, section_pages, store

UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC).replace(microsecond=0)
FEED = "https://wp.test/feed/"
WP = {"generator": "https://wordpress.org/?v=6.9.1"}


def E(link, hours_ago, title="Beijing and Kuala Lumpur sign a trade deal", summary="China said on Tuesday"):
    t = NOW - dt.timedelta(hours=hours_ago)
    return {"link": link, "title": title, "summary": summary, "published_parsed": t.utctimetuple()}


def page(n, start_h, size=10, step_h=0.25, prefix="https://wp.test/story-"):
    """The n-th page of a feed publishing one item every step_h hours, newest first."""
    return [E("%s%d" % (prefix, (n - 1) * size + i), start_h + i * step_h) for i in range(size)]


def ok(entries, feed=None):
    r = {"ok": True, "entries": entries, "status": 200, "error": None}
    if feed is not None:
        r["feed"] = feed
    return r


@pytest.fixture(autouse=True)
def _allowed(monkeypatch):
    """robots.txt allows everything and no wait is slept, unless a test says otherwise."""
    monkeypatch.setattr(fa, "can_fetch", lambda url: (True, "robots_allowed"))
    monkeypatch.setattr(fa, "domain_gap", lambda domain: 3.0)


def pages_of(book, calls):
    """A fake fetch_feed serving book[url] and recording each URL asked for."""
    def fetch(url):
        calls.append(url)
        return book.get(url) or {"ok": False, "entries": [], "status": 404, "error": "http_404"}
    return fetch


def wp_book(n_pages, size=10, step_h=0.25):
    book = {FEED: ok(page(1, 0.1, size, step_h), WP)}
    span = size * step_h
    for n in range(2, n_pages + 1):
        book[FEED + "?paged=%d" % n] = ok(page(n, 0.1 + (n - 1) * span, size, step_h))
    return book


# --- forms --------------------------------------------------------------------------------------------------

def test_a_wordpress_feed_pages_with_paged_and_others_do_not():
    form = fp.form_for(FEED, ok([], WP), forms={})
    assert form["source"] == "auto" and form["first"] == 2 and fp.page_url(form, 3) == FEED + "?paged=3"
    sana = fp.form_for("https://sana.test/?feed=rss2", ok([], WP), forms={})
    assert fp.page_url(sana, 2) == "https://sana.test/?feed=rss2&paged=2"
    assert fp.form_for(FEED, ok([], {"generator": "Ghost 6.76"}), forms={}) is None
    assert fp.form_for(FEED, ok([]), forms={}) is None
    # a recorded form wins, and pages: null turns WordPress paging off
    off = {FEED: fp.valid_form({"feed": FEED, "pages": None})}
    assert fp.form_for(FEED, ok([], WP), forms=off) is None


def test_malformed_recorded_forms_are_skipped():
    assert fp.valid_form({"feed": FEED, "pages": "https://wp.test/feed?p=2"}) is None          # no {n}
    assert fp.valid_form({"feed": FEED, "pages": "https://x.test/{n}", "type": "page"}) is None  # no link pattern
    assert fp.valid_form({"feed": FEED, "pages": "https://x.test/{n}", "type": "page", "links": "("}) is None
    assert fp.valid_form({"feed": FEED, "pages": "https://x.test/{n}", "type": "carrier"}) is None
    assert fp.valid_form({"feed": FEED, "pages": "https://x.test/{n}", "max_pages": 99})["max_pages"] == fp.MAX_PAGES_LIMIT


def test_the_recorded_forms_file_loads_and_names_registered_feeds():
    forms = fp.load_forms()
    feeds = {fe["url"] for o in registry.load_outlets() for fe in registry.feed_entries(o)}
    paged = [f for f in forms.values() if f.get("pages")]
    assert len(paged) >= 6
    assert all(f["feed"] in feeds for f in paged), [f["feed"] for f in paged if f["feed"] not in feeds]
    raw = Path(fp.PAGING_PATH).read_text(encoding="utf-8")
    assert "–" not in raw and "—" not in raw


# --- read_back ------------------------------------------------------------------------------------------------

def test_supported_paging_reads_until_the_stream_reaches_the_previous_poll():
    """Ten items a page, one every quarter hour: page 1 spans 2.5 hours; the previous poll was 6 hours ago,
    so pages 2 and 3 are needed and page 3 holds an item from before the poll."""
    book, calls = wp_book(6), []
    since = NOW - dt.timedelta(hours=6)
    form = fp.form_for(FEED, book[FEED], forms={})
    out = fp.read_back(FEED, book[FEED], since, form, fetch=pages_of(book, calls), budget=fp.Budget())
    assert calls == [FEED + "?paged=2", FEED + "?paged=3"]
    assert out["paging"]["reached"] and out["paging"]["stop"] == "reached" and out["paging"]["pages"] == 2
    assert out["first_entries"] == 10 and len(out["entries"]) == 30 and out["paging"]["added"] == 20
    assert len({e["link"] for e in out["entries"]}) == 30


def test_a_first_page_that_reaches_back_is_not_paged():
    book, calls = wp_book(3), []
    out = fp.read_back(FEED, book[FEED], NOW - dt.timedelta(hours=1), fp.form_for(FEED, book[FEED], forms={}),
                       fetch=pages_of(book, calls))
    assert calls == [] and out is book[FEED]
    # no previous poll (a feed's first poll) or a failed first page: nothing to page
    assert fp.read_back(FEED, book[FEED], None, fp.form_for(FEED, book[FEED], forms={}), fetch=pages_of(book, calls)) is book[FEED]
    failed = {"ok": False, "entries": [], "error": "http_500"}
    assert fp.read_back(FEED, failed, NOW, {"pages": FEED + "?paged={n}"}) is failed and calls == []


def test_a_page_that_repeats_page_one_is_unsupported():
    """IOL, RIA and Europa Press serve the first page again whatever the page parameter says."""
    first = ok(page(1, 0.1), WP)
    book, calls = {FEED: first, FEED + "?paged=2": ok(page(1, 0.1))}, []
    out = fp.read_back(FEED, first, NOW - dt.timedelta(hours=8), fp.form_for(FEED, first, forms={}),
                       fetch=pages_of(book, calls))
    assert calls == [FEED + "?paged=2"]
    assert out["paging"]["stop"] == "repeat" and out["paging"]["added"] == 0 and len(out["entries"]) == 10
    assert not out["paging"]["reached"]


def test_links_are_compared_as_the_database_compares_them():
    """Utusan's links carry utm parameters; a page that repeats page 1 under other tracking parameters is a
    repeat, and an item listed twice is inserted and counted once."""
    first = ok(page(1, 0.1), WP)
    tagged = [dict(e, link=e["link"] + "?utm_source=rss&utm_medium=rss") for e in page(1, 0.1)]
    out = fp.read_back(FEED, first, NOW - dt.timedelta(hours=8), fp.form_for(FEED, first, forms={}),
                       fetch=pages_of({FEED + "?paged=2": ok(tagged)}, []))
    assert out["paging"]["stop"] == "repeat" and len(out["entries"]) == 10
    shifted = ok([dict(page(1, 0.1)[-1], link=page(1, 0.1)[-1]["link"] + "?utm_campaign=x")] + page(2, 2.6)[:9])
    out = fp.read_back(FEED, first, NOW - dt.timedelta(hours=8), fp.form_for(FEED, first, forms={}),
                       fetch=pages_of({FEED + "?paged=2": shifted}, []))
    assert out["paging"]["added"] == 9 and len({store.url_hash(e["link"]) for e in out["entries"]}) == len(out["entries"])


def test_a_page_from_days_before_or_newer_than_the_last_is_refused():
    """Adnkronos answered ?page=2 with twelve day old items."""
    first = ok(page(1, 0.1), WP)
    stale = ok(page(2, 24 * 12))
    calls = []
    out = fp.read_back(FEED, first, NOW - dt.timedelta(hours=8), fp.form_for(FEED, first, forms={}),
                       fetch=pages_of({FEED + "?paged=2": stale}, calls))
    assert out["paging"]["stop"] == "gap" and out["paging"]["added"] == 0
    newer = ok(page(2, -1.0, step_h=0.01))              # different items, all newer than page 1
    out = fp.read_back(FEED, first, NOW - dt.timedelta(hours=8), fp.form_for(FEED, first, forms={}),
                       fetch=pages_of({FEED + "?paged=2": newer}, []))
    assert out["paging"]["stop"] == "not_older"


def test_a_quiet_night_between_pages_is_not_a_gap():
    """Village Voice News on 2026-11-09: 14.6 hours between its third and fourth pages."""
    first = ok(page(1, 0.1), WP)
    later = ok(page(2, 0.1 + 2.5 + 14.6))
    out = fp.read_back(FEED, first, NOW - dt.timedelta(hours=18), fp.form_for(FEED, first, forms={}),
                       fetch=pages_of({FEED + "?paged=2": later}, []))
    assert out["paging"]["stop"] == "reached" and out["paging"]["added"] == 10


def test_paging_stops_at_the_cap():
    book, calls = wp_book(20), []
    form = fp.form_for(FEED, book[FEED], forms={})
    out = fp.read_back(FEED, book[FEED], NOW - dt.timedelta(hours=40), form, fetch=pages_of(book, calls))
    assert len(calls) == fp.AUTO_MAX_PAGES and out["paging"]["stop"] == "cap" and not out["paging"]["reached"]
    assert len(out["entries"]) == 10 * (1 + fp.AUTO_MAX_PAGES)


def test_the_run_budget_and_the_deadline_stop_paging():
    book = wp_book(20)
    form = fp.form_for(FEED, book[FEED], forms={})
    budget, calls = fp.Budget(limit=3), []
    a = fp.read_back(FEED, book[FEED], NOW - dt.timedelta(hours=40), form, fetch=pages_of(book, calls), budget=budget)
    b = fp.read_back(FEED, book[FEED], NOW - dt.timedelta(hours=40), form, fetch=pages_of(book, calls), budget=budget)
    assert len(calls) == 3 and a["paging"]["stop"] == "run_budget" and b["paging"]["pages"] == 0
    c = fp.read_back(FEED, book[FEED], NOW - dt.timedelta(hours=40), form, fetch=pages_of(book, []),
                     deadline=fp.time.time() + fp.DEADLINE_MARGIN_SECONDS - 1)
    assert c["paging"]["stop"] == "deadline" and c["paging"]["pages"] == 0


def test_robots_refusal_of_a_page_url_stops_paging_without_a_request(monkeypatch):
    """robots.txt is checked for every page URL, query string included (Astro Awani disallows /*?*)."""
    monkeypatch.setattr(fa, "can_fetch", lambda url: (False, "robots_disallowed") if "?" in url else (True, "ok"))
    book, calls = wp_book(3), []
    out = fp.read_back(FEED, book[FEED], NOW - dt.timedelta(hours=6), fp.form_for(FEED, book[FEED], forms={}),
                       fetch=pages_of(book, calls))
    assert calls == [] and out["paging"]["stop"] == "robots_disallowed" and len(out["entries"]) == 10


def test_a_host_with_a_long_crawl_delay_is_not_paged(monkeypatch):
    monkeypatch.setattr(fa, "domain_gap", lambda domain: 60.0)
    book, calls = wp_book(3), []
    out = fp.read_back(FEED, book[FEED], NOW - dt.timedelta(hours=6), fp.form_for(FEED, book[FEED], forms={}),
                       fetch=pages_of(book, calls))
    assert calls == [] and out["paging"]["stop"] == "slow_host"


def test_a_failed_page_keeps_what_was_read():
    book, calls = wp_book(2), []
    out = fp.read_back(FEED, book[FEED], NOW - dt.timedelta(hours=40), fp.form_for(FEED, book[FEED], forms={}),
                       fetch=pages_of(book, calls))
    assert calls == [FEED + "?paged=2", FEED + "?paged=3"]
    assert out["paging"]["stop"] == "error:http_404" and len(out["entries"]) == 20


def test_the_feed_clock_offset_is_applied_before_judging_the_reach():
    """Berita Harian writes Malaysian time marked +0000: an item stamped 7 hours ago was published 15 hours
    ago, so with the 8 hour offset its first page already reaches a poll 10 hours back."""
    first = ok(page(1, -8 + 0.1, step_h=0.75), WP)        # stamps run 7.9 hours ahead to about 1 hour ago
    form = fp.form_for(FEED, first, forms={})
    since = NOW - dt.timedelta(hours=3)
    assert fp.read_back(FEED, first, since, form, offset_hours=8.0, fetch=pages_of({}, [])) is first
    out = fp.read_back(FEED, first, since, form, offset_hours=0.0, fetch=pages_of({}, []))
    assert out["paging"]["pages"] == 1          # without the offset it looked saturated and asked for page 2


# --- HTML listings ----------------------------------------------------------------------------------------------

LISTING = """<html><body><header><a href="/berita/1/nav-story-that-is-long-enough">Navigation story that is long enough</a></header>
<main>{items}</main>
<aside><a href="https://www.x.test/berita/100/old-popular-story-of-yesterday">An old popular story of yesterday here</a></aside>
</body></html>"""


def listing_page(ids):
    items = "".join('<div><a href="/berita/%d/story-%d"><img alt=""></a>'
                    '<a href="https://www.x.test/berita/%d/story-%d"><h3><small>15:34 ET</small> Story number %d of the day</h3>'
                    '<p>A summary that is not the title</p></a></div>' % (i, i, i, i, i) for i in ids)
    return LISTING.format(items=items)


def test_listing_links_leave_out_the_aside_and_take_the_heading():
    entries = section_pages.listing_links(listing_page([7, 6]), "https://www.x.test/terkini/4", r"^https://www\.x\.test/berita/\d+/")
    assert [e["link"] for e in entries] == ["https://www.x.test/berita/7/story-7", "https://www.x.test/berita/6/story-6"]
    assert entries[0]["title"] == "Story number 7 of the day" and entries[0]["published_parsed"] is None


def _listing_form():
    return fp.valid_form({"feed": "https://www.x.test/rss.xml", "type": "page", "pages": "https://www.x.test/terkini/{n}",
                          "first": 2, "max_pages": 6, "links": r"^https://www\.x\.test/berita/\d+/"})


def _listing_fetch(book, calls):
    def listing(url, pattern):
        calls.append(url)
        html = book.get(url)
        if html is None:
            return {"ok": False, "entries": [], "status": 404, "error": "http_404"}
        return {"ok": True, "entries": section_pages.listing_links(html, url, pattern), "status": 200, "error": None}
    return listing


def _rss_first(ids):
    return ok([E("https://www.x.test/berita/%d/story-%d" % (i, i), 0.1 + k * 0.2) for k, i in enumerate(ids)])


def test_an_undated_listing_reads_until_a_link_on_record_and_proves_it_continues_the_feed():
    """Antara: the feed's 50 items, then the /terkini/N listing, undated, until a link already on record."""
    first = _rss_first(range(60, 50, -1))                      # items 60 to 51
    book = {"https://www.x.test/terkini/2": listing_page(range(53, 43, -1)),   # shares 53 to 51 with the feed
            "https://www.x.test/terkini/3": listing_page(range(43, 33, -1)),
            "https://www.x.test/terkini/4": listing_page(range(33, 23, -1))}
    known = {store.url_hash("https://www.x.test/berita/35/story-35")}
    calls = []
    out = fp.read_back("https://www.x.test/rss.xml", first, NOW - dt.timedelta(hours=8), _listing_form(), known=known,
                       listing=_listing_fetch(book, calls))
    assert calls == ["https://www.x.test/terkini/2", "https://www.x.test/terkini/3"]
    assert out["paging"]["reached"] and out["paging"]["undated"] == out["paging"]["added"] == 7 + 10
    assert "https://www.x.test/berita/100/old-popular-story-of-yesterday" not in {e["link"] for e in out["entries"]}


def test_a_listing_that_shares_nothing_with_the_feed_is_not_read_on():
    first = _rss_first(range(60, 50, -1))
    calls = []
    out = fp.read_back("https://www.x.test/rss.xml", first, NOW - dt.timedelta(hours=8), _listing_form(), known=set(["x"]),
                       listing=_listing_fetch({"https://www.x.test/terkini/2": listing_page(range(40, 30, -1))}, calls))
    assert out["paging"]["stop"] == "no_overlap" and out["paging"]["added"] == 0 and len(calls) == 1


# --- discovery: the poll and its estimate ------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(section_pages, "aap_start", lambda conn, outlets: None)
    monkeypatch.setattr(fp, "load_forms", lambda path=None: {})
    c = store.connect(tmp_path / "p.db")
    yield c
    c.close()


OUTLET = {"id": "my_wp", "name": "WP Daily", "country": "MYS", "language": "en", "feeds": [FEED]}


def _previous_poll(conn, url, hours_ago):
    store.record_feed_health(conn, OUTLET["id"], url, True, None, 10)
    conn.execute("UPDATE feed_health SET last_ok=? WHERE feed_url=?", ((NOW - dt.timedelta(hours=hours_ago)).isoformat(), url))
    conn.commit()


def _discover(conn, monkeypatch, book, outlet=OUTLET):
    calls = []
    fetch = pages_of(book, calls)
    monkeypatch.setattr(fetch_feeds, "fetch_feed", fetch)
    monkeypatch.setattr(feeds_util, "fetch_feed", fetch)
    counts = fetch_feeds.run(conn, "t", outlets=[outlet], workers=1)
    return counts, calls


def _poll(conn, url=FEED):
    return conn.execute("SELECT polls, saturated, entries, new_items, missed_estimate FROM feed_polls WHERE feed_url=?",
                        (url,)).fetchone()


def test_a_paged_poll_that_reaches_back_is_not_saturated_and_keeps_every_item(conn, monkeypatch):
    _previous_poll(conn, FEED, 6)
    counts, calls = _discover(conn, monkeypatch, wp_book(6))
    assert calls == [FEED, FEED + "?paged=2", FEED + "?paged=3"]
    polls, sat, entries, new, missed = _poll(conn)
    assert (polls, sat, entries, new, missed) == (1, 0, 10, 30, 0.0)        # entries: the feed's window, its first page
    assert counts["feed_paging"] == {"feeds": 1, "pages": 2, "items": 20, "reached": 1, "stops": {"reached": 1}}
    assert conn.execute("SELECT last_entries FROM feed_health WHERE feed_url=?", (FEED,)).fetchone()[0] == 10


def test_a_paged_poll_cut_at_the_cap_estimates_only_the_gap_left(conn, monkeypatch):
    """The estimator over the whole window read: the rate times the gap from its oldest entry. Without the
    pages the same poll's estimate is larger by about the items the pages brought."""
    _previous_poll(conn, FEED, 40)
    book = wp_book(20)
    counts, calls = _discover(conn, monkeypatch, book)
    assert len(calls) == 1 + fp.AUTO_MAX_PAGES
    polls, sat, entries, new, missed = _poll(conn)
    assert sat == 1 and new == 60 and entries == 10
    first_only = fetch_feeds.saturation({"last_ok": (NOW - dt.timedelta(hours=40)).isoformat()}, 10, 10,
                                        [fetch_feeds._entry_time(e) for e in book[FEED]["entries"]])[1]
    assert 0 < missed < first_only and abs((first_only - missed) - 50) < 10


def test_paging_is_off_outside_a_discovery_run(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch_feeds, "fetch_feed", pages_of(wp_book(3), calls))
    out = fetch_feeds.poll_outlet(OUTLET)
    assert calls == [FEED] and "paging" not in out[0]["result"]


def test_a_paging_failure_leaves_the_first_page_and_the_outlet(conn, monkeypatch):
    _previous_poll(conn, FEED, 6)

    def broken(*a, **k):
        raise ValueError("boom")
    monkeypatch.setattr(fp, "read_back", broken)
    counts, calls = _discover(conn, monkeypatch, wp_book(3))
    assert counts["paging_errors"] == 1 and _poll(conn)[3] == 10 and counts["feeds_ok"] == 1


# --- the outlet's other feeds ---------------------------------------------------------------------------------------

SECTION = "https://wp.test/category/world/feed/"


def test_a_saturated_poll_is_scaled_by_what_a_section_feed_reaching_back_carries(conn, monkeypatch):
    """Utusan's main feed (no pages here) is saturated; its world section feed reaches back past the previous
    poll and carries 4 of the main feed's 10 items, so 60 percent of the loss is left."""
    outlet = dict(OUTLET, feeds=[FEED, SECTION])
    _previous_poll(conn, FEED, 8)
    _previous_poll(conn, SECTION, 8)
    main = page(1, 0.1)
    section = [main[i] for i in (0, 3, 6, 9)] + [E("https://wp.test/world-%d" % i, 3 + i) for i in range(8)]
    book = {FEED: ok(main), SECTION: ok(section)}
    _discover(conn, monkeypatch, book, outlet)
    plain = fetch_feeds.saturation({"last_ok": (NOW - dt.timedelta(hours=8)).isoformat()}, 10, 10,
                                   [fetch_feeds._entry_time(e) for e in main])[1]
    sat, missed = _poll(conn)[1], _poll(conn)[4]
    assert sat == 1 and missed == pytest.approx(plain * 0.6, abs=0.02)
    assert _poll(conn, SECTION)[1] == 0


def test_a_section_feed_that_does_not_reach_back_covers_nothing(conn, monkeypatch):
    outlet = dict(OUTLET, feeds=[FEED, SECTION])
    _previous_poll(conn, FEED, 8)
    _previous_poll(conn, SECTION, 8)
    main = page(1, 0.1)
    book = {FEED: ok(main), SECTION: ok([main[0], main[3]] + [E("https://wp.test/w-%d" % i, 3 + i * 0.2) for i in range(5)])}
    _discover(conn, monkeypatch, book, outlet)
    plain = fetch_feeds.saturation({"last_ok": (NOW - dt.timedelta(hours=8)).isoformat()}, 10, 10,
                                   [fetch_feeds._entry_time(e) for e in main])[1]
    assert _poll(conn)[4] == pytest.approx(plain, abs=0.02)


def test_cover_for_takes_only_feeds_that_reach_back():
    last = NOW - dt.timedelta(hours=4)
    srcs = [{"feed": "a", "oldest": NOW - dt.timedelta(hours=5), "hashes": {"1"}},
            {"feed": "b", "oldest": NOW - dt.timedelta(hours=1), "hashes": {"2"}},
            {"feed": "c", "oldest": None, "hashes": {"3"}},
            {"feed": "self", "oldest": NOW - dt.timedelta(hours=9), "hashes": {"4"}}]
    cover = fp.cover_for("self", last, srcs)
    assert cover["hashes"] == {"1"} and cover["oldest"] <= last
    assert fp.cover_for("self", last, srcs[1:3]) is None and fp.cover_for("self", None, srcs) is None
