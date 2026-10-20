"""Relay for outlets whose feeds refuse GitHub's runner addresses.

Two halves, one data flow, one database writer.

  bundle  runs on a machine on an ordinary network (the owner's Mac, from launchd).
          It polls and fetches the outlets marked collector: self_hosted, and polls (but
          leaves the pages to the runner) those marked collector: both, into its
          own database (TRACKER_DB_PATH), then writes every article discovered in the
          last BUNDLE_DAYS days, bodies included, to one gzipped JSON lines file and
          force-pushes that single file to the relay branch. The branch has no history:
          each push replaces the previous commit, so the repository does not grow. The
          commit is made in a throwaway repository, so the machine's own checkout does
          not keep every old bundle either. Once the runner has acknowledged a bundle
          (the relay-ack branch), the next one carries only the rows changed since.

  ingest  runs on the hosted runner at the start of every collect job. It reads the
          bundle from the relay branch and inserts the articles it does not have yet,
          bodies and discovery counts included, and after the database is saved it
          force-pushes the acknowledgement (ack). Classification then happens on the
          hosted side like any other article. Nothing here writes the main database from
          two places at once: the relay machine only ever writes its own database and
          the branch.

Usage:
  TRACKER_COLLECTOR=self_hosted TRACKER_DB_PATH=data/relay.db python -m pipeline.relay run
  python -m pipeline.relay ingest
  python -m pipeline.relay ack
"""
import argparse
import base64
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Dict, Iterable, Optional

from pipeline import config, store

BUNDLE_DAYS = 7
# Refused articles the relay takes over (seed_refused): how far back it looks in the main database,
# and how many it adds per outlet on one run, so a backlog never crowds out the hour's new items.
SEED_DAYS = 60
SEED_PER_OUTLET = 20
REFUSED_REASONS = ("http_403", "http_429")
RELAY_BRANCH = "relay"
BUNDLE_NAME = "bundle.jsonl.gz"
# The runner's acknowledgement: one small file, force-pushed to its own history-free branch after the
# runner has saved its database, naming the build time of the latest bundle it took in with no gap
# before it (relay_ack on the hosted side). A bundle built after that carries only the article rows
# and shared-feed polls changed since ACK_MARGIN_HOURS before it, instead of the whole window every
# hour; without an acknowledgement, or one older than the window, it carries the whole window. Each
# bundle opens with a bundle_meta record saying which it is, so the runner can tell a gap.
ACK_BRANCH = "relay-ack"
ACK_NAME = "ack.json"
ACK_MARGIN_HOURS = 1
GIT_IDENTITY = {"GIT_AUTHOR_NAME": "tracker-relay", "GIT_AUTHOR_EMAIL": "tracker-relay@users.noreply.github.com",
                "GIT_COMMITTER_NAME": "tracker-relay", "GIT_COMMITTER_EMAIL": "tracker-relay@users.noreply.github.com"}
# The collector a bundle's feed polls are stored under on the hosted side (feed_polls.collector).
# A bundle only ever carries the relay's own polls, whatever its database calls them.
RELAY_COLLECTOR = "self_hosted"
FEED_POLL_FIELDS = ["date", "feed_url", "outlet_id", "country", "polls", "saturated", "entries", "new_items",
                    "missed_estimate", "estimator"]
# Each poll of a feed both collectors poll (collector: both) travels on its own, for POLL_EVENT_DAYS,
# so the hosted side can judge it against the runner's polls (reconcile_poll). Shorter than
# store.POLL_LOG_DAYS, so a poll sent again is still in the log and is not counted twice.
POLL_EVENT_DAYS = 3
POLL_EVENT_FIELDS = ["feed_url", "polled_at", "outlet_id", "country", "entries", "new_items", "times", "prev_poll_at",
                     "saturated_own", "missed_own", "estimator"]
ARTICLE_FIELDS = ["url", "url_hash", "outlet_id", "country", "language", "feed_url", "title", "summary", "author",
                  "published_at", "discovered_at", "fetched_at", "status", "fail_reason", "http_status", "body_hash",
                  "body_chars", "gate_relevant", "gate_terms", "fetch_attempts", "page_labels", "day_of_record",
                  "extract_version"]

# How far an article has got. An existing hosted row takes the relay's status only when the relay's
# is strictly further along this ladder, so a status never regresses; a hosted row that carries a
# classification is never touched at all. Discovery is not counted again on either path: for relay
# outlets the per outlet-day counts come from the bundle's own rows.
STATUS_RANK = {
    "gated_out": 0,
    "blocked_robots": 1, "failed": 1,
    "discovered": 2, "queued": 2,
    "paywalled": 3,
    "fetched": 4,
    "awaiting_llm": 5,
    "llm_submitted": 6,
    "classified": 7,
}
# Fields worth carrying over when the relay's copy is further along. The hosted row's own
# discovery fields (url, outlet, country, discovered_at) are never rewritten.
ADVANCE_FIELDS = ["status", "fail_reason", "http_status", "body_hash", "body_chars", "fetched_at",
                  "fetch_attempts", "page_labels", "author", "published_at", "title", "summary", "extract_version"]


def _git(*args, check=True, input_bytes=None, env=None):
    return subprocess.run(["git", *args], cwd=str(config.ROOT), check=check, capture_output=True, input=input_bytes,
                          env=dict(os.environ, **env) if env else None)


# ---------------------------------------------------------------------------
# bundle side
# ---------------------------------------------------------------------------

# An update counts only when it touches a field the bundle carries (not audit_keep, say). The update
# trigger's name follows the field list, so a bundle that gains a field gets a new trigger, and the
# rows are all marked changed once, since changes to that field went unwatched until then.
UPDATE_TRIGGER = "relay_changes_update_" + hashlib.sha1(",".join(ARTICLE_FIELDS).encode("utf-8")).hexdigest()[:8]
CHANGE_TRIGGERS = ("relay_changes_insert", UPDATE_TRIGGER, "relay_changes_delete")


