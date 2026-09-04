#!/bin/zsh
# Hourly collection on this machine. Mirrors .github/workflows/collect.yml.
cd /Users/daniel/china-media-tracker || exit 1
export TRACKER_CONTACT="${TRACKER_CONTACT:-see repository issues}"
[ -f .env ] && set -a && source .env && set +a
.venv/bin/python -m pipeline.run all --budget-minutes 40 >> logs/local-collect.log 2>&1
git add data/tracker.db >/dev/null 2>&1
git -c user.name="tracker-local" -c user.email="tracker-local@localhost" commit -q -m "collect: local hourly run $(date -u +%Y-%m-%dT%H:%MZ)" >/dev/null 2>&1 || true
