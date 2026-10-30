"""Hosted travel disclosures: a disclosure flag, never a route to state origin.

Finds lines in which an article discloses that the reporting trip, the stay or a training programme
behind it was paid for or hosted by someone ("the writer travelled as a guest of ...", "the trip was
sponsored by ...", "Ce voyage a ete finance par ...", "Transparenzhinweis: Die Reise wurde von ...
bezahlt"), and checks who the host is. The patterns are in pipeline/hosted_travel.yaml, one block per
language, with how a line is read written at the top of that file.

What a flag is and is not (scratchpad DECISIONS D8, 2026-10-28). A flag says the article discloses a
hosted trip or programme. It does not say the article was written, commissioned or approved by its
host, and it is not state origin: no flag is ever folded into the categories (state origin,
unchecked state sourcing, independent journalism, undetermined), nothing here changes a count, a
label, the ruleset (RULESET_VERSION) or the China gate (GATE_VERSION). Absence of a flag proves
nothing: a hosted trip that is not disclosed, or disclosed in words the patterns do not know, is
missed. The counts are provisional until a hand check of the flagged lines.

Classes (the klass field of a flag):
  state_host    the host named in the disclosure is a Chinese state entity, embassy or consulate,
                state media outlet, party body or Chinese government programme: by
                classify_rules.has_state_entity (the same organisation list the rules use) or by a
                state_hosts pattern in the yaml (the forms has_state_entity leaves out on purpose
                because they are topics rather than organisations elsewhere, such as "the Chinese
                government", and the press centre programmes).
  unnamed_host  the disclosure names no host ("as a guest of the organisers", "the host
                government"): a weaker class, counted and shown apart, on China coverage only.
A disclosure whose host is named and is not a Chinese state body (a tournament, a company, another
country's tourism board) gives no flag.

Bodies are scanned where they exist, incrementally like the planted bylines scan (pipeline/planted.py):
rows added since the last scanned id and rows whose body arrived since the last fetch time, everything
again when the patterns version changes. The scan runs in the rules stage (pipeline/run.py) and again
at export (pipeline/export.py), which writes docs/data/hosted_travel.json and the hosted_travel block
of meta.json. Tables hosted_travel_flags and hosted_travel_state are created here, CREATE IF NOT EXISTS.

Usage:
  python -m pipeline.hosted_travel scan [--full] [--budget-seconds N]
  python -m pipeline.hosted_travel list [--limit N]
"""
import argparse
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

from pipeline import classify_rules, store

PATTERNS_PATH = Path(__file__).with_name("hosted_travel.yaml")
CLASSES = ("state_host", "unnamed_host")
HOST_MODES = ("after", "before", "group")
BASES = ("self", "label", "bio", "tail")
TAIL_CHARS = 600          # a disclosure line set at the end of the body sits within this many characters of it
LINE_MAX = 320            # and is no longer than this
HOST_CHARS = 140          # the host is read from at most this many characters
UNNAMED_HEAD = 40         # an unnamed host phrase starts within this many characters of the host text
EVIDENCE_MAX = 280        # the disclosure sentence as exported
FLAGS_EXPORT_CAP = 500
COMMIT_EVERY = 2000
# Cost. Anchors are run only near a trigger word of their own language (WINDOW characters either side),
# so a body is read once for the triggers and the regexes see a few hundred characters around each.
# A body longer than BODY_MAX (extraction gone wrong; the longest stored body on 2026-10-12 had 166,619
# characters) is read in its first BODY_HEAD and last BODY_TAIL characters, where disclosures sit.
WINDOW = 400
BODY_MAX = 200_000
BODY_HEAD = 150_000
BODY_TAIL = 50_000
# The export finishes what the rules stage left within this many seconds and carries on next run; the
# side file and meta say when the scan is not complete (bodies_left).
EXPORT_SCAN_SECONDS = 240
STATUS = "provisional"    # until a hand check of the flagged lines; see methodology_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS hosted_travel_flags (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL,
    klass TEXT NOT NULL,              -- state_host | unnamed_host
    pattern_id TEXT NOT NULL,
    language TEXT NOT NULL,           -- the pattern's language block
    basis TEXT NOT NULL,              -- self | label | tail: why the line reads as a disclosure
    host TEXT,                        -- the state entity matched, or the unnamed host phrase
    evidence TEXT,                    -- the disclosure sentence, capped at EVIDENCE_MAX
    flagged_at TEXT NOT NULL,
    patterns_version TEXT NOT NULL,
    UNIQUE(article_id, klass, pattern_id)
);
CREATE INDEX IF NOT EXISTS idx_hosted_travel_article ON hosted_travel_flags(article_id);
CREATE TABLE IF NOT EXISTS hosted_travel_read (
    article_id INTEGER PRIMARY KEY,   -- every row the scan looked at, so the page can say how many bodies were read
    patterns_version TEXT NOT NULL,
    has_body INTEGER NOT NULL         -- 0: the row has a body hash but its file could not be read
);
CREATE TABLE IF NOT EXISTS hosted_travel_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_SENT_END_RE = re.compile(r"(?<!\b[A-Z])[.!?](?=[\s\"'”»)\]]|$)|[。！？؟\n]")
_CLAUSE_CUT_RE = re.compile(
    r"[;:()\[\]،；]|,\s+(?=[a-zà-ÿа-яё])|\s(?:which|who|where|whose|while|during|when|after|before|to (?:attend|cover|report|visit|see|take part|join))\b"
    r"|\s(?:qui|où|pour (?:couvrir|assister|participer))\b|\s(?:que|quien|donde|para (?:cubrir|asistir|participar))\b"
    r"|\s(?:che|dove|per (?:seguire|partecipare))\b|\s(?:die|der|das|um (?:an|über))\s(?=\w+\s(?:zu|teilzunehmen))")
