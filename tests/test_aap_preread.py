"""The AAP release listing is read once before the discovery workers start (section_pages.aap_preread).

The read may take at most AAP_PREREAD_SHARE of the time the stage has left. A read cut short there keeps
what it read, which the mastheads use, and records in aap_state the stretch still to cover; the next run
reads the new releases from page 1 and then resumes at the recorded page. A fake clock moves three
seconds a page (the per domain rate), and fake pages stand in for the listing.
"""
import datetime as dt
import threading

from pipeline import fetch_articles, fetch_feeds, registry, section_pages, store

UTC = dt.timezone.utc
T1 = dt.datetime(2026, 10, 10, 12, 0, tzinfo=UTC)       # the first run's read; release i was published i hours before
SINCE = T1 - dt.timedelta(hours=95, minutes=30)           # the backfill date: releases 0 to 95 are inside it
PAGE_SECONDS = 3.0


def _sydney(t):
    from zoneinfo import ZoneInfo
    return t.astimezone(ZoneInfo(section_pages.AAP_TZ)).strftime("%d/%m/%Y %H:%M")


def _page(releases):
    lis = "".join(
        '<li class="aapr-widget_release"><span class="aapr-widget_title"><a href="javascript:window.open('
        "window.location.href.split('?')[0] + '?rkey=%s', '_self');\">Release %s</a></span>"
        '<span class="aapr-widget_datetime"><p>Published: %s</p></span>'
        '<span class="aapr-widget_abstract"><p></p></span></li>' % (k, k, _sydney(t)) for k, t in releases)
    return "<div><ul>%s</ul></div>" % lis


class Listing:
    """The release service: `releases` newest first as (rkey, published), ten a page. Every request
    moves the fake clock on by PAGE_SECONDS and is recorded with the thread that made it."""

    def __init__(self, monkeypatch, releases, start):
        self.releases, self.now, self.calls, self.fail = list(releases), start, [], set()
        monkeypatch.setattr(section_pages, "_clock", lambda: self.now)
        monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (True, "no_robots"))
        monkeypatch.setattr(fetch_articles, "get_page", self.get_page)

    def get_page(self, url):
        n = int(url.rsplit("from=", 1)[1])
        self.now += PAGE_SECONDS
        self.calls.append((n, threading.current_thread().name))
        if n in self.fail:
            return None, 503, "http_503"
        return _page(self.releases[10 * (n - 1):10 * n]), 200, None

    def pages(self):
        return [n for n, _ in self.calls]


def _old(n=100):
    return [("o%d" % i, T1 - dt.timedelta(hours=i)) for i in range(n)]


_REGISTRY = []


def _registry():
    """The registry, parsed once for the module (parsing sources/outlets.yaml takes seconds)."""
    if not _REGISTRY:
        _REGISTRY.extend(registry.load_outlets())
    return _REGISTRY


def _outlets(*ids):
    by_id = {o["id"]: o for o in _registry()}
    out = []
    for oid in ids:
        host = by_id[oid]["feeds"][0].split("//www.", 1)[1].split("/", 1)[0]
        out.append(dict(by_id[oid], feeds=[], section_feeds=[
            {"url": "https://www.%s/press-releases" % host, "kind": "press_release", "type": "aap_widget"}]))
    return out


def _start(conn, monkeypatch, outlets):
    monkeypatch.setattr(section_pages, "AAP_BACKFILL_SINCE", SINCE.replace(tzinfo=None).isoformat())
    section_pages.aap_start(conn, outlets)


def _deliver(conn, outlets, last_ok=None):
    """What the discovery loop does with a masthead's successful poll, then aap_finish. last_ok then
    moves the poll onto the fake timeline, for the next run's aap_start."""
    for o in outlets:
        store.record_feed_health(conn, o["id"], o["section_feeds"][0]["url"], True, None, 10)
    section_pages.aap_finish(conn)
    if last_ok is not None:
        conn.execute("UPDATE feed_health SET last_ok=?", (last_ok.isoformat(),))


