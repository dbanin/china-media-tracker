"""The stale copy guard (pipeline/freshness.py): how old a database or an export is, told by its newest
discovery, and every local tool that draws a sample refusing a copy more than two days old while the tools
that report findings warn about it loudly, naming the date and scripts/db_pull.sh. On synthetic databases,
and, where they are present, on the runner's snapshot (TRACKER_TEST_SNAPSHOT) and the stale local
data/tracker.db, both opened read only."""
import datetime as dt
import json
import os
import sqlite3
from pathlib import Path

import pytest

from pipeline import agreement, config, freshness, store

NOW = dt.datetime(2026, 11, 24, 14, 0, tzinfo=dt.timezone.utc)
OLD = "2026-10-12T21:04:00+00:00"          # the local copy's newest discovery
ROOT = Path(__file__).resolve().parent.parent
_REAL_CONNECT = store.connect


def _db(path: Path, newest: str = OLD) -> sqlite3.Connection:
    conn = store.connect(path)
    for i, t in enumerate(("2026-10-01T08:00:00+00:00", newest)):
        conn.execute("""INSERT INTO articles(url, url_hash, outlet_id, country, language, discovered_at, status, gate_relevant)
                        VALUES (?,?,?,?,?,?,?,?)""", ("https://a.test/%d" % i, "h%d" % i, "a", "AUS", "en", t, "classified", 1))
    conn.commit()
    return conn


def test_the_age_of_a_database_is_its_newest_discovery(tmp_path):
    conn = _db(tmp_path / "old.db")
    info = freshness.database_age(conn, NOW)
    assert info["newest"] == OLD and info["stale"] and abs(info["age_days"] - 42.70) < 0.01
    assert info["source"].endswith("old.db")
    fresh = _db(tmp_path / "fresh.db", newest="2026-11-23T20:00:00+00:00")
    assert not freshness.database_age(fresh, NOW)["stale"]
    assert freshness.database_age(fresh, NOW, max_age_days=0.5)["stale"]
    empty = store.connect(tmp_path / "empty.db")
    assert freshness.database_age(empty, NOW) == dict(freshness.database_age(empty, NOW), newest=None, age_days=None, stale=False)
    assert freshness.newest_discovery(None) is None and freshness.newest_discovery(sqlite3.connect(":memory:")) is None


def test_a_stale_copy_is_warned_about_or_refused_naming_the_date_and_the_pull(tmp_path, capsys):
    conn = _db(tmp_path / "old.db")
    assert freshness.guard(conn, "a report", now=NOW) is True
    err = capsys.readouterr().err
    assert err.startswith("WARNING: STALE COPY, a report\n")
    assert "newest discovery 2026-10-12 21:04 UTC, 42.7 days before now (the limit is 2 days)" in err
    assert "as it stood on 2026-10-12" in err and "not a pause in collection" in err
    assert "scripts/db_pull.sh" in err and "db-snapshot" in err
    assert freshness.guard(conn, "a sample", refuse=True, now=NOW) is False
    err = capsys.readouterr().err
    assert err.startswith("NOT DRAWN: a sample refuses a stale copy") and "Nothing was written" in err
    fresh = _db(tmp_path / "fresh.db", newest="2026-11-24T13:21:43+00:00")
    assert freshness.guard(fresh, "a sample", refuse=True, now=NOW) is True and capsys.readouterr().err == ""
    assert freshness.guard(store.connect(tmp_path / "empty.db"), "a sample", refuse=True, now=NOW) is True
    for text in (freshness.message(freshness.database_age(conn, NOW), "x", refused=True),
                 freshness.message(freshness.export_age({"generated_at": OLD}, NOW), "x", export=True)):
        assert "\u2013" not in text and "\u2014" not in text


def test_an_export_is_aged_by_its_newest_discovery_or_else_its_generation(capsys):
    m = {"generated_at": "2026-11-24T13:32:10+00:00", "last_discovered": "2026-11-20T10:00:00+00:00"}
    info = freshness.export_age(m, NOW)
    assert info["stale"] and info["basis"] == "newest discovery" and info["newest"].startswith("2026-11-20T10:00")
    old = freshness.export_age({"generated_at": OLD}, NOW)
    assert old["stale"] and old["basis"] == "export time"
    assert not freshness.export_age({"generated_at": "2026-11-24T02:00:00+00:00"}, NOW)["stale"]
    assert not freshness.export_age({}, NOW)["stale"] and not freshness.export_age(None, NOW)["stale"]
    freshness.guard_export({"generated_at": OLD}, "numbers_sheet", source="docs/data/meta.json", now=NOW)
    err = capsys.readouterr().err
    assert "docs/data/meta.json: export time 2026-10-12 21:04 UTC" in err and "git pull" in err


