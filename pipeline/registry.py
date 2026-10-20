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


_NAMES = None


def country_name(iso: str) -> str:
    """Common English name for an ISO alpha-3 code: docs/country-names.json first, then the ISO short name."""
    global _NAMES
    if _NAMES is None:
        m = {}
        table = config.ROOT / "docs" / "vendor" / "iso3166.json"
        if table.exists():
            with open(table, "r", encoding="utf-8") as fh:
                for row in json.load(fh):
                    m[row["alpha-3"]] = row["name"]
        common = config.ROOT / "docs" / "country-names.json"
        if common.exists():
            with open(common, "r", encoding="utf-8") as fh:
                m.update(json.load(fh).get("names", {}))
        _NAMES = m
    return _NAMES.get(iso, iso)


# The C YAML parser where PyYAML was built with libyaml (the pure Python one took 2.5 s per parse of
# sources/outlets.yaml, four parses an export). Both are safe loaders and read the registry the same way.
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
# Parsed registries by (path, mtime, size): {"blob": pickled outlets, "valid": schema key it was validated
# against}. tests/conftest.py keeps its own cache of the same shape for the real file.
_OUTLETS_CACHE: Dict = {}


def _file_key(path: Path):
    st = Path(path).stat()
    return str(Path(path).resolve()), st.st_mtime_ns, st.st_size


def load_outlets(path: Path = config.OUTLETS_PATH, validate: bool = True) -> List[Dict]:
    """The registry, parsed once per version of the file (path, mtime, size) and validated once per
    version of the file and of the schema; every call gets its own fresh copy, which it may change."""
    import pickle
    key = _file_key(path)
    entry = _OUTLETS_CACHE.get(key)
    if entry is None:
        with open(path, "r", encoding="utf-8") as fh:
            outlets = yaml.load(fh, Loader=_YAML_LOADER) or []
        if len(_OUTLETS_CACHE) > 8:
            _OUTLETS_CACHE.clear()
        entry = _OUTLETS_CACHE[key] = {"blob": pickle.dumps(outlets), "valid": None}
    outlets = pickle.loads(entry["blob"])
    if validate:
        schema = _file_key(config.OUTLETS_SCHEMA_PATH)
        if entry["valid"] != schema:
            validate_outlets(outlets)       # raises on every call while the file is invalid
            entry["valid"] = schema
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
        junk = o.get("junk_feed")
        if junk:
            # pipeline/junk_feeds.py withdraws the outlet's whole discovery from since until `until`, so
            # an outlet still collecting needs the day it left the feed, and must not poll it any more.
            if junk.get("until") and junk["until"] <= junk["since"]:
                raise ValueError("outlet %s: junk_feed until must come after since" % o["id"])
            if o["active"] and not junk.get("until"):
                raise ValueError("active outlet %s needs junk_feed until, the day it stopped polling the feed" % o["id"])
            if o["active"] and junk["url"] in {e["url"] for e in feed_entries(o)}:
                raise ValueError("active outlet %s still polls its junk_feed %s" % (o["id"], junk["url"]))
    shared = shared_feed_urls(outlets)
    if shared:
        raise ValueError("editorial feed polled by more than one active outlet (items are credited to whichever outlet "
                         "polls first, and its feed health flips between them): %s" % "; ".join(
                             "%s on %s" % (u, ", ".join(ids)) for u, ids in sorted(shared.items())))


def feed_key(url: str) -> str:
    """A feed URL as the duplicate check compares it: scheme, a leading www. and a trailing slash
    dropped and the host lower cased, so http://www.x.com/feed/ and https://x.com/feed are one feed."""
    from urllib.parse import urlsplit
    p = urlsplit(url.strip())
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    path = p.path.rstrip("/")
    return host + path + ("?" + p.query if p.query else "")


