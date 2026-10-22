"""Undetermined items (decision D1 as revised on 2026-10-22, after the original study): a state origin label
whose state authorship the evidence cannot settle is undetermined, code U, and excluded from every category
count, share and denominator, China coverage included. They are the model's state origin calls with no
checkable marker (route unattributed or empty) and its sponsored disclosure calls the rules did not confirm;
rules labels and the model's wire credit, stamp, byline and uncredited copy calls stay state origin. Nothing
rejoins state origin, and the blind hand check only reports what the coders found (pipeline/model_only.py)."""
import csv
import datetime as dt
import json

import pytest

from pipeline import classify_llm, config, doctrine, export, methodology, model_only, spikes, store

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
    # Italy: 5 wire credit (rules), 2 rules sponsored, 2 unattributed (model), 3 sponsored (model).
    # Singapore: 1 uncredited copy (model), 6 unattributed (model), 4 independent.
    _add(c, 5, "it_agency", "A", "rules", "wire_credit")
    _add(c, 2, "it_agency", "A", "rules", "sponsored_disclosure")
    _add(c, 2, "it_agency", "A", "llm", "unattributed")
    _add(c, 3, "it_agency", "A", "llm", "sponsored_disclosure")
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


def test_which_labels_are_undetermined():
    assert model_only.CODE == "U"
    assert model_only.is_undetermined("A", "unattributed")
    assert model_only.is_undetermined("A", None), "an empty route counts as unattributed, as in meta.route_totals"
    assert model_only.is_undetermined("A", "sponsored_disclosure", "llm"), "a sponsored disclosure only the model found"
    assert not model_only.is_undetermined("A", "sponsored_disclosure", "rules"), "the rules found it on the article"
    for route in ("wire_credit", "distribution_stamp", "diplomatic_byline", "uncredited_copy"):
        assert not model_only.is_undetermined("A", route, "llm"), route
        assert not model_only.is_undetermined("A", route, "rules"), route
    assert not model_only.is_undetermined("C", "unattributed")
    assert not model_only.is_undetermined("A", None, "rules"), "a rules label stored before routes is evidence"
    assert model_only.headline_category("A", "sponsored_disclosure", "llm") == "U"
    assert model_only.headline_category("A", "sponsored_disclosure", "rules") == "A"
    assert model_only.headline_category("A", "unattributed") == "U"
    assert model_only.headline_category("A", "wire_credit") == "A"
    assert model_only.counted_route("sponsored_disclosure", "llm") == "unattributed"
    assert model_only.counted_route("sponsored_disclosure", "rules") == "sponsored_disclosure"
    sql = model_only.evidence_sql("c")
    assert "sponsored_disclosure" in sql and "unattributed" in sql and "rules" in sql


def test_nothing_rejoins_state_origin():
    for name in ("held_apart", "status", "verdict", "PASS_PRECISION", "WITHHOLD_BELOW", "is_model_only"):
        assert not hasattr(model_only, name), name
    import inspect
    sig = inspect.signature(model_only.headline_category)
    assert "held" not in sig.parameters


