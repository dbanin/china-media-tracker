"""The workflows' shape (pipeline.yml, tests.yml) and the export guard (scripts/export_guard.py).

What these pin, from the CI audit of 2026-10-17: the tests run on every push and before collection,
not after a 35 minute collection; both workflows run on a pinned image; the cache key carries the run
attempt and the cache keeps the database's journal; the model key reaches only the steps that call
the model; the stage reads the site data at the branch tip; and an export is refused when the
database is behind the site. Since the relay was retired on 2026-10-22 no step ingests or acknowledges
a relay bundle and no step is given RELAY_BUNDLE_KEY.
"""
import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path

import yaml

from pipeline import config

ROOT = config.ROOT
WORKFLOWS = ROOT / ".github" / "workflows"


def _load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _on(wf):
    return wf.get("on", wf.get(True))          # PyYAML reads the key on as True


def _steps():
    return _load("pipeline.yml")["jobs"]["pipeline"]["steps"]


def _step(prefix):
    found = [s for s in _steps() if (s.get("name") or "").startswith(prefix)]
    assert len(found) == 1, prefix
    return found[0]


def _index(prefix):
    return [i for i, s in enumerate(_steps()) if (s.get("name") or "").startswith(prefix)][0]


def test_tests_workflow_runs_on_push_and_pull_request_on_a_pinned_image():
    wf = _load("tests.yml")
    on = _on(wf)
    assert "push" in on and "pull_request" in on
    assert set(on["push"]["branches-ignore"]) == {"relay", "relay-ack"}    # hourly data pushes
    job = wf["jobs"]["pytest"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert wf["permissions"] == {"contents": "read"}
    py = [s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python")][0]
    assert py["with"]["python-version"] == "3.11"
    assert any(s.get("uses", "").startswith("actions/setup-node") for s in job["steps"])
    assert any("pip install -r requirements.txt" in s.get("run", "") for s in job["steps"])
    assert any("python -m pytest" in s.get("run", "") for s in job["steps"])
    assert "secrets." not in (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")


def test_pipeline_runs_on_a_pinned_image():
    job = _load("pipeline.yml")["jobs"]["pipeline"]
    assert job["runs-on"] == "ubuntu-24.04"


def test_pipeline_tests_run_before_the_database_is_restored_and_collection_starts():
    t = _index("Tests, before")
    assert t < _index("Restore database") < _index("Discover, fetch, classify")
    step = _steps()[t]
    assert step["if"] == "env.STAGE == 'export'" and step["continue-on-error"] is True
    assert "pytest" in step["run"] and "data/tracker.db" in step["run"]   # a test may not leave one behind
    skip = _step("Collect only, since the tests failed")
    assert skip["if"] == "steps.tests.outcome == 'failure'"
    assert "STAGE=collect" in skip["run"] and "TESTS_FAILED=1" in skip["run"]
    fail = _step("Fail the run, since the tests failed")
    assert "TESTS_FAILED" in fail["if"] and "exit 1" in fail["run"]
    assert _index("Save database") < _index("Fail the run, since") < _index("Open or update failure issue")
    # the old test step after collection is gone
    assert not [s for s in _steps() if (s.get("name") or "").startswith("Tests, before anything irreversible")]


def test_cache_key_carries_the_attempt_and_the_journal_travels_with_the_database():
    restore, save = _step("Restore database from the most recent run"), _step("Save database for the next run")
    for s in (restore, save):
        assert s["with"]["key"] == "tracker-db-${{ github.run_id }}-${{ github.run_attempt }}"
        paths = s["with"]["path"].split()
        assert paths == ["data/tracker.db", "data/tracker.db-journal", "data/bodies"]
    assert restore["with"]["restore-keys"] == "tracker-db-"


def test_model_key_reaches_only_the_steps_that_call_the_model():
    wf = _load("pipeline.yml")
    assert "ANTHROPIC_API_KEY" not in wf["jobs"]["pipeline"]["env"]
    holders = sorted(s["name"] for s in _steps() if "ANTHROPIC_API_KEY" in (s.get("env") or {}))
    assert holders == ["Discover, fetch, classify", "Reliability study, by itself when it is due and the budget allows"]
    text = (WORKFLOWS / "pipeline.yml").read_text(encoding="utf-8")
    assert text.count("secrets.ANTHROPIC_API_KEY") == 2


def test_no_step_is_given_the_relay_key():
    """The relay was retired on 2026-10-22 and RELAY_BUNDLE_KEY is never to be added."""
    assert not [s.get("name") for s in _steps() if "RELAY_BUNDLE_KEY" in (s.get("env") or {})]
    assert "RELAY_BUNDLE_KEY" not in (WORKFLOWS / "pipeline.yml").read_text(encoding="utf-8")


def test_stage_reads_the_site_data_at_the_branch_tip():
    run = _step("Pick the stage")["run"]
    assert 'git fetch --quiet origin "$GITHUB_REF_NAME"' in run
    assert 'git show "origin/$GITHUB_REF_NAME:docs/data/meta.json"' in run
    assert 'open("docs/data/meta.json")' not in run


def test_the_workflow_no_longer_ingests_or_acknowledges_the_relay():
    text = (WORKFLOWS / "pipeline.yml").read_text(encoding="utf-8")
    assert "pipeline.relay" not in text
    assert not [s.get("name") for s in _steps() if "relay" in (s.get("name") or "").lower()]
    assert not [s.get("name") for s in _steps() if "pipeline.relay" in (s.get("run") or "")]


def test_export_guard_runs_before_the_export():
    guard = _step("Refuse to export a database behind the site")
    assert guard["if"] == "env.STAGE == 'export'"
    assert "scripts/export_guard.py --ref \"origin/$GITHUB_REF_NAME\"" in guard["run"]
    assert _index("Discover, fetch, classify") < _index("Refuse to export") < _index("Export, rollups")


def test_validate_runs_fit_inside_the_job_timeout():
    job = _load("pipeline.yml")["jobs"]["pipeline"]
    validate = _step("Validate every feed")
    assert validate["timeout-minutes"] <= 15
    run = _step("Discover, fetch, classify")["run"]
    assert '[ "$STAGE" = "validate" ] && BUDGET=20' in run and "--budget-minutes $BUDGET" in run
    # setup and restore (about 3), a 20 minute collection with its overrun, the capped check
    assert 3 + 20 + 5 + validate["timeout-minutes"] < job["timeout-minutes"]


def test_workflows_have_no_dashes_of_the_wrong_kind():
    for name in ("pipeline.yml", "tests.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "\u2013" not in text and "\u2014" not in text


# ---------------------------------------------------------------------------
# scripts/export_guard.py
# ---------------------------------------------------------------------------

def _guard():
    spec = importlib.util.spec_from_file_location("export_guard", ROOT / "scripts" / "export_guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db(path, outlet_days, junk=None):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE daily_outlet_discovery (date TEXT, outlet_id TEXT, country TEXT, discovered INTEGER, "
                 "gate_relevant INTEGER)")
    conn.executemany("INSERT INTO daily_outlet_discovery VALUES (?, ?, 'ITA', ?, 0)",
                     [("2026-10-%02d" % (i + 1), "o", n) for i, n in enumerate(outlet_days)])
    if junk is not None:
        conn.execute("CREATE TABLE junk_discovery (date TEXT, outlet_id TEXT, country TEXT, discovered INTEGER, "
                     "gate_relevant INTEGER, updated_at TEXT)")
        conn.execute("INSERT INTO junk_discovery VALUES ('2026-10-01', 'o', 'ITA', ?, 0, '')", (junk,))
    conn.commit()
    conn.close()
    return str(path)


def _meta(path, total):
    path.write_text(json.dumps({"articles_discovered": total}), encoding="utf-8")
    return str(path)


def test_guard_lets_an_export_through_when_the_database_is_level_or_ahead(tmp_path):
    g = _guard()
    db = _db(tmp_path / "t.db", [600, 400])
    assert g.check(db, None, _meta(tmp_path / "m.json", 1000))[0] is True
    assert g.check(db, None, _meta(tmp_path / "m.json", 900))[0] is True


def test_guard_refuses_a_database_restored_from_an_old_seed(tmp_path):
    g = _guard()
    db = _db(tmp_path / "t.db", [100])
    ok, msg = g.check(db, None, _meta(tmp_path / "m.json", 1000))
    assert ok is False and "100" in msg and "1000" in msg
    assert g.main(["--db", db, "--meta", str(tmp_path / "m.json")]) == 1
    # a missing database is a fresh one: nothing to export over the site's totals
    assert g.check(str(tmp_path / "none.db"), None, str(tmp_path / "m.json"))[0] is False


def test_guard_allows_a_small_drop_and_counts_the_junk_ledger(tmp_path):
    g = _guard()
    meta = _meta(tmp_path / "m.json", 100000)
    assert g.check(_db(tmp_path / "a.db", [99600]), None, meta)[0] is True        # 0.4 percent
    assert g.check(_db(tmp_path / "b.db", [99000]), None, meta)[0] is False       # 1 percent
    assert g.check(str(tmp_path / "b.db"), None, meta, max_drop=0.02)[0] is True
    # a junk feed correction moved 5000 discoveries from the outlet rows to the ledger
    assert g.check(_db(tmp_path / "c.db", [95000], junk=5000), None, meta)[0] is True


def test_guard_passes_when_the_site_has_no_total(tmp_path):
    g = _guard()
    (tmp_path / "m.json").write_text("{}", encoding="utf-8")
    assert g.check(_db(tmp_path / "t.db", [1]), None, str(tmp_path / "m.json"))[0] is True
    assert g.check(_db(tmp_path / "u.db", [1]), None, str(tmp_path / "absent.json"))[0] is True


def test_guard_reads_the_total_at_the_branch_tip(tmp_path, monkeypatch):
    g = _guard()
    repo = tmp_path / "repo"
    (repo / "docs" / "data").mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args], cwd=str(repo),
                       check=True, capture_output=True)
    git("init", "--quiet")
    _meta(repo / "docs" / "data" / "meta.json", 5000)
    git("add", "docs/data/meta.json")
    git("commit", "--quiet", "-m", "site")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    _meta(repo / "docs" / "data" / "meta.json", 10)      # the checked out file is older than the tip
    monkeypatch.chdir(repo)
    db = _db(tmp_path / "t.db", [1000])
    ok, msg = g.check(db, "origin/main")
    assert ok is False and "origin/main:docs/data/meta.json" in msg
    assert g.check(db, None)[0] is True                  # the checked out file alone would let it through
    assert g.check(db, "origin/missing")[0] is True      # an unknown ref falls back to the file
