"""The theme grid has no Worldwide distribution row (owner request, 2026-10-22). The grid compares countries,
and the press release wire stratum (WLD) is not a country: it had shown as the third row, coloured on a cap
its own large counts pushed up. The behaviour is checked in tests/frontend_smoke.js (C.themeGridRows,
C.themeScaleCap); these static checks keep docs/app.js wired to those helpers and the stratum on the rest
of the site."""
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
APP = (DOCS / "app.js").read_text(encoding="utf-8")
COMPUTE = (DOCS / "compute.js").read_text(encoding="utf-8")
SMOKE = (Path(__file__).resolve().parent / "frontend_smoke.js").read_text(encoding="utf-8")


def body(src, name):
    """The text of function name, from its declaration to the next top level function in the file."""
    start = src.index("function " + name + "(")
    nxt = re.search(r"\n  function \w+\(", src[start + 1:])
    return src[start:start + 1 + nxt.start()] if nxt else src[start:]


def test_world_grid_rows_come_from_the_helper():
    render = body(APP, "renderThemes")
    assert "C.themeGridRows(model.rows, sortKey, THEME_ROWS)" in render
    assert "model.rows.slice().sort" not in render
    assert "sel.iso !== C.WORLDWIDE_ISO" in render


def test_helper_drops_the_stratum_and_is_exported():
    helper = body(COMPUTE, "themeGridRows")
    assert "r.iso !== WORLDWIDE_ISO" in helper
    assert "themeGridRows: themeGridRows" in COMPUTE


def test_grid_cap_leaves_the_stratum_out():
    cap = body(COMPUTE, "themeScaleCap")
    assert "if (iso ? c !== iso : c === WORLDWIDE_ISO) return;" in cap


def test_days_view_has_no_stratum():
    days = body(APP, "renderCountryThemes")
    guard = days.index("if (iso === C.WORLDWIDE_ISO)")
    assert guard < days.index("themeKey(iso)") and guard < days.index('grid.classList.add("days")')


def test_grid_texts_are_plain():
    text = body(APP, "themeGridWiresText") + body(APP, "renderThemes")
    assert not re.search("[–—]", text)
    assert "Category A" not in text and "Category B" not in text
    assert "not a country, so they have no row here" in text


def test_smoke_asserts_no_wld_row():
    assert "theme grid rows never include WLD" in SMOKE
    assert "the theme grid's cap leaves the stratum out" in SMOKE


def test_stratum_stays_elsewhere():
    """Only the grid drops it: the legend line, top 10 line, ranked list and themes mix keep it."""
    assert "C.worldwideLegendLine(wld.mv, state.metric)" in APP
    assert 'top10Text(t, "worldwide"' in APP
    model = body(APP, "themeModel")
    assert "WORLDWIDE" not in model
