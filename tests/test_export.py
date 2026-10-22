"""Schema validation for the generated JSON and an export run on an empty database."""
import datetime as dt
import json
import sqlite3
from pathlib import Path

import jsonschema
import pytest

from pipeline import config, export, store

COUNTS_KEYS = ["A", "B", "C", "N", "U", "Ar", "Al", "Ah", "Br", "Bl", "Bh", "rev", "cls", "disc", "rel", "fetched",
               "paywalled", "failed", "blocked", "pending", "uniqA", "uniqAB", "tdisc", "ttarget", "tchina", "ta", "tb"]
COUNTS_SCHEMA = {"type": "object", "required": COUNTS_KEYS, "properties": {k: {"type": "integer"} for k in COUNTS_KEYS}}
DERIVED_SCHEMA = {"type": "object", "required": COUNTS_KEYS + ["china_total", "share_ab", "share_a", "per_outlet_ab", "paywall_share", "reviewed_share", "share_of_all_target", "share_of_all_china"]}

LATEST_SCHEMA = {
    "type": "object", "required": ["generated_at", "countries", "totals", "window_start_30d"],
    "properties": {
        "countries": {"type": "object", "patternProperties": {"^[A-Z]{3}$": {
            "type": "object", "required": ["coverage", "outlets_total", "outlets_active", "feeds_total", "feeds_ok", "all_time", "last_30d",
                                           "warnings", "population", "top_outlets", "top_carrier", "top_route"],
            "properties": {"coverage": {"enum": ["monitored", "no_active_outlets", "gap"]}, "all_time": DERIVED_SCHEMA, "last_30d": DERIVED_SCHEMA,
                           "warnings": {"type": "array", "items": {"type": "object", "required": ["type", "text"]}}}}},
                      "additionalProperties": False},
        "totals": {"type": "object", "required": ["all_time", "last_30d"]},
    },
}
DAILY_SCHEMA = {
    "type": "object", "required": ["month", "days"],
    "properties": {"month": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"},
                   "days": {"type": "object", "patternProperties": {"^[0-9]{4}-[0-9]{2}-[0-9]{2}$": {
                       "type": "object", "required": ["countries", "reviewed", "llm_ceiling_hit"],
                       "properties": {"countries": {"type": "object", "additionalProperties": COUNTS_SCHEMA},
                                      "llm_ceiling_hit": {"type": "boolean"}}}}}},
}
FINDINGS_SCHEMA = {
    "type": "object", "required": ["since", "all_time", "last_30d", "top_country", "by_ownership", "generated_at"],
    "properties": {
        "all_time": {"type": "object", "required": ["state_origin", "unchecked", "independent", "china_total", "state_share_of_china",
                                                    "state_origin_wires", "unchecked_wires", "independent_wires", "china_total_wires"]},
        "last_30d": {"type": "object", "required": ["state_origin", "state_origin_wires"]},
        "by_ownership": {"type": "object", "required": ["groups", "top"],
                         "properties": {"groups": {"type": "array", "items": {"type": "object",
                                        "required": ["id", "articles", "outlets", "per_outlet_rate"]}}}},
    },
}
META_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "ruleset_version", "llm_model", "generated_at", "outlets_total", "outlets_active",
                 "countries_monitored", "countries_in_gaps", "gaps", "articles_classified", "articles_reviewed",
                 "review_coverage", "paywall_share", "paywall_flagged_countries", "kappa", "b_counts_settled",
                 "llm_ceiling_days", "categories", "first_discovered", "relay_provisional", "relay_study", "findings", "planted", "measures",
                 "undetermined"],
    "properties": {"b_counts_settled": {"type": "boolean"}, "relay_provisional": {"type": "boolean"},
                   "planted": {"type": "object", "required": ["flags_total", "flags_30d", "list_version", "personas", "fronts", "operations"]},
                   "relay_study": {"type": "object", "required": ["auto", "state", "sample"]}, "review_coverage": {"type": "number"},
                   "categories": {"type": "object", "required": ["A", "B", "C", "not_relevant"]},
                   "gate_accounting": {"type": "object", "required": ["total", "parts", "other_statuses", "copies_counted_once"]},
                   "findings": FINDINGS_SCHEMA,
                   "measures": {"type": "object", "additionalProperties": {"type": "object", "required": ["status", "label", "file", "enabled", "check"],
                                "properties": {"status": {"enum": ["off", "provisional", "checked", "withheld"]}}}}},
}
OUTLETS_SCHEMA = {
    "type": "object", "required": ["generated_at", "outlets"],
    "properties": {"outlets": {"type": "array", "items": {"type": "object", "required": ["id", "name", "country", "language", "tier", "active", "feeds", "counts"],
                                                          "properties": {"counts": COUNTS_SCHEMA, "feeds": {"type": "array", "items": {"type": "object", "required": ["url", "ok"]}}}}}},
}
ARTICLES_SCHEMA = {"type": "array", "items": {"type": "object", "required": ["id", "outlet_id", "title", "url", "date", "category", "provenance", "sources"],
                                              "properties": {"category": {"enum": ["A", "B", "C", "U", "pending"]}, "provenance": {"enum": ["rules", "llm", "human"]}}}}
EXAMPLE_ITEM_SCHEMA = {"type": "object", "required": ["id", "headline", "outlet_id", "outlet_name", "country", "url", "date", "tell"],
                       "properties": {"tell": {"type": "object", "required": ["snippet", "start", "end"]}}}
EXAMPLES_SCHEMA = {"type": "object", "required": ["generated_at", "A"],
                   "properties": {"A": {"type": "array", "items": EXAMPLE_ITEM_SCHEMA},
                                  "B": {"type": "array", "items": EXAMPLE_ITEM_SCHEMA}}}


def _load(p):
    return json.loads(Path(p).read_text())


def _committed_schema_current():
    p = config.EXPORT_DIR / "meta.json"
    if not p.exists():
        return False
    return _load(p).get("schema_version") == config.SCHEMA_VERSION


@pytest.mark.skipif(not (config.EXPORT_DIR / "latest.json").exists(), reason="no export yet")
@pytest.mark.skipif((config.EXPORT_DIR / "latest.json").exists() and not _committed_schema_current(),
                    reason="committed export predates the current data schema; the next export regenerates it")
def test_committed_export_validates():
    d = config.EXPORT_DIR
    jsonschema.validate(_load(d / "latest.json"), LATEST_SCHEMA)
    jsonschema.validate(_load(d / "meta.json"), META_SCHEMA)
    jsonschema.validate(_load(d / "outlets.json"), OUTLETS_SCHEMA)
    for f in (d / "daily").glob("*.json"):
        jsonschema.validate(_load(f), DAILY_SCHEMA)
    for f in (d / "articles").glob("*.json"):
        jsonschema.validate(_load(f), ARTICLES_SCHEMA)
    jsonschema.validate(_load(d / "examples.json"), EXAMPLES_SCHEMA)
    series = _load(d / "global_series.json")
    assert isinstance(series, list)
    assert all(set(["date", "A", "B", "C", "llm_ceiling_hit", "wld"]) <= set(s) for s in series)
    assert all(set(s["wld"]) == set(export.SERIES_KEYS) for s in series)


