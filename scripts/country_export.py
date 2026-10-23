#!/usr/bin/env python3
"""Aggregate country export for a paper: counts only, for Italy, Canada, Australia and Singapore.

Reads one repository checkout (default this one): the committed audit files
data/export/articles-YYYY-MM-DD.jsonl, docs/data (meta.json, latest.json, outlets.json, the daily files),
sources/outlets.yaml, and the tracker's own definitions (pipeline/model_only.py, pipeline/placements.py,
pipeline/themes.yaml, pipeline/signatures.yaml, pipeline/classify_llm.py). Writes one CSV per table, each
with a country column, and a README.md with every definition and limitation. Never writes article text,
a headline, a quote, a link or an address: headlines and gate terms are read only to assign a theme and
a source, and nothing of them reaches a file.

    python scripts/country_export.py [--repo DIR] [--out DIR] [--countries ITA,CAN,AUS,SGP]
                                     [--overlay FILE] [--only themes]

--overlay applies session labels not yet ingested by the runner (one JSON line per article, the
pipeline.labels line format) to audit rows still waiting for the model, as pipeline.labels.ingest would.
--only themes writes the theme tables (sections 2 and 3) and THEMES_README.md only.
Read only: nothing in the repository is changed. Standard library plus PyYAML.
"""
import argparse
import calendar
import csv
import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import yaml

SCRIPT_ROOT = Path(__file__).resolve().parent.parent
COUNTRIES = ("ITA", "CAN", "AUS", "SGP")
COUNTRY_NAMES = {"ITA": "Italy", "CAN": "Canada", "AUS": "Australia", "SGP": "Singapore"}
OWNER_WINDOW_START = "2025-01-01"
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")

# Codes a placement is counted under. 1a and 1b together are tier 1 (state origin with an evidence route).
T1A, T1B, U, B, C, N = "1a", "1b", "U", "B", "C", "N"
TIER1 = (T1A, T1B)
COVERAGE = (T1A, T1B, B, C)                 # China coverage, the tracker's china_total: A + B + C, U excluded
SCREENED = (T1A, T1B, U, B, C)              # China relevant screened including undetermined

# Text fields of an audit line. None of them, nor any column named after them, is ever written.
TEXT_FIELDS = ("title", "url", "url_hash", "evidence_quote", "reasoning", "headline", "quote", "summary", "body")

# ---------------------------------------------------------------------------
# Themes: the fixed list, the mapping from the tracker's rule based themes, the keyword rules
# ---------------------------------------------------------------------------

TH_TRADE = "trade and tariffs"
TH_TECH = "technology and AI"
TH_MIL = "military and security"
TH_TAIWAN = "Taiwan"
TH_SCS = "South China Sea"
TH_HK = "Hong Kong"
TH_XT = "Xinjiang and Tibet"
TH_DIPLO = "diplomacy and summits"
TH_ECON = "economy and markets (inside China)"
TH_CULT = "culture, tourism and soft promotion"
TH_SOC = "domestic society and human interest"
TH_OTHER = "other"
THEMES = (TH_TRADE, TH_TECH, TH_MIL, TH_TAIWAN, TH_SCS, TH_HK, TH_XT, TH_DIPLO, TH_ECON, TH_CULT, TH_SOC, TH_OTHER)

# Keyword rules for the categories the tracker's themes cannot express. Terms are matched case
# insensitively on word boundaries; a trailing * matches any word characters. "headline" reads the stored
# headline only; "headline_or_gate" reads the headline and the stored gate terms (the China terms the
# relevance gate found in the headline and feed summary); "headline_or_gate_with_tag" accepts a gate term
# only when the tracker also tagged the article with the named theme.
KEYWORD_RULES = [
    {"target": TH_SCS, "reads": "headline_or_gate", "tag": None,
     "terms": ["South China Sea", "West Philippine Sea", "Spratly*", "Paracel*", "Scarborough Shoal",
               "Second Thomas Shoal", "nine-dash line", "Mar Cinese Meridionale", "Mar della Cina Meridionale",
               "mer de Chine méridionale"]},
    {"target": TH_TAIWAN, "reads": "headline_or_gate_with_tag", "tag": "taiwan_hk",
     "terms": ["Taiwan*", "Taïwan*", "Taipei", "Lai Ching-te", "Formosa"]},
    {"target": TH_HK, "reads": "headline_or_gate_with_tag", "tag": "taiwan_hk",
     "terms": ["Hong Kong*", "Hongkong*", "HKSAR", "Jimmy Lai"]},
    {"target": TH_XT, "reads": "headline_or_gate", "tag": None,
     "terms": ["Xinjiang", "Uyghur*", "Uighur*", "Uigur*", "Ouïghour*", "Tibet*", "Tibét*", "Xizang", "Lhasa",
               "Dalai Lama"]},
    {"target": TH_TRADE, "reads": "headline", "tag": None,
     "terms": ["tariff*", "trade", "trade war", "export control*", "anti-dumping", "antidumping", "dumping",
               "rare earth*", "dazi", "dazio", "guerra commerciale", "commercial*", "commercio", "export*",
               "import*", "esportazion*", "importazion*", "terre rare", "droits de douane", "douane*",
               "guerre commerciale", "terres rares", "tarif*"]},
]

# The tracker's rule based themes (pipeline/themes.yaml, version in meta.themes_version) and the fixed
# list category each one maps to. taiwan_hk is resolved by the keyword rules above only.
TRACKER_MAP = {
    "rights": TH_SOC, "politics": TH_SOC, "security": TH_MIL, "energy": TH_ECON, "investment": TH_TRADE,
    "diplomacy": TH_DIPLO, "disasters": TH_SOC, "culture": TH_CULT, "technology": TH_TECH,
    "economy": TH_ECON, "taiwan_hk": None, "other": TH_OTHER,
}
MAP_NOTES = {
    "rights": "Human rights and repression; Xinjiang, Tibet and Hong Kong are taken first by the keyword rules",
    "politics": "Chinese domestic politics; the fixed list has no politics category",
    "security": "direct; the South China Sea is taken first by its keyword rule",
    "energy": "Energy, industry and electric vehicles: Chinese industry",
    "investment": "Belt and Road, investment and infrastructure: cross border economic relations",
    "diplomacy": "direct",
    "disasters": "Disasters, health and environment",
    "culture": "Culture, education and tourism",
    "technology": "direct",
    "economy": "Trade and economy; trade is taken first by its headline keyword rule",
    "taiwan_hk": "not mapped by itself: Taiwan and Hong Kong are told apart by the keyword rules only",
    "other": "the tracker's Other (no theme term found)",
}
# The primary theme is the first rule in this order that applies. Most specific first: the keyword
# rules, then the tracker's themes from the rarest to the most frequent (frequency over the China coverage
# of the four countries in the export of 23 October 2026: rights 227, politics 651, security 1,255,
# energy 1,629, investment 1,747, diplomacy 1,800, disasters 1,961, culture 2,598, technology 3,074,
# economy 3,575). The order is fixed here, so it does not move with the data.
PRIORITY = [("keyword", TH_SCS), ("keyword", TH_TAIWAN), ("keyword", TH_HK), ("keyword", TH_XT),
            ("keyword", TH_TRADE), ("tracker", "rights"), ("tracker", "politics"), ("tracker", "security"),
            ("tracker", "energy"), ("tracker", "investment"), ("tracker", "diplomacy"), ("tracker", "disasters"),
            ("tracker", "culture"), ("tracker", "technology"), ("tracker", "economy"), ("tracker", "other")]


def _term_re(terms: Iterable[str]):
    alts = []
    for t in terms:
        esc = re.escape(t).replace(r"\*", r"\w*").replace(r"\ ", r"\s+").replace(r"\-", r"[\s\-]?")
        alts.append(esc)
    return re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(alts), re.IGNORECASE | re.UNICODE)


_KW = [(r, _term_re(r["terms"])) for r in KEYWORD_RULES]


def keyword_hits(headline: str, gate_terms: List[str], tags: Set[str]) -> Set[str]:
    """The fixed list categories the keyword rules give an article."""
    gate = " | ".join(t for t in gate_terms if not t.startswith("section:"))
    out = set()
    for rule, rx in _KW:
        if rx.search(headline or ""):
            out.add(rule["target"])
        elif rule["reads"] == "headline_or_gate" and rx.search(gate):
            out.add(rule["target"])
        elif rule["reads"] == "headline_or_gate_with_tag" and rule["tag"] in tags and rx.search(gate):
            out.add(rule["target"])
    return out


def primary_theme(headline: str, gate_terms: List[str], tags: Iterable[str]) -> Tuple[str, str]:
    """(fixed list theme, the rule that gave it) for one article: the first rule in PRIORITY that applies."""
    tags = set(tags or []) or {"other"}
    kw = keyword_hits(headline, gate_terms, tags)
    for kind, key in PRIORITY:
        if kind == "keyword" and key in kw:
            return key, "keyword:" + key
        if kind == "tracker" and key in tags and TRACKER_MAP.get(key):
            return TRACKER_MAP[key], "tracker:" + key
    return TH_OTHER, "fallback:other"


# ---------------------------------------------------------------------------
# Chinese state sources: what an article credits or cites
# ---------------------------------------------------------------------------

SOURCES = ("Xinhua", "CGTN", "China Daily", "Global Times", "People's Daily", "CCTV", "MFA spokesperson",
           "embassy", "other")