# --- every tool that draws a sample refuses, every tool that reports warns ----------------------------------

@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A database whose newest discovery is the local copy's, which every tool opens."""
    path = tmp_path / "old.db"
    conn = _db(path)
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(store, "connect", lambda p=None, *a, **k: _REAL_CONNECT(Path(p) if p else path))
    return path


def test_agreement_refuses_every_draw_and_warns_on_compute(old_db, tmp_path, monkeypatch, capsys):
    for argv in (["sample", "--out", str(tmp_path / "h.csv")], ["sample", "--n", "5", "--out", str(tmp_path / "p.csv")],
                 ["sample", "--since-prompt", "--out", str(tmp_path / "s.csv")]):
        with pytest.raises(SystemExit) as e:
            agreement.main(argv)
        assert e.value.code == 1 and "NOT DRAWN: agreement sample" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.csv"))
    monkeypatch.setattr(agreement, "cmd_compute", lambda *a, **k: {"n": 0})
    agreement.main(["compute", "--csv", str(tmp_path / "x.csv")])
    assert "WARNING: STALE COPY, agreement compute" in capsys.readouterr().err


def test_since_prompt_ages_the_copy_by_discovery_not_by_the_stopped_model_stage(tmp_path, capsys):
    """The model stage stopped at the provider's usage limit on 2026-11-19 while collection went on: the
    runner's own snapshot of 2026-11-24 has model labels five days old and must still be drawable."""
    conn = _db(tmp_path / "runner.db", newest=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)).isoformat())
    conn.execute("""INSERT INTO classifications(article_id, method, category, confidence, ruleset_version, classified_at,
                    model_version) VALUES (1, 'llm', 'A', 1.0, 't', '2026-11-19T05:59:33+00:00', 'm')""")
    conn.commit()
    res = agreement.cmd_human_sample(conn, tmp_path / "s.csv", since_prompt="2099-01-01")
    assert res["refused"] == "empty_pool"           # past the age check, to the (empty) pool
    old = _db(tmp_path / "old.db")
    res = agreement.cmd_human_sample(old, tmp_path / "s2.csv", since_prompt="2026-01-01")
    assert res["refused"] == "stale_database" and res["age_days"] > 2


def test_recall_and_gate_audit_samples_are_refused_and_the_description_warned(old_db, tmp_path, monkeypatch, capsys):
    from pipeline import gate_audit, recall_audit
    with pytest.raises(SystemExit):
        recall_audit.main(["sample", "--out", str(tmp_path / "r.csv")])
    assert "NOT DRAWN: recall_audit sample" in capsys.readouterr().err and not (tmp_path / "r.csv").exists()
    monkeypatch.setattr(recall_audit, "cmd_describe", lambda conn, bodies=None: None)
    recall_audit.main(["describe"])
    assert "WARNING: STALE COPY, recall_audit describe" in capsys.readouterr().err
    monkeypatch.setattr(recall_audit, "cmd_compute", lambda conn, csv: None)
    recall_audit.main(["compute", "--csv", "x.csv"])
    assert "WARNING: STALE COPY, recall_audit compute" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        gate_audit.main(["sample", "--out", str(tmp_path / "g.csv")])
    assert "NOT DRAWN: gate_audit sample" in capsys.readouterr().err and not (tmp_path / "g.csv").exists()


def test_model_only_and_second_rater_samples_are_refused_before_anything_is_drawn(old_db, tmp_path, monkeypatch, capsys):
    from pipeline import model_only, second_rater
    with pytest.raises(SystemExit):
        model_only.main(["sample", "--out", str(tmp_path / "m")])
    assert "NOT DRAWN: model_only sample" in capsys.readouterr().err and not (tmp_path / "m").exists()
    monkeypatch.setattr(second_rater, "rejudge", lambda *a, **k: pytest.fail("no model call on a stale copy"))
    with pytest.raises(SystemExit):
        second_rater.main(["sample", "--out", str(tmp_path / "s.json")])
    assert "NOT DRAWN: second_rater sample" in capsys.readouterr().err and not (tmp_path / "s.json").exists()


