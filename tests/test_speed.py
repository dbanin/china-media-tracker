"""Republication speed (pipeline/speed.py): buckets, time resolution, negative lags, the split between
absolute and relative lags, the reference time diagnostics, the hand check and the export shape.
Developed against synthetic state_copies rows."""
import csv
import json
from pathlib import Path

import pytest

from pipeline import measures, registry, speed, store

OUTLETS = [
    {"id": "o_it", "name": "Italpress", "country": "ITA", "tier": "national"},
    {"id": "o_fr", "name": "Le Quotidien", "country": "FRA", "tier": "national"},
    {"id": "o_wire", "name": "Release Wire", "country": "USA", "tier": registry.DISTRIBUTION_WIRE},
]
CTX = {"outlets": OUTLETS}
H = 3600


def _iso(day, hh=0, mm=0, zone="+00:00"):
    return "2026-10-%02dT%02d:%02d:00%s" % (day, hh, mm, zone)


class DB:
    def __init__(self, path):
        self.conn = store.connect(path)
        self.n = 0

    def article(self, outlet, published, discovered, route="wire_credit", country=None, day_of_record=None):
        self.n += 1
        c = self.conn.execute(
            """INSERT INTO articles(url, url_hash, outlet_id, country, language, title, published_at, discovered_at,
               status, day_of_record) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("https://x.test/%d" % self.n, "h%d" % self.n, outlet, country or next(o["country"] for o in OUTLETS if o["id"] == outlet),
             "en", "Title %d" % self.n, published, discovered, "classified", day_of_record))
        aid = c.lastrowid
        self.conn.execute(
            """INSERT INTO classifications(article_id, method, category, confidence, ruleset_version, classified_at, is_current, route)
               VALUES (?,?,?,?,?,?,1,?)""", (aid, "rules", "A", 1.0, "r", discovered, route))
        return aid

    def ref(self, source, published, discovered):
        self.n += 1
        return self.conn.execute(
            "INSERT INTO reference_items(source_id, url, url_hash, title, published_at, discovered_at, status) VALUES (?,?,?,?,?,?,?)",
            (source, "https://ref.test/%d" % self.n, "r%d" % self.n, "Original %d" % self.n, published, discovered, "fetched")).lastrowid

    def copy(self, aid, kind, ref_id=None, orig_article=None, source=None, copy_pub=None, orig_pub=None,
             copy_res="minute", orig_res="minute", lag=None, current=1):
        self.conn.execute(
            """INSERT INTO state_copies(copy_id, version, computed_at, current, original_kind, ref_id, original_article_id,
               original_source, copy_published_at, original_published_at, copy_ts_res, orig_ts_res, lag_seconds)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, "t", "2026-10-13T00:00:00+00:00", current, kind, ref_id, orig_article, source, copy_pub, orig_pub,
             copy_res, orig_res, lag))

    def ref_pair(self, outlet, orig_pub, copy_pub, source="ref_xinhua_en", copy_res="minute", orig_res="minute",
                 route="wire_credit", disc_day=13, lag=None):
        r = self.ref(source, orig_pub, _iso(disc_day, 23))
        a = self.article(outlet, copy_pub, _iso(disc_day, 23), route=route)
        self.copy(a, "reference", ref_id=r, source=source, copy_pub=copy_pub, orig_pub=orig_pub,
                  copy_res=copy_res, orig_res=orig_res, lag=lag)
        return a


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "s.db")
    yield d
    d.conn.close()


# ---------------------------------------------------------------------------
# Buckets and resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lag,bucket", [
    (0, "same_hour"), (59 * 60, "same_hour"), (-59 * 60, "same_hour"), (-H, "same_hour"), (-H - 1, "copy_precedes_original"),
    (H, "same_day"), (23 * H, "same_day"), (24 * H, "days_1_3"), (71 * H, "days_1_3"), (72 * H, "later"), (400 * H, "later")])
def test_timed_buckets(lag, bucket):
    assert speed.timed_bucket(lag) == bucket


