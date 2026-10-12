"""The per section feed relevance guard (fetch_feeds.section_feed_flooding).

Section feeds skip the keyword gate because paid state placements often name no China term in the
headline. When discovery registers a whole editorial desk as a section (Index.hr's sport page,
recorded as sponsored because its header reads "Sport sponsored by": 3,650 items in 30 days, two of
them relevant), the exemption floods the fetch queue. A feed that delivered more than
SECTION_GUARD_MIN_ITEMS resolved items in the trailing window with under
SECTION_GUARD_MAX_RELEVANT_SHARE classified as anything other than not_relevant is gated like an
editorial feed until it earns the exemption back.
"""
import datetime as dt

from pipeline import fetch_feeds, registry, store

FEED = "https://hr.test/sport"
OUTLET = {"id": "hr_test", "name": "HR", "country": "HRV", "language": "hr",
          "feeds": ["https://hr.test/rss"], "tier": "national_daily", "active": True,
          "section_feeds": [{"url": FEED, "kind": "sponsored", "section": FEED, "type": "page"}]}


def _seed(conn, n, relevant=0, pending=0, ledger=None, feed=FEED, days_ago=1):
    """n items from the feed discovered days_ago, the first `relevant` classified C, the next `pending`
    still queued, the rest classified not_relevant. ledger adds that many new_items to feed_polls on
    the same day, defaulting to n."""
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).replace(microsecond=0).isoformat()
    for i in range(n):
        aid = store.insert_discovered(conn, {"url": "%s/story-%d-%d" % (feed, days_ago, i), "outlet_id": OUTLET["id"],
                                             "country": "HRV", "language": "hr", "feed_url": feed,
                                             "title": "Story %d" % i, "status": "queued", "gate_relevant": 1,
                                             "gate_terms": ["section:sponsored"]})
        conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (when, aid))
        if i < relevant:
            store.insert_classification(conn, aid, "rules", "C", 0.75)
        elif i < relevant + pending:
            pass
        else:
            store.insert_classification(conn, aid, "rules", "not_relevant", 0.9)
    conn.execute("INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, entries, new_items) VALUES (?,?,?,?,1,?,?)",
                 (when[:10], feed, OUTLET["id"], "HRV", n, n if ledger is None else ledger))
    conn.commit()


def test_a_feed_under_the_item_threshold_stays_exempt(tmp_path):
    conn = store.connect(tmp_path / "a.db")
    _seed(conn, fetch_feeds.SECTION_GUARD_MIN_ITEMS, relevant=0)
    assert fetch_feeds.section_feed_flooding(conn, FEED) is None


def test_a_busy_feed_with_almost_nothing_relevant_loses_the_exemption(tmp_path):
    conn = store.connect(tmp_path / "b.db")
    n = fetch_feeds.SECTION_GUARD_MIN_ITEMS + 100
    _seed(conn, n, relevant=2)
    assert fetch_feeds.section_feed_flooding(conn, FEED) == {"resolved": n, "relevant": 2}


def test_a_feed_with_enough_relevant_items_keeps_the_exemption(tmp_path):
    conn = store.connect(tmp_path / "c.db")
    n = fetch_feeds.SECTION_GUARD_MIN_ITEMS + 100
    enough = int(n * fetch_feeds.SECTION_GUARD_MAX_RELEVANT_SHARE) + 1
    _seed(conn, n, relevant=enough)
    assert fetch_feeds.section_feed_flooding(conn, FEED) is None


def test_pending_items_are_not_read_as_irrelevance(tmp_path):
    """A backlog awaiting fetch or verdict is subtracted: 600 delivered, 200 still queued, is 400
    resolved, under the threshold."""
    conn = store.connect(tmp_path / "d.db")
    _seed(conn, 600, relevant=0, pending=200)
    assert fetch_feeds.section_feed_flooding(conn, FEED) is None
    # once they resolve as not_relevant the feed is guarded
    for row in conn.execute("SELECT id FROM articles WHERE status='queued'").fetchall():
        store.insert_classification(conn, row["id"], "rules", "not_relevant", 0.9)
    conn.commit()
    assert fetch_feeds.section_feed_flooding(conn, FEED) == {"resolved": 600, "relevant": 0}


