"""Syndication deals (pipeline/syndication.py): the entries file and its validation, the comparison
window, the Newcombe interval, the confounders, the off state, the export and the hand check."""
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from pipeline import config, measures, store
from pipeline import syndication as syn

ROOT = Path(__file__).resolve().parent.parent
OUTLETS = [
    {"id": "it_alpha", "name": "Alpha", "country": "ITA", "language": "it", "active": True, "tier": "national"},
    {"id": "gb_beta", "name": "Beta", "country": "GBR", "language": "en", "active": True, "tier": "national",
     "collector": "self_hosted"},
]
OUTLET_IDS = {o["id"] for o in OUTLETS}


def entry(**kw):
    e = {"id": "it_alpha_xinhua_2026", "outlet_id": "it_alpha", "partner": "xinhua", "kind": "mou",
         "signed_on": "2026-11-02", "precision": "day", "source_url": "https://example.org/deal",
         "source_title": "Alpha and Xinhua sign", "source_publisher": "Alpha", "source_kind": "primary",
         "accessed_on": "2026-12-20", "confidence": "stated", "notes": "Signed in Rome."}
    e.update(kw)
    return {k: v for k, v in e.items() if v is not None}


def errors_of(*entries):
    return syn.validate({"entries": list(entries)}, OUTLET_IDS)[1]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_a_good_entry_and_an_unmonitored_one_pass():
    good, errs = syn.validate({"entries": [entry(), entry(id="ke_star", outlet_id=None, unmonitored=True,
                                                         outlet_name="The Star (Kenya)", partner="china_daily",
                                                         kind="supplement")]}, OUTLET_IDS)
    assert errs == [] and len(good) == 2
    assert len(syn.monitored(good)) == 1


@pytest.mark.parametrize("kind", ["content_exchange", "news_exchange", "mou", "cooperation_agreement", "supplement",
                                  "paid_placement", "joint_venture", "training_partnership", "other"])
def test_every_kind_the_research_uses_is_accepted(kind):
    assert errors_of(entry(kind=kind)) == []


@pytest.mark.parametrize("bad, words", [
    (dict(outlet_id="it_nowhere"), "not in sources/outlets.yaml"),
    (dict(outlet_id=None), "needs outlet_id"),
    (dict(unmonitored=True), "names outlet_name, not outlet_id"),
    (dict(outlet_id=None, unmonitored=True), "needs outlet_name"),
    (dict(precision="month"), "does not match signed_on"),
    (dict(signed_on="2026-11", precision="day"), "does not match signed_on"),
    (dict(signed_on="2019", precision="month"), "does not match signed_on"),
    (dict(signed_on="2026-02-30"), "not a real date"),
    (dict(signed_on="2026-13", precision="month"), "not a real date"),
    (dict(signed_on="02/11/2026"), "does not match"),
    (dict(source_url=None), "source_url"),
    (dict(source_url="http://example.org/deal"), "source_url"),
    (dict(partner="tass"), "partner"),
    (dict(kind="supplement_insert"), "kind"),
    (dict(source_kind="blog"), "source_kind"),
    (dict(confidence="likely"), "confidence"),
    (dict(extra_field="x"), "extra_field"),
    (dict(ended_on="2026-10"), "ended_on 2026-10 comes before signed_on"),
    (dict(accessed_on="2026-10-01"), "comes after accessed_on"),
])
def test_each_rule_names_the_entry_and_the_problem(bad, words):
    errs = errors_of(entry(**bad))
    assert errs, bad
    assert any(words in e for e in errs), errs
    assert all(e.startswith("it_alpha_xinhua_2026:") for e in errs)


def test_duplicates_are_refused_and_only_the_later_one_dropped():
    first = entry()
    same_key = entry(id="other_id")
    good, errs = syn.validate({"entries": [first, same_key]}, OUTLET_IDS)
    assert [e["id"] for e in good] == ["it_alpha_xinhua_2026"]
    assert errs == ["other_id: duplicate of it_alpha_xinhua_2026: same outlet, partner, kind and signed_on"]
    good, errs = syn.validate({"entries": [first, entry(kind="content_exchange")]}, OUTLET_IDS)
    assert len(good) == 1 and "duplicate id" in errs[0]
    # Another kind, partner or date is a separate deal.
    assert errors_of(first, entry(id="b", kind="content_exchange"), entry(id="c", partner="cmg"),
                     entry(id="d", signed_on="2026-11-03")) == []
    # Unmonitored outlets are keyed by name, ignoring case and spacing.
    u = dict(outlet_id=None, unmonitored=True, outlet_name="The  Star")
    assert len(errors_of(entry(id="u1", **u), entry(id="u2", **dict(u, outlet_name="the star")))) == 1


def test_the_file_shape_and_yaml_dates(tmp_path):
    assert syn.validate([], OUTLET_IDS)[1] == ["the file needs a top level entries: list"]
    p = tmp_path / "s.yaml"
    p.write_text("entries:\n  - id: it_a\n    outlet_id: it_alpha\n    partner: cmg\n    kind: mou\n"
                 "    signed_on: 2025-05-28\n    precision: day\n    source_url: https://example.org/x\n"
                 "    source_title: T\n    source_publisher: P\n    source_kind: primary\n    accessed_on: 2026-10-13\n"
                 "    confidence: stated\n", encoding="utf-8")
    good, errs = syn.validate_file(p, OUTLETS)
    assert errs == [] and good[0]["signed_on"] == "2025-05-28"
    assert syn.validate_file(tmp_path / "missing.yaml", OUTLETS) == ([], [])


