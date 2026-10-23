"""The map's warning triangles are off for now (owner's request, 2026-10-23), behind one switch in docs/app.js,
while each country's data warnings still reach the tooltip, the ranked list, the country panel and the CSV export."""
import json
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


def _fn(src, name):
    """The body of a function declared as `function name(`, up to the next top level function at the same indent."""
    m = re.search(r"\n(\s*)function %s\(" % re.escape(name), src)
    assert m, name
    rest = src[m.end():]
    end = re.search(r"\n%sfunction \w+\(" % m.group(1), rest)
    return rest[: end.start() if end else len(rest)]


def test_switch_is_one_commented_false_flag():
    app = _app()
    assert len(re.findall(r"var SHOW_WARNING_MARKERS\s*=", app)) == 1
    m = re.search(r"/\*([^*]|\*(?!/))*\*/\n\s*var SHOW_WARNING_MARKERS = false;", app)
    assert m, "the switch is false and carries its comment"
    assert "owner's request" in m.group(0) and "2026-10-23" in m.group(0) and "set true to restore" in m.group(0)


def test_no_triangle_drawn_while_off():
    """Every warn-marker append sits behind the switch; the marker layer is still cleared on each render, so a
    restored switch draws on a clean layer and the zoomed map has nothing to scale."""
    app = _app()
    body = _fn(app, "renderMap")
    assert 'gMarkers.selectAll("*").remove();' in body
    assert app.count('attr("class", "warn-marker")') == 1
    guarded = re.search(r'if \(SHOW_WARNING_MARKERS\) gCountries\.selectAll\("path\.country"\)\.each\(function \(f\) \{(.*?)\n    \}\);', body, re.S)
    assert guarded and 'attr("class", "warn-marker")' in guarded.group(1) and "d3.symbolTriangle" in guarded.group(1)
    assert app.count("d3.symbolTriangle") == 1


def test_legend_item_behind_switch():
    app = _app()
    legend = _fn(app, "renderLegend")
    i = legend.index("Data warning: hover the country to read it")
    start = legend.rfind("\n", 0, i)
    assert legend[start:i].strip().startswith("(SHOW_WARNING_MARKERS ? '<li><svg class=\"sw-tri\"")
    assert legend.count("sw-tri") == 1


def test_warnings_still_reach_tooltip_list_panel_and_export():
    app = _app()
    assert "Warning: ' + esc(w) + '</div>'" in _fn(app, "showTip") and "countryWarnings(e, agg)" in _fn(app, "showTip")
    bars = _fn(app, "renderBars")
    assert "countryWarnings(entry, agg)" in bars and 'class="bar-warn"' in bars and ", data warning" in bars
    assert "countryWarnings(entry, agg).forEach(function (w) { html += '<p class=\"warn\">Warning: ' + esc(w) + '</p>'; });" in app
    assert 'r.warnings = countryWarnings(state.latest.countries[r.iso] || {}, agg).join("; ");' in app


def test_reader_text_no_longer_points_to_map_triangles():
    content = json.loads((DOCS / "content.json").read_text(encoding="utf-8"))
    flat = json.dumps(content).lower()
    assert "triangle" not in flat
    assert "triangle" not in (DOCS / "index.html").read_text(encoding="utf-8").lower()
    assert "triangle" not in (ROOT / "pipeline" / "methodology.py").read_text(encoding="utf-8").lower()
    text = content["captions"]["ranked_list"]
    assert "exclamation mark" in text and "country panel" in text


def test_markers_smoke(tmp_path):
    """app.js loads with no page around it and tests/map_markers_smoke.js passes: the switch reads false and
    countryWarnings still returns every warning."""
    stub = ('var document = {readyState: "loading", addEventListener: function () {}, getElementById: function () { return null; },'
            ' querySelector: function () { return null; }, querySelectorAll: function () { return []; }};\n')
    files = [DOCS / "vendor" / "d3.v7.min.js", DOCS / "compute.js", DOCS / "app.js", ROOT / "tests" / "map_markers_smoke.js"]
    shim = tmp_path / "shim.js"
    if JSC.exists():
        shim.write_text("var self = this; var window = this;\n"
                        'var console = {log: function (s) { print(String(s)); }, error: function (s) { print("ERROR " + String(s)); }};\n'
                        + stub + "".join('load("%s");\n' % f for f in files), encoding="utf-8")
        r = subprocess.run([str(JSC), str(shim)], capture_output=True, text=True)
    elif shutil.which("node"):
        shim.write_text("const fs = require('fs'), vm = require('vm');\n"
                        "const ctx = {console: console, Date: Date}; ctx.self = ctx; ctx.window = ctx; vm.createContext(ctx);\n"
                        "vm.runInContext(%r, ctx);\n" % stub
                        + "".join("vm.runInContext(fs.readFileSync(%r, 'utf8'), ctx);\n" % str(f) for f in files), encoding="utf-8")
        r = subprocess.run(["node", str(shim)], capture_output=True, text=True)
    else:
        pytest.skip("neither jsc nor node is installed")
    assert r.returncode == 0 and "map markers smoke ok" in r.stdout, r.stdout + r.stderr
