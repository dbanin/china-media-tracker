"""The Africa reference sources of 2026-10-28 (sources/reference.yaml): CGTN Africa, China Daily's Africa
channels, Xinhua's English Africa channel and its French Afrique channel.

Decision D10: they are originals only, like the six before them, so nothing counted moves when their items
arrive and China stays a gap. Around that: the registry entries, the link_list listing that reads the CGTN and
China Daily channel pages, the bjt_url_checked time rule (a re-listed French item keeps its URL date), the
cleaners, credits for the Africa services naming their publishers, and pairing a copy with an Africa original
in its own language (a French Xinhua copy is no longer a translation).
"""
import datetime as dt
from pathlib import Path

from pipeline import copies, reference, registry, store
from tests import test_copies as tc
from tests import test_reference as tr

FIX = Path(__file__).parent / "fixtures" / "reference"
NOW = tr.NOW
AFRICA = {"ref_cgtn_africa_en": ("cgtn", "en"), "ref_china_daily_africa_en": ("china_daily", "en"),
          "ref_xinhua_africa_en": ("xinhua", "en"), "ref_xinhua_africa_fr": ("xinhua", "fr")}


def _src(sid):
    return {s["id"]: s for s in reference.load_sources()}[sid]


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

def test_the_africa_services_are_in_the_registry_as_originals_of_their_publishers():
    srcs = {s["id"]: s for s in reference.load_sources()}
    raw = copies.load_reference_sources()
    for sid, (pub, lang) in AFRICA.items():
        s = srcs[sid]
        assert s["publisher"] == pub and s["language"] == lang and "country" not in s
        assert s["active"] is True and s["collector"] == "hosted" and s["verified_on"] == "2026-10-28"
        assert s["evidence"] and s["credits"]
        # pairing reads the same publisher, from the file and from the id alone
        assert copies.source_publisher(sid, raw) == pub and copies.source_publisher(sid, {}) == pub
        assert pub in copies.carried_publishers(pub, s["credits"])
        for f in reference._listings(s):
            if f["type"] == "link_list":
                assert f.get("link_pattern") and f["time_rule"] == "url_date"
    assert reference.date_zones()["ref_xinhua_africa_fr"] == 480
    # ids are apart from the outlets, and none of them is a monitored outlet
    assert not set(AFRICA) & {o["id"] for o in registry.load_outlets()}


def test_cgtn_listings_leave_cgtn_africa_to_its_own_source():
    cgtn = _src("ref_cgtn_en")
    for f in reference._listings(cgtn):
        assert f.get("link_pattern") and not reference.re.search(f["link_pattern"], "https://newsaf.cgtn.com/news/2026-10-14/x-1/p.html")
        assert reference.re.search(f["link_pattern"], "https://news.cgtn.com/news/2026-10-14/x-1/p.html")
        assert reference.re.search(f["link_pattern"], "https://newsus.cgtn.com/news/2026-10-14/x-1/p.html")
    entries, _ = reference.sitemap_entries((FIX / "cgtn_latestnews.xml").read_bytes())
    feed = next(f for f in reference._listings(cgtn) if "sitemap" in f["url"])
    assert not [i for i in reference.entries_to_items(cgtn, feed, entries, now=NOW) if "newsaf." in i["url"]]


# ---------------------------------------------------------------------------
# link_list
# ---------------------------------------------------------------------------

def test_link_list_reads_the_cgtn_africa_page():
    src = _src("ref_cgtn_africa_en")
    feed = reference._listings(src)[0]
    html = (FIX / "cgtn_africa_page.html").read_text()
    entries = reference.link_entries(html, feed["url"], feed["link_pattern"])
    urls = [e["link"] for e in entries]
    # the image link and the headline link are one entry, titled from the headline; the UTM query is gone;
    # the photo-only link (no headline), CGTN's own host and the video still are left out
    assert urls == ["https://newsaf.cgtn.com/news/2026-10-14/Beijing-forum-focuses-on-smarter-people-centered-cities-1RdxyyECXmM/p.html",
                    "https://newsaf.cgtn.com/news/2026-10-13/Kenya-Tanzania-agree-to-fast-track-border-trade-1RcAfrTst01/p.html",
                    "https://newsaf.cgtn.com/news/2026-10-13/Africa-Watch-Can-solar-mini-grids-power-rural-Africa--1RcAfrTst02/p.html",
                    "https://newsaf.cgtn.com/news/2026-08-01/Old-feature-on-Kibera-1RcAfrTst04/p.html"]
    assert entries[0]["title"] == "Beijing forum focuses on smarter, people-centered cities"
    assert entries[2]["title"] == "Africa Watch Can solar mini-grids power rural Africa?"
    items = reference.entries_to_items(src, feed, entries, now=NOW)
    # the URL date, a Beijing date; the 2026-08-01 feature is past the age limit
    assert [i["published_at"] for i in items] == ["2026-10-14", "2026-10-13", "2026-10-13"]


