"""Planted bylines detector: normalisation, the list and its validation, the three detectors,
storage, pruning, the deadline and resume, the recurring bylines diagnostic (command line only)
and the export. Temporary databases and temporary YAML only."""
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
                         "articles_total", "by_country", "by_outlet"}
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
    assert data["flags_total"] == 2 and data["articles_total"] == 2
    assert planted.meta_block(conn) == {"flags_total": 2, "articles_total": 2, "flags_30d": 0, "list_version": "2026.10.1",
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
    assert meta["planted"] == {"flags_total": 1, "articles_total": 1, "flags_30d": 1, "list_version": "2026.10.1",
                               "personas": 4, "fronts": 2, "operations": 2}
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


# ---------------------------------------------------------------------------
# Author field splitting
# ---------------------------------------------------------------------------

def _personas(*names):
    return [{"id": n.lower().replace(" ", "_"), "name": n, "operation": "op_bogus", "variants_n": planted.name_variants(n)}
            for n in names]


NAMED = _personas("Jenny Williams", "Alice Johnson", "Michael Harrison")


@pytest.mark.parametrize("field, expected", [
    ("Jenny Williams, Alice Johnson", ["jenny_williams", "alice_johnson"]),
    ("Alice Johnson; Bob Lee", ["alice_johnson"]),
    ("Michael Harrison, PhD", ["michael_harrison"]),
    ("Michael Harrison | Contributor", ["michael_harrison"]),
    ("email@x | Michael Harrison", ["michael_harrison"]),
    ("Williams, Jenny", ["jenny_williams"]),
    ("Dr. Michael Harrison", ["michael_harrison"]),
    # hits come back in persona list order, Jenny Williams before Michael Harrison in NAMED
    ("Michael Harrison and Jenny Williams", ["jenny_williams", "michael_harrison"]),
    ("Prof. Alice Johnson, MD", ["alice_johnson"]),
    ("Jenny Williams y Alice Johnson", ["jenny_williams", "alice_johnson"]),
    ("Jenny Williams / Alice Johnson & Michael Harrison", ["jenny_williams", "alice_johnson", "michael_harrison"]),
    ("michael.harrison@example.test", []),
    ("Michael Harrison in Beijing", []),
    ("Harrison", []),
])
def test_author_field_is_split_and_cleaned_before_matching(field, expected):
    assert [p["id"] for p, _ in planted.match_author(field, NAMED)] == expected


def test_author_segments_drop_emails_and_single_words():
    segs = planted._author_segments("email@x | Michael Harrison, PhD")
    assert "michael harrison" in segs and not any("@" in s for s in segs)
    assert all(len(s.split()) >= 2 for s in segs)
    assert planted._author_segments("   ") == []


# ---------------------------------------------------------------------------
# Byline head precision
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body, expected", [
    ("una declaración de Alice Johnson\n\nEl acuerdo.", []),
    ("The port deal was announced by Alice Johnson on Monday.", []),
    ("Opinion\n\nBy Alice Johnson\n\nThe port deal.", ["alice_johnson"]),
    ("Opinion. Written by Alice Johnson\n\nThe port deal.", ["alice_johnson"]),
    ("Escrito por Alice Johnson. Lima.", ["alice_johnson"]),
    ("Texte écrit par Alice Johnson.", ["alice_johnson"]),
    ("Von Michael Harrison\n\nBerlin.", ["michael_harrison"]),
    ("Un articolo di Alice Johnson sul porto.", []),
    ("Di Alice Johnson\n\nRoma.", ["alice_johnson"]),
    ("Byline\nBy Jenny Williams, Alice Johnson\n\ntext", ["jenny_williams"]),
])
def test_byline_head_only_at_line_start_or_after_written(body, expected):
    assert [p["id"] for p, _ in planted.match_head(body, NAMED)] == expected


def test_byline_words_no_longer_include_de():
    assert "de" not in planted.BYLINE_WORDS and "di" in planted.BYLINE_WORDS


# ---------------------------------------------------------------------------
# Front phrases and domains
# ---------------------------------------------------------------------------

def _fronts(*specs):
    out = []
    for name, domains in specs:
        out.append({"id": name.lower().replace(" ", "_"), "name": name, "operation": "op_front",
                    "variants_n": planted.name_variants(name), "domains_n": sorted(domains)})
    return out


FRONTS = _fronts(("Social Research Center", ["srcenter.example"]), ("Times Newswire", ["timesnewswire.example"]),
                 ("Monolith", []))


