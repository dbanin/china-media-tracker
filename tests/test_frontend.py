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


def test_static_files_present():
    for f in ("index.html", "app.js", "compute.js", "style.css", "vendor/d3.v7.min.js", "vendor/topojson-client.min.js",
              "vendor/countries-110m.json", "vendor/iso3166.json"):
        assert (ROOT / "docs" / f).exists(), f


def test_no_dashes_in_interface_prose():
    """Repository prose rule: no em dashes or en dashes."""
    for f in ("index.html", "app.js", "README.md", "CHANGELOG.md"):
        text = (ROOT / ("docs/" + f if f.endswith((".html", ".js")) else f)).read_text(encoding="utf-8")
        assert "—" not in text and "–" not in text, f


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
    """style.css, compute.js and app.js are always requested with the same cache-busting version,
    and content.json (fetched from app.js, not linked in the page) carries the same number."""
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    versions = set(re.findall(r'\.(?:css|js)\?v=(\d+)', html))
    assert len(versions) == 1, "style.css, compute.js and app.js should share one version: " + str(versions)
    app_js = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    m = re.search(r'ASSET_VERSION\s*=\s*"(\d+)"', app_js)
    assert m and m.group(1) == next(iter(versions)), "content.json's fetched version should match the asset version"
