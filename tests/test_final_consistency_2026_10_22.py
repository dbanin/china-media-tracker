"""The third kind of undetermined item (pipeline/model_only.py, kind (c), 2026-10-22): a model uncredited copy
whose recorded reference is not settled state origin is undetermined too. Every live text that lists the kinds
of undetermined item names it, in the voice of the other two, and no live text keeps the old provisional
category or its counts."""
import json
import re
from pathlib import Path

from pipeline import methodology

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
CONTENT_TEXT = (ROOT / "docs" / "content.json").read_text(encoding="utf-8")
CONTENT = json.loads(CONTENT_TEXT)
README = (ROOT / "README.md").read_text(encoding="utf-8")
METHOD_SRC = (ROOT / "pipeline" / "methodology.py").read_text(encoding="utf-8")


def _paragraph(html, head):
    start = html.index("<p><strong>%s" % head)
    return html[start:html.index("</p>", start)]


def test_routes_paragraph_names_all_three_kinds():
    p = _paragraph(INDEX, "Routes and arrival.")
    assert "An unattributed label rests on the model's judgement alone" in p
    assert "a sponsored disclosure the model named that the rules" in p
    assert ("An uncredited copy counts only when the item it repeats is settled state origin; a copy of an "
            "undetermined item repeats no known state original, so it is undetermined too.") in p
    assert p.index("so it is undetermined too") < p.index("As in the original study")


def test_methodology_route_paragraph_names_all_three_kinds():
    assert ("did not find, is undetermined and in no count (see Undetermined items below), and\n"
            "so is an uncredited copy of an undetermined item, which repeats no known state\noriginal.") in METHOD_SRC


def test_state_origin_split_names_all_three_kinds():
    text = methodology.state_origin_split_text({"undetermined": {"count": 1313}}, {"A": 1017, "Ar": 900, "Al": 117})
    assert ("A further 1,313 model judgements of state origin name no evidence a reader can check, name a sponsored "
            "disclosure the rules did not find, or repeat an undetermined item; they are undetermined") in text


def test_undetermined_route_card_and_readme_name_the_copies():
    card = CONTENT["routes"]["unattributed"]["how_it_arrives"]
    assert "an uncredited copy of an item that is undetermined itself" in card
    assert "excluded from every count" in card
    assert "and its uncredited copy calls whose recorded item is undetermined itself." in README


def test_australian_duplicate_figures_name_the_copies():
    assert ("in Australia model calls with no checkable marker and uncredited copies of such calls; without them "
            "the fall is also tenfold.") in INDEX
    assert "in Australia all model calls with no checkable marker" not in INDEX


def test_no_live_text_keeps_the_old_category_or_its_counts():
    live = {"index.html": INDEX, "content.json": CONTENT_TEXT, "README.md": README,
            "methodology.py": METHOD_SRC, "app.js": (ROOT / "docs" / "app.js").read_text(encoding="utf-8"),
            "compute.js": (ROOT / "docs" / "compute.js").read_text(encoding="utf-8")}
    for name, text in live.items():
        for stale in ("shown apart as provisional", "Model judgement alone, provisional", "1,077", "1,253", "1,123"):
            assert stale not in text, (name, stale)
        # "rejoin" survives only as "never rejoin(s)".
        for m in re.finditer(r"rejoin", text):
            assert re.search(r"(never|not)\s+rejoin", text[max(0, m.start() - 8):m.end()]) or \
                "rejoins_state_origin" in text[m.start() - 1:m.end() + 14], (name, text[m.start() - 80:m.end() + 40])


def test_new_text_keeps_the_site_rules():
    for text in (_paragraph(INDEX, "Routes and arrival."), CONTENT["routes"]["unattributed"]["how_it_arrives"],
                 CONTENT["country_notes"]["SGP"]):
        assert "\u2014" not in text and "\u2013" not in text
        assert "Category A" not in text
