"""Same text, many countries (pipeline/copies_map.py, docs/data/copies_map.json), on synthetic data.

One Xinhua text runs in three countries: twice at one US paper, at an Italian paper, on the AAP release
widget of two ACM mastheads (one placement in Australia) and on a release wire (the worldwide stratum,
never a country). A copy whose label is undetermined, in a fourth country, is left out. The text counts
three countries and four outlets, each copy counted once. Then the 30 day window, the alteration bands
(shown only while the measure is provisional or checked), headlines only where the site lists the
article, and the export writing the file.
"""
import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import config, copies_map, export, store

ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
TITLE = "China, EU agree to keep bilateral trade ties stable and expand cooperation"
OUTLETS = [
    {"id": "us_paper", "name": "US Paper", "country": "USA", "language": "en", "tier": "national", "active": True},
    {"id": "it_paper", "name": "Italian Paper", "country": "ITA", "language": "en", "tier": "national", "active": True},
    {"id": "au_one", "name": "ACM One", "country": "AUS", "language": "en", "tier": "regional", "active": True,
     "section_feeds": [{"type": "aap_widget"}]},
    {"id": "au_two", "name": "ACM Two", "country": "AUS", "language": "en", "tier": "regional", "active": True,
     "section_feeds": [{"type": "aap_widget"}]},
    {"id": "us_wire", "name": "A Release Wire", "country": "USA", "language": "en", "tier": "distribution_wire", "active": True},
    {"id": "gb_paper", "name": "British Paper", "country": "GBR", "language": "en", "tier": "national", "active": True},
]


def _day(days_ago: float) -> str:
    return (NOW - dt.timedelta(days=days_ago)).isoformat()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "COLLECTION_START", "2000-01-01")
    c = store.connect(tmp_path / "m.db")
    yield c
    c.close()


def _article(conn, url, outlet, country, days_ago=1.0, title=TITLE, sigs=("xinhua_dateline",), method="rules",
             route="wire_credit", group=None):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": country, "language": "en",
                                         "title": title, "status": "classified", "gate_relevant": 1})
    when = _day(days_ago)
    conn.execute("UPDATE articles SET discovered_at=?, published_at=?, day_of_record=?, dup_group_id=? WHERE id=?",
                 (when, when, when[:10], group, aid))
    store.insert_classification(conn, aid, method, "A", 1.0, signatures_fired=list(sigs), route=route)
    conn.commit()
    return aid


def _text(conn, it_days_ago=2.0):
    """The Xinhua text in three countries, and a second text in one country. Returns {name: id}."""
    ids = {}
    ids["us"] = _article(conn, "https://us.example/a", "us_paper", "USA", days_ago=3)
    g = ids["us"]
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (g, g))
    ids["us_again"] = _article(conn, "https://us.example/a-updated", "us_paper", "USA", days_ago=2.5, group=g)
    ids["it"] = _article(conn, "https://it.example/a", "it_paper", "ITA", days_ago=it_days_ago, group=g)
    ids["au1"] = _article(conn, "https://newsroom.aap.com.au/releases/abc?masthead=one", "au_one", "AUS", days_ago=2, group=g)
    ids["au2"] = _article(conn, "https://newsroom.aap.com.au/releases/abc?masthead=two", "au_two", "AUS", days_ago=2, group=g)
    ids["wire"] = _article(conn, "https://wire.example/a", "us_wire", "USA", days_ago=2, group=g, route="distribution_stamp")
    # Undetermined: a model label with no checkable marker (pipeline/model_only.py), in no count.
    ids["gb"] = _article(conn, "https://gb.example/a", "gb_paper", "GBR", days_ago=2, group=g, sigs=(), method="llm",
                         route="unattributed")
    ids["solo"] = _article(conn, "https://us.example/solo", "us_paper", "USA", days_ago=1, title="Another state text")
    return ids


def _window():
    end = (NOW.date() + dt.timedelta(days=1))
    return ((end - dt.timedelta(days=30)).isoformat(), end.isoformat())


