"""Bodies extracted under an older reading of their outlet's template (extract.EXTRACTION_CHANGES).

The Straits Times paid release bodies stored before the payload reading were the disclaimer and the
headline, and the Antara English bodies lacked the stamped lead. classify_rules.ensure_body reused a
stored body, so a reclassification would have read the stubs again. A stale body is now extracted
again from the saved page, or fetched again politely when the page is not on disk.
"""
import gzip
from pathlib import Path

import pytest

from pipeline import classify_rules as cr
from pipeline import config, extract, fetch_articles, store

FIX = Path(__file__).parent / "fixtures" / "extraction"
PPR_URL = ("https://www.straitstimes.com/paid-press-releases/"
           "xinhua-silk-road-chinese-youths-explore-mazu-culture-in-trendy-ways-20260929")
PPR_TITLE = "Press releaseXinhua Silk Road: Chinese youths explore Mazu culture in trendy ways"
# What extraction stored before the payload reading: the issuer disclaimer and the headline.
PPR_STUB = ("This press release is issued by the company named in it. The Straits Times is not responsible for "
            "its content.\nXinhua Silk Road: Chinese youths explore Mazu culture in trendy ways")
ANTARA_URL = ("https://en.antaranews.com/news/434130/"
              "hangzhou-investment-promotion-bureau-hangzhou-finds-a-gateway-to-the-sea-in-hong-kong")


def _page(name):
    with gzip.open(FIX / (name + ".html.gz"), "rt", encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "RAW_HTML_DIR", tmp_path / "raw")
    conn = store.connect(tmp_path / "t.db")
    yield conn
    conn.close()


def _article(conn, url, outlet, title, body, html=None, version=None, label=("rules", "not_relevant", "2026.09.9")):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": "SGP", "language": "en",
                                         "title": title, "status": "fetched", "gate_relevant": 1})
    h = store.url_hash(url)
    fields = dict(status="classified", fetch_attempts=1, body_hash=store.save_body(h, body), body_chars=len(body),
                  extract_version=version)
    if html is not None:
        fields["raw_html_path"] = store.save_raw_html(h, html)
    store.update_article(conn, aid, **fields)
    if label:
        store.insert_classification(conn, aid, label[0], label[1], 0.7, ruleset_version=label[2])
    conn.commit()
    return aid


def _no_fetch(monkeypatch):
    calls = []

    def fake(factory, row):
        calls.append(row["id"])
        return {"id": row["id"], "status": "failed", "reason": "http_503"}
    monkeypatch.setattr(fetch_articles, "process_article", fake)
    return calls


def test_only_listed_templates_under_an_older_version_are_stale():
    assert extract.stale_extraction(PPR_URL, None)
    assert extract.stale_extraction(ANTARA_URL, None)
    assert extract.stale_extraction(PPR_URL, "2026.9.9")
    assert extract.stale_extraction(PPR_URL, config.EXTRACT_VERSION) is None
    # Other sections of the same outlets, and every other outlet, keep their bodies.
    assert extract.stale_extraction("https://www.straitstimes.com/asia/east-asia/some-story", None) is None
    assert extract.stale_extraction("https://www.antaranews.com/berita/123/cerita", None) is None
    assert extract.stale_extraction("https://example.com/story", None) is None
    assert extract.stale_extraction(None, None) is None
    # Versions compare as numbers, not as text.
    assert extract._version_key("2026.10.1") > extract._version_key("2026.9.9")
    for changed, prefix, why in extract.EXTRACTION_CHANGES:
        assert extract._version_key(changed) <= extract._version_key(config.EXTRACT_VERSION), prefix
        assert why


def test_stale_body_is_extracted_again_from_the_saved_page(db, monkeypatch):
    calls = _no_fetch(monkeypatch)
    aid = _article(db, PPR_URL, "sg_straitstimes", PPR_TITLE, PPR_STUB, html=_page("en_sg_straitstimes_ppr_xinhua"))
    row = store.get_article(db, aid)
    assert cr.stale_body(db, row)
    body = cr.ensure_body(db, row)
    assert body.startswith("Xinhua Silk Road: Chinese youths explore Mazu culture in trendy ways\nBEIJING, Sept. 29, 2026 /PRNewswire/ --")
    assert calls == []  # the saved page was read; nothing was requested
    a = store.get_article(db, aid)
    assert a["extract_version"] == config.EXTRACT_VERSION
    assert a["body_chars"] == len(body) and store.load_body(a["url_hash"]) == body
    assert a["status"] == "classified"
    assert cr.stale_body(db, row) is None  # read from the database, not from the old row
    assert cr.ensure_body(db, row) == body


def test_stale_body_without_a_saved_page_is_fetched_again_through_the_fetch_path(db, monkeypatch):
    aid = _article(db, PPR_URL, "sg_straitstimes", PPR_TITLE, PPR_STUB)
    calls = []

    def refetch(factory, row):
        # What process_article does on success: the body saved, stamped with the current version.
        calls.append(row["id"])
        text = "Xinhua Silk Road: Chinese youths explore Mazu culture\nBEIJING, Sept. 29, 2026 /PRNewswire/ -- " + "x " * 300
        store.update_article(db, row["id"], status="fetched", body_hash=store.save_body(row["url_hash"], text),
                             body_chars=len(text), extract_version=config.EXTRACT_VERSION)
        db.commit()
        return {"id": row["id"], "status": "fetched"}
    monkeypatch.setattr(fetch_articles, "process_article", refetch)
    body = cr.ensure_body(db, store.get_article(db, aid))
    assert calls == [aid] and "/PRNewswire/" in body
    assert store.get_article(db, aid)["status"] == "classified"


