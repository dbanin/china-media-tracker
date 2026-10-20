"""Regressions for ruleset 2026.10.2 and gate 2026.10.2, from the label accuracy audit of 2026-10-16.

Disclosures in page chrome, BRI and the territories' names in the residual rule, model only state origin
labels as lookalike references, an AI glossary read as a wire credit, a reclassification queue that never
reached old rules labels, and model routes inherited from candidate signatures. The sponsored placement
and wire credit cases with real stored text are in tests/fixtures/rules/cases.yaml."""
import json
from pathlib import Path

import pytest

from pipeline import classify_rules as cr, config, gate, lookalike, store

FIX = Path(__file__).parent / "fixtures" / "rules"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    return store.connect(tmp_path / "r.db")


def _article(conn, n, title="A story", body=None, outlet="o", lang="en", country="USA", **extra):
    url = "https://e.test/%d" % n
    aid = store.insert_discovered(conn, dict({"url": url, "outlet_id": outlet, "country": country, "language": lang,
                                              "title": title, "status": "fetched", "gate_relevant": 1}, **extra))
    if body is not None:
        h = store.save_body(store.url_hash(url), body)
        store.update_article(conn, aid, body_hash=h, body_chars=len(body), extract_version=config.EXTRACT_VERSION)
    conn.commit()
    return aid


# --- BRI at the gate and in the residual rule --------------------------------------------------------

@pytest.mark.parametrize("title,summary,lang", [
    ("BRI Edukasi Menabung Sejak Dini Lewat Mobile Legends: Bang Bang",
     "Lewat kolaborasi dengan Mobile Legends: Bang Bang, BRI memanfaatkan pendekatan populer untuk mengenalkan "
     "kebiasaan mengelola uang kepada anak dan remaja.", "id"),          # Bank Rakyat Indonesia, item 2720796
    ("Persib tekuk Persija di BRI Super League", "Laga pekan kedelapan BRI Super League berakhir 2-1.", "id"),
    ("Paris : la BRI interpelle trois suspects", "Les policiers de la BRI sont intervenus dans la nuit.", "fr"),
    ("ธนาคารเปิดตัวบัตร BRI รุ่นใหม่", "", "th"),
])
def test_bri_alone_does_not_pass_the_gate(title, summary, lang):
    ok, terms = gate.check(title, summary, lang, "IDN")
    assert not ok and "BRI" in terms


@pytest.mark.parametrize("title,summary", [
    ("Indonesia dan China bahas proyek BRI", ""),
    ("BRI projects stall", "Lending under the Belt and Road Initiative fell again."),
])
def test_bri_beside_another_china_term_passes(title, summary):
    assert gate.check(title, summary, "id", "IDN")[0]


def test_a_bank_story_on_bri_is_not_china_coverage_by_rules():
    """Real stored body of item 2720796, labelled independent journalism by the rules on BRI alone."""
    body = (FIX / "cnnindonesia_bri_bank.txt").read_text()
    title = "BRI Edukasi Menabung Sejak Dini Lewat Mobile Legends: Bang Bang"
    assert cr.body_relevance(title, body, "id", "IDN") == (False, "0 distinct China terms, 0 occurrences")


def test_bri_far_from_china_counts_only_the_china_term():
    filler = "Laba bersih bank naik tajam pada kuartal ketiga tahun ini. " * 5   # 300 characters, no term
    body = "BRI melaporkan laba. " * 3 + filler + "Pasar saham China melemah."
    distinct, total, _ = gate.count("Bank catat laba", body, "id", "IDN", residual=True)
    assert (distinct, total) == (1, 1)
    near = "China mendanai proyek BRI di Jawa. BRI disebut proyek besar."
    assert gate.count("", near, "id", "IDN", residual=True)[0] == 2


# --- Hong Kong, Macao and Taiwan in the residual rule ---------------------------------------------------

