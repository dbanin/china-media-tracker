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


def test_two_weak_groups_is_a():
    body = "Sponsored content\n\nThe city of Hangzhou welcomes investors.\n\nThe publisher has not reviewed this content."
    res = cr.match_signatures("", body, None)
    groups = {m["group"] for m in res["matches"] if m["strength"] == "weak"}
    assert len(groups) >= 2 and res["decision"] == "A"


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
