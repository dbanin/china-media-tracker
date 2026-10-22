"""scripts/numbers_sheet.py: one numbers sheet from one export, read only.

Small exports are written into tmp_path in both shapes: schema 15, where the model only category is code M
(meta.model_only_state_origin, findings.model_only) and the export's own china_total includes it, and
schema 16, where it is undetermined, code U (meta.undetermined, findings.undetermined), and is in no total.
"""
import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("numbers_sheet", ROOT / "scripts" / "numbers_sheet.py")
ns = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ns)

GENERATED = "2026-10-05T12:00:00+00:00"
CATS = ("A", "Ar", "Al", "Ah", "B", "Br", "Bl", "C", "N", "pending", "rel")

# day -> country -> counts (the model only code is written as X and renamed per shape), routes and item ids.
DAYS = {
    "2026-09-01": {  # before the 30 day window
        "ITA": {"A": 2, "Ar": 2, "B": 1, "Bl": 1, "C": 5, "N": 3, "X": 1, "_routes": {"wire_credit": 2}, "_items": ["i1", "i2"]},
        "WLD": {"A": 1, "Al": 1, "C": 1, "N": 2, "X": 2, "_routes": {"distribution_stamp": 1}, "_items": ["w1"]},
    },
    "2026-09-10": {
        "ITA": {"A": 3, "Ar": 2, "Al": 1, "B": 1, "Bl": 1, "C": 4, "N": 2, "X": 1, "pending": 1,
                "_routes": {"wire_credit": 2, "sponsored_disclosure": 1}, "_items": ["i3", "i3", "i4"]},
        "ESP": {"A": 2, "Ar": 2, "C": 6, "N": 1, "_routes": {"distribution_stamp": 2}, "_items": ["e1", "e2"]},
        "FRA": {"A": 2, "Ar": 2, "C": 3, "_routes": {"diplomatic_byline": 2}, "_items": ["f1", "f2"]},
        "WLD": {"A": 1, "Ar": 1, "N": 1, "X": 1, "_routes": {"distribution_stamp": 1}, "_items": ["w2"]},
    },
    "2026-10-04": {
        "ESP": {"A": 1, "Ar": 1, "C": 2, "_routes": {"uncredited_copy": 1}, "_items": ["e3"]},
        "DEU": {"A": 1, "Al": 1, "C": 2, "X": 2, "_routes": {"wire_credit": 1}, "_items": ["d1"]},
        "ITA": {"C": 1},
    },
    "2026-10-05": {  # the export day, partial: in all time, not in the 30 complete days
        "ITA": {"A": 1, "Ar": 1, "C": 1, "_routes": {"wire_credit": 1}, "_items": ["i5"]},
    },
}


def _sum(days, code, countries=None, since=None):
    out = {}
    for d, per in days.items():
        if since and d < since:
            continue
        for c, v in per.items():
            if countries and c not in countries:
                continue
            t = out.setdefault(c, dict.fromkeys(CATS + (code,), 0))
            for k in CATS:
                t[k] += v.get(k, 0)
            t[code] += v.get("X", 0)
    return out


def _derive(t, code, new):
    t = dict(t)
    china = t["A"] + t["B"] + t["C"] + (0 if new else t[code])
    t["china_total"] = china
    t["share_a"] = round(t["A"] / china, 4) if china else None
    return t


