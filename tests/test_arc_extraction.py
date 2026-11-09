"""Arc XP story payloads (extract.arc_story_html) and the linear time page scanners of pipeline/extract.py.

The Chosun Ilbo serves its articles as an empty page plus the Fusion.globalContent payload the browser
draws them from, so extraction found no text: on the 2026-11-09 snapshot 1,234 of its 1,259 China
relevant articles failed as extraction_empty. The fixtures carry the markup of pages fetched that day
with invented text. Members only stories carry the same payload but are shown to members only, so they
are paywalled and never read from it.
"""
import gzip
import json
import random
import re
import time
from pathlib import Path

from pipeline import extract

FIX = Path(__file__).parent / "fixtures" / "extraction"
STORY_URL = "https://www.chosun.com/international/2026/11/09/SYNTHFREE0000000000000001/"
MEMBERS_URL = "https://www.chosun.com/international/2026/11/06/SYNTHMEMB0000000000000002/"


def _page(name):
    with gzip.open(FIX / (name + ".html.gz"), "rt", encoding="utf-8") as fh:
        return fh.read()


def _payload(html):
    m = extract._ARC_GLOBAL_RE.search(html)
    data, end = json.JSONDecoder().raw_decode(html, m.end())
    return data, m.end(), end


def _with_payload(html, change):
    data, start, end = _payload(html)
    change(data)
    return html[:start] + json.dumps(data, ensure_ascii=False) + html[end:]


def _paragraphs(html):
    data, _, _ = _payload(html)
    return [extract._plain(e["content"]) for e in data["content_elements"] if e.get("type") == "text"]


def test_chosun_story_is_read_from_the_payload():
    html = _page("ko_kr_chosun_arc_story")
    text = extract.extract(html, STORY_URL)["text"]
    assert text.startswith("[베이징=신화통신] ")
    for p in _paragraphs(html):
        assert p.replace("<br>", "")[:30] in text.replace("\n", " ")
    data, _, _ = _payload(html)
    heading = [e["content"] for e in data["content_elements"] if e["type"] == "header"][0]
    items = [i["content"] for e in data["content_elements"] if e["type"] == "list" for i in e["items"]]
    assert heading in text and all(i in text for i in items)
    assert "ad-slot" not in text
    assert extract.detect_paywall(html, text) == (False, None)
    assert extract.detect_unreadable(html, text) == (False, None)
    assert len(text) >= extract.EMPTY_BODY_CHARS


def test_without_the_payload_reader_the_story_page_has_no_text(monkeypatch):
    html = _page("ko_kr_chosun_arc_story")
    monkeypatch.setattr(extract, "arc_story_html", lambda h: None)
    text = extract.extract(html, STORY_URL)["text"]
    assert not text or len(text) < extract.EMPTY_BODY_CHARS


def test_members_only_story_is_paywalled_and_never_read_from_the_payload():
    html = _page("ko_kr_chosun_arc_membership")
    assert extract.arc_story_html(html) is None
    text = extract.extract(html, MEMBERS_URL)["text"] or ""
    paywalled, reason = extract.detect_paywall(html, text)
    assert paywalled and "조선멤버십" in reason
    for p in _paragraphs(html):
        assert p[:30] not in text


def test_payload_is_never_read_when_anything_marks_the_story_withheld():
    html = _page("ko_kr_chosun_arc_story")
    assert extract.arc_story_html(html)
    withheld = [
        lambda d: d.update(content_restrictions={"content_code": "red"}),
        lambda d: d.update(content_restrictions={"content_code": "metered"}),
        lambda d: d.update(content_restrictions={"content_code": "premium"}),
        lambda d: d.update(subtype="membership"),
        lambda d: d.update(subtype="Premium"),
        lambda d: d.update(label={"membership_icon": {"display": True, "text": "x"}}),
    ]
    for change in withheld:
        assert extract.arc_story_html(_with_payload(html, change)) is None
    # A paywall marker anywhere on the page, as for the Straits Times payload.
    walled = html.replace("<head>", '<head><script type="application/ld+json">{"isAccessibleForFree": false}</script>', 1)
    assert extract.arc_story_html(walled) is None
    walled = html.replace('<div id="fusion-app">', '<div class="paywall"></div><div id="fusion-app">', 1)
    assert extract.arc_story_html(walled) is None
    # Free codes and a missing restriction are read.
    for code in ("0", "free", "", None):
        assert extract.arc_story_html(_with_payload(html, lambda d: d.update(content_restrictions={"content_code": code})))
    assert extract.arc_story_html(_with_payload(html, lambda d: d.pop("content_restrictions")))


