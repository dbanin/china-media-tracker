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
# The snapshot release holds up to three assets (scripts/db_snapshot.sh): tracker.db.gz, the public
# copy, scrubbed of bylines, summaries, raw model output and reviewer names; and, only when the
# RELAY_BUNDLE_KEY secret is set, tracker-full.db.gz.enc and bodies.tar.gz.enc, the whole database and
# the article bodies, encrypted with it. On a cache miss the encrypted whole copy is preferred, then the
# public one. When a snapshot exists but cannot be read after three tries the step fails: a silent fall
# back to the September seed once published regressed totals.
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

# The key as both sides use it: surrounding white space dropped, and shorter than 32 characters is none
# (the same rule as pipeline/bundle_crypto.py). Never printed.
SNAPSHOT_PASS="$(printf '%s' "${RELAY_BUNDLE_KEY:-}" | tr -d '[:space:]')"
[ "${#SNAPSHOT_PASS}" -ge 32 ] || SNAPSHOT_PASS=""
export SNAPSHOT_PASS

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

decrypt() {  # $1 encrypted file -> stdout
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -md sha256 -pass env:SNAPSHOT_PASS -in "$1"
}

restore_bodies() {  # merge the encrypted bodies archive into data/bodies, never replacing a file there
  if ! has_asset bodies.tar.gz.enc; then
    echo "::warning::the snapshot carries no article bodies; articles without a body file are fetched again where they can be"
    return 0
  fi
  if [ -z "$SNAPSHOT_PASS" ]; then
    echo "::warning::the snapshot's bodies are encrypted and RELAY_BUNDLE_KEY is not set; they are not restored"
    return 0
  fi
  download bodies.tar.gz.enc || { echo "::error::the snapshot's bodies exist but could not be downloaded"; return 1; }
  mkdir -p "$WORK/bodies-out"
  if ! decrypt "$WORK/bodies.tar.gz.enc" | tar -xzf - -C "$WORK/bodies-out"; then
    echo "::error::the snapshot's bodies could not be decrypted (wrong RELAY_BUNDLE_KEY?) or are damaged"
    return 1
  fi
  local before after
  before=$(find data/bodies -type f | wc -l | tr -d ' ')
  if [ -d "$WORK/bodies-out/bodies" ] && [ "$before" -eq 0 ]; then
    rm -rf data/bodies && mv "$WORK/bodies-out/bodies" data/bodies
  elif [ -d "$WORK/bodies-out/bodies" ]; then
    (cd "$WORK/bodies-out/bodies" && find . -type f) | while IFS= read -r f; do
      [ -e "data/bodies/$f" ] && continue
      mkdir -p "data/bodies/$(dirname "$f")"
      mv "$WORK/bodies-out/bodies/$f" "data/bodies/$f"
    done
  fi
  after=$(find data/bodies -type f | wc -l | tr -d ' ')
  echo "bodies restored from the snapshot: $((after - before)) files added, $after in all"
}

# 1. The Actions cache, when it brought a whole database.
if [ -s "$DB" ]; then
  if db_ok "$DB"; then
    echo "database present ($(du -h "$DB" | cut -f1)), from cache, quick_check ok"
    rm -f "$DB-journal"
    if [ -n "$(find data/bodies -type f -print -quit)" ]; then exit 0; fi
    # A whole database but no bodies: take them from the snapshot, if it has any, and go on.
    if ! command -v gh >/dev/null; then echo "::warning::no bodies and no gh to fetch them"; exit 0; fi
    rc=0; snapshot_assets || rc=$?
    if [ "$rc" -eq 0 ]; then restore_bodies || echo "::warning::the bodies were not restored; the database is kept"; fi
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
if [ "$rc" -eq 0 ] && { has_asset tracker-full.db.gz.enc || has_asset tracker.db.gz; }; then
  if has_asset tracker-full.db.gz.enc && [ -n "$SNAPSHOT_PASS" ]; then
    download tracker-full.db.gz.enc || { echo "::error::the snapshot exists but could not be downloaded"; exit 1; }
    if ! decrypt "$WORK/tracker-full.db.gz.enc" | gunzip -c > "$DB"; then
      set_aside; echo "::error::the encrypted snapshot could not be decrypted (wrong RELAY_BUNDLE_KEY?) or is damaged"; exit 1
    fi
    source="encrypted whole snapshot"
  elif has_asset tracker.db.gz; then
    [ -n "$SNAPSHOT_PASS" ] || ! has_asset tracker-full.db.gz.enc || \
      echo "::warning::RELAY_BUNDLE_KEY is not set, so the public scrubbed snapshot is used: old rows lose their bylines, summaries and raw model output"
    download tracker.db.gz || { echo "::error::the snapshot exists but could not be downloaded"; exit 1; }
    if ! gunzip -c "$WORK/tracker.db.gz" > "$DB"; then set_aside; echo "::error::the snapshot is damaged"; exit 1; fi
    source="public snapshot"
  else
    echo "::error::the snapshot is encrypted and RELAY_BUNDLE_KEY is not set"; exit 1
  fi
  if ! db_ok "$DB"; then set_aside; echo "::error::the restored snapshot fails PRAGMA quick_check"; exit 1; fi
  echo "database restored from the $source ($(du -h "$DB" | cut -f1)), quick_check ok"
  restore_bodies
  exit 0
fi

# 3. No snapshot was ever published: the seed commit, else a fresh database.
SEED="${TRACKER_DB_SEED_COMMIT:-b1e5f36}"
if git cat-file -e "$SEED:data/tracker.db" 2>/dev/null; then
  git show "$SEED:data/tracker.db" > "$DB" && echo "no snapshot release; database restored from seed commit $SEED" && exit 0
fi
echo "no database found; a fresh one will be created"
