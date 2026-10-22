"""State origin by the model's judgement alone is held apart from state origin (decision D1, 2026-10-22):
counted under its own code M, out of every state origin count, share, ranking and finding, still in the
China denominator, with stored labels unchanged, until a blind hand check of it passes
(pipeline/model_only.py)."""
import csv
import datetime as dt
import json

import pytest

from pipeline import classify_llm, config, export, methodology, model_only, spikes, store

OUTLETS = [
    {"id": "it_agency", "name": "Agency", "country": "ITA", "language": "it", "tier": "agency", "active": True,
     "feeds": ["https://agency.test/rss"]},
    {"id": "sg_paper", "name": "Paper", "country": "SGP", "language": "en", "tier": "national_daily", "active": True,
     "feeds": ["https://paper.test/rss"]},
]
COUNTRY = {o["id"]: o["country"] for o in OUTLETS}
LANG = {o["id"]: o["language"] for o in OUTLETS}


def _yesterday():
    return (dt.date.fromisoformat(export._today()) - dt.timedelta(days=1)).isoformat()


def _add(conn, n, outlet, category, method="llm", route=None, day=None, prefix="x"):
    ids = []
    for i in range(n):
        url = "https://%s.test/%s-%s-%s-%s-%d" % (outlet, prefix, category, method, route, i)
        aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": COUNTRY[outlet],
                                             "language": LANG[outlet], "title": "China story %s %d" % (prefix, i),
                                             "status": "fetched", "gate_relevant": 1})
        conn.execute("UPDATE articles SET discovered_at=?, status='classified' WHERE id=?",
                     ("%sT09:00:00+00:00" % (day or _yesterday()), aid))
        store.insert_classification(conn, aid, method, category, 0.9, route=route,
                                    model_version=(config.LLM_MODEL + "@" + classify_llm.PROMPT_VERSION) if method == "llm" else None)
        ids.append(aid)
    conn.commit()
    return ids


@pytest.fixture
def reviews(tmp_path, monkeypatch):
    d = tmp_path / "reviews"
    d.mkdir()
    monkeypatch.setattr(model_only, "REVIEWS_DIR", d)
    return d


@pytest.fixture
def conn(tmp_path, reviews):
    c = store.connect(tmp_path / "m.db")
    store.sync_outlets(c, OUTLETS)
    # Italy: 5 wire credit (rules) and 2 model-only; Singapore: 1 uncredited copy, 6 model-only, 4 independent.
    _add(c, 5, "it_agency", "A", "rules", "wire_credit")
    _add(c, 2, "it_agency", "A", "llm", "unattributed")
    _add(c, 1, "sg_paper", "A", "llm", "uncredited_copy")
    _add(c, 6, "sg_paper", "A", "llm", "unattributed")
    _add(c, 4, "sg_paper", "C", "llm")
    return c


def _export(c):
    export.rebuild_rollups(c, OUTLETS)
    latest = export.build_latest(c, OUTLETS, [])
    daily = export.build_daily(c, OUTLETS)
    outlets_json = export.build_outlets(c, OUTLETS)
    meta = export.build_meta(c, OUTLETS, [], latest, outlets_json)
    return latest, daily, outlets_json, meta


def test_the_predicate_matches_the_route_totals_definition():
    assert model_only.is_model_only("A", "unattributed")
    assert model_only.is_model_only("A", None), "an empty route counts as unattributed, as in meta.route_totals"
    for route in model_only.EVIDENCE_ROUTES:
        assert not model_only.is_model_only("A", route)
    assert not model_only.is_model_only("C", "unattributed")
    assert not model_only.is_model_only("A", None, "rules"), "a rules label stored before routes is evidence"
    assert model_only.is_model_only("A", None, "llm")
    assert model_only.headline_category("A", None, held=True, method="rules") == "A"
    assert model_only.headline_category("A", "unattributed", held=True) == "M"
    assert model_only.headline_category("A", "unattributed", held=False) == "A"
    assert model_only.headline_category("A", "wire_credit", held=True) == "A"
    assert model_only.evidence_sql("c", held=False) == ""
    assert "unattributed" in model_only.evidence_sql("c", held=True)