def test_link_list_reads_the_china_daily_africa_channels():
    src = _src("ref_china_daily_africa_en")
    feeds = reference._listings(src)
    assert [f["url"] for f in feeds] == ["https://www.chinadaily.com.cn/world/africa", "https://www.chinadaily.com.cn/world/china-africa"]
    html = (FIX / "cd_africa_channel.html").read_text()
    entries = reference.link_entries(html, feeds[0]["url"], feeds[0]["link_pattern"])
    # protocol relative links resolve to https; the Global Edition copy and the e-paper are left out
    assert [e["link"] for e in entries] == ["https://www.chinadaily.com.cn/a/202610/13/WS6acdaf01e4b06d4aa0562901.html",
                                           "https://www.chinadaily.com.cn/a/202610/12/WS6acc0002e4b06d4aa0562902.html",
                                           "https://www.chinadaily.com.cn/a/202408/12/WS66b94d08a3104e74fddb9705.html"]
    items = reference.entries_to_items(src, feeds[0], entries, now=NOW)
    assert [i["published_at"] for i in items] == ["2026-10-13", "2026-10-12"]
    assert reference.page_time((FIX / "cd_page_time.html").read_text(), "jsonld") == "2026-10-13T15:22:59+00:00"


def test_link_list_goes_through_the_trackers_robots_check(monkeypatch):
    asked = []
    from pipeline import fetch_articles
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (asked.append(url) or False, "robots_disallowed"))
    monkeypatch.setattr(fetch_articles, "get_page", lambda url: (_ for _ in ()).throw(AssertionError("fetched")))
    src = _src("ref_cgtn_africa_en")
    got = reference.read_listing(src, reference._listings(src)[0])
    assert got["ok"] is False and got["error"] == "robots_disallowed" and asked == ["https://www.cgtn.com/2025/africa.html"]


# ---------------------------------------------------------------------------
# Times and cleaners
# ---------------------------------------------------------------------------

def test_a_relisted_xinhua_item_keeps_its_url_date_only():
    rule = "bjt_url_checked"
    url = "https://french.news.cn/%s/fdb81a67bc5941d3b4ce2e05bde22d0a/c.html"
    # the list time on the URL's Beijing date is Beijing time
    assert reference.normalise_time("2026-10-24 04:39:00", rule, None, url % "20261024") == "2026-10-23T20:39:00+00:00"
    # listed just before midnight with the next day's URL date: within a day, kept
    assert reference.normalise_time("2026-10-28 23:49:15", rule, None, url % "20261029") == "2026-10-28T15:49:15+00:00"
    # re-listed five days after the URL's date (the live list of 2026-10-28): the URL date only
    assert reference.normalise_time("2026-10-29 05:11:30", rule, None, url % "20261024") == "2026-10-24"
    # no date in the address: the list time as Beijing time
    assert reference.normalise_time("2026-10-29 05:11:30", rule, None, "https://x.test/a") == "2026-10-28T21:11:30+00:00"


def test_the_xinhua_african_lists_read_with_the_same_parser():
    html = (FIX / "xinhua_list.html").read_text()
    en = reference.list_entries(html, "https://english.news.cn/africa/latest/index.htm")
    fr = reference.list_entries(html, "https://french.news.cn/afrique/index.htm")
    assert en and all(e["link"].startswith("https://english.news.cn/africa/2026") for e in en)
    assert fr and all(e["link"].startswith("https://french.news.cn/2026") for e in fr)
    src = _src("ref_xinhua_africa_en")
    items = reference.entries_to_items(src, reference._listings(src)[0], en, now=NOW)
    assert items and all(reference._timed(i["published_at"]) for i in items)


def test_the_french_contact_box_and_the_cgtn_africa_label_are_stripped():
    body = ("ADDIS-ABEBA, 23 octobre (Xinhua) -- Des millions de telephones sont assembles chaque annee.\n"
            "Vous avez une question, une remarque, des suggestions ? Contactez\n"
            "notre équipe de rédaction par e-mail à xinhuanet_french@news.cn")
    assert reference.clean_body(body, "xinhua_fr") == body.split("\n")[0]
    cgtn = "CGTN\nAfrica;\nAfrican and Chinese officials have called for stronger cooperation.\nSource(s): Xinhua News Agency"
    assert reference.clean_body(cgtn, "cgtn") == ("CGTN\nAfrican and Chinese officials have called for stronger cooperation.\n"
                                                  "Source(s): Xinhua News Agency")
    # a body line that only ends in a semicolon after words is kept
    assert "Kenya, Uganda and Tanzania;" in reference.clean_body("Kenya, Uganda and Tanzania;\nmore", "cgtn")


