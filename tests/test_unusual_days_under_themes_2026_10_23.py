"""The Unusual days block (measure spikes, docs/m_spikes.js) sits directly under the theme chart: inside section#themes,
after the theme grid and its timeline, before the next section, with the same ids, the same hidden start and the same
slot lookup in docs/measures.js; What we found keeps everything else and no longer carries it."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


def _section(sec_id):
    m = re.search(r'<section id="%s"[^>]*>.*?</section>' % sec_id, HTML, re.S)
    assert m, sec_id
    return m.group(0)


def test_block_sits_under_the_theme_chart():
    themes = _section("themes")
    for anchor in ('id="theme-grid-wrap"', 'id="theme-legend"', 'id="theme-timeline"', 'id="th-hint"',
                   'id="findings-measures"', 'id="findings-spikes"'):
        assert anchor in themes, anchor
    i = themes.index
    assert i('id="theme-grid-wrap"') < i('id="theme-legend"') < i('id="theme-timeline"') < i('id="th-hint"') < i('id="findings-measures"')
    assert i('id="findings-measures"') < i('id="findings-spikes"')
    # Not inside the timeline: the timeline div closes before the block opens.
    tl = themes[i('id="theme-timeline"'):i('id="findings-measures"')]
    assert tl.count("<div") <= tl.count("</div>"), "the block follows the timeline, not inside it"
    # Directly after Themes: the next section is What we found.
    after = HTML[HTML.index('<section id="themes"'):]
    assert re.search(r'</section>\s*<section id="overview"', after)


def test_block_starts_hidden_once_and_only_once():
    assert HTML.count('id="findings-measures"') == 1 and HTML.count('id="findings-spikes"') == 1
    assert re.search(r'<div id="findings-measures"[^>]*\shidden', HTML), "hidden until the measure is on"


def test_what_we_found_keeps_the_rest():
    overview = _section("overview")
    assert "findings-measures" not in overview and "findings-spikes" not in overview
    for anchor in ('id="overview-title"', 'id="kpis"', 'id="findings-box"', 'id="top10"', 'id="top10-list"', 'id="top10-notes"'):
        assert anchor in overview, anchor


def test_slot_lookup_finds_the_block():
    js = (ROOT / "docs" / "measures.js").read_text(encoding="utf-8")
    assert '{id: "findings-measures", measures: ["spikes"]}' in js
    assert 'spikes: "findings-spikes"' in js


def test_spacing_reads_as_part_of_themes():
    css = (ROOT / "docs" / "measures.css").read_text(encoding="utf-8")
    block = css[css.index("/* ==== BEGIN spikes ==== */"):css.index("/* ==== END spikes ==== */")]
    assert "#themes #findings-measures { margin: 1.6rem 0 0; padding: 1.1rem 0 0; border-top: 1px solid var(--rule); max-width: none; }" in block
    assert "#themes #findings-spikes .m-spikes { margin-top: 0; padding-top: 0; border-top: 0; max-width: 46rem; }" in block
    # The timeline above has the same rhythm.
    style = (ROOT / "docs" / "style.css").read_text(encoding="utf-8")
    assert ".th-timeline { margin: 1.6rem 0 0; padding: 1.1rem 0 0; border-top: 1px solid var(--rule); }" in style


def test_comments_name_the_new_place():
    sp = (ROOT / "docs" / "m_spikes.js").read_text(encoding="utf-8")
    assert "#findings-spikes under the theme chart" in sp and "under What we found:" not in sp
    for text in (HTML, sp):
        assert "—" not in text and "–" not in text
