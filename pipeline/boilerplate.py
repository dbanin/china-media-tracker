"""Feed summary boilerplate: text a feed repeats in most of its item summaries.

Some feeds wrap every item's summary in the same promotional text. Ghana News Agency's feed puts
"Stay up-to-date on the latest news from Ghana and the world. Visit the Ghana News Agency (GNA)
website now..." before the lede and "Get the full content now. Go to the Ghana News Agency website"
after it, so any two of its items share about half of their summary shingles whatever they report.
Summary based matching reads that wrapper as content: the recall audit counted such pairs as
lookalikes of a state origin item, and the keyword gate reads every word of the summary.

The detector is data driven and per feed. A feed's recent summaries are its items discovered in
the WINDOW_DAYS before its latest item, at most MAX_ITEMS of them. When at least MIN_ITEMS of those
carry a summary of SHINGLE words or more, the boilerplate is every five word shingle (the shingles
of pipeline/lookalike.py: words, or character bigrams for scripts written without spaces) found in
more than MIN_SHARE of them. strip() removes the words those shingles cover. A feed with too few
summaries, or none repeated in most of them, has no boilerplate and its summaries are read whole.

Where it applies:
  - pipeline/lookalike.py: feed summaries are compared without their feed's boilerplate, in the
    rules stage's route (RouteIndex) and in the recall audit (pipeline/recall_audit.py);
  - pipeline/gate.py: the keyword terms are matched on the summary without its feed's boilerplate.
    The state origin signature check still reads the whole summary: a credit line a feed prints
    on every item is evidence, and the gate must never discard the material the instrument exists
    to find.

FeedBoilerplate computes an outlet's feed boilerplate on first use and keeps it for the life of the
object (one stage run). On a writable connection it also keeps it in the feed_boilerplate table,
which later runs read instead of computing it again until it is CACHE_HOURS old; on a read only
connection it computes and writes nothing.
"""
import datetime as dt
import json
import re
import sqlite3
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from pipeline.lookalike import SHINGLE, _DENSE, _WORD, tokens

WINDOW_DAYS = 30
MAX_ITEMS = 200
MIN_ITEMS = 10
MIN_SHARE = 0.5
# A feed's boilerplate is kept in feed_boilerplate and computed again when older than this.
CACHE_HOURS = 24

CACHE_SCHEMA = """CREATE TABLE IF NOT EXISTS feed_boilerplate (
    feed_url TEXT PRIMARY KEY,
    outlet_id TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    items INTEGER NOT NULL,
    shingles TEXT NOT NULL
)"""

Span = Tuple[str, int, int]


def _spans(text: str) -> List[Span]:
    """The tokens of pipeline/lookalike.tokens with their character spans in `text`. Where lowering
    changes the text's length (a handful of letters, such as the Turkish dotted capital I), the
    words are found in the text as written and lowered one by one."""
    text = text or ""
    low = text.lower()
    if len(low) != len(text):
        return [(m.group().lower(), m.start(), m.end()) for m in _WORD.finditer(text)]
    if low and len(_DENSE.findall(low)) > 0.3 * len(low):
        chars = [(i, ch) for i, ch in enumerate(low) if not ch.isspace()]
        return [(chars[j][1] + chars[j + 1][1], chars[j][0], chars[j + 1][0] + 1) for j in range(len(chars) - 1)]
    return [(m.group(), m.start(), m.end()) for m in _WORD.finditer(low)]


def _shingle_keys(spans: List[Span], k: int = SHINGLE) -> List[str]:
    toks = [s[0] for s in spans]
    return [" ".join(toks[i:i + k]) for i in range(max(0, len(toks) - k + 1))]


def common_shingles(summaries: Iterable[str], min_items: int = MIN_ITEMS, share: float = MIN_SHARE) -> frozenset:
    """Shingles found in more than `share` of the summaries that have any, provided at least
    `min_items` do. Empty otherwise."""
    counts = Counter()
    n = 0
    for s in summaries:
        # Word tuples rather than joined strings: the same shingles, counted at a third of the cost.
        t = tokens(s)
        keys = set(zip(*[t[i:] for i in range(SHINGLE)]))
        if not keys:
            continue
        n += 1
        counts.update(keys)
    if n < min_items:
        return frozenset()
    return frozenset(" ".join(k) for k, c in counts.items() if c > share * n)