@pytest.mark.parametrize("title,body", [
    ("Kitchee beat Eastern in the Hong Kong Premier League",
     "Hong Kong champions Kitchee beat Eastern 2-0. The Hong Kong FA said the derby drew a record crowd at "
     "Hong Kong Stadium. Hong Kong football is enjoying a revival."),
    ("LEGOLAND Hong Kong Resort opens its doors",
     "LEGOLAND Hong Kong opened on Tuesday in Tuen Mun. Hong Kong families queued from dawn. Hong Kong "
     "tourism officials expect a million visitors."),
    ("Pankki voitti Taiwan-tarjouskilpailun",
     "Suomalainen pankki voitti Taiwan-tarjouskilpailun. Taiwan valitsi pankin. Taiwan ja pankki allekirjoittivat. "
     "Taiwan, pankki ja Taiwan."),
])
def test_a_territory_story_with_no_mainland_term_is_not_relevant(title, body):
    relevant, why = cr.body_relevance(title, body, "en", "GBR")
    assert not relevant, why
    # the gate still admits it: the territories' names are not touched there
    assert gate.check(title, "", "en", "GBR")[0]


def test_a_territory_story_naming_the_mainland_counts_its_territory_terms():
    body = ("Hong Kong activist Joshua Wong pleaded guilty under the national security law that Beijing imposed on "
            "Hong Kong in 2020. Hong Kong prosecutors said he colluded with foreign forces.")
    assert gate.count("Joshua Wong pleads guilty in Hong Kong", body, "en", "GBR", residual=True) == (2, 5, True)
    assert cr.body_relevance("Joshua Wong pleads guilty in Hong Kong", body, "en", "GBR")[0]


def test_a_cross_strait_story_is_still_china_coverage():
    body = ("Beijing sent warships around Taiwan on Monday. China's defence ministry said the drills were a warning, "
            "and Chinese state media showed the People's Liberation Army rehearsing a blockade of Taiwan.")
    assert cr.body_relevance("China holds drills around Taiwan", body, "en", "GBR")[0]


def test_china_rose_and_porcelain_name_no_china():
    title = "Air Jordan 6 Low 'China Rose' Release Date, Where to Buy"
    body = "The Air Jordan 6 Low China Rose arrives next week. The China Rose colourway pairs pink suede with a gum sole."
    assert not cr.body_relevance(title, body, "en", "USA")[0]
    assert not gate.check(title, "Fine china and bone china for the holidays.", "en", "USA")[0]
    assert gate.check("China Rose sneaker sells out in China", "", "en", "USA")[0]
    # the country is left alone: rose the verb, in a headline too, and China after the verb fine
    for title in ("Exports to China Rose 15%", "Thai exports to China rose 15.5% in August",
                  "Regulators fine China Evergrande"):
        assert gate.check(title, "", "en", "USA")[0], title


# --- disclosures attached to the item -------------------------------------------------------------------

LABELS_CHROME = "A story - Site\narticle\n@type: NewsArticle\npublisher: Site\n"


@pytest.mark.parametrize("author,body,labels,expected", [
    # a kicker naming the sponsor on its own line of chrome
    (None, "A story about tourism.", LABELS_CHROME + "Paid content by the Hainan Provincial Government", "A"),
    # the same words split over a menu item and a later line
    (None, "A story about tourism.", LABELS_CHROME + "Paid content\nHainan Provincial Government", "none"),
    # the item's own section value
    (None, "L'ambassade de Chine a organisé la rencontre.",
     "T\narticleSection: Actualités, Publi-Reportage\n@type: NewsArticle", "A"),
    # the author field: naming the sponsor, or a desk byline beside a state outlet named in the body
    ("Contenuto sponsorizzato da Xinhua", "Un servizio sulla provincia.", "", "A"),
    ("Advertorial Desk", "YUNNAN, CHINA. The flower market in Kunming, as Xinhua reported, set a record.", "", "A_candidate"),
    # a disclosure line opening the body
    (None, "This content is sponsored by China Daily.\n\nA long story follows.", "", "A"),
    # sponsored in prose
    (None, "The forum, which was sponsored by the Shanghai Municipal Government and attended by Xinhua reporters, "
           "drew delegates from forty countries to the city for three days of talks.", "", "none"),
    (None, "The festival is co-sponsored by the Shaanxi Provincial People's Government.", "", "none"),
])
def test_a_disclosure_counts_only_where_it_is_attached_to_the_item(author, body, labels, expected):
    res = cr.match_signatures("", body, author, labels=labels)
    assert res["decision"] == expected, [m["id"] for m in res["matches"]]