TRIGGER_SOURCE = {
    "cites_xinhua": "Xinhua", "cites_cgtn": "CGTN", "cites_china_daily": "China Daily",
    "cites_global_times": "Global Times", "cites_peoples_daily": "People's Daily", "cites_cctv": "CCTV",
    "cites_china_media_group": "other", "mofa_spokesperson": "MFA spokesperson",
    "mofa_spokesperson_more": "MFA spokesperson", "chinese_embassy_quoted": "embassy",
    "chinese_embassy_quoted_more": "embassy",
}
# For the short source names the model lists (china_sources_cited), beside the trigger patterns, which are
# written for running text.
_EXTRA = [
    ("CCTV", re.compile(r"(?<!\w)CCTV|China Central Television|Televisione Centrale Cinese", re.I)),
    ("MFA spokesperson", re.compile(
        r"((foreign (affairs )?ministry|ministry of foreign affairs|\bMOFA\b|\bMFA\b|ministero degli esteri|"
        r"affaires (é|e)trang(è|e)res|foreign affairs)[^|]*(spokes\w*|portavoce|porte-parole)|"
        r"(spokes\w*|portavoce|porte-parole)[^|]*(foreign (affairs )?ministry|\bMOFA\b|\bMFA\b|esteri|"
        r"affaires (é|e)trang(è|e)res|foreign affairs)|\bMao Ning\b|\bGuo Jiakun\b|\bLin Jian\b)", re.I)),
    ("embassy", re.compile(r"embass\w*|ambasciat\w*|ambassade\w*|ambassador\w*|ambasciator\w*|ambassadeur\w*|"
                           r"consulat\w*|consolat\w*|consul general|charg(é|e) d.affaires|permanent mission|"
                           r"\benvoy\b", re.I)),
    ("other", re.compile(
        r"ministr\w*|minist(e|è)r\w*|government|governo|gouvernement|state council|politburo|communist party|"
        r"\bCPC\b|\bCCP\b|party committee|Xi Jinping|Li Qiang|Wang Yi|He Lifeng|bureau|administration|"
        r"commission|customs|people.s court|supreme people|police|public security|state security|coast guard|"
        r"\bPLA\b|taiwan affairs office|state media|state[- ]run|state news|state tv|state press|official|"
        r"authorit\w*|autorit\w*|central bank|people.s bank|information office|publicity department|"
        r"international communication cent|association for friendship|united front|state information|"
        r"statistic\w*|media (statali|di stato)|m(é|e)dias? (officiels|d.(É|E)tat)|presse officielle|"
        r"Chine nouvelle|China News Service|China Media Group|\bCMG\b|China Radio International|"
        r"\bICAC\b|\bHKTDC\b|tourism board|SAR government|Beijing \(", re.I)),
]
# State origin signatures (pipeline/signatures.yaml) and the source they credit, by id fragment, in order.
_SIG_SOURCE = [("china_watch", "China Daily"), ("xinhua", "Xinhua"), ("cgtn", "CGTN"),
               ("china_daily", "China Daily"), ("global_times", "Global Times"),
               ("peoples_daily", "People's Daily"), ("cctv", "CCTV"), ("mofa", "MFA spokesperson"),
               ("embassy", "embassy"), ("diplomat", "embassy"), ("china_media_group", "other"), ("cri_", "other"),
               ("china_news_service", "other"), ("state_outlet", "other"), ("state_entity", "other"),
               ("cairorcs", "other"), ("wire_release_headline_issuer", "other")]


class SourceReader:
    """Which Chinese state sources an article credits or cites, from signatures_fired, china_sources_cited
    and gate_terms. One article counts once per source, however often it names it."""

    def __init__(self, signatures: Dict):
        trig = {t["id"]: t["regex"] for t in signatures.get("llm_triggers") or []}
        self.patterns = []
        for tid, src in TRIGGER_SOURCE.items():
            if tid in trig:
                try:
                    self.patterns.append((src, re.compile(trig[tid])))
                except re.error:
                    pass
        self.outlet_patterns = [(s, p) for s, p in self.patterns
                                if s in ("Xinhua", "CGTN", "China Daily", "Global Times", "People's Daily", "other")]

    def from_signatures(self, sigs: List[str]) -> Set[str]:
        out = set()
        for sig in sigs:
            for frag, src in _SIG_SOURCE:
                if frag in sig:
                    out.add(src)
                    break
        return out

    def from_cited(self, cited: List[str]) -> Set[str]:
        out = set()
        for s in cited:
            s = str(s)
            found = {src for src, rx in self.patterns if rx.search(s)}
            for src, rx in _EXTRA:
                if src == "other" and found:
                    continue
                if rx.search(s):
                    found.add(src)
            out |= found
        return out

    def from_gate(self, terms: List[str]) -> Set[str]:
        out = set()
        for t in terms:
            if t.startswith("section:"):
                continue
            for src, rx in self.outlet_patterns:
                if rx.search(t):
                    out.add(src)
            if t.strip().upper() == "CCTV":
                out.add("CCTV")
        return out


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def import_pipeline(repo: Path):
    """The tracker's modules, from the checkout being read when it carries them, else from this one."""
    for root in (repo, SCRIPT_ROOT):
        if (root / "pipeline" / "model_only.py").exists():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            break
    from pipeline import model_only, placements  # noqa: E402
    return model_only, placements


def _json_list(v) -> List:
    if isinstance(v, list):
        return v
    try:
        x = json.loads(v or "[]")
    except (TypeError, ValueError):
        return []
    return x if isinstance(x, list) else []


def _themes_of(v) -> List[str]:
    try:
        d = json.loads(v) if isinstance(v, str) else (v or {})
    except (TypeError, ValueError):
        d = {}
    return (d.get("t") if isinstance(d, dict) else None) or ["other"]


def _themes_body(v) -> int:
    try:
        d = json.loads(v) if isinstance(v, str) else (v or {})
    except (TypeError, ValueError):
        d = {}
    return 1 if isinstance(d, dict) and d.get("b") == 1 else 0