@pytest.mark.parametrize("days,bucket", [(-1, "copy_precedes_original"), (0, "same_date"), (1, "date_1_3"),
                                         (3, "date_1_3"), (4, "date_later")])
def test_date_buckets(days, bucket):
    assert speed.date_bucket(days) == bucket


def test_resolution_reads_the_stored_value_or_the_string():
    assert speed.resolution("minute", "2026-10-10T10:00:00+00:00") == "minute"
    assert speed.resolution(None, "2026-10-10") == "day"
    assert speed.resolution("", "2026-10-10T10:00:00+00:00") == "second"
    assert speed.resolution("minute", None) == "none" and speed.resolution("none", "2026-10-10") == "none"
    # pipeline/copies.py writes a date only as midnight UTC with resolution "date"
    assert speed.resolution("date", "2026-10-10T00:00:00+00:00") == "day"
    assert speed.resolution("minute", "2026-10-10") == "day", "a bare date is a date whatever the stored value"


def test_pair_lag_timed_date_and_not_credible():
    c = {"published_at": _iso(10, 14), "discovered_at": _iso(10, 15), "res": "minute"}
    o = {"published_at": _iso(10, 8, 0, "+00:00"), "discovered_at": _iso(10, 9), "res": "minute"}
    r = speed.pair_lag(c, o)
    assert r == {"bucket": "same_day", "lag_seconds": 6 * H, "days": None, "resolution": "time"}
    # zones are honoured: 16:00 at +08:00 is 08:00 UTC, so the copy at 08:30 UTC is the same hour
    o2 = dict(o, published_at=_iso(10, 16, 0, "+08:00"))
    c2 = dict(c, published_at=_iso(10, 8, 30))
    assert speed.pair_lag(c2, o2)["bucket"] == "same_hour"
    # a date only on either side: calendar days, no lag in seconds
    d = speed.pair_lag(dict(c, published_at="2026-10-12", discovered_at=_iso(12, 9), res="day"), o)
    assert d == {"bucket": "date_1_3", "lag_seconds": None, "days": 2, "resolution": "date"}
    assert speed.pair_lag(dict(c, published_at="2026-10-10", res="day"), o)["bucket"] == "same_date"
    # a date stored as midnight UTC with resolution "date" is a date: never 14 h ahead of the original
    mid = dict(c, published_at="2026-10-10T00:00:00+00:00", res="date")
    assert speed.pair_lag(mid, o) == {"bucket": "same_date", "lag_seconds": None, "days": 0, "resolution": "date"}
    # a local midnight stored in UTC falls on the day before: the date the page wrote counts
    local = dict(c, published_at="2026-10-10T16:00:00+00:00", res="date", raw="2026-10-11T00:00:00+08:00",
                 discovered_at=_iso(11, 9))
    assert speed.pair_lag(local, o)["days"] == 1
    # published after its own discovery: not credible
    assert speed.pair_lag(dict(c, discovered_at=_iso(10, 13)), o)["bucket"] == "no_credible_time"
    # before collection began, or missing
    assert speed.pair_lag(c, dict(o, published_at="2026-08-01T00:00:00+00:00"))["bucket"] == "no_credible_time"
    assert speed.pair_lag(dict(c, published_at=None), o)["bucket"] == "no_credible_time"
    assert speed.pair_lag(dict(c, res="none"), o)["bucket"] == "no_credible_time"


def test_a_negative_lag():
    c = {"published_at": _iso(10, 6), "discovered_at": _iso(10, 7)}
    o = {"published_at": _iso(10, 9), "discovered_at": _iso(10, 10)}
    r = speed.pair_lag(c, o)
    assert r["bucket"] == "copy_precedes_original" and r["lag_seconds"] == -3 * H
    # thirty minutes ahead is clock noise: same hour, and it stays in the distribution
    c2 = dict(c, published_at=_iso(10, 8, 30), discovered_at=_iso(10, 9))
    assert speed.pair_lag(c2, o) == {"bucket": "same_hour", "lag_seconds": -1800, "days": None, "resolution": "time"}
    # a date only copy a calendar day ahead
    assert speed.pair_lag(dict(c, published_at="2026-10-09"), o)["bucket"] == "copy_precedes_original"


