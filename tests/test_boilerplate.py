"""Feed summary boilerplate (pipeline/boilerplate.py): detected per feed from its recent summaries,
stripped before summary matching (the lookalike route and the recall audit) and before the keyword
gate reads a summary."""
import datetime as dt
import sqlite3

import pytest

from pipeline import boilerplate, config, gate, lookalike, recall_audit, store

HEAD = ("Stay up-to-date on the latest news from Ghana and the world. Visit the Ghana News Agency (GNA) "
        "website now. GNA provides comprehensive coverage of Ghanaian politics, economy, business and more.")
TAIL = "Get the full content now. Go to the Ghana News Agency website: https://gna.org.gh"
WORDS = "cocoa board announced price coming season after talks with farmers union".split()


def _own(i, n=len(WORDS)):
    """Words of item i alone, so no shingle of them is repeated across items."""
    return " ".join("%s%d" % (w, i) for w in WORDS[:n])


LEDES = ["Accra, Oct. %d, GNA - %s" % (i, _own(i)) for i in range(1, 13)]
FEED = "https://gna.org.gh/category/world/feed/"


def _wrapped(lede):
    return "%s %s... %s" % (HEAD, lede, TAIL)


def test_common_shingles_are_the_wrapper_not_the_story():
    summaries = [_wrapped(_own(i)) for i in range(12)]
    common = boilerplate.common_shingles(summaries)
    assert "stay up to date on" in common and "get the full content now" in common
    assert not any("cocoa" in s for s in common)


def test_too_few_summaries_have_no_boilerplate():
    assert boilerplate.common_shingles([_wrapped("a lede of some length here today")] * (boilerplate.MIN_ITEMS - 1)) == frozenset()
    # blank summaries do not count towards the minimum
    assert boilerplate.common_shingles([""] * 50 + [_wrapped("x y z w v u")] * 3) == frozenset()


def test_shingles_in_half_or_fewer_are_kept():
    summaries = [_wrapped(_own(i)) for i in range(6)] + [_own(100 + i) for i in range(6)]
    assert boilerplate.common_shingles(summaries) == frozenset()


def test_strip_removes_the_wrapper_and_keeps_the_lede_as_written():
    common = boilerplate.common_shingles([_wrapped(l) for l in LEDES])
    lede = "Beijing, Oct. 5 (Xinhua/GNA) - Andrea Triboldi, an Italian tourist, arrived in China with a long list."
    out = boilerplate.strip(_wrapped(lede), common)
    assert "Stay up-to-date" not in out and "Get the full content" not in out
    assert "Beijing, Oct. 5 (Xinhua/GNA) - Andrea Triboldi, an Italian tourist, arrived in China" in out
    assert boilerplate.strip(lede, common) == lede
    assert boilerplate.strip(lede, frozenset()) == lede


def test_strip_works_on_scripts_written_without_spaces():
    tail = "更多精彩内容请关注本台官方网站和客户端"
    summaries = ["第%d条新闻讲的是一件完全不同的事情%s" % (i, tail) for i in range(12)]
    common = boilerplate.common_shingles(summaries)
    assert common
    out = boilerplate.strip("北京今天举行了新闻发布会介绍经济形势" + tail, common)
    assert out.startswith("北京今天举行了新闻发布会介绍经济形势") and "官方网站" not in out


# ---------------------------------------------------------------------------
# the per feed cache
# ---------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    c = store.connect(tmp_path / "b.db")
    c.execute("INSERT INTO outlets(id, name, country, language, tier, active, feeds, updated_at) "
              "VALUES ('gh_gna', 'Ghana News Agency', 'GHA', 'en', 'agency', 1, '[]', '2026-10-01')")
    c.commit()
    return c


def _add(conn, n, outlet="gh_gna", feed=FEED, summary=None, title=None, days=0.0, url=None):
    aid = store.insert_discovered(conn, {
        "url": url or "https://example.org/%s/%d" % (outlet, n), "outlet_id": outlet, "country": "GHA", "language": "en",
        "feed_url": feed, "title": title or "Headline number %d on a local matter" % n,
        "summary": summary if summary is not None else _wrapped(LEDES[n % len(LEDES)]),
        "status": "fetched", "gate_relevant": 1})
    at = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=days)
    conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (at.isoformat(), aid))
    conn.commit()
    return aid


def test_feed_boilerplate_is_per_feed_and_cached(conn, tmp_path):
    for i in range(12):
        _add(conn, i)
    for i in range(12):
        _add(conn, 100 + i, feed="https://gna.org.gh/feed/", summary=_own(100 + i))
    fb = boilerplate.FeedBoilerplate(conn)
    assert "get the full content now" in fb.shingles("gh_gna", FEED)
    assert fb.shingles("gh_gna", "https://gna.org.gh/feed/") == frozenset()
    assert fb.shingles("gh_gna", None) == frozenset() and fb.shingles(None, FEED) == frozenset()
    conn.commit()
    cached = dict(conn.execute("SELECT feed_url, items FROM feed_boilerplate").fetchall())
    assert cached == {FEED: 12, "https://gna.org.gh/feed/": 12}
    # A later run reads the cache instead of the articles.
    conn.execute("UPDATE articles SET summary='changed' WHERE outlet_id='gh_gna'")
    conn.commit()
    assert "get the full content now" in boilerplate.FeedBoilerplate(conn).shingles("gh_gna", FEED)
    # Once the cache is a day old it is computed again.
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=boilerplate.CACHE_HOURS + 1)
    assert boilerplate.FeedBoilerplate(conn, now=later).shingles("gh_gna", FEED) == frozenset()


