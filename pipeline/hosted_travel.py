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
nothing: most hosted trips are never disclosed, and a disclosure worded in a way the patterns do not
know is missed. The counts are provisional until a hand check of the flagged lines.

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
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import yaml

from pipeline import classify_rules, store

PATTERNS_PATH = Path(__file__).with_name("hosted_travel.yaml")
CLASSES = ("state_host", "unnamed_host")
HOST_MODES = ("after", "before", "group")
BASES = ("self", "label", "tail")
TAIL_CHARS = 600          # a disclosure line set at the end of the body sits within this many characters of it
LINE_MAX = 320            # and is no longer than this
HOST_CHARS = 140          # the host is read from at most this many characters
UNNAMED_HEAD = 40         # an unnamed host phrase starts within this many characters of the host text
EVIDENCE_MAX = 280        # the disclosure sentence as exported
FLAGS_EXPORT_CAP = 500
COMMIT_EVERY = 2000
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
    langs, anchors, seen, state_res, triggers = {}, [], set(), [], set()
    for lang, block in (data.get("languages") or {}).items():
        if not isinstance(block, dict):
            raise ValueError("hosted_travel.yaml: language %s is not a mapping" % lang)
        lb = {"lang": lang}
        for key in ("self", "label", "not_subject", "travel", "unnamed"):
            if not block.get(key):
                raise ValueError("hosted_travel.yaml: %s needs %s" % (lang, key))
            lb[key] = _compile(block[key], "%s.%s" % (lang, key))
        lb["label"] = _compile(r"^[\W_]{0,4}(?:%s)" % block["label"], "%s.label" % lang)
        for i, rx in enumerate(block.get("state_hosts") or []):
            state_res.append(_compile(rx, "%s.state_hosts[%d]" % (lang, i)))
        words = [str(w) for w in (block.get("triggers") or []) if str(w).strip()]
        if not words:
            raise ValueError("hosted_travel.yaml: %s needs triggers" % lang)
        triggers.update(words)
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
    return {"version": version, "languages": langs, "anchors": anchors, "state_hosts": state_res, "trigger_re": trigger_re}


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


def _host_text(text: str, m: "re.Match", anchor: Dict, s: int, e: int) -> str:
    if anchor["host"] == "group":
        return (m.group("host") or "").strip()
    if anchor["host"] == "before":
        seg = text[max(s, m.start() - HOST_CHARS):m.start()]
        cuts = list(_CLAUSE_CUT_RE.finditer(seg))
        return (seg[cuts[-1].end():] if cuts else seg).strip()
    seg = text[m.end():min(e, m.end() + HOST_CHARS)]
    cut = _CLAUSE_CUT_RE.search(seg)
    seg = seg[:cut.start()] if cut else seg
    return seg.strip().rstrip(".!?。！？؟ ")


def state_entity(host: str, pat: Dict) -> Optional[str]:
    """The Chinese state entity the host text names, or None. has_state_entity first, then the
    state_hosts patterns of every language (a French article may name the host in English)."""
    if not host:
        return None
    for rx in (classify_rules.STATE_ENTITY_RE, classify_rules.STATE_ENTITY_NATIVE_RE, classify_rules.CMG_WITH_CHINA_RE):
        m = rx.search(host)
        if m:
            return m.group(0).strip()
    for rx in pat["state_hosts"]:
        m = rx.search(host)
        if m:
            return m.group(0).strip()
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


def _basis(text: str, m: "re.Match", anchor: Dict, lb: Dict, s: int, e: int) -> Optional[str]:
    """Why the line reads as a disclosure of the article's own reporting, or None when it does not."""
    prefix = text[s:m.start()]
    if _inside_quote(prefix):
        return None
    ls, le = _line(text, m.start(), m.end())
    if lb["label"].search(text[ls:le]):
        return "label"
    if lb["self"].search(text[s:m.end()]) or lb["self"].search(text[m.end():e][:60]):
        return "self"
    if anchor["tail_ok"] and ls >= len(text) - TAIL_CHARS and (le - ls) <= LINE_MAX and not lb["not_subject"].search(text[s:e]):
        return "tail"
    return None


