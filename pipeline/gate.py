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
    csens = set(data.pop("capitalised", []) or [])
    home = {k: set(v) for k, v in (data.pop("home_terms", {}) or {}).items()}
    # Longest phrase first, so "Malaysian Chinese Association" goes before "Malaysian Chinese".
    phrases = {k: [_phrase_pattern(p) for p in sorted(v, key=len, reverse=True)]
               for k, v in (data.pop("home_phrases", {}) or {}).items()}
    return data, nowb, weak, regex, home, phrases, csens


def weak_terms() -> set:
    return _load()[2]


def supported_languages() -> set:
    """Languages with a keyword list of their own. Every other language is matched on the
    international and English terms only."""
    return {k for k in _load()[0] if k != "all"}


# A term is Latin script when it contains a Latin letter. Latin script terms keep a word
# boundary and their case rule in every language, including the languages that do without
# boundaries for their own script: without this, "Xi" matched inside "Taxi", "BRI" inside
# "British" and "PLA" inside "display" in Thai, Japanese, Lao, Khmer and Burmese items.
_LATIN_TERM_RE = re.compile(r"[A-Za-zÀ-ɏ]")
# The boundary for a Latin term is a Latin letter or digit, not any word character, so the term
# still matches where a native script runs straight into it, as Japanese and Korean write it
# ("中国のBRI構想").
_LATIN_EDGE = r"[A-Za-z0-9À-ɏ]"


def _capitalised_source(term: str) -> str:
    """Pattern source for a term whose first letter must be capitalised. The rest stays case
    blind, so KINA in an all-capitals headline matches and "kina", a cinema, does not."""
    out = re.escape(term[0].upper())
    for ch in term[1:]:
        lower, upper = ch.lower(), ch.upper()
        out += "[%s%s]" % (re.escape(lower), re.escape(upper)) if lower != upper else re.escape(ch)
    return out


@lru_cache(maxsize=None)
def _patterns(language: str):
    data, nowb, weak, regex, _, _, csens = _load()
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
        # Short terms (<=3 chars) are case sensitive to avoid matching "bri" inside prose in
        # languages where BRI is not an acronym. Terms listed under "capitalised" carry their
        # case rule in the pattern source instead, because a lowercase homograph makes them
        # useless while an all-capitals headline must still match.
        esc = _capitalised_source(term) if term in csens else re.escape(term)
        flags = 0 if (len(term) <= 3 or term in csens) else re.IGNORECASE
        if language in nowb and not _LATIN_TERM_RE.search(term):
            # Native script term in a language that writes without spaces: substring matching,
            # which is the only way to reach a noun carrying a particle or a counter.
            pat = re.compile(esc, flags | re.UNICODE)
        elif language in nowb:
            pat = re.compile(r"(?<!%s)%s(?!%s)" % (_LATIN_EDGE, esc, _LATIN_EDGE), flags | re.UNICODE)
        else:
            # Unicode aware word boundary. \b is unreliable for non-Latin scripts, so use
            # lookarounds on word characters.
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
    weak, home = _load()[2], _load()[4]
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
    Used by the residual relevance rule, which needs more than a yes or no.

    One stretch of text counts once. The term list overlaps itself, so counting each term
    separately turned a single phrase into several findings: "Xi Jinping" was both "Xi" and
    "Xi Jinping", "Belt and Road Initiative" was two more, and a headline naming one Chinese
    person cleared the "a term in the headline and at least two occurrences" branch on its own.
    Matches are taken longest first and a match wholly inside one already counted is dropped."""
    weak, home = _load()[2], _load()[4]
    ignore = home.get(country or "", set()) | weak
    title = mask_home_phrases(title or "", country)
    body_text = "%s\n%s" % (title, mask_home_phrases(body or "", country))
    found = []
    for term, pat in _patterns(language):
        if term in ignore:
            continue
        for m in pat.finditer(body_text):
            if m.end() > m.start():
                found.append((m.start(), m.end(), term))
    # Longest first, then leftmost, so the containing phrase claims the span before its parts.
    found.sort(key=lambda f: (f[0] - f[1], f[0], f[2]))
    accepted = []
    for start, end, term in found:
        if any(s <= start and end <= e for s, e, _ in accepted):
            continue
        accepted.append((start, end, term))
    title_end = len(title)
    return (len({t for _, _, t in accepted}), len(accepted),
            any(s < title_end for s, _, _ in accepted))
