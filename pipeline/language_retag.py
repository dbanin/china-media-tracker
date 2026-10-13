"""Correction for articles stored under a language other than the one their feed publishes in.

An article's language is copied from its outlet's registry entry when the item is discovered
(fetch_feeds), and the keyword gate, the residual rules (classify_rules.body_relevance) and theme
tagging (themes.tag) all read the stored copy. On 2026-10-12 a script count over the titles of the
previous 30 days found outlets whose feeds publish in another language than the one registered:
Asia-Plus English's former section feed (Russian), Haqqin.az (Russian, registered Azerbaijani),
Hankyoreh English and Telex English (their "English" feeds served the Korean and Hungarian sites),
North Press Agency and Yemen Press Agency (an Arabic feed on the English outlet), Monaco Tribune (an
English feed on the French outlet), Tunisie Focus (French, registered Arabic), Goobjoog (Somali,
registered English), PSM News (Dhivehi, registered English), Mimi Mefo Info (mostly French, registered
English) and NewsMada (mostly Malagasy, registered French). The registry was corrected (each outlet's
notes say how), which changes new items only.

classify_rules.reclassify does not pick the stored items up: it selects articles on the ruleset
version of their current label and on extractions that changed, and classify_article reads the
article's stored language, so a ruleset change, 2026.10.1 included, judges them again in the wrong
language.

For every article whose stored language differs from its feed's language (language_of):
  - the stored language is set right and its theme tags are cleared, so themes.ensure tags it again
    in its language at the next export.
  - gate relevant: the current rules labels the residual rule made (signatures residual_c and
    residual_not_relevant, the only labels that read the language) are withdrawn, is_current 0,
    never deleted, and a classified article goes back to fetched, so the rules stage judges it again
    in its language. Model labels and state origin labels from a signature match are left as they
    are: neither reads the language.
  - gated out and still stored: the keyword gate is run again on the stored title and summary in its
    language. An item it passes is re-admitted as fetch_feeds.readmit_gated_out does (status queued,
    its day's gate relevant counters raised by one; discovery is not counted again). Items that
    pipeline/section_retag.py gated out are left to it: it already gated them in the feed's language.

It runs at the start of every discover stage, before gated-out items are pruned, so an item a
relay bundle brings in with the old language is corrected too. It is idempotent: a corrected article
carries its feed's language, and a second run finds nothing.
"""
from typing import Dict, List, Optional, Tuple

from pipeline import boilerplate, gate

CORRECTED_ON = "2026-10-12"
RESIDUAL_SIGNATURES = ("residual_c", "residual_not_relevant")

# Feeds whose registry entry does not give the language their stored items are in. Each feed: (language
# of its items, URL part of the items that are not in that language, or None). Hankyoreh English's old
# feed served the Korean site's feed from 2026-10-09, with the English edition's items still among them
# (/arti/english_edition/); kr_hani_en now polls the English edition feed. Telex English's feed served
# the Hungarian site's items from 2026-10-06; hu_telex_en is inactive until an English feed is found.
FEED_LANGUAGES: Dict[str, Tuple[str, Optional[str]]] = {
    "https://english.hani.co.kr/rss/": ("ko", "/arti/english_edition/"),
    "https://telex.hu/rss/english": ("hu", "/english/"),
}


def feed_languages(outlets: Optional[List[Dict]] = None) -> Dict:
    """{"own": {(outlet id, feed url): language}, "any": {feed url: set of languages}} from the registry:
    every feed an outlet lists, active or not, and every rejected section feed whose language is
    recorded (rejected_feed_language)."""
    from pipeline import registry
    source = outlets if outlets is not None else registry.load_outlets()
    own: Dict[Tuple[str, str], str] = {}
    anyl: Dict[str, set] = {}
    for o in source:
        for e in registry.feed_entries(o):
            own[(o["id"], e["url"])] = o["language"]
            anyl.setdefault(e["url"], set()).add(o["language"])
        for s in (o.get("release_sections") or {}).get("sections") or []:
            if s.get("rejected_feed") and s.get("rejected_feed_language"):
                own.setdefault((o["id"], s["rejected_feed"]), s["rejected_feed_language"])
    return {"own": own, "any": anyl}


def language_of(outlet_id: str, feed_url: Optional[str], url: Optional[str], langs: Dict) -> Optional[str]:
    """The language an article's feed publishes in, or None when the registry does not say. In order: the
    FEED_LANGUAGES record, the language of the article's own outlet when it lists the feed (or records it
    as a rejected section feed with its language), and the one language of every outlet that lists it."""
    if not feed_url:
        return None
    if feed_url in FEED_LANGUAGES:
        language, unless = FEED_LANGUAGES[feed_url]
        return None if unless and unless in (url or "") else language
    if (outlet_id, feed_url) in langs["own"]:
        return langs["own"][(outlet_id, feed_url)]
    others = langs["any"].get(feed_url) or set()
    return next(iter(others)) if len(others) == 1 else None


def _mismatched_groups(conn, langs: Dict) -> List[Tuple[str, str, str]]:
    """(outlet id, feed url, stored language) of every stored group some of whose articles are in
    another language than their feed's."""
    out = []
    for oid, feed, stored in conn.execute(
            "SELECT DISTINCT outlet_id, feed_url, language FROM articles WHERE feed_url IS NOT NULL"):
        if feed in FEED_LANGUAGES or language_of(oid, feed, None, langs) not in (None, stored):
            out.append((oid, feed, stored))
    return out