def test_the_committed_file_is_valid_and_the_measure_is_on():
    """D7 (2026-10-28): the measure is switched on, and runs while at least 10 sourced, dated entries
    name a monitored outlet."""
    good, errs = syn.validate_file()
    assert errs == []
    assert isinstance(yaml.safe_load((ROOT / "sources" / "syndication.yaml").read_text(encoding="utf-8"))["entries"], list)
    spec = next(m for m in measures.load_config() if m["id"] == "syndication")
    assert spec["enabled"] is True and spec["min_inputs"] == 10 and spec["module"] == "pipeline.syndication"
    assert len(syn.monitored(good)) >= spec["min_inputs"]
    assert all(e.get("source_url") and e.get("signed_on") for e in good)


def test_every_committed_entry_carries_its_source_check():
    """Each of the 51 entries was re-read on 2026-10-28: a verification block with its status, the date the
    page gives (supporting signed_on at its precision) and a note; a moved source keeps its old URL."""
    raw = syn.load_file()["entries"]
    good, errs = syn.validate_file()
    assert errs == [] and len(raw) == len(good) == 51
    for e in raw:
        v = e.get("verification")
        assert isinstance(v, dict), e["id"]
        assert v["status"] in syn.VERIFICATION_STATUSES and v["checked_on"] == syn.VERIFIED_ON and v["note"].strip(), e["id"]
        if v["status"] != "unverified":
            assert syn.date_agrees(v["page_date"], e["signed_on"]), e["id"]
        if v["status"] == "source_moved":
            assert v["previous_url"].startswith("https://") and v["previous_url"] != e["source_url"], e["id"]
        assert "\u2014" not in v["note"] and "\u2013" not in v["note"], e["id"]
    counts = syn.verification_counts(good)
    assert counts == {"verified": 50, "source_moved": 1, "unverified": 0, "unchecked": 0}
    by_id = {e["id"]: e for e in raw}
    iol = by_id["za_iol_cctv_2014"]
    assert iol["partner"] == "cmg" and iol["signed_on"] == "2014-12" and iol["verification"]["status"] == "verified"
    moved = by_id["br_agenciabrasil_cmg_2019"]
    assert moved["verification"]["status"] == "source_moved" and "agenciabrasil.ebc.com.br" in moved["verification"]["previous_url"]
    # the page gives no day for the Mehr signing, so the precision is the month
    assert by_id["ir_mehr_xinhua_2024"]["signed_on"] == "2024-10" and by_id["ir_mehr_xinhua_2024"]["precision"] == "month"


@pytest.mark.parametrize("v, words", [
    ({"status": "source_moved", "checked_on": "2026-10-28", "note": "Moved."}, "previous_url"),
    ({"status": "source_moved", "checked_on": "2026-10-28", "note": "Moved.", "previous_url": "https://example.org/deal"},
     "same as source_url"),
    ({"status": "verified", "checked_on": "2026-10-28", "note": "Yes.", "page_date": "2026-11"}, "does not support signed_on"),
    ({"status": "verified", "checked_on": "2026-10-28", "note": "Yes.", "page_date": "2026-11-03"}, "does not support signed_on"),
    ({"status": "verified", "checked_on": "2026-02-30", "note": "Yes."}, "not a real date"),
    ({"status": "checked", "checked_on": "2026-10-28", "note": "Yes."}, "status"),
    ({"status": "verified", "checked_on": "2026-10-28"}, "note"),
    ({"status": "verified", "checked_on": "2026-10-28", "note": "Yes.", "extra": 1}, "extra"),
])
def test_the_verification_rules(v, words):
    errs = errors_of(entry(verification=v))
    assert errs and any(words in m for m in errs), errs


def test_a_good_verification_passes_and_an_unverified_entry_stays():
    ok = {"status": "verified", "checked_on": "2026-10-28", "page_date": "2026-11-02", "note": "The page states it."}
    un = {"status": "unverified", "checked_on": "2026-10-28", "note": "No page could be read."}
    moved = {"status": "source_moved", "checked_on": "2026-10-28", "page_date": "2026-11-02", "note": "Moved.",
             "previous_url": "https://example.org/old"}
    good, errs = syn.validate({"entries": [entry(verification=ok), entry(id="u", partner="cgtn", verification=un),
                                           entry(id="m", partner="cmg", verification=moved)]}, OUTLET_IDS)
    assert errs == [] and len(good) == 3
    assert syn.verification_counts(good + [entry(id="n")]) == {"verified": 1, "source_moved": 1, "unverified": 1, "unchecked": 1}


def test_the_command_line_validates_a_file(tmp_path, capsys):
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump({"entries": [entry(outlet_id="it_ansa"), entry(id="x", outlet_id="it_nowhere")]}), encoding="utf-8")
    assert syn.main(["validate", str(p)]) == 1
    out = capsys.readouterr().out
    assert "x: outlet_id it_nowhere is not in sources/outlets.yaml" in out and "1 errors" in out
    p.write_text(yaml.safe_dump({"entries": [entry(outlet_id="it_ansa")]}), encoding="utf-8")
    assert syn.main(["validate", str(p)]) == 0


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

