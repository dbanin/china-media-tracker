"""Cheap relevance gate on title and summary. Loose by design.

Rules, all from pipeline/keywords.yaml:
  - terms in "all" and in the outlet's language list, plus the English list
    for every language, are matched on Unicode word boundaries; terms of
    three characters or fewer are case sensitive (BRI, PRC, Xi)
  - languages under no_word_boundaries use substring matching
  - terms under "weak" count only when a non-weak term also matched
  - "regex" entries are applied verbatim and case sensitively
  - "home_terms" are ignored for outlets based in the territory they name
"""
import re
from functools import lru_cache
from typing import List, Optional, Tuple

import yaml

from pipeline import config


@lru_cache(maxsize=None)
def _load():
    with open(config.KEYWORDS_PATH, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    nowb = set(data.pop("no_word_boundaries", []) or [])
    weak = set(data.pop("weak", []) or [])
    regex = data.pop("regex", {}) or {}
    home = {k: set(v) for k, v in (data.pop("home_terms", {}) or {}).items()}
    return data, nowb, weak, regex, home


def weak_terms() -> set:
    return _load()[2]


@lru_cache(maxsize=None)
def _patterns(language: str):
    data, nowb, weak, regex, _ = _load()
    terms = list(data.get("all", [])) + list(data.get(language, []))
    # Always include English terms too, because English proper nouns leak into every language.
    if language != "en":
        terms += list(data.get("en", []))
    terms += sorted(weak)
    compiled = []
    seen = set()
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        esc = re.escape(term)
        if language in nowb:
            pat = re.compile(esc, re.IGNORECASE)
        else:
            # Unicode aware word boundary. \b is unreliable for non-Latin scripts, so use
            # lookarounds on word characters. Short terms (<=3 chars) are case sensitive
            # to avoid matching "bri" inside prose in languages where BRI is not an acronym.
            flags = 0 if len(term) <= 3 else re.IGNORECASE
            pat = re.compile(r"(?<!\w)%s(?!\w)" % esc, flags | re.UNICODE)
        compiled.append((term, pat))
    for label, rx in regex.items():
        compiled.append((label, re.compile(rx, re.UNICODE)))
    return compiled


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def check(title: str, summary: str, language: str, country: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Return (relevant, matched_terms). matched_terms lists every term that hit, weak ones
    included, so the audit trail shows why an item passed or failed."""
    text = "%s\n%s" % (title or "", strip_html(summary or ""))
    _, _, weak, _, home = _load()
    ignore = home.get(country or "", set())
    hits = []
    for term, pat in _patterns(language):
        if term in ignore:
            continue
        if pat.search(text):
            hits.append(term)
    strong = [h for h in hits if h not in weak]
    return (len(strong) > 0, sorted(set(hits)))


def count(title: str, body: str, language: str, country: Optional[str] = None) -> Tuple[int, int, bool]:
    """(distinct non-weak terms, total occurrences of non-weak terms, non-weak term in the title).
    Used by the residual relevance rule, which needs more than a yes or no."""
    _, _, weak, _, home = _load()
    ignore = home.get(country or "", set()) | weak
    body_text = "%s\n%s" % (title or "", body or "")
    distinct = 0
    total = 0
    in_title = False
    for term, pat in _patterns(language):
        if term in ignore:
            continue
        n = len(pat.findall(body_text))
        if n:
            distinct += 1
            total += n
            if pat.search(title or ""):
                in_title = True
    return distinct, total, in_title
