"""Exposure (pipeline/exposure.py): where outlets placed state origin items. The discovery level from the
stored feed and the registry, the homepage reads of the collector step (polite, inside a fixed budget, off
while the measure is off), the join by canonical address, the measure through the gate, the site file and
the hand check. Report only: nothing here changes a label, a count or a denominator.
"""
import csv
import datetime as dt
import json
import time
from pathlib import Path

import jsonschema
import pytest

from pipeline import config, exposure as ex, measures, registry, store

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

OUTLETS = [
    {"id": "it_paper", "name": "Paper", "country": "ITA", "language": "it", "tier": "national_daily", "active": True,
     "feeds": ["https://www.paper.it/rss/world.xml"], "news_sitemaps": ["https://www.paper.it/sitemap-news.xml"],
     "section_feeds": [{"url": "https://www.paper.it/comunicati/", "kind": "press_release", "type": "page"}],
     "main_sections": ["https://www.paper.it/mondo/"]},
    {"id": "my_star", "name": "Star", "country": "MYS", "language": "en", "tier": "national_daily", "active": True,
     "feeds": ["https://star.my/feed"], "homepage": "https://www.star.my/",
     "release_sections": {"searched_on": "2026-10-01", "status": "found", "sections": [
         {"url": "https://star.my/partner/", "kind": "partner", "feed": "https://star.my/partner/feed"},
         {"url": "https://star.my/latest/", "kind": "sponsored", "rejected": True,
          "rejected_feed": "https://star.my/latest/feed", "reason": "the site-wide feed"}]}},
    {"id": "us_wire", "name": "Release Wire", "country": "USA", "language": "en", "tier": "distribution_wire",
     "active": True, "feeds": ["https://wire.example.com/rss"]},
]


def _article(conn, url, outlet, feed, discovered, category="A", route="wire_credit", method="rules", terms=None,
             title="Headline"):
    country = {"it_paper": "ITA", "my_star": "MYS", "us_wire": "USA"}[outlet]
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": country, "language": "it",
                                         "title": title, "gate_relevant": 1, "feed_url": feed, "gate_terms": terms})
    conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (discovered, aid))
    if category:
        store.insert_classification(conn, aid, method, category, 0.9, route=route if category == "A" else None)
    return aid


@pytest.fixture
def db(tmp_path):
    conn = store.connect(tmp_path / "x.db")
    t = "2026-11-24T09:00:00+00:00"
    _article(conn, "https://www.paper.it/mondo/cina-1", "it_paper", "https://paper.it/rss/world.xml/", t)   # feed_key match
    _article(conn, "https://www.paper.it/mondo/cina-2", "it_paper", "https://www.paper.it/sitemap-news.xml", t)
    _article(conn, "https://www.paper.it/comunicati/xinhua-3", "it_paper", "https://www.paper.it/comunicati/", t,
             route="distribution_stamp", terms=["section:press_release"])
    _article(conn, "https://star.my/partner/cgtn-4", "my_star", "https://star.my/partner/feed", t, route="distribution_stamp")
    _article(conn, "https://star.my/news/china-5", "my_star", "https://star.my/latest/feed", t)     # rejected: site-wide
    _article(conn, "https://star.my/sponsored/old-6", "my_star", "https://star.my/gone/feed", t,
             route="distribution_stamp", terms=["section:sponsored"])                               # feed left the registry
    _article(conn, "https://star.my/news/old-7", "my_star", "https://star.my/gone/feed", t, terms=["China"])
    _article(conn, "https://wire.example.com/release-8", "us_wire", "https://wire.example.com/rss", t,
             route="distribution_stamp")
    _article(conn, "https://www.paper.it/mondo/model-9", "it_paper", "https://www.paper.it/rss/world.xml", t,
             method="llm", route="unattributed")                                                   # undetermined
    _article(conn, "https://www.paper.it/mondo/indep-10", "it_paper", "https://www.paper.it/rss/world.xml", t,
             category="C")
    _article(conn, "https://www.paper.it/mondo/old-11", "it_paper", "https://www.paper.it/rss/world.xml",
             "2026-09-01T09:00:00+00:00")
    conn.commit()
    return conn


