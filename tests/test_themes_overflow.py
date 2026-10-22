"""Phone width overflow (review of 2026-10-22): at 375px the page scrolled sideways by 19px. The theme grid
already scrolls inside its own wrapper; the cause was the "N pairs, M originals" line under each speed bar,
which inherited white-space: nowrap inside a 4.5rem column. These static checks keep both from returning."""
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
CSS = (DOCS / "style.css").read_text(encoding="utf-8") + "\n" + (DOCS / "measures.css").read_text(encoding="utf-8")
HTML = (DOCS / "index.html").read_text(encoding="utf-8")


def media_blocks(css, max_px):
    """Bodies of every @media (max-width: Npx) block with N >= max_px (so they apply at phone width)."""
    out = []
    for m in re.finditer(r"@media\s*\(max-width:\s*(\d+)px\)\s*\{", css):
        if int(m.group(1)) < max_px:
            continue
        depth, i = 1, m.end()
        while depth and i < len(css):
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out.append(css[m.end():i - 1])
    return out


def rules(css, selector):
    return [body for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
            if selector in [s.strip() for s in sel.split(",")]]


def test_speed_counts_wrap_at_phone_width():
    phone = "\n".join(media_blocks(CSS, 375))
    assert any(re.search(r"grid-template-columns:\s*1fr\s+[\d.]+rem", b) for b in rules(phone, "#copies-speed .sp-row")), \
        "the speed rows keep a fixed count column at phone width"
    assert any("nowrap" in b for b in rules(CSS, "#copies-speed .sp-n")), "the median stays on one line"
    assert any(re.search(r"white-space:\s*normal", b) for b in rules(phone, "#copies-speed .sp-n em")), \
        "the pairs and originals line must wrap inside the fixed column at phone width"


def test_theme_grid_scrolls_inside_its_wrapper():
    assert 'class="th-grid-wrap" id="theme-grid-wrap"><table class="th-grid" id="theme-grid">' in HTML
    assert any(re.search(r"overflow-x:\s*auto", b) for b in rules(CSS, ".th-grid-wrap"))
    assert any(re.search(r"grid-template-columns:\s*minmax\(0,\s*1fr\)", b) for b in rules(CSS, "#themes .th-body")), \
        "the grid column must be allowed to shrink below the table's width"
    assert 'class="th-scroll-hint"' in HTML and any("display: block" in b for b in rules(CSS, ".th-grid-wrap.scrolls + .th-scroll-hint"))
