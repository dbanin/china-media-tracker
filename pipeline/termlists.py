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
    text, and an acronym given in requires counts only when one of its own full terms appears;
  - one mention, one occurrence: an acronym printed in parentheses right after a full term of the
    same set ("Belt and Road Initiative (BRI)"), or a full term in parentheses right after its
    acronym ("BRI (Belt and Road Initiative)"), is the same mention and is not counted again;
  - context: a set compiled with context terms and a window counts a match only when one of the
    context terms appears within that many characters before or after it (the doctrine tracker
    asks for a China word near phrases that other governments use too).
"""
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def _mark_ranges() -> str:
    """Character class body of every combining mark (Unicode categories Mn, Mc and Me), as ranges."""
    cps = [c for c in range(0x110000) if unicodedata.category(chr(c)) in ("Mn", "Mc", "Me")]
    cps.sort()
    out, i = [], 0
    while i < len(cps):
        j = i
        while j + 1 < len(cps) and cps[j + 1] == cps[j] + 1:
            j += 1
        out.append("\\U%08x" % cps[i] + ("-\\U%08x" % cps[j] if j > i else ""))
        i = j + 1
    return "".join(out)


# Script aware word boundary, shared by the relevance gate, the theme counter and this matcher. In
# an abugida (Devanagari, Bengali and the other Indic scripts, Sinhala, Ge'ez) a vowel sign, a
# virama or a nasal sign is a combining mark, which \w does not cover, so "(?<!\w)" found a word
# start inside a word: चीन (China) matched inside प्राचीन (ancient), চীন inside প্রাচীন. A term
# therefore never starts right after a word character or a combining mark, nor after a zero width
# joiner or non-joiner that itself follows one, inside a word: چین (China) is not read in the
# Persian بلاک‌چین (blockchain). A joiner standing alone, as some feeds leave before a Latin word, does
# not hide the word. The end of a term still admits a following mark, because these languages
# inflect by suffix and a suffix that opens with a vowel sign is the same noun: চীনে (in China),
# চীনের (of China).
MARKS = _mark_ranges()
JOINERS = r"\u200c\u200d"   # regex escapes, not the characters themselves
WORD_START = r"(?<![\w%s])(?<![\w%s][%s])" % (MARKS, MARKS, JOINERS)
WORD_END = r"(?!\w)"
# A substring language's term in an Indic script (U+0900 to U+0DFF: Devanagari to Sinhala) does
# not start right after a combining mark either. Thai, Lao, Myanmar and Khmer are left matching
# anywhere: a Thai tone mark or vowel sign sits right before a following word's first consonant
# in "ที่จีน" (in China).
_INDIC_START = re.compile(r"[\u0900-\u0DFF]")
INDIC_START = r"(?<![%s])" % MARKS


def indic_start(term: str) -> bool:
    """Whether a substring term starts in an Indic script, and so carries INDIC_START."""
    return bool(_INDIC_START.match((term or "").replace("*", "").strip()))


def substring_alternations(patterns: Iterable[Tuple[str, str]]) -> List[str]:
    """Regex sources for (term, pattern source) pairs of a substring language, longest first: one
    alternation of the terms starting in an Indic script behind INDIC_START, one of the others."""
    patterns, out = list(patterns), []
    for indic in (True, False):
        group = sorted({src for term, src in patterns if indic_start(term) == indic}, key=lambda p: (-len(p), p))
        if group:
            out.append((INDIC_START if indic else "") + "(?:%s)" % "|".join(group))
    return out


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
                 requires: Optional[Dict[str, "TermSet"]] = None, context: Optional["TermSet"] = None,
                 context_window: int = 0):
        self.pats = pats
        self.excludes = excludes
        self.requires = requires or {}
        self.context = context
        self.context_window = int(context_window or 0)

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
        if self.context is not None and kept:
            w = self.context_window
            near = [(c[0], c[1]) for c in self.context._raw(text)]
            kept = [k for k in kept if any(s < k[1] + w and e > k[0] - w for s, e in near)]
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
        if any(k[3] for k in kept):
            kept = _drop_echoed_acronyms(text, kept)
        return kept

    def count(self, text: str) -> int:
        return len(self.find(text))


# "Belt and Road Initiative (BRI)": up to three more words after the full term, then an opening bracket.
_ECHO_BEFORE = re.compile(r"^(?:[\s\-]*[^\W_][\w\-]*){0,3}\s*[(\[（]\s*$", re.UNICODE)
_ECHO_CLOSE = re.compile(r"^\s*[)\]）]", re.UNICODE)
# "BRI (Belt and Road Initiative)": an opening bracket, the full term, up to three words and a closing one.
_ECHO_OPEN = re.compile(r"^\s*[(\[（]\s*$", re.UNICODE)
_ECHO_AFTER = re.compile(r"^(?:[\s\-]*[^\W_][\w\-]*){0,3}\s*[)\]）]", re.UNICODE)


def _drop_echoed_acronyms(text: str, kept: List[Match]) -> List[Match]:
    """kept less every acronym that only repeats a neighbouring full term of the same set in brackets."""
    out = []
    for i, k in enumerate(kept):
        if k[3]:
            prev = kept[i - 1] if i > 0 else None
            nxt = kept[i + 1] if i + 1 < len(kept) else None
            if prev is not None and not prev[3] and k[0] - prev[1] <= 60 and _ECHO_BEFORE.match(text[prev[1]:k[0]]) \
                    and _ECHO_CLOSE.match(text[k[1]:k[1] + 8]):
                continue
            if nxt is not None and not nxt[3] and nxt[0] - k[1] <= 8 and _ECHO_OPEN.match(text[k[1]:nxt[0]]) \
                    and _ECHO_AFTER.match(text[nxt[1]:nxt[1] + 120]):
                continue
        out.append(k)
    return out


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
    sub = substring_alternations((s, substring_pattern(s)) for s in sub_terms if s.replace("*", "").strip())
    pats = []
    if ci:
        pats.append((re.compile(WORD_START + r"(?:%s)" % "|".join(ci) + WORD_END, re.IGNORECASE | re.UNICODE), False))
    if cs:
        pats.append((re.compile(WORD_START + r"(?:%s)" % "|".join(cs) + WORD_END, re.UNICODE), True))
    for src in sub:
        pats.append((re.compile(src, re.IGNORECASE), False))
    return pats


def compile(terms_by_lang: Dict[str, Sequence[str]], lang: str, aliases: Optional[Dict[str, str]] = None,
            substring_langs: Iterable[str] = (), exclude: Optional[Dict[str, Sequence[str]]] = None,
            requires: Optional[Dict[str, Sequence[str]]] = None,
            context: Optional[Dict[str, Sequence[str]]] = None, context_window: int = 0) -> TermSet:
    """Compile a term list ({language: [terms]}, with all and en) for an article language.

    aliases maps a language to the one whose terms it uses (ms to id); substring_langs lists the
    languages matched anywhere. exclude is a list of the same shape: any match overlapping an
    excluded phrase is dropped. requires maps an acronym to the full terms of which one must appear
    for that acronym to count; every acronym already needs some full term of the set. context, a list
    of the same shape, with context_window above 0, keeps only the matches that have a context term
    within context_window characters before or after them."""
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
    ctx = None
    if context and context_window:
        ctx = compile(context, lang, None, substring_langs)
        if not ctx:
            ctx = TermSet([], [])   # no context term for the language: nothing can count
    return TermSet(pats, excludes, req, ctx, context_window if ctx is not None else 0)


def count_all(sets: Dict[str, TermSet], text: str) -> Dict[str, int]:
    """{set id: occurrences} for every set with at least one counted match."""
    out = {}
    for sid, ts in sets.items():
        n = ts.count(text)
        if n:
            out[sid] = n
    return out
