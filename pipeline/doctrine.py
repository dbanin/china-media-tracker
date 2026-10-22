"""Doctrine term tracker (measure doctrine): how often China coverage uses the Chinese state's own set
phrases, by kind of article. Report only: it changes no label, count, denominator or outlet.

Each relevant article is tagged with the occurrences of every term family (pipeline/doctrine.yaml)
found in its title, its feed summary and its body, matched by pipeline/termlists.py. Tags are stored in
articles.doctrine as {"v": version, "b": 1 when the body was read, 0 when only the title and summary
were, "f": {family: occurrences}}. ensure() tags whatever is missing, from another version, or was
tagged without a body that has since arrived; a body-read tag is never replaced by a title-only tag of
the same version. It is resumable: it stops at the export's deadline, newest articles first, and the
next export carries on. With each tag it stores, in the table doctrine_snippets, up to SNIPPETS_KEPT
matches per family with about SNIPPET_WORDS words either side, so a hand check sample can be drawn on a
machine that does not hold the bodies (the owner's machine draws from the runner's database) without
leaning to the matches in headlines. The table holds short excerpts of article text: a public copy of
the database should leave it out.

doctrine.json gives, per day of record and country (distribution wires as WLD, exactly as build_daily
places theme counts), each family's [China coverage, state origin, unchecked state sourcing,
independent journalism, occurrences], and per day and country the tagged articles of each kind, all
of them and those using any distinctive family, so the site can show rates per 1,000 articles.
"""
import csv
import json
import random
import sqlite3
import time
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from pipeline import config, gate, measures, registry, store, termlists

MEASURE_ID = "doctrine"
DOCTRINE_PATH = config.ROOT / "pipeline" / "doctrine.yaml"
SAMPLE_MAX = 200
SNIPPET_WORDS = 30
SNIPPETS_KEPT = 5          # matches stored per article and family for the hand check
CHECK_PRECISION = 0.9      # a family is checked at this precision or more ...
CHECK_MIN_CODED = 15       # ... on at least this many coded matches
WITHHOLD_PRECISION = 0.8   # a family is withheld below this precision ...
WITHHOLD_MIN_CODED = 5     # ... once at least this many of its matches are coded
YES = {"yes", "y", "1", "true", "correct"}
NO = {"no", "n", "0", "false", "wrong"}
SKIP = {"skip", "unreadable", "unclear"}
PENDING = ("awaiting_llm", "llm_submitted")

# Every doctrine version and the day it took effect. A version change re-tags every stored article at
# the next export; a hand check made under another version no longer counts. Bumping version in
# doctrine.yaml needs an entry here; a test fails until it has one.
HISTORY = [
    {"version": "2026.10.1", "date": "2026-10-14",
     "change": "doctrine term tracker introduced: thirteen families of the Chinese state's set phrases, "
               "matched in the title, the feed summary and the body"},
    {"version": "2026.10.2", "date": "2026-10-17",
     "change": "multipolar world and people-to-people exchanges muted and counted only within 140 characters "
               "of a China word; the generic Spanish, French, Russian and Portuguese forms of people-to-people "
               "exchanges and power politics dropped; an acronym in brackets beside its full term counts once"},
]

# One character for one character, so offsets in the normalised text stay meaningful for snippets.
_NORMALISE = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
    "「": '"', "」": '"', "『": '"', "』": '"',
    # Romanian comma letters to the cedilla letters that older Romanian text and Turkish use, so both
    # Romanian spellings match and Turkish text is left as it is.
    "ș": "ş", "Ș": "Ş", "ț": "ţ", "Ț": "Ţ",
})


def normalise(text: Optional[str]) -> str:
    """NFC, straight quotation marks, one spelling of the Romanian letters: terms and text alike."""
    return unicodedata.normalize("NFC", text or "").translate(_NORMALISE)


