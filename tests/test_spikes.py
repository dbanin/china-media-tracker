"""Unusual days (pipeline/spikes.py): the Poisson tail, the baseline and its trimming, the flag rule,
every confounder on synthetic daily files, the partial export day, the worldwide stratum, the side
file's shape and the hand check."""
import csv
import datetime as dt
import json
import math

import pytest

from pipeline import export, measures, spikes, store

START = "2026-09-01"


def _day(n):
    return (dt.date.fromisoformat(START) + dt.timedelta(days=n)).isoformat()


def _entry(**kw):
    e = {"A": 0, "Al": 0, "B": 0, "C": 0, "pending": 0, "cls": 0, "disc": 100, "polls": 0, "sat": 0}
    e.update(kw)
    if "cls" not in kw:
        e["cls"] = e["A"] + e["B"] + e["C"]
    return e


def _daily(series, day_fields=None):
    """{iso: {day: country entry}} and {day: extra day fields} as the daily files' {month: {...}}."""
    out = {}
    days = sorted({d for s in series.values() for d in s} | set(day_fields or {}))
    for d in days:
        m = out.setdefault(d[:7], {"month": d[:7], "days": {}})
        e = {"countries": {c: s[d] for c, s in series.items() if d in s}, "underlying_items": {},
             "llm_ceiling_hit": False, "relay_incomplete": False, "labels_on_older_ruleset": 0,
             "labels_on_older_prompt": 0, "llm_sampling": {}}
        e.update((day_fields or {}).get(d, {}))
        m["days"][d] = e
    return out


def _flat(n_days=30, a=1, disc=100, spike_day=29, spike=12, **spike_kw):
    """One country with a steady count and a surge on spike_day."""
    s = {}
    for i in range(n_days):
        s[_day(i)] = _entry(A=a, disc=disc)
    if spike_day is not None:
        s[_day(spike_day)] = _entry(A=spike, disc=spike_kw.pop("spike_disc", disc), **spike_kw)
    return s


def _rows(day, n, outlet="o_old", item=None, discovered=None, published=None, day_of_record=None, method="rules"):
    return [{"id": 1000 + i, "outlet_id": outlet if isinstance(outlet, str) else outlet[i], "title": "t%d" % i,
             "url": "https://x.test/%d" % i, "route": "wire_credit", "date": day,
             "discovered_at": discovered or (day + "T10:00:00+00:00"), "published_at": published or (day + "T08:00:00+00:00"),
             "day_of_record": day_of_record, "method": method, "item": item if item is not None else 1000 + i}
            for i in range(n)]


UNTIL = _day(30)
SPIKE = _day(29)


def _one(daily, meta=None, rows=None, first_seen=None, outlet_disc=None, country="ITA"):
    res = spikes.analyse(daily, UNTIL, meta or {}, {(SPIKE, country): rows} if rows is not None else {},
                         first_seen or {}, outlet_disc or {}, {"o_new": "New Outlet", "o_old": "Old Outlet"})
    found = [s for s in res["spikes"] if s["date"] == SPIKE and s["country"] == country]
    assert len(found) == 1, res["spikes"]
    return found[0]


def _reasons(s):
    return {c["reason"] for c in s["confounders"]}


# ---------------------------------------------------------------------------
# The Poisson tail, the baseline and the flag rule
# ---------------------------------------------------------------------------

def _direct_sf(x, lam):
    return 1.0 - math.fsum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(x))


@pytest.mark.parametrize("x,lam,want", [(5, 1.0, 0.0036598468273437), (10, 2.0, 4.6498075017e-05),
                                        (3, 10.0, 0.9972306042844884), (1, 0.2, 1 - math.exp(-0.2))])
def test_the_poisson_tail_against_known_values(x, lam, want):
    assert spikes.poisson_sf(x, lam) == pytest.approx(want, rel=1e-9)
    assert spikes.poisson_sf(x, lam) == pytest.approx(_direct_sf(x, lam), rel=1e-9)