# ---------------------------------------------------------------------------
# Credits and pairing
# ---------------------------------------------------------------------------

def test_credits_of_the_africa_services_name_their_publishers():
    assert copies.credits_of(china_sources_cited=["CGTN Africa"]) == {"cgtn"}
    assert copies.credits_of(china_sources_cited=["China Daily Africa"]) == {"china_daily"}
    assert copies.credits_of(china_sources_cited=["Xinhua Africa"]) == {"xinhua"}
    assert copies.credits_of(china_sources_cited=["Agence Xinhua"]) == {"xinhua"}
    assert copies.credits_of(title="CGTN Africa: Kenya hosts the summit") == {"cgtn"}
    assert copies.credits_of(title="China Daily Africa | Zero tariffs boost exports") == {"china_daily"}
    assert copies.credits_of(title="Africa: CGTN reports on the summit") == set()
    assert copies.credits_of(signatures_fired=["cgtn_credit"]) == {"cgtn"}       # "Source: CGTN Africa"


FR_PARAS = [
    "Le Fonds international de developpement agricole et le Benin ont signe mercredi a Cotonou un accord de financement de trente sept millions de dollars pour les jeunes ruraux.",
    "Ce projet de huit ans permettra aux jeunes ruraux d'acquerir des competences techniques, creera des emplois decents et favorisera des entreprises agricoles resilientes.",
    "Il beneficiera a environ cinquante mille menages ruraux dans plusieurs departements du pays, selon un communique de l'institution parvenu a la presse.",
    "Le projet combinera formation professionnelle et formation a l'entrepreneuriat, appui a la creation d'entreprises et acces au financement rural.",
]
FR_TITLE = "Le FIDA et le Benin signent un accord de financement pour les jeunes ruraux"


def test_a_french_xinhua_copy_pairs_with_the_afrique_original(conn):
    sources = copies.load_reference_sources()
    body = "COTONOU, 28 octobre (Xinhua) -- " + "\n".join(FR_PARAS)
    ref = tc._reference(conn, "https://french.news.cn/20261028/12cca875e9a644b591af1419663157a3/c.html", "ref_xinhua_africa_fr",
                        FR_TITLE, body, hours_ago=5, lang="fr")
    aid = tc._article(conn, "https://journal.example.bj/a", "bj_paper", FR_TITLE, body, hours_ago=2, lang="fr", country="BEN",
                      sigs=["xinhua_dateline"])
    tc._run(conn, sources)
    r = tc._row(conn, aid)
    assert r["original_kind"] == "reference" and r["ref_id"] == ref and r["original_source"] == "ref_xinhua_africa_fr"
    assert r["credit_consistent"] == 1 and r["band"] == "verbatim"


def test_without_the_afrique_source_the_french_copy_is_a_translation(conn):
    sources = {k: v for k, v in copies.load_reference_sources().items() if k != "ref_xinhua_africa_fr"}
    body = "COTONOU, 28 octobre (Xinhua) -- " + "\n".join(FR_PARAS)
    aid = tc._article(conn, "https://journal.example.bj/a", "bj_paper", FR_TITLE, body, hours_ago=2, lang="fr", country="BEN",
                      sigs=["xinhua_dateline"])
    tc._run(conn, sources)
    r = tc._row(conn, aid)
    assert r["original_kind"] == "none" and r["none_reason"] == "translation"


def test_cgtn_africa_and_china_daily_africa_originals_pair_with_their_copies(conn):
    sources = copies.load_reference_sources()
    cg = tc._reference(conn, "https://newsaf.cgtn.com/news/2026-10-28/China-EU-trade-1RTest/p.html", "ref_cgtn_africa_en",
                       tc.TITLE, "CGTN\n" + tc.ORIGINAL, hours_ago=4)
    a1 = tc._article(conn, "https://paper.example.ke/a", "ke_paper", tc.TITLE, tc.ORIGINAL + "\nSource: CGTN Africa", hours_ago=2,
                     country="KEN", sigs=["cgtn_credit"])
    cd_title = "Zero tariffs boost South Africa's exports to China in third quarter"
    cd = tc._reference(conn, "https://www.chinadaily.com.cn/a/202610/27/WS6ae000f2e4b06d4aa0564f2c.html", "ref_china_daily_africa_en",
                       cd_title, "\n".join(tc.PARAS[::-1]), hours_ago=6)
    a2 = tc._article(conn, "https://paper.example.za/b", "za_paper", cd_title, "\n".join(tc.PARAS[::-1]), hours_ago=2,
                     country="ZAF", sigs=["china_daily_credit"])
    tc._run(conn, sources)
    assert tc._row(conn, a1)["ref_id"] == cg and tc._row(conn, a1)["original_source"] == "ref_cgtn_africa_en"
    assert tc._row(conn, a2)["ref_id"] == cd and tc._row(conn, a2)["original_source"] == "ref_china_daily_africa_en"