def test_quantile_and_summarise():
    assert speed.quantile([], 0.5) is None
    assert speed.quantile([3, 1, 2], 0.5) == 2 and speed.quantile([1, 2, 3, 4], 0.25) == 1.75
    items = [{"bucket": speed.timed_bucket(h * H), "lag_seconds": h * H} for h in (0, 2, 4, 6, 30)]
    items += [{"bucket": "same_date", "lag_seconds": None}, {"bucket": "no_credible_time", "lag_seconds": None},
              {"bucket": "copy_precedes_original", "lag_seconds": -5 * H}]
    s = speed.summarise(items)
    assert s["n"] == 6 and s["n_timed"] == 5 and s["n_date"] == 1 and s["outside"] == 2
    assert s["median_hours"] == 4 and s["p25_hours"] == 2 and s["p75_hours"] == 6
    assert s["buckets"]["same_hour"] == 1 and s["buckets"]["same_day"] == 3 and s["buckets"]["days_1_3"] == 1
    # the copy ahead of its original never enters the median
    assert speed.summarise(items, min_median=6)["median_hours"] is None


# ---------------------------------------------------------------------------
# Pairs from the database
# ---------------------------------------------------------------------------

def test_absolute_and_relative_are_never_pooled_and_lag_is_recomputed(db):
    # reference original: 2 h; the stored lag_seconds is wrong on purpose and is ignored
    db.ref_pair("o_it", _iso(10, 8), _iso(10, 10), lag=999999)
    # monitored original: the first monitored copy at 08:00, this one 30 h later
    first = db.article("o_fr", _iso(10, 8), _iso(10, 9))
    later = db.article("o_it", _iso(11, 14), _iso(11, 15))
    db.copy(later, "monitored", orig_article=first, source="o_fr", copy_pub=_iso(11, 14), orig_pub=_iso(10, 8))
    # no original, and a row that is no longer current: neither is a pair
    none_a = db.article("o_it", _iso(10, 8), _iso(10, 9))
    db.copy(none_a, "none")
    old = db.article("o_it", _iso(10, 8), _iso(10, 9))
    db.copy(old, "reference", ref_id=db.ref("ref_xinhua_en", _iso(10, 7), _iso(10, 9)), current=0)
    db.conn.commit()
    assert speed.inputs_present(db.conn) == 2
    pairs = {p["copy_id"]: p for p in speed.load_pairs(db.conn, CTX)}
    assert len(pairs) == 2
    abs_ = [p for p in pairs.values() if p["kind"] == "absolute"]
    rel = [p for p in pairs.values() if p["kind"] == "relative"]
    assert len(abs_) == 1 and abs_[0]["lag_seconds"] == 2 * H and abs_[0]["bucket"] == "same_day"
    assert abs_[0]["source"] == "ref_xinhua_en"
    assert len(rel) == 1 and rel[0]["lag_seconds"] == 30 * H and rel[0]["bucket"] == "days_1_3"
    out = speed.build_export(db.conn, CTX)
    assert out["absolute"]["overall"]["n"] == 1 and out["relative"]["overall"]["n"] == 1
    assert out["absolute"]["overall"]["buckets"]["days_1_3"] == 0 and out["relative"]["overall"]["buckets"]["same_day"] == 0
    assert out["coverage"]["copies"] == 3 and out["coverage"]["none"] == 1 and out["coverage"]["reference"] == 1


def test_stored_times_fall_back_to_the_source_rows(db):
    r = db.ref("ref_cgtn_en", _iso(10, 8), _iso(10, 9))
    a = db.article("o_it", _iso(10, 9), _iso(10, 10))
    db.copy(a, "reference", ref_id=r, source="ref_cgtn_en", copy_pub=None, orig_pub=None, copy_res=None, orig_res=None)
    db.conn.commit()
    (p,) = speed.load_pairs(db.conn, CTX)
    assert p["lag_seconds"] == H and p["bucket"] == "same_day"


