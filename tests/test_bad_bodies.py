"""Bodies that are not the article: challenge pages, missed soft paywalls, menus, outlet templates.
They are recorded as failed or paywalled and never classified, and a label already made on one goes."""
import gzip
import json
from pathlib import Path

import pytest

from pipeline import body_dups, classify_rules as cr, config, extract, fetch_articles, store

BAD = Path(__file__).parent / "fixtures" / "bad_bodies"
FIX = Path(__file__).parent / "fixtures" / "extraction"
CASES = json.loads((BAD / "cases.json").read_text(encoding="utf-8"))

PROSE = ("The minister said on Monday that the talks would continue next week in the capital. "
         "Officials from both governments met for three hours and agreed to set up a working group.\n") * 3
MENU = "\n".join(["Home", "World", "Business", "Sport", "Opinion", "Lifestyle", "Video", "Podcasts", "Subscribe",
                  "Log in", "Weather", "Contact us", "About us", "Advertise", "Privacy", "Terms"] * 3)


def _html(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "RAW_HTML_DIR", tmp_path / "raw")
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_saved_bad_pages_are_never_fetched_as_articles(case):
    html = _html(BAD / (case["name"] + ".html.gz"))
    text = extract.extract(html, case["url"])["text"]
    pw, pw_why = extract.detect_paywall(html, text)
    bad, bad_why = extract.detect_unreadable(html, text)
    assert pw or bad
    if case["paywall"]:
        assert pw and pw_why == case["paywall"]
    else:
        assert not pw and bad and bad_why == case["unreadable"]


def test_challenge_text_alone_is_recognised_without_the_page():
    body = _html(BAD / "fr_lemonde_challenge.html.gz")
    text = extract.extract(body, None)["text"]
    assert extract.detect_unreadable(None, text)[0]


def test_prose_measure_separates_menus_from_running_text():
    assert extract.prose_chars(MENU) == 0
    assert extract.prose_chars(PROSE) > 400
    assert extract.detect_unreadable(None, MENU) == (True, "no article text: 0 characters of running prose")
    assert extract.detect_unreadable(None, PROSE) == (False, None)
    # Japanese has no word spaces: a sentence counts by its characters, a menu item does not.
    assert extract.prose_chars("台湾で、中国による侵攻に備えて、年に一度の大規模な軍事演習が行われました。") > 0
    assert extract.prose_chars("地図から選ぶ") == 0
    # Under the fetch minimum the extraction_empty rule governs, not this one.
    assert extract.detect_unreadable(None, "Short note.") == (False, None)


def test_premium_embedded_article_is_paywalled_and_never_read():
    html = _html(FIX / "en_my_nst_embedded.html.gz").replace("&quot;premium&quot;:false", "&quot;premium&quot;:true")
    assert extract.detect_paywall(html, None) == (True, "embedded article marked premium")
    assert extract.embedded_article(html)["premium"] is True
    assert extract.prose_chars(extract.extract(html, None)["text"]) < extract.MIN_PROSE_CHARS


def test_truncation_marker_holds_for_a_longer_teaser():
    html = '<p>本文</p><div>この記事は有料記事です。残り<b>886</b>文字</div>'
    assert extract.detect_paywall(html, "x" * 900) == (True, "truncation marker: 残り886文字")
    assert extract.detect_paywall(html, "x" * 2000) == (False, None)


def test_headline_overlap():
    assert body_dups.headline_overlap("Talks with ministers continue", PROSE) == 0.5
    assert body_dups.headline_overlap("Shein shares plunge", PROSE) == 0.0
    assert body_dups.headline_overlap("台湾軍 演習", "台湾軍が演習") == 1.0
    assert body_dups.headline_overlap("A B", PROSE) is None


def _article(conn, n, title, body, outlet="o1", status="classified", label="C"):
    url = "https://t.test/%s/%d" % (outlet, n)
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": "USA", "language": "en",
                                         "title": title, "status": status, "gate_relevant": 1})
    h = store.save_body(store.url_hash(url), body)
    store.update_article(conn, aid, body_hash=h, body_chars=len(body))
    if label:
        store.insert_classification(conn, aid, "rules", label, 0.75, ruleset_version="2026.09.9")
    conn.commit()
    row = store.get_article(conn, aid)
    body_dups.record(conn, row, body)
    conn.commit()
    return aid


OTHER_STORY = ("UNAMA said the report presents findings from research conducted by its human rights service on "
               "the victims of armed conflict, documenting civilian deaths and injuries across the provinces "
               "between the years covered, and the needs of the families left behind by the violence.\n") * 4


