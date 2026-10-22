#!/bin/bash
# RETIRED on 2026-10-22. The relay collected outlets that refuse the crawler from GitHub's runner
# addresses by fetching them from the owner's home connection instead, which works around the sites'
# access control. It was dropped: its outlets (collector: self_hosted) are inactive in
# sources/outlets.yaml, the pipeline no longer ingests or acknowledges relay bundles, and the
# RELAY_BUNDLE_KEY secret is never to be added. The launchd job that ran this script is to be unloaded;
# the script is kept, unchanged below, for the record of how the historic relay data were collected.
#
# Hourly relay for the outlets that refuse GitHub's runner addresses. Runs on the owner's machine
# from launchd. It runs from its own git worktree pinned to origin/main, so uncommitted edits in
# the main checkout can never change what the relay publishes (on 2026-09-14 an uncommitted
# bundle format reached GitHub before the code that reads it and stopped two hourly runs).
# It keeps its own database (data/relay.db in the main checkout) and never touches the main one.
#
# Three guards (2026-10-17):
#   - caffeinate -i holds off idle sleep while the run lasts;
#   - a wall-clock stop: a run still going TRACKER_RELAY_WALL_SECONDS (45 minutes) after it began,
#     by the clock on the wall, is stopped before it pushes. A closed lid still sleeps the machine,
#     and a run that woke hours later used to push data hours old; the next hour's run sends it fresh;
#   - SSH, then HTTPS: when origin cannot be reached over SSH the run uses HTTPS (git's own credential
#     helper, never a prompt), and a run that fails over SSH pushes its bundle again over HTTPS.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WT="${TRACKER_RELAY_WORKTREE:-$HOME/china-media-tracker-relay}"
PY="${TRACKER_RELAY_PYTHON:-$REPO/.venv/bin/python}"
WALL_SECONDS="${TRACKER_RELAY_WALL_SECONDS:-2700}"
POLL_SECONDS="${TRACKER_RELAY_POLL_SECONDS:-5}"
SSH_CMD="${TRACKER_RELAY_SSH:-ssh -i $HOME/.ssh/id_ed25519_tracker -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20}"
START=$(date +%s)
mkdir -p "$REPO/logs"
LOG="$REPO/logs/relay-$(date -u +%Y-%m-%d).log"

use_https() {  # for every git command from here on, this script's and the relay's own
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0="url.https://github.com/.insteadOf" GIT_CONFIG_VALUE_0="git@github.com:"
  export GIT_TERMINAL_PROMPT=0
}

relay() {  # pipeline.relay "$@" under caffeinate and the wall-clock stop; 124 when the clock stopped it
  ( cd "$WT" && TRACKER_COLLECTOR=self_hosted TRACKER_DB_PATH="$REPO/data/relay.db" TRACKER_MAIN_DB="$REPO/data/tracker.db" \
      exec "$PY" -X faulthandler -m pipeline.relay "$@" ) &
  local pid=$! waited
  if command -v caffeinate >/dev/null 2>&1; then caffeinate -i -w "$pid" & fi
  while kill -0 "$pid" 2>/dev/null; do
    if [ $(( $(date +%s) - START )) -ge "$WALL_SECONDS" ]; then
      echo "wall-clock stop: $(( ($(date +%s) - START) / 60 )) minutes since the run began; stopped before it pushes"
      kill -TERM "$pid" 2>/dev/null
      for waited in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      kill -KILL "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      return 124
    fi
    sleep "$POLL_SECONDS"
  done
  wait "$pid"
}

{
  echo "=== relay $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  export GIT_SSH_COMMAND="$SSH_CMD"
  transport=ssh
  if ! git -C "$REPO" ls-remote --quiet --exit-code origin HEAD >/dev/null 2>&1; then
    echo "origin cannot be reached over ssh; this run uses https"
    use_https
    transport=https
  fi
  git -C "$REPO" fetch --quiet origin main || echo "fetch failed; using the last fetched main"
  if [ ! -e "$WT/.git" ]; then git -C "$REPO" worktree add --quiet --detach "$WT" origin/main; fi
  git -C "$WT" checkout --quiet --detach origin/main
  mkdir -p "$WT/data"
  [ -e "$WT/data/bodies" ] || ln -s "$REPO/data/bodies" "$WT/data/bodies"
  # The main checkout's copy of the main database (scripts/db_pull.sh), read only, for the articles the
  # runner found on relay outlets and was refused (pipeline.relay.seed_refused).
  # The relay branch is public, so a bundle with article bodies is sealed with the relay key
  # (pipeline/bundle_crypto.py). The key is read from this file, which the owner creates once, holding
  # the same value as the RELAY_BUNDLE_KEY Actions secret:
  #   mkdir -p ~/.config/china-media-tracker && umask 077 && \
  #   python3 -c "import secrets; print(secrets.token_urlsafe(32))" > ~/.config/china-media-tracker/relay-bundle.key
  # then paste the file's content into the repository secret RELAY_BUNDLE_KEY. Never commit it. Until
  # the file exists the bundle goes out in clear with every body left out, and the runner fetches again
  # what it can. Only the path is passed here; the key itself never enters this script or its log.
  export RELAY_BUNDLE_KEY_FILE="${RELAY_BUNDLE_KEY_FILE:-$HOME/.config/china-media-tracker/relay-bundle.key}"
  relay run --budget-minutes 30
  status=$?
  if [ "$status" -eq 124 ]; then
    :
  elif [ "$status" -ne 0 ] && [ "$transport" = ssh ]; then
    # Most often the push itself (ssh refused or dropped); the bundle is rebuilt from relay.db and sent again.
    echo "relay run failed (status $status) over ssh; pushing the bundle again over https"
    use_https
    relay bundle
    status=$?
  fi
  echo "=== done $(date -u +%Y-%m-%dT%H:%M:%SZ) from $(git -C "$WT" rev-parse --short HEAD) status $status"
} >> "$LOG" 2>&1
