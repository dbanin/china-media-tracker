"""Planted bylines detector: normalisation, the three detectors, storage, pruning, the recurring
bylines diagnostic and the export. Temporary databases and temporary YAML only."""
import json

import pytest
import yaml

from pipeline import config, export, planted, store

PERSONAS = {
    "version": "2026.10.1",
    "operations": [
        {"id": "op_bogus", "name": "Bogus Bylines", "origin": "IR", "reported_by": "Example Lab",
         "report_url": "https://example.test/report", "reported_on": "2026-10-08", "summary": "Seven invented bylines."},
        {"id": "op_front", "name": "Dark Front", "origin": "RU", "reported_by": "Example Lab",
         "report_url": "https://example.test/report2", "reported_on": "2026-10-08", "summary": "A false front think tank."},
    ],
    "personas": [
        {"id": "ervin_b_hoskins", "name": "Ervin B. Hoskins", "operation": "op_bogus", "kind": "journalist", "claimed": "freelance writer"},
        {"id": "sophia_gonzalez", "name": "Sophia González", "variants": ["Sofia Gonzalez"], "operation": "op_bogus", "kind": "journalist"},
        {"id": "mia_clark", "name": "Mia Clark", "operation": "op_front", "kind": "expert"},
        {"id": "lonely", "name": "Lonely", "operation": "op_bogus", "kind": "journalist"},
    ],
    "fronts": [
        {"id": "social_research_center", "name": "Social Research Center", "variants": ["Centro de Investigación Social"],
         "operation": "op_front", "kind": "think_tank", "domains": ["srcenter.example"]},
        {"id": "oneword", "name": "Monolith", "operation": "op_front", "kind": "outlet"},
    ],
}


