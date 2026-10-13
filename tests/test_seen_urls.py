"""Pruned articles are remembered (store.seen_urls), so a feed that keeps listing one does not bring it
back, and count it among the items screened, after every prune (config.SEEN_URL_RETENTION_DAYS)."""
import datetime as dt
import gzip
import json

from pipeline import config, fetch_feeds, registry, relay, section_pages, store

UTC = dt.timezone.utc
FEED = "https://z.example/rss"
PR_FEED = "https://z.example/press/rss"
CHINA = "https://z.example/china-trade"
STORM = "https://z.example/storm"


def _entries(*links):
    titles = {CHINA: "China and Australia sign a trade deal", STORM: "Storms sweep across the coast overnight"}
    return [{"link": u, "title": titles[u], "summary": ""} for u in links]


def _poll(monkeypatch, conn, feeds):
    """One discovery pass over outlet au_z: feeds is [(feed_url, kind, links)]."""
    outlet = {"id": "au_z", "country": "AUS", "language": "en", "feeds": [f for f, _, _ in feeds]}
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": f, "kind": k, "result": {"ok": True, "error": None, "entries": _entries(*links)}}
        for f, k, links in feeds])
    monkeypatch.setattr(section_pages, "aap_start", lambda conn, outlets: None)
    for f, _, _ in feeds:     # polled before, so this is an ordinary poll and not a backfill read
        store.record_feed_health(conn, "au_z", f, True, None, 2)
    return fetch_feeds.run(conn, "t", outlets=[outlet], workers=1)


def _age(conn, url, days):
    """Move an article's discovery back `days`, with its outlet-day count, as if it had been found then."""
    h = store.url_hash(url)
    row = conn.execute("SELECT discovered_at, gate_relevant FROM articles WHERE url_hash=?", (h,)).fetchone()
    old = (dt.datetime.now(UTC) - dt.timedelta(days=days)).replace(microsecond=0)
    conn.execute("UPDATE articles SET discovered_at=? WHERE url_hash=?", (old.isoformat(), h))
    for table in ("daily_outlet_discovery", "daily_discovery"):
        conn.execute("UPDATE %s SET discovered=discovered-1, gate_relevant=gate_relevant-? WHERE date=?" % table,
                     (row["gate_relevant"], row["discovered_at"][:10]))
    store.record_discovery(conn, "AUS", bool(row["gate_relevant"]), "au_z", old.date().isoformat())
    conn.commit()
    return old.date().isoformat()


def _discovered(conn):
    return conn.execute("SELECT COALESCE(SUM(discovered), 0), COALESCE(SUM(gate_relevant), 0) "
                        "FROM daily_outlet_discovery").fetchone()[:]


def test_a_pruned_rejection_a_feed_still_lists_is_neither_inserted_nor_counted_again(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "a.db")
    first = _poll(monkeypatch, conn, [(FEED, registry.EDITORIAL, [CHINA, STORM])])
    assert first["items_new"] == 2 and _discovered(conn) == (2, 1)
    saturated = conn.execute("SELECT saturated FROM feed_polls WHERE feed_url=?", (FEED,)).fetchone()[0]
    old_day = _age(conn, STORM, 5)
    assert store.prune_gated_out(conn) == 1
    assert conn.execute("SELECT first_day, last_day, seen_again, relevant FROM seen_urls").fetchone()[:] == \
        (int(old_day.replace("-", "")), int(old_day.replace("-", "")), 0, 0)
    # Before 2026-10-12 the next poll inserted it again and counted it a second time.
    again = _poll(monkeypatch, conn, [(FEED, registry.EDITORIAL, [CHINA, STORM])])
    assert again["items_new"] == 0
    assert _discovered(conn) == (2, 1)
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE url_hash=?", (store.url_hash(STORM),)).fetchone()[0] == 0
    today = int(dt.datetime.now(UTC).date().isoformat().replace("-", ""))
    assert conn.execute("SELECT last_day, seen_again FROM seen_urls").fetchone()[:] == (today, 1)
    # The poll found nothing new, so it is not taken for a saturated one either.
    polls = conn.execute("SELECT polls, new_items, saturated FROM feed_polls WHERE feed_url=?", (FEED,)).fetchone()
    assert polls["polls"] == 2 and polls["new_items"] == 2 and polls["saturated"] == saturated


def test_a_pruned_rejection_that_returns_on_a_section_feed_is_taken_in_without_counting_it_again(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "b.db")
    _poll(monkeypatch, conn, [(FEED, registry.EDITORIAL, [STORM])])
    old_day = _age(conn, STORM, 6)
    assert store.prune_gated_out(conn) == 1
    counts = _poll(monkeypatch, conn, [(PR_FEED, "press_release", [STORM])])
    assert counts["readmitted_after_prune"] == 1 and counts["items_new"] == 0
    row = conn.execute("SELECT status, gate_relevant, day_of_record, feed_url FROM articles").fetchone()
    assert tuple(row) == ("queued", 1, old_day, PR_FEED)
    # Counted once, on the day it was first found, now as gate relevant there.
    assert {r["date"]: (r["discovered"], r["gate_relevant"]) for r in conn.execute(
        "SELECT date, discovered, gate_relevant FROM daily_outlet_discovery WHERE discovered>0")} == {old_day: (1, 1)}
    assert conn.execute("SELECT COUNT(*) FROM seen_urls").fetchone()[0] == 0


