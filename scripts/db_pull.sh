#!/bin/bash
# Replace the local working database with the latest daily snapshot published by the
# export workflow, keeping a backup of the local file. The runner's database is the
# canonical one; run this before any local review, agreement study or reclassification
# so local tooling does not work on a stale copy.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${TRACKER_REPO:-dbanin/china-media-tracker}"
URL="https://github.com/$REPO/releases/download/db-snapshot/tracker.db.gz"
mkdir -p data
if [ -s data/tracker.db ]; then
  cp data/tracker.db "data/tracker.db.local-$(date -u +%Y%m%dT%H%M%SZ).bak"
fi
echo "downloading $URL"
curl -fL --retry 3 -o data/tracker.db.gz "$URL"
gunzip -f data/tracker.db.gz
echo "local database replaced ($(du -h data/tracker.db | cut -f1)); previous copy kept as data/tracker.db.local-*.bak"
