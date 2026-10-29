"""State copies set beside their originals, and the alteration score (measure "alteration").

Report only. Nothing here changes a label, a count, a denominator or the outlets monitored: the
module writes state_copies and the side files, and reads everything else. China stays unmonitored.

Pairing. Each current state origin label gets one state_copies row (copy_id is its articles.id):
  1. a reference original first: an item from a reference source (reference_items, the state
     outlets' own pages, sources/reference.yaml) whose publisher the copy's credit names, in the
     copy's language, published from WINDOW_AFTER_DAYS before the copy to WINDOW_BEFORE_DAYS after
     it. Candidates come from the headline, feed summary and body fingerprint indexes of
     pipeline/lookalike.py (_Index, tie_rank) and pipeline/body_dups.py (fingerprint and its LSH
     bands). When both bodies exist, at least ALIGN_MIN of the copy's words must align with the
     candidate, or it is rejected;
  2. otherwise the earliest credited monitored copy of the same item, in the same language, from
     another outlet outside the copy's own network (the network field of sources/outlets.yaml: sister
     mastheads of one group, such as the ACM or Postmedia papers, share one copy pool, so a sister's
     earlier copy is not an original): the copy's underlying item group (body_dups.item_groups over
     dup_group_id) or the lookalike match in its llm_trigger. The match is snapshotted into the row (details), so a
     reclassification that rewrites llm_trigger does not lose it;
  3. otherwise none, with a reason (none_reason); details.sister_only says how many earlier copies
     were passed over because they ran at a sister outlet.
A monitored original is an earlier copy, not the state text: every figure says which kind of
original it rests on, and until reference pairs are scored the shares are against an earlier
monitored copy.
A copy whose language differs from every credit consistent reference source's language is a
translation (Italpress runs Xinhua English in Italian): it is never matched across languages. Its
row says so (details.translation, none_reason "translation" when no monitored original is found).
Copies first seen in the last REPAIR_DAYS days are paired again on every export; older rows stay as
they are unless the method version changes or a body they lacked has arrived. A row whose label is
withdrawn is kept with current = 0.

Metrics (standard library only), when both bodies exist. Both texts are normalised: credit lines,
datelines, the outlet's template lines (lines in at least TEMPLATE_LINE_SHARE of the same outlet's
paired bodies) and the known listing noise of the reference pages (the CGTN language menu, People's
Daily related links) are dropped. Each copy paragraph is aligned against the whole original with
difflib, keeping runs of at least MIN_RUN words, so a moved paragraph still counts as kept.
  headline_similarity  difflib ratio of the two headlines' words
  kept_share           share of the original's words found in the copy (cuts lower it)
  novel_share          share of the copy's words not found in the original: the alteration score
  rewritten_share      novel words inside paragraphs that keep some of the original
  added_paragraphs, added_words  paragraphs with almost nothing of the original, and their words
  cue_local, cue_counterpoint    weak keyword cues in the novel text: the outlet's own country, and
                       criticism or rebuttal words. Weak, checked by hand, never a finding alone.
Bands of novel_share, provisional: verbatim under 0.03, light under 0.15, moderate under 0.40,
substantial 0.40 or more.

Distinct originals and items. Many copies repeat one original, so the export counts copies,
distinct originals (a reference item or a monitored article) and distinct underlying items (the
copies' item groups joined through their originals). Headline band shares weigh each underlying
item once (its scored copies share its weight), and the copy counts stand beside them. A group's
median, and an outlet's place in the table, need OUTLET_MIN_N distinct originals.

Times for pipeline/speed.py: copy_published_at and original_published_at are the credible published
times (lookalike.published_ts) in UTC, else NULL; copy_ts_res and orig_ts_res are second, minute,
hour, date (no time of day, or exactly 00:00:00 as written) or none (not credible); lag_seconds is
copy minus original when both are credible.

Bodies exist only on the runner (data/bodies) and on the owner's machine for a few. body_state
records which bodies the metrics saw, and the export reports coverage as it is.
"""
import csv
import datetime as dt
import difflib
import json
import math
import random
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from pipeline import body_dups, config, lookalike, store

MEASURE_ID = "alteration"
VERSION = "2"

# Pairing
WINDOW_AFTER_DAYS = 11      # the original may come up to this many days before the copy
WINDOW_BEFORE_DAYS = 1      # and up to this many days after it (times are noisy across sources)
ALIGN_MIN = 0.30            # share of the copy's words that must align with the original when both bodies exist
FINGERPRINT_CANDIDATE = 0.3  # body fingerprint similarity that makes a reference item a candidate
CANDIDATES_PER_INDEX = 5
REPAIR_DAYS = 14
# Metrics
MIN_RUN = 3                 # words in a row that count as kept text
ADDED_PARAGRAPH_MAX = 0.2   # a copy paragraph with less than this share of kept words is an added paragraph
MIN_WORDS = 30              # both texts need this many words after normalisation to be scored
MAX_TOKENS = 4000           # longer texts are scored on their first MAX_TOKENS words
TEMPLATE_LINE_SHARE = 0.20
TEMPLATE_MIN_BODIES = 5
BANDS = ("verbatim", "light", "moderate", "substantial")
BAND_LIMITS = (("verbatim", 0.03), ("light", 0.15), ("moderate", 0.40))
HEADLINE_REWRITTEN = 0.6    # headline similarity below this counts as a rewritten headline in the export
OUTLET_MIN_N = 5
# Hand check
PER_BAND = 20
PAIR_PRECISION_MIN = 0.9
KAPPA_MIN = 0.6
COMMIT_EVERY = 25

REFERENCE_YAML = config.ROOT / "sources" / "reference.yaml"
NONE_REASONS = {
    "no_state_credit": "the copy names no Chinese state publisher and no earlier monitored copy was found",
    "no_reference_source": "no reference source covers the credited publisher, and no earlier monitored copy was found",
    "translation": "a translation: no reference source of the credited publisher is in the copy's language, and no earlier monitored copy in that language was found",
    "no_reference_match": "no reference item matched, and no earlier monitored copy was found",
    "alignment_rejected": "reference candidates were found but under 30 percent of the copy's words aligned, and no earlier monitored copy was found",
}

# ---------------------------------------------------------------------------
# Credits: which state publishers a copy names
# ---------------------------------------------------------------------------

# Signature id prefixes (pipeline/signatures.yaml) to publishers. china_media_group_credit, CCTV and CRI
# belong to China Media Group, CGTN's parent: cmg is credit consistent with a CGTN reference.
SIGNATURE_PUBLISHERS = (
    ("source_xinhua", "xinhua"), ("xinhua_", "xinhua"), ("china_daily_", "china_daily"),
    ("global_times_", "global_times"), ("cgtn_", "cgtn"), ("china_media_group_credit", "cmg"), ("cri_", "cmg"),
    ("peoples_daily_", "peoples_daily"), ("china_news_service_", "china_news_service"),
)
TRIGGER_PUBLISHERS = {"cites_xinhua": "xinhua", "cites_cgtn": "cgtn", "cites_global_times": "global_times",
                      "cites_china_daily": "china_daily", "cites_cctv": "cmg", "cites_peoples_daily": "peoples_daily",
                      "cites_china_media_group": "cmg"}
NAME_PUBLISHERS = (
    ("xinhua", "xinhua"), ("china daily", "china_daily"), ("cgtn", "cgtn"), ("china global television", "cgtn"),
    ("global times", "global_times"), ("people's daily", "peoples_daily"), ("people’s daily", "peoples_daily"),
    ("renmin", "peoples_daily"), ("china news service", "china_news_service"), ("ecns", "china_news_service"), ("china media group", "cmg"),
    ("cctv", "cmg"), ("china radio international", "cmg"),
)
# Reference source ids to publishers when sources/reference.yaml does not say.
SOURCE_PUBLISHERS = (("ref_xinhua", "xinhua"), ("ref_cgtn", "cgtn"), ("ref_china_daily", "china_daily"),
                     ("ref_global_times", "global_times"), ("ref_peoples_daily", "peoples_daily"), ("ref_ecns", "china_news_service"),
                     ("ref_cri", "cmg"))
PUBLISHER_ALSO = {"cgtn": {"cmg"}}
CREDITED_ROUTES = {"wire_credit", "distribution_stamp", "sponsored_disclosure", "diplomatic_byline"}


def _json(v, default):
    if v is None or v == "":
        return default
    if not isinstance(v, str):
        return v
    try:
        return json.loads(v)
    except ValueError:
        return default


# A press release whose headline names a state publisher as its issuer ("CGTN: How China uses digital
# tech ..."), as the release wires print them. Xinhua Silk Road, a commercial release service, is not
# Xinhua's newsroom: its releases do not appear on Xinhua's own pages. An Africa service ("CGTN Africa:",
# "China Daily Africa |") is its publisher's, as the reference sources ref_*_africa_* are.
_HEADLINE_ISSUER = re.compile(
    r"^\s*(cgtn|china daily|xinhua(?! silk road)|global times|people'?s daily|people\u2019s daily|ecns|"
    r"china news service|cctv|china media group)(?:\s+africa)?\s*[:|]", re.IGNORECASE)


def credits_of(signatures_fired=None, china_sources_cited=None, llm_trigger=None, title=None) -> Set[str]:
    """The state publishers an article's label names: signature ids, the model's cited sources, the
    triggers that sent it to the model, and a state issuer heading a press release's headline."""
    out = set()
    m = _HEADLINE_ISSUER.match(title or "")
    if m:
        low = m.group(1).lower().replace("\u2019", "'")
        out.update(pub for name, pub in NAME_PUBLISHERS if name == low or (name == "people's daily" and low == "peoples daily"))
    for s in _json(signatures_fired, []) or []:
        for pfx, pub in SIGNATURE_PUBLISHERS:
            if str(s).startswith(pfx):
                out.add(pub)
    for c in _json(china_sources_cited, []) or []:
        low = str(c or "").lower()
        for name, pub in NAME_PUBLISHERS:
            if name in low:
                out.add(pub)
    trig = _json(llm_trigger, {}) or {}
    if isinstance(trig, dict):
        for t in trig.get("triggers") or []:
            if t in TRIGGER_PUBLISHERS:
                out.add(TRIGGER_PUBLISHERS[t])
        for s in trig.get("a_candidate") or []:
            for pfx, pub in SIGNATURE_PUBLISHERS:
                if str(s).startswith(pfx):
                    out.add(pub)
    return out