def test_seen_urls_forget_what_no_feed_lists_any_more(tmp_path):
    conn = store.connect(tmp_path / "c.db")
    today = dt.datetime.now(UTC).date()

    def day(n):
        return int((today - dt.timedelta(days=n)).isoformat().replace("-", ""))
    rows = [(1, day(200), day(config.SEEN_URL_RETENTION_DAYS - 1), 1),     # listed again lately: kept
            (2, day(200), day(config.SEEN_URL_RETENTION_DAYS + 1), 1),     # gone from its feed too long
            (3, day(config.SEEN_URL_UNSEEN_DAYS - 1), day(config.SEEN_URL_UNSEEN_DAYS - 1), 0),
            (4, day(config.SEEN_URL_UNSEEN_DAYS + 1), day(config.SEEN_URL_UNSEEN_DAYS + 1), 0)]
    conn.executemany("INSERT INTO seen_urls(h, first_day, last_day, seen_again) VALUES (?,?,?,?)", rows)
    assert store.expire_seen(conn) == 2
    assert [r[0] for r in conn.execute("SELECT h FROM seen_urls ORDER BY h")] == [1, 3]


def test_seen_key_takes_any_url_hash():
    h = store.url_hash("https://z.example/a")
    assert store.seen_key(h) == int(h[:15], 16) < 2 ** 60
    assert store.seen_key("h1") != store.seen_key("h2")


def test_the_relay_never_brings_back_a_handed_off_row_it_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COLLECTOR", "self_hosted")
    conn = store.connect(tmp_path / "relay.db")
    outlet = {"id": "au_z", "country": "AUS", "language": "en", "collector": "both", "feeds": [FEED]}
    item = {"url": CHINA, "outlet_id": "au_z", "country": "AUS", "language": "en", "status": "queued", "gate_relevant": 1}
    assert store.insert_discovered(conn, dict(item))
    conn.execute("UPDATE articles SET discovered_at='2020-01-01T00:00:00+00:00'")
    conn.commit()
    assert relay.prune_handed_off(conn, outlets=[outlet]) == 1
    assert conn.execute("SELECT relevant FROM seen_urls").fetchone()[0] == 1
    assert store.insert_discovered(conn, dict(item)) is None
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0


def _bundle(rows):
    lines = [{"_type": "bundle_meta", "built_at": store.utcnow(), "since": None, "window_days": 7}] + rows
    return gzip.compress("".join(json.dumps(r) + "\n" for r in lines).encode("utf-8"))


def test_ingest_does_not_count_a_shared_item_this_database_pruned(tmp_path, monkeypatch):
    """A feed both collectors poll: the runner found the item, counted it and pruned it; the relay's copy
    (found later, or found again after the relay's own prune) must not be inserted and counted again."""
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "main.db")
    outlet = {"id": "au_z", "country": "AUS", "language": "en", "collector": "both", "feeds": [FEED]}
    for url in (STORM, CHINA + "-2"):
        store.insert_discovered(conn, {"url": url, "outlet_id": "au_z", "country": "AUS", "language": "en",
                                       "status": "gated_out", "gate_relevant": 0})
        store.record_discovery(conn, "AUS", False, "au_z", "2026-10-01")
    conn.execute("UPDATE articles SET discovered_at='2026-10-01T06:00:00+00:00', day_of_record='2026-10-01'")
    conn.commit()
    assert store.prune_gated_out(conn) == 2
    now = store.utcnow()
    rows = [{"url": STORM, "url_hash": store.url_hash(STORM), "outlet_id": "au_z", "country": "AUS", "language": "en",
             "discovered_at": now, "status": "gated_out", "gate_relevant": 0, "gate_terms": "[]"},
            {"url": CHINA + "-2", "url_hash": store.url_hash(CHINA + "-2"), "outlet_id": "au_z", "country": "AUS",
             "language": "en", "discovered_at": now, "status": "queued", "gate_relevant": 1,
             "gate_terms": "[\"section:press_release\"]", "title": "Trade fair opens"}]
    counts = relay.ingest(conn, _bundle(rows), outlets=[outlet])
    assert counts["seen_before"] == 1 and counts["inserted"] == 1
    # One discovery each, on the day the runner found them; the relevant one now counts as relevant there.
    assert {r["date"]: (r["discovered"], r["gate_relevant"]) for r in conn.execute(
        "SELECT date, discovered, gate_relevant FROM daily_outlet_discovery WHERE discovered>0")} == {"2026-10-01": (2, 1)}
    assert conn.execute("SELECT day_of_record FROM articles").fetchone()[0] == "2026-10-01"
    again = relay.ingest(conn, _bundle(rows), outlets=[outlet])
    assert again["inserted"] == 0 and _discovered(conn) == (2, 1)


def test_methodology_says_pruned_addresses_are_remembered(tmp_path, monkeypatch):
    from tests.test_export import _rendered_methodology
    text = _rendered_methodology(tmp_path, monkeypatch)
    assert "remembered, for %d days after a\nfeed last listed it" % config.SEEN_URL_RETENTION_DAYS in text
    assert "or %d days after its discovery" % config.SEEN_URL_UNSEEN_DAYS in text
