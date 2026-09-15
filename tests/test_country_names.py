"""Country names shown on the site and in METHODOLOGY.md read as common English names."""
import json

from pipeline import config, methodology, registry


def _iso():
    with open(config.ROOT / "docs" / "vendor" / "iso3166.json", encoding="utf-8") as fh:
        return {r["alpha-3"]: r["name"] for r in json.load(fh)}


def test_overrides_are_valid_codes():
    with open(config.ROOT / "docs" / "country-names.json", encoding="utf-8") as fh:
        names = json.load(fh)["names"]
    iso = _iso()
    assert all(code in iso or code == "XKX" for code in names)


def test_no_inverted_or_formal_names():
    for code in _iso():
        name = registry.country_name(code)
        for bad in (", Republic of", ", State of", ", Province of", "People's Democratic", "of America",
                    "Islamic Republic", "Bolivarian", "Plurinational", "Federated States", "Kingdom of the", "(Malvinas)"):
            assert bad not in name, (code, name)


def test_common_names():
    assert registry.country_name("KOR") == "South Korea"
    assert registry.country_name("PRK") == "North Korea"
    assert registry.country_name("USA") == "United States"
    assert registry.country_name("TWN") == "Taiwan"
    assert registry.country_name("XKX") == "Kosovo"
    assert registry.country_name("FRA") == "France"


def test_methodology_does_not_repeat_category_names():
    assert "State origin. {cat_a}" not in methodology.TEMPLATE