def test_the_poisson_tail_at_the_edges_and_far_out():
    assert spikes.poisson_sf(0, 3.0) == 1.0 and spikes.poisson_sf(-2, 3.0) == 1.0
    assert spikes.poisson_sf(1, 0.0) == 0.0
    # far in the tail it stays on the log scale: no overflow, no error, and the right order of magnitude
    lg = spikes.poisson_log_sf(1000, 1.0)
    assert lg == pytest.approx(-1.0 - math.lgamma(1001), rel=1e-6)
    assert spikes.poisson_sf(1000, 1.0) == 0.0
    assert spikes.poisson_sf(5000, 4000.0) < 1e-40
    # a large mean: the tail and the lower sum make one
    lower = math.fsum(math.exp(k * math.log(100.0) - 100.0 - math.lgamma(k + 1)) for k in range(150))
    assert spikes.poisson_sf(150, 100.0) == pytest.approx(1.0 - lower, rel=1e-6)
    # monotone in x
    vals = [spikes.poisson_sf(x, 2.5) for x in range(0, 30)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))


def test_trimming_drops_the_top_tenth_and_floors():
    assert spikes.trimmed_mean([1] * 18 + [50, 50]) == pytest.approx(1.0)       # 20 days: the 2 largest go
    assert spikes.trimmed_mean([2] * 13 + [100]) == pytest.approx(2.0)          # 14 days: the largest goes
    assert spikes.trimmed_mean([1] * 9) == pytest.approx(1.0)                   # 9 days: nothing to drop
    assert spikes.trimmed_mean([0] * 20) == spikes.LAMBDA_FLOOR
    assert spikes.trimmed_mean([]) is None


def test_the_baseline_needs_fourteen_monitored_days_and_skips_unmonitored_ones():
    day = _day(28)
    counts = {_day(i): 3 for i in range(28)}
    disc = {_day(i): (100 if i >= 15 else 0) for i in range(28)}               # 13 monitored days
    assert spikes.baseline(counts, disc, day) is None
    disc[_day(14)] = 100                                                         # 14 now
    b = spikes.baseline(counts, disc, day)
    assert b is not None and len(b["monitored"]) == 14 and len(b["days"]) == 28
    # an unmonitored day's zero never pulls the baseline down
    counts.update({_day(i): 0 for i in range(14)})
    assert spikes.baseline(counts, disc, day)["lambda"] == pytest.approx(3.0)
    # the day itself is never in its own baseline
    counts[day] = 500
    disc[day] = 100
    assert spikes.baseline(counts, disc, day)["lambda"] == pytest.approx(3.0)


def test_the_flag_rule_needs_all_three():
    assert spikes.is_flagged(5, 0.2)[0]
    assert not spikes.is_flagged(4, 0.2)[0], "fewer than 5"
    assert not spikes.is_flagged(25, 10.0)[0] and spikes.is_flagged(25, 10.0)[1] < 0.001, "under 3 times"
    assert not spikes.is_flagged(6, 2.0)[0], "3 times but the tail is not small enough"
    assert spikes.is_flagged(9, 2.0)[0]
    x, p = spikes.flag_threshold(2.0)
    assert x == 9 and p < 0.001 and spikes.poisson_sf(8, 2.0) >= 0.001
    assert spikes.flag_threshold(0.2)[0] == 5 and spikes.flag_threshold(10.0)[0] >= 30


# ---------------------------------------------------------------------------
# The series: complete days, the worldwide stratum, China
# ---------------------------------------------------------------------------

def test_a_clean_spike_and_its_fields():
    s = _one(_daily({"ITA": _flat()}), rows=_rows(SPIKE, 12, item=None))
    assert s["count"] == 12 and s["expected"] == pytest.approx(1.0) and s["baseline_days"] == 28
    assert s["status"] == "clean" and s["confounders"] == [] and s["descriptors"] == ["one_outlet"]
    assert s["rate"] == pytest.approx(0.12) and s["rate_expected"] == pytest.approx(0.01)
    assert s["ratio"] == 12.0 and s["p"] < 1e-6 and s["uniq"] == 12
    assert len(s["items"]) == 12 and set(s["items"][0]) == {"id", "outlet_id", "title", "url", "route", "date"}


def test_the_partial_export_day_is_never_tested():
    series = _flat(n_days=31, spike_day=30, spike=40)                            # the surge is on the export day
    res = spikes.analyse(_daily({"ITA": series}), UNTIL)
    assert res["spikes"] == [] and res["last_complete_day"] == SPIKE
    # the bound comes from latest.json exactly as the findings' 30 day window does
    latest = {"window_start_30d": _day(0)}
    assert spikes.last_tested_day_bound(latest) == export.complete_window_30d(latest)[1] == UNTIL
    assert spikes.last_tested_day_bound(None) == dt.datetime.now(dt.timezone.utc).date().isoformat()