def _levels(db):
    return {p["url"].rsplit("/", 1)[-1]: (p["level"], p["kind"], p["basis"]) for p in ex.placement_rows(db, OUTLETS)}


def _yaml(tmp_path, enabled):
    p = tmp_path / "measures.yaml"
    p.write_text("- id: exposure\n  label: Exposure\n  module: pipeline.exposure\n  enabled: %s\n  min_inputs: 1\n"
                 "  budget_seconds: 60\n" % ("true" if enabled else "false"), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# The discovery level: no request
# ---------------------------------------------------------------------------

def test_discovery_levels_from_the_stored_feed_and_the_registry(db):
    lv = _levels(db)
    assert lv["cina-1"] == ("news_feed", None, "rss")
    assert lv["cina-2"] == ("news_feed", None, "news_sitemap")
    assert lv["xinhua-3"] == ("release_only", "press_release", "section_page")
    assert lv["cgtn-4"] == ("release_only", "partner", "section_rss")
    assert lv["china-5"] == ("news_feed", None, "site_wide_feed")        # a rejected section feed is the site's feed
    assert lv["old-6"] == ("release_only", "sponsored", "arrival_marker")
    assert lv["old-7"] == ("news_feed", None, "arrival_marker")
    assert lv["release-8"] == ("release_only", "distribution_wire", "distribution_wire")
    assert lv["old-11"] == ("news_feed", None, "rss")
    # undetermined labels and other categories are in no count
    assert "model-9" not in lv and "indep-10" not in lv
    rows = {p["url"].rsplit("/", 1)[-1]: p for p in ex.placement_rows(db, OUTLETS)}
    assert rows["release-8"]["country"] == registry.WORLDWIDE and rows["cina-1"]["country"] == "ITA"


def test_a_section_feed_flagged_by_the_flood_guard_is_still_a_release_section(db):
    # The keyword gate applied (no section marker) but the feed is a registered section: the registry decides.
    _article(db, "https://www.paper.it/comunicati/cina-12", "it_paper", "https://www.paper.it/comunicati/",
             "2026-11-24T09:00:00+00:00", route="distribution_stamp", terms=["Cina"])
    assert _levels(db)["cina-12"][0] == "release_only"


def test_shared_copies_count_once(db, monkeypatch):
    from pipeline import placements
    ids = {p["url"].rsplit("/", 1)[-1]: p["id"] for p in ex.placement_rows(db, OUTLETS)}
    monkeypatch.setattr(placements, "shadowed", lambda conn: {ids["cina-2"]})
    assert "cina-2" not in _levels(db) and len(_levels(db)) == len(ids) - 1


# ---------------------------------------------------------------------------
# Links on a page
# ---------------------------------------------------------------------------

def test_page_links_are_canonical_same_site_and_in_order():
    html = """<html><body>
      <a href="/">Home</a><a href="https://www.paper.it/">Home again</a>
      <a href="/mondo/cina-1?utm_source=x#top">A</a>
      <a href="https://paper.it/mondo/cina-1/">A again</a>
      <a href="https://other.com/mondo/z">elsewhere</a>
      <a href="/search?q=x">search</a><a href="/login">login</a><a href="mailto:x@y.z">mail</a>
      <a href="javascript:window.open('x')">widget</a>
      <a href="//www.paper.it/comunicati/xinhua-3">B</a>
      <a href="/mondo/">section</a></body></html>"""
    links = ex.page_links(html, "https://www.paper.it/")
    assert links == [store.canonical_url("https://www.paper.it/mondo/cina-1"),
                     store.canonical_url("https://www.paper.it/comunicati/xinhua-3"),
                     store.canonical_url("https://www.paper.it/mondo/")]
    assert store.url_hash(links[0]) == store.url_hash("https://www.paper.it/mondo/cina-1?utm_source=feed")
    assert ex.page_links("", "https://www.paper.it/") == [] and ex.page_links("\x00<<<", "https://x.it/") == []
    many = "".join('<a href="/a/%d">x</a>' % i for i in range(ex.MAX_LINKS + 50))
    assert len(ex.page_links(many, "https://www.paper.it/")) == ex.MAX_LINKS


def test_reads_upsert_first_and_last_seen_and_best_rank(db):
    page = {"outlet_id": "it_paper", "page_url": "https://www.paper.it/", "page_kind": "homepage"}
    a, b = "https://paper.it/mondo/cina-1", "https://paper.it/mondo/cina-2"
    ex.record_read(db, page, "2026-11-24T10:00:00+00:00", {"html": "x", "status": 200}, [b, a])
    ex.record_read(db, page, "2026-11-24T11:00:00+00:00", {"html": "x", "status": 200}, [a])
    ex.record_read(db, page, "2026-11-24T12:00:00+00:00", {"html": None, "status": 503, "error": "http_503"}, [])
    r = db.execute("SELECT * FROM exposure_sightings WHERE url_hash=?", (store.url_hash(a),)).fetchone()
    assert (r["first_seen"], r["last_seen"], r["reads"], r["best_rank"]) == (
        "2026-11-24T10:00:00+00:00", "2026-11-24T11:00:00+00:00", 2, 1)
    reads = db.execute("SELECT ok, http_status, error, links FROM exposure_reads ORDER BY id").fetchall()
    assert [tuple(x) for x in reads] == [(1, 200, None, 2), (1, 200, None, 1), (0, 503, "http_503", 0)]


def test_prune_keeps_matched_links_and_recent_unmatched_ones(db):
    page = {"outlet_id": "it_paper", "page_url": "https://www.paper.it/", "page_kind": "homepage"}
    old = (NOW - dt.timedelta(hours=ex.UNMATCHED_KEEP_HOURS + 1)).isoformat()
    ex.record_read(db, page, old, {"html": "x"}, ["https://paper.it/mondo/cina-1", "https://paper.it/nav/old"])
    ex.record_read(db, page, NOW.isoformat(), {"html": "x"}, ["https://paper.it/nav/new"])
    db.execute("INSERT INTO exposure_reads(outlet_id, page_kind, page_url, read_at, ok) VALUES ('it_paper','homepage','u',?,1)",
               ((NOW - dt.timedelta(days=ex.READS_KEEP_DAYS + 1)).isoformat(),))
    got = ex.prune(db, NOW)
    left = {r[0] for r in db.execute("SELECT url FROM exposure_sightings")}
    assert left == {store.canonical_url("https://paper.it/mondo/cina-1"), store.canonical_url("https://paper.it/nav/new")}
    assert got == {"sightings_pruned": 1, "reads_pruned": 1}


# ---------------------------------------------------------------------------
# The collector step
# ---------------------------------------------------------------------------

def test_pages_due(db):
    pages = ex.pages_due(db, OUTLETS, NOW)
    # the wire is never read; the Star's registry homepage wins; the Paper's homepage is its articles' host
    assert [(p["outlet_id"], p["page_kind"], p["page_url"]) for p in pages] == [
        ("it_paper", "homepage", "https://www.paper.it/"), ("my_star", "homepage", "https://www.star.my/"),
        ("it_paper", "section", "https://www.paper.it/mondo/")]
    # longest unread first
    ex.record_read(db, pages[0], NOW.isoformat(), {"html": "x"}, [])
    assert ex.pages_due(db, OUTLETS, NOW)[-1]["page_url"] == "https://www.paper.it/"
    # nothing carried in the lookback: nothing to read
    assert ex.pages_due(db, OUTLETS, NOW + dt.timedelta(days=ex.LOOKBACK_DAYS + 2)) == []


def test_an_outlet_seen_only_through_the_shared_widget_is_not_read(tmp_path):
    conn = store.connect(tmp_path / "w.db")
    from pipeline import fetch_articles
    prefix = fetch_articles.FETCH_KEY_RULES[0][0]
    aid = store.insert_discovered(conn, {"url": prefix + "x-1?masthead=paper.it", "outlet_id": "it_paper", "country": "ITA",
                                         "language": "it", "gate_relevant": 1, "feed_url": "https://www.paper.it/comunicati/"})
    conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (NOW.isoformat(), aid))
    store.insert_classification(conn, aid, "rules", "A", 0.9, route="distribution_stamp")
    assert ex.pages_due(conn, OUTLETS, NOW) == []


