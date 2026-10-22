"""Check every feed in the registry, mark dead outlets inactive, and log the change.

Usage:
  python -m pipeline.validate_sources            # report only
  python -m pipeline.validate_sources --apply    # apply the deactivations to outlets.yaml
  python -m pipeline.validate_sources --json out.json

An outlet is dead when every feed fails on this run, at least MIN_FAILED_CHECKS failures are on
record, and the silence has lasted DEAD_AFTER_DAYS: no feed has succeeded since a day more than
DEAD_AFTER_DAYS ago, counted at the earliest from the day the outlet and its current feeds entered
the registry (read from the history of sources/outlets.yaml) or the day it came onto its current
collector (collector_since), whichever is later. The hourly collect job also writes feed health, so
a count of consecutive failures alone would deactivate an outlet after a three hour outage; the rule
is about elapsed time since the last success instead. The first run has no history, so --apply on a
fresh clone only reports.

A dead outlet is deactivated, with the reason: robots.txt disallows every feed, or every feed failed
on every check from the GitHub Actions runner. Until 2026-10-22 an outlet the runner could not
reach was moved instead to a relay on the owner's network (collector: self_hosted). The relay was
retired on 2026-10-22, because collecting from another network works around a site's
choice to refuse the crawler, and its outlets were made inactive. Nothing here moves an outlet to
another collector or makes an inactive outlet active again; an outlet the runner cannot reach is
deactivated like any other dead outlet.
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from pipeline import config, registry
from pipeline.feeds_util import fetch_feed

DEAD_AFTER_DAYS = 7
MIN_FAILED_CHECKS = 3
ROBOTS_ERRORS = ("robots_disallowed",)


def is_dead(results, history, today, started=None):
    """results: this run's per feed results for one outlet. history: feed_url -> row with
    last_ok, total_failures. today: ISO date (UTC). started: ISO date the silence can be measured
    from at the earliest (measured_from), or None when unknown. Returns (dead, last_ok_date or
    None).

    Only recorded history counts toward MIN_FAILED_CHECKS. Counting this run's own failures too
    meant three feeds failing once was already three failures, so a single bad morning could
    deactivate an outlet that had never been seen to fail before.

    A feed that has never succeeded has no last success to measure DEAD_AFTER_DAYS against. Until
    2026-10-17 the elapsed time was read from the count of recorded checks (168, a week of hourly
    polls), but GitHub drops scheduled runs, so the runner checked a feed a median 2.7 hours apart
    and 168 checks took about three weeks; now it is read from started, the day the outlet and its
    current feeds entered the registry or came onto this collector. Without a start the outlet is
    never dead on that rule."""
    if not results or not all(not r["ok"] for r in results):
        return False, None
    last_ok = max((history.get(r["feed_url"], {}).get("last_ok") or "" for r in results), default="")
    failures = sum(history.get(r["feed_url"], {}).get("total_failures", 0) or 0 for r in results)
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=DEAD_AFTER_DAYS)).isoformat()
    if failures < MIN_FAILED_CHECKS:
        return False, last_ok[:10] or None
    since = max(last_ok[:10], (started or "")[:10])
    if not since:
        return False, None
    return since < cutoff, last_ok[:10] or None


# ---------------------------------------------------------------------------
# when an outlet entered the registry
# ---------------------------------------------------------------------------

def _yaml_value(line: str) -> str:
    """The scalar on a registry line: '  - https://x', '  - url: https://x', '- id: x', quotes off."""
    s = line.strip()
    if s.startswith("- "):
        s = s[2:].strip()
    for key in ("url:", "id:"):
        if s.startswith(key):
            s = s[len(key):].strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1]
    return s


def registry_dates(path=None):
    """First day (UTC) each outlet id and each feed url appears in the committed history of the
    registry file: {("id", value) or ("url", value): ISO date}. One git log over the file's history,
    read once per run. None when git cannot read the history (no repository). A shallow clone dates
    everything older than its boundary to the boundary, which only makes outlets look newer."""
    path = path or config.OUTLETS_PATH
    try:
        out = subprocess.run(
            ["git", "log", "--reverse", "--format=commit %cI", "-p", "--unified=0", "--no-renames", "--", str(path)],
            cwd=str(config.ROOT), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return parse_registry_log(out.stdout)


def parse_registry_log(text: str):
    dates = {}
    day = None
    for line in text.splitlines():
        if line.startswith("commit "):
            stamp = line[7:].strip()
            when = dt.datetime.fromisoformat(stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp)
            day = when.astimezone(dt.timezone.utc).date().isoformat()
            continue
        if not line.startswith("+") or line.startswith("+++") or day is None:
            continue
        body = line[1:]
        stripped = body.strip()
        if stripped.startswith("- id:"):
            dates.setdefault(("id", _yaml_value(body)), day)
        elif stripped.startswith(("- http", "- 'http", '- "http', "- url:", "url:")):
            dates.setdefault(("url", _yaml_value(body)), day)
    return dates


def measured_from(outlet, dates, today):
    """The day the outlet's silence is counted from at the earliest: the latest of the day it entered
    the registry, the days its current feeds did, and collector_since. A feed or id not in the
    committed history is new (today). With no history at all only collector_since is known."""
    days = []
    if outlet.get("collector_since"):
        days.append(outlet["collector_since"])
    if dates is not None:
        days.append(dates.get(("id", outlet["id"]), today))
        days.extend(dates.get(("url", u), today) for u in outlet["feeds"])
    return max(days) if days else None


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def check_outlet(outlet):
    results = []
    for url in outlet["feeds"]:
        r = fetch_feed(url)
        results.append({
            "outlet_id": outlet["id"],
            "feed_url": url,
            "ok": r["ok"],
            "status": r["status"],
            "error": r["error"],
            "entries": len(r["entries"]),
            "elapsed": round(r["elapsed"], 2),
        })
    return results


def robots_blocked(results) -> bool:
    return bool(results) and all((r.get("error") or "") in ROBOTS_ERRORS for r in results)


def decide(outlet, results, history, today, started):
    """What to do with one outlet: None (alive, or not dead yet), or ("deactivate", why)."""
    dead, last_ok = is_dead(results, history, today, started)
    if not dead:
        return None
    since = last_ok or started
    if robots_blocked(results):
        return "deactivate", ("robots.txt disallows the crawler for every feed (checked %s; last success %s) (auto)"
                              % (today, last_ok or "never"))
    return "deactivate", (
        ("every feed failed on every check from the GitHub Actions runner for more than %d days; "
         "last success %s (auto)" % (DEAD_AFTER_DAYS, last_ok)) if last_ok else
        ("every feed failed on every check from the GitHub Actions runner, and no feed has succeeded "
         "since %s, more than %d days ago (auto)" % (since, DEAD_AFTER_DAYS)))


def apply_decisions(outlets, decisions, today):
    """decisions: outlet id -> (action, why). Edits the outlets in place; returns the ids deactivated.
    Only an active outlet is touched, and only to deactivate it."""
    deactivated = []
    for o in outlets:
        d = decisions.get(o["id"])
        if not d or not o.get("active"):
            continue
        action, why = d
        if action != "deactivate":
            raise ValueError("unknown validation action %r for %s" % (action, o["id"]))
        o["active"] = False
        o["inactive_reason"] = why
        o["inactive_since"] = today
        deactivated.append(o["id"])
    return deactivated


def run(apply=False, json_out=None, workers=12):
    outlets = registry.load_outlets()
    # UTC, like every other date this project records. last_ok is written in UTC, so comparing it
    # against the runner's local date moved the seven day boundary by up to a day either way.
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    to_check = registry.collectable(outlets)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        nested = list(pool.map(check_outlet, to_check))
    results = [r for group in nested for r in group]

    # Feed health history lives in the database when it exists, so the elapsed time rule can be
    # applied. Without history, report only.
    history = {}
    try:
        from pipeline import store
        conn = store.connect()
        for row in conn.execute(
            "SELECT feed_url, last_ok, total_failures, total_checks FROM feed_health"
        ):
            history[row[0]] = {"last_ok": row[1], "total_failures": row[2], "total_checks": row[3]}
        for r in results:
            store.record_feed_health(conn, r["outlet_id"], r["feed_url"], r["ok"], r["error"], r["entries"])
        conn.commit()
    except Exception as exc:  # store may not exist yet on a fresh checkout
        print("feed health history unavailable (%s); reporting only" % exc, file=sys.stderr)

    dates = registry_dates()
    if dates is None:
        print("registry history unavailable; outlets that never succeeded are only reported", file=sys.stderr)
    by_id = {o["id"]: o for o in outlets}

    decisions = {}
    by_outlet = {}
    for r in results:
        by_outlet.setdefault(r["outlet_id"], []).append(r)
    for oid, rs in by_outlet.items():
        o = by_id[oid]
        d = decide(o, rs, history, today, measured_from(o, dates, today))
        if d:
            decisions[oid] = d

    ok = sum(1 for r in results if r["ok"])
    print("checked %d feeds across %d outlets: %d ok, %d failing" % (
        len(results), len(to_check), ok, len(results) - ok))
    for r in results:
        if not r["ok"]:
            print("  FAIL %-24s %s  (%s)" % (r["outlet_id"], r["feed_url"], r["error"]))

    changed = []
    if apply and decisions:
        changed = apply_decisions(outlets, decisions, today)
        if changed:
            registry.save_outlets(outlets)
            print("marked inactive: %s" % ", ".join(changed))
    elif decisions:
        for oid, (action, why) in sorted(decisions.items()):
            print("would mark inactive with --apply: %s  (%s)" % (oid, why))

    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump({"date": today, "results": results, "newly_inactive": changed,
                       "dead_candidates": sorted(decisions), "decisions": {k: list(v) for k, v in decisions.items()}},
                      fh, indent=1)
    return results, changed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    run(apply=args.apply, json_out=args.json_out, workers=args.workers)
