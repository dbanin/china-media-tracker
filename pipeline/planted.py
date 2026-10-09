"""Planted contributions detector.

Flags articles in the monitored outlets whose byline, or whose cited organisation, matches a
persona or false front named in a published investigation (sources/personas.yaml), and computes a
diagnostic of bylines that recur across unrelated outlets.

What a flag is and is not. The tracker cannot prove a byline is fictitious. A flag means "the byline
matches a persona named in a published investigation, cited here". The recurring bylines table is a
diagnostic for human review, never a finding. Absence of a match proves nothing. No article flagged
here is ever folded into the existing categories (state origin, unchecked state sourcing,
independent journalism) and nothing here changes a category count.

Detectors (the detector field of a flag):
  author_field  the article's author field equals a persona name or variant after normalisation.
                Runs on every discovered item, including those the China gate rejected, because
                planted bylines are not only about China.
  byline_head   a persona name preceded by a byline word (by, por, par, von, di, de, door) within the
                first HEAD_CHARS characters of the body.
  front_cited   a front organisation's name, or one of its domains, appears anywhere in the title or
                body. A front with a one word name is never matched.

Names are normalised before comparison: casefold, diacritics stripped, punctuation dropped,
whitespace collapsed, and initial-less variants derived ("Ervin B. Hoskins" also matches "Ervin
Hoskins"). A single word never matches.

Usage:
  python -m pipeline.planted scan [--full]
  python -m pipeline.planted recurring [--days N]
"""
import argparse
import datetime as dt
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import yaml

from pipeline import config, store

HEAD_CHARS = 400
BYLINE_WORDS = ("by", "por", "par", "von", "di", "de", "door")
EVIDENCE_CONTEXT = 60
FLAGS_EXPORT_CAP = 500
RECURRING_MAX_ROWS = 50
RECURRING_MIN_OUTLETS = 3
RECURRING_MIN_COUNTRIES = 2
RECURRING_SAMPLE_TITLES = 3
DETECTORS = ("author_field", "byline_head", "front_cited")

