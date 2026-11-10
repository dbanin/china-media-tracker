"""The input watch (pipeline/input_watch.py), on small synthetic databases: which inputs are watched (every
polled input with an item in the window, networks counted once, inputs this collector does not poll), each
per input check firing on a real change and staying quiet on a steady state, small inputs and their
country groups, new against ongoing problems, the reference sources, the fetch backlog, the annotation
cap, ranking and escaping, the coverage line, the run_log row, and the workflow step."""
import datetime as dt
import io
import json

import yaml

from pipeline import config, input_watch as iw, store

NOW = dt.datetime(2026, 11, 9, 0, 27, tzinfo=dt.timezone.utc)
_seq = {"n": 0}


def _t(days=0.0, hours=0.0):
    return (NOW - dt.timedelta(days=days, hours=hours)).replace(microsecond=0).isoformat()


def _outlet(oid, network=None, active=True, collector=None):
    o = {"id": oid, "name": oid, "country": "AUS", "language": "en", "tier": "national",
         "feeds": ["https://%s.test/feed" % oid], "active": active}
    if network:
        o["network"] = network
    if collector:
        o["collector"] = collector
    return o


def _article(conn, oid, days, status="classified", relevant=1, category=None, group=None):
    _seq["n"] += 1
    n = _seq["n"]
    cur = conn.execute(
        """INSERT INTO articles(url, url_hash, outlet_id, country, language, discovered_at, status, gate_relevant,
                                dup_group_id) VALUES (?,?,?,?,?,?,?,?,?)""",
        ("https://%s.test/%d" % (oid, n), "h%d" % n, oid, "AUS", "en", _t(days), status, relevant, group))
    if category:
        conn.execute("""INSERT INTO classifications(article_id, method, category, confidence, ruleset_version,
                        classified_at) VALUES (?,?,?,?,?,?)""", (cur.lastrowid, "rules", category, 1.0, "t", _t(days)))
    return cur.lastrowid


def _labels(conn, oid, n, category="A", days=10, group_base=None):
    for i in range(n):
        _article(conn, oid, days, category=category, group=None if group_base is None else group_base + i)


def _healthy(conn, oid, ok_hours=0.5, newest_hours=1.0):
    conn.execute("""INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, consecutive_failures, newest_entry)
                    VALUES (?,?,?,?,0,?)""",
                 ("https://%s.test/feed" % oid, oid, _t(hours=0.2), _t(hours=ok_hours), _t(hours=newest_hours)))


def _discovered_since(conn, oid, days=60, every=1):
    """New items discovered on every `every`th day from `days` ago to today."""
    for d in range(0, days + 1, every):
        conn.execute("INSERT OR REPLACE INTO daily_outlet_discovery(date, outlet_id, country, discovered) VALUES (?,?,?,?)",
                     ((NOW - dt.timedelta(days=days - d)).date().isoformat(), oid, "AUS", 10))


def _steady(conn, oid, per_day=3, days=56, start=0):
    """per_day relevant classified items a day for `days` days, newest first at `start` days ago."""
    for d in range(start, days):
        for _ in range(per_day):
            _article(conn, oid, d + 0.1)


def _poll(conn, oid, new_items, missed, days=1, estimator=None, polls=10, saturated=2):
    conn.execute("""INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, saturated, entries, new_items,
                    missed_estimate, collector, estimator) VALUES (?,?,?,?,?,?,?,?,?,'hosted',?)""",
                 ((NOW - dt.timedelta(days=days)).date().isoformat(), "https://%s.test/feed" % oid, oid, "AUS", polls,
                  saturated, new_items, new_items, missed, estimator or config.MISSED_ESTIMATOR_VERSION))


def _stages(conn, hours=1.0):
    for stage in ("discover", "fetch"):
        conn.execute("INSERT INTO run_log(run_id, stage, started_at, finished_at, ok, counts) VALUES (?,?,?,?,1,?)",
                     ("r", stage, _t(hours=hours + 0.5), _t(hours=hours), json.dumps({"skipped_deadline": 0})))


def _db(tmp_path):
    return store.connect(tmp_path / "t.db")


def _checks(result, check=None):
    return [p for p in result["problems"] if check is None or p["check"] == check]


# --- which inputs are key ---------------------------------------------------------------------------------

def test_a_network_counts_once_and_its_shared_copies_weigh_one_item(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("au_a", "acm"), _outlet("au_b", "acm"), _outlet("au_c", "acm"), _outlet("solo")]
    for oid in ("au_a", "au_b", "au_c"):
        _labels(conn, oid, 10, group_base=1000)        # the same ten widget items in three papers
    _labels(conn, "solo", 25)
    key = iw.watched_inputs(conn, outlets, NOW)
    assert key["total"] == 35
    assert [(u["id"], u["weight"]) for u in key["units"]] == [("solo", 25), ("network:acm", 10)]
    assert key["units"][1]["members"] == ["au_a", "au_b", "au_c"]


def test_every_input_is_watched_largest_first_ties_by_id(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet(o) for o in ("big", "t2", "t1", "small")]
    _labels(conn, "big", 70)
    _labels(conn, "t1", 12, category="B")
    _labels(conn, "t2", 12)
    _labels(conn, "small", 1)
    key = iw.watched_inputs(conn, outlets, NOW)
    assert [u["id"] for u in key["units"]] == ["big", "t1", "t2", "small"]
    assert [u["rank"] for u in key["units"]] == [1, 2, 3, 4]
    assert key["covered"] == 95 and key["total"] == 95 and key["unpolled"] == 0


