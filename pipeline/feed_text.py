"""The whole text of a story from the feed that listed it, where its page shows the opening alone.

SRNA of Bosnia shows the opening paragraph of a new story and, in place of the rest, a line that the complete
news is for subscribers of its news service (extract.SUBSCRIBER_NOTICE_RE, where the counts are). The page shows
the whole text some hours later, and the agency's public feed carries it from the start, in the item's
description. Until 2026-11-22 such an item was recorded as paywalled with the opening alone, because what a
reader is shown is what is read. On 2026-11-22 the owner decided that the feed's text is the body of such a
story. It is the one exception to that rule and it holds only where the page shows the subscriber notice:
the text is the one the agency gives every reader of its feed, and nothing is asked of the sign-in page.

The feed is read again for it, once per process and only when a page shows the notice, by the same reader as
at discovery (feeds_util.fetch_feed: robots.txt, the per domain pacing, the project's user agent). The
description is not kept at discovery: articles.summary holds its first 2,000 characters as one of the gate's
and the duplicate checks' inputs, and a story runs to 4,000.

The text is used only when it is the same story as the page's. The item is found by its address, and every
line the page shows from the feed's first paragraph on must be the feed's own paragraphs, in order, with more
of them to come. Anything else (the item no longer in the feed, a feed that does not answer, an opening that
reads differently) leaves the item paywalled with the opening, as before.
"""
import re
import threading
from html import unescape
from typing import Dict, List, Optional

from pipeline import extract, gate, store

# The description ends with the desk's sign-off ("/end/tp/sg"), which the page does not print. It is dropped,
# so the body is the text the page reads as once the notice is gone: on four pages compared on 2026-11-22 the
# two were the same but for where lines break (the page holds the rest of the story as one paragraph and the
# feed keeps the story's own).
SIGN_OFF_RE = re.compile(r"\s*/end/[\w/]*$")
_BREAK_RE = re.compile(r"(?i)<br\s*/?>|</p\s*>")

_lock = threading.Lock()
_listed: Dict[str, Dict[str, List[str]]] = {}     # feed address -> {url hash of an item: its paragraphs}


def paragraphs(description: Optional[str]) -> List[str]:
    """A feed description as the story's paragraphs, each one line, without the desk's sign-off. SRNA's is
    plain text with a blank line between paragraphs; markup, where a feed has it, breaks at <br> and </p>."""
    text = unescape(gate.strip_html(_BREAK_RE.sub("\n", description or "")))
    out = [extract.one_line(p) for p in text.split("\n")]
    out = [p for p in out if p]
    if out:
        out[-1] = SIGN_OFF_RE.sub("", out[-1])
    return [p for p in out if p]


def listed(feed_url: str) -> Dict[str, List[str]]:
    """What the feed lists now, as {url hash of the item: its paragraphs}. Asked for once per process: a feed
    that does not answer is not asked again, and its items stay as their pages read."""
    with _lock:
        if feed_url not in _listed:
            from pipeline import feeds_util, fetch_feeds
            found: Dict[str, List[str]] = {}
            try:
                for entry in feeds_util.fetch_feed(feed_url)["entries"]:
                    if entry.get("link"):
                        found[store.url_hash(entry["link"])] = paragraphs(fetch_feeds._entry_summary(entry))
            except Exception:  # noqa: BLE001  a feed that cannot be read changes nothing
                found = {}
            _listed[feed_url] = found
        return _listed[feed_url]


def whole_story(row, html: str, text: Optional[str]) -> Optional[str]:
    """The body of a story whose page shows the subscriber notice: what the page shows ahead of the feed's
    first paragraph (the headline), then the feed's paragraphs. None when the page has no notice, the item
    came from no feed of the same site, the feed does not list it, or the feed's text is not the page's story
    with more to it. text is the page as extracted."""
    from pipeline.fetch_articles import domain_of
    parts = extract.subscriber_notice(html)
    feed_url = row["feed_url"]
    if not parts or not text or not feed_url or domain_of(feed_url) != domain_of(row["url"]):
        return None
    shown = [line for line in (extract.one_line(x) for x in text.split("\n")) if line and line != parts[1]]
    story = listed(feed_url).get(row["url_hash"])
    if not story or story[0] not in shown:
        return None
    at = shown.index(story[0])
    rest = shown[at:]
    if len(story) <= len(rest) or story[:len(rest)] != rest:
        return None
    return "\n".join(shown[:at] + story)
