"""Licensed archive checks: aggregate counts from rules only screens the owner ran inside a licensed
full text archive (ProQuest TDM Studio), shown on the site as their own block.

A separate source with a separate method. Nothing here changes a label, a count, a denominator, the map,
the totals, the shares, the top 10 or the findings: the export copies the checks into their own side
file, docs/data/archive_checks.json, and nothing else reads them. Only aggregate counts are recorded,
never article text, a headline or an address, because the archive's text stays inside it.

The file is sources/archive_checks.yaml, shaped by sources/archive_checks_schema.json: a top level
`checks:` list. Validation also checks the arithmetic, so a typo in a hand copied table cannot reach
the site: for each title, China relevant = awaiting model + no state signature found; each column's
rows add up to its total; China relevant plus not about China, over every title, equals the records
left after removing duplicates; duplicates removed never exceed the records pulled; state origin
signatures are at most the records; every trigger count is at most the articles awaiting the model,
and the triggers together at least that many (one article can trip several); every trigger pattern
names an llm_triggers id in pipeline/signatures.yaml, exactly or as a prefix ending in *; and every
outlet_id is in sources/outlets.yaml.

The articles with no state signature found are only that: no signature matched and no trigger sent
them to the model. No verification step ran, so they are never independent journalism.

Command line:
  python -m pipeline.archive_checks validate [path]   check a file (default sources/archive_checks.yaml)
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from pipeline import config

ARCHIVE_CHECKS_PATH = config.ROOT / "sources" / "archive_checks.yaml"
SCHEMA_PATH = config.ROOT / "sources" / "archive_checks_schema.json"
COLUMNS = ("china_relevant", "awaiting_model", "no_state_signature", "not_about_china")


def load_file(path: Optional[Path] = None) -> Dict:
    """The parsed file, {"checks": [...]}. A missing or empty file has no checks."""
    p = Path(path or ARCHIVE_CHECKS_PATH)
    if not p.exists():
        return {"checks": []}
    with open(p, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return {"checks": []} if raw is None else raw


def _schema() -> Dict:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def trigger_ids(path: Optional[Path] = None) -> List[str]:
    """The llm_triggers ids in pipeline/signatures.yaml."""
    with open(Path(path or config.SIGNATURES_PATH), encoding="utf-8") as fh:
        sig = yaml.safe_load(fh) or {}
    return [t["id"] for t in sig.get("llm_triggers") or [] if isinstance(t, dict) and t.get("id")]


def outlet_ids() -> List[str]:
    from pipeline import registry
    return [o["id"] for o in registry.load_outlets()]


def _pattern_known(pattern: str, ids: List[str]) -> bool:
    if pattern.endswith("*"):
        return any(i.startswith(pattern[:-1]) for i in ids)
    return pattern in ids


def check_arithmetic(c: Dict, triggers: Optional[List[str]] = None, outlets: Optional[List[str]] = None) -> List[str]:
    """Every way the rows of one check fail to add up, as sentences; empty when they all do."""
    errs = []
    cid = c.get("id", "?")
    titles = c.get("titles") or []
    totals = c.get("totals") or {}
    for t in titles:
        if t["china_relevant"] != t["awaiting_model"] + t["no_state_signature"]:
            errs.append("%s: %s China relevant %d is not awaiting model %d + no state signature %d"
                        % (cid, t["name"], t["china_relevant"], t["awaiting_model"], t["no_state_signature"]))
    for col in COLUMNS:
        s = sum(t[col] for t in titles)
        if s != totals.get(col):
            errs.append("%s: the %s rows add up to %d, the total says %s" % (cid, col, s, totals.get(col)))
    rows = sum(t["china_relevant"] + t["not_about_china"] for t in titles)
    if rows != totals.get("records"):
        errs.append("%s: China relevant plus not about China add up to %d, the records total says %s" % (cid, rows, totals.get("records")))
    if totals.get("records") != c.get("records_deduplicated"):
        errs.append("%s: records total %s is not the %s records left after removing duplicates"
                    % (cid, totals.get("records"), c.get("records_deduplicated")))
    if c.get("records_deduplicated", 0) > c.get("records_pulled", 0):
        errs.append("%s: more records after removing duplicates than were pulled" % cid)
    if c.get("state_origin_signatures", 0) > c.get("records_deduplicated", 0):
        errs.append("%s: more state origin signatures than records" % cid)
    trig = c.get("triggers") or []
    awaiting = totals.get("awaiting_model") or 0
    for tr in trig:
        if tr["count"] > awaiting:
            errs.append("%s: trigger %s counts %d, more than the %d articles awaiting the model" % (cid, tr["label"], tr["count"], awaiting))
        if triggers is not None and not _pattern_known(tr["pattern"], triggers):
            errs.append("%s: trigger pattern %s is not an llm_triggers id in pipeline/signatures.yaml" % (cid, tr["pattern"]))
    if awaiting and sum(tr["count"] for tr in trig) < awaiting:
        errs.append("%s: the triggers add up to %d, fewer than the %d articles they sent to the model"
                    % (cid, sum(tr["count"] for tr in trig), awaiting))
    if outlets is not None:
        for t in list(titles) + list(c.get("not_searchable") or []):
            if t.get("outlet_id") and t["outlet_id"] not in outlets:
                errs.append("%s: outlet_id %s is not in sources/outlets.yaml" % (cid, t["outlet_id"]))
    if (c.get("period_from") or "") > (c.get("period_to") or ""):
        errs.append("%s: period_from is after period_to" % cid)
    return errs


def validate(data, triggers: Optional[List[str]] = None, outlets: Optional[List[str]] = None) -> List[str]:
    """Schema errors first; the arithmetic only once the shape is right."""
    import jsonschema
    v = jsonschema.Draft202012Validator(_schema())
    errs = ["%s: %s" % ("/".join(str(p) for p in e.absolute_path) or "(top)", e.message) for e in v.iter_errors(data)]
    if errs:
        return errs
    seen = set()
    for c in data["checks"]:
        if c["id"] in seen:
            errs.append("duplicate check id %s" % c["id"])
        seen.add(c["id"])
        errs += check_arithmetic(c, triggers, outlets)
    return errs


def validate_file(path: Optional[Path] = None) -> List[str]:
    try:
        data = load_file(path)
    except yaml.YAMLError as exc:
        return ["not valid YAML: %s" % exc]
    return validate(data, trigger_ids(), outlet_ids())


def country_sums(c: Dict) -> Dict[str, Dict]:
    """Per country, summed from the title rows: the titles and every column. Never typed in by hand."""
    out: Dict[str, Dict] = {}
    for t in c.get("titles") or []:
        s = out.setdefault(t["country"], dict({"titles": []}, **{k: 0 for k in COLUMNS}))
        s["titles"].append(t["name"])
        for k in COLUMNS:
            s[k] += t[k]
    return out


def build_export(path: Optional[Path] = None) -> Dict:
    """The side file docs/data/archive_checks.json: the checks as recorded, each with its per country sums.
    An invalid file is refused rather than published."""
    data = load_file(path)
    errs = validate(data, trigger_ids(), outlet_ids())
    if errs:
        raise ValueError("sources/archive_checks.yaml: " + "; ".join(errs))
    checks = []
    for c in data["checks"]:
        row = dict(c)
        row["by_country"] = country_sums(c)
        checks.append(row)
    return {"kept_apart": True, "checks": checks}


def build_export_quietly(path: Optional[Path] = None) -> Dict:
    """build_export, printing a failure and returning no checks, never stopping the export."""
    try:
        return build_export(path)
    except Exception as exc:   # noqa: BLE001  report only, like the planted export
        print("archive checks not exported: %s" % exc, file=sys.stderr)
        return {"kept_apart": True, "checks": []}


def _int(n) -> str:
    return "{:,}".format(int(n or 0))


def _and(items: List[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def methodology_text(path: Optional[Path] = None) -> str:
    """One paragraph per check for METHODOLOGY.md's Known limits, written from the file; "" without checks
    or when the file does not validate (the export prints why)."""
    try:
        data = load_file(path)
        if validate(data, trigger_ids(), outlet_ids()):
            return ""
    except Exception:   # noqa: BLE001  the methodology never stops on this file
        return ""
    from pipeline import registry
    out = []
    for c in data.get("checks") or []:
        t = c["totals"]
        countries = []
        for row in c["titles"]:
            name = registry.country_name(row["country"])
            if name not in countries:
                countries.append(name)
        sig = c["state_origin_signatures"]
        text = ("Licensed archive check. Paywalled articles are missing from every count. To look behind some "
                "paywalls without crossing them, the owner ran the tracker's state origin signatures and model "
                "triggers (pipeline/signatures.yaml%s) as a rules only screen in %s, %s, %s, for %d titles in %s "
                "(sources/archive_checks.yaml). No model and no person read the articles, and only aggregate counts "
                "left the archive. Of the %s records left after removing duplicates (%s pulled), %s concern China "
                "and %s do not; %s carried a state origin signature. Of the China articles, %s cite Chinese official "
                "or state sources and await review inside the archive, because their text cannot reach the "
                "tracker's model; the other %s are recorded as no state signature found, which is not independent "
                "journalism, because no verification step ran."
                % (", ruleset " + c["ruleset_version"] if c.get("ruleset_version") else "",
                   c["source"], c["scope"], c["period"], len(c["titles"]), _and(countries),
                   _int(c["records_deduplicated"]), _int(c["records_pulled"]), _int(t["china_relevant"]),
                   _int(t["not_about_china"]), "none" if sig == 0 else _int(sig), _int(t["awaiting_model"]),
                   _int(t["no_state_signature"])))
        for n in c.get("not_searchable") or []:
            text += " %s returned no records: %s." % (n["name"], n["reason"])
        text += (" These counts come from a different source with a different method, so they are not part of "
                 "any total, map, share, top 10 or finding.")
        out.append(text)
    return "\n\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["validate"])
    ap.add_argument("path", nargs="?")
    args = ap.parse_args()
    problems = validate_file(Path(args.path) if args.path else None)
    for p in problems:
        print(p)
    print("ok" if not problems else "%d problem(s)" % len(problems))
    sys.exit(1 if problems else 0)
