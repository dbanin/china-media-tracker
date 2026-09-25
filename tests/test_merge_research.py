"""The merge of the leaning and ownership research into the registry."""
import importlib.util
from pathlib import Path

import yaml

SPEC = importlib.util.spec_from_file_location(
    "merge_outlet_research", Path(__file__).resolve().parent.parent / "scripts" / "merge_outlet_research.py")
merge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merge)


def _outlets():
    return [
        {"id": "it_a", "country": "ITA", "political_leaning": "unassessed"},
        {"id": "it_b", "country": "ITA", "political_leaning": "centre_left", "leaning_source": "https://x/1"},
        {"id": "it_c", "country": "ITA"},
    ]


def _write(tmp_path, name, outlets):
    (tmp_path / name).write_text(yaml.safe_dump([{"country": "ITA", "outlets": outlets}]))


def test_fills_unassessed_and_keeps_disagreements(tmp_path):
    _write(tmp_path, "lean_A.yaml", [
        {"id": "it_a", "political_leaning": "right", "leaning_source": "https://y/1"},
        {"id": "it_b", "political_leaning": "centre", "leaning_source": "https://y/2"},
        {"id": "it_c", "political_leaning": "left"},
        {"id": "it_zz", "political_leaning": "left", "leaning_source": "https://y/3"},
    ])
    _write(tmp_path, "own_1.yaml", [
        {"id": "it_c", "ownership": "public_service", "ownership_source": "https://z/1"},
        {"id": "it_a", "ownership": "unassessed"},
    ])
    outlets = _outlets()
    report, issues = merge.apply_attributes(outlets, tmp_path)
    by_id = {o["id"]: o for o in outlets}
    assert by_id["it_a"]["political_leaning"] == "right"
    assert by_id["it_a"]["leaning_source"] == "https://y/1"
    assert by_id["it_b"]["political_leaning"] == "centre_left"   # kept, reported
    assert "political_leaning" not in by_id["it_c"]              # no source, skipped
    assert by_id["it_c"]["ownership"] == "public_service"
    assert by_id["it_a"]["ownership"] == "unassessed"
    assert report["political_leaning_filled"] == 1
    assert report["political_leaning_disagreements"] == 1
    reasons = " ".join(i[2] for i in issues)
    assert "disagreement" in reasons and "without a source" in reasons and "not in the registry" in reasons


def test_chinese_language_outlet_registered_inactive():
    outlets = [{"id": "tw_a", "country": "TWN", "language": "en", "feeds": ["https://a.tw/rss"], "tier": "national_daily", "active": True}]
    block = {"country": "TWN", "outlets": [{"id": "tw_new", "new": True, "name": "New", "language": "zh", "tier": "national_daily",
                                            "feeds": ["https://new.tw/rss"], "feed_check": {"ok": True, "entries": 20}}]}
    report, issues = merge.apply(outlets, [block], "2026-09-23")
    new = {o["id"]: o for o in outlets}["tw_new"]
    assert new["active"] is False and "Chinese" in new["inactive_reason"]
    assert report["registered_excluded_language"] == 1


def test_state_owned_outlets_get_state_controlled_leaning():
    outlets = [
        {"id": "id_a", "country": "IDN", "ownership": "state", "ownership_source": "https://s/1"},
        {"id": "id_b", "country": "IDN", "ownership": "state", "ownership_source": "https://s/2", "political_leaning": "centre", "leaning_source": "https://l/1"},
        {"id": "id_c", "country": "IDN", "ownership": "private", "ownership_source": "https://s/3"},
        {"id": "id_d", "country": "IDN", "ownership": "state"},
    ]
    assert merge.derive_state_controlled(outlets) == 1
    by_id = {o["id"]: o for o in outlets}
    assert by_id["id_a"]["political_leaning"] == "state_controlled" and by_id["id_a"]["leaning_source"] == "https://s/1"
    assert by_id["id_b"]["political_leaning"] == "centre"          # a sourced leaning is kept
    assert "political_leaning" not in by_id["id_c"]                # private: nothing derived
    assert "political_leaning" not in by_id["id_d"]                # state without a source: nothing derived


def test_flat_attribute_lists_are_read(tmp_path):
    (tmp_path / "lean_9.yaml").write_text(yaml.safe_dump([
        {"id": "it_a", "political_leaning": "centre", "leaning_source": "https://y/9"},
        {"id": "it_c", "political_leaning": "unassessed", "note": "checked"},
    ]))
    outlets = _outlets()
    report, issues = merge.apply_attributes(outlets, tmp_path)
    by_id = {o["id"]: o for o in outlets}
    assert by_id["it_a"]["political_leaning"] == "centre" and report["political_leaning_filled"] == 1
    assert by_id["it_c"]["political_leaning"] == "unassessed" and not issues