def strip(text: str, common) -> str:
    """`text` without the words covered by any of the `common` shingles. Unchanged when there are
    none or none occur."""
    if not text or not common:
        return text or ""
    spans = _spans(text)
    keys = _shingle_keys(spans)
    covered = [False] * len(spans)
    hit = False
    for i, key in enumerate(keys):
        if key in common:
            hit = True
            for j in range(i, i + SHINGLE):
                covered[j] = True
    if not hit:
        return text
    out, pos, i = [], 0, 0
    while i < len(spans):
        if not covered[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(spans) and covered[j + 1]:
            j += 1
        out.append(text[pos:spans[i][1]])
        out.append(" ")
        pos = spans[j][2]
        i = j + 1
    out.append(text[pos:])
    return re.sub(r"[ \t]{2,}", " ", "".join(out)).strip()


def _ts(iso: Optional[str]) -> Optional[dt.datetime]:
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def recent_by_feed(rows: Iterable, window_days: int = WINDOW_DAYS, max_items: int = MAX_ITEMS) -> Dict[str, List[str]]:
    """Group (feed_url, summary, discovered_at) rows, newest first, into each feed's recent
    summaries: those discovered within window_days of the feed's latest item, at most max_items."""
    latest, out = {}, defaultdict(list)
    for feed, summary, at in rows:
        if not feed:
            continue
        t = _ts(at)
        if feed not in latest:
            latest[feed] = t
        top = latest[feed]
        if len(out[feed]) >= max_items:
            continue
        if top is not None and t is not None and t < top - dt.timedelta(days=window_days):
            continue
        out[feed].append(summary or "")
    return out


class FeedBoilerplate:
    """Per feed boilerplate shingles, computed on first use and kept per outlet. Without a
    connection every feed has none, so callers can pass one unconditionally."""

    def __init__(self, conn=None, window_days: int = WINDOW_DAYS, cache: bool = True,
                 now: Optional[dt.datetime] = None):
        self.conn = conn
        self.window_days = window_days
        self.cache = cache
        self.now = now or dt.datetime.now(dt.timezone.utc)
        self._by_outlet: Dict[str, Dict[str, frozenset]] = {}
        self._feed_of: Dict[int, Tuple[Optional[str], Optional[str]]] = {}

    def _cached(self, outlet_id: str) -> Optional[Dict[str, frozenset]]:
        """The outlet's feeds from feed_boilerplate when every row is younger than CACHE_HOURS."""
        try:
            rows = self.conn.execute("SELECT feed_url, shingles, computed_at FROM feed_boilerplate WHERE outlet_id=?",
                                     (outlet_id,)).fetchall()
        except sqlite3.OperationalError:
            return None
        cutoff = (self.now - dt.timedelta(hours=CACHE_HOURS)).replace(microsecond=0).isoformat()
        if not rows or any((r[2] or "") < cutoff for r in rows):
            return None
        return {r[0]: frozenset(json.loads(r[1] or "[]")) for r in rows}

    def _store(self, outlet_id: str, feeds: Dict[str, Tuple[int, frozenset]]) -> None:
        """Keep the outlet's feeds in feed_boilerplate. Nothing is written on a read only connection;
        the caller's own commit makes the rows durable."""
        if not self.cache or not feeds:
            return
        at = self.now.replace(microsecond=0).isoformat()
        try:
            self.conn.execute(CACHE_SCHEMA)
            self.conn.executemany(
                "INSERT OR REPLACE INTO feed_boilerplate(feed_url, outlet_id, computed_at, items, shingles) VALUES (?,?,?,?,?)",
                [(f, outlet_id, at, n, json.dumps(sorted(c), ensure_ascii=False)) for f, (n, c) in feeds.items()])
        except sqlite3.OperationalError:
            self.cache = False

    def _load_outlet(self, outlet_id: str) -> Dict[str, frozenset]:
        got = self._by_outlet.get(outlet_id)
        if got is not None:
            return got
        got = {}
        if self.conn is not None:
            cached = self._cached(outlet_id) if self.cache else None
            if cached is not None:
                got = {f: c for f, c in cached.items() if c}
            else:
                rows = self.conn.execute(
                    "SELECT feed_url, summary, discovered_at FROM articles WHERE outlet_id=? AND feed_url IS NOT NULL "
                    "ORDER BY discovered_at DESC, id DESC", (outlet_id,))
                feeds = {}
                for feed, summaries in recent_by_feed(rows, self.window_days).items():
                    common = common_shingles(summaries)
                    feeds[feed] = (len(summaries), common)
                    if common:
                        got[feed] = common
                self._store(outlet_id, feeds)
        self._by_outlet[outlet_id] = got
        return got

    def shingles(self, outlet_id: Optional[str], feed_url: Optional[str]) -> frozenset:
        if not outlet_id or not feed_url:
            return frozenset()
        return self._load_outlet(outlet_id).get(feed_url, frozenset())

    def _feed(self, row) -> Tuple[Optional[str], Optional[str]]:
        """(outlet_id, feed_url) of an articles row or a state origin item dict, looking the feed up
        by article id when the row does not carry it."""
        def get(k):
            try:
                return row[k]
            except (IndexError, KeyError):
                return None
        outlet, feed = get("outlet_id"), get("feed_url")
        if feed or self.conn is None:
            return outlet, feed
        aid = get("article_id") or get("id")
        if aid is None:
            return outlet, None
        if aid not in self._feed_of:
            r = self.conn.execute("SELECT outlet_id, feed_url FROM articles WHERE id=?", (aid,)).fetchone()
            self._feed_of[aid] = (r[0], r[1]) if r else (outlet, None)
        return self._feed_of[aid]

    def prefetch_feeds(self, ids: List[int]) -> None:
        """Look up the feed of many articles at once, for callers whose rows lack feed_url."""
        if self.conn is None:
            return
        ids = [i for i in ids if i is not None and i not in self._feed_of]
        for i in range(0, len(ids), 900):
            part = ids[i:i + 900]
            for r in self.conn.execute("SELECT id, outlet_id, feed_url FROM articles WHERE id IN (%s)"
                                       % ",".join("?" * len(part)), part):
                self._feed_of[r[0]] = (r[1], r[2])

    def summary(self, row, text: Optional[str] = None) -> str:
        """The row's feed summary (or `text`) without its feed's boilerplate."""
        if text is None:
            try:
                text = row["summary"]
            except (IndexError, KeyError):
                text = None
        if not text:
            return text or ""
        outlet, feed = self._feed(row)
        return strip(text, self.shingles(outlet, feed))


def feed_report(conn, since: str, min_items: int = MIN_ITEMS) -> List[Dict]:
    """For every feed with items discovered since `since`: its items, those with a summary, the
    boilerplate shingle count and the share of all its summary shingle occurrences that are
    boilerplate (shingles common to more than half of its items). Worst first."""
    rows = conn.execute(
        "SELECT a.outlet_id, a.feed_url, a.summary FROM articles a WHERE a.discovered_at >= ? AND a.feed_url IS NOT NULL",
        (since,))
    by_feed = defaultdict(list)
    outlet_of = {}
    for o, f, s in rows:
        by_feed[f].append(s or "")
        outlet_of[f] = o
    out = []
    for f, summaries in by_feed.items():
        keyed = [_shingle_keys(_spans(s)) for s in summaries]
        with_any = [k for k in keyed if k]
        common = common_shingles(summaries, min_items=min_items)
        total = sum(len(k) for k in with_any)
        boiler = sum(1 for k in with_any for x in k if x in common)
        out.append({"outlet_id": outlet_of[f], "feed_url": f, "items": len(summaries), "with_summary": len(with_any),
                    "boilerplate_shingles": len(common), "share": (boiler / float(total)) if total else 0.0})
    out.sort(key=lambda r: (-r["share"], -r["items"], r["feed_url"]))
    return out


def main(argv=None):
    import argparse
    from pipeline import config
    ap = argparse.ArgumentParser(description="Feeds whose summaries repeat boilerplate")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args(argv)
    conn = sqlite3.connect("file:%s?mode=ro" % config.DB_PATH, uri=True)
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)).replace(microsecond=0).isoformat()
    rep = feed_report(conn, since)
    print("feeds with items in the last %d days: %d; with boilerplate: %d" % (
        args.days, len(rep), sum(1 for r in rep if r["boilerplate_shingles"])))
    for r in rep[:args.top]:
        if not r["boilerplate_shingles"]:
            break
        print("  %5.1f%%  %-22s %5d items %5d with summary %4d shingles  %s" % (
            100 * r["share"], r["outlet_id"], r["items"], r["with_summary"], r["boilerplate_shingles"], r["feed_url"]))


if __name__ == "__main__":
    main()
