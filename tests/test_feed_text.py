"""The feed's text as the body of an SRNA story whose page shows the subscriber notice (pipeline/feed_text.py).
The saved page and the feed paragraphs here are synthetic text in the agency's markup."""
import gzip
from pathlib import Path

import pytest

from pipeline import classify_rules as cr, config, extract, feed_text, feeds_util, fetch_articles, store

FIX = Path(__file__).parent / "fixtures" / "extraction"
URL = "https://www.srna.rs/en/novost/1449761/news"
FEED = "https://www.srna.rs/rss"
HEADLINE = "PALILU: TEBUV, XI AND SATEP SET ZOGILA FOR NEW GAPOD PAKAL"
NOTICE = "COMPLETE NEWS AVAILABLE TO SUBSCRIBERS OF THE SRNA AGENCY NEWS SERVICE"
REST = ["The minister said on Monday that the talks would continue next week in the capital, and that officials "
        "from the three governments had met for three hours before the announcement.",
        "He added that a working group would be set up by the end of the month and that its first report was "
        "expected in the spring, when the delegations are due to meet again.",
        "Asked about the trade agreement, he said that nothing had been signed and that the two sides were "
        "still far apart on tariffs."]


def _page(name="en_ba_srna_subscriber_notice"):
    with gzip.open(FIX / (name + ".html.gz"), "rt", encoding="utf-8") as fh:
        return fh.read()


HTML = _page()
OPENING = extract.subscriber_notice(HTML)[0]
TEASER = extract.extract(HTML, URL)["text"]
WHOLE = "\n".join([HEADLINE, OPENING] + REST)
DESCRIPTION = "\n\n".join([OPENING] + REST) + " /end/tp/sg"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "RAW_HTML_DIR", tmp_path / "raw")
    monkeypatch.setattr(feed_text, "_listed", {})
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


def _feed(monkeypatch, entries, fails=False):
    """Serve the feed from memory; returns the list of addresses asked for."""
    asked = []

    def fetch_feed(url):
        asked.append(url)
        if fails:
            raise OSError("no answer")
        return {"ok": True, "entries": entries, "status": 200, "error": None, "elapsed": 0.1}
    monkeypatch.setattr(feeds_util, "fetch_feed", fetch_feed)
    return asked


def _item(description=DESCRIPTION, link=URL):
    return {"title": HEADLINE, "link": link, "summary": description}


def _row(conn, url=URL, feed_url=FEED, title=HEADLINE):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "ba_srna", "country": "BIH", "language": "en",
                                         "title": title, "feed_url": feed_url, "status": "queued",
                                         "gate_relevant": 1})
    conn.commit()
    return aid


def _fetch(conn, tmp_path, monkeypatch, aid, html=HTML):
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (True, "no_robots"))
    monkeypatch.setattr(fetch_articles, "get_page", lambda url: (html, 200, None))
    return fetch_articles.process_article(lambda: store.connect(tmp_path / "t.db"), store.get_article(conn, aid))


def test_description_is_read_as_the_story_s_paragraphs_without_the_sign_off():
    assert feed_text.paragraphs(DESCRIPTION) == [OPENING] + REST
    assert feed_text.paragraphs("One  line\nof it.\n\n\nTwo &amp; more. /end/ab") == ["One line", "of it.", "Two & more."]
    assert feed_text.paragraphs("<p>One.</p><p>Two,<br/>three.</p>/end/x/y") == ["One.", "Two,", "three."]
    assert feed_text.paragraphs("Ends with a place /end of the road") == ["Ends with a place /end of the road"]
    assert feed_text.paragraphs(None) == [] and feed_text.paragraphs(" /end/tp") == []


def test_notice_is_no_paywall_only_for_a_body_that_goes_past_the_opening():
    assert extract.subscriber_notice(HTML) == (OPENING, NOTICE)
    assert TEASER == "\n".join([HEADLINE, OPENING, NOTICE])
    marked = (True, extract.SUBSCRIBER_NOTICE)
    assert extract.detect_paywall(HTML, TEASER) == marked
    assert extract.detect_paywall(HTML, None) == marked
    assert extract.detect_paywall(HTML, HEADLINE + "\n" + OPENING) == marked
    assert extract.detect_paywall(HTML, WHOLE + "\n" + NOTICE) == marked           # the notice is not the story
    assert extract.detect_paywall(HTML, "\n".join([HEADLINE] + REST)) == marked    # not this page's story
    assert extract.detect_paywall(HTML, WHOLE) == (False, None)
    assert extract.subscriber_notice(_page("en_ba_srna")) is None


