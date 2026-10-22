"""Wording fixes of 2026-10-22: the original study's size, the independent journalism definition, the
uncredited copy lower bound, the Chinese-language blind spot and the Australian duplicate counting note."""
import ast
import json
import re
from pathlib import Path

from pipeline import lookalike, methodology

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
INDEX = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
CONTENT = json.loads((ROOT / "docs" / "content.json").read_text(encoding="utf-8"))
METH_PY = (ROOT / "pipeline" / "methodology.py").read_text(encoding="utf-8")
EXPORT_PY = (ROOT / "pipeline" / "export.py").read_text(encoding="utf-8")


def _flat(text):
    return " ".join(text.split())


def _meta_categories():
    """The meta categories block of export.build_meta, read from the source: the dict literal whose keys are
    exactly A, B, C and not_relevant."""
    for node in ast.walk(ast.parse(EXPORT_PY)):
        if isinstance(node, ast.Dict) and node.keys and all(isinstance(k, ast.Constant) for k in node.keys):
            if [k.value for k in node.keys] == ["A", "B", "C", "not_relevant"] and \
                    all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in node.values):
                cats = ast.literal_eval(node)
                if cats["A"].startswith("State origin."):
                    return cats
    raise AssertionError("meta categories block not found")


def test_original_study_is_596_articles_in_four_countries():
    assert "596 articles across four countries: Italy, Canada, Australia and Singapore" in _flat(README)
    texts = [("README.md", README), ("index.html", INDEX), ("content.json", json.dumps(CONTENT)),
             ("methodology.py", METH_PY)]
    texts += [(p.name, p.read_text(encoding="utf-8")) for p in (ROOT / "docs").glob("*.js")]
    for name, text in texts:
        flat = _flat(text)
        assert "484 articles" not in flat, name
        assert "Italy, Canada and Australia and runs it" not in flat, name
        assert "the three study countries" not in flat, name


def test_independent_journalism_leads_with_what_it_is_not():
    lead = "Not state origin and not unchecked state sourcing."
    c = _meta_categories()["C"]
    assert c.startswith("Independent journalism. " + lead)
    site = CONTENT["categories"]["independent_journalism"]
    assert site["label"] == "Independent journalism" and "proposed_clearer_label" not in site
    html = re.search(r"<p><strong>Independent journalism\.</strong> (.*?)</p>", INDEX).group(1)
    for name, text in (("export.py", c), ("content.json", site["definition"]), ("index.html", html)):
        assert lead in text, name
        assert "The outlet's own reporting" not in text.split(lead)[0], name
        for phrase in ("own reporting", "Reuters or AP", "position, policy, warning, threat, opinion or denial",
                       "press release"):
            assert phrase in text, (name, phrase)
        assert "Category" not in text, name


def test_uncredited_copy_limit_names_what_it_can_and_cannot_match():
    text = methodology.uncredited_copy_limit_text()
    assert text.startswith("The uncredited copy count is a lower bound.")
    assert "%d days before it to %d days after it" % (lookalike.WINDOW_BEFORE_DAYS, lookalike.WINDOW_AFTER_DAYS) in text
    assert "rests on the model's judgement alone is never such a reference" in text
    assert "never a reference for this match" in text and "ECNS" in text
    assert "Italian, Spanish or Portuguese" in text and "editions in other languages, which the tracker does not read" in text
    limits = methodology.known_limits_text({}, [])
    assert text in limits
    assert limits.index("Translated state copy run without a credit") < limits.index(text)
    for name, page in (("index.html", INDEX), ("README.md", _flat(README))):
        assert "The uncredited copy count is a lower bound." in page, name
        assert "Italian, Spanish or Portuguese" in page, name


def test_lookalike_references_are_credited_monitored_labels_only():
    """The disclosure rests on this: the route's index reads classifications only, never reference_items."""
    src = (ROOT / "pipeline" / "lookalike.py").read_text(encoding="utf-8")
    body = src.split("def build_index", 1)[1].split("\ndef ", 1)[0]
    assert "FROM classifications c" in body and "reference_items" not in body
    cond, routes = lookalike.credited_reference_sql("c")
    assert "unattributed" not in routes and "uncredited_copy" not in routes


def test_chinese_language_exclusion_is_a_blind_spot_beside_the_rule():
    limits = methodology.known_limits_text({}, [])
    assert "Chinese-language outlets are excluded." in limits
    for name, text in (("methodology", limits), ("README.md", _flat(README)), ("index.html", INDEX)):
        assert "registered but not monitored" in text, name
        assert "lifestyle section of a Chinese-language diaspora paper in Canada" in text, name
        assert "Chinese-language diaspora media is therefore not counted" in text or \
            "Chinese-language diaspora media is not counted" in text, name
    assert "## The Chinese language rule" in README and "registered with `active: false`" in README


def test_australia_duplicate_counting_is_explained():
    for name, text in (("methodology.py", _flat(METH_PY)), ("index.html", INDEX)):
        assert "cut Australia's state origin from 1,350 placements to 135 over 2026-09-03 to 2026-10-15" in text, name
        assert "tenfold" in text and "ten ACM mastheads" in text, name
        assert "original study sampled newspaper pages" in text and "not comparable" in text, name
        assert "sensitive to how duplicates are defined" in text, name


def test_repeat_shares_state_their_basis():
    flat = _flat(METH_PY)
    assert ("of the items each country screened from 2026-10-01 to 2026-10-11, about 16 percent in Britain" in flat)


def test_no_stale_world_shares_and_no_dashes_in_new_text():
    for name, text in (("README.md", README), ("index.html", INDEX), ("content.json", json.dumps(CONTENT)),
                       ("methodology.py", METH_PY)):
        assert not re.search(r"\b(4\.4|7\.3) percent", text), name
    for text in (methodology.uncredited_copy_limit_text(), methodology.known_limits_text({}, []),
                 _meta_categories()["C"], CONTENT["categories"]["independent_journalism"]["definition"]):
        assert "—" not in text and "–" not in text
