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
minutes. No outlet is started after it, and the outlets it never started are reported and never judged. Each
outlet's feed health is saved as soon as its check is done, so a run stopped early keeps what it did.
Outlets are checked least recently validated first (outlet_validation, then feed_health.last_checked),
so consecutive partial weeks cover the whole registry. An outlet whose only work left is a long
robots.txt Crawl-delay sleep does not hold the run either (fetch_feeds._arrivals, the patience rule of
discovery); it is judged on what it has (below), or else reported as left asleep, and comes first the next week.

Since 2026-11-09 (evening) an outlet's feeds are fetched least recently checked first, and the check of an
outlet stops at its first feed that answers: one working feed keeps an outlet alive, so the others are not
fetched and are reported as such. A feed the run could not fetch in time (its host's Crawl-delay puts its
slot past the deadline, or it was asleep or in flight when the run ended) is judged on its latest result in
feed_health, which hourly discovery writes, when that result is at most RECORDED_FRESH_HOURS old; an older
result is reported and never used. Every outlet's verdict names the evidence it rested on (judge()). An
outlet judged with a recorded result is not stamped in outlet_validation, so it starts first again next week.
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
# How old a feed's recorded result (feed_health.last_checked) may be and still stand in for a fetch the run
# could not make in time. teledoce.com asks 600 s between requests and its feeds redirect, so one feed takes
# two slots, 20 minutes, against an 11 minute deadline: uy_telemundo was reported every week and never judged.
# A recorded success within the window keeps the outlet as alive as a fetch would: an outlet that answered
# within a day has not been silent DEAD_AFTER_DAYS. A recorded failure counts toward death only with every other
# feed failing too, and the silence is then measured to the day of the oldest record used (judge(), as_of), never
# to today. The death rule reads dates, so a record within a day is as current as that rule can tell apart.
# Shorter would leave the slow hosts unjudged on most Mondays: discovery waits out a slow host one hour in
# fetch_feeds.SLOW_FEED_PATIENT_EVERY_HOURS, the runner drops scheduled runs, and teledoce.com/rss was polled
# once a day from 2026-11-01 to 2026-11-07 (feed_polls on the 2026-11-09 snapshot).
RECORDED_FRESH_HOURS = 24.0
# Verdicts. alive: a feed answered this run. alive_recorded: none answered this run, and a feed not fetched has a
# success on record within RECORDED_FRESH_HOURS. dead: every feed failed, this run or on a fresh record, and
# is_dead holds. not_dead: every feed failed, but is_dead does not hold (yet). not_judged: no feed answered and
# a feed has neither a fetch this run nor a fresh record, so the outlet is neither alive nor dead on evidence.
JUDGED = ("alive", "alive_recorded", "dead", "not_dead")


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

def check_outlet(outlet, stop=None, last_checked=None, until_ok=False, deadline=None, sink=None):
    """This run's result for each of the outlet's feeds (every feed, in registry order, by default, as a
    manual validation wants), or None when stop() said, before a feed, that the run is over.

    The weekly run passes last_checked (feed_url -> feed_health.last_checked), so the feeds go least recently
    checked first (feed_order), and until_ok, so the check stops at the first feed that answers. A feed not
    fetched gets a row with fetched False and the reason: "outlet_alive" after a feed answered, or
    "crawl_delay_past_deadline" when its host's next slot falls after the deadline. Rows are appended to sink
    (a list) as they come, so the run can read what an outlet it leaves in flight had already fetched."""
    results = sink if sink is not None else []
    for url, kind in feed_order(outlet, last_checked):
        if until_ok and any(r.get("fetched", True) and r["ok"] for r in results):
            results.append(_not_fetched(outlet, url, "outlet_alive"))
            continue
        if stop is not None and stop():
            return None
        if deadline is not None and _slot_past(url, deadline):
            results.append(_not_fetched(outlet, url, "crawl_delay_past_deadline"))
            continue
        r = news_sitemap.fetch_entries(url) if kind == "news_sitemap" else fetch_feed(url)
        results.append({
            "outlet_id": outlet["id"],
            "feed_url": url,
            "fetched": True,
            "ok": r["ok"],
            "status": r["status"],
            "error": r["error"],
            "entries": len(r["entries"]),
            "elapsed": round(r["elapsed"], 2),
        })
    return results