def test_whole_story_is_the_headline_and_the_feed_s_paragraphs(conn, monkeypatch):
    asked = _feed(monkeypatch, [_item(), _item("Another story.\n\nIts second paragraph.", URL.replace("61", "62"))])
    row = store.get_article(conn, _row(conn))
    assert feed_text.whole_story(row, HTML, TEASER) == WHOLE
    assert feed_text.whole_story(row, HTML, TEASER) == WHOLE and asked == [FEED]   # the feed is asked once


@pytest.mark.parametrize("description", [
    OPENING,                                                    # the feed holds no more than the page shows
    "\n\n".join([OPENING.replace("new", "old")] + REST),        # the opening reads differently
    "\n\n".join(REST),                                          # another text under the same address
    "",
], ids=["no more than the page", "another opening", "another text", "empty"])
def test_feed_text_that_is_not_the_page_s_story_with_more_to_it_is_not_used(conn, monkeypatch, description):
    _feed(monkeypatch, [_item(description)])
    assert feed_text.whole_story(store.get_article(conn, _row(conn)), HTML, TEASER) is None


def test_whole_story_needs_the_notice_the_item_and_a_feed_of_the_same_site(conn, monkeypatch):
    asked = _feed(monkeypatch, [_item()])
    row = store.get_article(conn, _row(conn))
    assert feed_text.whole_story(row, _page("en_ba_srna"), TEASER) is None         # a page without the notice
    assert feed_text.whole_story(row, HTML, None) is None and asked == []
    other = store.get_article(conn, _row(conn, URL.replace("61", "63")))           # not in the feed any more
    assert feed_text.whole_story(other, HTML, TEASER) is None
    away = store.get_article(conn, _row(conn, URL.replace("61", "64"), feed_url="https://news.test/rss"))
    none = store.get_article(conn, _row(conn, URL.replace("61", "65"), feed_url=None))
    assert feed_text.whole_story(away, HTML, TEASER) is None and feed_text.whole_story(none, HTML, TEASER) is None
    assert asked == [FEED]                                                          # the other site's is never asked


def test_feed_that_does_not_answer_is_asked_once_and_changes_nothing(conn, monkeypatch):
    asked = _feed(monkeypatch, [_item()], fails=True)
    row = store.get_article(conn, _row(conn))
    assert feed_text.whole_story(row, HTML, TEASER) is None and feed_text.whole_story(row, HTML, TEASER) is None
    assert asked == [FEED]


def test_fetch_stores_the_feed_s_text_for_a_page_with_the_notice(conn, tmp_path, monkeypatch):
    _feed(monkeypatch, [_item()])
    aid = _row(conn)
    out = _fetch(conn, tmp_path, monkeypatch, aid)
    assert out["status"] == "fetched" and out["feed_text"] is True
    row = store.get_article(conn, aid)
    assert row["status"] == "fetched" and row["fail_reason"] is None and row["body_chars"] == len(WHOLE)
    assert store.load_body(row["url_hash"]) == WHOLE
    # The saved page still carries the notice. Classification reads it beside the body and must not take the
    # row for a paywalled one, nor put the opening back in place of the story.
    html = cr.saved_html(row)
    assert html and extract.SUBSCRIBER_NOTICE_RE.search(html)
    assert cr.unreadable_reason(conn, row, WHOLE, html) is None
    assert cr.reread_saved(conn, row, html) is None and store.load_body(row["url_hash"]) == WHOLE
    assert cr.classify_article(conn, row).get("outcome") != "unreadable"
    row = store.get_article(conn, aid)
    assert row["status"] != "paywalled" and store.load_body(row["url_hash"]) == WHOLE


def test_fetch_leaves_the_item_paywalled_when_the_feed_no_longer_lists_it(conn, tmp_path, monkeypatch):
    _feed(monkeypatch, [_item(link=URL.replace("61", "62"))])
    aid = _row(conn)
    out = _fetch(conn, tmp_path, monkeypatch, aid)
    assert out["status"] == "paywalled" and not out.get("feed_text")
    row = store.get_article(conn, aid)
    assert (row["status"], row["fail_reason"]) == ("paywalled", extract.SUBSCRIBER_NOTICE)


def test_fetch_of_a_page_without_the_notice_never_asks_the_feed(conn, tmp_path, monkeypatch):
    asked = _feed(monkeypatch, [_item()])
    aid = _row(conn, "https://www.srna.rs/en/novost/1449728/news", title="KAVE MARUMOMUBOPU FOR JESULE")
    out = _fetch(conn, tmp_path, monkeypatch, aid, html=_page("en_ba_srna"))
    assert out["status"] == "fetched" and not out.get("feed_text") and asked == []


# The later page re-read (fetch_articles.notice_reread_candidates): a story the feed no longer listed while
# its page showed the notice is read again some hours later, when the page shows the whole text.

