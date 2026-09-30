"""Schema validation for the generated JSON and an export run on an empty database."""
import json
import sqlite3
from pathlib import Path

import jsonschema
import pytest

from pipeline import config, export, store

COUNTS_KEYS = ["A", "B", "C", "N", "Ar", "Al", "Ah", "Br", "Bl", "Bh", "rev", "cls", "disc", "rel", "fetched",
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
    "type": "object", "required": ["since", "all_time", "top_country", "by_ownership", "generated_at"],
    "properties": {
        "all_time": {"type": "object", "required": ["state_origin", "unchecked", "independent", "china_total", "state_share_of_china"]},
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
                 "llm_ceiling_days", "categories", "first_discovered", "relay_provisional", "relay_study", "findings"],
    "properties": {"b_counts_settled": {"type": "boolean"}, "relay_provisional": {"type": "boolean"},
                   "relay_study": {"type": "object", "required": ["auto", "state", "sample"]}, "review_coverage": {"type": "number"},
                   "categories": {"type": "object", "required": ["A", "B", "C", "not_relevant"]},
                   "findings": FINDINGS_SCHEMA},
}
OUTLETS_SCHEMA = {
    "type": "object", "required": ["generated_at", "outlets"],
    "properties": {"outlets": {"type": "array", "items": {"type": "object", "required": ["id", "name", "country", "language", "tier", "active", "feeds", "counts"],
                                                          "properties": {"counts": COUNTS_SCHEMA, "feeds": {"type": "array", "items": {"type": "object", "required": ["url", "ok"]}}}}}},
}
ARTICLES_SCHEMA = {"type": "array", "items": {"type": "object", "required": ["id", "outlet_id", "title", "url", "date", "category", "provenance", "sources"],
                                              "properties": {"category": {"enum": ["A", "B", "C", "pending"]}, "provenance": {"enum": ["rules", "llm", "human"]}}}}
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
    assert all(set(["date", "A", "B", "C", "llm_ceiling_hit"]) <= set(s) for s in series)


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


def test_findings_top_country_last30_excludes_wires(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    today = export._today()
    _insert_state_origin(conn, "it_big", "ITA", today)
    _insert_state_origin(conn, "it_big", "ITA", today)
    _insert_state_origin(conn, "it_wire", "ITA", today)   # must not inflate ITA's top_country figures
    _insert_state_origin(conn, "fr_state", "FRA", today)
    outlets = _outlets_for_findings()
    export.rebuild_rollups(conn, outlets)
    latest = export.build_latest(conn, outlets, [])
    meta = export.build_meta(conn, outlets, [], latest)
    top = meta["findings"]["top_country"]
    assert top["iso"] == "ITA" and top["state_origin"] == 2
    assert top["top_outlet"] == {"id": "it_big", "name": "Big Italian Outlet"}


def test_top_carrier_excludes_wires(tmp_path):
    conn = store.connect(tmp_path / "c.db")
    today = export._today()
    _insert_state_origin(conn, "it_big", "ITA", today)
    _insert_state_origin(conn, "it_wire", "ITA", today)
    outlets = _outlets_for_findings()
    export.rebuild_rollups(conn, outlets)
    latest = export.build_latest(conn, outlets, [])
    assert latest["countries"]["ITA"]["top_carrier"] == {"id": "it_big", "name": "Big Italian Outlet", "count": 1}
    assert latest["countries"]["ITA"]["top_route"] == {"id": "wire_credit", "count": 1}


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
