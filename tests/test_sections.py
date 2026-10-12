"""Press release, sponsored and partner sections: the probe, the registry fields, and the page reader."""
from pipeline import discover_sections as ds
from pipeline import registry, section_pages


def test_classify_link_kinds():
    assert ds.classify_link("Press Releases", "https://x.com/news") == "press_release"
    assert ds.classify_link("", "https://x.com/globenewswire/") == "press_release"
    assert ds.classify_link("", "https://x.com/contenu-sponsorise/") == "sponsored"
    assert ds.classify_link("Brand Studio", "https://x.com/bs") == "sponsored"
    assert ds.classify_link("Partner content", "https://x.com/p") == "partner"
    assert ds.classify_link("World", "https://x.com/world") is None


def test_normalize_section_trims_articles_to_their_section():
    assert ds.normalize_section("https://www.channelnewsasia.com/advertorial/asias-airport-ecosystem-ready-take-6") == "https://www.channelnewsasia.com/advertorial"
    assert ds.normalize_section("https://www.thestar.com/sponsored-sections/") == "https://www.thestar.com/sponsored-sections"
    assert ds.normalize_section("https://x.com/news/2026/09/some-long-story-about-china-and-trade") is None


def test_section_links_same_site_only_press_release_first():
    html = ('<a href="https://other.com/sponsored">Sponsored</a><a href="/brand-studio">Brand Studio</a>'
            '<a href="/press-releases">Press Releases</a><a href="/world">World</a>')
    links = ds.section_links(html, "https://www.example.com/")
    assert [(l["kind"], l["url"]) for l in links] == [("press_release", "https://www.example.com/press-releases"),
                                                     ("sponsored", "https://www.example.com/brand-studio")]


def test_feed_links_reads_alternate():
    html = '<link rel="alternate" type="application/rss+xml" href="/pr/feed"><link rel="stylesheet" href="/s.css">'
    assert ds.feed_links(html, "https://www.example.com/pr") == ["https://www.example.com/pr/feed"]


def test_feed_entries_editorial_first_then_sections_without_duplicates():
    outlet = {"feeds": ["https://x.com/rss"], "section_feeds": [
        {"url": "https://x.com/rss", "kind": "press_release", "type": "rss"},
        {"url": "https://x.com/press-releases", "kind": "press_release", "type": "page"}]}
    assert registry.feed_entries(outlet) == [
        {"url": "https://x.com/rss", "kind": "editorial", "type": "rss"},
        {"url": "https://x.com/press-releases", "kind": "press_release", "type": "page"}]
    assert "press_release" in registry.GATE_EXEMPT_KINDS and "editorial" not in registry.GATE_EXEMPT_KINDS


def test_schema_accepts_section_fields():
    outlet = {"id": "sg_cna", "name": "CNA", "country": "SGP", "language": "en", "feeds": ["https://x.com/rss"],
              "tier": "broadcaster", "active": True,
              "section_feeds": [{"url": "https://www.channelnewsasia.com/media-releases", "kind": "press_release",
                                 "section": "https://www.channelnewsasia.com/media-releases", "type": "page"}],
              "release_sections": {"searched_on": "2026-09-15", "status": "found",
                                   "sections": [{"url": "https://www.channelnewsasia.com/media-releases", "kind": "press_release",
                                                 "feed": None, "label": "Media releases"}]}}
    registry.validate_outlets([outlet])


def test_article_links_from_section_page():
    html = ('<a href="/media-releases/company-signs-deal-with-provincial-government-123456">Company signs deal with provincial government</a>'
            '<a href="/media-releases">Media releases</a><a href="/tag/china">China</a>'
            '<a href="https://other.com/story-one-two-three">Offsite story with a long headline</a>'
            '<a href="/media-releases/short">Short</a>'
            '<a href="/opinion/letters-to-the-editor/">Letters to the editor and other opinion</a>'
            '<a href="/media-release/second-company-opens-regional-office-today">Second company opens regional office today</a>')
    entries = section_pages.article_links(html, "https://www.example.com/media-releases")
    assert [e["link"] for e in entries] == ["https://www.example.com/media-releases/company-signs-deal-with-provincial-government-123456",
                                            "https://www.example.com/media-release/second-company-opens-regional-office-today"]
    assert entries[0]["title"].startswith("Company signs deal")


