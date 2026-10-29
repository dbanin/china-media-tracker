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
    ("ms", "Lawatan wartawan ini ditaja oleh Kedutaan Besar China.", "id_undangan"),
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
    assert "1 articles carried a disclosure with a state host" in text


def test_methodology_paragraph(pat):
    t = ht.methodology_text({})
    assert "not state origin" in t and "provisional" in t and "proves nothing" in t
    assert "—" not in t and "–" not in t
    t2 = ht.methodology_text({"hosted_travel": {"state_host": 3, "unnamed_host": 1, "countries": 2, "outlets": 2,
                                                "state_host_30d": 1, "unnamed_host_30d": 0, "patterns_version": "x"}})
    assert "3 articles carried a disclosure with a state host and 1 with an unnamed host" in t2


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
