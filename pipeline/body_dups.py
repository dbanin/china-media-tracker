"""Near-duplicate linking on body text, the second pass after title matching.

Title matching (pipeline/fetch_feeds.link_near_duplicates) links two items in one country whose
headlines share most of their words. It misses syndicated copy whose headline an outlet rewrote
("US imposes sanctions on 13 tied to Iran weapons procurement" over a wire story first headlined
"US sanctions 10 over allegedly procuring weapons for Iran"), and it never looks across countries.
This pass compares the fetched text itself.

Fingerprint. Every fetched body is cut into overlapping five word shingles (five character shingles
for scripts written without spaces, such as Japanese, Chinese or Thai), each shingle is hashed once,
and a one permutation MinHash of SIG_BINS bins keeps the smallest hash in each bin (empty bins of a
short text borrow from the next full bin, the rotation densification of Shrivastava and Li, 2014).
The share of bins two signatures agree on estimates the Jaccard overlap of their shingle sets. The
signature is SIG_BINS 32 bit integers, 256 bytes, in body_fingerprints. Pure Python, about a
millisecond per body.

Linking. Signatures are banded (LSH_BANDS bands of LSH_ROWS bins), so only pairs sharing a band are
compared. Two articles are linked when
  - they come from different outlets, in any countries;
  - they were discovered within WINDOW_DAYS of each other;
  - both bodies have at least MIN_SHINGLES shingles;
  - their estimated overlap is at least JACCARD_THRESHOLD;
  - neither body is a page template: a body that matches, at TEMPLATE_SIMILARITY or more, another
    article of its own outlet under a different headline is taken to be boilerplate the extractor
    returned in place of the article (the same text under unrelated headlines) and is never linked.
Same outlet pairs are left to title matching. Within one outlet a high overlap under a different
headline was, on the sample measured, mostly a follow up story reusing background paragraphs or an
extraction failure, not one item placed twice.

Each link is a row in body_links. The links do not rewrite articles.dup_group_id: that column is the
title group, which classify_llm uses to copy one placement's model label to its siblings, and a
body link must not start copying labels between outlets. item_groups() joins the title groups
through the body links (union find) for the export's underlying item counts, so category counts
are unchanged and the underlying item figures can only fall.

Translations are not matched. Body overlap is measured on words, and a translated copy shares
almost none with its original; matching them would need machine translation, which is not done.

Usage:
  python -m pipeline.body_dups backfill --budget-minutes 10   # fingerprint and link stored bodies
  python -m pipeline.body_dups stats
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import struct
import sys
import time
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from pipeline import store

FINGERPRINT_VERSION = 1
SHINGLE_WORDS = 5
SHINGLE_CHARS = 5
# A text whose words average more than this many characters has no word spaces to split on.
UNSPACED_MEAN_WORD_CHARS = 12
SIG_BINS = 64
LSH_BANDS = 32
LSH_ROWS = 2
MIN_SHINGLES = 25
WINDOW_DAYS = 7
JACCARD_THRESHOLD = 0.5
TEMPLATE_SIMILARITY = 0.9
# Seconds of a pipeline fetch stage the backfill may use; the new bodies of the stage come first.
STAGE_SECONDS = 180
_MASK = 0xFFFFFFFF
_GOLDEN = 0x9E3779B1
_SIG = struct.Struct("<%dI" % SIG_BINS)
_word_re = re.compile(r"\w+", re.UNICODE)


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------

def shingles(text: str) -> Set[int]:
    """64 bit hashes of the text's overlapping word (or character) five-grams."""
    toks = _word_re.findall((text or "").lower())
    if not toks:
        return set()
    if sum(len(t) for t in toks) > UNSPACED_MEAN_WORD_CHARS * len(toks):
        s = "".join(toks)
        grams = [s[i:i + SHINGLE_CHARS] for i in range(max(1, len(s) - SHINGLE_CHARS + 1))]
    else:
        grams = [" ".join(toks[i:i + SHINGLE_WORDS]) for i in range(max(1, len(toks) - SHINGLE_WORDS + 1))]
    return {int.from_bytes(hashlib.blake2b(g.encode("utf-8"), digest_size=8).digest(), "big") for g in grams}