def test_payload_is_not_read_when_the_page_prints_its_story():
    html = _page("ko_kr_chosun_arc_story")
    first = _paragraphs(html)[0]
    printed = html.replace('<div id="fusion-app">', '<div id="fusion-app"><article><p>%s</p></article>' % first, 1)
    assert extract.arc_story_html(printed) is None


def test_payload_reader_survives_malformed_payloads():
    html = _page("ko_kr_chosun_arc_story")
    _, start, end = _payload(html)
    for bad in ("{", "[" * 100000, '{"type":"story","content_elements":"x"}', '{"type":"video"}', "null",
                '{"type":"story","content_elements":[1,null,{"type":"text","content":5}]}'):
        assert extract.arc_story_html(html[:start] + bad + html[end:]) is None
    assert extract.arc_story_html("") is None
    assert extract.arc_story_html("Fusion.globalContent") is None


def test_chosun_and_rfa_bodies_under_the_old_version_are_stale():
    for url in (STORY_URL, "https://www.chosun.com/jp/economy-jp/2026/11/09/X/", "https://rfa.org/english/china/2026/09/02/x/",
                "https://www.rfa.org/korean/x"):
        assert extract.stale_extraction(url, None)
        assert extract.stale_extraction(url, "2026.10.1")
        assert extract.stale_extraction(url, "2026.11.1") is None
    assert extract.stale_extraction("https://www.chosunbiz.example/x", None) is None


# ---------------------------------------------------------------------------------------------------
# Linear time scanners: same results as the patterns they replace, on random tag soup
# ---------------------------------------------------------------------------------------------------
OLD_LABEL_CLASS_RE = re.compile(
    r'(?i)<(?:div|span|p|a|section|header|aside|small|em|strong|h[1-6]|li)\b[^>]*\b(?:class|id|data-[a-z-]+)="'
    r'(?![^"]*(?:widget|sidebar|taboola|outbrain|recommend|related|footer|nav|menu|promo-list|trending|most-read|'
    r'newsletter|banner|ad-slot|adslot|ad-container|dfp|rail|carousel|ticker|links-list|marketplace|comment))'
    r'[^"]*(?:sponsor|sponsored|branded|brand-content|native|partner|advertorial|studio|paid|promoted|kicker|eyebrow|'
    r'overline|section-name|article-tag|tagline|disclaimer|disclosure|supplement|pubbli|publi|anzeige|reklam|'
    r'author|byline|bio)'
    r'[^"]*"[^>]*>(.{0,400}?)</'
)
OLD_ANTARA_LEAD_RE = re.compile(
    r'(<div\b[^>]*\bclass="[^"]*\bwrap__article-detail-content\b[^"]*"[^>]*>)(\s*)([^<\s][^<]*)')
OLD_META_RE = re.compile(
    r'(?i)<meta\b[^>]*(?:name|property)="(?:article:section|article:tag|section|category|keywords|og:type|'
    r'article:content_tier|dc\.type|sailthru\.tags|parsely-section|parsely-tags|news_keywords|sponsor|sponsored)"[^>]*content="([^"]{0,300})"'
)
OLD_PAGE_URL_RES = (re.compile(r'(?i)<meta\b[^>]*\bproperty="og:url"[^>]*\bcontent="([^"]+)"'),
                    re.compile(r'(?i)<link\b[^>]*\brel="canonical"[^>]*\bhref="([^"]+)"'))
OLD_JSONLD_RE = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
PIECES = ['<div', '<DIV', '<span', '<p', '<P', '<li', '<h2', '<a', '<script', '<SCRIPT', '<style', '<noscript', '</script>',
          '</Script >', '</script>', '</style>', '</noscript>', '</p>', '</P>', '</div>', '</', '>', '>', '>', '"', '"', ' ',
          ' ', '\n', 'class="', ' class="', ' id="', ' data-x="', 'sponsor', 'byline', 'author', 'nav', 'widget', 'bio',
          'wrap__article-detail-content', ' x', 'Beijing said on Monday', '<br />', '=', 'é', '中国',
          '<title', '<TITLE', '</title>', 'client challenge', 'just a moment', 'we value your privacy', '<meta',
          ' name="keywords"', ' property="og:url"', ' content="', ' content="x"', '<link', ' rel="canonical"', ' href="u"',
          '<script type="application/ld+json">', ' type="application/ld+json"', '{"@type":"NewsArticle"}']