def test_one_xinhua_text_in_three_countries_counts_each_copy_once(conn):
    ids = _text(conn)
    d = copies_map.build(conn, OUTLETS, window=_window(), shown_ids=set(), alteration_status="provisional")
    ranked = d["scopes"]["all_time"]["ranked"]
    assert len(ranked) == 1, ranked
    top = ranked[0]
    assert top == {"id": "t%d" % ids["us"], "countries": 3, "outlets": 4}
    t = d["texts"][top["id"]]
    assert t["agency"] == "xinhua"
    assert t["first_seen"] == _day(3)[:10]
    by = {(p["o"], p["c"]): p for p in t["placements"]}
    # The widget counts once in Australia, the US paper once (its earliest copy), the wire under WLD.
    assert set(by) == {("us_paper", "USA"), ("it_paper", "ITA"), ("aap_release_widget", "AUS"), ("us_wire", "WLD")}
    assert by[("us_paper", "USA")]["d"] == _day(3)[:10]
    assert ("gb_paper", "GBR") not in by
    assert d["outlets"]["aap_release_widget"]["shared_widget"] is True
    assert d["outlets"]["us_paper"]["name"] == "US Paper"
    s = d["scopes"]["all_time"]
    assert s["shared_texts"] == 1 and s["texts"] == 2
    assert s["countries"] == {"AUS": {"texts": 1, "placements": 1}, "ITA": {"texts": 1, "placements": 1},
                              "USA": {"texts": 1, "placements": 1}, "WLD": {"texts": 1, "placements": 1}}
    assert d["window"] == {"start": _window()[0], "end": _window()[1]}


def test_the_30_day_window_counts_only_copies_inside_it(conn):
    _text(conn, it_days_ago=45)
    d = copies_map.build(conn, OUTLETS, window=_window(), alteration_status="provisional")
    assert d["scopes"]["all_time"]["ranked"][0]["countries"] == 3
    last = d["scopes"]["last_30d"]["ranked"]
    assert last[0]["countries"] == 2 and last[0]["outlets"] == 3
    assert "ITA" not in d["scopes"]["last_30d"]["countries"]


def test_a_text_in_one_country_is_not_shared(conn):
    _article(conn, "https://us.example/x", "us_paper", "USA")
    _article(conn, "https://wire.example/x", "us_wire", "USA", group=None)
    d = copies_map.build(conn, OUTLETS, window=_window())
    assert d["scopes"]["all_time"]["ranked"] == [] and d["texts"] == {}


def test_bands_only_while_the_measure_is_provisional_or_checked(conn):
    ids = _text(conn)
    conn.execute("""INSERT INTO state_copies(copy_id, version, computed_at, original_kind, original_source, band, novel_share)
                    VALUES (?, '2', ?, 'none', NULL, 'light', 0.1)""", (ids["it"], store.utcnow()))
    conn.commit()
    for status, shown in (("provisional", True), ("checked", True), ("withheld", False), ("off", False), (None, False)):
        d = copies_map.build(conn, OUTLETS, window=_window(), alteration_status=status)
        t = d["texts"]["t%d" % ids["us"]]
        it = next(p for p in t["placements"] if p["c"] == "ITA")
        assert ("b" in it) is shown, status
        assert d["alteration"]["shown"] is shown and d["alteration"]["status"] == (status or "off")


def test_copies_paired_with_one_reference_original_are_one_text(conn):
    a = _article(conn, "https://us.example/r", "us_paper", "USA", title="One headline")
    b = _article(conn, "https://it.example/r", "it_paper", "ITA", title="A different headline")
    for aid in (a, b):
        conn.execute("""INSERT INTO state_copies(copy_id, version, computed_at, original_kind, ref_id, original_source,
                            original_published_at) VALUES (?, '2', ?, 'reference', 7, 'ref_cgtn_en', ?)""",
                     (aid, store.utcnow(), _day(4)))
    conn.commit()
    d = copies_map.build(conn, OUTLETS, window=_window())
    top = d["scopes"]["all_time"]["ranked"]
    assert len(top) == 1 and top[0]["countries"] == 2
    t = d["texts"][top[0]["id"]]
    # Both copies name Xinhua in their signatures; the reference original is CGTN's page. Most copies win.
    assert t["agency"] == "xinhua" and t["original"] == {"kind": "reference", "source": "ref_cgtn_en", "published": _day(4)[:10]}


def test_agency_falls_back_to_the_reference_publisher_and_to_not_named(conn):
    from collections import Counter
    assert copies_map.agency_of(Counter(), Counter({"cgtn": 2})) == "cgtn"
    assert copies_map.agency_of(Counter({"xinhua": 1, "cgtn": 1}), Counter({"cgtn": 1})) == "cgtn"
    assert copies_map.agency_of(Counter(), Counter()) == "unnamed"


