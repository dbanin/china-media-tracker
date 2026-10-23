"""Central configuration. Values that may change live here, not as literals in other modules."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    """Environment value, falling back to the default when the variable is unset or empty.
    GitHub Actions passes an undefined repository variable as an empty string."""
    value = os.environ.get(name)
    return value if value else default


# TRACKER_DB_PATH lets the relay collector keep its own database beside the main one.
DB_PATH = ROOT / _env("TRACKER_DB_PATH", "data/tracker.db")
BODIES_DIR = ROOT / "data" / "bodies"
RAW_HTML_DIR = ROOT / "data" / "raw_html"
# Model labels produced outside the API and committed for the runner to import (pipeline.labels).
LABELS_DIR = ROOT / "data" / "labels"
OUTLETS_PATH = ROOT / "sources" / "outlets.yaml"
OUTLETS_SCHEMA_PATH = ROOT / "sources" / "outlets_schema.json"
GAPS_PATH = ROOT / "sources" / "gaps.yaml"
GAPS_SCHEMA_PATH = ROOT / "sources" / "gaps_schema.json"
SIGNATURES_PATH = ROOT / "pipeline" / "signatures.yaml"
KEYWORDS_PATH = ROOT / "pipeline" / "keywords.yaml"
DIPLOMATS_PATH = ROOT / "pipeline" / "diplomats.yaml"
# Personas and false fronts named in published investigations of planted contributions
# (pipeline.planted). A missing file loads as an empty list, never as an error.
PERSONAS_PATH = ROOT / "sources" / "personas.yaml"
EXPORT_DIR = ROOT / "docs" / "data"

PROJECT_NAME = "ChinaStateMediaTracker"

CONTACT = _env("TRACKER_CONTACT", "see repository issues")
# Which collector this process is. Outlets whose feeds refuse GitHub's runner addresses carried
# collector: self_hosted in the registry and were polled only by the relay on the owner's machine,
# until it was retired (RELAY_RETIRED_ON); they are now inactive.
COLLECTOR = _env("TRACKER_COLLECTOR", "hosted")
USER_AGENT = (
    "{name}/1.0 (+{repo}; "
    "research crawler, contact {contact})"
).format(name=PROJECT_NAME, repo=_env("TRACKER_REPO_URL", "https://github.com/dbanin/china-media-tracker"), contact=CONTACT)

# Polite fetching
MIN_SECONDS_PER_DOMAIN = 3.0
JITTER_SECONDS = 1.0
# A robots.txt Crawl-delay for this crawler (or for "*") raises a domain's gap above the minimum.
# Values above the cap are read as the cap and logged: one fetch every ten minutes is already as
# slow as a run can usefully go, and a typo such as 86400 would otherwise park the domain for a day.
CRAWL_DELAY_CAP_SECONDS = 600.0
# Threads that serve domains with a Crawl-delay above the minimum. They spend nearly all their time
# asleep between slots, so they sit beside the ordinary fetch workers rather than taking their places.
SLOW_DOMAIN_THREADS = 64
FETCH_TIMEOUT = 20
FEED_TIMEOUT = 20
MAX_RETRIES = 2

# Classification
LLM_MODEL = _env("TRACKER_LLM_MODEL", "claude-sonnet-5")
# Enough for the JSON plus a long evidence quote. Output tokens are billed as used, so headroom is
# nearly free, while a reply cut off mid-JSON wastes the whole call: 47 of 279 calls on 2026-09-16
# failed to parse and truncation is the likeliest cause.
LLM_MAX_TOKENS = 1200
LLM_DAILY_CALL_CEILING = int(_env("TRACKER_LLM_DAILY_CEILING", "600"))
# Dollars the model stage may spend in a calendar month, matched to the spend limit on the API
# account so the tracker stops itself before the account does. pipeline.llm_cost spreads what is
# left over the days remaining in the month and turns it into a second daily call cap; the lower of
# the two caps binds. 0 stops model calls. Set it very high to rely on the call ceiling alone.
# Since 8 October the default is sized for the API to judge every article the rules cannot settle,
# about 340 a day at September's intake (session route included), at the measured cost of a call.
LLM_MONTHLY_BUDGET_USD = float(_env("TRACKER_LLM_MONTHLY_BUDGET_USD", "110"))
# Published per million token prices for the classifier model, and the Batches API discount.
LLM_PRICES_PER_MTOK = {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30}
LLM_BATCH_DISCOUNT = 0.5
# One synchronous call, from the first month's bill: 36 dollars for 3,733 calls of which about nine
# in ten went through the Batches API at half price, so 0.0096 a call on average and about 0.0175
# for a call at full price. The budget cap uses the full price only until the database holds enough
# recorded calls to measure the real rate (pipeline.llm_cost.per_call_estimate); usage rows written
# before cost tracking existed are priced at the average.
LLM_COST_PER_CALL_UNBATCHED = 0.0175
LLM_MEASURED_COST_PER_CALL = 0.0096
# Recorded calls in the current and previous calendar month needed before their average replaces
# the full price estimate in the daily cap. Fewer, and one unusual day would set the cap.
LLM_MEASURED_MIN_CALLS = 500
LLM_BODY_CHAR_LIMIT = 12000
# Since 2026-09-30 waiting articles are also judged outside the API, in an interactive session on
# the owner's machine (pipeline.labels), which refetches the text there. Articles whose text the
# session could not fetch are listed in data/labels/api_first.jsonl and are offered to the API
# first. When this is above 0, any other waiting article is offered to the API only once it was
# discovered more than this many days ago, so the session gets the first chance at it. Since 8
# October the hold is off by default: the budget is sized for the API to take every waiting article,
# and the session route is a fallback for when the budget is spent.
LLM_SESSION_GRACE_DAYS = int(_env("TRACKER_SESSION_GRACE_DAYS", "0"))
# When the classifier prompt changes (classify_llm.PROMPT_VERSION), current model labels made under
# an older prompt are judged again (model state origin labels on no credit, stamp or byline first, then
# unchecked state sourcing, oldest first within each tier; classify_llm.RELABEL_PRIORITY) and keep their old label until the new one arrives.
# They take what the new articles leave of each run's calls, and never more than this share of the
# day's binding cap (the lower of the call ceiling and the budget cap), paced over the day.
LLM_RELABEL_DAY_SHARE = float(_env("TRACKER_LLM_RELABEL_SHARE", "0.5"))
REVIEW_CONFIDENCE_THRESHOLD = 0.85
KAPPA_WARNING_THRESHOLD = 0.7

# Items the relevance gate rejected are kept this many days for gate audits, then pruned.
# This is the one deliberate exception to the never-delete rule: rejected items carry no
# classification, and keeping every one of them would grow the database by tens of
# megabytes a day.
GATED_OUT_RETENTION_DAYS = 3
# Except a random share of them, kept permanently so the gate's recall can be audited over any
# period, not only the last GATED_OUT_RETENTION_DAYS. Membership is decided by a salted hash of
# the item's url_hash (store.gate_audit_keep), so it is reproducible, the same on the relay and
# the hosted side, and independent of outlet, country and language. At about 80,000 to 90,000
# rejections a day, 2 percent is roughly 1,700 rows a day; measured on the 2026-10-12 database at
# about 1.07 KB a row with indexes, that is about 1.8 MB a day, some 50 to 55 MB a month.
# Changing the share affects only items not yet decided.
GATE_AUDIT_KEEP_SHARE = 0.02
# The address of every item a prune deletes is remembered (store.seen_urls), so a feed that still lists
# it does not bring it in, and count it, a second time. Until 2026-10-12 nothing was remembered: a feed
# that listed an item for longer than GATED_OUT_RETENTION_DAYS brought it back after every prune, into
# the articles table and into the discovery counts. Comparing the database snapshots of 2026-09-30 to
# 2026-10-12 found 73,763 such repeats among the 905,252 discoveries of 2026-10-01 to 2026-10-11 (8.2
# percent; 89 percent of se_sr's, 99 percent of gb_economist's), at least 123,682 since collection began.
# Nearly all came back at the first poll after the prune (99 percent within 3.5 days of the previous
# sighting, none after more than 6.5 within the window the snapshots cover without a gap), and one in ten
# was published more than 100 days before it was seen again. So the clock runs from the latest sighting,
# not from publication: an address seen again is kept SEEN_URL_RETENTION_DAYS after its latest sighting,
# however long a feed keeps listing it, and one never seen again after its discovery is dropped
# SEEN_URL_UNSEEN_DAYS after it, twice the longest gap measured. At about 60,000 pruned rejections a day
# and about 21 bytes a row, that is some 20 to 30 MB.
SEEN_URL_RETENTION_DAYS = 120
SEEN_URL_UNSEEN_DAYS = 14
# What the repeats above did to the discovery counts, which stay as they were (the pruned addresses that
# would show which items came back are gone). Measured on the 2026-10-12 database and its snapshots: at
# least 123,682 of the 3,008,671 items screened since collection began were such repeats (4.1 percent,
# the items the snapshots show coming back), and applying each outlet's share of repeats in 2026-10-01 to
# 2026-10-11 (8.2 percent overall) to the whole period puts it near 8 percent, about 240,000. The share
# differs by country (about 72 percent in the Cayman Islands, 50 in Sweden, 47 in Kuwait, 28 in Uruguay,
# 18 in Vietnam, 16 in Britain), so every share of a country's output read before the fix is low by
# that country's share. export.rediscovery puts these in meta.rediscovery for the site and the
# methodology. REDISCOVERY_FIXED_FROM is used only when the database does not record the first run
# that remembered pruned addresses (store schema_migrations, "seen_urls.remembering").
REDISCOVERY_ESTIMATED_SHARE = 0.08
REDISCOVERY_MEASURED_FLOOR = 123682
REDISCOVERY_MEASURED_OF = 3008671
REDISCOVERY_WINDOW_SHARE = 0.082
REDISCOVERY_FIXED_FROM = "2026-10-13"
EXPORT_DIR_AUDIT = ROOT / "data" / "export"

# Day of record for items a backfill read brings in. Every figure counts an item on the UTC day it was
# discovered (export._day). A reader that reads a dated listing back to a past date, like the AAP
# release reader (section_pages.aap_entries), whose first run reads back to 2026-09-03 and finds about
# 1,459 releases for each of ten mastheads, would put weeks of output on one day, in the counts and in
# the "% of all its news" denominators alike. An item from such a read that was discovered more than
# BACKFILL_LAG_HOURS after its published time, and published on or after COLLECTION_START, counts on its
# publication day instead, in both (store.backfill_day, stored in articles.day_of_record at insert).
# The rule is for backfill reads only. Measured on the 2026-10-12 database, the same lag rule applied to
# every item would have moved 1,473 of 80,272 classified articles (1.8 percent; 23 percent in Kazakhstan,
# 18 in Australia), and could not have moved the denominators of days whose rejected items are already
# pruned, so numerator and denominator would have parted.
BACKFILL_LAG_HOURS = 36
COLLECTION_START = "2026-09-03"     # the first day of discovery; no item counts on an earlier day
# A feed's first successful poll ever, and its first successful poll after more than BACKFILL_GAP_DAYS
# without one, are backfill reads too (fetch_feeds.backfill_poll): a new outlet or section, or a feed
# back from an outage, delivers dozens of items at once that were published while nobody was reading it.
BACKFILL_GAP_DAYS = 3

# Paywall threshold above which a country is flagged as not comparable
PAYWALL_FLAG_SHARE = 0.33

# Window of the recurring bylines diagnostic (pipeline.planted.recurring_bylines): authors with
# China-relevant articles in several unrelated outlets and countries over this many days.
PLANTED_RECURRING_DAYS = 30

# Fraction of a country's feeds that may fail before the country gets a warning marker
FEED_FAILURE_WARNING_SHARE = 0.5

# The share of all published items stands for a country only when at least this many outlets are
# behind its denominator. Below it the share describes a handful of feeds and is not shown.
MIN_OUTLETS_FOR_OUTPUT_SHARE = 5

# Until the hand coding study has passed, unverified relay is shown as provisional: real counts, marked
# everywhere as one model's judgement that has not been checked. Provisional means not checked yet, never
# checked and failed: a hand coding kappa below the threshold, or a model stability check on the current
# prompt below it, withholds the counts. A passing stability check leaves them provisional.
RELAY_PROVISIONAL_DISPLAY = _env("TRACKER_RELAY_PROVISIONAL", "1") == "1"

# The reliability study runs by itself (pipeline.second_rater auto) when it is needed and the monthly
# budget can pay for it: through the Batches API, at most once a calendar month, for at most this share
# of the budget left in the month.
RELIABILITY_AUTO = _env("TRACKER_RELIABILITY_AUTO", "1") == "1"
RELIABILITY_SAMPLE = 250
RELIABILITY_MIN_POOL = 200        # model-labelled articles with a body on disk before a study is worth running
RELIABILITY_MIN_PAIRS = 150       # usable results below this record no study
RELIABILITY_REFRESH_DAYS = 90
RELIABILITY_MAX_BUDGET_SHARE = 0.15

# Unverified relay counts are withheld, not merely annotated, until an agreement study settles them.
# A language with at least this many items in the unverified relay versus independent journalism
# comparison and a kappa below the threshold has its relay counts withheld even when the overall
# kappa passes.
KAPPA_MIN_LANGUAGE_ITEMS = 20

# The relay collector on the owner's machine. A day on which it completed fewer than
# RELAY_DAY_MIN_HOURS hourly runs is incomplete for the countries it collects, and a relay silent
# for RELAY_STALE_HOURS puts a warning on those countries.
RELAY_DAY_MIN_HOURS = 12
RELAY_STALE_HOURS = 6
# The day the relay was retired. Collecting outlets that refuse the crawler from another network works
# around their access control, so it stopped: its outlets (collector: self_hosted) were made inactive
# that day, days after it are never marked incomplete, and nothing warns that it is silent.
RELAY_RETIRED_ON = "2026-10-22"

# Share of a country's feed polls that came back as a full window with no overlap with the previous
# poll, above which the country is warned that items were probably lost between polls.
FEED_SATURATION_WARNING_SHARE = 0.25
# Share of a country's feed items estimated missed between polls (missed against missed plus found, over
# the polls the current estimator covers), at or above which the country is warned. Saturated polls
# alone understated it: no country reached 25 percent of polls saturated while Egypt lost about half
# its items (measured 2026-10-17).
FEED_MISSED_WARNING_SHARE = 0.10
# A feed whose newest item is older than this many days is frozen: it answers, but with old items, so
# it counts as failing in feed health and in the warnings (feed_health.newest_entry).
FEED_FROZEN_DAYS = 14
# Share of attempted article fetches that could not be read for any reason (paywalled, failed or
# blocked by robots.txt together), at or above which the country is warned.
UNREADABLE_WARNING_SHARE = 0.33
# Fewer China relevant items than this in the last 30 days and a country's figures are too few to
# compare with other countries.
MIN_RELEVANT_ITEMS_30D = 50
# The site marks the data stale when meta.generated_at is older than this many hours.
DATA_STALE_HOURS = 24

# The missed-items estimator (pipeline/fetch_feeds.py saturation) a feed_polls row was made by, kept on
# the row (feed_polls.estimator; "v1" for every row made before 2026-10-12, which inflated batch
# publishers and short windows and cannot be recomputed, since single poll times are not kept). The
# export reports the estimate only from rows made by this version, from the first day it was used.
# Bump it whenever the estimator changes meaning.
MISSED_ESTIMATOR_VERSION = "2026-10-12"

# Current ruleset version. Bump in CHANGELOG.md whenever signatures.yaml or diplomats.yaml changes meaning.
# Must match ruleset_version in signatures.yaml: a label records the version the signatures carried,
# while reclassify() compares against this one, so a mismatch would reclassify the same rows forever.
RULESET_VERSION = "2026.10.2"

# Current extraction version, recorded on every stored body (articles.extract_version; NULL for bodies
# stored before it existed). Bump it whenever pipeline/extract.py reads an outlet's template
# differently, and list the template in extract.EXTRACTION_CHANGES: a body of that template stored
# under an older version is extracted again from the saved page, or fetched again politely, before it
# is classified (classify_rules.ensure_body). Templates not listed keep their bodies.
EXTRACT_VERSION = "2026.10.1"

# Reference sources (sources/reference.yaml, pipeline/reference.py): at most this many article pages of
# theirs are fetched per run, newest first, and the stored body of an item no state copy was paired
# with (no state_copies row) is deleted this many days after it was fetched. The row and its
# fingerprint are kept for good.
REFERENCE_FETCH_PER_RUN = 200
REFERENCE_UNPAIRED_BODY_DAYS = 30

# Current relevance gate version. Separate from the ruleset: the ruleset decides an article's label
# and is stored on every classification row, while the gate decides what enters the corpus at all.
# Gate decisions are recorded per item and never revisited, so a change here relabels nothing and
# must not trigger reclassification; it does move the boundary of what is collected, which is why the
# version and its date are published and marked on the timeline. Bump it in CHANGELOG.md under a
# "## Gate <version> (<date>)" heading whenever keywords.yaml or the gate's rules change meaning.
GATE_VERSION = "2026.10.2"
SCHEMA_VERSION = 18   # 18: docs/data/spikes.json carries each unusual day's context and sourced news notes (method.context, method.news); 17: docs/data/archive_checks.json, the licensed archive check (ProQuest TDM Studio aggregates, rules-only screen, never in any count); 16: the model-only category becomes U, undetermined as in the original study, excluded from every count and denominator (china_total is A + B + C), holding model state origin calls with no checkable marker, model-read sponsored disclosures the rules did not confirm and uncredited copies of undetermined items; meta.undetermined and findings.undetermined replace meta.model_only_state_origin and findings.model_only; 15: model-only state origin as its own category M in every count (held apart from state origin, counted in China coverage) with meta.model_only_state_origin and findings.model_only, meta.gate_accounting (every gate-passed item by status, adding up), meta.relay_collector describing the retired relay (retired_on, dropped_outlets, dropped_by_country, countries_all_inactive, countries_partial); 14: issuer types and review markers on labels (meta.issuer_type_totals, inconsistent_labels), placements counted once per shared widget, page and numbered series (meta.shared_widgets, duplicate_placements, outlets.json not_counted), top carrier and route details with ties and shares, new coverage warnings and fields (missed share, frozen feeds, unreadable, excluded languages, too few items, no working outlet), schema_version and generated_at stamped in the data files, meta.stale_after_hours and meta.llm_budget.provider_limit; 13: meta.measures (one gate block per new measure: status off, provisional, checked or withheld, with its hand check) and the side files docs/data/alteration.json, speed.json, doctrine.json, spikes.json, engagement.json and syndication.json, each written only while its measure is not off; 12: each day in the daily files carries underlying_items, the item ids behind its state origin and unchecked state sourcing counts per country, and latest.json uniqA and uniqAB count each item once per window and, in the totals, once across countries; 11: global_series.json carries the worldwide distribution stratum per day under wld, and meta.findings carries the wires' part of every all time total (state_origin_wires and so on) and a last_30d block, so the site's Distribution toggle can show the country outlets and the wires apart; 10: worldwide distribution stratum WLD in latest.json, the daily files, outlets.json (wires carry country WLD) and articles/WLD.json, included in world totals and never in a country; 9: docs/data/planted.json (planted byline flags and the recurring bylines diagnostic) and meta.planted; 8: meta.findings summary box, docs/data/examples.json worked examples with matched span offsets, per country top_carrier and top_route in latest.json; 7: discovery day attribution, distribution wire stratum, model only state origin, per day audit files; 6: tb (relay among top outlet items), fourth theme slot for unverified relay, relay_provisional, relay_study; 2: population and top outlet denominators; 3: ta; 4: per day theme counts, theme catalog in meta, themes on articles; 5: routes, model draw fractions, feed saturation, relay hours, language support, relay publication gate
