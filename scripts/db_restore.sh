#!/bin/bash
# Restore the working database on a runner. Order: already present (Actions cache) and whole, the daily
# release snapshot, the seed commit that last carried the database in git, else start fresh.
#
# The database runs with journal_mode=DELETE, so the cache keeps data/tracker.db-journal beside it: a
# writer killed mid transaction (a timeout, a cancelled run) leaves a hot journal, and SQLite rolls it
# back the first time the file is opened. The check below opens it first, with PRAGMA quick_check; a
# cached copy that still fails is set aside and the snapshot is used instead, so a torn file is never
# carried forward from run to run.
#
# The snapshot release holds one asset this restore reads (scripts/db_snapshot.sh): tracker.db.gz, the
# public copy, scrubbed of bylines, summaries, raw model output and reviewer names. The whole database
# and the article bodies are never published, so on a cache miss old rows lose their bylines, summaries
# and raw model output, and bodies are fetched again where they can be. Encrypted copies once sealed
# with the relay's key are ignored: the relay was retired on 2026-10-22 and the key is never to be set.
# When a snapshot exists but cannot be read after three tries the step fails: a silent fall back to the
# September seed once published regressed totals.
set -euo pipefail
cd "$(dirname "$0")/.."
SQLITE3="${SQLITE3:-sqlite3}"
PY="${PYTHON:-$(command -v python3 || command -v python || echo python3)}"
RELEASE="${TRACKER_SNAPSHOT_RELEASE:-db-snapshot}"
RETRY_SLEEP="${RETRY_SLEEP:-10}"
DB=data/tracker.db
mkdir -p data data/bodies
WORK="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/db-restore.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

db_ok() {  # PRAGMA quick_check says ok (it also rolls back a hot journal first)
  local out
  if command -v "$SQLITE3" >/dev/null 2>&1; then
    out="$("$SQLITE3" -batch "$1" "PRAGMA quick_check;" 2>&1)" || return 1
  else
    out="$("$PY" -c 'import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print("\n".join(str(r[0]) for r in c.execute("PRAGMA quick_check")))' "$1" 2>&1)" || return 1
  fi
  [ "$out" = "ok" ]
}

set_aside() {  # drop the database and any journal beside it, so a stale journal never meets a new file
  rm -f "$DB" "$DB-journal"
}

retry() {  # run "$@" up to three times
  local n
  for n in 1 2 3; do
    if "$@"; then return 0; fi
    echo "attempt $n of 3 failed: $1 $2 $3" >&2
    [ "$n" -lt 3 ] && sleep "$RETRY_SLEEP"
  done
  return 1
}

ASSETS=""
snapshot_assets() {  # the release's asset names into ASSETS; 2 when there is no release at all
  local n
  for n in 1 2 3; do
    if ASSETS="$(gh release view "$RELEASE" --json assets -q '.assets[].name' 2>"$WORK/err")"; then return 0; fi
    if grep -qi "not found" "$WORK/err"; then return 2; fi
    echo "attempt $n of 3 to list the snapshot failed: $(head -c 300 "$WORK/err")" >&2
    [ "$n" -lt 3 ] && sleep "$RETRY_SLEEP"
  done
  return 1
}

has_asset() { printf '%s\n' "$ASSETS" | grep -qx "$1"; }

download() { retry gh release download "$RELEASE" -p "$1" -D "$WORK" --clobber; }

bodies_note() {  # the snapshot never carries the article bodies
  echo "::notice::the snapshot carries no article bodies; articles without a body file are fetched again where they can be"
}

# 1. The Actions cache, when it brought a whole database.
if [ -s "$DB" ]; then
  if db_ok "$DB"; then
    echo "database present ($(du -h "$DB" | cut -f1)), from cache, quick_check ok"
    rm -f "$DB-journal"
    # A whole database without bodies keeps going: the snapshot has none to give.
    exit 0
  fi
  echo "::warning::the cached database fails PRAGMA quick_check; it is set aside and the snapshot is used"
  set_aside
fi
set_aside

# 2. The release snapshot.
if ! command -v gh >/dev/null; then
  echo "::error::gh is not installed, so the snapshot cannot be checked"; exit 1
fi
rc=0; snapshot_assets || rc=$?
if [ "$rc" -eq 1 ]; then
  echo "::error::the snapshot release could not be read after three tries; not falling back to the seed"; exit 1
fi
if [ "$rc" -eq 0 ] && has_asset tracker.db.gz; then
  download tracker.db.gz || { echo "::error::the snapshot exists but could not be downloaded"; exit 1; }
  if ! gunzip -c "$WORK/tracker.db.gz" > "$DB"; then set_aside; echo "::error::the snapshot is damaged"; exit 1; fi
  if ! db_ok "$DB"; then set_aside; echo "::error::the restored snapshot fails PRAGMA quick_check"; exit 1; fi
  echo "database restored from the public snapshot ($(du -h "$DB" | cut -f1)), quick_check ok"
  bodies_note
  exit 0
fi

# 3. No snapshot was ever published: the seed commit, else a fresh database.
SEED="${TRACKER_DB_SEED_COMMIT:-b1e5f36}"
if git cat-file -e "$SEED:data/tracker.db" 2>/dev/null; then
  git show "$SEED:data/tracker.db" > "$DB" && echo "no snapshot release; database restored from seed commit $SEED" && exit 0
fi
echo "no database found; a fresh one will be created"