def test_the_credibility_check_uses_each_items_own_discovery(db):
    # the reference item's time is after its own discovery (a zone written wrongly): no credible time
    r = db.ref("ref_ecns_en", _iso(10, 18), _iso(10, 12))
    a = db.article("o_it", _iso(10, 20), _iso(10, 21))
    db.copy(a, "reference", ref_id=r, source="ref_ecns_en", copy_pub=_iso(10, 20), orig_pub=_iso(10, 18))
    db.conn.commit()
    (p,) = speed.load_pairs(db.conn, CTX)
    assert p["bucket"] == "no_credible_time"
    s = speed.build_export(db.conn, CTX)["absolute"]["overall"]
    assert s["n"] == 0 and s["outside"] == 1 and s["buckets"]["no_credible_time"] == 1


def test_groups_wires_under_wld_and_outlets_need_ten_pairs(db):
    for i in range(12):
        db.ref_pair("o_it", _iso(10, 8), _iso(10, 8, 10 + i))           # same hour
    for i in range(9):
        db.ref_pair("o_fr", _iso(10, 8), _iso(10, 20), route="uncredited_copy")  # 12 h
    for i in range(10):
        db.ref_pair("o_wire", _iso(10, 8), _iso(11, 20), source="ref_cgtn_en", route="distribution_stamp")  # 36 h
    db.conn.commit()
    out = speed.build_export(db.conn, CTX)
    a = out["absolute"]
    countries = {r["key"]: r for r in a["by_country"]}
    assert set(countries) == {"ITA", "FRA", "WLD"} and "USA" not in countries
    assert countries["WLD"]["buckets"]["days_1_3"] == 10
    outlets = [r["key"] for r in a["by_outlet"]]
    assert outlets == ["o_it", "o_wire"], "ranked fastest first, and o_fr has only 9 pairs"
    assert a["by_outlet"][0]["name"] == "Italpress" and a["by_outlet"][1]["country"] == "WLD"
    assert a["by_outlet"][0]["median_hours"] < 1 and a["by_outlet"][1]["median_hours"] == 36
    routes = {r["key"]: r for r in a["by_route"]}
    assert routes["uncredited_copy"]["label"] and routes["uncredited_copy"]["median_hours"] == 12
    sources = {r["key"]: r["n"] for r in a["by_source"]}
    assert sources == {"ref_xinhua_en": 21, "ref_cgtn_en": 10}
    assert out["relative"]["overall"]["n"] == 0 and out["relative"]["by_outlet"] == []
    assert "CHN" not in json.dumps(out)


def test_date_pairs_are_bucketed_by_date_and_kept_out_of_the_median(db):
    for i in range(5):
        db.ref_pair("o_it", _iso(10, 8), _iso(10, 9))
    db.ref_pair("o_it", "2026-10-10", _iso(12, 9), orig_res="day")
    db.conn.commit()
    s = speed.build_export(db.conn, CTX)["absolute"]["overall"]
    assert s["n"] == 6 and s["n_timed"] == 5 and s["n_date"] == 1 and s["buckets"]["date_1_3"] == 1
    assert s["median_hours"] == 1


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def test_reference_time_diagnostics(db):
    # a well labelled source: published 1 to 3 h before discovery
    for h in (1, 2, 3):
        db.ref("ref_cgtn_en", _iso(10, 10 - h), _iso(10, 10))
    # a source writing Beijing time as UTC: every time reads 8 h late, after its discovery
    for h in (1, 2, 3, 4):
        db.ref("ref_ecns_en", _iso(10, 10 - h + 8), _iso(10, 10))
    db.ref("ref_ecns_en", _iso(10, 5), _iso(10, 10))
    # a date only source, and an item with no time at all
    for d in range(1, 11):
        db.ref("ref_peoples_daily_it", "2026-10-%02d" % d, _iso(10, 10))
    db.ref("ref_peoples_daily_it", None, _iso(10, 10))
    # two dates are too few to flag a source
    for d in (9, 10):
        db.ref("ref_china_daily_en", "2026-10-%02d" % d, _iso(10, 10))
    db.conn.commit()
    diag = {d["source"]: d for d in speed.diagnostics(db.conn)}
    c = diag["ref_cgtn_en"]
    assert c["timed"] == 3 and c["median_discovery_gap_hours"] == 2 and c["share_negative"] == 0 and c["flags"] == []
    e = diag["ref_ecns_en"]
    assert e["share_negative"] == 0.8 and e["median_negative_hours"] == -5.5 and "published after discovery" in e["flags"]
    p = diag["ref_peoples_daily_it"]
    assert p["items"] == 11 and p["no_time"] == 1 and p["share_date_only"] == 1.0 and "mostly date only" in p["flags"]
    # discovered more than a day before now and still a date only: flagged from the reference status too
    assert p["share_date_only_after_day_one"] == 1.0 and "date only after day one" in p["flags"]
    cd = diag["ref_china_daily_en"]
    assert cd["share_date_only"] == 1.0 and cd["flags"] == []
    assert speed.build_export(db.conn, CTX)["diagnostics"] == speed.diagnostics(db.conn)
    summ = speed.meta_summary(db.conn, CTX)
    assert summ["flagged_sources"] == ["ref_ecns_en", "ref_peoples_daily_it"]
    assert summ["source_flags"]["ref_ecns_en"] == ["published after discovery"]


