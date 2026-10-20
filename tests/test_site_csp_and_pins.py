"""The site's Content Security Policy holds (no inline script anywhere it would need one) and every
listed requirement is pinned to one version."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


def _csp():
    m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)"', INDEX)
    assert m, "index.html carries a Content-Security-Policy meta"
    return {d.split()[0]: d.split()[1:] for d in (x.strip() for x in m.group(1).split(";")) if d}


def test_csp_is_strict():
    csp = _csp()
    assert csp["default-src"] == ["'self'"]
    assert csp["script-src"] == ["'self'"], "no inline or remote script"
    assert csp["connect-src"] == ["'self'"] and csp["object-src"] == ["'none'"] and csp["base-uri"] == ["'none'"]
    assert "'unsafe-eval'" not in sum(csp.values(), [])
    # the CSP comes before any stylesheet or script it governs
    assert INDEX.index("Content-Security-Policy") < INDEX.index("<link rel=\"stylesheet\"")


def test_the_page_needs_no_inline_script():
    for tag in re.findall(r"<script\b[^>]*>", INDEX):
        assert 'src="' in tag and "//" not in tag, tag
    assert not re.search(r"<script\b[^>]*>\s*\S", re.sub(r"<script\b[^>]*src=[^>]*>\s*</script>", "", INDEX))
    assert not re.search(r"\son[a-z]+\s*=", INDEX), "no inline event handlers"
    for js in (ROOT / "docs").glob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert not re.search(r"['\"]\s*<[^>]*\son[a-z]+=", text), "%s writes an inline handler" % js.name
        assert "eval(" not in text and "new Function" not in text, js.name


def test_every_requirement_is_pinned():
    lines = [ln.split("#")[0].strip() for ln in (ROOT / "requirements.txt").read_text().splitlines()]
    reqs = [ln for ln in lines if ln]
    assert reqs and all(re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][\w.]*", r) for r in reqs), reqs
    assert any(r.startswith("requests==2.32.") for r in reqs)
