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
  - in a sports section (sports_sections, read from the item's URL path), one
    China term found only in the summary does not admit the item on its own
  - the summary is read without the boilerplate its feed repeats in most of its
    items (pipeline/boilerplate.py), when the caller passes it
  - an item judged on its title alone (title_only: a news sitemap entry) is also
    admitted by the "title_only" terms, Chinese cities, companies, fairs and
    state media brands that a headline names where a summary would say China

An item whose title or summary already carries a state origin signature
(pipeline/signatures.yaml) passes whatever its keywords say. Short wire and
release items about one Chinese province often name no China term, and the
gate must never discard the material the instrument exists to find.
"""
import re
from functools import lru_cache
from typing import List, Optional, Tuple
from urllib.parse import urlparse

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
    data.pop("sports_sections", None)   # read by _sports_sections; not a language list
    data.pop("title_only", None)        # read by _title_only_patterns; not a language list
    return data, nowb, weak, regex, home, phrases, csens


@lru_cache(maxsize=None)
def _sports_sections():
    """(path segments, terms that admit alone) of the sports_sections block in keywords.yaml."""
    with open(config.KEYWORDS_PATH, "r", encoding="utf-8") as fh:
        block = (yaml.safe_load(fh) or {}).get("sports_sections") or {}
    return ({s.lower() for s in block.get("segments") or []}, set(block.get("admit_alone") or []))


SPORTS_SECTION_MARK = "sports_section:uncorroborated"


def sports_section(url: Optional[str]) -> bool:
    """Whether the URL path has a sports section segment. The last segment is the article's own
    slug and is not read, so a headline slug that happens to contain "sport" does not count."""
    if not url:
        return False
    path = urlparse(url).path.lower()
    segments = [s for s in path.split("/") if s][:-1]
    return any(s in _sports_sections()[0] for s in segments)


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


def _term_pattern(term: str, language: str, nowb: set, csens: set):
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
    return pat


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
        compiled.append((term, _term_pattern(term, language, nowb, csens)))
    for label, rx in regex.items():
        compiled.append((label, re.compile(rx, re.UNICODE)))
    return compiled


TITLE_ONLY_MARK = "title_only:"


@lru_cache(maxsize=None)
def _title_only_patterns(language: str):
    """Compiled terms of the title_only block in keywords.yaml for this language: its "all" list,
    the language's own list and, as for the main lists, the English one."""
    with open(config.KEYWORDS_PATH, "r", encoding="utf-8") as fh:
        block = (yaml.safe_load(fh) or {}).get("title_only") or {}
    _, nowb, _, _, _, _, csens = _load()
    terms = list(block.get("all") or []) + list(block.get(language) or [])
    if language != "en":
        terms += list(block.get("en") or [])
    out, seen = [], set()
    for term in terms:
        if term not in seen:
            seen.add(term)
            out.append((term, _term_pattern(term, language, nowb, csens)))
    return out


def title_only_terms(title: str, language: str, country: Optional[str] = None) -> List[str]:
    """The title_only terms in a title, each as "title_only:<term>". Home terms and home phrases
    are set aside as in check."""
    ignore = _load()[4].get(country or "", set())
    text = mask_home_phrases(title or "", country)
    return [TITLE_ONLY_MARK + term for term, pat in _title_only_patterns(language)
            if term not in ignore and pat.search(text)]


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


def check(title: str, summary: str, language: str, country: Optional[str] = None,
          url: Optional[str] = None, feed_boilerplate=None, title_only: bool = False) -> Tuple[bool, List[str]]:
    """Return (relevant, matched_terms). matched_terms lists every term that hit, weak ones
    included, so the audit trail shows why an item passed or failed. An item let through by a
    state origin signature carries "state_origin_signature:<id>" among its terms.

    With the item's url, an item in a sports section (sports_sections in keywords.yaml) whose only
    China term is one found in the summary alone is not admitted on it: it needs a term in the
    headline, two distinct terms, or a state media name. Such an item carries
    "sports_section:uncorroborated" among its terms. A state origin signature still admits it.

    feed_boilerplate, the shingles the item's feed repeats in most of its summaries
    (pipeline/boilerplate.py, FeedBoilerplate.shingles), are removed from the summary before the
    terms are matched. The state origin signature check reads the whole summary.

    title_only, for an item that carries a title and nothing else (a news sitemap entry,
    pipeline/news_sitemap.py): the title_only terms in keywords.yaml also admit it when its title
    names one, each recorded as "title_only:<term>". A headline names a Chinese city, company,
    event or state media brand where a summary would have said China."""
    plain_summary = strip_html(summary or "")
    kw_summary = plain_summary
    if feed_boilerplate:
        from pipeline import boilerplate
        kw_summary = boilerplate.strip(plain_summary, feed_boilerplate)
    text = mask_home_phrases("%s\n%s" % (title or "", kw_summary), country)
    weak, home = _load()[2], _load()[4]
    ignore = home.get(country or "", set())
    hits = []
    for term, pat in _patterns(language):
        if term in ignore:
            continue
        if pat.search(text):
            hits.append(term)
    strong = [h for h in hits if h not in weak]
    if strong and sports_section(url) and not set(strong) & _sports_sections()[1]:
        distinct, _, in_title = count(title, kw_summary, language, country)
        if distinct < 2 and not in_title:
            strong = []
            hits = hits + [SPORTS_SECTION_MARK]
    if title_only:
        # Read after the sports section rule: a title_only term is in the headline by definition.
        extra = title_only_terms(title, language, country)
        hits, strong = hits + extra, strong + extra
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
