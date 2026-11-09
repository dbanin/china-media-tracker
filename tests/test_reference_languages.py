"""Reference sources, round of 2026-11-09: the extraction fixes (CGTN transcript pages, People's Daily video
pages, ECNS photo redirects), the honest reasons for pages with no text, the one time retry of rows failed by
the earlier extraction, and the language editions of Xinhua, CGTN and People's Daily with the copies.py
normalisation their copies need. Decision D10 holds throughout: reference items are originals only, never
classified, never counted (tests/test_reference.py keeps the export invariance test)."""
import datetime as dt
import html as htmllib
import json
import re

import pytest

from pipeline import config, copies, extract, fetch_articles, reference, store
from tests import test_copies as tc

NOW = dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.timezone.utc)
NEW = ["ref_xinhua_fr", "ref_xinhua_es", "ref_xinhua_pt", "ref_xinhua_ar", "ref_xinhua_ru", "ref_cgtn_es", "ref_cgtn_fr",
       "ref_cgtn_ar", "ref_cgtn_ru", "ref_cgtn_pt", "ref_cgtn_it", "ref_peoples_daily_fr", "ref_peoples_daily_es"]
PROSE = ("The lake in northern China has reached the third water quality class for five years in a row, "
         "monitoring staff said on Sunday at the center that tests a bottle of its water every week.")


def _src(sid):
    return {s["id"]: s for s in reference.load_sources()}[sid]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    return store.connect(tmp_path / "t.db")


# ---------------------------------------------------------------------------
# Where the text is when extraction misses it
# ---------------------------------------------------------------------------

def _cgtn_transcript_html(blocks):
    data = htmllib.escape(json.dumps([{"contentType": 1, "content": b} for b in blocks]), quote=True)
    return ('<html><head><title>Water Quality Restoration - CGTN</title></head><body><div class="menu">Choose your language'
            '</div><div class="content" id="cmsMainContent" data-json="%s"></div></body></html>' % data)


def test_a_cgtn_transcript_is_read_from_its_content_json():
    html = _cgtn_transcript_html([PROSE + "&nbsp;", "NING HONG Xiongan \"Looking at the water grass.<br>It is cleaner.\""])
    text = reference.cgtn_content_json(html)
    assert text.split("\n") == [PROSE, "NING HONG Xiongan \"Looking at the water grass.", "It is cleaner.\""]
    assert reference.cgtn_content_json("<html><div id='cmsMainContent' data-json='not json'></div></html>") is None
    assert reference.cgtn_content_json("<html><p>no store</p></html>") is None


def _nuxt_page(article_id, content, sidebar_id="ARTI1777000000000001"):
    # a flat Nuxt payload: objects point at other entries by index; the sidebar story comes first
    data = [{"story": 1, "side": 5},
            {"id": 2, "title": 3, "content": 4},
            article_id, "Visita del ministro", content,
            {"id": 6, "title": 7, "content": 8},
            sidebar_id, "Otra noticia", "<p>" + "Otra historia de la barra lateral con mucho texto. " * 20 + "</p>"]
    return ('<html><head><title>Visita del ministro</title></head><body><p>%s</p>'
            '<script type="application/json" id="__NUXT_DATA__" data-ssr="true">%s</script></body></html>'
            % ("Otra historia de la barra lateral con mucho texto. " * 20, json.dumps(data)))


def test_a_cgtn_language_page_is_read_from_its_nuxt_payload_by_its_own_id():
    url = "https://espanol.cgtn.com/2026/11/09/ARTI1794221271196158"
    content = ('<p><video src="https://v.example/x.m3u8"></video></p><p>https://38vodres.cgtn.com/cgtn/x.m3u8</p>'
               '<p>El presidente de China envio un mensaje de felicitacion.</p><p>Foto: CFP</p>')
    html = _nuxt_page("ARTI1794221271196158", content)
    text = reference.cgtn_nuxt_article(html, url)
    assert "barra lateral" not in text and "El presidente de China" in text
    assert reference.clean_body(text, "cgtn_nuxt") == "El presidente de China envio un mensaje de felicitacion.\nFoto: CFP"
    # another page's id, no payload, a broken payload: nothing
    assert reference.cgtn_nuxt_article(html, "https://espanol.cgtn.com/2026/11/09/ARTI1794221271196159") is None
    assert reference.cgtn_nuxt_article("<html></html>", url) is None
    assert reference.cgtn_nuxt_article(html.replace('[{"story"', '[{story'), url) is None