def test_apply_writes_sections_and_page_feeds(tmp_path, monkeypatch):
    import json
    outlets = [{"id": "sg_cna", "name": "CNA", "country": "SGP", "language": "en", "feeds": ["https://x.com/rss"],
                "tier": "broadcaster", "active": True},
               {"id": "sg_other", "name": "Other", "country": "SGP", "language": "en", "feeds": ["https://y.com/rss"],
                "tier": "broadcaster", "active": True}]
    saved = {}
    monkeypatch.setattr(registry, "load_outlets", lambda: [dict(o) for o in outlets])
    monkeypatch.setattr(registry, "save_outlets", lambda os_: saved.setdefault("o", os_))
    probe = {"sg_cna": {"id": "sg_cna", "searched_on": "2026-09-15", "status": "found", "sections": [
        {"url": "https://x.com/media-releases", "kind": "press_release", "label": "Media releases", "feed": None, "reachable": True},
        {"url": "https://x.com/brand-studio", "kind": "sponsored", "label": "Brand Studio", "feed": "https://x.com/brand-studio/feed", "reachable": True},
        {"url": "https://x.com/dead", "kind": "partner", "label": "Dead", "feed": None, "reachable": False}]}}
    p = tmp_path / "probe.json"
    p.write_text(json.dumps(probe))
    ds.apply(p)
    cna = [o for o in saved["o"] if o["id"] == "sg_cna"][0]
    other = [o for o in saved["o"] if o["id"] == "sg_other"][0]
    assert cna["release_sections"]["status"] == "found" and len(cna["release_sections"]["sections"]) == 2
    assert cna["section_feeds"] == [
        {"url": "https://x.com/media-releases", "kind": "press_release", "section": "https://x.com/media-releases", "type": "page"},
        {"url": "https://x.com/brand-studio/feed", "kind": "sponsored", "section": "https://x.com/brand-studio", "type": "rss"}]
    assert "release_sections" not in other


def test_plausible_section_rejects_stories_and_ad_sales_pages():
    assert ds.plausible_section("https://www.la-croix.com/tag/contenus-sponsorises")
    assert ds.plausible_section("https://www.sudanspost.com/category/press-releases")
    assert not ds.plausible_section("https://initiativesnews.com/communique-de-presse-de-la-cea-le-president-sortant-du-bureau-")
    assert not ds.plausible_section("https://www.independent.co.uk/us/beam-biotic-supplements-b3011821.html")
    assert not ds.plausible_section("https://observador.pt/anunciar")
    assert not ds.plausible_section("https://magneticmediatv.com/2026/05/academy-eagles-fc-crowned")


def test_apply_refilters_old_probe_results(tmp_path, monkeypatch):
    import json
    outlets = [{"id": "mr_x", "name": "X", "country": "MRT", "language": "fr", "feeds": ["https://x.com/rss"], "tier": "national_daily", "active": True}]
    saved = {}
    monkeypatch.setattr(registry, "load_outlets", lambda: [dict(o) for o in outlets])
    monkeypatch.setattr(registry, "save_outlets", lambda os_: saved.setdefault("o", os_))
    probe = {"mr_x": {"id": "mr_x", "searched_on": "2026-09-15", "status": "found", "sections": [
        {"url": "https://x.com/communique-de-presse-du-mouvement-des-jeunes-patriotes-section", "kind": "press_release",
         "label": "", "feed": "https://x.com/communique-de-presse-du-mouvement-des-jeunes-patriotes-section/feed", "reachable": True}]}}
    p = tmp_path / "probe.json"
    p.write_text(json.dumps(probe))
    ds.apply(p)
    o = saved["o"][0]
    assert o["release_sections"]["status"] == "none_found" and o["release_sections"]["sections"] == []
    assert "section_feeds" not in o