def ensure_change_log(conn) -> None:
    """On the relay: when each article row last changed (relay_changes, kept by triggers on articles),
    so a bundle can carry only the rows changed since the runner's acknowledgement. Every change counts,
    by any code path (discovery, fetch, a gate re-admission, section_retag), since the triggers see them
    all. When the log or a trigger is missing, every row is marked changed now, so a row written while
    nothing was watching is sent again rather than never."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger') "
                                       "AND name LIKE 'relay_changes%'")}
    conn.execute("CREATE TABLE IF NOT EXISTS relay_changes (url_hash TEXT PRIMARY KEY, changed_at TEXT NOT NULL)")
    stamp = "strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')"   # the format of store.utcnow()
    conn.execute("CREATE TRIGGER IF NOT EXISTS relay_changes_insert AFTER INSERT ON articles BEGIN "
                 "INSERT OR REPLACE INTO relay_changes(url_hash, changed_at) VALUES (NEW.url_hash, %s); END" % stamp)
    for old in sorted(have):
        if old.startswith("relay_changes_update") and old != UPDATE_TRIGGER:
            conn.execute('DROP TRIGGER IF EXISTS "%s"' % old)
    conn.execute("CREATE TRIGGER IF NOT EXISTS %s AFTER UPDATE OF %s ON articles BEGIN "
                 "INSERT OR REPLACE INTO relay_changes(url_hash, changed_at) VALUES (NEW.url_hash, %s); END"
                 % (UPDATE_TRIGGER, ", ".join(ARTICLE_FIELDS), stamp))
    conn.execute("CREATE TRIGGER IF NOT EXISTS relay_changes_delete AFTER DELETE ON articles BEGIN "
                 "DELETE FROM relay_changes WHERE url_hash=OLD.url_hash; END")
    if not {"relay_changes", *CHANGE_TRIGGERS} <= have:
        conn.execute("INSERT OR REPLACE INTO relay_changes(url_hash, changed_at) SELECT url_hash, ? FROM articles",
                     (store.utcnow(),))
    conn.commit()


def _stale_gated_cutoff(now: dt.datetime) -> str:
    """A gated-out article of a shared outlet discovered before this may be one the runner found itself and
    has pruned since (store.prune_gated_out): ingest never inserts it, so a bundle need not carry it. A day
    inside GATED_OUT_RETENTION_DAYS covers a relay that saw it somewhat later than the runner did."""
    return (now - dt.timedelta(days=max(0, config.GATED_OUT_RETENTION_DAYS - 1))).isoformat()


def delta_since(ack: Optional[str], since_days: int = BUNDLE_DAYS, now: Optional[dt.datetime] = None) -> Optional[str]:
    """The change time a bundle starts from, given the runner's acknowledgement: ACK_MARGIN_HOURS before
    it. None (the whole window) without one, when it cannot be read, or when it is older than the window."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        t = dt.datetime.fromisoformat(ack) if ack else None
    except (TypeError, ValueError):
        return None
    if t is None or t.tzinfo is None or t > now + dt.timedelta(hours=1):
        return None
    start = t - dt.timedelta(hours=ACK_MARGIN_HOURS)
    if start < now - dt.timedelta(days=since_days):
        return None
    return start.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def build_bundle(conn, since_days: int = BUNDLE_DAYS, ack: Optional[str] = None, outlets=None,
                 include_bodies: bool = True) -> bytes:
    """The bundle: every row of the window (no acknowledgement), or only the article rows and shared-feed
    polls changed since the runner's acknowledgement (ack, the build time of the latest bundle it took
    in without a gap). The small per feed and per day records always cover the whole window.

    This is the bundle in clear, for sealing (make_bundle). With include_bodies False no article or
    reference body is in it, a paywalled one included: each record that would have carried one says
    body_withheld instead, and bundle_meta says bodies "withheld", so the runner knows those bodies are
    to be fetched again where it can (classify_rules.ensure_body). push_bundle never pushes a bundle
    in clear that carries a body."""
    ensure_change_log(conn)
    built_at = store.utcnow()
    now = dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=since_days)).isoformat()
    changed = delta_since(ack, since_days, now)
    # Fetched in the window counts too: an article the runner found weeks ago and could not fetch,
    # taken over by seed_refused, is delivered when the relay fetches it.
    if changed is None:
        rows = conn.execute("SELECT * FROM articles WHERE discovered_at >= ? OR fetched_at >= ? ORDER BY id",
                            (since, since)).fetchall()
    else:
        rows = conn.execute("SELECT a.* FROM articles a JOIN relay_changes c ON c.url_hash = a.url_hash "
                            "WHERE c.changed_at >= ? AND (a.discovered_at >= ? OR a.fetched_at >= ?) ORDER BY a.id",
                            (changed, since, since)).fetchall()
    try:
        from pipeline import registry
        shared = {o["id"] for o in (outlets if outlets is not None else registry.load_outlets()) if registry.is_shared(o)}
    except Exception:  # noqa: BLE001  without the registry nothing is left out, as before
        shared = set()
    stale_gated = _stale_gated_cutoff(now)
    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8") as fh:
        # First, what this bundle covers: ingest advances the acknowledgement only when it follows on
        # from what the runner already holds (_advance_ack).
        fh.write(json.dumps({"_type": "bundle_meta", "built_at": built_at, "since": changed,
                             "window_days": since_days,
                             "bodies": "included" if include_bodies else "withheld"}) + "\n")
        # Feed health travels too, so the site shows the relayed feeds as healthy rather than
        # as the hosted runner sees them.
        for h in conn.execute("SELECT * FROM feed_health"):
            fh.write(json.dumps({"_type": "feed_health", **{k: h[k] for k in h.keys()}}, ensure_ascii=False) + "\n")
        # The relay database counts every item it discovers at insert time, so its per outlet-day
        # rows are exact; the hosted side replaces its rows for these outlets with them.
        for d in conn.execute("SELECT date, outlet_id, country, discovered, gate_relevant FROM daily_outlet_discovery"):
            fh.write(json.dumps({"_type": "outlet_discovery", **{k: d[k] for k in d.keys()}}) + "\n")
        # Every discovery pass the relay ran, so the hosted side can tell a quiet day in the relayed
        # countries from a day on which the machine was asleep.
        for run in conn.execute("SELECT started_at, finished_at, ok FROM run_log WHERE stage='discover' AND started_at >= ?", (since,)):
            fh.write(json.dumps({"_type": "relay_run", **{k: run[k] for k in run.keys()}}) + "\n")
        # Every poll of a shared feed, one by one (reconcile_poll on the hosted side). The relay logs a
        # poll once and never changes it, so its time is its change time. Polls of one feed go together,
        # in order, which gzip compresses far better; ingest judges each feed on its own. The entry dates
        # (times) are read again only to rejudge a saturated poll (fetch_feeds.rejudge), so an unsaturated
        # poll travels without them.
        since_polls = (now - dt.timedelta(days=POLL_EVENT_DAYS)).replace(microsecond=0).isoformat()
        if changed is not None and changed > since_polls:
            since_polls = changed
        for p in conn.execute("SELECT %s, saturated FROM feed_poll_log WHERE polled_at >= ? ORDER BY feed_url, polled_at"
                              % ", ".join(POLL_EVENT_FIELDS), (since_polls,)):
            ev = {k: p[k] for k in POLL_EVENT_FIELDS}
            if not (p["saturated_own"] or p["saturated"]):
                ev["times"] = "[]"
            fh.write(json.dumps({"_type": "feed_poll_event", **ev}, ensure_ascii=False) + "\n")
        # Every feed poll the relay made in the window, per feed and day, so feed saturation and the
        # missed-items estimate are measured for the relayed feeds too. Each row is the day's running
        # total; the hosted side replaces its copy of the row, so a bundle sent twice counts once.
        for p in conn.execute("SELECT %s FROM feed_polls WHERE date >= ?" % ", ".join(FEED_POLL_FIELDS), (since[:10],)):
            fh.write(json.dumps({"_type": "feed_poll", **{k: p[k] for k in FEED_POLL_FIELDS},
                                 "collector": RELAY_COLLECTOR}, ensure_ascii=False) + "\n")
        for r in rows:
            if r["outlet_id"] in shared and r["status"] == "gated_out" and not r["gate_relevant"] \
                    and (r["discovered_at"] or "") < stale_gated:
                continue      # ingest would not insert it (shared_stale_gated), and the runner's own copy needs nothing
            item = {k: r[k] for k in ARTICLE_FIELDS}
            body = store.load_body(r["url_hash"]) if r["status"] in ("fetched", "classified", "awaiting_llm", "paywalled") else None
            if body and include_bodies:
                item["body"] = base64.b64encode(gzip.compress(body.encode("utf-8"))).decode("ascii")
            elif body:
                item["body_withheld"] = True
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        # The relay's reference items (pipeline/reference.py, sources with collector: self_hosted), body
        # inline; the hosted side writes them into reference_items and nothing else.
        from pipeline import reference
        for rec in reference.bundle_records(conn, changed or since):
            if not include_bodies and rec.pop("body", None):
                rec["body_withheld"] = True
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return buf.getvalue()