def _not_fetched(outlet, url, reason):
    return {"outlet_id": outlet["id"], "feed_url": url, "fetched": False, "reason": reason}


def fetched_rows(rows):
    """The rows of feeds actually fetched this run (the others carry fetched False)."""
    return [r for r in rows or () if r.get("fetched", True)]


def feed_order(outlet, last_checked=None):
    """The outlet's editorial feeds, least recently checked first (feed_health.last_checked; never checked
    first, then registry order). Hourly discovery stamps the feeds it reaches, so the weekly check spends its
    requests on the feed whose evidence is oldest: on teledoce.com discovery reached only /rss from 2026-10-31
    to 2026-11-09, and /feed comes first here. Without last_checked, registry order."""
    feeds = editorial_feeds(outlet)
    if last_checked is None:
        return feeds
    return [f for _, f in sorted(enumerate(feeds), key=lambda x: (last_checked.get(x[1][0]) or "", x[0]))]


def _slot_past(url, deadline):
    """Whether the feed's host, by the robots.txt Crawl-delay already read this run, cannot be asked before the
    deadline. Reads the cached robots record only (domain_gap) and never fetches: a host not read yet is fetched
    as usual, and the fetch reads its robots.txt first."""
    from pipeline import fetch_articles as fa
    domain = fa.domain_of(url)
    if fa.domain_gap(domain) <= config.MIN_SECONDS_PER_DOMAIN:
        return False
    return time.time() + fa.slot_in(domain) > deadline


# ---------------------------------------------------------------------------
# the verdict and the evidence it rests on
# ---------------------------------------------------------------------------

def _hours_before(stamp, now):
    """Hours from an ISO stamp to now, or None when the stamp cannot be read or lies after now."""
    try:
        t = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    hours = (now - t).total_seconds() / 3600.0
    return hours if hours >= 0 else None


def recorded_result(row, now):
    """A feed's latest result on record (feed_health), as {ok, at, age_hours, error}, or None when there is no
    usable record. ok only when that latest check succeeded (no consecutive failure)."""
    if not row or not row.get("last_checked"):
        return None
    age = _hours_before(row["last_checked"], now)
    if age is None:
        return None
    ok = not (row.get("consecutive_failures") or 0) and bool(row.get("last_ok"))
    return {"ok": ok, "at": row["last_checked"], "age_hours": round(age, 1),
            "error": None if ok else (row.get("last_error") or "failed")}


