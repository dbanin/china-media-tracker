"""The doctrine term tracker: the generic matcher (pipeline/termlists.py), the term families
(pipeline/doctrine.yaml), the tagging pass, the side file, the hand check and the measure interface."""
import csv
import json
import time

import pytest
import yaml

from pipeline import config, doctrine, export, measures, store, termlists, themes


def t(title, lang, summary="", body=""):
    return doctrine.tag(title, summary, body, lang)


# ---------------------------------------------------------------------------
# The generic matcher
# ---------------------------------------------------------------------------

def test_termlists_copies_the_theme_word_pattern_and_acronym_test():
    for term in ("summit*", "foreign minister*", "win-win cooperation", "Ein-China-Prinzip*"):
        assert termlists.word_pattern(term) == themes._word_pattern(term)
    for term in ("BRI", "GSI", "G20", "AI", "Belt", "BRI Liga"):
        assert termlists.is_acronym(term) == bool(themes._ACRONYM.fullmatch(term))


def test_termlists_counts_occurrences_longest_first():
    ts = termlists.compile({"en": ["hegemonism", "hegemonism and power politics"]}, "en")
    assert ts.count("Hegemonism and power politics, and hegemonism again.") == 2
    found = ts.find("hegemonism and power politics")
    assert len(found) == 1 and found[0][2] == "hegemonism and power politics"
    assert ts.count("Hegemony is not hegemonisms") == 0


def test_termlists_language_rules_follow_themes():
    terms = {"all": ["BRI"], "en": ["Belt and Road"], "ko": ["일대일로"], "id": ["Satu Sabuk dan Satu Jalan"]}
    assert termlists.compile(terms, "de").count("Die Belt and Road Initiative") == 1     # en for every language
    assert termlists.compile(terms, "ko").count("중국일대일로사업") == 0                   # words, unless ...
    assert termlists.compile(terms, "ms", aliases={"ms": "id"}).count("Satu Sabuk dan Satu Jalan") == 1
    assert termlists.compile(terms, "ko", substring_langs=["ko"]).count("중국일대일로사업") == 1   # ... substring
    spaced = termlists.compile({"ko": ["인류 운명공동체"]}, "ko", substring_langs=["ko"])
    assert spaced.count("인류운명공동체 그리고 인류 운명공동체") == 2


def test_termlists_exclusions_drop_overlapping_matches():
    ts = termlists.compile({"all": ["BRI"], "en": ["Belt and Road"]}, "id",
                           exclude={"all": ["BRI Liga*", "Bank Rakyat Indonesia (BRI)"]})
    text = "Belt and Road. BRI Liga 1 dimulai. Bank Rakyat Indonesia (BRI) untung. BRI di Jakarta."
    terms = [f[2] for f in ts.find(text)]
    assert terms == ["Belt and Road", "BRI"]


def test_termlists_acronym_needs_a_full_term_and_its_own_when_required():
    ts = termlists.compile({"all": ["GDI", "GSI"], "en": ["Global Development Initiative", "Global Security Initiative"]},
                           "en", requires={"GSI": ["Global Security Initiative"]})
    assert ts.count("GDI and GSI were discussed") == 0
    assert ts.count("The Global Development Initiative (GDI) and the GSI") == 2       # GSI lacks its own full term
    assert ts.count("The Global Security Initiative (GSI)") == 2
    assert ts.count("the gdi") == 0                                                    # acronyms are case sensitive


# ---------------------------------------------------------------------------
# The families
# ---------------------------------------------------------------------------

def test_every_family_and_language_rule_in_the_yaml():
    data = doctrine.load()
    ids = [f["id"] for f in data["families"]]
    assert ids == ["shared_future", "global_initiatives", "belt_and_road", "win_win", "multipolarity", "hegemonism",
                   "cold_war_mentality", "true_multilateralism", "chinese_modernization", "one_china_principle",
                   "global_south", "people_to_people", "xi_thought"]
    muted = {f["id"] for f in data["families"] if not f["distinctive"]}
    assert muted == {"belt_and_road", "global_south"}
    theme_langs = sorted({l for th in themes.load()["themes"] for l in th["terms"] if l != "all"})
    assert sorted(data["languages"]) == theme_langs
    assert data["language_aliases"] == themes.load()["language_aliases"]
    assert data["substring_languages"] == themes.load()["substring_languages"]
    for f in data["families"]:
        assert set(f["terms"]) <= set(data["languages"]) | {"all"}, f["id"]
        assert f["terms"].get("en"), f["id"]