_KEY_FROM_ENV = object()


def make_bundle(conn, ack: Optional[str] = None, outlets=None, key=_KEY_FROM_ENV) -> bytes:
    """The bundle as it may be pushed to the public relay branch (scratchpad DECISIONS 7): sealed with
    the relay key (pipeline/bundle_crypto.py) and carrying bodies when there is a key, or in clear with
    every body left out when there is none. key defaults to the one the environment gives
    (RELAY_BUNDLE_KEY, or the file RELAY_BUNDLE_KEY_FILE names); it is never written anywhere."""
    from pipeline import bundle_crypto
    if key is _KEY_FROM_ENV:
        key = bundle_crypto.load_key()
    data = build_bundle(conn, ack=ack, outlets=outlets, include_bodies=bool(key))
    return bundle_crypto.seal(data, key) if key else data


def bodies_in_clear(data: bytes) -> int:
    """How many records of a bundle in clear carry a body (0 for a sealed bundle, and for bytes that
    are not a gzipped bundle at all, which ingest could not read either)."""
    from pipeline import bundle_crypto
    if bundle_crypto.is_sealed(data) or not data.startswith(b"\x1f\x8b"):
        return 0
    return sum(1 for item in iter_bundle(data) if item.get("body"))


def _origin_url() -> str:
    return _git("remote", "get-url", "origin").stdout.decode().strip()


def _single_file_commit(git, name: str, data: bytes, msg: str, date: Optional[str] = None) -> str:
    """A parentless commit holding one file, made with `git` (a function running git with its arguments).
    A fixed date makes the same content the same commit, so pushing it again changes nothing."""
    blob = git("hash-object", "-w", "--stdin", input_bytes=data).stdout.decode().strip()
    tree = git("mktree", input_bytes=("100644 blob %s\t%s\n" % (blob, name)).encode()).stdout.decode().strip()
    env = dict(GIT_IDENTITY)
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return git("commit-tree", tree, "-m", msg, env=env).stdout.decode().strip()


def _scratch_git(gitdir: str):
    """git against a throwaway bare repository, so nothing reaches the checkout's object store or refs."""
    subprocess.run(["git", "init", "--bare", "--quiet", gitdir], check=True, capture_output=True)

    def git(*args, check=True, input_bytes=None, env=None):
        return subprocess.run(["git", "--git-dir", gitdir, *args], check=check, capture_output=True, input=input_bytes,
                              env=dict(os.environ, **(env or {})))
    return git


def push_bundle(data: bytes, branch: str = RELAY_BRANCH, url: Optional[str] = None) -> str:
    """Write one file into a history-free commit and force-push it as the relay branch.

    The commit is made and pushed from a throwaway repository, not the checkout. Made in the checkout,
    every hourly bundle stayed in its object store, and the push recorded each one in the reflog of
    origin/relay, which kept them all from garbage collection: on 2026-10-12 the owner's checkout held
    230 old bundles, 667 MiB, although the branch never has more than one commit.

    The branch is public: a bundle in clear that carries any body is refused (make_bundle seals one
    that does, or leaves the bodies out without a key)."""
    n = bodies_in_clear(data)
    if n:
        raise ValueError("refusing to push %d article bodies in clear to the public relay branch; "
                         "set RELAY_BUNDLE_KEY or build the bundle with make_bundle" % n)
    url = url or _origin_url()
    with tempfile.TemporaryDirectory(prefix="relay-push-") as tmp:
        git = _scratch_git(tmp)
        commit = _single_file_commit(git, BUNDLE_NAME, data, "relay bundle %s" % store.utcnow())
        git("push", "--force", "--quiet", url, "%s:refs/heads/%s" % (commit, branch))
    return commit


def fetch_ack(url: Optional[str] = None, branch: str = ACK_BRANCH) -> Optional[str]:
    """The runner's acknowledgement (push_ack): the build time of the latest bundle it holds with no gap
    before it, or None when there is none or it cannot be read, which makes the next bundle whole.
    Fetched into a throwaway repository, like push_bundle."""
    try:
        url = url or _origin_url()
        with tempfile.TemporaryDirectory(prefix="relay-ack-") as tmp:
            git = _scratch_git(tmp)
            if git("fetch", "--quiet", "--depth=1", url, "refs/heads/%s" % branch, check=False).returncode != 0:
                return None
            r = git("show", "FETCH_HEAD:%s" % ACK_NAME, check=False)
            if r.returncode != 0:
                return None
            through = json.loads(r.stdout.decode("utf-8")).get("through")
    except Exception:  # noqa: BLE001  no acknowledgement only makes the bundle whole
        return None
    return through if isinstance(through, str) and delta_since(through, 3650) else None


