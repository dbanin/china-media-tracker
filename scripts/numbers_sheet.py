#!/usr/bin/env python3
"""One numbers sheet from one export, for quoting figures in a talk.

The counts move with every export (new days, the rules backlog clearing, relabelling), so a speaker should quote
every figure from one export only. This reads a single export's site files (default docs/data: meta.json,
latest.json, global_series.json and the daily files) and writes one Markdown sheet stamped with that export's
generated_at, schema, ruleset, gate and prompt versions. Every figure carries its basis: numerator, denominator,
window and counting unit, and the file and key it comes from. A figure the files do not carry is written as
"not in this export", never estimated.

The category that rests on the model's judgement alone is read under its new name, undetermined (code U,
meta.undetermined, findings.undetermined, schema 16), and under its old one, model judgement only (code M,
meta.model_only_state_origin, findings.model_only, schema 15), and labelled as what it is in that export. It is
never added into any total or denominator here, China coverage included: an old export whose own china_total and
share_a include it is recomputed without it, and the sheet says so.

Read only, standard library only:
    python3 scripts/numbers_sheet.py [--data DIR] [--out FILE]
Without --out the sheet is printed. Files whose stamps disagree (two exports mixed) stop it with exit code 2.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "docs" / "data"
WORLDWIDE = "WLD"
EVIDENCE_ROUTES = ("wire_credit", "distribution_stamp", "sponsored_disclosure", "diplomatic_byline", "uncredited_copy")
ROUTE_NAMES = {
    "wire_credit": "wire credit line or dateline",
    "distribution_stamp": "press release distribution stamp",
    "sponsored_disclosure": "sponsored or partner disclosure",
    "diplomatic_byline": "signed by a Chinese diplomat",
    "uncredited_copy": "uncredited copy of a credited state item",
}
GATE_PARTS = (
    ("classified", "labelled (a current label)"),
    ("paywalled", "paywalled, never read"),
    ("failed", "fetch failed after retries, or the page held no readable article"),
    ("blocked_robots", "blocked by robots.txt"),
    ("fetched_waiting_rules", "fetched, waiting for the rules stage"),
    ("fetched_text_not_delivered", "fetched on the owner's machine, text never delivered (relay retired)"),
    ("waiting_for_model", "waiting for the model"),
    ("not_yet_fetched", "not yet fetched"),
    ("other", "any other status"),
)
UND_ROUTE_NAMES = {
    "unattributed": "no evidence named, the model's judgement alone",
    "sponsored_disclosure": "a sponsored disclosure the model read and the rules did not find",
    # Kind (c), pipeline/model_only.py: the model's uncredited copies of an item that is not settled state origin.
    "uncredited_copy": "copies of undetermined items (uncredited copies whose item is not settled state origin)",
}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
NOT_IN = "not in this export"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def n(v) -> str:
    """A whole number with thousands separators, or "not in this export"."""
    if v is None:
        return NOT_IN
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return "{:,}".format(v) if isinstance(v, int) else str(v)


def pct(num, den, places: int = 1) -> str:
    if num is None or not den:
        return NOT_IN
    return ("%%.%df percent" % places) % (100.0 * num / den)


def portion(num, den) -> str:
    """A share in words. "most" only above 50 percent, "half" at exactly 50, otherwise the percentage alone."""
    if num is None or not den:
        return NOT_IN
    s = float(num) / den
    if s > 0.5:
        return "most (%s)" % pct(num, den)
    if s == 0.5:
        return "half (50.0 percent)"
    return pct(num, den)


def day_words(iso: Optional[str]) -> str:
    """2026-10-22 as 22 October 2026, so the sheet carries no dashes."""
    if not iso:
        return NOT_IN
    try:
        d = dt.date.fromisoformat(iso[:10])
    except ValueError:
        return str(iso)
    return "%d %s %d" % (d.day, MONTHS[d.month - 1], d.year)


def month_words(ym: Optional[str]) -> str:
    """2026-10 as October 2026."""
    try:
        y, m = (ym or "").split("-")[:2]
        return "%s %d" % (MONTHS[int(m) - 1], int(y))
    except (ValueError, IndexError):
        return n(ym)


def stamp_words(ts: Optional[str]) -> str:
    if not ts:
        return NOT_IN
    try:
        t = dt.datetime.fromisoformat(ts)
    except ValueError:
        return str(ts)
    t = t.astimezone(dt.timezone.utc) if t.tzinfo else t
    return "%s, %s UTC" % (day_words(t.date().isoformat()), t.strftime("%H:%M:%S"))


def dig(d, *keys):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def load_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Reading one export
# ---------------------------------------------------------------------------

def load_export(data_dir: Path) -> Dict:
    data_dir = Path(data_dir)
    ex = {"dir": data_dir, "meta": load_json(data_dir / "meta.json"), "latest": load_json(data_dir / "latest.json")}
    gs = data_dir / "global_series.json"
    ex["series"] = load_json(gs) if gs.exists() else None
    ex["daily"] = {}
    for p in sorted((data_dir / "daily").glob("*.json")) if (data_dir / "daily").is_dir() else []:
        ex["daily"][p.name] = load_json(p)
    # Country names sit beside the data directory (docs/); an export written elsewhere uses the repository's.
    names = {}
    site = data_dir.parent if (data_dir.parent / "country-names.json").exists() else ROOT / "docs"
    vendor = site / "vendor" / "iso3166.json"
    common = site / "country-names.json"
    try:
        if vendor.exists():
            names.update({r["alpha-3"]: r["name"] for r in load_json(vendor)})
        if common.exists():
            names.update(load_json(common).get("names", {}))
    except (ValueError, KeyError, TypeError):
        pass
    ex["names"] = names
    return ex


def stamp_problems(ex: Dict) -> List[str]:
    """Files whose schema_version or generated_at differ from meta.json's: a sheet from them would mix exports.
    global_series.json is an array and carries no stamp by design."""
    meta = ex["meta"]
    want = (meta.get("schema_version"), meta.get("generated_at"))
    out = []
    files = [("latest.json", ex["latest"])] + [("daily/" + k, v) for k, v in ex["daily"].items()]
    for name, data in files:
        if not isinstance(data, dict):
            continue
        got = (data.get("schema_version", want[0]), data.get("generated_at", want[1]))
        if got != want:
            out.append("%s carries schema %s, generated %s; meta.json carries schema %s, generated %s"
                       % (name, got[0], got[1], want[0], want[1]))
    return out


def undetermined_info(ex: Dict) -> Dict:
    """Which shape this export has, the code of the category resting on the model's judgement alone and its
    blocks. New shape: code U, meta.undetermined, findings.undetermined. Old: code M,
    meta.model_only_state_origin, findings.model_only."""
    meta, totals = ex["meta"], dig(ex["latest"], "totals", "all_time") or {}
    mblock = meta.get("undetermined")
    fblock = dig(meta, "findings", "undetermined")
    new = mblock is not None or fblock is not None or "U" in totals
    if not new:
        mblock = meta.get("model_only_state_origin")
        fblock = dig(meta, "findings", "model_only")
    code = (mblock or {}).get("code") or ("U" if new else "M")
    if code not in totals:
        code = "U" if "U" in totals else ("M" if "M" in totals else code)
    if new:
        name = "Undetermined"
        what = ("Labels the model gave as state origin with no evidence a reader can check, sponsored "
                "disclosures the model read, and from 22 October 2026 uncredited copies of items that are "
                "undetermined themselves; excluded from every count, China coverage included")
    else:
        name = "Model judgement only, provisional"
        what = ("State origin by the model's judgement alone, route unattributed; in this old shape sponsored "
                "disclosures the model read are still inside state origin. From schema 16 this category is "
                "renamed undetermined (code U). Excluded here from every count, China coverage included")
    return {"new": new, "code": code, "name": name, "what": what, "file_label": (mblock or {}).get("label"),
            "meta": mblock or {}, "findings": fblock or {}, "meta_key": "undetermined" if new else "model_only_state_origin",
            "findings_key": "undetermined" if new else "model_only"}


def complete_window(ex: Dict) -> Tuple[Optional[str], Optional[str]]:
    """The 30 complete days the findings and the site's map use, first and last day included."""
    f = dig(ex["meta"], "findings", "last_30d") or {}
    if f.get("window_start") and f.get("window_end"):
        return f["window_start"], f["window_end"]
    start = ex["latest"].get("window_start_30d")
    if not start:
        return None, None
    return start, (dt.date.fromisoformat(start) + dt.timedelta(days=29)).isoformat()


