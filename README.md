# China state media tracker

A research instrument that discovers, every day, how much Chinese state-origin
and Chinese-state-sourced news content is published in every country it can
monitor, keeps that history permanently, and shows it on a world map.

It generalizes the coding framework of a completed study of 484 articles across
Italy, Canada and Australia (Stanford, with Professor Larry Diamond) and runs it
continuously.

Read METHODOLOGY.md for what the system does and what its numbers mean. That
file is generated from live values and is the authoritative description.

## Increments landed

1. Outlet registry, schema, feed validator, and dense coverage of the three
   study countries.
2. Discovery and storage. Hourly feed polling, URL hash deduplication,
   near-duplicate title linking within a country, a loose multilingual
   relevance gate on title and summary, and a resumable SQLite store.
3. Full text retrieval. robots.txt honored and cached, one request per
   domain every three seconds with jitter, identifying user agent, paywall
   declarations respected with no bypass, two retries then a recorded
   failure. First run over 149 gated articles: 109 fetched, 33 paywalled,
   4 blocked by robots, 3 failed. Canada obtained only 22 percent because
   The Globe and Mail declares every article not free, so Canadian counts
   are flagged as not comparable until more open Canadian outlets are added.
4. Rules-based Category A detection. pipeline/signatures.yaml holds pattern
   groups for credit and dateline forms, distribution stamps, sponsored
   placement disclosures per language, and authored-by-state bylines. Every
   match records the pattern id. Real article fixtures in tests/fixtures/rules
   include a true positive and a hard negative per group. The first pass over
   109 fetched articles found one Category A item (Italpress carrying Xinhua
   under a "(XINHUA/ITALPRESS)" credit) and routed 23 to the LLM stage.
5. The map. docs/ is a static GitHub Pages site with no build step: a
   Robinson choropleth on Natural Earth topology, a single-hue scale used
   only above zero, hatching for countries with no monitored outlets,
   stippling for recorded gaps, a flat fill for monitored countries with
   zero detections, warning markers for failing feeds or heavy paywalls, a
   day scrubber with a global sparkline, a country panel with provenance on
   every classification, an always-visible methodology section, and CSV
   export with a citation string. Below 900 pixels it becomes a ranked bar
   chart. METHODOLOGY.md is generated from live values at export time.

## Running locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m pipeline.registry              # summarize the registry
.venv/bin/python -m pipeline.validate_sources      # check every feed
.venv/bin/python -m pipeline.run discover          # poll feeds into data/tracker.db
.venv/bin/python -m pipeline.run fetch --budget-minutes 20   # retrieve full text
.venv/bin/python -m pipeline.run classify --no-llm   # deterministic Category A pass
.venv/bin/python -m pipeline.run status            # what is in the database
.venv/bin/python -m pipeline.export               # rebuild rollups, docs/data JSON and METHODOLOGY.md
python3 -m http.server 8765 --directory docs       # then open http://localhost:8765
```

Set `TRACKER_CONTACT` to a contact address before running the crawler against
live sites, so the user agent identifies a real person. Set
`TRACKER_REPO_URL` to the repository URL once it exists.

## The Chinese language rule

Outlets that publish primarily in Chinese are registered with `active: false`
and a note, not removed. The project owner does not read Chinese and decided
that machine translation would miss precisely the distinctions the coding
framework turns on. The exclusion is therefore visible and reversible.
