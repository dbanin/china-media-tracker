"""Fold the outlet research into sources/outlets.yaml.

The research, one YAML file per batch of countries, gives for each country:
- a readership source and the five most-read outlets, with their ranks;
- any of those outlets that were missing, with feeds that were verified before they were proposed;
- a political leaning and an ownership type for every active outlet, each with a cited source.

Leaning and ownership also come from their own research jobs, in lean_*.yaml and own_*.yaml:
country blocks whose outlets carry {id, political_leaning, leaning_source} or {id, ownership,
ownership_source}. Those fill any outlet still unassessed. Where one already holds a different
sourced value, the existing value stays and the disagreement is reported for a person to settle.

This script applies that to the registry. It refuses to guess: a proposed outlet that collides with
an existing id or an existing feed host is reported, not added. An inactive outlet is never
reactivated here, because only the weekly feed validation may decide that.

Usage:
  python scripts/merge_outlet_research.py DIR            # report only
  python scripts/merge_outlet_research.py DIR --write    # apply and validate
"""
import argparse
import datetime as dt
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from pipeline import registry  # noqa: E402

LEANINGS = {"left", "centre_left", "centre", "centre_right", "right", "unassessed"}
OWNERSHIP = {"private", "public_service", "state", "party", "unassessed"}
TIERS = {"national_daily", "agency", "broadcaster", "business", "regional", "aggregator"}
# Project rule: Chinese-language outlets are registered but never monitored (see tw_udn_zh).
EXCLUDED_LANGUAGES = {"zh": "publishes primarily in Chinese; excluded by project rule because the owner does not read "
                            "Chinese and machine translation would miss the coding distinctions"}


def host(url):
    h = urlsplit(url or "").netloc.lower()
    return h[4:] if h.startswith("www.") else h


def load_blocks(directory):
    blocks = []
    for path in sorted(glob.glob(str(Path(directory) / "out_*.yaml"))):
        for b in yaml.safe_load(open(path, encoding="utf-8")) or []:
            b["_file"] = Path(path).name
            blocks.append(b)
    # Main blocks first, then supplements, so a supplement adds to what its country already holds.
    return sorted(blocks, key=lambda b: bool(b.get("supplement")))


def apply(outlets, blocks, today):
    by_id = {o["id"]: o for o in outlets}
    hosts = defaultdict(set)
    for o in outlets:
        for f in o.get("feeds") or []:
            hosts[(o["country"], host(f))].add(o["id"])
    report = Counter()
    issues = []
    for b in blocks:
        country = b.get("country")
        rank_source = b.get("rank_source")
        report["supplements" if b.get("supplement") else "countries"] += 1
        for e in b.get("outlets") or []:
            oid = e.get("id")
            lean, own = e.get("political_leaning"), e.get("ownership")
            if lean is not None and lean not in LEANINGS:
                issues.append((country, oid, "unknown leaning %r, skipped" % lean)); lean = None
            if own is not None and own not in OWNERSHIP:
                issues.append((country, oid, "unknown ownership %r, skipped" % own)); own = None
            if e.get("new"):
                if oid in by_id:
                    issues.append((country, oid, "proposed as new but the id exists; treated as an update"))
                    target = by_id[oid]
                else:
                    feeds = [f for f in (e.get("feeds") or []) if f]
                    if not feeds or not (e.get("feed_check") or {}).get("ok"):
                        issues.append((country, oid, "new outlet without a verified feed, not added")); continue
                    clash = set().union(*(hosts.get((country, host(f)), set()) for f in feeds))
                    if clash:
                        issues.append((country, oid, "feed host already used by %s, not added" % ", ".join(sorted(clash)))); continue
                    if e.get("tier") not in TIERS or not e.get("name") or not e.get("language"):
                        issues.append((country, oid, "incomplete record (name, language or tier), not added")); continue
                    if not oid.startswith(registry._alpha2_map().get(country, "").lower() + "_") and not oid.startswith(country.lower()[:2] + "_"):
                        issues.append((country, oid, "id does not carry the country prefix, not added")); continue
                    note = "Added %s from the readership research." % today
                    if e.get("substitute_for_unreachable"):
                        note += " Monitored in place of a more-read outlet that refuses the crawler or publishes no feed."
                    target = {"id": oid, "name": e["name"], "country": country, "language": e["language"],
                              "feeds": feeds, "tier": e["tier"], "notes": note, "active": True}
                    if e["language"] in EXCLUDED_LANGUAGES:
                        target.update(active=False, inactive_reason=EXCLUDED_LANGUAGES[e["language"]], inactive_since=today)
                        issues.append((country, oid, "registered inactive: %s" % EXCLUDED_LANGUAGES[e["language"]].split(";")[0]))
                        report["registered_excluded_language"] += 1
                    outlets.append(target); by_id[oid] = target
                    for f in feeds:
                        hosts[(country, host(f))].add(oid)
                    report["added"] += 1
                    if e.get("substitute_for_unreachable"):
                        report["substitutes"] += 1
            else:
                target = by_id.get(oid)
                if not target:
                    issues.append((country, oid, "update for an id not in the registry, skipped")); continue
                if target["country"] != country:
                    issues.append((country, oid, "belongs to %s, skipped" % target["country"])); continue
            if e.get("audience_rank"):
                target["audience_rank"] = int(e["audience_rank"])
                if rank_source:
                    target["rank_source"] = rank_source
                report["ranked"] += 1
                if not target.get("active"):
                    issues.append((country, oid, "ranked but inactive; left inactive for the feed validation to decide"))
            if lean:
                target["political_leaning"] = lean
                if lean != "unassessed" and e.get("leaning_source"):
                    target["leaning_source"] = e["leaning_source"]
                report["leaning_assessed" if lean != "unassessed" else "leaning_unassessed"] += 1
            if own:
                target["ownership"] = own
                if own != "unassessed" and e.get("ownership_source"):
                    target["ownership_source"] = e["ownership_source"]
                report["ownership_assessed" if own != "unassessed" else "ownership_unassessed"] += 1
    # Two outlets sharing one rank in a country is a research error worth seeing.
    ranks = defaultdict(list)
    for o in outlets:
        if o.get("audience_rank"):
            ranks[(o["country"], o["audience_rank"])].append(o["id"])
    for (c, r), ids in sorted(ranks.items()):
        if len(ids) > 1:
            issues.append((c, ",".join(ids), "share audience rank %d" % r))
    return report, issues


