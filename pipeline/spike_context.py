"""Sourced news context for the unusual days (pipeline/spikes.py): events in the news that coincide with
a flagged day, researched by hand and recorded in sources/spike_context.yaml (shape:
sources/spike_context_schema.json).

Report only, like the spikes themselves. A note says that an event coincides with an unusual day, never
that it caused it: the tracker shows timing, not cause. A day from one wire feed often follows a single
event that wire covered, and the checks listed beside the day (a new outlet, a backfill read, a
collector catching up, a ruleset or gate change) can explain it on their own. A spike with nothing found
records "no matching event found" with what was searched, rather than a guess.

Validation, beyond the schema: every date is a real date; an event falls inside the stated window
around its spike (any of its days, from event_date to event_end); event_end is not before event_date;
the research is not dated before the spike; each spike (country, date) appears once; no text carries an
en or em dash or a spaced hyphen; no quoted passage is longer than MAX_QUOTE_WORDS words; and an event
is one plain sentence.

The export attaches each spike's notes to it in docs/data/spikes.json (attach), and docs/content.json
carries the same notes (site_notes) under spike_news for a page whose data was exported before this file
existed; a test keeps the two equal.

Command line:
  python -m pipeline.spike_context validate [path]   check a file (default sources/spike_context.yaml)
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from pipeline import config

PATH = config.ROOT / "sources" / "spike_context.yaml"
SCHEMA_PATH = config.ROOT / "sources" / "spike_context_schema.json"
NO_MATCH = "no matching event found"
MAX_QUOTE_WORDS = 15
TEXT_FIELDS = ("event", "source_title", "basis", "searched", "note")
# Quoted passages: straight or curly double quotes, and curly single quotes (a straight apostrophe is
# too often a possessive to mark a quotation).
_QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”|‘([^’]+)’')
_DASHES = ("–", "—", "‒", "―")


def _plain(v):
    """YAML reads an unquoted YYYY-MM-DD as a date: every date back to its ISO string."""
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    return v


def load_file(path: Optional[Path] = None) -> Dict:
    """The parsed file; a missing or empty one has no spikes."""
    p = Path(path or PATH)
    if not p.exists():
        return {"window": {"days_before": 3, "days_after": 1}, "spikes": []}
    with open(p, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return {"window": {"days_before": 3, "days_after": 1}, "spikes": []} if raw is None else _plain(raw)


def _schema() -> Dict:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _date(s) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def text_errors(where: str, text: str) -> List[str]:
    """Dashes and long quotes in one piece of text."""
    errs = []
    if any(d in text for d in _DASHES):
        errs.append("%s: an en or em dash" % where)
    if " - " in text or " -- " in text:
        errs.append("%s: a spaced hyphen used as a dash" % where)
    for m in _QUOTED.finditer(text):
        q = next(g for g in m.groups() if g is not None)
        if len(q.split()) > MAX_QUOTE_WORDS:
            errs.append("%s: a quote of %d words, more than %d" % (where, len(q.split()), MAX_QUOTE_WORDS))
    return errs


def in_window(spike_date: str, event_date: str, event_end: Optional[str], window: Dict) -> bool:
    """Whether any day of the event, event_date to event_end, falls in the window around the spike."""
    s, a, b = _date(spike_date), _date(event_date), _date(event_end or event_date)
    if not (s and a and b):
        return False
    lo = s - dt.timedelta(days=int(window.get("days_before", 0)))
    hi = s + dt.timedelta(days=int(window.get("days_after", 0)))
    return a <= hi and b >= lo


def validate(data) -> List[str]:
    """Schema errors first; the dates, the window and the wording once the shape is right."""
    import jsonschema
    v = jsonschema.Draft202012Validator(_schema())
    errs = []
    for e in v.iter_errors(data):
        where = "/".join(str(p) for p in e.absolute_path) or "(top)"
        if e.validator == "oneOf" and isinstance(e.instance, dict) and e.context:
            # a note is either found or not found: say what is wrong with the kind it is meant to be
            branch = 1 if "result" in e.instance else 0
            subs = [c for c in e.context if c.schema_path and c.schema_path[0] == branch] or list(e.context)
            errs += ["%s: %s" % ("/".join(str(p) for p in c.absolute_path) or where, c.message) for c in subs]
        else:
            errs.append("%s: %s" % (where, e.message))
    if errs:
        return errs
    window = data["window"]
    seen = set()
    for s in data["spikes"]:
        key = "%s %s" % (s["country"], s["date"])
        if key in seen:
            errs.append("%s: listed twice" % key)
        seen.add(key)
        sd, rd = _date(s["date"]), _date(s["researched"])
        if not sd:
            errs.append("%s: date is not a real date" % key)
            continue
        if not rd:
            errs.append("%s: researched is not a real date" % key)
        elif rd < sd:
            errs.append("%s: researched before the day itself" % key)
        for i, n in enumerate(s["notes"]):
            where = "%s note %d" % (key, i + 1)
            for f in TEXT_FIELDS:
                if isinstance(n.get(f), str):
                    errs += text_errors("%s %s" % (where, f), n[f])
            if n.get("result") == NO_MATCH:
                continue
            a, b = _date(n["event_date"]), _date(n.get("event_end") or n["event_date"])
            if not a or not b:
                errs.append("%s: event_date or event_end is not a real date" % where)
                continue
            if b < a:
                errs.append("%s: event_end is before event_date" % where)
            if not in_window(s["date"], n["event_date"], n.get("event_end"), window):
                errs.append("%s: the event (%s) is outside the window of %d days before to %d after %s" % (
                    where, n["event_date"], window["days_before"], window["days_after"], s["date"]))
            ev = n["event"].strip()
            if not ev.endswith(".") or re.search(r"[.!?]\s+[A-Z]", ev[:-1]):
                errs.append("%s: the event is not one plain sentence" % where)
    return errs


def validate_file(path: Optional[Path] = None) -> List[str]:
    try:
        data = load_file(path)
    except yaml.YAMLError as exc:
        return ["not valid YAML: %s" % exc]
    return validate(data)


def _note(n: Dict) -> Dict:
    """One note as the site reads it: the found fields, or the no match fields."""
    if n.get("result") == NO_MATCH:
        out = {"scope": n["scope"], "result": NO_MATCH, "searched": n["searched"]}
        if n.get("note"):
            out["note"] = n["note"]
        return out
    out = {k: n[k] for k in ("scope", "event", "event_date", "source_title", "source_url", "confidence", "basis")}
    if n.get("event_end"):
        out["event_end"] = n["event_end"]
    return out


def by_spike(path: Optional[Path] = None) -> Dict[str, Dict]:
    """{"ISO YYYY-MM-DD": {"researched", "notes"}} from a valid file; {} (with the reason printed) when it
    does not validate, so a bad hand edit never reaches the site."""
    try:
        data = load_file(path)
        errs = validate(data)
    except Exception as exc:   # noqa: BLE001  report only: the export never stops on this file
        errs, data = ["%s" % exc], None
    if errs:
        print("spike news context not exported: %s" % "; ".join(errs[:5]), file=sys.stderr)
        return {}
    return {"%s %s" % (s["country"], s["date"]): {"researched": s["researched"], "notes": [_note(n) for n in s["notes"]]}
            for s in data["spikes"]}


def window(path: Optional[Path] = None) -> Dict:
    try:
        return dict(load_file(path).get("window") or {})
    except Exception:   # noqa: BLE001
        return {}


def attach(spikes: List[Dict], path: Optional[Path] = None) -> int:
    """Put each spike's researched notes on it as news ({"researched", "notes"}), in place; a spike with
    no entry gets none (not researched yet, which is not the same as nothing found). Returns how many
    spikes carry notes."""
    notes = by_spike(path)
    n = 0
    for s in spikes:
        hit = notes.get("%s %s" % (s.get("country"), s.get("date")))
        if hit:
            s["news"] = hit
            n += 1
    return n


def site_notes(path: Optional[Path] = None) -> Dict:
    """What docs/content.json carries under spike_news.notes: the same {key: {"researched", "notes"}}."""
    return by_spike(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.spike_context")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("path", nargs="?", default=None)
    args = ap.parse_args(argv)
    errs = validate_file(Path(args.path) if args.path else None)
    for e in errs:
        print(e)
    print("ok" if not errs else "%d problem%s" % (len(errs), "" if len(errs) == 1 else "s"))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
