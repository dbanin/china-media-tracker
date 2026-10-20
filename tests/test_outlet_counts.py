"""outlets.json counts: disc is the items discovered (the discovery counters the daily files read), with
the rows stored now beside it as stored; and the missed-items estimate is rounded once per feed_polls row,
so the outlet, day, country and overall totals add up to one another (the 2026-10-17 audits)."""
from pipeline import config, export, store

FEED_A, FEED_B = "https://a.test/feed", "https://b.test/feed"
OUTLETS = [{"id": "it_a", "name": "A", "country": "ITA", "language": "it", "feeds": [FEED_A], "tier": "regional",
            "active": True},
           {"id": "it_b", "name": "B", "country": "ITA", "language": "it", "feeds": [FEED_B], "tier": "regional",
            "active": True}]


def test_outlet_disc_is_discovery_not_rows_stored(tmp_path):
    conn = store.connect(tmp_path / "d.db")
    store.insert_discovered(conn, {"url": "https://a.test/1", "outlet_id": "it_a", "country": "ITA", "language": "it",
                                   "title": "Cina", "status": "queued", "gate_relevant": 1})
    for _ in range(5):              # five discovered, four of them gated out and pruned since
        store.record_discovery(conn, "ITA", False, "it_a", "2026-10-01")
    conn.commit()
    counts = {o["id"]: o["counts"] for o in export.build_outlets(conn, OUTLETS)["outlets"]}
    assert counts["it_a"]["disc"] == 5 and counts["it_a"]["stored"] == 1
    assert counts["it_b"]["disc"] == 0


def test_the_missed_estimate_is_rounded_once_and_every_total_agrees(tmp_path):
    conn = store.connect(tmp_path / "m.db")
    # Each feed's row for the day holds 1.4: rounded once per row the total is 2, while rounding the
    # country day's sum (2.8) and the overall sum gave 3 beside the outlets' 2.
    for feed, oid in ((FEED_A, "it_a"), (FEED_B, "it_b")):
        for miss in (0.4, 0.4, 0.6):
            store.record_feed_poll(conn, oid, "ITA", feed, 20, 20, True, miss)
    conn.commit()
    assert [round(r[0], 6) for r in conn.execute("SELECT missed_estimate FROM feed_polls")] == [1.4, 1.4]
    per_row = 2
    latest = export.build_latest(conn, OUTLETS, [], population={})
    meta = export.build_meta(conn, OUTLETS, [], latest)
    daily = export.build_daily(conn, OUTLETS)
    outlets = export.build_outlets(conn, OUTLETS)["outlets"]
    day = next(iter(next(iter(daily.values()))["days"].values()))["countries"]["ITA"]["miss"]
    totals = {"meta": meta["feed_saturation"]["missed_estimate"],
              "country": latest["countries"]["ITA"]["all_time"]["miss"],
              "outlets": sum(o["counts"]["miss"] for o in outlets), "day": day}
    assert set(totals.values()) == {per_row}, totals