def read_audit(repo: Path, countries: Iterable[str]) -> List[Dict]:
    keep = set(countries)
    rows = []
    for path in sorted((repo / "data" / "export").glob("articles-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("country") in keep:
                    d["_file_day"] = path.stem[len("articles-"):]
                    rows.append(d)
    return rows


def load_json(path: Path) -> Dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def load_outlets_yaml(repo: Path) -> List[Dict]:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with open(repo / "sources" / "outlets.yaml", encoding="utf-8") as fh:
        data = yaml.load(fh, Loader=loader)
    if isinstance(data, dict):
        data = data.get("outlets") or []
    return [o for o in data or [] if isinstance(o, dict)]


def load_signatures(repo: Path) -> Dict:
    for root in (repo, SCRIPT_ROOT):
        p = root / "pipeline" / "signatures.yaml"
        if p.exists():
            with open(p, encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    return {}


# ---------------------------------------------------------------------------
# Session label overlay
# ---------------------------------------------------------------------------

def apply_overlay(rows: List[Dict], path: Path) -> Dict:
    """Session labels not yet ingested by the runner, applied to audit rows still waiting for the model
    (status awaiting_llm) as pipeline.labels.ingest applies them: the line is validated
    (labels.validate), a label under an older prompt is skipped (classify_llm.on_current_prompt), the
    stored category follows classify_llm.store_result (state origin with a private or no issuer is stored
    as C), the route of a state origin label follows classify_llm._model_route (BASIS_ROUTES, a diplomatic
    byline only with a diplomatic issuer, an uncredited copy only with a lookalike reference, which the
    audit files do not carry: a context_match basis is taken as that reference), and every waiting row of
    the same near duplicate group takes the label too, as _settle_pending_dup_siblings does."""
    from pipeline import classify_llm, labels
    stats = {"file": str(path), "lines": 0, "malformed": 0, "older_prompt": 0, "applied": 0, "copied": 0,
             "not_awaiting": 0, "unknown": 0, "inconsistent": 0, "by_category": Counter(), "by_model": Counter(),
             "unattributed_a": 0}
    by_hash = {}
    for r in rows:
        if r.get("url_hash"):
            by_hash.setdefault(r["url_hash"], []).append(r)
    applied_groups = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            stats["lines"] += 1
            try:
                obj = json.loads(text)
                reason = labels.validate(obj)
            except ValueError:
                reason = "invalid JSON"
            if reason:
                stats["malformed"] += 1
                continue
            targets = by_hash.get(obj["url_hash"])
            if not targets:
                stats["unknown"] += 1
                continue
            if not classify_llm.on_current_prompt(obj["model_version"]):
                stats["older_prompt"] += 1
                continue
            obj["_raw"] = text
            data = labels._data(obj)
            for r in targets:
                if r.get("category") or r.get("status") != "awaiting_llm":
                    stats["not_awaiting"] += 1
                    continue
                _set_label(r, data, obj["model_version"], classify_llm, stats)
                stats["applied"] += 1
                if r.get("dup_group_id"):
                    applied_groups.setdefault(r["dup_group_id"], (data, obj["model_version"]))
    for r in rows:
        g = r.get("dup_group_id")
        if g in applied_groups and not r.get("category") and r.get("status") == "awaiting_llm":
            data, mv = applied_groups[g]
            _set_label(r, data, mv, classify_llm, stats, count=False)
            stats["copied"] += 1
    stats["by_category"] = dict(stats["by_category"])
    stats["by_model"] = dict(stats["by_model"])
    return stats


def _set_label(r: Dict, data: Dict, model_version: str, classify_llm, stats: Dict, count: bool = True) -> None:
    flaw = classify_llm.inconsistency_of(data)
    category = data["category"]
    if flaw == classify_llm.STATE_ORIGIN_WITHOUT_STATE_ISSUER:
        category = "C"
    route = None
    if category == "A":
        basis = classify_llm.state_basis_of(data)
        route = classify_llm.BASIS_ROUTES.get(basis) or "unattributed"
        if route == "diplomatic_byline" and classify_llm.issuer_type_of(data) not in (None, "diplomatic"):
            route = "unattributed"
        if route == "unattributed":
            stats["unattributed_a"] += 1
    r.update({"category": category, "method": "llm", "route": route, "model_version": model_version,
              "china_sources_cited": json.dumps(data.get("china_sources_cited") or []), "status": "classified",
              "_overlay": True})
    if count:
        stats["inconsistent"] += 1 if flaw else 0
        stats["by_category"][category] += 1
        stats["by_model"][model_version] += 1


# ---------------------------------------------------------------------------
# Deriving each row
# ---------------------------------------------------------------------------

def derive(rows: List[Dict], model_only, placements, wire_ids: Set[str], reader: SourceReader) -> None:
    widget_prefixes = tuple(getattr(placements, "SHARED_WIDGETS", {}) or {})
    for r in rows:
        r["_day"] = r.get("day_of_record") or (r.get("discovered_at") or "")[:10] or r["_file_day"]
        r["_copy"] = "counted_as" in r and r["counted_as"] is not None
        r["_wire"] = r.get("outlet_id") in wire_ids
        r["_item"] = r.get("dup_group_id") or r.get("id")
        cat = r.get("category")
        if not cat:
            code = None
        else:
            h = model_only.headline_category(cat, r.get("route"), r.get("method"))
            if h == "A":
                code = T1A if r.get("method") == "rules" else T1B
            elif h == model_only.CODE:
                code = U
            elif h in ("B", "C"):
                code = h
            else:
                code = N
        r["_code"] = code
        if r["_copy"]:
            url = r.get("url") or ""
            if widget_prefixes and url.startswith(widget_prefixes):
                r["_copy_kind"] = "shared widget"
            elif placements.series_base(r.get("title")):
                r["_copy_kind"] = "numbered series"
            else:
                r["_copy_kind"] = "same page at two addresses"
        tags = _themes_of(r.get("themes"))
        r["_tags"] = tags
        r["_tag_body"] = _themes_body(r.get("themes"))
        gate = [str(t) for t in _json_list(r.get("gate_terms"))]
        r["_theme"], r["_theme_rule"] = primary_theme(r.get("title") or "", gate, tags)
        sigs = [str(s) for s in _json_list(r.get("signatures_fired"))]
        cited = _json_list(r.get("china_sources_cited"))
        r["_cited_any"] = bool(cited)
        s_sig, s_cited, s_gate = reader.from_signatures(sigs), reader.from_cited(cited), reader.from_gate(gate)
        found = s_sig | s_cited | s_gate
        if found - {"other"}:
            found.discard("other")   # other: a state source found, none of the named ones
        r["_sources"] = found
        r["_src_by"] = {"signatures": s_sig, "cited": s_cited, "gate_terms": s_gate}


def reconcile_copies(rows: List[Dict], daily: Dict[str, Dict], countries: Iterable[str]) -> Dict:
    """Finds the model's uncredited copies of undetermined items (model_only.route_sql, copy_of_undetermined),
    which the audit files cannot show because they carry the stored route and not the lookalike reference,
    from the site's own state origin count per day and country (the daily files' A, made by the same export
    from the database). On a day where this export's tier 1 exceeds the site's A by exactly the number of
    tier 1b uncredited copies, every one of them is a copy of an undetermined item and is counted as
    undetermined (U), as the site counts it; where the excess is 0 none is. Any other excess is left in
    tier 1b and reported as unresolved. Session overlay labels are not in the daily files and are left as
    they are."""
    site = {}
    for f in daily.values():
        for day, d in (f.get("days") or {}).items():
            for c, v in (d.get("countries") or {}).items():
                site[(day, c)] = int(v.get("A") or 0)
    stats = {}
    for c in countries:
        st = {"copies": 0, "moved_to_undetermined": 0, "kept_settled": 0, "unresolved": 0, "mismatch_days": 0,
              "overlay_copies_unchecked": 0}
        by_day = defaultdict(list)
        for r in placements_of(rows, c):
            if r["_code"] in TIER1:
                by_day[r["_day"]].append(r)
        for day, rs in by_day.items():
            base = [r for r in rs if not r.get("_overlay")]
            copies = [r for r in base if r["_code"] == T1B and r.get("route") == "uncredited_copy"]
            st["overlay_copies_unchecked"] += sum(1 for r in rs if r.get("_overlay") and r.get("route") == "uncredited_copy")
            st["copies"] += len(copies)
            if (day, c) not in site:
                st["unresolved"] += len(copies)
                continue
            excess = len(base) - site[(day, c)]
            if excess == 0:
                st["kept_settled"] += len(copies)
            elif copies and excess == len(copies):
                for r in copies:
                    r["_code"] = U
                    r["_copy_of_undetermined"] = True
                st["moved_to_undetermined"] += len(copies)
            elif 0 < excess < len(copies):
                st["unresolved"] += excess
                st["kept_settled"] += len(copies) - excess
            else:
                st["mismatch_days"] += 1
        stats[c] = st
    return stats


def placements_of(rows: List[Dict], country: str) -> List[Dict]:
    """The rows of one country that are placements: a copy another row stands for left out, distribution
    wires (the tracker's worldwide stratum, never a country's) left out."""
    return [r for r in rows if r["country"] == country and not r["_copy"] and not r["_wire"]]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def share(num: int, den: int) -> str:
    return "" if not den else "%.1f" % (100.0 * num / den)


def words_day(day: Optional[str]) -> str:
    if not day:
        return "unknown"
    d = dt.date.fromisoformat(day[:10])
    return "%d %s %d" % (d.day, MONTHS[d.month - 1], d.year)


def words_time(iso: Optional[str]) -> str:
    if not iso:
        return "unknown"
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    return "%s at %s UTC" % (words_day(t.date().isoformat()), t.strftime("%H:%M:%S"))


def fmt(n) -> str:
    return "{:,}".format(n) if isinstance(n, int) else str(n)


def write_csv(out: Path, name: str, header: List[str], rows: List[Dict]) -> Path:
    for h in header:
        assert h.lower() not in TEXT_FIELDS, h
    out.mkdir(parents=True, exist_ok=True)
    p = out / name
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="raise")
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in header})
    return p


_URL = re.compile(r"https?://\S+|www\.\S+")


