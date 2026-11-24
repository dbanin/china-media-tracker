"""Item links that end in a template placeholder ("undefined"), and the rows stored under them.

Libero's old rss.xml listed beside items such as its "pillole" videos, under the same headline and with no
date, one to five entries at /news/<another id>/undefined/. The snapshot of 2026-11-24 held 129 such rows
(108 failed with 404, 21 gated out), counted in the discovered, gate relevant and failed figures as if each
were an item. The reader now drops such entries, and a one-time step deletes the stored rows and takes their
discovery off the day counts."""
import datetime as dt

import pytest

from pipeline import feeds_util, fetch_feeds, registry, section_pages, store


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(section_pages, "aap_start", lambda conn, outlets: None)
    c = store.connect(tmp_path / "b.db")
    yield c
    c.close()


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


LIBERO = (b'<?xml version="1.0"?><rss><channel>'
          b'<item><title>Cina, il razzo Zhuque-2E porta in orbita dieci satelliti</title>'
          b'<link>https://www.liberoquotidiano.it/news/49146852/undefined/</link></item>'
          b'<item><title>Cina, il razzo Zhuque-2E porta in orbita dieci satelliti</title>'
          b'<link>https://www.liberoquotidiano.it/news/49146532/undefined/</link></item>'
          b'<item><title>Cina, il razzo Zhuque-2E porta in orbita dieci satelliti</title>'
          b'<link>https://www.liberoquotidiano.it/news/pillole/49146469/cina-il-razzo-zhuque-2e-porta-in-orbita-dieci-satelliti/</link>'
          b'<pubDate>Tue, 15 Sep 2026 15:07:42 +0000</pubDate></item>'
          b'<item><title>Cina, la parola undefined in un titolo</title>'
          b'<link>https://www.liberoquotidiano.it/news/esteri/49146470/undefined-behaviour-in-cina/</link></item>'
          b'</channel></rss>')
ALL_BROKEN = (b'<?xml version="1.0"?><rss><channel>'
              b'<item><title>Cina uno</title><link>https://fp.test/news/1/undefined/</link></item>'
              b'<item><title>Cina due</title><link>https://fp.test/news/2/undefined</link></item>'
              b'</channel></rss>')


def test_broken_link_is_a_placeholder_as_the_last_segment_only():
    assert feeds_util.broken_link("https://www.liberoquotidiano.it/news/49146852/undefined/")
    assert feeds_util.broken_link("https://fp.test/news/2/undefined")
    assert feeds_util.broken_link(" https://fp.test/undefined/ ")
    assert not feeds_util.broken_link("https://fp.test/news/undefined-behaviour/")
    assert not feeds_util.broken_link("https://fp.test/undefined/news/7")
    assert not feeds_util.broken_link("https://fp.test/news/7?x=undefined")
    assert not feeds_util.broken_link("https://fp.test/news/Undefined/")
    assert not feeds_util.broken_link("https://fp.test/")
    assert not feeds_util.broken_link(None) and not feeds_util.broken_link(42)


def test_the_reader_drops_placeholder_entries_and_keeps_the_item(monkeypatch):
    _serve(monkeypatch, {"https://www.liberoquotidiano.it/rss.xml": LIBERO})
    r = feeds_util.fetch_feed("https://www.liberoquotidiano.it/rss.xml")
    assert r["ok"] and [e["link"] for e in r["entries"]] == [
        "https://www.liberoquotidiano.it/news/pillole/49146469/cina-il-razzo-zhuque-2e-porta-in-orbita-dieci-satelliti/",
        "https://www.liberoquotidiano.it/news/esteri/49146470/undefined-behaviour-in-cina/"]


def test_a_feed_of_placeholder_links_alone_is_a_failed_poll(monkeypatch):
    _serve(monkeypatch, {"https://fp.test/rss": ALL_BROKEN})
    r = feeds_util.fetch_feed("https://fp.test/rss")
    assert not r["ok"] and r["entries"] == [] and r["error"].startswith("no_entries")


def _run(conn, monkeypatch, feeds, outlet):
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": u, "kind": registry.EDITORIAL, "result": r} for u, r in feeds])
    return fetch_feeds.run(conn, "t", outlets=[outlet], workers=1)


def test_discovery_stores_the_item_once(conn, monkeypatch):
    _serve(monkeypatch, {"https://www.liberoquotidiano.it/rss.xml": LIBERO})
    r = feeds_util.fetch_feed("https://www.liberoquotidiano.it/rss.xml")
    outlet = {"id": "it_libero", "country": "ITA", "language": "it", "feeds": []}
    counts = _run(conn, monkeypatch, [("https://www.liberoquotidiano.it/rss.xml", r)], outlet)
    urls = [row[0] for row in conn.execute("SELECT url FROM articles")]
    assert counts["items_new"] == 2 and not any(feeds_util.broken_link(u) for u in urls)


DAY = "2026-09-15"