def test_the_worldwide_stratum_has_its_own_series_and_china_none():
    daily = _daily({"WLD": _flat(), "CHN": _flat(), "ITA": _flat(spike_day=None)})
    res = spikes.analyse(daily, UNTIL)
    assert [(s["country"], s["date"]) for s in res["spikes"]] == [("WLD", SPIKE)]
    # 2 series (WLD, ITA) and the days with a full baseline in each
    assert res["tests"] == 2 * (30 - spikes.MIN_BASELINE_DAYS)
    assert 0 < res["expected_false_flags"] < res["tests"] * spikes.P_THRESHOLD


def test_too_few_monitored_days_means_no_test():
    series = {_day(i): _entry(A=1, disc=(100 if i >= 20 else 0)) for i in range(29)}
    series[SPIKE] = _entry(A=30)
    res = spikes.analyse(_daily({"ITA": series}), UNTIL)
    assert res["spikes"] == [] and res["tests"] == 0


def test_descriptors_one_item():
    s = _one(_daily({"ITA": _flat()}), rows=_rows(SPIKE, 12, outlet=["o%d" % i for i in range(12)], item=7))
    assert s["descriptors"] == ["one_item"] and s["top_items"][0]["placements"] == 12
    assert len(s["top_items"][0]["outlets"]) == 12 and len(s["top_outlets"]) == spikes.TOP_N


# ---------------------------------------------------------------------------
# Confounders, one rule at a time
# ---------------------------------------------------------------------------

def test_new_outlet():
    first = {"o_new": _day(25), "o_old": _day(0)}
    s = _one(_daily({"ITA": _flat()}), rows=_rows(SPIKE, 12, outlet="o_new"), first_seen=first)
    assert _reasons(s) == {"new_outlet"} and s["status"] == "check" and "New Outlet" in s["confounders"][0]["detail"]
    s = _one(_daily({"ITA": _flat()}), rows=_rows(SPIKE, 12, outlet="o_old"), first_seen=first)
    assert s["confounders"] == [], "an outlet seen since the start of the baseline is not new"


def test_backfill():
    rows = _rows(SPIKE, 12)
    for r in rows[:4]:
        r["day_of_record"], r["discovered_at"] = SPIKE, _day(29 + 3) + "T10:00:00+00:00"
    s = _one(_daily({"ITA": _flat()}), rows=rows)
    assert "backfill" in _reasons(s) and s["status"] == "check"
    s = _one(_daily({"ITA": _flat()}), rows=_rows(SPIKE, 12)[:12])
    assert "backfill" not in _reasons(s)


def test_outage_recovery_from_a_gap_in_discovery():
    series = _flat()
    series[_day(27)] = _entry(A=0, disc=5)
    s = _one(_daily({"ITA": series}), rows=_rows(SPIKE, 12))
    assert _reasons(s) == {"outage_recovery"} and _day(27) in s["confounders"][0]["detail"]


def test_outage_recovery_from_stale_items_and_from_an_outlet_gap():
    s = _one(_daily({"ITA": _flat()}), rows=_rows(SPIKE, 12, published=_day(25) + "T08:00:00+00:00"))
    assert _reasons(s) == {"outage_recovery"} and "48 hours" in s["confounders"][0]["detail"]
    odisc = {("o_old", _day(i)): 20 for i in range(28)}                          # usually finds items, not on day 28
    s = _one(_daily({"ITA": _flat()}), rows=_rows(SPIKE, 12), outlet_disc=odisc)
    assert _reasons(s) == {"outage_recovery"} and "Old Outlet" in s["confounders"][0]["detail"]
    odisc[("o_old", _day(28))] = 20
    assert _one(_daily({"ITA": _flat()}), rows=_rows(SPIKE, 12), outlet_disc=odisc)["confounders"] == []