def signature(hashes: Iterable[int]) -> Optional[Tuple[int, ...]]:
    """One permutation MinHash: the top six bits pick the bin, the low 32 bits are the value kept."""
    bins: List[Optional[int]] = [None] * SIG_BINS
    for h in hashes:
        b = h >> 58
        v = h & _MASK
        cur = bins[b]
        if cur is None or v < cur:
            bins[b] = v
    if all(v is None for v in bins):
        return None
    out = []
    for i in range(SIG_BINS):
        if bins[i] is not None:
            out.append(bins[i])
            continue
        d = 1
        while bins[(i + d) % SIG_BINS] is None:
            d += 1
        out.append((bins[(i + d) % SIG_BINS] + d * _GOLDEN) & _MASK)
    return tuple(out)


def similarity(a: Tuple[int, ...], b: Tuple[int, ...]) -> float:
    return sum(1 for x, y in zip(a, b) if x == y) / float(SIG_BINS)


def fingerprint(text: str) -> Tuple[int, Optional[bytes]]:
    """(distinct shingles, packed signature or None when the text is too short to compare)."""
    hs = shingles(text)
    if len(hs) < MIN_SHINGLES:
        return len(hs), None
    return len(hs), _SIG.pack(*signature(hs))


def record(conn, row, text: Optional[str]) -> None:
    """Store the fingerprint of one article's body. Called by the fetch stage where the body is
    saved, and by the backfill. text None means the body file is missing: the row is marked with
    shingles -1 so the backfill does not try it again."""
    if text is None:
        n, sig = -1, None
    else:
        n, sig = fingerprint(text)
    # A refetched body replaces its fingerprint, and the links made from the old one go with it.
    conn.execute("DELETE FROM body_links WHERE a=? OR b=?", (row["id"], row["id"]))
    conn.execute(
        """INSERT OR REPLACE INTO body_fingerprints(article_id, version, shingles, sig, discovered_at, outlet_id,
               template, checked_at, computed_at) VALUES (?,?,?,?,?,?,0,?,?)""",
        (row["id"], FINGERPRINT_VERSION, n, sig, row["discovered_at"], row["outlet_id"],
         None if sig else store.utcnow(), store.utcnow()),
    )


def backfill_fingerprints(conn, deadline: Optional[float] = None, limit: Optional[int] = None,
                          chunk: int = 500) -> Dict:
    """Fingerprint stored bodies that have none yet, newest first, committing every chunk."""
    sql = """SELECT a.id, a.url_hash, a.discovered_at, a.outlet_id FROM articles a
             LEFT JOIN body_fingerprints f ON f.article_id=a.id
             WHERE a.body_hash IS NOT NULL AND f.article_id IS NULL ORDER BY a.discovered_at DESC"""
    if limit:
        sql += " LIMIT %d" % int(limit)
    rows = conn.execute(sql).fetchall()
    out = {"todo": len(rows), "fingerprinted": 0, "missing_body": 0, "too_short": 0, "stopped_deadline": False}
    for i, r in enumerate(rows):
        if deadline is not None and time.time() > deadline:
            out["stopped_deadline"] = True
            break
        text = store.load_body(r["url_hash"])
        record(conn, r, text)
        if text is None:
            out["missing_body"] += 1
        else:
            out["fingerprinted"] += 1
        if (i + 1) % chunk == 0:
            conn.commit()
    conn.commit()
    out["too_short"] = conn.execute(
        "SELECT COUNT(*) FROM body_fingerprints WHERE sig IS NULL AND shingles>=0").fetchone()[0]
    return out


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------

def _t(iso: str) -> float:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