def test_tag_listing_accepts_stories_elsewhere_on_site():
    html = ('<a href="/economie/entreprise-chinoise-signe-un-accord-avec-le-port">Une entreprise chinoise signe un accord avec le port</a>'
            '<a href="/abonnement">Abonnez-vous au journal maintenant</a>')
    entries = section_pages.article_links(html, "https://www.la-croix.com/tag/contenus-sponsorises")
    assert [e["link"] for e in entries] == ["https://www.la-croix.com/economie/entreprise-chinoise-signe-un-accord-avec-le-port"]


def test_check_section_feed_drops_comment_and_duplicate_feeds(monkeypatch):
    from pipeline import fetch_articles, feeds_util
    monkeypatch.setattr(fetch_articles, "_rate_wait", lambda d: None)
    feeds = {
        "https://x.com/press-releases/feed": [{"link": "https://x.com/pr/one", "title": "Company announces"}],
        "https://x.com/sponsored/feed": [{"link": "https://x.com/a", "title": "Story A"}, {"link": "https://x.com/b", "title": "Story B"}],
        "https://x.com/category/communique/feed": [{"link": "https://x.com/c", "title": "Comentario en Story C"},
                                                  {"link": "https://x.com/d", "title": "Comentario en Story D"}],
    }
    monkeypatch.setattr(feeds_util, "fetch_feed", lambda u, timeout=20: {"ok": True, "entries": feeds[u], "error": None})
    editorial = {"https://x.com/a", "https://x.com/b"}
    assert ds.check_section_feed({}, {"url": "https://x.com/press-releases/feed", "type": "rss"}, editorial)[0]
    assert ds.check_section_feed({}, {"url": "https://x.com/sponsored/feed", "type": "rss"}, editorial) == (False, "mostly the same items as the editorial feeds")
    assert ds.check_section_feed({}, {"url": "https://x.com/category/communique/feed", "type": "rss"}, editorial) == (False, "comments feed")
    assert ds.check_section_feed({}, {"url": "https://x.com/comments/feed", "type": "rss"}, editorial) == (False, "comments feed")


def test_saving_the_registry_never_drops_a_schema_field(tmp_path):
    """save_outlets writes a fixed key order. A field missing from it vanished from the whole registry the
    next time anything saved, and the weekly feed validation saves every Monday."""
    from pipeline import registry
    outs = registry.load_outlets()
    target = tmp_path / "outlets.yaml"
    registry.save_outlets(outs, target)
    again = registry.load_outlets(target)
    assert again == outs
    import pytest
    with pytest.raises(ValueError):
        registry.save_outlets([dict(outs[0], not_a_field=1)], tmp_path / "bad.yaml")


def test_desk_crediting_a_sponsor_is_not_a_sponsored_section():
    assert ds.classify_link("Sport sponsored by", "https://www.index.hr/sport") is None
    assert ds.classify_link("Weather presented by Acme", "https://x.com/weather") is None
    assert ds.classify_link("Podcasts powered by", "https://x.com/podcasts") is None
    html = '<a href="/sport">Sport sponsored by</a><a href="/sponsored">Sponsored</a>'
    assert [(l["kind"], l["url"]) for l in ds.section_links(html, "https://www.example.com/")] == [
        ("sponsored", "https://www.example.com/sponsored")]


def test_genuine_sections_still_classify():
    assert ds.classify_link("", "https://x.com/sponsored") == "sponsored"
    assert ds.classify_link("", "https://x.com/advertorial") == "sponsored"
    assert ds.classify_link("", "https://x.com/category/press-releases") == "press_release"
    assert ds.classify_link("", "https://x.com/globenewswire") == "press_release"
    assert ds.classify_link("Partner content", "https://x.com/p") == "partner"
    assert ds.classify_link("Sponsored", "https://x.com/s") == "sponsored"
    assert ds.classify_link("Sponsored content presented by Acme", "https://x.com/s") == "sponsored"
    assert ds.classify_link("Partner content sponsored by Acme", "https://x.com/p") == "partner"
    # A desk credit on a link whose path is itself a section still counts by the path.
    assert ds.classify_link("Sport sponsored by", "https://x.com/sponsored") == "sponsored"