def seed_refused(conn, main_db, outlets=None, days: int = SEED_DAYS, per_outlet: int = SEED_PER_OUTLET) -> Dict:
    """Queue on the relay the articles the hosted runner found for a relay outlet and could not fetch.

    The relay only fetches what is in its own database, and its database only learns of an article by
    polling the feed. An outlet moved to the relay because its pages refuse the runner (HTTP 403 or 429)
    leaves behind every article the runner already found and was refused: those still on the feed come
    back by discovery, the rest stayed failed for good, and a classified article whose body had to be
    fetched again (fail_reason refetch_failed:http_403) could never be classified again.

    main_db is a read-only copy of the main database on this machine (scripts/db_pull.sh); the relay
    never writes it. Rows the relay already has are left as they are, so each article is tried once
    here and then follows the relay's own retry rules. An outlet whose latest fetch on the relay was
    refused too gets nothing added: the relay machine is refused as well, and asking again would only
    repeat the refusal. Discovery is not counted: the runner counted these articles when it found them."""
    import sqlite3
    from pathlib import Path
    from pipeline import registry
    counts = {"seeded": 0, "outlets": 0, "skipped_refused_here": 0}
    path = Path(main_db) if main_db else None
    if path is None or not path.exists():
        counts["main_db"] = "absent"
        return counts
    outlets = outlets if outlets is not None else registry.load_outlets()
    mine = [o["id"] for o in registry.collectable(outlets, "self_hosted") if registry.fetcher_of(o) == "self_hosted"]
    if not mine:
        return counts
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    main = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    main.row_factory = sqlite3.Row
    try:
        have = {r[1] for r in main.execute("PRAGMA table_info(articles)")}
        cols = [c for c in ("url", "url_hash", "outlet_id", "country", "language", "feed_url", "title", "summary",
                            "author", "published_at", "discovered_at", "gate_relevant", "gate_terms", "day_of_record")
                if c in have]
        reasons = list(REFUSED_REASONS) + ["refetch_%%:%s" % r for r in REFUSED_REASONS]
        like = " OR ".join(["fail_reason LIKE ?"] * len(reasons))
        for oid in mine:
            last = conn.execute("SELECT fail_reason FROM articles WHERE outlet_id=? AND fetched_at IS NOT NULL "
                                "ORDER BY fetched_at DESC LIMIT 1", (oid,)).fetchone()
            if last and last[0] in REFUSED_REASONS:
                counts["skipped_refused_here"] += 1
                continue
            rows = main.execute(
                "SELECT %s FROM articles WHERE outlet_id=? AND gate_relevant=1 AND discovered_at>=? AND (%s) "
                "ORDER BY discovered_at DESC" % (", ".join(cols), like), [oid, since] + reasons).fetchall()
            added = 0
            for r in rows:
                if added >= per_outlet:
                    break
                cur = conn.execute("INSERT OR IGNORE INTO articles (%s, status) VALUES (%s, 'queued')"
                                   % (", ".join(cols), ", ".join("?" * len(cols))), [r[c] for c in cols])
                added += cur.rowcount
            if added:
                counts["outlets"] += 1
                counts["seeded"] += added
    finally:
        main.close()
    conn.commit()
    return counts


