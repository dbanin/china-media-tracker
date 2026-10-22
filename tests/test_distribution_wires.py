"""A global press release wire is counted as the worldwide distribution stratum, WLD, and never
inside a country: not in the counts, not in the routes, the themes or the article list, and not in
any denominator. The stratum is part of the world total and is never the top country. The first cut
of this left the wires in the daily build's own loop and in the discovery denominators, so a route
filtered view of the United States came out larger than its unfiltered total; the second left them
out of every total, which hid 179 state origin releases in one 30 day window."""
import datetime as dt
import json

from pipeline import config, export, methodology, registry, store

OUTLETS = [
    {"id": "us_paper", "name": "Paper", "country": "USA", "language": "en", "feeds": ["https://paper.test/rss"], "tier": "national_daily", "active": True},
    {"id": "us_wire", "name": "Wire", "country": "USA", "language": "en", "feeds": ["https://wire.test/rss"], "tier": "distribution_wire", "active": True},
    {"id": "us_oldwire", "name": "Old Wire", "country": "USA", "language": "en", "feeds": ["https://old.test/rss"], "tier": "distribution_wire", "active": False, "inactive_reason": "dead"},
]


def _article(conn, url, outlet, category, route, day="2026-09-10"):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": "USA", "language": "en",
                                         "title": "China " + url, "status": "fetched", "gate_relevant": 1})
    conn.execute("UPDATE articles SET discovered_at=?, status='classified' WHERE id=?", (day + "T09:00:00+00:00", aid))
    store.insert_classification(conn, aid, "rules", category, 1.0, route=route)


def _seed(tmp_path, day="2026-09-10"):
    conn = store.connect(tmp_path / "w.db")
    _article(conn, "https://paper.test/1", "us_paper", "A", "wire_credit", day)
    for i in range(5):
        _article(conn, "https://wire.test/%d" % i, "us_wire", "A", "distribution_stamp", day)
    conn.execute("INSERT OR REPLACE INTO daily_outlet_discovery(date, outlet_id, country, discovered, gate_relevant) VALUES (?,'us_paper','USA',20,1)", (day,))
    conn.execute("INSERT OR REPLACE INTO daily_outlet_discovery(date, outlet_id, country, discovered, gate_relevant) VALUES (?,'us_wire','USA',900,5)", (day,))
    conn.commit()
    return conn


def test_wires_are_in_no_country_figure(tmp_path):
    conn = _seed(tmp_path)
    roll = export.rebuild_rollups(conn, OUTLETS)
    assert roll["distribution_wires"]["A"] == 5
    day = export.build_daily(conn, OUTLETS)["2026-09"]["days"]["2026-09-10"]
    usa = day["countries"]["USA"]
    assert usa["A"] == 1, "the wire's five items are not the United States' state origin"
    assert usa["disc"] == 20 and usa["tdisc"] == 20, "nor are its 900 releases part of the country's output"
    assert day["routes"]["USA"] == {"wire_credit": 1}, "a route filtered view must never exceed the total"
    articles = export.build_articles(conn, outlets=OUTLETS)
    assert [a["outlet_id"] for a in articles.get("USA", [])] == ["us_paper"]
    ids, _ = registry.top_outlets(OUTLETS)
    assert ids["USA"] == {"us_paper"}


def test_wires_land_in_the_worldwide_stratum(tmp_path):
    conn = _seed(tmp_path)
    roll = export.rebuild_rollups(conn, OUTLETS)
    day = export.build_daily(conn, OUTLETS)["2026-09"]["days"]["2026-09-10"]
    wld = day["countries"]["WLD"]
    assert wld["A"] == 5 and wld["cls"] == 5
    # the stratum's share of output denominator is what the wires themselves published, nothing else
    assert wld["disc"] == 900 and wld["tdisc"] == 900 and wld["ta"] == 5
    assert day["routes"]["WLD"] == {"distribution_stamp": 5}
    assert day["arrivals"]["WLD"] == {"editorial_feed": 5}
    assert "WLD" in day["themes"]
    assert roll["distribution_wires"]["A"] == wld["A"], "the old wires block and the stratum are one figure"
    ids, ranked = registry.top_outlets(OUTLETS)
    assert ids["WLD"] == {"us_wire"} and "WLD" not in ranked
    articles = export.build_articles(conn, outlets=OUTLETS)
    assert sorted(a["outlet_id"] for a in articles["WLD"]) == ["us_wire"] * 5


