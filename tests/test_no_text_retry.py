"""Further tries for pages that held no article text, and the rules that keep them few.

A page read as empty gets a second fetch when its outlet's pages are otherwise read
(second_attempt_candidates), and one more after a change to how every page is read
(second_chance_candidates). A challenge page is the site's refusal: recorded as such and never asked
for again, and a domain that keeps answering with one is left alone for the run.
"""
import datetime as dt

import pytest

from pipeline import config, extract, fetch_articles, store

PROSE = ("The minister said on Monday that the talks would continue next week in the capital. "
         "Officials from both countries are expected to sign the agreement before the end of the month. ") * 6
ARTICLE = "<html><head><title>Talks continue</title></head><body><article><p>%s</p><p>%s</p></article></body></html>" % (
    PROSE, PROSE)
EMPTY = "<html><head><title>Talks continue</title></head><body><div id='root'></div></body></html>"
CHALLENGE = ('<html><head><title></title><script src="https://servicepipe.tech/loaders/x.js"></script></head>'
             "<body><js-challenge-loader></js-challenge-loader></body></html>")


def _ago(**kw):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**kw)).replace(microsecond=0).isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "RAW_HTML_DIR", tmp_path / "raw")
    db = tmp_path / "t.db"
    real_connect = store.connect
    monkeypatch.setattr(store, "connect", lambda path=db: real_connect(path))
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (True, "no_robots"))
    monkeypatch.setattr(fetch_articles, "_rate_wait", lambda domain: None)
    calls = []

    def serve(page):
        def get_page(url):
            calls.append(url)
            return (page(url) if callable(page) else page), 200, None
        monkeypatch.setattr(fetch_articles, "get_page", get_page)
    conn = store.connect()
    yield conn, calls, serve
    conn.close()
    fetch_articles._release_fetch_keys()


def _row(conn, url, outlet="zz_a", status="failed", reason="extraction_empty", version=None, attempts=1,
         gate=1, discovered=None, fetched=None):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": "AUS", "language": "en",
                                         "title": "China talks continue", "status": status, "gate_relevant": gate})
    fields = {"discovered_at": discovered or _ago(days=1)}
    if status == "failed":
        fields.update(fail_reason=reason, extract_version=version, fetch_attempts=attempts,
                      fetched_at=fetched or _ago(hours=12))
    store.update_article(conn, aid, **fields)
    conn.commit()
    return aid


def _ids(rows):
    return {r["id"] for r in rows}


def test_second_attempt_is_for_recent_empty_pages_of_an_outlet_whose_pages_are_read(env):
    conn, _, _ = env
    now = config.EXTRACT_VERSION
    _row(conn, "https://a.test/read", status="fetched")
    yes = _row(conn, "https://a.test/1", version=now)
    menus = _row(conn, "https://a.test/2", version=now, reason="unreadable: no article text: 20 characters of running prose")
    _row(conn, "https://a.test/3", version=now, fetched=_ago(hours=1))                 # too soon
    _row(conn, "https://a.test/4", version=now, attempts=2)                           # had its second fetch
    _row(conn, "https://a.test/5", version=now, discovered=_ago(days=10))             # past the window
    _row(conn, "https://a.test/6", version=now, gate=0)
    _row(conn, "https://a.test/7", version=now, reason="unreadable: challenge page: Servicepipe")
    _row(conn, "https://a.test/8", version=now, reason="unreadable: consent wall")
    _row(conn, "https://a.test/9", version=now, reason="http_403")
    _row(conn, "https://b.test/1", outlet="zz_b", version=now)                        # none of its pages are read
    _row(conn, "https://c.test/old", outlet="zz_c", status="fetched", discovered=_ago(days=30))
    _row(conn, "https://c.test/1", outlet="zz_c", version=now)                        # none read in the window
    assert _ids(fetch_articles.second_attempt_candidates(conn)) == {yes, menus}
    assert _ids(fetch_articles.retry_candidates(conn)) == {yes, menus}


def test_second_chance_is_for_any_empty_page_read_before_the_general_change(env, monkeypatch):
    conn, _, _ = env
    monkeypatch.setattr(extract, "GENERAL_EXTRACTION_VERSION", "2026.11.2")
    monkeypatch.setattr(config, "EXTRACT_VERSION", "2026.12.1")       # a later change to one template
    monkeypatch.setattr(extract, "EXTRACTION_CHANGES", (("2026.12.1", "https://elsewhere.test/", "one template"),))
    old = _ago(days=60)
    unversioned = _row(conn, "https://b.test/1", outlet="zz_b", discovered=old)
    earlier = _row(conn, "https://b.test/2", outlet="zz_b", version="2026.11.1", discovered=old, attempts=2)
    menus = _row(conn, "https://b.test/3", outlet="zz_b", discovered=old,
                 reason="unreadable: no article text: 0 characters of running prose")
    _row(conn, "https://b.test/4", outlet="zz_b", version="2026.11.2", discovered=old)    # had the chance
    _row(conn, "https://b.test/5", outlet="zz_b", version="2026.12.1", discovered=old)
    _row(conn, "https://b.test/6", outlet="zz_b", discovered=old, attempts=3)
    _row(conn, "https://b.test/7", outlet="zz_b", discovered=old, gate=0)
    _row(conn, "https://b.test/8", outlet="zz_b", discovered=old, reason="http_403")
    _row(conn, "https://b.test/9", outlet="zz_b", discovered=old, reason="unreadable: challenge page: Client Challenge")
    assert _ids(fetch_articles.second_chance_candidates(conn)) == {unversioned, earlier, menus}
    assert _ids(fetch_articles.retry_candidates(conn)) == {unversioned, earlier, menus}