def test_one_stray_time_after_discovery_flags_nothing(db):
    for h in range(1, 20):
        db.ref("ref_cgtn_en", _iso(10, 9, h), _iso(10, 10))
    db.ref("ref_cgtn_en", _iso(10, 12), _iso(10, 10))
    db.conn.commit()
    (c,) = speed.diagnostics(db.conn)
    assert c["share_negative"] == 0.05 and c["flags"] == []


def test_a_listing_that_reads_early_is_flagged(db):
    from pipeline import reference
    checks = [("h%d" % i, "https://rss", "as_published", _iso(10, 9), "https://sitemap", "bjt_if_unzoned", _iso(10, 1), -8 * H)
              for i in range(12)]
    reference.record_time_checks(db.conn, "ref_cgtn_en", checks)
    db.ref("ref_cgtn_en", _iso(10, 9), _iso(10, 10))
    db.conn.commit()
    (c,) = speed.diagnostics(db.conn)
    assert c["time_check"]["compared"] == 12 and c["time_check"]["share_earlier"] == 1.0
    assert c["time_check"]["median_diff_hours"] == -8 and "earlier than expected" in c["flags"]
    assert speed.meta_summary(db.conn, CTX)["source_flags"] == {"ref_cgtn_en": ["earlier than expected"]}


# ---------------------------------------------------------------------------
# Hand check
# ---------------------------------------------------------------------------

def test_coded_times_and_zones():
    assert speed.parse_zone("+08:00") == 480 and speed.parse_zone("UTC+8") == 480 and speed.parse_zone("GMT-5") == -300
    assert speed.parse_zone("bjt") == 480 and speed.parse_zone("CEST") == 120 and speed.parse_zone("") is None
    assert speed.parse_zone("somewhere") is None
    assert speed.coded_time("", "") is None and speed.coded_time("none", "")[0] == "none"
    assert speed.coded_time("2026-10-10 14:00", "unknown")[0] == "date", "no readable zone: the date only"
    t = speed.coded_time("2026-10-10 02:00", "+08:00")
    assert t[0] == "time" and str(t[2]) == "2026-10-09", "the UTC date"
    assert speed.coded_time("10 Oct 2026", "UTC") is None


