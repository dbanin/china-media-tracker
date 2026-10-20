"""Check every feed in the registry. Hand runner-only failures to the relay, mark dead outlets
inactive, and log the change.

Usage:
  python -m pipeline.validate_sources            # report only
  python -m pipeline.validate_sources --apply    # apply the moves and deactivations to outlets.yaml
  python -m pipeline.validate_sources --json out.json
  python -m pipeline.validate_sources --relay-db data/relay.db   # read the relay's feed health locally

An outlet is dead to a collector when every feed fails on this run, at least MIN_FAILED_CHECKS
failures are on record, and the silence has lasted DEAD_AFTER_DAYS: no feed has succeeded since a
day more than DEAD_AFTER_DAYS ago, counted at the earliest from the day the outlet and its current
feeds entered the registry (read from the history of sources/outlets.yaml) or the day it came onto
its current collector (collector_since), whichever is later. The hourly collect job also writes feed
health, so a count of consecutive failures alone would deactivate an outlet after a three hour
outage; the rule is about elapsed time since the last success instead. The first run has no
history, so --apply on a fresh clone only reports.

What happens to a dead outlet depends on who cannot reach it:
  - robots.txt disallows every feed: deactivated, since no network changes what the site asks;
  - the GitHub Actions runner cannot reach it (collector hosted, or both while the relay is not
    seen reaching it either): moved to the relay (collector: self_hosted, collector_since today),
    never deactivated, because many sites refuse the runner's addresses and nothing else;
  - collector both and the relay reaches it: moved to the relay as well;
  - the relay on the owner's network cannot reach it either (judged from the relay's own feed
    health: the relay database with --relay-db, or the feed health records of the relay bundle on
    the runner): deactivated.
Outlets only the relay polls were never evaluated before 2026-10-17; they are now judged from the
relay's feed health, and only while that health is fresh (RELAY_FRESH_DAYS), so a relay that is
asleep or down deactivates nothing.
"""
import argparse
import datetime as dt
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from pipeline import config, registry
from pipeline.feeds_util import fetch_feed

DEAD_AFTER_DAYS = 7
MIN_FAILED_CHECKS = 3
# Relay feed health is used only while it is this recent: older rows mean the relay has stopped
# polling (the owner's machine asleep or the relay down), which says nothing about the outlet.
RELAY_FRESH_DAYS = 2
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
# the relay's feed health
# ---------------------------------------------------------------------------

def relay_health_from_db(path):
    """feed_url -> feed health row, from the relay database opened read-only."""
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return {r["feed_url"]: dict(r) for r in conn.execute("SELECT * FROM feed_health")}
    finally:
        conn.close()


def relay_health_from_bundle(data: bytes):
    """feed_url -> feed health row, from the feed_health records of a relay bundle. Every bundle
    carries the relay's whole feed_health table, so the latest bundle is the relay's latest view."""
    from pipeline import relay
    rows = {}
    for item in relay.iter_bundle(data):
        if item.get("_type") == "feed_health":
            rows[item["feed_url"]] = {k: v for k, v in item.items() if k != "_type"}
    return rows


def load_relay_health(relay_db=None):
    """(rows, source): the relay database when given, else the latest bundle on the relay branch.
    ({}, None) when neither can be read."""
    try:
        if relay_db:
            return relay_health_from_db(relay_db), "relay database %s" % relay_db
        from pipeline import relay
        data = relay.fetch_bundle()
        if data:
            return relay_health_from_bundle(data), "relay bundle"
    except Exception as exc:  # noqa: BLE001  without relay health the relay outlets are only reported
        print("relay feed health unavailable (%s)" % exc, file=sys.stderr)
    return {}, None


def _fresh(row, today) -> bool:
    checked = (row or {}).get("last_checked") or ""
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=RELAY_FRESH_DAYS)).isoformat()
    return bool(checked) and checked[:10] >= cutoff


def relay_results(outlet, rows, today):
    """The relay's latest check of each feed, shaped like check_outlet's results, or None when the
    relay has no fresh row for every feed (it has not polled them yet, or has stopped polling)."""
    out = []
    for url in outlet["feeds"]:
        row = rows.get(url)
        if not _fresh(row, today):
            return None
        out.append({"outlet_id": outlet["id"], "feed_url": url, "ok": not (row.get("consecutive_failures") or 0),
                    "status": None, "error": row.get("last_error"), "entries": row.get("last_entries") or 0,
                    "elapsed": 0.0})
    return out


def relay_reaches(outlet, rows, today) -> bool:
    """The relay has had a success on one of the outlet's feeds within DEAD_AFTER_DAYS."""
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=DEAD_AFTER_DAYS)).isoformat()
    return any(((rows.get(u) or {}).get("last_ok") or "")[:10] >= cutoff for u in outlet["feeds"])


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


