"""State issuers of paid releases: the registry (sources/state_issuers.yaml), the reading of a release for
its own issuer, and the two groups held apart from state origin (owner decisions of 2026-11-24).

A paid release is an item a release wire carries (its dateline stamp, a wire named in the page labels or
the author field, an uppercase SOURCE trailer) or one from an outlet's press release, sponsored or partner
section. Its own issuer is the body named in one of five places, each recorded as its own signature id:

  state_issuer_header          the headline's "Issuer:" prefix, or a line that is the issuer's name alone
                               among the first three or the last six lines
  state_issuer_dateline        the dateline line, right after the wire stamp's dash ("BEIJING, Oct. 3, 2026
                               /PRNewswire/ -- The Publicity Department of ...")
  state_issuer_source_trailer  a SOURCE (or Source:) line among the last six lines that is the issuer alone
  state_issuer_issued_by       an "Issued by", "Presented by" or "Sponsored by" line among the first three or
                               the last eight lines
  state_issuer_media_contact   the media contact block in the last 1,200 characters: the issuer's name, or
                               its web or mail domain

A body named anywhere else, in a sentence of the release or in a news story, is never read as the issuer.
The registry groups decide the label:

  A  party-state issuers: state origin, a rules label like any strong signature.
  S  state-owned enterprises and state-run institutions (decision D13): stored as a rules label with
     issuer_type state_owned_enterprise, counted under code S, in no state origin, unchecked sourcing or
     independent count and in no China coverage denominator, shown on its own line.
  H  Hong Kong and Macao SAR government and statutory bodies (decision D14): the same, issuer_type
     sar_government, code H.

A model state origin label whose issuer_type is state_owned_enterprise or sar_government is read under S or
H the same way (pipeline/model_only.py: route_sql, headline_category); the stored label never changes.

A release carrying the shared Beijing contact number 86-10-63074558 (decision D15) and naming no registry
issuer in any of the five places is a state origin candidate for the model: the hint beijing_release_contact
is recorded in its llm_trigger. The number alone never makes a label.
"""
import datetime as dt
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from pipeline import config

PATH = config.ROOT / "sources" / "state_issuers.yaml"
SCHEMA_PATH = config.ROOT / "sources" / "state_issuers_schema.json"
DECIDED_ON = "2026-11-24"

S = "S"
H = "H"
CODES = (S, H)
# The stored issuer type of each group held apart, and back.
CODE_OF_ISSUER_TYPE = {"state_owned_enterprise": S, "sar_government": H}
ISSUER_TYPE_OF_CODE = {v: k for k, v in CODE_OF_ISSUER_TYPE.items()}
# The route model_only.route_sql reads for a label of each group. Never stored: the stored route stays.
READ_ROUTES = {S: "state_owned_issuer", H: "sar_issuer"}
CODE_OF_READ_ROUTE = {v: k for k, v in READ_ROUTES.items()}
LABELS = {S: "Releases of Chinese state-owned enterprises and state-run institutions",
          H: "Releases of Hong Kong and Macao SAR government bodies"}
SHORT_LABELS = {S: "State-owned issuers", H: "SAR government issuers"}

POSITIONS = ("header", "dateline", "source_trailer", "issued_by", "media_contact")
SIGNATURE_IDS = {p: "state_issuer_" + p for p in POSITIONS}
CONTACT_ID = "beijing_release_contact"
GROUP_RANK = {"A": 0, S: 1, H: 2}

# The shared Beijing contact number of the placement agency the paper found on local government releases,
# in its common forms: 86-10-63074558, +86 10 6307 4558, (86)10-63074558, 8610-63074558, 010-63074558.
CONTACT_RE = re.compile(r"(?<![\d+])(?:\+?\s?\(?\s?(?:00)?86\s?\)?[\s.\-]*\(?0?\)?[\s.\-]*|\(?0)?\(?10\)?[\s.\-]*6307[\s.\-]?4558(?!\d)")

_WIRES = (r"Globe ?Newswire|PR ?Newswire|PRNewswire|Business ?Wire|ACN Newswire|JCN Newswire|Cision|Newsfile|"
          r"ACCESS ?Newswire|Accesswire|EQS[- ]News|Media OutReach|Immediapress|PRN Asia|Kyodo News PR Wire")
