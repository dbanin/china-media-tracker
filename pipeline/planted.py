"""Planted contributions detector.

Flags articles in the monitored outlets whose byline, or whose cited organisation, matches a
persona or false front named in a published investigation (sources/personas.yaml), and computes a
command line diagnostic of bylines that recur across unrelated outlets.

What a flag is and is not. The tracker cannot prove a byline is fictitious. A flag means "the byline
matches a persona named in a published investigation, cited here". The recurring bylines table is a
diagnostic for human review on the command line, never a finding and never published. Absence of a
match proves nothing. No article flagged here is ever folded into the existing categories (state
origin, unchecked state sourcing, independent journalism) and nothing here changes a category count.

Detectors (the detector field of a flag):
  author_field  one name in the article's author field equals a persona name or variant after
                normalisation. The raw field is first split on , ; | / & and the words and, y, e,
                et, und, en; honorifics at the start of a segment (dr, prof, mr, ms, mrs, by) and
                credentials at its end (phd, md) are dropped; a two part comma field is also read as
                "Last, First"; a segment with an @ in it is dropped. Runs on every discovered item,
                including those the China gate rejected, because planted bylines are not only
                about China.
  byline_head   a persona name preceded by a byline word (by, por, par, von, door, av, af, di) that
                stands at the start of a line or right after "written", "escrito" or "ecrit",
                within the first HEAD_CHARS characters of the body. A byline word in the middle of
                a sentence ("announced by ...", "una declaracion de ...") never counts.
  front_cited   a front organisation's name, or one of its domains, appears anywhere in the title or
                body. Names match as whole phrases of two tokens or more and never when the word
                before them is a capitalised word that extends the name ("the National Social
                Research Center" does not cite the "Social Research Center"). Domains match as whole
                hostnames, a subdomain of a listed domain included.

Names are normalised before comparison: casefold, diacritics stripped, punctuation dropped,
whitespace collapsed, and initial-less variants derived ("Ervin B. Hoskins" also matches "Ervin
Hoskins"). A single word never matches.

The scan is incremental and resumable: it commits every COMMIT_EVERY rows, writing where it got to,
and stops cleanly at a deadline (time.monotonic based) so a cancelled run carries on next time.

Usage:
  python -m pipeline.planted scan [--full] [--budget-seconds N]
  python -m pipeline.planted recurring [--days N]
"""
import argparse
import bisect
import datetime as dt
import json
import re
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import yaml

from pipeline import config, store

HEAD_CHARS = 400
BYLINE_WORDS = ("by", "por", "par", "von", "door", "av", "af", "di")
BYLINE_LEAD_WORDS = ("written", "escrito", "ecrit")
HONORIFICS = {"dr", "prof", "mr", "ms", "mrs", "by"}
CREDENTIALS = {"phd", "md"}
EVIDENCE_CONTEXT = 60
FLAGS_EXPORT_CAP = 500
COMMIT_EVERY = 2000
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

# Capitalised words that may stand right before a front's name without extending it: articles,
# prepositions and reporting verbs that open a sentence or a clause in the tracked languages.
NAME_LEAD_STOP = {
    "the", "a", "an", "of", "and", "or", "by", "at", "in", "on", "to", "from", "with", "for", "as", "per", "via",
    "according", "says", "said", "that", "this", "which", "its", "their", "our", "his", "her", "called", "named",
    "el", "la", "los", "las", "un", "una", "del", "de", "y", "o", "en", "por", "para", "con", "segun",
    "le", "les", "l", "du", "des", "d", "et", "au", "aux", "sur", "dans", "pour", "avec", "selon",
    "der", "die", "das", "den", "dem", "ein", "eine", "und", "von", "vom", "im", "am", "bei", "mit", "nach", "laut",
    "il", "lo", "gli", "i", "uno", "dello", "della", "dei", "degli", "delle", "e", "ed", "nel", "nella", "al",
    "alla", "secondo", "os", "um", "uma", "do", "da", "dos", "das", "no", "na", "nos", "nas", "segundo",
    "het", "een", "van", "op", "bij", "volgens", "met",
}