def test_every_foreign_term_cites_its_source():
    """Each non-English term carries the state media or foreign ministry page it was checked on."""
    lines = (config.ROOT / "pipeline" / "doctrine.yaml").read_text(encoding="utf-8").splitlines()
    lang = None
    for ln in lines:
        s = ln.strip()
        if s.endswith(":") and not s.startswith("-") and len(s) <= 5:
            lang = s[:-1]
        elif s.startswith("- ") and lang not in (None, "en", "all") and ln.startswith("    - "):
            assert "#" in s, ln
            assert "http" in s or "same page" in s, ln


def test_matching_per_language():
    assert t("Xi urges a community with a shared future for mankind", "en") == {"shared_future": 1}
    assert t("Xi defiende la comunidad de futuro compartido", "es") == {"shared_future": 1}
    assert t("Bâtir une communauté d’avenir partagé", "fr") == {"shared_future": 1}        # curly apostrophe
    assert t("принцип «одного Китая» и подлинной многосторонности", "ru") == {
        "one_china_principle": 1, "true_multilateralism": 1}
    assert t("مبادرة الحزام والطريق", "ar") == {"belt_and_road": 1}
    assert t("Globale Sicherheitsinitiative und Ein-China-Prinzips", "de") == {
        "global_initiatives": 1, "one_china_principle": 1}
    assert t("中国は「一つの中国」原則を堅持し中国式現代化を進める", "ja") == {
        "one_china_principle": 1, "chinese_modernization": 1}
    assert t("인류운명공동체와 글로벌 사우스", "ko") == {"shared_future": 1, "global_south": 1}
    assert t("Kuşak ve Yol Girişimi", "tr") == {"belt_and_road": 1}
    assert t("Centura şi Drumul", "ro") == t("Centura și Drumul", "ro") == {"belt_and_road": 1}
    assert t("Satu Sabuk dan Satu Jalan", "ms") == {"belt_and_road": 1}                     # ms reads id terms
    assert t("Belt and Road", "hi") == {"belt_and_road": 1}                                 # no local terms: English


def test_exclusions_and_phrase_only_families():
    assert t("BRI Liga 1: Persija menang", "id") == {}
    assert t("Bank Rakyat Indonesia (BRI) dan Belt and Road", "id") == {"belt_and_road": 1}
    assert t("The UK keeps its one China policy", "en") == {}
    assert t("Beijing restates the one-China principle", "en") == {"one_china_principle": 1}
    assert t("A win-win deal for both clubs", "en") == {}
    assert t("Win-win cooperation, Xi says", "en") == {"win_win": 1}
    assert t("US hegemony in the Pacific", "en") == {}
    assert t("Opposing hegemonism", "en") == {"hegemonism": 1}
    assert t("La hegemonía de Estados Unidos", "es") == {}
    assert t("A zero-sum game in the final", "en") == {}


def test_acronym_rule_in_the_families():
    assert t("GDI ranking published", "en") == {}
    assert t("BRI shares rise", "id") == {}
    assert t("The Global Development Initiative (GDI)", "en") == {"global_initiatives": 2}
    assert t("Global Development Initiative and the GSI", "en") == {"global_initiatives": 1}
    assert t("Global Security Initiative (GSI)", "en") == {"global_initiatives": 2}
    assert t("Belt and Road (BRI)", "en", body="BRI projects in Kenya") == {"belt_and_road": 3}


def test_occurrences_across_title_summary_and_body():
    assert t("Chinese modernization", "en", summary="<p>Chinese modernization</p>",
             body="Chinese modernisation and Chinese-style modernization.") == {"chinese_modernization": 4}


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------

def _article(conn, url, title, lang="en", country="ITA", outlet="o", day=None, cat=None, status="fetched"):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": country, "language": lang,
                                         "title": title, "status": status, "gate_relevant": 1})
    if day:
        conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (day + "T09:00:00+00:00", aid))
    if cat:
        store.insert_classification(conn, aid, "rules", cat, 1.0)
    conn.commit()
    return aid


def _tag(conn, aid):
    return json.loads(store.get_article(conn, aid)["doctrine"])


