"""The page title (the header h1) is a large fluid display line: the last h1 rule in docs/style.css wins the cascade,
so these checks read that rule and evaluate its clamp() at the widths the site is checked at."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "docs" / "style.css").read_text(encoding="utf-8")
REM = 16.0


def _last_rule(selector):
    rules = re.findall(r"(?m)^" + re.escape(selector) + r" \{([^}]*)\}", CSS)
    assert rules, selector
    return dict((k.strip(), v.strip()) for k, v in (d.split(":", 1) for d in rules[-1].split(";") if ":" in d))


def _px(term, vw):
    term = term.strip()
    if term.endswith("rem"):
        return float(term[:-3]) * REM
    if term.endswith("vw"):
        return float(term[:-2]) * vw / 100.0
    if term.endswith("px"):
        return float(term[:-2])
    raise AssertionError(term)


def _clamp(expr, vw):
    m = re.fullmatch(r"clamp\(([^,]+),([^,]+),([^,]+)\)", expr)
    assert m, expr
    lo, mid, hi = (sum(_px(t, vw) for t in part.split("+")) for part in m.groups())
    return max(lo, min(mid, hi))


def test_title_is_fluid_and_larger_than_before():
    h1 = _last_rule("h1")
    size = h1["font-size"]
    assert size.startswith("clamp(")
    before = {375: 32.0, 768: 32.0, 1280: 40.96, 2000: 48.0}
    for vw, old in before.items():
        assert _clamp(size, vw) > old, vw
    ratio = _clamp(size, 1280) / before[1280]
    assert 1.5 <= ratio <= 2.0, ratio
    # Grows with the viewport, and stops growing on very wide screens.
    assert _clamp(size, 375) < _clamp(size, 768) < _clamp(size, 1280) <= _clamp(size, 2000)
    assert _clamp(size, 2000) == _clamp(size, 3000)
    # A phone title stays within reach of the 343px column (the longest word is ten characters).
    assert _clamp(size, 375) <= 40


def test_title_is_set_tight_for_a_large_display_serif():
    h1 = _last_rule("h1")
    assert float(h1["line-height"]) <= 1.08
    ls = h1["letter-spacing"]
    assert ls.startswith("-") and ls.endswith("em") and abs(float(ls[:-2])) <= 0.03
    assert h1["max-width"] == "none"
    assert "margin-bottom" in h1


def test_header_spacing_opens_up_around_the_title():
    header = _last_rule("header.top")
    assert header["padding-top"] == "var(--s5)"
    assert _last_rule("header.top .brand")["margin-bottom"] == "var(--s3)"
