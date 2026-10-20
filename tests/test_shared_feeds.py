"""A feed belongs to one active outlet (registry.validate_outlets, 2026-10-17)."""
import pytest

from pipeline import registry


def _o(oid, country, feeds, active=True, **kw):
    return dict({"id": oid, "name": oid, "country": country, "language": "es", "feeds": feeds,
                 "tier": "national_daily", "active": active}, **kw)


def test_two_active_outlets_on_one_feed_are_refused():
    """Infobae's site wide feed was polled for Argentina and Peru: Argentina got 1,963 items, Peru none."""
    a = _o("ar_x", "ARG", ["https://www.infobae.com/arc/outboundfeeds/rss/"])
    p = _o("pe_x", "PER", ["https://www.infobae.com/arc/outboundfeeds/rss/"])
    with pytest.raises(ValueError, match="more than one active outlet"):
        registry.validate_outlets([a, p])


def test_the_same_feed_spelled_differently_is_still_one_feed():
    a = _o("ar_x", "ARG", ["https://www.example.test/feed/"])
    p = _o("pe_x", "PER", ["http://example.test/feed"])
    with pytest.raises(ValueError):
        registry.validate_outlets([a, p])
    assert registry.feed_key("https://WWW.Example.test/feed/?a=1") == "example.test/feed?a=1"


def test_a_shared_news_sitemap_is_refused_but_section_feeds_may_be_shared():
    """Editorial feeds stay strict. A network's sponsored section or release page (France 24, RFI,
    RNZ) serves its editions by design, like the AAP widget, so section feeds are exempt."""
    sec = {"url": "https://www.rfi.fr/fr/contenus/rss", "kind": "sponsored", "type": "rss"}
    a = _o("fr_a", "FRA", ["https://a.test/rss"], section_feeds=[sec])
    b = _o("fr_b", "FRA", ["https://b.test/rss"], section_feeds=[dict(sec)])
    registry.validate_outlets([a, b])
    assert registry.shared_feed_urls([a, b]) == {}
    c = _o("fr_c", "FRA", ["https://c.test/rss"], news_sitemaps=["https://a.test/rss"])
    with pytest.raises(ValueError, match="a.test/rss"):
        registry.validate_outlets([a, c])


def test_an_inactive_duplicate_is_allowed():
    a = _o("ar_x", "ARG", ["https://x.test/rss"])
    p = _o("pe_x", "PER", ["https://x.test/rss"], active=False, inactive_reason="duplicate of ar_x")
    registry.validate_outlets([a, p])
    assert registry.shared_feed_urls([a, p]) == {}


def test_the_registry_shares_no_feed_between_active_outlets():
    outlets = registry.load_outlets()
    assert registry.shared_feed_urls(outlets) == {}
    by_id = {o["id"]: o for o in outlets}
    # the cases found on 2026-10-17
    assert by_id["pe_infobae"]["feeds"] != by_id["ar_infobae"]["feeds"]
    for dup in ("kp_dailynk", "kp_nkne", "de_nzz_de"):
        assert by_id[dup]["active"] is False and "duplicate of" in by_id[dup]["inactive_reason"]
    assert by_id["kr_dailynk"]["active"] and by_id["kr_nknews"]["active"] and by_id["ch_nzz"]["active"]
    # the shared sponsored feed of the RFI editions is intentional and kept
    rfi = "https://www.rfi.fr/fr/contenus-sponsoris%C3%A9s/rss"
    assert all(rfi in [f["url"] for f in by_id[i]["section_feeds"]] for i in ("fr_rfi", "fr_rfi_en", "br_rfi_pt"))


def test_reactivated_and_moved_outlets_carry_the_day_they_came_onto_their_collector():
    by_id = {o["id"]: o for o in registry.load_outlets()}
    for oid in ("th_thairath", "it_agi", "np_ratopati", "mw_times", "ro_stiripesurse", "om_alroya", "ke_nation"):
        o = by_id[oid]
        assert o["active"] and o["collector"] == "self_hosted" and o["collector_since"] >= "2026-10-17", oid
        assert "inactive_reason" not in o and "inactive_since" not in o, oid
    assert by_id["ca_torontostar"]["active"] is False and "robots.txt" in by_id["ca_torontostar"]["inactive_reason"]