LAST = dt.date(2026, 12, 30)          # last complete day; the eligible range is 2026-10-01 to 2026-12-02


def reason(**kw):
    return syn.window(entry(**kw), LAST, "2026-09-03")["reason"]


def test_the_eligible_range_and_its_edges():
    lo, hi = syn.eligible_range(LAST, "2026-09-03")
    assert (lo.isoformat(), hi.isoformat()) == ("2026-10-01", "2026-12-02")
    assert reason(signed_on="2026-10-01") == "eligible" and reason(signed_on="2026-09-30") == "too_early"
    assert reason(signed_on="2026-12-02") == "eligible" and reason(signed_on="2026-12-03") == "too_recent"
    w = syn.window(entry(signed_on="2026-10-01"), LAST, "2026-09-03")
    assert w["before"] == ["2026-09-03", "2026-09-30"] and w["after"] == ["2026-10-02", "2026-10-29"]
    w = syn.window(entry(signed_on="2026-12-02"), LAST, "2026-09-03")
    assert w["after"] == ["2026-12-03", "2026-12-30"]


def test_month_precision_uses_the_whole_month_and_year_precision_is_never_compared():
    assert reason(signed_on="2026-10", precision="month") == "eligible"
    w = syn.window(entry(signed_on="2026-10", precision="month"), LAST, "2026-09-03")
    assert w["before"] == ["2026-09-03", "2026-09-30"] and w["after"] == ["2026-11-01", "2026-11-28"]
    assert reason(signed_on="2026-12", precision="month") == "too_recent"
    assert reason(signed_on="2026-09", precision="month") == "too_early"
    assert reason(signed_on="2026", precision="year") == "imprecise"


def test_deals_before_collection_and_unmonitored_outlets():
    assert reason(signed_on="2019-03-22") == "predates_collection"
    assert syn.predates_collection(entry(signed_on="2026-09-02"), "2026-09-03")
    assert not syn.predates_collection(entry(signed_on="2026-09", precision="month"), "2026-09-03")
    assert reason(outlet_id=None, unmonitored=True, outlet_name="Z", signed_on="2026-11-02") == "unmonitored"


# ---------------------------------------------------------------------------
# Intervals
# ---------------------------------------------------------------------------

def test_wilson_and_newcombe_against_published_values():
    # Newcombe (1998), Statistics in Medicine 17: 81/263 gives 0.2553 to 0.3662, and the difference
    # 56/70 minus 48/80 gives 0.0524 to 0.3339 by the hybrid score method (method 10).
    lo, hi = syn.wilson(81, 263)
    assert round(lo, 4) == 0.2553 and round(hi, 4) == 0.3662
    d, lo, hi = syn.newcombe(56, 70, 48, 80)
    assert round(d, 4) == 0.2 and round(lo, 4) == 0.0524 and round(hi, 4) == 0.3339
    assert syn.wilson(0, 0) is None and syn.newcombe(1, 0, 1, 10) is None
    lo, hi = syn.wilson(0, 50)
    assert abs(lo) < 1e-12 and 0 < hi < 0.1


# ---------------------------------------------------------------------------
# Data: labels, discovery and polls in a fresh database
# ---------------------------------------------------------------------------

def _add(conn, outlet_id, day, cat, n, country="ITA"):
    for i in range(n):
        url = "https://x.example/%s/%s/%s/%d" % (outlet_id, day, cat, i)
        cur = conn.execute(
            """INSERT INTO articles(url, url_hash, outlet_id, country, language, discovered_at, status, gate_relevant)
               VALUES (?,?,?,?,?,?,?,1)""",
            (url, hashlib.sha1(url.encode()).hexdigest(), outlet_id, country, "it", day + "T10:00:00+00:00", "classified"))
        conn.execute(
            """INSERT INTO classifications(article_id, method, category, confidence, ruleset_version, classified_at, is_current)
               VALUES (?,?,?,?,?,?,1)""", (cur.lastrowid, "rules", cat, 1.0, config.RULESET_VERSION, day))


def _days(first, last):
    d, out = dt.date.fromisoformat(first), []
    while d <= dt.date.fromisoformat(last):
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