conn = tc.conn


# ---------------------------------------------------------------------------
# Originals only: nothing counted moves
# ---------------------------------------------------------------------------

def test_africa_poll_and_fetch_leave_every_counted_table_alone(db, monkeypatch):
    net = tr.FakeNet()
    net.install(monkeypatch)
    tr._seed_counted(db)
    before = tr._counts(db)
    srcs = [_src(sid) for sid in AFRICA]
    got = reference.poll(db, sources=srcs, now=NOW)
    assert got["feeds_ok"] == got["feeds"] == sum(len(reference._listings(s)) for s in srcs)
    assert set(got["by_source"]) == set(AFRICA) and all(v["new"] for v in got["by_source"].values())
    fetched = reference.fetch(db, sources=srcs, now=NOW)
    assert fetched["fetched"] > 0
    assert tr._counts(db) == before
    rows = db.execute("SELECT source_id, language FROM reference_items").fetchall()
    assert {r["source_id"] for r in rows} == set(AFRICA)
    assert {r["language"] for r in rows if r["source_id"] == "ref_xinhua_africa_fr"} == {"fr"}
    # no outlet, country or classification was made for them
    assert not db.execute("SELECT COUNT(*) FROM articles WHERE outlet_id LIKE 'ref_%'").fetchone()[0]


db = tr.db


def test_the_export_invariance_covers_every_africa_source():
    assert {sid for sid, _, _ in tr.AFRICA_ROWS} == set(AFRICA)
    assert all(lang == AFRICA[sid][1] for sid, _, lang in tr.AFRICA_ROWS)


# ---------------------------------------------------------------------------
# Stress test of 2026-10-29: addresses, link_list edges, the CGTN Africa page extras
# ---------------------------------------------------------------------------

def test_clean_url_drops_tracking_and_valueless_parameters_only():
    # CGTN's tech-sci feed links ".../p.html?Sci=", the same page its other feeds and sitemap list plainly
    base = "https://news.cgtn.com/news/2026-10-26/Hot-Take-1Ryz9kHDft6/p.html"
    assert reference.clean_url(base + "?Sci=") == base
    assert reference.clean_url(base + "?Sci") == base
    assert reference.clean_url(base + "?UTM_Source=rss&utm_medium=x#top") == base
    assert reference.clean_url("https://x.test/a?id=5&Sci=&utm_campaign=y") == "https://x.test/a?id=5"
    assert store.url_hash(reference.clean_url(base + "?Sci=")) == store.url_hash(reference.clean_url(base))


AFRICA_PAGE = """<html><body>
<a href="https://newsaf.cgtn.com/news/2026-10-28/Kenya-tea-1RAbc01/p.html"><img src="x.jpg"></a>
<a href="https://newsaf.cgtn.com/news/2026-10-28/Kenya-tea-1RAbc01/p.html">Kenya's tea exports to China rise again</a>
<a href="//newsaf.cgtn.com/news/2026-10-27/Protocol-relative-1RAbc02/p.html">A protocol relative story link here</a>
<a href="https://newsaf.cgtn.com/news/2026-10-27/Query-1RAbc03/p.html?Sci=">A story listed with an empty parameter</a>
<a href="https://newsaf.cgtn.com/news/2026-10-27/Query-1RAbc03/p.html">A story listed with an empty parameter</a>
<a href="https://newsaf.cgtn.com/news/2026-10-26/Utm-1RAbc04/p.html?utm_source=home#comments">Tracking parameters and a fragment</a>
<a href="http://newsaf.cgtn.com/news/2026-10-26/Plain-http-1RAbc05/p.html">A plain http link is not in the pattern</a>
<a href="https://newsaf.cgtn.com.evil.test/news/2026-10-26/Lookalike-1RAbc06/p.html">A lookalike host must be refused</a>
<a href="https://newsaf.cgtn.com@evil.test/news/2026-10-26/Userinfo-1RAbc07/p.html">A userinfo trick must be refused</a>
<a href="https://evil.test/r?u=https://newsaf.cgtn.com/news/2026-10-26/Redirect-1RAbc08/p.html">A redirector must be refused</a>
<a href="https://newsaf.cgtn.com/news/2026-10-26/Deep/extra/p.html">A deeper path is not a story page</a>
<a href="https://newsaf.cgtn.com/news/2026-10-26/Gallery-1RAbc09/p.html?page=2">A story with a real parameter is left out</a>
<a href="https://www.cgtn.com/2025/africa/page_2.html">Next page of the channel, never followed</a>
<a href="javascript:void(0)">A script link that goes nowhere</a>
<a href="mailto:desk@cgtn.com">Write to the desk by e-mail</a>
<a href="data:text/html,hello">A data address with some text</a>
<a href="https://newsaf.cgtn.com/news/2026-10-25/RTL-1RAbc10/p.html">مصر والصين توقعان اتفاقا</a>
<a href="https://newsaf.cgtn.com/news/2026-10-25/Long-1RAbc11/p.html">%s</a>
<a href="https://newsaf.cgtn.com/news/2026-10-25/Short-1RAbc12/p.html">Video</a>
</body></html>""" % ("Long headline " * 80)


