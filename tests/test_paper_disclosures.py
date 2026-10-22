"""Where the tracker differs from the original study (the 596 article study across Italy, Canada, Australia and
Singapore), disclosed in Known limits, on the site's methodology section and in the README (2026-10-22).

Four differences: the unit counted (pipeline/placements.py), the not relevant threshold, which is the tracker's
own rule (classify_rules.body_relevance), the looser test for unchecked state sourcing (the model's instructions
in classify_llm.py, which stay unchanged until after 2026-10-23), and section headers, which are not read as
evidence (the disclosure scope of the sponsored signatures, and arrival_of). Each test pins the disclosure and,
where it gives a number or a quotation, that the number or quotation is the live one."""
import json
import re
from pathlib import Path

from pipeline import classify_llm, classify_rules, config, export, methodology, placements, store

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
CONTENT = json.loads((ROOT / "docs" / "content.json").read_text(encoding="utf-8"))
RULES_PY = (ROOT / "pipeline" / "classify_rules.py").read_text(encoding="utf-8")

WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
         10: "ten"}
HITS = WORDS[classify_rules.BODY_RELEVANCE_MIN_HITS]
TOTAL = WORDS[classify_rules.BODY_RELEVANCE_MIN_TOTAL]
RULE = ("at least %s distinct China terms in the body, or %s occurrences of them, or a term in the headline with at "
        "least two occurrences" % (HITS, TOTAL))
QUOTE = "passed on without independent confirmation"


def _flat(text):
    return " ".join(text.split())


def _limits():
    return _flat(methodology.known_limits_text({}, outlets=[]))


def _site():
    """The site's study differences paragraphs: from the lead paragraph to the end of the methodology definitions."""
    start = INDEX.index('<p id="study-differences">')
    return _flat(INDEX[start:INDEX.index("</div>", start)])