def test_stale_body_that_cannot_be_fetched_again_is_not_classified(db, monkeypatch):
    calls = _no_fetch(monkeypatch)
    aid = _article(db, PPR_URL, "sg_straitstimes", PPR_TITLE, PPR_STUB)
    assert cr.ensure_body(db, store.get_article(db, aid)) == ""
    assert calls == [aid]
    a = store.get_article(db, aid)
    # The old label stands and the article keeps its status; the stub is never read again as the article.
    assert a["status"] == "classified" and a["fail_reason"].startswith("refetch_failed")
    assert store.current_classification(db, aid)["category"] == "not_relevant"
    assert cr.classify_article(db, store.get_article(db, aid)) == {"outcome": "body_unavailable"}
    # Out of attempts, nothing more is requested.
    db.execute("UPDATE articles SET fetch_attempts=? WHERE id=?", (cr.REFETCH_ATTEMPT_LIMIT, aid))
    db.commit()
    calls.clear()
    assert cr.ensure_body(db, store.get_article(db, aid)) == ""
    assert calls == []


def test_current_and_unlisted_bodies_are_used_as_stored(db, monkeypatch):
    calls = _no_fetch(monkeypatch)
    page = _page("en_sg_straitstimes_ppr_xinhua")
    fresh = _article(db, PPR_URL, "sg_straitstimes", PPR_TITLE, PPR_STUB, html=page, version=config.EXTRACT_VERSION)
    other = _article(db, "https://www.straitstimes.com/asia/east-asia/a-story", "sg_straitstimes", "A story",
                     "Body of an ordinary story. " * 20, html=page)
    for aid in (fresh, other):
        row = store.get_article(db, aid)
        assert cr.stale_body(db, row) is None
        assert cr.ensure_body(db, row) == store.load_body(row["url_hash"])
        assert store.get_article(db, aid)["body_hash"] == row["body_hash"]
    assert calls == []


def test_reclassify_reads_the_release_and_labels_it_state_origin(db, monkeypatch):
    calls = _no_fetch(monkeypatch)
    aid = _article(db, PPR_URL, "sg_straitstimes", PPR_TITLE, PPR_STUB, html=_page("en_sg_straitstimes_ppr_xinhua"))
    out = cr.reclassify(db, "t")
    assert out["stale_body"] == 1 and out["A"] == 1 and calls == []
    cur = store.current_classification(db, aid)
    assert cur["category"] == "A" and cur["ruleset_version"] == config.RULESET_VERSION
    # Done once: a second pass finds nothing to do.
    assert cr.reclassify(db, "t")["input"] == 0


def test_reclassify_takes_a_stale_body_whatever_the_ruleset_of_its_label(db, monkeypatch):
    calls = _no_fetch(monkeypatch)
    aid = _article(db, ANTARA_URL, "id_antara_en",
                   'Hangzhou Investment Promotion Bureau: Hangzhou Finds a "Gateway to the Sea" in Hong Kong',
                   "Hong Kong is the gateway. " * 30, html=_page("en_id_antara_release"),
                   label=("rules", "C", config.RULESET_VERSION))
    out = cr.reclassify(db, "t")
    assert out["input"] == 1 and out["stale_body"] == 1 and calls == []
    assert store.load_body(store.get_article(db, aid)["url_hash"]).startswith("Hangzhou, China (ANTARA/PRNewswire)- On September 22,")
    assert out["llm"] == 1  # the stamped lead now reaches the model, as at the release's other outlets
    # Waiting for the model it is picked up again, as every candidate is, but its body is current now.
    assert cr.reclassify(db, "t").get("stale_body", 0) == 0


def test_a_model_label_made_on_a_stub_gives_way_when_the_release_is_state_origin(db, monkeypatch):
    calls = _no_fetch(monkeypatch)
    aid = _article(db, PPR_URL, "sg_straitstimes", PPR_TITLE, PPR_STUB, html=_page("en_sg_straitstimes_ppr_xinhua"),
                   label=("llm", "C", "2026.09.9"))
    out = cr.reclassify(db, "t")
    assert out["A"] == 1 and calls == []
    cur = store.current_classification(db, aid)
    assert (cur["method"], cur["category"]) == ("rules", "A")


def test_fetch_stamps_the_extraction_version(db, tmp_path, monkeypatch):
    aid = store.insert_discovered(db, {"url": PPR_URL, "outlet_id": "sg_straitstimes", "country": "SGP", "language": "en",
                                       "title": PPR_TITLE, "status": "queued", "gate_relevant": 1})
    db.commit()
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (True, "robots_allowed"))
    monkeypatch.setattr(fetch_articles, "get_page", lambda url: (_page("en_sg_straitstimes_ppr_xinhua"), 200, None))
    res = fetch_articles.process_article(lambda: store.connect(tmp_path / "t.db"), store.get_article(db, aid))
    assert res["status"] == "fetched"
    a = store.get_article(db, aid)
    assert a["extract_version"] == config.EXTRACT_VERSION
    assert cr.stale_body(db, a) is None