def _soup(rnd, n):
    return "".join(rnd.choice(PIECES) for _ in range(n))


def test_scanners_match_the_patterns_they_replace_on_random_markup():
    rnd = random.Random(20261109)
    for _ in range(3000):
        h = _soup(rnd, rnd.randint(1, 60))
        assert [(m.span(), m.group(1)) for m in extract._tag_finditer(extract.LABEL_CLASS_RE, extract._LABEL_START_RE, h)] \
            == [(m.span(), m.group(1)) for m in OLD_LABEL_CLASS_RE.finditer(h)], h
        assert extract._strip_scripts(h) == extract._SCRIPT_RE.sub(" ", h), h
        assert list(extract._paragraphs(h)) == [m.group(1) for m in extract._P_RE.finditer(h)], h
        expected = h if "wrap__article-detail-content" not in h else OLD_ANTARA_LEAD_RE.sub(r"\1\2<p>\3</p>", h, count=1)
        assert extract._template_html(h) == expected, h
        assert extract._untag(h) == extract.TAG_RE.sub(" ", h), h
        old, new = extract.TITLE_RE.search(h), extract._title(h)
        assert (old and (old.span(), old.group(1))) == (new and (new.span(), new.group(1))), h
        for rx in (extract.CHALLENGE_TITLE_RE, extract.CONSENT_TITLE_RE):
            old, new = rx.search(h), extract._title_search(rx, h)
            assert (old and old.span()) == (new and new.span()), h
        assert [m.group(1) for m in extract._tag_finditer(extract.META_RE, extract._META_START_RE, h)] \
            == [m.group(1) for m in OLD_META_RE.finditer(h)], h
        for old_rx, (rx, start) in zip(OLD_PAGE_URL_RES, extract._PAGE_URL_RES):
            old, new = old_rx.search(h), next(extract._tag_finditer(rx, start, h), None)
            assert (old and old.group(1)) == (new and new.group(1)), h
        assert list(extract._jsonld_scripts(h)) == [m.group(1) for m in OLD_JSONLD_RE.finditer(h)], h


HOSTILE = {
    "unclosed_piano": '<div class="piano-' * 50000,
    "repeated_sponsor_attrs": '<div' + ' class="sponsor"' * 50000 + '>',
    "sponsor_tags_one_close": '<div class="sponsor"' * 50000 + '>',
    "unclosed_value": '<div class="' + 'sponsor ' * 100000,
    "antara_tags": '<div class="wrap__article-detail-content" ' * 20000 + '>',
    "unclosed_scripts": '<script>' * 100000,
    "unclosed_paragraphs": '<p>' * 100000 + '<p class="x">',
    "fusion_nested": 'Fusion.globalContent=' + '[' * 200000,
    "unclosed_titles": '<title>' * 100000,
    "unclosed_title_attrs": '<title ' * 100000 + '>',
    "unclosed_metas": '<meta name="keywords" ' * 50000 + '>',
    "og_url_metas": '<meta property="og:url" ' * 50000 + '>',
    "unclosed_jsonld": '<script type="application/ld+json">' * 30000,
    "jsonld_open_tags": '<script ' * 100000,
    "lone_angles": '<p>' + '<' * 500000 + '</p>',
}


def test_page_scanners_take_linear_time_on_hostile_pages():
    for name, h in HOSTILE.items():
        for fn in (extract.page_labels, extract._template_html, extract.paragraph_prose_chars, extract.arc_story_html,
                   lambda x: extract.detect_paywall(x, None), extract.jsonld_authors, lambda x: extract.consent_wall(x, x),
                   lambda x: extract.detect_unreadable(x, "x"), extract.clean_author):
            t = time.process_time()
            fn(h)
            # Each took seconds to minutes before (76 s for 4,000 unclosed piano tags in page_labels).
            assert time.process_time() - t < 3.0, name