@lru_cache(maxsize=None)
def load() -> Dict:
    with open(DOCTRINE_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def version() -> str:
    return str(load()["version"])


VERSION = version()


def changes() -> List[Dict]:
    return sorted((dict(h) for h in HISTORY), key=lambda h: h["date"])


def catalog() -> List[Dict]:
    """Ordered [{id, label, distinctive}]."""
    return [{"id": f["id"], "label": f["label"], "distinctive": bool(f.get("distinctive", True))}
            for f in load()["families"]]


def distinctive_ids() -> List[str]:
    return [f["id"] for f in catalog() if f["distinctive"]]


def languages() -> List[str]:
    """Languages with doctrine terms of their own, aliases included."""
    data = load()
    langs = {l for f in data["families"] for l in (f.get("terms") or {}) if l != "all"}
    langs |= {a for a, t in (data.get("language_aliases") or {}).items() if t in langs}
    return sorted(langs)


def _norm_lists(d: Optional[Dict]) -> Dict[str, List[str]]:
    return {lang: [normalise(str(t)) for t in (terms or [])] for lang, terms in (d or {}).items()}


@lru_cache(maxsize=None)
def compiled(language: str) -> Dict[str, termlists.TermSet]:
    """{family id: compiled terms} for an article language."""
    data = load()
    aliases = data.get("language_aliases") or {}
    subs = data.get("substring_languages") or []
    context = _norm_lists(data.get("china_context"))
    out = {}
    for f in data["families"]:
        req = {a: [normalise(t) for t in full] for a, full in (f.get("requires") or {}).items()}
        window = int(f.get("china_context") or 0)
        out[f["id"]] = termlists.compile(_norm_lists(f.get("terms")), language or "en", aliases, subs,
                                         exclude=_norm_lists(f.get("exclude")), requires=req,
                                         context=context if window else None, context_window=window)
    return out


def context_families() -> Dict[str, int]:
    """{family id: characters} for the families counted only beside a China word."""
    return {f["id"]: int(f["china_context"]) for f in load()["families"] if f.get("china_context")}


def text_of(title: Optional[str], summary: Optional[str], body: Optional[str]) -> str:
    """What is read, as the theme counter reads it: the title, the feed summary and the whole body."""
    return normalise("\n".join([title or "", gate.strip_html(summary or ""), body or ""]))


def tag(title: Optional[str], summary: Optional[str], body: Optional[str], language: str) -> Dict[str, int]:
    """{family: occurrences} for the families found; {} when none."""
    return termlists.count_all(compiled(language or "en"), text_of(title, summary, body))


def tag_with_snippets(title: Optional[str], summary: Optional[str], body: Optional[str],
                      language: str) -> Tuple[Dict[str, int], Dict[str, List[List[str]]]]:
    """({family: occurrences}, {family: [[matched term, snippet], ...]}): the tag and up to SNIPPETS_KEPT
    of each family's matches, spread over the article (the first, the last and evenly between)."""
    text = text_of(title, summary, body)
    counts, snips = {}, {}
    for fid, ts in compiled(language or "en").items():
        found = ts.find(text)
        if not found:
            continue
        counts[fid] = len(found)
        k = min(SNIPPETS_KEPT, len(found))
        picks = sorted({round(i * (len(found) - 1) / float(k - 1)) for i in range(k)}) if k > 1 else [0]
        snips[fid] = [[" ".join(found[i][2].split()), _snippet(text, found[i][0], found[i][1])] for i in picks]
    return counts, snips


SNIPPET_TABLE = """CREATE TABLE IF NOT EXISTS doctrine_snippets (
    article_id INTEGER NOT NULL,
    family TEXT NOT NULL,
    version TEXT NOT NULL,
    from_body INTEGER NOT NULL,
    matches TEXT NOT NULL,          -- JSON [[matched term, snippet], ...], at most SNIPPETS_KEPT
    PRIMARY KEY (article_id, family)
)"""


def ensure_snippet_table(conn: sqlite3.Connection) -> None:
    conn.execute(SNIPPET_TABLE)


def _has_snippet_table(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='doctrine_snippets'").fetchone() is not None


def stored_snippets(conn: sqlite3.Connection, article_id: int, family: str, v: Optional[str] = None) -> List[List[str]]:
    """The matches stored for an article and family under version v (the current one by default)."""
    if not _has_snippet_table(conn):
        return []
    r = conn.execute("SELECT matches FROM doctrine_snippets WHERE article_id=? AND family=? AND version=?",
                     (article_id, family, v or version())).fetchone()
    if r is None:
        return []
    try:
        m = json.loads(r[0])
    except (ValueError, TypeError):
        return []
    return [x for x in m if isinstance(x, list) and len(x) == 2] if isinstance(m, list) else []


def parse(stored: Optional[str]) -> Optional[Dict]:
    if not stored:
        return None
    try:
        d = json.loads(stored)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def _body(url_hash: Optional[str]) -> str:
    """The stored body, loaded as themes.ensure loads it, or "" when there is none."""
    if not url_hash:
        return ""
    try:
        return store.load_body(url_hash) or ""
    except (OSError, EOFError, ValueError):
        return ""


def ensure(conn: sqlite3.Connection, deadline: Optional[float] = None) -> Dict:
    """Tag every relevant article whose tag is missing, from another version, or title-only while its
    body now exists. Newest first; stops at deadline (a time.monotonic() value) and resumes next time.
    A body-read tag (b=1) is never replaced by a title-only one of the same version."""
    v = version()
    ensure_snippet_table(conn)
    rows = conn.execute("SELECT id, title, summary, language, url_hash, doctrine FROM articles "
                        "WHERE gate_relevant=1 ORDER BY id DESC").fetchall()
    tagged = with_body = retagged = 0
    stopped = False
    for r in rows:
        st = parse(r["doctrine"])
        current = st is not None and str(st.get("v")) == v
        has_file = bool(r["url_hash"]) and store.body_path(r["url_hash"]).exists()
        if current and (st.get("b") == 1 or not has_file):
            continue
        if deadline is not None and time.monotonic() >= deadline:
            stopped = True
            break
        body = _body(r["url_hash"]) if has_file else ""
        b = 1 if body else 0
        if current and not b:
            continue   # the body file is empty or unreadable: nothing new to read
        f, snips = tag_with_snippets(r["title"], r["summary"], body, r["language"])
        conn.execute("UPDATE articles SET doctrine=? WHERE id=?",
                     (json.dumps({"v": v, "b": b, "f": f}, sort_keys=True), r["id"]))
        conn.execute("DELETE FROM doctrine_snippets WHERE article_id=?", (r["id"],))
        conn.executemany("INSERT INTO doctrine_snippets(article_id, family, version, from_body, matches) "
                         "VALUES (?,?,?,?,?)",
                         [(r["id"], fid, v, b, json.dumps(m, ensure_ascii=False)) for fid, m in sorted(snips.items())])
        tagged += 1
        with_body += b
        retagged += 1 if current else 0
        if tagged % 500 == 0:
            conn.commit()
    # Snippets of articles that are gone or no longer relevant are dropped with them.
    conn.execute("DELETE FROM doctrine_snippets WHERE article_id NOT IN (SELECT id FROM articles WHERE gate_relevant=1)")
    conn.commit()
    return {"tagged": tagged, "with_body": with_body, "retagged_with_body": retagged, "complete": not stopped}


# ---------------------------------------------------------------------------
# Measure interface
# ---------------------------------------------------------------------------

def inputs_present(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM articles WHERE gate_relevant=1").fetchone()[0] or 0)


def compute(conn: sqlite3.Connection, ctx: Dict, deadline: float) -> Dict:
    return ensure(conn, deadline)


def _day(row) -> str:
    from pipeline import export   # the export's own day of record, so the days line up with daily files
    return export._day(row)


def _wire_ids(ctx: Optional[Dict]) -> set:
    outlets = (ctx or {}).get("outlets")
    return registry.distribution_wire_ids(outlets if outlets is not None else registry.load_outlets())


def coverage(conn: sqlite3.Connection) -> Dict:
    """How much of the relevant articles carries a tag of the current version, and how much of that
    was tagged without a body."""
    v = version()
    total = current = no_body = matched = 0
    for r in conn.execute("SELECT doctrine FROM articles WHERE gate_relevant=1"):
        total += 1
        st = parse(r["doctrine"])
        if st is None or str(st.get("v")) != v:
            continue
        current += 1
        no_body += 0 if st.get("b") == 1 else 1
        matched += 1 if st.get("f") else 0
    return {"articles": total, "tagged": current, "matched": matched,
            "tagged_share": round(current / total, 4) if total else None,
            "tagged_without_body": round(no_body / current, 4) if current else None}


def build_export(conn: sqlite3.Connection, ctx: Dict) -> Dict:
    v = version()
    wire_ids = _wire_ids(ctx)
    statuses = family_statuses()
    # The share using the vocabulary counts the distinctive families a hand check has not withheld.
    distinct = {f for f in distinctive_ids() if statuses.get(f, {}).get("status") != "withheld"}
    days: Dict[str, Dict[str, Dict[str, List[int]]]] = defaultdict(lambda: defaultdict(dict))
    totals: Dict[str, Dict[str, List[int]]] = defaultdict(dict)
    slot = {"A": 1, "B": 2, "C": 3}
    from pipeline import model_only, placements
    rows = conn.execute(
        """SELECT a.id, a.country, a.outlet_id, a.discovered_at, a.day_of_record, a.status, a.doctrine,
                  c.category AS m_cat, %s AS route, c.method
           FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1""" % model_only.route_sql("c"))
    shadow = placements.shadowed(conn)   # copies counted through another row, as in the daily files
    for r in rows:
        if r["id"] in shadow:
            continue
        pending = r["status"] in PENDING
        # An undetermined label (U, pipeline/model_only.py) is in no slot and not China coverage, as in the
        # daily files: it is in neither numerator nor denominator of any doctrine rate.
        cat = model_only.headline_category(r["m_cat"], r["route"], r["method"])
        if not (pending or cat in slot):
            continue   # only China coverage counts, as for the theme counts
        st = parse(r["doctrine"])
        if st is None or str(st.get("v")) != v:
            continue   # not tagged under this version yet: in neither numerator nor denominator
        country = registry.WORLDWIDE if r["outlet_id"] in wire_ids else r["country"]
        d = _day(r)
        fams = {k: int(n) for k, n in (st.get("f") or {}).items() if n}
        t = totals[d].setdefault(country, [0] * 8)
        any_d = any(k in distinct for k in fams)
        t[0] += 1
        t[4] += 1 if any_d else 0
        if cat in slot:
            t[slot[cat]] += 1
            t[4 + slot[cat]] += 1 if any_d else 0
        for fid, n in fams.items():
            cell = days[d][country].setdefault(fid, [0, 0, 0, 0, 0])
            cell[0] += 1
            if cat in slot:
                cell[slot[cat]] += 1
            cell[4] += n
    fams_out = [dict(f, status=statuses.get(f["id"], {}).get("status", "provisional")) for f in catalog()]
    cov = coverage(conn)
    return {"version": v, "families": fams_out, "languages": languages(),
            "tagged_share": cov["tagged_share"], "tagged_without_body": cov["tagged_without_body"],
            "days": {d: {c: dict(fs) for c, fs in sorted(per.items())} for d, per in sorted(days.items())},
            "totals": {d: dict(sorted(per.items())) for d, per in sorted(totals.items())},
            "totals_fields": ["china", "state_origin", "unchecked", "independent",
                              "china_any", "state_origin_any", "unchecked_any", "independent_any"],
            "cell_fields": ["china", "state_origin", "unchecked", "independent", "occurrences"],
            "changes": changes()}


def meta_summary(conn: sqlite3.Connection, ctx: Dict) -> Dict:
    cov = coverage(conn)
    statuses = family_statuses()
    return {"version": version(), "families": len(catalog()), "languages": len(languages()),
            "tagged_share": cov["tagged_share"], "tagged_without_body": cov["tagged_without_body"],
            "matched_articles": cov["matched"],
            "family_status": {fid: s.get("status", "provisional") for fid, s in statuses.items()}}


def _join(labels: List[str]) -> str:
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def methodology_text(block: Dict) -> str:
    fams = catalog()
    muted = [f["label"] for f in fams if not f["distinctive"]]
    ctx_ids = context_families()
    ctx = [f["label"] for f in fams if f["id"] in ctx_ids]
    window = max(ctx_ids.values()) if ctx_ids else 0
    if muted:
        muted_text = ("%s %s common well beyond Chinese state usage, so %s shown muted and left out of the share "
                      "of articles using the vocabulary." % (_join(muted), "are" if len(muted) > 1 else "is",
                                                             "they are" if len(muted) > 1 else "it is"))
    else:
        muted_text = "No family is common enough beyond Chinese state usage to be muted."
    if ctx:
        muted_text += (" %s %s also the stated policy of other governments, India, Russia, Vietnam and the BRICS "
                       "among them, so %s counted only when a China word (China, Chinese, Beijing, Xi Jinping and "
                       "their renderings in the article's language) appears within %d characters of the phrase." % (
                           _join(ctx), "are" if len(ctx) > 1 else "is", "they are" if len(ctx) > 1 else "it is",
                           window))
    paras = [
        "The doctrine term tracker counts how often China coverage uses the Chinese state's own set phrases, "
        "such as community with a shared future, true multilateralism, Chinese modernization and the "
        "one-China principle. There are %d families of terms. Each family is matched in English and in the "
        "renderings Chinese state media and the Chinese foreign ministry publish in the outlet's language; a "
        "language is left out of a family when no such rendering was found, or when the rendering is ordinary "
        "prose in that language, and every language is also matched on the English terms. The terms are read "
        "in the headline, the feed summary and the whole body, and every occurrence is counted, though an acronym "
        "in brackets beside its full term is the same mention and counts once with it. A match is a keyword match, not a reading of the article: "
        "it says the phrase is there, not who used it or whether the article endorses it." % len(fams),
        "Some matches are excluded by rule. A bare win-win is ordinary English and never counts, only "
        "win-win cooperation; hegemonism counts but the generic hegemony does not; the one-China principle "
        "is kept apart from the one China policy other governments state. An acronym counts only when a full "
        "term of the same family appears in the same article, and GSI only beside Global Security Initiative "
        "itself, so BRI in Indonesian football or banking news is not the Belt and Road. " + muted_text,
        "Rates are per 1,000 China articles of each kind (state origin, unchecked state sourcing, independent "
        "journalism) on the same days, counting only articles already tagged under the current term list. "
        "Where a body has not been read the headline and summary alone are matched, which finds fewer "
        "phrases; the article is tagged again once its body arrives. A hand check codes up to %d matches, "
        "spread evenly over the families first and over each family's languages within it, each shown with "
        "about %d words either side, stored when the article is tagged so the sample does not depend on where "
        "it is drawn. A family is checked at a precision of at least %.1f on at least %d coded matches and "
        "withheld below %.1f once %d are coded, and a withheld family shows no figure; the measure as a whole "
        "is checked when the matches of the families still shown reach %.1f together." % (
            SAMPLE_MAX, SNIPPET_WORDS, CHECK_PRECISION, CHECK_MIN_CODED, WITHHOLD_PRECISION, WITHHOLD_MIN_CODED,
            CHECK_PRECISION),
    ]
    return "\n\n".join(paras)


# ---------------------------------------------------------------------------
# Hand check
# ---------------------------------------------------------------------------

def _alias(lang: Optional[str]) -> str:
    return (load().get("language_aliases") or {}).get(lang or "en", lang or "en")


def _snippet(text: str, start: int, end: int, words: int = SNIPPET_WORDS) -> str:
    before = text[:start].split()[-words:]
    after = text[end:].split()[:words]
    return " ".join(before + ["[[" + " ".join(text[start:end].split()) + "]]"] + after)


def _outlet_names() -> Dict[str, str]:
    try:
        return {o["id"]: o.get("name") or o["id"] for o in registry.load_outlets()}
    except Exception:  # noqa: BLE001  a sample without outlet names is still a sample
        return {}


def _interleave(queues: Dict[str, List[int]]) -> List[Tuple[str, int]]:
    """One family's candidates, one language at a time in turn: [(language, article id)]."""
    out, langs = [], sorted(queues)
    qs = {l: list(queues[l]) for l in langs}
    while any(qs.values()):
        for l in langs:
            if qs[l]:
                out.append((l, qs[l].pop()))
    return out


def draw_sample(conn: sqlite3.Connection, n: Optional[int] = SAMPLE_MAX, seed=None,
                out_dir: Optional[Path] = None) -> Tuple[Path, Path]:
    """Up to SAMPLE_MAX matches, spread evenly over the families first: each family gives one match in
    turn until n are drawn, so a family with few matches in few languages still reaches the coded
    matches its status needs, and within a family the languages take turns. Each row shows the matched
    term and about 30 words either side, from the matches stored at tag time (doctrine_snippets), so the
    draw does not depend on the machine holding the bodies; a match is reproduced from the stored text
    only for a tag made before snippets were kept. The coder marks correct yes or no. Refuses to write
    an empty sample."""
    n = max(0, min(int(SAMPLE_MAX if n is None else n), SAMPLE_MAX))
    v = version()
    labels = {f["id"]: f["label"] for f in catalog()}
    order = [f["id"] for f in catalog()]
    per_fam: Dict[str, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
    arts = {}
    for r in conn.execute("SELECT id, url, outlet_id, language, title, summary, url_hash, doctrine FROM articles "
                          "WHERE gate_relevant=1 AND doctrine IS NOT NULL ORDER BY id"):
        st = parse(r["doctrine"])
        if st is None or str(st.get("v")) != v or not st.get("f"):
            continue
        arts[r["id"]] = r
        for fid in st["f"]:
            per_fam[fid][_alias(r["language"])].append(r["id"])
    rng = random.Random(str(seed))
    queues: Dict[str, List[Tuple[str, int]]] = {}
    for fid in sorted(per_fam, key=lambda f: (order.index(f) if f in order else len(order), f)):
        langs = {}
        for lang in sorted(per_fam[fid]):
            ids = sorted(per_fam[fid][lang])
            rng.shuffle(ids)
            langs[lang] = ids
        queues[fid] = _interleave(langs)
        queues[fid].reverse()   # popped from the end: the interleaved order first
    names = _outlet_names()
    rows, key_rows = [], {}
    fam_order = list(queues)
    stored_n = 0
    while len(rows) < n and any(queues.values()):
        for fid in fam_order:
            if len(rows) >= n:
                break
            q = queues[fid]
            while q:
                lang, aid = q.pop()
                r = arts[aid]
                stored = stored_snippets(conn, aid, fid, v)
                if stored:
                    term, snippet = rng.choice(stored)
                    stored_n += 1
                else:
                    text = text_of(r["title"], r["summary"], _body(r["url_hash"]))
                    found = compiled(r["language"] or "en")[fid].find(text)
                    if not found:
                        continue   # an old tag that no longer reproduces (a body went missing): the next one
                    s_, e_, term, _ = rng.choice(found)
                    term, snippet = " ".join(term.split()), _snippet(text, s_, e_)
                sid = "d-%03d" % (len(rows) + 1)
                rows.append({"sample_id": sid, "article_id": aid, "url": r["url"],
                             "outlet": names.get(r["outlet_id"], r["outlet_id"]), "language": r["language"],
                             "family": fid, "family_label": labels.get(fid, fid), "matched_term": term,
                             "snippet": snippet, "correct": "", "coder": "", "note": ""})
                key_rows[sid] = {"article_id": aid, "family": fid, "language": lang, "term": term,
                                 "stored": bool(stored)}
                break
    if not rows:
        raise ValueError("no doctrine matches of version %s to draw from" % v)
    key = {"rows": key_rows, "families": [f["id"] for f in catalog()], "strata": "family first, then language",
           "from_stored_snippets": stored_n,
           "rule": {"check_precision": CHECK_PRECISION, "check_min_coded": CHECK_MIN_CODED,
                    "withhold_precision": WITHHOLD_PRECISION, "withhold_min_coded": WITHHOLD_MIN_CODED}}
    fields = ["sample_id", "article_id", "url", "outlet", "language", "family", "family_label", "matched_term",
              "snippet", "correct", "coder", "note"]
    return measures.write_sample(Path(out_dir or measures.REVIEWS_DIR), MEASURE_ID, rows, fields, key,
                                 version=VERSION, seed=seed)


def family_status(precision: Optional[float], coded: int) -> str:
    if precision is not None and coded >= CHECK_MIN_CODED and precision >= CHECK_PRECISION:
        return "checked"
    if precision is not None and coded >= WITHHOLD_MIN_CODED and precision < WITHHOLD_PRECISION:
        return "withheld"
    return "provisional"


def score_check(rows: List[Dict], key: Dict) -> Dict:
    """Precision per family: correct among the coded yes or no matches. A family is checked at 0.9 or
    more on at least 15 coded matches and withheld below 0.8 (on at least 5). The measure's value is the
    pooled precision of the families not withheld; it passes at 0.9 once every row is coded. Family
    statuses are given only for a complete sample."""
    key_rows = (key or {}).get("rows") or {}
    n = len(key_rows) or len(rows)
    per = defaultdict(lambda: [0, 0])
    coded = 0
    for r in rows:
        sid = (r.get("sample_id") or "").strip()
        fam = (key_rows.get(sid) or {}).get("family") or (r.get("family") or "").strip()
        val = (r.get("correct") or "").strip().lower()
        if val in YES:
            per[fam][0] += 1
            per[fam][1] += 1
            coded += 1
        elif val in NO:
            per[fam][1] += 1
            coded += 1
        elif val in SKIP:
            coded += 1
    complete = n > 0 and coded >= n
    families = {}
    for fam, (yes, judged) in sorted(per.items()):
        p = yes / judged if judged else None
        families[fam] = {"precision": round(p, 4) if p is not None else None, "coded": judged,
                         "status": family_status(p, judged) if complete else "provisional"}
    shown = [(per[f][0], per[f][1]) for f in per if family_status(
        per[f][0] / per[f][1] if per[f][1] else None, per[f][1]) != "withheld"]
    yes_s, judged_s = sum(y for y, _ in shown), sum(j for _, j in shown)
    value = round(yes_s / judged_s, 4) if judged_s else None
    passed = (value is not None and value >= CHECK_PRECISION) if complete else None
    return measures.check_result("precision", value, CHECK_PRECISION, n, coded, passed=passed, complete=complete,
                                 families=families)


def family_statuses(reviews_dir: Optional[Path] = None) -> Dict[str, Dict]:
    """{family: {status, precision, coded}} from the newest hand check sample of this version, or {}."""
    try:
        for c, k in measures.sample_files(MEASURE_ID, reviews_dir):
            with open(k, encoding="utf-8") as fh:
                key = json.load(fh)
            if key.get("measure_version") is not None and str(key["measure_version"]) != VERSION:
                continue
            with open(c, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            return score_check(rows, key).get("families") or {}
    except Exception:  # noqa: BLE001  a broken sample leaves every family provisional
        return {}
    return {}