def test_the_finding_reports_warn_on_a_stale_copy(old_db, tmp_path, monkeypatch, capsys):
    from pipeline import engagement, hosted_travel, planted
    assert planted.main(["recurring"]) == 0
    out = capsys.readouterr()
    assert "WARNING: STALE COPY, planted recurring" in out.err and json.loads(out.out)["rows"] == []
    assert planted.main(["pending"]) == 0
    assert "WARNING: STALE COPY, planted pending" in capsys.readouterr().err
    assert hosted_travel.main(["list"]) == 0
    assert "WARNING: STALE COPY, hosted_travel list" in capsys.readouterr().err
    assert engagement.main(["urls", "--since", "2026-10-01", "--until", "2026-10-12", "--db", str(old_db),
                            "--out", str(tmp_path / "e")]) == 0
    assert "WARNING: STALE COPY, engagement urls" in capsys.readouterr().err


def _export_dir(tmp_path, generated_at):
    src = ROOT / "docs" / "data"
    d = tmp_path / "data"
    d.mkdir()
    for name in ("meta.json", "latest.json"):
        j = json.loads((src / name).read_text(encoding="utf-8"))
        j["generated_at"] = generated_at
        j.pop("last_discovered", None)
        (d / name).write_text(json.dumps(j), encoding="utf-8")
    return d


def test_numbers_sheet_and_country_export_warn_on_a_stale_export(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("numbers_sheet_t", ROOT / "scripts" / "numbers_sheet.py")
    ns = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ns)
    monkeypatch.setattr(ns, "stamp_problems", lambda ex: [])
    monkeypatch.setattr(ns, "build", lambda ex: "sheet\n")
    d = _export_dir(tmp_path, OLD)
    assert ns.main(["--data", str(d), "--out", str(tmp_path / "s.md")]) == 0
    err = capsys.readouterr().err
    assert "WARNING: STALE COPY, numbers_sheet" in err and "export time 2026-10-12 21:04 UTC" in err and "git pull" in err
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _export_dir(fresh, dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat())
    assert ns.main(["--data", str(fresh / "data"), "--out", str(tmp_path / "s2.md")]) == 0
    assert "STALE" not in capsys.readouterr().err

    spec = importlib.util.spec_from_file_location("country_export_t", ROOT / "scripts" / "country_export.py")
    ce = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ce)
    monkeypatch.setattr(ce, "build", lambda *a, **k: {"files": [], "out": "x"})
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    _export_dir(repo / "docs", OLD)
    ce.main(["--repo", str(repo)])
    assert "WARNING: STALE COPY, country_export" in capsys.readouterr().err


# --- the real copies, opened read only ------------------------------------------------------------------------

def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


@pytest.mark.skipif(os.environ.get("GITHUB_ACTIONS") == "true" or not os.environ.get("TRACKER_TEST_SNAPSHOT")
                    or not Path(os.environ.get("TRACKER_TEST_SNAPSHOT", "")).exists(),
                    reason="the runner's snapshot is named by TRACKER_TEST_SNAPSHOT on the owner's machine only")
def test_on_the_runners_snapshot_a_draw_goes_ahead_and_two_days_on_it_would_not(capsys):
    conn = _ro(os.environ["TRACKER_TEST_SNAPSHOT"])
    newest = freshness.newest_discovery(conn)
    assert newest is not None
    assert freshness.guard(conn, "a sample", refuse=True, now=newest + dt.timedelta(hours=6)) is True
    assert freshness.guard(conn, "a sample", refuse=True, now=newest + dt.timedelta(days=2, hours=1)) is False
    assert newest.date().isoformat() in capsys.readouterr().err


@pytest.mark.skipif(os.environ.get("GITHUB_ACTIONS") == "true" or not (ROOT / "data" / "tracker.db").exists(),
                    reason="the stale local copy exists on the owner's machine only")
def test_the_stale_local_copy_is_refused(capsys):
    conn = _ro(ROOT / "data" / "tracker.db")
    info = freshness.database_age(conn)
    if not info["stale"]:
        pytest.skip("the local copy has been pulled since")
    assert freshness.guard(conn, "a sample", refuse=True) is False
    assert info["newest"][:10] in capsys.readouterr().err
