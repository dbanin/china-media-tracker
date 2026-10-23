"""Warnings at the foot, for now (owner, 2026-10-22: "for now move the warnings to the bottom"). The preliminary
figures banner (div#preliminary) and the data notice (div#data-notice, beside which app.js adds div#stale-notice)
moved, intact, from between the header and the map into one labelled section, section#page-notes, after the
methodology and above the footer. The header leads straight into the map. The per-figure provisional marks in the
map legend, the ranked list and the country panel are labels on data and stay where they are. The move is in one
commented place in index.html and one in style.css, so it is easy to undo."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
INDEX = (DOCS / "index.html").read_text(encoding="utf-8")
APP = (DOCS / "app.js").read_text(encoding="utf-8")
CSS = (DOCS / "style.css").read_text(encoding="utf-8")


def _section():
    m = re.search(r'<section class="sec page-notes" id="page-notes"[^>]*>.*?</section>', INDEX, re.S)
    assert m, "section#page-notes missing"
    return m.group(0)


def test_header_leads_straight_into_the_map():
    between = INDEX[INDEX.index("</header>"):INDEX.index('<section id="map-card"')]
    for gone in ("prelim", "data-notice", 'class="notice', 'role="note"', 'role="status"', "<div"):
        assert gone not in re.sub(r"<!--.*?-->", "", between, flags=re.S), gone


def test_both_warnings_sit_in_one_labelled_section_above_the_footer():
    sec = _section()
    at = INDEX.index(sec)
    assert INDEX.index('<section class="sec method" id="method">') < at
    assert INDEX.index("</section>", INDEX.index('<section class="sec method" id="method">')) < at
    assert at + len(sec) < INDEX.index("<footer>")
    for anchor in ('id="preliminary"', 'role="note"', 'aria-labelledby="prelim-title"', 'id="prelim-title"',
                   'id="prelim-text"', 'id="prelim-link"', '<div id="data-notice" class="notice hidden"></div>'):
        assert sec.count(anchor) == 1 and INDEX.count(anchor) == 1, anchor
    assert sec.index('id="preliminary"') < sec.index('id="data-notice"')
    assert 'aria-labelledby="page-notes-title"' in sec
    assert '<h2 id="page-notes-title">Notes on these figures</h2>' in sec
    # The banner's heading sits one level under the section heading.
    assert '<h3 class="prelim-title" id="prelim-title"><em>Preliminary</em> figures</h3>' in sec
    assert '<h2 class="prelim-title"' not in INDEX


def test_the_move_is_commented_in_one_place_and_easy_to_undo():
    assert INDEX.count("WARNINGS AT THE FOOT, for now") == 1 and CSS.count("WARNINGS AT THE FOOT, for now") == 1
    assert INDEX.count("WARNINGS MOVED TO THE FOOT, for now") == 1
    assert INDEX.index("WARNINGS MOVED TO THE FOOT") < INDEX.index('<section id="map-card"')
    assert INDEX.index("WARNINGS AT THE FOOT") < INDEX.index('id="page-notes"')


def test_app_finds_the_warnings_by_id_wherever_they_sit():
    for call in ('el("prelim-title")', 'el("prelim-text")', 'el("data-notice")', 'dn.parentNode.insertBefore(n, dn.nextSibling)'):
        assert call in APP, call
    assert 'var steps = [\n        ["renderPreliminary", renderPreliminary],' in APP
    assert "top of the page" not in APP[APP.index("function loadAll"):APP.index("function loadAll") + 10] and \
        "the notice at the top of the page" not in APP


def test_per_figure_provisional_marks_stay_with_the_data():
    main = INDEX[INDEX.index("<main>"):INDEX.index("</main>")]
    for anchor in ('<div id="legend"></div>', '<div id="bars"></div>', '<aside id="panel">'):
        assert anchor in main, anchor
    assert INDEX.index("</main>") < INDEX.index('id="page-notes"')
    assert "lg-prov" in APP


def test_page_notes_styles_fit_a_phone_and_use_the_site_frame():
    rules = CSS[CSS.index("WARNINGS AT THE FOOT, for now"):]
    rules = rules[:rules.index(".page-notes .notice")] + rules[rules.index(".page-notes .notice"):].split("\n", 1)[0]
    assert ".page-notes {" in rules and ".page-notes .prelim-wrap {" in rules and ".page-notes .notice {" in rules
    assert not re.search(r"\.page-notes[^{]*\{[^}]*\bwidth:\s*\d{3,}px", rules), "a fixed width would scroll at 375px"
    assert "var(--s" in rules


def test_no_dashes_in_the_new_text():
    for s in (_section(), CSS[CSS.index("WARNINGS AT THE FOOT"):CSS.index(".page-notes .notice")]):
        assert "—" not in s and "–" not in s