class _Window:
    """Signatures discovered in [lo, hi), banded for candidate lookup."""

    def __init__(self, conn, lo: str, hi: str):
        from pipeline.fetch_feeds import normalize_title
        self.rows = {}
        self.bands = defaultdict(list)
        for r in conn.execute(
                """SELECT f.article_id, f.sig, f.discovered_at, f.outlet_id, f.template, f.checked_at, a.title
                   FROM body_fingerprints f JOIN articles a ON a.id=f.article_id
                   WHERE f.sig IS NOT NULL AND f.discovered_at>=? AND f.discovered_at<?""", (lo, hi)):
            sig = _SIG.unpack(r["sig"])
            self.rows[r["article_id"]] = {"sig": sig, "t": _t(r["discovered_at"]), "outlet": r["outlet_id"],
                                          "template": bool(r["template"]), "checked": r["checked_at"] is not None,
                                          "title": normalize_title(r["title"] or "")}
            for b in range(LSH_BANDS):
                self.bands[(b,) + sig[b * LSH_ROWS:(b + 1) * LSH_ROWS]].append(r["article_id"])

    def candidates(self, aid: int) -> Set[int]:
        sig = self.rows[aid]["sig"]
        out = set()
        for b in range(LSH_BANDS):
            out.update(self.bands.get((b,) + sig[b * LSH_ROWS:(b + 1) * LSH_ROWS], ()))
        out.discard(aid)
        return out


def _template_partners(win: _Window, aid: int, cands: Set[int]) -> List[int]:
    """Articles of the same outlet whose body matches this one's under a different headline."""
    from pipeline.fetch_feeds import TITLE_JACCARD_THRESHOLD, jaccard
    me = win.rows[aid]
    out = []
    for o in sorted(cands):
        other = win.rows[o]
        if other["outlet"] != me["outlet"] or abs(other["t"] - me["t"]) > WINDOW_DAYS * 86400:
            continue
        if similarity(me["sig"], other["sig"]) >= TEMPLATE_SIMILARITY and \
                jaccard(me["title"], other["title"]) < TITLE_JACCARD_THRESHOLD:
            out.append(o)
    return out


def link_pending(conn, deadline: Optional[float] = None) -> Dict:
    """Compare every fingerprint not yet checked against the window around it and record links.
    Works through the unchecked rows a week at a time, oldest first, so a backfill of months of
    bodies loads three weeks of signatures at once, never all of them."""
    out = {"checked": 0, "links": 0, "templates": 0, "stopped_deadline": False}
    week = 7 * 86400
    while True:
        first = conn.execute("SELECT MIN(discovered_at) FROM body_fingerprints WHERE checked_at IS NULL").fetchone()[0]
        if first is None:
            break
        if deadline is not None and time.time() > deadline:
            out["stopped_deadline"] = True
            break
        t0 = _t(first)
        lo, hi = _iso(t0), _iso(t0 + week)
        win = _Window(conn, _iso(t0 - WINDOW_DAYS * 86400), _iso(t0 + week + WINDOW_DAYS * 86400))
        todo = [r[0] for r in conn.execute(
            "SELECT article_id FROM body_fingerprints WHERE checked_at IS NULL AND discovered_at>=? AND discovered_at<?",
            (lo, hi))]
        now = store.utcnow()
        for i, aid in enumerate(todo):
            if i % 500 == 499 and deadline is not None and time.time() > deadline:
                out["stopped_deadline"] = True
                break
            me = win.rows.get(aid)
            if me is None:  # no signature: nothing to compare
                conn.execute("UPDATE body_fingerprints SET checked_at=? WHERE article_id=?", (now, aid))
                continue
            cands = win.candidates(aid)
            partners = _template_partners(win, aid, cands)
            if partners:
                # Both sides are template text; links either made earlier are withdrawn.
                for x in [aid] + partners:
                    if not win.rows[x]["template"]:
                        out["templates"] += 1
                    win.rows[x]["template"] = True
                    conn.execute("UPDATE body_fingerprints SET template=1 WHERE article_id=?", (x,))
                    conn.execute("DELETE FROM body_links WHERE a=? OR b=?", (x, x))
                conn.execute("UPDATE body_fingerprints SET checked_at=? WHERE article_id=?", (now, aid))
                continue
            for o in sorted(cands):
                other = win.rows[o]
                if other["outlet"] == me["outlet"] or other["template"]:
                    continue
                if abs(other["t"] - me["t"]) > WINDOW_DAYS * 86400:
                    continue
                s = similarity(me["sig"], other["sig"])
                if s < JACCARD_THRESHOLD:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO body_links(a, b, similarity, version, created_at) VALUES (?,?,?,?,?)",
                    (min(aid, o), max(aid, o), s, FINGERPRINT_VERSION, now))
                out["links"] += cur.rowcount
            me["checked"] = True
            conn.execute("UPDATE body_fingerprints SET checked_at=? WHERE article_id=?", (now, aid))
            out["checked"] += 1
        conn.commit()
        if out["stopped_deadline"]:
            break
    return out


