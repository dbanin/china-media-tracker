# China state media tracker

A research instrument that discovers, every day, how much Chinese state-origin
and Chinese-state-sourced news content is published in every country it can
monitor, keeps that history permanently, and shows it on a world map.

It generalizes the coding framework of a completed study of 484 articles across
Italy, Canada and Australia (Stanford, with Professor Larry Diamond) and runs it
continuously.

Every article is placed in one of four categories. The interface names them
state origin, unchecked state sourcing, independent journalism, and not about
China (the methodology file calls the last one not relevant). The classifier
prompt keeps the codebook's name, unverified relay, for unchecked state
sourcing; that name is internal, and the interface mentions it only to say what
the codebook calls the category. Inside the database and the data files the
same four labels are stored as the codes A, B, C and not_relevant, which is
how the original study's codebook numbered them.

Read METHODOLOGY.md for what the system does and what its numbers mean. That
file is generated from live values and is the authoritative description.

## Increments landed

1. Outlet registry, schema, feed validator, and dense coverage of the three
   study countries.
2. Discovery and storage. Hourly feed polling, URL hash deduplication,
   near-duplicate title linking within a country (and, since 2026-10-12,
   body text linking across outlets after fetch), a loose multilingual
   relevance gate on title and summary, and a resumable SQLite store.
3. Full text retrieval. robots.txt honored and cached, one request per
   domain every three seconds with jitter, or slower where robots.txt sets a
   longer Crawl-delay (honored up to ten minutes, for feeds as well as
   pages), identifying user agent, paywall
   declarations respected with no bypass, two retries then a recorded
   failure. First run over 149 gated articles: 109 fetched, 33 paywalled,
   4 blocked by robots, 3 failed. Canada obtained only 22 percent because
   The Globe and Mail declares every article not free, so Canadian counts
   are flagged as not comparable until more open Canadian outlets are added.
4. Rules-based state origin detection. pipeline/signatures.yaml holds pattern
   groups for credit and dateline forms, distribution stamps, sponsored
   placement disclosures per language, and authored-by-state bylines. Every
   match records the pattern id. Real article fixtures in tests/fixtures/rules
   include a true positive and a hard negative per group. The first pass over
   109 fetched articles found one state origin item (Italpress carrying Xinhua
   under a "(XINHUA/ITALPRESS)" credit) and routed 23 to the LLM stage.
5. The map. docs/ is a static GitHub Pages site with no build step: a
   Robinson choropleth on Natural Earth topology, a single-hue scale used
   only above zero, hatching for countries with no monitored outlets,
   stippling for recorded gaps, a flat fill for monitored countries with
   zero detections, warning markers for failing feeds or heavy paywalls, a
   day scrubber with a global sparkline, a country panel with provenance on
   every classification, an always-visible methodology section, and CSV
   export with a citation string. Below 900 pixels at the time (720 pixels
   now) it becomes a ranked bar chart. METHODOLOGY.md is generated from live values at export time.
6. Unchecked state sourcing adjudication by the model, the review queue and the agreement study.
   pipeline/classify_llm.py sends headline and body only, never outlet or
   country, with the category definitions verbatim in the prompt, structured
   JSON output, a daily call ceiling that is recorded when hit, synchronous
   and Message Batches modes, and near-duplicate copying so one wire item
   syndicated to five outlets costs one call. review/queue.py is the
   terminal review tool; human labels go to a separate table.
   pipeline/agreement.py draws a stratified sample for hand coding and
   computes Cohen's kappa, which the interface surfaces.
