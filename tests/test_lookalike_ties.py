"""Equally similar state origin items: the lookalike route and the recall audit name the same
counterpart in every run, the earliest copy (by credible publication time, else discovery), then the
lowest article id, whatever the per process string hash seed."""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

from pipeline import body_dups, lookalike, recall_audit

ROOT = Path(__file__).resolve().parent.parent
# Both items share six of the article's seven headline words (Jaccard 6/7), each through one word the
# other lacks, so the order the overlaps are counted in decides a naive pick.
TITLE = "Singapore tycoon unveils regional fintech venture with partners"
TITLE_A = "Singapore tycoon unveils regional fintech venture"
TITLE_B = "Singapore tycoon unveils regional fintech partners"
# Each summary repeats a different half of the article's: containment 0.5 for both.
WORDS = ["word%02d" % i for i in range(40)]
SUMMARY = " ".join(WORDS)
SUMMARY_A = " ".join(WORDS[:22])
SUMMARY_B = " ".join(WORDS[18:])


def _iso(days: float) -> str:
    return (dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=days)).isoformat()


def _ref(aid, outlet, title=TITLE_A, summary="", days=0.0, published=None):
    return {"article_id": aid, "outlet_id": outlet, "outlet_name": outlet, "language": "en", "title": title,
            "summary": summary, "discovered_at": _iso(days), "published_at": _iso(published) if published is not None else None}


def _row(title=TITLE, summary=""):
    return {"id": 100, "outlet_id": "sg_straits", "language": "en", "title": title, "summary": summary,
            "discovered_at": _iso(0)}


def test_equally_similar_headlines_go_to_the_earliest_copy():
    idx = lookalike.RouteIndex([_ref(3, "sg_cna", TITLE_A, days=-1), _ref(5, "us_prnewswire", TITLE_B, days=-2)])
    m = idx.match(_row())
    assert m["method"] == "title" and m["article_id"] == 5


def test_a_credible_published_time_counts_before_discovery():
    idx = lookalike.RouteIndex([_ref(3, "sg_cna", TITLE_A, days=-1),
                                _ref(5, "us_prnewswire", TITLE_B, days=0, published=-3)])
    assert idx.match(_row())["article_id"] == 5


def test_a_publication_date_without_a_time_does_not_count_as_midnight():
    timed = dict(_ref(5, "us_prnewswire", TITLE_B, days=0.1), published_at=_iso(0.05))
    dated = dict(_ref(3, "sg_cna", TITLE_A, days=0.2), published_at=_iso(0)[:10])
    assert lookalike.tie_rank(dated) == lookalike._ts(_iso(0.2))
    assert lookalike.RouteIndex([dated, timed]).match(_row())["article_id"] == 5


def test_at_the_same_time_the_lower_article_id_wins():
    idx = lookalike.RouteIndex([_ref(5, "us_prnewswire", TITLE_B, days=-1), _ref(3, "sg_cna", TITLE_A, days=-1)])
    assert idx.match(_row())["article_id"] == 3


def test_equally_contained_summaries_go_to_the_earliest_copy():
    other = "A headline that shares nothing with either"
    idx = lookalike.RouteIndex([_ref(3, "sg_cna", other, SUMMARY_A, days=-1),
                                _ref(5, "us_prnewswire", other, SUMMARY_B, days=-2)])
    m = idx.match(_row(title="Completely different words here today friends", summary=SUMMARY))
    assert m["method"] == "summary" and m["article_id"] == 5


def test_equal_body_fingerprints_go_to_the_earliest_copy():
    _, packed = body_dups.fingerprint(" ".join("w%d" % i for i in range(80)))
    sig = body_dups._SIG.unpack(packed)
    other = "A headline that shares nothing with either"
    idx = lookalike.RouteIndex([_ref(3, "sg_cna", other, days=-1), _ref(5, "us_prnewswire", other, days=-2)],
                               {3: sig, 5: sig})
    m = idx.match(_row(title="Completely different words here today friends"), sig)
    assert m["method"] == "body" and m["article_id"] == 5


def test_the_recall_audit_breaks_ties_the_same_way():
    refs = [dict(_ref(3, "sg_cna", TITLE_A, days=-1), route="xinhua"),
            dict(_ref(5, "us_prnewswire", TITLE_B, days=-2), route="xinhua")]
    rows = [{"article_id": 100, "language": "en", "title": TITLE, "summary": ""}]
    assert recall_audit.lookalikes(rows, refs)[100]["match_article_id"] == 5


_PROBE = r"""
import json, sys
sys.path.insert(0, %(root)r)
from tests import test_lookalike_ties as t
from pipeline import lookalike, recall_audit
out = []
for a, b in ((3, 5), (5, 3)):   # the same times: the lower id
    idx = lookalike.RouteIndex([t._ref(a, "sg_cna", t.TITLE_A, days=-1), t._ref(b, "us_prnewswire", t.TITLE_B, days=-1)])
    out.append(idx.match(t._row())["article_id"])
idx = lookalike.RouteIndex([t._ref(3, "sg_cna", "x", t.SUMMARY_A, days=-1), t._ref(5, "us_prnewswire", "y", t.SUMMARY_B, days=-1)])
out.append(idx.match(t._row(title="z", summary=t.SUMMARY))["article_id"])
refs = [dict(t._ref(3, "sg_cna", t.TITLE_A, days=-1), route="r"), dict(t._ref(5, "us_prnewswire", t.TITLE_B, days=-1), route="r")]
out.append(recall_audit.lookalikes([{"article_id": 100, "language": "en", "title": t.TITLE, "summary": ""}], refs)[100]["match_article_id"])
print(json.dumps(out))
"""


def test_the_counterpart_does_not_change_with_the_hash_seed():
    seen = set()
    for seed in ("0", "1", "2", "3", "4", "5"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        res = subprocess.run([sys.executable, "-c", _PROBE % {"root": str(ROOT)}], cwd=str(ROOT), env=env,
                             capture_output=True, text=True, check=True)
        seen.add(res.stdout.strip())
    assert len(seen) == 1
    assert json.loads(seen.pop()) == [3, 3, 3, 3]