ATTRIBUTES = (
    ("lean_*.yaml", "political_leaning", "leaning_source", LEANINGS),
    ("own_*.yaml", "ownership", "ownership_source", OWNERSHIP),
)


def apply_attributes(outlets, directory):
    by_id = {o["id"]: o for o in outlets}
    report = Counter()
    issues = []
    for pattern, field, source_field, allowed in ATTRIBUTES:
        for path in sorted(glob.glob(str(Path(directory) / pattern))):
            for b in yaml.safe_load(open(path, encoding="utf-8")) or []:
                country = b.get("country")
                for e in b.get("outlets") or []:
                    oid, value, source = e.get("id"), e.get(field), e.get(source_field)
                    target = by_id.get(oid)
                    if not target:
                        issues.append((country, oid, "%s for an id not in the registry, skipped" % field)); continue
                    if value not in allowed:
                        issues.append((country, oid, "unknown %s %r, skipped" % (field, value))); continue
                    if value != "unassessed" and not source:
                        issues.append((country, oid, "%s %s without a source, skipped" % (field, value))); continue
                    current = target.get(field) or "unassessed"
                    if value == "unassessed":
                        target.setdefault(field, "unassessed")
                        report[field + "_unassessed"] += 1
                    elif current == "unassessed":
                        target[field] = value
                        target[source_field] = source
                        report[field + "_filled"] += 1
                    elif current != value:
                        issues.append((country, oid, "%s disagreement: registry %s, research %s (%s); kept %s"
                                       % (field, current, value, source, current)))
                        report[field + "_disagreements"] += 1
                    else:
                        report[field + "_confirmed"] += 1
    return report, issues


def coverage(outlets):
    active = Counter(o["country"] for o in outlets if o.get("active") and o.get("tier") != "distribution_wire")
    return active


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    outlets = registry.load_outlets()
    before = coverage(outlets)
    report, issues = apply(outlets, load_blocks(args.directory), dt.date.today().isoformat())
    attr_report, attr_issues = apply_attributes(outlets, args.directory)
    report.update(attr_report)
    issues.extend(attr_issues)
    registry.validate_outlets(outlets)
    after = coverage(outlets)
    countries = sorted(set(before) | set(after))
    summary = {
        "report": dict(report),
        "countries_with_five_or_more_active": {"before": sum(1 for c in countries if before[c] >= 5),
                                               "after": sum(1 for c in countries if after[c] >= 5), "of": len(countries)},
        "issues": len(issues),
    }
    print(json.dumps(summary, indent=1))
    for i in issues:
        print("  %s %s: %s" % i)
    if args.write:
        registry.save_outlets(outlets)
        print("written to sources/outlets.yaml")


if __name__ == "__main__":
    main()