def test_site_wide_feeds():
    for url in ("https://www.mirror.co.uk/rss.xml", "https://news.liga.net/ua/all/rss.xml", "https://novayagazeta.eu/feed/rss",
                "https://asiaplus.news/feed/", "https://www.interfax.ru/rss", "https://x.com/feed", "https://x.com/feeds/feed.xml",
                "https://x.com/feed/atom/", "https://www.uzdaily.uz/ru/rss/", "https://novayagazeta.eu/feed/rss/en",
                "https://x.com/?service=rss"):
        assert ds.site_wide_feed(url), url
    for url in ("https://nationalpost.com/category/sponsored/feed", "https://www.sudouest.fr/contenu-partenaire/rss.xml",
                "https://www.mirror.co.uk/news/world-news/?service=rss", "https://www.europapress.es/rss/rss.aspx?ch=137",
                "https://www.reviewjournal.com/pc/feed", "https://www.srherald.com/./advertorial/feed/",
                "https://www.japantimes.co.jp/feed/topstories/", "https://x.com/feed/?cat=12"):
        assert not ds.site_wide_feed(url), url


def test_probe_skips_a_site_wide_alternate_and_falls_back_to_the_page(monkeypatch):
    from pipeline import fetch_articles
    pages = {"https://www.example.com/": '<a href="/partner-stories">Partner Stories</a>',
             "https://www.example.com/partner-stories": '<link rel="alternate" type="application/rss+xml" href="/rss.xml">'}
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda u: (True, None))
    monkeypatch.setattr(fetch_articles, "get_page", lambda u: (pages.get(u), 200 if u in pages else 404, None))
    monkeypatch.setattr(ds, "_feed_ok", lambda u: True)
    rec = ds.probe_outlet({"id": "gb_x", "feeds": []}, "https://www.example.com/")
    sec = rec["sections"][0]
    assert sec["url"] == "https://www.example.com/partner-stories" and sec["kind"] == "partner"
    assert sec["feed"] is None and sec["reachable"]   # apply registers the section index page instead


def test_apply_drops_sponsor_credits_and_site_wide_feeds_from_old_probe_results(tmp_path, monkeypatch):
    import json
    outlets = [{"id": "hr_x", "name": "X", "country": "HRV", "language": "hr", "feeds": ["https://x.com/rss"], "tier": "national_daily", "active": True}]
    saved = {}
    monkeypatch.setattr(registry, "load_outlets", lambda: [dict(o) for o in outlets])
    monkeypatch.setattr(registry, "save_outlets", lambda os_: saved.setdefault("o", os_))
    probe = {"hr_x": {"id": "hr_x", "searched_on": "2026-09-15", "status": "found", "sections": [
        {"url": "https://x.com/sport", "kind": "sponsored", "label": "Sport sponsored by", "feed": None, "reachable": True},
        {"url": "https://x.com/partner-stories", "kind": "partner", "label": "Partner Stories", "feed": "https://x.com/rss.xml", "reachable": True},
        {"url": "https://x.com/advertorial", "kind": "sponsored", "label": "/advertorial", "feed": "https://x.com/advertorial/feed", "reachable": True}]}}
    p = tmp_path / "probe.json"
    p.write_text(json.dumps(probe))
    ds.apply(p)
    o = saved["o"][0]
    assert [s["url"] for s in o["release_sections"]["sections"]] == ["https://x.com/partner-stories", "https://x.com/advertorial"]
    assert o["section_feeds"] == [
        {"url": "https://x.com/partner-stories", "kind": "partner", "section": "https://x.com/partner-stories", "type": "page"},
        {"url": "https://x.com/advertorial/feed", "kind": "sponsored", "section": "https://x.com/advertorial", "type": "rss"}]


def test_registry_has_no_site_wide_or_sponsor_credit_section_feeds():
    for o in registry.load_outlets():
        labels = {s["url"]: s.get("label") or "" for s in (o.get("release_sections") or {}).get("sections", [])}
        for f in o.get("section_feeds") or []:
            assert not (f.get("type", "rss") == "rss" and ds.site_wide_feed(f["url"])), (o["id"], f["url"])
            assert ds.classify_link(labels.get(f["section"], ""), f["section"]), (o["id"], f["section"])