def test_link_list_keeps_to_its_pattern_and_never_follows_pages():
    src = _src("ref_cgtn_africa_en")
    feed = reference._listings(src)[0]
    entries = reference.link_entries(AFRICA_PAGE, feed["url"], feed["link_pattern"])
    urls = [e["link"] for e in entries]
    assert urls == ["https://newsaf.cgtn.com/news/2026-10-28/Kenya-tea-1RAbc01/p.html",
                    "https://newsaf.cgtn.com/news/2026-10-27/Protocol-relative-1RAbc02/p.html",
                    "https://newsaf.cgtn.com/news/2026-10-27/Query-1RAbc03/p.html",
                    "https://newsaf.cgtn.com/news/2026-10-26/Utm-1RAbc04/p.html",
                    "https://newsaf.cgtn.com/news/2026-10-25/RTL-1RAbc10/p.html",
                    "https://newsaf.cgtn.com/news/2026-10-25/Long-1RAbc11/p.html"]
    # every entry is on CGTN Africa's host; the two listings of one story (with and without "?Sci=") are one
    assert all(u.split("/")[2] == "newsaf.cgtn.com" for u in urls) and len(set(urls)) == len(urls)
    assert entries[0]["title"] == "Kenya's tea exports to China rise again"
    assert entries[4]["title"].startswith("مصر") and len(entries[5]["title"]) == 500
    # the stored items keep the URL's Beijing date, and nothing in them is a time
    items = reference.entries_to_items(src, feed, entries, now=dt.datetime(2026, 10, 29, tzinfo=dt.timezone.utc))
    assert [i["published_at"] for i in items] == ["2026-10-28", "2026-10-27", "2026-10-27", "2026-10-26", "2026-10-25", "2026-10-25"]


def test_link_list_survives_empty_and_broken_pages():
    pat = _src("ref_cgtn_africa_en")["feeds"][0]["link_pattern"]
    for html in ("", None, "<html>", "\x00\x01 not html at all", "<?xml version='1.0' encoding='utf-8'?><html><a href='x'>y</a></html>",
                 "<a href='https://newsaf.cgtn.com/news/2026-10-28/X-1/p.html'></a>"):
        assert reference.link_entries(html, "https://www.cgtn.com/2025/africa.html", pat) == []


def test_the_xinhua_african_lists_keep_to_their_own_host():
    html = ("<li><a href='../20261028/%s/c.html'>Gabon : un titre en francais pour le test</a><span class='time'>2026-10-28 10:00:00</span></li>"
            "<li><a href='https://www.news.cn/20261028/%s/c.html'>A Chinese edition story on another host</a><span class='time'>2026-10-28 09:00:00</span></li>"
            "<li><a href='https://english.news.cn/20261028/%s/c.html'>An English story linked from the French list</a><span class='time'>2026-10-28 08:00:00</span></li>"
            % ("a" * 32, "b" * 32, "c" * 32))
    fr = _src("ref_xinhua_africa_fr")
    feed = reference._listings(fr)[0]
    entries = reference.list_entries(html, feed["url"])
    assert len(entries) == 3       # the list parser alone takes any address of the Xinhua shape
    items = reference.entries_to_items(fr, feed, entries, now=dt.datetime(2026, 10, 29, tzinfo=dt.timezone.utc))
    assert [i["url"] for i in items] == ["https://french.news.cn/20261028/%s/c.html" % ("a" * 32)]
    en = _src("ref_xinhua_africa_en")
    for feed in reference._listings(en):
        entries = reference.list_entries(html.replace("../20261028", "../../africa/20261028"), feed["url"])
        got = [i["url"] for i in reference.entries_to_items(en, feed, entries, now=dt.datetime(2026, 10, 29, tzinfo=dt.timezone.utc))]
        assert got == ["https://english.news.cn/africa/20261028/%s/c.html" % ("a" * 32), "https://english.news.cn/20261028/%s/c.html" % ("c" * 32)]