def _export(tmp_path, new, days=None, mutate=None):
    """Writes a small export of either shape and returns its data directory."""
    days = copy.deepcopy(days or DAYS)
    code = "U" if new else "M"
    schema = 16 if new else 15
    stamp = {"schema_version": schema, "generated_at": GENERATED}
    d = tmp_path / ("v%d_%d" % (schema, len(list(tmp_path.iterdir())))) / "data"
    (d / "daily").mkdir(parents=True)
    # daily files
    months = {}
    for day, per in days.items():
        entry = {"countries": {}, "routes": {}, "underlying_items": {}}
        for c, v in per.items():
            row = {k: v.get(k, 0) for k in CATS}
            row[code] = v.get("X", 0)
            entry["countries"][c] = row
            rs = dict(v.get("_routes", {}))
            if v.get("X"):
                rs["unattributed"] = v["X"]
            if rs:
                entry["routes"][c] = rs
            if v.get("_items"):
                entry["underlying_items"][c] = {"A": sorted(set(v["_items"]))}
        months.setdefault(day[:7], {})[day] = entry
    for m, ds in months.items():
        (d / "daily" / ("%s.json" % m)).write_text(json.dumps(dict(stamp, month=m, days=ds)))
    # global_series.json, no stamp
    series = []
    for day in sorted(days):
        tot = {k: 0 for k in ("A", "B", "C", "N", code, "rel", "pending")}
        wld = dict(tot)
        for c, v in days[day].items():
            for k in tot:
                x = v.get("X", 0) if k == code else v.get(k, 0)
                tot[k] += x
                if c == "WLD":
                    wld[k] += x
        series.append(dict(tot, date=day, wld=wld))
    (d / "global_series.json").write_text(json.dumps(series))
    (d.parent / "country-names.json").write_text(json.dumps({"names": {
        "ESP": "Spain", "ITA": "Italy", "FRA": "France", "DEU": "Germany"}}))
    # latest.json
    per_at = _sum(days, code)
    per_30 = _sum(days, code, since="2026-09-05")
    world_at = dict.fromkeys(CATS + (code,), 0)
    world_30 = dict.fromkeys(CATS + (code,), 0)
    for src, dst in ((per_at, world_at), (per_30, world_30)):
        for v in src.values():
            for k in dst:
                dst[k] += v[k]
    items = {i for per in days.values() for v in per.values() for i in v.get("_items", [])}
    world_at["uniqA"] = len(items)
    world_at["cls"] = sum(world_at[k] for k in ("A", "B", "C", "N", code))
    gate_parts = {"classified": world_at["cls"] + 3, "paywalled": 4, "failed": 2, "blocked_robots": 1,
                  "fetched_waiting_rules": 1, "fetched_text_not_delivered": 0, "waiting_for_model": 1,
                  "not_yet_fetched": 0, "other": 0}
    gate_total = sum(gate_parts.values())
    world_at["rel"] = gate_total - 3
    countries = {c: {"all_time": _derive(per_at[c], code, new),
                     "last_30d": _derive(per_30.get(c, dict.fromkeys(CATS + (code,), 0)), code, new)}
                 for c in per_at}
    latest = dict(stamp, window_start_30d="2026-09-05", window_end_30d="2026-10-05", window_end_30d_partial=True,
                  countries=countries, totals={"all_time": _derive(world_at, code, new),
                                               "last_30d": _derive(world_30, code, new), "population": 1})
    (d / "latest.json").write_text(json.dumps(latest))
    # meta.json
    so30 = sum(v.get("A", 0) for day, per in days.items() if "2026-09-05" <= day <= "2026-10-04" for v in per.values())
    x30 = sum(v.get("X", 0) for day, per in days.items() if "2026-09-05" <= day <= "2026-10-04" for v in per.values())
    route_totals = {}
    for per in days.values():
        for v in per.values():
            for r, k in v.get("_routes", {}).items():
                route_totals[r] = route_totals.get(r, 0) + k
    route_totals["unattributed"] = world_at[code]
    block = {"count": world_at[code], "count_last_30d": x30, "status": "provisional", "passed": False,
             "held_apart": True, "check": {"state": "none", "coded": 0, "n": 0, "precision": None},
             "reason": "no hand check yet", "code": code,
             "label": "Undetermined" if new else "State origin by the model's judgement alone (provisional)",
             "rule": {"pass_precision": 0.9, "withhold_below": 0.8, "min_coded": 100, "statistic": "precision"}}
    if new:
        # The schema 16 block (pipeline/model_only.meta_block): no status, never rejoins, counts by stored route.
        block = {"count": world_at[code], "count_last_30d": x30, "by_route": {"unattributed": world_at[code] - 2,
                 "sponsored_disclosure": 2}, "excluded_from_counts": True, "rejoins_state_origin": False,
                 "check": {"state": "none", "coded": 0, "n": 0}, "summary": "no hand check yet", "code": code,
                 "label": "Undetermined: who wrote or commissioned it could not be settled",
                 "rule": {"method_not": "rules", "routes": ["unattributed", "sponsored_disclosure"], "min_coded": 100}}
    fblock = {"all_time": world_at[code], "last_30d": x30, "status": "provisional"}
    meta = dict(
        stamp, ruleset_version="2026.10.2", gate_version="2026.10.2", llm_prompt_version="2026-10-17",
        llm_model="test-model", first_discovered="2026-09-01", outlets_total=12, outlets_active=10,
        countries_monitored=5, countries_in_gaps=2, gate_languages=3, articles_discovered=5000,
        articles_gate_relevant=gate_total, articles_classified=gate_parts["classified"], llm_labels_total=9,
        gate_accounting={"total": gate_total, "parts": gate_parts, "other_statuses": {}, "copies_counted_once": 3},
        route_totals=route_totals, kappa_warning_threshold=0.7, relay_publishable=False, relay_provisional=True,
        relay_human_study={"state": "none", "passed": False, "kappa_bc": None},
        official_sourcing_pending=1, official_sourcing_pending_countries=1, labels_on_older_prompt=0,
        llm_budget={"month": "2026-10", "estimated_usd": 1.0, "budget_usd": 110.0, "calls_left_today": 5,
                    "provider_limit": {"until": "2026-11-01T00:00:00+00:00", "recorded_at": "2026-10-05T10:00:00+00:00",
                                       "message": "limit"}},
        distribution_wires={"outlets": ["us_wire"], "stratum": "WLD"},
        findings={"since": "2026-09-01",
                  "last_30d": {"state_origin": so30, "window_start": "2026-09-05", "window_end": "2026-10-04"},
                  "top_country": {"iso": "ESP", "state_origin": 3, "underlying": 3, "share_of_world": round(3 / so30, 4),
                                  "top_outlet": {"name": "Outlet E", "count": 1, "total": 3, "tied": 1}}},
        last_runs={"classify_llm": "2026-10-05T11:00:00+00:00"},
    )
    if new:
        meta["undetermined"] = block
        meta["findings"]["undetermined"] = fblock
    else:
        meta["model_only_state_origin"] = block
        meta["findings"]["model_only"] = fblock
    if mutate:
        mutate(meta, latest)
        (d / "latest.json").write_text(json.dumps(latest))
    (d / "meta.json").write_text(json.dumps(meta))
    return d


