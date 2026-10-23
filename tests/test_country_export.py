"""scripts/country_export.py: the aggregate country export for the paper, on small synthetic audit fixtures.

Tiers never mix, undetermined items never reach state origin, copies are removed and counted, every share has
its denominator, no text field reaches any CSV, the theme mapping is deterministic, the session overlay follows
the ingest rules, and the README carries no dash in its prose.
"""
import csv
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("country_export", ROOT / "scripts" / "country_export.py")
ce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ce)

GEN = "2026-09-12T04:00:00+00:00"
SECRET = "Zyxwv secret headline"     # appears in titles, quotes and reasoning, never in an output


def _row(i, country="ITA", category="C", method="rules", route=None, status="classified", title=None, day="2026-09-10",
         themes=("economy",), gate=("China",), sigs=(), cited=(), outlet="it_one", **kw):
    d = {"id": i, "country": country, "category": category, "method": method, "route": route, "status": status,
         "title": title or ("%s %d" % (SECRET, i)), "url": "https://example.test/a/%d" % i, "url_hash": "h%d" % i,
         "evidence_quote": "%s quote %d" % (SECRET, i), "reasoning": "%s reasoning" % SECRET,
         "discovered_at": day + "T01:00:00+00:00", "published_at": day + "T00:00:00+00:00", "dup_group_id": None,
         "gate_terms": json.dumps(list(gate)), "signatures_fired": json.dumps(list(sigs)),
         "china_sources_cited": json.dumps(list(cited)), "language": "it" if country == "ITA" else "en",
         "outlet_id": outlet, "model_version": None if method == "rules" else "claude-sonnet-5",
         "ruleset_version": "2026.10.2", "human_category": None, "confidence": 0.9,
         "themes": json.dumps({"v": "2026.09.4", "t": list(themes), "b": 1}), "classified_at": GEN}
    if category is None:
        d.update({"method": None, "model_version": None})
    d.update(kw)
    return d


ROWS = [
    _row(1, category="A", route="wire_credit", sigs=["xinhua_slash_credit"], themes=["culture"]),
    _row(2, category="A", route="wire_credit", sigs=["xinhua_slash_credit"], themes=["culture"], counted_as=1),
    _row(3, category="A", method="llm", route="unattributed", themes=["security"]),
    _row(4, category="A", method="llm", route="sponsored_disclosure", themes=["security"]),
    _row(5, category="A", method="llm", route="uncredited_copy", themes=["technology"]),
    _row(6, category="B", method="llm", cited=["MOFA spokesperson Mao Ning"], themes=["diplomacy"]),
    _row(7, category="C", title="Taiwan Strait tensions rise", themes=["taiwan_hk", "security"], gate=["Taiwan"]),
    _row(8, category="C", gate=["China", "Global Times"], themes=["economy"], outlet="it_two"),
    _row(9, category="not_relevant"),
    _row(10, category=None, status="paywalled"),
    _row(11, category=None, status="awaiting_llm", url_hash="wait1"),
    _row(12, category=None, status="awaiting_llm", url_hash="wait2"),
    _row(13, category=None, status="awaiting_llm", url_hash="wait3"),
    _row(20, country="CAN", category="A", method="llm", route="uncredited_copy", outlet="ca_one", themes=["economy"]),
    _row(21, country="CAN", category="C", method="rules", outlet="ca_one", day="2026-09-11"),
    _row(30, country="AUS", category="A", route="distribution_stamp", outlet="au_wire", sigs=["wire_stamp_near_state_entity"]),
]


def _session(url_hash, category, basis, issuer):
    return {"url_hash": url_hash, "category": category, "confidence": 0.9, "evidence_quote": SECRET,
            "reasoning": SECRET, "china_sources_cited": ["Xinhua"] if category == "A" else [],
            "independent_confirmation_present": False, "confirmation_evidence": None,
            "model_version": "claude-opus-5-5 (session, prompt 2026-10-17)", "labelled_at": "2026-09-12",
            "source": "session", "issuer_type": issuer, "state_basis": basis}


