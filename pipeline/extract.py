"""Boilerplate removal and paywall detection.

Paywall rule: a page that declares isAccessibleForFree false in schema.org
metadata is paywalled, full stop. A page with an interstitial marker and a
short extracted body is paywalled. Nothing else is attempted. No archive
mirrors, no reader proxies, no bypass services.
"""
import json
import re
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
    out["author"] = get("author") or None
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