def test_latest_has_a_worldwide_entry_and_world_totals_include_it(tmp_path):
    conn = _seed(tmp_path)
    export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [], population={"USA": 300000000})
    wld = latest["countries"]["WLD"]
    assert wld["coverage"] == "monitored"
    assert wld["outlets_active"] == 1 and wld["outlets_total"] == 2, "the active wires, and every wire"
    assert wld["population"] is None and wld["language_support"] == "full"
    assert wld["feeds_total"] == 1 and wld["top_outlets"] == 1 and wld["top_outlets_ranked"] is False
    assert wld["all_time"]["A"] == 5 and wld["all_time"]["share_of_all_a"] == round(5 / 900, 5)
    assert wld["top_carrier"] is None, "the seeded day is outside the 30 day window"
    usa = latest["countries"]["USA"]
    assert usa["all_time"]["A"] == 1 and usa["outlets_total"] == 1 and usa["population"] == 300000000
    assert latest["totals"]["all_time"]["A"] == 6, "world totals include the stratum"
    assert latest["totals"]["population"] == 300000000
    assert registry.country_name("WLD") == "Worldwide distribution"


def test_top_carrier_of_the_stratum_is_a_wire(tmp_path):
    conn = _seed(tmp_path, _yesterday())   # the carriers count the 30 complete days, not the partial export day
    export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [], population={})
    pick = lambda d, *keys: {k: d[k] for k in keys}   # the entries also carry total, share and ties (export._leader)
    assert pick(latest["countries"]["WLD"]["top_carrier"], "id", "name", "count") == {"id": "us_wire", "name": "Wire", "count": 5}
    assert pick(latest["countries"]["WLD"]["top_route"], "id", "count") == {"id": "distribution_stamp", "count": 5}
    assert pick(latest["countries"]["USA"]["top_carrier"], "id", "name", "count") == {"id": "us_paper", "name": "Paper", "count": 1}


def _yesterday():
    """The last complete day: the findings' 30 day window leaves out the partial export day."""
    return (dt.date.fromisoformat(export._today()) - dt.timedelta(days=1)).isoformat()


def test_top_country_is_never_the_stratum(tmp_path):
    conn = _seed(tmp_path, _yesterday())   # the wire holds 5 state origin against the paper's 1
    export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [], population={})
    meta = export.build_meta(conn, OUTLETS, [], latest)
    top = meta["findings"]["top_country"]
    assert top["iso"] == "USA" and top["state_origin"] == 1
    assert top["share_of_world"] == round(1 / 6, 4), "the world the share is taken of includes the stratum"
    assert meta["findings"]["all_time"]["state_origin"] == 6
    assert meta["distribution_wires"]["counts"]["A"] == latest["countries"]["WLD"]["all_time"]["A"]
    assert meta["distribution_wires"]["stratum"] == "WLD"
    assert meta["countries_monitored"] == 1, "the stratum is not a monitored country"
    assert meta["outlets_active"] == 1, "nor are the wires monitored newsrooms"
    series = export.build_global_series(export.build_daily(conn, OUTLETS))
    assert series[-1]["A"] == 6 and series[-1]["countries_with_A"] == 1


def test_outlets_json_puts_wires_under_the_stratum(tmp_path):
    conn = _seed(tmp_path)
    out = {o["id"]: o for o in export.build_outlets(conn, OUTLETS)["outlets"]}
    assert out["us_wire"]["country"] == "WLD" and out["us_oldwire"]["country"] == "WLD"
    assert out["us_paper"]["country"] == "USA"
    assert OUTLETS[1]["country"] == "USA", "the registry itself is not rewritten"


