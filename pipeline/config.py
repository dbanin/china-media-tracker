"""Central configuration. Values that may change live here, not as literals in other modules."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "tracker.db"
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
def _env(name: str, default: str) -> str:
    """Environment value, falling back to the default when the variable is unset or empty.
    GitHub Actions passes an undefined repository variable as an empty string."""
    value = os.environ.get(name)
    return value if value else default


CONTACT = _env("TRACKER_CONTACT", "see repository issues")
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

# Current ruleset version. Bump in CHANGELOG.md whenever signatures.yaml changes meaning.
RULESET_VERSION = "2026.09.3"
SCHEMA_VERSION = 3   # 2: population and top outlet denominators (tdisc, ttarget, tchina); 3: ta, state origin in the top outlet set
