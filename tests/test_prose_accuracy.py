"""Reader-facing prose says what the system does (stress test of 2026-10-17, methodology and docs audit):
state origin is not a label decided without the model, the relabel queue works in priority tiers, stale
static notes are gone, country notes carry no counts or routes that drift, and METHODOLOGY.md states the
limits the audit found missing, from live values."""
import json
import re
from pathlib import Path

from pipeline import classify_llm, config, methodology

ROOT = Path(__file__).resolve().parents[1]
CONTENT = json.loads((ROOT / "docs" / "content.json").read_text(encoding="utf-8"))
INDEX = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
METH_PY = (ROOT / "pipeline" / "methodology.py").read_text(encoding="utf-8")


def _faq(question):
    return next(f["answer"] for f in CONTENT["faq"] if f["question"] == question)


def test_state_origin_is_never_called_a_label_decided_without_the_model():
    for name, text in (("index.html", INDEX), ("content.json", json.dumps(CONTENT)), ("README.md", README),
                       ("methodology.py", METH_PY)):
        flat = " ".join(text.split())
        assert "decided without the model" not in flat.replace("not a label decided without the model", ""), name
        assert "decided by signatures without the model" not in flat, name
    assert "decided by fixed rules where a signature shows it, otherwise by the model" in INDEX
    computer = _faq("Is a computer deciding this?")
    assert computer.startswith("Not on its own: the method is mine, and the computer carries it out.")
    assert "many state origin labels" in computer and "not perfectly repeatable" in computer
    assert "stands at zero today" not in computer


def test_state_origin_split_is_generated_from_the_rules_and_model_counts():
    tot = {"A": 3278, "Ar": 1256, "Al": 2022}
    text = methodology.state_origin_split_text({"route_totals": {"unattributed": 1737}}, tot)
    assert text.startswith("at export time fixed rules decided 1,256 of the 3,278 state origin labels (38 percent)")
    # Decision D1 as revised (2026-10-22): undetermined labels are in no count, outside these numbers.
    assert "A further 1,737 model judgements of state origin name no evidence a reader can check" in text
    assert "they are undetermined, excluded from every count" in text
    block = methodology.state_origin_split_text({"route_totals": {"unattributed": 9}, "undetermined": {"count": 1737}}, tot)
    assert "A further 1,737 model judgements" in block, "meta.undetermined.count is read first"
    assert methodology.state_origin_split_text({}, {"A": 0}) == "no state origin label had been made at export time."
    assert "no signature of any kind" not in methodology.state_origin_split_text({}, tot)


def test_technical_notes_are_gone_and_the_page_no_longer_says_they_come_from_the_data():
    assert "methodology_extra" not in CONTENT
    assert 'id="technical-notes-wrap"' in INDEX
    assert "reclassification status, written from the data at the last export" not in INDEX
    assert "not written from the data" in INDEX


def test_six_route_cards_including_the_undetermined_calls():
    order = re.search(r"ROUTE_CARD_ORDER = \[([^\]]*)\]", APP).group(1)
    ids = re.findall(r'"([a-z_]+)"', order)
    assert ids[-1] == "unattributed" and len(ids) == 6
    assert all(i in CONTENT["routes"] for i in ids)
    card = CONTENT["routes"]["unattributed"]
    assert card["plain_name"] == "Undetermined, not counted"
    assert "undetermined and excluded from every count" in card["how_it_arrives"] and "never rejoin" in card["how_it_arrives"]
    assert card["illustrative_line"].startswith("Illustrative only, not a quote from a real article:")
    assert "How state text arrives, in five ways, and the calls left undetermined" in INDEX and "in six ways" not in INDEX


def test_relabel_order_is_described_as_tiers_read_from_the_code():
    text = methodology.relabel_order_text()
    assert text.startswith("first ") and text.endswith(", then the rest")
    assert "_" not in text
    assert text.count(", then ") == len(classify_llm.RELABEL_PRIORITY)
    assert methodology._relabel_tier_name("A_something_new") == "state origin labels (something new)"
    assert methodology._relabel_tier_name("odd_key") == "odd key labels"
    quotes = _faq("Does every story that quotes Beijing count as unchecked state sourcing?")
    assert "oldest first, with up to half" not in quotes and "priority tiers" in quotes
    assert "oldest first within each kind" not in INDEX and "priority tiers" in INDEX
    assert "through the API, oldest\nfirst," not in README and "priority\ntiers" in README


