"""Outlet templates whose release text extraction used to miss (pipeline/extract.py), checked through
to the signature decision.

The same Chinese state paid releases were labelled state origin at some outlets and independent
journalism at others. At the Straits Times the release sits only in the page's React Router payload,
so the stored body was the 250 character header; at Antara English the opening paragraph, the one
with the "(ANTARA/PRNewswire)" stamp, was dropped. With the wire stamp and the headline gone no
signature or hint could fire, and the copy fell through to the residual C rule.
"""
import gzip
from pathlib import Path

from pipeline import classify_rules, extract

FIX = Path(__file__).parent / "fixtures" / "extraction"


def _page(name):
    with gzip.open(FIX / (name + ".html.gz"), "rt", encoding="utf-8") as fh:
        return fh.read()


def _decide(name, title, url):
    html = _page(name)
    text = extract.extract(html, url)["text"]
    res = classify_rules.match_signatures(title, text, None, labels=extract.page_labels(html))
    return text, res["decision"], [m["id"] for m in res["matches"]]


def test_straits_times_xinhua_silk_road_release_is_state_origin_by_rules():
    text, decision, ids = _decide(
        "en_sg_straitstimes_ppr_xinhua", "Press releaseXinhua Silk Road: Chinese youths explore Mazu culture in trendy ways",
        "https://www.straitstimes.com/paid-press-releases/xinhua-silk-road-chinese-youths-explore-mazu-culture-in-trendy-ways-20260929")
    assert text.startswith("Xinhua Silk Road: Chinese youths explore Mazu culture in trendy ways\nBEIJING, Sept. 29, 2026 /PRNewswire/ --")
    assert len(text) > 2000
    # The same route as the Channel NewsAsia and PR Newswire copies of this release.
    assert decision == "A"
    assert "wire_release_headline_issuer" in ids


def test_straits_times_bureau_release_reaches_the_model_like_its_siblings():
    text, decision, ids = _decide(
        "en_sg_straitstimes_ppr_bureau",
        'Press releaseHangzhou Investment Promotion Bureau: Hangzhou Finds a "Gateway to the Sea" in Hong Kong',
        "https://www.straitstimes.com/paid-press-releases/hangzhou-investment-promotion-bureau-hangzhou-finds-a-gateway-to-the-sea-in-hong-kong-20260929")
    assert "HANGZHOU, China, Sept. 29, 2026 /PRNewswire/ --" in text
    # The issuer is a municipal bureau no signature names, so the model judges it, as it did the
    # Utusan copy; before the fix the stub body fired nothing and the copy was C by rules.
    assert decision == "A_candidate"
    assert ids == ["wire_stamp_alone"]


def test_antara_keeps_the_opening_paragraph_with_the_wire_stamp():
    text, decision, ids = _decide(
        "en_id_antara_release", 'Hangzhou Investment Promotion Bureau: Hangzhou Finds a "Gateway to the Sea" in Hong Kong',
        "https://en.antaranews.com/news/434130/hangzhou-investment-promotion-bureau-hangzhou-finds-a-gateway-to-the-sea-in-hong-kong")
    assert text.startswith("Hangzhou, China (ANTARA/PRNewswire)- On September 22,")
    assert "Source: Hangzhou Investment Promotion Bureau" in text[-200:]
    assert decision == "A_candidate"
    assert ids == ["wire_stamp_alone"]


def test_antara_lead_fix_leaves_other_markup_alone():
    html = '<div class="post-content"><p>Text.</p></div>'
    assert extract._template_html(html) == html
    html = '<div class="wrap__article-detail-content post-content"><p>Already a paragraph.</p></div>'
    assert extract._template_html(html) == html
    html = '<div class="wrap__article-detail-content post-content">\n  Jakarta (ANTARA) - Lead.<br /><br />Next.</div>'
    assert extract._template_html(html) == (
        '<div class="wrap__article-detail-content post-content">\n  <p>Jakarta (ANTARA) - Lead.</p><br /><br />Next.</div>')


def test_press_release_payload_is_never_read_on_a_page_with_a_paywall_marker():
    html = _page("en_sg_straitstimes_ppr_xinhua")
    assert extract.press_release_payload(html) is not None
    walled = html.replace("<head>", '<head><script type="application/ld+json">{"isAccessibleForFree": false}</script>', 1)
    assert extract.press_release_payload(walled) is None
    walled = html.replace("<body", '<div class="paywall"></div><body', 1)
    assert extract.press_release_payload(walled) is None


def test_press_release_payload_needs_the_paid_release_route():
    html = _page("en_sg_straitstimes_ppr_xinhua").replace("routes/ArticlePPR/ArticlePPR", "routes/Article/Article")
    assert extract.press_release_payload(html) is None