def _reader(calls, results=None, delay=0.0):
    def read(url, latest_start=None):
        calls.append((url, time.monotonic(), latest_start))
        if delay:
            time.sleep(delay)
        r = (results or {}).get(url)
        if isinstance(r, Exception):
            raise r
        return r or {"html": '<a href="/mondo/cina-1">A</a><a href="/comunicati/xinhua-3">B</a>', "status": 200, "error": None}
    return read


def test_collect_reads_each_page_once_and_records_failures(db):
    calls = []
    res = {"https://www.star.my/": {"html": None, "status": None, "error": "robots_disallowed"},
           "https://www.paper.it/mondo/": RuntimeError("boom")}
    out = ex.collect(db, time.monotonic() + 60, OUTLETS, NOW, reader=_reader(calls, res))
    assert sorted(c[0] for c in calls) == ["https://www.paper.it/", "https://www.paper.it/mondo/", "https://www.star.my/"]
    assert (out["pages_due"], out["read"], out["ok"], out["failed"]) == (3, 3, 1, 2)
    errs = {r["page_url"]: r["error"] for r in db.execute("SELECT page_url, error FROM exposure_reads")}
    assert errs["https://www.star.my/"] == "robots_disallowed" and errs["https://www.paper.it/mondo/"].startswith("exception")
    assert db.execute("SELECT COUNT(*) FROM exposure_sightings").fetchone()[0] == 2


