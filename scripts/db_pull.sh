#!/bin/bash
# Replace the local working database with the latest daily snapshot published by the
# export workflow, keeping a backup of the local file. The runner's database is the
# canonical one; run this before any local review, agreement study or reclassification
# so local tooling does not work on a stale copy.
#
# The release holds one copy (scripts/db_snapshot.sh): tracker.db.gz, a public copy scrubbed of
# bylines, summaries, raw model output and reviewer names. The whole database is never published:
# the relay's key that could once have sealed it was retired with the relay on 2026-10-22.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${TRACKER_REPO:-dbanin/china-media-tracker}"
RELEASE="${TRACKER_SNAPSHOT_RELEASE:-db-snapshot}"
BASE="https://github.com/$REPO/releases/download/$RELEASE"
mkdir -p data
WORK="$(mktemp -d "${TMPDIR:-/tmp}/db-pull.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

fetch() {  # $1 asset name -> $WORK/$1
  echo "downloading $BASE/$1"
  if command -v gh >/dev/null 2>&1; then
    gh release download "$RELEASE" -p "$1" -D "$WORK" --clobber -R "$REPO"
  else
    curl -fL --retry 3 -o "$WORK/$1" "$BASE/$1"
  fi
}

echo "pulling the public scrubbed copy: its articles have no authors or summaries, its labels no raw model output, and reviewers are pseudonyms"
fetch tracker.db.gz
gunzip -c "$WORK/tracker.db.gz" > "$WORK/tracker.db"
source="public scrubbed copy"

if [ -s data/tracker.db ]; then
  cp data/tracker.db "data/tracker.db.local-$(date -u +%Y%m%dT%H%M%SZ).bak"
fi
rm -f data/tracker.db-journal
mv "$WORK/tracker.db" data/tracker.db
echo "local database replaced from the $source ($(du -h data/tracker.db | cut -f1)); previous copy kept as data/tracker.db.local-*.bak"