def test_read_only_connection_computes_and_writes_nothing(conn, tmp_path):
    for i in range(12):
        _add(conn, i)
    conn.close()
    ro = sqlite3.connect("file:%s?mode=ro" % (tmp_path / "b.db"), uri=True)
    ro.row_factory = sqlite3.Row
    fb = boilerplate.FeedBoilerplate(ro)
    assert "get the full content now" in fb.shingles("gh_gna", FEED)
    assert ro.execute("SELECT name FROM sqlite_master WHERE name='feed_boilerplate'").fetchone() is None


def test_recent_summaries_are_the_month_before_the_feed_latest_item(conn):
    for i in range(12):
        _add(conn, i, days=-60)          # an old wrapper the feed has since dropped
    for i in range(12):
        _add(conn, 50 + i, summary=_own(50 + i))
    assert boilerplate.FeedBoilerplate(conn).shingles("gh_gna", FEED) == frozenset()


def test_summary_looks_up_the_feed_of_a_row_without_one(conn):
    ids = [_add(conn, i) for i in range(12)]
    fb = boilerplate.FeedBoilerplate(conn)
    out = fb.summary({"id": ids[0], "outlet_id": "gh_gna", "summary": _wrapped(LEDES[0])})
    assert "GNA - %s" % _own(1) in out and "Stay up-to-date" not in out
    out = fb.summary({"article_id": ids[1], "summary": _wrapped(LEDES[1])})
    assert "Stay up-to-date" not in out
    assert boilerplate.FeedBoilerplate(None).summary({"summary": "x"}) == "x"


# ---------------------------------------------------------------------------
# summary matching
# ---------------------------------------------------------------------------

def test_recall_audit_no_longer_counts_the_wrapper_as_a_lookalike(conn):
    ids = [_add(conn, i, summary=_wrapped(_own(i, 4))) for i in range(12)]
    store.insert_classification(conn, ids[0], "rules", "A", 1.0, signatures_fired=["xinhua"])
    for aid in ids[1:]:
        store.insert_classification(conn, aid, "rules", "C", 0.75, signatures_fired=["residual_c"])
    conn.commit()
    rows, refs = recall_audit.population(conn), recall_audit.state_origin_items(conn)
    assert len(recall_audit.lookalikes(rows, refs)) == 11, "the shared wrapper alone passes the summary test"
    assert recall_audit.lookalikes(rows, refs, boiler=recall_audit.feed_boilerplate(conn)) == {}


def test_route_finds_a_lookalike_the_wrapper_diluted(conn):
    """The wrapper also hides real lookalikes: the same lede is under half of a wrapped summary."""
    for i in range(12):
        _add(conn, i)
    lede = ("The Chinese embassy in Accra has donated forty thousand textbooks to basic schools in the "
            "Northern Region under a programme agreed with the education ministry last year")
    aid = _add(conn, 99, summary=_wrapped(lede), title="Embassy donates books to schools")
    ref = {"article_id": 1000, "outlet_id": "cn_xinhua", "outlet_name": "Xinhua", "language": "en",
           "title": "China supports education in Ghana with donation", "summary": lede,
           "discovered_at": "2026-10-01T00:00:00+00:00"}
    row = dict(conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone())
    assert lookalike.RouteIndex([ref]).match(row) is None
    hit = lookalike.RouteIndex([ref], boiler=boilerplate.FeedBoilerplate(conn)).match(row)
    assert hit and hit["method"] == "summary" and hit["article_id"] == 1000


def test_build_index_strips_boilerplate(conn):
    ids = [_add(conn, i) for i in range(12)]
    store.insert_classification(conn, ids[0], "rules", "A", 1.0, signatures_fired=["xinhua"])
    conn.commit()
    idx = lookalike.build_index(conn, [dict(conn.execute("SELECT * FROM articles WHERE id=?", (ids[1],)).fetchone())])
    assert idx.boiler is not None
    assert "Stay up-to-date" not in idx._summary(idx.refs[ids[0]])


# ---------------------------------------------------------------------------
# the keyword gate
# ---------------------------------------------------------------------------

def test_gate_ignores_a_china_term_in_the_feed_wrapper():
    wrap = "Read more of our China and Asia coverage on the website every day of the week"
    summaries = ["Local council approves budget item %d %s" % (i, wrap) for i in range(12)]
    common = boilerplate.common_shingles(summaries)
    summary = "Local council approves new road budget. " + wrap
    assert gate.check("Council approves road budget", summary, "en")[0]
    ok, terms = gate.check("Council approves road budget", summary, "en", feed_boilerplate=common)
    assert not ok and terms == []
    # a China term outside the wrapper still counts
    assert gate.check("Council approves road budget", "Chinese firm wins the contract. " + wrap, "en",
                      feed_boilerplate=common)[0]


def test_gate_signature_still_reads_the_whole_summary():
    wrap = "PR Newswire -- The Information Office of the Haikou Municipal Government announced the programme."
    summaries = ["Item %d on an investment week %s" % (i, wrap) for i in range(12)]
    common = boilerplate.common_shingles(summaries)
    assert common
    ok, terms = gate.check("Investment week opens with record deals", "Item 99 on an investment week " + wrap, "en",
                           feed_boilerplate=common)
    assert ok and any(t.startswith("state_origin_signature:") for t in terms)
