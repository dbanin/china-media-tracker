"""The site interface (docs/app.js, docs/index.html controls, docs/style.css): the pure helpers run under
jsc or node (tests/app_smoke.js), and the markup and wiring the browser checks rely on are asserted here."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


def _app():
    return (DOCS / "app.js").read_text(encoding="utf-8")


def _html():
    return (DOCS / "index.html").read_text(encoding="utf-8")


def test_app_helpers_smoke(tmp_path):
    """app.js loads with no page around it (init waits for a document that never finishes loading) and its
    helpers pass tests/app_smoke.js."""
    stub = ('var document = {readyState: "loading", addEventListener: function () {}, getElementById: function () { return null; },'
            ' querySelector: function () { return null; }, querySelectorAll: function () { return []; }};\n')
    files = [DOCS / "vendor" / "d3.v7.min.js", DOCS / "compute.js", DOCS / "app.js", ROOT / "tests" / "app_smoke.js"]
    if JSC.exists():
        shim = tmp_path / "shim.js"
        shim.write_text("var self = this; var window = this;\n"
                        'var console = {log: function (s) { print(String(s)); }, error: function (s) { print("ERROR " + String(s)); }};\n'
                        + stub + "".join('load("%s");\n' % f for f in files), encoding="utf-8")
        r = subprocess.run([str(JSC), str(shim)], capture_output=True, text=True)
    elif shutil.which("node"):
        shim = tmp_path / "shim.js"
        shim.write_text("const fs = require('fs'), vm = require('vm');\n"
                        "const ctx = {console: console, Date: Date}; ctx.self = ctx; ctx.window = ctx; vm.createContext(ctx);\n"
                        "vm.runInContext(%r, ctx);\n" % stub
                        + "".join("vm.runInContext(fs.readFileSync(%r, 'utf8'), ctx);\n" % str(f) for f in files), encoding="utf-8")
        r = subprocess.run(["node", str(shim)], capture_output=True, text=True)
    else:
        pytest.skip("neither jsc nor node is installed")
    assert r.returncode == 0 and "app smoke ok" in r.stdout, r.stdout + r.stderr


def test_segmented_controls_are_labelled_groups():
    """Every segmented control in the page is a labelled group whose buttons carry aria-pressed, as the
    Distribution toggle always was; the page keeps the attribute in step (syncPressed)."""
    html = _html()
    segs = re.findall(r'<div class="segment[^"]*"[^>]*>', html)
    assert len(segs) >= 14
    for s in segs:
        assert 'role="group"' in s and ("aria-labelledby=" in s or "aria-label=" in s), s
        m = re.search(r'aria-labelledby="([^"]+)"', s)
        if m:
            assert html.count('id="%s"' % m.group(1)) == 1, m.group(1)
    for block in re.findall(r'<div class="segment[^"]*"[^>]*>(.*?)</div>', html, re.S):
        for b in re.findall(r"<button[^>]*>", block):
            assert 'aria-pressed="%s"' % ("true" if 'class="active"' in b else "false") in b, b
    app = _app()
    assert "function syncPressed()" in app and app.count("syncPressed();") >= 6


def test_ranked_list_has_its_own_scope_and_country_search():
    """#map-wrap is hidden at 720 pixels or less, so the ranked list carries Scope and a Country search."""
    html = _html()
    bars = re.search(r'<div class="bars-head">(.*?)<p id="bars-caption"', html, re.S).group(1)
    assert "data-scope-group" in bars and 'id="list-country"' in bars and "data-country-pick" in bars
    assert len(re.findall(r'id="list-country-list"', html)) == 1


def test_small_screen_view_toggle_list_cut_and_panel_reveal():
    app = _app()
    assert '"(max-width: 720px)"' in app and "function syncDefaultView()" in app and 'syncViewButtons(smallScreen() ? "list" : "map")' in app
    assert "var BARS_SMALL = 25" in app and 'id="bars-more"' in app
    assert "function revealPanel(force)" in app and "state._revealed = state.selected ? revealPanel(false) : false;" in app


def test_timeline_ends_on_the_last_complete_day():
    app = _app()
    setup = app[app.index("function setupTimeline()"):app.index("function setDay(i)")]
    assert "(!lastDay || d <= lastDay)" in setup and "scrub.value = Math.max(0, tlDays.length - 1);" in setup
    assert "if (Number(el(\"scrub\").value) >= tlDays.length - 1) setDay(0);" in app
    assert "if (Number(el(\"th-scrub\").value) >= tlDays.length - 1) setThemeDay(0);" in app


def test_opening_load_reads_only_the_months_the_default_view_needs():
    """Only the months covering the 31 days ending the last complete day load first; every other consumer
    waits for ensureAllMonths or falls back to global_series.json and the export's totals."""
    app = _app()
    load = app[app.index("function loadAll()"):app.index("function renderLoadFailures()")]
    assert "C.shiftDate(openingDay, -30).slice(0, 7)" in load and "m >= firstMonth" in load
    for consumer in ("function togglePlay()", "function toggleThemePlay()", "function exportDaily()"):
        body = app[app.index(consumer):app.index(consumer) + 400]
        assert "ensureAllMonths()" in body, consumer
    assert "demandMonths(state.endDate, state.windowDays)" in app and "demandThemeMonths()" in app
    assert "function exportAllTimeAgg(lastDay)" in app and "function seriesTotal(d, mi, distribution)" in app
    assert "function scaleDays(w)" in app and "partialScaleNote()" in app


def test_spike_event_sets_measure_window_and_distribution():
    app = _app()
    listener = app[app.index('document.addEventListener("tracker:select"'):]
    assert "openView(d);" in listener[:400]
    assert "open: openView" in app and "function viewFields(d)" in app


def test_share_of_output_reads_per_thousand_with_its_denominator():
    app = _app()
    assert "function outputShareGap(own, entry)" in app and "function valueText(v, metric)" in app
    assert "per 1,000 items published by each country's counted outlets" in app
    assert "<h3>Monitored outlets, all time to the last export</h3>" in app


def test_route_select_is_disabled_off_state_origin():
    app, html = _app(), _html()
    assert 'sel.disabled = state.measure !== "a";' in app
    assert 'id="route-note"' in html and 'aria-describedby="route-note"' in html


def test_contrast_tokens():
    css = (DOCS / "style.css").read_text(encoding="utf-8")
    m = re.search(r"--ash-faint:\s*#([0-9a-fA-F]{6})", css)
    assert m and int(m.group(1)[:2], 16) >= 0x8d, "--ash-faint at least #8d877c"
    assert "d3.lab(color).l > 55" in _app()


def test_new_human_study_states_have_their_own_words():
    """sample_superseded and stale have a sentence each, and a failure with no kappa uses failed_reason."""
    app = _app()
    assert 'sample_superseded: "the first hand coding sample was set aside' in app
    assert 'stale: "the last study checked labels made under earlier instructions"' in app
    short = app[app.index("function humanStudyShort()"):app.index("function stabilityUnstable()")]
    assert short.index("studyFailedReason()") < short.index('"hand coding put kappa below "')
    assert "hs.failed_reason" in app[app.index("function studyFailedReason()"):app.index("function studyFailedReason()") + 400]


def test_issuer_line_and_badge():
    app = _app()
    assert "renderIssuerLine();" in app and "issuerBadge(a.issuer_type)" in app


def test_findings_box_names_both_distributions_and_stale_notice():
    app = _app()
    assert '" (both distributions)"' in app
    assert '["renderStaleNotice", renderStaleNotice]' in app and "function staleLines(meta, gens, now)" in app