def test_undetermined_items_are_in_no_count_and_no_denominator(conn):
    latest, daily, outlets_json, meta = _export(conn)
    ita, sgp = latest["countries"]["ITA"]["all_time"], latest["countries"]["SGP"]["all_time"]
    assert (ita["A"], ita["U"]) == (5 + 2, 2 + 3)
    assert ita["Ar"] == 7 and ita["Al"] == 0
    assert (sgp["A"], sgp["U"]) == (1, 6)
    assert sgp["Al"] == 1, "the model's evidence route label stays in state origin"
    # china_total is A + B + C only: the undetermined placements are in no denominator.
    for k in (ita, sgp):
        assert k["china_total"] == k["A"] + k["B"] + k["C"]
    assert sgp["china_total"] == 1 + 4
    assert sgp["share_a"] == round(1 / 5.0, 4)
    tot = latest["totals"]["all_time"]
    assert (tot["A"], tot["U"], tot["china_total"]) == (8, 11, 12)
    assert tot["china_total"] == tot["A"] + tot["B"] + tot["C"]
    assert tot["tchina"] == 0 or tot["tchina"] <= tot["A"] + tot["B"] + tot["C"] + tot["pending"]
    assert tot["uniqA"] == 8, "underlying items count evidence route state origin only"
    # the daily files: A without them, U beside it, the route filter's undetermined layer under routes
    day = daily[_yesterday()[:7]]["days"][_yesterday()]
    assert day["countries"]["SGP"]["A"] == 1 and day["countries"]["SGP"]["U"] == 6
    assert day["countries"]["ITA"]["A"] == 7 and day["countries"]["ITA"]["U"] == 5
    assert day["routes"]["SGP"] == {"uncredited_copy": 1, "unattributed": 6}
    assert day["routes"]["ITA"] == {"wire_credit": 5, "sponsored_disclosure": 2, "unattributed": 5}
    assert day["arrivals"]["SGP"] == {"editorial_feed": 1}
    assert len(day["underlying_items"]["SGP"]["A"]) == 1
    # themes count China coverage only
    assert all(v[0] <= 5 for v in (day["themes"].get("SGP") or {}).values())
    # outlets
    by_id = {o["id"]: o for o in outlets_json["outlets"]}
    assert (by_id["sg_paper"]["counts"]["A"], by_id["sg_paper"]["counts"]["U"]) == (1, 6)
    assert (by_id["it_agency"]["counts"]["A"], by_id["it_agency"]["counts"]["U"]) == (7, 5)
    # findings and meta
    f = meta["findings"]
    assert f["all_time"]["state_origin"] == 8 and f["all_time"]["china_total"] == 12
    assert f["all_time"]["state_share_of_china"] == round(8 / 12.0, 4)
    assert f["undetermined"]["all_time"] == 11 and f["undetermined"]["last_30d"] == 11
    assert f["undetermined"]["excluded_from_counts"] is True
    assert "model_only" not in f
    assert f["last_30d"]["state_origin"] == 8
    assert f["top_country"]["iso"] == "ITA", "Singapore would lead only with its undetermined placements in"
    assert f["top_country"]["state_origin"] == 7 and f["top_country"]["top_route_detail"]["unattributed"] == 0
    ud = meta["undetermined"]
    assert "model_only_state_origin" not in meta
    assert ud["count"] == meta["route_totals"]["unattributed"] == 11
    assert ud["by_route"] == {"unattributed": 8, "sponsored_disclosure": 3}
    assert ud["count_last_30d"] == 11 and ud["excluded_from_counts"] is True and ud["rejoins_state_origin"] is False
    assert ud["code"] == "U" and ud["label"] == model_only.LABEL
    assert "status" not in ud and "held_apart" not in ud and "passed" not in ud
    assert meta["route_signature_totals"] == {"wire_credit": 5, "distribution_stamp": 0, "sponsored_disclosure": 2,
                                              "diplomatic_byline": 0, "uncredited_copy": 1}
    assert sum(meta["route_signature_totals"].values()) == tot["A"]
    assert meta["arrival_totals"] == {"editorial_feed": 8}
    assert {r["id"]: r["label"] for r in meta["routes"]}["unattributed"].startswith("Undetermined, not counted")
    # the gate accounting still adds up: U is a classified row
    assert tot["A"] + tot["B"] + tot["C"] + tot["N"] + tot["U"] == tot["cls"]
    # stored labels are unchanged
    assert conn.execute("SELECT COUNT(*) FROM classifications WHERE is_current=1 AND category='A'").fetchone()[0] == 19