def test_bare_anzeige_is_not_a_disclosure():
    body = "Anzeige\nDie chinesische Botschaft lädt zum Empfang. Xinhua berichtet."
    assert "sponsored_de_with_state_entity" not in [m["id"] for m in cr.match_signatures("", body, None)["matches"]]
    body = "Gesponserter Beitrag\nDie chinesische Botschaft lädt zum Empfang."
    assert "sponsored_de_with_state_entity" in [m["id"] for m in cr.match_signatures("", body, None)["matches"]]


def test_disclosure_scope_regions():
    text, regions = cr.disclosure_scope("Jane", "T\nfree\n@type: NewsArticle\nmenu", "Sponsored content\nBody", "Body")
    kinds = {(k, text[a:b] if k != "start" else text[a:a + 9]) for k, a, b in regions}
    assert ("line", "Jane") in kinds and ("free", "free") in kinds and ("line", "menu") in kinds
    assert ("start", "Sponsored") in kinds


# --- the native wire end credit -------------------------------------------------------------------------

def _wire_end(body):
    return "state_outlet_wire_end_native" in [m["id"] for m in cr.match_signatures("", body, None)["matches"]]


def test_a_native_wire_credit_closing_the_body_still_counts():
    assert _wire_end("시진핑 주석이 회담에서 밝혔다. " * 10 + "\n신화통신")
    assert _wire_end("Κείμενο της είδησης. " * 10 + "\nΣινχουά\n© 2026 Όλα τα δικαιώματα")


def test_an_ai_glossary_term_is_not_a_wire_credit():
    """Item 1630643, stored evidence: a list mark line, the term, then the sentence defining it."""
    body = ("양국 정상 간의 회담에서도 주요 안건으로 다뤄졌답니다. 🤖🧠✨\n-\n신화통신\n"
            "중국의 공식적인 국가 소식과 입장을 전하는 중국의 대표적인 관영 통신사를 말해요. 이번 기사에서는 시진핑 주석의 발언을 전했어요.")
    assert not _wire_end(body)
    assert not _wire_end("본문입니다. " * 10 + "\n-\n신화통신")


# --- lookalike references ---------------------------------------------------------------------------------

TITLE = "Cina, famiglia di tigri siberiane selvatiche avvistata in strada"


def _a_label(conn, aid, method, route):
    store.insert_classification(conn, aid, method, "A", 1.0 if method == "rules" else 0.9, route=route,
                                signatures_fired=["xinhua_slash_credit"] if method == "rules" else None)
    conn.commit()


def test_only_credited_state_origin_labels_are_lookalike_references(conn):
    refs = {}
    for n, (method, route) in enumerate([("rules", "wire_credit"), ("rules", None), ("llm", "distribution_stamp"),
                                         ("llm", "unattributed"), ("llm", "uncredited_copy"), ("llm", None)]):
        aid = _article(conn, 100 + n, TITLE, outlet="o%d" % n, lang="it", country="ITA")
        _a_label(conn, aid, method, route)
        refs[aid] = (method, route)
    probe = _article(conn, 200, TITLE, outlet="it_libero", lang="it", country="ITA")
    index = lookalike.build_index(conn, [store.get_article(conn, probe)])
    kept = {refs[a] for a in index.refs}
    assert kept == {("rules", "wire_credit"), ("rules", None), ("llm", "distribution_stamp")}