def _sheet(data_dir):
    return ns.build(ns.load_export(data_dir))


def _figure(text, label, section):
    """The number after '**label: ' inside a section (### heading)."""
    part = text.split("### " + section, 1)[1].split("\n### ", 1)[0]
    m = re.search(r"\*\*%s: ([\d,]+)" % re.escape(label), part)
    return int(m.group(1).replace(",", ""))


@pytest.mark.parametrize("new", [False, True])
def test_both_shapes_are_read_and_labelled(tmp_path, new):
    text = _sheet(_export(tmp_path, new))
    if new:
        assert "Undetermined (code U): 7 placements" in text
        assert "code M" not in text and "model_only_state_origin" not in text
        assert "meta.undetermined.count 7" in text
        assert ("By stored route (meta.undetermined.by_route): no evidence named, the model's judgement alone 5; a "
                "sponsored disclosure the model read and the rules did not find 2; they add up to 7.") in text
        assert ("**Undetermined (code U):** never rejoins state origin; hand check, information only: state none, 0 coded "
                "of 0 drawn, no shares yet. As the export puts it: no hand check yet.") in text
    else:
        assert "Model judgement only, provisional (code M): 7 placements" in text
        assert "renamed undetermined (code U)" in text
        assert "meta.model_only_state_origin.count 7" in text
        assert "**Model judgement only, provisional (code M):** status provisional" in text
        assert "Rule: rejoins state origin at a precision of at least 90 percent on at least 100 coded rows" in text
    assert "Schema version: %d" % (16 if new else 15) in text
    assert "Generated at: 5 October 2026, 12:00:00 UTC" in text
    assert "Ruleset: 2026.10.2. Gate: 2026.10.2. Classifier prompt version: 17 October 2026" in text


@pytest.mark.parametrize("new", [False, True])
def test_undetermined_is_in_no_total_or_denominator(tmp_path, new):
    text = _sheet(_export(tmp_path, new))
    for section, (a, b, c) in (("All time", (14, 2, 25)), ("Last 30 complete days", (10, 1, 18))):
        assert _figure(text, "State origin, evidence settled", section) == a
        assert _figure(text, "China coverage total", section) == a + b + c
        assert "**State origin share of China coverage: %.2f percent** (%d of %d" % (100.0 * a / (a + b + c), a, a + b + c) in text
    # The old export's own china_total and share_a include M, and the sheet says not to quote them.
    assert ("This export's own china_total, 48, differs by 7" in text) is (not new)
    assert ("Do not quote it" in text) is (not new)


