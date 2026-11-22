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
# The WordPress membership plugin WP-Members prints this box in place of a post's body for a reader who
# is not signed in, under whatever opening the site shows (Assabah of Morocco: "you can read the article
# after subscribing, or free after watching an advertisement"). The box is only there when text is held
# back, so it counts at any length of opening.
RESTRICTED_BOX_RE = re.compile(r'(?i)<div\b[^>]{0,200}\bid="wpmem_restricted_msg"')
# SRNA of Bosnia shows the opening paragraph of some stories and, in place of the rest, a line that says the
# complete news is for subscribers of its news service, inside a link to the service's sign-in page (2 of 7
# stories sampled on 2026-11-19; the others are shown whole and carry no such line). The line is only
# there when text is held back, so it counts at any length of opening. The sign-in link alone does not
# count: it is in the header of every page.
#
# The notice is on the page of a new story and gone some hours later: of 21 stories fetched from 2026-11-19
# to 21, the 8 fetched 43 to 73 minutes after publication carried it and the 13 fetched 3 hours 25 minutes or
# more after publication did not, and four pages asked for again on 2026-11-22 (three hours to three days
# after publication, two of them recorded with the notice) showed the whole text. The agency's public feed
# carries the whole text from the start, and since 2026-11-22, by the owner's decision, that text is the body
# of a story whose page shows the notice (pipeline/feed_text.py). So the notice counts against a body that
# stops where the page stops, and not against one that goes on past the opening the page shows
# (past_subscriber_notice): the page saved with such a body still carries the notice.
SUBSCRIBER_NOTICE_RE = re.compile(
    r'(?i)<a\b[^>]{0,300}\bhref="[^"]{0,200}/prijava/[^"]{0,200}"[^>]{0,100}>\s*<p\b[^>]{0,100}\bclass="restriction"'
    r'[^>]{0,100}>([^<]{0,300})')
SUBSCRIBER_NOTICE = "subscriber notice: restriction"
_PARAGRAPH_OPEN_RE = re.compile(r"(?i)<p\b")
NOTICE_OPENING_SPAN = 8000     # characters ahead of the notice in which the paragraph it follows opens

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
    # Chosun Ilbo membership stories ("content only Chosun Membership members can use"): the page
    # carries the story but shows it to members only, so it is never read (arc_story_html)
    r"조선멤버십 회원만 이용 가능한",
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
    if RESTRICTED_BOX_RE.search(html):
        return True, "members only box: wpmem_restricted_msg"
    if SUBSCRIBER_NOTICE_RE.search(html) and not past_subscriber_notice(html, body_text):
        return True, SUBSCRIBER_NOTICE
    m = _interstitial_re.search(html)
    if m and (not body_text or len(body_text) < SHORT_BODY_CHARS):
        return True, "interstitial marker with short body: %s" % m.group(0)[:60]
    m = TRUNCATION_MARKER_RE.search(html)
    if m and (not body_text or len(body_text) < TRUNCATION_BODY_CHARS):
        return True, "truncation marker: %s" % _untag(m.group(0), "")[:60]
    art = embedded_article(html)
    if art is not None and art.get("premium"):
        return True, "embedded article marked premium"
    return False, None


def one_line(text: Optional[str]) -> str:
    """The text with every run of white space, line breaks included, as one space."""
    return " ".join((text or "").split())


def subscriber_notice(html: str) -> Optional[Tuple[str, str]]:
    """(opening, notice) of a page that carries the subscriber notice (SUBSCRIBER_NOTICE_RE): the paragraph the
    page shows ahead of the notice and the notice's own words, each as one line. None without the notice."""
    m = SUBSCRIBER_NOTICE_RE.search(html or "")
    if not m:
        return None
    ahead = html[max(0, m.start() - NOTICE_OPENING_SPAN):m.start()]
    opens = [p.start() for p in _PARAGRAPH_OPEN_RE.finditer(ahead)]
    opening = one_line(unescape(_untag(ahead[opens[-1]:]))) if opens else ""
    return opening, one_line(unescape(m.group(1)))


def past_subscriber_notice(html: str, body_text: Optional[str]) -> bool:
    """Whether a body goes on past the opening that a page with the subscriber notice shows, so it is not
    that page's teaser: it holds the opening, more text after it, and not the notice's words. The text the
    page itself yields never does (it ends with the notice, or with the opening)."""
    parts = subscriber_notice(html)
    body = one_line(body_text)
    if not parts or not parts[0] or not parts[1] or parts[1] in body:
        return False
    at = body.find(parts[0])
    return at >= 0 and len(body) > at + len(parts[0])