7. Registry expansion to global coverage. About 4,000 candidate feed URLs
   were probed and only feeds that returned items were kept, which gave at
   the time 1,469 active outlets across 233 countries and territories in
   more than 60 languages, about 300 outlets recorded as blocking the
   identified crawler, 10 Chinese-language outlets recorded as excluded, and
   17 entries in sources/gaps.yaml with a stated reason: uninhabited
   territories, a few microstates and islands whose only outlets expose no
   feed, Western Sahara, and China itself, which is the origin of the content
   being tracked and is deliberately not monitored. On 12 October 2026 the
   registry held 2,132 outlets: 1,700 active across 235 countries and
   territories in 68 languages, plus the two press release distribution
   wires, about 310 inactive because they block the identified crawler, 19
   Chinese-language outlets excluded, and still 17 gap entries;
   `python -m pipeline.registry` prints the current figures. pipeline/gate_audit.py samples gated-out items,
   stratified by language, from a random 2 percent of rejections kept
   permanently, so the gate's false negative rate can be measured by hand for
   any period.

   Since 14 October 2026 the English sites of six Chinese state outlets,
   Xinhua, CGTN, Global Times, People's Daily, China Daily and ECNS, are read
   as reference originals only (sources/reference.yaml, pipeline/reference.py),
   so that state copy in monitored outlets can be set beside the text it came
   from. They are never classified, never counted and never in a denominator,
   China remains a gap, and sources/gaps.yaml is unchanged. The same day added
   six report only measures (sources/measures.yaml, pipeline/measures.py): the
   alteration score, republication speed, doctrine terms and unusual days,
   which are switched on, and engagement and syndication deals, which are
   built and switched off. None changes a label, a count or a denominator, and
   each is marked provisional until its own hand check passes.

