#!/bin/zsh
# Daily rebuild of the site data, METHODOLOGY.md and the single-file snapshot. Mirrors export.yml.
cd /Users/daniel/china-media-tracker || exit 1
[ -f .env ] && set -a && source .env && set +a
.venv/bin/python -m pipeline.export >> logs/local-export.log 2>&1
.venv/bin/python -m pipeline.build_standalone >> logs/local-export.log 2>&1
git add data/tracker.db docs/data METHODOLOGY.md >/dev/null 2>&1
git -c user.name="tracker-local" -c user.email="tracker-local@localhost" commit -q -m "export: local daily rebuild $(date -u +%Y-%m-%d)" >/dev/null 2>&1 || true
if git remote get-url origin >/dev/null 2>&1; then git push -q origin HEAD >> logs/local-export.log 2>&1 || true; fi