# Outlet templates whose article text trafilatura cannot see as served. Each fix is keyed on the
# template's own markup, not on the domain, and never reads text a paywalled page withholds.
#
# Antara (en.antaranews.com, www.antaranews.com) writes the opening paragraph as bare text at the
# top of div.wrap__article-detail-content, ahead of the first <br />, and trafilatura drops that
# text node. The opening paragraph is the one that carries the dateline and the wire credit,
# "Beijing (ANTARA/Xinhua) -" or "Hangzhou, China (ANTARA/PRNewswire)-", so every Antara body
# lost exactly the part a credit pattern reads. Wrapping the leading text in a paragraph keeps it.
# Matched through _tag_finditer, with the class value tested by a lookahead and the rest of the tag at
# most 2,000 characters, so a page of unclosed or repeated tags costs linear time (see LABEL_CLASS_RE).
ANTARA_LEAD_RE = re.compile(
    r'(<div\b[^>]*\bclass="(?=[^"]*\bwrap__article-detail-content\b)[^"]*"[^>]{0,2000}>)(\s*)([^<\s][^<]*)')

# The Straits Times paid press release pages (route routes/ArticlePPR/ArticlePPR) are rendered in
# the browser from a React Router data payload: the served document holds the issuer disclaimer and
# the headline, and the release itself is only in the payload's htmlContent field. Extraction
# returned a body of about 280 characters for every release in the section, without the wire
# dateline or the headline's issuer, so identical state releases were labelled state origin at
# other outlets and independent journalism here. The section is free to read; a page carrying any
# paywall marker is never read this way.
_RR_STREAM_RE = re.compile(r'streamController\.enqueue\(("(?:[^"\\]|\\.)*")\)')
_PPR_ROUTE = "routes/ArticlePPR/ArticlePPR"

# Arc XP (Fusion) pages hand the story to the browser as Fusion.globalContent, whose content_elements
# hold its paragraphs. Most Arc outlets also print them in the served document, and trafilatura reads
# those; the Chosun Ilbo prints none (the article is drawn in the browser), so extraction found no text
# or only the reporter's bio: on the 2026-11-09 snapshot 1,234 of its 1,259 China relevant articles
# failed as extraction_empty. The story is read from the payload only when the served document lacks
# its paragraphs, the page carries no paywall marker (detect_paywall, which knows the Chosun membership
# notice), its content_restrictions code is absent or one of ARC_FREE_CODES, and it is not marked as a
# membership or premium story. Seen on saved pages: Chosun "0" on every story, members only ones
# included (they are refused by their membership marks); The National and The Irish Times "free"; paid
# stories at The Globe and Mail ("red"), The Irish Times and La Nacion of Costa Rica ("metered").
_ARC_GLOBAL_RE = re.compile(r"\bFusion\.globalContent\s*=\s*")
ARC_FREE_CODES = ("0", "free")
ARC_GATED_SUBTYPES = ("membership", "premium", "subscriber", "subscribers")
# Paragraphs of the payload looked for in the served document, and the characters of each compared.
ARC_PROBE_PARAGRAPHS = 5
ARC_PROBE_CHARS = 40

# Templates whose extraction changed, as (config.EXTRACT_VERSION that changed it, URL prefix, why).
# A body of such a template stored under an older version (articles.extract_version, NULL before the
# column existed) was extracted by the old reading and is not the article as the page now reads: it is
# discarded and extracted again before it is classified (stale_extraction, classify_rules.ensure_body).
# Keyed on URL prefixes because the runner keeps bodies but not the saved pages, so the template
# cannot be recognised from the page there. Measured on the 2026-10-12 snapshot: the 410 stored
# Straits Times paid release bodies ran 205 to 424 characters, the disclaimer and the headline only;
# 20 of the 22 Antara English bodies whose page is on disk lacked the stamped lead. Other Straits
# Times sections and Indonesian Antara read the same under both versions and are not listed.
# 2026.11.1, the Arc story payload (arc_story_html): on 21 saved Chosun pages (15 of the 2026-10-12
# snapshot, 6 fetched on 2026-11-09) the old reading found no text on 18 and only the reporter's bio on
# the 3 members only stories, which are now paywalled; the new one reads all 18 free stories. On the
# 2026-11-09 snapshot all 13 Chosun bodies that were classified are members only stories labelled on
# a bio of 201 to 346 characters. Radio Free Asia serves some stories the same way: 1 of the 27 local
# RFA pages, whose stored body was the site menu. The 222 pages of every other Arc outlet on disk read
# the same under both versions.
EXTRACTION_CHANGES = (
    ("2026.10.1", "https://www.straitstimes.com/paid-press-releases/", "Straits Times paid release payload"),
    ("2026.10.1", "https://en.antaranews.com/", "Antara English lead paragraph"),
    ("2026.11.1", "https://www.chosun.com/", "Chosun Ilbo Arc story payload"),
    ("2026.11.1", "https://rfa.org/", "Radio Free Asia Arc story payload"),
    ("2026.11.1", "https://www.rfa.org/", "Radio Free Asia Arc story payload"),
)