WIRE_RE = re.compile(r"(?i)(?<![\w])(?:%s)(?![\w])" % _WIRES)
# The dateline line of a wire release, up to and including the dash after the stamp.
_STAMP_LINE_RE = re.compile(r"(?im)^([^\n]{0,200}?)(?<![\w])(?:%s)(?![\w])[^\n]{0,30}?(?:--|—|–|-)[ \t]*" % _WIRES)
# A dateline with no wire stamp (an item of a release or sponsored section): a capitalised place, a year,
# then a double dash or a long dash.
_PLAIN_DATELINE_RE = re.compile(r"(?m)^([ \t]*[A-Z][A-Za-z .,'’()]{2,80}?\b(?:19|20)\d\d\b[^\n]{0,40}?)(?:--|—|–)[ \t]*")
# A page label line or author field that names a release wire as the item's source.
_LABEL_WIRE_RE = re.compile(r"(?im)^(?:creator|articleSection|author|source|publisher)\s*:\s*[^\n]*(?:%s|globe-newswire|pr-newswire|business-wire)"
                            r"|Partnership Content supplied by|Author of the article:\s*(?:%s)" % (_WIRES, _WIRES))
_SOURCE_UPPER_RE = re.compile(r"(?m)^[ \t]*SOURCE[ \t]+\S")
_SOURCE_LINE_RE = re.compile(r"^\s*\(?\s*(?:SOURCE|Source|Fonte|Fuente|Quelle|Origine)\s*[:：]?\s+(?P<rest>.+?)\s*\)?\s*$")
_ISSUED_BY_RE = re.compile(
    r"(?i)^\s*(?:(?:This|The)\s+(?:press\s+|news\s+)?(?:release|article|content|announcement|story|feature)\s+(?:is|was|has\s+been)\s+)?"
    r"(?:issued|released|distributed|presented|sponsored|paid\s+for|brought\s+to\s+you)\s+by\s*[:：]?\s*|^\s*Issuer\s*[:：]\s*")
_CONTACT_HEAD_RE = re.compile(
    r"(?im)^[ \t*•>_\-]*(?:(?:Media|Press|News|Public)[ \t]+)?(?:Contacts?|Contact[ \t]+(?:Person|Information|Details)|"
    r"Inquir(?:y|ies)|Enquir(?:y|ies)|Relations)\b|^[ \t]*For[ \t]+(?:more|further|media)[ \t]+(?:information|inquiries|enquiries)\b")
_LEAD_RE = re.compile(r"^(?:(?:On|In)\s[^,\n]{1,40},\s+)?(?:(?:A|The)\s+(?:news\s+)?report\s+(?:from|by)\s+)?(?:[Tt]he\s+)?")
_ACRONYM_TAIL_RE = re.compile(r"\s*[(（][A-Za-z][A-Za-z0-9&.\- ]{0,14}[)）]\s*$")
_LINE_PUNCT = " \t .,;:|*•>_-–—"
HEAD_LINES = 3
TAIL_LINES = 6
ISSUED_BY_TAIL_LINES = 8
CONTACT_CHARS = 1200
CONTACT_BLOCK_LINES = 6
HEAD_CHARS = 600


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

def _plain(x):
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_plain(v) for v in x]
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()[:10]
    return x


def load_file(path: Optional[Path] = None) -> Dict:
    with open(path or PATH, encoding="utf-8") as fh:
        return _plain(yaml.safe_load(fh) or {})


def _schema() -> Dict:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _name_rx(name: str) -> str:
    e = re.escape(name).replace("'", "['’]").replace("\\ ", "[  ]+")
    return e


def _alternation(words: List[str]) -> str:
    return "(?:%s)" % "|".join(_name_rx(w) for w in sorted(set(words), key=len, reverse=True))


_NAME = r"[A-Z][a-z]+(?:['’][a-z]+)?(?:[ \-][A-Z][a-z]+(?:['’][a-z]+)?){0,2}"


def _expand(pattern: str, places: Dict) -> str:
    out = pattern.replace("{place}", _alternation(places["provinces"] + places["cities"]))
    out = out.replace("{province}", _alternation(places["provinces"]))
    return out.replace("{name}", _NAME)