def shared_feed_urls(outlets: List[Dict]) -> Dict[str, List[str]]:
    """Every editorial feed (RSS or news sitemap) that two or more active outlets poll:
    {url: [outlet ids]}. An editorial feed belongs to one outlet: the first outlet to poll an item is
    credited with it, so a feed shared by two editions (NZZ, Infobae) or listed under two countries
    (Daily NK) gave one edition all the items, the other none, and feed health that flipped between
    them. Press release, sponsored and partner section feeds are left out on purpose: one network's
    sponsored section (France 24, RFI) or release page (RNZ) serves its editions by design, like the
    AAP widget mirrored to the ACM mastheads."""
    by_key = {}
    for o in outlets:
        if not o.get("active"):
            continue
        for e in feed_entries(o):
            if e["kind"] != EDITORIAL:
                continue
            by_key.setdefault(feed_key(e["url"]), (e["url"], []))[1].append(o["id"])
    return {u: sorted(set(ids)) for u, ids in by_key.values() if len(set(ids)) > 1}


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
    key_order = ["id", "name", "country", "language", "feeds", "news_sitemaps", "section_feeds", "release_sections", "tier", "network", "notes",
                 "audience_rank", "rank_source", "political_leaning", "leaning_source", "leaning_second_source", "ownership", "ownership_source",
                 "government_stance", "stance_source", "stance_as_of",
                 "collector", "collector_since", "active", "inactive_reason", "inactive_since", "junk_feed"]
    # Every field the schema allows must be listed, or saving drops it silently. The weekly feed
    # validation saves through here, so a missing key would erase that field from the whole registry.
    with open(config.OUTLETS_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        schema = json.load(fh)
    allowed = set((schema.get("items") or schema)["properties"])
    missing = allowed - set(key_order)
    if missing:
        raise ValueError("save_outlets would drop schema fields: %s" % ", ".join(sorted(missing)))
    ordered = []
    for o in outlets:
        unknown = set(o) - set(key_order)
        if unknown:
            raise ValueError("outlet %s has fields save_outlets would drop: %s" % (o.get("id"), ", ".join(sorted(unknown))))
        ordered.append({k: o[k] for k in key_order if k in o})
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(ordered, fh, allow_unicode=True, sort_keys=False, width=200)


EDITORIAL = "editorial"
GATE_EXEMPT_KINDS = ("press_release", "sponsored", "partner")


def feed_entries(outlet: Dict) -> List[Dict]:
    """Every feed the outlet is polled on, as {url, kind}: the editorial feeds first, then the
    press release, sponsored and partner section feeds found by pipeline/discover_sections.py.
    The editorial feeds are the RSS feeds, then the Google News sitemaps (news_sitemaps, type
    news_sitemap, pipeline/news_sitemap.py), read after the RSS so an item on both keeps its summary."""
    out = [{"url": u, "kind": EDITORIAL, "type": "rss"} for u in outlet.get("feeds", [])]
    seen = {e["url"] for e in out}
    for u in outlet.get("news_sitemaps", []) or []:
        if u not in seen:
            out.append({"url": u, "kind": EDITORIAL, "type": "news_sitemap"})
            seen.add(u)
    for f in outlet.get("section_feeds", []) or []:
        if f["url"] not in seen:
            out.append({"url": f["url"], "kind": f["kind"], "type": f.get("type", "rss")})
            seen.add(f["url"])
    return out


# A global press release distribution service is not a national newsroom. Its releases are written
# by their issuers and pushed worldwide, so counting them against the country the service is
# registered in says nothing about that country's media: 118 of the United States' 122 state origin
# labels came from one such service, in several languages, none of them placed in a US newsroom.
# They are still collected and still classified, and they are counted as a stratum of their own,
# "Worldwide distribution", under the pseudo country code WLD (the World Bank's code for the world):
# a release reaches audiences in every country, so it is never attributed to the United States and
# never added into any individual country's numbers, which would count it once per country.
# The export groups every wire under WLD; sources/outlets.yaml keeps the registry country.
DISTRIBUTION_WIRE = "distribution_wire"
WORLDWIDE = "WLD"
WORLDWIDE_NAME = "Worldwide distribution"


def is_distribution_wire(outlet: Dict) -> bool:
    return outlet.get("tier") == DISTRIBUTION_WIRE


def distribution_wire_ids(outlets: List[Dict]) -> set:
    return {o["id"] for o in outlets if is_distribution_wire(o)}


def active_outlets(outlets: List[Dict]) -> List[Dict]:
    """Active outlets that stand for a country. Distribution wires are excluded: they form the
    worldwide distribution stratum, never a country's numerator or denominator."""
    return [o for o in outlets if o.get("active") and not is_distribution_wire(o)]


def active_wires(outlets: List[Dict]) -> List[Dict]:
    """The active distribution wires: the outlets behind the worldwide distribution stratum."""
    return [o for o in outlets if o.get("active") and is_distribution_wire(o)]


def export_country(outlet: Dict) -> str:
    """The country an outlet is counted under in the export: WLD for a distribution wire, the
    registry country for everything else."""
    return WORLDWIDE if is_distribution_wire(outlet) else outlet["country"]


def active_including_wires(outlets: List[Dict]) -> List[Dict]:
    """Everything that is polled, wires included. Used by collection, never by a country figure."""
    return [o for o in outlets if o.get("active")]


def collector_of(outlet: Dict) -> str:
    return outlet.get("collector") or "hosted"


# collector: both puts an outlet on both schedules. GitHub drops many scheduled runs, so the hosted
# runner reached each feed a median 2.7 hours apart in the 30 days to 2026-10-12 (3.6 hours in the
# last week), and a feed that carries 20 items or fewer rolled past items between polls: 221 such
# feeds were saturated in more than a tenth of their polls. The relay on the owner's machine polls
# hourly; with both schedules the median gap is about an hour. Both collectors poll the feeds, the
# hosted side ingests the relay's items by url_hash like any other relay item, and article pages are
# fetched once, by fetcher_of: the hosted runner for a shared outlet, which it can reach (an outlet
# that refuses the runner is self_hosted, not both).
HOSTED, SELF_HOSTED, BOTH = "hosted", "self_hosted", "both"


def polled_by(outlet: Dict, collector: str = None) -> bool:
    """Whether this collector polls the outlet's feeds: its own outlets and the shared ones."""
    c = collector_of(outlet)
    return c == (collector or config.COLLECTOR) or c == BOTH


def fetcher_of(outlet: Dict) -> str:
    """The one collector that fetches the outlet's article pages."""
    c = collector_of(outlet)
    return HOSTED if c == BOTH else c


def is_shared(outlet: Dict) -> bool:
    """Polled by both collectors."""
    return collector_of(outlet) == BOTH


# Media Bias/Fact Check is a US based rater that places outlets on a United States spectrum. Where it
# is the only source behind an assessed leaning outside the United States, the leaning is kept but
# marked weak, and the site shows it with a caution. A second source that is not this rater lifts it.
US_RATER_HOSTS = ("mediabiasfactcheck.com",)
LEANING_NOT_RATED = ("unassessed", "state_controlled")


def _host(url) -> str:
    from urllib.parse import urlsplit
    h = urlsplit(url or "").netloc.lower()
    return h[4:] if h.startswith("www.") else h


def is_us_rater(url) -> bool:
    h = _host(url)
    return any(h == u or h.endswith("." + u) for u in US_RATER_HOSTS)


def leaning_source_weak(outlet: Dict) -> bool:
    """True when an outlet outside the United States has an assessed left/right leaning whose only
    source is a US based rater. State controlled and unassessed leanings are not ratings."""
    if (outlet.get("political_leaning") or "unassessed") in LEANING_NOT_RATED:
        return False
    if outlet.get("country") == "USA" or not is_us_rater(outlet.get("leaning_source")):
        return False
    second = outlet.get("leaning_second_source")
    return not (second and not is_us_rater(second))


def is_mine(outlet_id: str, outlets: List[Dict] = None, collector: str = None) -> bool:
    """Whether this collector fetches article pages for the outlet (fetcher_of; a shared outlet's
    pages are fetched by the hosted runner only). Outlets not in the registry (test
    fixtures, retired ids) belong to whoever asks, so nothing is stranded."""
    collector = collector or config.COLLECTOR
    owners = {o["id"]: fetcher_of(o) for o in (outlets if outlets is not None else load_outlets())}
    return owners.get(outlet_id, collector) == collector


def collectable(outlets: List[Dict], collector: str = None) -> List[Dict]:
    """Active outlets whose feeds this collector polls, shared outlets included (polled_by)."""
    collector = collector or config.COLLECTOR
    return [o for o in outlets if o.get("active") and polled_by(o, collector)]


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
        # A release wire is no country's output, so it is in no country's denominator. The wires
        # together are the denominator of the worldwide distribution stratum.
        by_country.setdefault(export_country(o), []).append(o)
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