def _rkeys():
    return {r["rkey"] for r in section_pages._aap["listing"]["releases"]}


def test_a_preread_within_its_budget_reads_back_to_the_date_and_leaves_nothing(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "a.db")
    outlets = _outlets("au_canberratimes")
    site = Listing(monkeypatch, _old(), 1000.0)
    _start(conn, monkeypatch, outlets)
    out = section_pages.aap_preread(conn, deadline=1000.0 + 1000)   # 300 seconds for the read; it takes 30
    assert site.pages() == list(range(1, 11))                       # page 10 (releases 90 to 99) crosses the date
    assert _rkeys() == {"o%d" % i for i in range(96)}
    assert out["left_to_read_back_to"] is None and out["pages"] == 10 and out["seconds"] == 30.0
    _deliver(conn, outlets)
    assert section_pages.aap_gap(conn) is None
    assert conn.execute("SELECT COUNT(*) FROM aap_state").fetchone()[0] == 0


def test_a_preread_cut_short_keeps_what_it_read_and_the_next_run_resumes(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "b.db")
    outlets = _outlets("au_canberratimes", "au_examiner")
    site = Listing(monkeypatch, _old(), 1000.0)
    _start(conn, monkeypatch, outlets)
    # 40 seconds left: the read may take 12, so it reads pages 1 to 4 and stops before page 5.
    out = section_pages.aap_preread(conn, deadline=1000.0 + 40)
    assert site.pages() == [1, 2, 3, 4]
    assert out["left_to_read_back_to"] == SINCE.isoformat()
    assert _rkeys() == {"o%d" % i for i in range(40)}              # the mastheads get what was read
    first = section_pages.aap_entries(outlets[0]["section_feeds"][0]["url"])
    assert first["ok"] and len(first["entries"]) == 40
    _deliver(conn, outlets, last_ok=T1)
    assert section_pages.aap_gap(conn) == {"page": 4, "oldest": T1 - dt.timedelta(hours=39), "target": SINCE}

    # Fifteen hours later: fifteen new releases on top, so every old release sits a page and a half lower.
    site.releases = [("n%d" % h, T1 + dt.timedelta(hours=h)) for h in range(15, 0, -1)] + _old()
    site.calls = []
    _start(conn, monkeypatch, outlets)
    assert section_pages._aap["since"] == T1 - dt.timedelta(hours=section_pages.AAP_OVERLAP_HOURS)
    out = section_pages.aap_preread(conn, deadline=site.now + 1000)
    # Pages 1 to 3 back to the last poll less the overlap, then the stretch from page 4 to the date.
    assert site.pages() == [1, 2, 3] + list(range(4, 13))
    assert out["resumed_from_page"] == 4 and out["left_to_read_back_to"] is None
    second = _rkeys()
    assert {"n%d" % h for h in range(1, 16)} <= second
    assert {"o%d" % i for i in range(15, 96)} <= second and "o96" not in second
    _deliver(conn, outlets)
    assert section_pages.aap_gap(conn) is None


def test_a_resumed_read_cut_short_again_moves_the_record_on(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "c.db")
    outlets = _outlets("au_canberratimes")
    site = Listing(monkeypatch, _old(), 1000.0)
    _start(conn, monkeypatch, outlets)
    section_pages.aap_preread(conn, deadline=1000.0 + 40)           # pages 1 to 4
    _deliver(conn, outlets, last_ok=T1)
    site.calls = []
    _start(conn, monkeypatch, outlets)
    # 40 seconds again: page 1 (back to the last poll) and pages 4 to 6 of the stretch.
    section_pages.aap_preread(conn, deadline=site.now + 40)
    assert site.pages() == [1, 4, 5, 6]
    _deliver(conn, outlets, last_ok=T1)
    assert section_pages.aap_gap(conn) == {"page": 6, "oldest": T1 - dt.timedelta(hours=59), "target": SINCE}