def test_relay_incomplete():
    daily = _daily({"ITA": _flat()})
    meta = {"relay_collector": {"countries": ["ITA"], "shared_countries": [], "incomplete_days": [_day(28)]}}
    s = _one(daily, meta, rows=_rows(SPIKE, 12))
    assert _reasons(s) == {"relay_incomplete"} and s["status"] == "check"
    # a country the hosted runner also polls is not left uncollected by the relay
    meta["relay_collector"].update({"countries": [], "shared_countries": ["ITA"]})
    assert _one(daily, meta, rows=_rows(SPIKE, 12))["confounders"] == []
    # an incomplete day is understated, not inflated
    meta["relay_collector"].update({"countries": ["ITA"], "incomplete_days": [SPIKE]})
    assert _one(daily, meta, rows=_rows(SPIKE, 12))["confounders"] == []
    # an incomplete baseline against a complete day
    meta["relay_collector"]["incomplete_days"] = [_day(i) for i in (3, 9, 15)]
    assert _reasons(_one(daily, meta, rows=_rows(SPIKE, 12))) == {"relay_incomplete"}


def test_ruleset_change_from_the_older_label_share():
    series = _flat()
    base = {_day(i): {"labels_on_older_ruleset": 1} for i in range(29)}          # every baseline label older
    s = _one(_daily({"ITA": series}, base), rows=_rows(SPIKE, 12))
    assert _reasons(s) == {"ruleset_change"} and "100 percent on its baseline" in s["confounders"][0]["detail"]
    # every label on the same older ruleset: both sides alike, no confounder
    same = dict(base, **{SPIKE: {"labels_on_older_ruleset": 12}})
    assert _one(_daily({"ITA": series}, same), rows=_rows(SPIKE, 12))["confounders"] == []
    # a change just before the day while the day carries a few more older labels than its baseline
    few = {SPIKE: {"labels_on_older_ruleset": 1}}
    meta = {"ruleset_changes": [{"version": "r9", "date": _day(28)}]}
    s = _one(_daily({"ITA": series}, few), meta, rows=_rows(SPIKE, 12))
    assert _reasons(s) == {"ruleset_change"} and "ruleset r9 took effect" in s["confounders"][0]["detail"]
    meta = {"ruleset_changes": [{"version": "r8", "date": _day(10)}]}
    assert _one(_daily({"ITA": series}, few), meta, rows=_rows(SPIKE, 12))["confounders"] == []


def test_gate_change():
    series = {_day(i): _entry(A=(0 if i < 18 else 2)) for i in range(29)}
    series[SPIKE] = _entry(A=12)
    meta = {"gate_changes": [{"version": "g7", "date": _day(18)}]}
    s = _one(_daily({"ITA": series}), meta, rows=_rows(SPIKE, 12))
    assert _reasons(s) == {"gate_change"} and "twice the level" in s["confounders"][0]["detail"]
    # a gate change that left the level where it was is no confounder
    meta = {"gate_changes": [{"version": "g7", "date": _day(10)}]}
    assert _one(_daily({"ITA": _flat()}), meta, rows=_rows(SPIKE, 12))["confounders"] == []
    # one on the day itself always is
    meta = {"gate_changes": [{"version": "g8", "date": SPIKE}]}
    assert _reasons(_one(_daily({"ITA": _flat()}), meta, rows=_rows(SPIKE, 12))) == {"gate_change"}


def test_saturation():
    series = {d: dict(e, polls=10, sat=4) for d, e in _flat().items()}
    s = _one(_daily({"ITA": series}), {"feed_saturation": {"warning_share": 0.25}}, rows=_rows(SPIKE, 12))
    assert _reasons(s) == {"saturation"}
    series = {d: dict(e, polls=10, sat=1) for d, e in _flat().items()}
    assert _one(_daily({"ITA": series}), {"feed_saturation": {"warning_share": 0.25}}, rows=_rows(SPIKE, 12))["confounders"] == []


def test_llm_ceiling():
    series = {_day(i): _entry(A=1, Al=1, C=1, pending=3) for i in range(29)}     # the baseline still waits
    series[SPIKE] = _entry(A=12, Al=12, C=2, pending=0)
    capped = {_day(20): {"llm_ceiling_hit": True}}
    s = _one(_daily({"ITA": series}, capped), rows=_rows(SPIKE, 12, method="llm"))
    assert _reasons(s) == {"llm_ceiling"}
    sampled = {_day(20): {"llm_sampling": {"ITA": [50, 10]}}}
    assert _reasons(_one(_daily({"ITA": series}, sampled), rows=_rows(SPIKE, 12))) == {"llm_ceiling"}
    # no cap and no sampling on record: nothing
    assert _one(_daily({"ITA": series}), rows=_rows(SPIKE, 12))["confounders"] == []
    # rules labels only: the cap does not touch the count
    rules = {d: dict(e, Al=0) for d, e in series.items()}
    assert _one(_daily({"ITA": rules}, capped), rows=_rows(SPIKE, 12))["confounders"] == []