def test_there_is_no_cap_on_the_inputs_watched(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("o%d" % i) for i in range(300)]
    for i in range(300):
        _labels(conn, "o%d" % i, 1)
    key = iw.watched_inputs(conn, outlets, NOW)
    assert len(key["units"]) == 300 and key["covered"] == 300 and key["share"] == 1.0


def test_inputs_not_polled_count_in_the_total_but_are_not_watched(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("gone", active=False), _outlet("relay", collector="self_hosted"), _outlet("here")]
    _labels(conn, "gone", 50)
    _labels(conn, "relay", 30)
    _labels(conn, "here", 20)
    _labels(conn, "unknown_id", 5)                     # not in the registry at all
    key = iw.watched_inputs(conn, outlets, NOW)
    assert [u["id"] for u in key["units"]] == ["here"]
    assert key["total"] == 105 and key["unpolled"] == 85


def test_labels_outside_the_window_and_c_labels_do_not_count(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("a")]
    _labels(conn, "a", 5, days=iw.WINDOW_DAYS + 1)
    _labels(conn, "a", 5, category="C")
    assert iw.watched_inputs(conn, outlets, NOW)["total"] == 0
    assert iw.watched_inputs(conn, outlets, NOW)["units"] == []


def test_an_empty_database_is_quiet_apart_from_the_missing_stages(tmp_path):
    conn = _db(tmp_path)
    r = iw.check(conn, [], now=NOW, sources=[])
    assert {p["check"] for p in r["problems"]} == {"stage_stale"}
    assert r["key"]["units"] == [] and r["key"]["share"] == 0.0


# --- per input checks -------------------------------------------------------------------------------------

def _one_input(conn, oid="wire", per_day=3):
    outlets = [_outlet(oid)]
    _labels(conn, oid, 20)
    _discovered_since(conn, oid)
    _steady(conn, oid, per_day=per_day)
    _stages(conn)
    return outlets


def test_a_healthy_key_input_raises_nothing(tmp_path):
    conn = _db(tmp_path)
    outlets = _one_input(conn)
    _healthy(conn, "wire")
    _poll(conn, "wire", 500, 20)
    assert iw.check(conn, outlets, now=NOW, sources=[])["problems"] == []


def test_no_successful_poll_in_a_day_and_a_stale_newest_entry(tmp_path):
    conn = _db(tmp_path)
    outlets = _one_input(conn)
    _healthy(conn, "wire", ok_hours=30, newest_hours=80)
    r = iw.check(conn, outlets, now=NOW, sources=[])
    assert len(_checks(r, "no_poll")) == 1 and "30 hours" in _checks(r, "no_poll")[0]["line"]
    assert len(_checks(r, "stale_entries")) == 1 and "80 hours" in _checks(r, "stale_entries")[0]["line"]


def test_a_feed_never_polled_is_flagged_and_a_future_entry_cannot_hide_a_stale_one(tmp_path):
    conn = _db(tmp_path)
    outlets = _one_input(conn)
    r = iw.check(conn, outlets, now=NOW, sources=[])
    assert "has ever been polled" in _checks(r, "no_poll")[0]["line"]
    _healthy(conn, "wire", newest_hours=100)
    conn.execute("""INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, consecutive_failures, newest_entry)
                    VALUES ('https://wire.test/other', 'wire', ?, ?, 0, ?)""",
                 (_t(hours=0.2), _t(hours=0.2), (NOW + dt.timedelta(days=2)).isoformat()))
    r = iw.check(conn, outlets, now=NOW, sources=[])
    assert not _checks(r, "no_poll") and len(_checks(r, "stale_entries")) == 1


def test_one_working_feed_keeps_an_input_polled(tmp_path):
    conn = _db(tmp_path)
    outlets = _one_input(conn)
    _healthy(conn, "wire")
    conn.execute("""INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, consecutive_failures, newest_entry)
                    VALUES ('https://wire.test/dead', 'wire', ?, ?, 40, NULL)""", (_t(hours=0.2), _t(days=20)))
    assert not _checks(iw.check(conn, outlets, now=NOW, sources=[]), "no_poll")


def test_saturation_is_flagged_from_the_share_missed_over_the_week(tmp_path):
    conn = _db(tmp_path)
    outlets = _one_input(conn)
    _healthy(conn, "wire")
    _poll(conn, "wire", 300, 250, days=1)
    _poll(conn, "wire", 300, 250, days=3)
    _poll(conn, "wire", 300, 5000, days=9)                       # outside the week
    _poll(conn, "wire", 300, 5000, days=2, estimator="v1")       # an older estimator
    r = iw.check(conn, outlets, now=NOW, sources=[])
    line = _checks(r, "saturation")[0]["line"]
    assert "500 of 1,100 feed items (45%)" in line and "over 7 days" in line


