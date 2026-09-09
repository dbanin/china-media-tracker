#!/bin/bash
# Hourly relay for the outlets that refuse GitHub's runner addresses. Runs on the owner's
# machine from launchd. Keeps its own database (data/relay.db), never touches the main one,
# and publishes a single-file bundle to the relay branch for the hosted collector to ingest.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG="logs/relay-$(date -u +%Y-%m-%d).log"
{
  echo "=== relay $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/id_ed25519_tracker -o IdentitiesOnly=yes"
  git pull --rebase --quiet origin main || echo "pull failed; running with the checkout as is"
  TRACKER_COLLECTOR=self_hosted TRACKER_DB_PATH=data/relay.db \
    .venv/bin/python -X faulthandler -m pipeline.relay run --budget-minutes 30
  echo "=== done $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "$LOG" 2>&1
