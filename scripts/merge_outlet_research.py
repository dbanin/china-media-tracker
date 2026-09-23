"""Fold the outlet research into sources/outlets.yaml.

The research, one YAML file per batch of countries, gives for each country:
- a readership source and the five most-read outlets, with their ranks;
- any of those outlets that were missing, with feeds that were verified before they were proposed;
- a political leaning and an ownership type for every active outlet, each with a cited source.

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