def test_saturation_below_the_share_or_on_too_few_items_is_quiet(tmp_path):
    conn = _db(tmp_path)
    outlets = _one_input(conn)
    _healthy(conn, "wire")
    _poll(conn, "wire", 700, 300)                                # 30 percent
    assert not _checks(iw.check(conn, outlets, now=NOW, sources=[]), "saturation")
    conn.execute("DELETE FROM feed_polls")
    _poll(conn, "wire", 20, 60)                                  # 75 percent of only 80
    assert not _checks(iw.check(conn, outlets, now=NOW, sources=[]), "saturation")


def test_a_fetch_loss_that_rises_is_flagged_against_its_own_baseline(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("paper")]
    _labels(conn, "paper", 20)
    _discovered_since(conn, "paper")
    _stages(conn)
    _healthy(conn, "paper")
    for d in range(8, 56):
        _article(conn, "paper", d, "classified")
        _article(conn, "paper", d, "classified")
        if d % 4 == 0:
            _article(conn, "paper", d, "paywalled")
    for d in range(0, 7):
        _article(conn, "paper", d + 0.2, "classified")
        for _ in range(3):
            _article(conn, "paper", d + 0.3, "failed")
        _article(conn, "paper", d + 0.4, "queued")                 # no outcome yet: not counted
    line = _checks(iw.check(conn, outlets, now=NOW, sources=[]), "fetch_loss")[0]["line"]
    assert "21 of 28 China relevant items (75%) lost at fetch over 7 days" in line
    assert "12 of 128 (9%) in the 49 days before" in line      # the 20 labelled items count too


def test_a_steady_loss_is_not_news(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("paywall")]
    _labels(conn, "paywall", 20)
    _discovered_since(conn, "paywall")
    _stages(conn)
    _healthy(conn, "paywall")
    for d in range(0, 56):
        _article(conn, "paywall", d + 0.1, "paywalled")
        _article(conn, "paywall", d + 0.1, "paywalled")
        _article(conn, "paywall", d + 0.1, "classified")
    assert not _checks(iw.check(conn, outlets, now=NOW, sources=[]), "fetch_loss")


def test_a_rise_on_a_handful_of_items_is_not_flagged(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("tiny")]
    _labels(conn, "tiny", 20)
    _stages(conn)
    _healthy(conn, "tiny")
    for d in range(8, 56):
        _article(conn, "tiny", d, "classified")
    for d in range(0, 4):
        _article(conn, "tiny", d + 0.1, "failed")
    assert not _checks(iw.check(conn, outlets, now=NOW, sources=[]), "fetch_loss")


def test_silence_is_flagged_when_the_inputs_own_rate_makes_it_improbable(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("loud")]
    _labels(conn, "loud", 20, days=20)
    _discovered_since(conn, "loud")
    _stages(conn)
    _healthy(conn, "loud")
    _steady(conn, "loud", per_day=3, start=4)          # nothing relevant for the last 4 days
    line = _checks(iw.check(conn, outlets, now=NOW, sources=[]), "no_relevant")[0]["line"]
    assert "no China relevant item in 4.1 days" in line


def test_a_quiet_input_needs_a_longer_silence(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("quiet")]
    _labels(conn, "quiet", 20, days=20)
    _discovered_since(conn, "quiet")
    _stages(conn)
    _healthy(conn, "quiet")
    for d in range(5, 56, 2):                          # one every other day, the last 5 days ago
        _article(conn, "quiet", d)
    assert not _checks(iw.check(conn, outlets, now=NOW, sources=[]), "no_relevant")   # 2.5 expected
    conn.execute("DELETE FROM articles WHERE outlet_id='quiet' AND discovered_at > ?", (_t(days=16),))
    conn.execute("DELETE FROM classifications WHERE article_id NOT IN (SELECT id FROM articles)")
    _labels(conn, "quiet", 20, days=20)
    assert _checks(iw.check(conn, outlets, now=NOW, sources=[]), "no_relevant")        # silent 17 days


def test_a_new_outlet_is_judged_on_its_days_since_it_was_added(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("fresh")]
    _discovered_since(conn, "fresh", days=15)
    _stages(conn)
    _healthy(conn, "fresh")
    for d in range(5, 15):
        for _ in range(3):
            _article(conn, "fresh", d + 0.1, category="A")   # three a day for 10 days, then 5.1 days of silence
    m = iw.unit_metrics(conn, ["fresh"], NOW)
    assert round(m["base_days"]) == 10 and (m["rel_days"], m["rel_span"]) == (10, 10)
    # an item on each of its 10 days makes 5.1 silent days improbable; on 10 of the window's 56 they would not be
    line = _checks(iw.check(conn, outlets, now=NOW, sources=[]), "no_relevant")[0]["line"]
    assert "no China relevant item in 5.1 days, against 30 in the 10 days before, on 10 of 10 days" in line
    conn.execute("DELETE FROM daily_outlet_discovery")
    _discovered_since(conn, "fresh", days=60)
    assert not _checks(iw.check(conn, outlets, now=NOW, sources=[]), "no_relevant")


# --- reference sources ------------------------------------------------------------------------------------

def _ref(conn, sid, status, days, n=1):
    for _ in range(n):
        _seq["n"] += 1
        conn.execute("""INSERT INTO reference_items(source_id, url, url_hash, discovered_at, status)
                        VALUES (?,?,?,?,?)""", (sid, "https://ref.test/%d" % _seq["n"], "r%d" % _seq["n"], _t(days), status))


