"""Outlets are split between the hosted and the self-hosted collector."""
from pipeline import registry


def _o(i, **kw):
    d = {"id": i, "name": i, "country": "ITA", "language": "it", "feeds": [], "tier": "national", "active": True}
    d.update(kw)
    return d


def test_default_collector_is_hosted():
    assert registry.collector_of(_o("a")) == "hosted"


def test_collectable_splits_by_collector_and_activity():
    outlets = [_o("a"), _o("b", collector="self_hosted"), _o("c", active=False), _o("d", collector="hosted")]
    assert [o["id"] for o in registry.collectable(outlets, "hosted")] == ["a", "d"]
    assert [o["id"] for o in registry.collectable(outlets, "self_hosted")] == ["b"]


def test_is_mine_leaves_unknown_outlets_to_whoever_asks():
    outlets = [_o("a"), _o("b", collector="self_hosted")]
    assert registry.is_mine("a", outlets, "hosted") and not registry.is_mine("b", outlets, "hosted")
    assert registry.is_mine("b", outlets, "self_hosted") and not registry.is_mine("a", outlets, "self_hosted")
    assert registry.is_mine("zzz_unknown", outlets, "hosted") and registry.is_mine("zzz_unknown", outlets, "self_hosted")


def test_registry_keeps_the_retired_relay_outlets_inactive():
    """The relay was retired on 2026-10-22: its outlets stay registered, for their history, and inactive."""
    outlets = registry.load_outlets()
    sh = [o for o in outlets if registry.collector_of(o) == "self_hosted"]
    assert sh and not any(o["active"] for o in sh)
    assert not [o for o in registry.collectable(outlets, "self_hosted") if registry.collector_of(o) == "self_hosted"]