def _lifted():
    """The same page some hours later: the notice gone and the rest of the story in its place."""
    m = extract.SUBSCRIBER_NOTICE_RE.search(HTML)
    end = HTML.index("</a>", m.end()) + len("</a>")
    return HTML[:m.start()] + '<p class="desc">%s</p>' % "<br><br>".join(REST) + HTML[end:]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "RAW_HTML_DIR", tmp_path / "raw")
    monkeypatch.setattr(feed_text, "_listed", {})
    db = tmp_path / "t.db"
    real_connect = store.connect
    monkeypatch.setattr(store, "connect", lambda path=db: real_connect(path))
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (True, "no_robots"))
    monkeypatch.setattr(fetch_articles, "_rate_wait", lambda domain: None)
    asked = []

    def serve(page):
        def get_page(url):
            asked.append(url)
            return page, 200, None
        monkeypatch.setattr(fetch_articles, "get_page", get_page)
    c = store.connect()
    yield c, asked, serve
    c.close()
    fetch_articles._release_fetch_keys()


def _held(conn, n, fetched_hours=5, attempts=1, reason=extract.SUBSCRIBER_NOTICE, gate=1, discovered_days=1):
    import datetime as dt

    def ago(**kw):
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**kw)).replace(microsecond=0).isoformat()
    aid = _row(conn, URL.replace("1449761", str(n)))
    store.update_article(conn, aid, status="paywalled", fail_reason=reason, fetch_attempts=attempts,
                         gate_relevant=gate, fetched_at=ago(hours=fetched_hours),
                         discovered_at=ago(days=discovered_days, hours=fetched_hours))
    conn.commit()
    return aid


def test_lifted_page_reads_whole_without_the_feed():
    text = extract.extract(_lifted(), URL)["text"]
    assert extract.detect_paywall(_lifted(), text) == (False, None)
    assert extract.one_line(text) == extract.one_line(WHOLE)


def test_stories_left_paywalled_by_the_notice_are_read_again_hours_later(env):
    conn = env[0]
    yes = _held(conn, 1)
    second = _held(conn, 2, attempts=2)
    _held(conn, 3, fetched_hours=1)                                   # too soon
    _held(conn, 4, attempts=fetch_articles.RETRY_ATTEMPT_LIMIT)       # no fetches left
    _held(conn, 5, reason="paywall marker: subscribe")                # another paywall: left alone
    _held(conn, 6, gate=0)
    _held(conn, 7, discovered_days=fetch_articles.RETRY_WINDOW_DAYS + 1)
    assert {r["id"] for r in fetch_articles.notice_reread_candidates(conn)} == {yes, second}
    assert {r["id"] for r in fetch_articles.retry_candidates(conn)} == {yes, second}


def test_fetch_reads_the_page_again_once_the_notice_is_gone(env, monkeypatch):
    conn, asked, serve = env
    _feed(monkeypatch, [_item(link=URL.replace("61", "62"))])          # the feed no longer lists it
    aid = _row(conn)
    serve(HTML)
    counts = fetch_articles.run(conn, "t", workers=1)
    row = store.get_article(conn, aid)
    assert (row["status"], row["fail_reason"], row["fetch_attempts"]) == ("paywalled", extract.SUBSCRIBER_NOTICE, 1)
    assert "notice_rereads" not in counts
    # The next run, within the hours: the page is not asked for.
    asked.clear()
    assert "notice_rereads" not in fetch_articles.run(conn, "t2", workers=1) and asked == []
    # Some hours later the page shows the whole text and is read whole.
    import datetime as dt
    store.update_article(conn, aid, fetched_at=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        hours=fetch_articles.NOTICE_REREAD_AFTER_HOURS, minutes=5)).isoformat())
    conn.commit()
    serve(_lifted())
    counts = fetch_articles.run(conn, "t3", workers=1)
    assert counts["notice_rereads"] == 1 and counts["fetched"] == 1 and asked == [URL]
    row = store.get_article(conn, aid)
    assert (row["status"], row["fail_reason"], row["fetch_attempts"]) == ("fetched", None, 2)
    assert extract.one_line(store.load_body(row["url_hash"])) == extract.one_line(WHOLE)
    assert fetch_articles.notice_reread_candidates(conn) == []


def test_page_that_still_shows_the_notice_is_paywalled_again_and_waits(env, monkeypatch):
    conn, asked, serve = env
    _feed(monkeypatch, [])
    aid = _held(conn, 1)
    serve(HTML)
    counts = fetch_articles.run(conn, "t", workers=1)
    row = store.get_article(conn, aid)
    assert counts["notice_rereads"] == 1 and len(asked) == 1
    assert (row["status"], row["fail_reason"], row["fetch_attempts"]) == ("paywalled", extract.SUBSCRIBER_NOTICE, 2)
    assert fetch_articles.notice_reread_candidates(conn) == []        # fetched just now