# The last version that changed how a page of any template is read. 2026.11.2 reads an article a page
# carries as data or as plain sibling paragraphs (_fallback_text) and tells three more challenge pages
# by their markup (CHALLENGE_MARKUP). A page that held no article text under an older version is
# fetched once more (fetch_articles.second_chance_candidates); a later change to one template, listed
# above, does not start that over.
GENERAL_EXTRACTION_VERSION = "2026.11.2"


def _version_key(version: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def before_general_change(version: Optional[str]) -> bool:
    """True for a page read under an extraction older than GENERAL_EXTRACTION_VERSION (None: before
    versions were recorded on failed pages)."""
    return not version or _version_key(version) < _version_key(GENERAL_EXTRACTION_VERSION)


def stale_extraction(url: Optional[str], version: Optional[str]) -> Optional[str]:
    """Why a body stored under extraction `version` (None: before versions were recorded) for this URL
    predates its template's current extraction, or None when it does not."""
    for changed, prefix, why in EXTRACTION_CHANGES:
        if (url or "").startswith(prefix) and (not version or _version_key(version) < _version_key(changed)):
            return why
    return None


def _tag_finditer(rx, start_rx, html: str, pos: int = 0):
    """rx.finditer(html), in linear time, for a pattern rx that opens a tag ("<name\\b[^>]*", the part
    start_rx finds) and whose remainder depends only on where that attribute run ends. When a tag opened
    at s does not match, no tag opened before the next ">" can (its attribute run ends in the same
    places), so the search skips past that ">"; finditer tried each of them in turn, and a page of 4,000
    unclosed tags took over a minute."""
    n = len(html)
    while pos < n:
        m = start_rx.search(html, pos)
        if not m:
            return
        hit = rx.match(html, m.start())
        if hit:
            yield hit
            pos = max(hit.end(), m.start() + 1)
            continue
        gt = html.find(">", m.start())
        if gt < 0:
            return   # every later tag's attribute run ends in the places this one's did
        pos = gt + 1


_ANTARA_START_RE = re.compile(r"<div\b")


def _template_html(html: str) -> str:
    if not html or "wrap__article-detail-content" not in html:
        return html
    for m in _tag_finditer(ANTARA_LEAD_RE, _ANTARA_START_RE, html):
        return html[:m.start()] + m.expand(r"\1\2<p>\3</p>") + html[m.end():]
    return html


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


def arc_story_text(html: str) -> Optional[str]:
    """The text of the story arc_story_html finds, or None."""
    arc = arc_story_html(html)
    if not arc:
        return None
    try:
        return trafilatura.extract(arc, include_comments=False, include_tables=False, favor_recall=True) or None
    except Exception:
        return None


def _plain(fragment) -> str:
    return re.sub(r"\s+", " ", unescape(_untag(fragment if isinstance(fragment, str) else ""))).strip()


def _arc_elements_html(elements, depth: int = 0) -> str:
    """The story's text, heading, list and quote elements as HTML, in order. Images, galleries,
    embeds and raw HTML blocks are left out, as extraction leaves them out of a served page."""
    out = []
    for e in elements if isinstance(elements, list) else []:
        if not isinstance(e, dict):
            continue
        kind, content = e.get("type"), e.get("content")
        if kind == "text" and isinstance(content, str):
            out.append("<p>%s</p>" % content)
        elif kind == "header" and isinstance(content, str):
            level = e.get("level") if isinstance(e.get("level"), int) and 1 <= e.get("level") <= 6 else 2
            out.append("<h%d>%s</h%d>" % (level, content, level))
        elif kind == "list" and depth < 3 and isinstance(e.get("items"), list):
            out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % i["content"] for i in e["items"]
                                               if isinstance(i, dict) and isinstance(i.get("content"), str)))
        elif kind in ("quote", "blockquote") and depth < 3:
            out.append("<blockquote>%s</blockquote>" % _arc_elements_html(e.get("content_elements"), depth + 1))
    return "".join(out)


