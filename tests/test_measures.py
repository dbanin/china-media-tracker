"""The research measures foundation (pipeline/measures.py): the tables they write, the provisional gate,
and the module contract, proved with a fake measure.

The measures are report only: the prune never touches their tables, a missing module leaves its
measure off, a failing one is recorded and never stops the export, and the methodology carries a
paragraph only for a measure that is not off.
"""
import csv
import json
import sqlite3
import sys
import time
import types
from pathlib import Path

import pytest

from pipeline import config, export, measures, methodology, store

NEW_TABLES = {"reference_items", "reference_feed_health", "state_copies", "engagement_aggregates"}


def _cols(conn, table):
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]


# ---------------------------------------------------------------------------
# Tables and migrations
# ---------------------------------------------------------------------------

def test_new_tables_and_columns_on_a_new_database(tmp_path):
    conn = store.connect(tmp_path / "n.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert NEW_TABLES <= tables and set(store.MEASURE_TABLES) == NEW_TABLES
    assert "doctrine" in _cols(conn, "articles")
    assert _cols(conn, "reference_items") == [
        "id", "source_id", "url", "url_hash", "language", "title", "summary", "author", "published_at", "discovered_at",
        "fetched_at", "status", "fail_reason", "body_hash", "body_chars", "extract_version", "sig", "shingles",
        "body_pruned_at", "collector", "also_monitored"]
    # reference_feed_health mirrors feed_health, with source_id for outlet_id
    assert _cols(conn, "reference_feed_health") == [c.replace("outlet_id", "source_id") for c in _cols(conn, "feed_health")]
    assert _cols(conn, "state_copies") == [
        "copy_id", "version", "computed_at", "current", "original_kind", "ref_id", "original_article_id", "original_source",
        "match_method", "match_similarity", "credit_consistent", "none_reason", "copy_published_at", "original_published_at",
        "copy_ts_res", "orig_ts_res", "lag_seconds", "body_state", "headline_similarity", "kept_share", "novel_share",
        "rewritten_share", "added_paragraphs", "added_words", "cue_local", "cue_counterpoint", "band", "details"]
    assert set(_cols(conn, "engagement_aggregates")) >= {
        "key_type", "key", "platform", "period_start", "period_end", "posts_n", "reactions", "shares", "comments", "views",
        "suppressed", "query_date", "mcl_query_ref", "file_sha256", "imported_at"}
    # url_hash is unique among reference items, and copy_id is the key of state_copies
    conn.execute("INSERT INTO reference_items(source_id, url, url_hash, discovered_at, status) VALUES ('ref_x', 'u', 'h', 't', 'discovered')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO reference_items(source_id, url, url_hash, discovered_at, status) VALUES ('ref_y', 'v', 'h', 't', 'discovered')")
    conn.execute("INSERT INTO state_copies(copy_id, version, computed_at, original_kind) VALUES (1, 'v', 't', 'none')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO state_copies(copy_id, version, computed_at, original_kind) VALUES (1, 'v', 't', 'none')")
    # A new database has every column, so no doctrine column step is recorded.
    assert not conn.execute("SELECT 1 FROM schema_migrations WHERE name='articles.doctrine'").fetchone()


def test_an_older_database_gains_the_tables_and_the_column_once(tmp_path):
    db = tmp_path / "old.db"
    conn = store.connect(db)
    store.insert_discovered(conn, {"url": "https://a.test/1", "outlet_id": "o", "country": "ITA", "language": "it",
                                   "gate_relevant": 1})
    conn.commit()
    conn.close()
    raw = sqlite3.connect(str(db))
    raw.execute("ALTER TABLE articles DROP COLUMN doctrine")
    for t in NEW_TABLES:
        raw.execute("DROP TABLE %s" % t)
    raw.commit()
    raw.close()
    for _ in range(2):
        conn = store.connect(db)
        conn.close()
    conn = store.connect(db)
    assert _cols(conn, "articles").count("doctrine") == 1
    assert NEW_TABLES <= {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert [r[0] for r in conn.execute("SELECT name FROM schema_migrations WHERE name='articles.doctrine'")] == ["articles.doctrine"]
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE doctrine IS NULL").fetchone()[0] == 1


def test_the_prune_never_touches_the_measure_tables(tmp_path):
    conn = store.connect(tmp_path / "p.db")
    aid = store.insert_discovered(conn, {"url": "https://x.test/old", "outlet_id": "o", "country": "ITA", "language": "it",
                                         "title": "old", "status": "gated_out", "gate_relevant": 0})
    h = conn.execute("SELECT url_hash FROM articles WHERE id=?", (aid,)).fetchone()[0]
    conn.execute("UPDATE articles SET discovered_at='2020-01-01T00:00:00+00:00', audit_keep=0")
    # Rows that point at the article about to be pruned, and share its address.
    conn.execute("INSERT INTO reference_items(source_id, url, url_hash, discovered_at, status) VALUES (?,?,?,?,?)",
                 ("ref_xinhua_en", "https://x.test/old", h, "2020-01-01T00:00:00+00:00", "fetched"))
    conn.execute("INSERT INTO state_copies(copy_id, version, computed_at, original_kind, original_article_id) VALUES (?,?,?,?,?)",
                 (aid, "v", "2020-01-01", "monitored", aid))
    conn.execute("""INSERT INTO engagement_aggregates(key_type, key, platform, period_start, period_end, query_date,
                    file_sha256, imported_at) VALUES ('url', 'https://x.test/old', 'facebook', '2020-01-01', '2020-01-31',
                    '2020-02-01', 'abc', '2020-02-01')""")
    store.record_reference_feed_health(conn, "ref_xinhua_en", "https://ref.test/feed", False, "timeout", 0)
    conn.commit()
    before = {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in NEW_TABLES}
    assert all(before.values())
    assert store.prune_gated_out(conn) == 1
    store.expire_seen(conn)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0
    assert {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in NEW_TABLES} == before
    # The reference feed's health is its own table, never the outlets' feed health.
    assert conn.execute("SELECT COUNT(*) FROM feed_health").fetchone()[0] == 0
    store.record_reference_feed_health(conn, "ref_xinhua_en", "https://ref.test/feed", True, None, 12)
    r = conn.execute("SELECT * FROM reference_feed_health").fetchone()
    assert (r["consecutive_failures"], r["total_checks"], r["total_failures"], r["last_entries"]) == (0, 2, 1, 12)


# ---------------------------------------------------------------------------
# A fake measure proves the contract
# ---------------------------------------------------------------------------

def fake_measure(mid="fake", version="1", inputs=5, fail=None, threshold=0.8, seen=None):
    """A module that follows the measure interface. fail names the function that raises."""
    m = types.ModuleType("fake_measure_%s" % mid)
    m.MEASURE_ID, m.VERSION = mid, version

    def boom(where):
        if fail == where:
            raise RuntimeError("%s broke" % where)

    def inputs_present(conn):
        boom("inputs_present")
        return inputs

    def compute(conn, ctx, deadline):
        assert deadline > time.monotonic()
        if seen is not None:
            seen["ctx"] = sorted(ctx)
            seen["earlier"] = sorted(ctx["measures"])
        conn.execute("INSERT OR REPLACE INTO state_copies(copy_id, version, computed_at, original_kind) VALUES (?, ?, 't', 'none')",
                     (len(mid) * 100 + ord(mid[0]), version))
        boom("compute")
        return {"rows": 1}

    def build_export(conn, ctx):
        boom("build_export")
        return {"rows": conn.execute("SELECT COUNT(*) FROM state_copies").fetchone()[0]}

    def meta_summary(conn, ctx):
        return {"rows": 1}

    def methodology_text(block):
        boom("methodology_text")
        return "The %s method, %s." % (mid, block["status"])

    def draw_sample(conn, n, seed, out_dir):
        rows = [{"sample_id": str(i), "verdict": ""} for i in range(n)]
        return measures.write_sample(out_dir, mid, rows, ["sample_id", "verdict"],
                                     {"answers": {str(i): "yes" for i in range(n)}}, version=version, seed=seed,
                                     day=seen.get("day") if seen else None)

    def score_check(rows, key):
        coded = [r for r in rows if (r.get("verdict") or "").strip()]
        agree = sum(1 for r in coded if r["verdict"] == key["answers"][r["sample_id"]])
        return measures.check_result("precision", agree / len(coded) if coded else None, threshold, len(rows), len(coded))

    for f in (inputs_present, compute, build_export, meta_summary, methodology_text, draw_sample, score_check):
        setattr(m, f.__name__, f)
    return m


def _install(monkeypatch, tmp_path, mods, entries=None):
    """Put fake modules in sys.modules and a measures.yaml naming them in tmp_path; returns its path."""
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    entries = entries or [{"id": mod.MEASURE_ID, "label": mod.MEASURE_ID.title(), "module": name, "enabled": True,
                           "min_inputs": 1} for name, mod in mods.items()]
    p = tmp_path / "measures.yaml"
    import yaml
    p.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return p


def _code(csv_path, verdicts):
    """Fill the verdict column of a sample: verdicts is a list, '' leaves a row uncoded."""
    with open(csv_path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r, v in zip(rows, verdicts):
        r["verdict"] = v
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["sample_id", "verdict"])
        w.writeheader()
        w.writerows(rows)


def _run(conn, tmp_path, cfg, reviews):
    out = tmp_path / "out"
    return measures.run_all_quietly(conn, out, {"meta": {}}, config_path=cfg, reviews_dir=reviews), out


def test_the_gate_from_hand_check_files(tmp_path, monkeypatch):
    seen = {}
    mod = fake_measure(seen=seen)
    cfg = _install(monkeypatch, tmp_path, {"fake_measure_mod": mod})
    reviews = tmp_path / "reviews"
    conn = store.connect(tmp_path / "g.db")

    # No sample yet: provisional.
    blocks, out = _run(conn, tmp_path, cfg, reviews)
    b = blocks["fake"]
    assert b["status"] == "provisional" and b["check"]["state"] == "none" and b["reason"] == "no hand check yet"
    assert b["file"] == "fake.json" and json.loads((out / "fake.json").read_text()) == {"rows": 1}
    assert b["version"] == "1" and b["inputs_present"] == 5 and b["enabled"] is True and b["label"] == "Fake"
    assert b["summary"] == {"rows": 1} and b["method_text"] == "The fake method, provisional."
    assert set(b["check"]) >= {"state", "file", "n", "coded", "statistic", "value", "threshold", "checked_on"}

    # Coding incomplete: still provisional.
    seen["day"] = "2026-10-20"
    c1, k1 = mod.draw_sample(conn, 4, "s1", reviews)
    assert c1.name == "fake_sample_2026-10-20.csv" and k1.name == "fake_sample_2026-10-20.key.json"
    key = json.loads(k1.read_text())
    assert key["measure_id"] == "fake" and key["measure_version"] == "1" and key["n"] == 4 and key["seed"] == "s1"
    _code(c1, ["yes", "yes", "", ""])
    b = _run(conn, tmp_path, cfg, reviews)[0]["fake"]
    assert b["status"] == "provisional" and b["check"]["state"] == "incomplete"
    assert (b["check"]["n"], b["check"]["coded"]) == (4, 2) and "2 of 4 coded" in b["reason"]

    # Complete and passed: checked.
    _code(c1, ["yes", "yes", "yes", "yes"])
    b = _run(conn, tmp_path, cfg, reviews)[0]["fake"]
    assert b["status"] == "checked" and b["check"]["state"] == "passed"
    assert (b["check"]["statistic"], b["check"]["value"], b["check"]["threshold"]) == ("precision", 1.0, 0.8)
    assert b["check"]["checked_on"] == "2026-10-20" and b["check"]["file"].endswith("fake_sample_2026-10-20.csv")

    # A newer sample, complete and failed: withheld.
    seen["day"] = "2026-10-25"
    c2, _ = mod.draw_sample(conn, 4, "s2", reviews)
    _code(c2, ["no", "no", "yes", "no"])
    b = _run(conn, tmp_path, cfg, reviews)[0]["fake"]
    assert b["status"] == "withheld" and b["check"]["state"] == "failed" and b["check"]["value"] == 0.25
    assert b["method_text"] == "The fake method, withheld." and b["reason"].startswith("hand check failed on 2026-10-25")

    # A newer one still being coded leaves the failed check standing.
    seen["day"] = "2026-10-30"
    c3, _ = mod.draw_sample(conn, 4, "s3", reviews)
    b = _run(conn, tmp_path, cfg, reviews)[0]["fake"]
    assert b["status"] == "withheld" and b["check"]["checked_on"] == "2026-10-25" and "0 of 4 coded" in b["reason"]

    # Coded and passed, it decides again.
    _code(c3, ["yes"] * 4)
    b = _run(conn, tmp_path, cfg, reviews)[0]["fake"]
    assert b["status"] == "checked" and b["check"]["value"] == 1.0


def test_a_new_check_under_way_after_a_pass_is_provisional_and_a_stale_check_does_not_count(tmp_path, monkeypatch):
    seen = {"day": "2026-10-20"}
    mod = fake_measure(seen=seen)
    cfg = _install(monkeypatch, tmp_path, {"fake_measure_mod": mod})
    reviews = tmp_path / "reviews"
    conn = store.connect(tmp_path / "g.db")
    c1, _ = mod.draw_sample(conn, 2, "s1", reviews)
    _code(c1, ["yes", "yes"])
    assert _run(conn, tmp_path, cfg, reviews)[0]["fake"]["status"] == "checked"
    seen["day"] = "2026-10-21"
    mod.draw_sample(conn, 2, "s2", reviews)
    b = _run(conn, tmp_path, cfg, reviews)[0]["fake"]
    assert b["status"] == "provisional" and b["reason"].startswith("a new hand check is under way")
    # A new version: every sample made under version 1 is stale.
    mod.VERSION = "2"
    b = _run(conn, tmp_path, cfg, reviews)[0]["fake"]
    assert b["status"] == "provisional" and b["check"]["state"] == "stale" and "version 1" in b["reason"]


def test_a_broken_sample_file_is_reported_not_raised(tmp_path, monkeypatch):
    mod = fake_measure()
    cfg = _install(monkeypatch, tmp_path, {"fake_measure_mod": mod})
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "fake_sample_2026-10-20.csv").write_text("sample_id,verdict\n1,yes\n", encoding="utf-8")
    (reviews / "fake_sample_2026-10-20.key.json").write_text("{not json", encoding="utf-8")
    b = _run(store.connect(tmp_path / "g.db"), tmp_path, cfg, reviews)[0]["fake"]
    assert b["status"] == "provisional" and b["check"]["state"] == "error" and "could not be scored" in b["reason"]


def test_missing_switched_off_and_waiting_measures_are_off(tmp_path, monkeypatch):
    waiting = fake_measure("waiting", inputs=0)
    off = fake_measure("switched")
    cfg = _install(monkeypatch, tmp_path, {"fake_waiting": waiting, "fake_switched": off}, entries=[
        {"id": "ghost", "label": "Ghost", "module": "pipeline.no_such_measure_module", "enabled": True, "min_inputs": 1},
        {"id": "waiting", "label": "Waiting", "module": "fake_waiting", "enabled": True, "min_inputs": 1},
        {"id": "switched", "label": "Switched", "module": "fake_switched", "enabled": False, "min_inputs": 1}])
    out = tmp_path / "out"
    out.mkdir()
    (out / "ghost.json").write_text("{}")      # left by an earlier export
    blocks = measures.run_all_quietly(store.connect(tmp_path / "g.db"), out, {}, config_path=cfg, reviews_dir=tmp_path)
    assert [blocks[i]["status"] for i in ("ghost", "waiting", "switched")] == ["off"] * 3
    assert blocks["ghost"]["reason"] == "not built" and blocks["ghost"]["version"] is None
    assert blocks["waiting"]["reason"] == "waiting for inputs: 0 of at least 1" and blocks["waiting"]["inputs_present"] == 0
    assert blocks["switched"]["reason"] == "switched off in sources/measures.yaml" and blocks["switched"]["inputs_present"] is None
    assert all(blocks[i]["file"] is None and blocks[i]["method_text"] == "" for i in blocks)
    assert list(out.iterdir()) == []


def test_a_module_whose_own_import_fails_is_a_failure_not_unbuilt(tmp_path, monkeypatch):
    pkg = tmp_path / "pkgs"
    pkg.mkdir()
    (pkg / "broken_measure_mod.py").write_text("import no_such_dependency_at_all\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(pkg))
    cfg = _install(monkeypatch, tmp_path, {}, entries=[
        {"id": "broken", "label": "Broken", "module": "broken_measure_mod", "enabled": True, "min_inputs": 1}])
    out = tmp_path / "out"
    b = measures.run_all_quietly(store.connect(tmp_path / "g.db"), out, {}, config_path=cfg, reviews_dir=tmp_path)["broken"]
    assert b["status"] == "off" and "no_such_dependency_at_all" in b["error"] and b["reason"].startswith("failed: ")
    assert json.loads((out / "broken.json").read_text()) == {}


def test_a_failing_measure_never_stops_the_export(tmp_path, monkeypatch):
    seen = {}
    good, bad = fake_measure("good", seen=seen), fake_measure("bad", fail="compute")
    late = fake_measure("late", fail="methodology_text")
    cfg = _install(monkeypatch, tmp_path, {"fake_bad": bad, "fake_good": good, "fake_late": late}, entries=[
        {"id": "bad", "label": "Bad", "module": "fake_bad", "enabled": True, "min_inputs": 1},
        {"id": "good", "label": "Good", "module": "fake_good", "enabled": True, "min_inputs": 1},
        {"id": "late", "label": "Late", "module": "fake_late", "enabled": True, "min_inputs": 1}])
    monkeypatch.setattr(measures, "MEASURES_YAML", cfg)
    monkeypatch.setattr(measures, "REVIEWS_DIR", tmp_path / "reviews")
    db = tmp_path / "e.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    conn = store.connect(db)
    out = tmp_path / "site" / "data"
    export.run(conn, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M.md")
    meta = json.loads((out / "meta.json").read_text())
    for f in ("latest.json", "global_series.json", "outlets.json", "examples.json", "planted.json"):
        assert (out / f).exists(), f
    m = meta["measures"]
    assert list(m) == ["bad", "good", "late"]
    assert m["bad"]["status"] == "off" and m["bad"]["error"] == "RuntimeError: compute broke"
    assert json.loads((out / "bad.json").read_text()) == {}
    assert m["late"]["status"] == "off" and "methodology_text broke" in m["late"]["error"]
    assert m["good"]["status"] == "provisional" and json.loads((out / "good.json").read_text()) == {"rows": 1}
    # The failing compute's writes were rolled back. What a compute that returned wrote is committed, so a
    # later failure (late, in methodology_text) does not undo it, and the next export resumes from it.
    assert {r[0] for r in conn.execute("SELECT copy_id FROM state_copies")} == {503, 508}
    # ctx carries the export's structures and the blocks of the measures already run.
    assert seen["ctx"] == ["daily", "export_dir", "latest", "measures", "meta", "outlets", "outlets_json"]
    assert seen["earlier"] == ["bad"]
    text = (tmp_path / "M.md").read_text()
    assert "## New measures (provisional)" in text and "**Good (provisional).** The good method, provisional." in text
    assert "bad method" not in text and "late method" not in text


# ---------------------------------------------------------------------------
# Methodology and configuration
# ---------------------------------------------------------------------------

def test_methodology_text_only_for_measures_that_are_not_off():
    assert measures.methodology_text({}) == "" and measures.methodology_text({"measures": {}}) == ""
    off = {"a": {"status": "off", "label": "A", "method_text": "Hidden."}}
    assert measures.methodology_text({"measures": off}) == ""
    text = measures.methodology_text({"measures": dict(off, b={"status": "checked", "label": "B", "method_text": "Shown."},
                                                       c={"status": "withheld", "label": "C", "method_text": "Also."})})
    assert text.startswith("## New measures (provisional)\n\n") and text.endswith("\n\n")
    assert "**B (checked by hand).** Shown." in text and "**C (withheld).** Also." in text and "Hidden." not in text
    assert "\u2014" not in text and "\u2013" not in text


def test_the_template_reads_the_same_with_every_measure_off(tmp_path, monkeypatch):
    """{measures_text} is empty when every measure is off, and the planted section runs straight on into
    the next heading as before."""
    assert "{measures_text}## What the numbers mean" in methodology.TEMPLATE
    db = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    cfg = _install(monkeypatch, tmp_path, {}, entries=[
        {"id": "ghost", "label": "Ghost", "module": "pipeline.no_such_measure_module", "enabled": True, "min_inputs": 1}])
    monkeypatch.setattr(measures, "MEASURES_YAML", cfg)
    export.run(store.connect(db), "test", export_dir=tmp_path / "o", audit_dir=tmp_path / "a", methodology_path=tmp_path / "M.md")
    text = (tmp_path / "M.md").read_text()
    assert "New measures" not in text
    assert "\n\n## What the numbers mean" in text and "\n\n\n## What the numbers mean" not in text


def test_the_measures_file():
    specs = measures.load_config()
    assert [s["id"] for s in specs] == ["alteration", "speed", "doctrine", "spikes", "calendar", "engagement", "syndication"]
    on = {s["id"]: s["enabled"] for s in specs}
    assert on["engagement"] is False and on["syndication"] is True    # syndication on since 2026-10-28 (D7)
    assert all(on[i] for i in ("alteration", "speed", "doctrine", "spikes", "calendar"))
    assert all(s["module"].startswith("pipeline.") and s["min_inputs"] >= 1 for s in specs)
    assert all(s["label"] and "Category" not in s["label"] for s in specs)
    # every measure has its own budget, and the enabled ones fit inside the total
    raw = __import__("yaml").safe_load(measures.MEASURES_YAML.read_text(encoding="utf-8"))
    assert all("budget_seconds" in e for e in raw)
    assert sum(s["budget_seconds"] for s in specs if s["enabled"]) <= measures.TOTAL_BUDGET_SECONDS


@pytest.mark.parametrize("bad", [
    "- {id: x, module: m}",                                            # no enabled
    "- {id: x, module: m, enabled: true}\n- {id: x, module: n, enabled: true}",   # duplicate
    "- {id: X, module: m, enabled: true}",                            # bad id
    "- {id: x, enabled: true}",                                       # no module
    "- {id: x, module: m, enabled: true, min_inputs: -1}",
    "- {id: x, module: m, enabled: true, budget_seconds: 0}",
    "- {id: x, module: m, enabled: true, budget_seconds: soon}",
    "{id: x}",
])
def test_a_malformed_measures_file_is_refused(tmp_path, bad):
    p = tmp_path / "m.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError):
        measures.load_config(p)
    # with no previous export to fall back on, the export fails loudly rather than drop every measure
    with pytest.raises(RuntimeError, match="measures config failed"):
        measures.run_all_quietly(store.connect(tmp_path / "c.db"), tmp_path / "o", {}, config_path=p)


def test_a_malformed_measures_file_keeps_the_previous_blocks(tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    prev = {"fake": {"status": "checked", "label": "Fake", "file": "fake.json", "reason": "hand check passed",
                     "method_text": "The fake method."}}
    (out / "meta.json").write_text(json.dumps({"measures": prev}), encoding="utf-8")
    (out / "fake.json").write_text("{\"rows\": 1}", encoding="utf-8")
    p = tmp_path / "m.yaml"
    p.write_text("- {id: x, module: m}", encoding="utf-8")
    blocks = measures.run_all_quietly(store.connect(tmp_path / "c.db"), out, {}, config_path=p)
    b = blocks["fake"]
    assert b["status"] == "checked" and b["file"] == "fake.json" and b["config_error"] is True
    assert b["error"].startswith("measures config failed") and b["reason"].startswith("kept from the previous export")
    assert (out / "fake.json").read_text() == "{\"rows\": 1}"                 # its side file still matches it


def test_each_measure_gets_its_budget_and_all_of_them_the_total(tmp_path, monkeypatch):
    seen = {}

    def measure(mid, sleep=0.0):
        m = fake_measure(mid)
        inner = m.compute

        def compute(conn, ctx, deadline):
            seen[mid] = deadline - time.monotonic()
            time.sleep(sleep)
            return inner(conn, ctx, deadline) if deadline > time.monotonic() else {"stopped": True}
        m.compute = compute
        return m
    # Wide margins: a slow runner once spent the whole of a 0.35 second total before the second measure began.
    first, second, third = measure("first", sleep=1.5), measure("second", sleep=2.0), measure("third")
    cfg = _install(monkeypatch, tmp_path, {"m_first": first, "m_second": second, "m_third": third}, entries=[
        {"id": "first", "label": "First", "module": "m_first", "enabled": True, "min_inputs": 1, "budget_seconds": 50},
        {"id": "second", "label": "Second", "module": "m_second", "enabled": True, "min_inputs": 1, "budget_seconds": 0.5},
        {"id": "third", "label": "Third", "module": "m_third", "enabled": True, "min_inputs": 1}])
    out = tmp_path / "o"
    blocks = measures.run_all_quietly(store.connect(tmp_path / "g.db"), out, {}, config_path=cfg, reviews_dir=tmp_path,
                                      total_budget_seconds=3.0)
    assert 2.0 < seen["first"] <= 3.0, "the total ends before the measure's own budget"
    assert seen["second"] <= 0.5
    assert blocks["first"]["status"] == "provisional" and blocks["first"]["budget_seconds"] == 50
    # the total was used up before the third began: not run, off, with the reason
    assert "third" not in seen and blocks["third"]["status"] == "off"
    assert "used up" in blocks["third"]["reason"] and not (out / "third.json").exists()


def test_a_measure_past_its_deadline_is_stopped(tmp_path, monkeypatch):
    if not hasattr(measures.signal, "setitimer"):
        pytest.skip("no interval timer on this platform")
    slow = fake_measure("slow")

    def compute(conn, ctx, deadline):
        try:
            while True:
                time.sleep(0.01)
        except Exception:    # a module's own broad except does not swallow the stop
            return {"swallowed": True}
    slow.compute = compute
    monkeypatch.setattr(measures, "GRACE_SECONDS", 0.2)
    cfg = _install(monkeypatch, tmp_path, {"m_slow": slow}, entries=[
        {"id": "slow", "label": "Slow", "module": "m_slow", "enabled": True, "min_inputs": 1, "budget_seconds": 0.1}])
    t0 = time.monotonic()
    b = measures.run_all_quietly(store.connect(tmp_path / "g.db"), tmp_path / "o", {}, config_path=cfg,
                                 reviews_dir=tmp_path)["slow"]
    assert time.monotonic() - t0 < 5
    assert b["status"] == "off" and "MeasureTimeout" in b["error"] and "past its deadline" in b["error"]
    assert measures.signal.getitimer(measures.signal.ITIMER_REAL) == (0.0, 0.0), "the timer is cleared"


def test_samples_never_overwrite_a_same_day_draw_and_refuse_an_empty_one(tmp_path):
    rows = [{"sample_id": "1", "verdict": ""}]
    paths = [measures.write_sample(tmp_path, "fake", rows, ["sample_id", "verdict"], {}, day="2026-10-20")
             for _ in range(11)]
    names = [c.name for c, _ in paths]
    assert names[0] == "fake_sample_2026-10-20.csv" and names[1] == "fake_sample_2026-10-20_2.csv"
    assert names[10] == "fake_sample_2026-10-20_11.csv" and len(set(names)) == 11
    assert all(k.name == c.name.replace(".csv", ".key.json") and k.exists() for c, k in paths)
    measures.write_sample(tmp_path, "fake", rows, ["sample_id", "verdict"], {}, day="2026-10-19")
    order = [c.name for c, _ in measures.sample_files("fake", tmp_path)]
    assert order[0] == "fake_sample_2026-10-20_11.csv" and order[1] == "fake_sample_2026-10-20_10.csv"
    assert order[-2:] == ["fake_sample_2026-10-20.csv", "fake_sample_2026-10-19.csv"]
    with pytest.raises(ValueError, match="empty"):
        measures.write_sample(tmp_path, "fake", [], ["sample_id"], {}, day="2026-10-21")
    assert not list(tmp_path.glob("fake_sample_2026-10-21*"))


def test_the_command_line_uses_each_measure_s_own_sample_size():
    from pipeline import copies, doctrine, engagement, speed, spikes, syndication
    assert measures.default_sample_n(doctrine) == doctrine.SAMPLE_MAX == 200
    assert measures.default_sample_n(spikes) == spikes.SAMPLE_MAX == 30
    assert measures.default_sample_n(speed) == speed.SAMPLE_N
    # alteration: None, so copies draws its PER_BAND (20) from each of its four bands, as its method promises
    assert measures.default_sample_n(copies) is None and copies.PER_BAND == 20
    assert "20 pairs per band" in copies.methodology_text({"status": "provisional", "summary": {}})
    assert measures.default_sample_n(engagement) is None and measures.default_sample_n(syndication) is None


def test_a_module_must_offer_the_whole_interface(tmp_path, monkeypatch):
    mod = fake_measure()
    del mod.score_check
    cfg = _install(monkeypatch, tmp_path, {"fake_measure_mod": mod})
    b = measures.run_all_quietly(store.connect(tmp_path / "g.db"), tmp_path / "o", {}, config_path=cfg,
                                 reviews_dir=tmp_path)["fake"]
    assert b["status"] == "off" and "score_check" in b["error"]


def test_check_result():
    r = measures.check_result("precision", 0.95, 0.9, 20, 20)
    assert r["passed"] is True and r["complete"] is True
    assert measures.check_result("precision", 0.85, 0.9, 20, 20)["passed"] is False
    assert measures.check_result("precision", 0.95, 0.9, 20, 12)["passed"] is None
    assert measures.check_result("kappa", None, 0.6, 20, 20)["passed"] is None
    assert measures.check_result("precision", 0.95, 0.9, 200, 30, complete=True)["passed"] is True