def test_the_poll_ledger_keeps_a_guarded_feed_guarded_after_pruning(tmp_path):
    """Once guarded, the feed's items are gated out and pruned after three days, so the rows present
    shrink; the feed_polls ledger still says what the feed delivered."""
    conn = store.connect(tmp_path / "e.db")
    _seed(conn, 10, relevant=0, ledger=900)
    assert fetch_feeds.section_feed_flooding(conn, FEED) == {"resolved": 900, "relevant": 0}


def test_rows_present_cover_a_feed_the_ledger_missed(tmp_path):
    """Re-admitted items are never in the ledger and some outlets have no ledger rows at all."""
    conn = store.connect(tmp_path / "f.db")
    _seed(conn, 700, relevant=0, ledger=0)
    assert fetch_feeds.section_feed_flooding(conn, FEED) == {"resolved": 700, "relevant": 0}


def test_items_outside_the_window_do_not_count(tmp_path):
    conn = store.connect(tmp_path / "g.db")
    _seed(conn, 700, relevant=0, days_ago=fetch_feeds.SECTION_GUARD_DAYS + 5)
    assert fetch_feeds.section_feed_flooding(conn, FEED) is None


def _poll(entries):
    return [{"outlet": OUTLET, "feed_url": FEED, "kind": "sponsored",
             "result": {"ok": True, "entries": entries, "error": None, "status": 200}}]


ENTRIES = [
    {"link": "https://hr.test/sport/hajduk-dinamo-derbi-rezultat", "title": "Hajduk i Dinamo odigrali derbi bez pogodaka", "summary": ""},
    {"link": "https://hr.test/sport/kina-olimpijski-tim", "title": "Kina je objavila sastav olimpijskog tima", "summary": ""},
]


def test_the_poll_loop_applies_the_gate_to_a_flooding_section_feed(monkeypatch, tmp_path):
    conn = store.connect(tmp_path / "h.db")
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: _poll(ENTRIES))
    # Not flooding yet: both items are admitted on the section exemption.
    counts = fetch_feeds.run(conn, "t1", outlets=[OUTLET])
    assert counts["section_feeds_guarded"] == 0 and counts["section_items"] == 2
    rows = {r["url"]: r for r in conn.execute("SELECT url, status, gate_terms FROM articles")}
    assert all(r["status"] == "queued" and "section:sponsored" in r["gate_terms"] for r in rows.values())
    conn.execute("DELETE FROM articles")
    conn.commit()

    # Flooding: the keyword gate applies, so the China headline is still admitted and the derby is not.
    _seed(conn, fetch_feeds.SECTION_GUARD_MIN_ITEMS + 100, relevant=0)
    counts = fetch_feeds.run(conn, "t2", outlets=[OUTLET])
    assert counts["section_feeds_guarded"] == 1 and counts["section_items"] == 0
    rows = {r["url"]: r for r in conn.execute("SELECT url, status, gate_relevant, gate_terms FROM articles WHERE url LIKE '%/sport/%-%'")}
    derby, china = rows[ENTRIES[0]["link"]], rows[ENTRIES[1]["link"]]
    assert derby["status"] == "gated_out" and derby["gate_relevant"] == 0
    assert china["status"] == "queued" and china["gate_relevant"] == 1
    assert "section:" not in china["gate_terms"] and "Kina" in china["gate_terms"]


def test_a_gated_out_item_is_not_readmitted_through_a_guarded_feed(monkeypatch, tmp_path):
    """readmit_gated_out re-admits a gate-rejected URL when it turns up on a section feed; a guarded
    feed must not open that door either."""
    conn = store.connect(tmp_path / "i.db")
    editorial = [{"outlet": OUTLET, "feed_url": "https://hr.test/rss", "kind": registry.EDITORIAL,
                  "result": {"ok": True, "entries": ENTRIES[:1], "error": None, "status": 200}}]
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: editorial)
    fetch_feeds.run(conn, "t1", outlets=[OUTLET])
    assert conn.execute("SELECT status FROM articles WHERE url=?", (ENTRIES[0]["link"],)).fetchone()[0] == "gated_out"
    _seed(conn, fetch_feeds.SECTION_GUARD_MIN_ITEMS + 100, relevant=0)
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: _poll(ENTRIES[:1]))
    counts = fetch_feeds.run(conn, "t2", outlets=[OUTLET])
    assert counts["section_feeds_guarded"] == 1 and counts["readmitted_section"] == 0
    assert conn.execute("SELECT status FROM articles WHERE url=?", (ENTRIES[0]["link"],)).fetchone()[0] == "gated_out"