_QUOTES_OPEN = "“«„"
_QUOTES_CLOSE = "”»“"
# An acronym in brackets after a name ("China Global Television Network (CGTN)") belongs to the host;
# it is read as a word, so a bracket does not end the host there.
_ACRONYM_RE = re.compile(r"\(([A-Z]{2,8})\)")
# has_state_entity's subnational forms name no country: "Western Cape Provincial Government",
# "Government of Punjab Province", "Information Office of the Punjab Provincial ...". In a host text they
# count only next to a Chinese place name (the yaml's china_places). "People's Government" is the
# Chinese form and counts as it is.
_GENERIC_SUBNATIONAL_RE = re.compile(
    r"(?i)^(?:(?:municipal|provincial) government|government of .+|information office of the (?!state council\b).+)$")
_CMG_RE = re.compile(r"\bCMG\b")
# Format characters that split a phrase without showing (zero width spaces and joiners, bidirectional
# marks and isolates, the soft hyphen) are dropped, and the other Unicode spaces read as a space, so that
# "as a\u00a0guest of" and a right to left line wrapped in marks read like plain text.
_DROP_CHARS = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\u2060\u2066\u2067\u2068\u2069\u202a\u202b\u202c\u202d\u202e\ufeff\u00ad"))
_DROP_CHARS.update(dict.fromkeys(map(ord, "\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"), " "))


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

def _compile(rx: str, where: str) -> "re.Pattern":
    try:
        return re.compile(rx, re.IGNORECASE | re.UNICODE)
    except re.error as exc:
        raise ValueError("hosted_travel.yaml: %s does not compile: %s" % (where, exc))


def load_patterns(path: Optional[Path] = None) -> Dict:
    """The yaml, validated and compiled. Raises ValueError on a broken file: the scan's callers turn
    that into a count (scan_quietly), never a stop."""
    p = Path(path) if path else PATTERNS_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    version = str(data.get("version") or "")
    if not version:
        raise ValueError("hosted_travel.yaml: version is required")
    langs, anchors, seen, state_res, triggers, codes = {}, [], set(), [], set(), set()
    for lang, block in (data.get("languages") or {}).items():
        if not isinstance(block, dict):
            raise ValueError("hosted_travel.yaml: language %s is not a mapping" % lang)
        lb = {"lang": lang}
        codes.add(str(lang))
        codes.update(str(c) for c in (block.get("also") or []))
        for key in ("self", "label", "not_subject", "travel", "unnamed"):
            if not block.get(key):
                raise ValueError("hosted_travel.yaml: %s needs %s" % (lang, key))
            lb[key] = _compile(block[key], "%s.%s" % (lang, key))
        lb["label"] = _compile(r"^[\W_]{0,4}(?:%s)" % block["label"], "%s.label" % lang)
        lb["bio"] = _compile(block["bio"], "%s.bio" % lang) if block.get("bio") else None
        for i, rx in enumerate(block.get("state_hosts") or []):
            state_res.append(_compile(rx, "%s.state_hosts[%d]" % (lang, i)))
        words = [str(w) for w in (block.get("triggers") or []) if str(w).strip()]
        if not words:
            raise ValueError("hosted_travel.yaml: %s needs triggers" % lang)
        triggers.update(words)
        lb["triggers"] = sorted({w.lower() for w in words}, key=len, reverse=True)
        lb["trigger_re"] = re.compile("|".join(re.escape(w) for w in lb["triggers"]), re.IGNORECASE | re.UNICODE)
        for a in block.get("anchors") or []:
            aid = a.get("id")
            if not aid or aid in seen:
                raise ValueError("hosted_travel.yaml: anchor id missing or repeated: %r" % aid)
            seen.add(aid)
            host = a.get("host", "after")
            if host not in HOST_MODES:
                raise ValueError("hosted_travel.yaml: %s host must be one of %s" % (aid, ", ".join(HOST_MODES)))
            rx = _compile(a.get("regex") or "", aid)
            if host == "group" and "host" not in rx.groupindex:
                raise ValueError("hosted_travel.yaml: %s uses host: group without a (?P<host>...) group" % aid)
            anchors.append({"id": aid, "lang": lang, "re": rx, "host": host, "tail_ok": bool(a.get("tail_ok")),
                            "needs_travel": bool(a.get("needs_travel"))})
        langs[lang] = lb
    if not anchors:
        raise ValueError("hosted_travel.yaml: no anchors")
    trigger_re = re.compile("|".join(re.escape(w) for w in sorted(triggers, key=len, reverse=True)),
                            re.IGNORECASE | re.UNICODE)
    places = _compile(data["china_places"], "china_places") if data.get("china_places") else None
    return {"version": version, "languages": langs, "anchors": anchors, "state_hosts": state_res, "trigger_re": trigger_re,
            "china_places": places, "language_codes": sorted(codes)}


