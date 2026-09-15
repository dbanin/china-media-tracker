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
LLM_MAX_TOKENS = 700
LLM_DAILY_CALL_CEILING = int(_env("TRACKER_LLM_DAILY_CEILING", "600"))
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
RULESET_VERSION = "2026.09.5"

# Current relevance gate version. Separate from the ruleset: the ruleset decides an article's label
# and is stored on every classification row, while the gate decides what enters the corpus at all.
# Gate decisions are recorded per item and never revisited, so a change here relabels nothing and
# must not trigger reclassification; it does move the boundary of what is collected, which is why the
# version and its date are published and marked on the timeline. Bump it in CHANGELOG.md under a
# "## Gate <version> (<date>)" heading whenever keywords.yaml or the gate's rules change meaning.
GATE_VERSION = "2026.09.6"
SCHEMA_VERSION = 5   # 2: population and top outlet denominators; 3: ta; 4: per day theme counts, theme catalog in meta, themes on articles; 5: routes, model draw fractions, feed saturation, relay hours, language support, relay publication gate
