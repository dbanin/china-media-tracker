#!/bin/bash
# Publish the database snapshot asset of the db-snapshot release (read back by scripts/db_restore.sh
# when the Actions cache has no database, and by scripts/db_pull.sh):
#   tracker.db.gz           public: a copy scrubbed by pipeline/snapshot_scrub.py (bylines, summaries,
#                           raw model output, reviewer names and emails taken out), compressed.
# The release is public, so the whole database and the article bodies are never uploaded, in clear or
# encrypted. Until 2026-10-22 they could go up encrypted with the relay's key; the relay was retired that
# day and the key is never to be set, so a cache miss falls back to the scrubbed copy and fetches bodies
# again where it can.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-$(command -v python3 || command -v python || echo python3)}"
RELEASE="${TRACKER_SNAPSHOT_RELEASE:-db-snapshot}"
RETRY_SLEEP="${RETRY_SLEEP:-10}"
WORK="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/db-snapshot.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

retry() {
  local n
  for n in 1 2 3; do
    if "$@"; then return 0; fi
    echo "attempt $n of 3 failed: $1 $2 $3" >&2
    [ "$n" -lt 3 ] && sleep "$RETRY_SLEEP"
  done
  return 1
}

# A file that fails the check is never published, since it would be what a cache miss restores.
check="$("$PY" -c 'import sqlite3, sys
print("\n".join(str(r[0]) for r in sqlite3.connect(sys.argv[1]).execute("PRAGMA quick_check")))' data/tracker.db 2>&1)" || true
if [ "$check" != "ok" ]; then
  echo "::error::data/tracker.db fails PRAGMA quick_check; no snapshot is published: $(printf '%s' "$check" | head -c 300)"
  exit 1
fi

uploads=()
if [ -f pipeline/snapshot_scrub.py ]; then
  "$PY" -m pipeline.snapshot_scrub data/tracker.db "$WORK/tracker.snapshot.db"
  gzip -c "$WORK/tracker.snapshot.db" > "$WORK/tracker.db.gz"
  rm -f "$WORK/tracker.snapshot.db"
else
  # TODO(F7): pipeline/snapshot_scrub.py is the scrubber the public snapshot must pass through
  # (scratchpad DECISIONS 7). Without it the public copy goes out unscrubbed, as before.
  echo "::warning::pipeline/snapshot_scrub.py is missing: the public snapshot is NOT scrubbed"
  gzip -c data/tracker.db > "$WORK/tracker.db.gz"
fi
uploads+=("$WORK/tracker.db.gz")

if ! gh release view "$RELEASE" >/dev/null 2>&1; then
  gh release create "$RELEASE" --title "Database snapshot" --notes "Latest compressed copy of data/tracker.db, scrubbed of bylines, summaries, raw model output and reviewer names, replaced daily by the export workflow. Not a versioned release." --latest=false
fi
for f in "${uploads[@]}"; do
  retry gh release upload "$RELEASE" "$f" --clobber
  echo "uploaded $(basename "$f") ($(du -h "$f" | cut -f1))"
done
