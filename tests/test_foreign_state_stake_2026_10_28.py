"""Foreign state ownership stakes on outlets (decision D11, 2026-10-28): the schema and the validator
accept only a sourced stake, the export carries the field, the site shows it, and the IOL entries
carry the sourced Chinese state-linked stake in Independent Media."""
import copy

import jsonschema
import pytest

from pipeline import config, registry

ROOT = config.ROOT

BASE = {"id": "za_x", "name": "X", "country": "ZAF", "language": "en", "feeds": ["https://x.co.za/rss"],
        "tier": "national_daily", "active": True}
STAKE = {"state": "CHN", "holder": "A state fund", "holder_type": ["state_fund"], "state_link": "Owned by the state",
         "held_in": "X Holdings", "share_percent": 20, "from": "2013-08", "to": None, "status": "current",
         "as_of": "2026-07-22", "source_url": "https://example.org/a", "source_title": "A",
         "status_source_url": "https://example.org/b", "status_source_title": "B"}


def _outlet(**stake_changes):
    s = dict(STAKE, **stake_changes)
    s = {k: v for k, v in s.items() if v is not ...}
    return dict(copy.deepcopy(BASE), foreign_state_stake=[s])


def test_a_sourced_stake_validates():
    registry.validate_outlets([_outlet()])
    registry.validate_outlets([_outlet(status="ended", to="2018-03", as_of=...)])
    registry.validate_outlets([_outlet(share_percent="undisclosed", status="disputed")])
    registry.validate_outlets([copy.deepcopy(BASE)])            # no field: no stake recorded


@pytest.mark.parametrize("changes", [
    {"source_url": ...},                          # a stake is never recorded without its source
    {"source_url": "not a url"},
    {"status_source_url": ...},
    {"holder": ...},
    {"state_link": ...},
    {"holder_type": ["private_company"]},         # a private company with state ties is not a state holder
    {"holder_type": []},
    {"share_percent": 0},
    {"share_percent": 120},
    {"share_percent": "about a fifth"},
    {"from": "August 2013"},
    {"state": "China"},
    {"status": "probable"},
    {"guess": "inferred"},
])
def test_schema_rejects_an_unsourced_or_malformed_stake(changes):
    with pytest.raises(jsonschema.ValidationError):
        registry.validate_outlets([_outlet(**changes)])


def test_schema_rejects_an_empty_list():
    with pytest.raises(jsonschema.ValidationError):
        registry.validate_outlets([dict(copy.deepcopy(BASE), foreign_state_stake=[])])


@pytest.mark.parametrize("changes, message", [
    ({"state": "ZAF"}, "own country"),
    ({"status": "ended"}, "needs to"),
    ({"status": "ended", "to": "2012"}, "before from"),
    ({"to": "2020"}, "no end date"),
    ({"status": "disputed", "as_of": ...}, "needs as_of"),
    ({"as_of": "2012-01-01"}, "as_of comes before from"),
])
def test_validator_rejects_inconsistent_dates_and_states(changes, message):
    with pytest.raises(ValueError, match=message):
        registry.validate_outlets([_outlet(**changes)])


def test_the_iol_entries_carry_the_sourced_independent_media_stake():
    by_id = {o["id"]: o for o in registry.load_outlets()}
    for oid in ("za_iol", "za_independent"):
        stakes = by_id[oid].get("foreign_state_stake")
        assert stakes, oid
        s = stakes[0]
        assert s["state"] == "CHN" and s["share_percent"] == 20 and s["from"] == "2013-08"
        assert "China International Television Corporation" in s["holder"] and "China-Africa Development Fund" in s["holder"]
        assert set(s["holder_type"]) == {"state_media_group", "state_fund"}
        assert "Independent News & Media South Africa" in s["held_in"]
        assert s["source_url"].startswith("https://mg.co.za/") and s["status_source_url"].startswith("https://www.dailymaverick.co.za/")
        # Sources conflict on whether the stake still reaches IOL, so it is disputed, never shown as current.
        assert s["status"] == "disputed" and s["to"] is None and s["as_of"] == "2026-07-22"
        assert "Sagarmatha" in s["note"] and "2014" in s["note"]


def test_every_recorded_stake_is_foreign_and_sourced():
    for o in registry.load_outlets():
        for s in o.get("foreign_state_stake") or []:
            assert s["state"] != o["country"]
            assert s["source_url"].startswith("http") and s["status_source_url"].startswith("http")


def test_save_outlets_keeps_the_field(tmp_path):
    path = tmp_path / "outlets.yaml"
    registry.save_outlets([_outlet()], path)
    back = registry.load_outlets(path)
    assert back[0]["foreign_state_stake"] == [STAKE]


def test_export_carries_the_field(tmp_path):
    from pipeline import export, store
    conn = store.connect(tmp_path / "s.db")
    with_stake, without = _outlet(), dict(copy.deepcopy(BASE), id="za_y", feeds=["https://y.co.za/rss"])
    out = {o["id"]: o for o in export.build_outlets(conn, [with_stake, without])["outlets"]}
    assert out["za_x"]["foreign_state_stake"] == [STAKE]
    assert out["za_y"]["foreign_state_stake"] is None


def test_the_site_shows_stakes_in_the_country_panel_and_explains_absence():
    app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    compute = (ROOT / "docs" / "compute.js").read_text(encoding="utf-8")
    index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "stakeNotes: stakeNotes" in compute
    assert "C.stakeNotes(o.foreign_state_stake)" in app and "+ stakeLines(o) +" in app
    assert ".stake-note" in (ROOT / "docs" / "style.css").read_text(encoding="utf-8")
    assert "<strong>Foreign state ownership stakes.</strong>" in index
    assert "has none recorded, which is not the same as having none" in index
    meth = (ROOT / "pipeline" / "methodology.py").read_text(encoding="utf-8")
    assert "Foreign state ownership stakes." in meth and "does not mean it has\nnone" in meth