def daily_days(ex: Dict) -> Dict[str, Dict]:
    days = {}
    for f in ex["daily"].values():
        days.update(f.get("days") or {})
    return days


def window_counts(ex: Dict, code: str, start: str, end: str) -> Dict:
    """Sums over the days start..end (both included): the world from global_series.json and, from the daily
    files, every country, rules and model parts, routes and distinct underlying items."""
    keys = ("A", "B", "C", "N", code, "pending", "rel", "Ar", "Al", "Ah", "Br", "Bl")
    series = {"world": None, "wld": None}
    if ex["series"] is not None:
        w = dict.fromkeys(keys[:7], 0)
        wl = dict.fromkeys(keys[:7], 0)
        for row in ex["series"]:
            if start <= row.get("date", "") <= end:
                for k in w:
                    w[k] += row.get(k, 0) or 0
                    wl[k] += (row.get("wld") or {}).get(k, 0) or 0
        series = {"world": w, "wld": wl}
    days = {d: v for d, v in daily_days(ex).items() if start <= d <= end}
    per_country: Dict[str, Dict] = {}
    routes_world: Dict[str, int] = {}
    items: Dict[str, set] = {}
    for d, day in days.items():
        for c, v in (day.get("countries") or {}).items():
            t = per_country.setdefault(c, dict.fromkeys(keys, 0))
            for k in keys:
                t[k] += v.get(k, 0) or 0
        for c, rs in (day.get("routes") or {}).items():
            for r, k in rs.items():
                routes_world[r] = routes_world.get(r, 0) + k
        for c, ab in (day.get("underlying_items") or {}).items():
            items.setdefault(c, set()).update(ab.get("A") or [])
    world_daily = dict.fromkeys(keys, 0)
    for v in per_country.values():
        for k in keys:
            world_daily[k] += v[k]
    return {"series": series, "days": len(days), "countries": per_country, "world_daily": world_daily,
            "routes": routes_world, "items": items, "world_items": len(set().union(*items.values())) if items else 0}


