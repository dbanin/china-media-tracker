"""Rejected sections and section feeds stay rejected: discovery never registers one again.

The registry (sources/outlets.yaml release_sections) is the one list of them; pipeline/section_retag.py
reads the same records to correct the items a removed feed brought in."""
import json

import pytest

from pipeline import config, registry, section_retag
from pipeline import discover_sections as ds

SEVEN = {
    "https://www.index.hr/sport": "hr_index",
    "https://www.mirror.co.uk/rss.xml": "gb_mirror",
    "https://news.liga.net/ua/all/rss.xml": "ua_liga",
    "https://novayagazeta.eu/feed/rss": "ru_novayagazeta_en",
    "https://asiaplus.news/feed/": "tj_asiaplus_en",
    "https://www.japantimes.co.jp/feed/topstories/": "jp_japantimes",
    "https://7news.com.au/lifestyle/sponsored/feed": "au_7news",
}


@pytest.fixture(scope="module")
def outlets():
    return registry.load_outlets()


def _by_id(outlets):
    return {o["id"]: o for o in outlets}


def _capture_apply(monkeypatch, tmp_path, outlets, probe):
    saved = {}
    monkeypatch.setattr(registry, "load_outlets", lambda: json.loads(json.dumps(outlets)))
    monkeypatch.setattr(registry, "save_outlets", lambda os_: saved.setdefault("o", os_))
    p = tmp_path / "probe.json"
    p.write_text(json.dumps(probe, ensure_ascii=False), encoding="utf-8")
    ds.apply(p)
    return _by_id(saved["o"])


def test_the_registry_is_the_one_list_of_removed_section_feeds(outlets):
    removed = section_retag.removed_section_feeds(outlets)
    assert {u: v[0] for u, v in removed.items()} == SEVEN
    assert removed["https://asiaplus.news/feed/"][2] == "ru"
    assert all(v[3] and v[4] == "2026-10-12" for v in removed.values())
    assert section_retag.REMOVED_SECTION_FEEDS == section_retag.removed_section_feeds()


def test_a_reprobe_that_finds_the_7news_your_feed_page_does_not_register_it(monkeypatch, tmp_path, outlets):
    """The homepage still links "Sponsored" to /lifestyle/sponsored, whose /feed the probe's fallback finds."""
    from pipeline import fetch_articles
    sevennews = _by_id(outlets)["au_7news"]
    pages = {"https://7news.com.au/": '<a href="/lifestyle/sponsored">Sponsored</a><a href="/brand-studio">Brand Studio</a>',
             "https://7news.com.au/brand-studio": "<p>brand studio</p>"}
    visited, looked = [], []
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda u: (True, None))
    monkeypatch.setattr(fetch_articles, "get_page", lambda u: visited.append(u) or (pages.get(u), 200 if u in pages else 404, None))
    monkeypatch.setattr(ds, "_feed_ok", lambda u: looked.append(u) or True)
    rec = ds.probe_outlet(sevennews, "https://7news.com.au/")
    secs = {s["url"]: s for s in rec["sections"]}
    assert secs["https://7news.com.au/lifestyle/sponsored"] == {
        "url": "https://7news.com.au/lifestyle/sponsored", "kind": "sponsored", "label": "Sponsored", "feed": None,
        "reachable": False, "rejected": True}
    assert "https://7news.com.au/lifestyle/sponsored" not in visited
    assert not any("lifestyle/sponsored" in u for u in looked)
    assert secs["https://7news.com.au/brand-studio"]["feed"] == "https://7news.com.au/brand-studio/feed"

    after = _capture_apply(monkeypatch, tmp_path, outlets, {"au_7news": rec})["au_7news"]
    assert after["section_feeds"] == [{"url": "https://7news.com.au/brand-studio/feed", "kind": "sponsored",
                                       "section": "https://7news.com.au/brand-studio", "type": "rss"}]
    kept = [s for s in after["release_sections"]["sections"] if s["url"] == "https://7news.com.au/lifestyle/sponsored"]
    assert len(kept) == 1 and kept[0]["rejected"] is True and kept[0]["readable"] is False
    assert kept[0]["rejected_feed"] == "https://7news.com.au/lifestyle/sponsored/feed" and kept[0]["reason"]


