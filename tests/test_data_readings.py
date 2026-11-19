"""Pages that hold their article as data or as plain paragraphs, and pages that are a refusal.

The three readings added in 2026.11.2 (extract._fallback_text) run only on a page where nothing else
found an article and no paywall marker is set. What a reader is shown is what is read: a visible opening
is never filled in from the page's data.
"""
import json

import trafilatura

from pipeline import extract

S1 = "The minister said on Monday that the talks with Beijing would continue next week in the capital."
S2 = "Officials from both countries are expected to sign the agreement before the end of the month."
S3 = "Opposition parties asked for the full text of the accord to be published before any vote is held."
HIDDEN = "Only the page's data carries this closing sentence about the financing of the new port."
STORY = " ".join([S1, S2, S3, HIDDEN])
HEADLINE = "Talks with Beijing to continue next week, minister says"


def _jsonld(headline=HEADLINE, body=STORY, **more):
    """An entry inside a @graph, as The Sun of Malaysia prints it (trafilatura reads an articleBody
    only at the top level of a script, whatever its headline)."""
    node = dict({"@type": "NewsArticle", "headline": headline, "articleBody": body}, **more)
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "WebSite", "name": "The Daily"}, node]}
    return '<script type="application/ld+json">%s</script>' % json.dumps(graph)


def _page(head="", body="<div id='app'><span>Loading</span></div>", title="The Daily | " + HEADLINE):
    return "<html><head><title>%s</title>%s</head><body>%s</body></html>" % (title, head, body)


def test_story_carried_only_in_the_metadata_is_read():
    text = extract.extract(_page(_jsonld()))["text"]
    assert text and S1 in text and HIDDEN in text


def test_metadata_of_another_story_is_not_this_page():
    """The Macau Daily Times prints one old story's entry on every page."""
    page = _page(_jsonld(headline="Typhoon closes schools across the city for a second day"))
    assert extract.jsonld_article_body(page) is None
    assert HIDDEN not in (extract.extract(page)["text"] or "")
    assert extract.jsonld_article_body(_page(_jsonld(), title="")) is None            # no title to hold it to


def test_the_longest_entry_with_this_headline_is_taken():
    page = _page(_jsonld(body=S1) + _jsonld() + _jsonld(headline="Another story entirely, printed in a rail", body=STORY * 3))
    assert extract.jsonld_article_body(page) == STORY


def test_a_visible_opening_is_never_filled_in_from_the_data():
    """Assabah of Morocco printed 300 to 600 characters above a subscription box and the whole story in
    its metadata."""
    opening = "<article><p>%s</p><p>%s</p><p>%s</p></article>" % (S1, S2, S3)
    text = extract.extract(_page(_jsonld(), body=opening))["text"]
    assert S1 in text and HIDDEN not in text


def test_no_data_is_read_on_a_page_with_a_paywall_marker():
    assert HIDDEN not in (extract.extract(_page(_jsonld(isAccessibleForFree=False)))["text"] or "")
    box = '<div id="wpmem_restricted_msg"><p>Subscribe to read</p></div>'
    assert HIDDEN not in (extract.extract(_page(_jsonld(), body=box))["text"] or "")


def test_members_box_is_a_paywall_under_any_length_of_opening():
    box = '<div class="notice" id="wpmem_restricted_msg"><p>You can read the article after subscribing.</p></div>'
    opening = "<article>%s</article>" % ("<p>%s</p>" % STORY * 6)
    got, reason = extract.detect_paywall(_page(body=opening + box), STORY * 6)
    assert got and "wpmem_restricted_msg" in reason
    assert not extract.detect_paywall(_page(body=opening), STORY * 6)[0]


def _next_page(article, path=("data",)):
    node = {"article": article}
    for key in reversed(path):
        node = {key: node}
    data = {"props": {"pageProps": node}, "page": "/frett/[id]"}
    return _page('<script id="__NEXT_DATA__" type="application/json">%s</script>' % json.dumps(data))


def test_story_a_next_page_draws_in_the_browser_is_read_in_page_order():
    """RUV of Iceland (data.article) and Mosaique FM of Tunisia (pagedata.article)."""
    article = {"title": HEADLINE, "intro": "<p>%s</p>" % S1,
               "body": [{"type": "text", "html": "<p>%s</p><p>%s</p>" % (S2, S3)}, {"type": "text", "html": "<p>%s</p>" % HIDDEN}],
               "related": [{"body": "<p>A related story about something else entirely, with a long teaser line.</p>"}],
               "author": {"bio": "<p>The writer has covered the region for many years from the capital.</p>"}}
    for path in (("data",), ("pagedata",)):
        fragment = extract.next_data_article_html(_next_page(article, path))
        assert fragment.index(S1) < fragment.index(S2) < fragment.index(HIDDEN)
        assert "related story" not in fragment and "covered the region" not in fragment
    text = extract.extract(_next_page(article))["text"]
    assert S1 in text and HIDDEN in text and "related story" not in text


