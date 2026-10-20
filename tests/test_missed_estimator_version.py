"""The missed-items estimator version on every feed_polls row (2026-10-12).

Rows made before the estimator changed carry estimates that inflated batch publishers and short
windows, and cannot be made again because single poll times were not kept. Each row now names the
estimator that made it, and the export reports the estimate only from rows of the current one, saying
from which day, while polls and saturated polls stay counted over every row."""
import datetime as dt
import json
import sqlite3
import types

import pytest

from pipeline import config, export, relay, store

FEED = "https://x.test/feed"
TODAY = None        # set per test by _one_day: the day store's clock is held on


@pytest.fixture(autouse=True)
def _one_day(monkeypatch):
    """store's clock held at the instant the test starts, so the rows it writes and TODAY name the same
    day even when the test runs across midnight (a module level TODAY, read at collection, did not)."""
    global TODAY
    held = store.dt.datetime.now(dt.timezone.utc)
    real = store.dt.datetime

    class Held(real):
        @classmethod
        def now(cls, tz=None):
            return held if tz is not None else held.replace(tzinfo=None)
    clock = types.SimpleNamespace(**{k: getattr(store.dt, k) for k in dir(store.dt) if not k.startswith("__")})
    clock.datetime = Held
    monkeypatch.setattr(store, "dt", clock)
    TODAY = held.date().isoformat()
OUTLETS = [{"id": "o", "name": "O", "country": "ITA", "language": "it", "feeds": [FEED], "tier": "regional",
            "active": True}]


def _old_row(conn, date="2026-10-01", polls=10, sat=8, miss=500.0, collector="hosted"):
    """A row as the earlier estimator left it."""
    conn.execute("""INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, saturated, entries, new_items,
                                          missed_estimate, collector, estimator) VALUES (?,?,?,?,?,?,?,?,?,?, 'v1')""",
                 (date, FEED, "o", "ITA", polls, sat, 20 * polls, 20 * sat, miss, collector))
    conn.commit()


def _current_polls(conn, n=12, sat=6, miss=4.0):
    for i in range(n):
        store.record_feed_poll(conn, "o", "ITA", FEED, 20, 20 if i < sat else 3, i < sat, miss if i < sat else 0.0)
    conn.commit()