@pytest.fixture
def plist(tmp_path, monkeypatch):
    """A temporary persona file, pointed at by config, plus a temporary bodies directory."""
    p = tmp_path / "personas.yaml"
    p.write_text(yaml.safe_dump(PERSONAS, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(config, "PERSONAS_PATH", p)
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    return p


def _db(tmp_path, name="t.db"):
    return store.connect(tmp_path / name)


def _article(conn, i, author=None, title="t", status="fetched", relevant=1, body=None, outlet="o", country="ITA", discovered=None):
    aid = store.insert_discovered(conn, {"url": "https://x.test/%s" % i, "outlet_id": outlet, "country": country, "language": "it",
                                         "title": title, "author": author, "status": status, "gate_relevant": relevant})
    if body is not None:
        row = store.get_article(conn, aid)
        store.update_article(conn, aid, body_hash=store.save_body(row["url_hash"], body), body_chars=len(body), fetched_at=store.utcnow())
    if discovered:
        conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (discovered, aid))
    conn.commit()
    return aid


def _flags(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM planted_flags ORDER BY id")]


# ---------------------------------------------------------------------------
# Normalisation and the list
# ---------------------------------------------------------------------------

def test_normalise_and_initial_less_variants():
    assert planted.normalise("  Ervin  B. HOSKINS ") == "ervin b hoskins"
    assert planted.normalise("Sophia González") == "sophia gonzalez"
    assert planted.normalise("Jean-Pierre O'Neil") == "jean pierre o neil"
    assert planted.name_variants("Ervin B. Hoskins") == ["ervin b hoskins", "ervin hoskins"]
    assert planted.name_variants("Sophia González", ["Sofia Gonzalez"]) == ["sophia gonzalez", "sofia gonzalez"]
    # a single word is never a variant, and a middle initial is only dropped when two tokens remain
    assert planted.name_variants("Lonely") == []
    assert planted.name_variants("J. Rowling") == ["j rowling"]
    assert "" not in planted.name_variants(None)


def test_missing_or_broken_list_loads_empty(tmp_path):
    empty = planted.load_personas(tmp_path / "absent.yaml")
    assert empty == {"version": "", "operations": [], "personas": [], "fronts": []}
    broken = tmp_path / "broken.yaml"
    broken.write_text("just a string", encoding="utf-8")
    assert planted.load_personas(broken)["personas"] == []


def test_load_personas_compiles_variants(plist):
    lst = planted.load_personas()
    assert lst["version"] == "2026.10.1"
    by_id = {p["id"]: p for p in lst["personas"]}
    assert by_id["ervin_b_hoskins"]["variants_n"] == ["ervin b hoskins", "ervin hoskins"]
    assert by_id["lonely"]["variants_n"] == []
    fronts = {f["id"]: f for f in lst["fronts"]}
    assert "centro de investigacion social" in fronts["social_research_center"]["variants_n"]
    assert fronts["social_research_center"]["domains_n"] == ["srcenter.example"]
    assert fronts["oneword"]["variants_n"] == []


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def test_match_author_equality_and_single_word_never_matches(plist):
    personas = planted.load_personas()["personas"]
    assert [p["id"] for p, _ in planted.match_author("Ervin Hoskins", personas)] == ["ervin_b_hoskins"]
    assert [p["id"] for p, _ in planted.match_author("By Ervin B. Hoskins", personas)] == ["ervin_b_hoskins"]
    assert [p["id"] for p, _ in planted.match_author("Sofía González", personas)] == ["sophia_gonzalez"]
    assert [p["id"] for p, _ in planted.match_author("Mia Clark and Ervin Hoskins", personas)] == ["ervin_b_hoskins", "mia_clark"]
    # not equal: a longer field, a different person sharing a surname, a single word
    assert planted.match_author("Ervin Hoskins Jr Special Correspondent in Beijing", personas) == []
    assert planted.match_author("Hoskins", personas) == []
    assert planted.match_author("Lonely", personas) == []
    assert planted.match_author(None, personas) == []


def test_match_head_needs_byline_word_in_first_400_chars(plist):
    personas = planted.load_personas()["personas"]
    body = "Opinion\n\nBy Ervin B. Hoskins\n\nThe port deal was announced on Monday."
    hits = planted.match_head(body, personas)
    assert [p["id"] for p, _ in hits] == ["ervin_b_hoskins"] and "by ervin b hoskins" in hits[0][1]
    assert planted.match_head("Ervin Hoskins said the deal was good.", personas) == []
    late = "x " * 250 + "By Ervin Hoskins"
    assert planted.match_head(late, personas) == []
    assert [p["id"] for p, _ in planted.match_head("Por Sofia Gonzalez. Lima.", personas)] == ["sophia_gonzalez"]


def test_match_fronts_name_variant_and_domain_but_never_one_word(plist):
    fronts = planted.load_personas()["fronts"]
    assert [f["id"] for f, _ in planted.match_fronts("t", "A report by the Social Research Center found.", fronts)] == ["social_research_center"]
    assert [f["id"] for f, _ in planted.match_fronts("Según el Centro de Investigación Social", "", fronts)] == ["social_research_center"]
    assert [f["id"] for f, _ in planted.match_fronts("t", "See https://www.srcenter.example/report for more.", fronts)] == ["social_research_center"]
    assert planted.match_fronts("t", "The Monolith said nothing. Monolith Monolith.", fronts) == []
    assert planted.match_fronts("t", "A social research centre in Oslo.", fronts) == []


# ---------------------------------------------------------------------------
# Scan and storage
# ---------------------------------------------------------------------------

def test_scan_flags_author_field_on_gated_out_items(plist, tmp_path):
    conn = _db(tmp_path)
    gated = _article(conn, 1, author="Ervin Hoskins", status="gated_out", relevant=0)
    _article(conn, 2, author="Someone Else", status="gated_out", relevant=0)
    counts = planted.scan(conn)
    assert counts["flagged"] == 1 and counts["full"] is True
    flags = _flags(conn)
    assert len(flags) == 1
    assert flags[0]["article_id"] == gated and flags[0]["detector"] == "author_field"
    assert flags[0]["persona_id"] == "ervin_b_hoskins" and flags[0]["operation_id"] == "op_bogus"
    assert flags[0]["list_version"] == "2026.10.1" and flags[0]["front_id"] == ""


def test_scan_byline_head_and_front_cited_on_bodies(plist, tmp_path):
    conn = _db(tmp_path)
    a = _article(conn, 1, body="By Ervin B. Hoskins\n\nA long piece citing the Social Research Center at length.")
    b = _article(conn, 2, title="A report from the Social Research Center", body="Nothing in the body.")
    c = _article(conn, 3, body="By Nobody Known\n\nNo front here.")
    counts = planted.scan(conn)
    assert counts["bodies"] == 3 and counts["flagged"] == 3
    got = {(f["article_id"], f["detector"], f["persona_id"], f["front_id"]) for f in _flags(conn)}
    assert got == {(a, "byline_head", "ervin_b_hoskins", ""), (a, "front_cited", "", "social_research_center"),
                   (b, "front_cited", "", "social_research_center")}
    assert c not in {f["article_id"] for f in _flags(conn)}


def test_unique_prevents_duplicate_flags_and_rescan_adds_nothing(plist, tmp_path):
    conn = _db(tmp_path)
    _article(conn, 1, author="Ervin Hoskins", body="By Ervin Hoskins\n\ntext")
    planted.scan(conn)
    assert len(_flags(conn)) == 2
    assert planted.scan(conn, full=True)["flagged"] == 0
    assert len(_flags(conn)) == 2
    # the constraint itself, independent of the scan
    with pytest.raises(Exception):
        conn.execute("INSERT INTO planted_flags(article_id, detector, persona_id, front_id, operation_id, flagged_at, list_version) VALUES (1,'author_field','ervin_b_hoskins','','op_bogus','x','v')")


def test_scan_is_incremental_and_picks_up_bodies_fetched_later(plist, tmp_path):
    conn = _db(tmp_path)
    first = _article(conn, 1, author="Nobody")
    planted.scan(conn)
    state = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM planted_state")}
    assert state["last_scanned_id"] == str(first) and state["list_version"] == "2026.10.1"
    # a body arriving for an already scanned row still gets the body detectors
    row = store.get_article(conn, first)
    store.update_article(conn, first, body_hash=store.save_body(row["url_hash"], "By Mia Clark\n\ntext"), fetched_at="2999-01-01T00:00:00+00:00")
    conn.commit()
    counts = planted.scan(conn)
    assert counts["full"] is False and counts["flagged"] == 1
    assert _flags(conn)[0]["detector"] == "byline_head"
    # and a new row is scanned without touching old ones
    _article(conn, 2, author="Mia Clark")
    counts = planted.scan(conn)
    assert counts["scanned"] == 1 and counts["flagged"] == 1


def test_list_version_change_triggers_full_rescan(plist, tmp_path):
    conn = _db(tmp_path)
    _article(conn, 1, author="New Person")
    counts = planted.scan(conn)
    assert counts["full"] is True and counts["flagged"] == 0
    assert planted.scan(conn)["full"] is False
    data = dict(PERSONAS, version="2026.10.2", personas=PERSONAS["personas"] + [
        {"id": "new_person", "name": "New Person", "operation": "op_bogus", "kind": "journalist"}])
    plist.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    counts = planted.scan(conn)
    assert counts["full"] is True and counts["flagged"] == 1
    assert _flags(conn)[0]["list_version"] == "2026.10.2"


def test_scan_quietly_never_raises(plist, tmp_path, monkeypatch):
    conn = _db(tmp_path)
    monkeypatch.setattr(planted, "load_personas", lambda path=None: (_ for _ in ()).throw(RuntimeError("boom")))
    out = planted.scan_quietly(conn)
    assert out["error"].startswith("RuntimeError")


def test_prune_gated_out_keeps_flagged_rows(plist, tmp_path):
    conn = _db(tmp_path)
    old = "2020-01-01T00:00:00+00:00"
    kept = _article(conn, 1, author="Ervin Hoskins", status="gated_out", relevant=0, discovered=old)
    gone = _article(conn, 2, author="Someone Else", status="gated_out", relevant=0, discovered=old)
    planted.scan(conn)
    assert store.prune_gated_out(conn, days=3) == 1
    ids = {r[0] for r in conn.execute("SELECT id FROM articles")}
    assert kept in ids and gone not in ids


def test_migrate_adds_tables_to_an_old_database(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    raw = sqlite3.connect(str(db))
    start = store.SCHEMA.index("CREATE TABLE IF NOT EXISTS planted_flags")
    raw.executescript(store.SCHEMA[:start])
    raw.close()
    conn = store.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"planted_flags", "planted_state"} <= tables


# ---------------------------------------------------------------------------
# Recurring bylines
# ---------------------------------------------------------------------------

def _seed_recurring(conn):
    store.sync_outlets(conn, [{"id": "de_x", "name": "Deutsche Zeitung", "country": "DEU", "language": "de", "tier": "national_daily", "active": True, "feeds": []},
                              {"id": "wire_pr", "name": "Global Newswire", "country": "USA", "language": "en", "tier": "distribution_wire", "active": True, "feeds": []}])
    spread = [("o1", "ITA"), ("o2", "FRA"), ("o3", "DEU")]
    n = 0
    for name in ("Real Person", "Staff Reporter", "Deutsche Zeitung", "Global Newswire", "Copy Only", "Mixed Copy", "Lonely"):
        for outlet, country in spread:
            n += 1
            aid = _article(conn, n, author=name, title="%s %d" % (name, n), outlet=outlet, country=country)
            if name == "Copy Only":
                store.insert_classification(conn, aid, "rules", "A", 1.0, signatures_fired=["xinhua_dateline"], route="wire_credit")
            if name == "Mixed Copy" and n % 3 == 0:
                store.insert_classification(conn, aid, "rules", "A", 1.0, signatures_fired=["xinhua_dateline"], route="wire_credit")
    # two outlets only, one country only, and a gated out item do not count
    for i in range(3):
        n += 1
        _article(conn, n, author="Two Outlets", outlet="o%d" % (i % 2 + 1), country="ITA")
    n += 1
    _article(conn, n, author="Real Person", outlet="o9", country="ESP", status="gated_out", relevant=0)
    conn.commit()


def test_recurring_bylines_exclusions_and_order(tmp_path):
    conn = _db(tmp_path)
    _seed_recurring(conn)
    rows = planted.recurring_bylines(conn, days=30)
    names = [r["name"] for r in rows]
    assert names == ["Mixed Copy", "Real Person"]
    real = rows[1]
    assert real["outlets"] == ["o1", "o2", "o3"] and real["countries"] == ["DEU", "FRA", "ITA"] and real["n"] == 3
    assert len(real["titles"]) == 3 and real["wire_credit"] is False
    assert rows[0]["wire_credit"] is True
    # window: nothing old
    conn.execute("UPDATE articles SET discovered_at='2020-01-01T00:00:00+00:00'")
    conn.commit()
    assert planted.recurring_bylines(conn, days=30) == []


def test_recurring_bylines_wire_copy_read_from_signatures_when_route_missing(tmp_path):
    conn = _db(tmp_path)
    for i, (outlet, country) in enumerate([("o1", "ITA"), ("o2", "FRA"), ("o3", "DEU")]):
        aid = _article(conn, i, author="Copy Person", outlet=outlet, country=country)
        store.insert_classification(conn, aid, "rules", "A", 1.0, signatures_fired=["xinhua_dateline"])
    conn.commit()
    assert planted.recurring_bylines(conn, days=30) == []


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_build_export_shape_and_ordering(plist, tmp_path):
    conn = _db(tmp_path)
    store.sync_outlets(conn, [{"id": "o", "name": "Outlet O", "country": "ITA", "language": "it", "tier": "national_daily", "active": True, "feeds": []}])
    older = _article(conn, 1, author="Ervin Hoskins", discovered="2026-09-01T00:00:00+00:00")
    newer = _article(conn, 2, author="Mia Clark", country="USA", discovered="2026-09-05T00:00:00+00:00", outlet="us_x")
    planted.scan(conn)
    data = planted.build_export(conn)
    assert set(data) == {"generated", "list_version", "operations", "personas", "fronts", "flags", "flags_total",
                         "by_country", "by_outlet", "recurring_bylines"}
    assert data["list_version"] == "2026.10.1"
    assert [f["article_id"] for f in data["flags"]] == [newer, older]
    f = data["flags"][1]
    assert set(f) == {"article_id", "title", "url", "outlet_id", "outlet", "country", "published_at", "discovered_at",
                      "detector", "persona_id", "front_id", "operation_id", "evidence"}
    assert f["outlet"] == "Outlet O" and f["front_id"] is None and f["persona_id"] == "ervin_b_hoskins"
    assert data["by_country"] == {"ITA": 1, "USA": 1} and data["by_outlet"] == {"o": 1, "us_x": 1}
    ops = {o["id"]: o for o in data["operations"]}
    assert set(ops["op_bogus"]) == {"id", "name", "origin", "reported_by", "report_url", "reported_on", "summary", "flags"}
    assert ops["op_bogus"]["flags"] == 1 and ops["op_front"]["flags"] == 1
    personas = {p["id"]: p for p in data["personas"]}
    assert set(personas["ervin_b_hoskins"]) == {"id", "name", "operation", "kind", "claimed", "flags"}
    assert personas["ervin_b_hoskins"]["flags"] == 1 and personas["lonely"]["flags"] == 0
    fronts = {x["id"]: x for x in data["fronts"]}
    assert set(fronts["oneword"]) == {"id", "name", "operation", "kind", "flags"}
    assert data["recurring_bylines"] == {"window_days": config.PLANTED_RECURRING_DAYS, "rows": []}
    assert planted.meta_block(conn) == {"flags_total": 2, "flags_30d": 0, "list_version": "2026.10.1",
                                        "personas": 4, "fronts": 2, "operations": 2}


def test_build_export_caps_flags_and_drops_delisted_entries(plist, tmp_path, monkeypatch):
    conn = _db(tmp_path)
    monkeypatch.setattr(planted, "FLAGS_EXPORT_CAP", 2)
    for i in range(3):
        _article(conn, i, author="Ervin Hoskins")
    _article(conn, 9, author="Mia Clark")
    planted.scan(conn)
    data = planted.build_export(conn)
    assert len(data["flags"]) == 2 and data["flags_total"] == 4
    # an entry taken off the list keeps its rows in the database and leaves the export
    data = dict(PERSONAS, version="2026.10.2", personas=[p for p in PERSONAS["personas"] if p["id"] != "mia_clark"])
    plist.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    out = planted.build_export(conn)
    assert out["flags_total"] == 3 and conn.execute("SELECT COUNT(*) FROM planted_flags").fetchone()[0] == 4


def test_export_run_writes_planted_json_and_meta(plist, tmp_path, monkeypatch):
    db = tmp_path / "e.db"
    conn = store.connect(db)
    monkeypatch.setattr(config, "DB_PATH", db)
    _article(conn, 1, author="Ervin Hoskins", status="gated_out", relevant=0)
    out = tmp_path / "out"
    counts = export.run(conn, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M.md")
    assert counts["planted"]["flagged"] == 1
    data = json.loads((out / "planted.json").read_text(encoding="utf-8"))
    assert data["list_version"] == "2026.10.1" and len(data["flags"]) == 1
    assert data["flags"][0]["detector"] == "author_field"
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["planted"] == {"flags_total": 1, "flags_30d": 1, "list_version": "2026.10.1", "personas": 4, "fronts": 2, "operations": 2}
    assert meta["schema_version"] == config.SCHEMA_VERSION
    # no category count moved: the flagged item is gated out and stays uncounted
    assert meta["articles_classified"] == 0


def test_export_without_a_persona_file_degrades_to_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PERSONAS_PATH", tmp_path / "absent.yaml")
    db = tmp_path / "n.db"
    conn = store.connect(db)
    monkeypatch.setattr(config, "DB_PATH", db)
    out = tmp_path / "out"
    export.run(conn, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M.md")
    data = json.loads((out / "planted.json").read_text(encoding="utf-8"))
    assert data["flags"] == [] and data["personas"] == [] and data["list_version"] == ""
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["planted"]["flags_total"] == 0 and meta["planted"]["personas"] == 0


def test_cli_scan_and_recurring(plist, tmp_path, monkeypatch, capsys):
    db = tmp_path / "c.db"
    conn = store.connect(db)
    _article(conn, 1, author="Ervin Hoskins")
    conn.close()
    real_connect = store.connect
    monkeypatch.setattr(store, "connect", lambda path=db: real_connect(path))
    assert planted.main(["scan", "--full"]) == 0
    assert json.loads(capsys.readouterr().out)["flagged"] == 1
    assert planted.main(["recurring", "--days", "7"]) == 0
    assert json.loads(capsys.readouterr().out)["window_days"] == 7
    assert planted.main([]) == 2
