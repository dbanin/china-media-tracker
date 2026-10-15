import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_frontend_smoke():
    r = subprocess.run(["node", str(ROOT / "tests" / "frontend_smoke.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_measures_smoke():
    """docs/measures.js and every docs/m_<id>.js (tests/measures_smoke.js, and each tests/smoke_m_<id>.js)."""
    for f in [ROOT / "tests" / "measures_smoke.js"] + sorted((ROOT / "tests").glob("smoke_m_*.js")):
        r = subprocess.run(["node", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f.name + ": " + r.stdout + r.stderr


MEASURE_IDS = ("alteration", "speed", "doctrine", "spikes", "engagement", "syndication")


def test_static_files_present():
    for f in ("index.html", "app.js", "compute.js", "style.css", "vendor/d3.v7.min.js", "vendor/topojson-client.min.js",
              "vendor/countries-110m.json", "vendor/iso3166.json"):
        assert (ROOT / "docs" / f).exists(), f


def test_no_dashes_in_interface_prose():
    """Repository prose rule: no em dashes or en dashes, in the page, every script and stylesheet in docs,
    the README and the changelog."""
    files = [ROOT / "docs" / "index.html", ROOT / "README.md", ROOT / "CHANGELOG.md"]
    files += sorted((ROOT / "docs").glob("*.js")) + sorted((ROOT / "docs").glob("*.css"))
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert "\u2014" not in text and "\u2013" not in text, f.name


def test_measure_files_present():
    """The research measures' shared script and stylesheet, and one script per measure in sources/measures.yaml."""
    import yaml
    ids = [m["id"] for m in yaml.safe_load((ROOT / "sources" / "measures.yaml").read_text(encoding="utf-8"))]
    assert tuple(ids) == MEASURE_IDS
    for f in ["measures.js", "measures.css"] + ["m_%s.js" % i for i in ids]:
        assert (ROOT / "docs" / f).exists(), f
    css = (ROOT / "docs" / "measures.css").read_text(encoding="utf-8")
    for i in ("shared",) + MEASURE_IDS:
        assert css.count("/* ==== BEGIN %s ==== */" % i) == 1 and css.count("/* ==== END %s ==== */" % i) == 1, i


def test_measure_sections_present():
    """Each measure's section shell is in the page, hidden until its measure is on, with its kicker,
    heading, intro and body, and the slots measures.js fills (SLOTS) exist."""
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    for sec in ("copies", "doctrine", "engagement", "syndication"):
        m = re.search(r'<section id="%s"[^>]*>(.*?)</section>' % sec, html, re.S)
        assert m, sec
        assert " hidden" in m.group(0).split(">", 1)[0], sec + " starts hidden"
        body = m.group(1)
        for part in ('class="th-kicker"', 'id="%s-title"' % sec, 'id="%s-intro"' % sec, 'id="%s-body"' % sec):
            assert part in body, sec + ": " + part
    for anchor in ('id="copies-alteration"', 'id="copies-speed"', 'id="findings-measures"', 'id="findings-spikes"',
                   'id="measure-method"', 'id="measure-method-body"'):
        assert anchor in html, anchor
    overview = re.search(r'<section id="overview".*?</section>', html, re.S).group(0)
    assert 'id="findings-spikes"' in overview, "the unusual days slot sits under What we found"
    method = re.search(r'<section class="sec method" id="method">.*?</section>', html, re.S).group(0)
    assert 'id="measure-method"' in method
    js = (ROOT / "docs" / "measures.js").read_text(encoding="utf-8")
    for slot in re.findall(r'"([a-z-]+)"', js[js.index("var SLOTS"):js.index(";", js.index("var SLOTS"))]):
        if slot not in MEASURE_IDS:
            assert 'id="%s"' % slot in html, slot
    # Scripts in order: compute, measures, each measure, then app.
    order = re.findall(r'<script src="([a-z_]+)\.js\?v=\d+"></script>', html)
    assert order[0] == "compute" and order[1] == "measures" and order[-1] == "app", order
    assert sorted(order[2:-1]) == sorted("m_" + i for i in MEASURE_IDS), order


def test_reader_sections_present():
    """The reader-facing sections built from content.json and examples.json: each has a stable
    anchor in the page even when its data has not arrived, so the render functions in app.js have
    somewhere to hide the section rather than nothing to find."""
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    for anchor in ('id="findings-box"', 'id="start-here"', 'id="sh-categories"', 'id="sh-routes"',
                   'id="examples"', 'id="examples-list"', 'id="faq"', 'id="faq-list"',
                   'id="technical-notes-wrap"', 'id="technical-notes"', 'id="bars-caption"', 'id="cta-line"'):
        assert anchor in html, anchor


def test_asset_versions_match():
    """style.css, measures.css, compute.js, measures.js, every m_<id>.js and app.js are always requested
    with the same cache-busting version, and content.json (fetched from app.js, not linked in the page)
    carries the same number."""
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    local = ["style.css", "measures.css", "compute.js", "measures.js", "app.js"]
    local += [f.name for f in sorted((ROOT / "docs").glob("m_*.js"))]
    for f in local:
        assert re.search(r'(?:src|href)="%s\?v=\d+"' % re.escape(f), html), f + " is linked with a version"
    versions = set(re.findall(r'\.(?:css|js)\?v=(\d+)', html))
    assert len(versions) == 1, "style.css, compute.js and app.js should share one version: " + str(versions)
    app_js = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    m = re.search(r'ASSET_VERSION\s*=\s*"(\d+)"', app_js)
    assert m and m.group(1) == next(iter(versions)), "content.json's fetched version should match the asset version"