def validate(data) -> List[str]:
    """Schema errors first; then ids and names listed twice, groups and issuer types that disagree, and
    patterns that do not compile or use {name} without a term only the Chinese state uses."""
    import jsonschema
    errs = ["%s: %s" % ("/".join(str(p) for p in e.absolute_path) or "(top)", e.message)
            for e in jsonschema.Draft202012Validator(_schema()).iter_errors(data)]
    if errs:
        return errs
    ids, names = set(), {}
    for e in data["issuers"]:
        if e["id"] in ids:
            errs.append("%s: id listed twice" % e["id"])
        ids.add(e["id"])
        want = {S: ("state_owned_enterprise",), H: ("sar_government",),
                "A": ("state_media", "government", "party", "diplomatic")}[e["group"]]
        if e["issuer_type"] not in want:
            errs.append("%s: group %s takes issuer type %s, not %s" % (e["id"], e["group"], " or ".join(want), e["issuer_type"]))
        if e["group"] == S and e["kind"] not in ("state_owned_enterprise", "state_institution"):
            errs.append("%s: group S is for state-owned enterprises and state-run institutions" % e["id"])
        if e["group"] == H and e["kind"] != "sar_body":
            errs.append("%s: group H is for SAR government and statutory bodies" % e["id"])
        for n in e.get("names") or []:
            if n in names:
                errs.append("%s: name %r is also listed under %s" % (e["id"], n, names[n]))
            names[n] = e["id"]
        for p in e.get("patterns") or []:
            if "{name}" in p and not re.search(r"People|CPC|CCP|Communist Party", p):
                errs.append("%s: pattern %r uses {name} without a term only the Chinese state uses" % (e["id"], p))
            try:
                re.compile(_expand(p, data["places"]))
            except re.error as exc:
                errs.append("%s: pattern %r does not compile (%s)" % (e["id"], p, exc))
        for t in ("source_title", "basis"):
            if any(d in e.get(t, "") for d in ("–", "—")):
                errs.append("%s: %s has an en or em dash" % (e["id"], t))
    for group in ("Hong Kong", "Macao", "Macau", "Taiwan", "Taipei"):
        if group in data["places"]["provinces"] + data["places"]["cities"]:
            errs.append("places: %s is not a mainland place" % group)
    return errs


def validate_file(path: Optional[Path] = None) -> List[str]:
    try:
        data = load_file(path)
    except (OSError, yaml.YAMLError) as exc:
        return ["cannot read the registry: %s" % exc]
    return validate(data)


@lru_cache(maxsize=None)
def registry(path: str = str(PATH)) -> List[Dict]:
    """The compiled entries in registry order: each with rx (names and patterns in one regex, matched as
    written), domain_rx (or None) and its fields."""
    data = load_file(Path(path))
    errs = validate(data)
    if errs:
        raise ValueError("sources/state_issuers.yaml: %s" % "; ".join(errs[:5]))
    out = []
    for e in data["issuers"]:
        alts = []
        for n in e.get("names") or []:
            alts.append(_name_rx(n))
            if n.upper() != n:
                alts.append(_name_rx(n.upper()))
        alts += [_expand(p, data["places"]) for p in e.get("patterns") or []]
        rx = re.compile(r"(?<![\w&])(?:%s)(?![\w&])" % "|".join("(?:%s)" % a for a in alts))
        doms = e.get("domains") or []
        drx = re.compile(r"(?i)(?<![\w-])(?:[\w-]+\.)*(?:%s)(?![\w-])" % "|".join(re.escape(d) for d in doms)) if doms else None
        places = data["places"]["provinces"] + data["places"]["cities"]
        out.append(dict(e, rx=rx, domain_rx=drx, place_rx=re.compile(r"(?i)\b(?:China|%s)\b" % "|".join(
            _name_rx(p) for p in places))))
    return out


def entry(issuer_id: str) -> Optional[Dict]:
    return next((e for e in registry() if e["id"] == issuer_id), None)


# ---------------------------------------------------------------------------
# Reading a release
# ---------------------------------------------------------------------------

def _hit(e: Dict, position: str, name: str, span: str) -> Dict:
    named = any(re.fullmatch(_name_rx(n), name) or re.fullmatch(_name_rx(n.upper()), name) for n in e.get("names") or [])
    return {"issuer": e["id"], "group": e["group"], "issuer_type": e["issuer_type"], "kind": e["kind"],
            "position": position, "name": name, "named": named, "span": span[:200]}


def _china_dateline(e: Dict, dateline: Optional[str]) -> bool:
    return not e.get("china_dateline") or bool(dateline and e["place_rx"].search(dateline))


