#!/bin/bash
# Hourly relay for the outlets that refuse GitHub's runner addresses. Runs on the owner's machine
# from launchd. It runs from its own git worktree pinned to origin/main, so uncommitted edits in
# the main checkout can never change what the relay publishes (on 2026-09-14 an uncommitted
# bundle format reached GitHub before the code that reads it and stopped two hourly runs).
# It keeps its own database (data/relay.db in the main checkout) and never touches the main one.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WT="$HOME/china-media-tracker-relay"
mkdir -p "$REPO/logs"
LOG="$REPO/logs/relay-$(date -u +%Y-%m-%d).log"
{
  echo "=== relay $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/id_ed25519_tracker -o IdentitiesOnly=yes"
  git -C "$REPO" fetch --quiet origin main || echo "fetch failed; using the last fetched main"
  if [ ! -e "$WT/.git" ]; then git -C "$REPO" worktree add --quiet --detach "$WT" origin/main; fi
  git -C "$WT" checkout --quiet --detach origin/main
  mkdir -p "$WT/data"
  [ -e "$WT/data/bodies" ] || ln -s "$REPO/data/bodies" "$WT/data/bodies"
  cd "$WT" && TRACKER_COLLECTOR=self_hosted TRACKER_DB_PATH="$REPO/data/relay.db" \
    "$REPO/.venv/bin/python" -X faulthandler -m pipeline.relay run --budget-minutes 30
  echo "=== done $(date -u +%Y-%m-%dT%H:%M:%SZ) from $(git -C "$WT" rev-parse --short HEAD)"
} >> "$LOG" 2>&1
