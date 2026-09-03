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
