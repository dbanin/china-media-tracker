"""Outlets selected for their China coverage, not reach (RULES D12, 2026-10-28). These run offline."""
import pytest

from pipeline import export, methodology, registry, store

EVIDENCE = "https://chinadigitaltimes.net/2024/11/cdt-report-borrowed-boats-the-prcs-embedded-propaganda-in-local-african-media/"


def _outlet(**kw):
    o = {"id": "gh_x", "name": "X", "country": "GHA", "language": "en", "feeds": ["https://x.example/feed/"],
         "tier": "national_daily", "active": True, "collector": "hosted"}
    o.update(kw)
    return o


def test_schema_and_validator_accept_a_sourced_basis():
    registry.validate_outlets([_outlet(selection_basis=registry.SELECTED_FOR_CHINA + EVIDENCE)])


@pytest.mark.parametrize("basis", ["", "selected for China coverage", "selected for China coverage, not reach",
                                   "not reach; evidence: none"])
def test_basis_without_evidence_rejected(basis):
    with pytest.raises(Exception):
        registry.validate_outlets([_outlet(selection_basis=basis)])


def test_selected_outlet_cannot_carry_audience_rank():
    with pytest.raises(ValueError):
        registry.validate_outlets([_outlet(selection_basis=registry.SELECTED_FOR_CHINA + EVIDENCE, audience_rank=3)])


def test_helpers():
    sel = _outlet(selection_basis=registry.SELECTED_FOR_CHINA + EVIDENCE)
    reach = _outlet(id="gh_y", feeds=["https://y.example/feed/"])
    assert not registry.reach_selected(sel) and registry.reach_selected(reach)
    assert registry.selection_evidence(sel) == EVIDENCE and registry.selection_evidence(reach) is None
    assert registry.reach_outlets([sel, reach]) == [reach]
    s = registry.selection_summary([sel, reach, dict(sel, id="gh_z", active=False, inactive_reason="test")])
    assert s == {"china_selected_active": 1, "by_country": {"GHA": 1}, "ids": ["gh_x"]}


def test_save_outlets_keeps_the_field(tmp_path):
    p = tmp_path / "o.yaml"
    o = _outlet(selection_basis=registry.SELECTED_FOR_CHINA + EVIDENCE)
    registry.save_outlets([o], p)
    back = registry.load_outlets(p)
    assert back[0]["selection_basis"] == o["selection_basis"]


def test_outlets_json_flags_selection(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    out = export.build_outlets(conn, [_outlet(selection_basis=registry.SELECTED_FOR_CHINA + EVIDENCE),
                                      _outlet(id="gh_y", feeds=["https://y.example/feed/"])])["outlets"]
    assert out[0]["reach_selected"] is False and out[0]["selection_evidence"] == EVIDENCE
    assert out[0]["selection_basis"].startswith("selected for China coverage")
    assert out[1]["reach_selected"] is True and out[1]["selection_basis"] is None


def test_methodology_names_the_selection():
    text = methodology.selection_text({"selection_basis": {"china_selected_active": 2, "by_country": {"ZAF": 1, "GHA": 1}}})
    assert "2 active outlets in 2 countries" in text and "reach_selected" in text
    assert "No active outlet was selected this way" in methodology.selection_text({})
    assert "—" not in text and "–" not in text


# Added 2026-10-28 (small_africa_outlets): every feed validated with pipeline/validate_sources.check_outlet
# (the tracker's user agent, robots.txt honoured) and sample articles read without a paywall.
ADDED_2026_10_28 = {
    "NGA": ("ng_theniche", "ng_authority"),
    "ZWE": ("zw_zimbabwemail",),
    "UGA": ("ug_ubc",),
    "MOZ": ("mz_aim",),
    "ZAF": ("za_isolezwe",),
}


def test_added_small_africa_outlets_registered():
    from pipeline import gate
    by_id = {o["id"]: o for o in registry.load_outlets()}
    langs = gate.supported_languages()
    for country, ids in ADDED_2026_10_28.items():
        for oid in ids:
            o = by_id[oid]
            assert o["country"] == country and o["active"] is True, oid
            assert o.get("collector") == "hosted", oid
            assert o["language"] in langs and o["language"] != "zh", oid
            assert not registry.reach_selected(o) and registry.selection_evidence(o), oid
            assert not o.get("audience_rank"), oid
            assert "Verified 2026-10-28" in o["notes"], oid
    summary = registry.selection_summary(registry.load_outlets())
    assert set(summary["ids"]) >= {i for ids in ADDED_2026_10_28.values() for i in ids}
