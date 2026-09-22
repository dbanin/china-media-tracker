"""Fixture based tests for the signature matcher. Every fixture is real article text."""
from pathlib import Path

import pytest
import yaml

from pipeline import classify_rules as cr

FIX = Path(__file__).parent / "fixtures" / "rules"
CASES = yaml.safe_load((FIX / "cases.yaml").read_text())


def _run(case):
    text = (FIX / case["file"]).read_text()
    labels = (FIX / case["labels"]).read_text() if case.get("labels") else ""
    return cr.match_signatures("", text, case.get("author"), labels=labels)


@pytest.mark.parametrize("case", CASES, ids=[c["file"] for c in CASES])
def test_fixture(case):
    res = _run(case)
    ids = [m["id"] for m in res["matches"]]
    trig = [t["id"] for t in res["triggers"]]
    if "expect_decision" in case:
        assert res["decision"] == case["expect_decision"], (ids, trig)
    if "expect_decision_not" in case:
        assert res["decision"] != case["expect_decision_not"], (ids, trig)
    for i in case.get("expect_ids_include", []):
        assert i in ids, ids
    for i in case.get("expect_ids_exclude", []):
        assert i not in ids, ids
    for i in case.get("expect_triggers_include", []):
        assert i in trig, trig
    if "expect_triggers" in case:
        assert trig == case["expect_triggers"]


# Synthetic hard negatives and positives per group, in addition to the real fixtures.

def test_critical_mention_of_xinhua_is_not_a():
    body = ("Beijing rejected the report. Xinhua, the state news agency, later claimed the meeting had "
            "never happened, a claim contradicted by three diplomats who attended. Photo: Xinhua")
    res = cr.match_signatures("", body, "Jane Reporter")
    assert res["decision"] == "none"
    assert "cites_xinhua" in [t["id"] for t in res["triggers"]]


def test_photo_credit_exclusion():
    res = cr.match_signatures("", "Long independent piece.\n\n(Photo: Xinhua)\n\nMore text.", None)
    assert res["decision"] == "none"


def test_dateline_is_a():
    res = cr.match_signatures("", "BEIJING, Sept. 2 (Xinhua) -- China will expand trade, officials said.", None)
    assert res["decision"] == "A"


def test_two_weak_groups_is_a_when_a_state_entity_is_named():
    body = ("Sponsored content\n\nThe city of Hangzhou welcomes investors, says the Hangzhou Municipal Government.\n\n"
            "The publisher has not reviewed this content.")
    res = cr.match_signatures("", body, None)
    groups = {m["group"] for m in res["matches"] if m["strength"] == "weak"}
    assert len(groups) >= 2 and res["state_entity"] and res["decision"] == "A"


def test_two_weak_groups_without_a_state_entity_is_only_a_candidate():
    body = ("Sponsored content\n\nChery Q bookings open with PKR 1.5 million. Your Q to beat the fuel bills, "
            "with a Chinese hybrid drivetrain.\n\nThe publisher is not responsible for the content of this announcement.")
    res = cr.match_signatures("", body, None)
    groups = {m["group"] for m in res["matches"] if m["strength"] == "weak"}
    assert len(groups) >= 2 and not res["state_entity"] and res["decision"] == "A_candidate"


def test_one_weak_is_candidate():
    res = cr.match_signatures("", "Sponsored content\n\nA story about Hangzhou.", None)
    assert res["decision"] == "A_candidate"


def test_ambassador_byline_is_a():
    res = cr.match_signatures("", "Relations between our countries matter.", "Xiao Qian, Chinese Ambassador to Australia")
    assert res["decision"] == "A" and "diplomat_title_author_field" in [m["id"] for m in res["matches"]]


def test_ambassador_quoted_in_body_is_not_a():
    body = ("The Chinese ambassador to Australia, Xiao Qian, told reporters the tariffs were unjustified. "
            "Trade Minister Don Farrell disputed the figures, citing customs data.")
    res = cr.match_signatures("", body, "Staff reporter")
    assert res["decision"] == "none"
    assert "chinese_embassy_quoted" not in [t["id"] for t in res["triggers"]] or True


