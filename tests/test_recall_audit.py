import csv
import json

import pytest

from pipeline import methodology, recall_audit, store


def _outlet(conn, oid, tier="national_daily", lang="en"):
    conn.execute("INSERT INTO outlets(id, name, country, language, tier, active, feeds, updated_at) VALUES (?,?,?,?,?,1,'[]','2026-10-12')",
                 (oid, oid, "SGP", lang, tier))


def _article(conn, aid, outlet, title, category="C", method="rules", lang="en", summary="", trigger=None):
    conn.execute(
        """INSERT INTO articles(id, url, url_hash, outlet_id, country, language, title, summary, discovered_at, status, gate_relevant, llm_trigger)
           VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
        (aid, "https://%s.example/%d" % (outlet, aid), "h%d" % aid, outlet, "SGP", lang, title, summary,
         "2026-10-01T00:00:00+00:00", "classified", trigger))
    if category:
        store.insert_classification(conn, aid, method, category, 0.75, ruleset_version="2026.09.9")


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "r.db")
    for o, tier in (("sg_paper", "national_daily"), ("sg_other", "national_daily"), ("xx_agency", "agency"), ("it_wire", "agency")):
        _outlet(c, o, tier)
    # A state origin item carried with its credit by a wire, and the same headline uncredited elsewhere.
    _article(c, 1, "it_wire", "China opens the longest sea bridge in the world to traffic today", category="A")
    _article(c, 2, "sg_paper", "China opens the longest sea bridge in the world to traffic today")
    for i in range(3, 40):
        _article(c, i, "sg_paper", "Local story number %d about something else entirely" % i, category="C" if i % 2 else "not_relevant")
    for i in range(40, 60):
        _article(c, i, "xx_agency", "Agency item %d" % i)
    for i in range(60, 70):
        _article(c, i, "sg_other", "Gazeta %d" % i, lang="pt")
    # Never part of the population: routed to the model once, labelled by the model, or state origin by rules.
    _article(c, 70, "sg_paper", "Routed once", trigger=json.dumps({"triggers": ["cites_xinhua"]}))
    _article(c, 71, "sg_paper", "Model label", method="llm", category="B")
    _article(c, 72, "sg_paper", "Rules A", category="A")
    c.commit()
    return c


def test_population_is_rules_c_and_not_relevant_never_sent_to_the_model(conn):
    ids = {r["article_id"] for r in recall_audit.population(conn)}
    assert 2 in ids and 3 in ids and 4 in ids and 45 in ids
    assert not ids & {1, 70, 71, 72}


def test_an_uncredited_copy_of_a_state_origin_headline_is_a_lookalike(conn):
    rows = recall_audit.population(conn)
    looks = recall_audit.lookalikes(rows, recall_audit.state_origin_items(conn))
    assert set(looks) == {2}
    assert looks[2]["by"] == "title" and looks[2]["match_article_id"] == 1 and looks[2]["match_outlet_id"] == "it_wire"


def test_a_summary_that_repeats_a_state_origin_summary_is_a_lookalike():
    text = " ".join("word%d" % i for i in range(40))
    rows = [{"article_id": 2, "language": "fr", "title": "Un titre tout a fait different", "summary": text, "url_hash": "b"}]
    refs = [{"article_id": 1, "language": "en", "title": "Other", "summary": text, "url_hash": "a", "outlet_id": "w", "route": "wire_credit"}]
    assert recall_audit.lookalikes(rows, refs)[2]["by"] == "summary"
    # An item is never its own lookalike.
    assert recall_audit.lookalikes([dict(refs[0])], refs) == {}


def test_wire_habit_outlets_include_named_agencies_and_lookalike_carriers(conn):
    rows = recall_audit.population(conn)
    habit = recall_audit.wire_habit_outlets(conn, rows, {})
    assert habit["sg_asiaone"] == "named" and habit["xx_agency"] == "agency"
    assert "sg_paper" not in habit
    looks = {r["article_id"]: {} for r in rows if r["outlet_id"] == "sg_other"}
    assert recall_audit.wire_habit_outlets(conn, rows, looks)["sg_other"] == "lookalikes"


def test_allocate_is_proportional_with_a_floor_and_never_exceeds_a_stratum():
    a = recall_audit.allocate({"en": 9000, "ko": 800, "hr": 250, "mk": 20}, 100, floor=3, floor_min_pop=200)
    assert sum(a.values()) == 100
    assert a["hr"] >= 3 and a["en"] > a["ko"] > a["hr"]
    assert recall_audit.allocate({"en": 5, "pt": 2}, 50) == {"en": 5, "pt": 2}
    b = recall_audit.allocate({"en": 1000, "pt": 4}, 300, floor=10, floor_min_pop=1)
    assert b["pt"] == 4 and sum(b.values()) == 300


def test_split_language_oversamples_the_habit_stratum():
    h, o = recall_audit.split_language(20, 100, 900, oversample=3.0)
    assert h + o == 20 and h > 20 * 100 / 1000.0
    assert recall_audit.split_language(5, 0, 50) == (0, 5)
    assert recall_audit.split_language(5, 2, 50)[0] <= 2


def test_sample_writes_a_blind_csv_and_a_key_and_is_reproducible(conn, tmp_path):
    out = tmp_path / "audit.csv"
    key = recall_audit.cmd_sample(conn, n=30, seed=7, out=out)
    with open(out, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 30 == key["n"]
    assert list(rows[0].keys()) == ["sample_id", "url", "outlet", "language", "title", "human_category", "note"]
    assert all(r["human_category"] == "" and r["note"] == "" for r in rows)
    flat = open(out, encoding="utf-8").read()
    assert "not_relevant" not in flat and "rules" not in flat
    assert {i["rule_category"] for i in key["items"]} <= {"C", "not_relevant"}
    # The lookalike is in its own stratum and drawn; strata sizes add up to the population.
    assert key["strata"]["en|lookalike"] == {"population": 1, "drawn": 1}
    assert sum(s["population"] for s in key["strata"].values()) == key["population"]
    again = recall_audit.cmd_sample(conn, n=30, seed=7, out=tmp_path / "again.csv")
    assert [i["article_id"] for i in again["items"]] == [i["article_id"] for i in key["items"]]


def _fill(path, labels):
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["human_category"] = labels(r)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def test_compute_weights_back_to_the_population_and_records_the_audit(conn, tmp_path):
    out = tmp_path / "audit.csv"
    recall_audit.cmd_sample(conn, n=30, seed=7, out=out)
    _fill(out, lambda r: "unchecked state sourcing" if r["outlet"] == "xx_agency" else "independent journalism")
    res = recall_audit.cmd_compute(conn, out)
    ov = res["overall"]
    with open(out, encoding="utf-8") as fh:
        agency_drawn = sum(1 for r in csv.DictReader(fh) if r["outlet"] == "xx_agency")
    assert ov["n_coded"] == 30 and ov["n_missed"] == agency_drawn > 0
    # Every agency item is a miss, so the weighted rate is the agency share of the population, whatever
    # the oversampling did to its share of the sample.
    pop = recall_audit.population(conn)
    share = sum(1 for r in pop if r["outlet_id"] == "xx_agency") / float(len(pop))
    assert abs(ov["rate"] - share) < 1e-9
    assert ov["ci_low"] < ov["rate"] < ov["ci_high"]
    assert res["by_language"]["pt"]["n_missed"] == 0
    got = recall_audit.latest(conn)
    assert got["n_coded"] == 30 and abs(got["rate"] - share) < 1e-9 and "en" in got["by_language"]


def test_blank_rows_are_skipped_and_unknown_labels_refused(conn, tmp_path):
    out = tmp_path / "audit.csv"
    recall_audit.cmd_sample(conn, n=10, seed=1, out=out)
    _fill(out, lambda r: "C" if int(r["sample_id"]) <= 4 else "")
    key = json.load(open(recall_audit.key_path(out), encoding="utf-8"))
    assert len(recall_audit.read_coded(out, key)) == 4
    _fill(out, lambda r: "maybe")
    with pytest.raises(ValueError):
        recall_audit.read_coded(out, key)


def test_category_aliases():
    n = recall_audit.normalise_category
    assert n("State origin") == "A" and n("b") == "B" and n("Independent journalism") == "C"
    assert n("not about China") == "not_relevant" and n("not_relevant") == "not_relevant" and n(" ") is None


def test_wilson_interval_is_not_degenerate_at_zero():
    lo, hi = recall_audit.wilson(0.0, 100)
    assert lo == 0.0 and 0.0 < hi < 0.05


def test_latest_is_none_before_any_audit(conn):
    assert recall_audit.latest(conn) is None


def test_methodology_says_the_gate_cannot_see_uncredited_relay():
    text = methodology.candidate_recall_text({})
    assert "cannot see uncredited relay" in text and "not yet measured" in text
    assert "{candidate_recall_text}" in methodology.TEMPLATE
    done = methodology.candidate_recall_text({"candidate_recall": {
        "computed_at": "2026-10-20T00:00:00+00:00", "n_coded": 300, "population": 69504, "n_missed": 12,
        "rate": 0.031, "ci_low": 0.015, "ci_high": 0.06, "by_language": {"en": {"n": 82, "missed": 5, "rate": 0.05}}}})
    assert "3.1 percent" in done and "1.5 to 6.0" in done and "en 5.0 percent (5 of 82)" in done
    assert "not yet measured" not in done
    for t in (text, done):
        assert "\u2014" not in t and "\u2013" not in t and "Category" not in t
