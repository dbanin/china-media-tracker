"""One download per AAP release (fetch_articles.fetch_key, copy_from_sibling).

Each masthead keeps its own row for an AAP release, at the release's URL with a "masthead" parameter.
The fetch stage downloads the release once and gives every sibling row the same body and outcome;
a failed download is not copied, so the next sibling tries again.
"""
from pathlib import Path

import pytest

from pipeline import config, fetch_articles, store

FIXTURES = Path(__file__).parent / "fixtures"
RELEASE = "https://newsroom.aap.com.au/releases/cision20260908AE42174"
HOSTS = ["canberratimes.com.au", "newcastleherald.com.au", "illawarramercury.com.au", "examiner.com.au",
         "bendigoadvertiser.com.au", "thecourier.com.au", "bordermail.com.au", "standard.net.au",
         "dailyadvertiser.com.au", "theadvocate.com.au"]


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
    html = (FIXTURES / "aap_release.html").read_text()

    def serve(responses):
        def get_page(url):
            calls.append(url)
            r = responses[min(len(calls), len(responses)) - 1]
            return (html, 200, None) if r == "ok" else (None, 503, "http_503")
        monkeypatch.setattr(fetch_articles, "get_page", get_page)
    conn = store.connect()
    yield conn, calls, serve
    conn.close()
    fetch_articles._release_fetch_keys()


def _siblings(conn, key=RELEASE):
    ids = [store.insert_discovered(conn, {"url": "%s?masthead=%s" % (key, h), "outlet_id": "zz_aap_%d" % i,
                                          "country": "AUS", "language": "en", "title": "Xinhua Silk Road: marathon",
                                          "status": "queued", "gate_relevant": 1}) for i, h in enumerate(HOSTS)]
    conn.commit()
    return ids


def test_fetch_key_drops_only_the_masthead_parameter():
    fk = fetch_articles.fetch_key
    assert fk(RELEASE + "?masthead=canberratimes.com.au") == RELEASE
    assert fk(RELEASE + "?masthead=a.au&x=1") == RELEASE + "?x=1"
    assert fk(RELEASE) == RELEASE
    assert fk("https://www.canberratimes.com.au/story/1?masthead=a.au") is None


def test_ten_mastheads_one_request_ten_bodies(env):
    conn, calls, serve = env
    serve(["ok"])
    ids = _siblings(conn)
    # an unrelated article on another host is fetched as usual
    other = store.insert_discovered(conn, {"url": "https://f.test/1", "outlet_id": "zz_other", "country": "AUS",
                                           "language": "en", "title": "t", "status": "queued", "gate_relevant": 1})
    conn.commit()
    counts = fetch_articles.run(conn, "t", workers=1)
    assert [u for u in calls if "aap.com.au" in u] == [RELEASE + "?masthead=" + HOSTS[0]]
    assert counts["copied"] == 9 and counts["fetched"] == 11
    rows = [store.get_article(conn, i) for i in ids]
    assert {r["status"] for r in rows} == {"fetched"}
    assert len({r["body_hash"] for r in rows}) == 1 and rows[0]["body_hash"]
    bodies = [store.load_body(r["url_hash"]) for r in rows]
    assert all(b and "Shenyang Marathon" in b for b in bodies) and len(set(bodies)) == 1
    assert all(r["fetch_attempts"] == 1 and r["http_status"] == 200 for r in rows)
    assert conn.execute("SELECT COUNT(*) FROM body_fingerprints WHERE article_id IN (%s)" % ",".join("?" * 10),
                        ids).fetchone()[0] == 10
    assert store.get_article(conn, other)["fetch_attempts"] == 1


def test_a_failed_download_is_retried_by_the_next_sibling(env):
    conn, calls, serve = env
    serve(["fail", "ok"])
    ids = _siblings(conn)
    counts = fetch_articles.run(conn, "t", workers=1)
    assert len(calls) == 2 and counts["copied"] == 8
    rows = [store.get_article(conn, i) for i in ids]
    assert rows[0]["status"] == "failed" and rows[0]["fail_reason"] == "http_503" and rows[0]["body_hash"] is None
    assert {r["status"] for r in rows[1:]} == {"fetched"}
    # on a later run the failed sibling takes the saved body without a request
    conn.execute("UPDATE articles SET status='queued' WHERE id=?", (ids[0],))
    conn.commit()
    fetch_articles.run(conn, "t2", workers=1)
    assert len(calls) == 2
    first = store.get_article(conn, ids[0])
    assert first["status"] == "fetched" and first["body_hash"] == rows[1]["body_hash"] and first["fetch_attempts"] == 2


def test_outside_a_run_the_sibling_is_found_in_the_database(env):
    conn, calls, serve = env
    serve(["ok"])
    ids = _siblings(conn)[:3]
    out = fetch_articles.process_article(store.connect, store.get_article(conn, ids[0]))
    assert out["status"] == "fetched" and len(calls) == 1
    fetch_articles._release_fetch_keys()           # forget the run memory: the lookup goes to the table
    out = fetch_articles.process_article(store.connect, store.get_article(conn, ids[1]))
    assert out["copied_from"] == ids[0] and len(calls) == 1
    # a lender whose body file is gone lends nothing: the page is fetched again
    store.body_path(store.get_article(conn, ids[0])["url_hash"]).unlink()
    store.body_path(store.get_article(conn, ids[1])["url_hash"]).unlink()
    out = fetch_articles.process_article(store.connect, store.get_article(conn, ids[2]))
    assert "copied_from" not in out and out["status"] == "fetched" and len(calls) == 2