def test_next_story_marked_as_paid_is_left_alone():
    article = {"title": HEADLINE, "body": "<p>%s</p><p>%s</p><p>%s</p>" % (S1, S2, HIDDEN)}
    for gate in ({"isPremium": True}, {"paywall": "hard"}, {"subscriberOnly": 1}):
        assert extract.next_data_article_html(_next_page(dict(article, **gate))) is None
    for free in ({"isPremium": False}, {"paywall": "free"}, {"subscriberOnly": 0}):
        assert extract.next_data_article_html(_next_page(dict(article, **free)))
    assert extract.next_data_article_html(_page()) is None
    assert extract.next_data_article_html(_page('<script id="__NEXT_DATA__">{not json</script>')) is None


TEASERS = "".join('<div class="card"><p>%s</p></div>' % s for s in (S1, S2, S3, HIDDEN))


def test_sibling_paragraphs_of_one_block_are_the_story():
    story = '<div class="col"><p>%s</p><p>%s</p><p>By a staff writer</p><p>%s</p></div>' % (S1, S2, S3)
    footer = '<div class="site-footer"><p>%s</p><p>%s</p><p>%s</p><p>%s</p></div>' % (HIDDEN, HIDDEN, HIDDEN, HIDDEN)
    got = extract.paragraph_container_text(_page(body=TEASERS + story + footer))
    assert got.split("\n") == [S1, S2, "By a staff writer", S3]


def test_teasers_footers_and_short_blocks_are_not_a_story():
    para = extract.paragraph_container_text
    assert para(_page(body=TEASERS)) is None                                               # one paragraph a box
    assert para(_page(body="<footer><div><p>%s</p><p>%s</p><p>%s</p></div></footer>" % (S1, S2, S3))) is None
    assert para(_page(body='<div id="comments"><p>%s</p><p>%s</p><p>%s</p></div>' % (S1, S2, S3))) is None
    assert para(_page(body="<div><p>%s</p><p>%s</p></div>" % (S1, S2))) is None           # two paragraphs
    assert para(_page(body="<div><p>Home</p><p>World</p><p>Sport</p><p>Video</p></div>")) is None
    # Inside the page's <article>, only what lies between it and the paragraphs counts.
    inside = '<div class="widget-column"><article><div class="body"><p>%s</p><p>%s</p><p>%s</p></div></article></div>'
    assert para(_page(body=inside % (S1, S2, S3)))


def test_the_other_readings_run_when_the_first_returns_no_document(monkeypatch):
    """trafilatura returns nothing at all for some pages whose article is plainly there."""
    story = '<div class="col"><p>%s</p><p>%s</p><p>%s</p></div>' % (S1, S2, S3)
    monkeypatch.setattr(trafilatura, "bare_extraction", lambda *a, **k: None)
    monkeypatch.setattr(extract, "prose_blocks_html", lambda html: None)
    out = extract.extract(_page(body=story))
    assert out["text"].split("\n") == [S1, S2, S3] and out["title"] is None


SERVICEPIPE = ('<html><head><script src="https://servicepipe.tech/loaders/abc.js"></script></head><body>'
               "<js-challenge-loader></js-challenge-loader></body></html>")
AKAMAI = ('<html><head><meta http-equiv="refresh" content="5; URL=\'/category/x/story?bm-verify=AAQAAAAL\'" />'
          "</head><body><script>var i = 1;</script></body></html>")
ARVAN = ('<html><body><section class="error-section"><h2 class="error-section__subtitle error-section__subtitle--waiting">'
         "Transferring to the website...</h2></section></body></html>")


def test_challenge_pages_that_say_nothing_are_told_by_their_markup():
    for vendor, page in (("Servicepipe", SERVICEPIPE), ("Akamai", AKAMAI), ("ArvanCloud", ARVAN)):
        assert extract.detect_unreadable(page, None) == (True, "challenge page: %s" % vendor)
        assert extract.detect_unreadable(page, "Transferring to the website...")[0]


def test_the_same_markup_on_a_page_with_an_article_or_a_long_page_is_not_a_challenge():
    body = "\n".join([S1, S2, S3] * 4)
    assert len(body) >= extract.SHORT_BODY_CHARS
    assert extract.detect_unreadable(SERVICEPIPE, body) == (False, None)
    long_page = SERVICEPIPE.replace("</body>", "<!-- %s --></body>" % ("x" * extract.CHALLENGE_PAGE_MAX_CHARS))
    assert extract.detect_unreadable(long_page, None) == (False, None)
