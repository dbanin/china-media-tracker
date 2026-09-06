"""Schema validation for the generated JSON and an export run on an empty database."""
import json
import sqlite3
from pathlib import Path

import jsonschema
import pytest

from pipeline import config, export, store

COUNTS_KEYS = ["A", "B", "C", "N", "Ar", "Al", "Ah", "Br", "Bl", "Bh", "rev", "cls", "disc", "rel", "fetched",
               "paywalled", "failed", "blocked", "pending", "uniqA", "uniqAB", "tdisc", "ttarget", "tchina"]
COUNTS_SCHEMA = {"type": "object", "required": COUNTS_KEYS, "properties": {k: {"type": "integer"} for k in COUNTS_KEYS}}
DERIVED_SCHEMA = {"type": "object", "required": COUNTS_KEYS + ["china_total", "share_ab", "share_a", "per_outlet_ab", "paywall_share", "reviewed_share", "share_of_all_target", "share_of_all_china"]}

LATEST_SCHEMA = {
    "type": "object", "required": ["generated_at", "countries", "totals", "window_start_30d"],
    "properties": {
        "countries": {"type": "object", "patternProperties": {"^[A-Z]{3}$": {
            "type": "object", "required": ["coverage", "outlets_total", "outlets_active", "feeds_total", "feeds_ok", "all_time", "last_30d", "warnings", "population", "top_outlets"],
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
META_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "ruleset_version", "llm_model", "generated_at", "outlets_total", "outlets_active",
                 "countries_monitored", "countries_in_gaps", "gaps", "articles_classified", "articles_reviewed",
                 "review_coverage", "paywall_share", "paywall_flagged_countries", "kappa", "b_counts_settled",
                 "llm_ceiling_days", "categories", "first_discovered"],
    "properties": {"b_counts_settled": {"type": "boolean"}, "review_coverage": {"type": "number"},
                   "categories": {"type": "object", "required": ["A", "B", "C", "not_relevant"]}},
}
OUTLETS_SCHEMA = {
    "type": "object", "required": ["generated_at", "outlets"],
    "properties": {"outlets": {"type": "array", "items": {"type": "object", "required": ["id", "name", "country", "language", "tier", "active", "feeds", "counts"],
                                                          "properties": {"counts": COUNTS_SCHEMA, "feeds": {"type": "array", "items": {"type": "object", "required": ["url", "ok"]}}}}}},
}
ARTICLES_SCHEMA = {"type": "array", "items": {"type": "object", "required": ["id", "outlet_id", "title", "url", "date", "category", "provenance", "sources"],
                                              "properties": {"category": {"enum": ["A", "B", "C", "pending"]}, "provenance": {"enum": ["rules", "llm", "human"]}}}}


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
    # every registered country appears even with no articles, so the map can show them as monitored with zero data
    assert set(latest["countries"]) >= {"ITA", "CAN", "AUS"}
    assert latest["countries"]["ITA"]["all_time"]["share_ab"] is None


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
    latest = export.build_latest(conn, outlets31, [], population={"ITA": 1000000})
    assert latest["countries"]["ITA"]["population"] == 1000000
    assert abs(latest["countries"]["ITA"]["all_time"]["share_of_all_target"] - 1 / 60) < 1e-4


def test_share_and_flags():
    e = export._derive({**export._empty_country(), "A": 2, "B": 1, "C": 7, "fetched": 5, "paywalled": 5}, 4)
    assert e["china_total"] == 10 and e["share_ab"] == 0.3 and e["per_outlet_ab"] == 0.75 and e["paywall_share"] == 0.5
