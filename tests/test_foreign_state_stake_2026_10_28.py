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


# Stress test of 2026-10-29: bad records, the save round trip, old export shapes, the site's rendering and wording.

@pytest.mark.parametrize("changes", [
    {"source_url": "http://example.org/a"},       # the page links the source: https only
    {"status_source_url": "https://"},
    {"source_url": "https://exa mple.org/a"},
    {"source_url": "javascript:alert(1)"},
    {"holder": "   "},
    {"held_in": "\t"},
    {"source_title": " "},
    {"share_percent": True},
    {"share_percent": -5},
    {"holder_type": ["state_fund", "state_fund"]},
    {"note": None},
])
def test_schema_rejects_more_malformed_stakes(changes):
    with pytest.raises(jsonschema.ValidationError):
        registry.validate_outlets([_outlet(**changes)])


@pytest.mark.parametrize("changes, message", [
    ({"from": "2013-13"}, "not a calendar date"),
    ({"as_of": "2026-02-30"}, "not a calendar date"),
    ({"status": "ended", "to": "2018-00", "as_of": ...}, "not a calendar date"),
    ({"as_of": "2099-01-01"}, "after today"),
    ({"from": "2099", "as_of": "2099-02-01"}, "after today"),
    ({"status": "ended", "to": "2099-01", "as_of": ...}, "after today"),
    ({"state": "ZZZ"}, "not an ISO 3166"),
])
def test_validator_rejects_impossible_dates_and_codes(changes, message):
    with pytest.raises(ValueError, match=message):
        registry.validate_outlets([_outlet(**changes)])


def test_validator_compares_dates_of_mixed_precision_and_today():
    registry.validate_stakes(_outlet(**{"from": "2026", "as_of": "2026-10-28"}), today="2026-10-28")
    registry.validate_stakes(_outlet(**{"from": "2026-10", "as_of": "2026-10-28"}), today="2026-10-28")
    with pytest.raises(ValueError, match="after today"):
        registry.validate_stakes(_outlet(**{"from": "2026-11", "as_of": ...}), today="2026-10-28")
    with pytest.raises(ValueError, match="after today"):
        registry.validate_stakes(_outlet(as_of="2026-10-29"), today="2026-10-28")


def test_validator_rejects_the_same_stake_twice():
    o = _outlet()
    again = dict(o["foreign_state_stake"][0], holder="  a STATE   fund ", as_of="2026-08-01")
    o["foreign_state_stake"].append(again)
    with pytest.raises(ValueError, match="recorded twice"):
        registry.validate_outlets([o])
    o["foreign_state_stake"][1]["from"] = "2014"           # a second stake by the same holder, a year later
    registry.validate_outlets([o])


def test_several_stakes_unicode_and_long_names_validate():
    o = _outlet(holder="Société d'État, مؤسسة الدولة, " + "x" * 3000, held_in="Groupe Média SA")
    o["foreign_state_stake"].append(dict(STAKE, state="RUS", holder="Another state fund", share_percent=4.1, status="ended", to="2020"))
    registry.validate_outlets([o])


def test_save_round_trip_keeps_field_and_key_order(tmp_path):
    path = tmp_path / "outlets.yaml"
    o = _outlet(note="Unicode survives: Survé, Société, مؤسسة.")
    o["ownership"] = "private"
    o["government_stance"] = "unassessed"
    registry.save_outlets([o], path)
    text = path.read_text(encoding="utf-8")
    assert text.index("ownership:") < text.index("foreign_state_stake:") < text.index("government_stance:") < text.index("active:")
    back = registry.load_outlets(path)
    assert list(back[0]["foreign_state_stake"][0]) == list(o["foreign_state_stake"][0])
    assert back[0]["foreign_state_stake"] == o["foreign_state_stake"]
    registry.save_outlets(back, path)
    assert path.read_text(encoding="utf-8") == text          # saving again changes nothing


def test_the_real_registry_round_trips_its_stakes(tmp_path):
    outlets = registry.load_outlets()
    path = tmp_path / "outlets.yaml"
    registry.save_outlets(outlets, path)
    back = {o["id"]: o for o in registry.load_outlets(path)}
    for o in outlets:
        if o.get("foreign_state_stake"):
            assert back[o["id"]]["foreign_state_stake"] == o["foreign_state_stake"]
            assert [list(s) for s in back[o["id"]]["foreign_state_stake"]] == [list(s) for s in o["foreign_state_stake"]]


def test_the_iol_records_say_only_what_their_sources_say():
    by_id = {o["id"]: o for o in registry.load_outlets()}
    for oid in ("za_iol", "za_independent"):
        s = by_id[oid]["foreign_state_stake"][0]
        # The state link is the cited sources' own words; CITVC's parent is in none of them.
        assert "CCTV" not in s["state_link"] and "wholly owned" not in s["state_link"]
        assert "state media company" in s["state_link"] and "state policy bank" in s["state_link"]
        assert s["holder"].startswith("Interacom Holdings")      # the name amaBhungane gives
        assert "4.1 percent" in s["note"] and "31 December 2016" in s["note"]
        assert "not a finding that the shareholders direct" in s["note"]
        assert len(by_id[oid]["foreign_state_stake"]) == 1


