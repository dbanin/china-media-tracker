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
6. LLM Category B adjudication, the review queue and the agreement study.
   pipeline/classify_llm.py sends headline and body only, never outlet or
   country, with the category definitions verbatim in the prompt, structured
   JSON output, a daily call ceiling that is recorded when hit, synchronous
   and Message Batches modes, and near-duplicate copying so one wire item
   syndicated to five outlets costs one call. review/queue.py is the
   terminal review tool; human labels go to a separate table.
   pipeline/agreement.py draws a stratified sample for hand coding and
   computes Cohen's kappa, which the interface surfaces.
7. Registry expansion to global coverage. About 3,400 candidate feed URLs
   were probed and only feeds that returned items were kept: 1,361 active
   outlets across 178 countries in 60 languages, 303 outlets recorded as
   blocking the identified crawler, 10 Chinese-language outlets recorded as
   excluded, and 72 countries or territories in sources/gaps.yaml with a
   stated reason (mostly small territories never searched, plus China
   itself, North Korea and Eritrea). pipeline/gate_audit.py samples gated-out
   items so the gate's false negative rate can be measured by hand.

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
ANTHROPIC_API_KEY=... .venv/bin/python -m pipeline.run classify   # plus LLM adjudication
.venv/bin/python -m pipeline.classify_llm --batch   # Message Batches mode: submit now, collect next run
.venv/bin/python -m review.queue                   # review low-confidence B and LLM-resolved A candidates
.venv/bin/python -m pipeline.agreement sample --n 100 --out review/agreement.csv
.venv/bin/python -m pipeline.agreement compute --csv review/agreement.csv
.venv/bin/python -m pipeline.gate_audit sample --n 200 --out review/gate_audit.csv
.venv/bin/python -m pipeline.run status            # what is in the database
.venv/bin/python -m pipeline.export               # rebuild rollups, docs/data JSON and METHODOLOGY.md
python3 -m http.server 8765 --directory docs       # then open http://localhost:8765
```

Set `TRACKER_CONTACT` to a contact address before running the crawler against
live sites, so the user agent identifies a real person. Set
`TRACKER_REPO_URL` to the repository URL once it exists.

## Running on GitHub

1. Create a repository and push this tree. The default branch is `main`.
2. Settings, Secrets and variables, Actions: add the secret
   `ANTHROPIC_API_KEY`. Add the variable `TRACKER_CONTACT` with a contact
   address for the crawler's user agent. Optionally add `TRACKER_LLM_MODEL`
   and `TRACKER_LLM_DAILY_CEILING`.
3. Settings, Pages: set the source to GitHub Actions.
4. Settings, Actions, General: allow workflows read and write permissions.
5. Run the three workflows once by hand from the Actions tab to confirm they
   pass: collect (hourly), export (daily at 02:00 UTC, deploys Pages),
   validate feeds (weekly).

The database and the generated site data are committed by the workflows, so
the repository history is the audit trail. Article bodies and raw HTML are
not committed; `pipeline/rehydrate.py` re-fetches them when a
reclassification needs them.

## Changing the ruleset

Edit `pipeline/signatures.yaml`, add fixtures for every new pattern, bump
`RULESET_VERSION` in `pipeline/config.py`, describe the change in
`CHANGELOG.md`, and run:

```bash
.venv/bin/python -m pipeline.classify_rules reclassify 2026-09-01
```

Earlier classification rows stay in the database marked not current.

## Classifier model and cost

The model is a config value, `TRACKER_LLM_MODEL`, defaulting to
`claude-sonnet-5`. The B versus C judgement is the number that would
embarrass the project if inflated, so the default is the cheapest model
whose verification judgement is defensible rather than the cheapest model
available. `claude-haiku-4-5` is the cheaper alternative; if you switch,
rerun the agreement study, because kappa is measured per model. The daily
call ceiling `TRACKER_LLM_DAILY_CEILING` defaults to 600. At roughly three
thousand input tokens per article with the system prompt cached, a full
ceiling day costs on the order of two to four dollars on Sonnet.

## Things raised rather than assumed

Paywalls. On the first retrieval run, The Globe and Mail declared every
article not free in its schema.org metadata, so Canada obtained only 22
percent of gated articles in full text. Any country where paywalls remove
more than a third of retrieved articles is flagged in the interface with a
warning marker and named in the data notice and in meta.json under
`paywall_flagged_countries`. Its numbers are not comparable to the rest.
The remedy is to add more open Canadian outlets, not to bypass anything.

Registry unevenness. The registry was seeded densely in the three study
countries and then expanded by region. The export records
`registry_unevenness` in meta.json (minimum, median and maximum active
outlets per country, and the number of single-outlet countries). When the
densest country has three or more times the median, the interface says so
and reminds the reader that raw counts display the sampling. The default
metric is the share of monitored China coverage that is A or B, and the
per-outlet metrics are offered as the corrected count view.

## The Chinese language rule

Outlets that publish primarily in Chinese are registered with `active: false`
and a note, not removed. The project owner does not read Chinese and decided
that machine translation would miss precisely the distinctions the coding
framework turns on. The exclusion is therefore visible and reversible.