def test_last_30_days_are_the_complete_days(tmp_path):
    """The partial export day (5 October) and the days before the window (1 September) are left out."""
    text = _sheet(_export(tmp_path, True))
    assert "22 September" not in text
    assert "5 September 2026 through 4 October 2026, 30 days, 2 of them with data in the daily files" in text
    assert _figure(text, "Undetermined (code U)", "Last 30 complete days") == 4
    assert "Cross checks for this window: global_series.json, the daily files, findings.last_30d and meta.undetermined agree." in text


@pytest.mark.parametrize("new", [False, True])
def test_totals_add_up(tmp_path, new):
    text = _sheet(_export(tmp_path, new))
    assert "**The rows add up to 69; the gate total is 69: they agree.**" in text
    assert "so the site's own gate total is 66 placements (latest.json totals.all_time.rel: 66)" in text
    assert text.count("The five routes add up to") == 2
    assert "The five routes add up to 14 against 14 state origin placements: they agree." in text
    assert "The five routes add up to 10 against 10 state origin placements: they agree." in text
    assert "By the rules: 11; by the model: 3; hand reviewed: 0 (key Ar, Al, Ah). Rules plus model: 14." in text


def test_a_table_that_does_not_add_up_says_so(tmp_path):
    def broken(meta, latest):
        meta["gate_accounting"]["total"] += 1
        meta["route_totals"]["wire_credit"] += 2
    text = _sheet(_export(tmp_path, True, mutate=broken))
    assert "THEY DO NOT AGREE, do not quote this table" in text
    assert "they differ by 2, so quote routes as stored" in text


@pytest.mark.parametrize("new", [False, True])
def test_no_dashes_in_output(tmp_path, new):
    text = _sheet(_export(tmp_path, new))
    for bad in ("\u2014", "\u2013", " - ", "--"):
        assert bad not in text, bad
    assert not any(line.lstrip().startswith("- ") for line in text.splitlines())
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text), "dates are written in words"


def test_portion_uses_most_only_above_half():
    assert ns.portion(51, 100) == "most (51.0 percent)"
    assert ns.portion(50, 100) == "half (50.0 percent)"
    assert ns.portion(49, 100) == "49.0 percent"
    assert ns.portion(1, 0) == ns.NOT_IN


def test_most_appears_only_above_half(tmp_path):
    # Independent journalism is 25 of 41 China coverage placements (61 percent): "Most" is right.
    text = _sheet(_export(tmp_path, True))
    assert "\"Most (61.0 percent) of China coverage is independent journalism: 25 of 41" in text
    # Make independent journalism exactly half of China coverage, every other share under half: no "most" anywhere.
    days = copy.deepcopy(DAYS)
    days["2026-09-01"]["ITA"]["C"] = 0
    days["2026-09-10"]["ITA"]["C"] = 0
    days["2026-09-10"]["ESP"]["C"] = 0
    days["2026-09-10"]["FRA"]["C"] = 0
    days["2026-10-04"]["ESP"]["C"] = 4
    days["2026-10-04"]["DEU"]["C"] = 4
    days["2026-10-04"]["ITA"]["C"] = 3
    days["2026-10-05"]["ITA"]["C"] = 4   # C = WLD 1 + 4 + 4 + 3 + 4 = 16 = A 14 + B 2
    text = _sheet(_export(tmp_path, True, days=days))
    assert "Half (50.0 percent) of China coverage is independent journalism: 16 of 32" in text
    assert "most" not in text.lower()


def test_top_ten_states_ties(tmp_path):
    text = _sheet(_export(tmp_path, False))
    # ESP 3 first; ITA 3 as well (2026-09-10) so they tie; FRA 2; DEU 1. WLD is never ranked.
    assert "* 1. Spain (ESP): 3 placements, 3 underlying items, 30.0 percent of world" in text
    assert "(tied with Spain)" in text and "(tied with Italy)" in text
    assert "* 1. Italy (ITA): 3 placements, 2 underlying items" in text
    assert "* 3. France (FRA)" in text and "* 4. Germany (DEU)" in text
    assert "(WLD):" not in text
    assert "Spain and Italy each carried the largest number: 3 of the 10 state origin placements, 30.0 percent." in text