def test_model_only_placements_leave_state_origin_and_stay_china_coverage(conn):
    latest, daily, outlets_json, meta = _export(conn)
    ita, sgp = latest["countries"]["ITA"]["all_time"], latest["countries"]["SGP"]["all_time"]
    assert (ita["A"], ita["M"]) == (5, 2)
    assert (sgp["A"], sgp["M"]) == (1, 6)
    assert sgp["Al"] == 1, "the model's evidence route label stays in state origin"
    assert sgp["china_total"] == 1 + 6 + 4, "the China denominator keeps the model-only placements"
    assert sgp["share_a"] == round(1 / 11.0, 4)
    tot = latest["totals"]["all_time"]
    assert (tot["A"], tot["M"], tot["china_total"]) == (6, 8, 18)
    assert tot["uniqA"] == 6, "underlying items count evidence route state origin only"
    # the daily files: A without them, M beside it, the route filter's layer under routes
    day = daily[_yesterday()[:7]]["days"][_yesterday()]
    assert day["countries"]["SGP"]["A"] == 1 and day["countries"]["SGP"]["M"] == 6
    assert day["routes"]["SGP"] == {"uncredited_copy": 1, "unattributed": 6}
    assert day["arrivals"]["SGP"] == {"editorial_feed": 1}
    assert len(day["underlying_items"]["SGP"]["A"]) == 1
    # outlets
    by_id = {o["id"]: o for o in outlets_json["outlets"]}
    assert (by_id["sg_paper"]["counts"]["A"], by_id["sg_paper"]["counts"]["M"]) == (1, 6)
    # findings and meta
    f = meta["findings"]
    assert f["all_time"]["state_origin"] == 6 and f["all_time"]["china_total"] == 18
    assert f["all_time"]["state_share_of_china"] == round(6 / 18.0, 4)
    assert f["model_only"]["all_time"] == 8 and f["model_only"]["last_30d"] == 8
    assert f["last_30d"]["state_origin"] == 6
    assert f["top_country"]["iso"] == "ITA", "Singapore leads only with the model-only placements in"
    assert f["top_country"]["state_origin"] == 5 and f["top_country"]["top_route_detail"]["unattributed"] == 0
    mo = meta["model_only_state_origin"]
    assert mo["count"] == meta["route_totals"]["unattributed"] == 8
    assert mo["count_last_30d"] == 8 and mo["status"] == "provisional" and mo["held_apart"] is True
    assert meta["route_signature_totals"] == {"wire_credit": 5, "distribution_stamp": 0, "sponsored_disclosure": 0,
                                              "diplomatic_byline": 0, "uncredited_copy": 1}
    assert sum(meta["route_signature_totals"].values()) == tot["A"]
    assert meta["arrival_totals"] == {"editorial_feed": 6}
    # stored labels are unchanged
    assert conn.execute("SELECT COUNT(*) FROM classifications WHERE is_current=1 AND category='A'").fetchone()[0] == 14


def test_country_carriers_examples_and_articles(conn):
    latest, _, _, _ = _export(conn)
    assert latest["countries"]["SGP"]["top_route"]["id"] == "uncredited_copy"
    assert latest["countries"]["SGP"]["top_route"]["total"] == 1
    arts = export.build_articles(conn, outlets=OUTLETS)
    cats = [a["category"] for a in arts["SGP"]]
    assert cats.count("M") == 6 and cats.count("A") == 1
    assert cats.index("A") < cats.index("M") < cats.index("C"), "listed after state origin, before independent"
    assert all(a["route"] is None for a in arts["SGP"] if a["category"] == "M")


def test_a_rules_label_stored_before_routes_stays_state_origin(conn):
    _add(conn, 3, "it_agency", "A", "rules", None, prefix="old")
    latest, _, _, meta = _export(conn)
    assert latest["countries"]["ITA"]["all_time"]["A"] == 5 + 3
    assert latest["countries"]["ITA"]["all_time"]["M"] == 2
    assert meta["model_only_state_origin"]["count"] == 8


def test_spikes_count_evidence_routes_only(conn):
    rows = spikes.load_rows(conn, OUTLETS)
    assert sum(len(v) for v in rows.values()) == 6


def _code(csv_path, key_path, confirm_share, coded=None):
    """Codes the sheet: of the model-only rows, the first `coded` are coded, confirm_share of them as state
    origin and the rest as independent journalism; controls are coded independent journalism."""
    key = json.loads(key_path.read_text())
    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    mo = [r for r in rows if key["items"][r["sample_id"]]["role"] == "model_only"]
    coded = len(mo) if coded is None else coded
    yes = int(round(confirm_share * coded))
    for i, r in enumerate(mo[:coded]):
        r["human_category"] = "state_origin" if i < yes else "independent_journalism"
    for r in rows:
        if key["items"][r["sample_id"]]["role"] == "control":
            r["human_category"] = "independent_journalism"
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=model_only.FIELDS)
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def big(conn):
    _add(conn, 120, "sg_paper", "A", "llm", "unattributed", prefix="big")
    _add(conn, 60, "sg_paper", "not_relevant", "llm", prefix="nr")
    return conn


