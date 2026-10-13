"""Wire credits that a template keeps only in the data the page carries (extract.embedded_source).

Seven West Media pages (7NEWS, PerthNow, The West) name the source of the copy (AAP, AP, Reuters, BANG)
in the "source" field of the article's own object in window.PAGE_DATA and nowhere in the body, and
bylined wire copy "Digital Staff" or "Staff Writers". The source is read only from the object whose id
is the article's c-ID, never from a related card or a photo credit, and is credited beside a desk
byline so the existing author scoped Xinhua signatures can see it.
"""
import gzip
import json
import re
from pathlib import Path

import pytest

from pipeline import classify_rules, config, extract, fetch_articles, store

FIX = Path(__file__).parent / "fixtures" / "extraction"
GOLDEN = [c for c in json.loads((FIX / "golden.json").read_text(encoding="utf-8")) if "embedded_source" in c]
SEVEN = "en_au_7news_page_data"
PERTHNOW = "en_au_perthnow_page_data"
PERTHNOW_URL = "https://www.perthnow.com.au/news/politics/tung-chee-hwa-hk-first-leader-under-china-dies-at-89-c-22843175"
SEVEN_URL = ("https://7news.com.au/news/world/fitness-influencer-caught-cheating-at-berlin-marathon-after-hitching-"
             "ride-on-bike-during-race-c-22985359")
XINHUA_IDS = {"xinhua_author_field", "xinhua_co_credit_author", "xinhua_credit_line", "xinhua_wire_end",
              "xinhua_dateline", "xinhua_paren_inline", "state_outlet_source_label"}


def _page(name):
    with gzip.open(FIX / (name + ".html.gz"), "rt", encoding="utf-8") as fh:
        return fh.read()


def _own_source_as_xinhua(html):
    """The PerthNow fixture with only the article's own source changed from Reuters to Xinhua."""
    out, n = re.subn(r'("id":"C-22843175".{0,400}?"source":")Reuters"', r'\1Xinhua"', html, count=1, flags=re.S)
    assert n == 1
    return out


def _xinhua_hits(html, url, author):
    text = extract.extract(html, url)["text"]
    res = classify_rules.match_signatures("t", text, author, labels=extract.page_labels(html, url))
    return res["decision"], {m["id"] for m in res["matches"]} & XINHUA_IDS


@pytest.mark.parametrize("case", GOLDEN, ids=[c["name"] for c in GOLDEN])
def test_golden_pages_carry_their_own_source(case):
    html = _page(case["name"])
    want = tuple(case["embedded_source"])
    assert extract.embedded_source(html, case["url"]) == want
    # The classify stage rebuilds labels from the saved page without a URL: the page's og:url serves.
    assert extract.embedded_source(html) == want
    assert "source: %s" % want[0] in extract.page_labels(html).splitlines()


def test_both_seven_west_templates_are_covered():
    assert {c["name"] for c in GOLDEN} >= {SEVEN, PERTHNOW}


def test_photo_credit_naming_xinhua_is_not_the_articles_source():
    html = _page(SEVEN)
    assert '"credit":"AP, Xinhua"' in html  # a related card's picture credit on the real page
    assert extract.embedded_source(html, SEVEN_URL) == ("PerthNow", True)
    decision, hits = _xinhua_hits(html, SEVEN_URL, extract.wire_author("Digital Staff", None))
    assert not hits and decision != "A"


def test_xinhua_in_a_nested_image_credit_or_a_related_card_is_not_taken():
    html = _page(PERTHNOW)
    # A credit on an image inside the article's own object, and the source of a related card.
    html = html.replace('"credit":"AAP"', '"credit":"AP, Xinhua"', 1)
    html = re.sub(r'"source":"AAP"', '"source":"Xinhua"', html, count=1)
    assert extract.embedded_source(html, PERTHNOW_URL) == ("Reuters", False)
    author = extract.wire_author("Staff Writers", "Reuters")
    assert author == "Staff Writers; Reuters"
    decision, hits = _xinhua_hits(html, PERTHNOW_URL, author)
    assert not hits and decision != "A"


def test_own_source_xinhua_fires_the_existing_credit_signatures():
    html = _own_source_as_xinhua(_page(PERTHNOW))
    assert extract.embedded_source(html, PERTHNOW_URL) == ("Xinhua", False)
    assert "source: Xinhua" in extract.page_labels(html, PERTHNOW_URL).splitlines()
    # The desk byline as the feed gives it, and as extraction runs the two byline spans together.
    for byline in ("Staff Writers", "Staff Writers Xinhua"):
        author = extract.wire_author(byline, "Xinhua")
        assert author == "Staff Writers; Xinhua"
        decision, hits = _xinhua_hits(html, PERTHNOW_URL, author)
        assert decision == "A" and "xinhua_co_credit_author" in hits
    decision, hits = _xinhua_hits(html, PERTHNOW_URL, extract.wire_author(None, "Xinhua"))
    assert decision == "A" and "xinhua_author_field" in hits


def test_wire_author_leaves_named_writers_and_existing_credits_alone():
    assert extract.wire_author("Nick Wilson", "AAP") == "Nick Wilson"
    assert extract.wire_author("Staff Writers; Xinhua", "Xinhua") == "Staff Writers; Xinhua"
    assert extract.wire_author("Sebastian Tan, AAP", "AAP") == "Sebastian Tan, AAP"
    assert extract.wire_author("Digital Staff", "BANG") == "Digital Staff; BANG"
    assert extract.wire_author("Digital Staff", None) == "Digital Staff"