def test_model_sponsored_labels_are_undetermined_and_rules_ones_state_origin(conn):
    arts = export.build_articles(conn, outlets=OUTLETS)
    ita = arts["ITA"]
    a_routes = sorted(a["route"] for a in ita if a["category"] == "A")
    assert a_routes.count("sponsored_disclosure") == 2 and a_routes.count("wire_credit") == 5
    assert sum(1 for a in ita if a["category"] == "U") == 5
    assert all(a["route"] is None for a in ita if a["category"] == "U")


def test_country_carriers_examples_and_articles(conn):
    latest, _, _, _ = _export(conn)
    assert latest["countries"]["SGP"]["top_route"]["id"] == "uncredited_copy"
    assert latest["countries"]["SGP"]["top_route"]["total"] == 1
    assert latest["countries"]["ITA"]["top_route"]["total"] == 7
    arts = export.build_articles(conn, outlets=OUTLETS)
    cats = [a["category"] for a in arts["SGP"]]
    assert cats.count("U") == 6 and cats.count("A") == 1
    assert cats.index("A") < cats.index("U") < cats.index("C"), "listed after state origin, before independent"


def test_a_rules_label_stored_before_routes_stays_state_origin(conn):
    _add(conn, 3, "it_agency", "A", "rules", None, prefix="old")
    latest, _, _, meta = _export(conn)
    assert latest["countries"]["ITA"]["all_time"]["A"] == 7 + 3
    assert latest["countries"]["ITA"]["all_time"]["U"] == 5
    assert meta["undetermined"]["count"] == 11


def test_spikes_count_evidence_routes_only(conn):
    rows = spikes.load_rows(conn, OUTLETS)
    assert sum(len(v) for v in rows.values()) == 8


def test_doctrine_counts_no_undetermined_item(conn, monkeypatch):
    v = doctrine.version()
    tag = json.dumps({"v": v, "f": {}})
    conn.execute("UPDATE articles SET doctrine=?", (tag,))
    conn.commit()
    out = doctrine.build_export(conn, {"outlets": OUTLETS})
    china = sum(t[0] for per in out["totals"].values() for t in per.values())
    assert china == 8 + 4, "state origin and independent journalism only; no undetermined item in the denominator"


def _code(csv_path, key_path, confirm_share, coded=None, undetermined_share=0.0):
    """Codes the sheet: of the undetermined rows, the first `coded` are coded, confirm_share of them as state
    origin, undetermined_share as undetermined and the rest as independent journalism; controls are coded
    independent journalism."""
    key = json.loads(key_path.read_text())
    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    ud = [r for r in rows if key["items"][r["sample_id"]]["role"] == "undetermined"]
    coded = len(ud) if coded is None else coded
    yes = int(round(confirm_share * coded))
    unsure = int(round(undetermined_share * coded))
    for i, r in enumerate(ud[:coded]):
        r["human_category"] = ("state_origin" if i < yes else "undetermined" if i < yes + unsure
                               else "independent_journalism")
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
    assert csv_path.name == "undetermined_a_sample_2026-10-22.csv" and key_path.name == "undetermined_a_sample_2026-10-22.key.json"
    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    header = list(rows[0])
    assert header == model_only.FIELDS
    assert not {"category", "route", "article_id", "role", "method"} & set(header), "the coder never sees the label"
    key = json.loads(key_path.read_text())
    roles = [key["items"][r["sample_id"]]["role"] for r in rows]
    assert roles.count("undetermined") == 60 and roles.count("control") == 20
    assert key["prompt_version"] == classify_llm.PROMPT_VERSION
    # proportional by language: Italy's 5 of 131 undetermined labels get a few rows at most
    assert key["languages"]["en"]["sampled"] + key["languages"]["it"]["sampled"] == 60
    assert key["languages"]["it"]["sampled"] <= 3
    assert all(v["category"] in ("C", "not_relevant") for v in key["items"].values() if v["role"] == "control")
    again, _ = model_only.write_sample(big, n=60, controls=20, seed="t", day="2026-10-22")
    assert again.name == "undetermined_a_sample_2026-10-22_2.csv"
    assert again.read_text() == csv_path.read_text()


