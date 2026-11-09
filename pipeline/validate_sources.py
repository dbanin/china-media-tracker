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

The check has a deadline (DEADLINE_MINUTES, --deadline-minutes) well inside its workflow step's 15
minutes. No outlet is started after it, and the outlets it leaves are reported and never judged. Each
outlet's feed health is saved as soon as its check is done, so a run stopped early keeps what it did.
Outlets are checked least recently validated first (outlet_validation, then feed_health.last_checked),
so consecutive partial weeks cover the whole registry. An outlet whose only work left is a long
robots.txt Crawl-delay sleep does not hold the run either (fetch_feeds._arrivals, the patience rule of
discovery); it is reported as left asleep and comes first the next week.
"""
import argparse
import collections
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pipeline import config, news_sitemap, registry
from pipeline.feeds_util import fetch_feed

DEAD_AFTER_DAYS = 7
MIN_FAILED_CHECKS = 3
ROBOTS_ERRORS = ("robots_disallowed",)
# The step that runs the check is capped at 15 minutes (pipeline.yml, after a 20 minute collection) and
# the 2026-11-02 check was killed there with nothing saved. The full registry (1,619 outlets, 2,037 feeds
# on 2026-11-09) took 13 minutes on 2026-10-12, before feeds honoured robots.txt Crawl-delay, so a check
# now covers most of the registry in a week and the rest first the next week.
DEADLINE_MINUTES = 11.0
# Once every outlet left only sleeps through a long Crawl-delay (teledoce.com asks ten minutes between
# requests), the run waits this long from its start, and after that only for a sleep about to end
# (fetch_feeds.SLOW_FEED_NEAR_SECONDS). Discovery's own patience (fetch_feeds.SLOW_FEED_PATIENCE_SECONDS,
# 15 minutes) is longer than this whole check.
SLEEP_PATIENCE_SECONDS = 300
# When each outlet's feeds were last checked here, whatever the hourly collection does to feed_health.
CHECKS_TABLE = "outlet_validation"


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
        days.extend(dates.get(("url", u), today) for u, _ in editorial_feeds(outlet))
    return max(days) if days else None


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def check_outlet(outlet, stop=None):
    """This run's result for each of the outlet's feeds, or None when stop() said, before a feed, that
    the run is over: an outlet checked in part is not judged."""
    results = []
    for url, kind in editorial_feeds(outlet):
        if stop is not None and stop():
            return None
        r = news_sitemap.fetch_entries(url) if kind == "news_sitemap" else fetch_feed(url)
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


def editorial_feeds(outlet):
    """(url, type) for each feed the outlet is judged on: its RSS feeds, then its Google News sitemaps.
    Until 2026-11-09 only the RSS was checked, so an outlet read through its sitemap alone, because its
    RSS is refused by robots.txt (kr_koreatimes, br_cnnbrasil, cy_philenews, pk_ary), would have been
    judged dead on the RSS and retired while its sitemap worked."""
    return [(e["url"], e["type"]) for e in registry.feed_entries(outlet) if e["kind"] == registry.EDITORIAL]


def check_order(outlets, validated, last_checked):
    """The outlets, least recently checked first: by the day this check last covered them (none yet
    first), then by their oldest feed_health.last_checked, then in registry order. feed_health alone
    would not rotate: the hourly collection, which runs just before, stamps nearly every feed."""
    def key(item):
        i, o = item
        oldest = min((last_checked.get(u) or "" for u, _ in editorial_feeds(o)), default="")
        return (validated.get(o["id"]) or "", oldest, i)
    return [o for _, o in sorted(enumerate(outlets), key=key)]


def load_state(conn):
    """(history, validated, last_checked) from the database, read before this run records anything, so
    only recorded history counts toward MIN_FAILED_CHECKS."""
    history, last_checked = {}, {}
    for row in conn.execute("SELECT feed_url, last_ok, total_failures, total_checks, last_checked FROM feed_health"):
        history[row[0]] = {"last_ok": row[1], "total_failures": row[2], "total_checks": row[3]}
        last_checked[row[0]] = row[4]
    conn.execute("CREATE TABLE IF NOT EXISTS %s (outlet_id TEXT PRIMARY KEY, last_validated TEXT NOT NULL)" % CHECKS_TABLE)
    conn.commit()
    validated = {r[0]: r[1] for r in conn.execute("SELECT outlet_id, last_validated FROM %s" % CHECKS_TABLE)}
    return history, validated, last_checked


def save_outlet(conn, outlet_id, results):
    """One outlet's check, committed at once: its feeds' health and the day it was checked."""
    from pipeline import store
    for r in results:
        store.record_feed_health(conn, r["outlet_id"], r["feed_url"], r["ok"], r["error"], r["entries"])
    conn.execute("INSERT OR REPLACE INTO %s(outlet_id, last_validated) VALUES (?, ?)" % CHECKS_TABLE,
                 (outlet_id, store.utcnow()))
    conn.commit()


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


def run(apply=False, json_out=None, workers=12, deadline_minutes=DEADLINE_MINUTES):
    started = time.time()
    deadline = started + deadline_minutes * 60 if deadline_minutes and deadline_minutes > 0 else None
    outlets = registry.load_outlets()
    # UTC, like every other date this project records. last_ok is written in UTC, so comparing it
    # against the runner's local date moved the seven day boundary by up to a day either way.
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    to_check = registry.collectable(outlets)

    # Feed health history lives in the database when it exists, so the elapsed time rule can be
    # applied. Without history, report only.
    conn, history, validated, last_checked = None, {}, {}, {}
    try:
        from pipeline import store
        conn = store.connect()
        history, validated, last_checked = load_state(conn)
    except Exception as exc:  # store may not exist yet on a fresh checkout
        conn = None
        print("feed health history unavailable (%s); reporting only" % exc, file=sys.stderr)
    order = check_order(to_check, validated, last_checked)

    # Paced like discovery (fetch_feeds.run): a thread sleeping through a long Crawl-delay hands its
    # permit back, so the other workers go on, and _arrivals stops waiting once only such sleeps are left.
    from pipeline import fetch_articles, fetch_feeds
    permits = threading.BoundedSemaphore(workers)
    stopped = threading.Event()

    def late():
        return stopped.is_set() or (deadline is not None and time.time() > deadline)

    # A task takes its permit first and only then the next outlet in check order. Submitting one task per
    # outlet let a thread that had just finished take the permit back ahead of threads waiting since the
    # start: on the 2026-11-09 snapshot a run left 64 never checked outlets for 1,292 checked after them.
    queue, queue_lock = collections.deque(order), threading.Lock()

    def check_next():
        with queue_lock:
            o = queue.popleft() if queue else None
        if o is None:
            return None, None
        try:
            return o["id"], (None if late() else check_outlet(o, stop=late))
        except Exception as exc:       # one outlet must not take the check down; it is not judged
            return o["id"], exc

    checked, errors, save_errors, counts = {}, {}, 0, {}
    pool = ThreadPoolExecutor(max_workers=workers + config.SLOW_DOMAIN_THREADS)
    futures = [pool.submit(fetch_articles.paced(permits, check_next)) for _ in order]
    try:
        for fut in fetch_feeds._arrivals(futures, counts, started + SLEEP_PATIENCE_SECONDS, deadline):
            oid, group = fut.result()      # group None: not started, or stopped between feeds, at the deadline
            if isinstance(group, Exception):
                errors[oid] = "%s: %s" % (type(group).__name__, str(group)[:200])
            elif group is not None:
                checked[oid] = group
                if conn is not None:
                    try:
                        save_outlet(conn, oid, group)
                    except Exception as exc:
                        conn.rollback()
                        save_errors += 1
                        print("feed health not saved for %s (%s)" % (oid, exc), file=sys.stderr)
            if deadline is not None and time.time() > deadline:
                counts["stopped_at_deadline"] = True
                break
    finally:
        stopped.set()
        pool.shutdown(wait=False, cancel_futures=True)

    unchecked = [o["id"] for o in order if o["id"] not in checked and o["id"] not in errors]
    asleep = unchecked if counts.get("outlets_left_asleep") and not counts.get("stopped_at_deadline") else []
    skipped = [] if asleep else unchecked
    results = [r for o in order if o["id"] in checked for r in checked[o["id"]]]

    dates = registry_dates()
    if dates is None:
        print("registry history unavailable; outlets that never succeeded are only reported", file=sys.stderr)
    by_id = {o["id"]: o for o in outlets}

    # Only an outlet checked in full on this run is judged; one left for the deadline, asleep or broken
    # keeps its state until a later week checks it.
    decisions = {}
    for oid, rs in checked.items():
        o = by_id[oid]
        d = decide(o, rs, history, today, measured_from(o, dates, today))
        if d:
            decisions[oid] = d

    ok = sum(1 for r in results if r["ok"])
    print("checked %d of %d outlets in %.0f s (%d feeds: %d ok, %d failing); not checked: %d left at the "
          "%g minute deadline, %d left asleep on a long Crawl-delay, %d with an error" % (
              len(checked), len(order), time.time() - started, len(results), ok, len(results) - ok,
              len(skipped), deadline_minutes or 0, len(asleep), len(errors)))
    for r in results:
        if not r["ok"]:
            print("  FAIL %-24s %s  (%s)" % (r["outlet_id"], r["feed_url"], r["error"]))
    for label, ids in (("left at the deadline", skipped), ("left asleep", asleep)):
        if ids:
            print("  %s, first next week: %s%s" % (label, ", ".join(ids[:20]), " and %d more" % (len(ids) - 20) if len(ids) > 20 else ""))
    for oid, why in sorted(errors.items()):
        print("  ERROR %-24s %s" % (oid, why))

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
                       "dead_candidates": sorted(decisions), "decisions": {k: list(v) for k, v in decisions.items()},
                       "deadline_minutes": deadline_minutes, "seconds": round(time.time() - started, 1),
                       "outlets_to_check": len(order), "outlets_checked": len(checked),
                       "outlets_skipped_deadline": len(skipped), "outlets_left_asleep": len(asleep),
                       "outlet_errors": len(errors), "feed_health_save_errors": save_errors,
                       "unchecked": {"deadline": skipped, "asleep": asleep, "error": sorted(errors)}},
                      fh, indent=1)
    return results, changed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--deadline-minutes", type=float, default=DEADLINE_MINUTES,
                    help="start no outlet after this many minutes (0: no deadline)")
    args = ap.parse_args()
    run(apply=args.apply, json_out=args.json_out, workers=args.workers, deadline_minutes=args.deadline_minutes)
    # A worker still asleep on a long Crawl-delay, or still fetching at the deadline, would hold the
    # interpreter's exit until it wakes (the pool's threads are joined at exit), up to ten minutes past
    # the deadline and the step's limit. Everything is saved and written by now.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
