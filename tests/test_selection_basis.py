"""Outlets selected for their China coverage, not reach (RULES D12, 2026-10-28). These run offline."""
import pytest

from pipeline import export, methodology, registry, store

EVIDENCE = "https://chinadigitaltimes.net/2024/11/cdt-report-borrowed-boats-the-prcs-embedded-propaganda-in-local-african-media/"


def _outlet(**kw):
    o = {"id": "gh_x", "name": "X", "country": "GHA", "language": "en", "feeds": ["https://x.example/feed/"],
         "tier": "national_daily", "active": True, "collector": "hosted"}
    o.update(kw)
    return o


def test_schema_and_validator_accept_a_sourced_basis():
    registry.validate_outlets([_outlet(selection_basis=registry.SELECTED_FOR_CHINA + EVIDENCE)])


@pytest.mark.parametrize("basis", ["", "selected for China coverage", "selected for China coverage, not reach",
                                   "not reach; evidence: none"])
def test_basis_without_evidence_rejected(basis):
    with pytest.raises(Exception):
        registry.validate_outlets([_outlet(selection_basis=basis)])


def test_selected_outlet_cannot_carry_audience_rank():
    with pytest.raises(ValueError):
        registry.validate_outlets([_outlet(selection_basis=registry.SELECTED_FOR_CHINA + EVIDENCE, audience_rank=3)])


def test_helpers():
    sel = _outlet(selection_basis=registry.SELECTED_FOR_CHINA + EVIDENCE)
    reach = _outlet(id="gh_y", feeds=["https://y.example/feed/"])
    assert not registry.reach_selected(sel) and registry.reach_selected(reach)
    assert registry.selection_evidence(sel) == EVIDENCE and registry.selection_evidence(reach) is None
    assert registry.reach_outlets([sel, reach]) == [reach]
    s = registry.selection_summary([sel, reach, dict(sel, id="gh_z", active=False, inactive_reason="test")])
    assert s == {"china_selected_active": 1, "by_country": {"GHA": 1}, "ids": ["gh_x"]}


def test_save_outlets_keeps_the_field(tmp_path):
    p = tmp_path / "o.yaml"
    o = _outlet(selection_basis=registry.SELECTED_FOR_CHINA + EVIDENCE)
    registry.save_outlets([o], p)
    back = registry.load_outlets(p)
    assert back[0]["selection_basis"] == o["selection_basis"]


def test_outlets_json_flags_selection(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    out = export.build_outlets(conn, [_outlet(selection_basis=registry.SELECTED_FOR_CHINA + EVIDENCE),
                                      _outlet(id="gh_y", feeds=["https://y.example/feed/"])])["outlets"]
    assert out[0]["reach_selected"] is False and out[0]["selection_evidence"] == EVIDENCE
    assert out[0]["selection_basis"].startswith("selected for China coverage")
    assert out[1]["reach_selected"] is True and out[1]["selection_basis"] is None


def test_methodology_names_the_selection():
    text = methodology.selection_text({"selection_basis": {"china_selected_active": 2, "by_country": {"ZAF": 1, "GHA": 1}}})
    assert "2 active outlets in 2 countries" in text and "reach_selected" in text
    assert "No active outlet was selected this way" in methodology.selection_text({})
    assert "—" not in text and "–" not in text


# Added 2026-10-28 (small_africa_outlets): every feed validated with pipeline/validate_sources.check_outlet
# (the tracker's user agent, robots.txt honoured) and sample articles read without a paywall.
ADDED_2026_10_28 = {
    "NGA": ("ng_theniche", "ng_authority"),
    "ZWE": ("zw_zimbabwemail",),
    "UGA": ("ug_ubc",),
    "MOZ": ("mz_aim",),
    "ZAF": ("za_isolezwe",),
}


# Stress test of 2026-10-29: TheNiche's source (a news editor's interview with Freedom House) documents a view of
# the Xinhua content NAN supplies, not pro-China content or China coverage the outlet published, so it is taken
# out of monitoring; the other five stay active.
REMOVED_2026_10_29 = {"ng_theniche"}


def test_added_small_africa_outlets_registered():
    from pipeline import gate
    by_id = {o["id"]: o for o in registry.load_outlets()}
    langs = gate.supported_languages()
    for country, ids in ADDED_2026_10_28.items():
        for oid in ids:
            o = by_id[oid]
            assert o["country"] == country and o["active"] is (oid not in REMOVED_2026_10_29), oid
            assert o.get("collector") == "hosted", oid
            assert o["language"] in langs and o["language"] != "zh", oid
            assert not registry.reach_selected(o) and registry.selection_evidence(o), oid
            assert not o.get("audience_rank"), oid
            assert "Verified 2026-10-28" in o["notes"], oid
    summary = registry.selection_summary(registry.load_outlets())
    assert set(summary["ids"]) >= {i for ids in ADDED_2026_10_28.values() for i in ids} - REMOVED_2026_10_29
    assert not set(summary["ids"]) & REMOVED_2026_10_29


# --- stress test of 2026-10-29 ----------------------------------------------------------------------------------

def test_each_basis_names_what_its_source_documents():
    """The generic "selected for China coverage or documented pro-China content" said nothing an evidence
    page could be checked against; each basis now names what its source documents."""
    by_id = {o["id"]: o for o in registry.load_outlets()}
    for ids in ADDED_2026_10_28.values():
        for oid in ids:
            o = by_id[oid]
            basis, url = o["selection_basis"], registry.selection_evidence(o)
            assert basis.startswith("selected ") and basis.endswith("not reach; evidence: " + url), oid
            assert basis != registry.SELECTED_FOR_CHINA + url, oid
    removed = by_id["ng_theniche"]
    assert removed["active"] is False and removed["inactive_since"] == "2026-10-29"
    assert "evidence does not hold" in removed["inactive_reason"]
    assert "Independent Media, which published Isolezwe until July 2026" in by_id["za_isolezwe"]["selection_basis"]


def test_selection_warning_wording():
    one = registry.selection_warning(["Isolezwe"], 12, True)
    assert one.startswith("1 of the 12 active outlets monitored here (Isolezwe) was added for its China coverage")
    assert one.endswith("it carries no audience rank, so % of all its news leaves it out")
    two = registry.selection_warning(["b outlet", "A outlet"], 4, False)
    assert "(A outlet and b outlet) were added for their China coverage" in two
    assert two.endswith("this country has no audience ranks, so % of all its news counts them too")
    assert "—" not in one + two and "–" not in one + two


def _latest_with(tmp_path, outlets):
    import datetime as dt
    conn = store.connect(tmp_path / "w.db")
    day = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=2)).isoformat()
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    for o in outlets:
        conn.execute("""INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, consecutive_failures, newest_entry)
                        VALUES (?,?,?,?,?,?)""", (o["feeds"][0], o["id"], now, now, 0, now))
        conn.execute("INSERT INTO daily_outlet_discovery(date, outlet_id, country, discovered) VALUES (?,?,?,?)",
                     (day, o["id"], o["country"], 5))
    conn.commit()
    return export.build_latest(conn, outlets, [], population={})


