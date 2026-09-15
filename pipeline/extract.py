"""Boilerplate removal and paywall detection.

Paywall rule: a page that declares isAccessibleForFree false in schema.org
metadata is paywalled, full stop. A page with an interstitial marker and a
short extracted body is paywalled. Nothing else is attempted. No archive
mirrors, no reader proxies, no bypass services.
"""
import json
import re
from html import unescape
from typing import Dict, Optional, Tuple

import trafilatura

HARD_PAYWALL_JSONLD = re.compile(r'"isAccessibleForFree"\s*:\s*("?)(false|False|no)\1', re.IGNORECASE)

INTERSTITIAL_MARKERS = [
    # class or id fragments used by common paywall vendors
    r'class="[^"]*\b(paywall|tp-modal|piano-|meter-?wall|subscription-?wall|regwall|reg-wall)',
    r'id="[^"]*\b(paywall|piano-|tp-container|subscription-?wall)',
    # multilingual prompts
    r"subscribe to (continue|keep) reading",
    r"to continue reading, (please )?(subscribe|log in)",
    r"this article is (for|available to) subscribers",
    r"subscriber[- ]only",
    r"abbonati per (continuare|leggere)",
    r"contenuto riservato agli abbonati",
    r"riservato agli abbonati",
    r"réservé aux abonnés",
    r"article réservé",
    r"pour lire la suite, abonnez",
    r"contenido exclusivo para suscriptores",
    r"suscríbete para (seguir|continuar) leyendo",
    r"conteúdo exclusivo para assinantes",
    r"nur für abonnenten",
    r"artikel für abonnenten",
    r"alleen voor abonnees",
]
_interstitial_re = re.compile("|".join("(?:%s)" % m for m in INTERSTITIAL_MARKERS), re.IGNORECASE)

SHORT_BODY_CHARS = 600


def detect_paywall(html: str, body_text: Optional[str]) -> Tuple[bool, Optional[str]]:
    if not html:
        return False, None
    if HARD_PAYWALL_JSONLD.search(html):
        return True, "schema.org isAccessibleForFree false"
    m = _interstitial_re.search(html)
    if m and (not body_text or len(body_text) < SHORT_BODY_CHARS):
        return True, "interstitial marker with short body: %s" % m.group(0)[:60]
    return False, None


def extract(html: str, url: Optional[str] = None) -> Dict:
    """Return dict with text, title, author, date, sitename, description. Text may be None."""
    out = {"text": None, "title": None, "author": None, "date": None, "sitename": None, "description": None}
    if not html:
        return out
    try:
        doc = trafilatura.bare_extraction(
            html, url=url, include_comments=False, include_tables=False,
            favor_recall=True, with_metadata=True,
        )
    except Exception:
        doc = None
    if doc is None:
        return out
    if isinstance(doc, dict):
        get = doc.get
    else:
        get = lambda k, d=None: getattr(doc, k, d)  # noqa: E731
    out["text"] = get("text") or None
    out["title"] = get("title") or None
    out["author"] = clean_author(get("author"))
    out["date"] = get("date") or None
    out["sitename"] = get("sitename") or None
    out["description"] = get("description") or None
    return out


def jsonld_authors(html: str) -> Optional[str]:
    """Best effort author from JSON-LD when trafilatura finds none."""
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html or "", re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict):
                continue
            a = it.get("author")
            if isinstance(a, dict) and a.get("name"):
                return a["name"]
            if isinstance(a, list):
                names = [x.get("name") for x in a if isinstance(x, dict) and x.get("name")]
                if names:
                    return ", ".join(names)
            if isinstance(a, str):
                return a
    return None


LABEL_CLASS_RE = re.compile(
    r'(?i)<(?:div|span|p|a|section|header|aside|small|em|strong|h[1-6]|li)\b[^>]*\b(?:class|id|data-[a-z-]+)="'
    r'(?![^"]*(?:widget|sidebar|taboola|outbrain|recommend|related|footer|nav|menu|promo-list|trending|most-read|'
    r'newsletter|banner|ad-slot|adslot|ad-container|dfp|rail|carousel|ticker|links-list|marketplace|comment))'
    r'[^"]*(?:sponsor|sponsored|branded|brand-content|native|partner|advertorial|studio|paid|promoted|kicker|eyebrow|'
    r'overline|section-name|article-tag|tagline|disclaimer|disclosure|supplement|pubbli|publi|anzeige|reklam|'
    r'author|byline|bio)'
    r'[^"]*"[^>]*>(.{0,400}?)</'
)
META_RE = re.compile(
    r'(?i)<meta\b[^>]*(?:name|property)="(?:article:section|article:tag|section|category|keywords|og:type|'
    r'article:content_tier|dc\.type|sailthru\.tags|parsely-section|parsely-tags|news_keywords|sponsor|sponsored)"[^>]*content="([^"]{0,300})"'
)
TITLE_RE = re.compile(r'(?is)<title[^>]*>(.*?)</title>')
TAG_RE = re.compile(r'<[^>]+>')