def judge(outlet, rows, history, now, started=None, unfetched_reason="not_reached",
          fresh_hours=RECORDED_FRESH_HOURS):
    """The outlet's verdict (JUDGED or "not_judged"), the decision (None or ("deactivate", why)) and the evidence,
    one entry per editorial feed: source "fetched" (this run), "recorded" (feed_health, with its age), or
    "not_fetched" (no usable evidence; a stale record is shown and not used).

    rows: this run's rows for the outlet (fetched or not, check_outlet). A feed with no row was not reached
    (unfetched_reason). Order of proof: a feed that answered this run; else a success on record within
    fresh_hours; else, with every feed failing on fresh evidence, is_dead, its silence measured to the day of the
    oldest record used (as_of), never to today; else nothing is judged."""
    today = now.date().isoformat()
    by_url = {r["feed_url"]: r for r in rows or ()}
    evidence = []
    for url, _ in editorial_feeds(outlet):
        r = by_url.get(url)
        if r is not None and r.get("fetched", True):
            evidence.append({"feed_url": url, "source": "fetched", "ok": r["ok"], "error": r.get("error"),
                             "entries": r.get("entries")})
            continue
        why = r["reason"] if r is not None else unfetched_reason
        rec = recorded_result(history.get(url), now)
        if rec is not None and rec["age_hours"] <= fresh_hours and why != "outlet_alive":
            evidence.append(dict(rec, feed_url=url, source="recorded", why_not_fetched=why))
        else:
            e = {"feed_url": url, "source": "not_fetched", "why_not_fetched": why}
            if rec is not None:
                e.update(record_ok=rec["ok"], record_at=rec["at"], record_age_hours=rec["age_hours"])
            evidence.append(e)
    out = {"verdict": "not_judged", "decision": None, "evidence": evidence}
    fetched_ok = [e for e in evidence if e["source"] == "fetched" and e["ok"]]
    recorded_ok = [e for e in evidence if e["source"] == "recorded" and e["ok"]]
    if fetched_ok:
        out.update(verdict="alive", decided_on=[fetched_ok[0]["feed_url"]])
        return out
    if recorded_ok:
        best = min(recorded_ok, key=lambda e: e["age_hours"])
        out.update(verdict="alive_recorded", decided_on=[best["feed_url"]])
        return out
    if not evidence or any(e["source"] == "not_fetched" for e in evidence):
        return out
    recorded = [e for e in evidence if e["source"] == "recorded"]
    as_of = min([today] + [e["at"][:10] for e in recorded])
    failed = [{"feed_url": e["feed_url"], "ok": False, "error": e["error"]} for e in evidence]
    decision = decide(outlet, failed, history, as_of, started)
    if decision and recorded:
        action, why = decision
        note = " (not fetched this run, judged on the check on record: %s)" % ", ".join(
            "%s %s" % (e["feed_url"], e["at"]) for e in recorded)
        decision = (action, why[:-len(" (auto)")] + note + " (auto)" if why.endswith(" (auto)") else why + note)
    out.update(verdict="dead" if decision else "not_dead", decision=decision,
               decided_on=[e["feed_url"] for e in evidence], as_of=as_of)
    return out


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
    for row in conn.execute("SELECT feed_url, last_ok, total_failures, total_checks, last_checked, "
                            "consecutive_failures, last_error FROM feed_health"):
        history[row[0]] = {"last_ok": row[1], "total_failures": row[2], "total_checks": row[3],
                           "last_checked": row[4], "consecutive_failures": row[5], "last_error": row[6]}
        last_checked[row[0]] = row[4]
    conn.execute("CREATE TABLE IF NOT EXISTS %s (outlet_id TEXT PRIMARY KEY, last_validated TEXT NOT NULL)" % CHECKS_TABLE)
    conn.commit()
    validated = {r[0]: r[1] for r in conn.execute("SELECT outlet_id, last_validated FROM %s" % CHECKS_TABLE)}
    return history, validated, last_checked


def save_outlet(conn, outlet_id, results, validated=True):
    """One outlet's check, committed at once: the health of the feeds fetched this run (a feed not fetched gets
    no record either way) and, when validated, the day it was checked. The run leaves validated False for an
    outlet it could not judge, or judged on recorded results alone, so it keeps its place first in check_order
    and gets its live attempt the next week too."""
    from pipeline import store
    for r in fetched_rows(results):
        store.record_feed_health(conn, r["outlet_id"], r["feed_url"], r["ok"], r["error"], r["entries"])
    if validated:
        conn.execute("INSERT OR REPLACE INTO %s(outlet_id, last_validated) VALUES (?, ?)" % CHECKS_TABLE,
                     (outlet_id, store.utcnow()))
    conn.commit()


def stamps_validation(verdict) -> bool:
    """Whether a verdict counts as this week's validation of the outlet: judged on this run's fetches alone. One
    that needed a recorded result keeps its place first in check_order, so a slow host starts at the top of the
    next run too, where its first slot still falls before the deadline."""
    if verdict["verdict"] == "alive":
        return True
    return verdict["verdict"] in JUDGED and all(e["source"] == "fetched" for e in verdict["evidence"])


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