def test_score_check_maths():
    key = {"pairs": {"S001": {"bucket": "same_day"}, "S002": {"bucket": "same_hour"}, "S003": {"bucket": "date_1_3"},
                     "S004": {"bucket": "no_credible_time"}}}
    rows = [
        # 14:00 BJT is 06:00 UTC; 10:00 CET is 09:00 UTC: 3 h, same day
        {"pair": "S001", "copy_time_shown": "2026-10-10 10:00", "copy_zone_shown": "CET",
         "original_time_shown": "2026-10-10 14:00", "original_zone_shown": "BJT"},
        # the coder finds 2 h: disagrees with same hour
        {"pair": "S002", "copy_time_shown": "2026-10-10 10:00", "copy_zone_shown": "UTC",
         "original_time_shown": "2026-10-10 08:00", "original_zone_shown": "UTC"},
        {"pair": "S003", "copy_time_shown": "2026-10-12", "original_time_shown": "2026-10-10 08:00", "original_zone_shown": "+08:00"},
        {"pair": "S004", "copy_time_shown": "none", "original_time_shown": ""},   # not coded yet
    ]
    r = speed.score_check(rows, key)
    assert r["n"] == 4 and r["coded"] == 3 and r["agree"] == 2 and r["value"] == round(2 / 3, 4)
    assert r["complete"] is False and r["passed"] is None and r["threshold"] == 0.85
    rows[3]["original_time_shown"] = "2026-10-10"
    rows[1]["copy_time_shown"] = "2026-10-10 08:40"
    r = speed.score_check(rows, key)
    assert r["coded"] == 4 and r["value"] == 1.0 and r["passed"] is True
    rows[1]["copy_time_shown"] = "2026-10-10 10:00"
    r = speed.score_check(rows, key)
    assert r["value"] == 0.75 and r["passed"] is False


def test_draw_sample_and_score_through_the_gate(db, tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: OUTLETS)
    for i in range(70):
        db.ref_pair("o_it", _iso(10, 8), _iso(10, 8, 10))
    first = db.article("o_fr", _iso(10, 8), _iso(10, 9))
    later = db.article("o_it", _iso(11, 14), _iso(11, 15))
    db.copy(later, "monitored", orig_article=first, source="o_fr", copy_pub=_iso(11, 14), orig_pub=_iso(10, 8))
    db.conn.commit()
    csv_path, key_path = speed.draw_sample(db.conn, 60, "seed-1", tmp_path / "reviews")
    assert csv_path.name.startswith("speed_sample_") and key_path.name.endswith(".key.json")
    key = json.loads(key_path.read_text())
    assert key["n"] == 60 and len(key["pairs"]) == 60 and key["measure_version"] == speed.VERSION
    assert all(v["bucket"] == "same_hour" for v in key["pairs"].values())
    assert later not in {v["copy_id"] for v in key["pairs"].values()}, "reference pairs only"
    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == speed.SAMPLE_FIELDS and "bucket" not in rows[0], "the sheet is blind"
    assert speed.draw_sample(db.conn, 60, "seed-1", tmp_path / "again")[1].read_text().count("copy_id") == 60
    # coded as the measure: passes through the shared gate
    for r in rows:
        r.update({"copy_time_shown": "2026-10-10 08:10", "copy_zone_shown": "UTC",
                  "original_time_shown": "2026-10-10 16:00", "original_zone_shown": "UTC+8"})
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=speed.SAMPLE_FIELDS)
        w.writeheader()
        w.writerows(rows)
    status, check, reason = measures.assess(speed, "speed", tmp_path / "reviews")
    assert status == "checked" and check["value"] == 1.0 and check["n"] == 60


# ---------------------------------------------------------------------------
# Interface, export shape and text
# ---------------------------------------------------------------------------

def test_interface_and_export_shape(db):
    assert all(callable(getattr(speed, f)) for f in measures.REQUIRED) and speed.MEASURE_ID == "speed"
    assert speed.inputs_present(db.conn) == 0
    empty = speed.build_export(db.conn, CTX)
    assert set(empty) == {"measure", "version", "buckets", "min_outlet_pairs", "min_group_median", "absolute", "relative",
                          "coverage", "diagnostics", "reference_sources"}
    for kind in ("absolute", "relative"):
        assert set(empty[kind]) == {"overall", "by_route", "by_country", "by_source", "by_outlet"}
        assert set(empty[kind]["overall"]) == {"n", "n_timed", "n_date", "originals", "originals_timed", "outside",
                                               "median_hours", "p25_hours", "p75_hours", "buckets"}
        assert list(empty[kind]["overall"]["buckets"]) == list(speed.BUCKETS)
    assert set(empty["buckets"]["labels"]) == set(speed.BUCKETS)
    db.ref_pair("o_it", _iso(10, 8), _iso(10, 9))
    db.conn.commit()
    assert speed.compute(db.conn, CTX, 0) == {"pairs": 1, "absolute": 1, "absolute_in_distribution": 1, "relative": 0,
                                               "relative_in_distribution": 0}
    out = speed.build_export(db.conn, CTX)
    json.dumps(out)
    assert "Title" not in json.dumps(out) and "https://" not in json.dumps(out), "no titles, links or text"
    row = out["absolute"]["by_route"][0]
    assert {"key", "label", "n", "median_hours", "buckets"} <= set(row)
    summ = speed.meta_summary(db.conn, CTX)
    assert summ["absolute"] == {"pairs": 1, "n": 1, "originals": 1, "originals_timed": 1, "median_hours": None}
    (src,) = out["reference_sources"]
    assert src["source"] == "ref_xinhua_en" and src["statuses"]["fetched"] == 1