def test_the_cgtn_cleaner_takes_the_africa_page_extras():
    caption = "Gulu village perched on mountain cliffs in Sichuan Province, China. /CGTN Africa"
    text = "\n".join(["CGTN", ", Updated 16:46, 27-Oct-2026", "For generations, rural communities have watched their young people leave.",
                      caption, caption, "The mountains surrounding Gulu have not changed.", "CHINA NEWS",
                      "Keep up to date with all the top trends, insights and developments from China."])
    assert reference.clean_body(text, "cgtn") == "\n".join([
        "CGTN", "For generations, rural communities have watched their young people leave.", caption,
        "The mountains surrounding Gulu have not changed."])
    # a heading in capitals that is not over the newsletter box stays, and so do lines that differ
    kept = "KENYA\nNairobi hosts the forum.\nNairobi hosts the forum, officials said."
    assert reference.clean_body(kept, "cgtn") == kept


# ---------------------------------------------------------------------------
# Stress test of 2026-10-29: realistic copies of the Africa originals
# ---------------------------------------------------------------------------

AF_TITLE = "Kenya and China agree to expand avocado and tea exports under zero tariff deal"
AF_PARAS = [
    "Kenya and China on Tuesday signed a protocol that opens the Chinese market wider to Kenyan avocados, tea and cut flowers under the zero tariff treatment announced earlier this year.",
    "The agreement was signed in Nairobi by agriculture officials of the two countries, who said the first shipments under the new rules were expected before the end of the year.",
    "Kenyan exporters have long called for easier access to Chinese buyers, saying that inspection rules and long shipping times had kept their share of the market small.",
    "Officials said a joint working group would meet every quarter to review inspection procedures, cold chain logistics and the certification of farms that supply the trade.",
    "Trade between the two countries reached a record last year, and both sides said the protocol would help narrow the gap between Kenyan exports and imports from China.",
]
AF_BODY = "\n".join(AF_PARAS)


def _sigs(title, body):
    from pipeline import classify_rules
    return [m["id"] for m in classify_rules.match_signatures(title, body, None)["matches"]]


def test_kenyan_copies_of_a_cgtn_africa_story_pair_by_credit_and_score_their_changes(conn):
    from pipeline import speed
    sources = copies.load_reference_sources()
    orig = "CGTN Africa\n" + AF_BODY + "\n(With input from wires)"
    ref = tc._reference(conn, "https://newsaf.cgtn.com/news/2026-10-28/Kenya-China-avocado-1RAfr001/p.html", "ref_cgtn_africa_en",
                        AF_TITLE, orig, hours_ago=8)
    verbatim = AF_BODY + "\nSource: CGTN Africa"
    altered = "\n".join(AF_PARAS[:3] + ["Farmers in Murang'a county told this newspaper that they hoped the deal would bring better prices this season."]
                        + AF_PARAS[3:]) + "\nCGTN Africa"
    uncredited = AF_BODY
    a1 = tc._article(conn, "https://paper.example.ke/1", "ke_paper", AF_TITLE, verbatim, hours_ago=5, country="KEN",
                     sigs=_sigs(AF_TITLE, verbatim))
    a2 = tc._article(conn, "https://other.example.ke/2", "ke_other", AF_TITLE, altered, hours_ago=4, country="KEN",
                     sigs=_sigs(AF_TITLE, altered))
    a3 = tc._article(conn, "https://third.example.ug/3", "ug_paper", AF_TITLE, uncredited, hours_ago=3, country="UGA",
                     sigs=[], method="llm", route="uncredited_copy")
    assert "cgtn_credit" in _sigs(AF_TITLE, verbatim) and "cgtn_bare_line_tail" in _sigs(AF_TITLE, altered)
    tc._run(conn, sources)
    r1, r2, r3 = tc._row(conn, a1), tc._row(conn, a2), tc._row(conn, a3)
    for r in (r1, r2):
        assert r["original_kind"] == "reference" and r["ref_id"] == ref and r["original_source"] == "ref_cgtn_africa_en"
        assert r["credit_consistent"] == 1 and r["body_state"] == "both"
    assert r1["band"] == "verbatim" and r1["added_paragraphs"] == 0
    assert r2["band"] in ("light", "moderate") and r2["novel_share"] > r1["novel_share"] and r2["added_paragraphs"] == 1
    # an uncredited copy is never given a credit by the reference it resembles
    assert r3["original_kind"] != "reference" and r3["ref_id"] is None
    # the speed measure reads the same pairs: the original's time, the copies hours after it
    pairs = {p["copy_id"]: p for p in speed.load_pairs(conn)}
    assert pairs[a1]["source"] == "ref_cgtn_africa_en" and pairs[a1]["resolution"] == "time"
    assert 2.5 * 3600 <= pairs[a1]["lag_seconds"] <= 3.5 * 3600 and pairs[a2]["lag_seconds"] > pairs[a1]["lag_seconds"]