def test_the_sheet_is_blind_and_stratified(big, reviews):
    csv_path, key_path = model_only.write_sample(big, n=60, controls=20, seed="t", day="2026-10-22")
    assert csv_path.name == "model_only_a_sample_2026-10-22.csv" and key_path.name == "model_only_a_sample_2026-10-22.key.json"
    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    header = list(rows[0])
    assert header == model_only.FIELDS
    assert not {"category", "route", "article_id", "role", "method"} & set(header), "the coder never sees the label"
    key = json.loads(key_path.read_text())
    roles = [key["items"][r["sample_id"]]["role"] for r in rows]
    assert roles.count("model_only") == 60 and roles.count("control") == 20
    assert key["prompt_version"] == classify_llm.PROMPT_VERSION
    # proportional by language: Italy's 2 of 128 model-only labels get one row at most, Singapore the rest
    assert key["languages"]["en"]["sampled"] + key["languages"]["it"]["sampled"] == 60
    assert key["languages"]["it"]["sampled"] <= 1
    # every control is a model label the model did not call state origin
    assert all(v["category"] in ("C", "not_relevant") for v in key["items"].values() if v["role"] == "control")
    # the same seed draws the same sheet; a second sample the same day is numbered, never overwritten
    again, _ = model_only.write_sample(big, n=60, controls=20, seed="t", day="2026-10-22")
    assert again.name == "model_only_a_sample_2026-10-22_2.csv"
    assert again.read_text() == csv_path.read_text()


def test_the_gate_passes_withholds_and_stays_provisional(big, reviews):
    assert model_only.assess()["status"] == "provisional" and model_only.assess()["check"]["state"] == "none"
    csv_path, key_path = model_only.write_sample(big, n=110, controls=20, seed="g", day="2026-10-22")
    assert model_only.status() == "provisional"
    _code(csv_path, key_path, 0.95, coded=60)
    a = model_only.assess()
    assert a["status"] == "provisional" and a["check"]["state"] == "under_way" and a["check"]["coded"] == 60
    _code(csv_path, key_path, 0.85)
    a = model_only.assess()
    assert a["status"] == "provisional" and a["check"]["state"] == "inconclusive"
    _code(csv_path, key_path, 0.7)
    a = model_only.assess()
    assert a["status"] == "withheld" and a["check"]["precision"] < model_only.WITHHOLD_BELOW
    assert model_only.held_apart()
    _code(csv_path, key_path, 0.92)
    a = model_only.assess()
    assert a["status"] == "passed" and a["check"]["coded"] == 110 and a["check"]["precision"] >= 0.9
    assert a["check"]["controls_coded"] == 20
    assert not model_only.held_apart()


def test_a_passed_check_brings_them_back_into_state_origin(big, reviews):
    csv_path, key_path = model_only.write_sample(big, n=110, controls=10, seed="p", day="2026-10-22")
    _code(csv_path, key_path, 1.0)
    latest, daily, outlets_json, meta = _export(big)
    sgp = latest["countries"]["SGP"]["all_time"]
    assert sgp["M"] == 0 and sgp["A"] == 1 + 6 + 120
    assert meta["model_only_state_origin"]["status"] == "passed" and meta["model_only_state_origin"]["held_apart"] is False
    assert meta["model_only_state_origin"]["count"] == 128
    assert meta["findings"]["top_country"]["iso"] == "SGP"
    labels = {r["id"]: r["label"] for r in meta["routes"]}
    assert "confirmed by a hand check" in labels["unattributed"]


def test_a_sample_from_another_prompt_is_stale(big, reviews, monkeypatch):
    csv_path, key_path = model_only.write_sample(big, n=110, controls=10, seed="s", day="2026-10-22")
    _code(csv_path, key_path, 1.0)
    monkeypatch.setattr(classify_llm, "PROMPT_VERSION", "2099-01-01")
    a = model_only.assess()
    assert a["status"] == "provisional" and a["check"]["state"] == "stale"


def test_an_unreadable_sample_is_reported_not_raised(reviews):
    (reviews / "model_only_a_sample_2026-10-22.csv").write_text("sample_id\n")
    (reviews / "model_only_a_sample_2026-10-22.key.json").write_text("{not json")
    a = model_only.assess()
    assert a["status"] == "provisional" and a["check"]["state"] == "error"


def test_methodology_discloses_the_separation(conn, tmp_path):
    latest, _, _, meta = _export(conn)
    path = tmp_path / "M.md"
    methodology.write(meta, latest, path)
    text = path.read_text(encoding="utf-8")
    assert "State origin by the model's judgement alone." in text
    assert "kept blind to outlet, country and address" in text
    assert "At export time there were 8 such placements" in text
    assert "90 percent of at least 100 coded" in text and "fewer than 80 percent" in text
    assert "Status: provisional, no hand check yet." in text
    assert "A further 8 model judgements of state origin name no evidence at all" in text
    assert "\u2014" not in text and "\u2013" not in text


def test_the_methodology_text_follows_the_status():
    passed = model_only.methodology_text({"count": 10, "status": "passed",
                                          "check": {"confirmed": 95, "coded": 100, "precision": 0.95}})
    assert "counted in state origin again" in passed and "95 of 100" in passed
    withheld = model_only.methodology_text({"count": 10, "status": "withheld",
                                            "check": {"confirmed": 70, "coded": 100, "precision": 0.7}})
    assert "their figure is withheld" in withheld
    old = methodology.model_only_text({"model_only_state_origin": 1123})
    assert "1,123 such placements" in old
    for t in (passed, withheld, old):
        assert "\u2014" not in t and "\u2013" not in t
