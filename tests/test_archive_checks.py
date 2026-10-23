"""The licensed archive check (sources/archive_checks.yaml, pipeline/archive_checks.py): the owner's
ProQuest TDM Studio counts, validated to add up, exported to their own side file and shown apart from
every count of the tracker's own."""
import copy
import json
import re
from pathlib import Path

import pytest

from pipeline import archive_checks, config, export, store

ROOT = Path(__file__).resolve().parent.parent
CONTENT = json.loads((ROOT / "docs" / "content.json").read_text(encoding="utf-8"))
INDEX = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
YAML_TEXT = (ROOT / "sources" / "archive_checks.yaml").read_text(encoding="utf-8")

# The owner's table, verbatim: title -> (China relevant, awaiting model, no state signature, not about China).
OWNER_ROWS = {
    "The Globe and Mail": (113, 17, 96, 371),
    "National Post": (60, 4, 56, 200),
    "Toronto Sun": (51, 19, 32, 125),
    "Toronto Star": (45, 12, 33, 224),
    "The Business Times": (82, 4, 78, 210),
    "Calgary Herald": (17, 2, 15, 64),
    "El Cronista": (12, 1, 11, 23),
}
OWNER_TRIGGERS = [("state media reported (English)", 22), ("Chinese state body (English)", 17), ("cites Xinhua", 13),
                  ("embassy quoted", 12), ("official attribution", 9), ("foreign ministry", 4), ("CCTV", 3),
                  ("Global Times", 3), ("named spokesperson", 3), ("China Daily", 1)]


def _check():
    return archive_checks.load_file()["checks"][0]


def test_committed_file_validates():
    assert archive_checks.validate_file() == []


def test_committed_counts_are_the_owners():
    c = _check()
    assert c["source"] == "ProQuest TDM Studio (licensed full text, non-consumptive)"
    assert c["period"] == "September to October 2026" and c["scope"] == "editorial pages only"
    assert c["model_review"] is False and c["human_review"] is False
    assert {t["name"]: (t["china_relevant"], t["awaiting_model"], t["no_state_signature"], t["not_about_china"])
            for t in c["titles"]} == OWNER_ROWS
    assert c["totals"] == {"china_relevant": 380, "awaiting_model": 59, "no_state_signature": 321,
                           "not_about_china": 1217, "records": 1597}
    assert c["records_pulled"] == 1613 and c["records_deduplicated"] == 1597
    assert c["state_origin_signatures"] == 0
    assert [(t["label"], t["count"]) for t in c["triggers"]] == OWNER_TRIGGERS
    assert [(n["name"], n["records"]) for n in c["not_searchable"]] == [("Valor Econômico", 0)]
    assert "not searchable" in c["not_searchable"][0]["reason"]


def test_rows_add_up():
    c = _check()
    for t in c["titles"]:
        assert t["china_relevant"] == t["awaiting_model"] + t["no_state_signature"], t["name"]
    for k in archive_checks.COLUMNS:
        assert sum(t[k] for t in c["titles"]) == c["totals"][k], k
    assert sum(t["china_relevant"] + t["not_about_china"] for t in c["titles"]) == 1597


def test_country_sums_come_from_the_rows():
    s = archive_checks.country_sums(_check())
    assert set(s) == {"CAN", "SGP", "ARG"}
    assert s["CAN"]["china_relevant"] == 286 and s["CAN"]["awaiting_model"] == 54 and s["CAN"]["no_state_signature"] == 232
    assert len(s["CAN"]["titles"]) == 5
    assert s["SGP"]["china_relevant"] == 82 and s["ARG"]["china_relevant"] == 12


def test_trigger_patterns_are_signatures_ids():
    ids = archive_checks.trigger_ids()
    for tr in _check()["triggers"]:
        assert archive_checks._pattern_known(tr["pattern"], ids), tr


@pytest.mark.parametrize("breakage, expect", [
    (lambda c: c["titles"][0].update(awaiting_model=18), "is not awaiting model"),
    (lambda c: c["totals"].update(not_about_china=1216), "not_about_china rows add up"),
    (lambda c: c["totals"].update(records=1598), "records total"),
    (lambda c: c.update(records_deduplicated=1700, records_pulled=1613), "more records after removing duplicates"),
    (lambda c: c["triggers"].__setitem__(slice(None), [{"label": "x", "pattern": "cites_xinhua", "count": 5}]), "fewer than the 59"),
    (lambda c: c["triggers"][0].update(count=60), "more than the 59"),
    (lambda c: c["triggers"][0].update(pattern="cites_nobody"), "not an llm_triggers id"),
    (lambda c: c["titles"][0].update(outlet_id="ca_nosuchpaper"), "not in sources/outlets.yaml"),
])
def test_validator_catches_rows_that_do_not_add_up(breakage, expect):
    data = copy.deepcopy(archive_checks.load_file())
    breakage(data["checks"][0])
    errs = archive_checks.validate(data, archive_checks.trigger_ids(), archive_checks.outlet_ids())
    assert any(expect in e for e in errs), errs


