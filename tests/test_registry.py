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