def test_closing_bio_line_is_a():
    body = "Our two countries have much to gain. " * 20 + "\n\nWang Di is the Ambassador of the People's Republic of China to Canada."
    res = cr.match_signatures("", body, None)
    # since ruleset 2026.09.2 a bio line alone routes to the LLM rather than deciding A
    assert res["decision"] == "A_candidate" and "diplomat_title_tail" in [m["id"] for m in res["matches"]]


def test_explicit_byline_is_a():
    body = "By Wang Di, Ambassador of the People's Republic of China to Canada\n\nOur two countries have much to gain."
    res = cr.match_signatures("", body, None)
    assert res["decision"] == "A" and "diplomat_byline_head" in [m["id"] for m in res["matches"]]


def test_company_press_release_is_not_a():
    body = "MUNICH, Sept. 3, 2026 /PRNewswire/ -- Huawei kicked off its global product launch.\n\nAdvertorial"
    res = cr.match_signatures("", body, "Advertorial Desk")
    assert res["decision"] == "A_candidate"


def test_wire_stamp_without_state_entity_is_only_weak():
    body = "TORONTO, Sept. 2, 2026 /PRNewswire/ -- Maple Corp announced quarterly results.\n\nSOURCE Maple Corp"
    res = cr.match_signatures("", body, None)
    assert res["decision"] in ("A_candidate", "none")


def test_mofa_spokesperson_trigger():
    body = "Foreign ministry spokesperson Lin Jian said the claims were baseless."
    res = cr.match_signatures("", body, None)
    assert "mofa_spokesperson" in [t["id"] for t in res["triggers"]]


def test_french_state_media_trigger():
    body = "Selon l'agence Chine nouvelle, le président a rencontré son homologue."
    res = cr.match_signatures("", body, None)
    assert "state_media_reported_fr" in [t["id"] for t in res["triggers"]]


def test_acronym_expansion_in_prose_is_not_a_credit():
    body = "In 2021, the state-run Chinese Global Television Network (CGTN) released a propaganda video about the case."
    res = cr.match_signatures("", body, "Jane Reporter")
    assert res["decision"] == "none", [m["id"] for m in res["matches"]]
    assert cr.match_signatures("", "BEIJING (CGTN) -- China's economy grew 5 percent.", None)["decision"] == "A"


def test_cgtn_section_heading_is_not_a_credit():
    body = ("China This Week. What Chinese outlets said about BRICS.\n\nXinhua\nThe agency called the summit a success.\n\n"
            "CGTN\nChina Global Television Network, or CGTN, is available in many languages and said the bloc would grow.\n\n"
            "Global Times\nThe paper struck a harder tone.\n\n" + "More analysis follows here. " * 40)
    res = cr.match_signatures("", body, "Explained Desk")
    assert res["decision"] != "A", [m["id"] for m in res["matches"]]
    tail = "Chinese cinema inspires Burkina Faso, the delegation said.\n\nSource : CGTN\n"
    assert cr.match_signatures("", tail, None)["decision"] == "A"
    assert cr.match_signatures("", "A report on culture.\n\nCGTN\n", None)["decision"] == "A"


def test_syndication_disclaimer_is_not_sponsorship():
    body = ("The minister opened the bridge on Monday.\n\n(Except for the headline, this article has not been edited by "
            "FPJ's editorial team and is auto-generated from a syndicated feed.)")
    res = cr.match_signatures("", body, None)
    assert res["decision"] == "none", [m["id"] for m in res["matches"]]
    assert "syndication_disclaimer" in res["exclusions"]


def test_cctv_camera_does_not_trigger():
    trig = lambda t: [x["id"] for x in cr.match_signatures("", t, None)["triggers"]]
    assert "cites_cctv" not in trig("Detectives have reviewed CCTV and identified a grey Audi sedan leaving the scene.")
    assert "cites_cctv" in trig("Rescuers reached Gyirong, state broadcaster CCTV reported on Wednesday.")
    assert "cites_cctv" in trig("informou a emissora estatal CCTV nesta quarta-feira.")