def test_template_that_does_not_carry_its_headlines_is_unreadable(conn):
    ids = [_article(conn, i, t, OTHER_STORY) for i, t in enumerate(
        ["Pakistan premier warns on terrorism at regional summit", "Taliban dismiss summit decisions",
         "India links Eurasian stability to Kabul"])]
    body_dups.link_pending(conn)
    assert all(conn.execute("SELECT template FROM body_fingerprints WHERE article_id=?", (i,)).fetchone()[0] for i in ids)
    why = body_dups.template_unreadable(conn, store.get_article(conn, ids[0]), OTHER_STORY)
    assert why and why.startswith("template body:")
    out = cr.classify_article(conn, store.get_article(conn, ids[0]))
    assert out == {"outcome": "unreadable"}
    row = store.get_article(conn, ids[0])
    assert row["status"] == "failed" and row["fail_reason"].startswith("unreadable: template body")
    assert store.current_classification(conn, ids[0]) is None


def test_template_that_carries_its_headlines_is_kept(conn):
    ids = [_article(conn, 10 + i, t, OTHER_STORY) for i, t in enumerate(
        ["UNAMA report documents civilian deaths of armed conflict", "Victims of armed conflict: UNAMA findings"])]
    body_dups.link_pending(conn)
    assert conn.execute("SELECT template FROM body_fingerprints WHERE article_id=?", (ids[0],)).fetchone()[0]
    assert body_dups.template_unreadable(conn, store.get_article(conn, ids[0]), OTHER_STORY) is None


def test_label_on_a_challenge_body_is_withdrawn(conn):
    text = extract.extract(_html(BAD / "en_tw_digitimes_blocked.html.gz"), None)["text"]
    aid = _article(conn, 20, "Rare earths loom over US-China trade truce", text)
    out = cr.classify_article(conn, store.get_article(conn, aid))
    assert out == {"outcome": "unreadable"}
    row = store.get_article(conn, aid)
    assert row["status"] == "failed" and "challenge page" in row["fail_reason"]
    assert store.current_classification(conn, aid) is None
    assert conn.execute("SELECT COUNT(*) FROM classifications WHERE article_id=?", (aid,)).fetchone()[0] == 1


def test_menu_body_is_read_again_from_the_saved_page(conn):
    html = _html(FIX / "en_my_nst_embedded.html.gz")
    aid = _article(conn, 30, "Missing Malaysians: Families seek urgent meeting with Anwar", MENU, label="not_relevant")
    row = store.get_article(conn, aid)
    store.update_article(conn, aid, raw_html_path=store.save_raw_html(row["url_hash"], html))
    conn.commit()
    out = cr.classify_article(conn, store.get_article(conn, aid))
    assert out["outcome"] != "unreadable"
    body = store.load_body(row["url_hash"])
    assert body.startswith("KUALA LUMPUR: Malaysia Solidarity")
    assert store.get_article(conn, aid)["body_chars"] == len(body)


def test_short_body_is_read_again_when_the_page_holds_the_article(conn):
    html = _html(FIX / "en_au_lowy_prose_blocks.html.gz")
    standfirst = ("Half of China's recent ambassadors to Canberra went on to the vice-ministerial ranks, and the "
                  "odds for the new envoy look good.\nLiu Jinsong, photographed at the ministry in Beijing.")
    aid = _article(conn, 40, "Canberra will be a proving ground for Liu Jinsong's promotion path", standfirst,
                   outlet="au_lowyinterpreter", label="not_relevant")
    row = store.get_article(conn, aid)
    store.update_article(conn, aid, raw_html_path=store.save_raw_html(row["url_hash"], html))
    conn.commit()
    cr.classify_article(conn, store.get_article(conn, aid))
    body = store.load_body(row["url_hash"])
    assert body.startswith("Within the spectrum of Chinese diplomats, Liu Jinsong")
    assert len(body) > 4000


def test_fetch_records_a_challenge_page_as_failed(conn, tmp_path, monkeypatch):
    html = _html(BAD / "fr_lemonde_challenge.html.gz")
    aid = store.insert_discovered(conn, {"url": "https://www.lemonde.fr/a.html", "outlet_id": "fr_x", "country": "FRA",
                                         "language": "fr", "title": "t", "status": "queued", "gate_relevant": 1})
    conn.commit()
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (True, "no_robots"))
    monkeypatch.setattr(fetch_articles, "get_page", lambda url: (html, 200, None))
    out = fetch_articles.process_article(lambda: store.connect(tmp_path / "t.db"), store.get_article(conn, aid))
    assert out["status"] == "failed" and out["reason"] == "unreadable: challenge page: Client Challenge"
    row = store.get_article(conn, aid)
    assert row["status"] == "failed" and row["body_hash"] is None
    assert not row["fail_reason"].startswith(fetch_articles.TRANSIENT_FAIL_PREFIXES)