@pytest.mark.parametrize("text, expected", [
    ("the National Social Research Center of Kazakhstan published a poll", []),
    ("New York Times Newswire reported the deal", []),
    ("according to the Social Research Center, the deal is done", ["social_research_center"]),
    ("The Social Research Center said so.", ["social_research_center"]),
    ("Analysts in Lima. Social Research Center figures show", ["social_research_center"]),
    ("Lima Social Research Center figures show", []),
    ("A social research centre in Oslo.", []),
    ("Monolith Monolith Monolith", []),
    ("(Times Newswire) The deal", ["times_newswire"]),
])
def test_front_names_match_whole_phrases_not_extended_by_a_capitalised_word(text, expected):
    assert [f["id"] for f, _ in planted.match_fronts("t", text, FRONTS)] == expected


def test_front_name_in_title_is_bounded_by_the_title_line():
    hits = planted.match_fronts("Kazakhstan Social Research Center", "A body with nothing.", FRONTS)
    assert hits == []
    hits = planted.match_fronts("Kazakhstan", "Social Research Center polled voters.", FRONTS)
    assert [f["id"] for f, _ in hits] == ["social_research_center"]


@pytest.mark.parametrize("text, expected", [
    ("See https://www.srcenter.example/report for more.", ["social_research_center"]),
    ("Read it at srcenter.example.", ["social_research_center"]),
    ("Read it at notsrcenter.example.", []),
    ("Read it at srcenter.example.evil.test now.", []),
    ("Read it at srcenter.examples now.", []),
    ("Mail: info@srcenter.example", ["social_research_center"]),
])
def test_front_domains_match_whole_hostnames(text, expected):
    assert [f["id"] for f, _ in planted.match_fronts("t", text, FRONTS)] == expected


def test_one_alternation_regex_per_list_is_compiled_once(monkeypatch):
    planted._RE_CACHE.clear()
    calls = []
    real_compile = planted.re.compile
    monkeypatch.setattr(planted.re, "compile", lambda *a, **k: calls.append(a[0]) or real_compile(*a, **k))
    for _ in range(3):
        planted.match_fronts("t", "nothing here", FRONTS)
        planted.match_head("nothing here", NAMED)
    # one name pattern and one domain pattern for the fronts, one pattern for the head, built once
    assert len(calls) == 3
    assert all("|" in c for c in calls)


# ---------------------------------------------------------------------------
# The list: validation
# ---------------------------------------------------------------------------

