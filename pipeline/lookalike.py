"""Lookalikes: articles whose headline, feed summary or body nearly repeats a state origin item.

Agency copy republished without its credit line names no credit, no state entity and no official
source, so no signature or trigger in pipeline/signatures.yaml can see it. What gives it away is that
another outlet ran the same text with the credit: the headline, the feed summary or the body nearly
repeats an article already labelled state origin. Two places use the comparison:

  - the recall audit (pipeline/recall_audit.py, lookalikes) counts how many articles the rules
    labelled independent journalism or not relevant although they nearly repeat a state origin item;
  - the rules stage (pipeline/classify_rules.py, classify_article) sends such an article to the model
    as a state origin candidate instead of labelling it (RouteIndex below), and records the match in
    its llm_trigger, so the model decides whether it is that state text with no substantive alteration.

The measures, shared by both:
  - headline: Jaccard similarity of the headline word sets (words of three letters or more, or
    character bigrams for scripts written without spaces), at least TITLE_JACCARD over at least
    TITLE_MIN_TOKENS words, within one language;
  - feed summary: the share of the summary's five word shingles found in one state origin item's
    summary, at least SUMMARY_CONTAINMENT over at least SUMMARY_MIN_SHINGLES shingles, both summaries
    read without the boilerplate their feed repeats in most of its items (pipeline/boilerplate.py);
  - body: the recall audit compares stored body text (BODY_CONTAINMENT); the rules stage compares the
    MinHash fingerprints pipeline/body_dups.py keeps for every fetched body, at that module's own
    JACCARD_THRESHOLD, and never a body body_dups marked as page template text.
The rules stage adds two conditions the audit does not need: the state origin item is from a
different outlet, and it appeared between WINDOW_BEFORE_DAYS before and WINDOW_AFTER_DAYS after
the article: by publication time when both have a credible one (published_ts), else by discovery.
"""
import datetime as dt
import heapq
import re
import sqlite3
from collections import Counter, defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# Lookalike thresholds: Jaccard similarity of headline word sets (at least TITLE_MIN_TOKENS words),
# or containment of the feed summary's five word shingles in one state origin item's summary.
TITLE_JACCARD = 0.6
TITLE_MIN_TOKENS = 5
SUMMARY_CONTAINMENT = 0.5
SUMMARY_MIN_SHINGLES = 15
BODY_CONTAINMENT = 0.3
BODY_MIN_SHINGLES = 30
SHINGLE = 5
# The rules stage's window: the state origin item appeared at most this many days before the
# article, or at most this many days after it (the credited copy can reach its feed later), by
# publication or by discovery as set out below the two values.
# Evidence (2026-10-12 database, judged by hand): of the recall audit's 17 lookalikes whose credited
# item was discovered 2.9 to 20.5 days after the uncredited one, the 13 from another outlet that are
# the same item lie at +2.9 to +9.4 days (press releases Antara and Laotian Times rerun a week or more
# after the wire, Xinhua copy run again days later); the two different stories from another outlet sit
# at +7.8 (independent coverage of the same news) and +12.0 (a later story with the same headline
# pattern); the other two are same outlet feed boilerplate the outlet rule already drops. Across the
# 132 pairs of state origin items from different outlets with matching headlines, every second copy
# came within 10.2 days of the first. Eleven days either side keeps all of these and drops the +12.0
# pair. Past -11 the audit has two more pairs: a press release rerun at -14.4 and an advertorial at
# -20.0 whose two copies were published a day apart (one outlet's copy was discovered late), too few
# and too irregular to widen for.
WINDOW_BEFORE_DAYS = 11
WINDOW_AFTER_DAYS = 11
# The window is measured between publication times when both items have a credible one, because an
# item found late (an outlet's first read of its feed, a page reached days after it ran) carries its
# discovery away from its twin's. A published time is credible (published_ts) when it is not after
# the item's discovery, not before config.COLLECTION_START (the rule store.backfill_day applies to a
# backfill read's items), and not more than PUBLISHED_MAX_LAG_DAYS before discovery unless a backfill
# read brought the item in (articles.day_of_record is set). Otherwise both items are compared by
# discovery, as before. Evidence (2026-10-12 database): of 75,425 labelled articles with a published
# time inside those first two bounds, 99.9 percent were discovered within 12 days of it, and the
# lags up to 12 days include whole first reads (France 24 English found on 2026-09-15 items it
# published from 2026-09-03); the 15 past 14 days are sports pages, a credit union page and an
# advertorial, dates too far from the observation to stand in for it.
PUBLISHED_MAX_LAG_DAYS = 14

_WORD = re.compile(r"\w+", re.UNICODE)
_DENSE = re.compile(r"[฀-๿぀-ヿ㐀-鿿가-힯]")


