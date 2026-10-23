"""The country panel's time series (renderMini in docs/app.js, .mini rules in docs/style.css), enlarged on
2026-10-22: drawn at its shown size rather than stretched from a 360 by 88 box, taller, with day and count
ticks, an axis label naming the count and its basis, a legend of line keys, the unusual days the spikes
measure marks for the country, and a crosshair readout; the panel widens beside the map while a country
is open. The browser checks at 1280 and 375 pixels are in the changelog entry; these hold the wiring."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def _app():
    return (DOCS / "app.js").read_text(encoding="utf-8")


def _css():
    return (DOCS / "style.css").read_text(encoding="utf-8")


def _mini():
    app = _app()
    start = app.index("  function renderMini(iso, relay) {")
    return app[start:app.index("  function loadArticles(iso) {")]


def _rem(rule):
    m = re.search(r"height:\s*([\d.]+)rem", rule)
    assert m, rule
    return float(m.group(1))


def test_drawn_at_its_measured_size_not_a_fixed_box():
    mini = _mini()
    assert "var w = 360, h = 88" not in mini
    assert "getBoundingClientRect()" in mini
    assert 'svgm.attr("viewBox", "0 0 " + w + " " + h)' in mini


def test_taller_on_desktop_and_phone():
    css = _css()
    base = re.search(r"^\.mini \{[^}]*\}", css, re.M).group(0)
    assert _rem(base) >= 13, "the chart was 5.5rem (88px); about 2.5 times that is wanted"
    phone = re.search(r"@media \(max-width: 640px\) \{ \.mini \{[^}]*\} \}", css).group(0)
    assert _rem(phone) >= 11
    assert "5.5rem" not in base


def test_axis_ticks_label_and_legend():
    mini = _mini()
    assert "Articles per day in " in mini and "monitored outlets, busiest day " in mini
    assert "y.ticks(4)" in mini and "Math.round(t) === t" in mini
    assert "x.ticks(12)" in mini and 'd3.utcFormat(sameYear ? "%-d %b" : "%-d %b %Y")' in mini
    assert 'legend.className = "mini-legend"' in mini
    for code in ('clearerLabel("A"', 'clearerLabel("B"', 'clearerLabel("pending"'):
        assert code in mini


def test_lines_follow_the_mark_spec_in_site_tokens():
    css = _css()
    for cls, colour in (("a", "var(--gold)"), ("b", "var(--bone-soft)"), ("p", "var(--ash)")):
        rule = re.search(r"^\.mini path\.%s[^{]*\{[^}]*\}" % cls, css, re.M).group(0)
        assert colour in rule
        width = float(re.search(r"stroke-width:\s*([\d.]+)", rule).group(1))
        assert width >= 2
    text = re.search(r"^\.mini text \{[^}]*\}", css, re.M).group(0)
    assert float(re.search(r"font-size:\s*([\d.]+)rem", text).group(1)) >= 0.75


def test_crosshair_readout_by_pointer_and_keyboard():
    mini = _mini()
    assert 'tip.className = "mini-tip"' in mini
    assert '"pointermove"' in mini and '"pointerleave"' in mini
    assert "ArrowLeft" in mini and "ArrowRight" in mini and 'node.setAttribute("tabindex", "0")' in mini
    assert "textContent" in mini and "d3.bisector" in mini


def test_unusual_days_marked_only_when_the_measure_is_on():
    app = _app()
    fn = app[app.index("  function miniSpikes(iso) {"):app.index("  function renderMini(iso, relay) {")]
    assert "state.measureData.spikes" in fn and "MS.isOff(b)" in fn and "s.country === iso" in fn
    assert "MS.provisionalSuffix(b)" in fn
    mini = _mini()
    assert '"mini-spike" + (clean ? "" : " spike-check")' in mini


def test_series_ends_on_the_last_complete_day():
    mini = _mini()
    assert "lastCompleteDay()" in mini and "d <= lastDay" in mini


def test_panel_widens_beside_the_map_while_a_country_is_open():
    app = _app()
    panel = app[app.index("  function renderPanel() {"):app.index("  function bindClose()")]
    assert 'classList.toggle("country-open", !!state.selected)' in panel
    css = _css()
    assert "@media (min-width: 1101px) { .map-card > main.country-open { grid-template-columns: minmax(0, 1fr) 28rem; } }" in css


def test_resize_listener_is_guarded_for_the_test_shim():
    app = _app()
    assert 'if (typeof window.addEventListener === "function") window.addEventListener("resize", function () {' in app
    assert "renderMini(miniState.iso, miniState.relay)" in app


def test_no_dashes_in_the_new_text():
    for text in (_mini(), _css()):
        assert "—" not in text and "–" not in text