def test_a_failing_reference_source_and_a_silent_one(tmp_path):
    conn = _db(tmp_path)
    _stages(conn)
    _ref(conn, "ref_bad", "failed", 1, n=20)
    _ref(conn, "ref_bad", "fetched", 1, n=10)
    _ref(conn, "ref_bad", "expired", 1, n=50)                    # never tried: not counted
    _ref(conn, "ref_bad", "failed", 10, n=50)                    # before the week
    _ref(conn, "ref_ok", "fetched", 0.1, n=40)
    _ref(conn, "ref_ok", "failed", 0.1, n=4)
    _ref(conn, "ref_quiet", "fetched", 3, n=40)
    r = iw.check(conn, [], now=NOW, sources=[{"id": "ref_bad"}, {"id": "ref_ok"}, {"id": "ref_quiet"},
                                             {"id": "ref_new"}])
    lines = {(p["check"], p["input"]): p["line"] for p in r["problems"]}
    assert "20 of 30 items (67%) failed to fetch or extract over 7 days" in lines[("ref_failing", "ref_bad")]
    assert ("ref_failing", "ref_ok") not in lines
    assert "no new item in 72 hours" in lines[("ref_silent", "ref_quiet")]
    assert lines[("ref_silent", "ref_new")].endswith("no item on record")
    assert ("ref_silent", "ref_bad") not in lines                # its newest item is a day old


def test_reference_pages_with_no_article_are_not_counted_as_lost(tmp_path):
    conn = _db(tmp_path)
    _stages(conn)
    _ref(conn, "ref_cgtn", "fetched", 1, n=20)
    _ref(conn, "ref_cgtn", "failed", 1, n=5)
    _ref(conn, "ref_cgtn", "failed", 1, n=60)
    conn.execute("UPDATE reference_items SET fail_reason='no_text: breaking news alert, headline only' "
                 "WHERE source_id='ref_cgtn' AND status='failed' AND id > (SELECT MIN(id) + 4 FROM reference_items "
                 "WHERE source_id='ref_cgtn' AND status='failed')")
    r = iw.check(conn, [], now=NOW, sources=[{"id": "ref_cgtn"}])
    assert not [p for p in r["problems"] if p["check"] == "ref_failing"]
    conn.execute("UPDATE reference_items SET fail_reason='extraction_empty' WHERE fail_reason LIKE 'no_text:%'")
    r = iw.check(conn, [], now=NOW, sources=[{"id": "ref_cgtn"}])
    assert [p for p in r["problems"] if p["check"] == "ref_failing"]


def test_reference_sources_fall_back_to_those_on_record_when_the_file_cannot_be_read(tmp_path, monkeypatch):
    from pipeline import reference
    conn = _db(tmp_path)

    def broken(*a, **k):
        raise ValueError("bad yaml")
    monkeypatch.setattr(reference, "load_sources", broken)
    conn.execute("""INSERT INTO reference_feed_health(feed_url, source_id, last_checked) VALUES ('u', 'ref_x', ?)""",
                 (_t(),))
    assert iw._reference_ids(conn, None) == ["ref_x"]


# --- global -----------------------------------------------------------------------------------------------

def _fetch_runs(conn, *left):
    for i, n in enumerate(left):
        h = len(left) - i
        conn.execute("INSERT INTO run_log(run_id, stage, started_at, finished_at, ok, counts) VALUES (?,?,?,?,1,?)",
                     ("r%d" % i, "fetch", _t(hours=h + 0.5), _t(hours=h), json.dumps({"skipped_deadline": n})))


def _discover_ok(conn):
    conn.execute("INSERT INTO run_log(run_id, stage, started_at, finished_at, ok, counts) VALUES ('d','discover',?,?,1,'{}')",
                 (_t(hours=1), _t(hours=0.9)))


def test_a_backlog_rising_three_runs_running_is_flagged(tmp_path):
    conn = _db(tmp_path)
    _discover_ok(conn)
    outlets = [_outlet("here"), _outlet("dropped", active=False, collector="self_hosted")]
    _fetch_runs(conn, 3, 8, 326)                       # 2026-10-22 18:40
    for _ in range(5):
        _article(conn, "here", 0.5, "queued")
        _article(conn, "dropped", 20, "queued")        # stranded with the relay: left out
    line = _checks(iw.check(conn, outlets, now=NOW, sources=[]), "backlog_rising")[0]["line"]
    assert "left 3, 8, 326 China relevant items queued, 5 queued now" in line


def test_a_backlog_spike_or_a_falling_backlog_is_quiet(tmp_path):
    conn = _db(tmp_path)
    _discover_ok(conn)
    _fetch_runs(conn, 36, 32, 744)                     # 2026-11-04: one run, cleared by the next
    _fetch_runs(conn, 600, 400)
    assert not _checks(iw.check(conn, [], now=NOW, sources=[]), "backlog_rising")
    _fetch_runs(conn, 100, 200, 240)                   # rising but under the floor
    assert not _checks(iw.check(conn, [], now=NOW, sources=[]), "backlog_rising")


def test_a_backlog_past_the_workflows_own_warning_is_left_to_it(tmp_path):
    conn = _db(tmp_path)
    _discover_ok(conn)
    _fetch_runs(conn, 619, 904, 1133)
    assert not _checks(iw.check(conn, [], now=NOW, sources=[]), "backlog_rising")