_PATTERNS_CACHE: Dict[str, Tuple[float, Dict]] = {}


def patterns(path: Optional[Path] = None) -> Dict:
    """load_patterns, cached on the file's modification time."""
    p = Path(path) if path else PATTERNS_PATH
    mtime = p.stat().st_mtime
    hit = _PATTERNS_CACHE.get(str(p))
    if hit and hit[0] == mtime:
        return hit[1]
    pat = load_patterns(p)
    _PATTERNS_CACHE[str(p)] = (mtime, pat)
    return pat


# ---------------------------------------------------------------------------
# Detection (pure)
# ---------------------------------------------------------------------------

def _sentence(text: str, start: int, end: int) -> Tuple[int, int]:
    s = 0
    for m in _SENT_END_RE.finditer(text, max(0, start - 400), start):
        s = m.end()
    m = _SENT_END_RE.search(text, end)
    e = m.end() if m else len(text)
    return s, e


def _line(text: str, start: int, end: int) -> Tuple[int, int]:
    s = text.rfind("\n", 0, start) + 1
    e = text.find("\n", end)
    return s, (len(text) if e == -1 else e)


def _inside_quote(prefix: str) -> bool:
    if prefix.count('"') % 2 == 1:
        return True
    opens = sum(prefix.count(c) for c in "«„")
    closes = sum(prefix.count(c) for c in "»")
    if opens > closes:
        return True
    # “ ... ” pairs; a lone “ before the anchor means the anchor is quoted.
    return prefix.count("“") > prefix.count("”")


def _host_span(text: str, m: "re.Match", anchor: Dict, s: int, e: int) -> Tuple[str, int, int]:
    """The host text and where it sits in text. A bracketed acronym after a name is kept with it."""
    if anchor["host"] == "group":
        return (m.group("host") or "").strip(), m.start("host"), m.end("host")
    if anchor["host"] == "before":
        start = max(s, m.start() - HOST_CHARS)
        seg = _ACRONYM_RE.sub(r" \1 ", text[start:m.start()])
        cuts = list(_CLAUSE_CUT_RE.finditer(seg))
        off = cuts[-1].end() if cuts else 0
        return seg[off:].strip(), start + off, m.start()
    end = min(e, m.end() + HOST_CHARS)
    seg = _ACRONYM_RE.sub(r" \1 ", text[m.end():end])
    cut = _CLAUSE_CUT_RE.search(seg)
    seg = seg[:cut.start()] if cut else seg
    return seg.strip().rstrip(".!?。！？؟*_ "), m.end(), m.end() + len(seg)


def _host_text(text: str, m: "re.Match", anchor: Dict, s: int, e: int) -> str:
    return _host_span(text, m, anchor, s, e)[0]


def state_entity(host: str, pat: Dict, context: str = "") -> Optional[str]:
    """The Chinese state entity the host text names, or None. has_state_entity first, then the
    state_hosts patterns of every language (a French article may name the host in English). A
    subnational government with no country in its name counts only next to a Chinese place name, and
    "CMG" on its own only in a sentence (context) that places it in China."""
    if not host:
        return None
    places = pat.get("china_places")
    for rx in (classify_rules.STATE_ENTITY_RE, classify_rules.STATE_ENTITY_NATIVE_RE, classify_rules.CMG_WITH_CHINA_RE):
        for m in rx.finditer(host):
            name = m.group(0).strip()
            if _GENERIC_SUBNATIONAL_RE.match(name) and not (places is not None and places.search(host)):
                continue
            return name
    for rx in pat["state_hosts"]:
        m = rx.search(host)
        if m:
            return m.group(0).strip()
    if context and _CMG_RE.search(host) and (classify_rules.CMG_WITH_CHINA_RE.search(context)
                                             or (places is not None and places.search(context))):
        return "CMG"
    return None