def run(conn, deadline: Optional[float] = None, max_seconds: Optional[float] = STAGE_SECONDS) -> Dict:
    """The fetch stage's pass: fingerprint any stored body still without one (the bodies saved this
    stage already have theirs), then link. Bounded by the stage deadline and by max_seconds, so a
    long backfill spreads over several runs instead of taking time from classification."""
    if max_seconds is not None:
        cap = time.time() + max_seconds
        deadline = cap if deadline is None else min(deadline, cap)
    started = time.time()
    out = {"backfill": backfill_fingerprints(conn, deadline=deadline)}
    out.update(link_pending(conn, deadline=deadline))
    out["seconds"] = round(time.time() - started, 1)
    return out


# ---------------------------------------------------------------------------
# Template bodies that are not the article
# ---------------------------------------------------------------------------
# A template is one text stored under several headlines of one outlet. Sometimes that is real: a
# follow up story reusing its predecessor's paragraphs (Antara on Xi and Myanmar), or one story under
# two URLs. Then the text carries the headlines. When the outlet served a challenge page, its menus or
# another story in place of each article (Afghanistan International's related article card, Minsk
# News, a gambling spam block injected into BusinessWorld pages), the text carries none of them, and a
# label on it rests on the headline alone. The test: on average over the group, the shared text holds
# fewer than HEADLINE_COVERAGE_MIN of each headline's words. On the 2026-10-12 snapshot the 50 template
# bodies split cleanly: under 0.32 for every extraction failure, 0.58 and above for every real case.
HEADLINE_COVERAGE_MIN = 0.5
HEADLINE_WORD_CHARS = 4
_unspaced_script_re = re.compile(r"[฀-໿က-႟ក-៿぀-ヿ㐀-䶿一-鿿]")


def headline_overlap(title: str, body: str) -> Optional[float]:
    """Share of the headline's words (of HEADLINE_WORD_CHARS letters or more; character pairs in a
    script written without spaces) that occur in the body, or None when the headline has none."""
    t = (title or "").lower()
    b = (body or "").lower()
    if _unspaced_script_re.search(t):
        runs = _word_re.findall(t)
        keys = {r[i:i + 2] for r in runs for i in range(len(r) - 1)}
    else:
        keys = {w for w in _word_re.findall(t) if len(w) >= HEADLINE_WORD_CHARS}
    if not keys:
        return None
    return sum(1 for k in keys if k in b) / float(len(keys))