def china(c: Dict) -> int:
    """China coverage: state origin, unchecked state sourcing and independent journalism. Never undetermined."""
    return (c.get("A") or 0) + (c.get("B") or 0) + (c.get("C") or 0)


def country_name(ex: Dict, iso: str) -> str:
    if iso == WORLDWIDE:
        return "Worldwide distribution stratum"
    return ex["names"].get(iso, iso)


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------

class Sheet:
    def __init__(self):
        self.lines: List[str] = []

    def h(self, text: str, level: int = 2):
        self.lines += ["", "#" * level + " " + text, ""]

    def p(self, text: str):
        self.lines += [text, ""]

    def b(self, text: str, indent: int = 0):
        self.lines.append("  " * indent + "* " + text)

    def end(self):
        self.lines.append("")

    def text(self) -> str:
        out = "\n".join(self.lines).strip() + "\n"
        # The repository rule: no em or en dashes, no spaced hyphen, no double hyphen.
        for bad in ("\u2014", "\u2013", " - ", "--"):
            out = out.replace(bad, ", " if bad != "--" else " ")
        return out


def figures_block(s: Sheet, ex: Dict, und: Dict, label: str, c: Optional[Dict], *, window: str,
                  routes: Optional[Dict], route_src: str, und_count, und_src: str, file_china=None,
                  file_share=None, src: str):
    """State origin, its routes, undetermined, unchecked, independent, not about China, China coverage and the
    share, for one window, each with its basis."""
    meta = ex["meta"]
    s.h(label, 3)
    if c is None:
        s.p("Every figure in this window: " + NOT_IN + ".")
        return
    A, B, C, N = c.get("A"), c.get("B"), c.get("C"), c.get("N")
    tot = china(c)
    unit = "placements (one article at one outlet; a copy one row stands for is counted once)"
    s.b("Window: %s. Unit: %s. Source: %s." % (window, unit, src))
    s.b("**State origin, evidence settled: %s placements.** Text authored or commissioned by a Chinese state entity, "
        "published without substantive alteration, with evidence a reader can check (one of the five routes below, "
        "or a rules label from before routes were stored)." % n(A))
    if c.get("Ar") is not None or c.get("Al") is not None:
        s.b("By the rules: %s; by the model: %s; hand reviewed: %s (key Ar, Al, Ah). Rules plus model: %s."
            % (n(c.get("Ar")), n(c.get("Al")), n(c.get("Ah")), n((c.get("Ar") or 0) + (c.get("Al") or 0))), 1)
    if routes is not None:
        s.b("Routes (%s):" % route_src, 1)
        total_routes = 0
        for r in EVIDENCE_ROUTES:
            k = routes.get(r)
            total_routes += k or 0
            s.b("%s: %s" % (ROUTE_NAMES[r], n(k) if k is not None else "0"), 2)
        s.b("The five routes add up to %s against %s state origin placements: %s." % (
            n(total_routes), n(A), "they agree" if total_routes == A else
            "they differ by %s, so quote routes as stored, not as a split of the headline" % n(abs((A or 0) - total_routes))), 2)
        s.b("Each route split by rules versus model: %s (the files carry the rules and model split for state origin as a "
            "whole, above, and route counts without their method)." % NOT_IN, 2)
        if not und["new"]:
            s.b("In this old shape the sponsored disclosures the model read are inside the sponsored route and inside "
                "state origin; how many: %s. From schema 16 they move to undetermined." % NOT_IN, 2)
    else:
        s.b("Routes: " + NOT_IN + ".", 1)
    s.b("**%s (code %s): %s placements.** %s. Source: %s." % (und["name"], und["code"], n(und_count), und["what"], und_src))
    by_route = und["meta"].get("by_route")
    if by_route and label == "All time":
        s.b("By stored route (meta.%s.by_route): %s; they add up to %s." % (
            und["meta_key"], "; ".join("%s %s" % (UND_ROUTE_NAMES.get(r, r), n(k)) for r, k in sorted(by_route.items(), key=lambda t: (t[0] not in UND_ROUTE_NAMES, list(UND_ROUTE_NAMES).index(t[0]) if t[0] in UND_ROUTE_NAMES else 0, t[0]))),
            n(sum(by_route.values()))), 1)
        if by_route.get("uncredited_copy"):
            s.b("Copies of undetermined items: %s (meta.%s.by_route.uncredited_copy). Their stored route is uncredited "
                "copy, but the item each repeats is not settled state origin, so they are undetermined too: in no "
                "total, not in state origin and not in its uncredited copy route above." % (
                    n(by_route["uncredited_copy"]), und["meta_key"]), 2)
    st_b = "publishable" if meta.get("relay_publishable") else "provisional, not published as checked"
    s.b("**Unchecked state sourcing: %s placements.** The outlet's own story whose central content is a factual claim "
        "attributed to a Chinese official or state source, passed on without independent confirmation, by the model's "
        "judgement. Status: %s; it is published as checked only when hand coding reaches a Cohen's kappa of at least %s. "
        "Rules: %s; model: %s." % (n(B), st_b, n(meta.get("kappa_warning_threshold")), n(c.get("Br")), n(c.get("Bl"))))
    s.b("**Independent journalism: %s placements.**" % n(C))
    s.b("**Not about China: %s placements** (passed the gate, judged not to concern China; outside China coverage)." % n(N))
    s.b("**China coverage total: %s placements** = state origin %s + unchecked state sourcing %s + independent journalism %s. "
        "%s (code %s) is not in it." % (n(tot), n(A), n(B), n(C), und["name"], und["code"]))
    if file_china is not None and file_china != tot:
        s.b("This export's own china_total, %s, differs by %s: it includes the %s figure. Do not quote it."
            % (n(file_china), n(file_china - tot), und["name"].lower()), 1)
    s.b("**State origin share of China coverage: %s** (%s of %s, same window, code %s in neither)."
        % (pct(A, tot, 2), n(A), n(tot), und["code"]))
    if file_share is not None and tot and round((A or 0) / float(tot), 4) != file_share:
        s.b("This export's own share_a, %s, has the %s figure in its denominator. Do not quote it."
            % (pct(file_share, 1, 2), und["name"].lower()), 1)
    if c.get("pending") is not None:
        s.b("Waiting for the model, in no count: %s placements (key pending)." % n(c.get("pending")))
    s.end()