def test_malformed_fetch_counts_read_as_nothing_left(tmp_path):
    conn = _db(tmp_path)
    for i, c in enumerate(("not json", "[]", None)):
        conn.execute("INSERT INTO run_log(run_id, stage, started_at, finished_at, ok, counts) VALUES (?,?,?,?,1,?)",
                     ("r", "fetch", _t(hours=3 - i), _t(hours=3 - i), c))
    assert iw.backlog_series(conn) == [0, 0, 0]


def test_no_discovery_pass_for_a_day_is_said_once_not_for_every_input(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("a"), _outlet("b")]
    _labels(conn, "a", 10)
    _labels(conn, "b", 10)
    for oid in ("a", "b"):
        _healthy(conn, oid, ok_hours=40)
    conn.execute("INSERT INTO run_log(run_id, stage, started_at, finished_at, ok, counts) VALUES ('r','discover',?,?,1,'{}')",
                 (_t(hours=41), _t(hours=40)))
    _fetch_runs(conn, 0)
    r = iw.check(conn, outlets, now=NOW, sources=[])
    assert [p["input"] for p in _checks(r, "stage_stale")] == ["discover"]
    assert not _checks(r, "no_poll")


# --- output and the run_log row ---------------------------------------------------------------------------

def test_annotations_are_capped_escaped_and_say_how_many_more():
    problems = [{"check": "x", "input": str(i), "line": "input %d: 5 of 10 (50%%)\nnext" % i} for i in range(14)]
    lines = iw.annotations(problems)
    assert len(lines) == iw.MAX_ANNOTATIONS
    assert lines[0] == "::warning title=Input at risk::input 0: 5 of 10 (50%25)%0Anext"
    assert lines[-1].startswith("::warning title=Input at risk::5 more inputs at risk")
    assert iw.annotations(problems[:3])[-1].startswith("::warning title=Input at risk::input 2")


def test_run_prints_lines_and_annotations_and_records_a_run_log_row(tmp_path):
    conn = _db(tmp_path)
    outlets = _one_input(conn)
    _healthy(conn, "wire", ok_hours=30)                          # new: its feed failed a day and a quarter
    _poll(conn, "wire", 300, 700)                                # ongoing: saturated a day ago as well
    out = io.StringIO()
    res = iw.run(conn, outlets=outlets, now=NOW, sources=[], out=out)
    text = out.getvalue()
    assert text.startswith("input watch: watching 1 input that carried 20 of 20 state origin and unchecked state "
                           "sourcing items (100%) over 56 days, 0 (0%) on inputs not polled here; could judge 20 "
                           "(100%), 20 alone and 0 in 0 country groups; 0 reference sources; 2 problems, 1 new; ")
    assert "input watch: [new] wire, 20 items at stake: no successful feed poll in 30 hours" in text
    assert "input watch: [ongoing] wire, 20 items at stake: an estimated 700 of 1,000 feed items (70%) missed" in text
    assert "::warning title=Input at risk::wire, 20 items at stake: no successful feed poll in 30 hours" in text
    assert text.count("::warning") == 1
    assert ("::notice title=Inputs at risk, ongoing::ongoing, not warned again: 1 input degraded carrying 20 items "
            "(feed saturation 1); largest: wire 20 (feed saturation)") in text
    row = conn.execute("SELECT ok, counts, notes FROM run_log WHERE stage='input_watch'").fetchone()
    counts = json.loads(row["counts"])
    assert row["ok"] == 1 and counts["problems"] == 2 and counts["by_check"] == {"no_poll": 1, "saturation": 1}
    assert counts["new"] == 1 and counts["new_by_check"] == {"no_poll": 1}
    assert counts["inputs_watched"] == 1 and counts["window_items"] == 20 and counts["watched_items"] == 20
    assert counts["all"] == [["wire", "no_poll", 20, 1], ["wire", "saturation", 20, 0]]
    assert counts["estimator"] == config.MISSED_ESTIMATOR_VERSION
    assert "no successful feed poll" in row["notes"]
    assert res["counts"] == counts


