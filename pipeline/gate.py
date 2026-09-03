"""Cheap relevance gate on title and summary. Loose by design."""
import re
from functools import lru_cache
from typing import List, Tuple

import yaml

from pipeline import config


@lru_cache(maxsize=None)
def _load():
    with open(config.KEYWORDS_PATH, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    nowb = set(data.pop("no_word_boundaries", []) or [])
    return data, nowb


@lru_cache(maxsize=None)
def _patterns(language: str):
    data, nowb = _load()
    terms = list(data.get("all", [])) + list(data.get(language, []))
    # Always include English terms too, because English proper nouns leak into every language.
    if language != "en":
        terms += list(data.get("en", []))
    compiled = []
    for term in terms:
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
    return compiled


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def check(title: str, summary: str, language: str) -> Tuple[bool, List[str]]:
    """Return (relevant, matched_terms)."""
    text = "%s\n%s" % (title or "", strip_html(summary or ""))
    hits = []
    for term, pat in _patterns(language):
        if pat.search(text):
            hits.append(term)
    return (len(hits) > 0, sorted(set(hits)))