def test_export_on_empty_database(tmp_path, monkeypatch):
    db = tmp_path / "empty.db"
    conn = store.connect(db)
    out = tmp_path / "out"
    monkeypatch.setattr(config, "DB_PATH", db)
    counts = export.run(conn, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M.md")
    assert counts["articles"] == 0
    assert (tmp_path / "M.md").exists() and (out.parent / "METHODOLOGY.md").exists()
    latest = _load(out / "latest.json")
    jsonschema.validate(latest, LATEST_SCHEMA)
    meta = _load(out / "meta.json")
    jsonschema.validate(meta, META_SCHEMA)
    assert meta["b_counts_settled"] is False
    assert meta["findings"]["all_time"]["state_origin"] == 0
    assert meta["findings"]["top_country"] is None
    # every registered country appears even with no articles, so the map can show them as monitored with zero data
    assert set(latest["countries"]) >= {"ITA", "CAN", "AUS"}
    assert latest["countries"]["ITA"]["all_time"]["share_ab"] is None
    assert latest["countries"]["ITA"]["top_carrier"] is None
    examples = _load(out / "examples.json")
    jsonschema.validate(examples, EXAMPLES_SCHEMA)
    assert examples["A"] == []


def test_write_json_atomic_leaves_no_temp_file(tmp_path):
    """A browser reading docs/data/latest.json while an export runs must never see a half-written
    file: write_json must land the file in one rename, with no .tmp left beside it afterward."""
    path = tmp_path / "out" / "thing.json"
    export.write_json(path, {"b": [1, 2, 3], "a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2, 3]}
    assert list(path.parent.glob("*.tmp")) == []


def test_write_json_failure_leaves_target_and_no_temp(tmp_path):
    """If json serialisation blows up partway through, the previously published file must survive
    untouched and no stray temp file should be left behind."""
    path = tmp_path / "out" / "thing.json"
    export.write_json(path, {"a": 1})
    before = path.read_bytes()

    class Unserialisable:
        pass

    with pytest.raises(TypeError):
        export.write_json(path, {"bad": Unserialisable()})
    assert path.read_bytes() == before
    assert list(path.parent.glob("*.tmp")) == []


def test_generated_files_stay_out_of_the_repository(tmp_path, monkeypatch):
    """A test run must never rewrite METHODOLOGY.md or data/export in the working tree."""
    before = (config.ROOT / "METHODOLOGY.md").read_bytes() if (config.ROOT / "METHODOLOGY.md").exists() else None
    conn = store.connect(tmp_path / "x.db")
    export.run(conn, "test", export_dir=tmp_path / "o", audit_dir=tmp_path / "a", methodology_path=tmp_path / "m.md")
    after = (config.ROOT / "METHODOLOGY.md").read_bytes() if (config.ROOT / "METHODOLOGY.md").exists() else None
    assert before == after


def test_articles_newest_first_within_category(tmp_path):
    conn = store.connect(tmp_path / "n.db")
    for i, (cat, day) in enumerate([("C", "2026-09-01"), ("C", "2026-09-04"), ("A", "2026-09-02"), ("C", "2026-09-03")]):
        aid = store.insert_discovered(conn, {"url": "https://x.test/%d" % i, "outlet_id": "o", "country": "ITA", "language": "it",
                                             "title": "t%d" % i, "status": "fetched", "gate_relevant": 1, "published_at": day + "T10:00:00+00:00"})
        conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (day + "T12:00:00+00:00", aid))
        store.insert_classification(conn, aid, "rules", cat, 1.0)
    conn.commit()
    arts = export.build_articles(conn)["ITA"]
    assert [a["category"] for a in arts] == ["A", "C", "C", "C"]
    assert [a["date"] for a in arts if a["category"] == "C"] == ["2026-09-04", "2026-09-03", "2026-09-01"]


def test_discovered_totals_survive_pruning(tmp_path, monkeypatch):
    conn = store.connect(tmp_path / "p.db")
    store.insert_discovered(conn, {"url": "https://x.test/old", "outlet_id": "o", "country": "ITA", "language": "it",
                                   "title": "old", "status": "gated_out", "gate_relevant": 0})
    store.record_discovery(conn, "ITA", False)
    conn.execute("UPDATE articles SET discovered_at='2020-01-01T00:00:00+00:00'")
    conn.commit()
    assert store.prune_gated_out(conn) == 1
    assert conn.execute("SELECT SUM(discovered) FROM daily_discovery").fetchone()[0] == 1
    meta = export.build_meta(conn, [], [], export.build_latest(conn, [], []))
    assert meta["articles_discovered"] == 1


def test_share_of_all_items_uses_top_outlets(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    outlets = [{"id": "big", "name": "Big", "country": "ITA", "language": "it", "feeds": [], "tier": "national", "active": True, "audience_rank": 1},
               {"id": "small", "name": "Small", "country": "ITA", "language": "it", "feeds": [], "tier": "local", "active": True, "audience_rank": 40}]
    for i in range(60):
        store.record_discovery(conn, "ITA", False, "big")
    store.record_discovery(conn, "ITA", True, "small")
    aid = store.insert_discovered(conn, {"url": "https://x.test/a", "outlet_id": "big", "country": "ITA", "language": "it", "title": "t", "status": "fetched", "gate_relevant": 1})
    store.insert_classification(conn, aid, "rules", "A", 1.0)
    bid = store.insert_discovered(conn, {"url": "https://x.test/b", "outlet_id": "small", "country": "ITA", "language": "it", "title": "u", "status": "fetched", "gate_relevant": 1})
    store.insert_classification(conn, bid, "rules", "A", 1.0)
    conn.commit()
    import pipeline.registry as registry
    ids, ranked = registry.top_outlets(outlets)
    assert ids["ITA"] == {"big", "small"} and ranked == ["ITA"]
    outlets31 = outlets + [{"id": "o%d" % i, "name": "o", "country": "ITA", "language": "it", "feeds": [], "tier": "local", "active": True, "audience_rank": i + 2} for i in range(30)]
    ids, _ = registry.top_outlets(outlets31)
    assert "small" not in ids["ITA"] and len(ids["ITA"]) == 30
    export.rebuild_rollups(conn, outlets31)
    row = conn.execute("SELECT SUM(top_discovered) d, SUM(top_target) t, SUM(top_china) c FROM daily_coverage").fetchone()
    assert (row["d"], row["t"], row["c"]) == (60, 1, 1)
    assert conn.execute("SELECT SUM(top_a) FROM daily_coverage").fetchone()[0] == 1
    latest = export.build_latest(conn, outlets31, [], population={"ITA": 1000000})
    assert latest["countries"]["ITA"]["population"] == 1000000
    assert abs(latest["countries"]["ITA"]["all_time"]["share_of_all_target"] - 1 / 60) < 1e-4


def test_daily_theme_counts_and_catalog(tmp_path):
    conn = store.connect(tmp_path / "th.db")
    specs = [("Cina, vertice BRICS e dazi", "A", "fetched"), ("Cina, vertice a Pechino", "C", "fetched"),
             ("Un ristorante cinese apre a Roma", None, "awaiting_llm"), ("Cina, dazi sulle auto", "not_relevant", "fetched")]
    for i, (title, cat, status) in enumerate(specs):
        aid = store.insert_discovered(conn, {"url": "https://x.test/%d" % i, "outlet_id": "o", "country": "ITA", "language": "it",
                                             "title": title, "status": status, "gate_relevant": 1,
                                             "published_at": "2026-09-10T08:00:00+00:00"})
        conn.execute("UPDATE articles SET discovered_at='2026-09-10T09:00:00+00:00', status=? WHERE id=?", (status, aid))
        if cat:
            store.insert_classification(conn, aid, "rules", cat, 1.0)
            conn.execute("UPDATE articles SET status=? WHERE id=?", ("classified", aid))
    conn.commit()
    from pipeline import themes
    assert themes.ensure(conn) == 4
    daily = export.build_daily(conn)   # before any rollup rows exist: theme days must still appear
    assert "ITA" in daily["2026-09"]["days"]["2026-09-10"]["themes"]
    export.rebuild_rollups(conn, [])
    day = export.build_daily(conn)["2026-09"]["days"]["2026-09-10"]["themes"]["ITA"]
    # [all China coverage, target, state origin, unverified relay]; the not_relevant article never counts
    assert day["diplomacy"] == [2, 1, 1, 0]
    assert day["economy"] == [1, 1, 1, 0]
    assert day["other"] == [1, 1, 0, 0]
    meta = export.build_meta(conn, [], [], export.build_latest(conn, [], []))
    assert meta["themes"][0]["id"] == "diplomacy" and meta["themes"][-1]["id"] == "other" and meta["themes_version"]
    arts = export.build_articles(conn)["ITA"]
    assert all("themes" in a for a in arts)


def test_share_and_flags():
    e = export._derive({**export._empty_country(), "A": 2, "B": 1, "C": 7, "fetched": 5, "paywalled": 5}, 4)
    assert e["china_total"] == 10 and e["share_ab"] == 0.3 and e["per_outlet_ab"] == 0.75 and e["paywall_share"] == 0.5


# ---------------------------------------------------------------------------
# meta.findings, docs/data/examples.json, and per country top_carrier/top_route
# ---------------------------------------------------------------------------

def _outlets_for_findings():
    return [
        {"id": "it_big", "name": "Big Italian Outlet", "country": "ITA", "language": "it", "feeds": [],
         "tier": "national", "active": True, "ownership": "private"},
        {"id": "it_wire", "name": "A Release Wire", "country": "ITA", "language": "it", "feeds": [],
         "tier": "distribution_wire", "active": True, "ownership": "state"},
        {"id": "fr_state", "name": "French State Outlet", "country": "FRA", "language": "fr", "feeds": [],
         "tier": "national", "active": True, "ownership": "state"},
    ]


_seq = [0]


def _insert_state_origin(conn, outlet_id, country, day, evidence_quote=None, signatures_fired=None, route=None):
    _seq[0] += 1
    aid = store.insert_discovered(conn, {"url": "https://x.test/%s/%s/%d" % (outlet_id, day, _seq[0]), "outlet_id": outlet_id,
                                         "country": country, "language": "it", "title": "State origin story %s" % day,
                                         "status": "fetched", "gate_relevant": 1})
    conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (day + "T09:00:00+00:00", aid))
    store.insert_classification(conn, aid, "rules", "A", 1.0, evidence_quote=evidence_quote,
                                signatures_fired=signatures_fired or ["xinhua_dateline"], route=route or "wire_credit")
    conn.commit()
    return aid


def test_findings_all_time_totals_and_state_share(tmp_path):
    conn = store.connect(tmp_path / "f.db")
    _insert_state_origin(conn, "it_big", "ITA", "2026-09-10")
    aid = store.insert_discovered(conn, {"url": "https://x.test/c1", "outlet_id": "it_big", "country": "ITA",
                                         "language": "it", "title": "Independent story", "status": "fetched", "gate_relevant": 1})
    store.insert_classification(conn, aid, "rules", "C", 0.8)
    conn.commit()
    outlets = _outlets_for_findings()
    export.rebuild_rollups(conn, outlets)
    latest = export.build_latest(conn, outlets, [])
    meta = export.build_meta(conn, outlets, [], latest)
    f = meta["findings"]["all_time"]
    assert f["state_origin"] == 1 and f["independent"] == 1 and f["china_total"] == 2
    assert f["state_share_of_china"] == 0.5
    assert meta["findings"]["since"] == "2026-09-10"


def test_daily_files_through_the_last_complete_day_give_the_all_time_findings(tmp_path):
    """The site's all time cards and summary sum the daily files up to the last complete day, the day the
    map's All time view ends (allTimeInWindow in docs/compute.js), and fall back to findings.all_time. The
    two agree on every total and its wires part except for the partial export day, which only the
    export's totals hold."""
    conn = store.connect(tmp_path / "at.db")
    outlets = _outlets_for_findings()
    _insert_state_origin(conn, "it_big", "ITA", _days_ago(40))
    _insert_state_origin(conn, "it_wire", "ITA", _days_ago(2))
    _insert_state_origin(conn, "fr_state", "FRA", _days_ago(1))
    for day in (_days_ago(3), _days_ago(0)):
        aid = store.insert_discovered(conn, {"url": "https://x.test/ind/%s" % day, "outlet_id": "it_wire", "country": "ITA",
                                             "language": "it", "title": "Independent %s" % day, "status": "fetched", "gate_relevant": 1})
        conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (day + "T09:00:00+00:00", aid))
        store.insert_classification(conn, aid, "rules", "C", 0.8)
    for _ in range(3):
        _insert_state_origin(conn, "fr_state", "FRA", _days_ago(0))   # the partial export day
    conn.commit()
    export.rebuild_rollups(conn, outlets)
    latest = export.build_latest(conn, outlets, [])
    f = export.build_meta(conn, outlets, [], latest)["findings"]["all_time"]
    last_complete = _days_ago(1)
    total = {"A": 0, "B": 0, "C": 0}
    wires = {"A": 0, "B": 0, "C": 0}
    partial = {"A": 0, "B": 0, "C": 0}
    for m in export.build_daily(conn, outlets).values():
        for d, v in m["days"].items():
            for iso, k in v["countries"].items():
                for cat in total:
                    if d <= last_complete:
                        total[cat] += k[cat]
                        if iso == "WLD":
                            wires[cat] += k[cat]
                    else:
                        partial[cat] += k[cat]
    assert total == {"A": 3, "B": 0, "C": 1} and wires == {"A": 1, "B": 0, "C": 1} and partial == {"A": 3, "B": 0, "C": 1}
    assert f["state_origin"] == total["A"] + partial["A"] and f["independent"] == total["C"] + partial["C"]
    assert f["china_total"] == sum(total.values()) + sum(partial.values())
    assert f["state_origin_wires"] == wires["A"] and f["independent_wires"] == wires["C"] + partial["C"]
    assert f["china_total_wires"] == sum(wires.values()) + partial["C"]


def test_findings_unchecked_is_null_when_relay_not_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELAY_PROVISIONAL_DISPLAY", False)
    conn = store.connect(tmp_path / "r.db")
    aid = store.insert_discovered(conn, {"url": "https://x.test/b1", "outlet_id": "it_big", "country": "ITA",
                                         "language": "it", "title": "Relay story", "status": "fetched", "gate_relevant": 1})
    store.insert_classification(conn, aid, "llm", "B", 0.7, evidence_quote="Xinhua said so.", model_version="test-model")
    conn.commit()
    outlets = _outlets_for_findings()
    export.rebuild_rollups(conn, outlets)
    latest = export.build_latest(conn, outlets, [])
    meta = export.build_meta(conn, outlets, [], latest)
    assert export.relay_visible(conn) is False
    assert meta["findings"]["all_time"]["unchecked"] is None
    # the raw count is still on the totals the interface derives shares from, it is only the
    # published finding that is withheld
    assert latest["totals"]["all_time"]["B"] == 1


def test_findings_by_ownership_matches_lean_model_measure_a(tmp_path):
    conn = store.connect(tmp_path / "o.db")
    _insert_state_origin(conn, "it_big", "ITA", "2026-09-10")
    _insert_state_origin(conn, "it_big", "ITA", "2026-09-11")
    _insert_state_origin(conn, "fr_state", "FRA", "2026-09-10")
    outlets = _outlets_for_findings()
    export.rebuild_rollups(conn, outlets)
    latest = export.build_latest(conn, outlets, [])
    meta = export.build_meta(conn, outlets, [], latest)
    groups = {g["id"]: g for g in meta["findings"]["by_ownership"]["groups"]}
    # it_wire is a distribution wire and must not count here even though it is active
    assert groups["private"] == {"id": "private", "articles": 2, "outlets": 1, "per_outlet_rate": 2.0}
    assert groups["state"] == {"id": "state", "articles": 1, "outlets": 1, "per_outlet_rate": 1.0}
    assert meta["findings"]["by_ownership"]["top"]["id"] == "private"


def _days_ago(n):
    return (dt.date.fromisoformat(export._today()) - dt.timedelta(days=n)).isoformat()


def _pick(d, *keys):
    return {k: d[k] for k in keys} if d else d


def test_findings_top_country_last30_excludes_wires(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    yesterday = _days_ago(1)   # the export day itself is partial and outside the findings' window
    _insert_state_origin(conn, "it_big", "ITA", yesterday)
    _insert_state_origin(conn, "it_big", "ITA", yesterday)
    _insert_state_origin(conn, "it_wire", "ITA", yesterday)   # must not inflate ITA's top_country figures
    _insert_state_origin(conn, "fr_state", "FRA", yesterday)
    outlets = _outlets_for_findings()
    export.rebuild_rollups(conn, outlets)
    latest = export.build_latest(conn, outlets, [])
    meta = export.build_meta(conn, outlets, [], latest)
    top = meta["findings"]["top_country"]
    assert top["iso"] == "ITA" and top["state_origin"] == 2
    assert _pick(top["top_outlet"], "id", "name") == {"id": "it_big", "name": "Big Italian Outlet"}


def test_findings_top_country_counts_complete_days_only(tmp_path):
    """The site's map and ranked list open on the 30 complete days before the export day, so the top
    country card and the findings sentence count the same days. On 2026-10-12 the card said Italy 436
    while the map said 423: the 13 extra articles were the partial export day."""
    conn = store.connect(tmp_path / "p.db")
    outlets = _outlets_for_findings() + [
        {"id": "it_small", "name": "Small Italian Outlet", "country": "ITA", "language": "it", "feeds": [],
         "tier": "national", "active": True, "ownership": "private"}]
    _insert_state_origin(conn, "it_big", "ITA", _days_ago(30))   # the first day of the window
    _insert_state_origin(conn, "it_big", "ITA", _days_ago(1))
    _insert_state_origin(conn, "it_big", "ITA", _days_ago(1))
    _insert_state_origin(conn, "it_wire", "ITA", _days_ago(1))   # the stratum: in the world, never the country
    _insert_state_origin(conn, "fr_state", "FRA", _days_ago(1))
    for _ in range(5):
        _insert_state_origin(conn, "fr_state", "FRA", _days_ago(31))   # before the window
        _insert_state_origin(conn, "fr_state", "FRA", _days_ago(0))    # the partial export day
    for _ in range(3):
        _insert_state_origin(conn, "it_small", "ITA", _days_ago(0))    # would tie it_big and win on its id
    export.rebuild_rollups(conn, outlets)
    latest = export.build_latest(conn, outlets, [])
    assert latest["countries"]["FRA"]["last_30d"]["A"] == 6, "latest.json's own last_30d still keeps the partial day"
    f = export.build_meta(conn, outlets, [], latest)["findings"]
    top = f["top_country"]
    assert top["iso"] == "ITA" and top["state_origin"] == 3, top
    assert _pick(top["top_outlet"], "id", "name") == {"id": "it_big", "name": "Big Italian Outlet"}
    assert f["last_30d"] == {"state_origin": 5, "state_origin_wires": 1,
                             "window_start": _days_ago(30), "window_end": _days_ago(1)}
    assert top["share_of_world"] == round(3 / 5, 4), "the denominator is the complete day world total, wires included"
    # the same days the site sums: the daily files from window_start to window_end
    days = {}
    for m in export.build_daily(conn, outlets).values():
        days.update(m["days"])
    site = sum(v["countries"].get("ITA", {}).get("A", 0) for d, v in days.items() if _days_ago(30) <= d <= _days_ago(1))
    assert site == top["state_origin"]


def test_top_carrier_excludes_wires(tmp_path):
    conn = store.connect(tmp_path / "c.db")
    yesterday = _days_ago(1)   # the export day itself is partial and outside the carriers' window
    _insert_state_origin(conn, "it_big", "ITA", yesterday)
    _insert_state_origin(conn, "it_wire", "ITA", yesterday)
    outlets = _outlets_for_findings()
    export.rebuild_rollups(conn, outlets)
    latest = export.build_latest(conn, outlets, [])
    assert _pick(latest["countries"]["ITA"]["top_carrier"], "id", "name", "count") == {"id": "it_big", "name": "Big Italian Outlet", "count": 1}
    assert _pick(latest["countries"]["ITA"]["top_route"], "id", "count") == {"id": "wire_credit", "count": 1}


def test_top_carrier_and_route_count_complete_days_only(tmp_path):
    """The map tooltip's "Carried mostly by" and "Mostly arrives as" sit beside the 30 complete days the
    map opens on, so they count those days: not the partial export day, not the day before the window."""
    conn = store.connect(tmp_path / "cw.db")
    outlets = _outlets_for_findings() + [
        {"id": "it_small", "name": "Small Italian Outlet", "country": "ITA", "language": "it", "feeds": [],
         "tier": "national", "active": True, "ownership": "private"}]
    _insert_state_origin(conn, "it_big", "ITA", _days_ago(30), route="wire_credit")   # the first day of the window
    _insert_state_origin(conn, "it_big", "ITA", _days_ago(1), route="wire_credit")    # the last day of the window
    for _ in range(3):
        _insert_state_origin(conn, "it_small", "ITA", _days_ago(0), route="diplomatic_byline")   # the partial export day
        _insert_state_origin(conn, "it_small", "ITA", _days_ago(31), route="diplomatic_byline")  # before the window
    export.rebuild_rollups(conn, outlets)
    latest = export.build_latest(conn, outlets, [])
    start, until = export.complete_window_30d(latest)
    assert (start, until) == (_days_ago(30), _days_ago(0))
    ita = latest["countries"]["ITA"]
    assert _pick(ita["top_carrier"], "id", "name", "count") == {"id": "it_big", "name": "Big Italian Outlet", "count": 2}, ita["top_carrier"]
    assert _pick(ita["top_route"], "id", "count") == {"id": "wire_credit", "count": 2}, ita["top_route"]
    # the same days as the figure beside it: the state origin the daily files hold for the window
    days = {}
    for m in export.build_daily(conn, outlets).values():
        days.update(m["days"])
    window = sum(v["countries"].get("ITA", {}).get("A", 0) for d, v in days.items() if start <= d < until)
    assert window == ita["top_carrier"]["count"]
    # no end still means every day from the start, as before
    assert export._country_top_carriers(conn, outlets, start)["ITA"]["top_carrier"]["id"] == "it_small"


def _network_outlets():
    """Three Italian mastheads of one network, an outlet outside it, the release wire and France."""
    return _outlets_for_findings() + [
        {"id": "it_n%d" % i, "name": "Network Paper %d" % i, "country": "ITA", "language": "it", "feeds": [],
         "tier": "regional", "active": True, "network": "Rete"} for i in (1, 2, 3)] + [
        {"id": "it_small", "name": "Small Italian Outlet", "country": "ITA", "language": "it", "feeds": [],
         "tier": "national", "active": True, "ownership": "private"}]


def _unsign(conn, aid):
    conn.execute("UPDATE classifications SET route=NULL WHERE article_id=?", (aid,))
    conn.commit()


def test_top_carrier_and_route_report_ties_and_shares(tmp_path):
    """"Carried mostly by" read The Courier (Ballarat) on 94 of 940 placements, tied with nine sister papers
    and chosen by its id, and "Mostly arrives as" read a route that carried 200 of 940 because the unsigned
    placements were dropped before the comparison (stress test, 2026-10-17). Each entry now carries the
    window's total, its share of it and its ties, and the unsigned placements stay in the route's total."""
    conn = store.connect(tmp_path / "tie.db")
    outlets = _network_outlets()
    day = _days_ago(1)
    for oid in ("it_n1", "it_n2", "it_n3"):
        _insert_state_origin(conn, oid, "ITA", day, route="wire_credit")
        _unsign(conn, _insert_state_origin(conn, oid, "ITA", day))
    export.rebuild_rollups(conn, outlets)
    ita = export.build_latest(conn, outlets, [])["countries"]["ITA"]
    c, r = ita["top_carrier"], ita["top_route"]
    assert c["count"] == 2 and c["total"] == 6 and c["share"] == round(2 / 6, 4) and c["tied"] == 3, c
    assert c["network"] == "Rete" and c["tied_names"] == ["Network Paper 1", "Network Paper 2", "Network Paper 3"], c
    assert r["id"] == "wire_credit" and r["count"] == 3 and r["total"] == 6 and r["unattributed"] == 3, r
    assert r["share"] == 0.5 and r["tied"] == 1, "half is not most: the share is over every placement, signed or not"
    # one outlet outside the network joins the tie: no common network is claimed
    _insert_state_origin(conn, "it_small", "ITA", day)
    _insert_state_origin(conn, "it_small", "ITA", day)
    c2 = export._country_top_carriers(conn, outlets, _days_ago(30), _days_ago(0))["ITA"]["top_carrier"]
    assert c2["tied"] == 4 and c2["network"] is None and c2["total"] == 8, c2


def test_findings_top_country_majority_underlying_and_route_detail(tmp_path):
    """The findings' top country carries its distinct underlying items beside its placements, its leading
    outlet's share and ties, and the leading signed route with the unsigned placements beside it."""
    conn = store.connect(tmp_path / "maj.db")
    outlets = _network_outlets()
    day = _days_ago(1)
    a1 = _insert_state_origin(conn, "it_big", "ITA", day)
    a2 = _insert_state_origin(conn, "it_big", "ITA", day)
    _insert_state_origin(conn, "it_big", "ITA", day, route="diplomatic_byline")
    _unsign(conn, _insert_state_origin(conn, "it_small", "ITA", day))
    _insert_state_origin(conn, "fr_state", "FRA", day)
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id IN (?, ?)", (a1, a1, a2))   # two placements of one item
    conn.commit()
    export.rebuild_rollups(conn, outlets)
    latest = export.build_latest(conn, outlets, [])
    top = export.build_meta(conn, outlets, [], latest)["findings"]["top_country"]
    assert top["iso"] == "ITA" and top["state_origin"] == 4 and top["underlying"] == 3, top
    o = top["top_outlet"]
    assert o["id"] == "it_big" and o["name"] == "Big Italian Outlet" and o["count"] == 3 and o["total"] == 4 and o["share"] == 0.75 and o["tied"] == 1, o
    d = top["top_route_detail"]
    assert top["top_route"] == "wire_credit" and d["count"] == 2 and d["total"] == 4 and d["unattributed"] == 1 and d["share"] == 0.5, d


def test_findings_unassessed_owner_never_leads():
    """"Not assessed outlets publish at about 1.3 times the rate of private outlets": the outlets with no
    sourced owner had the highest rate and were named as the top group. They are never the top group."""
    oj = {"outlets": [
        {"active": True, "tier": "regional", "ownership": None, "counts": {"A": 30}},
        {"active": True, "tier": "national", "ownership": "state", "counts": {"A": 2}},
        {"active": True, "tier": "national", "ownership": "private", "counts": {"A": 3}},
    ]}
    f = export.by_ownership_findings(oj)
    assert f["top"] == {"id": "private", "per_outlet_rate": 3.0}, f["top"]
    assert {g["id"]: g["per_outlet_rate"] for g in f["groups"]}["unassessed"] == 30.0, "the group itself is still reported"


def _long_xinhua_span(n_extra_words=39):
    return "BEIJING, Sept. 2 (Xinhua) -- " + " ".join("word%d" % i for i in range(n_extra_words))


def test_examples_state_origin_tell_is_capped_and_offsets_match(tmp_path):
    conn = store.connect(tmp_path / "e.db")
    span = _long_xinhua_span()
    _insert_state_origin(conn, "it_big", "ITA", "2026-09-10", evidence_quote=span, signatures_fired=["xinhua_dateline"])
    outlets = _outlets_for_findings()
    examples = export.build_examples(conn, outlets)
    assert len(examples["A"]) == 1
    ex = examples["A"][0]
    assert ex["outlet_id"] == "it_big" and ex["country"] == "ITA" and ex["route"] == "wire_credit"
    tell = ex["tell"]
    assert len(tell["snippet"].split()) <= 25
    assert tell["snippet"][tell["start"]:tell["end"]] == "(Xinhua)" or "Xinhua" in tell["snippet"][tell["start"]:tell["end"]]


def test_examples_exclude_paywalled(tmp_path):
    conn = store.connect(tmp_path / "pw.db")
    span = _long_xinhua_span()
    aid = _insert_state_origin(conn, "it_big", "ITA", "2026-09-10", evidence_quote=span, signatures_fired=["xinhua_dateline"])
    conn.execute("UPDATE articles SET status='paywalled' WHERE id=?", (aid,))
    conn.commit()
    outlets = _outlets_for_findings()
    examples = export.build_examples(conn, outlets)
    assert examples["A"] == []


def test_examples_unchecked_omitted_when_relay_not_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELAY_PROVISIONAL_DISPLAY", False)
    conn = store.connect(tmp_path / "ne.db")
    aid = store.insert_discovered(conn, {"url": "https://x.test/b2", "outlet_id": "it_big", "country": "ITA",
                                         "language": "it", "title": "Relay story", "status": "fetched", "gate_relevant": 1})
    store.insert_classification(conn, aid, "llm", "B", 0.7, evidence_quote="Xinhua said so.", model_version="test-model")
    conn.commit()
    outlets = _outlets_for_findings()
    examples = export.build_examples(conn, outlets)
    assert "B" not in examples and examples["A"] == []


def test_examples_snippet_replaces_bare_urls_with_their_host(tmp_path):
    # A press release quote that ends in a long attachment link once made the page scroll sideways
    # on a phone; the snippet now carries only the link's host, and the highlight still lines up.
    conn = store.connect(tmp_path / "url.db")
    span = ("BEIJING, Sept. 2 (Xinhua) -- The full report is available at: "
            "https://www.globenewswire.com/NewsRoom/AttachmentNg/6bf50962-0c1e-4a3b-9d2f-7e8a1b2c3d4e.")
    _insert_state_origin(conn, "it_big", "ITA", "2026-09-10", evidence_quote=span, signatures_fired=["xinhua_dateline"])
    tell = export.build_examples(conn, _outlets_for_findings())["A"][0]["tell"]
    assert "http" not in tell["snippet"] and "NewsRoom" not in tell["snippet"]
    assert tell["snippet"].endswith("available at: globenewswire.com.")
    assert "Xinhua" in tell["snippet"][tell["start"]:tell["end"]]


def test_shorten_urls_shifts_offsets_and_keeps_a_matched_url_whole():
    text = "See https://www.example.com/a/very/long/path?x=1 and www.other.org/p, then (Xinhua) says."
    i = text.index("(Xinhua)")
    out, s, e = export._shorten_urls(text, i, i + len("(Xinhua)"))
    assert out == "See example.com and other.org, then (Xinhua) says."
    assert out[s:e] == "(Xinhua)"
    # A URL inside the highlighted match is left as it is, offsets unchanged.
    text2 = "Source: http://xinhuanet.com/english/2026-09/02/c_1.htm today"
    j = text2.index("http")
    assert export._shorten_urls(text2, j, j + 10) == (text2, j, j + 10)


def test_tell_moves_the_highlight_onto_the_host_when_the_match_is_inside_the_url():
    # The live case: the wire stamp pattern matched "globenewswire" inside the attachment link, so the
    # link itself carried the highlight. The snippet keeps only the host, highlighted.
    span = ("Files accompanying this announcement are available at:\n"
            "https://www.globenewswire.com/NewsRoom/AttachmentNg/6bf50962-ffc1-42c9-935b-013101055")
    tell = export._tell("B", None, None, json.dumps({"triggers": ["wire_stamp_alone"], "spans": [span]}))
    # The stored span is a window cut on both sides (60 or more characters around the match), so the
    # snippet carries an ellipsis at each end; the cut link's host survives.
    assert tell["snippet"] == "\u2026 Files accompanying this announcement are available at:\nglobenewswire.com \u2026"
    assert tell["snippet"][tell["start"]:tell["end"]].lower() == "globenewswire"


def _rules_window(text, phrase, at=0):
    # The span the rules store: 60 characters either side of the match, stripped (classify_rules).
    i = text.index(phrase, at)
    return text[max(0, i - 60):i + len(phrase) + 60].strip()


def test_tell_snippet_cut_mid_word_at_both_ends_starts_and_ends_on_whole_words():
    # The live case: "ttend the summit ... Wang's first" came from a window that began inside "attend".
    text = ("Wang Yi will travel to Azerbaijan, Croatia, Montenegro and Bulgaria and attend the summit on "
            "Thursday, Foreign Ministry spokesperson Mao Ning said Monday, adding that GlobeNewswire carried "
            "the notice to subscribers across these regions before the formal announcement this weekend.")
    span = _rules_window(text, "GlobeNewswire")
    assert not text[text.index(span) - 1].isspace() and not text[text.index(span) + len(span)].isspace()
    tell = export._tell("B", None, None, json.dumps({"triggers": ["wire_stamp_alone"], "spans": [span]}))
    snip = tell["snippet"]
    assert snip.startswith("\u2026 ") and snip.endswith(" \u2026")
    assert snip[tell["start"]:tell["end"]] == "GlobeNewswire"
    words = text.split()
    assert all(w in words for w in snip.split() if w != "\u2026")
    # The cut words themselves are gone, not shown in part.
    assert span.split()[0] not in snip.split() and span.split()[-1] not in snip.split()


def test_trim_keeps_a_highlight_near_each_edge_exact():
    text = "abc def Xinhua ghi jkl mno pqr stu vwx yz1 yz2 end Xinhua zz"
    # Near the left edge, beside a partial word that is dropped.
    out, s, e = export._trim_to_words(text, 8, 14, cut_left=True, cut_right=True)
    assert out == "\u2026 def Xinhua ghi jkl mno pqr stu vwx yz1 yz2 end Xinhua \u2026"
    assert out[s:e] == "Xinhua"
    # Near the right edge, beside a partial word that is dropped.
    j = text.rindex("Xinhua")
    out, s, e = export._trim_to_words(text, j, j + 6, cut_left=True, cut_right=True)
    assert out[s:e] == "Xinhua" and out.endswith("end Xinhua \u2026") and out.startswith("\u2026 def ")
    # A match that reaches into the cut word keeps it whole, since the matched text must stay.
    out, s, e = export._trim_to_words(text, 0, 3, cut_left=True, cut_right=False)
    assert out.startswith("\u2026 abc def") and out[s:e] == "abc"
    # Cut by the word budget at both sides: an ellipsis marks each, counted in the 25, offsets exact.
    long = " ".join("w%d" % i for i in range(60))
    k = long.index("w3 ")
    out, s, e = export._trim_to_words(long, k, k + 2)
    assert out.startswith("w0 w1 w2 w3 ") and out.endswith(" \u2026") and out[s:e] == "w3"
    assert len(out.split()) <= 25
    k = long.index("w58")
    out, s, e = export._trim_to_words(long, k, k + 3)
    assert out.startswith("\u2026 ") and out.endswith("w58 w59") and out[s:e] == "w58" and len(out.split()) <= 25


def test_tell_cjk_snippet_is_cut_on_characters():
    text = ("\u636e\u62a5\u9053\uff0c\u4e2d\u56fd\u4e0e\u4e1c\u76df\u56fd\u5bb6\u5728\u7ecf\u6d4e\u3001\u8d38\u6613"
            "\u548c\u6295\u8d44\u9886\u57df\u7684\u5408\u4f5c\u4e0d\u65ad\u6df1\u5316\uff0c\u53cc\u65b9\u8d38\u6613"
            "\u989d\u6301\u7eed\u589e\u957f\u3002\u65b0\u534e\u793e\u62a5\u9053\u79f0\uff0c\u5404\u65b9\u5c06\u5728"
            "\u672a\u6765\u4e00\u5e74\u5185\u7ee7\u7eed\u63a8\u8fdb\u57fa\u7840\u8bbe\u65bd\u5efa\u8bbe\u548c\u4eba"
            "\u6587\u4ea4\u6d41\u5408\u4f5c\uff0c\u52a0\u5feb\u81ea\u8d38\u533a\u5347\u7ea7\u8fdb\u7a0b\uff0c\u4fc3"
            "\u8fdb\u5730\u533a\u7ecf\u6d4e\u4e00\u4f53\u5316\u53d1\u5c55\u548c\u5171\u540c\u7e41\u8363\u3002") * 2
    span = _rules_window(text, "\u65b0\u534e\u793e", at=60)
    tell = export._tell("B", None, None, json.dumps({"triggers": ["cites_xinhua"], "spans": [span]}))
    snip = tell["snippet"]
    assert snip[tell["start"]:tell["end"]] == "\u65b0\u534e\u793e"
    # No space between the ellipsis and the characters, and nothing in between dropped: what is shown
    # is a run of the original text, at most 50 characters (half a word each, ellipses counted).
    assert snip.startswith("\u2026") and not snip.startswith("\u2026 ") and snip.endswith("\u2026")
    assert snip.strip("\u2026") in text
    assert len(snip.strip("\u2026")) <= 46
    # A Thai vowel mark stays on its consonant; a mark orphaned by the cut is dropped.
    thai = "\u0e34\u0e19\u0e17\u0e35\u0e48\u0e19\u0e35\u0e48 Xinhua"
    out, s, e = export._trim_to_words(thai, 9, 15, cut_left=True)
    assert out == "\u2026\u0e19\u0e17\u0e35\u0e48\u0e19\u0e35\u0e48 Xinhua" and out[s:e] == "Xinhua"


def _xinhua_tell(span):
    return export._tell("B", None, None, json.dumps({"triggers": ["cites_xinhua"], "spans": [span]}))


def test_tell_keeps_every_word_of_a_span_the_rules_widened_to_whole_words():
    from pipeline import classify_rules
    # The window's left edge falls inside "attend", a lowercase word the old trim had to drop.
    text = ("Wang Yi will travel to Azerbaijan, Croatia, Montenegro and Bulgaria and attend the summit on "
            "Thursday, adding that the visits, which Xinhua carried to subscribers, follows months of "
            "preparation in the regions before the formal announcement this weekend.")
    i = text.index("Xinhua")
    old = _rules_window(text, "Xinhua")
    assert old.startswith("ttend") and not text[text.index(old) + len(old)].isspace()
    span = classify_rules.evidence_span(text, i, i + 6)
    assert span.startswith("\u2026 attend the summit") and span.endswith(" \u2026")
    tell = _xinhua_tell(span)
    snip = tell["snippet"]
    assert snip[tell["start"]:tell["end"]] == "Xinhua"
    # Nothing is dropped as if cut: the snippet is the span's words, an ellipsis at each end.
    assert snip == span
    # A span stored before the marks still goes through the old fix: the half words go.
    tell_old = _xinhua_tell(old)
    assert tell_old["snippet"].startswith("\u2026 the summit") and "ttend" not in tell_old["snippet"]
    assert tell_old["snippet"][tell_old["start"]:tell_old["end"]] == "Xinhua"


def test_tell_of_a_span_cut_inside_a_long_url_drops_the_partial_token():
    from pipeline import classify_rules
    text = ("Read more at https://example.com/files/" + "x" * 90 + " where Xinhua reported the talks would "
            "resume next week, see https://example.org/" + "y" * 90 + " ok")
    i = text.index("Xinhua")
    span = classify_rules.evidence_span(text, i, i + 6)
    # No boundary within reach on either side: the character window, marked as cut inside a token.
    assert span == "\u2026" + _rules_window(text, "Xinhua") + "\u2026"
    tell = _xinhua_tell(span)
    assert tell["snippet"] == "\u2026 where Xinhua reported the talks would resume next week, see \u2026"
    assert tell["snippet"][tell["start"]:tell["end"]] == "Xinhua"


def test_tell_of_cjk_and_thai_spans_is_unchanged_by_the_marks():
    from pipeline import classify_rules
    cjk = ("\u636e\u62a5\u9053\uff0c\u4e2d\u56fd\u4e0e\u4e1c\u76df\u56fd\u5bb6\u5728\u7ecf\u6d4e\u3001\u8d38\u6613"
           "\u548c\u6295\u8d44\u9886\u57df\u7684\u5408\u4f5c\u4e0d\u65ad\u6df1\u5316\uff0c\u65b0\u534e\u793e"
           "\u62a5\u9053\u79f0\uff0c\u5404\u65b9\u5c06\u5728\u672a\u6765\u4e00\u5e74\u5185\u7ee7\u7eed\u63a8\u8fdb") * 3
    i = cjk.index("\u65b0\u534e\u793e", 60)
    span = classify_rules.evidence_span(cjk, i, i + 3)
    assert span == _rules_window(cjk, "\u65b0\u534e\u793e", at=60)
    tell = _xinhua_tell(span)
    assert "\u2026 " not in tell["snippet"] and tell["snippet"].strip("\u2026") in cjk
    assert tell["snippet"][tell["start"]:tell["end"]] == "\u65b0\u534e\u793e"
    thai = ("\u0e2a\u0e33\u0e19\u0e31\u0e01\u0e02\u0e48\u0e32\u0e27\u0e0b\u0e34\u0e19\u0e2b\u0e31\u0e27"
            "\u0e23\u0e32\u0e22\u0e07\u0e32\u0e19\u0e27\u0e48\u0e32\u0e08\u0e35\u0e19\u0e41\u0e25\u0e30") * 6
    k = thai.index("\u0e0b\u0e34\u0e19\u0e2b\u0e31\u0e27", 70)
    span = classify_rules.evidence_span(thai, k, k + 6)
    assert span == _rules_window(thai, "\u0e0b\u0e34\u0e19\u0e2b\u0e31\u0e27", at=70)
    tell = _xinhua_tell(span)
    assert tell["snippet"][tell["start"]:tell["end"]] == "\u0e0b\u0e34\u0e19\u0e2b\u0e31\u0e27"


def _rendered_methodology(tmp_path, monkeypatch, **meta_changes):
    from pipeline import methodology
    db = tmp_path / "m.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    out = tmp_path / "out"
    export.run(store.connect(db), "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M0.md")
    meta, latest = _load(out / "meta.json"), _load(out / "latest.json")
    meta.update(meta_changes)
    path = tmp_path / "M.md"
    methodology.write(meta, latest, path=path)
    return path.read_text(encoding="utf-8")


def test_methodology_without_a_stale_stability_check_says_nothing_of_one(tmp_path, monkeypatch):
    text = _rendered_methodology(tmp_path, monkeypatch, relay_reliability_history=None)
    assert "earlier version of the classifier's instructions" not in text
    assert "kept as history" not in text


def test_methodology_reports_a_stale_stability_check_as_history(tmp_path, monkeypatch):
    """A stability check made under an older classifier prompt is named as history, with its date, size and
    kappa, and the paragraph says how many current labels the next check waits for."""
    hist = {"method": "same_model_rerun", "model_a": "m", "model_b": "m", "n": 225, "kappa_all": 0.7,
            "kappa_bc": 0.41, "n_bc": 116, "computed_at": "2026-10-09T02:29:14+00:00", "stale": True,
            "prompt_version": "2026-09-01", "role": "stability_check"}
    study = {"auto": True, "sample": 225, "earliest": None, "state": "waiting_for_labels", "pool": 37,
             "min_pool": config.RELIABILITY_MIN_POOL, "reason": "the classifier prompt changed"}
    text = _rendered_methodology(tmp_path, monkeypatch, relay_reliability=None,
                                 relay_reliability_history=hist, relay_study=study)
    assert ("Stability check. The last stability check (2026-10-09, 225 articles, kappa 0.41 on the unchecked "
            "sourcing versus independent journalism judgement) was run under an earlier version of the classifier's "
            "instructions, dated 2026-09-01, so it is kept as history") in text
    assert ("A new check runs once %d articles have been labelled under the current instructions; 37 have been so far."
            % config.RELIABILITY_MIN_POOL) in text
    assert "—" not in text and "–" not in text


def test_stability_history_text_follows_the_study_state():
    from pipeline import methodology
    hist = {"n": 50, "kappa_bc": 0.9, "computed_at": "2026-10-01T00:00:00+00:00", "stale": True, "prompt_version": None}
    assert methodology.stability_history_text({"relay_reliability_history": None}) == ""
    assert methodology.stability_history_text({"relay_reliability_history": dict(hist, stale=False)}) == ""
    bare = methodology.stability_history_text({"relay_reliability_history": hist})
    assert "instructions, so it is kept as history" in bare and "dated" not in bare
    assert ("once %d articles have been labelled" % config.RELIABILITY_MIN_POOL) in bare and "so far" not in bare
    due = methodology.stability_history_text({"relay_reliability_history": hist, "relay_study": {
        "auto": True, "state": "waiting_for_budget", "earliest": "2026-11-01", "min_pool": 200}})
    assert "Enough articles have now been labelled" in due and "2026-11-01 at the earliest" in due
    sent = methodology.stability_history_text({"relay_reliability_history": hist, "relay_study": {"state": "submitted"}})
    assert "has been submitted" in sent
    manual = methodology.stability_history_text({"relay_reliability_history": hist, "relay_study": {
        "auto": False, "state": "waiting_for_labels", "pool": 3, "min_pool": 200}})
    assert "can be run by hand once 200 articles" in manual and "3 have been so far" in manual


_PROVISIONAL = {"relay_measured": True, "relay_provisional": True, "relay_publishable": False,
                "relay_withheld_languages": ["it"]}
_STUDY_WAITING = {"auto": True, "sample": 225, "earliest": None, "state": "waiting_for_labels", "pool": 12,
                  "min_pool": config.RELIABILITY_MIN_POOL, "reason": "the classifier prompt changed"}


def test_methodology_provisional_paragraph_without_any_stability_check(tmp_path, monkeypatch):
    """With neither a current nor an older stability check in meta, the provisional paragraph does not point to a
    check "described below"; it says none has run and when one will, and the withheld languages sentence names
    its subject."""
    text = _rendered_methodology(tmp_path, monkeypatch, relay_reliability=None, relay_reliability_history=None,
                                 relay_study=_STUDY_WAITING, **_PROVISIONAL)
    assert "described below" not in text and "\n\nStability check." not in text
    assert ("No stability check has been run yet. A check runs once %d articles have been labelled under the current "
            "instructions; 12 have been so far. When one runs, it never takes the provisional marking away."
            % config.RELIABILITY_MIN_POOL) in text
    assert "Unchecked state sourcing counts are withheld for outlets publishing in it, where" in text
    assert " They are withheld for" not in text
    assert "—" not in text and "–" not in text


def test_methodology_provisional_paragraph_with_a_current_stability_check(tmp_path, monkeypatch):
    rr = {"method": "same_model_rerun", "model_a": "m", "model_b": "m", "n": 225, "kappa_all": 0.8,
          "kappa_bc": 0.71, "n_bc": 116, "computed_at": "2026-10-11T00:00:00+00:00"}
    text = _rendered_methodology(tmp_path, monkeypatch, relay_reliability=rr, relay_reliability_history=None,
                                 relay_study=dict(_STUDY_WAITING, state="done"), **_PROVISIONAL)
    assert "A stability check, described below, never takes the provisional marking away." in text
    assert "\n\nStability check. The same model (m) judged a sample of 225 articles" in text
    assert "No stability check has been run yet" not in text
    assert "Unchecked state sourcing counts are withheld for outlets publishing in it, where" in text


def test_methodology_provisional_paragraph_with_a_stale_stability_check(tmp_path, monkeypatch):
    hist = {"method": "same_model_rerun", "model_a": "m", "model_b": "m", "n": 225, "kappa_all": 0.7,
            "kappa_bc": 0.41, "n_bc": 116, "computed_at": "2026-10-09T02:29:14+00:00", "stale": True,
            "prompt_version": "2026-09-01", "role": "stability_check"}
    text = _rendered_methodology(tmp_path, monkeypatch, relay_reliability=None, relay_reliability_history=hist,
                                 relay_study=_STUDY_WAITING, **_PROVISIONAL)
    assert "A stability check, described below, never takes the provisional marking away." in text
    assert "\n\nStability check. The last stability check (2026-10-09, 225 articles" in text
    assert "No stability check has been run yet" not in text
    assert "A new check runs once %d articles" % config.RELIABILITY_MIN_POOL in text


def test_methodology_lists_each_themes_version_from_meta(tmp_path, monkeypatch):
    """The themes paragraph names every version in meta.themes_changes with its date and change, and says the
    re-tagging leaves no break in the series while the timeline marks record when the method changed."""
    from pipeline import themes
    text = _rendered_methodology(tmp_path, monkeypatch)
    for h in themes.changes():
        assert "%s from %s (%s)" % (h["version"], h["date"], h["change"]) in text
    assert ("2026.09.4 from 2026-09-15 (terms read anywhere in the body instead of the first 800 characters") in text
    assert "the published series has no break at a version change" in text
    assert "the marks on the theme timeline record when the method changed" in text
    assert "{themes_changes_text}" not in text and "—" not in text and "–" not in text


def test_methodology_renders_without_themes_changes(tmp_path, monkeypatch):
    """An export made before meta.themes_changes existed still renders, with no version list and no marks claimed."""
    text = _rendered_methodology(tmp_path, monkeypatch, themes_changes=None)
    assert "Themes versions, each with the day it took effect" not in text
    assert "the theme timeline shows no version mark" in text
    assert "the published series has no break at a version change" in text
    assert "{themes_changes_text}" not in text


def _relay_outlet(oid, country, collector):
    return {"id": oid, "name": oid, "country": country, "language": "it", "feeds": [], "tier": "national", "active": True,
            "collector": collector}


RELAY_SPLIT_OUTLETS = [_relay_outlet("it_relay", "ITA", "self_hosted"), _relay_outlet("it_shared", "ITA", "both"),
                       _relay_outlet("de_shared", "DEU", "both"), _relay_outlet("de_shared2", "DEU", "both"),
                       _relay_outlet("fr_hosted", "FRA", "hosted")]


def _relay_split_db(tmp_path, hours_ago):
    import datetime as dt
    conn = store.connect(tmp_path / "rs.db")
    t = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO relay_runs(started_at, finished_at, ok) VALUES (?,?,1)", (t, t))
    conn.commit()
    return conn


def test_relay_status_states_relay_only_and_shared_outlets_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELAY_RETIRED_ON", None)    # as it was before the relay was retired
    rc = export.relay_status(_relay_split_db(tmp_path, 1), RELAY_SPLIT_OUTLETS)
    assert (rc["outlets"], rc["countries"]) == (1, ["ITA"])
    assert (rc["shared_outlets"], rc["shared_countries"]) == (3, ["DEU", "ITA"])
    assert rc["stale"] is False


def test_stale_relay_calls_only_relay_only_outlets_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELAY_RETIRED_ON", None)    # as it was before the relay was retired
    latest = export.build_latest(_relay_split_db(tmp_path, 9), RELAY_SPLIT_OUTLETS, [], population={})
    ita, deu, fra = (latest["countries"][c] for c in ("ITA", "DEU", "FRA"))
    assert (ita["relay_outlets"], ita["shared_outlets"], deu["relay_outlets"], deu["shared_outlets"]) == (1, 1, 0, 2)
    by_type = lambda e: {w["type"]: w["text"] for w in e["warnings"]}
    assert "1 of this country's outlets are collected only from the owner's machine" in by_type(ita)["relay_stale"]
    assert "recent counts for them are incomplete" in by_type(ita)["relay_stale"]
    shared = by_type(deu)["relay_stale_shared"]
    assert shared.startswith("2 of this country's outlets are also polled from the owner's machine") and "hosted runner's hourly polls" in shared
    assert "incomplete" not in shared and "relay_stale" not in by_type(deu)
    assert not any(w["type"].startswith("relay") for w in fra["warnings"])
    (tmp_path / "fresh").mkdir()
    fresh = export.build_latest(_relay_split_db(tmp_path / "fresh", 1), RELAY_SPLIT_OUTLETS, [], population={})
    assert not any(w["type"].startswith("relay_stale") for c in fresh["countries"].values() for w in c["warnings"])


def test_methodology_states_relay_only_and_shared_outlets_apart(tmp_path, monkeypatch):
    rc = {"outlets": 88, "countries": ["AUS", "ITA"], "shared_outlets": 148, "shared_countries": ["DEU", "FRA", "ITA"],
          "last_run": "2026-10-12T08:06:06+00:00", "hours_since_last_run": 1.2, "incomplete_days": ["2026-10-01"]}
    text = _rendered_methodology(tmp_path, monkeypatch, relay_collector=rc)
    assert ("88 outlets collected only there, in 2 countries; 148 more polled there and by the hosted runner, in 3 countries; "
            "last pass 2026-10-12T08:06:06+00:00 (1.2 hours before export); 1 incomplete days") in text
    flat = " ".join(text.split())
    assert "whose outlets only it collected" in flat and "The outlets it shared with the hosted runner are not marked" in flat


def test_rediscovery_dates_the_fix_from_the_first_run_that_remembered_pruned_addresses(tmp_path):
    """meta.rediscovery: the day after the first run with seen_urls (schema_migrations), the config date when
    the database has no such row, and nothing for a database with no discovery counted before that run."""
    conn = store.connect(tmp_path / "r.db")
    assert conn.execute("SELECT 1 FROM schema_migrations WHERE name='seen_urls.remembering'").fetchone()
    assert export.rediscovery(conn) is None
    assert export.build_meta(conn, [], [], export.build_latest(conn, [], []))["rediscovery"] is None
    conn.execute("UPDATE schema_migrations SET applied_at='2026-10-12T15:00:00+00:00' WHERE name='seen_urls.remembering'")
    conn.execute("INSERT INTO daily_discovery(date, country, discovered, gate_relevant) VALUES ('2026-09-01', 'ITA', 5, 0)")
    conn.commit()
    rd = export.build_meta(conn, [], [], export.build_latest(conn, [], []))["rediscovery"]
    assert rd == {"fixed_from": "2026-10-13", "estimated_share": config.REDISCOVERY_ESTIMATED_SHARE,
                  "measured_floor": config.REDISCOVERY_MEASURED_FLOOR, "measured_of": config.REDISCOVERY_MEASURED_OF,
                  "window_share": config.REDISCOVERY_WINDOW_SHARE}
    assert (rd["estimated_share"], rd["measured_floor"]) == (0.08, 123682)
    conn.execute("DELETE FROM schema_migrations WHERE name='seen_urls.remembering'")
    assert export.rediscovery(conn)["fixed_from"] == config.REDISCOVERY_FIXED_FROM
    conn.execute("UPDATE daily_discovery SET date='2026-10-20'")
    assert export.rediscovery(conn) is None


def test_methodology_states_the_rediscovery_figures_the_site_shows(tmp_path, monkeypatch):
    """The paragraph on items counted again after a prune reads the same date and share as the site's note
    under the running total (compute.js rediscoveryNote: "Before <fixed_from>, ... an estimated 8 percent")."""
    rd = {"fixed_from": "2026-10-14", "estimated_share": 0.08, "measured_floor": 123682, "measured_of": 3008671,
          "window_share": 0.082}
    text = " ".join(_rendered_methodology(tmp_path, monkeypatch, rediscovery=rd).split())
    assert "a second time. Before 2026-10-14 it did: after every prune such an item came back as new." in text
    assert "8.2 percent of the items screened were such repeats" in text
    assert "at least 4.1 percent were (123,682 items" in text
    assert "near 8 percent of the running total of items screened, and the site says so under that total" in text
    assert "The counts before 2026-10-14 are left as they were" in text
    assert "% of all its news" in text and "72 in the Cayman Islands" in text
    (tmp_path / "x").mkdir()
    fallback = " ".join(_rendered_methodology(tmp_path / "x", monkeypatch, rediscovery=None).split())
    assert "Before %s it did" % config.REDISCOVERY_FIXED_FROM in fallback
