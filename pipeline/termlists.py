"""A generic multilingual term matcher that counts occurrences.

It copies the theme counter's matching (pipeline/themes.py, which is left untouched) so a term list
reads the same way in both places:
  - every language uses its own terms plus the all and en lists;
  - terms are matched on word boundaries, case insensitively, except all capital acronyms
    (themes._ACRONYM), which are case sensitive;
  - a trailing or inner asterisk matches any word characters, and a space matches any run of
    whitespace (themes._word_pattern);
  - in a substring language (words not separated by spaces, or carrying attached particles) the
    language's own terms match anywhere in the text. One addition: there a space in a term matches
    any whitespace or none, because spacing varies (Korean writes the same phrase spaced and unspaced).

It adds what the theme counter lacks:
  - occurrences, not presence: overlapping matches are resolved to the longest, leftmost ones, so
    "hegemonism and power politics" counts once even when "hegemonism" is a term too;
  - exclusions: a match that overlaps an excluded phrase (in the same term syntax) is dropped, so
    "BRI Liga 1" never counts as the Belt and Road;
  - the acronym rule: an acronym counts only when a full term of the same set appears in the same
    text, and an acronym given in requires counts only when one of its own full terms appears.
"""
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# The theme counter's acronym test and word pattern, copied so the two read terms the same way.
ACRONYM = re.compile(r"[A-Z0-9][A-Z0-9\-]{1,5}")


def word_pattern(term: str) -> str:
    """themes._word_pattern: an asterisk is any word characters, a space any whitespace."""
    return re.escape(term).replace(r"\*", r"\w*").replace(r"\ ", r"\s+")


def substring_pattern(term: str) -> str:
    """A substring language's term: matched anywhere, the asterisk dropped (as themes does), and a
    space matching any whitespace or none."""
    return re.escape(term.replace("*", "")).replace(r"\ ", r"\s*")


def is_acronym(term: str) -> bool:
    return bool(ACRONYM.fullmatch(term))


Match = Tuple[int, int, str, bool]   # start, end, the matched text, whether it is an acronym


class TermSet:
    """One set of terms compiled for one language. find(text) returns the counted matches,
    count(text) how many there are."""

    def __init__(self, pats: List[Tuple["re.Pattern", bool]], excludes: List["re.Pattern"],
                 requires: Optional[Dict[str, "TermSet"]] = None):
        self.pats = pats
        self.excludes = excludes
        self.requires = requires or {}

    def __bool__(self) -> bool:
        return bool(self.pats)

    def _raw(self, text: str) -> List[Match]:
        found = []
        for pat, acro in self.pats:
            for m in pat.finditer(text):
                if m.end() > m.start():
                    found.append((m.start(), m.end(), m.group(0), acro))
        # Longest leftmost first, then keep the matches that do not overlap one already kept.
        found.sort(key=lambda x: (x[0], -(x[1] - x[0])))
        kept, last_end = [], -1
        for f in found:
            if f[0] >= last_end:
                kept.append(f)
                last_end = f[1]
        return kept

    def find(self, text: str) -> List[Match]:
        if not text or not self.pats:
            return []
        kept = self._raw(text)
        if self.excludes and kept:
            spans = [(m.start(), m.end()) for ex in self.excludes for m in ex.finditer(text) if m.end() > m.start()]
            if spans:
                kept = [k for k in kept if not any(k[0] < e and s < k[1] for s, e in spans)]
        if any(k[3] for k in kept):
            full = any(not k[3] for k in kept)
            out = []
            for k in kept:
                if not k[3]:
                    out.append(k)
                    continue
                if not full:
                    continue
                req = self.requires.get(k[2])
                if req is not None and not req.find(text):
                    continue
                out.append(k)
            kept = out
        return kept

    def count(self, text: str) -> int:
        return len(self.find(text))


def _terms_for(terms_by_lang: Dict[str, Sequence[str]], lang: str, substring: bool) -> Tuple[List[str], List[str]]:
    """(word terms, substring terms) for a language, as themes._compiled assembles them."""
    word_terms = list(terms_by_lang.get("all") or [])
    if lang != "en":
        word_terms += list(terms_by_lang.get("en") or [])
    local = list(terms_by_lang.get(lang) or [])
    if substring:
        return word_terms, local
    return word_terms + local, []


def _patterns(word_terms: Iterable[str], sub_terms: Iterable[str]) -> List[Tuple["re.Pattern", bool]]:
    word_terms, sub_terms = list(word_terms), list(sub_terms)
    # Longer terms first, so an alternation prefers the longer of two terms starting at one place.
    ci = sorted({word_pattern(w) for w in word_terms if not is_acronym(w)}, key=lambda p: (-len(p), p))
    cs = sorted({word_pattern(w) for w in word_terms if is_acronym(w)}, key=lambda p: (-len(p), p))
    sub = sorted({substring_pattern(s) for s in sub_terms if s.replace("*", "").strip()}, key=lambda p: (-len(p), p))
    pats = []
    if ci:
        pats.append((re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(ci), re.IGNORECASE | re.UNICODE), False))
    if cs:
        pats.append((re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(cs), re.UNICODE), True))
    if sub:
        pats.append((re.compile("|".join(sub), re.IGNORECASE), False))
    return pats


def compile(terms_by_lang: Dict[str, Sequence[str]], lang: str, aliases: Optional[Dict[str, str]] = None,
            substring_langs: Iterable[str] = (), exclude: Optional[Dict[str, Sequence[str]]] = None,
            requires: Optional[Dict[str, Sequence[str]]] = None) -> TermSet:
    """Compile a term list ({language: [terms]}, with all and en) for an article language.

    aliases maps a language to the one whose terms it uses (ms to id); substring_langs lists the
    languages matched anywhere. exclude is a list of the same shape: any match overlapping an
    excluded phrase is dropped. requires maps an acronym to the full terms of which one must appear
    for that acronym to count; every acronym already needs some full term of the set."""
    lang = (aliases or {}).get(lang or "en", lang or "en")
    substring = lang in set(substring_langs or ())
    word_terms, sub_terms = _terms_for(terms_by_lang or {}, lang, substring)
    # A term that is an acronym is case sensitive, wherever it is listed (as in themes, where only the
    # word terms can be acronyms; a substring language's own acronyms are matched as words here).
    acr_sub = [s for s in sub_terms if is_acronym(s)]
    pats = _patterns(word_terms + acr_sub, [s for s in sub_terms if not is_acronym(s)])
    excludes = []
    if exclude:
        ew, es = _terms_for(exclude, lang, substring)
        excludes = [p for p, _ in _patterns(ew, es)]
    req = {}
    for acro, full in (requires or {}).items():
        req[acro] = compile({"all": list(full)}, lang, None, substring_langs)
    return TermSet(pats, excludes, req)


def count_all(sets: Dict[str, TermSet], text: str) -> Dict[str, int]:
    """{set id: occurrences} for every set with at least one counted match."""
    out = {}
    for sid, ts in sets.items():
        n = ts.count(text)
        if n:
            out[sid] = n
    return out
