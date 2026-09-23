"""A global press release wire is counted as its own stratum and never inside a country: not in the
counts, not in the routes, the themes or the article list, and not in any denominator. The first cut
of this left the wires in the daily build's own loop and in the discovery denominators, so a route
filtered view of the United States came out larger than its unfiltered total."""
import json

from pipeline import export, registry, store

OUTLETS = [
    {"id": "us_paper", "name": "Paper", "country": "USA", "language": "en", "feeds": ["https://paper.test/rss"], "tier": "national_daily", "active": True},
    {"id": "us_wire", "name": "Wire", "country": "USA", "language": "en", "feeds": ["https://wire.test/rss"], "tier": "distribution_wire", "active": True},
]


def _article(conn, url, outlet, category, route):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": "USA", "language": "en",
                                         "title": "China " + url, "status": "fetched", "gate_relevant": 1})
    conn.execute("UPDATE articles SET discovered_at='2026-09-10T09:00:00+00:00', status='classified' WHERE id=?", (aid,))
    store.insert_classification(conn, aid, "rules", category, 1.0, route=route)


def test_wires_are_in_no_country_figure(tmp_path):
    conn = store.connect(tmp_path / "w.db")
    _article(conn, "https://paper.test/1", "us_paper", "A", "wire_credit")
    for i in range(5):
        _article(conn, "https://wire.test/%d" % i, "us_wire", "A", "distribution_stamp")
    conn.execute("INSERT OR REPLACE INTO daily_outlet_discovery(date, outlet_id, country, discovered, gate_relevant) VALUES ('2026-09-10','us_paper','USA',20,1)")
    conn.execute("INSERT OR REPLACE INTO daily_outlet_discovery(date, outlet_id, country, discovered, gate_relevant) VALUES ('2026-09-10','us_wire','USA',900,5)")
    conn.commit()
    roll = export.rebuild_rollups(conn, OUTLETS)
    assert roll["distribution_wires"]["A"] == 5
    day = export.build_daily(conn, OUTLETS)["2026-09"]["days"]["2026-09-10"]
    usa = day["countries"]["USA"]
    assert usa["A"] == 1, "the wire's five items are not the United States' state origin"
    assert usa["disc"] == 20 and usa["tdisc"] == 20, "nor are its 900 releases part of the country's output"
    assert day["routes"]["USA"] == {"wire_credit": 1}, "a route filtered view must never exceed the total"
    articles = export.build_articles(conn, outlets=OUTLETS)
    assert [a["outlet_id"] for a in articles.get("USA", [])] == ["us_paper"]
    ids, _ = registry.top_outlets(OUTLETS)
    assert ids["USA"] == {"us_paper"}
