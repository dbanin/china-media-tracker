from pathlib import Path

from pipeline import extract

FIX = Path(__file__).parent / "fixtures"


def test_jsonld_paywall_detected():
    html = (FIX / "paywall_jsonld.html").read_text()
    ex = extract.extract(html)
    pw, reason = extract.detect_paywall(html, ex["text"])
    assert pw and "isAccessibleForFree" in reason


def test_free_article_extracts():
    html = (FIX / "free_article.html").read_text()
    ex = extract.extract(html)
    assert ex["text"] and "customs data" in ex["text"]
    pw, _ = extract.detect_paywall(html, ex["text"])
    assert not pw
    assert extract.jsonld_authors(html) == "Jane Reporter"


def test_interstitial_needs_short_body():
    html = '<html><body><div class="paywall">Subscribe to continue reading</div><p>%s</p></body></html>' % ("x " * 2000)
    pw, _ = extract.detect_paywall(html, "x " * 2000)
    assert not pw
    pw, _ = extract.detect_paywall(html, "short")
    assert pw