def test_refetch_of_the_same_bad_page_withdraws_the_label(conn, tmp_path, monkeypatch):
    """On a runner without the body, the re-fetch shows the page the label was made on."""
    from pipeline import registry
    html = _html(BAD / "fr_lemonde_challenge.html.gz")
    text = extract.extract(html, None)["text"]
    aid = _article(conn, 50, "La Chine impose un contrôle strict", text, outlet="fr_x")
    row = store.get_article(conn, aid)
    store.body_path(row["url_hash"]).unlink()
    monkeypatch.setattr(registry, "is_mine", lambda *a, **k: True)
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (True, "no_robots"))
    monkeypatch.setattr(fetch_articles, "get_page", lambda url: (html, 200, None))
    monkeypatch.setattr(store, "connect", lambda *a, **k: _real_connect(tmp_path / "t.db"))
    out = cr.classify_article(conn, store.get_article(conn, aid))
    assert out == {"outcome": "body_unavailable"}
    assert store.current_classification(conn, aid) is None
    assert store.get_article(conn, aid)["status"] == "failed"


_real_connect = store.connect


SEZNAM_HEADLINE = "110 kilometrů od Tchaj-wanu. Japonsko mění ostrovy v obrannou linii"
BANNER = ("We value your privacy\n"
          "We and our partners store and/or access information on a device, such as cookies, and process personal "
          "data for personalised ads and content, ad and content measurement, audience insights.\n"
          "Accept all\nReject all\nManage preferences")


def _seznam_text():
    return extract.extract(_html(BAD / "cz_seznam_consent.html.gz"), None)["text"]


def test_consent_wall_is_unreadable_at_any_length():
    text = _seznam_text()
    assert len(text) > extract.SHORT_BODY_CHARS and extract.prose_chars(text) > extract.MIN_PROSE_CHARS
    assert extract.detect_unreadable(None, text, SEZNAM_HEADLINE) == (True, "consent wall")
    assert extract.detect_unreadable(None, text) == (True, "consent wall")
    # The wall's own words do not make it the article.
    assert extract.detect_unreadable(None, text, "It seems the page failed to load correctly") == (False, None)


def test_consent_platform_wall_with_unrelated_headline_is_unreadable():
    assert extract.detect_unreadable(None, BANNER * 3, "Taiwan stages drills simulating invasion") == \
        (True, "consent wall")
    google = ("Before you continue to Google\nWe use cookies and data to deliver and maintain Google services, "
              "measure audience engagement and site statistics, and show personalised ads.\nAccept all\nReject all")
    assert extract.detect_unreadable('<title>Before you continue to Google</title>', google, "Xi meets Lula") == \
        (True, "consent wall")


def test_article_with_a_small_cookie_banner_is_kept():
    headline = "Talks with ministers continue next week"
    for body in (BANNER + "\n" + PROSE, PROSE + BANNER, PROSE + "Didomi consent settings\n"):
        assert extract.detect_unreadable(None, body, headline) == (False, None)
        assert extract.detect_unreadable(None, body) == (False, None)
    # Mostly banner, but the body carries its headline: never failed on the banner.
    assert extract.detect_unreadable(None, BANNER + "\nTalks with ministers continue next week.", headline) == \
        (False, None)
    # Indonesian "didominasi" (dominated by) is not Didomi.
    assert not extract.consent_wall(None, "Langit didominasi warna kemerahan, kata warga.\n" * 5, None)


@pytest.mark.parametrize("case", json.loads((FIX / "golden.json").read_text(encoding="utf-8")),
                         ids=lambda c: c["name"])
def test_golden_articles_under_consent_platforms_are_not_walls(case):
    """The golden pages load Sourcepoint, Didomi, Funding Choices, Taboola and other banners."""
    html = _html(FIX / (case["name"] + ".html.gz"))
    text = extract.extract(html, case["url"])["text"]
    assert not extract.consent_wall(html, text, case["title"])
    assert not extract.consent_wall(html, text, None)


def test_fetch_records_a_consent_wall_as_failed(conn, tmp_path, monkeypatch):
    html = _html(BAD / "cz_seznam_consent.html.gz")
    aid = store.insert_discovered(conn, {"url": "https://www.seznamzpravy.cz/clanek/a-1", "outlet_id": "cz_seznam",
                                         "country": "CZE", "language": "cs", "title": SEZNAM_HEADLINE,
                                         "status": "queued", "gate_relevant": 1})
    conn.commit()
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (True, "no_robots"))
    monkeypatch.setattr(fetch_articles, "get_page", lambda url: (html, 200, None))
    out = fetch_articles.process_article(lambda: store.connect(tmp_path / "t.db"), store.get_article(conn, aid))
    assert out["status"] == "failed" and out["reason"] == "unreadable: consent wall"
    row = store.get_article(conn, aid)
    assert row["status"] == "failed" and row["body_hash"] is None
    assert not row["fail_reason"].startswith(fetch_articles.TRANSIENT_FAIL_PREFIXES)


def test_label_on_a_consent_wall_is_withdrawn(conn):
    aid = _article(conn, 60, SEZNAM_HEADLINE, _seznam_text(), outlet="cz_seznam")
    out = cr.classify_article(conn, store.get_article(conn, aid))
    assert out == {"outcome": "unreadable"}
    row = store.get_article(conn, aid)
    assert row["status"] == "failed" and row["fail_reason"] == "unreadable: consent wall"
    assert store.current_classification(conn, aid) is None