def _warn(msg: str) -> None:
    print("planted: %s" % msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _strip_marks(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalise(text: Optional[str]) -> str:
    """Casefold, strip diacritics, drop punctuation, collapse whitespace."""
    if not text:
        return ""
    text = _strip_marks(text).casefold()
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


def _string_list(value, what: str, entry_id: str) -> List[str]:
    """A list of strings; a bare string is one entry; anything else is dropped with a warning."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, str):
                out.append(v)
            else:
                _warn("%s: %s entry %r is not a string, dropped" % (entry_id, what, v))
        return out
    _warn("%s: %s is not a list, ignored" % (entry_id, what))
    return []


def _entries(data: Dict, key: str, op_ids: set) -> List[Dict]:
    """Personas or fronts validated: a dict with a string name, an id unique within the section and
    an operation that resolves. Anything else is dropped with a one line warning."""
    out, seen = [], set()
    for e in data.get(key) or []:
        if not isinstance(e, dict) or not e.get("id"):
            _warn("%s entry without an id dropped: %r" % (key, e))
            continue
        eid = str(e["id"])
        if eid in seen:
            _warn("duplicate %s id %s: later entry dropped" % (key, eid))
            continue
        if not isinstance(e.get("name"), str):
            _warn("%s %s: name is not a string, dropped" % (key, eid))
            continue
        op = e.get("operation")
        if not isinstance(op, str) or op not in op_ids:
            _warn("%s %s: operation %r does not resolve, dropped" % (key, eid, op))
            continue
        seen.add(eid)
        e = dict(e, id=eid)
        e["variants"] = _string_list(e.get("variants"), "variants", eid)
        e["variants_n"] = name_variants(e["name"], e["variants"])
        if key == "fronts":
            e["domains"] = _string_list(e.get("domains"), "domains", eid)
            e["domains_n"] = sorted({d.strip().casefold() for d in e["domains"] if d.strip()})
        out.append(e)
    return out


def load_personas(path: Optional[Path] = None) -> Dict:
    """sources/personas.yaml compiled for matching. A missing, empty or broken file loads as empty
    lists, never as an error: the detector is optional and must not stop a run. Every parse or
    validation problem is one warning line on stderr. Each persona gains a `variants_n` list of
    normalised forms; each front gains `variants_n` and `domains_n`. Entries without a usable name
    (fewer than two tokens) are kept for the export but match nothing."""
    p = Path(path) if path is not None else config.PERSONAS_PATH
    data = None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        return _empty_list()
    except (OSError, yaml.YAMLError) as exc:
        _warn("%s could not be read: %s: %s" % (p, type(exc).__name__, re.sub(r"\s+", " ", str(exc))))
        return _empty_list()
    if data is None:
        return _empty_list()
    if not isinstance(data, dict):
        _warn("%s is not a mapping, loaded as empty" % p)
        return _empty_list()
    if "version" not in data:
        _warn("%s has no version, loaded as empty" % p)
        return _empty_list()
    if not isinstance(data["version"], str):
        _warn("%s: version %r is not a string (quote it), loaded as empty" % (p, data["version"]))
        return _empty_list()
    out = _empty_list()
    out["version"] = data["version"]
    seen = set()
    for op in data.get("operations") or []:
        if not isinstance(op, dict) or not op.get("id"):
            _warn("operations entry without an id dropped: %r" % (op,))
            continue
        oid = str(op["id"])
        if oid in seen:
            _warn("duplicate operations id %s: later entry dropped" % oid)
            continue
        seen.add(oid)
        out["operations"].append(dict(op, id=oid))
    out["personas"] = _entries(data, "personas", seen)
    out["fronts"] = _entries(data, "fronts", seen)
    return out


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

_AUTHOR_SPLIT_RE = re.compile(r"\s*(?:[,;|/&]|\b(?:and|y|e|et|und|en)\b)\s*", re.IGNORECASE)
_RE_CACHE: Dict = {}
_RE_CACHE_MAX = 64


def _cached(key, build):
    """One compiled alternation per distinct list, built on first use."""
    hit = _RE_CACHE.get(key)
    if hit is None:
        if len(_RE_CACHE) >= _RE_CACHE_MAX:
            _RE_CACHE.clear()
        hit = _RE_CACHE[key] = build()
    return hit


def _alternation(forms: Iterable[str]) -> str:
    return "|".join(re.escape(f) for f in sorted(set(forms), key=lambda f: (-len(f), f)))


def _clean_segment(seg: str) -> str:
    """A normalised author segment without its honorifics and credentials."""
    toks = normalise(seg).split()
    while toks and toks[0] in HONORIFICS:
        toks.pop(0)
    while toks and toks[-1] in CREDENTIALS:
        toks.pop()
    return " ".join(toks)


def _author_segments(author: str) -> List[str]:
    """The names an author field may hold: the whole field, each name in a list of several, and the
    reversed reading of a two part comma field ("Williams, Jenny"). Each form is normalised and
    stripped of honorifics and credentials; a segment with an @ in it (an email) is dropped; a form
    shorter than two tokens can never match and is left out."""
    raw = author or ""
    if not raw.strip():
        return []
    parts = [p for p in _AUTHOR_SPLIT_RE.split(raw) if p and p.strip() and "@" not in p]
    segs: List[str] = []

    def add(s: str) -> None:
        if s and len(s.split()) >= 2 and s not in segs:
            segs.append(s)

    cleaned = [_clean_segment(p) for p in parts]
    add(_clean_segment(" ".join(parts)))
    for c in cleaned:
        add(c)
    if len(parts) == 2 and raw.count(",") == 1:
        add(" ".join(c for c in reversed(cleaned) if c))
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


def _head_text(body: Optional[str]) -> str:
    """The first HEAD_CHARS characters normalised line by line, line breaks kept, so a byline word
    can be told at the start of a line from one in the middle of a sentence."""
    lines = [normalise(line) for line in (body or "")[:HEAD_CHARS].splitlines()]
    return "\n".join(line for line in lines if line)


def _head_matcher(personas: List[Dict]):
    key = ("head",) + tuple((p["id"], tuple(p.get("variants_n") or ())) for p in personas)

    def build():
        lookup: Dict[str, List[Dict]] = defaultdict(list)
        for p in personas:
            for v in p.get("variants_n") or []:
                lookup[v].append(p)
        if not lookup:
            return None, lookup
        pat = r"(?:^|(?:^|\s)(?:%s)\s)(?:%s)\s(%s)(?!\S)" % (
            "|".join(BYLINE_LEAD_WORDS), "|".join(BYLINE_WORDS), _alternation(lookup))
        return re.compile(pat, re.MULTILINE), lookup
    return _cached(key, build)


def match_head(body: Optional[str], personas: List[Dict]) -> List[Tuple[Dict, str]]:
    """Personas whose name follows a byline word that opens a line, or follows "written" and its
    translations, within the first HEAD_CHARS characters of the body. Returns (persona, evidence),
    the evidence being the normalised byline with its context."""
    head = _head_text(body)
    if not head:
        return []
    rx, lookup = _head_matcher(personas)
    if rx is None:
        return []
    hits: Dict[str, str] = {}
    for m in rx.finditer(head):
        for p in lookup.get(m.group(1), ()):
            hits.setdefault(p["id"], _context(head, m.start(), m.end()))
    return [(p, hits[p["id"]]) for p in personas if p["id"] in hits]


def _front_matchers(fronts: List[Dict]):
    key = ("front",) + tuple((f["id"], tuple(f.get("variants_n") or ()), tuple(f.get("domains_n") or ())) for f in fronts)

    def build():
        names: Dict[str, List[Dict]] = defaultdict(list)
        domains: Dict[str, List[Dict]] = defaultdict(list)
        for f in fronts:
            for v in f.get("variants_n") or []:
                names[v].append(f)
            for d in f.get("domains_n") or []:
                domains[d].append(f)
        name_rx = re.compile(r"(?<!\S)(%s)(?!\S)" % _alternation(names)) if names else None
        domain_rx = re.compile(r"(?<![\w-])(%s)(?![\w-])(?!\.[\w-])" % _alternation(domains)) if domains else None
        return name_rx, names, domain_rx, domains
    return _cached(key, build)


_TOKEN_RE = re.compile(r"[^\W_]+")
_CLAUSE_BREAK_RE = re.compile(r"[.!?:;,()\[\]\"'«»“”„\n\r|]")


class _CasedText:
    """The title and body tokenised with case kept, built only once a phrase has matched, to tell
    "the Social Research Center" (cited) from "the National Social Research Center" (another body)."""

    def __init__(self, raw: str):
        stripped = _strip_marks(raw)
        self.spans = [(m.start(), m.end(), m.group()) for m in _TOKEN_RE.finditer(stripped)]
        self.raw = stripped
        low = [t.casefold() for _, _, t in self.spans]
        self.text = " ".join(low)
        self.starts, pos = [], 0
        for t in low:
            self.starts.append(pos)
            pos += len(t) + 1

    def first_clean(self, rx) -> Optional[Tuple[int, int]]:
        """The first occurrence of the phrase not preceded by a capitalised word of the same name."""
        for m in rx.finditer(self.text):
            i = bisect.bisect_left(self.starts, m.start())
            if i == 0 or i >= len(self.spans):
                return m.start(), m.end()
            _, prev_end, prev_tok = self.spans[i - 1]
            between = self.raw[prev_end:self.spans[i][0]]
            if _CLAUSE_BREAK_RE.search(between):
                return m.start(), m.end()
            if prev_tok[0].isupper() and prev_tok.casefold() not in NAME_LEAD_STOP:
                continue
            return m.start(), m.end()
        return None


def match_fronts(title: Optional[str], body: Optional[str], fronts: List[Dict]) -> List[Tuple[Dict, str]]:
    """Fronts whose name (two tokens or more, as a whole phrase not extended by a capitalised word
    before it) or one of whose domains (as a whole hostname) appears anywhere in the title or body.
    Returns (front, evidence)."""
    raw = "%s\n%s" % (title or "", body or "")
    if not raw.strip():
        return []
    name_rx, names, domain_rx, domains = _front_matchers(fronts)
    hits: Dict[str, str] = {}
    if name_rx is not None:
        text = normalise(raw)
        cased = None
        checked: Dict[str, Optional[str]] = {}
        for m in name_rx.finditer(text):
            phrase = m.group(1)
            if phrase not in checked:
                if cased is None:
                    cased = _CasedText(raw)
                span = cased.first_clean(re.compile(r"(?<!\S)%s(?!\S)" % re.escape(phrase)))
                checked[phrase] = _context(cased.text, *span) if span else None
            ev = checked[phrase]
            if ev is None:
                continue
            for f in names.get(phrase, ()):
                hits.setdefault(f["id"], ev)
    if domain_rx is not None:
        low = raw.casefold()
        for m in domain_rx.finditer(low):
            for f in domains.get(m.group(1), ()):
                hits.setdefault(f["id"], _context(low, m.start(), m.end()))
    return [(f, hits[f["id"]]) for f in fronts if f["id"] in hits]


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


def scan(conn: sqlite3.Connection, full: bool = False, deadline: Optional[float] = None,
         personas: Optional[Dict] = None) -> Dict:
    """Flag articles against the persona list. Incremental by default: rows discovered since the
    last scanned id, plus rows fetched since the last scan so a body that arrived later still gets
    the body detectors. Everything is rescanned when the list version changed or full is True.
    Flags are never deleted; a rescan adds what is new and the UNIQUE constraint ignores the rest.
    Where the scan got to is committed every COMMIT_EVERY rows, and once time.monotonic() passes
    the deadline the scan commits and returns with stopped_at_deadline True, so the next call
    carries on from the last row done."""
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
              "personas": len(personas_l), "fronts": len(fronts_l), "stopped_at_deadline": False}
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
    done_id = last_id
    for r in rows:
        if deadline is not None and time.monotonic() > deadline:
            counts["stopped_at_deadline"] = True
            break
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
        done_id = r["id"]
        if counts["scanned"] % COMMIT_EVERY == 0:
            _set_state(conn, last_scanned_id=done_id, last_fetched_at=max_fetched, list_version=version)
            conn.commit()
    if counts["stopped_at_deadline"]:
        # Rows up to done_id are done; rows after it are caught next time by id, and a body fetched
        # for an earlier row after this run is caught by its fetch time.
        _set_state(conn, last_scanned_id=done_id, last_fetched_at=max_fetched, list_version=version)
    else:
        _set_state(conn, last_scanned_id=max_id, last_fetched_at=max_fetched, list_version=version, scanned_at=now)
    conn.commit()
    return counts


def scan_quietly(conn: sqlite3.Connection, full: bool = False, deadline: Optional[float] = None) -> Dict:
    """scan for the stages: an exception becomes a count, never a stop. The detector is a side
    channel and a broken persona file must not cost a discovery, fetch or export run."""
    try:
        return scan(conn, full=full, deadline=deadline)
    except Exception as exc:  # noqa: BLE001  anything at all is logged and swallowed here
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        print("planted scan failed: %s: %s" % (type(exc).__name__, exc))
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


# ---------------------------------------------------------------------------
# Recurring bylines diagnostic (command line only, never exported)
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
    human review on the command line, never published: wire reporters appear here whenever outlets
    print their names. pieces counts distinct headlines, so the same piece printed by several
    outlets (syndication) reads as one."""
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


def empty_export() -> Dict:
    """The docs/data/planted.json structure with nothing in it, written when the export fails."""
    return {"generated": store.utcnow(), "list_version": "", "operations": [], "personas": [], "fronts": [],
            "flags": [], "flags_total": 0, "articles_total": 0, "by_country": {}, "by_outlet": {}}


def empty_meta_block() -> Dict:
    return {"flags_total": 0, "articles_total": 0, "flags_30d": 0, "list_version": "", "personas": 0, "fronts": 0,
            "operations": 0}


def build_export(conn: sqlite3.Connection, personas: Optional[Dict] = None) -> Dict:
    """The docs/data/planted.json structure. Flags are newest first and capped at FLAGS_EXPORT_CAP;
    the counts by operation, persona, front, country and outlet are over every flag. The recurring
    bylines diagnostic is not part of it: it stays on the command line."""
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
        "articles_total": len({f["article_id"] for f in flags}),
        # A null country must not stop the export: keys are sorted by their string form.
        "by_country": dict(sorted(by_country.items(), key=lambda kv: str(kv[0]))),
        "by_outlet": dict(sorted(by_outlet.items(), key=lambda kv: str(kv[0]))),
    }


def meta_block(conn: sqlite3.Connection, personas: Optional[Dict] = None) -> Dict:
    """The planted block of meta.json. flags_30d counts flags on articles discovered in the last 30
    days, the same window the rest of the site uses, not flags written in the last 30 days: a full
    rescan after a list change rewrites nothing but would otherwise look like a surge."""
    plist = personas if personas is not None else load_personas()
    flags = _flag_rows(conn, plist)
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).replace(microsecond=0).isoformat()
    return {"flags_total": len(flags), "articles_total": len({f["article_id"] for f in flags}),
            "flags_30d": sum(1 for f in flags if (f["discovered_at"] or "") >= since),
            "list_version": plist["version"], "personas": len(plist["personas"]), "fronts": len(plist["fronts"]),
            "operations": len(plist["operations"])}


