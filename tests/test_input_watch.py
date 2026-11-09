"""The input watch (pipeline/input_watch.py), on small synthetic databases: which inputs are key (networks
counted once, the share and the cap, ties, inputs this collector does not poll), each per input check
firing on a real change and staying quiet on a steady state, the reference sources, the fetch backlog,
the annotation cap and escaping, the run_log row, and the workflow step."""
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


def _discovered_since(conn, oid, days=60):
    conn.execute("INSERT INTO daily_outlet_discovery(date, outlet_id, country, discovered) VALUES (?,?,?,?)",
                 ((NOW - dt.timedelta(days=days)).date().isoformat(), oid, "AUS", 10))


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
    key = iw.key_inputs(conn, outlets, NOW)
    assert key["total"] == 35
    assert [(u["id"], u["weight"]) for u in key["units"]] == [("solo", 25), ("network:acm", 10)]
    assert key["units"][1]["members"] == ["au_a", "au_b", "au_c"]


def test_the_key_set_stops_at_the_share_and_keeps_ties_at_the_edge(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet(o) for o in ("big", "t1", "t2", "small")]
    _labels(conn, "big", 70)
    _labels(conn, "t1", 12, category="B")
    _labels(conn, "t2", 12)
    _labels(conn, "small", 6)
    key = iw.key_inputs(conn, outlets, NOW)
    # 70 of 100 is short of 80; t1 takes it to 82; t2 ties with t1 and comes too; small does not
    assert [u["id"] for u in key["units"]] == ["big", "t1", "t2"]
    assert key["covered"] == 94 and key["total"] == 100


def test_the_key_set_is_capped_and_says_the_share_it_reached(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    monkeypatch.setattr(iw, "KEY_MAX", 3)
    outlets = [_outlet("o%d" % i) for i in range(10)]
    for i in range(10):
        _labels(conn, "o%d" % i, 10 - i)
    key = iw.key_inputs(conn, outlets, NOW)
    assert len(key["units"]) == 3 and key["covered"] == 27 and key["total"] == 55


def test_inputs_not_polled_count_in_the_total_but_are_not_watched(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("gone", active=False), _outlet("relay", collector="self_hosted"), _outlet("here")]
    _labels(conn, "gone", 50)
    _labels(conn, "relay", 30)
    _labels(conn, "here", 20)
    _labels(conn, "unknown_id", 5)                     # not in the registry at all
    key = iw.key_inputs(conn, outlets, NOW)
    assert [u["id"] for u in key["units"]] == ["here"]
    assert key["total"] == 105


def test_labels_outside_the_window_and_c_labels_do_not_count(tmp_path):
    conn = _db(tmp_path)
    outlets = [_outlet("a")]
    _labels(conn, "a", 5, days=iw.WINDOW_DAYS + 1)
    _labels(conn, "a", 5, category="C")
    assert iw.key_inputs(conn, outlets, NOW)["total"] == 0
    assert iw.key_inputs(conn, outlets, NOW)["units"] == []


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
    _discovered_since(conn, "fresh", days=10)
    _stages(conn)
    _healthy(conn, "fresh")
    for d in range(4, 10):
        for _ in range(3):
            _article(conn, "fresh", d + 0.1, category="A")   # three a day for 6 days, then 4.1 days of silence
    m = iw.unit_metrics(conn, ["fresh"], NOW)
    assert round(m["base_days"]) == 6
    # 18 items over 6 days expect 12 in the silence; spread over the whole window they would expect 1.4
    assert "(3.0 a day, 12 expected in the silence)" in _checks(iw.check(conn, outlets, now=NOW, sources=[]),
                                                                  "no_relevant")[0]["line"]


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
    _healthy(conn, "wire")
    _poll(conn, "wire", 300, 700)
    out = io.StringIO()
    res = iw.run(conn, outlets=outlets, now=NOW, sources=[], out=out)
    text = out.getvalue()
    assert text.startswith("input watch: 1 key inputs carried 20 of 20 ")
    assert "input watch: wire, key input 1: an estimated 700 of 1,000 feed items (70%) missed" in text
    assert "::warning title=Input at risk::wire, key input 1" in text
    row = conn.execute("SELECT ok, counts, notes FROM run_log WHERE stage='input_watch'").fetchone()
    counts = json.loads(row["counts"])
    assert row["ok"] == 1 and counts["problems"] == 1 and counts["by_check"] == {"saturation": 1}
    assert counts["key_inputs"] == 1 and counts["window_labels"] == 20 and "missed" in row["notes"]
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
