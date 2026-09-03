"""Schema validation for the generated JSON and an export run on an empty database."""
import json
import sqlite3
from pathlib import Path

import jsonschema
import pytest

from pipeline import config, export, store

COUNTS_KEYS = ["A", "B", "C", "N", "Ar", "Al", "Ah", "Br", "Bl", "Bh", "rev", "cls", "disc", "rel", "fetched",
               "paywalled", "failed", "blocked", "pending", "uniqA", "uniqAB"]
COUNTS_SCHEMA = {"type": "object", "required": COUNTS_KEYS, "properties": {k: {"type": "integer"} for k in COUNTS_KEYS}}
DERIVED_SCHEMA = {"type": "object", "required": COUNTS_KEYS + ["china_total", "share_ab", "share_a", "per_outlet_ab", "paywall_share", "reviewed_share"]}

LATEST_SCHEMA = {
    "type": "object", "required": ["generated_at", "countries", "totals", "window_start_30d"],
    "properties": {
        "countries": {"type": "object", "patternProperties": {"^[A-Z]{3}$": {
            "type": "object", "required": ["coverage", "outlets_total", "outlets_active", "feeds_total", "feeds_ok", "all_time", "last_30d", "warnings"],
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


@pytest.mark.skipif(not (config.EXPORT_DIR / "latest.json").exists(), reason="no export yet")
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
    counts = export.run(conn, "test", export_dir=out)
    assert counts["articles"] == 0
    latest = _load(out / "latest.json")
    jsonschema.validate(latest, LATEST_SCHEMA)
    meta = _load(out / "meta.json")
    jsonschema.validate(meta, META_SCHEMA)
    assert meta["b_counts_settled"] is False
    # every registered country appears even with no articles, so the map can show them as monitored with zero data
    assert set(latest["countries"]) >= {"ITA", "CAN", "AUS"}
    assert latest["countries"]["ITA"]["all_time"]["share_ab"] is None


def test_share_and_flags():
    e = export._derive({**export._empty_country(), "A": 2, "B": 1, "C": 7, "fetched": 5, "paywalled": 5}, 4)
    assert e["china_total"] == 10 and e["share_ab"] == 0.3 and e["per_outlet_ab"] == 0.75 and e["paywall_share"] == 0.5