def _at_start(text: str, position: str, span: str, dateline: Optional[str]) -> List[Dict]:
    """Registry issuers whose name opens text (after a lead such as "The" or "A news report from")."""
    lead = _LEAD_RE.match(text).end()
    out = []
    for e in registry():
        m = e["rx"].match(text, lead)
        if m and _china_dateline(e, dateline):
            out.append(_hit(e, position, m.group(0), span))
    return out


# A line that lists several issuers ("Publicity Department of the CPC Dingzhou Municipal Committee, Dingzhou
# Municipal Culture and Tourism Bureau and Dingzhou Converged Media Center") is read as a list of names: every
# part made of capitalised words and connectives only, never a sentence.
_LIST_SPLIT_RE = re.compile(r"\s*(?:[,;/\uff0c]|\band\b|&)\s*")
_CONNECTIVES = {"of", "and", "the", "for", "in", "on", "de", "du", "da", "di"}


def _name_like(part: str) -> bool:
    words = part.split()
    return bool(words) and len(part) <= 100 and all(
        w[:1].isupper() or w[:1].isdigit() or w.lower() in _CONNECTIVES or w[:1] in "('\u2019" for w in words)


def _whole(line: str, position: str, dateline: Optional[str]) -> List[Dict]:
    """Registry issuers that are the whole line, after "The" and before an acronym in brackets, or the
    first of a list of names that makes up the whole line."""
    core = _ACRONYM_TAIL_RE.sub("", line.strip().strip(_LINE_PUNCT)).strip(_LINE_PUNCT)
    core = re.sub(r"^(?:[Tt]he|From|By)\s+", "", core)
    out = []
    if not core or len(core) > 300:
        return out
    parts = [p for p in _LIST_SPLIT_RE.split(core) if p]
    listed = len(parts) > 1 and all(_name_like(_ACRONYM_TAIL_RE.sub("", p)) for p in parts)
    for e in registry():
        m = e["rx"].fullmatch(core) if len(core) <= 140 else None
        if not m and listed:
            # the first name of the list, which may itself hold a comma ("Bureau of ..., Gangbei District")
            m = e["rx"].match(core)
            if m and not _LIST_SPLIT_RE.match(core, m.end()) and m.end() != len(core):
                m = None
        if m and _china_dateline(e, dateline):
            out.append(_hit(e, position, m.group(0), line.strip()))
    return out


def _lines(text: str) -> List[str]:
    return [ln for ln in (text or "").splitlines() if ln.strip()]