def build_export_quietly(conn: sqlite3.Connection) -> Dict:
    """build_export for the export stage: a failure is printed and becomes an empty structure,
    never a stop, in the spirit of scan_quietly."""
    try:
        return build_export(conn)
    except Exception as exc:  # noqa: BLE001
        _warn("planted export failed: %s: %s" % (type(exc).__name__, exc))
        return empty_export()


def meta_block_quietly(conn: sqlite3.Connection) -> Dict:
    """meta_block for the export stage: a failure is printed and becomes an empty block."""
    try:
        return meta_block(conn)
    except Exception as exc:  # noqa: BLE001
        _warn("planted meta block failed: %s: %s" % (type(exc).__name__, exc))
        return empty_meta_block()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Planted contributions detector")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("scan", help="flag new articles against sources/personas.yaml")
    s.add_argument("--full", action="store_true", help="rescan every article")
    s.add_argument("--budget-seconds", type=float, default=None, help="stop cleanly after this long; the next scan resumes")
    r = sub.add_parser("recurring", help="bylines recurring across unrelated outlets (a diagnostic, never published)")
    r.add_argument("--days", type=int, default=config.PLANTED_RECURRING_DAYS)
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    conn = store.connect()
    if args.cmd == "scan":
        deadline = time.monotonic() + args.budget_seconds if args.budget_seconds else None
        print(json.dumps(scan(conn, full=args.full, deadline=deadline)))
    else:
        print(json.dumps({"window_days": args.days, "rows": recurring_bylines(conn, args.days)}, ensure_ascii=False, indent=1))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
