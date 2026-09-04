#!/bin/bash
# Publish the working database, compressed, as the single asset of the db-snapshot release.
set -euo pipefail
cd "$(dirname "$0")/.."
gzip -c data/tracker.db > data/tracker.db.gz
if ! gh release view db-snapshot >/dev/null 2>&1; then
  gh release create db-snapshot --title "Database snapshot" --notes "Latest compressed copy of data/tracker.db, replaced daily by the export workflow. Not a versioned release." --latest=false
fi
gh release upload db-snapshot data/tracker.db.gz --clobber
echo "snapshot uploaded ($(du -h data/tracker.db.gz | cut -f1))"
rm -f data/tracker.db.gz