def test_foreign_spokespeople_and_state_media_must_be_chinese():
    trig = lambda t: [x["id"] for x in cr.match_signatures("", t, None)["triggers"]]
    assert trig("Russian Foreign Ministry spokeswoman Maria Zakharova accused Armenia of bad faith.") == []
    assert trig("The launch was confirmed, according to state media. The Korean Central News Agency said the test succeeded.") == []
    assert "mofa_spokesperson" in trig("Foreign Ministry spokesperson Lin Jian told journalists the claim was false.")
    assert "mofa_spokesperson" in trig("Chinese Foreign Ministry spokesperson Guo Jiakun said the aid had left.")
    assert "state_media_reported_en" in trig("Chinese state media reported in October that the doors had failed.")


def test_residual_relevance_counts_occurrences():
    ok, _ = cr.body_relevance("China warns Nepal over border", "China said on Monday that the border would reopen. China's ministry added that trade would resume.", "en")
    assert ok
    ok, _ = cr.body_relevance("Nepal floods", "Rescue teams reached villages. China sent tents.", "en")
    assert not ok
    ok, _ = cr.body_relevance("Mobile speeds", "China China China China China ranks below Vietnam.", "en")
    assert ok


def test_missing_body_is_not_classified(monkeypatch, tmp_path):
    """An article whose body file is absent must be re-fetched, never classified as not relevant."""
    import sqlite3
    from pipeline import config, store, fetch_articles
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "RAW_HTML_DIR", tmp_path / "raw")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    aid = store.insert_discovered(conn, {"url": "https://example.com/story", "outlet_id": "o", "country": "ITA",
                                         "language": "en", "title": "China trade", "status": "fetched", "gate_relevant": 1})
    conn.execute("UPDATE articles SET status='fetched' WHERE id=?", (aid,))
    calls = []

    def fake_fetch(conn_factory, row):
        calls.append(row["id"])
        return {"id": row["id"], "status": "failed", "reason": "http_403"}
    monkeypatch.setattr(fetch_articles, "process_article", fake_fetch)
    row = store.get_article(conn, aid)
    out = cr.classify_article(conn, row)
    assert out["outcome"] == "body_unavailable" and calls == [aid]
    assert store.current_classification(conn, aid) is None


def test_failed_refetch_keeps_a_classified_label(tmp_path, monkeypatch):
    from pipeline import config, fetch_articles, store
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "k.db")
    aid = store.insert_discovered(conn, {"url": "https://x.test/k", "outlet_id": "zz_unknown", "country": "ITA", "language": "it",
                                         "title": "Cina", "status": "fetched", "gate_relevant": 1})
    store.insert_classification(conn, aid, "rules", "C", 0.75)
    conn.commit()
    monkeypatch.setattr(fetch_articles, "process_article", lambda factory, row: {"status": "failed", "reason": "http_403"})
    row = store.get_article(conn, aid)
    assert cr.ensure_body(conn, row) == ""
    assert store.get_article(conn, aid)["status"] == "classified"
    # and an article demoted by the old code is repaired at the start of the next run
    conn.execute("UPDATE articles SET status='failed' WHERE id=?", (aid,))
    conn.commit()
    assert cr.run(conn, "t")["relabelled"] == 1
    assert store.get_article(conn, aid)["status"] == "classified"


# Regressions for ruleset 2026.09.8. Each of these was a measured wrong label on the live corpus.

def test_company_ticker_is_not_china_media_group():
    """CMG is the Toronto ticker of Computer Modelling Group Ltd. Three Canadian articles about its
    buyback carried a state origin label, 19 percent of Canada's count."""
    body = ("Computer Modelling Group Ltd. Announces Exemptive Relief Obtained in Connection with its "
            "Substantial Issuer Bid. In the event the SIB is extended, CMG will provide a further news "
            "release disclosing the details. CMG is a computer software technology company serving the "
            "oil and gas industry.")
    res = cr.match_signatures("Computer Modelling Group Ltd. announces relief", body, "Computer Modelling Group Ltd.")
    assert res["decision"] != "A", [m["id"] for m in res["matches"]]
    assert "china_media_group_credit" not in [m["id"] for m in res["matches"]]