def test_peoples_daily_video_and_language_pages_are_read_from_their_text_containers():
    pd = ('<html><body><div class="w860 d2txtCon cf"><p>showPlayer({scriptId: "q", videoInfo: []})</p><p>%s</p></div>'
          '<div class="rel"><p>Related Stories</p></div></body></html>' % PROSE)
    assert reference.alternative_text(pd, "peoples_daily") == PROSE
    fr = '<html><body><ul><li>- Plus</li></ul><div class="w1280 fren-textCon clearfix"><p>%s</p><p>Fin</p></div></body></html>' % PROSE
    assert reference.alternative_text(fr, "peoples_daily_lang") == PROSE + "\nFin"
    es = '<html><body><div class="w1280 span-d2Content span-textCon clearfix"><p>%s</p></div></body></html>' % PROSE
    assert reference.alternative_text(es, "peoples_daily_lang") == PROSE
    assert reference.alternative_text(es, "none") is None


def test_a_bare_meta_refresh_is_followed_only_within_the_site():
    page = '<html><head><META HTTP-EQUIV="Refresh" CONTENT="0;URL=https://www.ecns.cn/hd/2026-11-06/detail-a.shtml"></head></html>'
    assert reference.meta_refresh_target(page, "http://www.ecns.cn/photo/2026-11-06/detail-b.shtml") == \
        "https://www.ecns.cn/hd/2026-11-06/detail-a.shtml"
    other = page.replace("www.ecns.cn/hd", "evil.example/hd")
    assert reference.meta_refresh_target(other, "http://www.ecns.cn/photo/x.shtml") is None
    assert reference.meta_refresh_target(page + "<p>%s</p>" % (PROSE * 30), "http://www.ecns.cn/photo/x.shtml") is None
    assert reference.meta_refresh_target(page, "https://www.ecns.cn/hd/2026-11-06/detail-a.shtml") is None


# ---------------------------------------------------------------------------
# Pages with no text say so
# ---------------------------------------------------------------------------

def test_no_text_reasons():
    alert = "I agree\n07:24, 09-Nov-2026\nBREAKING NEWS\nTRUMP ON PRESS SECRETARY\nBREAKING NEWS\nsubscribe"
    assert reference.no_text_reason(alert, "TRUMP ON PRESS SECRETARY", "TRUMP ON PRESS SECRETARY", False, 0) == \
        "no_text: breaking news alert, headline only"
    caption = "The pandas have made a cuddly debut in Atlanta!\nNitza Soledad Perez has the story."
    assert reference.no_text_reason("01:47\n" + caption, caption, "Pandas premiere in Atlanta", True, 90) == \
        "no_text: video, %d characters of caption" % len(caption.replace("\n", " "))
    flash = "Source: Xinhua\nEditor: huaxia\n2026-11-08 21:01:00\nFlash: Train accident in Egypt"
    assert reference.no_text_reason(flash, flash, "Flash: Train accident in Egypt", False, 77) == "no_text: headline only"
    brief = "BEIJING, Oct. 30 (Xinhua) -- Chinese President Xi Jinping met with the Swiss president on Friday."
    assert reference.no_text_reason(brief, brief, "Xi meets Swiss president", False, 98) == "short_text: %d characters" % len(brief)
    # the page's own paragraphs hold more text than the body: an extraction failure, kept as one
    assert reference.no_text_reason(caption, caption, "Pandas", True, reference.NO_TEXT_PAGE_PROSE) is None


class Net:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []
        self.robots = []

    def install(self, monkeypatch):
        monkeypatch.setattr(fetch_articles, "can_fetch", lambda u: (self.robots.append(u), (True, "no_robots"))[1])
        monkeypatch.setattr(fetch_articles, "get_page", self.get)

    def get(self, url):
        self.requests.append(url)
        if url in self.pages:
            return self.pages[url], 200, None
        return None, 404, "http_404"