def test_a_xinhua_credited_copy_of_a_cgtn_africa_rerun_pairs_with_xinhua_not_cgtn(conn):
    sources = copies.load_reference_sources()
    xbody = "NAIROBI, Oct. 28 (Xinhua) -- " + AF_BODY
    cg = tc._reference(conn, "https://newsaf.cgtn.com/news/2026-10-28/Kenya-China-avocado-1RAfr002/p.html", "ref_cgtn_africa_en",
                       AF_TITLE, "CGTN\n" + AF_BODY + "\nSource(s): Xinhua News Agency", hours_ago=6)
    copy = xbody
    aid = tc._article(conn, "https://paper.example.ke/x", "ke_paper", AF_TITLE, copy, hours_ago=2, country="KEN",
                      sigs=_sigs(AF_TITLE, copy))
    # with only CGTN Africa's rerun of the Xinhua story, the Xinhua copy is not put down to CGTN
    tc._run(conn, sources)
    r = tc._row(conn, aid)
    assert r["original_kind"] == "none" and r["ref_id"] is None and r["none_reason"] == "no_reference_match"
    xa = tc._reference(conn, "https://english.news.cn/africa/20261028/%s/c.html" % ("d" * 32), "ref_xinhua_africa_en",
                       AF_TITLE, xbody, hours_ago=7)
    conn.execute("DELETE FROM state_copies")
    tc._run(conn, sources)
    r = tc._row(conn, aid)
    assert r["original_kind"] == "reference" and r["ref_id"] == xa and r["original_source"] == "ref_xinhua_africa_en"
    assert cg != xa


def test_a_lightly_altered_french_copy_pairs_with_the_afrique_original_and_its_beijing_date(conn):
    from pipeline import speed
    sources = copies.load_reference_sources()
    body = "COTONOU, 28 octobre (Xinhua) -- " + "\n".join(FR_PARAS)
    url = "https://french.news.cn/20261028/%s/c.html" % ("e" * 32)
    ref = tc._reference(conn, url, "ref_xinhua_africa_fr", FR_TITLE, body, hours_ago=30, lang="fr")
    copy = ("\n".join(FR_PARAS[:2] + ["Au marche Dantokpa, plusieurs jeunes producteurs ont salue cette annonce lundi matin."] + FR_PARAS[2:])
            + "\nSource : Xinhua")
    sigs = _sigs(FR_TITLE, copy)
    assert sigs
    aid = tc._article(conn, "https://journal.example.sn/a", "sn_paper", FR_TITLE, copy, hours_ago=2, lang="fr", country="SEN", sigs=sigs)
    tc._run(conn, sources)
    r = tc._row(conn, aid)
    assert r["original_kind"] == "reference" and r["ref_id"] == ref and r["original_source"] == "ref_xinhua_africa_fr"
    assert r["band"] in ("light", "moderate") and r["added_paragraphs"] == 1
    # a re-listed original keeps its URL's Beijing date only (bjt_url_checked), days before it was listed
    # again and discovered: the speed measure then reads the pair by date, in Beijing time
    day = (tc.NOW + dt.timedelta(hours=8) - dt.timedelta(days=3)).date().isoformat()
    conn.execute("UPDATE reference_items SET published_at=? WHERE id=?", (day, ref))
    conn.execute("DELETE FROM state_copies")
    tc._run(conn, sources)
    p = {x["copy_id"]: x for x in speed.load_pairs(conn)}[aid]
    assert p["resolution"] == "date" and p["source"] == "ref_xinhua_africa_fr" and p["lag_seconds"] is None
    assert p["days"] in (2, 3) and p["bucket"] == "date_1_3"


