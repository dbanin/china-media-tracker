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
    # Asahi Shimbun: "become a paid member to read the rest"
    r"有料会員になると続き",
    # DIGITIMES Asia teaser pages
    r"the article requires paid subscription",
    # NHK ONE serves the first lines and asks every reader to register and accept its terms first
    r"NHK ONEはどなたでもご利用になれます",
]
_interstitial_re = re.compile("|".join("(?:%s)" % m for m in INTERSTITIAL_MARKERS), re.IGNORECASE)

SHORT_BODY_CHARS = 600
# A marker that states how much of the article is withheld ("残り886文字", 886 characters remain) is
# a truncation, not a sign-up box beside a full text, so it holds for a longer teaser. Characters in
# Japanese carry several times the content of Latin ones, and Asahi teasers run to 700.
TRUNCATION_MARKER_RE = re.compile(r"残り(?:\s|<[^>]+>)*[\d,]+(?:\s|<[^>]+>)*文字")
TRUNCATION_BODY_CHARS = 1500


def detect_paywall(html: str, body_text: Optional[str]) -> Tuple[bool, Optional[str]]:
    if not html:
        return False, None
    if HARD_PAYWALL_JSONLD.search(html):
        return True, "schema.org isAccessibleForFree false"
    m = _interstitial_re.search(html)
    if m and (not body_text or len(body_text) < SHORT_BODY_CHARS):
        return True, "interstitial marker with short body: %s" % m.group(0)[:60]
    m = TRUNCATION_MARKER_RE.search(html)
    if m and (not body_text or len(body_text) < TRUNCATION_BODY_CHARS):
        return True, "truncation marker: %s" % TAG_RE.sub("", m.group(0))[:60]
    art = embedded_article(html)
    if art is not None and art.get("premium"):
        return True, "embedded article marked premium"
    return False, None


# Outlet templates whose article text trafilatura cannot see as served. Each fix is keyed on the
# template's own markup, not on the domain, and never reads text a paywalled page withholds.
#
# Antara (en.antaranews.com, www.antaranews.com) writes the opening paragraph as bare text at the
# top of div.wrap__article-detail-content, ahead of the first <br />, and trafilatura drops that
# text node. The opening paragraph is the one that carries the dateline and the wire credit,
# "Beijing (ANTARA/Xinhua) -" or "Hangzhou, China (ANTARA/PRNewswire)-", so every Antara body
# lost exactly the part a credit pattern reads. Wrapping the leading text in a paragraph keeps it.
ANTARA_LEAD_RE = re.compile(
    r'(<div\b[^>]*\bclass="[^"]*\bwrap__article-detail-content\b[^"]*"[^>]*>)(\s*)([^<\s][^<]*)')

# The Straits Times paid press release pages (route routes/ArticlePPR/ArticlePPR) are rendered in
# the browser from a React Router data payload: the served document holds the issuer disclaimer and
# the headline, and the release itself is only in the payload's htmlContent field. Extraction
# returned a body of about 280 characters for every release in the section, without the wire
# dateline or the headline's issuer, so identical state releases were labelled state origin at
# other outlets and independent journalism here. The section is free to read; a page carrying any
# paywall marker is never read this way.
_RR_STREAM_RE = re.compile(r'streamController\.enqueue\(("(?:[^"\\]|\\.)*")\)')
_PPR_ROUTE = "routes/ArticlePPR/ArticlePPR"

# Templates whose extraction changed, as (config.EXTRACT_VERSION that changed it, URL prefix, why).
# A body of such a template stored under an older version (articles.extract_version, NULL before the
# column existed) was extracted by the old reading and is not the article as the page now reads: it is
# discarded and extracted again before it is classified (stale_extraction, classify_rules.ensure_body).
# Keyed on URL prefixes because the runner keeps bodies but not the saved pages, so the template
# cannot be recognised from the page there. Measured on the 2026-10-12 snapshot: the 410 stored
# Straits Times paid release bodies ran 205 to 424 characters, the disclaimer and the headline only;
# 20 of the 22 Antara English bodies whose page is on disk lacked the stamped lead. Other Straits
# Times sections and Indonesian Antara read the same under both versions and are not listed.
EXTRACTION_CHANGES = (
    ("2026.10.1", "https://www.straitstimes.com/paid-press-releases/", "Straits Times paid release payload"),
    ("2026.10.1", "https://en.antaranews.com/", "Antara English lead paragraph"),
)