def test_country_notes_and_faq_carry_no_stale_counts_or_credits():
    notes = CONTENT["country_notes"]
    blob = json.dumps(CONTENT, ensure_ascii=False)
    assert "Immediapress" not in blob and "kept moving" not in blob and "kept circulating" not in blob
    assert '"(XINHUA/ITALPRESS)"' in notes["ITA"] and '"(XINHUA/ITALPRESS)"' in _faq("Why is Italy so high?")
    usa = _faq("Why is the United States zero?")
    assert not re.search(r"\b\d+ US outlets\b", usa)
    taiwan = _faq("Why is Taiwan zero?")
    assert "Chinese-language" in taiwan and "one active monitored outlet" not in taiwan
    assert not re.search(r"\b\d+ articles\b", taiwan)
    assert "Mehr News English" in notes["IRN"] and "top carrier" not in notes["IRN"]
    assert "Guinéenews" not in notes["GIN"]
    for iso in ("ITA", "ZAF", "SGP", "IRN", "GIN", "HKG", "SEN"):
        assert "route" not in notes[iso].replace("route each label arrived by", "").replace("carrier and route shown", ""), iso
    assert "every country at once" not in blob and "every country at once" not in INDEX


def test_faq_and_country_notes_speak_in_the_first_person_singular():
    texts = [f["answer"] for f in CONTENT["faq"]] + list(CONTENT["country_notes"].values())
    for t in texts:
        assert not re.search(r"\b(we|We|our|Our|us)\b", t), t[:80]
    assert "neither do we" not in json.dumps(CONTENT)


def test_not_about_china_matches_the_codebook():
    d = CONTENT["categories"]["not_relevant"]["definition"]
    assert "nothing to do with China" not in d and "full output" not in d
    assert "does not substantively concern China" in d and "State text never lands here" in d


def test_over_claims_are_softened():
    assert "the world's newspapers" not in INDEX and "about 1,700 monitored outlets" in INDEX
    assert "Text placed by the Chinese state" not in APP and "run unaltered" not in APP
    assert "Every item the monitored feeds and wires published" not in APP
    assert "Every item the tracker saw in the monitored feeds and wires since" in APP
    assert "appears unaltered" not in INDEX and "appears unaltered" not in CONTENT["start_here"]
    assert "never\ndiscards" not in METH_PY and "never discards" not in METH_PY
    assert "so counts can be reweighted" not in INDEX and "recorded per day as a diagnostic" in INDEX
    assert "until audience ranks are recorded" not in INDEX
    assert "near-identical bodies in different outlets" in INDEX


def test_kappa_rule_names_both_sides_as_raters():
    flat = " ".join(METH_PY.split())
    for name, text in (("index.html", INDEX), ("content.json", json.dumps(CONTENT)), ("methodology.py", flat)):
        assert "articles on each side" not in text, name
    assert "the coders and the model each placing at least 10 articles in unchecked state sourcing" in INDEX


