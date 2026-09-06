"""Load and validate the outlet registry and the coverage gaps file."""
import json
from pathlib import Path
from typing import Dict, List

import jsonschema
import yaml

from pipeline import config


_ALPHA2 = None


def _alpha2_map() -> Dict[str, str]:
    """ISO alpha-3 to alpha-2, from the vendored ISO 3166 table. Kosovo is added as XKX/XK."""
    global _ALPHA2
    if _ALPHA2 is None:
        table = config.ROOT / "docs" / "vendor" / "iso3166.json"
        m = {}
        if table.exists():
            with open(table, "r", encoding="utf-8") as fh:
                for row in json.load(fh):
                    m[row["alpha-3"]] = row["alpha-2"]
        m["XKX"] = "XK"
        _ALPHA2 = m
    return _ALPHA2


def load_outlets(path: Path = config.OUTLETS_PATH, validate: bool = True) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as fh:
        outlets = yaml.safe_load(fh) or []
    if validate:
        validate_outlets(outlets)
    return outlets


def validate_outlets(outlets: List[Dict]) -> None:
    with open(config.OUTLETS_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.validate(outlets, schema)
    ids = [o["id"] for o in outlets]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError("duplicate outlet ids: %s" % ", ".join(dupes))
    alpha2 = _alpha2_map()
    for o in outlets:
        prefix = o["id"].split("_", 1)[0]
        allowed = {o["country"].lower(), o["country"].lower()[:2], alpha2.get(o["country"], "").lower()}
        if prefix not in allowed:
            raise ValueError("outlet id %s does not start with the ISO alpha-2 prefix for %s" % (o["id"], o["country"]))
        if o["language"] == "zh" and o["active"]:
            raise ValueError(
                "outlet %s publishes in Chinese and must be inactive with an inactive_reason" % o["id"]
            )
        if not o["active"] and not o.get("inactive_reason"):
            raise ValueError("inactive outlet %s needs an inactive_reason" % o["id"])


def load_gaps(path: Path = config.GAPS_PATH, validate: bool = True) -> List[Dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as fh:
        gaps = yaml.safe_load(fh) or []
    if validate:
        with open(config.GAPS_SCHEMA_PATH, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        jsonschema.validate(gaps, schema)
    return gaps


def save_outlets(outlets: List[Dict], path: Path = config.OUTLETS_PATH) -> None:
    """Write the registry back. Preserves key order used across the file."""
    key_order = ["id", "name", "country", "language", "feeds", "tier", "notes", "active",
                 "inactive_reason", "inactive_since"]
    ordered = []
    for o in outlets:
        ordered.append({k: o[k] for k in key_order if k in o})
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(ordered, fh, allow_unicode=True, sort_keys=False, width=200)


def active_outlets(outlets: List[Dict]) -> List[Dict]:
    return [o for o in outlets if o.get("active")]


def registry_summary(outlets: List[Dict]) -> Dict:
    active = active_outlets(outlets)
    countries = {}
    for o in active:
        countries.setdefault(o["country"], 0)
        countries[o["country"]] += 1
    counts = sorted(countries.values())
    unevenness = None
    if counts:
        median = counts[len(counts) // 2]
        unevenness = {"min": counts[0], "median": median, "max": counts[-1],
                      "max_over_median": round(counts[-1] / float(median), 2) if median else None,
                      "countries_with_one_outlet": sum(1 for c in counts if c == 1)}
    return {
        "outlets_total": len(outlets),
        "outlets_active": len(active),
        "countries_covered": len(countries),
        "per_country": countries,
        "unevenness": unevenness,
    }


if __name__ == "__main__":
    outlets = load_outlets()
    gaps = load_gaps()
    s = registry_summary(outlets)
    print("outlets: %d total, %d active, %d countries, %d gap entries" % (
        s["outlets_total"], s["outlets_active"], s["countries_covered"], len(gaps)))
    for c in sorted(s["per_country"], key=lambda k: -s["per_country"][k]):
        print("  %s %d" % (c, s["per_country"][c]))


TOP_OUTLETS_PER_COUNTRY = 30


def population_source(path=None) -> str:
    import json
    path = path or (config.ROOT / "sources" / "population.json")
    with open(path, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    return "%s; %s" % (d["sources"]["world_bank"], d["sources"]["other"])


def load_population(path=None):
    """ISO3 -> resident population from sources/population.json."""
    import json
    path = path or (config.ROOT / "sources" / "population.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)["population"]


def top_outlets(outlets):
    """Per country, the outlet ids that form the denominator for the share of all published
    items: the TOP_OUTLETS_PER_COUNTRY best audience_rank values when any outlet in the
    country carries a rank, otherwise every active outlet. Returns (ids_by_country, ranked_countries)."""
    by_country = {}
    for o in outlets:
        by_country.setdefault(o["country"], []).append(o)
    ids = {}
    ranked = []
    for country, os_ in by_country.items():
        with_rank = sorted([o for o in os_ if o.get("audience_rank")], key=lambda o: o["audience_rank"])
        if with_rank:
            ids[country] = {o["id"] for o in with_rank[:TOP_OUTLETS_PER_COUNTRY]}
            ranked.append(country)
        else:
            ids[country] = {o["id"] for o in os_ if o["active"]}
    return ids, sorted(ranked)
