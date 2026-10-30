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

Since themes version 2026.10.1 a theme may also carry exclusions and proximity rules (exclude and
near in themes.yaml), read by the shared matcher (pipeline/termlists.py): a term overlapping an
excluded phrase does not count, an acronym counts only beside a full term of its own list, and a
near rule counts a term only with a term of each of its context lists within window characters.
The eleven themes from before keep their plain terms and their matching, so their tags do not
change. A version change re-tags history newest first within the export's time budget
(BODY_RETAG_SECONDS); a tag of the older version keeps counting as it stands until it is re-read,
and progress() says how far the re-reading has come.
"""
import json
import re
import time
from functools import lru_cache
from typing import Dict, List, Optional

import yaml

from pipeline import config, gate, store, termlists

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
    # Three themes added for the owner's request of 2026-10-28, crossed with outlet leaning on the site.
    # The eleven earlier themes are unchanged; history is re-tagged newest first within the export budget.
    {"version": "2026.10.1", "date": "2026-10-28",
     "change": "three themes added: leaving the Belt and Road, Chinese overcapacity and the EU and China trade war; the earlier themes unchanged; history re-tagged newest first over the next exports",
     "added": ["bri_withdrawal", "overcapacity", "eu_china_trade"]},
    # The three themes' terms corrected after every hit of 2026.10.1 in the snapshot and the runner's audit files
    # was read (overcapacity 281 of 324 right, the EU and China trade war 226 of 234, leaving the Belt and Road 4
    # of 5). changed lists the themes whose counts are partial until the re-tagging is complete, as for added.
    {"version": "2026.10.2", "date": "2026-10-29",
     "change": "the three new themes' terms corrected: overcapacity no longer counted for Volkswagen's own plants, China's own anti-dumping cases or other countries' dumping cases; the EU and China trade war also read in tariffs, quotas, safeguards and anti-dumping cases beside an EU name and a China word; the eleven earlier themes unchanged; history re-tagged newest first over the next exports",
     "changed": ["overcapacity", "eu_china_trade", "bri_withdrawal"]},
]

# The part of an export that re-reads title-only tags whose body has since arrived (not the tagging of
# missing tags or tags of another version, which always completes) stops after this many seconds and
# carries on, newest articles first, at the next export. Measured on 2026-10-14: about 2.9 ms an article
# with a body, loading included (6,371 in 18.6 seconds), so the first pass over tags stored without "b",
# up to about 89,000 articles with a body, takes about four minutes or more and may span two exports.
# Afterwards only the bodies that arrived since the last export are read.
# Since themes version 2026.10.1 the same budget also bounds the re-tagging of tags stored under an older
# version, which used to run to the end in one export. Measured on 2026-10-28 on a copy of the database
# (98,095 relevant articles, all on 2026.09.4, 7,199 bodies on file locally): tagging an article with a
# body takes 4.2 ms under 2026.09.4 and 6.8 ms under 2026.10.1 on the owner's machine, about 1 ms with
# the title and summary alone. Scaled to the 2.9 ms measured above, about 4.6 ms an article on the runner,
# so re-tagging 98,000 articles takes about seven to eight minutes: three exports of 180 seconds, about a
# day and a half at two exports a day, rather than one export over its budget. The old tag keeps counting
# as it stands until it is re-read, so a version that adds themes leaves the earlier themes' counts as
# they were and fills the new ones in, newest articles first.
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
        terms = t.get("terms") or {}
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
            pats.append(re.compile(termlists.WORD_START + r"(?:%s)" % "|".join(ci) + termlists.WORD_END,
                                   re.IGNORECASE | re.UNICODE))
        if cs:
            pats.append(re.compile(termlists.WORD_START + r"(?:%s)" % "|".join(cs) + termlists.WORD_END, re.UNICODE))
        # A term in an Indic script does not start right after a combining mark (termlists.INDIC_START):
        # चीन (China) is not read inside प्राचीन (ancient).
        for src in termlists.substring_alternations((s, re.escape(s.replace("*", ""))) for s in sub_terms):
            pats.append(re.compile(src, re.IGNORECASE))
        if t.get("exclude") or t.get("near"):
            out.append((t["id"], _ruled_matcher(t, lang, data)))
        else:
            out.append((t["id"], _plain_matcher(pats)))
    return out


def _plain_matcher(pats):
    """A theme of plain terms (the eleven themes before 2026.10.1): any term anywhere in the text."""
    return lambda text: any(p.search(text) for p in pats)


def _ruled_matcher(theme: Dict, lang: str, data: Dict):
    """A theme with exclusions or proximity rules, read by the shared matcher (pipeline/termlists.py).

    terms count anywhere unless they overlap a phrase under exclude; an acronym among them counts only
    when a full term of the same list is in the text (BRI beside Belt and Road, never Bank BRI alone).
    Each near rule {window, terms, context: [lists], exclude, avoid} counts one of its terms, outside
    the theme's and the rule's exclusions, only when every context list has a term within window
    characters before or after it and no avoid term is that close. A context or avoid term counts on
    its own, acronyms included (EU); the theme's exclusions apply to the context lists as well (the
    Spanish EU, which in Mexico's press is Estados Unidos)."""
    aliases = data.get("language_aliases") or {}
    subs = data.get("substring_languages") or []
    exclude = theme.get("exclude") or {}
    direct = termlists.compile(theme.get("terms") or {}, lang, aliases, subs, exclude=exclude or None)
    rules = []
    for rule in theme.get("near") or []:
        excl = {k: list(exclude.get(k) or []) + list((rule.get("exclude") or {}).get(k) or [])
                for k in set(exclude) | set(rule.get("exclude") or {})}
        main = termlists.compile(rule["terms"], lang, aliases, subs, exclude=excl or None)
        ctxs = [termlists.compile(c, lang, aliases, subs, exclude=exclude or None) for c in rule["context"]]
        avoid = termlists.compile(rule["avoid"], lang, aliases, subs) if rule.get("avoid") else None
        if main and all(ctxs):   # an empty context list: the avoid list alone decides
            rules.append((main, ctxs, avoid or None, int(rule["window"])))

    def present(ts, text: str) -> bool:
        # A cheap search before find, which lists every match: most texts carry no term at all.
        return any(p.search(text) for p, _ in ts.pats)

    def match(text: str) -> bool:
        if direct and present(direct, text) and direct.find(text):
            return True
        for main, ctxs, avoid, w in rules:
            if not present(main, text):
                continue
            hits = main.find(text)
            if not hits:
                continue
            if any(all(_near(c, text, h, w) for c in ctxs) and not (avoid is not None and _near(avoid, text, h, w))
                   for h in hits):
                return True
        return False
    return match


