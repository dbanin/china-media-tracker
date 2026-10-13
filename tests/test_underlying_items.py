"""Underlying items count each item once per window and scope. They used to be distinct within one day
and country only, then summed over the days of a window and across countries, so a story placed on
three days of a 30 day window, or in two countries, counted as three, or two, underlying items."""
import datetime as dt

from pipeline import config, export, store

OUTLETS = [
    {"id": "it_paper", "name": "Carta", "country": "ITA", "language": "it", "feeds": ["https://carta.test/rss"], "tier": "national_daily", "active": True},
    {"id": "de_paper", "name": "Blatt", "country": "DEU", "language": "de", "feeds": ["https://blatt.test/rss"], "tier": "national_daily", "active": True},
    {"id": "wire", "name": "Wire", "country": "USA", "language": "en", "feeds": ["https://wire.test/rss"], "tier": "distribution_wire", "active": True},
]


def _day(days_ago):
    return (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days_ago)).isoformat()


def _article(conn, url, outlet, country, category, days_ago, group=None):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": country, "language": "en",
                                         "title": "China " + url, "status": "fetched", "gate_relevant": 1})
    conn.execute("UPDATE articles SET discovered_at=?, status='classified', dup_group_id=? WHERE id=?",
                 (_day(days_ago) + "T09:00:00+00:00", group, aid))
    store.insert_classification(conn, aid, "rules", category, 1.0)
    conn.commit()
    return aid


def _seed(tmp_path):
    conn = store.connect(tmp_path / "u.db")
    x = _article(conn, "https://carta.test/x1", "it_paper", "ITA", "A", 3)
    # The same story on two more days in Italy, in Germany, and on a wire.
    _article(conn, "https://carta.test/x2", "it_paper", "ITA", "A", 2, group=x)
    _article(conn, "https://carta.test/x3", "it_paper", "ITA", "A", 1, group=x)
    _article(conn, "https://blatt.test/x", "de_paper", "DEU", "A", 2, group=x)
    _article(conn, "https://wire.test/x", "wire", "USA", "A", 1, group=x)
    # A second Italian story, and an unchecked state sourcing placement of the first one.
    _article(conn, "https://carta.test/y", "it_paper", "ITA", "A", 2)
    _article(conn, "https://carta.test/xb", "it_paper", "ITA", "B", 1, group=x)
    # An old Italian story outside the 30 day block.
    _article(conn, "https://carta.test/old", "it_paper", "ITA", "A", 45)
    return conn


def test_latest_counts_each_item_once_per_window_and_scope(tmp_path):
    conn = _seed(tmp_path)
    export.rebuild_rollups(conn, OUTLETS)
    per_day = conn.execute("SELECT SUM(n_unique_items) FROM daily_counts WHERE country='ITA' AND category='A' AND scope='all'").fetchone()[0]
    assert per_day == 5, "the per day figures, summed, count the Italian story once per day"
    latest = export.build_latest(conn, OUTLETS, [], population={})
    ita = latest["countries"]["ITA"]
    assert ita["last_30d"]["A"] == 4 and ita["last_30d"]["uniqA"] == 2
    assert ita["all_time"]["A"] == 5 and ita["all_time"]["uniqA"] == 3
    # Per label: the B placement is one unchecked state sourcing item, beside the state origin items.
    assert ita["last_30d"]["uniqAB"] - ita["last_30d"]["uniqA"] == 1
    assert latest["countries"]["DEU"]["last_30d"]["uniqA"] == 1
    assert latest["countries"]["WLD"]["last_30d"]["uniqA"] == 1
    world = latest["totals"]["last_30d"]
    assert world["A"] == 6 and world["uniqA"] == 2, "the story carried in Italy, Germany and on a wire is one item worldwide"
    assert latest["totals"]["all_time"]["uniqA"] == 3 and world["uniqAB"] == 3


def test_daily_files_carry_the_item_ids(tmp_path):
    conn = _seed(tmp_path)
    export.rebuild_rollups(conn, OUTLETS)
    daily = export.build_daily(conn, OUTLETS)
    days = {d: e for m in daily.values() for d, e in m["days"].items()}
    x = conn.execute("SELECT id FROM articles WHERE url='https://carta.test/x1'").fetchone()[0]
    one = days[_day(1)]["underlying_items"]
    assert one["ITA"] == {"A": [x], "B": [x]} and one["WLD"] == {"A": [x]}
    two = days[_day(2)]["underlying_items"]
    assert two["DEU"] == {"A": [x]} and len(two["ITA"]["A"]) == 2 and "B" not in two["ITA"]
    for d, e in days.items():
        for iso, c in e["countries"].items():
            ids = e["underlying_items"].get(iso, {})
            assert c["uniqA"] == len(ids.get("A", [])), "a day's own figure equals its ids"
            assert c["uniqAB"] == len(ids.get("A", [])) + len(ids.get("B", []))


def test_schema_version_names_the_item_ids():
    assert config.SCHEMA_VERSION >= 12
