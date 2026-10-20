#!/bin/bash
# Replace the local working database with the latest daily snapshot published by the
# export workflow, keeping a backup of the local file. The runner's database is the
# canonical one; run this before any local review, agreement study or reclassification
# so local tooling does not work on a stale copy.
#
# The release holds two copies (scripts/db_snapshot.sh): tracker-full.db.gz.enc, the whole database
# encrypted with the relay key, and tracker.db.gz, a public copy scrubbed of bylines, summaries, raw
# model output and reviewer names. The whole copy is preferred; the key is read from RELAY_BUNDLE_KEY,
# else from the file RELAY_BUNDLE_KEY_FILE names (by default ~/.config/china-media-tracker/
# relay-bundle.key, the relay's own). Without a usable key, or when the release has no whole copy,
# the scrubbed copy is pulled and a message says what it lacks. The key is never printed or put on a
# command line: openssl reads it from the environment.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${TRACKER_REPO:-dbanin/china-media-tracker}"
RELEASE="${TRACKER_SNAPSHOT_RELEASE:-db-snapshot}"
BASE="https://github.com/$REPO/releases/download/$RELEASE"
mkdir -p data
WORK="$(mktemp -d "${TMPDIR:-/tmp}/db-pull.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

raw="${RELAY_BUNDLE_KEY:-}"
if [ -z "$(printf '%s' "$raw" | tr -d '[:space:]')" ]; then
  keyfile="${RELAY_BUNDLE_KEY_FILE:-$HOME/.config/china-media-tracker/relay-bundle.key}"
  raw="$(cat "$keyfile" 2>/dev/null || true)"
fi
SNAPSHOT_PASS="$(printf '%s' "$raw" | tr -d '[:space:]')"
unset raw
[ "${#SNAPSHOT_PASS}" -ge 32 ] || SNAPSHOT_PASS=""
export SNAPSHOT_PASS

has_whole() {  # 0 when the release carries the whole encrypted copy, 1 when it does not, 2 when unknown
  if command -v gh >/dev/null 2>&1; then
    local names
    names="$(gh release view "$RELEASE" --json assets -q '.assets[].name' -R "$REPO" 2>/dev/null)" || return 2
    printf '%s\n' "$names" | grep -qx tracker-full.db.gz.enc && return 0
    return 1
  fi
  local code
  code="$(curl -sIL -o /dev/null -w '%{http_code}' "$BASE/tracker-full.db.gz.enc" 2>/dev/null || true)"
  [ "$code" = 200 ] && return 0
  [ "$code" = 404 ] && return 1
  return 2
}

fetch() {  # $1 asset name -> $WORK/$1
  echo "downloading $BASE/$1"
  if command -v gh >/dev/null 2>&1; then
    gh release download "$RELEASE" -p "$1" -D "$WORK" --clobber -R "$REPO"
  else
    curl -fL --retry 3 -o "$WORK/$1" "$BASE/$1"
  fi
}

source=""
if [ -n "$SNAPSHOT_PASS" ]; then
  rc=0; has_whole || rc=$?
  if [ "$rc" -eq 0 ]; then
    fetch tracker-full.db.gz.enc
    if openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -md sha256 -pass env:SNAPSHOT_PASS \
         -in "$WORK/tracker-full.db.gz.enc" 2>/dev/null | gunzip -c > "$WORK/tracker.db" 2>/dev/null; then
      source="whole database (decrypted)"
    else
      echo "the whole copy could not be decrypted: the key does not match the RELAY_BUNDLE_KEY secret, or the file is damaged"
    fi
  elif [ "$rc" -eq 1 ]; then
    echo "the release has no whole copy (tracker-full.db.gz.enc): the runner has no RELAY_BUNDLE_KEY secret yet"
  else
    echo "could not tell whether the release has a whole copy"
  fi
else
  echo "no relay key (RELAY_BUNDLE_KEY, or the file ${RELAY_BUNDLE_KEY_FILE:-~/.config/china-media-tracker/relay-bundle.key}, of 32 characters or more)"
fi
if [ -z "$source" ]; then
  echo "pulling the public scrubbed copy instead: its articles have no authors or summaries, its labels no raw model output, and reviewers are pseudonyms"
  fetch tracker.db.gz
  gunzip -c "$WORK/tracker.db.gz" > "$WORK/tracker.db"
  source="public scrubbed copy"
fi

if [ -s data/tracker.db ]; then
  cp data/tracker.db "data/tracker.db.local-$(date -u +%Y%m%dT%H%M%SZ).bak"
fi
rm -f data/tracker.db-journal
mv "$WORK/tracker.db" data/tracker.db
echo "local database replaced from the $source ($(du -h data/tracker.db | cut -f1)); previous copy kept as data/tracker.db.local-*.bak"