# A byline is a name, sometimes a desk or a wire co-credit. It is never a slab of page chrome.
AUTHOR_MAX_CHARS = 80
# In a list of bylines every element is short and reads as a name; an affiliation or a bio does not.
# "Wanning Sun, Professor of Media and Cultural Studies, University of Technology Sydney" has three
# short segments but only the first is a name, so length alone cannot tell them apart.
AUTHOR_SEGMENT_MAX = 40
AUTHOR_NAME_WORDS_MAX = 4
# Words that belong to navigation and furniture, never to a byline. "Trending" is deliberately absent:
# "India Today Trending Desk" is a real desk byline, and desk names have to survive.
AUTHOR_JUNK_RE = re.compile(
    r"(?i)\b(sections?|menu|navigation|subscribe|newsletter|breadcrumb|follow us|share this|sign ?up|"
    r"log ?in|cookies?)\b")
# Furniture that extraction tacks onto the end of an otherwise good byline.
AUTHOR_TRAILING_JUNK_RE = re.compile(r"(?i)[\s|,;-]*(read more|share this|follow us|more from .*)$")


def clean_author(value: Optional[str]) -> Optional[str]:
    """An author field a byline pattern can trust, or None.

    Extraction sometimes returns markup ("di <a href=...>Annamaria Grisorio</a>") or a slab of page
    chrome ("SECTIONS China represented by Foreign Minister Wang Yi"). Author scoped signatures read
    this field, so junk in it becomes a false state origin label: that Economic Times navigation text
    put a diplomat's name and title in the author field and the diplomat list matched it.

    The rules, each measured against the real fields in the database rather than guessed. Markup is
    stripped rather than rejected, because a byline wrapped in a link is still a byline. Trailing
    furniture is trimmed. Navigation words reject the field outright. A long field is kept whole when
    it reads as a list of names, and reduced to its first element when a name is followed by an
    affiliation, which is how The Conversation bylines its academics. Anything else too long to be a
    byline is dropped. Wire co-credits ("Antaranews Com; Xinhua") and desk names ("Advertorial Desk")
    must survive: credit and disclosure patterns depend on them.
    """
    if not value:
        return None
    text = TAG_RE.sub(" ", unescape(value))
    text = re.sub(r"\s+", " ", text).strip()
    text = AUTHOR_TRAILING_JUNK_RE.sub("", text).strip().strip(",;|-").strip()
    if not text or AUTHOR_JUNK_RE.search(text):
        return None
    if len(text) <= AUTHOR_MAX_CHARS:
        return text
    segments = [s.strip() for s in text.split(",") if s.strip()]
    looks_like_names = all(len(s) <= AUTHOR_SEGMENT_MAX and len(s.split()) <= AUTHOR_NAME_WORDS_MAX for s in segments)
    if len(segments) > 1 and looks_like_names:
        return text
    head = segments[0] if segments else ""
    if head and len(head) <= AUTHOR_MAX_CHARS and re.search(r"[^\W\d_]", head):
        return head
    return None


def clean_stored_authors(conn) -> int:
    """Apply clean_author to author fields already stored, returning the number changed. Author
    scoped patterns read the stored field, so without this a reclassification would keep matching
    junk captured before the rule existed."""
    changed = 0
    for r in conn.execute("SELECT id, author FROM articles WHERE author IS NOT NULL AND author != ''").fetchall():
        cleaned = clean_author(r["author"])
        if cleaned != r["author"]:
            conn.execute("UPDATE articles SET author=? WHERE id=?", (cleaned, r["id"]))
            changed += 1
    conn.commit()
    return changed


def page_labels(html: str) -> str:
    """Text from page chrome where disclosures live: title tag, meta section and tag values,
    JSON-LD sponsor fields, and elements whose class or id names a sponsored or partner label."""
    if not html:
        return ""
    parts = []
    m = TITLE_RE.search(html)
    if m:
        parts.append(TAG_RE.sub(" ", m.group(1)).strip())
    for m in META_RE.finditer(html):
        parts.append(m.group(1))
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict):
                continue
            for key in ("sponsor", "funder", "articleSection", "genre", "keywords", "@type", "creator", "publisher"):
                v = it.get(key)
                if isinstance(v, dict):
                    v = v.get("name")
                if isinstance(v, list):
                    v = ", ".join(str(x.get("name") if isinstance(x, dict) else x) for x in v)
                if v:
                    parts.append("%s: %s" % (key, v))
    seen = 0
    for m in LABEL_CLASS_RE.finditer(html):
        txt = TAG_RE.sub(" ", m.group(1))
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt:
            parts.append(txt)
            seen += 1
            if seen > 60:
                break
    return "\n".join(parts)[:8000]