def test_a_reprobe_skips_a_rejected_feed_and_keeps_the_section_unregistered(monkeypatch, tmp_path, outlets):
    """A real section whose registered feed was rejected: the feed is skipped wherever it is declared, and the
    section is registered again only on another feed, never on its page in the rejected feed's place."""
    from pipeline import fetch_articles
    jt = _by_id(outlets)["jp_japantimes"]
    jt = dict(jt, release_sections=dict(jt["release_sections"], sections=jt["release_sections"]["sections"] + [
        {"url": "https://www.japantimes.co.jp/partner-content", "kind": "partner", "feed": None, "label": "Partner content",
         "readable": False, "rejected_feed": "https://www.japantimes.co.jp/partner-content/feed", "reason": "test"}]))
    pages = {"https://www.japantimes.co.jp/": '<a href="/partner-content">Partner content</a>',
             "https://www.japantimes.co.jp/partner-content":
                 '<link rel="alternate" type="application/rss+xml" href="https://www.japantimes.co.jp/feed/topstories/">'}
    looked = []
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda u: (True, None))
    monkeypatch.setattr(fetch_articles, "get_page", lambda u: (pages.get(u), 200 if u in pages else 404, None))
    monkeypatch.setattr(ds, "_feed_ok", lambda u: looked.append(u) or True)
    rec = ds.probe_outlet(jt, "https://www.japantimes.co.jp/")
    assert looked == []          # topstories is rejected, partner-content/feed too (the fallback's first try)
    assert rec["sections"] == [{"url": "https://www.japantimes.co.jp/partner-content", "kind": "partner",
                                "label": "Partner content", "feed": None, "reachable": True}]
    # An older probe file that still carries the rejected feed is filtered the same way.
    old = dict(rec, sections=[dict(rec["sections"][0], feed="https://www.japantimes.co.jp/partner-content/feed")])
    after = _capture_apply(monkeypatch, tmp_path, [jt], {"jp_japantimes": old})["jp_japantimes"]
    assert "section_feeds" not in after
    rs = {s["url"]: s for s in after["release_sections"]["sections"]}
    assert rs["https://www.japantimes.co.jp/partner-content"]["rejected_feed"] == "https://www.japantimes.co.jp/partner-content/feed"
    assert rs["https://www.japantimes.co.jp/partner-content"]["readable"] is False
    assert rs["https://www.japantimes.co.jp/comfacts/348"]["rejected"] is True    # kept though not found again


def test_apply_on_the_saved_probe_leaves_all_seven_out(monkeypatch, tmp_path, outlets):
    path = config.ROOT / "data" / "sections_probe.json"
    probe = json.loads(path.read_text(encoding="utf-8"))
    assert set(SEVEN.values()) <= set(probe)
    after = _capture_apply(monkeypatch, tmp_path, outlets, probe)
    seven = {ds._url_key(u) for u in SEVEN}
    for o in after.values():
        for f in o.get("section_feeds") or []:
            assert ds._url_key(f["url"]) not in seven, (o["id"], f["url"])
    for url, oid in SEVEN.items():
        o = after[oid]
        recs, never = ds.rejections(o)
        assert ds._url_key(url) in never, oid
        rejected_sections = {u for u, s in recs.items()}
        for f in o.get("section_feeds") or []:
            assert ds._url_key(f["section"]) not in rejected_sections, (oid, f)
        assert any(s.get("reason") for s in recs.values()), oid
    assert after["au_7news"]["release_sections"]["status"] == "none_found"
    assert after["hr_index"]["release_sections"]["status"] == "none_found"