def test_readme_and_changelog_header():
    assert not re.search(r"Stanford|University|Professor", README)
    head = "\n".join((ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()[:5])
    assert "Code changes that do not alter classification are not listed" not in head
    assert "the site" in head


def test_rediscovery_share_is_of_the_total_it_names():
    rd = {"estimated_share": 0.08, "measured_of": 3008671}
    grown = methodology.rediscovery_total_text(rd, 3352040)
    assert grown == ("the 3,008,671 items screened when it was measured, about 7 percent of the running total of "
                     "3,352,040 items screened at this export")
    assert methodology.rediscovery_total_text(rd, 0) == "the running total of items screened"
    assert methodology.rediscovery_total_text(rd, None) == "the running total of items screened"


def test_known_limits_are_generated_from_the_registry_and_meta():
    outlets = [
        {"id": "tw_a", "country": "TWN", "language": "zh", "active": False, "inactive_reason": "publishes primarily in Chinese"},
        {"id": "tw_b", "country": "TWN", "language": "zh", "active": False, "inactive_reason": "publishes primarily in Chinese"},
        {"id": "au_a", "country": "AUS", "language": "zh", "active": False, "inactive_reason": "publishes primarily in Chinese"},
        {"id": "x_a", "country": "ITA", "language": "it", "active": False, "inactive_reason": "feed returned HTTP 403 to identified crawler"},
        {"id": "x_b", "country": "ITA", "language": "it", "active": False,
         "inactive_reason": "robots.txt disallows the crawler; earlier: every feed failed on every check"},
        {"id": "x_c", "country": "ITA", "language": "it", "active": False, "inactive_reason": "every feed failed on every check"},
        {"id": "x_d", "country": "ITA", "language": "it", "active": True},
    ]
    meta = {"session_labels": {"total": 4557}, "llm_labels_total": 13301}
    text = methodology.known_limits_text(meta, outlets)
    assert text.startswith("## Known limits\n\n")
    assert "3 outlets are excluded this way: 2 in Taiwan and 1 in Australia." in text
    assert "4,557 of the 13,301 current model labels (34 percent) were made in an interactive session" in text
    assert "data/labels/README.md" in text and "undercount unchecked state sourcing" in text
    assert "2 registered outlets are inactive because they refuse the identified crawler" in text
    assert "and 1 more because every feed failed" in text
    assert "first %s characters" % "{:,}".format(config.LLM_BODY_CHAR_LIMIT) in text
    assert "Translated state copy run without a credit is invisible" in text
    assert "state_owned_enterprise or sar_government" in text and "private Chinese companies" in text
    empty = methodology.known_limits_text({}, [])
    assert "No outlet is excluded this way at present." in empty
    assert "No current model label was made in an interactive session at export time." in empty
    assert "\u2014" not in text and "\u2013" not in text


def test_methodology_session_order_no_longer_claims_the_budget_covers_everything():
    off = methodology.session_order_text(0)
    assert "sized for the API" not in off and "no article is held back" in off
    assert "articles left over wait for a later day" in off
    assert "definitions verbatim" not in METH_PY


def test_issuer_types_are_explained_with_their_counts():
    text = methodology.issuer_text({"issuer_type_totals": {"by_type": {
        "state_owned_enterprise": {"A": 12, "C": 3}, "sar_government": {"A": 4}, "private_release": {"C": 20}},
        "unrecorded": 9000}})
    assert "count as state origin and are reported separately" in text and "are not state origin" in text
    assert "will move over the coming weeks" in text
    assert "state_owned_enterprise 15 (12 state origin); sar_government 4 (4 state origin); private_release 20 (0 state origin)" in text
    assert "9,000 current model labels carried no issuer type yet" in text
    assert "At export time" not in methodology.issuer_text({})
    assert "never from the signature that sent the article" in METH_PY and "never from the signature that sent the article" in INDEX
    assert "the prompt of 2026-10-17 added the issuer rule" in METH_PY


def test_human_study_text_states_and_failure_rule():
    stale = methodology.human_study_text({"state": "stale", "stale": True, "computed_at": "2026-10-14T01:00:00+00:00",
                                          "prompt_version": "2026-10-12", "current_prompt_version": "2026-10-17",
                                          "labels_compared": "the current labels.", "min_positive": 10})
    assert " The study compares the coders with the current labels." in stale
    assert "computed 2026-10-14, compared the coders with labels made under classifier prompt 2026-10-12" in stale
    assert "a passing result no longer publishes the counts, while a failing one keeps withholding them" in stale
    assert stale.endswith("at least 10 times and the coders fewer, overall or in a language, has failed.")
    sup = methodology.human_study_text({"state": "sample_superseded", "current_prompt_version": "2026-10-17",
                                        "superseded_samples": [{"created_at": "2026-10-12T20:12:00+00:00",
                                                                "reason": "all its labels predate the current prompt."}]})
    assert "The sample drawn on 2026-10-12 was set aside before coding began: all its labels predate the current prompt." in sup
    assert methodology.human_study_text({}) == ""


def test_failed_study_without_kappa_prints_its_reason(tmp_path, monkeypatch):
    from tests.test_export import _rendered_methodology
    reason = "the model placed 14 of 300 articles in unchecked state sourcing and the coders 3, fewer than the 10 a kappa needs"
    hs = {"state": "failed", "kappa_bc": None, "failed_reason": reason, "min_positive": 10}
    text = " ".join(_rendered_methodology(tmp_path, monkeypatch, relay_measured=True, relay_provisional=False,
                                          relay_publishable=False, relay_human_study=hs).split())
    assert "the hand coding study failed, because " + reason + "." in text
    assert "n/a, below the" not in text


def test_snapshot_sentences_name_the_scrubbed_copy_and_no_whole_copy():
    """Since the relay was retired on 2026-10-22 the whole database and the bodies are never uploaded,
    encrypted or not, and nothing names the relay's key as a way to back them up."""
    flat = " ".join(METH_PY.split())
    assert "is snapshotted daily, compressed, as a release asset" not in flat
    assert "The whole database and the article bodies are never uploaded, in clear or encrypted" in flat
    assert "RELAY_BUNDLE_KEY" not in flat and "tracker-full.db.gz.enc" not in flat
    readme = " ".join(README.split())
    assert "as the single asset of the `db-snapshot` release" not in readme
    assert "The whole database and the bodies are never uploaded, in clear or encrypted" in readme
    assert "`tracker-full.db.gz.enc`" not in readme


def test_readme_says_the_relay_was_stopped_and_its_key_is_never_added():
    readme = " ".join(README.split())
    assert "articles, bodies included, to the `relay` branch" not in readme
    assert "So the relay was stopped on 2026-10-22: its 123 outlets are inactive" in readme
    assert "the `RELAY_BUNDLE_KEY` secret is never to be added" in readme
    assert "the Cook Islands and Kiribati with no active outlet" in readme