def test_schema_refuses_article_level_fields():
    data = copy.deepcopy(archive_checks.load_file())
    data["checks"][0]["titles"][0]["headline"] = "anything"
    assert archive_checks.validate(data)


def test_no_article_text_headline_or_address_is_recorded():
    assert "http" not in YAML_TEXT and "www." not in YAML_TEXT
    exported = archive_checks.build_export()
    payload = json.dumps(exported, ensure_ascii=False)
    assert re.search(r"https?://|www\.", payload) is None

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    assert not ({"url", "headline", "title", "text", "body", "snippet", "quote"} & set(keys(exported)))


def test_invalid_file_is_refused_quietly(tmp_path):
    bad = tmp_path / "a.yaml"
    bad.write_text(YAML_TEXT.replace("china_relevant: 113", "china_relevant: 114"), encoding="utf-8")
    with pytest.raises(ValueError):
        archive_checks.build_export(bad)
    assert archive_checks.build_export_quietly(bad) == {"kept_apart": True, "checks": []}
    assert archive_checks.methodology_text(bad) == ""


def test_export_writes_the_side_file_and_nothing_else_reads_it(tmp_path, monkeypatch):
    db = tmp_path / "e.db"
    conn = store.connect(db)
    monkeypatch.setattr(config, "DB_PATH", db)
    out = tmp_path / "out"
    export.run(conn, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M.md")
    side = json.loads((out / "archive_checks.json").read_text(encoding="utf-8"))
    assert side["kept_apart"] is True and side["checks"][0]["totals"]["china_relevant"] == 380
    assert side["checks"][0]["by_country"]["CAN"]["china_relevant"] == 286
    # No figure of the check reaches the tracker's own files.
    for f in ("meta.json", "latest.json", "global_series.json", "outlets.json"):
        text = (out / f).read_text(encoding="utf-8").lower()
        leaked = [w for w in ("archive_check", "proquest", "tdm studio", "no_state_signature") if w in text]
        assert leaked == [], f
    md = (tmp_path / "M.md").read_text(encoding="utf-8")
    known = md[md.index("## Known limits"):]
    assert "Licensed archive check." in known and "no state signature found" in known
    assert "380 concern China" in known and "59 cite Chinese official" in known and "Valor Econômico returned no records" in known


def test_methodology_paragraph_wording():
    t = archive_checks.methodology_text()
    assert "rules only screen" in t and "No model and no person read" in t
    assert "no state signature found, which is not independent journalism" in t
    assert "not part of any total, map, share, top 10 or finding" in t


def test_site_text():
    ac = CONTENT["archive_check"]
    text = json.dumps(ac, ensure_ascii=False)
    assert ac["title"] == "Licensed archive check"
    assert ac["head_no_signature"] == "No state signature found"
    assert "rules only screen" in ac["intro"] and "no model and no person" in ac["intro"]
    assert "not part of the tracker's totals, map, shares, top 10 or findings" in ac["intro"]
    assert "not counted as independent journalism" in ac["no_signature_note"]
    assert "await review there" in ac["awaiting_note"]
    assert "{reason}" in ac["not_searchable"]
    assert "—" not in text and "–" not in text
    assert not re.search(r"Category [ABC]\b", text)
    assert not re.search(r"(?i)universit|stanford", text)
    assert not re.search(r"\b[Mm]ost\b", text)


def test_block_sits_beside_the_paywall_disclosure():
    i = INDEX.index('id="archive-check"')
    assert INDEX.rindex("<strong>Denominators.</strong>", 0, i) < i < INDEX.index("<strong>Placements and underlying items.</strong>")
    assert re.search(r'<div id="archive-check"[^>]*hidden', INDEX)


def test_app_reads_the_side_file_only_for_its_block_and_panel_line():
    assert 'quiet("data/archive_checks.json", null)' in APP
    assert '["renderArchiveCheck", renderArchiveCheck]' in APP
    assert "html += archiveCheckPanelLine(iso, name);" in APP
    # state.archiveChecks is read only by the block and the panel line, never by a count, map or finding.
    uses = [m.start() for m in re.finditer(r"state\.archiveChecks", APP)]
    start, end = APP.index("function archiveCheckText()"), APP.index("/* --------------------------------------------------------------- exports */")
    assert uses and all(start <= u < end for u in uses[1:]), "only the load and the archive check functions read it"


def test_country_line_names_five_canadian_titles():
    """The panel line's per country figure is summed from the rows: five Canadian titles, 286 China articles."""
    s = archive_checks.country_sums(_check())["CAN"]
    assert s["titles"] == ["The Globe and Mail", "National Post", "Toronto Sun", "Toronto Star", "Calgary Herald"]
    assert "{titles_phrase}" in CONTENT["archive_check"]["country_line"] and "{china}" in CONTENT["archive_check"]["country_line"]
