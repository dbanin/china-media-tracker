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
