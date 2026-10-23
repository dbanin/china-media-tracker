"""Static checks on the Start here spacing pass in docs/style.css (2026-10-22)."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "docs" / "style.css").read_text(encoding="utf-8")
MARK = "/* Start here, spacing pass:"


def _block():
    i = CSS.index(MARK)
    return CSS[i:]


def test_spacing_pass_is_present_and_scoped():
    block = _block()
    # Every selector in the pass is scoped to the Start here section, so no other section moves.
    body = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    selectors = re.findall(r"([^{}@]+)\{[^{}]*\}", body)
    assert selectors
    for sel in selectors:
        for part in sel.split(","):
            assert part.strip().startswith(".start-here"), part


def test_spacing_values_use_one_scale():
    body = re.sub(r"/\*.*?\*/", "", _block(), flags=re.S)
    allowed = {"0", "4px", "12px", "28px", "var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"}
    for prop, value in re.findall(r"\b(margin(?:-[a-z]+)?|padding(?:-[a-z]+)?|gap|column-gap|top|right)\s*:\s*([^;]+);", body):
        for token in value.split():
            assert token in allowed, (prop, value)


def test_one_content_column_on_wide_screens():
    block = _block()
    assert re.search(r"\.start-here \.sh-intro \{ columns: 2; column-gap: var\(--s4\); \}", block)
    assert ".start-here .sh-categories { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in block
    # The intro gutter and the grid gaps are the same step, so the column edges line up.
    assert ".start-here .sh-categories { grid-template-columns: minmax(0, 1fr); gap: var(--s4);" in block
    assert ".start-here .sh-routes { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--s4); }" in block
    assert '.start-here .sh-route-card[data-route-id="unattributed"] { grid-column: 1 / -1; }' in block


def test_phone_width_is_a_single_column():
    block = _block()
    phone = block[block.index("@media (max-width: 640px)"):]
    assert "grid-template-columns: minmax(0, 1fr)" in phone
    assert ".start-here .sh-route-card:nth-child(n) { grid-column: auto; }" in phone