def test_export_writes_the_stratum_articles_file(tmp_path, monkeypatch):
    conn = _seed(tmp_path)
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: OUTLETS)
    monkeypatch.setattr(registry, "load_gaps", lambda *a, **k: [])
    monkeypatch.setattr(registry, "load_population", lambda *a, **k: {})
    out = tmp_path / "out"
    export.run(conn, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M.md")
    arts = json.loads((out / "articles" / "WLD.json").read_text(encoding="utf-8"))
    assert len(arts) == 5 and all(a["outlet_id"] == "us_wire" for a in arts)
    assert json.loads((out / "meta.json").read_text(encoding="utf-8"))["schema_version"] == config.SCHEMA_VERSION >= 11
    text = (tmp_path / "M.md").read_text(encoding="utf-8")
    assert "Worldwide\ndistribution" in text or "Worldwide distribution" in text
    assert "never\nadded into any individual country's numbers" in text or "never added into any individual country's numbers" in text
    assert "us_wire, us_oldwire" in text or "us_oldwire, us_wire" in text
    assert "5 state origin" in text


def test_methodology_names_the_stratum():
    t = methodology.TEMPLATE
    assert '"Worldwide\ndistribution"' in t or '"Worldwide distribution"' in t
    assert "counted on their own" not in t
    assert "never\nattributed to the United States" in t or "never attributed to the United States" in t


# ---------------------------------------------------------------------------
# The site's Distribution toggle (both, country outlets, worldwide wires) reads the stratum's part
# of every world figure from the export, so it can subtract without a second export.
# ---------------------------------------------------------------------------

def test_global_series_carries_the_stratum_per_day(tmp_path):
    conn = _seed(tmp_path)
    export.rebuild_rollups(conn, OUTLETS)
    series = export.build_global_series(export.build_daily(conn, OUTLETS))
    day = [s for s in series if s["date"] == "2026-09-10"][0]
    assert day["A"] == 6, "the world total keeps the stratum"
    assert day["wld"] == {"A": 5, "B": 0, "C": 0, "N": 0, "M": 0, "rel": 5, "paywalled": 0, "pending": 0}
    assert day["A"] - day["wld"]["A"] == 1, "the country outlets alone are the difference"
    assert set(day["wld"]) == set(export.SERIES_KEYS)


def test_global_series_wld_is_zeros_without_the_stratum(tmp_path):
    conn = _seed(tmp_path)
    no_wires = [o for o in OUTLETS if o["tier"] != "distribution_wire"]
    export.rebuild_rollups(conn, no_wires)
    series = export.build_global_series(export.build_daily(conn, no_wires))
    day = [s for s in series if s["date"] == "2026-09-10"][0]
    assert "WLD" not in export.build_daily(conn, no_wires)["2026-09"]["days"]["2026-09-10"]["countries"]
    assert day["wld"] == {k: 0 for k in export.SERIES_KEYS}


def test_findings_carry_the_wires_part_of_every_total(tmp_path):
    conn = _seed(tmp_path, _yesterday())
    export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [], population={})
    f = export.build_meta(conn, OUTLETS, [], latest)["findings"]
    at = f["all_time"]
    assert at["state_origin"] == 6 and at["state_origin_wires"] == 5
    assert at["independent"] == 0 and at["independent_wires"] == 0
    assert at["china_total"] == 6 and at["china_total_wires"] == 5
    assert at["unchecked"] is None and at["unchecked_wires"] is None, "the model stage has not run, so unchecked is withheld on both sides"
    assert f["last_30d"]["state_origin"] == 6 and f["last_30d"]["state_origin_wires"] == 5
    assert f["last_30d"]["window_end"] == _yesterday()
    # what the toggle shows: country outlets is the difference, worldwide wires is the part
    assert at["state_origin"] - at["state_origin_wires"] == 1
    assert f["top_country"]["share_of_world"] == round(1 / 6, 4)
    assert round(f["top_country"]["state_origin"] / (f["last_30d"]["state_origin"] - f["last_30d"]["state_origin_wires"]), 4) == 1.0


def test_findings_wires_fields_are_zero_without_the_stratum(tmp_path):
    conn = _seed(tmp_path)
    no_wires = [o for o in OUTLETS if o["tier"] != "distribution_wire"]
    export.rebuild_rollups(conn, no_wires)
    latest = export.build_latest(conn, no_wires, [], population={})
    f = export.build_meta(conn, no_wires, [], latest)["findings"]
    assert "WLD" not in latest["countries"]
    assert f["all_time"]["state_origin_wires"] == 0 and f["all_time"]["china_total_wires"] == 0 and f["all_time"]["independent_wires"] == 0
    assert f["last_30d"]["state_origin_wires"] == 0


def test_findings_unchecked_wires_is_null_when_relay_not_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELAY_PROVISIONAL_DISPLAY", False)
    conn = _seed(tmp_path)
    aid = store.insert_discovered(conn, {"url": "https://wire.test/b", "outlet_id": "us_wire", "country": "USA", "language": "en",
                                         "title": "China relay", "status": "fetched", "gate_relevant": 1})
    store.insert_classification(conn, aid, "llm", "B", 0.7, evidence_quote="Xinhua said so.", model_version="test-model")
    conn.commit()
    export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [], population={})
    f = export.build_meta(conn, OUTLETS, [], latest)["findings"]
    assert export.relay_visible(conn) is False
    assert f["all_time"]["unchecked"] is None and f["all_time"]["unchecked_wires"] is None, "a withheld total is withheld on the wires' side too, or subtraction gives it back"


def test_schema_bump_names_the_toggle_fields():
    assert config.SCHEMA_VERSION >= 11