def test_rows_already_recorded_become_v1_and_new_rows_carry_the_current_version(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.execute("""CREATE TABLE feed_polls (date TEXT NOT NULL, feed_url TEXT NOT NULL, outlet_id TEXT NOT NULL,
                   country TEXT NOT NULL, polls INTEGER NOT NULL DEFAULT 0, saturated INTEGER NOT NULL DEFAULT 0,
                   entries INTEGER NOT NULL DEFAULT 0, new_items INTEGER NOT NULL DEFAULT 0,
                   missed_estimate REAL NOT NULL DEFAULT 0, collector TEXT NOT NULL DEFAULT 'hosted',
                   PRIMARY KEY(date, feed_url, collector))""")
    old.execute("""CREATE TABLE feed_poll_log (feed_url TEXT NOT NULL, collector TEXT NOT NULL, polled_at TEXT NOT NULL,
                   outlet_id TEXT NOT NULL, country TEXT NOT NULL, entries INTEGER NOT NULL DEFAULT 0,
                   new_items INTEGER NOT NULL DEFAULT 0, times TEXT, prev_poll_at TEXT,
                   saturated_own INTEGER NOT NULL DEFAULT 0, missed_own REAL NOT NULL DEFAULT 0,
                   saturated INTEGER NOT NULL DEFAULT 0, missed_estimate REAL NOT NULL DEFAULT 0,
                   PRIMARY KEY(feed_url, polled_at, collector))""")
    old.execute("INSERT INTO feed_polls VALUES ('2026-10-01', ?, 'o', 'ITA', 4, 1, 80, 30, 2.5, 'hosted')", (FEED,))
    old.execute("INSERT INTO feed_poll_log VALUES (?, 'hosted', '2026-10-11T08:00:00+00:00', 'o', 'ITA', 20, 20, '[]',"
                " NULL, 1, 9.0, 1, 9.0)", (FEED,))
    old.commit()
    old.close()
    conn = store.connect(path)
    assert [tuple(r) for r in conn.execute("SELECT date, polls, missed_estimate, collector, estimator FROM feed_polls")] == \
        [("2026-10-01", 4, 2.5, "hosted", "v1")]
    assert conn.execute("SELECT estimator FROM feed_poll_log").fetchone()[0] == "v1"
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    assert {"feed_polls.estimator", "feed_poll_log.estimator"} <= done
    store.record_feed_poll(conn, "o", "ITA", FEED, 20, 3, False, 0.0)
    store.log_feed_poll(conn, FEED, "hosted", store.utcnow(), "o", "ITA", 20, 3, [], None, False, 0.0)
    conn.commit()
    assert conn.execute("SELECT estimator FROM feed_polls WHERE date=?", (TODAY,)).fetchone()[0] == \
        config.MISSED_ESTIMATOR_VERSION
    assert conn.execute("SELECT estimator FROM feed_poll_log WHERE polled_at>'2026-10-12'").fetchone()[0] == \
        config.MISSED_ESTIMATOR_VERSION
    conn.close()
    again = store.connect(path)       # a second opening finds nothing to do
    assert again.execute("SELECT COUNT(*) FROM feed_polls WHERE estimator='v1'").fetchone()[0] == 1


def test_a_database_from_before_the_collector_column_gets_both(tmp_path):
    path = tmp_path / "older.db"
    old = sqlite3.connect(str(path))
    old.execute("""CREATE TABLE feed_polls (date TEXT NOT NULL, feed_url TEXT NOT NULL, outlet_id TEXT NOT NULL,
                   country TEXT NOT NULL, polls INTEGER NOT NULL DEFAULT 0, saturated INTEGER NOT NULL DEFAULT 0,
                   entries INTEGER NOT NULL DEFAULT 0, new_items INTEGER NOT NULL DEFAULT 0,
                   missed_estimate REAL NOT NULL DEFAULT 0, PRIMARY KEY(date, feed_url))""")
    old.execute("INSERT INTO feed_polls VALUES ('2026-10-01', ?, 'o', 'ITA', 4, 1, 80, 30, 2.5)", (FEED,))
    old.commit()
    old.close()
    conn = store.connect(path)
    assert tuple(conn.execute("SELECT collector, estimator FROM feed_polls").fetchone()) == (config.COLLECTOR, "v1")


def test_a_day_estimated_by_two_versions_is_mixed(tmp_path):
    conn = store.connect(tmp_path / "m.db")
    store.record_feed_poll(conn, "o", "ITA", FEED, 20, 20, True, 40.0, estimator="v1")
    store.record_feed_poll(conn, "o", "ITA", FEED, 20, 20, True, 4.0)
    store.record_feed_poll(conn, "o", "ITA", FEED, 20, 3, False, 0.0)
    r = conn.execute("SELECT polls, saturated, missed_estimate, estimator FROM feed_polls").fetchone()
    assert tuple(r) == (3, 2, 44.0, store.ESTIMATOR_MIXED)
    # a row made by one version throughout keeps it
    store.record_feed_poll(conn, "o", "ITA", FEED + "2", 20, 3, False, 0.0)
    store.record_feed_poll(conn, "o", "ITA", FEED + "2", 20, 3, False, 0.0)
    assert conn.execute("SELECT estimator FROM feed_polls WHERE feed_url=?", (FEED + "2",)).fetchone()[0] == \
        config.MISSED_ESTIMATOR_VERSION


def test_the_export_reports_the_estimate_from_current_rows_only(tmp_path):
    conn = store.connect(tmp_path / "e.db")
    _old_row(conn)
    _current_polls(conn)
    # meta: polls and saturated over every row, the estimate over the current rows and from their first day
    latest = export.build_latest(conn, OUTLETS, [], population={})
    fs = export.build_meta(conn, OUTLETS, [], latest)["feed_saturation"]
    assert (fs["polls"], fs["saturated"], fs["missed_estimate"], fs["estimated_polls"]) == (22, 14, 24, 12)
    assert fs["missed_estimate_since"] == TODAY and fs["estimator"] == config.MISSED_ESTIMATOR_VERSION
    # the daily files: the old day keeps its polls and has no estimate
    daily = export.build_daily(conn, OUTLETS)
    old = daily["2026-10"]["days"]["2026-10-01"]["countries"]["ITA"]
    assert (old["polls"], old["sat"], old["miss"], old["mpolls"]) == (10, 8, 0, 0)
    new = daily[TODAY[:7]]["days"][TODAY]["countries"]["ITA"]
    assert (new["polls"], new["sat"], new["miss"], new["mpolls"]) == (12, 6, 24, 12)
    # the country warning says what the estimate covers and since when
    at = latest["countries"]["ITA"]["all_time"]
    assert (at["polls"], at["miss"], at["mpolls"]) == (22, 24, 12)
    warn = [w["text"] for w in latest["countries"]["ITA"]["warnings"] if w["type"] == "feed_saturation"]
    assert warn and "an estimated 24 were missed over 12 of these polls (estimated since %s)" % TODAY in warn[0]
    assert "500" not in warn[0]
    # outlets.json
    counts = export.build_outlets(conn, OUTLETS)["outlets"][0]["counts"]
    assert (counts["polls"], counts["sat"], counts["miss"], counts["mpolls"]) == (22, 14, 24, 12)


def test_before_any_current_row_the_estimate_is_absent_not_zero(tmp_path):
    conn = store.connect(tmp_path / "v1.db")
    _old_row(conn, polls=12, sat=8)
    latest = export.build_latest(conn, OUTLETS, [], population={})
    fs = export.build_meta(conn, OUTLETS, [], latest)["feed_saturation"]
    assert (fs["polls"], fs["missed_estimate"], fs["estimated_polls"], fs["missed_estimate_since"]) == (12, 0, 0, None)
    warn = [w["text"] for w in latest["countries"]["ITA"]["warnings"] if w["type"] == "feed_saturation"]
    assert warn and "no estimate of the items missed yet (estimated since %s)" % config.MISSED_ESTIMATOR_VERSION in warn[0]


def test_the_relay_bundle_carries_the_version_and_an_older_bundle_counts_as_v1(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    src = store.connect(tmp_path / "relay.db")
    store.record_feed_poll(src, "o_relay", "ITA", "https://relay.test/feed", 20, 20, True, 3.0, collector="self_hosted")
    src.commit()
    data = relay.build_bundle(src)
    polls = [it for it in relay.iter_bundle(data) if it.get("_type") == "feed_poll"]
    assert polls[0]["estimator"] == config.MISSED_ESTIMATOR_VERSION
    dst = store.connect(tmp_path / "main.db")
    relay.ingest(dst, data, outlets=[])
    assert dst.execute("SELECT estimator FROM feed_polls WHERE collector='self_hosted'").fetchone()[0] == \
        config.MISSED_ESTIMATOR_VERSION
    # a bundle from a relay older than the field: its row is replaced and marked v1
    old_item = dict(polls[0], polls=2)
    del old_item["estimator"]
    relay.ingest(dst, _bundle([old_item]), outlets=[])
    assert tuple(dst.execute("SELECT polls, estimator FROM feed_polls WHERE collector='self_hosted'").fetchone()) == (2, "v1")


def _bundle(items):
    import gzip
    return gzip.compress("".join(json.dumps(i) + "\n" for i in items).encode("utf-8"))


def test_a_relay_poll_event_keeps_its_estimators_version(tmp_path):
    conn = store.connect(tmp_path / "main.db")
    at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    ev = {"_type": "feed_poll_event", "feed_url": FEED, "outlet_id": "o", "country": "ITA", "entries": 20,
          "new_items": 20, "times": "[]", "prev_poll_at": (at - dt.timedelta(hours=3)).isoformat(),
          "saturated_own": 1, "missed_own": 7.0}
    # no estimator field: an event from an older relay, its estimate the old estimator's
    relay.reconcile_poll(conn, dict(ev, polled_at=(at - dt.timedelta(hours=1)).isoformat()))
    assert conn.execute("SELECT estimator FROM feed_polls").fetchone()[0] == "v1"
    assert conn.execute("SELECT estimator FROM feed_poll_log").fetchone()[0] == "v1"
    conn.execute("DELETE FROM feed_polls")
    conn.execute("DELETE FROM feed_poll_log")
    relay.reconcile_poll(conn, dict(ev, polled_at=at.isoformat(), estimator=config.MISSED_ESTIMATOR_VERSION))
    assert tuple(conn.execute("SELECT polls, missed_estimate, estimator FROM feed_polls").fetchone()) == \
        (1, 7.0, config.MISSED_ESTIMATOR_VERSION)


def test_relay_poll_events_carry_the_version(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    src = store.connect(tmp_path / "relay.db")
    store.log_feed_poll(src, FEED, "self_hosted", store.utcnow(), "o", "ITA", 20, 4, [], None, False, 0.0)
    src.commit()
    events = [it for it in relay.iter_bundle(relay.build_bundle(src)) if it.get("_type") == "feed_poll_event"]
    assert events and events[0]["estimator"] == config.MISSED_ESTIMATOR_VERSION


def test_methodology_says_since_when_the_estimate_runs(tmp_path, monkeypatch):
    from pipeline import methodology
    db = tmp_path / "m.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    out = tmp_path / "out"
    conn = store.connect(db)
    _old_row(conn)
    _current_polls(conn)
    export.run(conn, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M0.md")
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    path = tmp_path / "M.md"
    methodology.write(meta, latest, path=path)
    text = path.read_text(encoding="utf-8")
    assert "14 of 22 polls; an estimated 24 items missed over the 12 polls estimated since %s" % TODAY in text
    assert "counts only the polls made by the current estimator" in text
    meta["feed_saturation"].update(estimated_polls=0, missed_estimate=0, missed_estimate_since=None)
    methodology.write(meta, latest, path=path)
    assert "items missed not yet estimated by the estimator of %s" % config.MISSED_ESTIMATOR_VERSION in \
        path.read_text(encoding="utf-8")