def arc_story_html(html: str) -> Optional[str]:
    """The story of an Arc XP page as an HTML document, from its Fusion.globalContent payload, when the
    served document does not print it and nothing marks it as withheld (see ARC_FREE_CODES); else None."""
    if not html or "Fusion.globalContent" not in html or detect_paywall(html, None)[0]:
        return None
    m = _ARC_GLOBAL_RE.search(html)
    if not m:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(html, m.end())
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("type") != "story":
        return None
    restrictions = data.get("content_restrictions")
    code = restrictions.get("content_code") if isinstance(restrictions, dict) else None
    if code not in (None, "") and str(code).strip().lower() not in ARC_FREE_CODES:
        return None
    label = data.get("label")
    icon = label.get("membership_icon") if isinstance(label, dict) else None
    if isinstance(icon, dict) and icon.get("display"):
        return None
    if str(data.get("subtype") or "").strip().lower() in ARC_GATED_SUBTYPES:
        return None
    elements = data.get("content_elements")
    paragraphs = [p for p in (_plain(e.get("content")) for e in (elements if isinstance(elements, list) else [])
                              if isinstance(e, dict) and e.get("type") == "text") if len(p) >= 20]
    if not paragraphs:
        return None
    served = _plain(_strip_scripts(html))
    if any(p[:ARC_PROBE_CHARS] in served for p in paragraphs[:ARC_PROBE_PARAGRAPHS]):
        return None   # the page prints its story, and trafilatura reads it there
    body = _arc_elements_html(elements)
    return "<html><body><article>%s</article></body></html>" % body if body else None


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
# (pattern, the tag opening it): searched through _tag_finditer, the rest of the tag after the first
# attribute at most 1,000 characters (64 at most on the saved pages), so hostile markup costs linear time.
_PAGE_URL_RES = (
    (re.compile(r'(?i)<meta\b[^>]*\bproperty="og:url"[^>]{0,1000}\bcontent="([^"]+)"'), re.compile(r"(?i)<meta\b")),
    (re.compile(r'(?i)<link\b[^>]*\brel="canonical"[^>]{0,1000}\bhref="([^"]+)"'), re.compile(r"(?i)<link\b")),
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
    for rx, start in _PAGE_URL_RES:
        m = next(_tag_finditer(rx, start, html), None)
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
#
# Since 2026.11.2 three more readings of the served page are tried, on a page where neither the first
# extraction nor the older fallbacks find MIN_PROSE_CHARS of running text (trafilatura returns no
# document at all for some pages whose article is plainly there). Sized on two failed pages of every
# outlet with at least three such failures in the 2026-11-18 snapshot, fetched again that day:
#   - the articleBody of the page's schema.org metadata (jsonld_article_body), from the entry whose
#     headline is in the page's own title: The Sun of Malaysia prints the story nowhere else in the
#     served document;
#   - the article object of a Next.js page's __NEXT_DATA__ (next_data_article_html): RUV of Iceland and
#     Mosaique FM of Tunisia draw the story in the browser from it;
#   - the element whose own paragraphs hold the most running text (paragraph_container_text): the
#     Jamaica Observer and the Taipei Times print the story as plain sibling paragraphs that
#     trafilatura returns nothing for.
# None of them is tried on a page that carries any paywall marker, and the Next.js article is left alone
# when one of its own fields marks it as paid. A page that shows an opening of the story is never
# filled in from its data: what a reader is shown is what is read. On the 4,020 saved pages the three
# readings changed no body that had been read; they read 5 pages that had held nothing.
#
#
# SRNA of Bosnia (first read 2026-11-19) prints, inside the element that holds the story, the section name,
# the date and time, and a strip of other stories with their openings, and extraction returned all of it
# as the body (4,202 characters for a story of 2,742). Those three parts are pruned. No page saved before
# that day carries the markup, so no stored body reads differently.
PRUNE_XPATH = ["//*[contains(@class,'RelatedArticle')]",
               "//main[@id='single-main']/div[contains(@class,'title-container')]",
               "//main[@id='single-main']//div[@class='date-time']",
               "//main[@id='single-main']//section[@id='categories']"]
_EMBEDDED_ARTICLE_RE = re.compile(r'<article-component\b[^>]*?\s:article="([^"]*)"', re.S)
_NEXT_DATA_RE = re.compile(r'(?i)<script\b[^>]{0,300}\bid="__NEXT_DATA__"[^>]{0,300}>')
# The article object is looked for this many levels below pageProps (RUV: data.article, Mosaique FM:
# pagedata.article), its fields are walked this deep, and at most this many paragraph fields are joined.
NEXT_DATA_ARTICLE_DEPTH = 4
NEXT_DATA_LIST_ITEMS = 20
NEXT_DATA_WALK_DEPTH = 8
NEXT_DATA_MAX_PARTS = 400
# Fields of the article object that hold something other than the story.
NEXT_DATA_SKIP_KEY_RE = re.compile(
    r"(?i)relat|recommend|embed|more|next|prev|nav|menu|footer|comment|custom|live|clip|author|tag|topic|"
    r"parent|seo|image|media|caption|promo|advert|\bads?\b|newsletter|teaser")
# Fields that mark the story as paid when set: it is then never read from the data.
NEXT_DATA_GATED_KEY_RE = re.compile(r"(?i)premium|paywall|paid|locked|subscri|member|restrict|plus\b")
JSONLD_MAX_NODES = 5000
JSONLD_HEADLINE_CHARS = 40
# paragraph_container_text: the fewest prose paragraphs a story block holds, and the page furniture
# whose paragraphs are never a story.
PARAGRAPH_CONTAINER_MIN = 3
FURNITURE_TAGS = ("nav", "header", "footer", "aside", "form", "figure", "figcaption", "button", "select",
                  "dialog", "template", "blockquote")
FURNITURE_ATTR_RE = re.compile(
    r"(?i)foot|cookie|consent|gdpr|privacy|comment|disqus|side-?bar|\baside|relat|recommend|newsletter|"
    r"subscri|sign-?up|login|copyright|disclaimer|legal|\bterms|menu|\bnav|widget|promo|advert|\bads?\b|"
    r"sponsor|popup|modal|overlay|banner|share|social|breadcrumb|\bbio\b|author|teaser|most-?(?:read|popular)|"
    r"trending|latest|popular|ticker|carousel|slider|\btags?\b|about")

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
# Challenge pages that say nothing a reader could quote: a script, a spinner and the vendor's name. Told
# by their markup, on a page of at most CHALLENGE_PAGE_MAX_CHARS with no article text. Seen on
# 2026-11-18: TASS (Servicepipe, 2,172 characters), Japan Today (Akamai, a refresh to the same address
# with a bm-verify token, 2,951) and ISNA (ArvanCloud, "Transferring to the website...", 6,138). The
# tracker neither runs the script nor follows the token; the site's answer is recorded and kept.
CHALLENGE_MARKUP = (
    ("Servicepipe", re.compile(r"(?i)servicepipe\.tech/loaders|<js-challenge-loader\b")),
    ("Akamai", re.compile(r"(?i)http-equiv=\"refresh\"[^>]{0,400}[?&]bm-verify=")),
    ("ArvanCloud", re.compile(r"(?i)error-section__subtitle--waiting\">\s*Transferring to the website")),
)
CHALLENGE_PAGE_MAX_CHARS = 20000

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


def _fallback_text(html: str, url: Optional[str], found: int = 0) -> Optional[str]:
    """The best of the other readings of a page whose first extraction found little running text (found
    characters of it): trafilatura's precision mode and the joined typography blocks. When neither those
    nor the first extraction found MIN_PROSE_CHARS, so that the page as read holds no article at all, and
    it carries no paywall marker, also the article it holds as data (embedded_article,
    next_data_article_html, jsonld_article_body) or as plain sibling paragraphs
    (paragraph_container_text). A page that shows an opening and withholds the rest is left as it is
    read: on the saved pages Assabah of Morocco printed 300 to 600 characters above a subscription box
    and carried the whole story in its metadata."""
    best, best_n = None, 0

    def offer(t):
        nonlocal best, best_n
        if t and prose_chars(t) > best_n:
            best, best_n = t, prose_chars(t)

    def read(fragment):
        try:
            return trafilatura.extract("<html><body><article>%s</article></body></html>" % fragment,
                                       include_comments=False, include_tables=False, favor_recall=True)
        except Exception:
            return None

    try:
        doc = trafilatura.bare_extraction(html, url=url, include_comments=False, include_tables=False,
                                          favor_precision=True, prune_xpath=PRUNE_XPATH)
        t = (doc.get("text") if isinstance(doc, dict) else getattr(doc, "text", None)) if doc is not None else None
    except Exception:
        t = None
    offer(t)
    blocks = prose_blocks_html(html)
    if blocks:
        try:
            t = trafilatura.extract(blocks, include_comments=False, include_tables=False, favor_recall=True)
        except Exception:
            t = None
        offer(t)
    if max(found, best_n) >= MIN_PROSE_CHARS or detect_paywall(html, None)[0]:
        return best
    art = embedded_article(html)
    if art and isinstance(art.get("body"), str):
        offer(read(art["body"]))
    fragment = next_data_article_html(html)
    if fragment:
        offer(read(fragment))
    offer(jsonld_article_body(html))
    offer(paragraph_container_text(html))
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



def _letters(text) -> str:
    """Letters and digits of a headline, lower case, for comparing two renderings of it."""
    return re.sub(r"[\W_]+", "", unescape(text if isinstance(text, str) else "")).casefold()


def jsonld_article_body(html: str) -> Optional[str]:
    """The articleBody of the page's schema.org metadata as plain text, one paragraph a line where the
    field marks them, or None. Only an entry whose headline is in the page's own <title> is read (the
    longest, when several are): the Macau Daily Times prints one old story's entry on every page.
    Never called on a page with a paywall marker (_fallback_text): some paid sites put the whole story
    there for search engines."""
    m = _title(html or "")
    title = _letters(_untag(m.group(1))) if m else ""
    if not title:
        return None
    best = ""
    for block in _jsonld_scripts(html or ""):
        try:
            data = json.loads(block, strict=False)
        except Exception:
            continue
        stack, seen = [data], 0
        while stack and seen < JSONLD_MAX_NODES:
            it = stack.pop()
            seen += 1
            if isinstance(it, list):
                stack.extend(it)
            elif isinstance(it, dict):
                body = it.get("articleBody")
                head = _letters(it.get("headline") or it.get("name"))[:JSONLD_HEADLINE_CHARS]
                if isinstance(body, str) and len(body) > len(best) and len(head) >= 8 and head in title:
                    best = body
                if isinstance(it.get("@graph"), list):
                    stack.extend(it["@graph"])
    if not best.strip():
        return None
    text = unescape(best)
    if "<" in text:
        text = _untag(re.sub(r"(?i)</p\s*>|<br\s*/?>|</div\s*>|</h[1-6]\s*>", "\n", text))
    lines = (re.sub(r"[^\S\n]+", " ", s).strip() for s in unescape(text).split("\n"))
    return "\n".join(s for s in lines if s) or None


def _next_data(html: str):
    """The parsed __NEXT_DATA__ payload of a Next.js page, or None."""
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        return None
    end = html.find("</script>", m.end())
    if end < 0:
        return None
    try:
        return json.loads(html[m.end():end])
    except ValueError:
        return None


def _find_article(node, depth: int = 0):
    """The first object stored under the key "article" within NEXT_DATA_ARTICLE_DEPTH levels, breadth
    first, or None."""
    level = [node]
    for _ in range(NEXT_DATA_ARTICLE_DEPTH):
        nxt = []
        for it in level:
            if isinstance(it, dict):
                art = it.get("article")
                if isinstance(art, dict):
                    return art
                nxt.extend(v for v in it.values() if isinstance(v, (dict, list)))
            elif isinstance(it, list):
                nxt.extend(v for v in it[:NEXT_DATA_LIST_ITEMS] if isinstance(v, (dict, list)))
        level = nxt
    return None


def next_data_article_html(html: str) -> Optional[str]:
    """The markup of the story a Next.js page carries as data and draws in the browser: the fields of
    props.pageProps' article object that hold paragraphs (a string with a <p> element), in page order,
    joined. Fields named for other things than the story (related items, embeds of other stories, menus,
    tags, authors, live feeds: NEXT_DATA_SKIP_KEY_RE) are passed over. None when the page has no such
    object, when it holds no paragraphs, or when one of its own fields marks it as paid
    (NEXT_DATA_GATED_KEY_RE)."""
    data = _next_data(html)
    props = (data.get("props") or {}).get("pageProps") if isinstance(data, dict) else None
    art = _find_article(props) if isinstance(props, dict) else None
    if not art:
        return None
    for k, v in art.items():
        if NEXT_DATA_GATED_KEY_RE.search(k) and v and str(v).lower() not in ("false", "0", "free", "none", "no"):
            return None
    parts = []

    def walk(node, depth):
        if depth > NEXT_DATA_WALK_DEPTH or len(parts) >= NEXT_DATA_MAX_PARTS:
            return
        if isinstance(node, str):
            if _P_OPEN_RE.search(node):
                parts.append(node)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)
        elif isinstance(node, dict):
            for k, v in node.items():
                if v and not NEXT_DATA_SKIP_KEY_RE.search(k):
                    walk(v, depth + 1)

    walk(art, 0)
    return "\n".join(parts) or None