def test_an_old_database_without_the_tables(tmp_path):
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.row_factory = sqlite3.Row
    assert speed.inputs_present(conn) == 0 and speed.load_pairs(conn, CTX) == [] and speed.diagnostics(conn) == []


def test_methodology_text_is_plain():
    t = speed.methodology_text({"status": "provisional", "summary": {"absolute": {"n": 12}}})
    assert "\u2014" not in t and "\u2013" not in t and " - " not in t and not t.startswith("#")
    assert "12 absolute pairs" in t and t.count("\n\n") == 1
    t2 = speed.methodology_text({"summary": {"absolute": {"n": 12, "originals": 3}}})
    assert "with 3 distinct originals" in t2 and "one zone" in t2 and "eight hours earlier" in t2 and "sister outlet" in t2
    for words in ("last updated", "time zones wrongly", "only a date", "channels", "Coverage is partial", "WLD"):
        assert words in t
    assert "Category" not in t


def test_the_measures_entry():
    spec = next(m for m in measures.load_config() if m["id"] == "speed")
    assert spec["module"] == "pipeline.speed" and spec["enabled"] is True
    ids = [m["id"] for m in measures.load_config()]
    assert ids.index("alteration") < ids.index("speed"), "speed reads what alteration wrote"


def test_rows_as_pipeline_copies_writes_them(db):
    """pipeline/copies.py stores UTC times, a date only as midnight with resolution "date", and NULL
    with resolution "none" when a time is not credible."""
    first = db.article("o_fr", _iso(10, 5, 34), _iso(10, 7))
    later = db.article("o_it", "2026-10-10", _iso(10, 7, 1))
    db.copy(later, "monitored", orig_article=first, source="o_fr", copy_pub="2026-10-10T00:00:00+00:00",
            orig_pub=_iso(10, 5, 34), copy_res="date", orig_res="second", lag=-20040)
    bad = db.article("o_it", _iso(12, 9), _iso(10, 8))       # published after discovery
    db.copy(bad, "monitored", orig_article=first, source="o_fr", copy_pub=None, orig_pub=_iso(10, 5, 34),
            copy_res="none", orig_res="second")
    db.conn.commit()
    got = {p["copy_id"]: p["bucket"] for p in speed.load_pairs(db.conn, CTX)}
    assert got == {later: "same_date", bad: "no_credible_time"}, "never counted as a copy 5 h ahead"



# ---------------------------------------------------------------------------
# Distinct originals and one zone for dates (F6a)
# ---------------------------------------------------------------------------

def test_one_release_at_many_sister_outlets_gives_no_median(db):
    # eleven copies of one release, as the ACM mastheads ran one press release: one original
    r = db.ref("ref_xinhua_en", _iso(10, 8), _iso(10, 23))
    for i in range(11):
        a = db.article("o_it", _iso(10, 10, 37), _iso(10, 23))
        db.copy(a, "reference", ref_id=r, source="ref_xinhua_en", copy_pub=_iso(10, 10, 37), orig_pub=_iso(10, 8))
    db.conn.commit()
    s = speed.build_export(db.conn, CTX)["absolute"]
    o = s["overall"]
    assert o["n_timed"] == 11 and o["originals"] == 1 and o["originals_timed"] == 1
    assert o["median_hours"] is None and o["p25_hours"] is None, "a median needs distinct originals, not pairs"
    assert s["by_outlet"] == [], "an outlet is listed from ten distinct originals"
    # five distinct originals make a median
    for i in range(4):
        db.ref_pair("o_it", _iso(10, 8), _iso(10, 10))
    db.conn.commit()
    o = speed.build_export(db.conn, CTX)["absolute"]["overall"]
    assert o["originals_timed"] == 5 and o["median_hours"] is not None


