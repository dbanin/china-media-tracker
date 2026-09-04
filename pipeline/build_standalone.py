"""Build docs/standalone.html: the whole map in one file with every data file inlined.

Useful for sharing a snapshot as a single page (for example as a hosted artifact)
when the static site is not deployed. D3, the topology client and everything else,
including the Natural Earth topology, the ISO table, the registry and the
per-country article lists, is embedded. The file carries no <html>, <head> or
<body> wrapper so a host can wrap it; opened directly in a browser it still
renders because browsers tolerate the omission.
"""
import json
import re
from pathlib import Path

from pipeline import config

DOCS = config.ROOT / "docs"
D3_CDN = "https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def build(out: Path = DOCS / "standalone.html") -> Path:
    index = _read(DOCS / "index.html")
    body = index[index.index("<body>") + len("<body>"):index.index("</body>")]
    body = re.sub(r'\s*<script src="[^"]*"></script>', "", body)
    body = body.replace('<a href="../METHODOLOGY.md" id="method-link">Full methodology file</a>',
                        '<span class="muted" id="method-link">Full methodology file: METHODOLOGY.md in the repository</span>')
    # Hosted single-file snapshots cannot hand the viewer a download, so the CSV buttons are replaced by a note.
    body = re.sub(r'<button id="export-view">.*?</button>\s*<button id="export-daily">.*?</button>',
                  '<span class="muted">CSV export is available on the deployed site and from docs/data in the repository; this snapshot cannot save files.</span>'
                  '<button id="export-view" hidden></button><button id="export-daily" hidden></button>', body, flags=re.S)
    data = {}
    for rel in ("data/meta.json", "data/latest.json", "data/global_series.json", "data/outlets.json",
                "vendor/countries-110m.json", "vendor/iso3166.json"):
        data[rel] = json.loads(_read(DOCS / rel))
    for f in sorted((DOCS / "data" / "daily").glob("*.json")):
        data["data/daily/" + f.name] = json.loads(_read(f))
    for f in sorted((DOCS / "data" / "articles").glob("*.json")):
        data["data/articles/" + f.name] = json.loads(_read(f))
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    css = _read(DOCS / "style.css")
    css += "\nbody { margin: 0; }\n"
    html = "".join([
        "<title>China State Media Tracker</title>\n",
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;1,300;1,400&family=Manrope:wght@300;400;500&display=swap">\n',
        "<style>\n", css, "\n</style>\n",
        body,
        "\n<script>\n", _read(DOCS / "vendor" / "d3.v7.min.js"), "\n</script>\n",
        "<script>\n", _read(DOCS / "vendor" / "topojson-client.min.js"), "\n</script>\n",
        "<script>window.__TRACKER_DATA = ", blob, ";</script>\n",
        "<script>\n", _read(DOCS / "compute.js"), "\n</script>\n",
        "<script>\n", _read(DOCS / "app.js"), "\n</script>\n",
    ])
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    p = build()
    print("wrote %s (%.1f MB)" % (p, p.stat().st_size / 1e6))