def test_rediscovery_touches_the_rate_only():
    s = _one(_daily({"ITA": _flat()}), {"rediscovery": {"fixed_from": _day(20)}}, rows=_rows(SPIKE, 12))
    assert _reasons(s) == {"rediscovery"} and s["confounders"][0]["affects"] == "rate"
    assert s["status"] == "clean", "a note on the rate never makes a day one to check"
    assert _one(_daily({"ITA": _flat()}), {"rediscovery": {"fixed_from": _day(0)}}, rows=_rows(SPIKE, 12))["confounders"] == []


def test_denominator_spike():
    s = _one(_daily({"ITA": _flat(spike_disc=1000)}), rows=_rows(SPIKE, 12))
    assert _reasons(s) == {"denominator_spike"} and s["status"] == "check"
    # the intake doubled but the rate per discovered item rose six times: state text surged on its own
    assert _one(_daily({"ITA": _flat(spike_disc=200)}), rows=_rows(SPIKE, 12))["confounders"] == []


# ---------------------------------------------------------------------------
# The side file, the gate and the hand check, on a database
# ---------------------------------------------------------------------------

OUTLETS = [
    {"id": "it_paper", "name": "Paper", "country": "ITA", "language": "it", "feeds": ["https://paper.test/rss"], "tier": "national_daily", "active": True},
    {"id": "it_news", "name": "News", "country": "ITA", "language": "it", "feeds": ["https://news.test/rss"], "tier": "national_daily", "active": True},
    {"id": "us_wire", "name": "Wire", "country": "USA", "language": "en", "feeds": ["https://wire.test/rss"], "tier": "distribution_wire", "active": True},
]


def _seed(conn, spike_n=45):
    n = 0
    for i in range(30):
        d = _day(i)
        k = spike_n if i == 29 else 1
        for j in range(k):
            n += 1
            outlet = "it_paper" if j % 3 else "it_news"
            aid = store.insert_discovered(conn, {"url": "https://paper.test/%d" % n, "outlet_id": outlet, "country": "ITA",
                                                 "language": "it", "title": "Cina %d" % n, "status": "fetched", "gate_relevant": 1})
            conn.execute("UPDATE articles SET discovered_at=?, status='classified' WHERE id=?", (d + "T09:00:00+00:00", aid))
            store.insert_classification(conn, aid, "rules", "A", 1.0, route="wire_credit")
        for o, c in (("it_paper", "ITA"), ("it_news", "ITA"), ("us_wire", "USA")):
            conn.execute("INSERT OR REPLACE INTO daily_outlet_discovery(date, outlet_id, country, discovered, gate_relevant) VALUES (?,?,?,50,1)", (d, o, c))
    conn.commit()
    export.rebuild_rollups(conn, OUTLETS)


def _ctx(conn, tmp_path):
    return {"daily": export.build_daily(conn, OUTLETS), "latest": {"window_start_30d": _day(0)}, "outlets": OUTLETS,
            "meta": {"first_discovered": _day(0)}, "export_dir": tmp_path}


