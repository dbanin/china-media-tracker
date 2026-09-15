"""Theme counter. Tags every relevant article with the themes it concerns.

Deterministic and multilingual, so the counts are reproducible without a model: an
article carries every theme whose terms (pipeline/themes.yaml) appear in its title, its
feed summary, or anywhere in its body. Until themes version 2026.09.4 only the first 800
characters of the body were read, which favored whatever an article names early,
typically diplomacy and place names. An article with no theme is Other. Tags are stored
on the article with the themes version, and an export re-tags any article whose stored
version differs, so a term change applies to history.
"""
import json
import re
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


def themes_of(stored: Optional[str]) -> List[str]:
    if not stored:
        return ["other"]
    try:
        return json.loads(stored).get("t") or ["other"]
    except (ValueError, AttributeError):
        return ["other"]


def ensure(conn) -> int:
    """Tag every relevant article whose stored tags are missing or from another version."""
    v = version()
    rows = conn.execute("SELECT id, title, summary, language, url_hash, themes FROM articles WHERE gate_relevant=1").fetchall()
    n = 0
    for r in rows:
        if r["themes"]:
            try:
                if json.loads(r["themes"]).get("v") == v:
                    continue
            except ValueError:
                pass
        body = store.load_body(r["url_hash"]) or ""
        conn.execute("UPDATE articles SET themes=? WHERE id=?",
                     (json.dumps({"v": v, "t": tag(r["title"], r["summary"], body, r["language"])}), r["id"]))
        n += 1
        if n % 1000 == 0:
            conn.commit()
    conn.commit()
    return n