def run_relay(conn, budget_minutes: float = 30.0, push: bool = True, main_db=None) -> Dict:
    """Discover and fetch the relay outlets, then bundle and push. main_db (default: TRACKER_MAIN_DB)
    is a read-only copy of the main database, read for the refused articles the relay takes over."""
    from pipeline import fetch_articles, fetch_feeds
    run_id = "relay-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    deadline = time.time() + budget_minutes * 60
    ensure_change_log(conn)      # before anything writes, so every change of this run is logged
    store.prune_gated_out(conn)
    prune_handed_off(conn)
    counts = {"discover": fetch_feeds.run(conn, run_id, deadline=deadline)}
    try:
        # The relay's own copies of items from the section feeds removed on 2026-10-12, so the bundle
        # and its per outlet-day counts carry them as the hosted side does (pipeline/section_retag.py).
        from pipeline import section_retag
        retagged = section_retag.run(conn)
        if retagged:
            counts["section_retag"] = retagged
    except Exception as exc:  # noqa: BLE001  a correction never stops the hour's own collection
        counts["section_retag"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        counts["seed"] = seed_refused(conn, main_db or os.environ.get("TRACKER_MAIN_DB"))
    except Exception as exc:  # noqa: BLE001  the backlog never stops the hour's own collection
        counts["seed"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    counts["fetch"] = fetch_articles.run(conn, run_id, deadline=deadline)
    try:
        # The reference sources marked collector: self_hosted (none yet; pipeline/reference.py).
        from pipeline import reference
        counts["reference"] = {"poll": reference.run_stage(conn, "poll", deadline),
                               "fetch": reference.run_stage(conn, "fetch", deadline)}
    except Exception as exc:  # noqa: BLE001  never stops the hour's own collection
        conn.rollback()
        counts["reference"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    ack = fetch_ack() if push else None
    data = make_bundle(conn, ack=ack)
    from pipeline import bundle_crypto
    counts["bundle_sealed"] = bundle_crypto.is_sealed(data)    # False: in clear, bodies left out
    counts["bundle_bytes"] = len(data)
    counts["bundle_since"] = delta_since(ack)      # None: the whole window
    if push:
        counts["commit"] = push_bundle(data)
    else:
        out = config.ROOT / "data" / BUNDLE_NAME
        out.write_bytes(data)
        counts["written"] = str(out)
    return counts


# ---------------------------------------------------------------------------
# ingest side
# ---------------------------------------------------------------------------

def fetch_bundle(branch: str = RELAY_BRANCH, key=_KEY_FROM_ENV) -> Optional[bytes]:
    """The bundle on the relay branch, in clear, or None when there is none or it cannot be opened.
    A sealed bundle (pipeline/bundle_crypto.py) is opened with RELAY_BUNDLE_KEY (an Actions secret on
    the runner); without the key, or when its tag does not verify, the reason is printed and the run
    goes on as if there were no bundle. A bundle in clear (no key on the relay yet) carries no bodies."""
    r = _git("fetch", "--quiet", "origin", "%s:refs/remotes/origin/%s" % (branch, branch), check=False)
    if r.returncode != 0:
        return None
    r = _git("show", "origin/%s:%s" % (branch, BUNDLE_NAME), check=False)
    return open_bundle(r.stdout, key) if r.returncode == 0 else None


def open_bundle(data: Optional[bytes], key=_KEY_FROM_ENV) -> Optional[bytes]:
    """A fetched bundle in clear: a sealed one opened with the key, one in clear as it is, None when
    a sealed one cannot be opened (no key, a wrong key, a changed bundle)."""
    from pipeline import bundle_crypto
    if not data or not bundle_crypto.is_sealed(data):
        return data
    if key is _KEY_FROM_ENV:
        key = bundle_crypto.load_key()
    try:
        return bundle_crypto.open_sealed(data, key)
    except (bundle_crypto.BundleKeyError, bundle_crypto.BundleAuthError) as exc:
        print("relay ingest: the sealed bundle was not opened: %s" % exc, file=sys.stderr)
        return None


def iter_bundle(data: bytes, problems: Optional[Dict] = None) -> Iterable[Dict]:
    """The bundle's records in order. Without `problems` a damaged bundle raises. With it (ingest), a
    line that is not a JSON object is skipped and counted in problems["malformed_lines"], and a stream
    that ends early (a truncated gzip) stops there with problems["truncated"] set: the records read
    before it are kept."""
    import zlib
    if problems is None:
        with gzip.open(io.BytesIO(data), "rt", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
        return
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as fh:
        while True:
            try:
                line = fh.readline()
            except (EOFError, OSError, zlib.error) as exc:
                problems["truncated"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
                return
            if not line:
                return
            if not line.strip():
                continue
            try:
                item = json.loads(line.decode("utf-8"))
            except ValueError:
                item = None
            if not isinstance(item, dict):
                problems["malformed_lines"] = problems.get("malformed_lines", 0) + 1
                continue
            yield item


def _save_body(item: Dict, counts: Optional[Dict] = None) -> bool:
    """Store a record's body. A body that does not decode (bad base64, a truncated gzip, not UTF-8)
    is left out and counted in counts["body_malformed"]; the article itself is still taken in."""
    import binascii
    import zlib
    if not item.get("body"):
        return False
    try:
        text = gzip.decompress(base64.b64decode(item["body"])).decode("utf-8")
    except (binascii.Error, EOFError, OSError, TypeError, ValueError, zlib.error) as exc:
        if counts is not None:
            counts["body_malformed"] = counts.get("body_malformed", 0) + 1
        print("relay ingest: body of %s left out: %s" % (item.get("url_hash"), type(exc).__name__), file=sys.stderr)
        return False
    store.save_body(item["url_hash"], text)
    return True


def fill_body(conn, row, item: Dict, counts: Optional[Dict] = None) -> bool:
    """A delivered body for a hosted row that changed in nothing else but has no body file: stored when
    the row's recorded body is this very text (a body withheld from an earlier bundle, before the bundle
    key existed, arrives now), or when the row records no body at all (it then takes the relay's body_hash
    and body_chars). A row whose recorded body is another text keeps it: its label was made on that one."""
    if not item.get("body") or row["status"] == "junk_feed" or store.body_path(item["url_hash"]).exists():
        return False
    recorded = row["body_hash"] if "body_hash" in row.keys() else None
    if recorded and recorded != item.get("body_hash"):
        return False
    if not _save_body(item, counts):
        return False
    if not recorded and item.get("body_hash"):
        store.update_article(conn, row["id"], body_hash=item["body_hash"],
                             **({"body_chars": item["body_chars"]} if item.get("body_chars") is not None else {}))
    return True


def update_existing(conn, row, item: Dict) -> Optional[str]:
    """Bring a hosted row forward when the relay's copy of the same article is strictly more
    advanced. Returns what changed, or None when nothing did.

    The relay machine runs the gate and the fetcher on outlets the hosted runner cannot reach, so
    its copy is often further along: an item the hosted side has as gated_out can be gate-relevant
    on the relay because it arrived there on a press release section feed, and an item the hosted
    side failed to fetch can be fetched there. Skipping every url_hash it already had left those
    decisions stranded on the relay machine forever.

    Two things are never done: a hosted classification is never overwritten, and a status never
    goes backwards. Nothing here counts discovery: the row was counted when it was first seen."""
    if row["status"] == "junk_feed":
        return None      # withdrawn: its feed no longer carried its outlet's news (pipeline/junk_feeds.py)
    refetch = "fail_reason" in row.keys() and (row["fail_reason"] or "").startswith("refetch_")
    if refetch and item.get("status") == "fetched" and item.get("body"):
        # The hosted side holds a label or a candidate whose body it had to fetch again and was refused
        # (classify_rules.ensure_body). The relay fetched the page: the body is delivered as that re-fetch
        # would have stored it, and the status, the label and the hosted attempt count stay as they are.
        fields = {k: item[k] for k in ("body_hash", "body_chars", "fetched_at", "http_status", "extract_version")
                  if item.get(k) is not None}
        fields["fail_reason"] = None
        store.update_article(conn, row["id"], **fields)
        return "body"
    if row["status"] == "classified" or conn.execute(
            "SELECT 1 FROM classifications WHERE article_id=? AND is_current=1", (row["id"],)).fetchone():
        return None
    if row["status"] == "gated_out" and not item.get("gate_relevant"):
        return None      # still outside the corpus on both sides; a status alone would not make it in
    if row["status"] == "gated_out" and "fail_reason" in row.keys() and \
            (row["fail_reason"] or "").startswith("section_feed_removed:"):
        # Gated out when its section feed turned out not to be a section (pipeline/section_retag.py).
        # The relay's copy may predate that and still carry the section tag; it must not re-admit it.
        return None
    fields = {}
    changed = []
    if item.get("gate_relevant") and not row["gate_relevant"]:
        # The gate decision the relay made wins: it saw the item on a feed the hosted side does not poll.
        fields["gate_relevant"] = 1
        fields["gate_terms"] = item.get("gate_terms") or "[]"
        if item.get("feed_url"):
            fields["feed_url"] = item["feed_url"]
        changed.append("gate_relevant")
    relay_rank = STATUS_RANK.get(item.get("status"), -1)
    hosted_rank = STATUS_RANK.get(row["status"], -1)
    if relay_rank > hosted_rank:
        for k in ADVANCE_FIELDS:
            if k in item and item[k] is not None:
                fields[k] = item[k]
        if "status" in fields and "fail_reason" in item:
            # A row that moves on to fetched drops the failure it had (http_403 on the runner): a fetched
            # article still carrying it would be counted as refused.
            fields["fail_reason"] = item["fail_reason"]
        # A row promoted out of gated_out goes to the relay's status, which is at least queued.
        changed.append("status")
    elif "gate_relevant" in changed and row["status"] == "gated_out":
        fields["status"] = "queued"
        changed.append("status")
    if not fields:
        return None
    store.update_article(conn, row["id"], **fields)
    return ",".join(changed)


def prune_handed_off(conn, outlets=None, days: int = BUNDLE_DAYS + 1) -> int:
    """On the relay: drop the unfetched articles of outlets whose pages the runner fetches (collector:
    both) once they are older than the bundle window. The relay polls those feeds but never fetches
    their pages, so their queued rows would otherwise pile up here; every bundle in the window carried
    them to the runner already. Their addresses are remembered (store.seen_urls), so a feed that still
    lists one does not bring it back here and count it again."""
    from pipeline import registry
    outlets = outlets if outlets is not None else registry.load_outlets()
    handed = [o["id"] for o in outlets if registry.polled_by(o, config.COLLECTOR)
              and registry.fetcher_of(o) != config.COLLECTOR]
    if not handed:
        return 0
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    where = """status IN ('queued', 'discovered') AND discovered_at < ? AND outlet_id IN (%s)
           AND id NOT IN (SELECT article_id FROM classifications)
           AND id NOT IN (SELECT article_id FROM planted_flags)""" % ",".join("?" * len(handed))
    store.remember_pruned(conn, where, [cutoff] + handed)
    cur = conn.execute("DELETE FROM articles WHERE " + where, [cutoff] + handed)
    conn.commit()
    return cur.rowcount


def reconcile_poll(conn, ev: Dict) -> Optional[Dict]:
    """Take one relay poll of a shared feed (collector: both) into the main database, judged against
    the previous poll by either collector. Returns None when it was taken in before.

    The relay judged the poll against its own previous poll only (saturated_own, missed_own). Here the
    previous poll is the later of that and the latest poll of the feed already logged: a runner poll in
    between means the window had only to reach back to it (fetch_feeds.rejudge). The poll is added to
    the relay's feed_polls row for its day once, keyed in feed_poll_log, so a bundle sent again counts
    nothing twice. A runner poll that this relay poll came before, judged saturated without knowing of
    it, is judged again against it, and the runner's feed_polls row is corrected by the difference."""
    from pipeline.fetch_feeds import _aware, rejudge
    feed, at = ev["feed_url"], ev["polled_at"]
    if conn.execute("SELECT 1 FROM feed_poll_log WHERE feed_url=? AND polled_at=? AND collector=?",
                    (feed, at, RELAY_COLLECTOR)).fetchone():
        return None
    times = ev.get("times")
    times = json.loads(times) if isinstance(times, str) else (times or [])
    entries, new_items = int(ev.get("entries") or 0), int(ev.get("new_items") or 0)
    sat_own, miss_own = bool(ev.get("saturated_own")), float(ev.get("missed_own") or 0.0)
    prev, sat, miss = ev.get("prev_poll_at"), sat_own, miss_own
    # The relay's estimate was made by its own estimator version (an event from a relay older than the
    # field, v1); one judged again here is made by this side's.
    estimator = ev.get("estimator") or "v1"
    before = store.last_poll_at(conn, feed, before=at)
    if before and (not prev or (_aware(before) and _aware(prev) and _aware(before) > _aware(prev))):
        prev = before
        if sat:
            sat, miss = rejudge(conn, {"feed_url": feed, "polled_at": at, "times": json.dumps(times),
                                       "entries": entries}, before)
            estimator = config.MISSED_ESTIMATOR_VERSION
    store.log_feed_poll(conn, feed, RELAY_COLLECTOR, at, ev["outlet_id"], ev["country"], entries, new_items, times,
                        prev, sat_own, miss_own, sat, miss, estimator=estimator)
    conn.execute(
        """INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, saturated, entries, new_items,
                                  missed_estimate, collector, estimator) VALUES (?,?,?,?,1,?,?,?,?,?,?)
           ON CONFLICT(date, feed_url, collector) DO UPDATE SET outlet_id=excluded.outlet_id, country=excluded.country,
             polls=polls+1, saturated=saturated+excluded.saturated, entries=entries+excluded.entries,
             new_items=new_items+excluded.new_items, missed_estimate=missed_estimate+excluded.missed_estimate,
             """ + store.ESTIMATOR_MERGE,
        (at[:10], feed, ev["outlet_id"], ev["country"], 1 if sat else 0, entries, new_items, miss or 0.0, RELAY_COLLECTOR,
         estimator))
    rejudged = 0
    later = conn.execute(
        """SELECT * FROM feed_poll_log WHERE feed_url=? AND polled_at>? AND saturated=1
           AND (prev_poll_at IS NULL OR prev_poll_at<?)""", (feed, at, at)).fetchall()
    for row in later:
        s2, m2 = rejudge(conn, row, at)
        # The feed_polls row is corrected by the difference whatever its estimator: a row holding an
        # older estimate is left out of the published estimate anyway.
        conn.execute("UPDATE feed_poll_log SET prev_poll_at=?, saturated=?, missed_estimate=?, estimator=? "
                     "WHERE feed_url=? AND polled_at=? AND collector=?",
                     (at, 1 if s2 else 0, m2, config.MISSED_ESTIMATOR_VERSION, feed, row["polled_at"], row["collector"]))
        conn.execute("UPDATE feed_polls SET saturated=MAX(0, saturated-?), missed_estimate=MAX(0, missed_estimate+?) "
                     "WHERE date=? AND feed_url=? AND collector=?",
                     (0 if s2 else 1, (m2 or 0.0) - (row["missed_estimate"] or 0.0), row["polled_at"][:10], feed,
                      row["collector"]))
        rejudged += 1
    return {"saturated_own": sat_own, "saturated": sat, "rejudged": rejudged}


# What a malformed record raises while it is taken in (a missing field, a value of the wrong type, a value
# SQLite will not bind or a constraint refuses). A locked or full database is not among them: that stops
# the ingest as before.
_MALFORMED_RECORD = (KeyError, TypeError, ValueError, AttributeError, IndexError,
                     sqlite3.IntegrityError, sqlite3.InterfaceError)


def ingest(conn, data: bytes, outlets=None) -> Dict:
    """Insert every article the main database does not know, and bring forward the ones whose relay
    copy is further along. Discovery is never counted twice.

    For a shared outlet (collector: both) the runner polls the same feeds, so the relay's per
    outlet-day discovery rows, feed health and daily poll totals cannot replace the runner's: discovery
    is counted here when an article is first inserted (the runner counted the ones it found first),
    the runner keeps its own feed health, and the relay's polls arrive one by one (reconcile_poll)."""
    from pipeline import registry
    from pipeline.fetch_feeds import link_near_duplicates
    counts = {"seen": 0, "inserted": 0, "bodies": 0, "relevant": 0, "already_delivered": 0, "updated": 0}
    from pipeline import junk_feeds
    source = outlets if outlets is not None else registry.load_outlets()
    shared = {o["id"] for o in source if registry.is_shared(o)}
    # Outlet days a junk feed's items were withdrawn from (pipeline/junk_feeds.py): the relay's own rows
    # still count those items.
    junk = junk_feeds.windows(source)
    # A gated-out article of a shared outlet older than this may be one the runner found itself and has
    # pruned since (prune_gated_out): inserting it again would count it again. A day of margin covers a
    # relay that saw it somewhat later than the runner did.
    stale_gated = _stale_gated_cutoff(dt.datetime.now(dt.timezone.utc))
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)).isoformat()
    conn.execute("DELETE FROM relay_seen WHERE seen_at < ?", (cutoff,))
    meta = None
    # Each record is taken in under its own savepoint: one that cannot be (bad JSON, a missing field,
    # a value of the wrong type) is rolled back alone and counted in skipped_malformed, and the rest of
    # the bundle goes on. A damaged bundle (skipped lines, a truncated stream) holds the acknowledgement,
    # so the next bundle sends the window again; taking a record in twice changes nothing.
    problems = {}
    for item in iter_bundle(data, problems):
        if not conn.in_transaction:
            conn.execute("BEGIN")
        conn.execute("SAVEPOINT relay_record")
        commit_due, counted = False, dict(counts)
        try:
            if item.get("_type") == "bundle_meta":
                meta = item
                continue
            if item.get("_type") in ("outlet_discovery", "feed_health", "feed_poll") and item.get("outlet_id") in shared:
                counts["shared_skipped"] = counts.get("shared_skipped", 0) + 1
                continue
            if item.get("_type") == "feed_poll_event":
                done = reconcile_poll(conn, item)
                if done is not None:
                    counts["shared_polls"] = counts.get("shared_polls", 0) + 1
                    counts["shared_polls_saturated"] = counts.get("shared_polls_saturated", 0) + (1 if done["saturated"] else 0)
                    counts["shared_polls_rejudged"] = counts.get("shared_polls_rejudged", 0) + done["rejudged"]
                continue
            if item.get("_type") == "outlet_discovery":
                item = junk_feeds.relay_outlet_day(conn, item, junk)
                if item is None:
                    counts["junk_outlet_days"] = counts.get("junk_outlet_days", 0) + 1
                    continue
                conn.execute(
                    """INSERT INTO daily_outlet_discovery(date, outlet_id, country, discovered, gate_relevant) VALUES (?,?,?,?,?)
                       ON CONFLICT(date, outlet_id) DO UPDATE SET country=excluded.country, discovered=excluded.discovered,
                         gate_relevant=excluded.gate_relevant""",
                    (item["date"], item["outlet_id"], item["country"], item["discovered"], item.get("gate_relevant", 0)))
                counts["outlet_days"] = counts.get("outlet_days", 0) + 1
                continue
            if item.get("_type") == "relay_run":
                conn.execute("INSERT OR IGNORE INTO relay_runs(started_at, finished_at, ok) VALUES (?,?,?)",
                             (item["started_at"], item.get("finished_at"), item.get("ok")))
                conn.execute("UPDATE relay_runs SET finished_at=?, ok=? WHERE started_at=? AND finished_at IS NULL",
                             (item.get("finished_at"), item.get("ok"), item["started_at"]))
                counts["relay_runs"] = counts.get("relay_runs", 0) + 1
                continue
            if item.get("_type") == "feed_health":
                conn.execute(
                    """INSERT INTO feed_health(feed_url, outlet_id, last_checked, last_ok, last_error, last_entries,
                       consecutive_failures, total_checks, total_failures, newest_entry) VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(feed_url) DO UPDATE SET outlet_id=excluded.outlet_id, last_checked=excluded.last_checked,
                         last_ok=excluded.last_ok, last_error=excluded.last_error, last_entries=excluded.last_entries,
                         consecutive_failures=excluded.consecutive_failures, total_checks=excluded.total_checks,
                         total_failures=excluded.total_failures,
                         newest_entry=COALESCE(excluded.newest_entry, feed_health.newest_entry)""",
                    (item["feed_url"], item["outlet_id"], item["last_checked"], item["last_ok"], item["last_error"],
                     item["last_entries"], item["consecutive_failures"], item["total_checks"], item["total_failures"],
                     item.get("newest_entry")))
                counts["feeds"] = counts.get("feeds", 0) + 1
                continue
            if item.get("_type") == "feed_poll":
                # Keyed by day, feed and collector, and replaced rather than added to: the relay's row is
                # its running total for the day, so a re-sent or later bundle never counts a poll twice,
                # and the runner's own polls of the same feed stay in their own row.
                conn.execute(
                    """INSERT INTO feed_polls(date, feed_url, outlet_id, country, polls, saturated, entries, new_items,
                                              missed_estimate, collector, estimator) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(date, feed_url, collector) DO UPDATE SET outlet_id=excluded.outlet_id,
                         country=excluded.country, polls=excluded.polls, saturated=excluded.saturated,
                         entries=excluded.entries, new_items=excluded.new_items, missed_estimate=excluded.missed_estimate,
                         estimator=excluded.estimator""",
                    (item["date"], item["feed_url"], item["outlet_id"], item["country"], item.get("polls", 0),
                     item.get("saturated", 0), item.get("entries", 0), item.get("new_items", 0),
                     item.get("missed_estimate", 0.0), RELAY_COLLECTOR, item.get("estimator") or "v1"))
                counts["feed_poll_days"] = counts.get("feed_poll_days", 0) + 1
                continue
            if item.get("_type") == "reference_item":
                # A reference source item (pipeline/reference.py): into reference_items only, never an article.
                from pipeline import reference
                done = reference.ingest_record(conn, item)
                counts["reference_" + done] = counts.get("reference_" + done, 0) + 1
                continue
            if item.get("_type") or "url_hash" not in item:
                # A record type this version does not know, from a newer relay. Skip it rather than fail
                # the whole run: on 2026-09-14 a bundle with a new record type stopped two hourly runs.
                counts["skipped_unknown"] = counts.get("skipped_unknown", 0) + 1
                continue
            counts["seen"] += 1
            existing = conn.execute(
                "SELECT id, status, gate_relevant, country, title, fail_reason, body_hash FROM articles WHERE url_hash=?", (item["url_hash"],)).fetchone()
            if existing:
                changed = update_existing(conn, existing, item)
                if changed:
                    counts["updated"] += 1
                    if "gate_relevant" in changed:
                        counts["promoted_relevant"] = counts.get("promoted_relevant", 0) + 1
                        link_near_duplicates(conn, existing["id"], item.get("title") or existing["title"] or "",
                                             item.get("country") or existing["country"])
                    if _save_body(item, counts):
                        counts["bodies"] += 1
                    commit_due = True
                elif fill_body(conn, existing, item, counts):
                    counts["bodies"] += 1
                    counts["bodies_filled"] = counts.get("bodies_filled", 0) + 1
                    commit_due = True
                continue
            if conn.execute("SELECT 1 FROM relay_seen WHERE url_hash=?", (item["url_hash"],)).fetchone():
                counts["already_delivered"] += 1   # delivered earlier and pruned since; not a new item
                continue
            # An article this database held and pruned (store.seen_urls): outside the gate it stays out and
            # is not counted again; inside it now, it is taken in on the day its discovery was counted here.
            seen = store.seen_before(conn, item["url_hash"])
            if seen is not None and (seen[1] or not item.get("gate_relevant")):
                counts["seen_before"] = counts.get("seen_before", 0) + 1
                continue
            seen_day = seen[0] if seen is not None else None
            if seen_day is not None:
                item = dict(item, day_of_record=seen_day)
                store.forget_seen(conn, item["url_hash"])
            is_shared = item.get("outlet_id") in shared
            if is_shared and not item.get("gate_relevant") and item.get("status") == "gated_out" \
                    and (item.get("discovered_at") or "") < stale_gated:
                counts["shared_stale_gated"] = counts.get("shared_stale_gated", 0) + 1
                continue
            cols = [k for k in ARTICLE_FIELDS if k in item]
            conn.execute("INSERT INTO articles (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" * len(cols))),
                         [item[k] for k in cols])
            new_id = conn.execute("SELECT id FROM articles WHERE url_hash=?", (item["url_hash"],)).fetchone()[0]
            conn.execute("INSERT OR REPLACE INTO relay_seen(url_hash, seen_at) VALUES (?,?)", (item["url_hash"], store.utcnow()))
            counts["inserted"] += 1
            relevant = bool(item.get("gate_relevant"))
            # Discovery counts for relay outlets come from the bundle's own per outlet-day rows above; a
            # shared outlet's are counted here, once, on the day the relay found the article.
            if is_shared and seen_day is not None:
                store.record_relevant_again(conn, item["country"], item["outlet_id"], seen_day)
            elif is_shared:
                store.record_discovery(conn, item["country"], relevant, item["outlet_id"],
                                       item.get("day_of_record") or (item.get("discovered_at") or "")[:10] or None)
            if _save_body(item, counts):
                counts["bodies"] += 1
            if relevant:
                counts["relevant"] += 1
                link_near_duplicates(conn, new_id, item.get("title") or "", item["country"])
            if counts["inserted"] % 200 == 0:
                commit_due = True
        except _MALFORMED_RECORD as exc:
            conn.execute("ROLLBACK TO relay_record")
            counts.clear()
            counts.update(counted)
            counts["skipped_malformed"] = counts.get("skipped_malformed", 0) + 1
            counts["last_malformed"] = "%s: %s: %s" % (item.get("_type") or "article", type(exc).__name__, str(exc)[:160])
        finally:
            conn.execute("RELEASE relay_record")
            if commit_due:
                conn.commit()
    if problems.get("malformed_lines"):
        counts["skipped_malformed"] = counts.get("skipped_malformed", 0) + problems["malformed_lines"]
    if problems.get("truncated"):
        counts["bundle_truncated"] = problems["truncated"]
    conn.commit()
    if meta is not None and (counts.get("skipped_malformed") or counts.get("bundle_truncated")):
        counts["ack_held"] = True
    elif meta is not None:
        counts.update(_advance_ack(conn, meta))
    return counts


def _ack_through(conn) -> Optional[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS relay_ack (id INTEGER PRIMARY KEY CHECK (id = 1), through TEXT NOT NULL, "
                 "updated_at TEXT NOT NULL)")
    row = conn.execute("SELECT through FROM relay_ack WHERE id = 1").fetchone()
    return row[0] if row else None


def _advance_ack(conn, meta: Dict) -> Dict:
    """After a whole bundle is in: move the acknowledgement on to its build time, but only when it follows
    on from what this database holds. A bundle of changes since a time later than the acknowledgement
    stored here (the database came back from an older cache or the snapshot) leaves a gap: its rows are
    taken in, the acknowledgement stays, and push_ack sends that back, so the next bundle covers the gap.
    The acknowledgement lives in this database, so it goes back whenever the database does."""
    prior = _ack_through(conn)
    since, built = meta.get("since"), meta.get("built_at")
    out = {"bundle": "changes" if since else "window"}
    if not built:
        return out
    if since and (prior is None or since > prior):
        out["bundle_gap_after"] = prior
        return out
    if prior is None or built > prior:
        conn.execute("INSERT INTO relay_ack(id, through, updated_at) VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE "
                     "SET through=excluded.through, updated_at=excluded.updated_at", (built, store.utcnow()))
        conn.commit()
        prior = built
    out["acked_through"] = prior
    return out


def push_ack(conn) -> Dict:
    """On the runner, once the database is saved: force-push the acknowledgement, whatever it is. No
    acknowledgement is sent too ("through": null), so a relay that was told more than this database still
    holds sends the whole window again. The commit is dated by the acknowledgement, so an unchanged one is
    the same commit and the push changes nothing."""
    through = _ack_through(conn)
    conn.commit()
    body = json.dumps({"through": through}).encode("utf-8")
    commit = _single_file_commit(_git, ACK_NAME, body, "relay ack %s" % through,
                                 date=through or "2026-01-01T00:00:00+00:00")
    _git("push", "--force", "--quiet", "origin", "%s:refs/heads/%s" % (commit, ACK_BRANCH))
    return {"through": through, "commit": commit}


def cmd_ingest(conn) -> Dict:
    log_id = store.start_stage(conn, "ingest", "relay_ingest")
    data = fetch_bundle()
    if data is None:
        counts = {"bundle": "absent"}
    else:
        counts = ingest(conn, data)
    store.finish_stage(conn, log_id, True, counts)
    print("relay ingest:", json.dumps(counts))
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "bundle", "ingest", "ack"])
    ap.add_argument("--budget-minutes", type=float, default=30.0)
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)
    conn = store.connect()
    if args.cmd == "run":
        print(json.dumps(run_relay(conn, args.budget_minutes, push=not args.no_push)))
    elif args.cmd == "bundle":
        data = make_bundle(conn, ack=None if args.no_push else fetch_ack())
        print(json.dumps({"commit": push_bundle(data)} if not args.no_push else {"bytes": len(data)}))
    elif args.cmd == "ack":
        print("relay ack:", json.dumps(push_ack(conn)))
    else:
        cmd_ingest(conn)


if __name__ == "__main__":
    main()
