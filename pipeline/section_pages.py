"""Read a press release, sponsored or partner section index page as if it were a feed.

Most such sections publish no RSS, so the collector reads the section page itself and
treats every same-site link that looks like an article as a feed entry. The page is
fetched under the same rules as article pages (robots.txt, one request per domain every
three seconds, identifying user agent). The return value has the shape of
feeds_util.fetch_feed, so the collector handles both the same way.
"""
import re
from typing import Dict, List
from urllib.parse import urljoin, urlsplit

MAX_ENTRIES = 60
MIN_TITLE_CHARS = 20
_SKIP_PATH = re.compile(r"/(tag|tags|topic|topics|author|authors|category|categories|page|search|login|subscribe|about|contact|privacy|terms)(/|$)", re.I)


def _same_site(a: str, b: str) -> bool:
    ha = urlsplit(a).netloc.lower().replace("www.", "")
    hb = urlsplit(b).netloc.lower().replace("www.", "")
    return bool(ha) and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha))


def _stem(path: str) -> str:
    first = next((s for s in path.split("/") if s), "")
    return re.sub(r"s$", "", first.lower())


def article_links(html: str, section_url: str) -> List[Dict]:
    """Links under the section that look like articles: same site, deeper than the section
    path or carrying a story slug, with a headline length anchor text. First occurrence wins."""
    from lxml import html as lhtml
    try:
        doc = lhtml.fromstring(html)
    except Exception:
        return []
    section_path = urlsplit(section_url).path.rstrip("/")
    out, seen = [], set()
    for a in doc.iter("a"):
        href = a.get("href")
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = urljoin(section_url, href).split("#")[0]
        if not url.startswith("http") or not _same_site(url, section_url) or url in seen:
            continue
        path = urlsplit(url).path.rstrip("/")
        if not path or path == section_path or _SKIP_PATH.search(path):
            continue
        last = path.rsplit("/", 1)[-1]
        # The link must sit under the section: its path, or a first segment that is a close variant of the
        # section's ("/media-release/story" under "/media-releases"). Site-wide navigation does not count.
        under = path.startswith(section_path + "/") or _stem(path) == _stem(section_path)
        looks_like_story = under and (path.startswith(section_path + "/") or last.count("-") >= 3 or re.search(r"\d{5,}", last))
        title = " ".join((a.text_content() or "").split())
        if not looks_like_story or len(title) < MIN_TITLE_CHARS:
            continue
        seen.add(url)
        out.append({"link": url, "title": title[:300], "summary": "", "published_parsed": None})
        if len(out) >= MAX_ENTRIES:
            break
    return out


def fetch_entries(url: str) -> Dict:
    """Fetch a section page and return {ok, entries, status, error}, like feeds_util.fetch_feed."""
    from pipeline import fetch_articles
    allowed, why = fetch_articles.can_fetch(url)
    if not allowed:
        return {"ok": False, "entries": [], "status": None, "error": why}
    html, status, err = fetch_articles.get_page(url)
    if html is None:
        return {"ok": False, "entries": [], "status": status, "error": err}
    entries = article_links(html, url)
    if not entries:
        return {"ok": False, "entries": [], "status": status, "error": "no_article_links"}
    return {"ok": True, "entries": entries, "status": status, "error": None}