def test_the_side_file_and_the_gate(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    _seed(conn)
    assert spikes.inputs_present(conn) == 30
    cfg = tmp_path / "m.yaml"
    cfg.write_text("- {id: spikes, label: Unusual days, module: pipeline.spikes, enabled: true, min_inputs: 15}\n")
    written = {}
    blocks = measures.run_all_quietly(conn, tmp_path, _ctx(conn, tmp_path), write_json=lambda p, d: written.__setitem__(p.name, d),
                                      config_path=cfg, reviews_dir=tmp_path / "reviews")
    b = blocks["spikes"]
    assert b["status"] == "provisional" and b["file"] == "spikes.json" and "error" not in b, b
    d = written["spikes.json"]
    assert set(d) >= {"generated_at", "version", "method", "first_tested", "last_complete_day", "tests",
                      "expected_false_flags", "expected_false_flag_rate", "counts", "spikes"}
    # two series: Italy, and the worldwide stratum the wire's discovery opens (no state origin in it)
    assert d["last_complete_day"] == SPIKE and d["tests"] == 2 * (30 - spikes.MIN_BASELINE_DAYS)
    s = d["spikes"][0]
    assert set(s) >= {"date", "country", "count", "uniq", "expected", "p", "ratio", "rate", "rate_expected", "top_outlets",
                      "top_items", "confounders", "descriptors", "status", "items"}
    assert (s["date"], s["country"], s["count"], s["uniq"]) == (SPIKE, "ITA", 45, 45)
    # items straight from the articles table, cut at 40, every one of them that day's state origin
    assert len(s["items"]) == spikes.MAX_ITEMS and {i["date"] for i in s["items"]} == {SPIKE}
    assert s["top_outlets"][0] == {"id": "it_paper", "name": "Paper", "placements": 30}
    assert b["summary"]["tests"] == d["tests"] and b["summary"]["spikes"] == 1
    text = b["method_text"]
    assert "by chance alone" in text and "false flag rate" in text and chr(0x2013) not in text and chr(0x2014) not in text
    assert "Category" not in text and "CHN" not in json.dumps(d)
    json.dumps(d)   # serialisable as it stands


def test_the_hand_check(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    _seed(conn)
    csv_path, key_path = spikes.draw_sample(conn, 60, "seed-1", tmp_path / "reviews")
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    key = json.load(open(key_path, encoding="utf-8"))
    assert key["measure_id"] == "spikes" and key["measure_version"] == spikes.VERSION and key["n"] == len(rows)
    assert rows and "status" not in rows[0] and "confounders" not in rows[0], "the coder sees no status"
    assert set(key["spikes"]) == {r["sample_id"] for r in rows}
    # score: 4 real of 5 passes at 0.8, 3 of 5 fails, an unfinished sample has no verdict
    fake_key = {"spikes": {"S%02d" % i: {"status": "clean", "confounders": ["gate_change"]} for i in range(1, 6)}}

    def coded(js, types=None):
        return [{"sample_id": "S%02d" % i, "judgement": j, "artefact_type": (types or {}).get(i, "")} for i, j in enumerate(js, 1)]
    r = spikes.score_check(coded(["real", "Real", "real", "real", "artifact"], {5: "gate_change"}), fake_key)
    assert r["value"] == pytest.approx(0.8) and r["passed"] is True and r["n"] == 5 and r["coded"] == 5
    assert r["artefacts_matching_a_confounder"] == 1 and r["clean_precision"] == pytest.approx(0.8)
    r = spikes.score_check(coded(["real", "real", "real", "artefact", "artefact"]), fake_key)
    assert r["passed"] is False
    r = spikes.score_check(coded(["real", "real", "", "maybe", "real"]), fake_key)
    assert r["coded"] == 3 and r["passed"] is None and r["complete"] is False
    # scored through the gate from the files on disk
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=spikes.SAMPLE_FIELDS)
        w.writeheader()
        for row in rows:
            row["judgement"] = "real"
            w.writerow(row)
    status, check, _ = measures.assess(spikes, "spikes", tmp_path / "reviews")
    assert status == "checked" and check["state"] == "passed"


def test_the_sample_is_capped_at_thirty(monkeypatch):
    many = [{"date": _day(i % 28), "country": "C%02d" % i, "count": 9, "expected": 1.0, "uniq": 9, "top_outlets": [],
             "top_items": [], "items": [], "status": "clean", "confounders": [], "descriptors": []} for i in range(45)]
    import types
    captured = {}

    def fake_write(out_dir, mid, rows, fields, key, version=None, seed=None):
        captured["rows"], captured["key"] = rows, key
        return "c", "k"
    monkeypatch.setattr(spikes, "run_analysis", lambda conn, ctx=None: {"spikes": many})
    monkeypatch.setattr(measures, "write_sample", fake_write)
    spikes.draw_sample(types.SimpleNamespace(), 60, "s", "/nowhere")
    assert len(captured["rows"]) == spikes.SAMPLE_MAX == len(captured["key"]["spikes"])