def test_isolezwe_carries_the_stake_as_ended_when_its_title_moved():
    """Freedom House (2022) names Isolezwe among Independent Media's papers; since August 2026 National Media
    Group publishes it under licences from Sagarmatha Technologies (Daily Maverick, 5 August 2026), and no
    source documents a Chinese state stake there, so the record ends for Isolezwe in July 2026."""
    o = {x["id"]: x for x in registry.load_outlets()}["za_isolezwe"]
    s, = o["foreign_state_stake"]
    assert s["state"] == "CHN" and s["share_percent"] == 20 and s["from"] == "2013-08"
    assert s["status"] == "ended" and s["to"] == "2026-07" and "as_of" not in s
    assert s["source_url"] == "https://freedomhouse.org/country/south-africa/beijings-global-media-influence/2022"
    assert "2026-08-04-different-name-same-network" in s["status_source_url"]
    assert "until the end of July 2026" in s["held_in"]
    assert "Ended for Isolezwe, not for Independent Media" in s["note"] and "National Media Group" in s["note"]
    assert "National Media Group since August 2026" in o["notes"] and "isiZulu daily of Independent Media" not in o["notes"]


def test_only_the_independent_media_titles_carry_a_stake():
    with_stake = sorted(o["id"] for o in registry.load_outlets() if o.get("foreign_state_stake"))
    assert with_stake == ["za_independent", "za_iol", "za_isolezwe"]


def test_old_export_without_the_field_and_new_export_with_it(tmp_path):
    """outlets.json from before the field (schema 18) has no key at all; the site treats it like null."""
    import json
    old = json.loads((ROOT / "docs" / "data" / "outlets.json").read_text(encoding="utf-8"))
    assert isinstance(old["outlets"], list)
    compute = (ROOT / "docs" / "compute.js").read_text(encoding="utf-8")
    assert "Array.isArray(stakes) ? stakes : []" in compute
    from pipeline import export, store
    conn = store.connect(tmp_path / "s.db")
    out = export.build_outlets(conn, registry.load_outlets())["outlets"]
    by_id = {o["id"]: o for o in out}
    assert by_id["za_isolezwe"]["foreign_state_stake"][0]["status"] == "ended"
    assert all("foreign_state_stake" in o for o in out)
    assert sum(1 for o in out if o["foreign_state_stake"]) == 3


def test_the_panel_lines_render_safely_and_say_the_status_aloud(tmp_path):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    stub = ('var document = {readyState: "loading", addEventListener: function () {}, getElementById: function () { return null; },'
            ' querySelector: function () { return null; }, querySelectorAll: function () { return []; }};')
    files = [ROOT / "docs" / "vendor" / "d3.v7.min.js", ROOT / "docs" / "compute.js", ROOT / "docs" / "app.js", ROOT / "tests" / "smoke_stakes.js"]
    shim = tmp_path / "shim.js"
    shim.write_text("const fs = require('fs'), vm = require('vm');\n"
                    "const ctx = {console: console, Date: Date}; ctx.self = ctx; ctx.window = ctx; vm.createContext(ctx);\n"
                    "vm.runInContext(%r, ctx);\n" % stub
                    + "".join("vm.runInContext(fs.readFileSync(%r, 'utf8'), ctx);\n" % str(f) for f in files), encoding="utf-8")
    r = subprocess.run([node, str(shim)], capture_output=True, text=True)
    assert r.returncode == 0 and "stakes smoke ok" in r.stdout, r.stdout + r.stderr


def test_the_wording_never_claims_none_or_control():
    index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    para = index[index.index("<strong>Foreign state ownership stakes.</strong>"):]
    para = para[:para.index("</p>")]
    assert "A stake records ownership; it is not a finding about editorial control." in para
    assert "a subsidiary of CCTV" not in para and "ended for Isolezwe" in para and "disputed for IOL" in para
    meth = (ROOT / "pipeline" / "methodology.py").read_text(encoding="utf-8")
    assert "A stake records ownership; it is not a finding about\neditorial control." in meth
    app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    assert "html += stakeAbsenceNote(" in app and "' title=\"' + esc(n.note)" not in app
    css = (ROOT / "docs" / "style.css").read_text(encoding="utf-8")
    assert ".stake-status.is-disputed" in css and "overflow-wrap: anywhere" in css.split(".stake-note {")[1].split("}")[0]
    # The outlet column keeps a readable width wherever the panel is narrow (phone, or the wide screen's side panel).
    assert ".stake-note { min-width: min(15rem, calc(100vw - 7rem)); }" in css
