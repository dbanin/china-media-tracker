"""Registry validation tests. These run offline."""
import pytest

from pipeline import registry


def test_registry_validates():
    outlets = registry.load_outlets()
    assert len(outlets) >= 30


def test_study_countries_dense():
    outlets = registry.active_outlets(registry.load_outlets())
    per = {}
    for o in outlets:
        per[o["country"]] = per.get(o["country"], 0) + 1
    for c in ("ITA", "CAN", "AUS"):
        assert per.get(c, 0) >= 10, "study country %s has fewer than 10 active outlets" % c


def test_chinese_language_outlets_inactive():
    for o in registry.load_outlets():
        if o["language"] == "zh":
            assert o["active"] is False
            assert o.get("inactive_reason")


def test_inactive_have_reason():
    for o in registry.load_outlets():
        if not o["active"]:
            assert o.get("inactive_reason"), o["id"]


def test_gaps_validate():
    registry.load_gaps()


def test_duplicate_ids_rejected():
    outlets = registry.load_outlets()
    bad = outlets + [outlets[0]]
    with pytest.raises(ValueError):
        registry.validate_outlets(bad)


# Methodology review 2026-10-12: international broadcasters and the US based leaning rater.
INTERNATIONAL = ("us_rfa", "kh_rfakhmer", "mm_rfaburmese", "kp_rfa", "la_rfalao", "ge_radiotavisupleba", "de_dw", "de_dw_en",
                 "ru_dw_ru", "fr_france24", "fr_france24_en", "fr_rfi", "fr_rfi_en", "br_rfi_pt", "in_bbchindi", "mm_bbcburmese")
DOMESTIC_PUBLIC = ("gb_bbc", "au_abc", "au_sbs", "ca_radiocanada", "jp_nhk_ja", "de_tagesschau", "fi_yle", "nl_nos")


def test_international_broadcasters_apart_from_domestic_public_service():
    by_id = {o["id"]: o for o in registry.load_outlets()}
    for oid in INTERNATIONAL:
        assert by_id[oid]["ownership"] == "state_funded_international", oid
        assert "state_funded_international" in by_id[oid]["notes"], oid
    for oid in DOMESTIC_PUBLIC:
        assert by_id[oid]["ownership"] == "public_service", oid
    # state run outlets without editorial independence stay state, at home or abroad
    for oid in ("ru_rt", "kg_sputnik"):
        assert by_id[oid]["ownership"] == "state", oid


def test_schema_rejects_unknown_ownership():
    o = dict(registry.load_outlets()[0], ownership="state_funded_abroad")
    with pytest.raises(Exception):
        registry.validate_outlets([o])


def test_leaning_source_weak_rule():
    mbfc = "https://mediabiasfactcheck.com/example/"
    base = {"country": "FRA", "political_leaning": "centre", "leaning_source": mbfc}
    assert registry.leaning_source_weak(base)
    assert not registry.leaning_source_weak(dict(base, country="USA"))
    assert not registry.leaning_source_weak(dict(base, leaning_source="https://en.wikipedia.org/wiki/Le_Monde"))
    assert not registry.leaning_source_weak(dict(base, political_leaning="unassessed"))
    assert not registry.leaning_source_weak(dict(base, political_leaning="state_controlled"))
    assert not registry.leaning_source_weak(dict(base, leaning_second_source="https://www.eurotopics.net/en/148437/"))
    assert registry.leaning_source_weak(dict(base, leaning_second_source="https://www.mediabiasfactcheck.com/other/"))
    assert registry.leaning_source_weak(dict(base, leaning_source="https://www.mediabiasfactcheck.com/example/"))


def test_registry_weak_leanings_are_the_non_us_mbfc_ones():
    outlets = registry.load_outlets()
    weak = [o for o in outlets if registry.leaning_source_weak(o)]
    assert weak, "the registry has Media Bias/Fact Check leanings outside the US"
    assert all(o["country"] != "USA" and "mediabiasfactcheck.com" in o["leaning_source"] for o in weak)
    assert not any(registry.leaning_source_weak(o) for o in outlets if o["country"] == "USA")


def test_export_carries_weak_mark_and_international_group():
    from pipeline import export
    assert "state_funded_international" in export.OWNERSHIP_GROUPS
    oj = {"outlets": [
        {"active": True, "tier": "broadcaster", "ownership": "state_funded_international", "counts": {"A": 3}},
        {"active": True, "tier": "broadcaster", "ownership": "public_service", "counts": {"A": 1}},
    ]}
    groups = {g["id"]: g for g in export.by_ownership_findings(oj)["groups"]}
    assert groups["state_funded_international"]["articles"] == 3 and groups["public_service"]["articles"] == 1


def test_build_outlets_exports_weak_mark(tmp_path):
    from pipeline import export, store
    conn = store.connect(tmp_path / "w.db")
    outlets = [{"id": "fr_x", "name": "X", "country": "FRA", "language": "fr", "feeds": ["https://x.fr/rss"], "tier": "national_daily",
                "active": True, "political_leaning": "centre", "leaning_source": "https://mediabiasfactcheck.com/x/",
                "ownership": "state_funded_international", "ownership_source": "https://en.wikipedia.org/wiki/X"}]
    out = export.build_outlets(conn, outlets)["outlets"][0]
    assert out["leaning_source_weak"] is True and out["ownership"] == "state_funded_international"
    assert out["leaning_second_source"] is None