def test_nothing_is_recorded_until_a_masthead_has_used_the_read(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "d.db")
    outlets = _outlets("au_canberratimes")
    Listing(monkeypatch, _old(), 1000.0)
    _start(conn, monkeypatch, outlets)
    section_pages.aap_preread(conn, deadline=1000.0 + 40)
    section_pages.aap_finish(conn)                                  # the run stopped before any masthead was polled
    assert section_pages.aap_gap(conn) is None
    _start(conn, monkeypatch, outlets)
    assert section_pages._aap["since"] == SINCE                     # so the next run reads it all again


def test_a_failed_page_leaves_the_stretch_for_the_next_run(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "e.db")
    outlets = _outlets("au_canberratimes")
    site = Listing(monkeypatch, _old(), 1000.0)
    _start(conn, monkeypatch, outlets)
    section_pages.aap_preread(conn, deadline=1000.0 + 40)
    _deliver(conn, outlets, last_ok=T1)
    gap = section_pages.aap_gap(conn)
    # The resumed stretch fails on its second page: the new releases still reach the mastheads,
    # the record stays where it was.
    site.releases = [("n1", T1 + dt.timedelta(hours=1))] + _old()
    site.fail = {5}
    _start(conn, monkeypatch, outlets)
    out = section_pages.aap_preread(conn, deadline=site.now + 1000)
    assert out["error"] == "http_503" and section_pages._aap["listing"]["ok"] and "n1" in _rkeys()
    _deliver(conn, outlets, last_ok=T1)
    assert section_pages.aap_gap(conn) == gap
    # A failure on page 1 fails the read for every masthead, as before.
    site.fail = {1}
    _start(conn, monkeypatch, outlets)
    section_pages.aap_preread(conn, deadline=site.now + 1000)
    r = section_pages.aap_entries(outlets[0]["section_feeds"][0]["url"])
    assert not r["ok"] and r["error"] == "http_503"
    assert section_pages.aap_gap(conn) == gap


def test_a_resumed_read_steps_back_when_releases_were_withdrawn(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "f.db")
    outlets = _outlets("au_canberratimes")
    site = Listing(monkeypatch, _old(), 1000.0)
    _start(conn, monkeypatch, outlets)
    section_pages.aap_preread(conn, deadline=1000.0 + 40)           # pages 1 to 4, oldest release o39
    _deliver(conn, outlets, last_ok=T1)
    # Twenty old releases withdrawn: page 4 now starts at o60, below o39, so releases 40 to 59 moved up.
    site.releases = [r for r in _old() if not 10 <= int(r[0][1:]) < 30]
    site.calls = []
    _start(conn, monkeypatch, outlets)
    section_pages.aap_preread(conn, deadline=site.now + 1000)
    assert site.pages()[:3] == [1, 4, 1]                            # page 4 is too low; back to page 1
    assert {"o%d" % i for i in range(40, 96)} <= _rkeys()


def test_the_workers_never_wait_on_the_listing(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "g.db")
    outlets = _outlets("au_canberratimes", "au_newcastleherald", "au_examiner")
    site = Listing(monkeypatch, _old(), 1000.0)
    monkeypatch.setattr(section_pages, "AAP_BACKFILL_SINCE", SINCE.replace(tzinfo=None).isoformat())
    seen = []
    poll = fetch_feeds.poll_outlet

    def watched(outlet):
        # By the time any worker polls, the listing has been read and the lock is free.
        seen.append((len(site.calls), section_pages._aap["listing"] is not None, section_pages._aap_lock.locked()))
        return poll(outlet)
    monkeypatch.setattr(fetch_feeds, "poll_outlet", watched)
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: [dict(o) for o in _registry()])
    counts = fetch_feeds.run(conn, "t", outlets=outlets, workers=3)
    assert len(seen) == 3 and all(n == 10 and read and not locked for n, read, locked in seen)
    assert {name for _, name in site.calls} == {threading.main_thread().name}
    assert counts["aap_listing"]["pages"] == 10 and counts["feeds_ok"] == 3
    assert conn.execute("SELECT COUNT(DISTINCT outlet_id) FROM articles").fetchone()[0] == 3
