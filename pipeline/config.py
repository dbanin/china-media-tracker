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
OUTLETS_PATH = ROOT / "sources" / "outlets.yaml"
OUTLETS_SCHEMA_PATH = ROOT / "sources" / "outlets_schema.json"
GAPS_PATH = ROOT / "sources" / "gaps.yaml"
GAPS_SCHEMA_PATH = ROOT / "sources" / "gaps_schema.json"
SIGNATURES_PATH = ROOT / "pipeline" / "signatures.yaml"
KEYWORDS_PATH = ROOT / "pipeline" / "keywords.yaml"
DIPLOMATS_PATH = ROOT / "pipeline" / "diplomats.yaml"
EXPORT_DIR = ROOT / "docs" / "data"

PROJECT_NAME = "ChinaStateMediaTracker"

CONTACT = _env("TRACKER_CONTACT", "see repository issues")
# Which collector this process is. Outlets whose feeds refuse GitHub's runner addresses carry
# collector: self_hosted in the registry and are polled only by the self-hosted runner.
COLLECTOR = _env("TRACKER_COLLECTOR", "hosted")
USER_AGENT = (
    "{name}/1.0 (+{repo}; "
    "research crawler, contact {contact})"
).format(name=PROJECT_NAME, repo=_env("TRACKER_REPO_URL", "https://github.com/dbanin/china-media-tracker"), contact=CONTACT)

# Polite fetching
MIN_SECONDS_PER_DOMAIN = 3.0
JITTER_SECONDS = 1.0
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
LLM_MONTHLY_BUDGET_USD = float(_env("TRACKER_LLM_MONTHLY_BUDGET_USD", "30"))
# Published per million token prices for the classifier model, and the Batches API discount.
LLM_PRICES_PER_MTOK = {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30}
LLM_BATCH_DISCOUNT = 0.5
# One synchronous call, from the first month's bill: 36 dollars for 3,733 calls of which about nine
# in ten went through the Batches API at half price, so 0.0096 a call on average and about 0.0175
# for a call at full price. The budget cap uses this before a month has recorded costs of its own;
# usage rows written before cost tracking existed are priced at the average.
LLM_COST_PER_CALL_UNBATCHED = 0.0175
LLM_MEASURED_COST_PER_CALL = 0.0096
LLM_BODY_CHAR_LIMIT = 12000
REVIEW_CONFIDENCE_THRESHOLD = 0.85
KAPPA_WARNING_THRESHOLD = 0.6

# Items the relevance gate rejected are kept this many days for gate audits, then pruned.
# This is the one deliberate exception to the never-delete rule: rejected items carry no
# classification, and keeping every one of them would grow the database by tens of
# megabytes a day.
GATED_OUT_RETENTION_DAYS = 3
EXPORT_DIR_AUDIT = ROOT / "data" / "export"

# Paywall threshold above which a country is flagged as not comparable
PAYWALL_FLAG_SHARE = 0.33

# Fraction of a country's feeds that may fail before the country gets a warning marker
FEED_FAILURE_WARNING_SHARE = 0.5

# The share of all published items stands for a country only when at least this many outlets are
# behind its denominator. Below it the share describes a handful of feeds and is not shown.
MIN_OUTLETS_FOR_OUTPUT_SHARE = 5

# Until a reliability study has run, unverified relay is shown as provisional: real counts, marked
# everywhere as one model's judgement that has not been checked. Provisional means not checked yet, never
# checked and failed: once any study has produced a kappa, the counts are either published or withheld.
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

# Share of a country's feed polls that came back as a full window with no overlap with the previous
# poll, above which the country is warned that items were probably lost between polls.
FEED_SATURATION_WARNING_SHARE = 0.25

# Current ruleset version. Bump in CHANGELOG.md whenever signatures.yaml changes meaning.
# Must match ruleset_version in signatures.yaml: a label records the version the signatures carried,
# while reclassify() compares against this one, so a mismatch would reclassify the same rows forever.
RULESET_VERSION = "2026.09.9"

# Current relevance gate version. Separate from the ruleset: the ruleset decides an article's label
# and is stored on every classification row, while the gate decides what enters the corpus at all.
# Gate decisions are recorded per item and never revisited, so a change here relabels nothing and
# must not trigger reclassification; it does move the boundary of what is collected, which is why the
# version and its date are published and marked on the timeline. Bump it in CHANGELOG.md under a
# "## Gate <version> (<date>)" heading whenever keywords.yaml or the gate's rules change meaning.
GATE_VERSION = "2026.09.7"
SCHEMA_VERSION = 7   # 7: discovery day attribution, distribution wire stratum, model only state origin, per day audit files; 6: tb (relay among top outlet items), fourth theme slot for unverified relay, relay_provisional, relay_study; 2: population and top outlet denominators; 3: ta; 4: per day theme counts, theme catalog in meta, themes on articles; 5: routes, model draw fractions, feed saturation, relay hours, language support, relay publication gate