def _repo(tmp_path: Path, rows=ROWS) -> Path:
    repo = tmp_path / "repo"
    (repo / "data" / "export").mkdir(parents=True)
    by_day = {}
    for r in rows:
        by_day.setdefault(r.get("day_of_record") or r["discovered_at"][:10], []).append(r)
    for day, rs in by_day.items():
        with open(repo / "data" / "export" / ("articles-%s.jsonl" % day), "w", encoding="utf-8") as fh:
            for r in rs:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
    data = repo / "docs" / "data"
    (data / "daily").mkdir(parents=True)
    meta = {"generated_at": GEN, "first_discovered": "2026-09-10", "schema_version": 16, "ruleset_version": "2026.10.2",
            "gate_version": "2026.10.2", "llm_model": "claude-sonnet-5", "llm_prompt_version": "2026-10-17",
            "articles_reviewed": 0, "themes_version": "2026.09.4", "theme_languages": ["en", "it"]}
    (data / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    # The site's own daily state origin counts: Italy's model uncredited copy is settled (A 2 on its day),
    # Canada's is a copy of an undetermined item (A 0).
    daily = {"days": {"2026-09-10": {"countries": {"ITA": {"A": 2, "disc": 100, "rel": 12}, "CAN": {"A": 0, "disc": 50, "rel": 1},
                                                   "AUS": {"A": 1, "disc": 10, "rel": 1}}},
                      "2026-09-11": {"countries": {"CAN": {"A": 0, "disc": 40, "rel": 1}}}}}
    (data / "daily" / "2026-09.json").write_text(json.dumps(daily), encoding="utf-8")
    latest = {"countries": {"ITA": {"all_time": {"A": 2, "U": 2, "china_total": 5}},
                            "CAN": {"all_time": {"A": 0, "U": 1, "china_total": 1}}}}
    (data / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
    outlets = {"outlets": [
        {"id": "it_one", "name": "Uno", "country": "ITA", "active": True, "collector": "hosted",
         "feeds": [{"ok": False, "last_ok": "2026-09-01T00:00:00+00:00", "consecutive_failures": 40, "frozen": False}]},
        {"id": "it_two", "name": "Due", "country": "ITA", "active": True, "collector": "hosted",
         "feeds": [{"ok": True, "last_ok": GEN, "consecutive_failures": 0, "frozen": False}]},
        {"id": "ca_one", "name": "One", "country": "CAN", "active": True, "feeds": []},
        {"id": "au_wire", "name": "Wire", "country": "AUS", "active": True, "feeds": []}]}
    (data / "outlets.json").write_text(json.dumps(outlets), encoding="utf-8")
    (repo / "sources").mkdir()
    (repo / "sources" / "outlets.yaml").write_text(
        "- id: it_one\n  name: Uno\n  country: ITA\n  tier: national_daily\n"
        "- id: it_two\n  name: Due\n  country: ITA\n  tier: national_daily\n  active: false\n  inactive_since: '2026-09-11'\n"
        "  inactive_reason: 'every feed failed on every check for more than 7 days, see https://example.test/x'\n"
        "- id: ca_one\n  name: One\n  country: CAN\n  tier: regional\n"
        "- id: au_wire\n  name: Wire\n  country: AUS\n  tier: distribution_wire\n", encoding="utf-8")
    return repo


@pytest.fixture
def built(tmp_path):
    repo = _repo(tmp_path)
    ov = tmp_path / "session.jsonl"
    ov.write_text("\n".join(json.dumps(x) for x in (
        _session("wait1", "A", "credit_line", "state_media"),       # tier 1b, wire credit
        _session("wait2", "A", "issuer_named", "government"),      # undetermined
        _session("wait3", "A", "credit_line", "private_release"),  # stored as C, as store_result does
        _session("nowhere", "C", "none", "none"))) + "\n", encoding="utf-8")
    plain = ce.build(repo, tmp_path / "out", ("ITA", "CAN", "AUS"))
    over = ce.build(repo, tmp_path / "out_overlay", ("ITA", "CAN", "AUS"), overlay=ov)
    return plain, over


def _csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _total(res, country, measure):
    return next(r["count"] for r in res["tables"]["totals"] if r["country"] == country and r["measure"] == measure)


def test_tiers_never_mix_and_undetermined_is_never_state_origin(built):
    plain, _ = built
    t = lambda m: _total(plain, "ITA", m)  # noqa: E731
    assert t("tier 1a found by the rules") == 1                  # row 1 (row 2 is its copy)
    assert t("tier 1b evidence route named by the model") == 1   # row 5, settled per the daily count
    assert t("tier 1 state origin with an evidence route") == 2
    assert t("tier 3 model only, undetermined (U), not in any total") == 2   # unattributed and model sponsored
    assert t("tier 2 provisional (model flagged and hand confirmed)") == 0
    assert t("China coverage (A + B + C, U excluded)") == 5      # 1a 1 + 1b 1 + B 1 + C 2, undetermined left out


def test_coverage_counts(built):
    plain, _ = built
    assert _total(plain, "ITA", "C independent journalism") == 2
    assert _total(plain, "ITA", "B unchecked state sourcing") == 1
    assert _total(plain, "ITA", "China coverage (A + B + C, U excluded)") == 5
    assert _total(plain, "ITA", "China relevant screened including undetermined (A + B + C + U)") == 7
    # Every undetermined row is outside every tier 1 count, in every table.
    for r in plain["tables"]["themes"]:
        assert r["tier1"] == r["tier1a_rules"] + r["tier1b_model_evidence"]
        assert r["china_coverage"] == r["tier1"] + r["B_unchecked_state_sourcing"] + r["C_independent"]
    themes_u = sum(r["tier3_undetermined_not_in_totals"] for r in plain["tables"]["themes"] if r["country"] == "ITA")
    assert themes_u == 2
    assert sum(r["china_coverage"] for r in plain["tables"]["themes"] if r["country"] == "ITA") == 5


def test_copy_of_undetermined_found_from_the_daily_count(built):
    plain, _ = built
    # Canada's model uncredited copy is undetermined on the site (daily A 0): moved to U, never tier 1.
    assert _total(plain, "CAN", "tier 1 state origin with an evidence route") == 0
    assert _total(plain, "CAN", "tier 3 model only, undetermined (U), not in any total") == 1
    assert _total(plain, "CAN", "of which copies of an undetermined item, counted as undetermined (U)") == 1
    assert _total(plain, "ITA", "of which copies of settled state origin, kept in tier 1b") == 1


def test_undetermined_rule_is_the_trackers(built):
    from pipeline import model_only
    for cat, route, method in (("A", "unattributed", "llm"), ("A", None, "llm"), ("A", "sponsored_disclosure", "llm")):
        assert model_only.is_undetermined(cat, route, method)
    assert not model_only.is_undetermined("A", "sponsored_disclosure", "rules")
    assert not model_only.is_undetermined("A", "wire_credit", "llm")


def test_dedup_counts(built):
    plain, _ = built
    assert _total(plain, "ITA", "syndicated copies removed (counted_as)") == 1
    assert _total(plain, "ITA", "placements") == len([r for r in ROWS if r["country"] == "ITA"]) - 1
    d = {r["group"]: r for r in plain["tables"]["dedup"] if r["country"] == "ITA"}
    assert d["tier 1a"]["stored_rows"] == 2 and d["tier 1a"]["copies_removed"] == 1 and d["tier 1a"]["placements"] == 1
    # A distribution wire is never in a country.
    assert _total(plain, "AUS", "distribution wire rows left out") == 1
    assert _total(plain, "AUS", "placements") == 0


def test_every_share_has_its_denominator(built):
    plain, _ = built
    out = plain["out"]
    for path in sorted(out.glob("*.csv")):
        rows = _csv(path)
        if not rows:
            continue
        cols = list(rows[0].keys())
        assert cols[0] == "country", path.name
        pcts = [c for c in cols if c.endswith("_pct") and c != "difference_pct_points"]
        if pcts:
            assert any("denominator" in c for c in cols), path.name
        for r in rows:
            if "share_pct" in r and r["share_pct"] != "":
                assert r["denominator"] not in ("", "0"), (path.name, r)
    for r in plain["tables"]["themes"]:
        assert r["coverage_denominator"] == 5 if r["country"] == "ITA" else True
        assert r["tier1_share_denominator"] == r["china_coverage"]
    for r in plain["tables"]["comparison"]:
        assert r["tier1_denominator"] == sum(x["tier1"] for x in plain["tables"]["themes"] if x["country"] == r["country"])


def test_no_text_field_leaks_into_any_csv(built):
    for res in built:
        for path in sorted(res["out"].glob("*.csv")):
            text = path.read_text(encoding="utf-8")
            header = [h.lower() for h in text.splitlines()[0].split(",")]
            for banned in ("title", "headline", "url", "url_hash", "quote", "evidence_quote", "reasoning", "summary"):
                assert banned not in header, (path.name, banned)
            assert SECRET not in text, path.name
            assert "Taiwan Strait tensions" not in text, path.name
            assert "http" not in text and "example.test" not in text, path.name
        readme = (res["out"] / "README.md").read_text(encoding="utf-8")
        assert SECRET not in readme and "example.test" not in readme


def test_theme_mapping_is_deterministic(built, tmp_path):
    assert ce.primary_theme("Taiwan Strait tensions rise", ["Taiwan"], ["taiwan_hk", "security"])[0] == ce.TH_TAIWAN
    # A gate term alone gives Taiwan only with the tracker's Taiwan and Hong Kong tag.
    assert ce.primary_theme("Navy drill", ["Taiwan"], ["security"])[0] == ce.TH_MIL
    assert ce.primary_theme("Navy drill", ["South China Sea"], ["security"])[0] == ce.TH_SCS
    assert ce.primary_theme("New tariffs on steel", [], ["economy", "technology"])[0] == ce.TH_TRADE
    assert ce.primary_theme("Fair opens", [], ["technology", "economy"])[0] == ce.TH_TECH
    assert ce.primary_theme("Fair opens", [], [])[0] == ce.TH_OTHER
    # Order of the tracker's tags does not matter, and every primary is on the fixed list.
    for tags in (["economy", "culture", "rights"], ["rights", "culture", "economy"]):
        assert ce.primary_theme("x", [], tags) == ("domestic society and human interest", "tracker:rights")
    for r in ROWS:
        assert ce.primary_theme(r["title"], json.loads(r["gate_terms"]), json.loads(r["themes"])["t"])[0] in ce.THEMES
    # Every tracker theme is mapped, and two runs write the same mapping and theme tables.
    assert set(ce.TRACKER_MAP) == {"diplomacy", "economy", "investment", "technology", "energy", "security",
                                   "taiwan_hk", "rights", "politics", "culture", "disasters", "other"}
    plain, _ = built
    again = ce.build(_repo(tmp_path / "again"), tmp_path / "again_out", ("ITA", "CAN", "AUS"), only="themes")
    for name in ("theme_mapping.csv", "themes.csv", "theme_comparison.csv", "themes_top5.csv"):
        assert (plain["out"] / name).read_text(encoding="utf-8") == (again["out"] / name).read_text(encoding="utf-8")


def test_citing_official_sources_counts_articles(built):
    plain, _ = built
    # Row 6 (B, MFA spokesperson) and row 8 (C, Global Times among its gate terms); row 7 cites none.
    assert _total(plain, "ITA", "citing a Chinese official or state outlet without state origin (B or C)") == 2
    assert _total(plain, "ITA", "of which B") == 1
    src = {r["source"]: r for r in plain["tables"]["sources"] if r["country"] == "ITA"}
    assert src["Xinhua"]["tier1a_rules"] == 1
    assert src["MFA spokesperson"]["B"] == 1 and src["Global Times"]["C"] == 1


def test_overlay_applies_session_labels_as_the_ingest_would(built):
    plain, over = built
    ov = over["ctx"]["overlay"]
    assert ov["applied"] == 3 and ov["unknown"] == 1
    assert _total(over, "ITA", "tier 1b evidence route named by the model") == 2   # row 5 and wait1
    assert _total(over, "ITA", "tier 3 model only, undetermined (U), not in any total") == 3   # plus wait2
    assert _total(over, "ITA", "C independent journalism") == 3   # plus wait3, state origin with a private issuer
    readme = (over["out"] / "README.md").read_text(encoding="utf-8")
    assert "session labels not yet ingested by the runner" in readme
    assert "claude-opus-5-5 (session, prompt 2026-10-17)" in readme
    assert _total(plain, "ITA", "tier 1b evidence route named by the model") == 1


def test_readme_window_versions_and_no_dashes(built):
    plain, _ = built
    for name in ("README.md",):
        text = (plain["out"] / name).read_text(encoding="utf-8")
        assert "10 September 2026" in text and GEN in text
        assert "agreement: none" in text and "Tier 2" in text
        assert not re.search(r"Category [ABC]", text)
        prose = re.sub(r"`[^`]*`", "", text)
        for line in prose.splitlines():
            if set(line.strip()) <= set("|-"):
                continue   # a table rule
            assert not re.search("[-\\u2013\\u2014]", line), line
    themes = ce.build(_repo(Path(str(plain["out"]) + "_t")), Path(str(plain["out"]) + "_tt"), ("ITA",), only="themes")
    text = (themes["out"] / "THEMES_README.md").read_text(encoding="utf-8")
    assert "\u2013" not in text and "\u2014" not in text


def test_monthly_series_marks_months_before_collection(built):
    plain, _ = built
    months = [r for r in plain["tables"]["monthly"] if r["country"] == "ITA"]
    assert months[0]["month"] == "2025-01" and months[0]["collected"] == "no"
    sep = next(r for r in months if r["month"] == "2026-09")
    assert sep["collected"] == "partial" and sep["china_coverage"] == 5 and sep["tier3_undetermined_not_in_totals"] == 2


def test_feed_health_and_reasons_without_addresses(built):
    plain, _ = built
    f = {r["outlet_id"]: r for r in plain["tables"]["feeds"]}
    assert f["it_one"]["feeds_failing_7_days_or_more_at_export"] == 1
    assert f["it_two"]["deactivated_in_window"] == "yes"
    assert f["it_two"]["deactivated_after_feed_failure_over_7_days"] == "yes"
    assert "http" not in f["it_two"]["inactive_reason"]