def test_the_check_reports_and_decides_nothing(big, reviews):
    a = model_only.assess()
    assert a["check"]["state"] == "none" and a["summary"] == "no hand check yet"
    csv_path, key_path = model_only.write_sample(big, n=110, controls=20, seed="g", day="2026-10-22")
    _code(csv_path, key_path, 0.5, coded=60, undetermined_share=0.2)
    a = model_only.assess()
    assert a["check"]["state"] == "under_way" and a["check"]["coded"] == 60
    assert a["check"]["confirmed"] == 30 and a["check"]["undetermined"] == 12 and a["check"]["not_state"] == 18
    _code(csv_path, key_path, 1.0)
    a = model_only.assess()
    assert a["check"]["state"] == "reported" and a["check"]["coded"] == 110 and a["check"]["confirmed_share"] == 1.0
    assert a["check"]["could_be_state_share"] == 1.0 and a["check"]["controls_coded"] == 20
    # every coded row confirmed as state origin, and still nothing rejoins
    latest, _, _, meta = _export(big)
    sgp = latest["countries"]["SGP"]["all_time"]
    assert sgp["U"] == 6 + 120 and sgp["A"] == 1
    assert meta["undetermined"]["check"]["confirmed_share"] == 1.0
    assert meta["undetermined"]["rejoins_state_origin"] is False
    assert meta["findings"]["top_country"]["iso"] == "ITA"
    assert "settled 110 as state origin" in meta["undetermined"]["summary"]


def test_a_sample_from_another_prompt_is_stale(big, reviews, monkeypatch):
    csv_path, key_path = model_only.write_sample(big, n=110, controls=10, seed="s", day="2026-10-22")
    _code(csv_path, key_path, 1.0)
    monkeypatch.setattr(classify_llm, "PROMPT_VERSION", "2099-01-01")
    assert model_only.assess()["check"]["state"] == "stale"


def test_an_unreadable_sample_is_reported_not_raised(reviews):
    (reviews / "undetermined_a_sample_2026-10-22.csv").write_text("sample_id\n")
    (reviews / "undetermined_a_sample_2026-10-22.key.json").write_text("{not json")
    assert model_only.assess()["check"]["state"] == "error"


def test_methodology_defines_undetermined_as_the_paper_does(conn, tmp_path):
    latest, _, _, meta = _export(conn)
    path = tmp_path / "M.md"
    methodology.write(meta, latest, path)
    text = path.read_text(encoding="utf-8")
    assert "Undetermined items. As in the original study, an item whose authorship or commissioning cannot be settled" in text
    assert "excluded from every category count" in text and "never rejoin state origin" in text
    assert "sponsored disclosure calls that the rules did not confirm" in text
    assert "kept blind to outlet, country and address until after 23 October 2026" in text
    assert "At export time there were 11 undetermined placements (8 with no marker, 3 sponsored disclosures" in text
    assert "A further 11 model judgements of state origin" in text and "undetermined, excluded from every count" in text
    for gone in ("rejoin state origin once", "90 percent of at least", "withheld if fewer", "provisional, no hand check"):
        assert gone not in text, gone
    assert "—" not in text and "–" not in text


def test_the_methodology_text_reports_the_check_as_information():
    t = model_only.methodology_text({"count": 10, "by_route": {"unattributed": 7, "sponsored_disclosure": 3},
                                     "check": {"coded": 100, "confirmed": 60, "undetermined": 25, "not_state": 15,
                                               "confirmed_share": 0.6, "could_be_state_share": 0.85}})
    assert "settled 60 as state origin (60 percent), left 25 undetermined and placed 15 elsewhere; 85 percent could be state" in t
    assert "moves nothing into a count" in t
    old = methodology.undetermined_text({"route_totals": {"unattributed": 1123}})
    assert "1,123 undetermined placements" in old
    for s in (t, old):
        assert "—" not in s and "–" not in s