def _load(tmp_path, data, capsys, name="p.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data, allow_unicode=True) if not isinstance(data, str) else data, encoding="utf-8")
    out = planted.load_personas(p)
    return out, capsys.readouterr().err


def test_load_personas_missing_version_warns_and_loads_empty(tmp_path, capsys):
    data = {k: v for k, v in PERSONAS.items() if k != "version"}
    out, err = _load(tmp_path, data, capsys)
    assert out == {"version": "", "operations": [], "personas": [], "fronts": []}
    assert "version" in err and err.count("\n") == 1


def test_load_personas_non_string_version_warns_and_loads_empty(tmp_path, capsys):
    out, err = _load(tmp_path, dict(PERSONAS, version=2026.1), capsys)
    assert out["personas"] == [] and "version" in err


def test_load_personas_parse_failure_is_one_warning_line(tmp_path, capsys):
    out, err = _load(tmp_path, "version: '1'\npersonas: [unclosed\n", capsys)
    assert out == {"version": "", "operations": [], "personas": [], "fronts": []}
    assert err.count("\n") == 1 and "could not be read" in err
    out, err = _load(tmp_path, "just a string", capsys, name="s.yaml")
    assert out["personas"] == [] and "not a mapping" in err


def test_load_personas_missing_file_is_silent(tmp_path, capsys):
    assert planted.load_personas(tmp_path / "absent.yaml")["version"] == ""
    assert capsys.readouterr().err == ""


def test_load_personas_name_must_be_a_string(tmp_path, capsys):
    data = dict(PERSONAS, personas=[{"id": "x", "name": ["Not", "A", "String"], "operation": "op_bogus"},
                                    {"id": "y", "name": "Real Name", "operation": "op_bogus"}])
    out, err = _load(tmp_path, data, capsys)
    assert [p["id"] for p in out["personas"]] == ["y"] and "name is not a string" in err


def test_load_personas_bare_string_variants_and_domains_become_lists(tmp_path, capsys):
    data = dict(PERSONAS, personas=[{"id": "x", "name": "Some Body", "variants": "Some B. Body", "operation": "op_bogus"}],
                fronts=[{"id": "f", "name": "Front Org", "domains": "front.example", "variants": ["Front Organisation", 7],
                         "operation": "op_front"}])
    out, err = _load(tmp_path, data, capsys)
    assert out["personas"][0]["variants"] == ["Some B. Body"]
    assert "some b body" in out["personas"][0]["variants_n"]
    assert out["fronts"][0]["domains"] == ["front.example"] and out["fronts"][0]["domains_n"] == ["front.example"]
    assert out["fronts"][0]["variants"] == ["Front Organisation"] and "7" in err


def test_load_personas_duplicate_ids_drop_the_later_entry(tmp_path, capsys):
    data = dict(PERSONAS, personas=[{"id": "dup", "name": "First Entry", "operation": "op_bogus"},
                                    {"id": "dup", "name": "Second Entry", "operation": "op_bogus"}],
                fronts=[{"id": "fd", "name": "First Front", "operation": "op_front"},
                        {"id": "fd", "name": "Second Front", "operation": "op_front"}],
                operations=PERSONAS["operations"] + [dict(PERSONAS["operations"][0], name="Again")])
    out, err = _load(tmp_path, data, capsys)
    assert [p["name"] for p in out["personas"]] == ["First Entry"]
    assert [f["name"] for f in out["fronts"]] == ["First Front"]
    assert [o["name"] for o in out["operations"]] == ["Bogus Bylines", "Dark Front"]
    assert err.count("duplicate") == 3


def test_load_personas_unresolved_operation_drops_the_entry(tmp_path, capsys):
    data = dict(PERSONAS, personas=[{"id": "x", "name": "Some Body", "operation": "op_unknown"},
                                    {"id": "y", "name": "No Operation"},
                                    {"id": "z", "name": "Fine Person", "operation": "op_bogus"}],
                fronts=[{"id": "f", "name": "Front Org", "operation": "op_unknown"}])
    out, err = _load(tmp_path, data, capsys)
    assert [p["id"] for p in out["personas"]] == ["z"] and out["fronts"] == []
    assert err.count("does not resolve") == 3


def test_real_persona_file_loads_without_warnings(capsys):
    lst = planted.load_personas(config.ROOT / "sources" / "personas.yaml")
    assert lst["version"] and lst["personas"] and lst["fronts"]
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# Deadline and resume
# ---------------------------------------------------------------------------

def test_scan_stops_at_deadline_and_resumes_where_it_stopped(plist, tmp_path, monkeypatch):
    conn = _db(tmp_path)
    ids = [_article(conn, i, author="Nobody %d" % i) for i in range(3)]
    last = _article(conn, 9, author="Mia Clark")
    ticks = iter(range(100))
    monkeypatch.setattr(planted.time, "monotonic", lambda: next(ticks))
    # the clock reads 0, 1, 2, ... once per row: rows 1 and 2 pass, the third check stops the scan
    counts = planted.scan(conn, deadline=1.5)
    assert counts["stopped_at_deadline"] is True and counts["scanned"] == 2 and counts["flagged"] == 0
    state = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM planted_state")}
    assert state["last_scanned_id"] == str(ids[1]) and state["list_version"] == "2026.10.1"
    assert "last_fetched_at" in state and "scanned_at" not in state
    # the next scan is incremental, not a fresh full rescan, and carries on from the third row
    counts = planted.scan(conn)
    assert counts["full"] is False and counts["stopped_at_deadline"] is False
    assert counts["scanned"] == 2 and counts["flagged"] == 1
    assert _flags(conn)[0]["article_id"] == last
    state = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM planted_state")}
    assert state["last_scanned_id"] == str(last) and "scanned_at" in state


def test_scan_with_an_expired_deadline_scans_nothing_and_keeps_its_place(plist, tmp_path):
    conn = _db(tmp_path)
    _article(conn, 1, author="Mia Clark")
    import time
    counts = planted.scan(conn, deadline=time.monotonic() - 1)
    assert counts["scanned"] == 0 and counts["stopped_at_deadline"] is True
    state = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM planted_state")}
    assert state["last_scanned_id"] == "0"
    assert planted.scan(conn)["flagged"] == 1


def test_scan_writes_its_place_at_every_periodic_commit(plist, tmp_path, monkeypatch):
    conn = _db(tmp_path)
    monkeypatch.setattr(planted, "COMMIT_EVERY", 2)
    ids = [_article(conn, i, author="Nobody %d" % i) for i in range(5)]
    writes = []
    real_set_state = planted._set_state

    def spy(c, **values):
        writes.append(dict(values))
        real_set_state(c, **values)
    monkeypatch.setattr(planted, "_set_state", spy)
    planted.scan(conn)
    assert [w["last_scanned_id"] for w in writes] == [ids[1], ids[3], ids[4]]
    assert all(w["list_version"] == "2026.10.1" and "last_fetched_at" in w for w in writes)
    assert "scanned_at" not in writes[0] and "scanned_at" in writes[-1]


def test_scan_quietly_passes_the_deadline(plist, tmp_path):
    conn = _db(tmp_path)
    _article(conn, 1, author="Mia Clark")
    import time
    out = planted.scan_quietly(conn, deadline=time.monotonic() - 1)
    assert out["stopped_at_deadline"] is True and out["scanned"] == 0


def test_cli_scan_budget(plist, tmp_path, monkeypatch, capsys):
    db = tmp_path / "b.db"
    conn = store.connect(db)
    _article(conn, 1, author="Ervin Hoskins")
    conn.close()
    real_connect = store.connect
    monkeypatch.setattr(store, "connect", lambda path=db: real_connect(path))
    assert planted.main(["scan", "--budget-seconds", "30"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["flagged"] == 1 and out["stopped_at_deadline"] is False


# ---------------------------------------------------------------------------
# Export guards
# ---------------------------------------------------------------------------

def test_build_export_survives_a_null_country_and_outlet(plist, tmp_path, monkeypatch):
    """articles.country is NOT NULL today, so the null reaches build_export through the flag rows
    (an older database or a future column change), where sorting None against str used to raise."""
    conn = _db(tmp_path)
    _article(conn, 1, author="Ervin Hoskins")
    _article(conn, 2, author="Mia Clark", country="USA", outlet="us_x")
    planted.scan(conn)
    real_rows = planted._flag_rows

    def null_rows(c, plist_):
        rows = real_rows(c, plist_)
        rows[-1]["country"] = None
        rows[-1]["outlet_id"] = None
        return rows
    monkeypatch.setattr(planted, "_flag_rows", null_rows)
    data = planted.build_export(conn)
    assert data["by_country"] == {None: 1, "USA": 1} and data["by_outlet"] == {None: 1, "us_x": 1}
    assert json.loads(json.dumps(data))["by_country"] == {"null": 1, "USA": 1}
    assert planted.meta_block(conn)["flags_total"] == 2


def test_articles_total_counts_distinct_articles(plist, tmp_path):
    conn = _db(tmp_path)
    _article(conn, 1, author="Ervin Hoskins", body="By Ervin Hoskins\n\nA piece citing the Social Research Center.")
    _article(conn, 2, author="Mia Clark")
    planted.scan(conn)
    data = planted.build_export(conn)
    assert data["flags_total"] == 4 and data["articles_total"] == 2
    meta = planted.meta_block(conn)
    assert meta["flags_total"] == 4 and meta["articles_total"] == 2


def test_build_export_has_no_recurring_bylines_but_the_cli_still_does(plist, tmp_path, monkeypatch, capsys):
    conn = _db(tmp_path)
    assert "recurring_bylines" not in planted.build_export(conn)
    assert planted.recurring_bylines(conn) == []
    db = tmp_path / "r.db"
    real_connect = store.connect
    monkeypatch.setattr(store, "connect", lambda path=db: real_connect(path))
    assert planted.main(["recurring"]) == 0
    assert "rows" in json.loads(capsys.readouterr().out)


def test_export_run_degrades_when_the_planted_export_fails(plist, tmp_path, monkeypatch, capsys):
    db = tmp_path / "f.db"
    conn = store.connect(db)
    monkeypatch.setattr(config, "DB_PATH", db)
    _article(conn, 1, author="Ervin Hoskins")

    def boom(*a, **k):
        raise RuntimeError("broken export")
    monkeypatch.setattr(planted, "build_export", boom)
    monkeypatch.setattr(planted, "meta_block", boom)
    out = tmp_path / "out"
    counts = export.run(conn, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M.md")
    assert counts["planted"]["flagged"] == 1
    data = json.loads((out / "planted.json").read_text(encoding="utf-8"))
    assert data["flags"] == [] and data["flags_total"] == 0 and data["articles_total"] == 0 and data["list_version"] == ""
    assert set(data) == {"generated", "list_version", "operations", "personas", "fronts", "flags", "flags_total",
                         "articles_total", "by_country", "by_outlet"}
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["planted"] == planted.empty_meta_block()
    err = capsys.readouterr().err
    assert "planted export failed: RuntimeError: broken export" in err and "planted meta block failed" in err


def test_methodology_text_names_the_command_line_diagnostic_only():
    from pipeline import methodology
    text = methodology.planted_text({"planted": {"flags_total": 1, "flags_30d": 0, "personas": 1, "fronts": 1, "operations": 1,
                                                 "list_version": "v"}})
    assert "python -m pipeline.planted recurring" in text and "never published" in text
    assert "site also shows" not in text
