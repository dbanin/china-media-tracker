"""Cheap relevance gate on title and summary. Loose by design.

Rules, all from pipeline/keywords.yaml:
  - terms in "all" and in the outlet's language list, plus the English list
    for every language, are matched on Unicode word boundaries; terms of
    three characters or fewer are case sensitive (BRI, PRC, Xi)
  - languages under no_word_boundaries use substring matching
  - terms under "weak" count only when a non-weak term also matched
  - "regex" entries are applied verbatim and case sensitively
  - "home_terms" are ignored for outlets based in the territory they name
  - "home_phrases" are removed before matching for outlets in the country they
    are listed under, so a Singapore or Malaysia outlet's news about its own
    Chinese community ("Chinese New Year", "Malaysian Chinese") does not read
    as coverage of China

An item whose title or summary already carries a state origin signature
(pipeline/signatures.yaml) passes whatever its keywords say. Short wire and
release items about one Chinese province often name no China term, and the
gate must never discard the material the instrument exists to find.
"""
import re
from functools import lru_cache
from typing import List, Optional, Tuple

import yaml

from pipeline import config


def _phrase_pattern(phrase: str):
    return re.compile(r"(?<!\w)%s(?!\w)" % r"\s+".join(re.escape(w) for w in phrase.split()), re.IGNORECASE | re.UNICODE)


@lru_cache(maxsize=None)
def _load():
    with open(config.KEYWORDS_PATH, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    nowb = set(data.pop("no_word_boundaries", []) or [])
    weak = set(data.pop("weak", []) or [])
    regex = data.pop("regex", {}) or {}
    home = {k: set(v) for k, v in (data.pop("home_terms", {}) or {}).items()}
    # Longest phrase first, so "Malaysian Chinese Association" goes before "Malaysian Chinese".
    phrases = {k: [_phrase_pattern(p) for p in sorted(v, key=len, reverse=True)]
               for k, v in (data.pop("home_phrases", {}) or {}).items()}
    return data, nowb, weak, regex, home, phrases


def weak_terms() -> set:
    return _load()[2]


def supported_languages() -> set:
    """Languages with a keyword list of their own. Every other language is matched on the
    international and English terms only."""
    return {k for k in _load()[0] if k != "all"}


@lru_cache(maxsize=None)
def _patterns(language: str):
    data, nowb, weak, regex, _, _ = _load()
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


def mask_home_phrases(text: str, country: Optional[str]) -> str:
    """Blank out the phrases that, for an outlet in this country, describe its own community."""
    for pat in _load()[5].get(country or "", ()):
        text = pat.sub(" ", text)
    return text


def state_origin_signature(title: str, summary: str) -> Optional[str]:
    """The id of the signature that makes the title and summary state origin on their own, or None.
    Only a decision the rules would take without the model counts. A hint or a lone weak signature
    does not, or every commercial press release on a newswire would pass."""
    from pipeline import classify_rules  # classify_rules imports this module
    res = classify_rules.match_signatures(title or "", summary or "", None)
    if res["decision"] != "A":
        return None
    strong = [m["id"] for m in res["matches"] if m["strength"] == "strong"]
    return (strong or [m["id"] for m in res["matches"]])[0]


def check(title: str, summary: str, language: str, country: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Return (relevant, matched_terms). matched_terms lists every term that hit, weak ones
    included, so the audit trail shows why an item passed or failed. An item let through by a
    state origin signature carries "state_origin_signature:<id>" among its terms."""
    plain_summary = strip_html(summary or "")
    text = mask_home_phrases("%s\n%s" % (title or "", plain_summary), country)
    _, _, weak, _, home, _ = _load()
    ignore = home.get(country or "", set())
    hits = []
    for term, pat in _patterns(language):
        if term in ignore:
            continue
        if pat.search(text):
            hits.append(term)
    strong = [h for h in hits if h not in weak]
    if not strong:
        sig = state_origin_signature(title, plain_summary)
        if sig:
            return True, sorted(set(hits + ["state_origin_signature:%s" % sig]))
    return (len(strong) > 0, sorted(set(hits)))


def count(title: str, body: str, language: str, country: Optional[str] = None) -> Tuple[int, int, bool]:
    """(distinct non-weak terms, total occurrences of non-weak terms, non-weak term in the title).
    Used by the residual relevance rule, which needs more than a yes or no."""
    _, _, weak, _, home, _ = _load()
    ignore = home.get(country or "", set()) | weak
    title = mask_home_phrases(title or "", country)
    body_text = "%s\n%s" % (title, mask_home_phrases(body or "", country))
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
            if pat.search(title):
                in_title = True
    return distinct, total, in_title