def test_collect_starts_nothing_inside_the_reserve_and_never_waits_past_the_deadline(db, monkeypatch):
    calls = []
    t0 = time.monotonic()
    out = ex.collect(db, t0 + ex.READ_RESERVE_SECONDS - 1, OUTLETS, NOW, reader=_reader(calls))
    assert calls == [] and out["not_reached"] == 3 and out["read"] == 0
    # a read still running at the deadline is left, unrecorded, and collect returns at the deadline
    monkeypatch.setattr(ex, "READ_RESERVE_SECONDS", 0.5)
    calls = []
    t0 = time.monotonic()
    out = ex.collect(db, t0 + 0.8, OUTLETS, NOW, reader=_reader(calls, delay=1.5), workers=1)
    took = time.monotonic() - t0
    assert len(calls) == 1 and out["abandoned"] == 1 and out["read"] == 0 and out["not_reached"] == 2
    assert took < 1.3
    assert calls[0][2] == pytest.approx(t0 + 0.3, abs=0.05)   # the reader is told when to give up


def test_collect_skips_a_site_whose_polite_slot_is_not_due(db, monkeypatch):
    from pipeline import fetch_articles as fa
    monkeypatch.setattr(fa, "slot_in", lambda dom: 600.0 if "star.my" in dom else 0.0)
    calls = []
    out = ex.collect(db, time.monotonic() + 60, OUTLETS, NOW, reader=_reader(calls))
    assert "https://www.star.my/" not in [c[0] for c in calls] and out["skipped_slot"] == 1
    # the reader's own late check (robots.txt just read, a Crawl-delay learnt) skips without a record
    res = {"https://www.paper.it/": {"html": None, "status": None, "error": "slot_not_due", "skipped": True}}
    n = db.execute("SELECT COUNT(*) FROM exposure_reads").fetchone()[0]
    out = ex.collect(db, time.monotonic() + 60, OUTLETS, NOW, reader=_reader([], res))
    assert out["skipped_slot"] == 2 and db.execute("SELECT COUNT(*) FROM exposure_reads").fetchone()[0] == n + 1