def test_ensure_keeps_a_body_tag_and_retags_when_the_body_arrives(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "d.db")
    aid = _article(conn, "https://x.test/1", "Xi on the Global South")
    h = store.get_article(conn, aid)["url_hash"]
    r = doctrine.ensure(conn)
    assert r["tagged"] == 1 and r["complete"]
    assert _tag(conn, aid) == {"v": doctrine.version(), "b": 0, "f": {"global_south": 1}}
    assert doctrine.ensure(conn)["tagged"] == 0                          # nothing new to read
    store.save_body(h, "He praised true multilateralism and the Global South twice: Global South.")
    r = doctrine.ensure(conn)
    assert r["tagged"] == 1 and r["retagged_with_body"] == 1
    assert _tag(conn, aid) == {"v": doctrine.version(), "b": 1, "f": {"global_south": 3, "true_multilateralism": 1}}
    store.body_path(h).unlink()                                          # the body goes missing again
    assert doctrine.ensure(conn)["tagged"] == 0
    assert _tag(conn, aid)["b"] == 1                                     # never replaced by a title-only tag
    monkeypatch.setattr(doctrine, "version", lambda: "next")
    assert doctrine.ensure(conn)["tagged"] == 1                          # another version is tagged again
    assert _tag(conn, aid) == {"v": "next", "b": 0, "f": {"global_south": 1}}


def test_ensure_stops_at_the_deadline_and_resumes_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "d.db")
    ids = [_article(conn, "https://x.test/%d" % i, "Belt and Road %d" % i) for i in range(3)]
    r = doctrine.ensure(conn, deadline=time.monotonic() - 1)
    assert r == {"tagged": 0, "with_body": 0, "retagged_with_body": 0, "complete": False}
    assert all(store.get_article(conn, i)["doctrine"] is None for i in ids)
    clock = iter([0.0, 0.0, 5.0, 5.0, 5.0])
    monkeypatch.setattr(doctrine.time, "monotonic", lambda: next(clock))
    r = doctrine.ensure(conn, deadline=1.0)
    assert r["tagged"] == 2 and not r["complete"]
    assert store.get_article(conn, ids[0])["doctrine"] is None          # the oldest waits for the next run
    monkeypatch.setattr(doctrine.time, "monotonic", lambda: 0.0)
    assert doctrine.ensure(conn, deadline=1.0)["tagged"] == 1


# ---------------------------------------------------------------------------
# The side file
# ---------------------------------------------------------------------------

def _outlets():
    return [{"id": "o", "name": "Outlet", "country": "ITA", "languages": ["it"], "tier": "national", "active": True},
            {"id": "wire", "name": "Wire", "country": "USA", "languages": ["en"], "tier": "distribution_wire",
             "active": True}]