def build(ex: Dict) -> str:
    meta, latest = ex["meta"], ex["latest"]
    und = undetermined_info(ex)
    code = und["code"]
    s = Sheet()
    s.lines.append("# Numbers sheet")
    s.p("")
    s.p("Quote every figure from this one export. The counts move with every run.")

    # -- Stamp ------------------------------------------------------------------------------------------
    s.h("This export")
    s.b("Generated at: %s (meta.generated_at)." % stamp_words(meta.get("generated_at")))
    s.b("Schema version: %s, %s." % (n(meta.get("schema_version")),
        "new shape, the model only category is undetermined, code U" if und["new"]
        else "old shape, the model only category is model judgement only, code M"))
    s.b("Ruleset: %s. Gate: %s. Classifier prompt version: %s. Model: %s." % (
        n(meta.get("ruleset_version")), n(meta.get("gate_version")), day_words(meta.get("llm_prompt_version")),
        n(meta.get("llm_model"))))
    s.b("Files read: meta.json, latest.json, global_series.json%s. Their stamps agree." % (
        "".join(", daily/" + k for k in ex["daily"])))
    start30, end30 = complete_window(ex)
    first = meta.get("first_discovered") or dig(meta, "findings", "since")
    gen_day = (meta.get("generated_at") or "")[:10]
    s.b("All time: %s (first discovery) through %s, the export day, still partial." % (day_words(first), day_words(gen_day)))
    s.b("Last 30 complete days: %s through %s, the export day left out, as on the site's map." % (
        day_words(start30), day_words(end30)))
    s.end()

    # -- Collection scale -------------------------------------------------------------------------------
    s.h("Collection scale")
    s.b("Outlets registered: %s; active: %s (meta.outlets_total, outlets_active)." % (n(meta.get("outlets_total")), n(meta.get("outlets_active"))))
    s.b("Countries and territories monitored (with an active outlet): %s; in gaps (no outlet to monitor): %s "
        "(meta.countries_monitored, countries_in_gaps). China is a gap by design." % (
            n(meta.get("countries_monitored")), n(meta.get("countries_in_gaps"))))
    s.b("Gate languages (keyword lists): %s (meta.gate_languages)." % n(meta.get("gate_languages")))
    s.b("Collecting since: %s (meta.first_discovered)." % day_words(first))
    disc = meta.get("articles_discovered")
    s.b("Items discovered (screened by the gate), all time: %s (meta.articles_discovered)." % n(disc))
    rd = meta.get("rediscovery")
    if rd:
        of = rd.get("measured_of")
        extra = ""
        if isinstance(disc, int) and of and disc > of and rd.get("estimated_share") is not None:
            extra = ", about %d percent of the running total" % round(100.0 * rd["estimated_share"] * of / disc)
        s.b("Repeats before %s: estimated %s percent of the %s items screened when measured%s (meta.rediscovery)."
            % (day_words(rd.get("fixed_from")), n(round(100 * (rd.get("estimated_share") or 0))), n(of), extra), 1)
    s.b("Passed the gate (stored rows): %s (meta.articles_gate_relevant)." % n(meta.get("articles_gate_relevant")))
    s.b("Labelled (stored rows with a current label): %s (meta.articles_classified); of them by the model: %s "
        "(meta.llm_labels_total)." % (n(meta.get("articles_classified")), n(meta.get("llm_labels_total"))))
    at = dig(latest, "totals", "all_time") or {}
    s.b("Labelled placements, each copy counted once: %s (latest.json totals.all_time.cls)." % n(at.get("cls")))
    s.end()

    # -- Gate accounting --------------------------------------------------------------------------------
    s.h("Gate accounting")
    ga = meta.get("gate_accounting")
    if not ga:
        s.p("Gate accounting: " + NOT_IN + ".")
    else:
        parts = ga.get("parts") or {}
        s.p("Every stored row that passed the gate, by what became of it (meta.gate_accounting; unit: stored rows).")
        total = 0
        for k, lab in GATE_PARTS:
            if k in parts:
                total += parts[k] or 0
                s.b("%s: %s" % (lab, n(parts[k])))
        for k, v in sorted(parts.items()):
            if k not in dict(GATE_PARTS):
                total += v or 0
                s.b("%s: %s" % (k, n(v)))
        for k, v in sorted((ga.get("other_statuses") or {}).items()):
            s.b("status %s (inside any other status): %s" % (k, n(v)), 1)
        ok = total == ga.get("total")
        s.b("**The rows add up to %s; the gate total is %s: %s.**" % (
            n(total), n(ga.get("total")), "they agree" if ok else "THEY DO NOT AGREE, do not quote this table"))
        if meta.get("articles_gate_relevant") is not None and ga.get("total") != meta.get("articles_gate_relevant"):
            s.b("The gate total differs from meta.articles_gate_relevant (%s)." % n(meta.get("articles_gate_relevant")))
        if ga.get("copies_counted_once") is not None:
            s.b("Of these rows, %s are copies counted as one placement with another row, so the site's own gate total "
                "is %s placements (latest.json totals.all_time.rel: %s)." % (
                    n(ga["copies_counted_once"]), n((ga.get("total") or 0) - ga["copies_counted_once"]), n(at.get("rel"))))
        s.end()

    # -- Figures ----------------------------------------------------------------------------------------
    s.h("What the labels say")
    route_all = meta.get("route_totals")
    und_all = at.get(code) if code in at else (und["meta"].get("count") if und["meta"] else None)
    figures_block(s, ex, und, "All time", at, window="all time, %s through %s" % (day_words(first), day_words(gen_day)),
                  routes=route_all, route_src="meta.route_totals, every route all time, the stratum included",
                  und_count=und_all, und_src="latest.json totals.all_time.%s; meta.%s.count %s" % (
                      code, und["meta_key"], n(und["meta"].get("count"))),
                  file_china=at.get("china_total"), file_share=at.get("share_a"),
                  src="latest.json totals.all_time, every country and the worldwide distribution stratum")
    w = window_counts(ex, code, start30, end30) if start30 else None
    c30 = None
    if w and (w["series"]["world"] is not None or w["days"]):
        # The daily files carry the rules and model parts and the routes; global_series.json only the categories.
        sw = w["series"]["world"]
        c30 = dict(w["world_daily"]) if w["days"] else dict(sw)
        und30 = c30.get(code)
        und30_meta = und["meta"].get("count_last_30d")
        figures_block(s, ex, und, "Last 30 complete days", c30,
                      window="%s through %s, %s days, %s of them with data in the daily files" % (
                          day_words(start30), day_words(end30),
                          n((dt.date.fromisoformat(end30) - dt.date.fromisoformat(start30)).days + 1), n(w["days"])),
                      routes={r: v for r, v in w["routes"].items() if r in EVIDENCE_ROUTES} if w["days"] else None,
                      route_src="the daily files' routes, summed over the window",
                      und_count=und30, und_src="the daily files' %s, summed; meta.%s.count_last_30d %s" % (
                          code, und["meta_key"], n(und30_meta)),
                      src="the daily files, summed over the window, every country and the worldwide distribution stratum")
        checks = []
        f30 = dig(meta, "findings", "last_30d", "state_origin")
        if f30 is not None and f30 != c30.get("A"):
            checks.append("findings.last_30d.state_origin is %s against %s summed here" % (n(f30), n(c30.get("A"))))
        if sw is not None and w["days"]:
            for k in ("A", "B", "C", "N", code):
                if sw.get(k) != w["world_daily"].get(k):
                    checks.append("global_series.json %s is %s against %s in the daily files" % (k, n(sw.get(k)), n(w["world_daily"].get(k))))
        if und30_meta is not None and und30 is not None and und30_meta != und30:
            checks.append("meta.%s.count_last_30d is %s against %s summed here" % (und["meta_key"], n(und30_meta), n(und30)))
        s.p("Cross checks for this window: " + ("; ".join(checks) + "." if checks else
            "global_series.json, the daily files, findings.last_30d and meta.%s agree." % und["meta_key"]))
    else:
        figures_block(s, ex, und, "Last 30 complete days", None, window="", routes=None, route_src="", und_count=None,
                      und_src="", src="")
    s.p("Underlying items (one story counted once across its placements, days and countries): state origin all time %s "
        "(latest.json totals.all_time.uniqA); last 30 complete days %s (the daily files' underlying item ids, united)." % (
            n(at.get("uniqA")), n(w["world_items"]) if w and w["days"] else NOT_IN))

    # -- Worldwide distribution stratum ----------------------------------------------------------------
    s.h("Worldwide distribution stratum")
    wld = dig(latest, "countries", WORLDWIDE)
    dw = meta.get("distribution_wires") or {}
    s.p("Global press release wires (%s). Their releases are counted in the world totals and never in any country; "
        "the stratum is never ranked as a country." % (", ".join(dw.get("outlets") or []) or NOT_IN))
    if wld:
        wa = wld.get("all_time") or {}
        s.b("All time (latest.json countries.WLD.all_time): state origin %s, %s %s, unchecked state sourcing %s, "
            "independent %s, not about China %s; China coverage %s; state origin share of it %s." % (
                n(wa.get("A")), und["name"].lower(), n(wa.get(code)), n(wa.get("B")), n(wa.get("C")), n(wa.get("N")),
                n(china(wa)), pct(wa.get("A"), china(wa))))
        s.b("Country outlets alone, all time (world less the stratum): state origin %s of China coverage %s, %s." % (
            n((at.get("A") or 0) - (wa.get("A") or 0)), n(china(at) - china(wa)),
            pct((at.get("A") or 0) - (wa.get("A") or 0), china(at) - china(wa))), 1)
    else:
        s.b("All time: " + NOT_IN + ".")
    if w and w["days"]:
        wl = w["countries"].get(WORLDWIDE) or {}
        s.b("Last 30 complete days (daily files, WLD): state origin %s, %s %s, unchecked state sourcing %s, independent %s, "
            "not about China %s; China coverage %s; state origin share of it %s." % (
                n(wl.get("A", 0)), und["name"].lower(), n(wl.get(code, 0)), n(wl.get("B", 0)), n(wl.get("C", 0)),
                n(wl.get("N", 0)), n(china(wl)), pct(wl.get("A"), china(wl))))
        s.b("Country outlets alone, last 30 complete days: state origin %s of China coverage %s, %s." % (
            n((c30.get("A") or 0) - (wl.get("A") or 0)), n(china(c30) - china(wl)),
            pct((c30.get("A") or 0) - (wl.get("A") or 0), china(c30) - china(wl))), 1)
    s.end()

    # -- Top 10 -----------------------------------------------------------------------------------------
    s.h("Top 10 countries, last 30 complete days")
    top_lines = []
    if w and w["days"]:
        world_a = c30.get("A") or 0
        ranked = sorted(((v.get("A") or 0, iso) for iso, v in w["countries"].items() if iso != WORLDWIDE and v.get("A")),
                        key=lambda t: (-t[0], t[1]))
        s.p("Ranked by state origin placements, %s through %s. Share of world: of the %s state origin placements "
            "worldwide in the window, the stratum's %s included (the basis of findings.top_country.share_of_world). "
            "Share of own coverage: of that country's China coverage in the window. Underlying items: distinct stories."
            % (day_words(start30), day_words(end30), n(world_a), n((w["countries"].get(WORLDWIDE) or {}).get("A", 0))))
        if ranked:
            cutoff = ranked[min(9, len(ranked) - 1)][0]
            shown = [t for t in ranked if t[0] >= cutoff]
            for a, iso in shown:
                rank = 1 + sum(1 for b, _ in ranked if b > a)
                v = w["countries"][iso]
                tied = [i for b, i in ranked if b == a and i != iso]
                tie = (" (tied with %s)" % ", ".join(country_name(ex, i) for i in tied)) if tied else ""
                s.b("%d. %s (%s): %s placements, %s underlying items, %s of world, %s of its own %s China coverage "
                    "placements%s." % (rank, country_name(ex, iso), iso, n(a), n(len(w["items"].get(iso, ()))),
                                       pct(a, world_a), pct(a, china(v)), n(china(v)), tie))
                top_lines.append((iso, a))
            if len(shown) > 10:
                s.b("Tenth place is shared, so the list has %s entries." % n(len(shown)))
            s.b("Countries with any state origin placement in the window: %s." % n(len(ranked)))
            tc = dig(meta, "findings", "top_country") or {}
            if tc.get("iso"):
                agree = tc.get("iso") == ranked[0][1] and tc.get("state_origin") == ranked[0][0]
                s.b("findings.top_country: %s with %s placements, %s underlying items, share of world %s: %s." % (
                    tc.get("iso"), n(tc.get("state_origin")), n(tc.get("underlying")), pct(tc.get("share_of_world"), 1),
                    "agrees with the list" if agree else "DIFFERS from the list"))
                to = tc.get("top_outlet") or {}
                if to.get("name"):
                    s.b("Its top outlet: %s, %s of its %s placements (%s)%s." % (
                        to["name"], n(to.get("count")), n(to.get("total")), pct(to.get("count"), to.get("total")),
                        ", tied with %s" % ", ".join(x for x in (to.get("tied_names") or []) if x != to["name"])
                        if (to.get("tied") or 1) > 1 else ""), 1)
                tr = tc.get("top_route_detail") or {}
                if tr.get("id"):
                    s.b("Its top route: %s, %s of %s (%s)%s." % (
                        ROUTE_NAMES.get(tr["id"], tr["id"]), n(tr.get("count")), n(tr.get("total")),
                        pct(tr.get("count"), tr.get("total")),
                        ", tied with %s" % ", ".join(ROUTE_NAMES.get(x, x) for x in (tr.get("tied_ids") or []) if x != tr["id"])
                        if (tr.get("tied") or 1) > 1 else ""), 1)
        else:
            s.b("No country has a state origin placement in the window.")
    else:
        s.p("Top 10 countries: " + NOT_IN + " (no daily files).")
    s.end()

    # -- Hand checks ------------------------------------------------------------------------------------
    s.h("Hand check status")
    reviewed = meta.get("articles_reviewed")
    s.b("Articles read and coded by a person, any figure: %s (meta.articles_reviewed)." % n(reviewed))
    um = und["meta"]
    chk = um.get("check") or {}
    rule = um.get("rule") or {}
    precision = n(chk.get("precision")) if chk.get("precision") is not None else "none yet"
    if "status" in um:
        # Schema 15: the model only figure rejoins state origin once its hand check passes.
        s.b("**%s (code %s):** status %s; hand check state %s, %s coded of %s drawn, precision %s. Rule: rejoins state "
            "origin at a precision of at least %s on at least %s coded rows, withheld below %s.%s" % (
                und["name"], code, n(um.get("status")), n(chk.get("state")), n(chk.get("coded")), n(chk.get("n")),
                precision, pct(rule.get("pass_precision"), 1, 0), n(rule.get("min_coded")),
                pct(rule.get("withhold_below"), 1, 0), (" Reason: %s." % um["reason"]) if um.get("reason") else ""))
    else:
        # Schema 16: undetermined never rejoins state origin; its hand check is information only.
        shares = "; ".join("%s %s" % (k.replace("_", " "), pct(chk[k], 1)) for k in ("confirmed_share", "could_be_state_share")
                           if chk.get(k) is not None) or "no shares yet"
        s.b("**%s (code %s):** %s; hand check, information only: state %s, %s coded of %s drawn, %s.%s" % (
            und["name"], code,
            "never rejoins state origin" if um.get("rejoins_state_origin") is False else "rejoins state origin: %s" % NOT_IN,
            n(chk.get("state")), n(chk.get("coded")), n(chk.get("n")), shares,
            (" As the export puts it: %s." % um["summary"]) if um.get("summary") else
            ((" Reason: %s." % um["reason"]) if um.get("reason") else "")))
    hs = meta.get("relay_human_study") or {}
    s.b("**Unchecked state sourcing:** publishable %s, provisional %s; hand coding study state %s, passed %s, kappa %s "
        "(threshold %s, meta.kappa_warning_threshold)." % (
            "yes" if meta.get("relay_publishable") else "no", "yes" if meta.get("relay_provisional") else "no",
            n(hs.get("state")), "yes" if hs.get("passed") else "no",
            n(hs.get("kappa_bc")) if hs.get("kappa_bc") is not None else "none yet",
            n(meta.get("kappa_warning_threshold"))))
    if meta.get("relay_provisional_reason"):
        s.b("Reason as the export states it: %s" % meta["relay_provisional_reason"], 1)
    hist = meta.get("relay_reliability_history") or {}
    if hist.get("kappa_bc") is not None:
        s.b("Last stability check, model against model, under an older prompt and kept as history only: kappa %.2f on %s "
            "articles. It never unlocks publication." % (hist["kappa_bc"], n(hist.get("n"))), 1)
    s.b("**State origin, evidence settled:** no hand check of its own is reported in this export; it rests on the "
        "evidence routes. Candidate recall audit: %s." % ("none has reported" if meta.get("candidate_recall") is None
                                                          else json.dumps(meta.get("candidate_recall"))))
    for mid, m in sorted((meta.get("measures") or {}).items()):
        mc = m.get("check") or {}
        s.b("Measure %s: status %s; hand check state %s, %s coded." % (
            m.get("label") or mid, n(m.get("status")), n(mc.get("state")), n(mc.get("coded"))))
    s.end()

    # -- Model stage ------------------------------------------------------------------------------------
    s.h("Model stage")
    lb = meta.get("llm_budget") or {}
    pl = lb.get("provider_limit")
    if pl:
        s.b("**Paused by the provider's usage limit** until %s, recorded %s (meta.llm_budget.provider_limit). No model "
            "label is added until then." % (stamp_words(pl.get("until")), stamp_words(pl.get("recorded_at"))))
    elif "provider_limit" in lb:
        s.b("Provider usage limit: none active (meta.llm_budget.provider_limit is empty).")
    else:
        s.b("Provider usage limit: " + NOT_IN + ".")
    ga_parts = (meta.get("gate_accounting") or {}).get("parts") or {}
    s.b("Items waiting for the model: %s stored rows in %s countries (meta.official_sourcing_pending, "
        "official_sourcing_pending_countries; gate accounting waiting for the model: %s); %s placements, each copy "
        "counted once (latest.json totals.all_time.pending)." % (
            n(meta.get("official_sourcing_pending")), n(meta.get("official_sourcing_pending_countries")),
            n(ga_parts.get("waiting_for_model")), n(at.get("pending"))))
    s.b("Model labels made under an older classifier prompt: %s of %s (meta.labels_on_older_prompt, llm_labels_total)." % (
        n(meta.get("labels_on_older_prompt")), n(meta.get("llm_labels_total"))))
    if lb:
        s.b("Spending this month (%s): %s USD estimated of a %s USD budget; calls left today %s." % (
            month_words(lb.get("month")), n(lb.get("estimated_usd")), n(lb.get("budget_usd")), n(lb.get("calls_left_today"))))
    s.b("Last model run: %s (meta.last_runs.classify_llm)." % stamp_words(dig(meta, "last_runs", "classify_llm")))
    s.end()

    # -- Say it this way --------------------------------------------------------------------------------
    s.h("Say it this way")
    tot_all = china(at)
    s.b("Scale: \"We watch %s active news outlets in %s countries and territories, with keyword lists in %s languages, "
        "and since %s we have screened %s items.\"" % (n(meta.get("outlets_active")), n(meta.get("countries_monitored")),
                                                        n(meta.get("gate_languages")), day_words(first), n(disc)))
    s.b("State origin, all time: \"Since %s we found %s placements of Chinese state text with evidence a reader can "
        "check, %s of the %s China related articles we could judge.\"" % (
            day_words(first), n(at.get("A")), pct(at.get("A"), tot_all), n(tot_all)))
    if c30:
        s.b("State origin, last 30 days: \"In the 30 days to %s, %s placements, %s of China coverage.\"" % (
            day_words(end30), n(c30.get("A")), pct(c30.get("A"), china(c30))))
    s.b("Independent journalism: \"%s of China coverage is independent journalism: %s of %s articles.\"" % (
        portion(at.get("C"), tot_all).capitalize(), n(at.get("C")), n(tot_all)))
    if und["new"]:
        copies = (und["meta"].get("by_route") or {}).get("uncredited_copy")
        s.b("Undetermined: \"A further %s placements are undetermined: the model judged them state origin without "
            "evidence the rules could confirm%s. They are kept out of every count, China coverage included.\"" % (
                n(und_all), ", or as copies of items that are undetermined themselves" if copies else ""))
    else:
        s.b("%s: \"A further %s placements the model alone called state origin, with nothing a reader can check, are "
            "kept out of every count until a hand check confirms them.\"" % (und["name"], n(und_all)))
    s.b("Unchecked state sourcing: \"By one model's judgement, %s articles pass on a factual claim from a Chinese official "
        "source unchecked. That figure is provisional until hand coding agrees with it at a kappa of %s.\"" % (
            n(at.get("B")), n(meta.get("kappa_warning_threshold"))))
    if top_lines and c30:
        lead_a = top_lines[0][1]
        leaders = [iso for iso, a in top_lines if a == lead_a]
        names = " and ".join(country_name(ex, i) for i in leaders)
        verb = "each carried" if len(leaders) > 1 else "carried"
        s.b("Top country: \"In those 30 days %s %s the largest number: %s of the %s state origin placements, %s.\"" % (
            names, verb, n(lead_a), n(c30.get("A")), portion(lead_a, c30.get("A"))))
        to = (dig(meta, "findings", "top_country", "top_outlet") or {})
        if to.get("name") and dig(meta, "findings", "top_country", "iso") == leaders[0] and to.get("total"):
            s.b("Top outlet: \"%s carried %s of them, %s of %s.\"" % (
                to["name"], portion(to.get("count"), to.get("total")), n(to.get("count")), n(to.get("total"))), 1)
    if wld:
        wa = wld.get("all_time") or {}
        s.b("Wires: \"Global press release wires carried %s state origin placements; they are counted worldwide and in no "
            "country.\"" % n(wa.get("A")))
    if pl:
        s.b("Model stage: \"The model stage is paused until %s, with %s items waiting.\"" % (
            day_words((pl.get("until") or "")[:10]), n(meta.get("official_sourcing_pending"))))
    s.end()
    return s.text()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write one numbers sheet from one export's site files.")
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="the export's site data directory (default docs/data)")
    ap.add_argument("--out", default=None, help="the Markdown file to write (default: print)")
    args = ap.parse_args(argv)
    ex = load_export(Path(args.data))
    problems = stamp_problems(ex)
    if problems:
        sys.stderr.write("The files are not from one export:\n" + "\n".join("  " + p for p in problems) + "\n")
        return 2
    text = build(ex)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
