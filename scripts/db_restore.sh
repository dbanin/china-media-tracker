#!/bin/bash
# Restore the working database on a runner. Order: already present (Actions cache), the daily
# release snapshot, the seed commit that last carried the database in git, else start fresh.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data
if [ -s data/tracker.db ]; then echo "database present ($(du -h data/tracker.db | cut -f1)), from cache"; exit 0; fi
if command -v gh >/dev/null && gh release download db-snapshot -p tracker.db.gz -D data --clobber 2>/dev/null; then
  gunzip -f data/tracker.db.gz && echo "database restored from release snapshot ($(du -h data/tracker.db | cut -f1))" && exit 0
fi
SEED="${TRACKER_DB_SEED_COMMIT:-b1e5f36}"
if git cat-file -e "$SEED:data/tracker.db" 2>/dev/null; then
  git show "$SEED:data/tracker.db" > data/tracker.db && echo "database restored from seed commit $SEED" && exit 0
fi
echo "no database found; a fresh one will be created"