def _near(ts, text: str, hit, window: int) -> bool:
    """Whether a term of ts (acronyms on their own, no exclusions) lies within window characters before or
    after hit. Only the stretch around the hit is searched; a lookbehind still sees the text before it, and
    the margin keeps a term that runs past the window whole."""
    lo, hi = hit[0] - window, hit[1] + window
    pos, end = max(0, lo - 200), min(len(text), hi + 200)
    for pat, _acro in ts.pats:
        for m in pat.finditer(text, pos, end):
            if m.end() > m.start() and m.start() < hi and m.end() > lo:
                if ts.excludes and any(x.start() < m.end() and m.start() < x.end()
                                       for ex in ts.excludes for x in ex.finditer(text, pos, end)):
                    continue
                return True
    return False


def _term_lists(theme: Dict):
    yield theme.get("terms") or {}
    for rule in theme.get("near") or []:
        yield rule.get("terms") or {}
        for c in rule.get("context") or []:
            yield c


def languages() -> set:
    """Languages with theme terms of their own, aliases included."""
    data = load()
    langs = {l for t in data["themes"] for terms in _term_lists(t) for l in terms if l != "all"}
    return langs | {a for a, target in (data.get("language_aliases") or {}).items() if target in langs}


def tag(title: Optional[str], summary: Optional[str], body: Optional[str], language: str) -> List[str]:
    text = "\n".join([title or "", gate.strip_html(summary or ""), body or ""])
    found = [tid for tid, match in _compiled(language or "en") if match(text)]
    return found or ["other"]