def _add(conn, url, status, relevant=1, body=None, outlet="it_libero", day_of_record=None, group=None):
    cur = conn.execute(
        "INSERT INTO articles(url, url_hash, outlet_id, country, language, discovered_at, status, fail_reason, "
        "http_status, gate_relevant, fetch_attempts, body_hash, day_of_record, dup_group_id, title) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (url, store.url_hash(url), outlet, "ITA", "it", DAY + "T17:46:29+00:00", status,
         "http_404" if status == "failed" else None, 404 if status == "failed" else None, relevant,
         1 if status == "failed" else 0, body, day_of_record, group, "Cina, il razzo Zhuque-2E"))
    return cur.lastrowid


def _count(conn, day, outlet="it_libero", discovered=0, relevant=0):
    for _ in range(discovered):
        store.record_discovery(conn, "ITA", False, outlet, day)
    for _ in range(relevant):
        store.record_discovery(conn, "ITA", True, outlet, day)


def test_stored_placeholder_rows_are_deleted_once_and_their_discovery_taken_off(conn, monkeypatch):
    first = _add(conn, "https://www.liberoquotidiano.it/news/49146852/undefined/", "failed")
    _add(conn, "https://www.liberoquotidiano.it/news/49146532/undefined/", "failed", group=first)
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (first, first))
    twin = _add(conn, "https://www.liberoquotidiano.it/news/pillole/49146469/cina-il-razzo/", "classified", group=first)
    _add(conn, "https://www.liberoquotidiano.it/news/49344938/undefined/", "gated_out", relevant=0)
    _add(conn, "https://www.liberoquotidiano.it/news/49000001/undefined/", "failed", day_of_record="2026-09-03")
    labelled = _add(conn, "https://fp.test/news/9/undefined", "classified")      # a label: kept
    with_body = _add(conn, "https://fp.test/news/8/undefined", "failed", body="abc")   # a body: kept
    reviewed = _add(conn, "https://fp.test/news/7/undefined", "failed")
    conn.execute("INSERT INTO human_reviews(article_id, reviewer, human_category, reviewed_at) VALUES (?,?,?,?)",
                 (reviewed, "r", "C", store.utcnow()))
    store.insert_classification(conn, labelled, "rules", "C", 0.75, ruleset_version="t")
    _add(conn, "https://fp.test/news/undefined-behaviour/", "failed")              # not a placeholder: kept
    _count(conn, DAY, discovered=1, relevant=9)     # the rows of the 15th, and one more item
    _count(conn, "2026-09-03", relevant=1)
    conn.commit()
    counts = _run(conn, monkeypatch, [], {"id": "it_libero", "country": "ITA", "language": "it", "feeds": []})
    assert counts["broken_addresses_dropped"] == {"deleted": 4, "gate_relevant": 3, "kept": 3}
    urls = {r[0] for r in conn.execute("SELECT url FROM articles")}
    assert not any("liberoquotidiano.it/news/49" in u for u in urls)
    assert len(urls) == 5
    od = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT date, discovered, gate_relevant FROM daily_outlet_discovery WHERE outlet_id='it_libero'")}
    assert od == {DAY: (7, 7), "2026-09-03": (0, 0)}
    dd = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT date, discovered, gate_relevant FROM daily_discovery")}
    assert dd == {DAY: (7, 7), "2026-09-03": (0, 0)}
    # the title group lives on in the twin, keyed by the deleted row's id
    assert conn.execute("SELECT dup_group_id FROM articles WHERE id=?", (twin,)).fetchone()[0] == first
    # once: a row stored later under such an address (an old database's relay, say) is left to the reader
    _add(conn, "https://www.liberoquotidiano.it/news/49999999/undefined/", "failed")
    conn.commit()
    again = _run(conn, monkeypatch, [], {"id": "it_libero", "country": "ITA", "language": "it", "feeds": []})
    assert "broken_addresses_dropped" not in again and "broken_addresses_drop_error" not in again


def test_a_database_without_such_rows_records_the_step_and_does_nothing(conn, monkeypatch):
    _add(conn, "https://fp.test/news/1/", "failed")
    conn.commit()
    counts = _run(conn, monkeypatch, [], {"id": "it_libero", "country": "ITA", "language": "it", "feeds": []})
    assert "broken_addresses_dropped" not in counts
    assert conn.execute("SELECT 1 FROM schema_migrations WHERE name='articles.url.broken_template_address'").fetchone()


def test_the_registry_reads_the_italpress_xinhua_video_feed_and_medianet_as_a_wire():
    outlets = {o["id"]: o for o in registry.load_outlets()}
    assert "https://www.italpress.com/category/video-news/video-xinhua/feed/" in outlets["it_italpress"]["feeds"]
    medianet = outlets["au_medianet"]
    assert registry.is_distribution_wire(medianet) and medianet["country"] == "AUS" and medianet["active"]
    # aap.com.au/aapreleases is the AAP listing's own set of releases (section_pages, 2026-11-24): never polled twice
    polled = [f["url"] for o in outlets.values() for f in registry.feed_entries(o)]
    assert not any("aap.com.au/aapreleases" in u for u in polled)