def paragraph_container_text(html: str) -> Optional[str]:
    """The paragraphs of the element whose own <p> children hold the most running text, one a line, when
    at least PARAGRAPH_CONTAINER_MIN of them are prose and together they reach MIN_PROSE_CHARS. A story
    printed as plain sibling paragraphs sits in one such element; teasers of other stories, each in its
    own box, do not add up. Page furniture is not looked at: navigation, headers, footers, asides and
    forms, and any element whose class or id names a footer, comments, a consent notice, related or
    recommended items, a newsletter box and the like (FURNITURE_ATTR_RE), judged on the element and on
    what encloses it up to the page's <article> or <main>."""
    if not html or not _P_OPEN_RE.search(html):
        return None
    try:
        from lxml import html as lxml_html
        doc = lxml_html.fromstring(html)
    except Exception:
        return None
    by_parent: Dict = {}
    for p in doc.iter("p"):
        parent = p.getparent()
        if parent is not None:
            by_parent.setdefault(parent, []).append(p)

    def is_furniture(el) -> bool:
        # Up to the enclosing <article> or <main>: inside one, only what lies between it and the
        # paragraphs counts (the Jamaica Observer's article sits in a "widget" column like its sidebars).
        while el is not None:
            if el.tag in FURNITURE_TAGS or FURNITURE_ATTR_RE.search(
                    "%s %s" % (el.get("class") or "", el.get("id") or "")):
                return True
            if el.tag in ("article", "main"):
                return False
            el = el.getparent()
        return False

    best, best_n = None, 0
    for parent, ps in by_parent.items():
        if len(ps) < PARAGRAPH_CONTAINER_MIN or is_furniture(parent):
            continue
        lines = []
        for p in ps:
            for bad in p.xpath(".//script|.//style|.//noscript"):
                bad.drop_tree()
            line = re.sub(r"\s+", " ", p.text_content()).strip()
            if line:
                lines.append(line)
        sizes = [prose_chars(line) for line in lines]
        if sum(1 for n in sizes if n) >= PARAGRAPH_CONTAINER_MIN and sum(sizes) > best_n:
            best, best_n = lines, sum(sizes)
    return "\n".join(best) if best and best_n >= MIN_PROSE_CHARS else None