def _residual_labels(conn, article_id: int) -> List:
    return [r for r in conn.execute(
        "SELECT id, signatures_fired FROM classifications WHERE article_id=? AND is_current=1 AND method='rules'",
        (article_id,)) if any('"%s"' % s in (r[1] or "") for s in RESIDUAL_SIGNATURES)]


def _empty() -> Dict:
    return {"corrected": 0, "from": {}, "to": None, "residual_withdrawn": {}, "requeued": 0,
            "kept_labels": 0, "residual_left": 0, "left_to_section_retag": 0, "regated": 0, "readmitted": 0}


def news_sitemap_feeds(outlets: Optional[List[Dict]] = None) -> set:
    """Every feed the registry lists as a Google News sitemap (registry.feed_entries, type news_sitemap).
    A stored article does not record how its feed was read; its feed_url is the sitemap's URL."""
    from pipeline import news_sitemap, registry
    source = outlets if outlets is not None else registry.load_outlets()
    return {e["url"] for o in source for e in registry.feed_entries(o) if e.get("type") == news_sitemap.NEWS_SITEMAP}


def run(conn, outlets: Optional[List[Dict]] = None, dry_run: bool = False) -> Dict:
    """Apply the correction (or, with dry_run, only count it). Returns per outlet counts; an empty dict
    when there is nothing left to correct."""
    from pipeline import fetch_feeds, section_retag
    langs = feed_languages(outlets)
    removed = section_retag.removed_section_feeds(outlets)
    # The gate is run as fetch_feeds runs it: one feed boilerplate for the run (a dry run leaves the
    # feed_boilerplate cache alone), and a sitemap item's title_only terms; the sitemaps are read on first use.
    boiler = boilerplate.FeedBoilerplate(conn, cache=not dry_run)
    sitemaps: Optional[set] = None
    out: Dict[str, Dict] = {}
    for oid, feed, stored in _mismatched_groups(conn, langs):
        rows = conn.execute(
            """SELECT id, url, outlet_id, country, language, feed_url, title, summary, status, gate_relevant,
                      gate_terms, fail_reason FROM articles WHERE outlet_id=? AND feed_url=? AND language=? ORDER BY id""",
            (oid, feed, stored)).fetchall()
        for row in rows:
            want = language_of(oid, feed, row["url"], langs)
            if not want or want == stored:
                continue
            t = out.setdefault(oid, _empty())
            t["corrected"] += 1
            t["from"][stored] = t["from"].get(stored, 0) + 1
            t["to"] = want
            if row["gate_relevant"] and feed in removed:
                # Still tagged with a section that was never one: section_retag runs the gate in the feed's
                # language at the next export, and gates out what fails it with its labels. Only what it
                # keeps needs judging again here.
                d = section_retag.decide(row)
                if d is not None and not d["relevant"]:
                    t["left_to_section_retag"] += 1
                    if not dry_run:
                        conn.execute("UPDATE articles SET language=?, themes=NULL WHERE id=?", (want, row["id"]))
                    continue
            if row["gate_relevant"]:
                labels = _residual_labels(conn, row["id"])
                if labels and row["status"] != "classified":
                    # Not in the counts as classified (an unreadable or failed body): left to the stage that owns it.
                    t["residual_left"] += 1
                    labels = []
                for (lid, sigs) in labels:
                    kind = "residual_c" if '"residual_c"' in (sigs or "") else "residual_not_relevant"
                    t["residual_withdrawn"][kind] = t["residual_withdrawn"].get(kind, 0) + 1
                if not labels and conn.execute("SELECT 1 FROM classifications WHERE article_id=? AND is_current=1",
                                               (row["id"],)).fetchone():
                    t["kept_labels"] += 1
                requeue = bool(labels)
                t["requeued"] += 1 if requeue else 0
                if not dry_run:
                    conn.execute("UPDATE articles SET language=?, themes=NULL WHERE id=?", (want, row["id"]))
                    for (lid, _sigs) in labels:
                        conn.execute("UPDATE classifications SET is_current=0 WHERE id=?", (lid,))
                    if requeue:
                        conn.execute("UPDATE articles SET status='fetched' WHERE id=?", (row["id"],))
                continue
            if row["status"] != "gated_out" or (row["fail_reason"] or "").startswith(section_retag.REASON + ":"):
                if not dry_run:
                    conn.execute("UPDATE articles SET language=? WHERE id=?", (want, row["id"]))
                continue
            t["regated"] += 1
            if sitemaps is None:
                sitemaps = news_sitemap_feeds(outlets)
            relevant, hits = gate.check(row["title"] or "", row["summary"] or "", want, row["country"], url=row["url"],
                                        feed_boilerplate=boiler.shingles(row["outlet_id"], feed),
                                        title_only=feed in sitemaps)
            if relevant:
                t["readmitted"] += 1
            if dry_run:
                continue
            conn.execute("UPDATE articles SET language=? WHERE id=?", (want, row["id"]))
            if relevant:
                again = fetch_feeds.readmit_gated_out(conn, {"url": row["url"], "gate_terms": hits, "feed_url": feed})
                if again:
                    fetch_feeds.link_near_duplicates(conn, again, row["title"] or "", row["country"])
    if not dry_run:
        conn.commit()
    return out