@pytest.fixture
def db(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    # Before 2026-11-02: 10 state origin of 100 China; after: 30 of 120. Discovery every day except
    # three after, and saturated polls after.
    _add(conn, "it_alpha", "2026-10-10", "A", 10)
    _add(conn, "it_alpha", "2026-10-11", "B", 20)
    _add(conn, "it_alpha", "2026-10-12", "C", 70)
    _add(conn, "it_alpha", "2026-10-12", "not_relevant", 40)
    _add(conn, "it_alpha", "2026-11-02", "A", 99)                 # the signing day is on neither side
    _add(conn, "it_alpha", "2026-11-10", "A", 30)
    _add(conn, "it_alpha", "2026-11-11", "C", 90)
    _add(conn, "it_alpha", "2026-12-15", "A", 50)                 # outside the after window
    for d in _days("2026-10-05", "2026-11-30"):
        if d not in ("2026-11-20", "2026-11-21", "2026-11-22"):
            conn.execute("INSERT INTO daily_outlet_discovery(date, outlet_id, country, discovered) VALUES (?,?,?,5)",
                         (d, "it_alpha", "ITA"))
        conn.execute("""INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, saturated)
                        VALUES (?,?,?,?,?,?)""", (d, "https://x.example/feed", "it_alpha", "ITA", 10,
                                                  3 if d > "2026-11-02" else 0))
    conn.commit()
    yield conn
    conn.close()


def test_side_counts_use_current_china_labels_by_day(db):
    assert syn.side_counts(db, "it_alpha", "2026-10-05", "2026-11-01") == {
        "start": "2026-10-05", "end": "2026-11-01", "state_origin": 10, "china": 100}
    assert syn.side_counts(db, "it_alpha", "2026-11-03", "2026-11-30")["china"] == 120


def test_side_checks_and_confounders(db):
    out = syn.side_checks(db, OUTLETS[0], "2026-11-03", "2026-11-30", set())
    assert out == {"relay_incomplete_share": None, "zero_discovery_days": 3, "saturation_share": 0.3, "polls": 280}
    before = syn.side_checks(db, OUTLETS[0], "2026-10-05", "2026-11-01", {"2026-10-06"})
    assert before["relay_incomplete_share"] is None and before["zero_discovery_days"] == 0 and before["saturation_share"] == 0.0
    # Only an outlet the owner's machine alone collects is exposed to an incomplete relay day.
    relay = syn.side_checks(db, OUTLETS[1], "2026-10-05", "2026-11-01", {"2026-10-06", "2026-10-07", "2026-10-08"})
    assert relay["relay_incomplete_share"] == round(3 / 28, 4) and relay["zero_discovery_days"] == 28
    assert relay["saturation_share"] is None
    changes = {"ruleset": [{"version": "r1", "date": "2026-10-20"}, {"version": "r0", "date": "2026-09-01"}],
               "gate": [{"version": "g1", "date": "2026-11-30"}]}
    conf = syn.confounders({"before": relay, "after": out}, "2026-10-05", "2026-11-30", changes)
    types = sorted((c["type"], c.get("side", "")) for c in conf)
    assert types == [("gate_change", ""), ("relay_incomplete", "before"), ("ruleset_change", ""),
                     ("saturation", "after"), ("zero_discovery", "after"), ("zero_discovery", "before")]
    clean = {"relay_incomplete_share": 0.05, "zero_discovery_days": 2, "saturation_share": 0.09, "polls": 10}
    assert syn.confounders({"before": clean, "after": clean}, "2026-10-05", "2026-11-30", {}) == []
    assert all("\u2014" not in c["text"] and "\u2013" not in c["text"] for c in conf)


def _install_file(monkeypatch, tmp_path, entries):
    p = tmp_path / "syndication.yaml"
    p.write_text(yaml.safe_dump({"entries": entries}, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(syn, "SYNDICATION_PATH", p)
    monkeypatch.setattr(syn, "_changes", lambda: {"ruleset": [], "gate": [{"version": "g9", "date": "2026-11-20"}]})
    return p


ENTRIES = [entry(),
           entry(id="it_alpha_cmg_2019", partner="cmg", kind="content_exchange", signed_on="2019-03", precision="month",
                 ended_on="2022-06", confidence="reported", source_kind="ngo_report"),
           entry(id="ke_star", outlet_id=None, unmonitored=True, outlet_name="The Star (Kenya)", partner="china_daily",
                 kind="supplement", signed_on="2025-07-16"),
           entry(id="it_alpha_thin", partner="cgtn", signed_on="2026-10-03", kind="training_partnership")]


def test_the_export_shape(db, tmp_path, monkeypatch):
    _install_file(monkeypatch, tmp_path, ENTRIES)
    ctx = {"outlets": OUTLETS, "today": "2026-12-31"}
    out = syn.build_export(db, ctx)
    assert set(out) == {"measure", "version", "collection_start", "last_complete_day", "window_days", "min_china_per_side",
                        "eligible_from", "eligible_until", "counts", "verification", "timeline", "comparisons"}
    assert out["verification"] == {"checked_on": "2026-10-28", "wording": "sources verified on 2026-10-28, not hand coded",
                                   "hand_coded": False}
    assert out["last_complete_day"] == "2026-12-30" and out["eligible_from"] == "2026-10-01"
    assert [t["id"] for t in out["timeline"]] == ["it_alpha_xinhua_2026", "it_alpha_thin", "ke_star", "it_alpha_cmg_2019"]
    t = {x["id"]: x for x in out["timeline"]}
    assert t["it_alpha_cmg_2019"]["predates_collection"] and t["it_alpha_cmg_2019"]["comparison"] == "predates_collection"
    assert t["ke_star"]["monitored"] is False and t["ke_star"]["outlet_name"] == "The Star (Kenya)" and t["ke_star"]["country"] is None
    assert t["it_alpha_xinhua_2026"]["links"] == [{"label": "Alpha and Xinhua sign", "publisher": "Alpha", "url": "https://example.org/deal"}]
    assert t["it_alpha_xinhua_2026"]["partner_label"] == "Xinhua" and t["it_alpha_xinhua_2026"]["country"] == "ITA"
    assert out["counts"] == {"entries": 4, "monitored": 3, "unmonitored": 1, "predates_collection": 2, "eligible": 2,
                             "compared": 1, "confounded": 1, "invalid": 0,
                             "verified": 0, "source_moved": 0, "unverified": 0, "unchecked": 4}
    assert all(x["verification"]["status"] == "unchecked" for x in out["timeline"])
    c = {x["entry_id"]: x for x in out["comparisons"]}
    full = c["it_alpha_xinhua_2026"]
    assert full["before"]["state_origin"] == 10 and full["before"]["china"] == 100 and full["before"]["share"] == 0.1
    assert full["after"]["state_origin"] == 30 and full["after"]["china"] == 120 and full["after"]["share"] == 0.25
    d, lo, hi = syn.newcombe(30, 120, 10, 100)
    assert full["difference"] == round(d, 4) and full["difference_ci"] == [round(lo, 4), round(hi, 4)]
    assert full["confounded"] and {x["type"] for x in full["confounders"]} == {"gate_change", "saturation", "zero_discovery"}
    thin = c["it_alpha_thin"]
    assert not thin["sufficient"] and thin["difference"] is None and thin["before"]["share"] is None
    assert "fewer than 50 China articles" in thin["reason"]
    text = json.dumps(out)
    assert "\u2014" not in text and "\u2013" not in text
    assert syn.meta_summary(db, ctx) == out["counts"]
    assert syn.meta_summary(db, {"outlets": OUTLETS, "today": "2026-12-31"}) == out["counts"]


def test_the_export_carries_each_entry_s_source_check(db, tmp_path, monkeypatch):
    arch = {"status": "verified", "checked_on": "2026-10-28", "page_date": "2026-11-02", "note": "Read from an archived copy.",
            "archived_url": "https://web.archive.org/web/2026/https://example.org/deal"}
    _install_file(monkeypatch, tmp_path, [entry(verification=arch)])
    out = syn.build_export(db, {"outlets": OUTLETS, "today": "2026-12-31"})
    row = out["timeline"][0]
    assert row["verification"]["status"] == "verified" and row["verification"]["page_date"] == "2026-11-02"
    assert row["links"][1] == {"label": "archived copy", "publisher": "Internet Archive", "url": arch["archived_url"]}
    assert out["counts"]["verified"] == 1 and out["counts"]["unchecked"] == 0


def test_inputs_present_counts_valid_monitored_entries(tmp_path, monkeypatch):
    bad = entry(id="bad", outlet_id="it_nowhere")
    _install_file(monkeypatch, tmp_path, ENTRIES + [bad])
    monkeypatch.setattr(syn.registry, "load_outlets", lambda *a, **k: OUTLETS)
    assert syn.inputs_present(None) == 3


def test_off_and_below_min_inputs_leave_no_side_file(db, tmp_path, monkeypatch):
    _install_file(monkeypatch, tmp_path, ENTRIES)
    monkeypatch.setattr(syn.registry, "load_outlets", lambda *a, **k: OUTLETS)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "syndication.json").write_text("{}", encoding="utf-8")
    cfg = tmp_path / "measures.yaml"

    def run(enabled, min_inputs):
        cfg.write_text(yaml.safe_dump([{"id": "syndication", "label": "Syndication deals", "module": "pipeline.syndication",
                                        "enabled": enabled, "min_inputs": min_inputs}]), encoding="utf-8")
        written = {}
        blocks = measures.run_all_quietly(db, out_dir, {"outlets": OUTLETS, "today": "2026-12-31"},
                                          write_json=lambda p, d: written.update({Path(p).name: d}),
                                          config_path=cfg, reviews_dir=tmp_path / "reviews")
        return blocks["syndication"], written

    b, written = run(False, 10)
    assert b["status"] == "off" and "switched off" in b["reason"] and not written
    assert not (out_dir / "syndication.json").exists()
    b, written = run(True, 10)
    assert b["status"] == "off" and b["reason"] == "waiting for inputs: 3 of at least 10" and not written
    b, written = run(True, 3)
    assert b["status"] == "provisional" and "error" not in b
    assert set(written) == {"syndication.json"} and len(written["syndication.json"]["timeline"]) == 4
    assert "timing, not cause" in b["method_text"] and b["summary"]["entries"] == 4


def test_methodology_text():
    t = syn.methodology_text({"status": "provisional", "summary": {"entries": 51, "predates_collection": 47}})
    assert "timing, not cause" in t and "3 September 2026" in t and "(47 of the 51 entries now listed)" in t
    assert "\u2014" not in t and "\u2013" not in t and "Category" not in t and " - " not in t
    assert len([p for p in t.split("\n\n") if p.strip()]) == 3
    assert "sources verified on 2026-10-28, not hand coded" in t and "Most of these deals were signed before collection began" in t
    assert "3 September 2026" in syn.methodology_text({})


# ---------------------------------------------------------------------------
# Hand check
# ---------------------------------------------------------------------------

def test_the_hand_check_rereads_source_and_date(tmp_path, monkeypatch):
    many = [entry(id="it_e%02d" % i, signed_on="2025-%02d-%02d" % (1 + i % 12, 1 + i), accessed_on="2026-10-13")
            for i in range(25)]
    _install_file(monkeypatch, tmp_path, many)
    csv_path, key_path = syn.draw_sample(None, 60, "seed-1", tmp_path / "reviews", outlets=OUTLETS)
    import csv
    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    key = json.loads(key_path.read_text(encoding="utf-8"))
    assert len(rows) == 20 and key["n"] == 20 and key["measure_id"] == "syndication" and key["measure_version"] == syn.VERSION
    assert "signed_on" not in rows[0] and all(r["source_url"].startswith("https://") for r in rows)
    again, _ = syn.draw_sample(None, 60, "seed-1", tmp_path / "again", outlets=OUTLETS)
    assert again.read_text(encoding="utf-8") == csv_path.read_text(encoding="utf-8")
    ans = key["answers"]
    r = syn.score_check(rows, key)
    assert r["coded"] == 0 and r["passed"] is None and not r["complete"]
    for i, row in enumerate(rows):
        row["page_states_deal"] = "yes"
        row["page_date"] = ans[row["sample_id"]]["signed_on"]
        if i == 0:
            row["page_date"] = row["page_date"][:7]          # the page gives only the month: does not support a day
        if i == 1:
            row["page_states_deal"] = "no"
    r = syn.score_check(rows, key)
    assert r["coded"] == 20 and r["value"] == 0.9 and r["passed"] is True and r["threshold"] == 0.9
    rows[2]["page_date"] = "2024-01-01"
    assert syn.score_check(rows, key)["passed"] is False
    assert syn.date_agrees("2025-05-28", "2025-05") and syn.date_agrees("2025-05-28", "2025")
    assert not syn.date_agrees("2025-05", "2025-05-28") and not syn.date_agrees("", "2025") and not syn.date_agrees("May 2025", "2025-05")


# ---------------------------------------------------------------------------
# Stress test of 2026-10-29
# ---------------------------------------------------------------------------

def _add_model(conn, outlet_id, day, cat, n, route, method="llm"):
    """n current model labels with a route, as the export reads them."""
    ids = []
    for i in range(n):
        url = "https://x.example/%s/%s/%s/%s/%d" % (outlet_id, day, cat, route, i)
        cur = conn.execute(
            """INSERT INTO articles(url, url_hash, outlet_id, country, language, discovered_at, status, gate_relevant)
               VALUES (?,?,?,?,?,?,?,1)""",
            (url, hashlib.sha1(url.encode()).hexdigest(), outlet_id, "ITA", "it", day + "T10:00:00+00:00", "classified"))
        conn.execute(
            """INSERT INTO classifications(article_id, method, category, confidence, ruleset_version, classified_at,
               is_current, route) VALUES (?,?,?,?,?,?,1,?)""",
            (cur.lastrowid, method, cat, 0.9, config.RULESET_VERSION, day, route))
        ids.append(cur.lastrowid)
    return ids


def test_side_counts_leave_out_undetermined_labels_and_shadowed_copies(db, monkeypatch):
    """Standing definitions: an undetermined state origin label (pipeline/model_only.py) is in no count or
    denominator, and a copy another row stands for (pipeline/placements.py) is not the outlet's placement."""
    _add_model(db, "it_alpha", "2026-10-13", "A", 7, "unattributed")          # undetermined: out of both counts
    _add_model(db, "it_alpha", "2026-10-13", "A", 4, "sponsored_disclosure")  # undetermined
    _add_model(db, "it_alpha", "2026-10-13", "A", 5, "wire_credit")           # settled state origin
    copies = _add_model(db, "it_alpha", "2026-10-14", "C", 3, None)           # copies another row stands for
    db.commit()
    monkeypatch.setattr(syn.placements, "shadowed", lambda conn: set(copies))
    got = syn.side_counts(db, "it_alpha", "2026-10-05", "2026-11-01")
    assert got["state_origin"] == 10 + 5 and got["china"] == 100 + 5
    assert syn.side_counts(db, "it_alpha", "2026-10-05", "2026-11-01", set())["china"] == 108


def test_the_fifty_article_minimum_is_inclusive(db, tmp_path, monkeypatch):
    """49 China articles on a side give no share; 50 do. The rate is state origin over A + B + C."""
    _add(db, "gb_beta", "2026-11-20", "A", 5, country="GBR")
    _add(db, "gb_beta", "2026-11-21", "C", 44, country="GBR")       # before: 49
    _add(db, "gb_beta", "2026-12-05", "A", 25, country="GBR")
    _add(db, "gb_beta", "2026-12-06", "B", 25, country="GBR")       # after: 50
    db.commit()
    e = entry(id="gb_beta_cgtn", outlet_id="gb_beta", partner="cgtn", signed_on="2026-12-01")
    _install_file(monkeypatch, tmp_path, [e])
    out = syn.build_export(db, {"outlets": OUTLETS, "today": "2026-12-30"})       # last complete 29 Dec
    row = out["comparisons"][0]
    assert row["before"]["china"] == 49 and not row["before"]["sufficient"] and row["before"]["share"] is None
    assert row["after"]["china"] == 50 and row["after"]["sufficient"] and row["after"]["share"] == 0.5
    assert not row["sufficient"] and row["difference"] is None and row["reason"] == "fewer than 50 China articles before"
    assert out["counts"]["eligible"] == 1 and out["counts"]["compared"] == 0
    # One more before: both sides pass and the difference carries Newcombe's interval.
    _add(db, "gb_beta", "2026-11-22", "B", 1, country="GBR")
    db.commit()
    row = syn.build_export(db, {"outlets": OUTLETS, "today": "2026-12-30"})["comparisons"][0]
    d, lo, hi = syn.newcombe(25, 50, 5, 50)
    assert row["sufficient"] and row["before"]["share"] == 0.1
    assert row["difference"] == round(d, 4) and row["difference_ci"] == [round(lo, 4), round(hi, 4)]


def test_deals_at_the_edges_of_collection_and_of_today():
    """A deal on the first eligible day uses collection's first day; one whose after side would end on
    the export's own partial day waits; month precision waits for the whole month."""
    cs = "2026-09-03"
    first = syn.window(entry(signed_on="2026-10-01"), dt.date(2026, 10, 29), cs)
    assert first["eligible"] and first["before"][0] == cs and first["after"][1] == "2026-10-29"
    assert syn.window(entry(signed_on="2026-10-01"), dt.date(2026, 10, 28), cs)["reason"] == "too_recent"
    assert syn.last_complete_day({"today": "2026-10-30"}) == dt.date(2026, 10, 29)
    # The committed RTHK memorandum of 9 October 2026 is the first deal collection can compare: from the
    # export of 7 November 2026 (last complete day 6 November).
    rthk = next(e for e in syn.load_file()["entries"] if e["id"] == "hk_rthk_szmg_2026")
    assert syn.window(rthk, dt.date(2026, 11, 5))["reason"] == "too_recent"
    assert syn.window(rthk, dt.date(2026, 11, 6))["eligible"]
    for eid in ("hk_scmp_nanfang_2026", "id_antara_nanfang_2026"):     # 2 days after collection began
        e = next(x for x in syn.load_file()["entries"] if x["id"] == eid)
        assert not syn.predates_collection(e) and syn.window(e, dt.date(2027, 6, 1))["reason"] == "too_early"
    # A month that starts before collection is never compared, though it does not predate it.
    sept = entry(signed_on="2026-09", precision="month")
    assert not syn.predates_collection(sept, cs) and syn.window(sept, dt.date(2027, 1, 1), cs)["reason"] == "too_early"


def test_an_outlet_no_longer_collected_is_not_compared(db, tmp_path, monkeypatch):
    stopped = dict(OUTLETS[0], active=False, inactive_reason="feed gone", inactive_since="2026-11-20")
    later = dict(OUTLETS[0], active=False, inactive_reason="feed gone", inactive_since="2026-12-20")
    unknown = dict(OUTLETS[0], active=False, inactive_reason="feed gone")
    e = entry()
    assert syn.window(e, LAST, "2026-09-03", outlet=stopped)["reason"] == "not_collected"
    assert syn.window(e, LAST, "2026-09-03", outlet=unknown)["reason"] == "not_collected"
    assert syn.window(e, LAST, "2026-09-03", outlet=later)["eligible"]        # stopped after the after side
    assert syn.window(e, LAST, "2026-09-03", outlet=OUTLETS[0])["eligible"]
    _install_file(monkeypatch, tmp_path, [e])
    out = syn.build_export(db, {"outlets": [stopped, OUTLETS[1]], "today": "2026-12-31"})
    assert out["comparisons"] == [] and out["timeline"][0]["comparison"] == "not_collected"


def test_partner_other_names_the_body_and_the_timeline_uses_it(db, tmp_path, monkeypatch):
    errs = errors_of(entry(partner="other"))
    assert any("partner other needs partner_name" in m for m in errs), errs
    assert errors_of(entry(partner="other", partner_name="Nanfang Media Group")) == []
    assert errors_of(entry(partner="cmg", partner_name="")) and errors_of(entry(partner_name="  "))
    assert syn.partner_label(entry(partner="other", partner_name="Hubei  Media Group")) == "Hubei Media Group"
    assert syn.partner_label(entry(partner="cmg")) == "China Media Group"
    _install_file(monkeypatch, tmp_path, [entry(partner="cmg", partner_name="CCTV (now part of China Media Group)")])
    row = syn.build_export(db, {"outlets": OUTLETS, "today": "2026-12-31"})["timeline"][0]
    assert row["partner"] == "cmg" and row["partner_label"] == "CCTV (now part of China Media Group)"


def test_unquoted_verification_dates_and_a_file_that_is_not_utf8(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("entries:\n  - id: it_a\n    outlet_id: it_alpha\n    partner: cmg\n    kind: mou\n"
                 "    signed_on: 2025\n    precision: year\n    source_url: https://example.org/x\n"
                 "    source_title: T\n    source_publisher: P\n    source_kind: primary\n    accessed_on: 2026-10-13\n"
                 "    confidence: stated\n    verification:\n      status: verified\n      checked_on: 2026-10-28\n"
                 "      page_date: 2025-05-28\n      note: ok\n", encoding="utf-8")
    good, errs = syn.validate_file(p, OUTLETS)
    assert errs == [] and good[0]["verification"]["checked_on"] == "2026-10-28" and good[0]["verification"]["page_date"] == "2025-05-28"
    bad = tmp_path / "b.yaml"
    bad.write_bytes(b"entries:\n  - id: \xff\xfe\n")
    good, errs = syn.validate_file(bad, OUTLETS)
    assert good == [] and errs and "not UTF-8" in errs[0]
    assert syn.main(["validate", str(bad)]) == 1
    broken = tmp_path / "c.yaml"
    broken.write_text("entries:\n  - id: [unclosed\n", encoding="utf-8")
    assert syn.validate_file(broken, OUTLETS)[0] == [] and "not valid YAML" in syn.validate_file(broken, OUTLETS)[1][0]


def test_a_malformed_file_turns_the_measure_off_and_removes_the_side_file(db, tmp_path, monkeypatch):
    p = tmp_path / "syndication.yaml"
    p.write_text("entries:\n  - id: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(syn, "SYNDICATION_PATH", p)
    monkeypatch.setattr(syn.registry, "load_outlets", lambda *a, **k: OUTLETS)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "syndication.json").write_text("{}", encoding="utf-8")
    cfg = tmp_path / "measures.yaml"
    cfg.write_text(yaml.safe_dump([{"id": "syndication", "label": "Syndication deals", "module": "pipeline.syndication",
                                    "enabled": True, "min_inputs": 10}]), encoding="utf-8")
    blocks = measures.run_all_quietly(db, out_dir, {"outlets": OUTLETS, "today": "2026-12-31"}, write_json=lambda p, d: None,
                                      config_path=cfg, reviews_dir=tmp_path / "reviews")
    assert blocks["syndication"]["status"] == "off" and "0 of at least 10" in blocks["syndication"]["reason"]
    assert not (out_dir / "syndication.json").exists()


def test_entries_without_a_source_check_are_counted_and_said(db, tmp_path, monkeypatch):
    ok = {"status": "verified", "checked_on": "2026-10-28", "page_date": "2026-11-02", "note": "The page states it."}
    _install_file(monkeypatch, tmp_path, [entry(verification=ok), entry(id="new_one", partner="cgtn")])
    out = syn.build_export(db, {"outlets": OUTLETS, "today": "2026-12-31"})
    assert out["counts"]["verified"] == 1 and out["counts"]["unchecked"] == 1
    t = syn.methodology_text({"summary": out["counts"]})
    assert "1 entry added since has not been checked yet" in t and "each entry's page was reread" not in t
    t = syn.methodology_text({"summary": {"entries": 4, "predates_collection": 2}})
    assert "Most" not in t and "2 of the 4 entries now listed were signed before collection began" in t
    assert "\u2014" not in t and "\u2013" not in t


def test_the_committed_entries_after_the_stress_test():
    """The 2026-10-29 rereading: names, kinds, confidence and precision follow the pages."""
    by_id = {e["id"]: e for e in syn.load_file()["entries"]}
    nan = by_id["ng_nan_xinhua_2019"]
    assert nan["signed_on"] == "2019-05" and nan["precision"] == "month" and nan["verification"]["page_date"] == "2019-05"
    assert by_id["za_iol_cctv_2014"]["confidence"] == "stated"          # the Cape Times is Independent Media's
    mehr = by_id["ir_mehr_xinhua_2024"]
    assert mehr["confidence"] == "stated" and mehr["source_kind"] == "primary"   # the Tehran Times is Mehr Media Group's
    assert by_id["tl_tatoli_guangxi_2025"]["kind"] == "mou"            # a memorandum of cooperation
    others = [e for e in by_id.values() if e["partner"] == "other"]
    assert len(others) == 8 and all(e.get("partner_name") for e in others)
    assert by_id["to_tbc_cctv_2006"]["partner_name"].startswith("CCTV") and by_id["za_iol_cctv_2014"]["partner_name"].startswith("CCTV")
    # One agreement listed once per monitored outlet it covers: Fairfax (3 papers) and Nanfang with Antara (2 services).
    keys = {(e["source_url"], syn.partner_label(e), e["kind"], e["signed_on"]) for e in by_id.values()}
    assert len(by_id) == 51 and len(keys) == 48
    # Every outlet id is a registry outlet that is collected; the one outlet outside the registry is marked.
    from pipeline import registry
    outlets = {o["id"]: o for o in registry.load_outlets()}
    for e in by_id.values():
        if e.get("unmonitored"):
            assert e["outlet_name"] and not e.get("outlet_id")
        else:
            assert outlets[e["outlet_id"]]["active"], e["id"]
            assert outlets[e["outlet_id"]]["country"] != "CHN", e["id"]


def test_another_deal_of_the_outlet_inside_the_window_is_listed(db, tmp_path, monkeypatch):
    """Two deals of one outlet in one window share its counts: each row says so."""
    a = entry()
    b = entry(id="it_alpha_cmg_2026", partner="cmg", kind="content_exchange", signed_on="2026-11", precision="month")
    far = entry(id="it_alpha_cgtn_2025", partner="cgtn", signed_on="2025-11-02")
    other_outlet = entry(id="gb_beta_x", outlet_id="gb_beta", signed_on="2026-11-02")
    _install_file(monkeypatch, tmp_path, [a, b, far, other_outlet])
    out = syn.build_export(db, {"outlets": OUTLETS, "today": "2027-01-31"})
    rows = {c["entry_id"]: c for c in out["comparisons"]}
    od = [x for x in rows["it_alpha_xinhua_2026"]["confounders"] if x["type"] == "other_deal"]
    assert [x["entry_id"] for x in od] == ["it_alpha_cmg_2026"] and "November 2026" in od[0]["text"]
    assert [x["entry_id"] for x in rows["it_alpha_cmg_2026"]["confounders"] if x["type"] == "other_deal"] == ["it_alpha_xinhua_2026"]
    assert not [x for x in rows["gb_beta_x"]["confounders"] if x["type"] == "other_deal"]
    assert rows["it_alpha_xinhua_2026"]["confounded"]