def test_a_beijing_date_is_compared_with_the_copy_time_in_beijing():
    # The copy at 20:00 UTC on the 10th is 04:00 on the 11th in Beijing: a Beijing date of the 11th is the same date.
    c = {"published_at": _iso(10, 20), "discovered_at": _iso(10, 21), "res": "minute"}
    o = {"published_at": "2026-10-11", "discovered_at": _iso(10, 21), "res": "day", "raw": "2026-10-11", "date_zone": 480}
    assert speed.pair_lag(c, o) == {"bucket": "same_date", "lag_seconds": None, "days": 0, "resolution": "date"}
    # read as a UTC date (the old reading) the original would seem published after its own discovery
    assert speed.pair_lag(c, dict(o, date_zone=0))["bucket"] == "no_credible_time"
    # and with a later discovery, a copy a day ahead of its original
    assert speed.pair_lag(c, dict(o, date_zone=0, discovered_at=_iso(11, 9)))["bucket"] == "copy_precedes_original"
    assert speed.pair_lag(c, dict(o, discovered_at=_iso(11, 9)))["bucket"] == "same_date"
    # the Global Times sitemap date is a UTC date
    assert speed.pair_lag(c, dict(o, published_at="2026-10-10", raw="2026-10-10", date_zone=0))["days"] == 0
    # two dates in two zones: by their middays
    cd = {"published_at": "2026-10-11T00:00:00+00:00", "raw": "2026-10-11", "discovered_at": _iso(11, 9), "res": "date"}
    assert speed.pair_lag(cd, dict(o, published_at="2026-10-10", raw="2026-10-10", discovered_at=_iso(10, 9)))["days"] == 1


def test_reference_dates_take_their_sources_zone(db):
    from pipeline import reference
    zones = reference.date_zones()
    assert zones["ref_xinhua_en"] == zones["ref_peoples_daily_en"] == zones["ref_china_daily_en"] == 480
    assert zones["ref_global_times_en"] == 0 and zones["ref_ecns_en"] == 480
    r = db.ref("ref_peoples_daily_en", "2026-10-11", _iso(10, 22))
    a = db.article("o_it", _iso(10, 20), _iso(10, 21))
    db.copy(a, "reference", ref_id=r, source="ref_peoples_daily_en", copy_pub=_iso(10, 20), orig_pub="2026-10-11T00:00:00+00:00",
            orig_res="date")
    db.conn.commit()
    (p,) = speed.load_pairs(db.conn, CTX)
    assert p["days"] == 0 and p["bucket"] == "same_date" and p["orig_key"] == "r%d" % r


def test_the_coded_times_count_dates_in_one_zone_as_the_measure_does():
    # the state page gives a Beijing date; the copy at 20:00 UTC is already the next day in Beijing
    row = {"copy_time_shown": "2026-10-10 20:00", "copy_zone_shown": "UTC", "original_time_shown": "2026-10-11",
           "original_zone_shown": ""}
    assert speed.coded_bucket(row) == "same_date"
    assert speed.coded_bucket(dict(row, original_zone_shown="UTC")) == "copy_precedes_original"
    # a copy that gives a date only, against a timed original converted into the copy's zone (UTC unless written)
    row = {"copy_time_shown": "2026-10-11", "copy_zone_shown": "", "original_time_shown": "2026-10-11 02:00",
           "original_zone_shown": "BJT"}
    assert speed.coded_bucket(row) == "date_1_3"
    assert speed.coded_bucket(dict(row, copy_zone_shown="+08:00")) == "same_date"