def cross_themes() -> List[str]:
    """The themes the site crosses with outlet leaning (themes.yaml leaning_cross), in catalog order."""
    data = load()
    want = set(data.get("leaning_cross") or [])
    return [t["id"] for t in data["themes"] if t["id"] in want]


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

    Missing and unreadable tags are always all made: an untagged article would count as Other. The
    re-tagging of tags from another version and the re-reading of title-only tags stop at body_deadline
    (a time.monotonic() value; by default BODY_RETAG_SECONDS from the start) and resume, newest first,
    at the next export; until then a tag of another version counts as it stands. Whether a body exists
    is checked by its path, and a body is loaded only for an article that is tagged. A body-read tag (b
    1) is never replaced by a title-only one of the same version. stats, when given, is filled with
    tagged, with_body, retagged_with_body, retagged_version, body_retags_left, version_retags_left and
    complete."""
    v = version()
    if body_deadline is None:
        body_deadline = time.monotonic() + BODY_RETAG_SECONDS
    rows = conn.execute("SELECT id, title, summary, language, url_hash, themes FROM articles "
                        "WHERE gate_relevant=1 ORDER BY id DESC").fetchall()
    tagged = with_body = retagged = left = version_retagged = version_left = 0
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
        elif st is not None and time.monotonic() >= body_deadline:
            version_left += 1
            continue   # a tag of another version counts as it stands until the next export re-reads it
        version_retagged += 1 if (st is not None and not current) else 0
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
                      "retagged_version": version_retagged, "body_retags_left": left,
                      "version_retags_left": version_left, "complete": left == 0 and version_left == 0})
    return tagged


def progress(conn) -> Dict:
    """How far the current themes version has come over the relevant articles: relevant, current (tagged
    under this version), complete, the day the version took effect, and the themes it added and the themes
    whose terms it changed. Until complete is true a theme it added counts only the current tags, and a theme
    it changed counts the older tags as they were made (under the version before) beside the current ones."""
    v = version()
    rel = cur = 0
    for (stored,) in conn.execute("SELECT themes FROM articles WHERE gate_relevant=1"):
        rel += 1
        st = parse(stored)
        cur += 1 if (st is not None and str(st.get("v")) == v) else 0
    last = next((h for h in reversed(HISTORY) if h["version"] == v), {})
    return {"version": v, "since": last.get("date"), "added": list(last.get("added") or []),
            "changed": list(last.get("changed") or []), "relevant": rel, "current": cur, "complete": cur == rel}


def cross_counts(rows, theme_ids: Optional[List[str]] = None) -> Dict[str, Dict]:
    """Per outlet, the China coverage behind the leaning crossing: {outlet id: {"china": articles, "A":
    state origin among them, "cur": articles tagged under the current themes version, "t": {theme id:
    [articles with the theme, state origin among them]}}}.

    rows are export.article_rows (copies another row stands for left out, undetermined labels in no
    category). China coverage is A + B + C: state origin, unchecked state sourcing and independent
    journalism. Candidates still waiting for the model carry no category yet and are left out (until
    2026-10-29 they were counted). Outlets without any are left out."""
    want = list(theme_ids if theme_ids is not None else cross_themes())
    v = version()
    out: Dict[str, Dict] = {}
    for r in rows:
        cat = r["m_cat"]
        if cat not in ("A", "B", "C"):
            continue
        o = out.get(r["outlet_id"])
        if o is None:
            o = out[r["outlet_id"]] = {"china": 0, "A": 0, "cur": 0, "t": {t: [0, 0] for t in want}}
        a = 1 if cat == "A" else 0
        o["china"] += 1
        o["A"] += a
        st = parse(r["themes"])
        o["cur"] += 1 if (st is not None and str(st.get("v")) == v) else 0
        tags = themes_of(r["themes"])
        for t in want:
            if t in tags:
                o["t"][t][0] += 1
                o["t"][t][1] += a
    return out