def _rendered(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    out = tmp_path / "out"
    export.run(store.connect(db), "test", export_dir=out, audit_dir=tmp_path / "audit",
               methodology_path=tmp_path / "M0.md")
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    path = tmp_path / "M.md"
    methodology.write(meta, latest, path=path)
    return _flat(path.read_text(encoding="utf-8"))


def test_the_rule_text_quotes_the_live_constants():
    assert methodology.relevance_rule_text() == RULE
    # The headline arm of the rule is a literal in body_relevance, not a constant: pin it to the source.
    assert "(in_title and total >= 2)" in RULES_PY


def test_the_quotation_is_the_model_definition_word_for_word():
    # The text quotes the model's definition of unchecked state sourcing; classify_llm.py is never edited here.
    assert QUOTE in classify_llm.CATEGORY_DEFINITIONS
    assert "does not make it C, because it takes the claim as given" in classify_llm.SYSTEM_PROMPT
    assert "Do not let the tone of the article" in classify_llm.SYSTEM_PROMPT
    assert "second official" not in classify_llm.SYSTEM_PROMPT


def test_known_limits_carries_the_four_differences():
    text = _limits()
    assert ("How the tracker differs from the original study. The original study coded 596 articles across four "
            "countries: Italy, Canada, Australia and Singapore." in text)
    # 1. The unit counted.
    assert "The original study counted identical wire copy once per publication." in text
    assert "The tracker counts every placement in each monitored outlet" in text
    assert "a story shown by one shared widget across a network's mastheads counts once per country" in text
    assert "one page at two addresses, or the parts of one numbered series at one outlet on one day, count once" in text
    assert "Language editions of one wire release stay separate placements and are one underlying item." in text
    assert "the tracker's counts and shares cannot be set beside the study's" in text
    assert "The Australian example under What the numbers mean" in text and "1,350" not in text
    # 2. Not relevant, by the tracker's rule.
    assert "Not relevant, by the tracker's rule." in text and "which is not in the study's codebook" in text
    assert "the article concerns China when it has " + RULE + " (pipeline/classify_rules.py, body_relevance)" in text
    # 3. Unchecked state sourcing.
    assert "Unchecked state sourcing is looser than the study's. The study scored verification on five levels" in text
    assert "corroborated, contested and contextualized count as independent journalism" in text
    assert "merely attributed and unassessed count as unchecked state sourcing" in text
    assert "a second official or another outlet repeating the claim is not corroboration, and tone does not count" in text
    assert '"%s"' % QUOTE in text and "such contextualized items were 27 of 58" in text
    assert "probably overcounts relative to the study, and it stays provisional" in text
    assert ("The five level scale goes into the model's instructions after 23 October 2026, and every unchecked "
            "state sourcing label is then judged again." in text)
    # 4. Section headers.
    assert '"partnership content from GlobeNewswire" header at a Canadian daily' in text
    assert "only on the article itself: its own page labels, its author field, its headline" in text
    assert "never the outlet, the address or the section" in text
    assert "the arrival alone never makes an item state origin" in text
    assert "can therefore be missed, or end up undetermined" in text
    assert "Giving the model the address and the section is planned after 23 October 2026." in text


def test_rendered_methodology_carries_the_differences_and_the_live_rule(tmp_path, monkeypatch):
    text = _rendered(tmp_path, monkeypatch)
    assert "## Known limits" in text and "How the tracker differs from the original study." in text
    assert text.index("## Known limits") < text.index("How the tracker differs from the original study.")
    assert text.index("How the tracker differs from the original study.") < text.index("## Reproducibility")
    # The residual rule where the pipeline is described is the tracker's, with the live numbers.
    assert ("concerns China by the tracker's own rule, which is not in the original study's codebook (Known limits): "
            + RULE + ", otherwise not relevant." in text)
    assert "{relevance_rule}" not in text and "at least three distinct China terms, or five occurrences" not in text


def test_site_methodology_carries_the_four_differences():
    site = _site()
    assert "The original study coded 596 articles across four countries: Italy, Canada, Australia and Singapore." in site
    for head in ("How the tracker differs from the original study.", "The unit counted.",
                 "Not relevant, by the tracker's rule.", "Unchecked state sourcing is looser than the study's.",
                 "Section headers are not evidence here."):
        assert "<strong>%s</strong>" % head in site, head
    assert "The original study counted identical wire copy once per publication." in site
    assert "a story shown by one shared widget across a network's mastheads counts once per country" in site
    assert "the tracker's counts and shares cannot be set beside the study's" in site
    # The AAP example is referred to, not repeated: it stands in Placements and underlying items, above.
    assert "The Australian example under Placements and underlying items above" in site and "1,350" not in site
    assert INDEX.index("<strong>Placements and underlying items.</strong>") < INDEX.index('id="study-differences"')
    assert "which is not in the study's codebook" in site and RULE in site
    assert '"%s"' % QUOTE in site and "27 of 58" in site and "probably overcounts relative to the study" in site
    assert "goes into the model's instructions after 23 October 2026" in site
    assert '"partnership content from GlobeNewswire"' in site and "never the outlet, the address or the section" in site
    assert "can therefore be missed, or end up undetermined" in site
    assert "Giving the model the address and the section is planned after 23 October 2026." in site


def test_readme_and_faq_point_to_the_differences():
    readme = _flat(README)
    assert "The tracker's figures cannot be set beside the original study's, for four reasons" in readme
    assert "counted identical wire copy once per publication" in readme
    assert "probably overcounts relative to the study" in readme and "after 23 October 2026" in readme
    faq = next(f["answer"] for f in CONTENT["faq"] if f["question"] == "How reliable is the unchecked state sourcing count?")
    assert "looser than the original study's measure, which scored verification on five levels" in faq
    assert not re.search(r"\b(we|We|our|Our|us)\b", faq)


def test_the_code_still_does_what_the_disclosures_say():
    # Shared widgets count once per country, and placements.py says so.
    assert "counts once per country" in placements.__doc__
    assert "one underlying item" in placements.__doc__
    # Arrival is its own variable and never a route; section feeds are the only arrivals besides the editorial feed.
    assert classify_rules.ARRIVAL_IDS == ["editorial_feed", "press_release_section", "sponsored_section",
                                          "partner_section"]
    assert not set(classify_rules.ARRIVAL_IDS) & set(classify_rules.ROUTE_IDS)
    assert classify_rules.arrival_of(json.dumps(["section:sponsored"])) == "sponsored_section"
    # The model's message carries the headline and body, never the outlet, the address or the section.
    msg = classify_llm.build_user_message("Headline here", "Body text here.")
    assert msg.startswith("Headline: Headline here\n\nArticle text:\nBody text here.")
    assert "http" not in msg and "section" not in msg.lower() and "outlet" not in msg.lower()
    # A bare page chrome line that names no state entity is not a disclosure.
    res = classify_rules.match_signatures("Company announces results", "The company reported quarterly revenue.",
                                          None, labels="This section is Partnership Content supplied by GlobeNewswire")
    assert res["decision"] != "A"


def test_no_dashes_codes_or_university_names_in_the_new_text():
    for name, text in (("methodology", _limits()), ("index.html", _site()), ("README.md", _flat(README))):
        assert "—" not in text and "–" not in text, name
        assert "Category A" not in text and "Category B" not in text and "Category C" not in text, name
        assert not re.search(r"\b(University|Università|Stanford|college)\b", text), name