def test_export_matches_build_daily_days_and_countries(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(doctrine, "family_statuses", lambda reviews_dir=None: {})
    conn = store.connect(tmp_path / "d.db")
    a = _article(conn, "https://x.test/a", "Xinhua: Chinese modernization and the Global South", day="2026-10-01", cat="A")
    _article(conn, "https://x.test/c", "Il Global South e la modernizzazione cinese", lang="it", day="2026-10-01", cat="C")
    _article(conn, "https://x.test/c2", "Nothing doctrinal here", day="2026-10-02", cat="C")
    _article(conn, "https://x.test/w", "Wire: true multilateralism", country="USA", outlet="wire", day="2026-10-02", cat="B")
    _article(conn, "https://x.test/n", "Not about China: Belt and Road", day="2026-10-02", cat="not_relevant")
    _article(conn, "https://x.test/p", "Pending: Xi Jinping Thought", day="2026-10-03", status="awaiting_llm")
    # A backfilled item counts on its day of record, as in build_daily.
    conn.execute("UPDATE articles SET day_of_record='2026-09-30' WHERE id=?", (a,))
    conn.commit()
    doctrine.ensure(conn)
    themes.ensure(conn)
    out = doctrine.build_export(conn, {"outlets": _outlets()})
    assert out["version"] == doctrine.version()
    assert [f["id"] for f in out["families"]] == [f["id"] for f in doctrine.catalog()]
    assert all(set(f) == {"id", "label", "distinctive", "status"} for f in out["families"])
    assert out["tagged_share"] == 1.0 and out["tagged_without_body"] == 1.0
    assert out["days"]["2026-09-30"]["ITA"] == {"chinese_modernization": [1, 1, 0, 0, 1], "global_south": [1, 1, 0, 0, 1]}
    assert out["days"]["2026-10-01"]["ITA"] == {"chinese_modernization": [1, 0, 0, 1, 1], "global_south": [1, 0, 0, 1, 1]}
    assert out["days"]["2026-10-02"] == {"WLD": {"true_multilateralism": [1, 0, 1, 0, 1]}}
    assert out["days"]["2026-10-03"]["ITA"] == {"xi_thought": [1, 0, 0, 0, 1]}            # pending: China only
    assert out["totals"]["2026-10-01"]["ITA"] == [1, 0, 0, 1, 1, 0, 0, 1]
    assert out["totals"]["2026-10-02"] == {"ITA": [1, 0, 0, 1, 0, 0, 0, 0], "WLD": [1, 0, 1, 0, 1, 0, 1, 0]}
    # Global South is muted: an article matching only it does not count as using the vocabulary.
    _article(conn, "https://x.test/g", "Only the Global South", day="2026-10-04", cat="C")
    doctrine.ensure(conn)
    out = doctrine.build_export(conn, {"outlets": _outlets()})
    assert out["totals"]["2026-10-04"]["ITA"] == [1, 0, 0, 1, 0, 0, 0, 0]
    # The same days and countries as the theme counts in build_daily.
    daily = export.build_daily(conn, _outlets())
    theme_cells = {(d, c) for m in daily.values() for d, day in m["days"].items() for c in day["themes"]}
    assert {(d, c) for d, per in out["totals"].items() for c in per} == theme_cells
    json.dumps(out)


def test_export_counts_only_tags_of_the_current_version(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(doctrine, "family_statuses", lambda reviews_dir=None: {})
    conn = store.connect(tmp_path / "d.db")
    aid = _article(conn, "https://x.test/a", "Chinese modernization", day="2026-10-01", cat="C")
    _article(conn, "https://x.test/b", "Chinese modernization", day="2026-10-01", cat="C")
    doctrine.ensure(conn)
    conn.execute("UPDATE articles SET doctrine=? WHERE id=?", (json.dumps({"v": "old", "b": 1, "f": {}}), aid))
    conn.commit()
    out = doctrine.build_export(conn, {"outlets": _outlets()})
    assert out["tagged_share"] == 0.5
    assert out["totals"]["2026-10-01"]["ITA"][0] == 1


# ---------------------------------------------------------------------------
# The hand check
# ---------------------------------------------------------------------------

def _coded(fams):
    """rows and key for {family: (correct, wrong)}."""
    rows, key = [], {"rows": {}}
    i = 0
    for fam, (yes, no) in fams.items():
        for v in ["yes"] * yes + ["no"] * no:
            i += 1
            sid = "d-%03d" % i
            rows.append({"sample_id": sid, "family": fam, "correct": v})
            key["rows"][sid] = {"family": fam, "language": "en"}
    return rows, key


def test_score_check_per_family():
    rows, key = _coded({"shared_future": (15, 0), "global_south": (9, 1), "hegemonism": (3, 2), "win_win": (13, 1)})
    r = doctrine.score_check(rows, key)
    fam = r["families"]
    assert fam["shared_future"]["status"] == "checked"            # 1.0 on 15
    assert fam["global_south"]["status"] == "provisional"         # 0.9 but only 10 coded
    assert fam["hegemonism"]["status"] == "withheld"              # 0.6 on 5
    assert fam["win_win"]["status"] == "provisional"              # 0.93 on 14
    assert r["statistic"] == "precision" and r["threshold"] == 0.9 and r["complete"]
    assert r["value"] == round(37 / 39, 4) and r["passed"] is True   # pooled over the families still shown
    rows[0]["correct"] = ""
    r = doctrine.score_check(rows, key)
    assert r["complete"] is False and r["passed"] is None
    assert all(f["status"] == "provisional" for f in r["families"].values())
    rows, key = _coded({"shared_future": (12, 3), "global_south": (8, 2)})
    r = doctrine.score_check(rows, key)
    assert r["families"]["shared_future"]["status"] == "provisional" and r["passed"] is False   # 0.8 pooled


def test_score_check_skips_unreadable_rows_and_reaches_the_gate(tmp_path, monkeypatch):
    rows, key = _coded({"shared_future": (15, 0)})
    rows.append({"sample_id": "d-099", "family": "shared_future", "correct": "unreadable"})
    key["rows"]["d-099"] = {"family": "shared_future"}
    r = doctrine.score_check(rows, key)
    assert r["coded"] == 16 and r["n"] == 16 and r["families"]["shared_future"]["coded"] == 15
    reviews = tmp_path / "reviews"
    c, k = measures.write_sample(reviews, "doctrine", rows, ["sample_id", "family", "correct"], key,
                                 version=doctrine.VERSION, seed=1, day="2026-10-20")
    status, check, _ = measures.assess(doctrine, "doctrine", reviews)
    assert status == "checked" and check["state"] == "passed"
    assert doctrine.family_statuses(reviews)["shared_future"]["status"] == "checked"
    monkeypatch.setattr(doctrine, "VERSION", "other")
    assert doctrine.family_statuses(reviews) == {}                 # a sample of another version is stale


def test_draw_sample_spreads_matches_and_shows_a_snippet(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(doctrine, "_outlet_names", lambda: {"o": "Outlet"})
    conn = store.connect(tmp_path / "d.db")
    for i in range(6):
        aid = _article(conn, "https://x.test/sf%d" % i, "Story %d" % i)
        store.save_body(store.get_article(conn, aid)["url_hash"],
                        " ".join("w%d" % j for j in range(50)) + " community with a shared future " +
                        " ".join("v%d" % j for j in range(50)))
    for i in range(2):
        _article(conn, "https://x.test/es%d" % i, "El Sur Global %d" % i, lang="es")
    doctrine.ensure(conn)
    c, k = doctrine.draw_sample(conn, 4, "seed", tmp_path / "out")
    rows = list(csv.DictReader(open(c, encoding="utf-8")))
    key = json.load(open(k, encoding="utf-8"))
    assert len(rows) == 4 and key["measure_id"] == "doctrine" and key["measure_version"] == doctrine.VERSION
    strata = {(key["rows"][r["sample_id"]]["family"], key["rows"][r["sample_id"]]["language"]) for r in rows}
    assert strata == {("shared_future", "en"), ("global_south", "es")}
    sf = next(r for r in rows if r["family"] == "shared_future")
    words = sf["snippet"].split()
    assert sf["matched_term"] == "community with a shared future" and "[[community" in sf["snippet"]
    assert words[0] == "w20" and words[-1] == "v29"                # 30 words either side
    assert sf["correct"] == "" and sf["outlet"] == "Outlet"
    again = doctrine.draw_sample(conn, 4, "seed", tmp_path / "out2")
    assert open(again[0], encoding="utf-8").read() == open(c, encoding="utf-8").read()
    assert len(list(csv.DictReader(open(doctrine.draw_sample(conn, 999, "s", tmp_path / "o3")[0], encoding="utf-8")))) == 8


# ---------------------------------------------------------------------------
# The measure interface
# ---------------------------------------------------------------------------

def test_measure_interface_history_and_method_text(tmp_path):
    for f in measures.REQUIRED:
        assert callable(getattr(doctrine, f)), f
    assert doctrine.MEASURE_ID == "doctrine" and doctrine.VERSION == doctrine.version()
    hist = doctrine.changes()
    assert hist[-1]["version"] == doctrine.version() and len({h["version"] for h in hist}) == len(hist)
    spec = next(m for m in measures.load_config() if m["id"] == "doctrine")
    assert spec["module"] == "pipeline.doctrine" and spec["enabled"]
    text = doctrine.methodology_text({"status": "provisional"})
    assert text and "—" not in text and "–" not in text and "Category" not in text
    assert all(h["change"] and "—" not in h["change"] for h in hist)
    conn = store.connect(tmp_path / "d.db")
    assert doctrine.inputs_present(conn) == 0
    s = doctrine.meta_summary(conn, {})
    assert s["families"] == 13 and s["tagged_share"] is None


def test_the_measure_runs_through_the_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "d.db")
    _article(conn, "https://x.test/a", "True multilateralism", day="2026-10-01", cat="C")
    written = {}
    blocks = measures.run_all_quietly(conn, tmp_path / "data", {"outlets": _outlets()},
                                      write_json=lambda p, d: written.__setitem__(p.name, d),
                                      reviews_dir=tmp_path / "reviews")
    b = blocks["doctrine"]
    assert b["status"] == "provisional" and b["file"] == "doctrine.json" and "error" not in b
    assert written["doctrine.json"]["days"]["2026-10-01"]["ITA"]["true_multilateralism"] == [1, 0, 0, 1, 1]
    assert b["computed"]["complete"] and b["summary"]["matched_articles"] == 1