# What these two patterns find is found by _strip_scripts and _paragraphs, which give the same results
# in linear time: run with finditer or sub, each unclosed opening tag made the search run to the end of
# the page again (a page of 20,000 "<script>" took minutes).
_P_RE = re.compile(r"(?is)<p\b[^>]*>(.*?)</p>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript)\b.*?</\1>")
_SCRIPT_OPEN_RE = re.compile(r"(?i)<(script|style|noscript)\b")
_SCRIPT_CLOSE_RES = {name: re.compile(r"(?i)</%s>" % name) for name in ("script", "style", "noscript")}
_P_OPEN_RE = re.compile(r"(?i)<p\b")
_P_CLOSE_RE = re.compile(r"(?i)</p>")


def _strip_scripts(html: str) -> str:
    """_SCRIPT_RE.sub(" ", html): each script, style and noscript element replaced by a space. An opening
    tag with no closing tag after it is left alone, and so is every later one of its name."""
    out, pos, last, unclosed = [], 0, 0, set()
    while True:
        m = _SCRIPT_OPEN_RE.search(html, pos)
        if not m:
            break
        name = m.group(1).lower()
        close = None if name in unclosed else _SCRIPT_CLOSE_RES[name].search(html, m.end())
        if close is None:
            unclosed.add(name)
            pos = m.end()
            continue
        out.append(html[last:m.start()])
        out.append(" ")
        last = pos = close.end()
    out.append(html[last:])
    return "".join(out)