# Generic bylines, after normalisation, that name a desk rather than a person. A byline equal to one
# of these phrases, or containing one of the stop tokens, is left out of the recurring diagnostic.
# Wire agencies whose name is printed as the byline sit here too: a wire name recurring across
# outlets is the wire working as intended, not a byline worth a reviewer's time.
BYLINE_STOPLIST = {
    "admin", "administrator", "staff", "staff reporter", "staff writer", "staff writers", "staff reports",
    "staff report", "editor", "editors", "editorial", "editorial board", "editorial staff", "redaccion",
    "la redaccion", "redaction", "la redaction", "redazione", "la redazione", "redactie", "de redactie",
    "redaktion", "die redaktion", "redakcja", "redakce", "newsdesk", "news desk", "newsroom", "web desk",
    "online desk", "digital desk", "agencies", "agencias", "agences", "agenzie", "agencia", "agency",
    "correspondent", "our correspondent", "special correspondent", "contributor", "guest contributor",
    "contributors", "press release", "sponsored content", "sponsored", "advertorial", "partner content",
    "associated press", "the associated press", "agence france presse", "agencia efe", "agencia afp",
    "europa press", "bloomberg news", "deutsche welle", "press trust of india", "united press international",
    "kyodo news", "yonhap news agency", "xinhua news agency", "the economist", "the conversation",
    "project syndicate", "the new york times", "the washington post", "the guardian", "voice of america",
    "radio free asia", "al jazeera", "the canadian press", "australian associated press",
}
BYLINE_STOP_TOKENS = {
    "admin", "staff", "editor", "editors", "editorial", "desk", "newsdesk", "newsroom", "redaccion", "redaction",
    "redazione", "redactie", "redaktion", "redakcja", "redakce", "agencies", "agencias", "agences", "agenzie",
    "agencia", "agency", "team", "bureau", "correspondent", "reporter", "reporters", "contributor",
    "contributors", "news", "press", "sponsored", "advertorial", "wire", "network", "media",
}


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalise(text: Optional[str]) -> str:
    """Casefold, strip diacritics, drop punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[^\w\s]|_", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_byline_word(name: str) -> str:
    """A normalised author field sometimes keeps its byline word ("by jane doe")."""
    toks = name.split()
    if len(toks) >= 3 and toks[0] in BYLINE_WORDS:
        return " ".join(toks[1:])
    return name


def name_variants(name: Optional[str], extra: Iterable[str] = ()) -> List[str]:
    """Every normalised form a name may take: the name, each explicit variant, and each of those
    without its middle initials. Forms shorter than two tokens are dropped, so a single word is never
    a variant and never matches."""
    out = []
    for raw in [name] + list(extra or []):
        n = normalise(raw)
        toks = n.split()
        if len(toks) < 2:
            continue
        forms = [toks, [t for i, t in enumerate(toks) if not (len(t) == 1 and 0 < i < len(toks) - 1)]]
        for f in forms:
            if len(f) >= 2 and " ".join(f) not in out:
                out.append(" ".join(f))
    return out


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------

def _empty_list() -> Dict:
    return {"version": "", "operations": [], "personas": [], "fronts": []}


def load_personas(path: Optional[Path] = None) -> Dict:
    """sources/personas.yaml compiled for matching. A missing or empty file loads as empty lists,
    never as an error: the detector is optional and must not stop a run. Each persona gains a
    `variants_n` list of normalised forms; each front gains `variants_n` and `domains_n`. Entries
    without a usable name (fewer than two tokens) are kept for the export but match nothing."""
    p = Path(path) if path is not None else config.PERSONAS_PATH
    data = None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        data = None
    if not isinstance(data, dict):
        return _empty_list()
    out = _empty_list()
    out["version"] = str(data.get("version") or "")
    for op in data.get("operations") or []:
        if isinstance(op, dict) and op.get("id"):
            out["operations"].append(dict(op, id=str(op["id"])))
    for ps in data.get("personas") or []:
        if not isinstance(ps, dict) or not ps.get("id"):
            continue
        ps = dict(ps, id=str(ps["id"]))
        ps["variants_n"] = name_variants(ps.get("name"), ps.get("variants") or [])
        out["personas"].append(ps)
    for fr in data.get("fronts") or []:
        if not isinstance(fr, dict) or not fr.get("id"):
            continue
        fr = dict(fr, id=str(fr["id"]))
        fr["variants_n"] = name_variants(fr.get("name"), fr.get("variants") or [])
        fr["domains_n"] = sorted({str(d).strip().casefold() for d in (fr.get("domains") or []) if str(d).strip()})
        out["fronts"].append(fr)
    return out


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

_SEGMENT_RE = re.compile(r"\s*(?:,|;|\||/|&|\band\b|\by\b|\be\b|\bet\b|\bund\b|\ben\b)\s*")


def _author_segments(author: str) -> List[str]:
    """The normalised author field as a whole and each name in it when it lists several."""
    n = strip_byline_word(normalise(author))
    if not n:
        return []
    segs = [n]
    for s in _SEGMENT_RE.split(n):
        s = strip_byline_word(s.strip())
        if s and s not in segs:
            segs.append(s)
    return segs


def match_author(author: Optional[str], personas: List[Dict]) -> List[Tuple[Dict, str]]:
    """Personas whose name or a variant equals the author field, or one name in a list of authors,
    after normalisation. Returns (persona, matched form)."""
    segs = _author_segments(author or "")
    if not segs:
        return []
    out = []
    for ps in personas:
        for v in ps.get("variants_n") or []:
            if v in segs:
                out.append((ps, v))
                break
    return out


def match_head(body: Optional[str], personas: List[Dict]) -> List[Tuple[Dict, str]]:
    """Personas whose name follows a byline word within the first HEAD_CHARS characters of the body.
    Returns (persona, evidence), the evidence being the normalised byline with its context."""
    head = normalise((body or "")[:HEAD_CHARS])
    if not head:
        return []
    out = []
    words = "|".join(BYLINE_WORDS)
    for ps in personas:
        for v in ps.get("variants_n") or []:
            m = re.search(r"(?:^|(?<=\s))(?:%s) %s(?=\s|$)" % (words, re.escape(v)), head)
            if m:
                out.append((ps, _context(head, m.start(), m.end())))
                break
    return out


def match_fronts(title: Optional[str], body: Optional[str], fronts: List[Dict]) -> List[Tuple[Dict, str]]:
    """Fronts whose name (two tokens or more) or one of whose domains appears anywhere in the title or
    body. Returns (front, evidence)."""
    raw = "%s\n%s" % (title or "", body or "")
    if not raw.strip():
        return []
    text = normalise(raw)
    low = raw.casefold()
    out = []
    for fr in fronts:
        hit = None
        for v in fr.get("variants_n") or []:
            m = re.search(r"(?:^|(?<=\s))%s(?=\s|$)" % re.escape(v), text)
            if m:
                hit = _context(text, m.start(), m.end())
                break
        if hit is None:
            for d in fr.get("domains_n") or []:
                m = re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(d), low)
                if m:
                    hit = _context(low, m.start(), m.end())
                    break
        if hit is not None:
            out.append((fr, hit))
    return out


def _context(text: str, start: int, end: int) -> str:
    a = max(0, start - EVIDENCE_CONTEXT)
    b = min(len(text), end + EVIDENCE_CONTEXT)
    snippet = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("..." if a > 0 else "") + snippet + ("..." if b < len(text) else "")


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def _state(conn: sqlite3.Connection) -> Dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM planted_state")}


def _set_state(conn: sqlite3.Connection, **values) -> None:
    for k, v in values.items():
        conn.execute("INSERT INTO planted_state(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (k, "" if v is None else str(v)))


def _flag(conn: sqlite3.Connection, article_id: int, detector: str, persona_id: str, front_id: str,
          operation_id: str, evidence: str, now: str, version: str) -> int:
    cur = conn.execute(
        """INSERT OR IGNORE INTO planted_flags(article_id, detector, persona_id, front_id, operation_id, evidence,
           flagged_at, list_version) VALUES (?,?,?,?,?,?,?,?)""",
        (article_id, detector, persona_id or "", front_id or "", operation_id or "", evidence, now, version))
    return cur.rowcount


def scan(conn: sqlite3.Connection, full: bool = False, personas: Optional[Dict] = None) -> Dict:
    """Flag articles against the persona list. Incremental by default: rows discovered since the
    last scanned id, plus rows fetched since the last scan so a body that arrived later still gets
    the body detectors. Everything is rescanned when the list version changed or full is True.
    Flags are never deleted; a rescan adds what is new and the UNIQUE constraint ignores the rest."""
    plist = personas if personas is not None else load_personas()
    version = plist["version"]
    state = _state(conn)
    rescan = full or state.get("list_version") != version
    last_id = 0 if rescan else int(state.get("last_scanned_id") or 0)
    last_fetched = "" if rescan else (state.get("last_fetched_at") or "")
    personas_l = [p for p in plist["personas"] if p.get("variants_n")]
    fronts_l = [f for f in plist["fronts"] if f.get("variants_n") or f.get("domains_n")]
    now = store.utcnow()
    counts = {"scanned": 0, "bodies": 0, "flagged": 0, "full": bool(rescan), "list_version": version,
              "personas": len(personas_l), "fronts": len(fronts_l)}
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM articles").fetchone()[0]
    max_fetched = conn.execute("SELECT COALESCE(MAX(fetched_at), '') FROM articles").fetchone()[0]
    if not personas_l and not fronts_l:
        _set_state(conn, last_scanned_id=max_id, last_fetched_at=max_fetched, list_version=version, scanned_at=now)
        conn.commit()
        return counts
    rows = conn.execute(
        """SELECT id, url_hash, author, title, body_hash, fetched_at FROM articles
           WHERE id > ? OR (fetched_at IS NOT NULL AND fetched_at > ?) ORDER BY id""",
        (last_id, last_fetched)).fetchall()
    for r in rows:
        counts["scanned"] += 1
        for ps, form in match_author(r["author"], personas_l):
            counts["flagged"] += _flag(conn, r["id"], "author_field", ps["id"], "", ps.get("operation"),
                                       "author field: %s" % (r["author"] or form), now, version)
        if r["body_hash"] and (r["id"] > last_id or (r["fetched_at"] or "") > last_fetched):
            body = store.load_body(r["url_hash"])
            if body:
                counts["bodies"] += 1
                for ps, ev in match_head(body, personas_l):
                    counts["flagged"] += _flag(conn, r["id"], "byline_head", ps["id"], "", ps.get("operation"), ev, now, version)
                for fr, ev in match_fronts(r["title"], body, fronts_l):
                    counts["flagged"] += _flag(conn, r["id"], "front_cited", "", fr["id"], fr.get("operation"), ev, now, version)
        if counts["scanned"] % 2000 == 0:
            conn.commit()
    _set_state(conn, last_scanned_id=max_id, last_fetched_at=max_fetched, list_version=version, scanned_at=now)
    conn.commit()
    return counts


def scan_quietly(conn: sqlite3.Connection, full: bool = False) -> Dict:
    """scan for the stages: an exception becomes a count, never a stop. The detector is a side
    channel and a broken persona file must not cost a discovery, fetch or export run."""
    try:
        return scan(conn, full=full)
    except Exception as exc:  # noqa: BLE001  anything at all is logged and swallowed here
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        print("planted scan failed: %s: %s" % (type(exc).__name__, exc))
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


# ---------------------------------------------------------------------------
# Recurring bylines diagnostic
# ---------------------------------------------------------------------------

def _is_generic(name: str) -> bool:
    if name in BYLINE_STOPLIST:
        return True
    return any(t in BYLINE_STOP_TOKENS for t in name.split())


def _is_wire_copy(row) -> bool:
    """Current state origin label from a wire credit: the article is wire text that happens to carry
    a reporter's name. The route column is filled by classify_rules.ensure_routes; a label still
    without one is read from its signatures."""
    if row["category"] != "A":
        return False
    if row["route"]:
        return row["route"] == "wire_credit"
    from pipeline import classify_rules
    try:
        ids = json.loads(row["signatures_fired"] or "[]")
    except ValueError:
        ids = []
    return classify_rules.route_of_ids(ids) == "wire_credit"


def recurring_bylines(conn: sqlite3.Connection, days: int = config.PLANTED_RECURRING_DAYS) -> List[Dict]:
    """Authors (normalised) with China-relevant articles in at least RECURRING_MIN_OUTLETS outlets
    and RECURRING_MIN_COUNTRIES countries discovered in the window. Left out: generic bylines
    (BYLINE_STOPLIST), single word bylines, names equal to an outlet or wire name in the outlets table
    (the distribution wire tier included), and authors whose every article in the window is wire
    copy with a reporter name (a current state origin label from a wire credit). A diagnostic for
    human review: wire reporters appear here whenever outlets print their names. pieces counts
    distinct headlines, so the same piece printed by several outlets (syndication) reads as one."""
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).replace(microsecond=0).isoformat()
    outlet_names = {normalise(r["name"]) for r in conn.execute("SELECT name FROM outlets")}
    outlet_names |= {normalise(r["id"]) for r in conn.execute("SELECT id FROM outlets")}
    rows = conn.execute(
        """SELECT a.author, a.outlet_id, a.country, a.title, c.category, c.route, c.signatures_fired
           FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 AND a.discovered_at>=? AND a.author IS NOT NULL AND a.author<>''
           ORDER BY a.discovered_at DESC, a.id DESC""", (since,)).fetchall()
    groups = {}
    for r in rows:
        name = strip_byline_word(normalise(r["author"]))
        if len(name.split()) < 2 or _is_generic(name) or name in outlet_names:
            continue
        g = groups.get(name)
        if g is None:
            g = groups[name] = {"name": r["author"].strip(), "outlets": set(), "countries": set(), "n": 0,
                                "titles": [], "pieces": set(), "wire_credit": False, "all_wire": True}
        g["n"] += 1
        g["outlets"].add(r["outlet_id"])
        g["countries"].add(r["country"])
        # Distinct pieces: the same headline printed by several outlets is one syndicated piece,
        # which is the wire pattern; a byline with as many pieces as articles is the persona pattern.
        g["pieces"].add(normalise(r["title"]) or ("#%d" % g["n"]))
        if r["title"] and len(g["titles"]) < RECURRING_SAMPLE_TITLES and r["title"] not in g["titles"]:
            g["titles"].append(r["title"])
        wire = _is_wire_copy(r)
        g["wire_credit"] = g["wire_credit"] or wire
        g["all_wire"] = g["all_wire"] and wire
    out = []
    for g in groups.values():
        if len(g["outlets"]) < RECURRING_MIN_OUTLETS or len(g["countries"]) < RECURRING_MIN_COUNTRIES or g["all_wire"]:
            continue
        out.append({"name": g["name"], "outlets": sorted(g["outlets"]), "countries": sorted(g["countries"]),
                    "n": g["n"], "pieces": len(g["pieces"]), "titles": g["titles"], "wire_credit": g["wire_credit"]})
    out.sort(key=lambda g: (-len(g["countries"]), -g["n"], g["name"]))
    return out[:RECURRING_MAX_ROWS]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _flag_rows(conn: sqlite3.Connection, plist: Dict) -> List[Dict]:
    """Every flag whose persona or front is still on the list, newest article first. A flag whose
    entry was taken off the list (a corrected report) stays in the database and leaves the site."""
    persona_ids = {p["id"] for p in plist["personas"]}
    front_ids = {f["id"] for f in plist["fronts"]}
    rows = conn.execute(
        """SELECT f.article_id, f.detector, f.persona_id, f.front_id, f.operation_id, f.evidence, f.flagged_at,
                  a.title, a.url, a.outlet_id, a.country, a.published_at, a.discovered_at, o.name AS outlet
           FROM planted_flags f JOIN articles a ON a.id=f.article_id LEFT JOIN outlets o ON o.id=a.outlet_id
           ORDER BY a.discovered_at DESC, f.id DESC""").fetchall()
    out = []
    for r in rows:
        if r["persona_id"] and r["persona_id"] not in persona_ids:
            continue
        if r["front_id"] and r["front_id"] not in front_ids:
            continue
        out.append({"article_id": r["article_id"], "title": r["title"], "url": r["url"], "outlet_id": r["outlet_id"],
                    "outlet": r["outlet"] or r["outlet_id"], "country": r["country"], "published_at": r["published_at"],
                    "discovered_at": r["discovered_at"], "detector": r["detector"],
                    "persona_id": r["persona_id"] or None, "front_id": r["front_id"] or None,
                    "operation_id": r["operation_id"] or None, "evidence": r["evidence"], "flagged_at": r["flagged_at"]})
    return out


def build_export(conn: sqlite3.Connection, personas: Optional[Dict] = None, days: int = config.PLANTED_RECURRING_DAYS) -> Dict:
    """The docs/data/planted.json structure. Flags are newest first and capped at FLAGS_EXPORT_CAP;
    the counts by operation, persona, front, country and outlet are over every flag."""
    plist = personas if personas is not None else load_personas()
    flags = _flag_rows(conn, plist)
    by_op, by_persona, by_front = defaultdict(int), defaultdict(int), defaultdict(int)
    by_country, by_outlet = defaultdict(int), defaultdict(int)
    for f in flags:
        by_op[f["operation_id"]] += 1
        if f["persona_id"]:
            by_persona[f["persona_id"]] += 1
        if f["front_id"]:
            by_front[f["front_id"]] += 1
        by_country[f["country"]] += 1
        by_outlet[f["outlet_id"]] += 1
    return {
        "generated": store.utcnow(),
        "list_version": plist["version"],
        "operations": [{"id": o["id"], "name": o.get("name"), "origin": o.get("origin"), "reported_by": o.get("reported_by"),
                        "report_url": o.get("report_url"), "reported_on": str(o.get("reported_on") or ""),
                        "summary": o.get("summary"), "flags": by_op.get(o["id"], 0)} for o in plist["operations"]],
        "personas": [{"id": p["id"], "name": p.get("name"), "operation": p.get("operation"), "kind": p.get("kind"),
                      "claimed": p.get("claimed"), "flags": by_persona.get(p["id"], 0)} for p in plist["personas"]],
        "fronts": [{"id": f["id"], "name": f.get("name"), "operation": f.get("operation"), "kind": f.get("kind"),
                    "flags": by_front.get(f["id"], 0)} for f in plist["fronts"]],
        "flags": [{k: v for k, v in f.items() if k != "flagged_at"} for f in flags[:FLAGS_EXPORT_CAP]],
        "flags_total": len(flags),
        "by_country": dict(sorted(by_country.items())),
        "by_outlet": dict(sorted(by_outlet.items())),
        "recurring_bylines": {"window_days": days, "rows": recurring_bylines(conn, days)},
    }


def meta_block(conn: sqlite3.Connection, personas: Optional[Dict] = None) -> Dict:
    """The planted block of meta.json. flags_30d counts flags on articles discovered in the last 30
    days, the same window the rest of the site uses, not flags written in the last 30 days: a full
    rescan after a list change rewrites nothing but would otherwise look like a surge."""
    plist = personas if personas is not None else load_personas()
    flags = _flag_rows(conn, plist)
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).replace(microsecond=0).isoformat()
    return {"flags_total": len(flags), "flags_30d": sum(1 for f in flags if (f["discovered_at"] or "") >= since),
            "list_version": plist["version"], "personas": len(plist["personas"]), "fronts": len(plist["fronts"]),
            "operations": len(plist["operations"])}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Planted contributions detector")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("scan", help="flag new articles against sources/personas.yaml")
    s.add_argument("--full", action="store_true", help="rescan every article")
    r = sub.add_parser("recurring", help="bylines recurring across unrelated outlets")
    r.add_argument("--days", type=int, default=config.PLANTED_RECURRING_DAYS)
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    conn = store.connect()
    if args.cmd == "scan":
        print(json.dumps(scan(conn, full=args.full)))
    else:
        print(json.dumps({"window_days": args.days, "rows": recurring_bylines(conn, args.days)}, ensure_ascii=False, indent=1))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