def test_read_page_uses_the_trackers_own_helpers(monkeypatch):
    from pipeline import fetch_articles as fa
    seen = {}

    class Resp:
        status_code, headers, text, encoding, url = 200, {"Content-Type": "text/html"}, "<a href='/x/y'>x</a>", "utf-8", "https://p.it/"
    monkeypatch.setattr(fa, "can_fetch", lambda url: (True, "robots_allowed"))
    monkeypatch.setattr(fa, "_rate_wait", lambda dom: seen.setdefault("paced", dom))
    monkeypatch.setattr(fa, "slot_in", lambda dom: 0.0)
    monkeypatch.setattr(fa, "polite_get", lambda url, headers, timeout: seen.update(ua=headers["User-Agent"]) or Resp())
    r = ex.read_page("https://p.it/")
    assert r["html"] and seen == {"paced": "p.it", "ua": config.USER_AGENT}
    monkeypatch.setattr(fa, "can_fetch", lambda url: (False, "robots_disallowed"))
    assert ex.read_page("https://p.it/") == {"html": None, "status": None, "error": "robots_disallowed"}
    monkeypatch.setattr(fa, "can_fetch", lambda url: (True, "robots_allowed"))
    monkeypatch.setattr(fa, "slot_in", lambda dom: 600.0)
    assert ex.read_page("https://p.it/", latest_start=time.monotonic() + 30)["skipped"] is True


def test_off_as_shipped_makes_no_request(db, tmp_path, monkeypatch):
    spec = next(m for m in measures.load_config() if m["id"] == "exposure")
    assert spec["enabled"] is False and spec["module"] == "pipeline.exposure"
    assert ex.collector_on() is False
    calls = []
    assert ex.run_stage(db, time.time() + 600, reader=_reader(calls)) == {"skipped": "measure off"} and calls == []
    on = _yaml(tmp_path, True)
    assert ex.collector_on(on) is True
    monkeypatch.setattr(config, "COLLECTOR", "self_hosted")
    assert ex.collector_on(on) is False


def test_run_stage_keeps_inside_the_stage_deadline(db, tmp_path):
    on = _yaml(tmp_path, True)
    calls = []
    assert ex.run_stage(db, time.time() + 5, config_path=on, outlets=OUTLETS, now=NOW, reader=_reader(calls)) == \
        {"skipped": "no time left"} and calls == []
    out = ex.run_stage(db, time.time() + 600, config_path=on, outlets=OUTLETS, now=NOW, reader=_reader(calls))
    assert out["read"] == 3 and out["seconds"] < ex.STAGE_SECONDS
    assert all(c[2] <= time.monotonic() + ex.STAGE_SECONDS for c in calls)
    bad = ex.run_stage(db, time.time() + 600, config_path=on, outlets=OUTLETS, now=NOW, reader="not callable")
    assert "error" in bad or bad["failed"] == bad["read"]


def test_the_fetch_stage_runs_the_step_after_the_article_fetch(monkeypatch, tmp_path):
    from pipeline import run, fetch_articles, reference, exposure
    order = []
    monkeypatch.setattr(fetch_articles, "run", lambda conn, run_id, deadline=None, limit=None: order.append("fetch") or {})
    monkeypatch.setattr(run, "_planted_scan", lambda *a: order.append("planted"))
    monkeypatch.setattr(run, "_body_links", lambda *a: order.append("body_links"))
    monkeypatch.setattr(reference, "run_stage", lambda conn, stage, deadline: order.append("reference") or {})
    got = {}

    def stage(conn, deadline=None):
        order.append("exposure")
        got["deadline"] = deadline
        raise RuntimeError("broken")
    monkeypatch.setattr(exposure, "run_stage", stage)
    conn = store.connect(tmp_path / "r.db")
    deadline = time.time() + 1000
    counts = run.cmd_fetch(conn, "r", type("A", (), {"limit": None})(), deadline)
    assert order == ["fetch", "planted", "body_links", "exposure", "reference"]
    assert got["deadline"] == deadline - reference.FETCH_SLICE_SECONDS      # never into the reference slice
    assert counts["exposure"]["error"].startswith("RuntimeError")          # a failure never stops the run


# ---------------------------------------------------------------------------
# Joining reads to placements, the side file and the gate
# ---------------------------------------------------------------------------