def _version_key(version: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def stale_extraction(url: Optional[str], version: Optional[str]) -> Optional[str]:
    """Why a body stored under extraction `version` (None: before versions were recorded) for this URL
    predates its template's current extraction, or None when it does not."""
    for changed, prefix, why in EXTRACTION_CHANGES:
        if (url or "").startswith(prefix) and (not version or _version_key(version) < _version_key(changed)):
            return why
    return None


def _template_html(html: str) -> str:
    return ANTARA_LEAD_RE.sub(r"\1\2<p>\3</p>", html, count=1)


def press_release_payload(html: str) -> Optional[Tuple[Optional[str], str]]:
    """(headline, release HTML) from a Straits Times paid press release payload, or None."""
    if not html or _PPR_ROUTE not in html or detect_paywall(html, None)[0]:
        return None
    for m in _RR_STREAM_RE.finditer(html):
        try:
            data = json.loads(json.loads(m.group(1)))
        except ValueError:
            continue
        if not isinstance(data, list) or _PPR_ROUTE not in data:
            continue
        for obj in data:
            if not isinstance(obj, dict):
                continue
            fields = {}
            for k, v in obj.items():
                if not (k.startswith("_") and k[1:].isdigit() and isinstance(v, int) and 0 <= v < len(data)):
                    continue
                ki = int(k[1:])
                if ki < len(data) and data[ki] in ("htmlContent", "title"):
                    fields[data[ki]] = data[v]
            body = fields.get("htmlContent")
            if isinstance(body, str) and body.strip():
                title = fields.get("title")
                return (title if isinstance(title, str) and title.strip() else None), body
    return None


# Templates that keep the wire credit only in the data the page carries. Seven West Media pages
# (7news.com.au, perthnow.com.au, thewest.com.au) pass the article to the browser in window.PAGE_DATA,
# and the article's own object names where the copy came from in its "source" field: AAP, AP, Reuters,
# CNN, NBC, PA, Deutsche Presse Agentur, BANG, or a house name such as 7NEWS. The extracted body never
# carries it, 7NEWS shows it only as an agency logo, and wire copy is bylined "Digital Staff" (7NEWS)
# or "Staff Writers" (PerthNow, The West). Syndicated Xinhua copy there would have no credit line for
# any signature to read. The same page carries many other "source" and "credit" values, for the
# related article cards and for photographs ("credit":"AP, Xinhua" on a picture of an airport), so
# the field is read only from the object whose id is the article's own content id, the c-ID at the
# end of its URL (https://7news.com.au/...-c-22837752 is "id":"C-22837752"). Measured on the 817
# Seven West pages of the 2026-10-12 Australian audit: every one carries the field.
#
# Each rule: (name, content id in the URL, the id key and the form of its value in the page's data,
# the field naming the source, the publisher's own names). A house name is a desk of the publisher,
# not a wire: it is recorded in the page labels but never added to a byline.
EMBEDDED_SOURCE_RULES = (
    ("Seven West Media publication data", re.compile(r"-c-(\d+)(?:[/?#]|$)", re.I), ("id", "C-%s"), "source",
     ("7NEWS", "7NEWS Sport", "7LIFE", "7NEWS.com.au", "Sunrise", "The Morning Show", "Seven", "PerthNow",
      "The West Australian", "The Nightly", "CarExpert")),
)
# A source value is a name: short, one line.
EMBEDDED_SOURCE_MAX_CHARS = 60
# Bylines that name a desk rather than a writer, so the copy's real source has to be credited beside
# them for author scoped signatures to see it: "Staff Writers" (PerthNow), "Digital Staff" (7NEWS).
GENERIC_BYLINE_RE = re.compile(
    r"(?i)^\s*(?:(?:digital|online|web|editorial|news|wire)\s+)?"
    r"(?:staff(?:\s+(?:writers?|reporters?))?|desk|newsdesk|newsroom|wires?|wire\s+services|reporters?)\s*$")
_PAGE_URL_RES = (
    re.compile(r'(?i)<meta\b[^>]*\bproperty="og:url"[^>]*\bcontent="([^"]+)"'),
    re.compile(r'(?i)<link\b[^>]*\brel="canonical"[^>]*\bhref="([^"]+)"'),
)


def _string_end(s: str, i: int) -> int:
    """Index of the quote closing the JSON string that opens at s[i], or -1."""
    j = i + 1
    n = len(s)
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j
        j += 1
    return -1


def _enclosing_object(s: str, pos: int) -> Optional[int]:
    """Index of the "{" opening the object whose key starts at s[pos], or None. Walks back over
    strings, so braces inside them do not count."""
    depth = 0
    i = pos - 1
    while i >= 0:
        c = s[i]
        if c == '"':
            j = i - 1
            while j >= 0:
                if s[j] == '"':
                    k = j - 1
                    while k >= 0 and s[k] == "\\":
                        k -= 1
                    if (j - k - 1) % 2 == 0:
                        break
                j -= 1
            i = j - 1
            continue
        if c in "}]":
            depth += 1
        elif c in "{[":
            if depth == 0:
                return i if c == "{" else None
            depth -= 1
        i -= 1
    return None


def _own_string_field(s: str, start: int, field: str) -> Optional[str]:
    """The string value of `field` among the object's own keys (not those of objects nested in it),
    for the object opening at s[start]. The page data is a script literal, not strict JSON (it holds
    bare undefined values), so the object is walked rather than parsed whole."""
    depth = 0
    i = start
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            j = _string_end(s, i)
            if j < 0:
                return None
            if depth == 1:
                k = j + 1
                while k < n and s[k] in " \t\r\n":
                    k += 1
                if k < n and s[k] == ":" and s[i + 1:j] == field:
                    k += 1
                    while k < n and s[k] in " \t\r\n":
                        k += 1
                    if k < n and s[k] == '"':
                        e = _string_end(s, k)
                        try:
                            return json.loads(s[k:e + 1]) if e > 0 else None
                        except ValueError:
                            return None
                    return None
            i = j + 1
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth <= 0:
                return None
        i += 1
    return None


def embedded_source(html: str, url: Optional[str] = None) -> Optional[Tuple[str, bool]]:
    """(source, is_house) from the article's own object in the page data of a template listed in
    EMBEDDED_SOURCE_RULES, or None. The content id is taken from `url`, else from the page's og:url or
    canonical link. is_house is True when the source is one of the publisher's own names."""
    if not html:
        return None
    urls = [url] if url else []
    for rx in _PAGE_URL_RES:
        m = rx.search(html)
        if m:
            urls.append(unescape(m.group(1)))
    for _name, cid_re, id_key, field, house in EMBEDDED_SOURCE_RULES:
        for u in urls:
            m = cid_re.search(u or "")
            if not m:
                continue
            key_re = re.compile(r'"%s"\s*:\s*"%s"' % (re.escape(id_key[0]), re.escape(id_key[1] % m.group(1))),
                                re.I)
            for hit in list(key_re.finditer(html))[:5]:
                start = _enclosing_object(html, hit.start())
                value = _own_string_field(html, start, field) if start is not None else None
                value = re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""
                if value and len(value) <= EMBEDDED_SOURCE_MAX_CHARS:
                    return value, value.lower() in {h.lower() for h in house}
    return None


def wire_author(author: Optional[str], source: Optional[str]) -> Optional[str]:
    """The author field with a wire source credited beside a desk byline: "Staff Writers" and Xinhua
    give "Staff Writers; Xinhua", which xinhua_co_credit_author reads; no byline gives "Xinhua", which
    xinhua_author_field reads. A named writer is left alone, as is a byline already crediting the
    source. A byline extraction ran together with the source ("Staff Writers Reuters", from the two
    spans of the PerthNow byline) is separated."""
    if not source:
        return author
    a = re.sub(r"\s+", " ", author or "").strip()
    if not a:
        return source
    if any(seg.lower() == source.lower() for seg in re.split(r"\s*[;,/|]\s*", a)):
        return author
    rest = a[:-len(source)].strip() if a.lower().endswith(" " + source.lower()) else a
    if GENERIC_BYLINE_RE.match(rest):
        return "%s; %s" % (rest, source)
    return author


# ---------------------------------------------------------------------------
# Pages that are not the article
# ---------------------------------------------------------------------------
# A body is worth classifying only when it is the article. The stored bodies showed four ways a fetch
# returned something else, and a label made on any of them rested on the headline and on boilerplate:
#   - a related article card holding another story's full text, which extraction preferred to the
#     article (Afghanistan International, whose cards carry a RelatedArticle class);
#   - an article the page builds in the browser from data it carries, so the served document holds
#     only navigation (the New Straits Times Press sites, NST, Harian Metro and Berita Harian, pass the
#     article to a Vue component as an :article attribute), or a body that extraction's recall mode
#     passes over and its precision mode finds (the Lowy Institute's Interpreter);
#   - a bot or script challenge served with status 200 in place of the page (Le Monde's "Client
#     Challenge", DIGITIMES "Access denied");
#   - menus and furniture with no running text at all.
# The first two are repaired here, from the served page only: an embedded article marked premium is
# paywalled, and a page carrying any paywall marker is never read from its embedded data. The last two
# are recorded as failed and never classified (detect_unreadable).
PRUNE_XPATH = ["//*[contains(@class,'RelatedArticle')]"]
_EMBEDDED_ARTICLE_RE = re.compile(r'<article-component\b[^>]*?\s:article="([^"]*)"', re.S)

# A prose line is running text: at least PROSE_LINE_WORDS words, or, in a script written without
# spaces, at least PROSE_LINE_CHARS characters. Menu items, section names, bylines, datelines and share
# buttons are shorter. Hard wrapped copy (ANSA breaks its lines every ten words or so) still counts.
PROSE_LINE_WORDS = 7
PROSE_LINE_CHARS = 25
# A body with less running text than this holds no article. Measured on the 6,855 stored bodies of the
# 2026-10-12 snapshot: everything under 150 was a menu, a loading notice or a headline with a byline;
# the shortest real items, photo captions and one line wire flashes, have about 200.
MIN_PROSE_CHARS = 150
# The fetch stage records a body shorter than this as extraction_empty (fetch_articles.process_article).
EMPTY_BODY_CHARS = 200
# Below this much running text the extraction fallbacks are tried, and the one with most prose is kept.
FALLBACK_PROSE_CHARS = 600
_word_re = re.compile(r"\w+", re.UNICODE)
# Scripts written without spaces between words.
_UNSPACED_SCRIPT_RE = re.compile(r"[฀-໿က-႟ក-៿぀-ヿ㐀-䶿一-鿿]")

CHALLENGE_TITLE_RE = re.compile(
    r"(?is)<title[^>]*>\s*(client challenge|access denied|just a moment\.*|attention required![^<]*|"
    r"are you a robot\??|security check|ddos-guard|pardon our interruption|403 forbidden|request blocked)\s*</title>")
CHALLENGE_TEXT_RE = re.compile(
    r"(?i)javascript is disabled in your browser|please enable javascript to (?:proceed|continue)|"
    r"unusual traffic from your (?:computer )?network|has been blocked temporarily|"
    r"verify (?:that )?you are (?:a )?human|checking (?:if the site connection is secure|your browser before)")

# A consent or cookie wall served in place of the article, at any length: Seznam (Novinky.cz, Seznam
# Zpravy) answers automated clients with a 21,041 character page titled "Nastavení souhlasu s
# personalizací" whose 1,091 character body says the page failed to load; all 61 stored bodies of each
# outlet were that text. A page is a wall when its title is a consent dialog's, or its text carries a
# consent platform's marker (Seznam, Google "Before you continue", Didomi, OneTrust, Quantcast, Sourcepoint,
# Funding Choices, Taboola) and consent wording fills CONSENT_LINE_SHARE of it; and the body carries
# under CONSENT_HEADLINE_OVERLAP of the headline's words when the headline is known. A real article
# under a small cookie banner carries its headline and is mostly its own text. Consent is never given.
CONSENT_TITLE_RE = re.compile(
    r"(?is)<title[^>]*>\s*(nastavení souhlasu s personalizací|before you continue(?: to (?:google|youtube))?|"
    r"bevor sie (?:zu google )?weitergehen|we value your privacy|your privacy choices|consent required|"
    r"(?:cookie|privacy|consent) (?:settings|preferences|consent|notice|wall))\s*</title>")
CONSENT_TEXT_RE = re.compile(
    r"(?i)it seems the page failed to load correctly|něco nám brání v načtení stránky|cmp\.seznam\.cz|"
    r"before you continue to (?:google|youtube)|bevor sie zu google weitergehen|consent\.(?:google|youtube)\.com|"
    r"we value your privacy|privacy preference center|\bdidomi\b|\bonetrust\b|\bquantcast\b|\bsourcepoint\b|"
    r"fundingchoicesmessages|asks for (?:your )?consent to use your (?:personal )?data|"
    r"we and our (?:\d+ )?partners (?:store|use|process)|store and/or access information on a device|"
    r"\btaboola\b[^\n]{0,80}\bconsent\b|\bconsent\b[^\n]{0,80}\btaboola\b")
CONSENT_LINE_RE = re.compile(
    r"(?i)cookie|consent|privacy|partners|personali[sz]|vendors?\b|legitimate interest|advertis|\bads?\b|"
    r"\baccept|\breject|manage (?:options|preferences|settings)|preferences|\bvpn\b|\bproxy\b|ad ?block|"
    r"extension|souhlas|personaliz|blokátor|rozšíření|načíst|načtení|failed to load|reload|"
    r"datenschutz|einwilligung|partenaires|consentement|\bcmp\b|\bdata\b|device")
CONSENT_LINE_SHARE = 0.5
CONSENT_HEADLINE_OVERLAP = 0.5


def prose_chars(text: Optional[str]) -> int:
    """Characters of running text in a body: the sum of its prose lines (see PROSE_LINE_WORDS)."""
    n = 0
    for line in (text or "").split("\n"):
        s = line.strip()
        if not s:
            continue
        toks = _word_re.findall(s)
        if _UNSPACED_SCRIPT_RE.search(s) and s.count(" ") * 10 < len(s):  # Japanese, Chinese, Thai, Khmer
            ok = len(s) >= PROSE_LINE_CHARS
        else:
            ok = len(toks) >= PROSE_LINE_WORDS
        if ok:
            n += len(s)
    return n


def embedded_article(html: str) -> Optional[Dict]:
    """The article object a New Straits Times Press page hands its Vue component, or None."""
    m = _EMBEDDED_ARTICLE_RE.search(html or "")
    if not m:
        return None
    try:
        data = json.loads(unescape(m.group(1)))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _fallback_text(html: str, url: Optional[str]) -> Optional[str]:
    """The best of the other readings of a page whose first extraction found little running text:
    trafilatura's precision mode, and the embedded article of a free, unmarked page."""
    best, best_n = None, 0
    try:
        doc = trafilatura.bare_extraction(html, url=url, include_comments=False, include_tables=False,
                                          favor_precision=True, prune_xpath=PRUNE_XPATH)
        t = (doc.get("text") if isinstance(doc, dict) else getattr(doc, "text", None)) if doc is not None else None
    except Exception:
        t = None
    if t and prose_chars(t) > best_n:
        best, best_n = t, prose_chars(t)
    art = embedded_article(html)
    if art and not art.get("premium") and isinstance(art.get("body"), str) and not detect_paywall(html, None)[0]:
        try:
            t = trafilatura.extract("<html><body><article>%s</article></body></html>" % art["body"],
                                    include_comments=False, include_tables=False, favor_recall=True)
        except Exception:
            t = None
        if t and prose_chars(t) > best_n:
            best, best_n = t, prose_chars(t)
    blocks = prose_blocks_html(html)
    if blocks:
        try:
            t = trafilatura.extract(blocks, include_comments=False, include_tables=False, favor_recall=True)
        except Exception:
            t = None
        if t and prose_chars(t) > best_n:
            best, best_n = t, prose_chars(t)
    return best


def prose_blocks_html(html: str) -> Optional[str]:
    """The Lowy Institute's Interpreter splits each article into several Tailwind typography blocks,
    div.prose.dark:prose-invert, between a standfirst block (prose-lg) and an author block, and neither
    reading of trafilatura joins them: recall mode returned the standfirst and a photo caption, 262 to
    420 characters, and precision mode a single block. The article blocks joined, as one document."""
    if not html or "dark:prose-invert" not in html:
        return None
    try:
        from lxml import html as lxml_html
        doc = lxml_html.fromstring(html)
    except Exception:
        return None
    nodes = []
    for n in doc.xpath("//div[@class]"):
        cls = set(n.get("class").split())
        if {"prose", "dark:prose-invert"} <= cls and "prose-lg" not in cls:
            if not any(a in nodes for a in n.iterancestors()):
                nodes.append(n)
    if not nodes:
        return None
    return "<html><body><article>%s</article></body></html>" % "".join(
        lxml_html.tostring(n, encoding="unicode") for n in nodes)


_P_RE = re.compile(r"(?is)<p\b[^>]*>(.*?)</p>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript)\b.*?</\1>")


def paragraph_prose_chars(html: Optional[str]) -> int:
    """Running text the page holds in its own paragraph elements, scripts and styles aside."""
    if not html:
        return 0
    stripped = _SCRIPT_RE.sub(" ", html)
    return sum(prose_chars(re.sub(r"\s+", " ", unescape(TAG_RE.sub(" ", m.group(1)))))
               for m in _P_RE.finditer(stripped))


def consent_wall(html: Optional[str], text: Optional[str], headline: Optional[str] = None) -> bool:
    """True when the extracted text is a consent or cookie wall and not the article (see CONSENT_TITLE_RE)."""
    if not text:
        return False
    title_hit = bool(html and CONSENT_TITLE_RE.search(html))
    if not title_hit:
        if not CONSENT_TEXT_RE.search(text):
            return False
        lines = [s.strip() for s in text.split("\n") if s.strip()]
        total = sum(len(s) for s in lines)
        if not total or sum(len(s) for s in lines if CONSENT_LINE_RE.search(s)) < CONSENT_LINE_SHARE * total:
            return False
    if headline:
        from pipeline.body_dups import headline_overlap
        overlap = headline_overlap(headline, text)
        if overlap is not None and overlap >= CONSENT_HEADLINE_OVERLAP:
            return False
    return True


def detect_unreadable(html: Optional[str], text: Optional[str],
                      headline: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """(True, reason) when what was extracted is not an article: a bot or script challenge page, a
    consent or cookie wall at any length (given the headline, only when the body carries little of it),
    or a body without MIN_PROSE_CHARS of running text. Works on the body alone when html is None, as in
    the classify stage, which has only the stored body."""
    if not text or len(text) < SHORT_BODY_CHARS:
        m = (CHALLENGE_TITLE_RE.search(html) if html else None) or CHALLENGE_TEXT_RE.search(text or "")
        if m:
            return True, "challenge page: %s" % re.sub(r"\s+", " ", TAG_RE.sub("", m.group(0))).strip()[:60]
    if consent_wall(html, text, headline):
        return True, "consent wall"
    # A body under EMPTY_BODY_CHARS is the fetch stage's extraction_empty case and is judged there; the
    # prose test is for bodies long enough to pass that rule on menus alone.
    n = prose_chars(text)
    if text and len(text) >= EMPTY_BODY_CHARS and n < MIN_PROSE_CHARS:
        return True, "no article text: %d characters of running prose" % n
    return False, None


def extract(html: str, url: Optional[str] = None) -> Dict:
    """Return dict with text, title, author, date, sitename, description. Text may be None."""
    out = {"text": None, "title": None, "author": None, "date": None, "sitename": None, "description": None}
    if not html:
        return out
    try:
        doc = trafilatura.bare_extraction(
            _template_html(html), url=url, include_comments=False, include_tables=False,
            favor_recall=True, with_metadata=True, prune_xpath=PRUNE_XPATH,
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
    ppr = press_release_payload(html)
    if ppr:
        headline, body_html = ppr
        try:
            release = trafilatura.extract(body_html, include_comments=False, include_tables=False, favor_recall=True)
        except Exception:
            release = None
        if release and len(release) > len(out["text"] or ""):
            # The headline is printed above the release on the page, as other outlets' copies carry it.
            out["text"] = "%s\n%s" % (headline.strip(), release) if headline else release
    if prose_chars(out["text"]) < FALLBACK_PROSE_CHARS:
        alt = _fallback_text(html, url)
        if alt and prose_chars(alt) >= max(MIN_PROSE_CHARS, 2 * prose_chars(out["text"])):
            out["text"] = alt
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


def page_labels(html: str, url: Optional[str] = None) -> str:
    """Text from page chrome where disclosures live: title tag, the source the page data credits the
    article to (embedded_source, as "source: AAP"), meta section and tag values, JSON-LD sponsor
    fields, and elements whose class or id names a sponsored or partner label."""
    if not html:
        return ""
    parts = []
    m = TITLE_RE.search(html)
    if m:
        parts.append(TAG_RE.sub(" ", m.group(1)).strip())
    src = embedded_source(html, url)
    if src:
        parts.append("source: %s" % src[0])
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