def test_countries_with_a_selected_outlet_carry_the_warning(tmp_path):
    sel = registry.SELECTED_FOR_CHINA + EVIDENCE
    outlets = [
        _outlet(id="za_big", country="ZAF", feeds=["https://za-big.example/feed/"], audience_rank=1),
        _outlet(id="za_small", name="Small <Zulu>", country="ZAF", language="zu", feeds=["https://za-small.example/feed/"], selection_basis=sel),
        _outlet(id="gh_a", feeds=["https://gh-a.example/feed/"]),
        _outlet(id="gh_b", name="B", feeds=["https://gh-b.example/feed/"], selection_basis=sel),
        _outlet(id="gh_c", name="C", feeds=["https://gh-c.example/feed/"], selection_basis=sel),
        _outlet(id="gh_off", feeds=["https://gh-off.example/feed/"], selection_basis=sel, active=False, inactive_reason="test"),
        _outlet(id="fr_a", country="FRA", language="fr", feeds=["https://fr-a.example/feed/"]),
    ]
    latest = _latest_with(tmp_path, outlets)
    types = lambda iso: {w["type"]: w["text"] for w in latest["countries"][iso]["warnings"]}
    za, gh = types("ZAF"), types("GHA")
    assert za["china_selected"].startswith("1 of the 2 active outlets monitored here (Small <Zulu>) was added")
    assert za["china_selected"].endswith("leaves it out")          # South Africa has audience ranks
    assert gh["china_selected"].startswith("2 of the 3 active outlets monitored here (B and C) were added")
    assert gh["china_selected"].endswith("counts them too")         # no ranks: every active outlet counts
    assert "china_selected" not in types("FRA")
    assert all(w["type"] != "china_selected" for w in latest["countries"].get("WLD", {"warnings": []})["warnings"])


def test_methodology_says_where_the_site_flags_them():
    text = methodology.selection_text({"selection_basis": {"china_selected_active": 5, "by_country": {"NGA": 1, "ZAF": 1}}})
    assert text.startswith("Most outlets are chosen as each country's main outlets, ranked by audience where a readership source gives a rank")
    assert "carries a warning saying so in its country panel, the ranked list, the map tooltip and the CSV" in text
    assert "the top 10 marks it" in text and "taken out of monitoring" in text
    assert "Outlets are chosen by reach: the largest outlets" not in text


def test_site_wiring_and_smoke(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    app = (root / "docs" / "app.js").read_text(encoding="utf-8")
    assert "stakeLines(o) + selectionLine(o) + '</td>" in app
    assert "C.selectionTop10Note(" in app and "C.chinaSelectedCountry(" in app
    content = (root / "docs" / "content.json").read_text(encoding="utf-8")
    assert "some of its monitored outlets were added for their China coverage rather than their reach" in content
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    stub = ('var document = {readyState: "loading", addEventListener: function () {}, getElementById: function () { return null; },'
            ' querySelector: function () { return null; }, querySelectorAll: function () { return []; }};')
    files = [root / "docs" / "vendor" / "d3.v7.min.js", root / "docs" / "compute.js", root / "docs" / "app.js", root / "tests" / "smoke_selection.js"]
    shim = tmp_path / "shim.js"
    shim.write_text("const fs = require('fs'), vm = require('vm');\n"
                    "const ctx = {console: console, Date: Date}; ctx.self = ctx; ctx.window = ctx; vm.createContext(ctx);\n"
                    "vm.runInContext(%r, ctx);\n" % stub
                    + "".join("vm.runInContext(fs.readFileSync(%r, 'utf8'), ctx);\n" % str(f) for f in files), encoding="utf-8")
    r = subprocess.run([node, str(shim)], capture_output=True, text=True)
    assert r.returncode == 0 and "selection smoke ok" in r.stdout, r.stdout + r.stderr


@pytest.mark.xfail(strict=True, reason="gate gap found 2026-10-29: isiZulu joins a concord prefix to the noun "
                   "(yaseChina, eShayina), which the word boundary rule never matches; fixing it is a gate change "
                   "(GATE_VERSION), proposed in the stress_small_outlets report")
def test_isizulu_prefixed_china_is_matched():
    from pipeline import gate
    # From an Isolezwe article of 19 October 2026: "inkampani yezimoto yaseChina" (a car company of China).
    assert gate.check("Inkampani yezimoto yaseChina ifikile eNingizimu Afrika", "", "zu", "ZAF")[0]