def tokens(text: str) -> List[str]:
    """Words, or character bigrams for scripts written without spaces."""
    text = (text or "").lower()
    if text and len(_DENSE.findall(text)) > 0.3 * len(text):
        text = re.sub(r"\s+", "", text)
        return [text[i:i + 2] for i in range(len(text) - 1)]
    return _WORD.findall(text)


def title_set(text: str) -> frozenset:
    return frozenset(t for t in tokens(text) if len(t) > 2 or _DENSE.search(t))


def shingles(text: str, k: int = SHINGLE) -> set:
    t = tokens(text)
    return {" ".join(t[i:i + k]) for i in range(max(0, len(t) - k + 1))}


class _Index:
    """Inverted index from a feature to the state origin items that carry it. `allow`, when given,
    is a predicate on an item's key: items it rejects are never returned. Among equally similar items
    the one with the lowest `rank` (a function of the key, such as tie_rank) wins, then the lowest
    key. Features are strings, whose set order changes with the per process hash seed, so no result
    may depend on the order the overlaps were counted in."""

    def __init__(self, rank: Optional[Callable] = None):
        self.post = defaultdict(set)
        self.sizes = {}
        self.rank = rank

    def _order(self, key):
        return (self.rank(key), key) if self.rank is not None else key

    def add(self, key, feats):
        self.sizes[key] = len(feats)
        for f in feats:
            self.post[f].add(key)

    def _overlaps(self, feats, allow: Optional[Callable] = None) -> Counter:
        c = Counter()
        for f in feats:
            for k in self.post.get(f, ()):
                c[k] += 1
        if allow is not None:
            c = Counter({k: n for k, n in c.items() if allow(k)})
        return c

    def best(self, feats, allow: Optional[Callable] = None) -> Optional[Tuple[object, int]]:
        c = self._overlaps(feats, allow)
        return min(c.items(), key=lambda kv: (-kv[1], self._order(kv[0]))) if c else None

    def best_jaccard(self, feats, top: int = 5, allow: Optional[Callable] = None) -> Optional[Tuple[object, float]]:
        c = self._overlaps(feats, allow)
        if not c:
            return None
        # The `top` items sharing the most features, and every item tied with the last of them.
        floor = heapq.nlargest(top, c.values())[-1]
        best = None
        for k, inter in c.items():
            if inter < floor:
                continue
            j = inter / float(len(feats) + self.sizes[k] - inter)
            if best is None or j > best[1] or (j == best[1] and self._order(k) < self._order(best[0])):
                best = (k, j)
        return best


# ---------------------------------------------------------------------------
# The rules stage's route
# ---------------------------------------------------------------------------

