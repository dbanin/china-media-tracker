"""The preliminary figures banner under the title (review of 2026-10-22): the site says, near the top and on
every visit, that its counts are preliminary, which state origin evidence the headline rests on, that the
model only state origin and unchecked state sourcing are shown apart and provisional, and that no figure has
yet been checked against human coding. The text lives in content.json; the default is in index.html so it
shows without JavaScript; app.js rebuilds it from meta so a passed hand check is never called unchecked."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
INDEX = (DOCS / "index.html").read_text(encoding="utf-8")
APP = (DOCS / "app.js").read_text(encoding="utf-8")
CSS = (DOCS / "style.css").read_text(encoding="utf-8")
BANNER = json.loads((DOCS / "content.json").read_text(encoding="utf-8"))["preliminary_banner"]
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


def _strings(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, str):
        yield node


def _banner_block():
    m = re.search(r'<div class="prelim" id="preliminary".*?</div>', INDEX, re.S)
    assert m, "banner block missing"
    return m.group(0)


def test_banner_sits_under_the_title_and_above_the_map_controls():
    for anchor in ('id="preliminary"', 'role="note"', 'aria-labelledby="prelim-title"', 'id="prelim-title"',
                   'id="prelim-text"', 'id="prelim-link"'):
        assert anchor in INDEX, anchor
    at = INDEX.index('id="preliminary"')
    assert INDEX.index("</header>") < at < INDEX.index('id="map-card"') < INDEX.index('<div class="controls">')
    block = _banner_block()
    assert " hidden" not in block and "dismiss" not in block and "<button" not in block
    assert "<noscript" not in INDEX[INDEX.index("</header>"):at]


def test_static_text_is_the_default_composed_from_content_json():
    """The no JavaScript text is exactly what app.js shows on today's meta (nothing checked, both provisional)."""
    text = " ".join([BANNER["lead"], BANNER["evidence"], BANNER["apart"]["both"], BANNER["checked"]["none"]])
    block = _banner_block()
    assert ">" + text + ' <a href="' + BANNER["link_href"] + '" id="prelim-link">' + BANNER["link_text"] + "</a></p>" in block
    assert '<em>' + BANNER["heading_em"] + '</em>' + BANNER["heading"].replace(BANNER["heading_em"], "", 1) in block


def test_banner_keeps_every_fact_of_the_accepted_wording():
    text = " ".join([BANNER["lead"], BANNER["evidence"], BANNER["apart"]["both"], BANNER["checked"]["none"]])
    for fact in ("preliminary", "hand checks", "wire credit or dateline", "press release distribution stamp",
                 "sponsored or partner disclosure", "Chinese diplomat's byline", "close match with credited state text",
                 "judged by the model alone", "unchecked state sourcing", "shown apart", "marked provisional",
                 "No figure has yet been checked against human coding."):
        assert fact.lower() in text.lower(), fact
    assert BANNER["link_href"] == "#method" and 'id="method"' in INDEX
    assert BANNER["link_text"] == "How the figures are checked"


def test_banner_prose_follows_the_house_rules():
    for s in _strings(BANNER):
        assert "—" not in s and "–" not in s, s
        assert not re.search(r"Category [ABC]\b|Universit|Stanford|\bmost\b", s), s
    for key in ("none", "none_published", "model_only", "relay", "both"):
        assert BANNER["checked"][key].endswith(".")
    assert "checked against human coding" not in BANNER["checked"]["relay"].split(";")[0]


def test_app_renders_the_banner_first_from_content_and_meta():
    assert "C.preliminaryBanner(state.content && state.content.preliminary_banner, state.meta)" in APP
    assert 'var steps = [\n        ["renderPreliminary", renderPreliminary],' in APP


def test_banner_styles_use_the_site_tokens_and_fit_a_phone():
    rules = CSS[CSS.index(".prelim-wrap {"):]
    for token in ("var(--gold)", "var(--display)", "var(--card)", "var(--gutter)", "var(--bone-soft)"):
        assert token in rules, token
    assert not re.search(r"\.prelim[^{]*\{[^}]*\bwidth:\s*\d{3,}px", rules), "a fixed width would scroll at 375px"


def _run_smoke():
    smoke = ROOT / "tests" / "smoke_preliminary.js"
    if shutil.which("node"):
        return subprocess.run(["node", str(smoke)], capture_output=True, text=True)
    shim = "\n".join([
        'var console = {log: function (s) { print(String(s)); }, error: function (s) { print("ERROR " + String(s)); }};',
        'var process = {exit: function (c) { throw new Error("process.exit(" + c + ")"); }};',
        "var module = {exports: {}};",
        'load("%s"); var __c = module.exports;' % (DOCS / "compute.js"),
        "function require(p) { return __c; }",
        'load("%s");' % smoke,
    ])
    return _jsc(shim)


def _jsc(shim):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "shim.js"
        f.write_text(shim, encoding="utf-8")
        return subprocess.run([str(JSC), str(f)], capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None and not JSC.exists(), reason="no JavaScript engine")
def test_banner_smoke():
    r = _run_smoke()
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "preliminary smoke ok" in out and "FAIL" not in out, out