def test_checked_and_on_homepage(db):
    home = {"outlet_id": "it_paper", "page_url": "https://www.paper.it/", "page_kind": "homepage"}
    # read 2 hours after discovery: cina-1 linked (under another form of its address), xinhua-3 not
    ex.record_read(db, home, "2026-11-24T11:00:00+00:00", {"html": "x"}, [store.canonical_url("http://paper.it/mondo/cina-1/")])
    sec = {"outlet_id": "it_paper", "page_url": "https://www.paper.it/mondo/", "page_kind": "section"}
    ex.record_read(db, sec, "2026-11-24T11:00:00+00:00", {"html": "x"}, ["https://paper.it/mondo/cina-2"])
    rows = {p["url"].rsplit("/", 1)[-1]: p for p in ex.assess_placements(db, OUTLETS)}
    assert rows["cina-1"]["checked"] and rows["cina-1"]["on_homepage"] and rows["cina-1"]["exposure"] == "homepage"
    assert rows["cina-2"]["on_section_page"] and rows["cina-2"]["exposure"] == "section_page"
    assert rows["xinhua-3"]["checked"] and not rows["xinhua-3"]["on_homepage"] and rows["xinhua-3"]["exposure"] == "release_only"
    assert not rows["old-11"]["checked"]                              # discovered long before any read
    assert not rows["cgtn-4"]["checked"]                              # its outlet's homepage was never read
    # a failed read checks nothing
    db.execute("UPDATE exposure_reads SET ok=0")
    assert not any(p["checked"] for p in ex.assess_placements(db, OUTLETS))


def test_check_window_edges():
    at = dt.datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    h = dt.timedelta(hours=1)
    assert ex._checked([at - ex.CHECK_BEFORE_HOURS * h], at)
    assert not ex._checked([at - (ex.CHECK_BEFORE_HOURS + 1) * h], at)
    assert ex._checked([at + ex.CHECK_AFTER_HOURS * h], at)
    assert not ex._checked([at + (ex.CHECK_AFTER_HOURS + 1) * h], at)
    assert not ex._checked([], at) and not ex._checked([at], None)


def test_export_shape_and_no_addresses(db):
    home = {"outlet_id": "it_paper", "page_url": "https://www.paper.it/", "page_kind": "homepage"}
    ex.record_read(db, home, "2026-11-24T10:00:00+00:00", {"html": "x"}, ["https://paper.it/comunicati/xinhua-3"])
    ex.record_read(db, home, "2026-11-24T12:00:00+00:00", {"html": "x"}, ["https://paper.it/comunicati/xinhua-3"])
    d = ex.build_export(db, {"outlets": OUTLETS})
    assert d["totals"]["placements"] == 9 and d["newsrooms"]["placements"] == 8
    assert d["newsrooms"]["release_only"] == 3 and d["newsrooms"]["news_feed"] == 5
    assert d["release_kinds"] == {"press_release": 1, "sponsored": 1, "partner": 1, "distribution_wire": 1}
    assert d["newsrooms"]["checked"] == 3 and d["newsrooms"]["on_homepage"] == 1
    assert d["newsrooms"]["release_only_checked"] == 1 and d["newsrooms"]["release_only_on_homepage"] == 1
    assert d["newsrooms"]["share_on_homepage"] == round(1 / 3, 4)
    assert d["homepage"]["median_hours_seen"] == 2.0 and d["homepage"]["seen_in_one_read"] == 0
    assert d["window"] == {"first": "2026-09-01", "last": "2026-11-24"}
    by = {r["id"]: r for r in d["by_country"]}
    assert by["WLD"]["release_only"] == 1 and by["ITA"]["placements"] == 4 and by["MYS"]["placements"] == 4
    text = json.dumps(d)
    assert "http" not in text and "Headline" not in text and "paper.it" not in text
    s = ex.meta_summary(db, {"outlets": OUTLETS})
    assert s["release_only"] == 3 and s["newsroom_placements"] == 8


def test_export_with_no_reads_and_an_empty_database(db, tmp_path):
    d = ex.build_export(db, {"outlets": OUTLETS})
    assert d["homepage"]["reads"] == 0 and d["newsrooms"]["checked"] == 0 and d["newsrooms"]["share_on_homepage"] is None
    empty = store.connect(tmp_path / "empty.db")
    e = ex.build_export(empty, {"outlets": OUTLETS})
    assert e["totals"]["placements"] == 0 and e["window"] == {"first": None, "last": None} and e["by_country"] == []
    assert ex.inputs_present(empty) == 0 and ex.inputs_present(db) == 9