def _unnamed(host: str, lb: Dict, mode: str) -> Optional[str]:
    if not host:
        return None
    if mode == "before":
        m = lb["unnamed"].search(host[-(UNNAMED_HEAD + 20):])
    elif mode == "group":
        m = lb["unnamed"].search(host)
    else:
        m = lb["unnamed"].search(host[:UNNAMED_HEAD + 20])
        if m and m.start() > UNNAMED_HEAD:
            m = None
    return m.group(0).strip() if m else None


def _basis(text: str, m: "re.Match", anchor: Dict, lb: Dict, s: int, e: int, host: str = "") -> Optional[str]:
    """Why the line reads as a disclosure of the article's own reporting, or None when it does not.
    label: the line or the sentence opens with a disclosure label ("(Disclosure: ..." mid paragraph).
    self: the sentence names the article or its author. bio: a short last paragraph that names the
    author (an author note), whatever the anchor. tail: a short closing line, for tail_ok anchors.
    bio and tail need a sentence whose subject (read without the host) is not a politician, a team or
    a reported claim; the host's own name is left out of that check, so "the Embassy" in a host does
    not read as "an ambassador" in the subject."""
    prefix = text[s:m.start()]
    if _inside_quote(prefix):
        return None
    ls, le = _line(text, m.start(), m.end())
    if lb["label"].search(text[ls:le]) or lb["label"].search(text[s:e]):
        return "label"
    if lb["self"].search(text[s:m.end()]) or lb["self"].search(text[m.end():e][:60]):
        return "self"
    rest = text[s:e]
    at = rest.lower().find(host.lower()) if host else -1
    if at >= 0:
        rest = rest[:at] + " " + rest[at + len(host):]
    if lb["not_subject"].search(rest):
        return None
    line = text[ls:le]
    if le >= len(text.rstrip()) - TAIL_CHARS and (le - ls) <= 2 * LINE_MAX and (
            lb["self"].search(line) or (lb.get("bio") is not None and lb["bio"].search(line))):
        return "bio"
    if anchor["tail_ok"] and ls >= len(text) - TAIL_CHARS and (le - ls) <= LINE_MAX:
        return "tail"
    return None


def normalize(text: str) -> str:
    """NFC, format characters dropped, Unicode spaces read as spaces; a body over BODY_MAX is cut to
    its head and tail. detect reads this text, and the evidence it returns is from it."""
    text = unicodedata.normalize("NFC", text).translate(_DROP_CHARS)
    if len(text) > BODY_MAX:
        text = text[:BODY_HEAD] + "\n\n" + text[-BODY_TAIL:]
    return text


def _windows(text: str, pat: Dict) -> Dict[str, List[Tuple[int, int]]]:
    """Per language block, the stretches of text within WINDOW characters of one of its trigger words,
    merged. A language none of whose triggers appears gets none, so its anchors are never run."""
    low = text.lower()
    same = len(low) == len(text)          # lower() can change the length (a dotted capital I); then the regex
    out: Dict[str, List[Tuple[int, int]]] = {}
    for lang, lb in pat["languages"].items():
        hits: List[int] = []
        if same:
            for w in lb["triggers"]:
                i = low.find(w)
                while i != -1 and len(hits) < 5000:
                    hits.append(i)
                    i = low.find(w, i + len(w))
        else:
            hits = [m.start() for m in lb["trigger_re"].finditer(text)]
        if not hits:
            continue
        hits.sort()
        spans: List[Tuple[int, int]] = []
        for h in hits:
            a, b = max(0, h - WINDOW), min(len(text), h + WINDOW)
            if spans and a <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], b))
            else:
                spans.append((a, b))
        out[lang] = spans
    return out


def _matches(anchor: Dict, text: str, spans: List[Tuple[int, int]]):
    if spans is None:                     # windows off (tests compare both ways)
        yield from anchor["re"].finditer(text)
        return
    for a, b in spans:
        yield from anchor["re"].finditer(text, a, b)


