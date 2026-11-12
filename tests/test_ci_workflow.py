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


def test_collection_is_triggered_every_15_minutes_off_the_hour():
    """GitHub drops most scheduled triggers (7 of about 27 started on 2026-11-08), so four an hour are asked
    for to get about one run an hour; none on the hour, where the queue is longest."""
    crons = [c["cron"] for c in _on(_load("pipeline.yml"))["schedule"]]
    assert "7,22,37,52 * * * *" in crons and "17 * * * *" not in crons
    minutes = [int(m) for m in crons[0].split()[0].split(",")]
    assert len(minutes) == 4 and 0 not in minutes
    assert [b - a for a, b in zip(minutes, minutes[1:])] == [15, 15, 15]


def test_one_run_at_a_time_and_a_run_under_way_is_never_cancelled():
    wf = _load("pipeline.yml")
    assert wf["concurrency"] == {"group": "tracker-database", "cancel-in-progress": False}


def test_the_weekly_check_has_no_trigger_of_its_own_and_runs_when_it_is_due():
    """A run asked to validate at a fixed minute would be replaced by the next trigger before it started
    (one queued run per concurrency group), so the first scheduled collect run of the week validates."""
    wf = _load("pipeline.yml")
    assert not [c["cron"] for c in _on(wf)["schedule"] if c["cron"].split()[4] != "*"]
    requested = wf["jobs"]["pipeline"]["env"]["STAGE_REQUESTED"]
    assert "'validate'" not in requested and "github.event.inputs.stage" in requested     # by hand still
    assert "validate" in _on(wf)["workflow_dispatch"]["inputs"]["stage"]["options"]
    step = _step("Validate too, when the weekly feed check is due")
    assert step["if"] == "env.AUTOMATIC == 'true' && env.STAGE == 'collect' && env.TESTS_FAILED != '1'"
    assert "python -m pipeline.validate_sources --due" in step["run"]
    assert '"due:"*) echo "STAGE=validate" >> "$GITHUB_ENV"' in step["run"]
    assert "::warning" in step["run"]                   # no answer: collect, and say so
    assert "continue-on-error" not in step and "|| answer=" in step["run"]
    # the database is there to be asked, and the answer comes before the collection it shortens
    due = _index("Validate too, when")
    assert _index("Fall back to the snapshot or seed") < due < _index("Discover, fetch, classify")
    assert _index("Collect only, since the tests failed") < due


def test_the_due_answer_is_one_the_workflow_can_read(tmp_path):
    env = {"PATH": __import__("os").environ["PATH"], "TRACKER_DB_PATH": str(tmp_path / "t.db")}
    out = subprocess.run([__import__("sys").executable, "-m", "pipeline.validate_sources", "--due"], cwd=str(ROOT),
                         env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1].startswith("due: ")       # a new database has no check on record


# ---------------------------------------------------------------------------
# the job next and scripts/next_run.py: each run starts the following one
# ---------------------------------------------------------------------------