def _paragraphs(html: str):
    """The contents of the page's paragraph elements, as _P_RE.finditer finds them (group 1)."""
    pos = 0
    while True:
        m = _P_OPEN_RE.search(html, pos)
        if not m:
            return
        gt = html.find(">", m.end())
        if gt < 0:
            return
        close = _P_CLOSE_RE.search(html, gt + 1)
        if not close:
            return
        yield html[gt + 1:close.start()]
        pos = close.end()


def paragraph_prose_chars(html: Optional[str]) -> int:
    """Running text the page holds in its own paragraph elements, scripts and styles aside."""
    if not html:
        return 0
    stripped = _strip_scripts(html)
    return sum(prose_chars(re.sub(r"\s+", " ", unescape(_untag(p))))
               for p in _paragraphs(stripped))


def consent_wall(html: Optional[str], text: Optional[str], headline: Optional[str] = None) -> bool:
    """True when the extracted text is a consent or cookie wall and not the article (see CONSENT_TITLE_RE)."""
    if not text:
        return False
    title_hit = bool(html and _title_search(CONSENT_TITLE_RE, html))
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
    """(True, reason) when what was extracted is not an article: a bot or script challenge page (told by
    its title, its text or its markup, CHALLENGE_MARKUP), a
    consent or cookie wall at any length (given the headline, only when the body carries little of it),
    or a body without MIN_PROSE_CHARS of running text. Works on the body alone when html is None, as in
    the classify stage, which has only the stored body."""
    if not text or len(text) < SHORT_BODY_CHARS:
        m = (_title_search(CHALLENGE_TITLE_RE, html) if html else None) or CHALLENGE_TEXT_RE.search(text or "")
        if m:
            return True, "challenge page: %s" % re.sub(r"\s+", " ", _untag(m.group(0), "")).strip()[:60]
        if html and len(html) <= CHALLENGE_PAGE_MAX_CHARS:
            for vendor, rx in CHALLENGE_MARKUP:
                if rx.search(html):
                    return True, "challenge page: %s" % vendor
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
    if doc is not None:
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
    # A page trafilatura finds nothing in at all can still be an Arc story drawn in the browser, or carry
    # its article in one of the forms _fallback_text reads, so the other readings run on it too.
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
    story = arc_story_text(html)
    if story and (out["text"] is None or prose_chars(story) > prose_chars(out["text"])):
        out["text"] = story
    if prose_chars(out["text"]) < FALLBACK_PROSE_CHARS:
        alt = _fallback_text(html, url, prose_chars(out["text"]))
        if alt and prose_chars(alt) >= max(MIN_PROSE_CHARS, 2 * prose_chars(out["text"])):
            out["text"] = alt
    return out


def jsonld_authors(html: str) -> Optional[str]:
    """Best effort author from JSON-LD when trafilatura finds none."""
    for block in _jsonld_scripts(html or ""):
        try:
            data = json.loads(block)
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