def test_tenth_place_shared(tmp_path):
    days = copy.deepcopy(DAYS)
    for i, iso in enumerate(["AUT", "BEL", "CHE", "DNK", "FIN", "GRC", "HUN", "IRL", "LUX"]):
        days["2026-10-04"][iso] = {"A": 1, "Ar": 1, "C": 1, "_routes": {"wire_credit": 1}, "_items": ["x%d" % i]}
    text = _sheet(_export(tmp_path, True, days=days))
    assert "Tenth place is shared, so the list has 13 entries." in text


def test_mixed_exports_are_refused(tmp_path, capsys):
    d = _export(tmp_path, True)
    latest = json.loads((d / "latest.json").read_text())
    latest["generated_at"] = "2026-10-05T00:00:00+00:00"
    (d / "latest.json").write_text(json.dumps(latest))
    assert ns.main(["--data", str(d), "--out", str(tmp_path / "s.md")]) == 2
    assert "not from one export" in capsys.readouterr().err
    assert not (tmp_path / "s.md").exists()


def test_missing_figures_say_so(tmp_path):
    def strip(meta, latest):
        for k in ("gate_accounting", "llm_budget", "route_totals", "gate_languages"):
            meta.pop(k)
    text = _sheet(_export(tmp_path, True, mutate=strip))
    assert "Gate accounting: not in this export." in text
    assert "Provider usage limit: not in this export." in text
    assert "Gate languages (keyword lists): not in this export" in text
    assert "Routes: not in this export." in text


def test_writes_the_file_and_reads_nothing_else(tmp_path):
    d = _export(tmp_path, False)
    before = {p: p.read_bytes() for p in d.rglob("*") if p.is_file()}
    out = tmp_path / "sheet.md"
    assert ns.main(["--data", str(d), "--out", str(out)]) == 0
    assert out.read_text().startswith("# Numbers sheet")
    assert {p: p.read_bytes() for p in d.rglob("*") if p.is_file()} == before


def test_committed_export_reads():
    data = ROOT / "docs" / "data"
    if not (data / "meta.json").exists():
        pytest.skip("no committed export")
    ex = ns.load_export(data)
    if ns.stamp_problems(ex):
        pytest.skip("the committed export is being rewritten")
    text = ns.build(ex)
    assert "Generated at:" in text and "not in this export" in text
    for bad in ("\u2014", "\u2013", " - ", "--"):
        assert bad not in text


def _with_copies(meta, latest):
    """The block pipeline/model_only.meta_block writes once kind (c) exists: uncredited copies of items that
    are not settled state origin, under their stored route uncredited_copy, inside the same count of 7."""
    meta["undetermined"]["by_route"] = {"unattributed": 3, "sponsored_disclosure": 2, "uncredited_copy": 2}


def test_copies_of_undetermined_items_are_shown_and_kept_out_of_every_total(tmp_path):
    text = _sheet(_export(tmp_path, True, mutate=_with_copies))
    assert ("By stored route (meta.undetermined.by_route): no evidence named, the model's judgement alone 3; a "
            "sponsored disclosure the model read and the rules did not find 2; copies of undetermined items "
            "(uncredited copies whose item is not settled state origin) 2; they add up to 7.") in text
    assert ("Copies of undetermined items: 2 (meta.undetermined.by_route.uncredited_copy). Their stored route is "
            "uncredited copy, but the item each repeats is not settled state origin, so they are undetermined too: "
            "in no total, not in state origin and not in its uncredited copy route above.") in text
    assert "or as copies of items that are undetermined themselves. They are kept out of every count" in text
    assert "Undetermined (code U): 7 placements" in text
    # Nothing moves into any total: the same figures as without the key.
    for section, (a, b, c) in (("All time", (14, 2, 25)), ("Last 30 complete days", (10, 1, 18))):
        assert _figure(text, "State origin, evidence settled", section) == a
        assert _figure(text, "China coverage total", section) == a + b + c
    for bad in ("\u2014", "\u2013", " - ", "--"):
        assert bad not in text, bad


def test_no_copies_line_without_the_key(tmp_path):
    text = _sheet(_export(tmp_path, True))
    assert "Copies of undetermined items" not in text and "copies of undetermined items" not in text
    assert "or as copies of items that are undetermined themselves" not in text