def _next_run():
    spec = importlib.util.spec_from_file_location("next_run", ROOT / "scripts" / "next_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Done:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _fake_gh(runs, own_created="2026-11-12T10:00:00Z", list_fails=False, dispatch_fails=False):
    calls = []

    def gh(args):
        calls.append(list(args))
        if args[0] == "api" and "/workflows/" in args[1]:
            return _Done(returncode=1, stderr="HTTP 502") if list_fails else _Done(json.dumps({"workflow_runs": runs}))
        if args[0] == "api":
            return _Done(json.dumps({"id": 100, "created_at": own_created}))
        return _Done(returncode=1, stderr="HTTP 403") if dispatch_fails else _Done()
    return gh, calls


def test_a_run_that_ends_well_starts_the_next_one_and_only_that_job_may():
    wf = _load("pipeline.yml")
    assert list(wf["jobs"]) == ["pipeline", "next"]
    job = wf["jobs"]["next"]
    assert job["needs"] == "pipeline" and job["runs-on"] == "ubuntu-24.04"
    # not after a failed or cancelled run, not on a branch, and the owner's variable stops it
    assert job["if"] == "needs.pipeline.result == 'success' && github.ref == 'refs/heads/main' && vars.TRACKER_CHAIN != 'off'"
    assert job["permissions"] == {"contents": "read", "actions": "write"}
    assert wf["permissions"]["actions"] == "read"           # the job that holds the database cannot start a run
    step = job["steps"][-1]
    assert step["run"] == 'python3 scripts/next_run.py --started "$STARTED"'
    assert step["env"] == {"GH_TOKEN": "${{ github.token }}", "STARTED": "${{ needs.pipeline.outputs.started }}"}
    assert "secrets." not in json.dumps(job)
    # the wait is at most the floor, and the job outlasts it
    assert job["timeout-minutes"] * 60 > _next_run().FLOOR_SECONDS
    pipeline = wf["jobs"]["pipeline"]
    assert pipeline["outputs"] == {"started": "${{ steps.clock.outputs.started }}"}
    assert pipeline["steps"][0]["id"] == "clock" and 'started=$(date +%s)' in pipeline["steps"][0]["run"]
    assert pipeline["timeout-minutes"] == 55


def test_a_chained_run_is_treated_as_a_scheduled_one():
    wf = _load("pipeline.yml")
    inputs = _on(wf)["workflow_dispatch"]["inputs"]
    assert inputs["chained"]["type"] == "boolean" and inputs["chained"]["default"] is False
    env = wf["jobs"]["pipeline"]["env"]
    assert env["AUTOMATIC"] == "${{ github.event_name == 'schedule' || github.event.inputs.chained == 'true' }}"
    assert '[ "$AUTOMATIC" = "true" ] && [ "$stage" = "collect" ]' in _step("Pick the stage")["run"]
    assert "GITHUB_EVENT_NAME" not in _step("Pick the stage")["run"]
    # the form's stage defaults to export and a hand run is synchronous unless batch is set, so the
    # dispatch names all three: without them every chained run would export and pay full price
    fields = dict(_next_run().FIELDS)
    assert inputs["stage"]["default"] == "export"
    assert fields == {"stage": "collect", "batch": "true", "chained": "true"}
    assert set(fields) <= set(inputs)


def test_the_wait_ends_an_hour_after_the_run_began():
    n = _next_run()
    assert n.FLOOR_SECONDS == 3600
    assert n.wait_seconds("1000", 1000 + 36 * 60) == 24 * 60
    assert n.wait_seconds(1000, 1000 + 3600) == 0 and n.wait_seconds(1000, 1000 + 9000) == 0
    assert n.wait_seconds("1000", 1000) == 3600
    for unread in ("", None, "soon", "0", "-5", str(10 ** 12)):      # no time, or one in the future
        assert n.wait_seconds(unread, 5000) == n.DEFAULT_WAIT
    assert 0 < n.DEFAULT_WAIT < n.FLOOR_SECONDS


def test_the_next_run_is_dispatched_after_the_wait_as_a_chained_collect_on_the_same_branch():
    n = _next_run()
    gh, calls = _fake_gh([{"id": 100, "status": "in_progress", "created_at": "2026-11-12T10:00:00Z"},
                          {"id": 99, "status": "completed", "created_at": "2026-11-12T09:00:00Z"}])
    slept = []
    env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "100", "GITHUB_REF_NAME": "main"}
    assert n.main(["--started", "1000"], now=lambda: 1000 + 40 * 60, sleep=slept.append, gh=gh, env=env) == 0
    assert slept == [20 * 60]
    assert calls[-1] == ["workflow", "run", "pipeline.yml", "--repo", "o/r", "--ref", "main",
                         "-f", "stage=collect", "-f", "batch=true", "-f", "chained=true"]
    assert len([c for c in calls if c[0] == "workflow"]) == 1


def test_nothing_is_dispatched_when_a_run_is_already_waiting(capsys):
    """GitHub keeps one waiting run per group: a dispatch would cancel a run the owner started by hand."""
    n = _next_run()
    for status in ("pending", "queued", "waiting", "requested"):
        gh, calls = _fake_gh([{"id": 101, "status": status, "event": "workflow_dispatch", "created_at": "2026-11-12T10:20:00Z"},
                              {"id": 100, "status": "in_progress", "created_at": "2026-11-12T10:00:00Z"}])
        assert n.start_next("o/r", "100", "main", gh=gh) == "waiting"
        assert not [c for c in calls if c[0] == "workflow"]
    assert "run 101 is already waiting" in capsys.readouterr().out


def test_a_leftover_queued_run_older_than_this_one_does_not_stop_the_chain():
    """Run 43097088498 sat queued with no jobs from 2026-11-11 while later runs came and went."""
    n = _next_run()
    gh, calls = _fake_gh([{"id": 100, "status": "in_progress", "created_at": "2026-11-12T10:00:00Z"},
                          {"id": 7, "status": "queued", "event": "schedule", "created_at": "2026-11-11T20:36:49Z"}])
    assert n.start_next("o/r", 100, "main", gh=gh) == "dispatched"
    assert n.waiting([{"id": 100, "status": "queued", "created_at": "2026-11-12T10:00:01Z"}], "100", "2026-11-12T10:00:00Z") == []


def test_a_list_that_cannot_be_read_dispatches_anyway_and_a_failed_dispatch_never_fails_the_run(capsys):
    n = _next_run()
    gh, calls = _fake_gh([], list_fails=True)
    assert n.start_next("o/r", "100", "main", gh=gh) == "dispatched"
    assert "::warning title=Next run::could not list the runs (HTTP 502)" in capsys.readouterr().out
    gh, calls = _fake_gh([], dispatch_fails=True)
    env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "100", "GITHUB_REF_NAME": "main"}
    assert n.main(["--started", "1000"], now=lambda: 9999, sleep=lambda s: None, gh=gh, env=env) == 0
    assert "the next run was not dispatched (HTTP 403); the schedule starts the next one" in capsys.readouterr().out

    def broken(args):
        raise OSError("gh is not installed")
    assert n.main(["--started", "x"], now=lambda: 9999, sleep=lambda s: None, gh=broken, env=env) == 0
    assert n.main(["--started", "1000"], now=lambda: 9999, sleep=lambda s: None, gh=gh, env={}) == 0     # no environment
    assert "::warning title=Next run::" in capsys.readouterr().out


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