def no_urls(text: Optional[str]) -> str:
    return _URL.sub("[address removed]", text or "").strip()


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def themes_tables(rows: List[Dict], countries: Iterable[str]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    themes, top5, comparison = [], [], []
    for c in countries:
        ps = placements_of(rows, c)
        cov = [r for r in ps if r["_code"] in COVERAGE]
        n_cov = len(cov)
        t1_all = [r for r in cov if r["_code"] in TIER1]
        other_all = [r for r in cov if r["_code"] not in TIER1]
        per = {}
        for th in THEMES:
            sub = [r for r in cov if r["_theme"] == th]
            t1 = [r for r in sub if r["_code"] in TIER1]
            bc = [r for r in sub if r["_code"] in (B, C)]
            cit = [r for r in bc if r["_sources"]]
            und = [r for r in ps if r["_code"] == U and r["_theme"] == th]
            per[th] = {"country": c, "theme": th, "china_coverage": len(sub), "coverage_denominator": n_cov,
                       "share_of_coverage_pct": share(len(sub), n_cov),
                       "tier1": len(t1), "tier1a_rules": sum(1 for r in t1 if r["_code"] == T1A),
                       "tier1b_model_evidence": sum(1 for r in t1 if r["_code"] == T1B),
                       "tier1_share_of_theme_pct": share(len(t1), len(sub)), "tier1_share_denominator": len(sub),
                       "tier2_provisional": 0,
                       "B_unchecked_state_sourcing": sum(1 for r in sub if r["_code"] == B),
                       "C_independent": sum(1 for r in sub if r["_code"] == C),
                       "citing_official_or_state_B_or_C": len(cit),
                       "citing_B": sum(1 for r in cit if r["_code"] == B),
                       "citing_C": sum(1 for r in cit if r["_code"] == C),
                       "citing_share_of_theme_pct": share(len(cit), len(sub)),
                       "tier3_undetermined_not_in_totals": len(und)}
        by_vol = sorted(THEMES, key=lambda t: (-per[t]["china_coverage"], THEMES.index(t)))
        for i, t in enumerate(by_vol, 1):
            per[t]["rank_by_volume"] = i
        themes.extend(per[t] for t in THEMES)
        # Top 5 by volume, ties at the cut kept and stated.
        top5.extend(_top(c, "volume", by_vol, per, key=lambda t: per[t]["china_coverage"],
                         ok=lambda t: per[t]["china_coverage"] > 0))
        eligible = [t for t in THEMES if per[t]["china_coverage"] >= 10]
        by_share = sorted(eligible, key=lambda t: (-(per[t]["tier1"] / float(per[t]["china_coverage"])),
                                                   -per[t]["china_coverage"], THEMES.index(t)))
        top5.extend(_top(c, "tier1_share_min10", by_share, per,
                         key=lambda t: round(per[t]["tier1"] / float(per[t]["china_coverage"]), 6), ok=lambda t: True,
                         eligible=len(eligible)))
        for th in THEMES:
            a = sum(1 for r in t1_all if r["_theme"] == th)
            o = sum(1 for r in other_all if r["_theme"] == th)
            comparison.append({"country": c, "theme": th, "tier1_articles": a, "tier1_denominator": len(t1_all),
                               "tier1_share_pct": share(a, len(t1_all)), "other_coverage_articles": o,
                               "other_coverage_denominator": len(other_all), "other_coverage_share_pct": share(o, len(other_all)),
                               "difference_pct_points": ("%.1f" % (100.0 * a / len(t1_all) - 100.0 * o / len(other_all)))
                               if t1_all and other_all else ""})
    return themes, top5, comparison


def _top(country, kind, ordered, per, key, ok, eligible=None) -> List[Dict]:
    ordered = [t for t in ordered if ok(t)]
    if not ordered:
        return [{"country": country, "list": kind, "rank": "", "theme": "none",
                 "note": "no theme qualifies" + (" (minimum 10 articles)" if kind != "volume" else "")}]
    cut = key(ordered[min(4, len(ordered) - 1)])
    keep = [t for i, t in enumerate(ordered) if i < 5 or key(t) == cut]
    out = []
    for i, t in enumerate(keep, 1):
        p = per[t]
        tied = [x for x in keep if key(x) == key(t) and x != t]
        out.append({"country": country, "list": kind, "rank": i, "theme": t, "china_coverage": p["china_coverage"],
                    "coverage_denominator": p["coverage_denominator"], "share_of_coverage_pct": p["share_of_coverage_pct"],
                    "tier1": p["tier1"], "tier1_share_of_theme_pct": p["tier1_share_of_theme_pct"],
                    "tier1_share_denominator": p["china_coverage"],
                    "themes_eligible": eligible if eligible is not None else "",
                    "note": ("tied with " + "; ".join(tied)) if tied else ""})
    return out


def mapping_rows() -> List[Dict]:
    out = []
    pr = {k: i for i, k in enumerate(PRIORITY, 1)}
    for rule in KEYWORD_RULES:
        out.append({"country": "all", "kind": "keyword_rule", "priority": pr[("keyword", rule["target"])],
                    "target_theme": rule["target"], "tracker_theme": rule["tag"] or "",
                    "reads": rule["reads"], "terms": "; ".join(rule["terms"]),
                    "note": "deterministic keyword rule; no model"})
    for key, target in TRACKER_MAP.items():
        out.append({"country": "all", "kind": "tracker_theme", "priority": pr.get(("tracker", key), ""),
                    "target_theme": target or "(keyword rules only)", "tracker_theme": key, "reads": "tracker tag",
                    "terms": "", "note": MAP_NOTES.get(key, "")})
    out.append({"country": "all", "kind": "fallback", "priority": len(PRIORITY) + 1, "target_theme": TH_OTHER,
                "tracker_theme": "", "reads": "", "terms": "", "note": "no rule applies"})
    return out


def totals_table(rows: List[Dict], c: str, latest: Dict, rec: Optional[Dict] = None) -> List[Dict]:
    allc = [r for r in rows if r["country"] == c]
    ps = placements_of(rows, c)
    by = Counter(r["_code"] for r in ps)
    cov = sum(by[k] for k in COVERAGE)
    scr = sum(by[k] for k in SCREENED)
    t1 = by[T1A] + by[T1B]
    bc = [r for r in ps if r["_code"] in (B, C)]
    cit = [r for r in bc if r["_sources"]]
    lat = ((latest.get("countries") or {}).get(c) or {}).get("all_time") or {}
    out = []

    def add(measure, n, den_name="", den=None, note=""):
        out.append({"country": c, "measure": measure, "count": n, "denominator_name": den_name,
                    "denominator": "" if den is None else den, "share_pct": share(n, den) if den else "", "note": note})

    add("stored gate relevant rows", len(allc), note="every audit line of the country")
    add("distribution wire rows left out", sum(1 for r in allc if r["_wire"] and not r["_copy"]),
        note="the tracker counts global release wires as its own worldwide stratum, never in a country")
    add("syndicated copies removed (counted_as)", sum(1 for r in allc if r["_copy"]), "stored gate relevant rows", len(allc))
    add("placements", len(ps))
    add("placements without a label yet", by[None], "placements", len(ps),
        "paywalled, failed, blocked by robots.txt, queued or waiting for the model")
    add("China relevant screened including undetermined (A + B + C + U)", scr)
    add("China coverage (A + B + C, U excluded)", cov)
    add("tier 1 state origin with an evidence route", t1, "China coverage (A + B + C)", cov)
    add("tier 1a found by the rules", by[T1A], "China coverage (A + B + C)", cov)
    add("tier 1b evidence route named by the model", by[T1B], "China coverage (A + B + C)", cov)
    add("tier 2 provisional (model flagged and hand confirmed)", 0, "China coverage (A + B + C)", cov,
        "no hand coding exists yet (meta.articles_reviewed 0, every human_category empty)")
    add("tier 3 model only, undetermined (U), not in any total", by[U], "China relevant screened including undetermined", scr)
    add("B unchecked state sourcing", by[B], "China coverage (A + B + C)", cov)
    add("C independent journalism", by[C], "China coverage (A + B + C)", cov)
    add("citing a Chinese official or state outlet without state origin (B or C)", len(cit), "B + C", len(bc))
    add("of which B", sum(1 for r in cit if r["_code"] == B), "B", by[B])
    add("of which C", sum(1 for r in cit if r["_code"] == C), "C", by[C])
    add("B or C with any source listed by the model (china_sources_cited not empty)",
        sum(1 for r in bc if r["_cited_any"]), "B + C", len(bc), "broader: includes companies and universities")
    add("not about China after the gate (N)", by[N], "placements with a label", len(ps) - by[None])
    for code, name in ((T1A, "tier 1a"), (T1B, "tier 1b"), (U, "tier 3 undetermined")):
        sub = [r for r in ps if r["_code"] == code]
        add("underlying items, %s" % name, len({r["_item"] for r in sub}), "placements, %s" % name, len(sub),
            "near duplicate title groups (dup_group_id); body links and language editions are not in the audit files")
    t1rows = [r for r in ps if r["_code"] in TIER1]
    add("underlying items, tier 1", len({r["_item"] for r in t1rows}), "placements, tier 1", len(t1rows))
    covrows = [r for r in ps if r["_code"] in COVERAGE]
    add("underlying items, China coverage", len({r["_item"] for r in covrows}), "placements, China coverage", len(covrows))
    rec = rec or {}
    add("model uncredited copies in the audit files (stored route uncredited_copy)", rec.get("copies", 0),
        note="tier 1b by stored route; the audit files carry no lookalike reference")
    add("of which copies of an undetermined item, counted as undetermined (U)", rec.get("moved_to_undetermined", 0),
        "model uncredited copies", rec.get("copies", 0),
        "found from the site's own daily state origin count (daily files A): on those days tier 1 exceeded it by exactly these copies")
    add("of which copies of settled state origin, kept in tier 1b", rec.get("kept_settled", 0), "model uncredited copies", rec.get("copies", 0))
    add("of which unresolved, left in tier 1b", rec.get("unresolved", 0), "model uncredited copies", rec.get("copies", 0),
        "the daily count could not tell which copies they are")
    if rec.get("overlay_copies_unchecked"):
        add("session overlay uncredited copies, reference unchecked", rec["overlay_copies_unchecked"])
    if lat:
        add("site figure: state origin (latest.json all_time A)", lat.get("A", 0),
            note="computed by the tracker from its database, where an uncredited copy of an undetermined item is undetermined")
        add("site figure: undetermined (latest.json all_time U)", lat.get("U", 0))
        add("site figure: China coverage (latest.json china_total)", lat.get("china_total", 0))
    return out


def dedup_table(rows: List[Dict], c: str) -> List[Dict]:
    allc = [r for r in rows if r["country"] == c and not r["_wire"]]
    out = []
    groups = [("tier 1a", (T1A,)), ("tier 1b", (T1B,)), ("tier 1", TIER1), ("tier 3 undetermined", (U,)), ("B", (B,)),
              ("C", (C,)), ("China coverage", COVERAGE), ("not about China", (N,)), ("no label yet", (None,))]
    for name, codes in groups:
        stored = [r for r in allc if r["_code"] in codes]
        copies = [r for r in stored if r["_copy"]]
        ps = [r for r in stored if not r["_copy"]]
        items = len({r["_item"] for r in ps})
        kinds = Counter(r["_copy_kind"] for r in copies)
        out.append({"country": c, "group": name, "stored_rows": len(stored), "copies_removed": len(copies),
                    "copies_shared_widget": kinds["shared widget"], "copies_numbered_series": kinds["numbered series"],
                    "copies_same_page_two_addresses": kinds["same page at two addresses"], "placements": len(ps),
                    "underlying_items": items, "placements_merged_into_items": len(ps) - items})
    return out


def outlets_table(rows: List[Dict], c: str, names: Dict[str, str], widget_names: Dict[str, str]) -> List[Dict]:
    ps = [r for r in placements_of(rows, c) if r["_code"] in SCREENED]
    per = defaultdict(Counter)
    for r in ps:
        carrier = r.get("outlet_id")
        for prefix, w in widget_names.items():
            if (r.get("url") or "").startswith(prefix):
                carrier = w
        per[carrier][r["_code"]] += 1
    vol = {k: sum(v[x] for x in COVERAGE) for k, v in per.items()}
    order = sorted(per, key=lambda k: (-vol[k], k))
    order = [k for k in order if vol[k] > 0]
    if not order:
        return []
    cut = vol[order[min(9, len(order) - 1)]]
    keep = [k for i, k in enumerate(order) if i < 10 or vol[k] == cut]
    total = sum(vol.values())
    out = []
    for i, k in enumerate(keep, 1):
        v = per[k]
        t1 = v[T1A] + v[T1B]
        tied = [x for x in keep if vol[x] == vol[k] and x != k]
        out.append({"country": c, "rank": i, "outlet_id": k, "outlet_name": names.get(k, k), "china_coverage": vol[k],
                    "coverage_denominator_country": total, "share_of_country_coverage_pct": share(vol[k], total),
                    "tier1": t1, "tier1a_rules": v[T1A], "tier1b_model_evidence": v[T1B],
                    "tier1_share_of_outlet_pct": share(t1, vol[k]), "tier1_share_denominator": vol[k],
                    "B": v[B], "C": v[C], "tier3_undetermined_not_in_totals": v[U],
                    "note": ("tied with " + "; ".join(names.get(x, x) for x in tied)) if tied else ""})
    return out


def sources_table(rows: List[Dict], c: str) -> List[Dict]:
    ps = [r for r in placements_of(rows, c) if r["_code"] in SCREENED]
    cov = sum(1 for r in ps if r["_code"] in COVERAGE)
    out = []
    for s in SOURCES:
        sub = [r for r in ps if s in r["_sources"]]
        by = Counter(r["_code"] for r in sub)
        n_cov = sum(by[k] for k in COVERAGE)
        out.append({"country": c, "source": s, "articles_china_coverage": n_cov, "coverage_denominator": cov,
                    "share_of_coverage_pct": share(n_cov, cov), "tier1": by[T1A] + by[T1B], "tier1a_rules": by[T1A],
                    "tier1b_model_evidence": by[T1B], "B": by[B], "C": by[C], "tier3_undetermined_not_in_totals": by[U],
                    "via_signatures": sum(1 for r in sub if s in r["_src_by"]["signatures"]),
                    "via_model_source_list": sum(1 for r in sub if s in r["_src_by"]["cited"]),
                    "via_gate_terms": sum(1 for r in sub if s in r["_src_by"]["gate_terms"]),
                    "note": "an article naming several named sources counts once under each; other counts an article whose state source is none of the named ones"})
    none = [r for r in ps if r["_code"] in COVERAGE and not r["_sources"]]
    out.append({"country": c, "source": "no state source found", "articles_china_coverage": len(none),
                "coverage_denominator": cov, "share_of_coverage_pct": share(len(none), cov),
                "tier1": sum(1 for r in none if r["_code"] in TIER1), "B": sum(1 for r in none if r["_code"] == B),
                "C": sum(1 for r in none if r["_code"] == C), "note": ""})
    return out


ROUTE_GROUPS = [
    ("wire_credit", "agency syndication (wire credit or dateline)"),
    ("distribution_stamp", "paid release (distribution stamp)"),
    ("sponsored_disclosure", "sponsored placement (disclosure found by the rules)"),
    ("diplomatic_byline", "signed column (diplomatic byline)"),
    ("uncredited_copy", "other: uncredited copy of a credited state item"),
    (None, "other: rules label from before routes were stored"),
]


def routes_table(rows: List[Dict], c: str) -> List[Dict]:
    t1 = [r for r in placements_of(rows, c) if r["_code"] in TIER1]
    out = []
    for route, name in ROUTE_GROUPS:
        sub = [r for r in t1 if (r.get("route") or None) == route]
        out.append({"country": c, "route_group": name, "stored_route": route or "(none)", "tier1": len(sub),
                    "tier1_denominator": len(t1), "share_of_tier1_pct": share(len(sub), len(t1)),
                    "tier1a_rules": sum(1 for r in sub if r["_code"] == T1A),
                    "tier1b_model_evidence": sum(1 for r in sub if r["_code"] == T1B)})
    known = {g for g, _ in ROUTE_GROUPS}
    rest = [r for r in t1 if (r.get("route") or None) not in known]
    if rest:
        out.append({"country": c, "route_group": "other: any other stored route", "stored_route": "other",
                    "tier1": len(rest), "tier1_denominator": len(t1), "share_of_tier1_pct": share(len(rest), len(t1)),
                    "tier1a_rules": sum(1 for r in rest if r["_code"] == T1A),
                    "tier1b_model_evidence": sum(1 for r in rest if r["_code"] == T1B)})
    return out


def months_between(start: str, end: str) -> List[str]:
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while (y, m) <= (int(end[:4]), int(end[5:7])):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def monthly_table(rows: List[Dict], c: str, first: str, last: str) -> List[Dict]:
    ps = placements_of(rows, c)
    out = []
    for mo in months_between(OWNER_WINDOW_START, last):
        if mo < first[:7]:
            out.append({"country": c, "month": mo, "collected": "no", "first_day": "", "last_day": ""})
            continue
        sub = [r for r in ps if r["_day"][:7] == mo]
        by = Counter(r["_code"] for r in sub)
        cov = sum(by[k] for k in COVERAGE)
        t1 = by[T1A] + by[T1B]
        fd = max(first, mo + "-01")
        ld = min(last, "%s-%02d" % (mo, calendar.monthrange(int(mo[:4]), int(mo[5:7]))[1]))
        partial = fd != mo + "-01" or ld == last
        out.append({"country": c, "month": mo, "collected": "partial" if partial else "yes", "first_day": fd, "last_day": ld,
                    "china_coverage": cov, "tier1": t1, "tier1a_rules": by[T1A], "tier1b_model_evidence": by[T1B],
                    "tier1_share_pct": share(t1, cov), "tier1_share_denominator": cov, "tier2_provisional": 0,
                    "tier3_undetermined_not_in_totals": by[U], "B": by[B], "C": by[C]})
    return out


def quality_tables(rows: List[Dict], c: str, latest: Dict, daily: Dict[str, Dict], outlets_json: List[Dict],
                   outlets_yaml: List[Dict], first: str, gen_at: str, lang_support: Dict) -> Tuple[List[Dict], List[Dict]]:
    allc = [r for r in rows if r["country"] == c and not r["_wire"]]
    ps = placements_of(rows, c)
    lc = (latest.get("countries") or {}).get(c) or {}
    days_c = [((day.get("countries") or {}).get(c) or {}) for f in daily.values() for day in (f.get("days") or {}).values()]
    disc = sum(int(v.get("disc") or 0) for v in days_c)
    rel = sum(int(v.get("rel") or 0) for v in days_c)
    q = []

    def add(measure, n, basis, den_name="", den=None, note=""):
        q.append({"country": c, "measure": measure, "count": n, "basis": basis, "denominator_name": den_name,
                  "denominator": "" if den is None else den, "share_pct": share(n, den) if den else "", "note": note})

    add("items discovered in the feeds", disc, "daily files, sum of disc over every day")
    add("gate relevant stored rows", len(allc), "audit files")
    add("removed by the relevance gate (not about China by the keyword gate)", max(0, disc - len(allc)),
        "daily files disc minus audit rows", "items discovered", disc,
        "estimate: gated out rows are pruned from the database, so the count is the difference")
    add("gate relevant placements (daily files rel)", rel, "daily files, sum of rel")
    nl = [r for r in ps if r["_code"] is not None]
    add("removed after the gate: labelled not about China (N)", sum(1 for r in nl if r["_code"] == N),
        "audit files, current label", "placements with a label", len(nl))
    for st in ("paywalled", "failed", "blocked_robots", "queued", "awaiting_llm", "fetched"):
        n = sum(1 for r in ps if r["_code"] is None and r.get("status") == st)
        add("placements unread or unlabelled: %s" % st, n, "audit files, status", "placements", len(ps))
    langs = Counter(r.get("language") or "und" for r in ps)
    for lang, n in sorted(langs.items()):
        cov = sum(1 for r in ps if (r.get("language") or "und") == lang and r["_code"] in COVERAGE)
        flags = ["gate keyword list: %s" % ("own" if lang in lang_support["gate"] else "international and English terms only"),
                 "signature language patterns: %s" % ("own" if lang in lang_support["signatures"] else "other language family only"),
                 "theme terms: %s" % ("own" if lang in lang_support["themes"] else "international and English terms only")]
        add("placements in language %s" % lang, n, "audit files", "placements", len(ps), "; ".join(flags) + "; China coverage %d" % cov)
    uncovered = sum(n for lang, n in langs.items() if lang not in lang_support["gate"] or lang not in lang_support["themes"])
    add("placements in a language without its own gate or theme terms", uncovered, "audit files", "placements", len(ps))
    for lang, n in sorted((lc.get("excluded_languages") or {}).items()):
        add("registered outlets in excluded language %s (never read)" % lang, n, "latest.json excluded_languages",
            note="Chinese language outlets are registered but never monitored, by project rule")
    # Feeds and outlets
    gen = dt.datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
    yml = {o["id"]: o for o in outlets_yaml if o.get("country") == c}
    feed_rows = []
    failing7 = deact = 0
    for o in sorted((x for x in outlets_json if x.get("country") == c), key=lambda x: x["id"]):
        y = yml.get(o["id"], {})
        feeds = o.get("feeds") or []
        bad = [f for f in feeds if not f.get("ok")]
        over7 = []
        for f in bad:
            lo = f.get("last_ok")
            if not lo or (gen - dt.datetime.fromisoformat(lo.replace("Z", "+00:00"))).days >= 7:
                over7.append(f)
        since = str(y.get("inactive_since") or "")
        reason = no_urls(y.get("inactive_reason") or o.get("inactive_reason"))
        auto7 = "more than 7 days" in reason or "every feed failed on every check" in reason
        in_window = bool(since) and since >= first
        if o.get("active") and over7:
            failing7 += 1
        if in_window:
            deact += 1
        last_oks = [f.get("last_ok") for f in feeds if f.get("last_ok")]
        feed_rows.append({"country": c, "outlet_id": o["id"], "outlet_name": o.get("name") or o["id"],
                          "active_at_export": "yes" if o.get("active") else "no", "collector": o.get("collector") or "",
                          "inactive_since": since,
                          "deactivated_in_window": "yes" if in_window else ("before window" if since else ""),
                          "deactivated_after_feed_failure_over_7_days": "yes" if auto7 else "",
                          "feeds": len(feeds), "feeds_failing_at_export": len(bad),
                          "feeds_failing_7_days_or_more_at_export": len(over7),
                          "feeds_frozen_at_export": sum(1 for f in feeds if f.get("frozen")),
                          "max_consecutive_failures": max([int(f.get("consecutive_failures") or 0) for f in feeds] or [0]),
                          "latest_success": max(last_oks)[:10] if last_oks else "",
                          "inactive_reason": reason[:400]})
    add("active outlets with a feed failing for 7 days or more at export", failing7, "outlets.json feed state at export",
        note="a failure that ended before the export is not in the committed data")
    add("outlets deactivated in the window", deact, "sources/outlets.yaml inactive_since")
    add("outlets deactivated in the window after every feed failed for more than 7 days", sum(
        1 for f in feed_rows if f["deactivated_after_feed_failure_over_7_days"] and f["deactivated_in_window"] == "yes"),
        "sources/outlets.yaml inactive_reason")
    for w in lc.get("warnings") or []:
        add("site warning: %s" % w.get("type"), 1, "latest.json warnings", note=no_urls(w.get("text"))[:400])
    return q, feed_rows


def models_table(rows: List[Dict], c: str) -> List[Dict]:
    ps = [r for r in placements_of(rows, c) if r["_code"] is not None]
    per = defaultdict(Counter)
    for r in ps:
        per[(r.get("method") or "", r.get("model_version") or "", r.get("ruleset_version") or "",
             "session overlay" if r.get("_overlay") else "audit file")][r["_code"]] += 1
    out = []
    for (method, mv, rv, src), v in sorted(per.items()):
        out.append({"country": c, "method": method, "model_version": mv or "(rules, no model)", "ruleset_version": rv,
                    "label_source": src, "placements": sum(v.values()), "tier1a_rules": v[T1A],
                    "tier1b_model_evidence": v[T1B], "tier3_undetermined": v[U], "B": v[B], "C": v[C], "N": v[N]})
    return out


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------

def _mapping_md() -> str:
    lines = ["| Priority | Rule | Gives | Reads |", "|---|---|---|---|"]
    pr = {k: i for i, k in enumerate(PRIORITY, 1)}
    for rule in KEYWORD_RULES:
        lines.append("| %d | keyword rule | %s | %s |" % (pr[("keyword", rule["target"])], rule["target"],
                                                     {"headline": "headline only",
                                                      "headline_or_gate": "headline or gate terms",
                                                      "headline_or_gate_with_tag": "headline, or gate terms when the tracker tagged Taiwan and Hong Kong"}[rule["reads"]]))
    for kind, key in PRIORITY:
        if kind == "tracker":
            lines.append("| %d | tracker theme `%s` | %s | %s |" % (pr[(kind, key)], key, TRACKER_MAP[key], MAP_NOTES[key]))
    lines.append("| %d | none of the above | other | |" % (len(PRIORITY) + 1))
    return "\n".join(lines)


def themes_readme(ctx: Dict, themes: List[Dict], top5: List[Dict]) -> str:
    s = [header_md(ctx, "Theme tables"),
         "## Files", "",
         "`themes.csv` per theme and country; `themes_top5.csv` the top 5 by volume and by tier 1 share; "
         "`theme_comparison.csv` the theme shares of tier 1 against all other China coverage; `theme_mapping.csv` "
         "the mapping and keyword rules.", "",
         TIERS_SHORT, "",
         theme_method_md(ctx), "",
         "## Headline figures", ""]
    for c in ctx["countries"]:
        rows_c = [t for t in themes if t["country"] == c]
        cov = sum(t["china_coverage"] for t in rows_c)
        t1 = sum(t["tier1"] for t in rows_c)
        s.append("%s: China coverage %s articles; tier 1 %s (%s percent of %s; 1a %s, 1b %s); tier 2 0; tier 3 "
                 "undetermined %s, not in any total; B or C citing a Chinese official or state outlet %s." % (
                     COUNTRY_NAMES.get(c, c), fmt(cov), fmt(t1), share(t1, cov), fmt(cov),
                     fmt(sum(t["tier1a_rules"] for t in rows_c)), fmt(sum(t["tier1b_model_evidence"] for t in rows_c)),
                     fmt(sum(t["tier3_undetermined_not_in_totals"] for t in rows_c)),
                     fmt(sum(t["citing_official_or_state_B_or_C"] for t in rows_c))))
        s.append("")
        tops = [t for t in top5 if t["country"] == c and t["list"] == "volume"]
        sh = [t for t in top5 if t["country"] == c and t["list"] == "tier1_share_min10"]
        s.append("%s: top themes by volume %s. By tier 1 share (minimum 10 articles): %s." % (
            COUNTRY_NAMES.get(c, c),
            "; ".join("%s %s of %s" % (t["theme"], fmt(t.get("china_coverage", 0)), fmt(t.get("coverage_denominator", 0))) for t in tops if t["theme"] != "none") or "none",
            "; ".join("%s %s of %s (%s percent)" % (t["theme"], fmt(t["tier1"]), fmt(t["china_coverage"]), t["tier1_share_of_theme_pct"])
                      for t in sh if t["theme"] != "none") or "no theme has 10 articles"))
        s.append("")
    return "\n".join(s) + "\n"


TIERS_SHORT = "\n".join([
    "## Tiers", "",
    "Tier 1: state origin with an evidence route, as the site counts it. 1a found by the rules (wire credit or "
    "dateline, distribution stamp, sponsored disclosure found by the rules, diplomatic byline); 1b the evidence "
    "route named by the model (wire credit, distribution stamp, diplomatic byline, or an uncredited copy of a "
    "settled state origin item). Tier 2, provisional (model flagged and hand confirmed): 0, because no hand coding "
    "exists yet. Tier 3: undetermined (code U, `pipeline.model_only`), the model's state origin calls with no "
    "evidence route; never added to state origin or to any total. China coverage is tier 1 + B + C. B is unchecked "
    "state sourcing, C independent journalism. Placements: one article at one outlet, counted once per country; "
    "copies another row stands for (`counted_as` in the audit files) are removed."])


def header_md(ctx: Dict, title: str) -> str:
    ov = ctx.get("overlay")
    lines = ["# China Media Tracker: %s for %s" % (title, ", ".join(COUNTRY_NAMES.get(c, c) for c in ctx["countries"])), "",
             "Window asked for: 1 January 2025 to the latest export. Effective window: %s to %s. The tracker began "
             "collecting on %s (`meta.first_discovered`); no data exists before that day, and every month before "
             "it is marked not collected. The last day, %s, is partial: it runs to the export time." % (
                 words_day(ctx["first"]), words_day(ctx["last"]), words_day(ctx["first_discovered"]), words_day(ctx["last"])),
             "", "Export: generated %s (`meta.generated_at` `%s`), schema %s." % (
                 words_time(ctx["generated_at"]), ctx["generated_at"], ctx.get("schema_version")), ""]
    if ov:
        lines += ["Session labels: %s labels from `%s` were applied to articles still waiting for the model (%s more "
                  "copied to waiting near duplicates of them). These are session labels not yet ingested by the "
                  "runner, model version %s; they will change when the runner ingests them. Lines skipped: %s "
                  "malformed, %s under an older prompt, %s for articles not in these countries, %s for articles not "
                  "waiting." % (fmt(ov["applied"]), Path(ov["file"]).name, fmt(ov["copied"]),
                                ", ".join("`%s` (%s)" % (k, fmt(v)) for k, v in sorted(ov["by_model"].items())) or "none",
                                fmt(ov["malformed"]), fmt(ov["older_prompt"]), fmt(ov["unknown"]), fmt(ov["not_awaiting"])), ""]
    rec = ctx.get("reconcile") or {}
    if rec:
        lines += ["Uncredited copies: a model uncredited copy counts as state origin only when the item it repeats is "
                  "settled state origin; otherwise it is undetermined, as on the site. The audit files cannot show "
                  "which, so it is recovered from the site's own daily state origin counts. Moved to undetermined: %s. "
                  "Unresolved and left in tier 1b: %s." % (
                      "; ".join("%s %s of %s" % (COUNTRY_NAMES.get(c, c), fmt(rec[c]["moved_to_undetermined"]),
                                                 fmt(rec[c]["copies"])) for c in ctx["countries"] if c in rec),
                      fmt(sum(rec[c]["unresolved"] for c in rec))), ""]
    lines += ["Counts only. No article text, headline, quote or address is in any file. Every share comes with its "
              "numerator and denominator columns.", ""]
    return "\n".join(lines)


def theme_method_md(ctx: Dict) -> str:
    return "\n".join([
        "## Themes", "",
        "Theme classifier: rules, the tracker's theme counter (`pipeline/themes.py`, terms in `pipeline/themes.yaml`, "
        "version `%s`), which tags an article with every theme whose terms appear in its title, feed summary or body. "
        "No model assigns a theme. Hand coded validation of themes: none, so agreement is not known. %s of the "
        "China coverage placements were tagged with their body read; the rest from title and summary only." % (
            ctx.get("themes_version"), ctx.get("tag_body_text", "")), "",
        "Each China relevant article gets one primary theme from the fixed list: %s. The tracker's themes cannot "
        "tell Taiwan from Hong Kong, and have no South China Sea, Xinjiang and Tibet or trade category; those come "
        "from deterministic keyword rules on the stored headline and the stored gate terms only (the China terms "
        "the relevance gate found in the headline and feed summary), never a model. The primary theme is the first "
        "rule that applies in this order: the keyword rules first, then the tracker's themes from the rarest to the "
        "most frequent, so the most specific theme wins. The order is fixed in the script and does not move with "
        "the data." % "; ".join(THEMES), "",
        _mapping_md(), "",
        "Keyword terms are listed in full in `theme_mapping.csv`. Theme totals are China coverage (tier 1, B and C); "
        "undetermined items (tier 3) are shown in their own column and never added in. Tier 1 share of a theme is "
        "tier 1 over that theme's China coverage. Citing a Chinese official or state outlet is a B or C article in "
        "which a state source was found (see Sources). The top 5 by tier 1 share takes only themes with at least 10 "
        "articles; ties at the cut are kept and named. The comparison gives each theme's share of tier 1 (denominator "
        "all tier 1 articles) beside its share of all other China coverage, B and C (denominator all B and C articles)."])


def full_readme(ctx: Dict, tables: Dict) -> str:
    totals = tables["totals"]

    def tv(c, measure):
        for r in totals:
            if r["country"] == c and r["measure"] == measure:
                return r["count"]
        return 0

    s = [header_md(ctx, "country export"),
         "## Versions", "",
         "Ruleset `%s` (`meta.ruleset_version`); labels in these countries were made under rulesets %s. Gate `%s`. "
         "Model labels: API model `%s` (`meta.llm_model`), prompt `%s`; labels by model version in these countries: %s. "
         "Labels whose version names a session were made in a session run by the owner, not through the API "
         "(`meta.session_labels`). Theme classifier: rules, themes `%s`, no hand validation (agreement: none)." % (
             ctx["ruleset_version"], ctx["ruleset_mix"], ctx["gate_version"], ctx["llm_model"], ctx["prompt_version"],
             ctx["model_mix"], ctx["themes_version"]), "",
         "## Headline figures", ""]
    for c in ctx["countries"]:
        cov = tv(c, "China coverage (A + B + C, U excluded)")
        s.append("%s: China coverage %s placements; tier 1 %s (%s percent of %s), of which 1a %s and 1b %s; tier 2 0; "
                 "tier 3 undetermined %s (not in any total); citing a Chinese official or state outlet without state "
                 "origin %s (B %s); copies removed %s." % (
                     COUNTRY_NAMES.get(c, c), fmt(cov), fmt(tv(c, "tier 1 state origin with an evidence route")),
                     share(tv(c, "tier 1 state origin with an evidence route"), cov), fmt(cov),
                     fmt(tv(c, "tier 1a found by the rules")), fmt(tv(c, "tier 1b evidence route named by the model")),
                     fmt(tv(c, "tier 3 model only, undetermined (U), not in any total")),
                     fmt(tv(c, "citing a Chinese official or state outlet without state origin (B or C)")),
                     fmt(tv(c, "of which B")), fmt(tv(c, "syndicated copies removed (counted_as)"))))
        s.append("")
    s += ["## Files", "",
          "| File | Table |", "|---|---|",
          "| `totals.csv` | 1. totals per country, with the site's own figures beside them |",
          "| `dedup.csv` | copies removed, placements and underlying items per tier |",
          "| `themes.csv`, `themes_top5.csv` | 2. themes |",
          "| `theme_comparison.csv` | 3. theme shares of tier 1 against all other China coverage |",
          "| `theme_mapping.csv` | the theme mapping, keyword rules and priority |",
          "| `outlets.csv` | 4. top 10 outlets by China coverage |",
          "| `sources.csv` | 5. Chinese state sources credited or cited |",
          "| `routes.csv` | 6. routes of tier 1 |",
          "| `monthly.csv` | 7. monthly series |",
          "| `data_quality.csv`, `outlet_feed_health.csv` | 8. data quality |",
          "| `model_versions.csv` | labels by method and model version |", "",
          "## Definitions", "",
          "Basis. The committed audit files (`data/export/articles-*.jsonl`), one line per gate relevant stored "
          "article with its current label, route, method, outlet, country and status; `docs/data` for the site's "
          "own figures, discovery counts and feed state; `sources/outlets.yaml` for outlet names and deactivations. "
          "The tracker's own functions are used where they can be imported: `pipeline.model_only` "
          "(`headline_category`, `is_undetermined`) for the tiers, `pipeline.placements` for the kinds of copy.", "",
          "Placement. One article at one outlet, counted once per country. A row whose audit line carries "
          "`counted_as` is a copy another row stands for (a shared widget such as the AAP release pages of ten "
          "Australian mastheads, one page at two addresses, the numbered parts of one photo series) and is removed; "
          "`dedup.csv` gives the number removed by kind and tier. Underlying items: placements grouped by the "
          "tracker's near duplicate title groups (`dup_group_id`) within a country, reported separately. Global "
          "release wires are the tracker's worldwide stratum and are never in a country.", "",
          "Day of an article: its day of record (a backfilled item's publication day) or else its discovery day, as "
          "the tracker counts it.", "",
          "Tier 1, state origin with an evidence route: a current state origin label that is not undetermined by "
          "`pipeline.model_only`, which is what the site counts as state origin. Tier 1a: found by the rules (method rules), any "
          "route: wire credit or dateline, distribution stamp, sponsored disclosure found on the article by the rules, "
          "diplomatic byline, or a rules label stored before routes were. Tier 1b: a model label whose evidence route "
          "the model named: wire credit, distribution stamp, diplomatic byline, or uncredited copy (a close text match "
          "to a credited state item within 11 days, counted only when the item it repeats is settled state origin, "
          "see limitation 2). The site counts both as state origin; the owner's wording "
          "\"found by the rules\" fits tier 1a alone, so both are given and every table keeps them apart. A model "
          "sponsored disclosure the rules did not find is undetermined, never tier 1.", "",
          "Tier 2, provisional (flagged by the model and confirmed by hand): 0 in every table. The tracker has no hand "
          "coding yet: `meta.articles_reviewed` is %s and no audit line has a `human_category`." % ctx["articles_reviewed"], "",
          "Tier 3, model only: undetermined (code U), the model's state origin calls with no evidence route "
          "(`unattributed` or no route) and its sponsored disclosures the rules did not confirm. Never added to state "
          "origin, to China coverage or to any denominator but the screened total.", "",
          "China coverage: tier 1 + B + C, the tracker's `china_total`, undetermined excluded. China relevant screened "
          "including undetermined: tier 1 + B + C + U. Both are in `totals.csv`. Articles still without a label "
          "(paywalled, failed, blocked by robots.txt, queued, waiting for the model) are in neither.", "",
          "Citing a Chinese official or state outlet without state origin: a B (unchecked state sourcing) or C "
          "(independent journalism) placement in which at least one Chinese state source was found, counted once "
          "per article however many it names. B is given separately; every B article rests on Chinese official "
          "sourcing by definition, so B articles with no source found are model labels whose source list named "
          "none the patterns recognise. The broader count of B or C articles with any source listed by the model "
          "(companies and universities included) is in `totals.csv`.", "",
          "Sources. An article counts once under each state source it credits or cites, found three ways: a state "
          "origin signature it fired (`signatures_fired`, mapped by signature id, for example `xinhua_slash_credit` to "
          "Xinhua and the diplomat and embassy signatures to embassy; distribution stamp and sponsored signatures that "
          "name a state entity count as other); a source the model listed (`china_sources_cited`), read with the "
          "tracker's own citation patterns from `pipeline/signatures.yaml` (`cites_xinhua`, `cites_cgtn`, "
          "`cites_global_times`, `cites_china_daily`, `cites_peoples_daily`, `cites_cctv`, `mofa_spokesperson`, "
          "`chinese_embassy_quoted` and their `_more` forms) and a short list of patterns for names of ministries, "
          "officials and other state bodies, Hong Kong and Macao government bodies included as the codebook counts "
          "them; and a state outlet name among the gate terms. China Watch counts as China Daily; China Media "
          "Group, China News Service and China Radio International as other. Other counts an article whose state source is none of the eight named ones; an article naming several named sources counts once under each, so the rows do not add up to the coverage. The via columns say which of the three ways found it (an article can be found more than one way). Rules labels of B or C carry no source "
          "list, so for them only the gate terms can show a source; the counts are a lower bound there.", "",
          "Routes of tier 1: agency syndication (route `wire_credit`), paid release (`distribution_stamp`), sponsored "
          "placement (`sponsored_disclosure`, rules only), signed column (`diplomatic_byline`), other (uncredited "
          "copy, and rules labels from before routes were stored).", "",
          theme_method_md(ctx), "",
          "## Data quality", "",
          "Removed by the relevance gate: the gate's rejections are pruned from the database, so the count is the "
          "items discovered in the feeds (the daily files' `disc`, summed) less the gate relevant rows in the audit "
          "files; it is an estimate. Removed after the gate: placements whose current label is not about China (N). "
          "Languages: each placement language is checked against the gate's own keyword lists, the signature "
          "language patterns and the theme terms; the four countries publish in %s here, all with their own gate and "
          "theme terms. Chinese language outlets are registered but never read, by project rule (`excluded_languages` "
          "in `latest.json`). Feeds: the committed data holds each feed's state at the export only (last success, "
          "consecutive failures, frozen), not its history, so a feed that failed for more than 7 days and then "
          "recovered cannot be shown. What can be shown is in `outlet_feed_health.csv`: feeds failing for 7 days or "
          "more at the export, and outlets deactivated in the window with the date and reason from "
          "`sources/outlets.yaml`, including the automatic deactivations after every feed failed for more than 7 "
          "days and the outlets collected from the owner's network until 22 October 2026." % ctx["languages_text"], "",
          "## Limitations", "",
          "1. The window is %s days, not 1 January 2025 onward: nothing was collected before %s." % (
              ctx["days"], words_day(ctx["first_discovered"])),
          "2. An uncredited copy of an undetermined item is undetermined on the site (`model_only.route_sql`, "
          "`copy_of_undetermined`), but the audit files carry only the stored route, not the lookalike reference, so "
          "it cannot be detected from them row by row. The script recovers it from the site's own daily state origin "
          "count: on each day where tier 1 exceeds the daily files' A by exactly the number of model uncredited "
          "copies, those copies are counted as undetermined. Per country (copies moved to undetermined, kept in tier "
          "1b, unresolved, of all model uncredited copies): %s." % ctx["copy_text"],
          "3. Tier 2 is empty: no hand coding exists. Nothing in tier 1b or tier 3 has been checked by hand.",
          "4. Underlying items use the title groups only; the tracker also joins items through body text links and "
          "language editions, which the audit files do not carry, so item counts here can be higher than the site's.",
          "5. Themes are rule based and unvalidated; the mapping to the fixed list and the priority order are choices "
          "stated above, and the keyword rules read only the headline and gate terms.",
          "6. Unread articles (paywalled, failed, blocked) are missing from every count; in Canada they are a large "
          "share of what the gate admitted (see `data_quality.csv` and the site warnings there).",
          "7. Labels move between exports (relabelling, the model queue clearing); every figure here is from the one "
          "export stamped above. Rerun the script after the next export.",
          "8. A licensed archive check exists in the repository (`sources/archive_checks.yaml`, ProQuest TDM Studio, "
          "aggregate counts only); it is a separate source with a separate method and is not merged into any table "
          "here.", ""]
    return "\n".join(s) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build(repo: Path, out: Optional[Path] = None, countries: Iterable[str] = COUNTRIES, overlay: Optional[Path] = None,
          only: Optional[str] = None) -> Dict:
    repo = Path(repo).resolve()
    countries = tuple(countries)
    model_only, placements = import_pipeline(repo)
    data = repo / "docs" / "data"
    meta = load_json(data / "meta.json")
    latest = load_json(data / "latest.json")
    outlets_json = (load_json(data / "outlets.json") or {}).get("outlets") or []
    daily = {p.stem: load_json(p) for p in sorted((data / "daily").glob("*.json"))}
    outlets_yaml = load_outlets_yaml(repo)
    names = {o["id"]: o.get("name") or o["id"] for o in outlets_yaml}
    names.update({o["id"]: o.get("name") or o["id"] for o in outlets_json if o.get("id") not in names})
    wire_ids = {o["id"] for o in outlets_yaml if o.get("tier") == "distribution_wire"}
    widget_names = {p: w.get("id") for p, w in (getattr(placements, "SHARED_WIDGETS", {}) or {}).items()}
    for p, w in (getattr(placements, "SHARED_WIDGETS", {}) or {}).items():
        names[w["id"]] = "%s (shared widget of %s)" % (w["name"], w.get("network") or "a network")
    rows = read_audit(repo, countries)
    ov = apply_overlay(rows, Path(overlay)) if overlay else None
    derive(rows, model_only, placements, wire_ids, SourceReader(load_signatures(repo)))
    reconcile = reconcile_copies(rows, daily, countries)

    days = sorted({r["_day"] for r in rows if r["_day"]})
    first_discovered = meta.get("first_discovered") or (days[0] if days else None)
    first = max(OWNER_WINDOW_START, first_discovered or OWNER_WINDOW_START)
    gen_at = meta.get("generated_at") or ""
    last = max(days[-1] if days else first, gen_at[:10] if gen_at else first)
    if out is None:
        out = Path.home() / "Desktop" / ("tracker-country-export-%s" % (gen_at[:10] or dt.date.today().isoformat()))
    out = Path(out)
    cov_rows = [r for c in countries for r in placements_of(rows, c) if r["_code"] in COVERAGE]
    ctx = {"countries": countries, "first": first, "last": last, "first_discovered": first_discovered,
           "generated_at": gen_at, "schema_version": meta.get("schema_version"), "overlay": ov, "reconcile": reconcile,
           "themes_version": meta.get("themes_version"),
           "tag_body_text": "%s of %s" % (fmt(sum(r["_tag_body"] for r in cov_rows)), fmt(len(cov_rows))),
           "days": (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days + 1}

    themes, top5, comparison = themes_tables(rows, countries)
    files = []
    files.append(write_csv(out, "themes.csv", list(themes[0].keys()) if themes else ["country"], themes))
    top_header = ["country", "list", "rank", "theme", "china_coverage", "coverage_denominator", "share_of_coverage_pct",
                  "tier1", "tier1_share_of_theme_pct", "tier1_share_denominator", "themes_eligible", "note"]
    files.append(write_csv(out, "themes_top5.csv", top_header, top5))
    files.append(write_csv(out, "theme_comparison.csv", list(comparison[0].keys()) if comparison else ["country"], comparison))
    mp = mapping_rows()
    files.append(write_csv(out, "theme_mapping.csv", list(mp[0].keys()), mp))
    tables = {"themes": themes, "top5": top5, "comparison": comparison}
    if only == "themes":
        (out / "THEMES_README.md").write_text(themes_readme(ctx, themes, top5), encoding="utf-8")
        files.append(out / "THEMES_README.md")
        return {"out": out, "files": files, "tables": tables, "ctx": ctx}

    totals, dedup, outl, srcs, routes, monthly, quality, feeds, models = [], [], [], [], [], [], [], [], []
    try:
        from pipeline import gate as _gate
        gate_langs = set(_gate.supported_languages())
    except Exception:  # noqa: BLE001
        gate_langs = set()
    try:
        from pipeline import themes as _themes
        theme_langs = set(_themes.languages())
    except Exception:  # noqa: BLE001
        theme_langs = set(meta.get("theme_languages") or [])
    sig = load_signatures(repo)
    sig_langs = {t["id"].rsplit("_", 1)[-1] for t in sig.get("llm_triggers") or [] if t["id"].startswith("state_media_reported_")}
    lang_support = {"gate": gate_langs | {"en"}, "themes": theme_langs | {"en"}, "signatures": sig_langs}
    for c in countries:
        totals += totals_table(rows, c, latest, ctx["reconcile"].get(c))
        dedup += dedup_table(rows, c)
        outl += outlets_table(rows, c, names, widget_names)
        srcs += sources_table(rows, c)
        routes += routes_table(rows, c)
        monthly += monthly_table(rows, c, first, last)
        q, f = quality_tables(rows, c, latest, daily, outlets_json, outlets_yaml, first, gen_at, lang_support)
        quality += q
        feeds += f
        models += models_table(rows, c)
    tables.update({"totals": totals, "dedup": dedup, "outlets": outl, "sources": srcs, "routes": routes,
                   "monthly": monthly, "quality": quality, "feeds": feeds, "models": models})
    for name, rs, hdr in (
            ("totals.csv", totals, ["country", "measure", "count", "denominator_name", "denominator", "share_pct", "note"]),
            ("dedup.csv", dedup, None), ("outlets.csv", outl, None),
            ("sources.csv", srcs, ["country", "source", "articles_china_coverage", "coverage_denominator",
                                   "share_of_coverage_pct", "tier1", "tier1a_rules", "tier1b_model_evidence", "B", "C",
                                   "tier3_undetermined_not_in_totals", "via_signatures", "via_model_source_list",
                                   "via_gate_terms", "note"]),
            ("routes.csv", routes, ["country", "route_group", "stored_route", "tier1", "tier1_denominator",
                                    "share_of_tier1_pct", "tier1a_rules", "tier1b_model_evidence"]),
            ("monthly.csv", monthly, ["country", "month", "collected", "first_day", "last_day", "china_coverage", "tier1",
                                      "tier1a_rules", "tier1b_model_evidence", "tier1_share_pct", "tier1_share_denominator",
                                      "tier2_provisional", "tier3_undetermined_not_in_totals", "B", "C"]),
            ("data_quality.csv", quality, ["country", "measure", "count", "basis", "denominator_name", "denominator",
                                           "share_pct", "note"]),
            ("outlet_feed_health.csv", feeds, None), ("model_versions.csv", models, None)):
        files.append(write_csv(out, name, hdr or (list(rs[0].keys()) if rs else ["country"]), rs))

    in_scope = [r for c in countries for r in placements_of(rows, c) if r["_code"] is not None]
    ctx.update({
        "ruleset_version": meta.get("ruleset_version"), "gate_version": meta.get("gate_version"),
        "llm_model": meta.get("llm_model"), "prompt_version": meta.get("llm_prompt_version"),
        "articles_reviewed": meta.get("articles_reviewed"),
        "ruleset_mix": ", ".join("`%s` (%s)" % (k, fmt(v)) for k, v in sorted(Counter(
            r.get("ruleset_version") or "none" for r in in_scope).items())),
        "model_mix": ", ".join("`%s` (%s)" % (k, fmt(v)) for k, v in sorted(Counter(
            r.get("model_version") for r in in_scope if r.get("method") != "rules").items())) or "none",
        "languages_text": ", ".join(sorted({r.get("language") or "und" for r in in_scope})),
    })
    copy_bits = []
    for c in countries:
        st = ctx["reconcile"].get(c) or {}
        copy_bits.append("%s %s moved, %s kept, %s unresolved, of %s" % (
            COUNTRY_NAMES.get(c, c), fmt(st.get("moved_to_undetermined", 0)), fmt(st.get("kept_settled", 0)),
            fmt(st.get("unresolved", 0)), fmt(st.get("copies", 0))))
    ctx["copy_text"] = "; ".join(copy_bits) or "not available"
    (out / "README.md").write_text(full_readme(ctx, tables), encoding="utf-8")
    files.append(out / "README.md")
    return {"out": out, "files": files, "tables": tables, "ctx": ctx}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Aggregate country export (counts only) for a paper")
    ap.add_argument("--repo", default=str(SCRIPT_ROOT), help="repository checkout to read (default this one)")
    ap.add_argument("--out", default=None, help="output directory (default ~/Desktop/tracker-country-export-<export date>)")
    ap.add_argument("--countries", default=",".join(COUNTRIES))
    ap.add_argument("--overlay", default=None, help="session label file (JSON lines) not yet ingested by the runner")
    ap.add_argument("--only", choices=["themes"], default=None)
    args = ap.parse_args(argv)
    res = build(Path(args.repo), Path(args.out).expanduser() if args.out else None,
                [c.strip().upper() for c in args.countries.split(",") if c.strip()],
                Path(args.overlay) if args.overlay else None, args.only)
    print("wrote %d files to %s" % (len(res["files"]), res["out"]))


if __name__ == "__main__":
    main(sys.argv[1:])