def detect(text: Optional[str], pat: Optional[Dict] = None, china_coverage: bool = True) -> List[Dict]:
    """Every hosted travel disclosure in the text, one entry per anchor match that qualifies, with its
    class. Pure: no database. unnamed_host entries are returned only when china_coverage is True."""
    if not text:
        return []
    pat = pat or patterns()
    if not pat["trigger_re"].search(text):
        return []
    out, seen = [], set()
    for anchor in pat["anchors"]:
        lb = pat["languages"][anchor["lang"]]
        for m in anchor["re"].finditer(text):
            s, e = _sentence(text, m.start(), m.end())
            if anchor["needs_travel"] and not lb["travel"].search(text[s:e]):
                continue
            basis = _basis(text, m, anchor, lb, s, e)
            if basis is None:
                continue
            host = _host_text(text, m, anchor, s, e)
            ent = state_entity(host, pat)
            if ent:
                klass, host_out = "state_host", ent
            else:
                un = _unnamed(host, lb, anchor["host"])
                if not un or not china_coverage:
                    continue
                klass, host_out = "unnamed_host", un
            key = (klass, anchor["id"])
            if key in seen:
                continue
            seen.add(key)
            sentence = re.sub(r"\s+", " ", text[s:e]).strip()
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
    patterns find, so a pattern taken out takes its flags with it. Commits every COMMIT_EVERY rows and
    stops cleanly once time.monotonic() passes the deadline; the next call carries on."""
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
    rows = conn.execute(
        """SELECT id, url_hash, body_hash, fetched_at, gate_relevant FROM articles
           WHERE body_hash IS NOT NULL AND (id > ? OR (fetched_at IS NOT NULL AND fetched_at > ?)) ORDER BY id""",
        (last_id, last_fetched)).fetchall()
    done_id = last_id
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
        done_id = r[0]
        if counts["scanned"] % COMMIT_EVERY == 0:
            # Rows up to done_id are done under this version; a run that stops here resumes after it.
            _set_state(conn, last_scanned_id=done_id, last_fetched_at=max_fetched, patterns_version=version)
            conn.commit()
    if counts["stopped_at_deadline"]:
        # Rows up to done_id are done; rows after it are caught next time by id.
        _set_state(conn, last_scanned_id=done_id, last_fetched_at=max_fetched, patterns_version=version)
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
            "state_host": 0, "unnamed_host": 0, "by_country": {}, "by_outlet": {}, "articles": [], "error": None}


def build_export(conn: sqlite3.Connection, pat: Optional[Dict] = None) -> Dict:
    """The docs/data/hosted_travel.json structure: counts per class over every flagged article, by
    country and by outlet, and the articles newest first, capped at FLAGS_EXPORT_CAP."""
    pat = pat or patterns()
    rows = _article_rows(conn)
    outlet_names = {r["outlet_id"]: r["outlet"] for r in rows}
    by_outlet = _tally(rows, "outlet_id")
    for oid, v in by_outlet.items():
        v["name"] = outlet_names.get(oid) or oid
    return {
        "generated": store.utcnow(),
        "status": STATUS,
        "patterns_version": pat["version"],
        "languages": sorted(pat["languages"]),
        "articles_total": len(rows),
        "state_host": sum(1 for r in rows if r["klass"] == "state_host"),
        "unnamed_host": sum(1 for r in rows if r["klass"] == "unnamed_host"),
        "by_country": _tally(rows, "country"),
        "by_outlet": by_outlet,
        "articles": rows[:FLAGS_EXPORT_CAP],
    }


def empty_meta_block() -> Dict:
    return {"status": STATUS, "articles_total": 0, "state_host": 0, "unnamed_host": 0, "state_host_30d": 0,
            "unnamed_host_30d": 0, "countries": 0, "outlets": 0, "patterns_version": "", "languages": 0}


def meta_block(conn: sqlite3.Connection, pat: Optional[Dict] = None) -> Dict:
    """The hosted_travel block of meta.json. The 30 day counts are over articles discovered in the
    last 30 days, as elsewhere on the site. No clock value, so two exports of one database agree."""
    pat = pat or patterns()
    rows = _article_rows(conn)
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).replace(microsecond=0).isoformat()
    recent = [r for r in rows if (r["discovered_at"] or "") >= since]
    return {"status": STATUS, "articles_total": len(rows),
            "state_host": sum(1 for r in rows if r["klass"] == "state_host"),
            "unnamed_host": sum(1 for r in rows if r["klass"] == "unnamed_host"),
            "state_host_30d": sum(1 for r in recent if r["klass"] == "state_host"),
            "unnamed_host_30d": sum(1 for r in recent if r["klass"] == "unnamed_host"),
            "countries": len({r["country"] for r in rows}), "outlets": len({r["outlet_id"] for r in rows}),
            "patterns_version": pat["version"], "languages": len(pat["languages"])}


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
    head = ("Since 28 October 2026 the pipeline also looks in every stored article body for a disclosure that the "
            "reporting trip, the stay or a training programme behind the article was paid for or hosted by someone, "
            "such as \"the writer travelled as a guest of\", \"the trip was sponsored by\", \"à l'invitation de\", "
            "\"auf Einladung der\" or \"atas undangan\" (pipeline/hosted_travel.yaml, with patterns in English, French, "
            "Spanish, Portuguese, Italian, German, Arabic, Russian, Indonesian and Malay, Dutch, Turkish, Chinese, "
            "Ukrainian, Croatian and Serbian, Korean and Vietnamese). A line counts only when it is about the article's "
            "own reporting: it names the article or its author, opens with a disclosure label, or is a short line at the "
            "end of the body whose subject is not a politician, delegation or team; a quoted line never counts. The "
            "host is then checked. A disclosure whose host is a Chinese state entity, embassy, state media outlet, party "
            "body or Chinese government programme (the organisation list the rules use, plus forms such as \"the Chinese "
            "government\" and the China press centre programmes) is flagged as a state host; one that names no host "
            "(\"the organisers\", \"the host government\") is flagged apart as an unnamed host, on China coverage only, "
            "and is the weaker of the two; a host that is named and is not a Chinese state body gives no flag. A flag "
            "marks a disclosed hosted trip. It is not state origin and never becomes it: flags never enter the "
            "categories, never change a label or a count, and the ruleset and gate versions are unchanged by them. "
            "Most hosted trips are never disclosed, and a disclosure worded in a way the patterns do not know is "
            "missed, so the absence of a flag proves nothing. The counts are provisional until the flagged lines have "
            "been checked by hand.")
    if not h:
        return head
    return head + (" At export time %d articles carried a disclosure with a state host and %d with an unnamed host, "
                   "in %d countries and %d outlets (%d and %d of them discovered in the last thirty days; patterns "
                   "version %s)." % (h.get("state_host", 0), h.get("unnamed_host", 0), h.get("countries", 0),
                                     h.get("outlets", 0), h.get("state_host_30d", 0), h.get("unnamed_host_30d", 0),
                                     h.get("patterns_version") or "none"))


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