def detect(text: Optional[str], pat: Optional[Dict] = None, china_coverage: bool = True, windows: bool = True) -> List[Dict]:
    """Every hosted travel disclosure in the text, one entry per anchor match that qualifies, with its
    class. Pure: no database. unnamed_host entries are returned only when china_coverage is True.
    windows=False runs every anchor over the whole text (slow; the tests check that it finds the same)."""
    if not text:
        return []
    pat = pat or patterns()
    text = normalize(text)
    if windows:
        wins = _windows(text, pat)
        if not wins:
            return []
    elif not pat["trigger_re"].search(text):
        return []
    out, seen = [], set()
    for anchor in pat["anchors"]:
        lb = pat["languages"][anchor["lang"]]
        if windows and anchor["lang"] not in wins:
            continue
        for m in _matches(anchor, text, wins[anchor["lang"]] if windows else None):
            s, e = _sentence(text, m.start(), m.end())
            if anchor["needs_travel"] and not lb["travel"].search(text[s:e]):
                continue
            host = _host_text(text, m, anchor, s, e)
            ent = state_entity(host, pat, text[s:e])
            if ent:
                klass, host_out = "state_host", ent
            else:
                un = _unnamed(host, lb, anchor["host"])
                if not un or not china_coverage:
                    continue
                klass, host_out = "unnamed_host", un
            basis = _basis(text, m, anchor, lb, s, e, ent or "")
            if basis is None:
                continue
            key = (klass, anchor["id"])
            if key in seen:
                continue
            seen.add(key)
            sentence = re.sub(r"\s+", " ", text[s:e]).strip().strip("*_ ")   # an italic footer's markers are not text
            if len(sentence) > EVIDENCE_MAX:
                sentence = sentence[:EVIDENCE_MAX - 3].rstrip() + "..."
            out.append({"klass": klass, "pattern_id": anchor["id"], "language": anchor["lang"], "basis": basis,
                        "host": host_out, "evidence": sentence, "start": m.start(), "end": m.end()})
    return out


def article_class(hits: Iterable[Dict]) -> Optional[str]:
    """state_host if any hit names a state host, else unnamed_host if any, else None."""
    ks = {h["klass"] for h in hits}
    return "state_host" if "state_host" in ks else ("unnamed_host" if "unnamed_host" in ks else None)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _state(conn: sqlite3.Connection) -> Dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT key, value FROM hosted_travel_state")}


