"""Deploy self heal (2026-10-23): after a deploy a browser can hold the old index.html with new data, or the
reverse. docs/app.js fetches index.html past the cache and reloads once when it links another app.js version,
fetches data files from another export once more before drawing, and treats a schema newer than KNOWN_SCHEMA as
a new deploy. The pure helpers run under jsc or node (tests/deploy_heal_smoke.js); the wiring is asserted here."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import config

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


def _app():
    return (DOCS / "app.js").read_text(encoding="utf-8")


def _fn(src, name):
    """The body of a top level function in app.js, up to the next function at the same indent."""
    m = re.search(r"\n  function %s\(.*?(?=\n  (?:function |var |/\*))" % re.escape(name), src, re.S)
    assert m, name
    return m.group(0)


def test_heal_helpers_smoke(tmp_path):
    stub = ('var document = {readyState: "loading", addEventListener: function () {}, getElementById: function () { return null; },'
            ' querySelector: function () { return null; }, querySelectorAll: function () { return []; }};\n'
            "var __INDEX_HTML = %s;\n" % json.dumps((DOCS / "index.html").read_text(encoding="utf-8")))
    files = [DOCS / "vendor" / "d3.v7.min.js", DOCS / "compute.js", DOCS / "app.js", ROOT / "tests" / "deploy_heal_smoke.js"]
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
    assert r.returncode == 0 and "deploy heal smoke ok" in r.stdout, r.stdout + r.stderr


def test_known_schema_is_bumped_with_schema_version():
    m = re.search(r"var KNOWN_SCHEMA = (\d+);", _app())
    assert m and int(m.group(1)) == config.SCHEMA_VERSION, (m and m.group(1), config.SCHEMA_VERSION)


def test_index_is_fetched_past_the_cache_and_reload_is_guarded():
    src = _app()
    check = _fn(src, "checkFreshIndex")
    assert 'fetch("index.html", {cache: "no-store"})' in check and "healEnabled()" in check
    assert "pageAssetVersion(html)" in check and "running: ASSET_VERSION" in check
    reload = _fn(src, "healReload")
    # the key is stored and read back before the reload, and storage errors never reload
    i_set, i_read, i_go = reload.index("sessionStorage.setItem(HEAL_KEY"), reload.index("healTried() || []"), reload.index("location.replace(")
    assert i_set < i_read < i_go and "catch (e) { return false; }" in reload and "healing" in reload
    assert src.count("location.replace(") == 1 and src.count("location.reload(") == 0
    tried = _fn(src, "healTried")
    assert "window.sessionStorage" in tried and "catch (e) { return null; }" in tried
    enabled = _fn(src, "healEnabled")
    assert "healTried() !== null" in enabled and "__TRACKER_DATA" in enabled
    assert re.search(r"function init\(\) \{\n    checkFreshIndex\(\);\n    loadAll\(\)\.then\(healStragglers\)\.then\(function \(\) \{\n      checkSchema\(\);", src)


def test_data_files_revalidate_and_stragglers_are_bounded():
    src = _app()
    assert 'fetch(url, {cache: "no-cache"})' in _fn(src, "getJSON")
    heal = _fn(src, "healStragglers")
    assert '"?" + bust' in heal and "HEAL_WAIT_MS" in heal and "Promise.race" in heal and "if (!open) return;" in heal
    assert re.search(r"HEAL_WAIT_MS = (\d+)", src) and int(re.search(r"HEAL_WAIT_MS = (\d+)", src).group(1)) <= 5000
    for slot in ('dataSlots["data/meta.json"]', 'dataSlots["data/latest.json"]', 'dataSlots["data/outlets.json"]',
                 'dataSlots["data/daily/" + m + ".json"]', 'dataSlots["data/" + mf.file]'):
        assert slot in src, slot
    # still mixed after the second fetch: the existing notice reads the updated generations
    assert "state.generations = state.generations.filter" in heal and "noteGeneration(u, j)" in heal


def test_csp_allows_the_same_origin_index_fetch():
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    assert "connect-src 'self'" in html
