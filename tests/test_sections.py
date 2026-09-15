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