def _set_state(conn: sqlite3.Connection, **values) -> None:
    for k, v in values.items():
        conn.execute("INSERT INTO hosted_travel_state(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (k, "" if v is None else str(v)))


def scan(conn: sqlite3.Connection, full: bool = False, deadline: Optional[float] = None, pat: Optional[Dict] = None) -> Dict:
    """Flag articles with a body. Incremental by default, rescanning everything when the patterns
    version changed or full is True; a rescan replaces an article's flags with what the current
    patterns find, so a pattern taken out takes its flags with it, and drops the flags of articles whose
    body is gone (they can no longer be read under the new patterns). Commits every COMMIT_EVERY rows and
    stops cleanly once time.monotonic() passes the deadline; the next call carries on. Rows come in id
    order: first the older rows whose body arrived again since the last scan, then the rows added since.
    The state moves past the re-fetched rows only once all of them are done, so a stop never skips one."""
    ensure_schema(conn)
    pat = pat or patterns()
    version = pat["version"]
    state = _state(conn)
    rescan = full or state.get("patterns_version") != version
    last_id = 0 if rescan else int(state.get("last_scanned_id") or 0)
    last_fetched = "" if rescan else (state.get("last_fetched_at") or "")
    now = store.utcnow()
    counts = {"scanned": 0, "bodies": 0, "flagged": 0, "state_host": 0, "unnamed_host": 0, "full": bool(rescan),
              "patterns_version": version, "stopped_at_deadline": False}
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM articles").fetchone()[0]
    max_fetched = conn.execute("SELECT COALESCE(MAX(fetched_at), '') FROM articles").fetchone()[0]
    if rescan:
        conn.execute("""DELETE FROM hosted_travel_flags WHERE article_id NOT IN
                        (SELECT id FROM articles WHERE body_hash IS NOT NULL)""")
    rows = conn.execute(
        """SELECT id, url_hash, body_hash, fetched_at, gate_relevant FROM articles
           WHERE body_hash IS NOT NULL AND (id > ? OR (fetched_at IS NOT NULL AND fetched_at > ?)) ORDER BY id""",
        (last_id, last_fetched)).fetchall()
    done_id = last_id

    def progress() -> Dict[str, object]:
        # done_id <= last_id: still among the re-fetched older rows, so nothing moves yet.
        if done_id <= last_id:
            return {"last_scanned_id": last_id, "last_fetched_at": last_fetched, "patterns_version": version}
        return {"last_scanned_id": done_id, "last_fetched_at": max_fetched, "patterns_version": version}

    for r in rows:
        if deadline is not None and time.monotonic() > deadline:
            counts["stopped_at_deadline"] = True
            break
        counts["scanned"] += 1
        body = store.load_body(r[1])
        if body:
            counts["bodies"] += 1
            hits = detect(body, pat, china_coverage=bool(r[4]))
            conn.execute("DELETE FROM hosted_travel_flags WHERE article_id=?", (r[0],))
            for h in hits:
                conn.execute(
                    """INSERT OR IGNORE INTO hosted_travel_flags(article_id, klass, pattern_id, language, basis, host, evidence,
                       flagged_at, patterns_version) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (r[0], h["klass"], h["pattern_id"], h["language"], h["basis"], h["host"], h["evidence"], now, version))
            k = article_class(hits)
            if k:
                counts["flagged"] += 1
                counts[k] += 1
        elif rescan:
            conn.execute("DELETE FROM hosted_travel_flags WHERE article_id=?", (r[0],))
        conn.execute("INSERT OR REPLACE INTO hosted_travel_read(article_id, patterns_version, has_body) VALUES (?,?,?)",
                     (r[0], version, 1 if body else 0))
        done_id = r[0]
        if counts["scanned"] % COMMIT_EVERY == 0:
            # Rows up to done_id are done under this version; a run that stops here resumes after it.
            _set_state(conn, **progress())
            conn.commit()
    if counts["stopped_at_deadline"]:
        # Rows up to done_id are done; rows after it are caught next time by id.
        _set_state(conn, **progress())
        counts["bodies_left"] = len(rows) - counts["scanned"]
    else:
        _set_state(conn, last_scanned_id=max_id, last_fetched_at=max_fetched, patterns_version=version, scanned_at=now)
    conn.commit()
    return counts


def scan_quietly(conn: sqlite3.Connection, full: bool = False, deadline: Optional[float] = None) -> Dict:
    """scan for the stages: an exception becomes a count, never a stop."""
    try:
        return scan(conn, full=full, deadline=deadline)
    except Exception as exc:  # noqa: BLE001  a side channel; a broken patterns file must not cost a run
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        _warn("hosted travel scan failed: %s: %s" % (type(exc).__name__, exc))
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def export_scan(conn: sqlite3.Connection) -> Dict:
    """For pipeline/export.py: scan_quietly within EXPORT_SCAN_SECONDS, so that a full rescan (a new
    patterns version, a first run on a large database) can never hold up the export; what is left is
    read in the next runs, and scan_status says how much."""
    return scan_quietly(conn, deadline=time.monotonic() + EXPORT_SCAN_SECONDS)


def scan_status(conn: sqlite3.Connection, pat: Optional[Dict] = None) -> Dict:
    """Whether every stored body has been read under the current patterns, and how many are left."""
    ensure_schema(conn)
    pat = pat or patterns()
    state = _state(conn)
    if state.get("patterns_version") != pat["version"]:
        left = conn.execute("SELECT COUNT(*) FROM articles WHERE body_hash IS NOT NULL").fetchone()[0]
    else:
        left = conn.execute(
            """SELECT COUNT(*) FROM articles WHERE body_hash IS NOT NULL
               AND (id > ? OR (fetched_at IS NOT NULL AND fetched_at > ?))""",
            (int(state.get("last_scanned_id") or 0), state.get("last_fetched_at") or "")).fetchone()[0]
    stored = conn.execute("SELECT COUNT(*) FROM articles WHERE body_hash IS NOT NULL").fetchone()[0]
    read = conn.execute("""SELECT COUNT(*) FROM hosted_travel_read r JOIN articles a ON a.id=r.article_id
                           WHERE r.patterns_version=? AND r.has_body=1""", (pat["version"],)).fetchone()[0]
    return {"scan_complete": left == 0, "bodies_left": int(left), "bodies_stored": int(stored), "bodies_read": int(read)}


def stage_scan(conn: sqlite3.Connection, counts: Dict, deadline: Optional[float] = None) -> None:
    """For pipeline/run.py: scan within a stage's wall clock deadline (time.time based), recording the
    result in the stage's counts under hosted_travel. Never raises."""
    try:
        mono = None if deadline is None else time.monotonic() + (deadline - time.time())
        counts["hosted_travel"] = scan_quietly(conn, deadline=mono)
    except Exception as exc:  # noqa: BLE001
        counts["hosted_travel"] = {"error": "%s: %s" % (type(exc).__name__, exc)}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _article_rows(conn: sqlite3.Connection) -> List[Dict]:
    """One entry per flagged article still in the database, newest first: its class (state_host over
    unnamed_host), the hosts, patterns and bases, and the first disclosure sentence of its class."""
    ensure_schema(conn)
    rows = conn.execute(
        """SELECT f.article_id, f.klass, f.pattern_id, f.language, f.basis, f.host, f.evidence,
                  a.title, a.url, a.outlet_id, a.country, a.language AS article_language, a.published_at, a.discovered_at,
                  o.name AS outlet
           FROM hosted_travel_flags f JOIN articles a ON a.id=f.article_id LEFT JOIN outlets o ON o.id=a.outlet_id
           ORDER BY a.discovered_at DESC, a.id DESC, f.id""").fetchall()
    by_article: Dict[int, Dict] = {}
    order: List[int] = []
    for r in rows:
        r = dict(r)
        aid = r["article_id"]
        if r["klass"] not in CLASSES:
            continue
        e = by_article.get(aid)
        if e is None:
            e = {"article_id": aid, "title": r["title"], "url": r["url"], "outlet_id": r["outlet_id"],
                 "outlet": r["outlet"] or r["outlet_id"], "country": r["country"], "language": r["article_language"],
                 "published_at": r["published_at"], "discovered_at": r["discovered_at"], "klass": None,
                 "hosts": [], "patterns": [], "bases": [], "evidence": None, "_ev": {}}
            by_article[aid] = e
            order.append(aid)
        e["_ev"].setdefault(r["klass"], r["evidence"])
        for key, val in (("hosts", r["host"]), ("patterns", r["pattern_id"]), ("bases", r["basis"])):
            if val and val not in e[key]:
                e[key].append(val)
        if r["klass"] == "state_host" or e["klass"] is None:
            e["klass"] = "state_host" if r["klass"] == "state_host" else (e["klass"] or r["klass"])
    out = []
    for aid in order:
        e = by_article[aid]
        e["evidence"] = e["_ev"].get(e["klass"])
        del e["_ev"]
        out.append(e)
    return out


def _tally(rows: List[Dict], key: str) -> Dict[str, Dict[str, int]]:
    t: Dict[str, Dict[str, int]] = defaultdict(lambda: {"state_host": 0, "unnamed_host": 0})
    for r in rows:
        t[str(r.get(key))][r["klass"]] += 1
    return {k: dict(v) for k, v in sorted(t.items())}


def empty_export() -> Dict:
    return {"generated": store.utcnow(), "status": STATUS, "patterns_version": "", "languages": [], "articles_total": 0,
            "state_host": 0, "unnamed_host": 0, "by_country": {}, "by_outlet": {}, "articles": [], "error": None,
            "scan_complete": False, "bodies_left": None, "bodies_stored": None, "bodies_read": None}


def build_export(conn: sqlite3.Connection, pat: Optional[Dict] = None) -> Dict:
    """The docs/data/hosted_travel.json structure: counts per class over every flagged article, by
    country and by outlet, and the articles newest first, capped at FLAGS_EXPORT_CAP; with the scan's
    status (scan_complete, bodies_left, bodies_stored: rows with a body, bodies_read: bodies actually read
    under the current patterns; a body file that cannot be read is not read)."""
    pat = pat or patterns()
    rows = _article_rows(conn)
    status = scan_status(conn, pat)
    outlet_names = {r["outlet_id"]: r["outlet"] for r in rows}
    by_outlet = _tally(rows, "outlet_id")
    for oid, v in by_outlet.items():
        v["name"] = outlet_names.get(oid) or oid
    return {
        "generated": store.utcnow(),
        "status": STATUS,
        "patterns_version": pat["version"],
        "languages": pat["language_codes"],
        "articles_total": len(rows),
        "state_host": sum(1 for r in rows if r["klass"] == "state_host"),
        "unnamed_host": sum(1 for r in rows if r["klass"] == "unnamed_host"),
        "by_country": _tally(rows, "country"),
        "by_outlet": by_outlet,
        "articles": rows[:FLAGS_EXPORT_CAP],
        "scan_complete": status["scan_complete"],
        "bodies_left": status["bodies_left"],
        "bodies_stored": status["bodies_stored"],
        "bodies_read": status["bodies_read"],
    }


def empty_meta_block() -> Dict:
    return {"status": STATUS, "articles_total": 0, "state_host": 0, "unnamed_host": 0, "state_host_30d": 0,
            "unnamed_host_30d": 0, "countries": 0, "outlets": 0, "patterns_version": "", "languages": 0,
            "scan_complete": False, "bodies_left": None, "bodies_stored": None, "bodies_read": None}


def meta_block(conn: sqlite3.Connection, pat: Optional[Dict] = None) -> Dict:
    """The hosted_travel block of meta.json. The 30 day counts are over articles discovered in the
    last 30 days, as elsewhere on the site. No clock value, so two exports of one database agree."""
    pat = pat or patterns()
    rows = _article_rows(conn)
    status = scan_status(conn, pat)
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).replace(microsecond=0).isoformat()
    recent = [r for r in rows if (r["discovered_at"] or "") >= since]
    return {"status": STATUS, "articles_total": len(rows),
            "state_host": sum(1 for r in rows if r["klass"] == "state_host"),
            "unnamed_host": sum(1 for r in rows if r["klass"] == "unnamed_host"),
            "state_host_30d": sum(1 for r in recent if r["klass"] == "state_host"),
            "unnamed_host_30d": sum(1 for r in recent if r["klass"] == "unnamed_host"),
            "countries": len({r["country"] for r in rows}), "outlets": len({r["outlet_id"] for r in rows}),
            "patterns_version": pat["version"], "languages": len(pat["language_codes"]),
            "scan_complete": status["scan_complete"], "bodies_left": status["bodies_left"],
            "bodies_stored": status["bodies_stored"], "bodies_read": status["bodies_read"]}


def build_export_quietly(conn: sqlite3.Connection) -> Dict:
    try:
        return build_export(conn)
    except Exception as exc:  # noqa: BLE001
        _warn("hosted travel export failed: %s: %s" % (type(exc).__name__, exc))
        e = empty_export()
        e["error"] = "%s: %s" % (type(exc).__name__, exc)
        return e


def meta_block_quietly(conn: sqlite3.Connection) -> Dict:
    try:
        return meta_block(conn)
    except Exception as exc:  # noqa: BLE001
        _warn("hosted travel meta block failed: %s: %s" % (type(exc).__name__, exc))
        return empty_meta_block()


def methodology_text(meta: Dict) -> str:
    """The methodology paragraph (pipeline/methodology.py, under Hosted travel disclosures)."""
    h = meta.get("hosted_travel") or {}
    head = ("Since 28 October 2026 the pipeline also reads the stored article bodies for a disclosure that the "
            "reporting trip, the stay or a training programme behind the article was paid for or hosted by someone, "
            "such as \"the writer travelled as a guest of\", \"the trip was sponsored by\", \"à l'invitation de\", "
            "\"auf Einladung der\" or \"atas undangan\" (pipeline/hosted_travel.yaml, with patterns in English, French, "
            "Spanish, Portuguese, Italian, German, Arabic, Russian, Indonesian and Malay, Dutch, Turkish, Chinese, "
            "Ukrainian, Croatian and Serbian, Korean and Vietnamese). A line counts only when it is about the article's "
            "own reporting: it names the article or its author, opens with a disclosure label, sits in a short author note "
            "at the end, or is a short line at the end of the body whose subject is not a politician, delegation or team "
            "and which reports no one else's claim; a quoted line never counts. The "
            "host is then checked. A disclosure whose host is a Chinese state entity, embassy, state media outlet, party "
            "body or Chinese government programme (the organisation list the rules use, plus forms such as \"the Chinese "
            "government\" and the China press centre programmes) is flagged as a state host; one that names no host "
            "(\"the organisers\", \"the host government\") is flagged apart as an unnamed host, on China coverage only, "
            "and is the weaker of the two; a host that is named and is not a Chinese state body gives no flag, and a "
            "provincial or city government counts only when a Chinese place is named with it. A flag "
            "marks a disclosed hosted trip. It is not state origin and never becomes it: flags never enter the "
            "categories, never change a label or a count, and the ruleset and gate versions are unchanged by them. "
            "A hosted trip that is not disclosed, or disclosed in words the patterns do not know, is missed, so the "
            "absence of a flag proves nothing; a body longer than %s characters is read in its first %s and last %s. "
            "The counts are provisional until the flagged lines have been checked by hand." % (
                format(BODY_MAX, ","), format(BODY_HEAD, ","), format(BODY_TAIL, ",")))
    if not h:
        return head
    def n(v, one, many):
        return "%d %s" % (v, one if v == 1 else many)
    return head + (" At export time %s a disclosure with a state host and %d with an unnamed host, "
                   "in %s and %s (%d and %d of them discovered in the last thirty days; patterns "
                   "version %s)." % (n(h.get("state_host", 0), "article carried", "articles carried"), h.get("unnamed_host", 0),
                                     n(h.get("countries", 0), "country", "countries"), n(h.get("outlets", 0), "outlet", "outlets"),
                                     h.get("state_host_30d", 0), h.get("unnamed_host_30d", 0),
                                     h.get("patterns_version") or "none")) + (
        "" if h.get("scan_complete") or h.get("bodies_left") is None else
        " The scan had not yet read %d of the %d stored bodies under that version, so these counts can still rise."
        % (h.get("bodies_left") or 0, h.get("bodies_stored") or 0)) + (
        "" if h.get("bodies_read") is None else " %d article bodies had been read under it." % h["bodies_read"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Hosted travel disclosures (a disclosure flag, never state origin)")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("scan", help="flag article bodies against pipeline/hosted_travel.yaml")
    s.add_argument("--full", action="store_true", help="rescan every body")
    s.add_argument("--budget-seconds", type=float, default=None)
    li = sub.add_parser("list", help="the flagged articles, newest first")
    li.add_argument("--limit", type=int, default=50)
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    conn = store.connect()
    if args.cmd == "scan":
        deadline = time.monotonic() + args.budget_seconds if args.budget_seconds else None
        print(json.dumps(scan(conn, full=args.full, deadline=deadline)))
    else:
        print(json.dumps({"rows": _article_rows(conn)[:args.limit]}, ensure_ascii=False, indent=1))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