def release_kind(title: str, body: str, author: str, labels: str, arrival: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(kind, dateline) of a paid release, or (None, None): kind is "wire" (a wire stamp in the dateline
    line, a wire named as the item's source in the page labels or author field, or an uppercase SOURCE
    trailer) or the section it arrived from ("press_release_section", "sponsored_section",
    "partner_section"). dateline is the text of the dateline line before the stamp, when there is one."""
    head = (body or "")[:HEAD_CHARS]
    m = _STAMP_LINE_RE.search(head)
    dateline = m.group(1) if m else None
    if not dateline:
        p = _PLAIN_DATELINE_RE.search(head)
        dateline = p.group(1) if p else None
    tail_lines = _lines(body)[-TAIL_LINES:]
    if m or WIRE_RE.search(author or "") or _LABEL_WIRE_RE.search(labels or "") \
            or any(_SOURCE_UPPER_RE.match(ln) for ln in tail_lines):
        return "wire", dateline
    if arrival and arrival != "editorial_feed":
        return arrival, dateline
    return None, None


def read_release(title: str, body: str, author: str = "", labels: str = "", arrival: Optional[str] = None) -> Dict:
    """What a paid release says about its own issuer: {"release": kind or None, "signature_group":
    distribution_stamp or sponsored_placement, "issuers": [hits in every position], "issuer": the hit that
    decides (A before S before H, then by position), "contact": the span of the shared Beijing contact
    number or None}. Nothing is read from an item that is not a paid release."""
    out = {"release": None, "signature_group": None, "issuers": [], "issuer": None, "contact": None}
    body = body or ""
    kind, dateline = release_kind(title, body, author, labels, arrival)
    if not kind:
        return out
    out["release"] = kind
    out["signature_group"] = "sponsored_placement" if kind in ("sponsored_section", "partner_section") else "distribution_stamp"
    hits: List[Dict] = []
    # header: the headline's "Issuer:" prefix, or a line of the issuer's name alone at the top or foot
    t = (title or "").strip()
    for h in _at_start(t, "header", t, dateline):
        rest = t[_LEAD_RE.match(t).end() + len(h["name"]):]
        if re.match(r"\s*(?:\([^)]{1,15}\)\s*)?[:：|]", rest):
            hits.append(h)
    lines = _lines(body)
    for ln in lines[:HEAD_LINES] + lines[-TAIL_LINES:]:
        hits += _whole(ln, "header", dateline)
    # dateline: the issuer opens the text after the wire stamp's dash
    head = body[:HEAD_CHARS]
    for rx in (_STAMP_LINE_RE, _PLAIN_DATELINE_RE):
        m = rx.search(head)
        if m:
            line_end = head.find("\n", m.end())
            hits += _at_start(head[m.end():], "dateline", head[m.start():len(head) if line_end < 0 else line_end], dateline)
            break
    # SOURCE trailer
    for ln in lines[-TAIL_LINES:]:
        m = _SOURCE_LINE_RE.match(ln)
        if m:
            for h in _whole(m.group("rest"), "source_trailer", dateline):
                h["span"] = ln.strip()[:200]
                hits.append(h)
    # issued by, presented by, sponsored by
    for ln in lines[:HEAD_LINES] + lines[-ISSUED_BY_TAIL_LINES:]:
        m = _ISSUED_BY_RE.match(ln)
        if m and len(ln.strip()) <= 160:
            for h in _at_start(ln[m.end():], "issued_by", ln.strip(), dateline):
                hits.append(h)
    # the media contact block
    tail = body[-CONTACT_CHARS:]
    for m in _CONTACT_HEAD_RE.finditer(tail):
        block = "\n".join(tail[m.start():].split("\n")[:CONTACT_BLOCK_LINES + 1])
        for e in registry():
            n = e["rx"].search(block)
            d = e["domain_rx"].search(block) if e["domain_rx"] else None
            if (n or d) and _china_dateline(e, dateline):
                hits.append(_hit(e, "media_contact", (n or d).group(0), block.strip().replace("\n", " / ")))
    seen = set()
    uniq = []
    for h in hits:
        k = (h["issuer"], h["position"])
        if k not in seen:
            seen.add(k)
            uniq.append(h)
    out["issuers"] = uniq
    if uniq:
        # A party-state issuer before a state-owned one before a SAR body; then by place; a registry entry
        # that lists the name before a generic name form.
        out["issuer"] = sorted(uniq, key=lambda h: (GROUP_RANK[h["group"]], POSITIONS.index(h["position"]),
                                                    not h["named"], -len(h["name"])))[0]
    c = CONTACT_RE.search(body)
    if c:
        out["contact"] = body[max(0, c.start() - 60):c.end() + 20].strip().replace("\n", " / ")
    return out


# ---------------------------------------------------------------------------
# The groups held apart: counting, the meta block, the findings and the method text
# ---------------------------------------------------------------------------

def code_of(category, route=None, issuer_type=None) -> Optional[str]:
    """S or H for a current state origin label of a group held apart (its issuer type, or the route
    model_only.route_sql reads for it), else None."""
    if category != "A":
        return None
    return CODE_OF_READ_ROUTE.get(route or "") or CODE_OF_ISSUER_TYPE.get(issuer_type or "")


def separate_sql(alias: str = "c", code: Optional[str] = None) -> str:
    """The SQL condition true for a label of a group held apart (S, H, or both when code is None)."""
    types = [ISSUER_TYPE_OF_CODE[code]] if code else [ISSUER_TYPE_OF_CODE[S], ISSUER_TYPE_OF_CODE[H]]
    return "(%s.category = 'A' AND COALESCE(%s.issuer_type, '') IN (%s))" % (
        alias, alias, ", ".join("'%s'" % t for t in types))


def _pool(conn) -> List[Dict]:
    from pipeline import placements
    shadow = placements.shadowed(conn)
    rows = conn.execute(
        """SELECT a.id, a.discovered_at, a.day_of_record, c.issuer_type, c.method
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 AND %s ORDER BY a.id""" % separate_sql("c"))
    return [dict(r) for r in rows if r["id"] not in shadow]


def _in(r: Dict, start: Optional[str], end: Optional[str]) -> bool:
    d = r["day_of_record"] or (r["discovered_at"] or "")[:10]
    return not ((start and d < start) or (end and d >= end))


def meta_block(conn, start30: Optional[str] = None, end30: Optional[str] = None) -> Dict:
    """meta.separate_issuers: per group, the placements all time and over the 30 complete days the map opens
    on, by method (rules or model), with the plain label and the rule."""
    pool = _pool(conn)
    groups = {}
    for code in CODES:
        rows = [r for r in pool if CODE_OF_ISSUER_TYPE.get(r["issuer_type"]) == code]
        groups[code] = {"count": len(rows),
                        "count_last_30d": sum(1 for r in rows if _in(r, start30, end30)) if start30 else None,
                        "by_method": {"rules": sum(1 for r in rows if r["method"] == "rules"),
                                      "model": sum(1 for r in rows if r["method"] != "rules")},
                        "label": LABELS[code], "short_label": SHORT_LABELS[code], "code": code,
                        "issuer_type": ISSUER_TYPE_OF_CODE[code]}
    return {"groups": groups, "decided_on": DECIDED_ON, "excluded_from_counts": True, "rejoins_state_origin": False,
            "window_start": start30,
            "window_end": (dt.date.fromisoformat(end30) - dt.timedelta(days=1)).isoformat() if end30 else None,
            "registry": "sources/state_issuers.yaml"}


def findings_block(conn, tot: Dict, wld_all: Dict, start30: str, until30: str) -> Dict:
    """findings.separate_issuers: each group's placements all time and in the 30 complete days of last_30d,
    each with its worldwide wires part, from the daily_counts rows (codes S and H) the daily files carry."""
    from pipeline import registry as reg
    out = {"excluded_from_counts": True, "decided_on": DECIDED_ON}
    for code in CODES:
        n30 = {r[0]: r[1] or 0 for r in conn.execute(
            "SELECT country, SUM(n) FROM daily_counts WHERE scope='all' AND category=? AND date>=? AND date<? GROUP BY country",
            (code, start30, until30))}
        out[code] = {"all_time": tot.get(code, 0), "all_time_wires": wld_all.get(code, 0),
                     "last_30d": sum(n30.values()), "last_30d_wires": n30.get(reg.WORLDWIDE, 0), "label": LABELS[code]}
    return out


def methodology_text(block: Optional[Dict]) -> str:
    """The paragraph for METHODOLOGY.md and the site's method section, from meta.separate_issuers."""
    b = block or {}
    g = b.get("groups") or {}
    lead = ("Releases of state-owned enterprises and of the Hong Kong and Macao governments. On 2026-11-24 the "
            "owner decided that paid releases issued by Chinese state-owned enterprises and state-run institutions "
            "(state universities, state laboratories), and those issued by the Hong Kong SAR government and its "
            "statutory bodies (such as the Hong Kong Tourism Board), with Macao SAR bodies under the same rule, are "
            "held apart from state origin. Each is stored with its evidence, kept in the audit files and shown on a "
            "line of its own; neither is in the state origin, unchecked sourcing or independent journalism counts, "
            "nor in the China coverage total. The rules read a paid release for its own issuer, named in its "
            "headline or a line of its own, the dateline line after the wire stamp, the SOURCE line, an issued by "
            "line or the media contact block, never in a sentence of the text, against a registry of state issuers "
            "with a public source for each (sources/state_issuers.yaml): central state media, provincial and city "
            "international communication centres, party committees and their publicity departments, people's "
            "governments and their bureaus at every level and provincial party dailies are state origin; the "
            "state-owned and SAR issuers are held apart. A model call of state origin that names a state-owned "
            "enterprise or a SAR government body as the issuer is held apart the same way. A release that carries "
            "the Beijing contact number 86-10-63074558, shared by local government releases placed through one "
            "agency, and names no registered issuer, goes to the model as a state origin candidate; the number "
            "alone never makes a label.")
    if g:
        s, h = g.get(S) or {}, g.get(H) or {}
        lead += (" At export time %s placements were held apart as state-owned issuer releases and %s as SAR "
                 "government releases" % ("{:,}".format(s.get("count") or 0), "{:,}".format(h.get("count") or 0)))
        if s.get("count_last_30d") is not None and b.get("window_end"):
            lead += (", %s and %s of them in the 30 complete days ending %s" % (
                "{:,}".format(s.get("count_last_30d") or 0), "{:,}".format(h.get("count_last_30d") or 0), b["window_end"]))
        lead += "."
    return lead


def main(argv=None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["validate"] or not args:
        errs = validate_file()
        for e in errs:
            print(e)
        print("ok" if not errs else "%d errors" % len(errs))
        return 1 if errs else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