def load_reference_sources(path: Optional[Path] = None) -> Dict[str, Dict]:
    """{source_id: {publisher, language, credits}} from sources/reference.yaml, {} when it is absent."""
    p = Path(path or REFERENCE_YAML)
    if not p.exists():
        return {}
    import yaml
    with open(p, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    items = raw.get("sources") if isinstance(raw, dict) else raw
    out = {}
    for e in items or []:
        if isinstance(e, dict) and e.get("id"):
            out[str(e["id"])] = {"publisher": e.get("publisher"), "language": e.get("language"),
                                 "credits": [str(c) for c in (e.get("credits") or [])]}
    return out


def source_publisher(source_id: str, sources: Dict[str, Dict]) -> Optional[str]:
    pub = (sources.get(source_id) or {}).get("publisher")
    if pub:
        return str(pub)
    for pfx, p in SOURCE_PUBLISHERS:
        if str(source_id).startswith(pfx):
            return p
    return None


def carried_publishers(publisher: Optional[str], credit_prefixes: Iterable[str] = ()) -> Set[str]:
    """The publishers whose text a reference source carries: its own, and those its credit signature
    ids name (People's Daily English lists xinhua_, since it runs Xinhua copy)."""
    out = {publisher} if publisher else set()
    if publisher:
        out |= PUBLISHER_ALSO.get(publisher, set())
    for c in credit_prefixes or ():
        if not c:
            continue
        for pfx, pub in SIGNATURE_PUBLISHERS:
            if str(c).startswith(pfx) or pfx.startswith(str(c)):
                out.add(pub)
    return out


def credit_consistent(copy_credits: Set[str], copy_sigs: Iterable[str], publisher: Optional[str],
                      credit_prefixes: Iterable[str] = ()) -> bool:
    """Whether the copy's credit names this publisher, or one the source carries through its credit
    signature ids, or the copy fired one of those signature ids."""
    if copy_credits & carried_publishers(publisher, credit_prefixes):
        return True
    prefixes = [p for p in credit_prefixes if p]
    return any(str(s).startswith(p) for s in copy_sigs for p in prefixes)


# ---------------------------------------------------------------------------
# Times
# ---------------------------------------------------------------------------

_TIME_OF_DAY = re.compile(r"[T ](\d{2}):(\d{2})(?::(\d{2}))?")


def ts_resolution(row) -> str:
    """second, minute, hour, date or none: how precise the item's credible published time is as
    written. No time of day, or exactly 00:00:00, is date; a time that is not credible is none."""
    if lookalike.published_ts(row) is None:
        return "none"
    raw = (lookalike._get(row, "published_at") or "").strip()
    m = _TIME_OF_DAY.search(raw)
    if not m:
        return "date"
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if h == 0 and mi == 0 and s == 0:
        return "date"
    if s:
        return "second"
    if mi:
        return "minute"
    return "hour"


def _iso_or_none(ts: Optional[float]) -> Optional[str]:
    return lookalike._iso(ts) if ts is not None else None


def _when(row) -> Optional[float]:
    return lookalike.published_ts(row)


def _in_window(copy_row, orig_row) -> bool:
    """The original lies from WINDOW_AFTER_DAYS before the copy to WINDOW_BEFORE_DAYS after it, by
    credible published times when both have one, else by discovery."""
    cp, op = lookalike.published_ts(copy_row), lookalike.published_ts(orig_row)
    if cp is None or op is None:
        cp, op = lookalike._ts(lookalike._get(copy_row, "discovered_at")), lookalike._ts(lookalike._get(orig_row, "discovered_at"))
    if cp is None or op is None:
        return False
    return cp - WINDOW_AFTER_DAYS * 86400 <= op <= cp + WINDOW_BEFORE_DAYS * 86400


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_DASHES = "-\u2013\u2014"
# Agency names in credits and datelines. Full names match in any case; the short tags (AP, AGI, CNA,
# NOVA, APA ...) only in capitals, since in lower case they are words ("ap", "agi", "nova") and in
# title case they open ordinary sentences.
_AGENCIES_ANY_CASE = (r"xinhua|xinhuanet|ecns|cgtn|cctv|china daily|global times|people'?s daily|people\u2019s daily|"
                      r"italpress|agenzia nova|adnkronos|bernama|antara|reuters")
_AGENCIES_CAPS = r"ANSA|AGI|NOVA|Nova|AKI|AFP|AP|EFE|LUSA|Lusa|APA|DPA|dpa|CNA|KPL|MNA|AIP|CRI|ECNS|CGTN|CCTV"
_AGENCIES = r"(?i:%s)|%s" % (_AGENCIES_ANY_CASE, _AGENCIES_CAPS)
# A dateline: a place (and date) of at most eight words in capitals or title case, the agency or an
# upper case tag in brackets, then a dash: "BEIJING, Oct. 13 (Xinhua) --", "ROMA (ITALPRESS) -",
# "Jakarta (ANTARA) -". Case sensitive outside the agency names, so "The minister (who spoke) - " is
# text, not a dateline.
_DATELINE = re.compile(
    r"^(?:[A-Z\u00C0-\u00DE][\w\u00C0-\u024F.,'\u2019/]*(?:\s+[\w\u00C0-\u024F.,'\u2019/]+){0,7}\s*)?"
    r"\(\s*(?:%s|[A-Z][A-Z .]{1,24})\s*\)\s*[%s]{1,2}\s*" % (_AGENCIES, _DASHES))
_DATELINE_UPPER = re.compile(r"^[A-Z][A-Z .,'\u2019]{2,40}(?:\([A-Z .]{2,20}\))?\s*[%s]{1,2}\s+(?=\S)" % _DASHES)
_TAIL_CREDIT = re.compile(r"\(\s*(?:%s)\s*\)\.?\s*$" % _AGENCIES)
_CREDIT_WORDS = re.compile(
    r"^\W*(?:source|sources|fonte|fuente|fontes|sumber|quelle|photo|photos|foto|fotos|fotografia|image|images|video|"
    r"editor|editing|edited by|reporting|reported by|written by|translated|translation|traduzione|tradu\u00e7\u00e3o|"
    r"traducci\u00f3n|credit|credits|cr\u00e9dit|copyright|courtesy|agenzia|agencia|agence|ag\u00eancia|by|text|testo|"
    r"texto|\u00a9)(?!\w)", re.IGNORECASE)
_AGENCY_NAME = re.compile(r"(?<!\w)(?:%s)(?!\w)" % _AGENCIES)
_AGENCY_MARKED = re.compile(r"[\(\[]\s*(?:[^\)\]]*?[\s/])?(?:%s)\s*[\)\]]|(?<!\w)(?:%s)\s*/|/\s*(?:%s)(?!\w)|\u00a9"
                            % (_AGENCIES, _AGENCIES, _AGENCIES))
_CODE_LINE = re.compile(r"^[\w]{1,6}(?:/[\w]{1,6})+$")
_RELATED = re.compile(
    r"^(?:related(?: stories| news| links| articles| reading)?|recommended(?: stories)?|read more|more stories|"
    r"you may (?:also )?like|also read|leggi anche|articoli correlati|lire aussi|à lire aussi|lea también|"
    r"leia também|baca juga|lihat juga|mehr zum thema)\b", re.IGNORECASE)
# The CGTN language menu and section bar (T1a: about 100 lines kept by the extractor).
_MENU_LINES = {
    "english", "中文", "français", "francais", "español", "espanol", "русский",
    "العربية", "عربي", "português", "deutsch", "italiano",
    "日本語", "한국어", "türkçe", "cgtn", "live", "home", "world", "politics", "business",
    "tech & sci", "tech", "sci-tech", "culture", "sports", "sport", "opinions", "opinion", "video", "videos", "photos",
    "radio", "search", "share", "copied", "copy link", "follow us", "download app", "app", "nature", "travel",
    "documentary", "specials", "more", "menu", "trending", "top news", "latest", "newsletter", "subscribe",
}
_NOISE_LINE = re.compile(r"^[■□●•*#|_=~.\s]*$")


def _line_key(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip().lower())


def is_credit_line(line: str) -> bool:
    """A short line that is a credit, not text: a photo or source credit ("Source: Xinhua", "(Photo by
    Li Ming/Xinhua)", "Photo: AP"), an agency tag ("(ANSA)", "AFP"), a code ("fsc/abr"). The agency must
    be named in a credit's place: after a credit word that opens the line, in brackets, beside a slash
    or after a copyright sign. A sentence that merely names an agency ("Asked by AP, she said no.") is
    text, and so is a long sentence opening with a credit word."""
    s = line.strip()
    words = s.split()
    if not words:
        return True
    if _CODE_LINE.match(s.replace(" ", "")) and len(words) <= 2:
        return True
    if len(words) > 12:
        return False
    if re.fullmatch(r"[\(\[]?\s*(?:%s)\s*[\)\]]?\.?" % _AGENCIES, s):
        return True
    if not _AGENCY_NAME.search(s):
        return False
    if _DATELINE.match(s) and len(_DATELINE.sub("", s, count=1).split()) >= 2:
        return False      # a short opening paragraph after its dateline: "ROMA (ITALPRESS) - Il governo ha detto."
    if _AGENCY_MARKED.search(s):
        return True
    m = _CREDIT_WORDS.match(s)
    if m:
        # "By Xinhua" is a credit, "By the way, AP ..." is not: after a bare "by" the agency comes next.
        if re.sub(r"\W", "", m.group(0)).lower() == "by" and not _AGENCY_NAME.match(s[m.end():].lstrip()):
            return False
        # "Source: Xinhua" is a credit; "Reporting by AP showed that the island's ports ..." is a sentence.
        return not (len(words) >= 8 and s[-1:] in ".!?")
    return False


def normalise(text: str, template_lines: Optional[Set[str]] = None) -> List[str]:
    """The paragraphs of a body that are the article's text: credit lines, datelines, template lines
    and listing noise dropped."""
    lines = [l.strip() for l in (text or "").replace("\r", "\n").split("\n")]
    lines = [l for l in lines if l]
    # A related links block in the last part of the body ends it.
    for i, l in enumerate(lines):
        if i >= max(1, int(len(lines) * 0.6)) and _RELATED.match(l):
            lines = lines[:i]
            break
    out = []
    for i, l in enumerate(lines):
        key = _line_key(l)
        if template_lines and key in template_lines:
            continue
        if _NOISE_LINE.match(l) or (len(l.split()) <= 3 and key in _MENU_LINES):
            continue
        if is_credit_line(l):
            continue
        if i < 4:
            l = _DATELINE.sub("", l, count=1)
            l = _DATELINE_UPPER.sub("", l, count=1)
        l = _TAIL_CREDIT.sub("", l).strip()
        if l:
            out.append(l)
    return out


def template_lines_of(bodies: Iterable, share: float = TEMPLATE_LINE_SHARE,
                      min_bodies: int = TEMPLATE_MIN_BODIES) -> Set[str]:
    """Lines found in at least `share` of these bodies (one outlet's), when there are at least
    min_bodies of them. A body may be given as (story, text): bodies of one story (the same headline)
    count once, so an outlet that ran one story several times does not make its text a template."""
    groups: Dict[str, Set[str]] = {}
    for i, b in enumerate(bodies):
        story, text = b if isinstance(b, tuple) else (str(i), b)
        if not text:
            continue
        groups.setdefault(str(story), set()).update(_line_key(l) for l in text.split("\n") if l.strip())
    if len(groups) < min_bodies:
        return set()
    c = Counter()
    for lines in groups.values():
        c.update(lines)
    need = max(2, int(math.ceil(share * len(groups))))
    return {k for k, n in c.items() if n >= need}


def _tokens(paragraph: str) -> List[str]:
    return lookalike.tokens(paragraph)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

_COUNTER = {
    "en": r"however|critics?|critici[sz]\w*|accus\w*|alleg\w*|disput\w*|denie[sd]|denial|human rights|rights groups?|"
          r"propaganda|sceptic\w*|skeptic\w*|opposition|contradict\w*|rebut\w*|rejected|concerns? (?:about|over)",
    "it": r"tuttavia|critic[ahio]\w*|accus\w*|presunt\w*|smenti\w*|diritti umani|propaganda|oppositor\w*|contestat\w*|"
          r"denunci\w*|preoccupazion\w*",
    "fr": r"cependant|toutefois|critiqu\w*|accus\w*|présum\w*|démenti\w*|droits de l homme|droits humains|"
          r"propagande|opposant\w*|contest\w*|dénonc\w*|inquiétude\w*",
    "es": r"sin embargo|crític\w*|acus\w*|presunt\w*|desment\w*|derechos humanos|propaganda|opositor\w*|cuestion\w*|"
          r"denunci\w*|preocupación\w*",
    "pt": r"no entanto|contudo|crític\w*|acus\w*|alegad\w*|desment\w*|direitos humanos|propaganda|opositor\w*|"
          r"contest\w*|denunci\w*|preocupação\w*",
    "id": r"namun|kritik\w*|tuduh\w*|menuduh|dugaan|membantah|hak asasi manusia|propaganda|oposisi|kekhawatiran",
    "ms": r"namun|bagaimanapun|kritik\w*|tuduh\w*|menuduh|dakwaan|menafikan|hak asasi manusia|propaganda|pembangkang|kebimbangan",
    "de": r"jedoch|kritik\w*|vorwürf\w*|vorgeworfen|angeblich\w*|bestritt\w*|menschenrechte\w*|propaganda|opposition|bedenken",
}
_COUNTER_RE = {k: re.compile(r"(?<!\w)(?:%s)(?!\w)" % v) for k, v in _COUNTER.items()}
# Weak local context cue: the outlet's own country named in the added text (stems, lower case).
_LOCAL = {
    "ITA": "itali", "USA": "united states|american|americans|u s|washington", "MYS": "malaysia",
    "IDN": "indonesia", "LAO": "lao|laos|laotian|vientiane", "SGP": "singapor", "TCD": "tchad|chad",
    "ZAF": "south africa", "CAN": "canad", "GUY": "guyan", "SLV": "el salvador|salvadoreñ", "GIN": "guinée|guinea|conakry",
    "ESP": "españ|spain|spanish", "MLI": "mali", "IRN": "iran", "CHL": "chile", "HKG": "hong kong", "BRA": "brasil|brazil",
    "BFA": "burkina", "GBR": "britain|british|united kingdom|u k", "PER": "perú|peru", "SEN": "sénégal|senegal",
    "STP": "são tomé|sao tome", "GNB": "guiné bissau|guinea bissau", "PAK": "pakistan", "THA": "thai|ไทย",
    "GHA": "ghana", "EGY": "egypt", "NGA": "nigeria", "OMN": "oman", "RWA": "rwanda", "CUB": "cuba", "KGZ": "kyrgyz",
    "KHM": "cambod|កម្ពុជា", "KWT": "kuwait|الكويت", "DEU": "deutsch|germany|german",
    "FRA": "france|français|french", "ARG": "argentin", "BIH": "bosn", "PHL": "philippin|filipin", "VNM": "việt nam|vietnam",
    "KEN": "kenya", "IND": "india", "AUS": "australi", "NZL": "new zealand", "MEX": "méxico|mexico|mexican", "COL": "colombia",
    "PRT": "portug", "NLD": "nederland|dutch|netherlands", "BEL": "belg", "CHE": "suisse|schweiz|svizzera|swiss",
}
_LOCAL_RE = {k: re.compile(r"(?<!\w)(?:%s)" % v) for k, v in _LOCAL.items()}


def band_of(novel_share: Optional[float]) -> Optional[str]:
    if novel_share is None:
        return None
    for name, lim in BAND_LIMITS:
        if novel_share < lim:
            return name
    return "substantial"


def headline_similarity(a: str, b: str) -> Optional[float]:
    ta, tb = _tokens(a or ""), _tokens(b or "")
    if not ta or not tb:
        return None
    return round(difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio(), 4)


def _align(copy_paras: List[List[str]], orig: List[str]) -> Tuple[List[Set[int]], Set[int]]:
    """For each copy paragraph, the positions of its words that sit in a run of at least MIN_RUN
    words found in the original; and the original's positions so found. Each paragraph is matched
    against the whole original, so moved paragraphs still count as kept."""
    sm = difflib.SequenceMatcher(None, autojunk=False)
    sm.set_seq2(orig)
    kept_orig: Set[int] = set()
    marks = []
    for para in copy_paras:
        sm.set_seq1(para)
        m = set()
        for a, b, size in sm.get_matching_blocks():
            if size >= MIN_RUN:
                m.update(range(a, a + size))
                kept_orig.update(range(b, b + size))
        marks.append(m)
    return marks, kept_orig


def _cap(paras: List[List[str]]) -> List[List[str]]:
    out, n = [], 0
    for p in paras:
        if n >= MAX_TOKENS:
            break
        out.append(p[:MAX_TOKENS - n])
        n += len(out[-1])
    return out


def alignment(copy_text: str, orig_text: str, copy_templates: Optional[Set[str]] = None,
              orig_templates: Optional[Set[str]] = None) -> Optional[float]:
    """Share of the copy's words that align with the original (runs of MIN_RUN words), or None when
    either text is too short to say."""
    cp = _cap([_tokens(p) for p in normalise(copy_text, copy_templates)])
    op = [t for p in _cap([_tokens(p) for p in normalise(orig_text, orig_templates)]) for t in p]
    n = sum(len(p) for p in cp)
    if n < MIN_WORDS or len(op) < MIN_WORDS:
        return None
    marks, _ = _align(cp, op)
    return sum(len(m) for m in marks) / float(n)


def metrics(copy_text: str, orig_text: str, copy_title: str = "", orig_title: str = "",
            language: Optional[str] = None, country: Optional[str] = None,
            copy_templates: Optional[Set[str]] = None, orig_templates: Optional[Set[str]] = None) -> Optional[Dict]:
    """The alteration metrics of a copy against its original, or None when either text is too short."""
    cparas = [p for p in _cap([_tokens(p) for p in normalise(copy_text, copy_templates)]) if p]
    orig = [t for p in _cap([_tokens(p) for p in normalise(orig_text, orig_templates)]) for t in p]
    n = sum(len(p) for p in cparas)
    if n < MIN_WORDS or len(orig) < MIN_WORDS:
        return None
    marks, kept_orig = _align(cparas, orig)
    rewritten = added_words = added_paras = 0
    novel_text = []
    for para, m in zip(cparas, marks):
        if len(m) < ADDED_PARAGRAPH_MAX * len(para):
            added_paras += 1
            added_words += len(para)
            novel_text.append(" ".join(para))
        else:
            loose = [w for i, w in enumerate(para) if i not in m]
            rewritten += len(loose)
            if loose:
                novel_text.append(" ".join(loose))
    novel = (rewritten + added_words) / float(n)
    joined = " ".join(novel_text)
    lang = (language or "").split("-")[0].lower()
    cre = _COUNTER_RE.get(lang)
    lre = _LOCAL_RE.get((country or "").upper())
    return {
        "headline_similarity": headline_similarity(copy_title, orig_title),
        "kept_share": round(len(kept_orig) / float(len(orig)), 4),
        "novel_share": round(novel, 4),
        "rewritten_share": round(rewritten / float(n), 4),
        "added_paragraphs": added_paras,
        "added_words": added_words,
        "cue_local": len(set(lre.findall(joined))) if lre else 0,
        "cue_counterpoint": len(set(cre.findall(joined))) if cre else 0,
        "band": band_of(novel),
        "copy_words": n, "orig_words": len(orig),
        "aligned_share": round(sum(len(m) for m in marks) / float(n), 4),
    }


# ---------------------------------------------------------------------------
# Loading what pairing needs
# ---------------------------------------------------------------------------

_COPY_SQL = """SELECT a.id, a.outlet_id, a.country, a.language, a.title, a.summary, a.url, a.url_hash, a.published_at,
                      a.discovered_at, a.day_of_record, a.dup_group_id, a.llm_trigger, a.feed_url,
                      c.method, c.route, c.signatures_fired, c.china_sources_cited
               FROM classifications c JOIN articles a ON a.id = c.article_id
               WHERE c.is_current = 1 AND c.category = 'A'"""


def _current_copies(conn) -> Dict[int, Dict]:
    from pipeline import placements
    # One copy per placement: a row another row stands for (pipeline/placements.py) is not measured again.
    shadow = placements.shadowed(conn)
    out = {}
    for r in conn.execute(_COPY_SQL + " ORDER BY a.id"):
        if r["id"] in shadow:
            continue
        d = dict(r)
        d["credits"] = credits_of(d["signatures_fired"], d["china_sources_cited"], d["llm_trigger"], d["title"])
        d["sigs"] = [str(s) for s in (_json(d["signatures_fired"], []) or [])]
        out[d["id"]] = d
    # An uncredited copy (the lookalike route) carries the credit of the credited item it repeats.
    for d in out.values():
        if not d["credits"]:
            m = lookalike.context_of(d["llm_trigger"])
            if m and m.get("article_id") in out:
                d["credits"] = set(out[m["article_id"]]["credits"])
                d["credits_via"] = m["article_id"]
    return out


def _snapshots(conn) -> Dict[int, Dict]:
    out = {}
    for r in conn.execute("SELECT copy_id, details FROM state_copies"):
        d = _json(r["details"], {}) or {}
        if isinstance(d, dict) and isinstance(d.get("lookalike"), dict):
            out[r["copy_id"]] = d["lookalike"]
    return out


def _body(url_hash: Optional[str], cache: Dict) -> Optional[str]:
    if not url_hash:
        return None
    if url_hash not in cache:
        cache[url_hash] = store.load_body(url_hash)
    return cache[url_hash]


def _has_body(url_hash: Optional[str]) -> bool:
    return bool(url_hash) and store.body_path(url_hash).exists()


def ref_key(url_hash: Optional[str]) -> Optional[str]:
    """Where a reference item's body is stored: pipeline/reference.body_key, a key of its own ("r" and
    the url_hash), so it never shares a file with a monitored article at the same address. Monitored
    bodies stay under the bare url_hash."""
    if not url_hash:
        return None
    try:
        from pipeline import reference
        return reference.body_key(url_hash)
    except ImportError:  # pipeline/reference.py not present: the same rule
        return "r" + url_hash


class _References:
    """The reference items around the copies to pair, indexed by headline (per language), feed
    summary and body fingerprint bands, as pipeline/lookalike.py indexes state origin items."""

    def __init__(self, conn, lo: Optional[str], hi: Optional[str], sources: Dict[str, Dict]):
        self.rows: Dict[int, Dict] = {}
        self.titles = defaultdict(lambda: lookalike._Index(self._rank))
        self.summ = lookalike._Index(self._rank)
        self.bands = defaultdict(set)
        self.sigs: Dict[int, Tuple[int, ...]] = {}
        self.sources = sources
        self.languages_by_publisher: Dict[str, Set[str]] = defaultdict(set)
        self.first_seen: Dict[str, str] = {}
        for sid, s in sources.items():
            pub = source_publisher(sid, sources)
            if pub and s.get("language"):
                self.languages_by_publisher[pub].add(str(s["language"]))
        try:
            for r in conn.execute("SELECT source_id, language, MIN(discovered_at) FROM reference_items GROUP BY source_id, language"):
                pub = source_publisher(r[0], sources)
                if pub and r[1]:
                    self.languages_by_publisher[pub].add(r[1])
                if r[2] and (r[0] not in self.first_seen or r[2] < self.first_seen[r[0]]):
                    self.first_seen[r[0]] = r[2]
            if lo is None:
                return
            for r in conn.execute(
                    """SELECT id, source_id, url, url_hash, language, title, summary, published_at, discovered_at,
                              sig, body_pruned_at FROM reference_items
                       WHERE discovered_at >= ? AND discovered_at <= ? ORDER BY id""", (lo, hi)):
                self.add(dict(r))
        except sqlite3.OperationalError:  # a database from before the reference tables
            return

    def add(self, r: Dict) -> None:
        key = r["id"]
        r["publisher"] = source_publisher(r["source_id"], self.sources)
        r["credit_prefixes"] = (self.sources.get(r["source_id"]) or {}).get("credits") or []
        r["rank"] = lookalike.tie_rank(r)
        if not r.get("language"):
            r["language"] = (self.sources.get(r["source_id"]) or {}).get("language")
        self.rows[key] = r
        ts = lookalike.title_set(r.get("title"))
        if ts:
            self.titles[r.get("language")].add(key, ts)
        sh = lookalike.shingles(r.get("summary") or "")
        if len(sh) >= lookalike.SUMMARY_MIN_SHINGLES:
            self.summ.add(key, sh)
        if r.get("sig"):
            try:
                sig = body_dups._SIG.unpack(r["sig"])
            except Exception:  # noqa: BLE001  a malformed signature is skipped
                sig = None
            if sig:
                self.sigs[key] = sig
                for b in range(body_dups.LSH_BANDS):
                    self.bands[(b,) + tuple(sig[b * body_dups.LSH_ROWS:(b + 1) * body_dups.LSH_ROWS])].add(key)

    def _rank(self, key):
        return self.rows[key]["rank"]

    def consistent_languages(self, copy: Dict) -> Set[str]:
        """Languages of the reference sources whose publisher the copy's credit names."""
        out = set()
        for pub, langs in self.languages_by_publisher.items():
            if credit_consistent(copy["credits"], copy["sigs"], pub):
                out |= langs
        for sid, s in self.sources.items():
            if s.get("language") and credit_consistent(copy["credits"], copy["sigs"], None, s.get("credits") or []):
                out.add(str(s["language"]))
        return out

    def _allow(self, copy: Dict):
        def allow(key) -> bool:
            r = self.rows[key]
            if r.get("language") != copy.get("language"):
                return False
            if not credit_consistent(copy["credits"], copy["sigs"], r["publisher"], r["credit_prefixes"]):
                return False
            return _in_window(copy, r)
        return allow

    def candidates(self, copy: Dict, sig: Optional[Tuple[int, ...]]) -> List[Tuple[str, int, float]]:
        """(method, ref id, similarity) for every candidate, best first within each method."""
        allow = self._allow(copy)
        out: List[Tuple[str, int, float]] = []
        ts = lookalike.title_set(copy.get("title"))
        idx = self.titles.get(copy.get("language"))
        if idx is not None and len(ts) >= lookalike.TITLE_MIN_TOKENS:
            hits = []
            for k, inter in idx._overlaps(ts, allow).items():
                j = inter / float(len(ts) + idx.sizes[k] - inter)
                if j >= lookalike.TITLE_JACCARD:
                    hits.append((-j, self._rank(k), k, j))
            out += [("title", k, round(j, 4)) for _, _, k, j in sorted(hits)[:CANDIDATES_PER_INDEX]]
        sh = lookalike.shingles(copy.get("summary") or "")
        if len(sh) >= lookalike.SUMMARY_MIN_SHINGLES:
            hits = []
            for k, inter in self.summ._overlaps(sh, allow).items():
                c = inter / float(len(sh))
                if c >= lookalike.SUMMARY_CONTAINMENT:
                    hits.append((-c, self._rank(k), k, c))
            out += [("summary", k, round(c, 4)) for _, _, k, c in sorted(hits)[:CANDIDATES_PER_INDEX]]
        if sig and self.sigs:
            cands = set()
            for b in range(body_dups.LSH_BANDS):
                cands.update(self.bands.get((b,) + tuple(sig[b * body_dups.LSH_ROWS:(b + 1) * body_dups.LSH_ROWS]), ()))
            hits = []
            for k in cands:
                if not allow(k):
                    continue
                s = body_dups.similarity(sig, self.sigs[k])
                if s >= FINGERPRINT_CANDIDATE:
                    hits.append((-s, self._rank(k), k, s))
            out += [("body", k, round(s, 4)) for _, _, k, s in sorted(hits)[:CANDIDATES_PER_INDEX]]
        return out


def _group_keys(copies: Dict[int, Dict], groups: Dict[int, int]) -> Dict[int, int]:
    out = {}
    for aid, d in copies.items():
        g = d.get("dup_group_id") or aid
        out[aid] = groups.get(g, g)
    return out


def _credited(d: Dict) -> bool:
    return bool(d.get("credits")) or (d.get("route") in CREDITED_ROUTES)


_NETWORK_CACHE: Dict[str, Tuple[float, Dict[str, str]]] = {}


def network_map(ctx: Optional[Dict] = None) -> Dict[str, str]:
    """{outlet id: network} from the outlets the export passes (ctx["outlets"]), else from
    sources/outlets.yaml (read once per change of the file); outlets with no network are absent."""
    outlets = (ctx or {}).get("outlets")
    if outlets is None:
        path = config.OUTLETS_PATH
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        hit = _NETWORK_CACHE.get(str(path))
        if hit and hit[0] == mtime:
            return dict(hit[1])
        try:
            from pipeline import registry
            outlets = registry.load_outlets(path, validate=False)
        except Exception:  # noqa: BLE001  no registry, no networks
            return {}
        out = {o["id"]: str(o["network"]) for o in outlets if o.get("id") and o.get("network")}
        _NETWORK_CACHE[str(path)] = (mtime, out)
        return dict(out)
    return {o["id"]: str(o["network"]) for o in outlets if o.get("id") and o.get("network")}


def same_network(networks: Dict[str, str], a: Optional[str], b: Optional[str]) -> bool:
    """Whether two outlets belong to one recorded network."""
    na = networks.get(a or "")
    return bool(na) and na == networks.get(b or "")


# ---------------------------------------------------------------------------
# Pairing one copy
# ---------------------------------------------------------------------------

class _Run:
    """What one compute() pass shares across copies."""

    def __init__(self, conn, copies: Dict[int, Dict], todo: List[int], sources: Dict[str, Dict],
                 networks: Optional[Dict[str, str]] = None):
        self.conn = conn
        self.copies = copies
        self.networks = networks or {}
        self.snap = _snapshots(conn)
        # A copy whose llm_trigger lost its lookalike match keeps the credit of the item it repeats.
        for aid, d in copies.items():
            m = self.snap.get(aid)
            if not d["credits"] and m and m.get("article_id") in copies and not lookalike.context_of(d.get("llm_trigger")):
                d["credits"] = set(copies[m["article_id"]]["credits"])
                d["credits_via"] = m["article_id"]
        try:
            groups = body_dups.item_groups(conn)
        except sqlite3.OperationalError:
            groups = {}
        self.group_of = _group_keys(copies, groups)
        self.members = defaultdict(list)
        for aid, g in self.group_of.items():
            self.members[g].append(aid)
        times = [t for aid in todo for t in (lookalike._ts(copies[aid]["discovered_at"]), lookalike.published_ts(copies[aid]))
                 if t is not None]
        lo = hi = None
        if times:
            lo = lookalike._iso(min(times) - (WINDOW_AFTER_DAYS + 1) * 86400)
            hi = lookalike._iso(max(times) + (WINDOW_BEFORE_DAYS + lookalike.PUBLISHED_MAX_LAG_DAYS + 1) * 86400)
        self.refs = _References(conn, lo, hi, sources)
        self.cache: Dict[str, Optional[str]] = {}
        self.sig_cache: Dict[int, Optional[Tuple[int, ...]]] = {}

    def body(self, url_hash):
        return _body(url_hash, self.cache)

    def sig_of(self, copy: Dict) -> Optional[Tuple[int, ...]]:
        aid = copy["id"]
        if aid not in self.sig_cache:
            sig = lookalike.fingerprint_of(self.conn, aid)
            if sig is None:
                text = self.body(copy["url_hash"])
                if text:
                    _, packed = body_dups.fingerprint(text)
                    sig = body_dups._SIG.unpack(packed) if packed else None
            self.sig_cache[aid] = sig
        return self.sig_cache[aid]

    def lookalike_match(self, copy: Dict) -> Optional[Dict]:
        return lookalike.context_of(copy.get("llm_trigger")) or self.snap.get(copy["id"])

    def ref_body(self, r: Dict) -> Optional[str]:
        return None if r.get("body_pruned_at") else self.body(ref_key(r.get("url_hash")))

    def pair(self, copy: Dict) -> Dict:
        """The pairing fields of one copy's row."""
        details: Dict = {"credits": sorted(copy["credits"])}
        match = self.lookalike_match(copy)
        if match:
            details["lookalike"] = match
        if copy.get("credits_via"):
            details["credits_via"] = copy["credits_via"]
        reason = None
        cbody = self.body(copy["url_hash"])
        langs = self.refs.consistent_languages(copy)
        if not copy["credits"]:
            reason = "no_state_credit"
        elif not langs:
            reason = "no_reference_source"
        elif copy.get("language") not in langs:
            reason = "translation"
            details["translation"] = True
            details["reference_languages"] = sorted(langs)
        else:
            cands = self.refs.candidates(copy, self.sig_of(copy))
            best, rejected = None, 0
            seen = set()
            for method, key, sim in cands:
                if key in seen:
                    continue
                seen.add(key)
                r = self.refs.rows[key]
                al = None
                rbody = self.ref_body(r)
                if cbody and rbody:
                    al = alignment(cbody, rbody)
                    if al is not None and al < ALIGN_MIN:
                        rejected += 1
                        continue
                direct = 1 if (r["publisher"] and copy["credits"] & ({r["publisher"]} | PUBLISHER_ALSO.get(r["publisher"], set()))) else 0
                score = (0 if al is None else 1, direct, al or 0.0, {"title": 2, "summary": 1, "body": 0}[method], sim)
                if best is None or score > best[0] or (score == best[0] and r["rank"] < self.refs.rows[best[2]]["rank"]):
                    best = (score, method, key, sim, al)
            if best is not None:
                _, method, key, sim, al = best
                r = self.refs.rows[key]
                details["alignment"] = None if al is None else round(al, 4)
                details["candidates"] = len(seen)
                return dict(original_kind="reference", ref_id=key, original_article_id=None,
                            original_source=r["source_id"], match_method=method, match_similarity=sim,
                            credit_consistent=1, none_reason=None, orig=r, details=details)
            reason = "alignment_rejected" if rejected else "no_reference_match"
            if rejected:
                details["rejected_alignment"] = rejected
        mon = self.monitored(copy, cbody, details)
        if mon is not None:
            return mon
        return dict(original_kind="none", ref_id=None, original_article_id=None, original_source=None,
                    match_method=None, match_similarity=None, credit_consistent=None, none_reason=reason,
                    orig=None, details=details)

    def monitored(self, copy: Dict, cbody: Optional[str], details: Dict) -> Optional[Dict]:
        """The earliest credited monitored copy of the same item in the same language from another
        outlet outside this copy's network, earlier than this one; None when this copy is the first or
        stands alone. Earlier copies passed over because they ran at a sister outlet are counted in
        details["sister_only"] when nothing else is found."""
        aid = copy["id"]
        via = {}
        mine = copy.get("dup_group_id")
        for m in self.members.get(self.group_of.get(aid), []):
            via[m] = "dup_group" if mine and self.copies[m].get("dup_group_id") == mine else "item_group"
        match = self.lookalike_match(copy)
        if match and match.get("article_id") in self.copies:
            via.setdefault(match["article_id"], "lookalike_" + str(match.get("method") or "match"))
        via.pop(aid, None)
        me = (lookalike.tie_rank(copy), aid)
        cands = []
        sisters = 0
        for k, how in via.items():
            o = self.copies[k]
            if o.get("outlet_id") == copy.get("outlet_id") or o.get("language") != copy.get("language"):
                continue
            if not _credited(o):
                continue
            if copy["credits"] and o["credits"] and not (copy["credits"] & o["credits"]):
                continue
            rank = (lookalike.tie_rank(o), k)
            if rank >= me:
                continue
            if same_network(self.networks, o.get("outlet_id"), copy.get("outlet_id")):
                sisters += 1
                continue
            cands.append((rank, k, how))
        for _, k, how in sorted(cands):
            o = self.copies[k]
            al = None
            obody = self.body(o["url_hash"])
            if cbody and obody:
                al = alignment(cbody, obody)
                if al is not None and al < ALIGN_MIN:
                    details["rejected_alignment"] = details.get("rejected_alignment", 0) + 1
                    continue
            details["alignment"] = None if al is None else round(al, 4)
            cc = 1 if (copy["credits"] & o["credits"]) else None
            sim = match.get("similarity") if (match and match.get("article_id") == k) else None
            return dict(original_kind="monitored", ref_id=None, original_article_id=k, original_source=o["outlet_id"],
                        match_method=how, match_similarity=sim, credit_consistent=cc, none_reason=None, orig=o,
                        details=details)
        if sisters:
            details["sister_only"] = sisters
        return None


def _times(copy: Dict, orig: Optional[Dict]) -> Dict:
    cp = lookalike.published_ts(copy)
    op = lookalike.published_ts(orig) if orig else None
    return {"copy_published_at": _iso_or_none(cp), "original_published_at": _iso_or_none(op),
            "copy_ts_res": ts_resolution(copy), "orig_ts_res": ts_resolution(orig) if orig else None,
            "lag_seconds": int(round(cp - op)) if cp is not None and op is not None else None}


_ROW_COLS = ("version", "computed_at", "current", "original_kind", "ref_id", "original_article_id", "original_source",
             "match_method", "match_similarity", "credit_consistent", "none_reason", "copy_published_at",
             "original_published_at", "copy_ts_res", "orig_ts_res", "lag_seconds", "body_state", "headline_similarity",
             "kept_share", "novel_share", "rewritten_share", "added_paragraphs", "added_words", "cue_local",
             "cue_counterpoint", "band", "details")


def _write(conn, copy_id: int, fields: Dict) -> None:
    vals = [fields.get(c) for c in _ROW_COLS]
    conn.execute("INSERT OR REPLACE INTO state_copies(copy_id, %s) VALUES (?%s)" % (", ".join(_ROW_COLS), ",?" * len(_ROW_COLS)),
                 [copy_id] + vals)


def _body_state(copy_hash: Optional[str], orig_hash: Optional[str], kind: str, orig_pruned: bool = False) -> str:
    if kind == "none":
        return "no_original"
    c, o = _has_body(copy_hash), (not orig_pruned) and _has_body(orig_hash)
    return "both" if c and o else "copy_only" if c else "original_only" if o else "neither"


# ---------------------------------------------------------------------------
# The measure interface
# ---------------------------------------------------------------------------

def inputs_present(conn) -> int:
    """Current state origin labels: each is a copy to pair."""
    return conn.execute("SELECT COUNT(*) FROM classifications WHERE is_current=1 AND category='A'").fetchone()[0]


def _todo(conn, copies: Dict[int, Dict], now: Optional[dt.datetime] = None) -> List[int]:
    now = now or dt.datetime.now(dt.timezone.utc)
    recent = (now - dt.timedelta(days=REPAIR_DAYS)).replace(microsecond=0).isoformat()
    rows = {r["copy_id"]: dict(r) for r in conn.execute(
        """SELECT s.copy_id, s.version, s.body_state, s.original_kind, s.ref_id, s.original_article_id, r.url_hash AS ref_hash,
                  r.body_pruned_at
           FROM state_copies s LEFT JOIN reference_items r ON r.id = s.ref_id""")}
    new, recent_ids, stale = [], [], []
    for aid, d in copies.items():
        row = rows.get(aid)
        if row is None:
            new.append(aid)
        elif row["version"] != VERSION or row["body_state"] is None or (d["discovered_at"] or "") >= recent:
            recent_ids.append(aid)
        elif row["original_kind"] != "none":
            oh = ref_key(row["ref_hash"]) if row["original_kind"] == "reference" else (
                copies.get(row["original_article_id"], {}).get("url_hash"))
            if _body_state(d["url_hash"], oh, row["original_kind"], bool(row["body_pruned_at"])) != row["body_state"]:
                stale.append(aid)
    key = lambda a: (copies[a]["discovered_at"] or "", a)  # noqa: E731
    return sorted(new, key=key, reverse=True) + sorted(recent_ids, key=key, reverse=True) + sorted(stale, key=key)


def _mark_current(conn, ids: Iterable[int]) -> Tuple[int, int]:
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _copies_now(id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM _copies_now")
    conn.executemany("INSERT OR IGNORE INTO _copies_now(id) VALUES (?)", [(i,) for i in ids])
    gone = conn.execute("UPDATE state_copies SET current=0 WHERE current=1 AND copy_id NOT IN (SELECT id FROM _copies_now)").rowcount
    back = conn.execute("UPDATE state_copies SET current=1 WHERE current=0 AND copy_id IN (SELECT id FROM _copies_now)").rowcount
    conn.execute("DROP TABLE _copies_now")
    return gone, back


def compute(conn, ctx: Optional[Dict] = None, deadline: Optional[float] = None, now: Optional[dt.datetime] = None,
            sources: Optional[Dict[str, Dict]] = None) -> Dict:
    """Pair every current state origin copy that needs it, then score each paired copy whose bodies
    exist. Resumable: stops at the deadline (time.monotonic()) and carries on at the next export."""
    deadline = deadline if deadline is not None else time.monotonic() + 300
    sources = load_reference_sources() if sources is None else sources
    copies = _current_copies(conn)
    gone, back = _mark_current(conn, copies)
    todo = _todo(conn, copies, now)
    out = {"copies": len(copies), "todo": len(todo), "paired": 0, "reference": 0, "monitored": 0, "none": 0,
           "scored": 0, "finished": 0, "withdrawn": gone, "restored": back, "stopped_deadline": False}
    if not todo:
        conn.commit()
        out["remaining"] = 0
        return out
    run = _Run(conn, copies, todo, sources, network_map(ctx))
    stamp = store.utcnow()
    done: List[Tuple[int, Dict]] = []
    for i, aid in enumerate(todo):
        if time.monotonic() >= deadline:
            out["stopped_deadline"] = True
            break
        copy = copies[aid]
        p = run.pair(copy)
        orig = p.pop("orig")
        fields = dict(p, version=VERSION, computed_at=stamp, current=1, body_state=None)
        fields.update(_times(copy, orig))
        fields["details"] = json.dumps(p["details"], ensure_ascii=False, sort_keys=True)
        _write(conn, aid, fields)
        done.append((aid, dict(fields, orig=orig, details=p["details"])))
        out["paired"] += 1
        out[p["original_kind"]] += 1
        if (i + 1) % COMMIT_EVERY == 0:
            conn.commit()
    conn.commit()
    # Metrics, outlet by outlet so each outlet's template lines are read once.
    templates: Dict[str, Set[str]] = {}
    for aid, f in done:
        if time.monotonic() >= deadline:
            out["stopped_deadline"] = True
            break
        copy, orig = copies[aid], f["orig"]
        upd = {k: None for k in ("headline_similarity", "kept_share", "novel_share", "rewritten_share", "added_paragraphs",
                                 "added_words", "cue_local", "cue_counterpoint", "band")}
        details = f["details"]
        if f["original_kind"] == "none":
            state = "no_original"
        else:
            ohash = ref_key(orig.get("url_hash")) if f["original_kind"] == "reference" else orig.get("url_hash")
            pruned = bool(orig.get("body_pruned_at"))
            state = _body_state(copy["url_hash"], ohash, f["original_kind"], pruned)
            upd["headline_similarity"] = headline_similarity(copy.get("title"), orig.get("title"))
            if state == "both":
                okey = "ref:" + orig["source_id"] if f["original_kind"] == "reference" else orig["outlet_id"]
                ct = templates.get(copy["outlet_id"])
                if ct is None:
                    ct = templates[copy["outlet_id"]] = _outlet_templates(run, copy["outlet_id"])
                ot = templates.get(okey)
                if ot is None:
                    ot = templates[okey] = (_source_templates(run, orig["source_id"]) if f["original_kind"] == "reference"
                                            else _outlet_templates(run, orig["outlet_id"]))
                m = metrics(run.body(copy["url_hash"]), run.body(ohash), copy.get("title"), orig.get("title"),
                            copy.get("language"), _country_of(copy, ctx), ct, ot)
                if m is None:
                    state = "too_short"
                else:
                    for k in upd:
                        upd[k] = m[k]
                    details = dict(details, copy_words=m["copy_words"], orig_words=m["orig_words"],
                                   aligned_share=m["aligned_share"])
                    out["scored"] += 1
        upd["body_state"] = state
        upd["details"] = json.dumps(details, ensure_ascii=False, sort_keys=True)
        conn.execute("UPDATE state_copies SET %s WHERE copy_id=?" % ", ".join("%s=?" % k for k in upd),
                     list(upd.values()) + [aid])
        out["finished"] += 1
        if out["finished"] % COMMIT_EVERY == 0:
            conn.commit()       # as the pairing above: the write lock is not held for the whole metrics pass
    conn.commit()
    out["remaining"] = len(todo) - out["finished"]
    return out


def _country_of(copy: Dict, ctx: Optional[Dict]) -> Optional[str]:
    return copy.get("country")


def _outlet_templates(run: _Run, outlet_id: str) -> Set[str]:
    """Lines in at least TEMPLATE_LINE_SHARE of the outlet's paired copy bodies (state_copies rows
    with an original), plus its other state origin bodies when it has too few paired ones."""
    ids = [r[0] for r in run.conn.execute(
        """SELECT s.copy_id FROM state_copies s JOIN articles a ON a.id = s.copy_id
           WHERE a.outlet_id = ? AND s.current = 1 AND s.original_kind <> 'none' ORDER BY s.copy_id DESC LIMIT 200""",
        (outlet_id,))]
    picked = [run.copies[i] for i in ids if i in run.copies]
    if len([d for d in picked if _has_body(d["url_hash"])]) < TEMPLATE_MIN_BODIES:
        have = {d["id"] for d in picked}
        picked += [d for d in run.copies.values() if d["outlet_id"] == outlet_id and d["id"] not in have][:200]
    return template_lines_of((_story(d.get("title")), run.body(d["url_hash"])) for d in picked if _has_body(d["url_hash"]))


def _story(title: Optional[str]) -> str:
    return " ".join(sorted(lookalike.title_set(title or ""))) or (title or "")


def _source_templates(run: _Run, source_id: str) -> Set[str]:
    try:
        hashes = [(r[0], r[1]) for r in run.conn.execute(
            "SELECT url_hash, title FROM reference_items WHERE source_id=? AND body_pruned_at IS NULL ORDER BY id DESC LIMIT 200",
            (source_id,))]
    except sqlite3.OperationalError:
        return set()
    return template_lines_of((_story(t), run.body(ref_key(h))) for h, t in hashes if _has_body(ref_key(h)))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

# The route is read through model_only.route_sql (filled in by _rows), so a copy of an item that is not settled
# state origin is seen as undetermined.
_ROWS_SQL = """SELECT s.*, a.outlet_id, a.country, a.language, a.title, a.url, a.dup_group_id, %s AS route, c.category AS c_category, c.method AS c_method
               FROM state_copies s JOIN articles a ON a.id = s.copy_id
               LEFT JOIN classifications c ON c.article_id = s.copy_id AND c.is_current = 1 AND c.category = 'A'
               WHERE s.current = 1"""


def _median(xs: List[float]) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return round(xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0, 4)


def _outlet_map(ctx: Optional[Dict]) -> Dict[str, Dict]:
    from pipeline import registry
    out = {}
    for o in (ctx or {}).get("outlets") or []:
        out[o["id"]] = {"name": o.get("name") or o["id"], "country": registry.export_country(o)}
    return out


def orig_key(r: Dict) -> Optional[str]:
    """The original a row was paired with: "r<id>" for a reference item, "a<id>" for a monitored
    article, None for no original."""
    if r.get("original_kind") == "reference" and r.get("ref_id") is not None:
        return "r%s" % r["ref_id"]
    if r.get("original_kind") == "monitored" and r.get("original_article_id") is not None:
        return "a%s" % r["original_article_id"]
    return None


def attach_items(conn, rows: List[Dict]) -> List[Dict]:
    """Set orig_key and item_key on each row: the item is the copy's underlying item group
    (body_dups.item_groups over dup_group_id), joined with its original and the original's group, so
    copies of one state text count as one item."""
    try:
        groups = body_dups.item_groups(conn)
    except sqlite3.OperationalError:
        groups = {}
    oids = sorted({r["original_article_id"] for r in rows if r.get("original_article_id")})
    odup: Dict[int, int] = {}
    for i in range(0, len(oids), 900):
        part = oids[i:i + 900]
        for r in conn.execute("SELECT id, dup_group_id FROM articles WHERE id IN (%s)" % ",".join("?" * len(part)), part):
            odup[r[0]] = r[1] or r[0]
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    gkeys = []
    for r in rows:
        g = r.get("dup_group_id") or r["copy_id"]
        gk = "g%s" % groups.get(g, g)
        find(gk)
        r["orig_key"] = orig_key(r)
        if r["orig_key"]:
            union(gk, r["orig_key"])
        oid = r.get("original_article_id")
        if oid:
            og = odup.get(oid, oid)
            union(gk, "g%s" % groups.get(og, og))
        gkeys.append(gk)
    for r, gk in zip(rows, gkeys):
        r["item_key"] = find(gk)
    return rows


def item_summary(scored: List[Dict]) -> Dict:
    """Band shares with each underlying item counted once (its scored copies share its weight), the
    copy counts, and the distinct originals and items: {scored, originals, items, bands (copies),
    item_bands (weights), item_shares, median_novel_items, median_kept_items (medians over items of
    each item's median)}."""
    by = defaultdict(list)
    for r in scored:
        by[r.get("item_key") or ("c%s" % r.get("copy_id"))].append(r)
    w = {b: 0.0 for b in BANDS}
    novel, kept = [], []
    for rs in by.values():
        for r in rs:
            w[r["band"]] += 1.0 / len(rs)
        novel.append(_median([r["novel_share"] for r in rs]))
        kept.append(_median([r["kept_share"] for r in rs]))
    n = len(by)
    return {"scored": len(scored), "originals": len({r.get("orig_key") for r in scored if r.get("orig_key")}),
            "items": n, "bands": {b: sum(1 for r in scored if r["band"] == b) for b in BANDS},
            "item_bands": {b: round(v, 2) for b, v in w.items()},
            "item_shares": {b: (round(v / float(n), 4) if n else None) for b, v in w.items()},
            "median_novel_items": _median(novel), "median_kept_items": _median(kept)}


def _group(rows: List[Dict], key) -> List[Dict]:
    acc = defaultdict(list)
    for r in rows:
        k = key(r)
        if k is not None:
            acc[k].append(r)
    out = []
    for k, rs in acc.items():
        scored = [r for r in rs if r["band"]]
        bands = {b: sum(1 for r in scored if r["band"] == b) for b in BANDS}
        its = item_summary(scored)
        enough = its["originals"] >= OUTLET_MIN_N
        out.append({"id": k, "n": len(rs), "scored": len(scored), "originals": its["originals"], "items": its["items"],
                    "bands": bands,
                    "shares": {b: (round(v / float(len(scored)), 4) if scored else None) for b, v in bands.items()},
                    "item_shares": its["item_shares"],
                    "median_novel": _median([r["novel_share"] for r in scored]) if enough else None,
                    "median_kept": _median([r["kept_share"] for r in scored]) if enough else None})
    return sorted(out, key=lambda g: (-g["scored"], -g["n"], str(g["id"])))


def _rows(conn, ctx) -> List[Dict]:
    from pipeline import model_only
    om = _outlet_map(ctx)
    out = []
    for r in conn.execute(_ROWS_SQL % model_only.route_sql("c")):
        d = dict(r)
        # A copy whose state origin label is undetermined (pipeline/model_only.py) is in no count, so it is
        # never measured as a state copy.
        cat, method = d.pop("c_category", None), d.pop("c_method", None)
        if model_only.is_undetermined(cat, d.get("route"), method):
            continue
        o = om.get(d["outlet_id"])
        d["export_country"] = o["country"] if o else d["country"]
        d["outlet_name"] = o["name"] if o else d["outlet_id"]
        out.append(d)
    return attach_items(conn, out)


def recall(conn, rows: List[Dict], sources: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    """Reference recall per reference source: of the copies credited to its publisher, in its
    language and first seen after the source began, the share paired with a reference item."""
    sources = load_reference_sources() if sources is None else sources
    try:
        srcs = [dict(r) for r in conn.execute(
            "SELECT source_id, language, MIN(discovered_at) AS first_seen, COUNT(*) AS items FROM reference_items GROUP BY source_id, language")]
    except sqlite3.OperationalError:
        return []
    out = []
    for s in srcs:
        pub = source_publisher(s["source_id"], sources)
        prefixes = (sources.get(s["source_id"]) or {}).get("credits") or []
        start = s["first_seen"]
        elig = paired = to_this = 0
        for r in rows:
            det = _json(r["details"], {}) or {}
            creds = set(det.get("credits") or [])
            if r["language"] != s["language"] or (r.get("copy_published_at") or "") < (start or ""):
                continue
            if not credit_consistent(creds, [], pub, ()) and not (prefixes and r["original_source"] == s["source_id"]):
                continue
            elig += 1
            if r["original_kind"] == "reference":
                paired += 1
                if r["original_source"] == s["source_id"]:
                    to_this += 1
        out.append({"source": s["source_id"], "publisher": pub, "language": s["language"], "items": s["items"],
                    "since": (start or "")[:10] or None, "eligible": elig, "paired": paired, "paired_to_source": to_this,
                    "recall": round(paired / float(elig), 4) if elig else None})
    return sorted(out, key=lambda x: x["source"])


def build_export(conn, ctx: Optional[Dict] = None) -> Dict:
    """docs/data/alteration.json: band shares by route, country (wires under WLD), outlet (at least
    OUTLET_MIN_N scored copies), reference source and language, with pair coverage and the
    reference recall diagnostic. Headlines, URLs and body text are never in it."""
    rows = _rows(conn, ctx)
    scored = [r for r in rows if r["band"]]
    kinds = Counter(r["original_kind"] for r in rows)
    reasons = Counter(r["none_reason"] for r in rows if r["original_kind"] == "none")
    translations = sum(1 for r in rows if (_json(r["details"], {}) or {}).get("translation"))
    om = _outlet_map(ctx)
    outlets = []
    for g in _group(rows, lambda r: r["outlet_id"]):
        if g["originals"] < OUTLET_MIN_N:
            continue
        sample = next(r for r in rows if r["outlet_id"] == g["id"])
        g.update({"name": (om.get(g["id"]) or {}).get("name") or g["id"], "country": sample["export_country"]})
        outlets.append(g)
    outlets.sort(key=lambda g: (-(g["median_novel"] or 0), -g["scored"], g["id"]))
    hs = [r["headline_similarity"] for r in rows if r["original_kind"] != "none" and r["headline_similarity"] is not None]
    route_labels = {}
    try:
        from pipeline.export import ROUTE_LABELS
        route_labels = dict(ROUTE_LABELS)
    except Exception:  # noqa: BLE001
        pass
    by_route = _group(rows, lambda r: r["route"] or "unattributed")
    for g in by_route:
        g["label"] = route_labels.get(g["id"], g["id"])
    its = item_summary(scored)
    against = {k: item_summary([r for r in scored if r["original_kind"] == k]) for k in ("reference", "monitored")}
    paired = [r for r in rows if r["original_kind"] != "none"]
    return {
        "generated_at": store.utcnow(), "version": VERSION, "bands": list(BANDS),
        "band_limits": {k: v for k, v in BAND_LIMITS}, "outlet_min_n": OUTLET_MIN_N,
        "totals": {"copies": len(rows), "paired": len(rows) - kinds.get("none", 0), "scored": len(scored),
                   "originals": its["originals"], "items": its["items"], "item_bands": its["item_bands"],
                   "item_shares": its["item_shares"], "median_novel_items": its["median_novel_items"],
                   "median_kept_items": its["median_kept_items"],
                   "scored_reference": against["reference"]["scored"], "scored_monitored": against["monitored"]["scored"],
                   "against": against,
                   "bands": {b: sum(1 for r in scored if r["band"] == b) for b in BANDS},
                   "median_novel": _median([r["novel_share"] for r in scored]),
                   "median_kept": _median([r["kept_share"] for r in scored]),
                   "cut_half_or_more": sum(1 for r in scored if r["kept_share"] is not None and r["kept_share"] < 0.5),
                   "headline_compared": len(hs),
                   "headline_rewritten": sum(1 for h in hs if h < HEADLINE_REWRITTEN),
                   "cue_local": sum(1 for r in scored if (r["cue_local"] or 0) > 0),
                   "cue_counterpoint": sum(1 for r in scored if (r["cue_counterpoint"] or 0) > 0)},
        "coverage": {"copies": len(rows), "reference": kinds.get("reference", 0), "monitored": kinds.get("monitored", 0),
                     "none": kinds.get("none", 0), "none_by_reason": dict(sorted(reasons.items(), key=lambda kv: str(kv[0]))),
                     "translation": translations,
                     "sister_only": sum(1 for r in rows if r["original_kind"] == "none"
                                        and (_json(r["details"], {}) or {}).get("sister_only")),
                     "items": len({r["item_key"] for r in rows}), "paired_items": len({r["item_key"] for r in paired}),
                     "originals": len({r["orig_key"] for r in paired if r.get("orig_key")}),
                     "body_states": dict(sorted(Counter(r["body_state"] or "pending" for r in rows).items())),
                     "scored": len(scored)},
        "reference_sources": reference_sources(conn),
        "by_route": by_route,
        "by_country": _group(rows, lambda r: r["export_country"]),
        "by_outlet": outlets,
        "by_source": _group([r for r in rows if r["original_kind"] == "reference"], lambda r: r["original_source"]),
        "by_language": _group(rows, lambda r: r["language"]),
        "by_kind": _group(rows, lambda r: r["original_kind"]),
        "recall": recall(conn, rows),
        "review_file": write_review(conn, ctx, rows),
    }


REVIEW_FIELDS = ["copy_id", "outlet_id", "country", "language", "route", "headline", "url", "original_kind",
                 "original_source", "original_headline", "original_url", "match_method", "headline_similarity",
                 "kept_share", "novel_share", "rewritten_share", "added_paragraphs", "added_words", "cue_local",
                 "cue_counterpoint"]


def review_path(ctx: Optional[Dict]) -> Path:
    """data/export/alteration_review.csv for the site's own export; beside an export made elsewhere
    (a test, a preview) in its sibling audit directory, never in the repository."""
    ed = Path((ctx or {}).get("export_dir") or config.EXPORT_DIR)
    if ed.resolve() == config.EXPORT_DIR.resolve():
        return config.EXPORT_DIR_AUDIT / "alteration_review.csv"
    return ed.parent / "audit" / "alteration_review.csv"


def _originals(conn, rows: List[Dict]) -> Dict[Tuple[str, int], Dict]:
    out = {}
    refs = [r["ref_id"] for r in rows if r["ref_id"]]
    arts = [r["original_article_id"] for r in rows if r["original_article_id"]]
    for i in range(0, len(refs), 900):
        part = refs[i:i + 900]
        for r in conn.execute("SELECT id, title, url FROM reference_items WHERE id IN (%s)" % ",".join("?" * len(part)), part):
            out[("r", r["id"])] = dict(r)
    for i in range(0, len(arts), 900):
        part = arts[i:i + 900]
        for r in conn.execute("SELECT id, title, url FROM articles WHERE id IN (%s)" % ",".join("?" * len(part)), part):
            out[("a", r["id"])] = dict(r)
    return out


def write_review(conn, ctx: Optional[Dict], rows: Optional[List[Dict]] = None) -> Optional[str]:
    """Every substantial copy, with headlines, URLs and metrics only, for the owner's review."""
    rows = rows if rows is not None else _rows(conn, ctx)
    subs = sorted([r for r in rows if r["band"] == "substantial"], key=lambda r: (-(r["novel_share"] or 0), r["copy_id"]))
    origs = _originals(conn, subs)
    path = review_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in subs:
            o = origs.get(("r", r["ref_id"])) if r["ref_id"] else origs.get(("a", r["original_article_id"])) or {}
            o = o or {}
            w.writerow(dict(r, country=r["export_country"], headline=r["title"], original_headline=o.get("title"),
                            original_url=o.get("url")))
    return path.name


def reference_sources(conn) -> List[Dict]:
    """Each reference source's status (pipeline/reference.py source_status): items by status (fetched,
    expired, failed, paywalled, discovered, blocked by robots.txt), bodies kept, the date only shares
    and flags. Says why reference pairs are few. [] when unavailable."""
    try:
        from pipeline import reference
        return reference.source_status(conn)
    except Exception:  # noqa: BLE001  a report, never a stop
        return []


def meta_summary(conn, ctx: Optional[Dict] = None) -> Dict:
    r = conn.execute(
        """SELECT COUNT(*) n, SUM(original_kind='reference') ref, SUM(original_kind='monitored') mon,
                  SUM(band IS NOT NULL) scored, SUM(band IS NOT NULL AND original_kind='reference') sref,
                  SUM(band IS NOT NULL AND original_kind='monitored') smon FROM state_copies WHERE current=1""").fetchone()
    bands = {b: n for b, n in conn.execute(
        "SELECT band, COUNT(*) FROM state_copies WHERE current=1 AND band IS NOT NULL GROUP BY band")}
    scored = [x for x in _rows(conn, ctx) if x["band"]]
    its = item_summary(scored)
    srcs = {d["source"]: {"items": d["items"], "fetched": d["statuses"].get("fetched", 0),
                          "expired": d["statuses"].get("expired", 0), "failed": d["statuses"].get("failed", 0),
                          "paywalled": d["statuses"].get("paywalled", 0), "share_date_only": d["share_date_only"],
                          "share_date_only_after_day_one": d["share_date_only_after_day_one"], "flags": d["flags"]}
            for d in reference_sources(conn)}
    return {"copies": r["n"] or 0, "reference": r["ref"] or 0, "monitored": r["mon"] or 0, "scored": r["scored"] or 0,
            "scored_reference": r["sref"] or 0, "scored_monitored": r["smon"] or 0,
            "originals": its["originals"], "items": its["items"],
            "bands": {b: bands.get(b, 0) for b in BANDS}, "reference_sources": srcs}


def methodology_text(block: Optional[Dict] = None) -> str:
    """Plain paragraphs for METHODOLOGY.md and the site: what the score tests, how pairs are found,
    which kind of original each figure rests on, what the bands mean and where they stop."""
    s = (block or {}).get("summary") or {}
    cov = ""
    if s.get("copies"):
        cov = (" At the last export %d of %d state origin copies had an original (%d from a reference source, %d an "
               "earlier monitored copy), and %d had both texts available to score: %d against a reference original "
               "and %d against an earlier monitored copy, from %d distinct originals and %d distinct underlying items."
               % (s.get("reference", 0) + s.get("monitored", 0), s["copies"], s.get("reference", 0), s.get("monitored", 0),
                  s.get("scored", 0), s.get("scored_reference", 0), s.get("scored_monitored", 0), s.get("originals", 0),
                  s.get("items", 0)))
        if not s.get("scored_reference"):
            cov += (" No copy has yet been scored against the state outlet's own text, so every score so far measures "
                    "change against an earlier monitored copy, not against the state original.")
    return (
        "The alteration score tests the second half of the state origin definition, that the text is "
        "reproduced without substantive editorial alteration. It sets each state origin copy beside the earliest "
        "version of the same text that can be found: the state outlet's own page (a reference original) where one "
        "is found, otherwise an earlier copy of the same item at another monitored outlet. Only a reference "
        "original is the state text itself; against an earlier monitored copy the score says how far one outlet's "
        "copy differs from another's, and every figure says which kind of original it rests on. It is report "
        "only: it never changes a label, a count or a denominator, and a copy with a high score keeps its label."
        + cov + "\n\n"
        "An original is looked for first among the reference sources, the English and other editions of Chinese state "
        "outlets read only as originals. A reference item qualifies when the copy's credit names its publisher, it is in "
        "the copy's language, it appeared from eleven days before the copy to one day after it, and its headline, feed "
        "summary or body fingerprint nearly repeats the copy's; when both texts are available at least 30 percent of the "
        "copy's words must line up with it. Failing that, the original is the earliest credited copy of the same item at "
        "another monitored outlet, in the same language, outside the copy's own media group: sister papers of one group "
        "(the ACM regional mastheads, the Postmedia papers, the Nine or News Corp mastheads) share one copy pool, so a "
        "sister's earlier copy is the same placement, not an original. Translations are never matched across languages, "
        "so an Italian copy of Xinhua English has no reference original and is counted as a translation. Copies first "
        "seen in the last 14 days are paired again at every export.\n\n"
        "Both texts are compared after credit lines, datelines and lines an outlet repeats on many of its pages are "
        "removed. The score is the share of the copy's words that are not found in the original, in runs of three words "
        "or more. Cuts are reported separately as the share of the original kept, and headline changes as a headline "
        "similarity. The provisional bands are verbatim (under 3 percent new words), light (under 15 percent), moderate "
        "(under 40 percent) and substantial (40 percent or more). Many copies repeat one original, so the shares in the "
        "lead count each underlying item once, with the number of copies beside them, and an outlet is ranked, or a "
        "median given, only from %d distinct originals.\n\n"
        "The bands measure wording, not meaning. A copy can keep every word and still change the sense by cutting a "
        "paragraph, and a copy can score as substantial because an outlet added its own background or a rebuttal. Two "
        "keyword cues, one for local context and one for counterpoint, flag added text for a reader to check; they are "
        "weak and never a finding on their own. Bodies are stored only where the tracker runs, so only copies whose two "
        "texts are both available are scored, and the coverage line says how many that is; each reference source's "
        "counts of pages fetched, expired, failed and paywalled are given beside it. Every copy in the "
        "substantial band is listed for review by hand, and a blind hand check of 20 pairs per band tests both the "
        "pairs and the bands." % OUTLET_MIN_N)


# ---------------------------------------------------------------------------
# Hand check
# ---------------------------------------------------------------------------

SAMPLE_FIELDS = ["sample_id", "copy_outlet", "copy_language", "copy_headline", "copy_url", "original_source",
                 "original_headline", "original_url", "same_text", "coder_band", "notes"]


def draw_sample(conn, n: Optional[int] = None, seed=None, out_dir: Optional[Path] = None):
    """A blind sample of scored pairs, up to n // 4 per band (PER_BAND when n is not given): the CSV
    shows the two headlines and links, never the machine's band or score. Coders fill same_text
    (yes or no: the original is the text the copy reproduces) and coder_band. See
    data/reviews/ALTERATION_GUIDE.md."""
    from pipeline import measures
    per = PER_BAND if not n else max(1, int(n) // len(BANDS))
    rng = random.Random(str(seed))
    rows = [dict(r) for r in conn.execute(
        """SELECT s.copy_id, s.band, s.novel_share, s.kept_share, s.original_kind, s.ref_id, s.original_article_id,
                  s.original_source, a.outlet_id, a.language, a.title, a.url
           FROM state_copies s JOIN articles a ON a.id = s.copy_id
           WHERE s.current = 1 AND s.band IS NOT NULL ORDER BY s.copy_id""")]
    picked = []
    for b in BANDS:
        rs = [r for r in rows if r["band"] == b]
        rng.shuffle(rs)
        picked += rs[:per]
    rng.shuffle(picked)
    origs = _originals(conn, picked)
    out_rows, key = [], {}
    for i, r in enumerate(picked, 1):
        sid = "P%03d" % i
        o = (origs.get(("r", r["ref_id"])) if r["ref_id"] else origs.get(("a", r["original_article_id"]))) or {}
        out_rows.append({"sample_id": sid, "copy_outlet": r["outlet_id"], "copy_language": r["language"],
                         "copy_headline": r["title"], "copy_url": r["url"], "original_source": r["original_source"],
                         "original_headline": o.get("title"), "original_url": o.get("url"),
                         "same_text": "", "coder_band": "", "notes": ""})
        key[sid] = {"copy_id": r["copy_id"], "band": r["band"], "novel_share": r["novel_share"],
                    "kept_share": r["kept_share"], "original_kind": r["original_kind"]}
    return measures.write_sample(Path(out_dir or measures.REVIEWS_DIR), MEASURE_ID, out_rows, SAMPLE_FIELDS,
                                 {"pairs": key, "per_band": per, "bands": list(BANDS)}, version=VERSION, seed=seed)


_YES = {"yes", "y", "1", "true", "same", "si", "sì", "oui"}
_NO = {"no", "n", "0", "false", "different", "non"}


def quadratic_weighted_kappa(a: List[int], b: List[int], k: int = len(BANDS)) -> Optional[float]:
    """Cohen's kappa with quadratic weights for two ratings on 0..k-1, or None with no pairs."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        obs[x][y] += 1
    ra = [sum(obs[i]) for i in range(k)]
    rb = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = (i - j) ** 2 / float((k - 1) ** 2)
            num += w * obs[i][j]
            den += w * ra[i] * rb[j] / n
    if den == 0:
        return 1.0 if num == 0 else 0.0
    return round(1.0 - num / den, 4)


def score_check(rows: List[Dict], key: Dict) -> Dict:
    """Pair precision (same_text yes among coded pairs) at least PAIR_PRECISION_MIN, and quadratic
    weighted kappa between the machine band and the coder's band on the true pairs at least
    KAPPA_MIN. The statistic shown is the one that fails, or kappa when both pass."""
    from pipeline import measures
    pairs = (key or {}).get("pairs") or {}
    n = len(pairs) or len(rows)
    coded = yes = 0
    mach, hum = [], []
    for r in rows:
        sid = (r.get("sample_id") or "").strip()
        k = pairs.get(sid)
        if k is None:
            continue
        st = (r.get("same_text") or "").strip().lower()
        cb = (r.get("coder_band") or "").strip().lower()
        if st in _YES:
            if cb not in BANDS:
                continue  # a true pair is coded once its band is too
            coded += 1
            yes += 1
            mach.append(BANDS.index(k["band"]))
            hum.append(BANDS.index(cb))
        elif st in _NO:
            coded += 1
    precision = round(yes / float(coded), 4) if coded else None
    kappa = quadratic_weighted_kappa(mach, hum)
    complete = n > 0 and coded >= n
    passed = None
    if complete and precision is not None:
        passed = precision >= PAIR_PRECISION_MIN and kappa is not None and kappa >= KAPPA_MIN
    if precision is not None and precision < PAIR_PRECISION_MIN:
        stat, value, thr = "pair precision", precision, PAIR_PRECISION_MIN
    else:
        stat, value, thr = "quadratic weighted kappa", kappa, KAPPA_MIN
    return measures.check_result(stat, value, thr, n, coded, passed=passed, complete=complete,
                                 pair_precision=precision, kappa=kappa, true_pairs=yes)
