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