def test_xinhua_silk_road_and_xinhua_daily_are_not_xinhua():
    assert copies_map.refine_credits({"xinhua"}, ["Xinhua Silk Road"], "Forum opens") == {"xinhua_silk_road"}
    assert copies_map.refine_credits({"xinhua"}, [], "Xinhua Silk Road: forum opens") == {"xinhua_silk_road"}
    assert copies_map.refine_credits({"xinhua"}, ["Xinhua Daily Media Group"], "Forum opens") == set()
    assert copies_map.refine_credits({"xinhua"}, [], "t", ["xinhua_dateline"]) == {"xinhua"}
    assert copies_map.refine_credits({"xinhua", "cgtn"}, ["Xinhua News Agency"], "t") == {"xinhua", "cgtn"}


def test_a_release_with_no_named_publisher_is_a_release_by_a_state_body(conn):
    for url, outlet, country in (("https://us.example/rel", "us_paper", "USA"), ("https://it.example/rel", "it_paper", "ITA")):
        _article(conn, url, outlet, country, sigs=(), route="distribution_stamp", title="Forum opens in Nanjing")
    g = conn.execute("SELECT MIN(id) FROM articles").fetchone()[0]
    conn.execute("UPDATE articles SET dup_group_id=?", (g,))
    conn.commit()
    d = copies_map.build(conn, OUTLETS, window=_window())
    t = d["texts"][d["scopes"]["all_time"]["ranked"][0]["id"]]
    assert t["agency"] == "release" and d["agencies"]["release"] == "Release by a Chinese state body"


def test_headlines_only_where_the_site_lists_the_article(conn):
    ids = _text(conn)
    d = copies_map.build(conn, OUTLETS, window=_window(), shown_ids=set())
    assert d["texts"]["t%d" % ids["us"]]["headline"] is None
    d = copies_map.build(conn, OUTLETS, window=_window(), shown_ids={ids["it"]})
    assert d["texts"]["t%d" % ids["us"]]["headline"] == TITLE
    payload = json.dumps(d)
    assert "http" not in payload and "example" not in payload


def test_export_writes_the_side_file(tmp_path, monkeypatch, conn):
    db = tmp_path / "e.db"
    c = store.connect(db)
    monkeypatch.setattr(config, "DB_PATH", db)
    out = tmp_path / "out"
    export.run(c, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M.md")
    side = json.loads((out / "copies_map.json").read_text(encoding="utf-8"))
    assert side["version"] == copies_map.VERSION and side["scopes"]["all_time"]["ranked"] == []
    assert side["window"] and side["window"]["start"] < side["window"]["end"]
    assert side["alteration"]["bands"] == ["verbatim", "light", "moderate", "substantial"]
    c.close()


def test_a_failure_leaves_the_empty_structure(monkeypatch, conn):
    def boom(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(copies_map, "build", boom)
    assert copies_map.build_export_quietly(conn, OUTLETS) == copies_map.empty()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_smoke():
    """The pure helpers of docs/copies_map.js (tests/smoke_copies_map.js)."""
    r = subprocess.run(["node", str(ROOT / "tests" / "smoke_copies_map.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_page_wiring():
    """The section sits after the themes and before What we found, starts hidden, has a list fallback, and the
    module is linked with the site's asset version and mounted from app.js."""
    import re
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    sec = re.search(r'<section id="copies-map".*?</section>', html, re.S).group(0)
    assert re.search(r'<section id="copies-map"[^>]*\shidden', sec)
    for anchor in ('id="cm-list"', 'id="cm-map"', 'id="cm-detail"', 'id="cm-scope"', 'id="cm-view"', 'id="cm-mode"'):
        assert anchor in sec, anchor
    assert html.index('<section id="themes"') < html.index('<section id="copies-map"') < html.index('<section id="overview"')
    v = re.search(r'app\.js\?v=(\d+)', html).group(1)
    assert 'src="copies_map.js?v=%s"' % v in html and 'href="copies_map.css?v=%s"' % v in html
    assert html.index("copies_map.js") < html.index("app.js?v=")
    assert "TrackerCopiesMap.mount(" in (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