def test_a_failing_watch_warns_records_ok_0_and_never_raises(tmp_path, monkeypatch):
    conn = _db(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(iw, "check", boom)
    out = io.StringIO()
    res = iw.run(conn, outlets=[], now=NOW, sources=[], out=out)
    assert res == {"error": "RuntimeError: disk gone"}
    assert "::warning title=Input watch failed::RuntimeError: disk gone" in out.getvalue()
    row = conn.execute("SELECT ok, notes FROM run_log WHERE stage='input_watch'").fetchone()
    assert row["ok"] == 0 and "disk gone" in row["notes"]


def test_main_rejects_a_bad_time(capsys):
    import pytest
    with pytest.raises(SystemExit):
        iw.main(["--now", "yesterday"])


def test_binomial_tail_edges():
    assert iw.binomial_tail(0, 10, 0.3) == 1.0
    assert iw.binomial_tail(3, 10, 0.0) == 0.0
    assert abs(iw.binomial_tail(1, 1, 0.25) - 0.25) < 1e-12
    assert iw.binomial_tail(661, 669, 0.35) < 1e-100      # the Postmedia papers on the snapshot
    assert 0.0 <= iw.binomial_tail(5000, 13205, 0.35) <= 1.0


# --- the workflow -----------------------------------------------------------------------------------------

def test_the_workflow_runs_the_watch_after_collection_and_never_fails_on_it():
    wf = yaml.safe_load((config.ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["pipeline"]["steps"]
    names = [s.get("name") or "" for s in steps]
    i = [k for k, s in enumerate(steps) if "pipeline.input_watch" in (s.get("run") or "")]
    assert len(i) == 1
    step = steps[i[0]]
    assert step.get("continue-on-error") is True and "if" not in step       # every stage, every run
    assert "ANTHROPIC_API_KEY" not in json.dumps(step)
    assert i[0] == names.index("Discover, fetch, classify") + 1


# --- the wider watch: every input, small inputs, country groups, new against ongoing ----------------------

def _small_country(conn, country="FJI", ids=("s1", "s2", "s3"), silent_days=4, offset=0.1):
    """Three small inputs of one country, each with an item every third day (too few to judge alone), together
    an item every day, all silent for the last `silent_days` days."""
    outlets = []
    for k, oid in enumerate(ids):
        o = _outlet(oid)
        o["country"] = country
        outlets.append(o)
        _labels(conn, oid, 1)
        _discovered_since(conn, oid)
        _healthy(conn, oid)
        for d in range(silent_days, 56):
            if d % len(ids) == k:
                _article(conn, oid, d + offset)
    return outlets


def test_the_watch_covers_every_polled_input_and_the_coverage_line_says_what_it_could_judge(tmp_path):
    conn = _db(tmp_path)
    outlets = _one_input(conn, "wire") + _small_country(conn, silent_days=0) + [_outlet("gone", active=False)]
    _healthy(conn, "wire")
    _labels(conn, "wire", 10)
    _labels(conn, "gone", 5)
    r = iw.check(conn, outlets, now=NOW, sources=[])
    key = r["key"]
    assert [u["id"] for u in key["units"]] == ["wire", "s1", "s2", "s3"]
    assert (key["covered"], key["total"], key["unpolled"]) == (33, 38, 5)
    assert (key["judged_alone"], key["judged_group"], key["groups"]) == (30, 3, 1)
    assert [g["units"] for g in r["groups"]] == [["s1", "s2", "s3"]]
    line = iw.coverage_line(r, 0.4)
    assert line.startswith("input watch: watching 4 inputs that carried 33 of 38 state origin and unchecked state "
                           "sourcing items (87%) over 56 days, 5 (13%) on inputs not polled here; could judge 33 "
                           "(87%), 30 alone and 3 in 1 country group; 0 reference sources; ")
    assert r["problems"] == []


def test_small_inputs_are_judged_together_by_country_and_say_nothing_alone(tmp_path):
    conn = _db(tmp_path)
    outlets = _small_country(conn, silent_days=3, offset=0.6)
    _stages(conn)
    r = iw.check(conn, outlets, now=NOW, sources=[])
    assert [p["input"] for p in r["problems"]] == ["group:FJI"]
    p = r["problems"][0]
    assert p["check"] == "no_relevant" and p["new"] and p["stake"] == 3   # a day ago the silence was under 3 days
    assert p["line"].startswith("country group FJI, 3 small inputs, 3 items at stake: no China relevant item in 3.6 days")


def test_an_input_dark_on_its_own_is_left_out_of_its_country_group(tmp_path):
    conn = _db(tmp_path)
    outlets = _small_country(conn, silent_days=0)
    _stages(conn)
    conn.execute("UPDATE feed_health SET last_ok = ? WHERE outlet_id = 's3'", (_t(hours=40),))
    r = iw.check(conn, outlets, now=NOW, sources=[])
    assert [g["units"] for g in r["groups"]] == [["s1", "s2"]]
    assert [(p["input"], p["check"]) for p in r["problems"]] == [("s3", "no_poll")]


def test_silence_on_a_small_input_needs_a_long_record_and_a_long_silence():
    assert iw.silence_score(30, 30, 2.4) >= iw.ZERO_EXPECTED          # an item every day: 2.4 days is news
    assert iw.silence_score(15, 30, 8) < iw.ZERO_EXPECTED             # every other day: 8 days is not
    assert iw.silence_score(5, 30, 56) < iw.ZERO_EXPECTED             # 5 days of 30: not within the window
    assert iw.silence_score(4, 4, 1000) == 0.0                        # under ZERO_MIN_DAYS: never judged
    assert iw.silence_score(0, 0, 3) == 0.0


def test_a_small_input_silent_for_weeks_says_nothing(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("tiny")]
    _labels(conn, "tiny", 2, days=50)
    _discovered_since(conn, "tiny")
    _stages(conn)
    _healthy(conn, "tiny")
    for d in (40, 45, 50):
        _article(conn, "tiny", d + 0.1)                # items on 3 days, then 40 silent days
    r = iw.check(conn, outlets, now=NOW, sources=[], detail=True)
    assert not _checks(r, "no_relevant") and r["judged"]["tiny"] is False


def test_a_rise_against_a_small_baseline_is_not_called_improbable(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("paper")]
    _labels(conn, "paper", 2)
    _discovered_since(conn, "paper")
    _stages(conn)
    _healthy(conn, "paper")
    for d in range(10):
        _article(conn, "paper", 20 + d)                # 0 of 10 lost before
    for d in range(10):
        _article(conn, "paper", d * 0.6 + 0.1, "failed" if d < 3 else "classified")   # 3 of 10 lost now
    # a binomial against the earlier share of nought would call any loss impossible; the Fisher test does not
    assert iw.binomial_tail(3, 10, 0.0) == 0.0 and iw.fisher_tail(3, 10, 0, 10) > iw.LOSS_P
    assert not _checks(iw.check(conn, outlets, now=NOW, sources=[]), "fetch_loss")


def test_stale_entries_are_judged_against_the_inputs_own_entry_rate(tmp_path):
    conn = _db(tmp_path)
    weekly, daily = _outlet("weekly"), _outlet("daily")
    for oid, every in (("weekly", 7), ("daily", 1)):
        _labels(conn, oid, 3)
        _discovered_since(conn, oid, days=55, every=every)
        _healthy(conn, oid, newest_hours=100)
    _stages(conn)
    r = iw.check(conn, [weekly, daily], now=NOW, sources=[])
    assert [p["input"] for p in _checks(r, "stale_entries")] == ["daily"]
    assert "against new items on 51 of the 51 days before it" in _checks(r, "stale_entries")[0]["line"]


def _dark_at_fetch(conn, oid, lost_days, base_lost=False):
    """Two China relevant items a day; those of the last `lost_days` days lost at fetch (HTTP 403)."""
    for d in range(56):
        for k in range(2):
            lost = d < lost_days or (base_lost and k == 0)
            aid = _article(conn, oid, d + 0.2 + 0.3 * k, "failed" if lost else "classified")
            if lost:
                conn.execute("UPDATE articles SET fail_reason='http_403', http_status=403 WHERE id=?", (aid,))


def test_nearly_all_lost_at_fetch_is_new_when_it_starts_and_ongoing_when_steady(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet(o) for o in ("broke", "always", "blocked_new")]
    for o in outlets:
        _labels(conn, o["id"], 4)
        _discovered_since(conn, o["id"])
        _healthy(conn, o["id"])
    _stages(conn)
    _dark_at_fetch(conn, "broke", lost_days=7)          # 0 lost before, all of the last 7 days now
    _dark_at_fetch(conn, "always", lost_days=56)        # lost since the window began
    for d in range(4):                                  # a new outlet blocked from its first day
        for _ in range(3):
            _article(conn, "blocked_new", d + 0.3, "failed")
    conn.execute("UPDATE articles SET fail_reason='http_403' WHERE outlet_id='blocked_new' AND status='failed'")
    conn.execute("DELETE FROM daily_outlet_discovery WHERE outlet_id='blocked_new'")
    _discovered_since(conn, "blocked_new", days=4)
    r = iw.check(conn, outlets, now=NOW, sources=[])
    dark = {p["input"]: p for p in _checks(r, "fetch_dark")}
    assert dark["broke"]["new"] and dark["blocked_new"]["new"]
    assert not dark["always"]["new"]                    # a block that has held all window is known
    assert "14 of 14 China relevant items (100%) lost at fetch over 7 days, against 0 of 102" in dark["broke"]["line"]
    assert dark["broke"]["line"].endswith("; mostly http_403 14")
    assert dark["blocked_new"]["line"].endswith("; mostly http_403 12")


def _old_dark(conn, oid, error="http_403", hours=240):
    conn.execute("""INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, last_error, consecutive_failures,
                    newest_entry) VALUES (?,?,?,?,?,?,?)""",
                 ("https://%s.test/feed" % oid, oid, _t(hours=0.2), _t(hours=hours), error, 80, _t(hours=hours)))


def test_long_dark_inputs_are_counted_in_one_notice_and_a_newly_dark_one_is_warned(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet(o) for o in ("old_a", "old_b", "fresh", "fine")]
    for o, n in (("old_a", 5), ("old_b", 3), ("fresh", 2), ("fine", 9)):
        _labels(conn, o, n)
        _discovered_since(conn, o)
    _old_dark(conn, "old_a")
    _old_dark(conn, "old_b", error="robots_disallowed", hours=500)
    _healthy(conn, "fresh", ok_hours=30)
    _healthy(conn, "fine")
    _stages(conn)
    out = io.StringIO()
    res = iw.run(conn, outlets=outlets, now=NOW, sources=[], out=out)
    text = out.getvalue()
    assert [(p["input"], p["new"]) for p in _checks(res, "no_poll")] == [("old_a", False), ("old_b", False),
                                                                        ("fresh", True)]
    warnings = [line for line in text.splitlines() if line.startswith("::warning")]
    assert warnings == ["::warning title=Input at risk::fresh, 2 items at stake: no successful feed poll in 30 hours "
                        "(limit 24)"]
    assert ("::notice title=Inputs at risk, ongoing::ongoing, not warned again: 2 inputs dark for over 24 hours "
            "carrying 8 items (feed http_403 1, feed robots_disallowed 1; median onset 2026-10-30)") in text
    assert "input watch: [ongoing since 2026-10-30] old_a, 5 items at stake: no successful feed poll in 240 hours" in text
    counts = res["counts"]
    assert counts["dark_ongoing_inputs"] == 2 and counts["dark_ongoing_items"] == 8 and counts["new"] == 1


def test_a_dark_input_that_also_stops_polling_is_not_news(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("blocked")]
    _labels(conn, "blocked", 4)
    _discovered_since(conn, "blocked")
    _stages(conn)
    _dark_at_fetch(conn, "blocked", lost_days=20)       # lost at fetch for 20 days
    _healthy(conn, "blocked", ok_hours=30)              # and now its feed fails too
    r = iw.check(conn, outlets, now=NOW, sources=[])
    assert {p["check"]: p["new"] for p in r["problems"]} == {"fetch_dark": False, "no_poll": False}


def test_a_problem_that_comes_back_within_a_week_is_not_warned_again(tmp_path):
    def build(conn, earlier_gap):
        outlets = [_outlet("wire")]
        _labels(conn, "wire", 6)
        _discovered_since(conn, "wire")
        _healthy(conn, "wire")
        _stages(conn)
        days = [3, 4, 5] + list(range(10 if earlier_gap else 6, 56))
        for d in days:
            _article(conn, "wire", d + 0.1)
        return outlets
    conn = _db(tmp_path)
    p = _checks(iw.check(conn, build(conn, earlier_gap=False), now=NOW, sources=[]), "no_relevant")
    assert len(p) == 1 and p[0]["new"]                  # silent 3.1 days after an unbroken record: new
    conn2 = store.connect(tmp_path / "t2.db")
    p = _checks(iw.check(conn2, build(conn2, earlier_gap=True), now=NOW, sources=[]), "no_relevant")
    assert len(p) == 1 and not p[0]["new"]              # the same, silent for 5 days a week ago too: ongoing


def test_saturation_is_new_on_a_jump_not_on_a_drift(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("jump"), _outlet("drift")]
    for o in ("jump", "drift"):
        _labels(conn, o, 3)
        _discovered_since(conn, o)
        _healthy(conn, o)
    _stages(conn)
    for d in range(2, 7):
        _poll(conn, "jump", 300, 10, days=d)            # 3 percent until yesterday
    _poll(conn, "jump", 300, 3000, days=0)              # then most of today's items missed
    for d in range(1, 7):
        _poll(conn, "drift", 650, 350, days=d)          # 35 percent, over the site's own line, for a week
    _poll(conn, "drift", 300, 700, days=0)
    sat = {p["input"]: p for p in _checks(iw.check(conn, outlets, now=NOW, sources=[]), "saturation")}
    assert sat["jump"]["new"] and not sat["drift"]["new"]
    assert "3,050 of 4,850 feed items (63%)" in sat["jump"]["line"] and "(40%)" in sat["drift"]["line"]


def test_after_a_change_of_estimator_saturation_is_read_from_config_and_not_flooded(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    outlets = [_outlet("wire")]
    _labels(conn, "wire", 3)
    _discovered_since(conn, "wire")
    _healthy(conn, "wire")
    _stages(conn)
    for d in range(2, 7):
        _poll(conn, "wire", 300, 10, days=d)            # the old estimator: 3 percent
    monkeypatch.setattr(config, "MISSED_ESTIMATOR_VERSION", "2026-11-10")
    _poll(conn, "wire", 300, 700, days=0)               # the new one, from today only
    r = iw.check(conn, outlets, now=NOW, sources=[])
    p = _checks(r, "saturation")[0]
    assert "700 of 1,000 feed items (70%)" in p["line"]   # only the new estimator's rows are read
    assert not p["new"]                                  # no share to compare with a day before: not news


def test_new_problems_are_warned_by_items_at_stake_one_line_per_input_ongoing_ones_not_at_all():
    def p(inp, check, stake, new=True, category="dark"):
        return {"input": inp, "check": check, "stake": stake, "new": new, "category": category,
                "line": "%s, %d items at stake: %s" % (inp, stake, check)}
    problems = [p("fetch", "backlog_rising", 4000, category="global"),
                p("big", "no_poll", 90), p("big", "stale_entries", 90),
                p("known", "no_poll", 80, new=False)] + [p("i%02d" % k, "no_relevant", 20 - k) for k in range(12)]
    lines = iw.annotations(problems)
    assert len(lines) == iw.MAX_ANNOTATIONS
    assert lines[0] == "::warning title=Input at risk::fetch, 4000 items at stake: backlog_rising"
    assert lines[1] == "::warning title=Input at risk::big, 90 items at stake: no_poll; stale_entries"
    assert all("known" not in line for line in lines)
    assert lines[-1] == ("::warning title=Input at risk::5 more inputs at risk; see the input watch lines in this "
                         "step's log")
    assert iw.ongoing_summary(problems).startswith("ongoing, not warned again: 1 input dark for over 24 hours carrying "
                                                   "80 items")
    assert iw.ongoing_summary([q for q in problems if q["new"]]) is None


def test_the_watch_of_hundreds_of_inputs_stays_fast(tmp_path):
    import time
    conn = _db(tmp_path)
    outlets = [_outlet("o%03d" % i) for i in range(300)]
    for i, o in enumerate(outlets):
        _labels(conn, o["id"], 1 + i % 5)
        _healthy(conn, o["id"])
        for d in range(0, 56, 1 + i % 7):
            _article(conn, o["id"], d + 0.1)
    _stages(conn)
    started = time.monotonic()
    r = iw.check(conn, outlets, now=NOW, sources=[])
    assert len(r["key"]["units"]) == 300 and time.monotonic() - started < 5.0