# ---------------------------------------------------------------------------
# Stress test of 2026-10-29: the export invariance with pairs to every Africa source
# ---------------------------------------------------------------------------

def test_the_export_is_the_same_with_africa_pairs_feed_health_and_time_checks(tmp_path, monkeypatch):
    import json
    import shutil
    from pipeline import config, export
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    outlets = [{"id": "it_big", "name": "Big Italian Outlet", "country": "ITA", "language": "it", "feeds": ["https://x.test/f1"],
                "tier": "national", "active": True, "ownership": "private"},
               {"id": "it_wire", "name": "A Release Wire", "country": "ITA", "language": "it", "feeds": ["https://x.test/f2"],
                "tier": "distribution_wire", "active": True, "ownership": "state"},
               {"id": "fr_state", "name": "French State Outlet", "country": "FRA", "language": "fr", "feeds": ["https://x.test/f3"],
                "tier": "national", "active": True, "ownership": "state"}]
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: [dict(o) for o in outlets])
    base = tmp_path / "base.db"
    tr._fixture_db(base).close()
    with_ref = tmp_path / "with_ref.db"
    shutil.copy(base, with_ref)
    c = store.connect(with_ref)
    now = store.utcnow()
    ids = {}
    for i, (sid, url, lang) in enumerate(tr.AFRICA_ROWS):
        cur = c.execute("""INSERT INTO reference_items(source_id, url, url_hash, language, title, published_at, discovered_at,
                               fetched_at, status, body_hash, body_chars, sig, shingles, collector) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (sid, url, store.url_hash(url), lang, "Story %d about China and Xinhua" % i,
                         "2026-10-12" if sid.endswith("_fr") else "2026-10-12T01:00:00+00:00", now, now, "fetched", "b%d" % i, 900,
                         b"\x00" * 256, 80, "hosted"))
        ids[sid] = cur.lastrowid
    for s in reference.load_sources():
        if s["id"] in AFRICA:
            for f in reference._listings(s):
                store.record_reference_feed_health(c, s["id"], f["url"], True, None, 20)
            store.record_reference_feed_health(c, s["id"], reference._listings(s)[0]["url"], False, "http_503", 0)
    reference.record_time_checks(c, "ref_cgtn_africa_en", [(store.url_hash(tr.AFRICA_ROWS[0][1]), "a", "url_date", now, "b", "jsonld", now, 0)], now)
    # the three state origin copies of the fixture, each paired with an Africa original
    for aid, sid in ((1, "ref_cgtn_africa_en"), (3, "ref_xinhua_africa_fr"), (5, "ref_china_daily_africa_en")):
        c.execute("""INSERT INTO state_copies(copy_id, version, computed_at, original_kind, ref_id, original_source, band, novel_share,
                         copy_published_at, original_published_at, copy_ts_res, orig_ts_res, lag_seconds, body_state)
                     VALUES (?, ?, ?, 'reference', ?, ?, 'light', 0.1, ?, ?, 'minute', 'minute', 3600, 'both')""",
                  (aid, copies.VERSION, now, ids[sid], sid, now, now))
    c.commit()
    c.close()
    results, raw = {}, {}
    for name, path in (("base", base), ("ref", with_ref)):
        monkeypatch.setattr(config, "DB_PATH", path)
        out, audit = tmp_path / name / "data", tmp_path / name / "audit"
        export.run(store.connect(path), "test", export_dir=out, audit_dir=audit,
                   methodology_path=tmp_path / name / "M.md", prune=False)
        results[name] = tr._site_files(out, audit)
        raw[name] = out
    assert set(results["base"]) == set(results["ref"])
    for k in results["base"]:
        assert results["base"][k] == results["ref"][k], k
    assert results["ref"]["latest.json"]["countries"]["CHN"]["coverage"] == "gap"
    assert results["ref"]["articles/CHN.json"] == []
    # no Africa source id reaches a counted file, the outlets or the feed health
    for k, v in results["ref"].items():
        text = v if isinstance(v, str) else json.dumps(v)
        assert not any(sid in text for sid in AFRICA), k
    # and the Africa rows were there throughout, for the copies measures to read
    c = store.connect(with_ref)
    assert c.execute("SELECT COUNT(*) FROM reference_items WHERE source_id LIKE 'ref_%africa%'").fetchone()[0] == len(AFRICA)
    assert c.execute("SELECT COUNT(*) FROM reference_feed_health WHERE source_id IN (%s)" % ",".join("?" * len(AFRICA)),
                     list(AFRICA)).fetchone()[0] == 6
