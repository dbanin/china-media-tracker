"""Theme counter. Tags every relevant article with the themes it concerns.

Deterministic and multilingual, so the counts are reproducible without a model: an
article carries every theme whose terms (pipeline/themes.yaml) appear in its title, its
feed summary, or anywhere in its body. Until themes version 2026.09.4 only the first 800
characters of the body were read, which favored whatever an article names early,
typically diplomacy and place names. An article with no theme is Other. Tags are stored
on the article as {"v": themes version, "t": [theme ids], "b": 1 when the body was read, 0
when only the title and summary were}; a tag stored before 2026-10-14 has no "b" and counts
as 0. An export re-tags any article whose stored version differs, so a term change applies
to history, and any article tagged without a body that has since been fetched, so an
article tagged before its body arrived does not stay tagged from its title and summary for
good. A body-read tag is never replaced by a title-only tag of the same version.
"""
import json
import re
import time
from functools import lru_cache
from typing import Dict, List, Optional

import yaml

from pipeline import config, gate, store

THEMES_PATH = config.ROOT / "pipeline" / "themes.yaml"
_ACRONYM = re.compile(r"[A-Z0-9][A-Z0-9\-]{1,5}")


@lru_cache(maxsize=None)
def load() -> Dict:
    with open(THEMES_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def version() -> str:
    return load()["version"]


# Every themes version and the day it took effect, so the theme timeline can mark the day the
# method changed, the way ruleset and gate changes are marked. A version change re-tags every
# stored article (ensure), so each day's counts follow the current version; the mark says when
# figures read or downloaded before it were made under different terms or a different reading.
# Bumping version in themes.yaml needs an entry here; a test fails until it has one.
HISTORY = [
    {"version": "2026.09.3", "date": "2026-09-14",
     "change": "theme counter introduced; terms read in the title, the feed summary and the first 800 characters of the body"},
    {"version": "2026.09.4", "date": "2026-09-15",
     "change": "terms read anywhere in the body instead of the first 800 characters; every article re-tagged"},
]

# The part of an export that re-reads title-only tags whose body has since arrived (not the tagging of
# missing tags or tags of another version, which always completes) stops after this many seconds and
# carries on, newest articles first, at the next export. Measured on 2026-10-14: about 2.9 ms an article
# with a body, loading included (6,371 in 18.6 seconds), so the first pass over tags stored without "b",
# up to about 89,000 articles with a body, takes about four minutes or more and may span two exports.
# Afterwards only the bodies that arrived since the last export are read.
BODY_RETAG_SECONDS = 180.0


def changes() -> List[Dict]:
    """The themes version history, oldest first, for meta.themes_changes."""
    return sorted((dict(h) for h in HISTORY), key=lambda h: h["date"])


def catalog() -> List[Dict]:
    """Ordered [{id, label, short}], Other last."""
    data = load()
    return [{"id": t["id"], "label": t["label"], "short": t["short"]} for t in data["themes"]] + [dict(data["other"])]


def _word_pattern(term: str) -> str:
    esc = re.escape(term).replace(r"\*", r"\w*").replace(r"\ ", r"\s+")
    return esc


@lru_cache(maxsize=None)
def _compiled(language: str):
    data = load()
    lang = data.get("language_aliases", {}).get(language, language)
    substring = lang in set(data.get("substring_languages", []))
    out = []
    for t in data["themes"]:
        terms = t["terms"]
        word_terms = list(terms.get("all", []))
        if lang != "en":
            word_terms += terms.get("en", [])
        local = terms.get(lang, [])
        sub_terms = []
        if substring:
            sub_terms = local
        else:
            word_terms += local
        ci = [_word_pattern(w) for w in word_terms if not _ACRONYM.fullmatch(w)]
        cs = [_word_pattern(w) for w in word_terms if _ACRONYM.fullmatch(w)]
        pats = []
        if ci:
            pats.append(re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(ci), re.IGNORECASE | re.UNICODE))
        if cs:
            pats.append(re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(cs), re.UNICODE))
        if sub_terms:
            pats.append(re.compile("|".join(re.escape(s.replace("*", "")) for s in sub_terms), re.IGNORECASE))
        out.append((t["id"], pats))
    return out


def languages() -> set:
    """Languages with theme terms of their own, aliases included."""
    data = load()
    langs = {l for t in data["themes"] for l in t["terms"] if l != "all"}
    return langs | {a for a, target in (data.get("language_aliases") or {}).items() if target in langs}


def tag(title: Optional[str], summary: Optional[str], body: Optional[str], language: str) -> List[str]:
    text = "\n".join([title or "", gate.strip_html(summary or ""), body or ""])
    found = [tid for tid, pats in _compiled(language or "en") if any(p.search(text) for p in pats)]
    return found or ["other"]


def parse(stored: Optional[str]) -> Optional[Dict]:
    """The stored tag as a dict, or None when there is none or it cannot be read."""
    if not stored:
        return None
    try:
        d = json.loads(stored)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def themes_of(stored: Optional[str]) -> List[str]:
    """The theme ids of a stored tag, in either format ({"v","t"} or {"v","t","b"}); Other when none."""
    d = parse(stored)
    return (d.get("t") if d else None) or ["other"]


def _body(url_hash: Optional[str]) -> str:
    """The stored body, or "" when there is none or it cannot be read."""
    if not url_hash:
        return ""
    try:
        return store.load_body(url_hash) or ""
    except (OSError, EOFError, ValueError):
        return ""


def ensure(conn, body_deadline: Optional[float] = None, stats: Optional[Dict] = None) -> int:
    """Tag every relevant article whose stored tag is missing, unreadable or from another version, and
    re-tag every one tagged without a body (b 0, or no b) whose body file now exists. Returns how many
    articles were written.

    Missing and other-version tags are always all made: an untagged article would count as Other. The
    re-reading of title-only tags stops at body_deadline (a time.monotonic() value; by default
    BODY_RETAG_SECONDS from the start) and resumes, newest first, at the next export. Whether a body
    exists is checked by its path, and a body is loaded only for an article that is tagged. A body-read
    tag (b 1) is never replaced by a title-only one of the same version. stats, when given, is filled
    with tagged, with_body, retagged_with_body, body_retags_left and complete."""
    v = version()
    if body_deadline is None:
        body_deadline = time.monotonic() + BODY_RETAG_SECONDS
    rows = conn.execute("SELECT id, title, summary, language, url_hash, themes FROM articles "
                        "WHERE gate_relevant=1 ORDER BY id DESC").fetchall()
    tagged = with_body = retagged = left = 0
    for r in rows:
        st = parse(r["themes"])
        current = st is not None and str(st.get("v")) == v
        if current:
            if st.get("b") == 1:
                continue
            if not (r["url_hash"] and store.body_path(r["url_hash"]).exists()):
                continue   # still no body: nothing more to read
            if time.monotonic() >= body_deadline:
                left += 1
                continue   # the next export carries on
        body = _body(r["url_hash"])
        b = 1 if body else 0
        if current and not b:
            continue   # the body file is empty or unreadable: nothing new to read
        conn.execute("UPDATE articles SET themes=? WHERE id=?",
                     (json.dumps({"v": v, "t": tag(r["title"], r["summary"], body, r["language"]), "b": b}), r["id"]))
        tagged += 1
        with_body += b
        retagged += 1 if current else 0
        if tagged % 1000 == 0:
            conn.commit()
    conn.commit()
    if stats is not None:
        stats.update({"tagged": tagged, "with_body": with_body, "retagged_with_body": retagged,
                      "body_retags_left": left, "complete": left == 0})
    return tagged