def run(apply=False, json_out=None, workers=12, deadline_minutes=DEADLINE_MINUTES, now=None):
    """now: the moment recorded results are aged against and the date of the check (UTC); the clock by default,
    a fixed moment in a replay of a snapshot."""
    started = time.time()
    deadline = started + deadline_minutes * 60 if deadline_minutes and deadline_minutes > 0 else None
    outlets = registry.load_outlets()
    # UTC, like every other date this project records. last_ok is written in UTC, so comparing it
    # against the runner's local date moved the seven day boundary by up to a day either way.
    today = now.date().isoformat() if now else dt.datetime.now(dt.timezone.utc).date().isoformat()
    now = now or dt.datetime.now(dt.timezone.utc)
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
    # What each outlet started has fetched so far, so one still asleep or in flight when the run ends is
    # judged on that and on recorded results for the rest (judge), not dropped.
    progress = {}

    def check_next():
        with queue_lock:
            o = queue.popleft() if queue else None
        if o is None:
            return None, None
        if late():
            return o["id"], None
        rows = progress[o["id"]] = []
        try:
            return o["id"], check_outlet(o, stop=late, last_checked=last_checked, until_ok=True,
                                         deadline=deadline, sink=rows)
        except Exception as exc:       # one outlet must not take the check down; it is not judged
            return o["id"], exc

    def save(oid, rows, verdict):
        nonlocal save_errors
        if conn is None:
            return
        try:
            save_outlet(conn, oid, rows, validated=stamps_validation(verdict))
        except Exception as exc:
            conn.rollback()
            save_errors += 1
            print("feed health not saved for %s (%s)" % (oid, exc), file=sys.stderr)

    by_id = {o["id"]: o for o in outlets}
    finished, errors, save_errors, counts = {}, {}, 0, {}
    pool = ThreadPoolExecutor(max_workers=workers + config.SLOW_DOMAIN_THREADS)
    futures = [pool.submit(fetch_articles.paced(permits, check_next)) for _ in order]
    try:
        for fut in fetch_feeds._arrivals(futures, counts, started + SLEEP_PATIENCE_SECONDS, deadline):
            oid, group = fut.result()      # group None: not started, or stopped between feeds, at the deadline
            if isinstance(group, Exception):
                errors[oid] = "%s: %s" % (type(group).__name__, str(group)[:200])
            elif group is not None:
                finished[oid] = group
                save(oid, group, judge(by_id[oid], group, history, now, unfetched_reason="run_ended"))
            if deadline is not None and time.time() > deadline:
                counts["stopped_at_deadline"] = True
                break
    finally:
        stopped.set()
        pool.shutdown(wait=False, cancel_futures=True)

    # An outlet started and not finished (asleep on a Crawl-delay, or in flight at the deadline) keeps what it
    # fetched; its health is saved, and it is judged below like the others.
    cut = {}
    for o in order:
        oid = o["id"]
        if oid in progress and oid not in finished and oid not in errors:
            cut[oid] = list(progress[oid])
            if fetched_rows(cut[oid]):
                save(oid, cut[oid], judge(o, cut[oid], history, now, unfetched_reason="run_ended"))

    dates = registry_dates()
    if dates is None:
        print("registry history unavailable; outlets that never succeeded are only reported", file=sys.stderr)

    # Every outlet started gets a verdict on its evidence (judge); one never started, or broken, is not judged
    # and keeps its state until a later week checks it.
    verdicts = {}
    for o in order:
        oid = o["id"]
        rows = finished.get(oid, cut.get(oid))
        if rows is None:
            verdicts[oid] = {"verdict": "not_judged", "decision": None, "evidence": [
                {"feed_url": u, "source": "not_fetched", "why_not_fetched": "error" if oid in errors else "not_started"}
                for u, _ in editorial_feeds(o)]}
            continue
        verdicts[oid] = judge(o, rows, history, now, measured_from(o, dates, today), unfetched_reason="run_ended")
    judged = [o["id"] for o in order if verdicts[o["id"]]["verdict"] in JUDGED]
    on_record = [oid for oid in judged if any(e["source"] == "recorded" for e in verdicts[oid]["evidence"])]
    decisions = {oid: verdicts[oid]["decision"] for oid in judged if verdicts[oid]["decision"]}

    unchecked = [o["id"] for o in order if o["id"] not in errors and verdicts[o["id"]]["verdict"] not in JUDGED]
    asleep = unchecked if counts.get("outlets_left_asleep") and not counts.get("stopped_at_deadline") else []
    skipped = [] if asleep else unchecked
    judged_set = set(judged)
    results = [r for o in order if o["id"] in judged_set
               for r in fetched_rows(finished.get(o["id"], cut.get(o["id"])))]
    tally = collections.Counter(verdicts[oid]["verdict"] for oid in judged)
    not_fetched = sum(1 for oid in judged for e in verdicts[oid]["evidence"] if e["source"] != "fetched")

    ok = sum(1 for r in results if r["ok"])
    print("checked %d of %d outlets in %.0f s (%d feeds: %d ok, %d failing); not checked: %d left at the "
          "%g minute deadline, %d left asleep on a long Crawl-delay, %d with an error" % (
              len(judged), len(order), time.time() - started, len(results), ok, len(results) - ok,
              len(skipped), deadline_minutes or 0, len(asleep), len(errors)))
    print("  verdicts: %s; %d judged with a recorded result, %d feeds of judged outlets not fetched "
          "(%d once a feed had answered)" % (
              ", ".join("%s %d" % (k, tally[k]) for k in JUDGED), len(on_record), not_fetched,
              sum(1 for oid in judged for e in verdicts[oid]["evidence"] if e.get("why_not_fetched") == "outlet_alive")))
    for r in results:
        if not r["ok"]:
            print("  FAIL %-24s %s  (%s)" % (r["outlet_id"], r["feed_url"], r["error"]))
    for oid in on_record:
        v = verdicts[oid]
        print("  RECORDED %-20s %s on %s" % (oid, v["verdict"], "; ".join(
            "%s %s %s (%.1f h old, not fetched: %s)" % (e["feed_url"], "ok" if e["ok"] else "failed", e["at"],
                                                         e["age_hours"], e["why_not_fetched"])
            for e in v["evidence"] if e["source"] == "recorded")))
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
        report_outlets = {}
        for o in order:
            v = verdicts[o["id"]]
            entry = {"verdict": v["verdict"], "decided_on": v.get("decided_on", []), "evidence": v["evidence"],
                     "finished": o["id"] in finished}
            if v.get("as_of"):
                entry["as_of"] = v["as_of"]
            report_outlets[o["id"]] = entry
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump({"date": today, "results": results, "newly_inactive": changed,
                       "dead_candidates": sorted(decisions), "decisions": {k: list(v) for k, v in decisions.items()},
                       "deadline_minutes": deadline_minutes, "seconds": round(time.time() - started, 1),
                       "recorded_fresh_hours": RECORDED_FRESH_HOURS,
                       "outlets_to_check": len(order), "outlets_checked": len(judged),
                       "outlets_judged_on_record": len(on_record), "outlets_cut_short": len(cut),
                       "verdicts": {k: tally[k] for k in JUDGED},
                       "outlets_skipped_deadline": len(skipped), "outlets_left_asleep": len(asleep),
                       "outlet_errors": len(errors), "feed_health_save_errors": save_errors,
                       "unchecked": {"deadline": skipped, "asleep": asleep, "error": sorted(errors)},
                       "outlets": report_outlets},
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
