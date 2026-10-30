"""Hosted travel disclosures (pipeline/hosted_travel.py, D8): the patterns file, true positives in every
major language, hard negatives, the unnamed host class, the scan (incremental, resumable, rescans on a
new patterns version), the export and meta block, the methodology paragraph, that nothing here touches
a label, a category or the ruleset, and the site section. Every fixture text is written for these tests
(synthetic, no real article text). Temporary databases only."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from pipeline import classify_rules, config, export, hosted_travel as ht, store

ROOT = Path(__file__).resolve().parent.parent
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")
FILLER = "The port handled more cargo this year than last, the operator said in a statement on Monday.\n\n"


@pytest.fixture(scope="module")
def pat():
    return ht.load_patterns()


def _one(text, pat, china=True):
    return [(h["klass"], h["pattern_id"]) for h in ht.detect(text, pat, china_coverage=china)]


# ---------------------------------------------------------------------------
# Patterns file
# ---------------------------------------------------------------------------

def test_patterns_file_loads_and_covers_the_major_languages(pat):
    assert pat["version"]
    for lang in ("en", "fr", "es", "pt", "it", "de", "ar", "ru", "id"):
        assert lang in pat["languages"], lang
        assert any(a["lang"] == lang for a in pat["anchors"]), lang
    ids = [a["id"] for a in pat["anchors"]]
    assert len(ids) == len(set(ids))


def test_a_broken_patterns_file_is_refused(tmp_path):
    p = tmp_path / "h.yaml"
    p.write_text("version: '1'\nlanguages:\n  en:\n    self: x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ht.load_patterns(p)
    p.write_text("languages: {}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ht.load_patterns(p)


def test_the_flag_changes_no_ruleset_or_gate_version():
    """D8: a separate disclosure flag. The ruleset and the gate are as they were, and the patterns file
    is not part of the ruleset."""
    text = (ROOT / "pipeline" / "hosted_travel.py").read_text(encoding="utf-8")
    assert "RULESET_VERSION =" not in text and "GATE_VERSION =" not in text
    assert "hosted_travel" not in (ROOT / "pipeline" / "signatures.yaml").read_text(encoding="utf-8")
    assert "hosted_travel" not in (ROOT / "pipeline" / "classify_rules.py").read_text(encoding="utf-8")
    assert "hosted_travel" not in (ROOT / "pipeline" / "gate.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# True positives
# ---------------------------------------------------------------------------

POSITIVES = [
    ("en self", FILLER + "The writer travelled to Beijing as a guest of the Chinese Embassy in Nairobi.", "en_guest_of"),
    ("en closing line", FILLER + "Jane Doe travelled to Shanghai as a guest of the All-China Journalists Association.", "en_guest_of"),
    ("en label", "Disclosure: Travel and accommodation were provided by the China Public Diplomacy Association.\n" + FILLER, "en_trip_paid_by"),
    ("en programme", FILLER + "The author was a participant in the China-Africa Press Centre programme in Beijing.", "en_participant"),
    ("en reverse", FILLER + "The Chinese Embassy paid for the reporter's trip.", "en_reverse_paid"),
    ("en courtesy", FILLER + "This reporter travelled to Urumqi on a press tour organised by the State Council Information Office.", "en_travelled_courtesy"),
    ("en state media", FILLER + "The trip was paid for by CGTN.", "en_trip_paid_by"),
    ("fr", FILLER + "Ce voyage a été financé par l'ambassade de Chine à Paris.", "fr_voyage_finance"),
    ("fr invitation", "Notre envoyée spéciale s'est rendue à Pékin à l'invitation du gouvernement chinois.", "fr_invitation"),
    ("es", "La periodista viajó a China por invitación de la Embajada de China en Lima.", "es_invitacion"),
    ("es paid", FILLER + "El viaje fue financiado por el gobierno chino.", "es_viaje_financiado"),
    ("pt", FILLER + "O repórter viajou a convite do governo chinês.", "pt_convite"),
    ("it", "L'inviato ha viaggiato in Cina su invito dell'Ambasciata cinese.", "it_invito"),
    ("de", FILLER + "Transparenzhinweis: Die Reise wurde von der chinesischen Botschaft bezahlt.", "de_reise_bezahlt"),
    ("ar", "زار الكاتب الصين بدعوة من السفارة الصينية في القاهرة.", "ar_dawa"),
    ("ru", "Корреспондент побывал в Китае по приглашению посольства КНР.", "ru_priglashenie"),
    ("id", "Penulis berkunjung ke Tiongkok atas undangan Kedutaan Besar Tiongkok di Jakarta.", "id_undangan"),
    ("ms", "Lawatan wartawan ini ditaja oleh Kedutaan Besar China.", "id_dibiayai_oleh"),
    ("nl", "De verslaggever reisde op uitnodiging van de Chinese ambassade.", "nl_uitnodiging"),
    ("tr", "Muhabirimiz Çin Büyükelçiliğinin davetlisi olarak Pekin'e gitti.", "tr_davet"),
    ("zh", "记者应中国外交部邀请赴华采访。", "zh_ying_yaoqing"),
]


@pytest.mark.parametrize("name,text,pattern", POSITIVES, ids=[p[0] for p in POSITIVES])
def test_true_positives(pat, name, text, pattern):
    hits = ht.detect(text, pat)
    assert ("state_host", pattern) in [(h["klass"], h["pattern_id"]) for h in hits], (name, hits)
    assert ht.article_class(hits) == "state_host"
    h = [x for x in hits if x["pattern_id"] == pattern][0]
    assert h["basis"] in ht.BASES and h["host"] and h["evidence"]


def test_unnamed_host_is_its_own_weaker_class(pat):
    text = FILLER + "The reporter travelled to Xinjiang as a guest of the organisers."
    assert _one(text, pat) == [("unnamed_host", "en_guest_of")]
    assert _one(text, pat, china=False) == [], "unnamed hosts only on China coverage"
    assert _one("La autora viajó a Shanghái invitada por los organizadores.", pat) == [("unnamed_host", "es_invitacion")]
    both = text + "\nThe trip was paid for by the Chinese government."
    assert ht.article_class(ht.detect(both, pat)) == "state_host", "a state host outranks an unnamed one"


# ---------------------------------------------------------------------------
# Hard negatives
# ---------------------------------------------------------------------------

NEGATIVES = [
    ("a named host that is not a state body", FILLER + "The writer travelled to China as a guest of the tournament."),
    ("a company", FILLER + "The trip was sponsored by Huawei."),
    ("another country's tourism board", FILLER + "The writer travelled to Sydney as a guest of Tourism Australia."),
    ("another government", FILLER + "The writer travelled to Nairobi as a guest of the government of Kenya."),
    ("generic sponsorship, not travel", FILLER + "This article is sponsored by China Daily."),
    ("generic sponsorship in French", FILLER + "Contenu sponsorisé par l'ambassade de Chine."),
    ("a politician's visit in the news", "President Ruto travelled to Beijing as a guest of the Chinese government last week, officials said, and signed deals worth billions."),
    ("a delegation's visit in the closing line", FILLER + "The working visit, at the invitation of the Chinese government, is the prime minister's fourth to the country."),
    ("a quote", FILLER + '"We came here as guests of the Chinese government," said the minister.'),
    ("a programme in the news, no disclosure", FILLER + "The programme was funded by the Chinese government."),
    ("a news sentence mid body", "Officials said the students travelled to Beijing at the invitation of the Chinese Embassy, which also covered the costs of the trip for all forty of them over the summer.\n\n" + FILLER + FILLER),
    ("Spanish in charge of, not paid by", FILLER + "La investigación está a cargo de la Embajada de China, dijo el fiscal."),
    ("a host after a relative clause", FILLER + "The writer travelled to China as a guest of the tournament, which was broadcast by CGTN."),
    ("German supported by without a trip", FILLER + "Hinweis: Die Ausstellung wird unterstützt von der chinesischen Botschaft."),
    ("no trigger word at all", FILLER * 3),
]


@pytest.mark.parametrize("name,text", NEGATIVES, ids=[n[0] for n in NEGATIVES])
def test_hard_negatives(pat, name, text):
    assert ht.detect(text, pat) == [], name


def test_the_state_check_reuses_has_state_entity(pat):
    # Every organisation has_state_entity knows is a state host here too.
    for org in ("Xinhua", "the Confucius Institute", "China Media Group", "the Consulate General of the People's Republic of China"):
        assert classify_rules.has_state_entity(org)
        assert ht.state_entity(org, pat), org
    assert ht.state_entity("the Chinese government", pat) and not classify_rules.has_state_entity("the Chinese government")
    assert ht.state_entity("the tournament", pat) is None and ht.state_entity("", pat) is None


# ---------------------------------------------------------------------------
# Scan, export, meta
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "t.db")
    conn.execute("INSERT INTO outlets(id, name, country, language, tier, active, feeds, updated_at) "
                 "VALUES ('o1','Outlet One','KEN','en','national',1,'[]','2026-10-28')")
    conn.commit()
    return conn


def _article(conn, i, body=None, country="KEN", outlet="o1", relevant=1):
    aid = store.insert_discovered(conn, {"url": "https://x.test/%s" % i, "outlet_id": outlet, "country": country, "language": "en",
                                         "title": "Headline %s" % i, "status": "fetched", "gate_relevant": relevant})
    if body is not None:
        row = store.get_article(conn, aid)
        store.update_article(conn, aid, body_hash=store.save_body(row["url_hash"], body), body_chars=len(body), fetched_at=store.utcnow())
    conn.commit()
    return aid


STATE_BODY = FILLER + "The writer travelled to Beijing as a guest of the Chinese Embassy."
UNNAMED_BODY = FILLER + "The reporter travelled to Xinjiang as a guest of the organisers."


def test_scan_flags_exports_and_never_touches_labels(db):
    a1 = _article(db, 1, STATE_BODY)
    a2 = _article(db, 2, UNNAMED_BODY, country="MYS")
    _article(db, 3, FILLER)
    _article(db, 4, None)
    labels_before = db.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
    c = ht.scan(db)
    assert c["flagged"] == 2 and c["state_host"] == 1 and c["unnamed_host"] == 1 and c["full"] is True
    assert db.execute("SELECT COUNT(*) FROM classifications").fetchone()[0] == labels_before
    ex = ht.build_export(db)
    assert ex["status"] == "provisional" and ex["state_host"] == 1 and ex["unnamed_host"] == 1 and ex["articles_total"] == 2
    assert ex["by_country"] == {"KEN": {"state_host": 1, "unnamed_host": 0}, "MYS": {"state_host": 0, "unnamed_host": 1}}
    assert ex["by_outlet"]["o1"]["name"] == "Outlet One" and ex["by_outlet"]["o1"]["state_host"] == 1
    rows = {r["article_id"]: r for r in ex["articles"]}
    assert rows[a1]["klass"] == "state_host" and rows[a1]["title"] == "Headline 1" and rows[a1]["url"] == "https://x.test/1"
    assert rows[a1]["hosts"] == ["Chinese Embassy"] and "guest of the Chinese Embassy" in rows[a1]["evidence"]
    assert rows[a2]["klass"] == "unnamed_host"
    m = ht.meta_block(db)
    assert m["state_host"] == 1 and m["unnamed_host"] == 1 and m["state_host_30d"] == 1 and m["countries"] == 2 and m["outlets"] == 1
    assert "generated" not in m, "no clock value in meta, so two exports of one database agree"


def test_scan_is_incremental_and_rescans_on_a_new_version(db, tmp_path):
    _article(db, 1, STATE_BODY)
    assert ht.scan(db)["scanned"] == 1
    assert ht.scan(db)["scanned"] == 0
    _article(db, 2, STATE_BODY)
    c = ht.scan(db)
    assert c["scanned"] == 1 and c["full"] is False
    p = tmp_path / "v2.yaml"
    p.write_text((ROOT / "pipeline" / "hosted_travel.yaml").read_text(encoding="utf-8").replace(
        'version: "%s"' % ht.load_patterns()["version"], 'version: "9999.1"'), encoding="utf-8")
    c = ht.scan(db, pat=ht.load_patterns(p))
    assert c["full"] is True and c["scanned"] == 2 and c["flagged"] == 2
    assert db.execute("SELECT COUNT(*) FROM hosted_travel_flags").fetchone()[0] == 2, "a rescan replaces, never duplicates"


def test_scan_stops_at_the_deadline_and_resumes(db):
    for i in range(3):
        _article(db, i, STATE_BODY)
    c = ht.scan(db, deadline=0.0)
    assert c["stopped_at_deadline"] is True and c["scanned"] == 0
    assert ht.scan(db)["scanned"] == 3


def test_a_broken_scan_is_a_count_not_a_stop(db, monkeypatch):
    monkeypatch.setattr(ht, "patterns", lambda path=None: (_ for _ in ()).throw(ValueError("broken")))
    assert "error" in ht.scan_quietly(db)
    counts = {}
    ht.stage_scan(db, counts, deadline=None)
    assert "error" in counts["hosted_travel"]
    assert ht.build_export_quietly(db)["articles"] == [] and ht.meta_block_quietly(db)["articles_total"] == 0


def test_export_writes_the_side_file_and_meta_block(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    db = tmp_path / "e.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    conn = store.connect(db)
    _article(conn, 1, STATE_BODY)
    out = tmp_path / "site" / "data"
    counts = export.run(conn, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M.md", prune=False)
    assert counts["hosted_travel"]["flagged"] == 1
    side = json.loads((out / "hosted_travel.json").read_text())
    assert side["state_host"] == 1 and side["articles"][0]["klass"] == "state_host"
    meta = json.loads((out / "meta.json").read_text())
    assert meta["hosted_travel"]["state_host"] == 1 and meta["hosted_travel"]["status"] == "provisional"
    text = (tmp_path / "M.md").read_text()
    assert "## Hosted travel disclosures" in text and "It is not state origin" in text
    assert "1 article carried a disclosure with a state host" in text


def test_methodology_paragraph(pat):
    t = ht.methodology_text({})
    assert "not state origin" in t and "provisional" in t and "proves nothing" in t
    assert "—" not in t and "–" not in t
    t2 = ht.methodology_text({"hosted_travel": {"state_host": 3, "unnamed_host": 1, "countries": 2, "outlets": 2,
                                                "state_host_30d": 1, "unnamed_host_30d": 0, "patterns_version": "x"}})
    assert "3 articles carried a disclosure with a state host and 1 with an unnamed host" in t2 and "in 2 countries and 2 outlets" in t2


# ---------------------------------------------------------------------------
# Site
# ---------------------------------------------------------------------------

def test_site_section_and_script():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    for anchor in ('id="hosted-travel"', 'id="hosted-travel-intro"', 'id="hosted-travel-body"', 'href="#hosted-travel"'):
        assert anchor in html, anchor
    assert html.index('id="planted"') < html.index('id="hosted-travel"')
    assert 'src="hosted_travel.js?v=' in html
    assert "not state origin" in html[html.index('id="hosted-travel"'):html.index('id="hosted-travel-body"')]


def _js_smoke():
    smoke = ROOT / "tests" / "hosted_travel_smoke.js"
    if shutil.which("node"):
        return subprocess.run(["node", str(smoke)], capture_output=True, text=True)
    shim = "\n".join([
        'var console = {log: function (s) { print(String(s)); }, error: function (s) { print("ERROR " + String(s)); }};',
        'var process = {exit: function (c) { throw new Error("process.exit(" + c + ")"); }};',
        "var module = {exports: {}};",
        'load("%s"); var __h = module.exports;' % (ROOT / "docs" / "hosted_travel.js"),
        "function require(p) { return __h; }",
        'load("%s");' % smoke,
    ])
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "shim.js"
        f.write_text(shim, encoding="utf-8")
        return subprocess.run([str(JSC), str(f)], capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None and not JSC.exists(), reason="no JavaScript engine")
def test_js_smoke():
    r = _js_smoke()
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "hosted travel smoke ok" in out and "FAIL" not in out, out


# ---------------------------------------------------------------------------
# Stress test of 2026-10-29: a larger synthetic set, cost, Unicode, resumption, D8 invariance
# ---------------------------------------------------------------------------

import time  # noqa: E402

from tests import hosted_travel_corpus as corpus  # noqa: E402


def _rates(pat, positives, negatives, windows=True):
    per = {}
    for lang, pos, line in positives:
        p = per.setdefault(lang, {"tp": 0, "fn": 0, "fp": 0, "tn": 0})
        hit = ht.article_class(ht.detect(corpus.body(lang, pos, line), pat, windows=windows)) == "state_host"
        p["tp" if hit else "fn"] += 1
    for lang, pos, line in negatives:
        p = per.setdefault(lang, {"tp": 0, "fn": 0, "fp": 0, "tn": 0})
        p["fp" if ht.detect(corpus.body(lang, pos, line), pat, windows=windows) else "tn"] += 1
    return per


def test_the_corpus_is_large_and_covers_17_languages():
    assert len(corpus.LANGUAGES) == 17
    assert len(corpus.POSITIVES) >= 150 and len(corpus.NEGATIVES) >= 150
    for lang in corpus.LANGUAGES:
        assert sum(1 for p in corpus.POSITIVES if p[0] == lang) >= 8, lang
        assert sum(1 for p in corpus.NEGATIVES if p[0] == lang) >= 8, lang
        assert sum(1 for p in corpus.HELDOUT_POSITIVES if p[0] == lang) >= 3, lang
    assert {p[1] for p in corpus.POSITIVES} == set(corpus.POSITIONS)
    text = (ROOT / "tests" / "hosted_travel_corpus.py").read_text(encoding="utf-8")
    assert "\u2014" not in text and "\u2013" not in text


@pytest.mark.parametrize("which", ["development", "held out"])
def test_precision_and_recall_per_language(pat, which):
    """Every disclosure with a state host is found and no hard negative is flagged, per language.
    The held-out lists were written after the patterns were fitted to the development lists; on their
    first run (2026-10-29) they gave precision 1.000 and recall 59 of 71."""
    P, N = ((corpus.POSITIVES, corpus.NEGATIVES) if which == "development"
            else (corpus.HELDOUT_POSITIVES, corpus.HELDOUT_NEGATIVES))
    per = _rates(pat, P, N)
    misses = {k: v for k, v in per.items() if v["fn"] or v["fp"]}
    assert not misses, misses


def test_windows_find_what_a_full_read_finds(pat):
    """Anchors run only near their own language's trigger words; on every corpus text that gives the
    same flags as running every anchor over the whole text (so no anchor lacks its trigger)."""
    for lang, pos, line in corpus.POSITIVES + corpus.NEGATIVES + corpus.HELDOUT_POSITIVES + corpus.HELDOUT_NEGATIVES:
        text = corpus.body(lang, pos, line)
        a = [(h["klass"], h["pattern_id"], h["basis"]) for h in ht.detect(text, pat)]
        b = [(h["klass"], h["pattern_id"], h["basis"]) for h in ht.detect(text, pat, windows=False)]
        assert a == b, (lang, line, a, b)


def test_subnational_governments_need_a_chinese_place(pat):
    for host in ("the Western Cape Provincial Government", "the Government of Punjab Province",
                 "the Department of Culture and Tourism Abu Dhabi", "the Bali Provincial Government"):
        assert ht.state_entity(host, pat) is None, host
    for host in ("the Yunnan Provincial Foreign Affairs Office", "the Chongqing Municipal People's Government",
                 "the Guangxi Zhuang Autonomous Region government", "the Shanghai Municipal Information Office"):
        assert ht.state_entity(host, pat), host
    assert ht.state_entity("CMG", pat, "The journalist was hosted in Beijing by CMG.") == "CMG"
    assert ht.state_entity("CMG", pat, "The trip was paid for by CMG.") is None, "CMG alone is also a Toronto ticker"


@pytest.mark.parametrize("text", [
    "The writer travelled to Beijing as a guest of the Chinese Embassy.",
    "The writer travelled to Beijing as a guest​ of the Chinese­ Embassy.",
    "Ce reportage a été réalisé à l'invitation de l'ambassade de Chine.",
    "‏زار الكاتب الصين بدعوة من السفارة الصينية.‏",
    "⁧زار الكاتب الصين بدعوة من السفارة الصينية.⁩",
    "İSTANBUL. Muhabirimiz, Çin Büyükelçiliğinin davetlisi olarak Pekin'e gitti.",
], ids=["nbsp", "zero width and soft hyphen", "decomposed accents", "right to left marks", "bidi isolates", "dotted capital I"])
def test_unicode_and_right_to_left_text(pat, text):
    assert ht.article_class(ht.detect(text, pat)) == "state_host", ht.detect(text, pat)


def test_long_bodies_are_cheap_and_capped(pat):
    filler = ("The port handled more cargo this year than last, the operator said. Officials were invited to the "
              "ceremony and the guest list was long; support was provided by sponsors. ")
    end = "\n\nThe writer travelled to Beijing as a guest of the Chinese Embassy."
    body = filler * (2_000_000 // len(filler)) + end
    t = time.perf_counter()
    assert ht.article_class(ht.detect(body, pat)) == "state_host"
    assert time.perf_counter() - t < 10, "a 2 MB body full of trigger words was 48 s before the windows"
    plain = "The port handled more cargo this year than last, the operator said on Monday. " * 60 + end
    t = time.perf_counter()
    for _ in range(50):
        ht.detect(plain, pat)
    assert (time.perf_counter() - t) / 50 < 0.05
    lost = "x" * (ht.BODY_HEAD + 20_000) + end + "\n\n" + "y" * (ht.BODY_TAIL + 10)
    assert ht.detect(lost, pat) == [], "the middle of an overlong body is not read, as the methodology says"
    for junk in ("guest invited sponsored " * 50_000, "안 " * 200_000 + "초청", "应" * 100_000 + "邀请"):
        t = time.perf_counter()
        ht.detect(junk, pat)
        assert time.perf_counter() - t < 10


def test_a_stop_never_skips_a_refetched_row(db):
    """Regression: a deadline stop before the re-fetched older rows used to move last_fetched_at past
    them, so their new bodies were never read."""
    a1 = _article(db, 1, FILLER)
    _article(db, 2, FILLER)
    assert ht.scan(db)["scanned"] == 2
    row = store.get_article(db, a1)
    store.update_article(db, a1, body_hash=store.save_body(row["url_hash"], STATE_BODY), fetched_at="2999-01-01T00:00:00+00:00")
    db.commit()
    c = ht.scan(db, deadline=0.0)
    assert c["stopped_at_deadline"] and c["scanned"] == 0 and c["bodies_left"] == 1
    assert ht.scan_status(db) == {"scan_complete": False, "bodies_left": 1, "bodies_stored": 2, "bodies_read": 2}
    c = ht.scan(db)
    assert c["scanned"] == 1 and c["state_host"] == 1
    assert ht.scan_status(db)["scan_complete"] is True


def test_a_rescan_drops_flags_whose_body_is_gone(db, tmp_path):
    a1 = _article(db, 1, STATE_BODY)
    assert ht.scan(db)["state_host"] == 1
    store.update_article(db, a1, body_hash=None)
    db.commit()
    p = tmp_path / "v.yaml"
    p.write_text(ht.PATTERNS_PATH.read_text(encoding="utf-8").replace(
        'version: "%s"' % ht.load_patterns()["version"], 'version: "9999.2"'), encoding="utf-8")
    ht.scan(db, pat=ht.load_patterns(p))
    assert db.execute("SELECT COUNT(*) FROM hosted_travel_flags").fetchone()[0] == 0


def test_the_export_scan_has_a_budget_and_says_when_it_is_not_done(db, monkeypatch):
    for i in range(3):
        _article(db, i, STATE_BODY)
    monkeypatch.setattr(ht, "EXPORT_SCAN_SECONDS", -1)
    c = ht.export_scan(db)
    assert c["stopped_at_deadline"] is True and c["bodies_left"] == 3
    m, ex = ht.meta_block(db), ht.build_export(db)
    assert m["scan_complete"] is False and m["bodies_left"] == 3 and m["bodies_stored"] == 3 and m["bodies_read"] == 0
    assert ex["scan_complete"] is False and ex["bodies_left"] == 3
    assert "had not yet read 3 of the 3 stored bodies" in ht.methodology_text({"hosted_travel": m})
    monkeypatch.setattr(ht, "EXPORT_SCAN_SECONDS", 240)
    ht.export_scan(db)
    assert ht.meta_block(db)["scan_complete"] is True and ht.meta_block(db)["state_host"] == 3 and ht.meta_block(db)["bodies_read"] == 3


def test_huge_and_empty_side_files(db):
    for i in range(ht.FLAGS_EXPORT_CAP + 20):
        _article(db, i, STATE_BODY, country=("KEN", "MYS", "PER")[i % 3])
    ht.scan(db)
    ex = ht.build_export(db)
    assert ex["articles_total"] == ht.FLAGS_EXPORT_CAP + 20 and len(ex["articles"]) == ht.FLAGS_EXPORT_CAP
    assert sum(v["state_host"] for v in ex["by_country"].values()) == ex["state_host"] == ex["articles_total"]
    json.dumps(ex)
    e = ht.empty_export()
    assert e["articles"] == [] and e["scan_complete"] is False and set(ht.empty_meta_block()) == set(ht.meta_block(db))


_CLOCK_KEYS = ("generated", "generated_at", "hosted_travel", "seconds", "last_successful_run", "last_runs")


def _strip_clock(v):
    if isinstance(v, dict):
        return {k: _strip_clock(x) for k, x in v.items() if k not in _CLOCK_KEYS}
    if isinstance(v, list):
        return [_strip_clock(x) for x in v]
    return v


def test_d8_labels_and_counts_are_identical_with_and_without_the_flag(tmp_path, monkeypatch):
    """D8: every data file but hosted_travel.json, and every audit file, is the same whether the flag
    finds disclosures or not, and the labels in the database are untouched. Two identical databases
    (fixed times), one exported with the detector finding nothing, one with it working."""
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    labels = ["A", "A", "C", "B"]
    bodies = [STATE_BODY, FILLER, UNNAMED_BODY, FILLER + "Disclosure: The trip was paid for by Xinhua."]
    when = "2026-10-20T08:00:00+00:00"

    def build(tag):
        path = tmp_path / ("%s.db" % tag)
        conn = store.connect(path)
        conn.execute("INSERT INTO outlets(id, name, country, language, tier, active, feeds, updated_at) "
                     "VALUES ('o1','Outlet One','KEN','en','national',1,'[]','2026-10-28')")
        for i, (label, body) in enumerate(zip(labels, bodies)):
            aid = _article(conn, i, body)
            store.insert_classification(conn, aid, "rules", label, 1.0)
            conn.execute("UPDATE articles SET discovered_at=?, published_at=?, fetched_at=? WHERE id=?", (when, when, when, aid))
        conn.execute("UPDATE classifications SET classified_at=?", (when,))
        conn.commit()
        return path, conn

    def run(tag):
        path, conn = build(tag)
        monkeypatch.setattr(config, "DB_PATH", path)
        out, audit = tmp_path / tag / "data", tmp_path / tag / "audit"
        counts = export.run(conn, "test", export_dir=out, audit_dir=audit, methodology_path=tmp_path / tag / "M.md", prune=False)
        files = {}
        for root in (out, audit):
            for f in sorted(root.rglob("*")):
                if f.is_file() and f.name != "hosted_travel.json":
                    raw = f.read_text(encoding="utf-8")
                    try:
                        files[str(f.relative_to(tmp_path / tag))] = _strip_clock(json.loads(raw))
                    except ValueError:
                        files[str(f.relative_to(tmp_path / tag))] = [_strip_clock(json.loads(x)) if x.startswith("{") else x
                                                                    for x in raw.splitlines()]
        labels_now = [tuple(r) for r in conn.execute("SELECT article_id, category, is_current FROM classifications ORDER BY id")]
        return counts, files, json.loads((out / "hosted_travel.json").read_text()), labels_now

    with monkeypatch.context() as mp:
        mp.setattr(ht, "detect", lambda *a, **k: [])
        c_off, off, side_off, labels_off = run("off")
    c_on, on, side_on, labels_on = run("on")
    assert side_off["articles_total"] == 0 and side_on["state_host"] == 2 and side_on["unnamed_host"] == 1
    assert set(off) == set(on)
    diff = [k for k in on if on[k] != off[k]]
    assert not diff, diff
    for k in c_on:
        if k != "hosted_travel":
            assert c_on[k] == c_off[k], k
    assert labels_on == labels_off == [(i + 1, label, 1) for i, label in enumerate(labels)]


def test_evidence_drops_italic_markers_and_the_language_count_is_codes(pat):
    h = ht.detect(FILLER + "*The writer travelled to Beijing as a guest of the Chinese Embassy.*", pat)
    assert h and h[0]["evidence"] == "The writer travelled to Beijing as a guest of the Chinese Embassy."
    assert pat["language_codes"] == sorted(set(pat["languages"]) | {"ms", "sr"}) and len(pat["language_codes"]) == 18