def test_the_sweep_ignores_a_model_only_state_origin_label(conn):
    copy = _article(conn, 1, TITLE, "Una famiglia di tigri siberiane avvistata in strada. " * 3, outlet="it_libero",
                    lang="it", country="ITA")
    store.insert_classification(conn, copy, "rules", "C", 0.75, signatures_fired=["residual_c"])
    ref = _article(conn, 2, TITLE, outlet="it_vivo", lang="it", country="ITA")
    _a_label(conn, ref, "llm", "unattributed")
    assert cr.sweep_lookalikes(conn)["refs"] == 0
    conn.execute("UPDATE classifications SET route='wire_credit' WHERE article_id=?", (ref,))
    conn.commit()
    out = cr.sweep_lookalikes(conn)
    assert out["refs"] == 1 and out["routed"] == 1


# --- reclassification order ---------------------------------------------------------------------------------

CMG = ("CALGARY, Alberta, Sept. 16, 2026 (GLOBE NEWSWIRE) Computer Modelling Group Ltd. (CMG or the Company) "
       "(TSX: CMG) is pleased to announce exemptive relief in connection with its substantial issuer bid. "
       "In the event the SIB is extended, CMG will provide a further news release disclosing the details.")


def test_reclassify_takes_rules_state_origin_first_and_carries_kept_model_labels_forward(conn):
    kept = []
    for n in range(5):   # model labels with lower ids than the old rules label
        aid = _article(conn, n, "Una storia", "Una storia qualunque sul mercato del grano. " * 5)
        store.insert_classification(conn, aid, "llm", "C", 0.8, model_version="m", ruleset_version="2026.09.7")
        kept.append(aid)
    c = _article(conn, 10, "Computer Modelling Group Ltd. Announces Exemptive Relief", CMG, country="CAN")
    store.insert_classification(conn, c, "rules", "A", 1.0, signatures_fired=["china_media_group_credit"],
                                ruleset_version="2026.09.7", route="wire_credit")
    conn.commit()
    out = cr.reclassify(conn, "t", limit=1)
    assert out["input"] == 1 and out["not_relevant"] == 1
    cur = store.current_classification(conn, c)
    assert (cur["method"], cur["category"], cur["ruleset_version"]) == ("rules", "not_relevant", config.RULESET_VERSION)
    out = cr.reclassify(conn, "t")
    assert out["kept_llm"] == 5
    for aid in kept:
        cur = store.current_classification(conn, aid)
        assert (cur["method"], cur["category"], cur["ruleset_version"]) == ("llm", "C", config.RULESET_VERSION)
    assert cr.reclassify(conn, "t")["input"] == 0


def test_a_model_label_whose_body_is_missing_is_not_marked_checked(conn):
    aid = _article(conn, 1, "Una storia")
    store.insert_classification(conn, aid, "llm", "C", 0.8, model_version="m", ruleset_version="2026.09.7")
    conn.commit()
    out = cr.reclassify(conn, "t")
    assert out["kept_llm"] == 0
    assert store.current_classification(conn, aid)["ruleset_version"] == "2026.09.7"


# --- model routes are never inherited from candidate signatures -----------------------------------------------

def test_ensure_routes_gives_a_model_label_no_route_from_its_candidate_signatures(conn):
    plain = _article(conn, 1, "GAC Cambodia factory starts production")
    conn.execute("UPDATE articles SET llm_trigger=? WHERE id=?",
                 (json.dumps({"a_candidate": ["diplomat_list_name_only"], "triggers": []}), plain))
    copied = _article(conn, 2, "A copy")
    conn.execute("UPDATE articles SET llm_trigger=? WHERE id=?",
                 (json.dumps({"a_candidate": ["xinhua_dateline"], "triggers": [], "lookalike": {"article_id": 9}}), copied))
    for aid in (plain, copied):
        store.insert_classification(conn, aid, "llm", "A", 0.9, model_version="m")
    conn.commit()
    assert cr.ensure_routes(conn) == 2
    assert store.current_classification(conn, plain)["route"] == "unattributed"
    assert store.current_classification(conn, copied)["route"] == "uncredited_copy"