def decide(outlet, results, history, today, started, collector, relay_rows=None, judged_by=None):
    """What to do with one outlet: None (alive, or not dead yet), or (action, why) with action
    "move" (to the relay) or "deactivate". judged_by: "relay" when results and history are the
    relay's own feed health, else this collector's probes."""
    dead, last_ok = is_dead(results, history, today, started)
    if not dead:
        return None
    since = last_ok or started
    if robots_blocked(results):
        return "deactivate", ("robots.txt disallows the crawler for every feed (checked %s; last success %s) (auto)"
                              % (today, last_ok or "never"))
    if judged_by == "relay" or collector != registry.HOSTED:
        return "deactivate", (
            ("every feed failed on every check from the relay on the owner's network for more than %d days; "
             "last success %s (auto)" % (DEAD_AFTER_DAYS, last_ok)) if last_ok else
            ("every feed failed on every check from the relay on the owner's network, and no feed has succeeded "
             "since %s, more than %d days ago (auto)" % (since, DEAD_AFTER_DAYS)))
    # The hosted runner cannot reach it. A shared outlet the relay also fails on, with fresh relay
    # health, is dead everywhere; anything else goes to the relay, which judges it in its turn.
    if registry.is_shared(outlet) and relay_rows is not None:
        rr = relay_results(outlet, relay_rows, today)
        if rr is not None and not relay_reaches(outlet, relay_rows, today):
            relay_dead, relay_last = is_dead(rr, relay_rows, today, started)
            if relay_dead:
                return "deactivate", (
                    "every feed failed on every check from both the GitHub Actions runner and the relay on the "
                    "owner's network for more than %d days; last success %s (auto)" % (DEAD_AFTER_DAYS, relay_last or last_ok or "never"))
    return "move", ("every feed failed on every check from the GitHub Actions runner for more than %d days "
                    "(last success %s), so it goes to the relay on the owner's network, which judges it in its turn (auto)"
                    % (DEAD_AFTER_DAYS, last_ok or "never, counted from %s" % since))


def apply_decisions(outlets, decisions, today):
    """decisions: outlet id -> (action, why). Edits the outlets in place; returns (moved, deactivated)."""
    moved, deactivated = [], []
    for o in outlets:
        d = decisions.get(o["id"])
        if not d or not o.get("active"):
            continue
        action, why = d
        if action == "move":
            o["collector"] = registry.SELF_HOSTED
            o["collector_since"] = today
            note = "Moved to the relay (collector: self_hosted) %s: %s." % (today, why)
            o["notes"] = ("%s %s" % (o["notes"].rstrip(), note)) if o.get("notes") else note
            moved.append(o["id"])
        else:
            o["active"] = False
            o["inactive_reason"] = why
            o["inactive_since"] = today
            deactivated.append(o["id"])
    return moved, deactivated


def run(apply=False, json_out=None, workers=12, relay_db=None):
    outlets = registry.load_outlets()
    collector = config.COLLECTOR
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
    relay_rows, relay_source = ({}, None)
    remote = [o for o in outlets if o.get("active") and not registry.polled_by(o, collector)]
    if collector == registry.HOSTED:
        relay_rows, relay_source = load_relay_health(relay_db)
    by_id = {o["id"]: o for o in outlets}

    decisions = {}
    by_outlet = {}
    for r in results:
        by_outlet.setdefault(r["outlet_id"], []).append(r)
    for oid, rs in by_outlet.items():
        o = by_id[oid]
        d = decide(o, rs, history, today, measured_from(o, dates, today), collector,
                   relay_rows if relay_source else None)
        if d:
            decisions[oid] = d
    relay_judged, relay_unjudged = [], []
    for o in remote:
        rr = relay_results(o, relay_rows, today) if relay_source else None
        if rr is None:
            relay_unjudged.append(o["id"])
            continue
        relay_judged.append(o["id"])
        d = decide(o, rr, relay_rows, today, measured_from(o, dates, today), collector, judged_by="relay")
        if d:
            decisions[o["id"]] = d

    ok = sum(1 for r in results if r["ok"])
    print("checked %d feeds across %d outlets: %d ok, %d failing" % (
        len(results), len(to_check), ok, len(results) - ok))
    for r in results:
        if not r["ok"]:
            print("  FAIL %-24s %s  (%s)" % (r["outlet_id"], r["feed_url"], r["error"]))
    if remote:
        print("relay outlets: %d judged from %s, %d without fresh relay health" % (
            len(relay_judged), relay_source or "nothing", len(relay_unjudged)))

    moved, changed = [], []
    if apply and decisions:
        moved, changed = apply_decisions(outlets, decisions, today)
        if moved or changed:
            registry.save_outlets(outlets)
        if moved:
            print("moved to the relay: %s" % ", ".join(moved))
        if changed:
            print("marked inactive: %s" % ", ".join(changed))
    elif decisions:
        for oid, (action, why) in sorted(decisions.items()):
            print("would %s with --apply: %s  (%s)" % ("move to the relay" if action == "move" else "mark inactive", oid, why))

    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump({"date": today, "results": results, "newly_inactive": changed, "moved_to_relay": moved,
                       "dead_candidates": sorted(decisions), "decisions": {k: list(v) for k, v in decisions.items()},
                       "relay_health_source": relay_source, "relay_judged": relay_judged,
                       "relay_unjudged": relay_unjudged}, fh, indent=1)
    return results, changed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--relay-db", dest="relay_db", help="the relay's database, read-only, for the relay outlets' health")
    args = ap.parse_args()
    run(apply=args.apply, json_out=args.json_out, workers=args.workers, relay_db=args.relay_db)