# Elements whose class, id or data attribute names a label. Matched through _tag_finditer, so a page of
# unclosed tags costs linear time: with finditer, 4,000 repetitions of '<div class="piano-' took 76
# seconds. The attribute value is tested by lookaheads and then taken whole, so a tag that fails after
# it is not tried again at every keyword in the value, and the rest of the tag after the value is at
# most LABEL_TAG_REST_CHARS long (the longest on the 4,019 saved pages was 564). On every saved page
# page_labels returns exactly what the earlier unbounded pattern gave.
LABEL_TAG_REST_CHARS = 2000
_LABEL_TAGS = r"(?:div|span|p|a|section|header|aside|small|em|strong|h[1-6]|li)"
_LABEL_START_RE = re.compile(r"(?i)<" + _LABEL_TAGS + r"\b")
LABEL_CLASS_RE = re.compile(
    r'(?i)<' + _LABEL_TAGS + r'\b[^>]*\b(?:class|id|data-[a-z-]+)="'
    r'(?![^"]*(?:widget|sidebar|taboola|outbrain|recommend|related|footer|nav|menu|promo-list|trending|most-read|'
    r'newsletter|banner|ad-slot|adslot|ad-container|dfp|rail|carousel|ticker|links-list|marketplace|comment))'
    r'(?=[^"]*(?:sponsor|sponsored|branded|brand-content|native|partner|advertorial|studio|paid|promoted|kicker|eyebrow|'
    r'overline|section-name|article-tag|tagline|disclaimer|disclosure|supplement|pubbli|publi|anzeige|reklam|'
    r'author|byline|bio))'
    r'[^"]*"[^>]{0,%d}>(.{0,400}?)</' % LABEL_TAG_REST_CHARS
)
# Searched through _tag_finditer, with at most 1,000 characters between the name and the content
# attribute (88 at most on the saved pages), so hostile markup costs linear time.
META_RE = re.compile(
    r'(?i)<meta\b[^>]*(?:name|property)="(?:article:section|article:tag|section|category|keywords|og:type|'
    r'article:content_tier|dc\.type|sailthru\.tags|parsely-section|parsely-tags|news_keywords|sponsor|sponsored)"[^>]{0,1000}content="([^"]{0,300})"'
)
TITLE_RE = re.compile(r'(?is)<title[^>]*>(.*?)</title>')
TAG_RE = re.compile(r'<[^>]+>')
_TITLE_START_RE = re.compile(r"(?i)<title")
_META_START_RE = re.compile(r"(?i)<meta\b")
_JSONLD_OPEN_RE = re.compile(r"(?i)<script[^>]+application/ld\+json[^>]*>")
_JSONLD_START_RE = re.compile(r"(?i)<script")
_SCRIPT_CLOSE_RE = re.compile(r"(?i)</script>")


def _untag(text: str, repl: str = " ") -> str:
    """TAG_RE.sub(repl, text) in linear time. No tag opens after the last ">", and TAG_RE.sub tried
    every "<" there against the whole rest of the text (a run of 100,000 took minutes)."""
    end = text.rfind(">")
    return text if end < 0 else TAG_RE.sub(repl, text[:end + 1]) + text[end + 1:]


def _title(html: str):
    """TITLE_RE.search(html) in linear time: when the first title tag has no closing tag after it, no
    later one has either, and search tried each of them to the end of the page."""
    m = _TITLE_START_RE.search(html)
    return TITLE_RE.match(html, m.start()) if m else None


def _title_search(rx, html: str):
    """rx.search(html) for a pattern opening "<title[^>]*>" (CHALLENGE_TITLE_RE, CONSENT_TITLE_RE)."""
    return next(_tag_finditer(rx, _TITLE_START_RE, html), None)


def _jsonld_scripts(html: str):
    """The contents of the page's JSON-LD script elements, as the pattern
    <script[^>]+application/ld\\+json[^>]*>(.*?)</script> (re.S, re.I) finds them, in linear time."""
    pos = 0
    while True:
        m = next(_tag_finditer(_JSONLD_OPEN_RE, _JSONLD_START_RE, html, pos), None)
        if not m:
            return
        close = _SCRIPT_CLOSE_RE.search(html, m.end())
        if not close:
            return
        yield html[m.end():close.start()]
        pos = close.end()

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
    text = _untag(unescape(value))
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
    m = _title(html)
    if m:
        parts.append(_untag(m.group(1)).strip())
    src = embedded_source(html, url)
    if src:
        parts.append("source: %s" % src[0])
    for m in _tag_finditer(META_RE, _META_START_RE, html):
        parts.append(m.group(1))
    for block in _jsonld_scripts(html):
        try:
            data = json.loads(block)
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
    for m in _tag_finditer(LABEL_CLASS_RE, _LABEL_START_RE, html):
        txt = _untag(m.group(1))
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt:
            parts.append(txt)
            seen += 1
            if seen > 60:
                break
    return "\n".join(parts)[:8000]