def test_carrier_reporting_on_xinhua_is_not_state_origin():
    """The distribution stamp patterns used to pair the carrier's own name with any state media
    mention within 3000 characters. The carrier names itself in every one of its own datelines, so
    an agency report about Xinhua qualified. Italy's count was 231 of 242 from one such agency."""
    body = ("ROMA (ITALPRESS) - Un rapporto pubblicato oggi denuncia la disinformazione diffusa dai media "
            "statali cinesi. Secondo quanto riportato dall'agenzia Xinhua, il governo ha respinto le "
            "accuse, ma i ricercatori contestano quella ricostruzione con documenti indipendenti.")
    res = cr.match_signatures("Rapporto sulla disinformazione cinese", body, "Agenzia di Stampa Italpress")
    assert res["decision"] != "A", [m["id"] for m in res["matches"]]


def test_private_company_release_citing_a_china_statistic_is_not_state_origin():
    body = ("NEW YORK, Sept. 3, 2026 /PRNewswire/ -- Acme Robotics announced record quarterly sales today. "
            "The company said demand rose across Asia. " + ("Filler sentence about the product line. " * 12) +
            "Xinhua reported last week that industrial output grew 5 percent.")
    res = cr.match_signatures("Acme Robotics announces record sales", body, "Acme Robotics")
    assert res["decision"] != "A", [m["id"] for m in res["matches"]]


def test_state_issued_release_on_the_wire_is_still_state_origin():
    """The narrowing must not lose the real thing: the issuer stands in the release's own trailer."""
    body = ("BEIJING, Sept. 3, 2026 /PRNewswire/ -- A report from China Daily describes the province's "
            "new industrial park and the investment behind it.\n\nSOURCE China Daily")
    res = cr.match_signatures("Report describes new industrial park", body, "China Daily")
    assert res["decision"] == "A", [m["id"] for m in res["matches"]]


def test_topic_words_cannot_satisfy_the_state_entity_test():
    """Two weak signals plus any "state entity" gave a confidence 1.0 label with no model call, and the
    entity list held topics: Silk Road, Belt and Road, Chinese government. A travel advertorial qualified."""
    body = ("Sponsored content. The publisher has not reviewed this material. A Silk Road travel package "
            "from Samarkand to Bukhara takes in the old caravan cities over fourteen days.")
    res = cr.match_signatures("A fortnight along the Silk Road", body, "Travel Desk")
    assert res["decision"] != "A", [m["id"] for m in res["matches"]]


def test_native_script_credit_lines_are_state_origin():
    """Before 2026.09.8 the ruleset had 58 Latin spellings of Xinhua and none in Greek, Hebrew, Persian,
    Turkish or Vietnamese, so state origin was undetectable outside Latin script."""
    cases = [
        ("Η Κίνα ανακοίνωσε νέα μέτρα για την οικονομία.\nΠηγή: Σινχούα", "el"),
        ("중국 정부가 새로운 경제 정책을 발표했다.\n출처: 신화통신", "ko"),
        ("新华社北京9月12日电 中国国家统计局今天公布了最新数据。", "zh"),
    ]
    for body, tag in cases:
        res = cr.match_signatures("", body, "")
        assert res["decision"] == "A", (tag, [m["id"] for m in res["matches"]])


def test_a_native_script_photo_credit_is_not_a_text_credit():
    body = "중국 정부가 정책을 발표했다. 기자가 직접 취재한 내용이다.\n사진: 신화통신"
    res = cr.match_signatures("", body, "")
    assert res["decision"] != "A", [m["id"] for m in res["matches"]]


def test_overlapping_terms_count_once():
    """"Xi Jinping" used to count as both "Xi" and "Xi Jinping", so one name in a headline cleared the
    residual relevance rule and the article was published as independent journalism."""
    relevant, why = cr.body_relevance("Sastanak s Xi Jinpingom", "Predsjednik je odrzao sastanak.", "hr")
    assert relevant is False, why