def template_unreadable(conn, row, body: str) -> Optional[str]:
    """A reason when this article's body is a template that is not the article, else None."""
    import sqlite3
    from pipeline.fetch_feeds import TITLE_JACCARD_THRESHOLD, jaccard, normalize_title
    try:
        me = conn.execute("SELECT sig, template, discovered_at, outlet_id FROM body_fingerprints WHERE article_id=?",
                          (row["id"],)).fetchone()
    except sqlite3.OperationalError:  # a database from before body fingerprints
        return None
    if me is None or not me["template"] or me["sig"] is None:
        return None
    sig = _SIG.unpack(me["sig"])
    t0 = _t(me["discovered_at"])
    lo, hi = _iso(t0 - WINDOW_DAYS * 86400), _iso(t0 + WINDOW_DAYS * 86400)
    my_title = normalize_title(row["title"] or "")
    titles = [row["title"] or ""]
    for r in conn.execute(
            """SELECT f.sig, a.title FROM body_fingerprints f JOIN articles a ON a.id=f.article_id
               WHERE f.outlet_id=? AND f.article_id<>? AND f.sig IS NOT NULL AND f.discovered_at>=? AND f.discovered_at<=?""",
            (me["outlet_id"], row["id"], lo, hi)):
        if similarity(sig, _SIG.unpack(r["sig"])) >= TEMPLATE_SIMILARITY and \
                jaccard(my_title, normalize_title(r["title"] or "")) < TITLE_JACCARD_THRESHOLD:
            titles.append(r["title"] or "")
    # With no partner left (each was fetched or read again and no longer matches) the body is judged
    # on its own headline.
    cover = [x for x in (headline_overlap(t, body) for t in titles) if x is not None]
    if not cover:
        return None
    mean = sum(cover) / len(cover)
    if mean < HEADLINE_COVERAGE_MIN:
        return "template body: %.0f percent of the words of the %d headlines it was stored under" % (
            100 * mean, len(titles))
    return None


# ---------------------------------------------------------------------------
# Groups for the export
# ---------------------------------------------------------------------------

def item_groups(conn) -> Dict[int, int]:
    """Underlying item groups: title groups joined through body links. Returns a map from an
    article's title group id (dup_group_id, or its own id when it has none) to the group id it
    counts under; groups no body link touches are absent and count under their own id."""
    links = conn.execute("SELECT a, b FROM body_links").fetchall()
    if not links:
        return {}
    ids = {x for r in links for x in (r[0], r[1])}
    title_group = {}
    id_list = list(ids)
    for i in range(0, len(id_list), 900):
        part = id_list[i:i + 900]
        for r in conn.execute("SELECT id, dup_group_id FROM articles WHERE id IN (%s)" % ",".join("?" * len(part)), part):
            title_group[r[0]] = r[1] or r[0]
    parent: Dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for a, b in links:
        ga, gb = find(title_group.get(a, a)), find(title_group.get(b, b))
        if ga != gb:
            parent[max(ga, gb)] = min(ga, gb)
    return {g: find(g) for g in list(parent)}


def stats(conn) -> Dict:
    f = conn.execute(
        """SELECT COUNT(*) n, SUM(sig IS NOT NULL) signed, SUM(shingles<0) missing, SUM(template) templates,
                  SUM(checked_at IS NULL) unchecked FROM body_fingerprints""").fetchone()
    unfingerprinted = conn.execute(
        """SELECT COUNT(*) FROM articles a LEFT JOIN body_fingerprints f ON f.article_id=a.id
           WHERE a.body_hash IS NOT NULL AND f.article_id IS NULL""").fetchone()[0]
    groups = item_groups(conn)
    merged = sum(1 for g, root in groups.items() if g != root)
    return {"fingerprints": f["n"], "with_signature": f["signed"] or 0, "body_missing": f["missing"] or 0,
            "templates": f["templates"] or 0, "unchecked": f["unchecked"] or 0,
            "bodies_without_fingerprint": unfingerprinted,
            "body_links": conn.execute("SELECT COUNT(*) FROM body_links").fetchone()[0],
            "title_groups_merged": merged}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["backfill", "stats"])
    ap.add_argument("--budget-minutes", type=float, default=10.0)
    args = ap.parse_args(argv)
    conn = store.connect()
    if args.cmd == "backfill":
        res = run(conn, deadline=time.time() + args.budget_minutes * 60, max_seconds=None)
    else:
        res = stats(conn)
    conn.close()
    print(json.dumps(res))
    return res


if __name__ == "__main__":
    sys.exit(0 if main() is not None else 1)
