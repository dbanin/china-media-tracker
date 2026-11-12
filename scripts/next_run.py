"""Start the workflow's next run, an hour after this one began.

GitHub's scheduler starts five to eight runs of the pipeline a day whatever the cron asks for: 24 triggers a
day until 2026-11-11 and 96 a day since gave the same count. Feeds were therefore read every four to eight
hours and the busy ones listed more new items between reads than they hold. So a run that ends well starts the
next one itself (the job next in .github/workflows/pipeline.yml), with the token GitHub gives every run: a
workflow_dispatch is one of the two events that token is allowed to raise. The cron stays and restarts the
chain whenever it breaks.

In order:
  1. Wait until FLOOR_SECONDS after the run's pipeline job began (--started, a Unix time). The wait holds the
     workflow's concurrency group, so no run begins sooner, scheduled or started by hand: feeds are read at
     most once an hour, which is what the cron asked for before GitHub stopped honouring it.
  2. List the workflow's runs. When one created after this run is already waiting (a scheduled trigger GitHub
     did start, or a run the owner started by hand), dispatch nothing: GitHub keeps one waiting run per
     concurrency group and a dispatch would cancel that one. It carries the chain on when it ends.
  3. Otherwise dispatch the workflow on the same branch with the FIELDS below.

It never fails the run. A start time that cannot be read waits DEFAULT_WAIT, a list that cannot be read
dispatches anyway, and a dispatch that fails prints a warning and leaves the restart to the cron.

Usage (the job sets GH_TOKEN, and GitHub sets GITHUB_REPOSITORY, GITHUB_RUN_ID and GITHUB_REF_NAME):
  python3 scripts/next_run.py --started 1794500000
"""
import argparse
import json
import os
import subprocess
import sys
import time

WORKFLOW = "pipeline.yml"
FLOOR_SECONDS = 3600
DEFAULT_WAIT = 1200
# stage: the form's default is export, and a chained run must collect (it still exports when the site data is
# stale and validates when the weekly check is due, as a scheduled run does). batch: the model stage at half
# price, as on a scheduled run. chained: tells the workflow to treat the run as a scheduled one.
FIELDS = (("stage", "collect"), ("batch", "true"), ("chained", "true"))
NOT_WAITING = ("completed", "in_progress")


def wait_seconds(started, now, floor=FLOOR_SECONDS):
    """Seconds left until floor after started; DEFAULT_WAIT when started is not a time at or before now."""
    try:
        began = float(started)
    except (TypeError, ValueError):
        return DEFAULT_WAIT
    if began <= 0 or began > now + 60:
        return DEFAULT_WAIT
    return int(max(0, min(floor, began + floor - now)))


def waiting(runs, own_id, own_created):
    """Runs of the workflow that wait behind this one. Only a run created after this one can: the group's one
    waiting place goes to the newest run, so an older run still listed as queued is a leftover (run
    43097088498 sat queued with no jobs from 2026-11-11) and must not stop the chain."""
    return [r for r in runs
            if str(r.get("id")) != str(own_id) and r.get("status") not in NOT_WAITING
            and (r.get("created_at") or "") > own_created]


def _gh(args):
    return subprocess.run(["gh"] + list(args), capture_output=True, text=True, timeout=120)


def _warn(text):
    print("::warning title=Next run::%s" % text, flush=True)


def start_next(repo, run_id, ref, gh=_gh):
    """Dispatch the next run unless one is waiting. Returns "dispatched", "waiting" or "failed"."""
    try:
        own = gh(["api", "repos/%s/actions/runs/%s" % (repo, run_id)])
        listed = gh(["api", "repos/%s/actions/workflows/%s/runs?per_page=50" % (repo, WORKFLOW)])
        if own.returncode or listed.returncode:
            raise ValueError((own.stderr or listed.stderr or "").strip()[:200])
        behind = waiting(json.loads(listed.stdout)["workflow_runs"], run_id, json.loads(own.stdout)["created_at"])
    except Exception as e:                    # the next run matters more than the one a dispatch might replace
        _warn("could not list the runs (%s); dispatching without knowing whether one is waiting" % e)
        behind = []
    if behind:
        print("run %s is already waiting (%s); it goes next and nothing is dispatched" % (
            behind[0].get("id"), behind[0].get("event")), flush=True)
        return "waiting"
    args = ["workflow", "run", WORKFLOW, "--repo", repo, "--ref", ref]
    for key, value in FIELDS:
        args += ["-f", "%s=%s" % (key, value)]
    try:
        sent = gh(args)
        if sent.returncode:
            raise ValueError((sent.stderr or "").strip()[:200])
    except Exception as e:
        _warn("the next run was not dispatched (%s); the schedule starts the next one" % e)
        return "failed"
    print("dispatched the next run on %s" % ref, flush=True)
    return "dispatched"


def main(argv=None, now=time.time, sleep=time.sleep, gh=_gh, env=os.environ):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--started", default="", help="Unix time at which this run's pipeline job began")
    args = ap.parse_args(argv)
    try:
        wait = wait_seconds(args.started, now())
        print("waiting %d seconds, to %d minutes after this run began" % (wait, FLOOR_SECONDS // 60), flush=True)
        sleep(wait)
        start_next(env["GITHUB_REPOSITORY"], env["GITHUB_RUN_ID"], env["GITHUB_REF_NAME"], gh=gh)
    except Exception as e:
        _warn("the next run was not started (%s); the schedule starts the next one" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