def _ts(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(microsecond=0).isoformat()


def _get(row, key, default=None):
    try:
        v = row[key]
    except (IndexError, KeyError):
        return default
    return default if v is None else v


def published_ts(row) -> Optional[float]:
    """An item's publication time when it is credible, else None: readable, not after its discovery,
    on or after config.COLLECTION_START, and at most PUBLISHED_MAX_LAG_DAYS before its discovery
    unless a backfill read brought it in (day_of_record set, store.backfill_day)."""
    from pipeline import config
    p = _ts((_get(row, "published_at") or "").strip())
    d = _ts(_get(row, "discovered_at"))
    if p is None or d is None or p > d or _iso(p)[:10] < config.COLLECTION_START:
        return None
    if d - p > PUBLISHED_MAX_LAG_DAYS * 86400 and not _get(row, "day_of_record"):
        return None
    return p


def tie_rank(row) -> float:
    """Where an item stands among equally similar counterparts: its credible publication time
    (published_ts), else its discovery time, else last. The earliest copy is the one credited; an
    _Index breaks a remaining tie by the lower article id. A publication date without a time of day
    counts as the end of that day or the discovery, whichever is earlier: read as midnight it would
    put a copy that gives only a date (CNA's paid releases) ahead of the timed original it reran."""
    p = published_ts(row)
    d = _ts(_get(row, "discovered_at"))
    if p is not None:
        if "T" not in (_get(row, "published_at") or "").strip() and d is not None:
            return min(p + 86400 - 1, d)
        return p
    return d if d is not None else float("inf")


def _span(rows: Iterable, before: float, after: float) -> Optional[Tuple[float, float]]:
    """The times another item must fall between to pair with any of `rows` (by publication or by
    discovery), `before` days before the earliest to `after` days after the latest; None if none."""
    times = []
    for r in rows:
        times += [t for t in (_ts(_get(r, "discovered_at")), published_ts(r)) if t is not None]
    if not times:
        return None
    return min(times) - before * 86400, max(times) + after * 86400


def _in_span(item, span: Tuple[float, float]) -> bool:
    """Whether the item's discovery or credible publication time lies in span."""
    return any(t is not None and span[0] <= t <= span[1]
               for t in (_ts(_get(item, "discovered_at")), published_ts(item)))


def _span_sql(span: Tuple[float, float]) -> Tuple[str, Tuple[str, ...]]:
    """A WHERE clause (on articles a) that keeps every item _in_span can keep: discovered in the span
    or up to PUBLISHED_MAX_LAG_DAYS after it, or brought in by a backfill read on a day in it."""
    lo, hi = _iso(span[0] - 1), _iso(span[1] + PUBLISHED_MAX_LAG_DAYS * 86400 + 1)
    return ("((a.discovered_at >= ? AND a.discovered_at <= ?) OR (a.day_of_record >= ? AND a.day_of_record <= ?))",
            (lo, hi, lo[:10], _iso(span[1] + 1)[:10]))


class RouteIndex:
    """Current state origin items, indexed by headline words (per language), feed summary shingles
    and body fingerprint bands, for the rules stage. Built once per stage over the window that covers
    every article the stage will judge; match() then costs a few dictionary lookups per article."""

    def __init__(self, refs: Iterable[Dict] = (), sigs: Optional[Dict[int, Tuple[int, ...]]] = None,
                 boiler=None):
        self.refs: Dict[int, Dict] = {}
        self.titles = defaultdict(lambda: _Index(self._rank))
        self.summ = _Index(self._rank)
        self.bands = defaultdict(set)
        self.sigs: Dict[int, Tuple[int, ...]] = {}
        # Feed summaries are compared without their feed's boilerplate (pipeline/boilerplate.py,
        # a FeedBoilerplate); None compares them whole.
        self.boiler = boiler
        for r in refs:
            self.add(r, (sigs or {}).get(r["article_id"]))

    def __len__(self):
        return len(self.refs)

    def add(self, ref: Dict, sig: Optional[Tuple[int, ...]] = None) -> None:
        """Index one state origin item: article_id, outlet_id, language, title, summary,
        discovered_at, published_at and day_of_record (for published_ts), and for the trigger record
        outlet_name; sig is its body fingerprint or None."""
        key = ref["article_id"]
        if key in self.refs:
            return
        self.refs[key] = dict(ref, t=_ts(ref.get("discovered_at")), p=published_ts(ref), rank=tie_rank(ref))
        ts = title_set(ref.get("title"))
        if ts:
            self.titles[ref.get("language")].add(key, ts)
        sh = shingles(self._summary(ref))
        if len(sh) >= SUMMARY_MIN_SHINGLES:
            self.summ.add(key, sh)
        if sig:
            from pipeline import body_dups
            self.sigs[key] = sig
            for b in range(body_dups.LSH_BANDS):
                self.bands[(b,) + tuple(sig[b * body_dups.LSH_ROWS:(b + 1) * body_dups.LSH_ROWS])].add(key)

    def _rank(self, key) -> float:
        """tie_rank of an indexed item, stored by add()."""
        return self.refs[key]["rank"]

    def _summary(self, row) -> str:
        text = _get(row, "summary")
        return self.boiler.summary(row, text) if self.boiler is not None and text else (text or "")

    def _allow(self, row) -> Callable:
        me, outlet, t = _get(row, "id"), _get(row, "outlet_id"), _ts(_get(row, "discovered_at"))
        p = published_ts(row)

        def allow(key) -> bool:
            ref = self.refs[key]
            if key == me or ref.get("outlet_id") == outlet:
                return False
            # Both published times credible: compare those; otherwise compare discoveries.
            mine, theirs = (p, ref["p"]) if p is not None and ref["p"] is not None else (t, ref["t"])
            if mine is None or theirs is None:
                return False
            return mine - WINDOW_BEFORE_DAYS * 86400 <= theirs <= mine + WINDOW_AFTER_DAYS * 86400
        return allow

    def match(self, row, sig: Optional[Tuple[int, ...]] = None) -> Optional[Dict]:
        """The state origin item this article nearly repeats, by headline first, then feed summary,
        then body fingerprint, or None. row is an articles row (id, outlet_id, language, title,
        summary, discovered_at); sig is the article's body fingerprint, when it has one."""
        if not self.refs:
            return None
        allow = self._allow(row)
        ts = title_set(_get(row, "title"))
        lang = _get(row, "language")
        if len(ts) >= TITLE_MIN_TOKENS and lang in self.titles:
            b = self.titles[lang].best_jaccard(ts, allow=allow)
            if b and b[1] >= TITLE_JACCARD:
                return self._hit("title", b[0], b[1])
        sh = shingles(self._summary(row))
        if len(sh) >= SUMMARY_MIN_SHINGLES:
            b = self.summ.best(sh, allow=allow)
            if b and b[1] / float(len(sh)) >= SUMMARY_CONTAINMENT:
                return self._hit("summary", b[0], b[1] / float(len(sh)))
        if sig and self.sigs:
            # Hook for body comparison: the fingerprints of pipeline/body_dups.py, banded as that
            # module bands them, compared at its own threshold.
            from pipeline import body_dups
            cands = set()
            for b in range(body_dups.LSH_BANDS):
                cands.update(self.bands.get((b,) + tuple(sig[b * body_dups.LSH_ROWS:(b + 1) * body_dups.LSH_ROWS]), ()))
            best = None
            for k in sorted(cands):
                if not allow(k):
                    continue
                s = body_dups.similarity(sig, self.sigs[k])
                if best is None or s > best[1] or (s == best[1] and (self._rank(k), k) < (self._rank(best[0]), best[0])):
                    best = (k, s)
            if best and best[1] >= body_dups.JACCARD_THRESHOLD:
                return self._hit("body", best[0], best[1])
        return None

    def _hit(self, by: str, key, score: float) -> Dict:
        m = self.refs[key]
        return {"method": by, "similarity": round(score, 3), "article_id": key,
                "outlet_id": m.get("outlet_id"), "outlet": m.get("outlet_name") or m.get("outlet_id"),
                "title": (m.get("title") or "").strip()}


def fingerprints(conn, ids: List[int]) -> Dict[int, Tuple[int, ...]]:
    """Body fingerprints of these articles from body_fingerprints, leaving out bodies body_dups found
    to be page template text. Empty when the table does not exist yet."""
    from pipeline import body_dups
    out = {}
    try:
        for i in range(0, len(ids), 900):
            part = ids[i:i + 900]
            for r in conn.execute(
                    "SELECT article_id, sig FROM body_fingerprints WHERE sig IS NOT NULL AND template=0 AND article_id IN (%s)"
                    % ",".join("?" * len(part)), part):
                out[r[0]] = body_dups._SIG.unpack(r[1])
    except sqlite3.OperationalError:
        return {}
    return out


def fingerprint_of(conn, article_id: int, body: Optional[str] = None) -> Optional[Tuple[int, ...]]:
    """An article's body fingerprint: the stored one, or computed from `body` when none is stored.
    None for a page template body or one too short to compare."""
    from pipeline import body_dups
    try:
        r = conn.execute("SELECT sig, template FROM body_fingerprints WHERE article_id=?", (article_id,)).fetchone()
    except sqlite3.OperationalError:
        r = None
    if r is not None and r[1]:
        return None
    if r is not None and r[0]:
        return body_dups._SIG.unpack(r[0])
    if body:
        _, packed = body_dups.fingerprint(body)
        return body_dups._SIG.unpack(packed) if packed else None
    return None


def build_index(conn, rows: Iterable) -> RouteIndex:
    """The index for a stage that will judge `rows`: every current state origin item discovered or
    credibly published in the window around any of them, with its body fingerprint where one is stored."""
    span = _span(rows, WINDOW_BEFORE_DAYS, WINDOW_AFTER_DAYS)
    if span is None:
        return RouteIndex()
    where, params = _span_sql(span)
    refs = [dict(r) for r in conn.execute(
        """SELECT a.id AS article_id, a.outlet_id, a.feed_url, a.language, a.title, a.summary, a.published_at,
                  a.discovered_at, a.day_of_record, o.name AS outlet_name
           FROM classifications c JOIN articles a ON a.id = c.article_id LEFT JOIN outlets o ON o.id = a.outlet_id
           WHERE c.is_current = 1 AND c.category = 'A' AND %s
           ORDER BY a.id""" % where, params) if _in_span(r, span)]
    return RouteIndex(refs, fingerprints(conn, [r["article_id"] for r in refs]), boiler=feed_boilerplate(conn))


def feed_boilerplate(conn):
    """A FeedBoilerplate (pipeline/boilerplate.py) on this connection, for a RouteIndex."""
    from pipeline import boilerplate
    return boilerplate.FeedBoilerplate(conn)


def context_of(llm_trigger) -> Optional[Dict]:
    """The lookalike match recorded in an article's llm_trigger, or None."""
    import json
    if not llm_trigger:
        return None
    try:
        trig = json.loads(llm_trigger) if isinstance(llm_trigger, str) else llm_trigger
    except ValueError:
        return None
    m = (trig or {}).get("lookalike") if isinstance(trig, dict) else None
    return m if isinstance(m, dict) and m.get("title") else None