def test_a_page_under_two_rules_is_fetched_once(env):
    conn, calls, serve = env
    _row(conn, "https://a.test/read", status="fetched")
    aid = _row(conn, "https://a.test/1")          # recent, outlet read, and never versioned: both rules
    assert [r["id"] for r in fetch_articles.retry_candidates(conn)] == [aid]
    serve(ARTICLE)
    counts = fetch_articles.run(conn, "t", workers=1)
    assert calls == ["https://a.test/1"] and counts["fetched"] == 1 and counts["retried"] == 1
    row = store.get_article(conn, aid)
    assert row["status"] == "fetched" and row["fetch_attempts"] == 2 and row["extract_version"] == config.EXTRACT_VERSION


def test_a_page_empty_again_is_stamped_and_not_taken_a_third_time(env):
    conn, calls, serve = env
    _row(conn, "https://a.test/read", status="fetched")
    aid = _row(conn, "https://a.test/1")
    serve(EMPTY)
    counts = fetch_articles.run(conn, "t", workers=1)
    row = store.get_article(conn, aid)
    assert counts["failed"] == 1 and row["fail_reason"] == "extraction_empty"
    assert row["fetch_attempts"] == 2 and row["extract_version"] == config.EXTRACT_VERSION
    assert fetch_articles.retry_candidates(conn) == []


def test_a_challenge_page_is_recorded_as_one_at_any_length_and_is_final(env):
    conn, _, serve = env
    aid = _row(conn, "https://t.test/1", status="queued")
    serve(CHALLENGE)
    out = fetch_articles.process_article(store.connect, store.get_article(conn, aid))
    row = store.get_article(conn, aid)
    assert out["status"] == "failed" and out["reason"] == "unreadable: challenge page: Servicepipe"
    assert row["fail_reason"] == out["reason"] and row["extract_version"] == config.EXTRACT_VERSION
    assert fetch_articles.retry_candidates(conn) == []


def test_a_domain_answering_with_its_challenge_page_is_left_alone_for_the_run(env):
    conn, calls, serve = env
    fresh = _row(conn, "https://t.test/new", status="queued")
    old = [_row(conn, "https://t.test/%d" % i, discovered=_ago(days=40 + i)) for i in range(5)]
    other = _row(conn, "https://u.test/1", outlet="zz_u", discovered=_ago(days=40))
    serve(lambda url: CHALLENGE if "t.test" in url else ARTICLE)
    counts = fetch_articles.run(conn, "t", workers=1)
    asked = [u for u in calls if "t.test" in u]
    assert asked == ["https://t.test/new", "https://t.test/0", "https://t.test/1"]        # newest first, then no more
    assert counts["skipped_challenged"] == 3 and counts["fetched"] == 1
    assert store.get_article(conn, fresh)["fail_reason"].startswith("unreadable: challenge page")
    for aid in old[2:]:
        row = store.get_article(conn, aid)
        assert row["fetch_attempts"] == 1 and row["fail_reason"] == "extraction_empty"
        assert row["extract_version"] == config.EXTRACT_VERSION
    assert store.get_article(conn, other)["status"] == "fetched"
    assert fetch_articles.retry_candidates(conn) == []
    # The next run asks for new pages only: the refusal does not stop the site being asked at all.
    _row(conn, "https://t.test/newer", status="queued")
    del calls[:]
    fetch_articles.run(conn, "t2", workers=1)
    assert calls == ["https://t.test/newer"]


def test_second_chances_stop_on_a_domain_the_new_reading_does_not_read(env, monkeypatch):
    conn, calls, serve = env
    monkeypatch.setattr(fetch_articles, "NO_TEXT_STREAK", 4)
    old = [_row(conn, "https://s.test/%d" % i, discovered=_ago(days=40 + i)) for i in range(6)]
    serve(EMPTY)
    counts = fetch_articles.run(conn, "t", workers=1)
    assert len(calls) == 4 and counts["skipped_no_text"] == 2 and counts["skipped_challenged"] == 0
    assert all(store.get_article(conn, a)["extract_version"] == config.EXTRACT_VERSION for a in old)
    assert fetch_articles.retry_candidates(conn) == []


def test_a_read_page_starts_the_count_again(env, monkeypatch):
    conn, calls, serve = env
    monkeypatch.setattr(fetch_articles, "NO_TEXT_STREAK", 2)
    for i in range(5):
        _row(conn, "https://s.test/%d" % i, discovered=_ago(days=40 + i))
    serve(lambda url: ARTICLE if url.endswith("/1") else EMPTY)
    counts = fetch_articles.run(conn, "t", workers=1)
    assert calls == ["https://s.test/%d" % i for i in range(4)]         # empty, read, empty, empty, then stop
    assert counts["fetched"] == 1 and counts["skipped_no_text"] == 1


def test_version_comparison_is_by_number_not_by_text():
    assert extract.before_general_change(None) and extract.before_general_change("")
    assert extract.before_general_change("2026.9.3") and extract.before_general_change("2026.11.1")
    assert not extract.before_general_change(extract.GENERAL_EXTRACTION_VERSION)
    assert not extract.before_general_change("2027.1.1")