## Running locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m pipeline.registry              # summarize the registry
.venv/bin/python -m pipeline.validate_sources      # check every feed
.venv/bin/python -m pipeline.run discover          # poll feeds into data/tracker.db
.venv/bin/python -m pipeline.run fetch --budget-minutes 20   # retrieve full text
.venv/bin/python -m pipeline.run classify --no-llm   # deterministic state origin pass
ANTHROPIC_API_KEY=... .venv/bin/python -m pipeline.run classify   # plus LLM adjudication
.venv/bin/python -m pipeline.classify_llm --batch   # Message Batches mode: submit now, collect next run
.venv/bin/python -m review.queue                   # review low-confidence B and LLM-resolved A candidates
.venv/bin/python -m pipeline.agreement sample --n 100 --out review/agreement.csv
.venv/bin/python -m pipeline.agreement compute --csv review/agreement.csv
.venv/bin/python -m pipeline.gate_audit sample --n 200 --since 2026-09-01 --until 2026-10-01 --out review/gate_audit.csv
.venv/bin/python -m pipeline.gate_audit compute --csv review/gate_audit.csv
.venv/bin/python -m pipeline.recall_audit sample --n 300 --seed 20261012   # articles the rules settled without the model
.venv/bin/python -m pipeline.recall_audit compute --csv data/reviews/recall_audit_2026-10-12.csv
.venv/bin/python -m pipeline.run status            # what is in the database
.venv/bin/python -m pipeline.export               # rebuild rollups, docs/data JSON and METHODOLOGY.md
python3 -m http.server 8765 --directory docs       # then open http://localhost:8765
```

Set `TRACKER_CONTACT` to a contact address before running the crawler against
live sites, so the user agent identifies a real person. Set
`TRACKER_REPO_URL` to the repository URL once it exists.

## Where it runs

Repository: https://github.com/dbanin/china-media-tracker. Live site:
https://dbanin.github.io/china-media-tracker/, rebuilt and deployed by the
pipeline workflow every day, scheduled for 02:00 UTC, from data the same
workflow gathers on an hourly schedule. There is one workflow on purpose:
GitHub keeps at most one queued run per concurrency group and cancels the
older one, so separate collect and export workflows used to cancel each other. GitHub Actions delays scheduled runs
under load, often by one to four hours, so the real cadence is looser than
the schedule and every run's actual time is in the run log. The weekly feed
validation is the same workflow's Monday run.

Set up on 2026-09-04: Pages deploys from Actions, workflows have write
permission, the `TRACKER_CONTACT` variable holds the crawler contact address,
and a write deploy key lets the owner's machine push. Still to add by the
owner at the time, and added since: the `ANTHROPIC_API_KEY` secret, which
switches on the verification stage. Optional variables: `TRACKER_LLM_MODEL`,
`TRACKER_LLM_DAILY_CEILING`, `TRACKER_LLM_MONTHLY_BUDGET_USD`,
`TRACKER_SESSION_GRACE_DAYS`.

Some outlets answer 403 or time out for GitHub's runner addresses while
their feeds work from an ordinary network. Those carry `collector:
self_hosted` in the registry and are relayed from the owner's Mac: a launchd
job runs `scripts/relay.sh` every hour, which polls and fetches only those
outlets into a separate database (`data/relay.db`), running from its own git worktree pinned to the published main branch so local uncommitted edits never reach it, then force-pushes one
gzipped file of the last seven days of articles, bodies included, to the
`relay` branch. That branch has no history, so the repository does not grow.
The hosted pipeline job ingests the bundle at the start of every run and
inserts whatever it does not have yet; classification then happens on the
hosted side. The main database still has exactly one writer. When the Mac
is asleep the relay simply skips an hour; nothing queues and nothing is
blocked. Logs are in `logs/relay-YYYY-MM-DD.log`.

Only the workflows write the database. Before running any local tool that
reads it (the review queue, the agreement study, a reclassification), pull
the latest snapshot:

```bash
scripts/db_pull.sh
```

That replaces `data/tracker.db` with the daily release snapshot and keeps
the previous local file as a backup. Local runs of the pipeline are for
development; do not push their generated files, or the export workflow's
commit will conflict with them.

The generated site data (docs/data) and the audit files (data/export, one
JSON line per gated article per month with its current classification) are
committed daily, so the repository history is the audit trail. The working
database is not committed: a growing SQLite file passes GitHub's 100 MB file
limit within weeks and would bloat the history hourly. It travels between
runs through the Actions cache and is snapshotted, compressed, as the single
asset of the `db-snapshot` release every day; `scripts/db_restore.sh` shows
the restore order. Extracted article bodies travel in the same cache.
Items the relevance gate rejected are pruned after three days, the one
deliberate exception to the never-delete rule; a permanent per-day
discovery count is kept so totals do not shrink. A random 2 percent of
rejections (`GATE_AUDIT_KEEP_SHARE`, chosen by a hash of the url so it is
reproducible and blind to outlet and language) is kept for audit,
permanently, with its title, summary, url, outlet, country, language and
gate terms, so the gate audit can sample any period. That adds about 1,700
rows, roughly 1.8 MB, a day, some 50 to 55 MB a month. Raw HTML is kept only on the machine that
fetched it; the page chrome labels the sponsored placement patterns need
are extracted at fetch time and stored with the article. When a body is
missing anyway, `pipeline/rehydrate.py` and the classifiers re-fetch it,
up to three attempts, under the usual politeness rules.

## Theme counter

Under the map, the theme counter shows what the coverage is about. Every
article that concerns China is tagged at export time with each theme whose
terms appear in its headline, its feed summary or anywhere in its body (the
first 800 characters only, before themes version 2026.09.4): diplomacy and summits, trade and economy, Belt and Road and
investment, technology, energy and electric vehicles, military and security,
Taiwan and Hong Kong, human rights and repression, Chinese domestic politics,
culture and tourism, and disasters and health. An article can carry several
themes; one with none is Other. The terms live in `pipeline/themes.yaml`, in
English plus eighteen other languages (Malay reads the Indonesian list), and
the tagger in `pipeline/themes.py`.
Changing a term means bumping `version` in that file; the next export
re-tags every article. The daily files carry, per day and country, each
theme's count of all China coverage, unchecked state sourcing and state origin
articles, so the counter follows the Measure, Window and Distribution
toggles. They also
carry a combined count that adds the candidates still waiting for the model;
no measure reads it, since it would mix judged and unjudged articles. The tags are
keyword matches, not a reading of the article; on the 12 October 2026
snapshot about one in ten articles that concern China matched no theme, most
often in languages with no term list of their own, such as Bulgarian,
Ukrainian and Greek.

## Planted bylines

Below the examples, the site keeps a watchlist of fictitious journalist and
expert personas, and of false front organisations and outlets, that published
investigations have named as placing or pitching articles in real news outlets.
The list is `sources/personas.yaml`; every entry cites the report that exposed
it, and `version` must be bumped on any change, which makes the next run scan
every stored item again. The matcher in `pipeline/planted.py` compares the
author field of every discovered item (China gate or not) with the list after
normalising names and deriving initial-less forms, and for items with a body
also looks for a listed name after a byline word at the top of the text and
for a listed front or its domains anywhere in the text. Matches are stored in
`planted_flags`, exported to `docs/data/planted.json`, and shown in the
Planted bylines section with the detector, the investigation and a link to its
report. A flag says only that the byline matches an exposed persona; the
tracker cannot prove a byline is fictitious, absence of a match proves
nothing, and flags never enter the three categories or change a count. A
recurring bylines diagnostic exists on the command line only
(`python -m pipeline.planted recurring`) and is never published, because it
lists syndicated agency reporters. `python -m pipeline.planted scan --full`
rescans everything; `--budget-seconds N` stops a scan cleanly and the next
scan resumes where it stopped.

## Near-duplicates

Placements of one underlying item are linked in two passes. Discovery links
headlines within a country (pipeline/fetch_feeds.py, word overlap of 0.7 or
more over three days). The fetch stage then links bodies
(pipeline/body_dups.py): each saved body gets a 64 value MinHash signature
of its five word shingles (five character shingles for scripts without
spaces), and articles from different outlets, in any countries, discovered
within seven days of each other are linked when the signatures estimate at
least half their shingles are shared. That catches syndicated copy under a
rewritten headline. Same outlet pairs are left to the title pass, and a body
that matches its own outlet's text under another headline is treated as
page template and never linked. The links sit in the body_links table;
articles.dup_group_id stays the title group, which is the only group the
model's label is copied along. The export joins the two for the underlying
item counts, so category counts do not move and underlying item figures
can only fall: in a country's figure for links inside that country, and in
a world figure for any link.

Underlying items are counted once per window and scope, never summed day
by day. Each day of the daily files carries `underlying_items`, the item
ids behind its state origin (`A`) and unchecked state sourcing (`B`) counts
per country, and the page counts the distinct ids over whatever window and
countries it shows; latest.json does the same for its all time and 30 day
blocks and, in its totals, across countries. An item is counted under each
label it carries. The daily CSV gives each day's own figure, so summing its
underlying item columns over days counts a story once per day it appeared.
Files from an export before schema 12 carry no ids, and the page then says
that its underlying items are summed day by day.

Translations are not matched. A translated copy shares almost no words with
its original, and matching it would need machine translation, which the
tracker does not do; a story carried in Spanish and in English counts as two
underlying items.

The fetch stage fingerprints older stored bodies for up to three minutes a
run until none is left. `python -m pipeline.body_dups backfill
--budget-minutes 10` does it in one go, and `python -m pipeline.body_dups
stats` reports progress.

## Changing the ruleset

Edit `pipeline/signatures.yaml`, add fixtures for every new pattern, bump
`RULESET_VERSION` in `pipeline/config.py`, describe the change in
`CHANGELOG.md`, and run:

```bash
.venv/bin/python -m pipeline.classify_rules reclassify 2026-09-01
```

Earlier classification rows stay in the database marked not current. The
hourly collect run also carries forward, up to 2,000 articles a run
(`RECLASSIFY_PER_RUN` in pipeline/classify_rules.py) within its time budget,
any article whose current label is from an older ruleset, so a bump needs
no manual step on the runner.

## Classifier model and cost

The model is a config value, `TRACKER_LLM_MODEL`, defaulting to
`claude-sonnet-5`. The B versus C judgement is the number that would
embarrass the project if inflated, so the default is the cheapest model
whose verification judgement is defensible rather than the cheapest model
available. `claude-haiku-4-5` is the cheaper alternative; if you switch,
rerun the agreement study, because kappa is measured per model. The daily
call ceiling `TRACKER_LLM_DAILY_CEILING` defaults to 600. The monthly budget
`TRACKER_LLM_MONTHLY_BUDGET_USD` defaults to 110 dollars, matched to the spend
limit on the API account; pipeline/llm_cost.py spreads what is left of it over
the days left in the month and divides by the price of a call, which gives a
second daily cap, and the lower of the two binds. A call sends about 1,600
tokens of article beside about 1,800 tokens of cached system prompt and gets
back about 500. Priced from the tokens recorded in September and October
(`llm_cost.measured_per_call`), that is about two cents a call at full price
and one cent through the Batches API, so a full ceiling day would cost about
12 dollars synchronous or 6 batched, and the budget binds first: on 12
October 2026 it allowed 235 synchronous or 470 batched calls a day. Until 500
calls are on record the cap prices a call at `LLM_COST_PER_CALL_UNBATCHED`,
0.0175 dollars, from the first month's bill.

Unverified relay (unchecked state sourcing in the interface) counts an
article only when its central content is a factual claim attributed to a
Chinese official or state source, an assertion about events, numbers or
actions, passed on without independent confirmation. Reporting a stated
position, policy, warning, threat, opinion or denial is independent
journalism even without outside comment. The system prompt carries a date,
`PROMPT_VERSION` in pipeline/classify_llm.py, and every model label records
it in its model version, for example `claude-sonnet-5 (prompt 2026-10-12)`.
Bump it whenever the prompt changes what a label means. Current model labels
made under an older prompt are then judged again through the API, oldest
first, with what new articles leave of each run's calls and at most
`TRACKER_LLM_RELABEL_SHARE` (default 0.5) of the day's call cap; each keeps
its old label until the new one is stored, and the timeline marks the days
still waiting.

## Things raised rather than assumed

Paywalls. On the first retrieval run, The Globe and Mail declared every
article not free in its schema.org metadata, so Canada obtained only 22
percent of gated articles in full text. Any country where paywalls remove
more than a third of retrieved articles is flagged in the interface with a
warning marker and named in the data notice and in meta.json under
`paywall_flagged_countries`. Its numbers are not comparable to the rest.
The remedy is to add more open Canadian outlets, not to bypass anything.

Bot protection. On the first global retrieval run, 490 article fetches
returned HTTP 403 to the identified crawler, mostly from sites behind bot
protection, and 65 were disallowed by robots.txt. Both are recorded as
failures, never retried by another route, and any country where failures
and robots blocks together exceed a third of attempts carries a warning
marker. Kommersant, the Wall Street Journal and Folha are the largest
robots.txt exclusions.

Registry unevenness. The registry was seeded densely in the three study
countries and then expanded by region. The export records
`registry_unevenness` in meta.json (minimum, median and maximum active
outlets per country, and the number of single-outlet countries). When the
densest country has three or more times the median, the interface says so
and reminds the reader that raw counts display the sampling; on 12 October
2026 the densest country, the United States, had 44 active outlets against a
median of 6, and 16 countries had a single outlet. The site has three
measures: state origin, the default and the one label decided without the
model, unchecked state sourcing, and all China coverage. It has four bases:
number of articles, per outlet we monitor, percent of the country's China
coverage and percent of all its news; per outlet and the share of all its
news are the corrected views. A Distribution toggle, described below,
separates the country outlets from the worldwide press release wires. No per
capita figure is shown, because population is a poor denominator for what outlets
publish; the export still carries each country's resident population
(sources/population.json), which reaches only the population column of the CSV
of the current view. Unchecked state sourcing
is shown as not yet measured until the model stage has run, then marked provisional
until the hand coding agreement study settles it, and withheld if that study,
or a model stability check, puts kappa below the threshold; a passing stability
check never publishes it. Candidates still waiting for the model are in
no measure on the map; the country panel and the CSV exports show them as
their own figure, which is not a finding.

Press release distribution wires. PR Newswire and GlobeNewswire are
registered as outlets with the tier `distribution_wire` and polled like any
other, but a release pushed through them is not a placement in a national
newsroom: the same wire terminal delivers it to subscribers in every country
at once. The export counts them as a stratum of their own, "Worldwide
distribution", under the pseudo country code WLD (the World Bank's code for
the world) in latest.json, the daily files, outlets.json and
articles/WLD.json. The stratum is listed in the ranked list and the country
search beside the countries and is drawn nowhere on the map; it is never
attributed to the United States and never added into any individual country's
numbers, which would count one release once per country. World totals include
it, and `meta.distribution_wires` carries the same figures under their old
name. Earlier exports left these releases out of every total, which hid 179
state origin items in one 30 day window. The site's Distribution toggle, next
to Measure, reads the two apart: Both, Country outlets (the wires left out of
every figure that sums across countries) or Worldwide wires (the stratum alone,
the map left unfilled), from the per day `wld` block in global_series.json and
the `_wires` fields in meta.findings.

Uncredited relay. The model reads an article only when a weak state origin
signature or an official sourcing trigger fires: a citation of Xinhua, CGTN,
Global Times, China Daily, CCTV, People's Daily or China Media Group, a
Chinese foreign ministry spokesperson, a Chinese embassy, a named official
spokesperson, or a "state media reported" construction
(pipeline/classify_rules.py, `classify_article`; `llm_triggers` in
pipeline/signatures.yaml). Agency copy run without a credit line names none
of these, so the gate cannot see it; the article is labelled independent
journalism or not relevant by rules and the model never reads it. On the
12 October 2026 snapshot that was 69,504 articles, against about 9,800 the
model judged. 153 of them repeat the headline or feed summary of a state
origin item another outlet carried with its credit, most of them Xinhua
material that Italpress distributes under a "(XINHUA/ITALPRESS)" stamp, run
by Libero, Il Tempo and Quotidiano.net with no stamp in the text the tracker
fetched, and press releases in
CNA and The Straits Times. AsiaOne, where agency copy runs uncredited most often, was not
in the registry at the time of that snapshot; it was registered the same day as
`sg_asiaone`. Recall is measured by hand: pipeline/recall_audit.py draws a
seeded sample of those articles, stratified by language and oversampling the
lookalikes and the outlets with a wire copy habit, writes it to data/reviews/
without the rule label, and computes the share coders place in state origin
or unchecked state sourcing, weighted back to the population, with a 95
percent interval per language and overall. The result is stored in the
recall_audits table and stated in METHODOLOGY.md. The first sample, 300
articles, is data/reviews/recall_audit_2026-10-12.csv; the miss rate is not
yet measured. Since the lookalike check (pipeline/lookalike.py,
`classify_rules.route_lookalike` and `sweep_lookalikes`) such a near repeat
goes to the model as a state origin candidate, the match kept under
"lookalike" in `articles.llm_trigger`; a state origin label the model gives
it is stored with its own route, `uncredited_copy`
(`classify_rules.model_route`), not `unattributed`, and `ensure_routes`
moves older such labels across at export.

Methodology review, September 2026. The changes it prompted are listed in
CHANGELOG.md under 2026-09-15: province terms and a state origin signature
exemption in the relevance gate, community phrases for Singapore and Malaysia,
a stratified random draw under the model call ceiling, a route and an arrival
on every state origin label, press release and sponsored sections polled as
feeds, feed saturation and missed-item estimates, a heartbeat from the relay
collector, per language agreement, a color scale fixed across dates, and
whole-body theme tagging.

Leaning and ownership sources, October 2026. Media Bias/Fact Check rates
outlets on a United States spectrum. It is kept as a source, but a leaning
outside the United States that rests on it alone is exported with
`leaning_source_weak: true` (pipeline/registry.py) and shown with a caution
in the "Who publishes it" table; a `leaning_second_source` that is not the
same rater lifts the mark. No rule requires two sources, so these outlets are
not moved to not assessed. Government funded international broadcasters
(Radio Free Asia, RFE/RL, Deutsche Welle, RFI, France 24, the BBC World
Service language services, KBS World, RNZ Pacific) have their own ownership
value, `state_funded_international`, instead of counting as public service.

## The Chinese language rule

Outlets that publish primarily in Chinese are registered with `active: false`
and a note, not removed. The project owner does not read Chinese and decided
that machine translation would miss precisely the distinctions the coding
framework turns on. The exclusion is therefore visible and reversible. On 12
October 2026, 19 outlets were excluded under this rule: seven in Australia,
five in Taiwan, three in Hong Kong, two in Malaysia and one each in South
Korea and Singapore.