def test_other_templates_have_no_embedded_source():
    html = _page("en_au_abc")
    assert extract.embedded_source(html, "https://www.abc.net.au/news/2026-09-03/x/107101108") is None
    assert extract.embedded_source("<html><body>id C-1</body></html>", "https://7news.com.au/a-c-1") is None


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "RAW_HTML_DIR", tmp_path / "raw")
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.mark.parametrize("name,url,feed_author,source,want", [
    (PERTHNOW, PERTHNOW_URL, "Staff Writers", "Xinhua", "Staff Writers; Xinhua"),
    (PERTHNOW, PERTHNOW_URL, "Staff Writers", "Reuters", "Staff Writers; Reuters"),
    (SEVEN, SEVEN_URL, "Zach Margolius", "PerthNow", "Zach Margolius"),
])
def test_fetch_credits_the_wire_beside_a_desk_byline(conn, tmp_path, monkeypatch, name, url, feed_author,
                                                      source, want):
    html = _page(name)
    if source == "Xinhua":
        html = _own_source_as_xinhua(html)
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "au_x", "country": "AUS", "language": "en",
                                         "title": "t", "author": feed_author, "status": "queued",
                                         "gate_relevant": 1})
    conn.commit()
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda u: (True, "no_robots"))
    monkeypatch.setattr(fetch_articles, "get_page", lambda u: (html, 200, None))
    out = fetch_articles.process_article(lambda: store.connect(tmp_path / "t.db"), store.get_article(conn, aid))
    assert out["status"] == "fetched"
    row = store.get_article(conn, aid)
    assert row["author"] == want
    assert "source: %s" % source in row["page_labels"].splitlines()


# A named writer keeps the byline (wire_author leaves it alone), so the source line in the page labels
# is the only place the credit is read: state_outlet_source_label (ruleset 2026.10.1).
SEVEN_CID = "C-22985359"
PERTHNOW_CID = "C-22843175"


def _own_source_as(html, cid, old, new):
    """The fixture with only the source field of the article's own object changed."""
    out, n = re.subn(r'("id":"%s".{0,400}?"source":")%s"' % (cid, re.escape(old)), r'\g<1>%s"' % new, html,
                     count=1, flags=re.S)
    assert n == 1
    return out


@pytest.mark.parametrize("name,url,cid,old,writer", [
    (PERTHNOW, PERTHNOW_URL, PERTHNOW_CID, "Reuters", "Li Wei"),
    (SEVEN, SEVEN_URL, SEVEN_CID, "PerthNow", "Zach Margolius"),
])
def test_named_writer_with_own_source_xinhua_fires_the_source_label_signature(name, url, cid, old, writer):
    html = _own_source_as(_page(name), cid, old, "Xinhua")
    assert extract.embedded_source(html, url) == ("Xinhua", False)
    assert extract.wire_author(writer, "Xinhua") == writer
    # At fetch (with the URL) and at classify, which rebuilds labels from the saved page without one.
    for u in (url, None):
        text = extract.extract(html, url)["text"]
        res = classify_rules.match_signatures("t", text, writer, labels=extract.page_labels(html, u))
        ids = {m["id"] for m in res["matches"]}
        assert res["decision"] == "A" and "state_outlet_source_label" in ids
        assert not ids & {"xinhua_co_credit_author", "xinhua_author_field"}
        hit = next(m for m in res["matches"] if m["id"] == "state_outlet_source_label")
        assert hit["strength"] == "strong" and hit["scope"] == "labels"


@pytest.mark.parametrize("name,url,cid,old", [
    (PERTHNOW, PERTHNOW_URL, PERTHNOW_CID, "Reuters"),
    (SEVEN, SEVEN_URL, SEVEN_CID, "PerthNow"),
])
def test_a_state_outlet_in_a_photo_credit_or_nested_source_never_makes_a_source_line(name, url, cid, old):
    html = _page(name)
    # Inside the article's own object: every picture credit, and a "source" key of a nested object that
    # comes before the article's own one (requiredAccess), all naming Xinhua.
    html = re.sub(r'"credit":"[^"]*"', '"credit":"Xinhua"', html)
    html, n = re.subn(r'("id":"%s".{0,300}?"requiredAccess":\{"level":"anonymous")\}' % cid,
                      r'\g<1>,"source":"Xinhua"}', html, count=1, flags=re.S)
    assert n == 1
    assert extract.embedded_source(html, url) == (old, old != "Reuters")
    for u in (url, None):
        lines = [ln for ln in extract.page_labels(html, u).splitlines() if ln.startswith("source: ")]
        assert lines == ["source: %s" % old]
        decision, hits = _xinhua_hits(html, url, extract.wire_author("Li Wei", old))
        assert not hits and decision != "A"


def test_fetch_keeps_a_named_writer_and_the_source_line_fires_at_classify(conn, tmp_path, monkeypatch):
    html = _own_source_as(_page(PERTHNOW), PERTHNOW_CID, "Reuters", "Xinhua")
    aid = store.insert_discovered(conn, {"url": PERTHNOW_URL, "outlet_id": "au_x", "country": "AUS",
                                         "language": "en", "title": "t", "author": "Li Wei", "status": "queued",
                                         "gate_relevant": 1})
    conn.commit()
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda u: (True, "no_robots"))
    monkeypatch.setattr(fetch_articles, "get_page", lambda u: (html, 200, None))
    out = fetch_articles.process_article(lambda: store.connect(tmp_path / "t.db"), store.get_article(conn, aid))
    assert out["status"] == "fetched"
    row = store.get_article(conn, aid)
    assert row["author"] == "Li Wei"
    res = classify_rules.match_signatures(row["title"], extract.extract(html, PERTHNOW_URL)["text"], row["author"],
                                          labels=classify_rules.labels_for(row))
    assert res["decision"] == "A"
    assert "state_outlet_source_label" in {m["id"] for m in res["matches"]}