def test_the_measure_through_the_gate(db, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "exposure.json").write_text("{}")
    blocks = measures.run_all_quietly(db, out_dir, {"outlets": OUTLETS}, config_path=_yaml(tmp_path, False),
                                      reviews_dir=tmp_path / "reviews")
    assert blocks["exposure"]["status"] == "off" and not (out_dir / "exposure.json").exists()
    blocks = measures.run_all_quietly(db, out_dir, {"outlets": OUTLETS}, config_path=_yaml(tmp_path, True),
                                      reviews_dir=tmp_path / "reviews")
    b = blocks["exposure"]
    assert b["status"] == "provisional" and b["file"] == "exposure.json" and not b.get("error")
    assert json.loads((out_dir / "exposure.json").read_text())["newsrooms"]["placements"] == 8
    assert b["summary"]["release_only"] == 3 and "Runs come about once an hour" in b["method_text"]


def test_methodology_text_is_plain():
    t = ex.methodology_text({})
    assert "—" not in t and "–" not in t and "Category" not in t
    assert "less than an hour" in t and "lower bound" in t and "robots.txt" in t
    assert "%d seconds" % ex.STAGE_SECONDS in t and "%d hours after" % ex.CHECK_AFTER_HOURS in t
    assert len([p for p in t.split("\n\n") if p.strip()]) == 5


# ---------------------------------------------------------------------------
# The hand check
# ---------------------------------------------------------------------------

def test_hand_check_sample_is_blind_and_balanced(db, tmp_path):
    csv_path, key_path = ex.draw_sample(db, 4, "s", tmp_path, outlets=OUTLETS)
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    key = json.loads(Path(key_path).read_text())
    assert len(rows) == 4 and set(rows[0]) == set(ex.SAMPLE_FIELDS)
    assert "release_only" not in Path(csv_path).read_text() and "news_feed" not in Path(csv_path).read_text()
    assert sorted(key["answers"].values()) == ["news_feed", "news_feed", "release_only", "release_only"]
    assert not any("wire.example.com" in r["url"] for r in rows)       # wires are releases by construction
    assert key["measure_version"] == ex.VERSION
    again = ex.draw_sample(db, 4, "s", tmp_path / "b", outlets=OUTLETS)
    assert Path(again[0]).read_text() == Path(csv_path).read_text()     # seeded


def test_score_check():
    key = {"answers": {"x01": "release_only", "x02": "news_feed", "x03": "news_feed", "x04": "release_only"},
           "threshold": 0.8}
    rows = [{"sample_id": "x01", "where_placed": "release"}, {"sample_id": "x02", "where_placed": "News "},
            {"sample_id": "x03", "where_placed": "unclear"}, {"sample_id": "x04", "where_placed": ""}]
    r = ex.score_check(rows, key)
    assert (r["n"], r["coded"], r["decided"], r["value"], r["passed"], r["complete"]) == (4, 3, 2, 1.0, None, False)
    rows[3]["where_placed"] = "news"
    r = ex.score_check(rows, key)
    assert (r["coded"], r["value"], r["passed"]) == (4, round(2 / 3, 4), False)
    assert ex.score_check([], key)["value"] is None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_tables_on_a_new_and_an_old_database(tmp_path):
    conn = store.connect(tmp_path / "n.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"exposure_reads", "exposure_sightings"} <= tables
    assert "exposure_reads" not in store.MEASURE_TABLES
    conn.execute("DROP TABLE exposure_sightings")
    conn.commit()
    conn.close()
    conn = store.connect(tmp_path / "n.db")
    assert conn.execute("SELECT COUNT(*) FROM exposure_sightings").fetchone()[0] == 0


def test_the_registry_fields():
    schema = json.loads(config.OUTLETS_SCHEMA_PATH.read_text(encoding="utf-8"))
    ok = [dict(OUTLETS[0], tier="national_daily")]
    ok[0].pop("section_feeds")
    jsonschema.validate(ok, schema)
    for bad in ({"homepage": "paper.it"}, {"main_sections": []}, {"main_sections": ["ftp://x"]}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate([dict(ok[0], **bad)], schema)
    registry.load_outlets()      # the real registry still validates