def _item(db, sid, url, title, status="discovered", fail_reason=None, version=None):
    cur = db.execute("""INSERT INTO reference_items(source_id, url, url_hash, language, title, published_at, discovered_at,
                            status, fail_reason, extract_version, fetched_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (sid, url, store.url_hash(url), _src(sid)["language"], title, "2026-11-09T01:00:00+00:00",
                      "2026-11-09T02:00:00+00:00", status, fail_reason, version,
                      None if status == "discovered" else "2026-10-17T02:00:00+00:00"))
    db.commit()
    return cur.lastrowid


def _row(db, rid):
    return db.execute("SELECT * FROM reference_items WHERE id=?", (rid,)).fetchone()


CGTN_HEAD = "Choose your language\nAlbanian\nShqip\nBy continuing to browse our site you agree to our use of cookies.\nI agree\n"
CGTN_TAIL = "\nSearch Trends\nCHOOSE YOUR LANGUAGE\nCopyright © 2026 CGTN."


def test_fetch_gives_each_page_its_honest_outcome(db, monkeypatch):
    flash = "https://news.cgtn.com/news/2026-11-09/news-1RVbBfnwteo/p.html"
    video = "https://newsus.cgtn.com/news/2026-11-09/Pandas-premiere-in-Atlanta-1RS00OpaOD6/p.html"
    transcript = "https://news.cgtn.com/news/2026-11-08/VHJhbnNjcmlwdDkzMTc4/index.html"
    buggy = "https://news.cgtn.com/news/2026-11-08/Long-feature-1RTest/p.html"
    photo = "http://www.ecns.cn/photo/2026-11-06/detail-ihfmuiku4868368.shtml"
    gallery = "https://www.ecns.cn/hd/2026-11-06/detail-ihfmuiku4868294.shtml"
    texts = {
        flash: CGTN_HEAD + "07:24, 09-Nov-2026\nBREAKING NEWS\nTRUMP ON PRESS SECRETARY\nBREAKING NEWS" + CGTN_TAIL,
        video: CGTN_HEAD + "North America\n03:25, 09-Nov-2026\n01:47\nThe pandas have made a cuddly debut in Atlanta!" + CGTN_TAIL,
        transcript: CGTN_HEAD + CGTN_TAIL,
        buggy: CGTN_HEAD + "short head only" + CGTN_TAIL,
    }
    pages = {
        flash: "<html><p>By continuing to browse our site you agree to our use of cookies, revised Privacy Policy.</p></html>",
        video: '<html><meta property="og:video" content="x"><p>The pandas have made a cuddly debut in Atlanta!</p></html>',
        transcript: _cgtn_transcript_html([PROSE, PROSE.replace("lake", "river")]),
        buggy: "<html>%s</html>" % "".join("<p>%s</p>" % PROSE.replace("Sunday", d) for d in ("Monday", "Tuesday", "Friday")),
        photo: '<html><head><META HTTP-EQUIV="Refresh" CONTENT="0;URL=%s"></head></html>' % gallery,
        gallery: "<html><article><p>%s</p><p>%s</p></article></html>" % (PROSE, PROSE),
    }
    net = Net(pages)
    net.install(monkeypatch)
    real = extract.extract

    def fake(html, url=None):
        if url in texts:
            return {"text": texts[url], "title": None, "author": None, "date": None}
        return real(html, url)
    monkeypatch.setattr(extract, "extract", fake)
    ids = {u: _item(db, sid, u, t) for u, sid, t in [
        (flash, "ref_cgtn_en", "TRUMP ON PRESS SECRETARY"), (video, "ref_cgtn_en", "Pandas premiere in Atlanta"),
        (transcript, "ref_cgtn_en", "Water Quality Restoration"), (buggy, "ref_cgtn_en", "Long feature"),
        (photo, "ref_ecns_en", "Shepherd's purse enters harvest season in Henan")]}
    reference.fetch(db, sources=[_src("ref_cgtn_en"), _src("ref_ecns_en")], now=NOW)
    got = {u: _row(db, i) for u, i in ids.items()}
    assert got[flash]["status"] == "failed" and got[flash]["fail_reason"] == "no_text: breaking news alert, headline only"
    assert got[video]["fail_reason"].startswith("no_text: video, ")
    assert got[transcript]["status"] == "fetched" and reference.load_body(got[transcript]).startswith(PROSE)
    # the page holds text the body lacks: still an extraction failure, never "no text"
    assert got[buggy]["fail_reason"] in ("extraction_empty", "extraction_empty_after_cleaning")
    # the ECNS photo item is read from the gallery it redirects to, robots.txt checked for both addresses
    assert got[photo]["status"] == "fetched" and gallery in net.requests and gallery in net.robots
    # every page read records its extraction version, failed or not, so no row is retried twice
    assert all(r["extract_version"] == config.EXTRACT_VERSION for r in got.values())


def test_rows_failed_by_the_old_extraction_are_retried_once_when_a_copy_is_paired_with_them(db, monkeypatch):
    transcript = "https://news.cgtn.com/news/2026-10-17/VHJhbnNjcmlwdDkyOTgw/index.html"
    net = Net({transcript: _cgtn_transcript_html([PROSE, PROSE.replace("lake", "river")])})
    net.install(monkeypatch)
    monkeypatch.setattr(extract, "extract", lambda html, url=None: {"text": CGTN_HEAD + CGTN_TAIL, "title": None,
                                                                    "author": None, "date": None})
    old = _item(db, "ref_cgtn_en", transcript, "A transcript", "failed", "unreadable: no article text: 0 characters of running prose")
    unpaired = _item(db, "ref_cgtn_en", "https://news.cgtn.com/news/2026-10-17/x-1/p.html", "Unpaired", "failed",
                     "extraction_empty_after_cleaning")
    honest = _item(db, "ref_cgtn_en", "https://news.cgtn.com/news/2026-10-17/y-1/p.html", "Alert", "failed",
                   "no_text: breaking news alert, headline only", config.EXTRACT_VERSION)
    gone = _item(db, "ref_cgtn_en", "https://news.cgtn.com/news/2026-10-17/z-1/p.html", "Gone", "failed", "http_404")
    for rid in (old, unpaired, honest, gone):
        db.execute("INSERT INTO state_copies(copy_id, version, computed_at, current, original_kind, ref_id) VALUES (?,?,?,?,?,?)",
                   (1000 + rid, copies.VERSION, "2026-11-09", 1 if rid != unpaired else 0, "reference", rid))
    db.commit()
    got = reference.fetch(db, sources=[_src("ref_cgtn_en")], now=NOW)
    assert got["paired_retries"] == 1 and net.requests == [transcript]
    assert _row(db, old)["status"] == "fetched"
    again = reference.fetch(db, sources=[_src("ref_cgtn_en")], now=NOW)
    assert again["paired_retries"] == 0 and net.requests == [transcript]
    assert _row(db, gone)["status"] == "failed" and _row(db, honest)["fail_reason"].startswith("no_text")


# ---------------------------------------------------------------------------
# The language editions
# ---------------------------------------------------------------------------

def test_the_language_editions_are_registered_as_originals_only():
    srcs = {s["id"]: s for s in reference.load_sources()}
    assert all(i in srcs for i in NEW)
    for i in NEW:
        s = srcs[i]
        assert s["language"] != "en" and "country" not in s and s["collector"] == "hosted" and s["active"]
        assert s["verified_on"] == "2026-11-09" and s["evidence"]
    langs = {(s["publisher"], s["language"]) for s in srcs.values()}
    for pair in [("xinhua", l) for l in ("fr", "es", "pt", "ar", "ru")] + [("cgtn", l) for l in ("es", "fr", "ar", "ru", "pt", "it")] \
            + [("peoples_daily", "fr"), ("peoples_daily", "es")]:
        assert pair in langs
    assert ("xinhua", "it") not in langs        # there is no Xinhua Italian


@pytest.mark.parametrize("sid,url,ok", [
    ("ref_xinhua_es", "https://spanish.news.cn/20261109/5bc3777805d2499f9df4c98ee29219a3/c.html", True),
    ("ref_xinhua_es", "https://english.news.cn/20261109/5bc3777805d2499f9df4c98ee29219a3/c.html", False),
    ("ref_xinhua_ru", "https://russian.news.cn/20261109/b0bf290305b94f7cb6f92ea5a6cdbf01/c.html", True),
    ("ref_cgtn_fr", "https://francais.cgtn.com/2026/11/09/ARTI1794226111478177", True),
    ("ref_cgtn_fr", "https://francais.cgtn.com/2026/11/09/VIDE1794235135375422", False),
    ("ref_peoples_daily_fr", "http://french.peopledaily.com.cn/Afrique/n3/2026/1109/c96852-20516811.html", True),
    ("ref_peoples_daily_es", "http://spanish.peopledaily.com.cn/n3/2026/1109/c31616-20517141.html", True),
    ("ref_peoples_daily_es", "http://spanish.people.com.cn/n3/2025/1031/c92122-20384571.html", False),
])
def test_link_patterns_keep_each_edition_on_its_own_pages(sid, url, ok):
    feed = reference._listings(_src(sid))[0]
    assert bool(re.search(feed["link_pattern"], url)) is ok


def test_times_of_the_language_editions():
    # CGTN's language sitemaps write Beijing time with a Z: 18:47Z is the page's 10:47:56 UTC (2026-11-09)
    feed = reference._listings(_src("ref_cgtn_es"))[0]
    items = reference.entries_to_items(_src("ref_cgtn_es"), feed, [
        {"link": "https://espanol.cgtn.com/2026/11/09/ARTI1794221271196158", "title": "Xi felicita a Iliana Iotova",
         "published": "2026-11-09T18:47Z"}], now=NOW)
    assert items[0]["published_at"] == "2026-11-09T10:47:00+00:00"
    assert reference.url_date("https://espanol.cgtn.com/2026/11/09/ARTI1794221271196158") == "2026-11-09"
    # Xinhua Russian's untimed list: the URL's Beijing date, then the page's stamp
    assert reference.page_time("<p>2026-11-09 21:07:45丨Russian.News.Cn</p>", "bjt_stamp") == "2026-11-09T13:07:45+00:00"
    assert all(reference.date_zone_minutes(_src(i)) == reference.BJT_MINUTES for i in NEW)


def test_the_xinhua_language_cleaner():
    raw = "Titre\n2026-11-09 21:07:45丨Russian.News.Cn\nBody line one.\nFin\n/نهاية الخبر/"
    assert reference.clean_body(raw, "xinhua_lang") == "Titre\nBody line one."


# ---------------------------------------------------------------------------
# copies.py: credit lines, datelines and end marks in the new languages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("Берлин, 9 ноября /Синьхуа/ -- "
     "Канцлер ФРГ выдвинул кандидатуру.",
     ["Канцлер ФРГ выдвинул кандидатуру."]),
    ("واشنطن 8 نوفمبر 2026 (شينخوا) "
     "قال الرئيس الأمريكي يوم الأحد.",
     ["قال الرئيس الأمريكي يوم الأحد."]),
    ("ESTAMBUL, 9 nov (Xinhua) -- Tres personas murieron en una fabrica.", ["Tres personas murieron en una fabrica."]),
    ("N'DJAMENA, 9 novembre (Le Quotidien du Peuple en ligne) - Le texte commence ici.", ["Le texte commence ici."]),
    ("Источник: Синьхуа", []),
    ("المصدر: شينخوا", []),
    ("(Фото: VCG)", []),
    ("Fin", []),
    ("Il a dit que la fin est proche. Fin", ["Il a dit que la fin est proche."]),
    # text that only names the agency, or brackets something else, is text
    ("Как сообщает агентство "
     "Синьхуа, переговоры продолжатся "
     "в среду и четверг в столице.", None),
    ("The minister (who spoke) - said the fin was large.", None),
    ("La fin de la saison approche.", None),
])
def test_normalisation_of_the_new_languages(line, expected):
    assert copies.normalise(line) == ([line] if expected is None else expected)


RU_PARAS = [
    "Канцлер ФРГ Фридрих Мерц в понедельник "
    "выдвинул кандидатуру Ильзе Айгнер "
    "на пост президента страны, что может "
    "привести к избранию первой женщины.",
    "Мерц, который также возглавляет партию "
    "Христианско-демократический союз, "
    "объявил об этом решении на пресс-конференции "
    "в Берлине в понедельник днем.",
]
RU_TITLE = ("Мерц выдвинул кандидатуру "
            "Ильзе Айгнер на пост президента ФРГ")


def test_a_russian_xinhua_copy_pairs_with_xinhua_russian_and_scores_verbatim(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "COLLECTION_START", "2000-01-01")
    conn = store.connect(tmp_path / "c.db")
    sources = copies.load_reference_sources()
    orig = "Берлин, 9 ноября /Синьхуа/ -- " + "\n".join(RU_PARAS)
    ref = tc._reference(conn, "https://russian.news.cn/20261109/b0bf290305b94f7cb6f92ea5a6cdbf01/c.html", "ref_xinhua_ru",
                        RU_TITLE, orig, hours_ago=5, lang="ru")
    copy_body = "\n".join(RU_PARAS) + "\nИсточник: Синьхуа"
    aid = tc._article(conn, "https://paper.example.kz/a", "kz_paper", RU_TITLE, copy_body, hours_ago=2, lang="ru",
                      country="KAZ", sigs=["xinhua_dateline"])
    tc._run(conn, sources)
    r = tc._row(conn, aid)
    assert r["original_kind"] == "reference" and r["ref_id"] == ref and r["original_source"] == "ref_xinhua_ru"
    assert r["band"] == "verbatim"
    # without the Russian edition the same copy is a translation, as it was before 2026-11-09
    conn2 = store.connect(tmp_path / "d.db")
    aid2 = tc._article(conn2, "https://paper.example.kz/a", "kz_paper", RU_TITLE, copy_body, hours_ago=2, lang="ru",
                       country="KAZ", sigs=["xinhua_dateline"])
    tc._run(conn2, {k: v for k, v in sources.items() if k != "ref_xinhua_ru"})
    assert tc._row(conn2, aid2)["none_reason"] == "translation"
