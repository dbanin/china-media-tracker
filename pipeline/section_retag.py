"""Correction for articles collected through section feeds that were never sections.

Seven feeds were registered as press release, sponsored or partner sections and removed from
sources/outlets.yaml on 2026-10-12 (each outlet's notes say why, and each is recorded with its reason as a
rejected_feed on its section in release_sections, which this module reads). Five were a desk or the site-wide
feed, the Japan Times entry polled the frozen "Today's Top Stories" feed, and 7NEWS's "Sponsored" link
led to a dormant topic page whose feed carries the latest stories across desks. A section feed skips the
keyword gate, so every item they brought in was stored gate relevant with the single gate term
"section:<kind>", and classify_rules.arrival_of read each one as having arrived by a sponsored, partner
or press release section. They are ordinary editorial items.

For every article whose feed_url is one of these feeds and whose gate terms still carry a section term:
  - the section term is dropped. When a China term remains (a keyword reader that also tagged the
    section), the article stays relevant on it. Otherwise the keyword gate (pipeline/gate.py) is run
    on the stored title and summary, as for any editorial feed item, in the feed's language, and its
    terms are stored. It reads them as pipeline/fetch_feeds.py does: with the item's url (the sports
    section rule) and without the summary boilerplate of the item's feed (pipeline/boilerplate.py),
    so a China term the feed prints on every item does not keep an article it would have gated out.
  - an article the gate passes keeps its status and labels; with the section term gone its arrival is
    editorial_feed.
  - an article the gate rejects leaves the counts as any gated-out item does: status gated_out,
    gate_relevant 0. Its current labels are withdrawn (is_current 0), never deleted, and fail_reason
    records why and what the status was. Its day's gate-relevant discovery counter is lowered by one,
    the reverse of fetch_feeds.readmit_gated_out. store.prune_gated_out never deletes a row that holds
    a label, so these rows and their withdrawn labels stay.

It runs at the start of every export and every relay run and is idempotent: a corrected article no
longer carries a section term, so a second run finds nothing. The one exception is a model label that
arrives later for an article that was waiting in a submitted batch when it was gated out; it is
withdrawn the same way on the next run.
"""
import json
from typing import Dict, List, Optional

from pipeline import boilerplate, gate

REMOVED_ON = "2026-10-12"
# The removed feeds are read from sources/outlets.yaml, where each is recorded on the section it was
# registered for (release_sections: rejected_feed, rejected_feed_language, rejected_on, reason). That is
# the one list: pipeline/discover_sections.py reads the same records and never registers one again.
# removed_section_feeds() gives feed url: (outlet that registered it as a section, kind it was registered
# as, language of the feed's items when it is not the stored article's, what the feed actually is, date
# removed). Asia-Plus's English outlet
# registered its Russian site's feed, so its items are stored as English and are gated in Russian here;
# the English terms alone reject every one. Novaya Gazeta's /feed/rss is the Russian outlet's
# (ru_novayagazeta) editorial feed: the English outlet's section poll re-admitted its gated-out items
# (fetch_feeds.readmit_gated_out), so they are stored under ru_novayagazeta, in Russian, and the gate
# that already rejected them once is simply run again.
_removed: Optional[Dict] = None


def removed_section_feeds(outlets: Optional[List[Dict]] = None) -> Dict[str, tuple]:
    """Every rejected section feed in the registry (or in `outlets`); the registry's is read once."""
    global _removed
    if outlets is None and _removed is not None:
        return _removed
    if outlets is None:
        from pipeline import registry
        source = registry.load_outlets()
    else:
        source = outlets
    out: Dict[str, tuple] = {}
    for o in source:
        for s in (o.get("release_sections") or {}).get("sections") or []:
            if s.get("rejected_feed"):
                out.setdefault(s["rejected_feed"], (o["id"], s["kind"], s.get("rejected_feed_language"),
                                                    s.get("reason") or "", s.get("rejected_on") or REMOVED_ON))
    if outlets is None:
        _removed = out
    return out


# A rejected section feed that another outlet now polls as its own editorial feed: Asia-Plus's Russian
# site feed, registered on Asia-Plus English (tj_asiaplus_en) as its special projects section, is polled
# from 2026-10-12 by tj_asiaplus, the Russian outlet split from it. The items the section poll stored
# under the registering outlet are the owner's items, so they are moved to it once (_rehome), with their
# outlet-day discovery counts and the feed's poll records, before the gate is run again: the same gate,
# language and feed boilerplate the owner's own polls get. Items the owner polls itself never carry a
# section term or this correction's fail_reason, so nothing below touches them.
_owners: Optional[Dict] = None


def section_feed_owners(outlets: Optional[List[Dict]] = None) -> Dict[str, tuple]:
    """Rejected section feed url: (id, country) of the one other outlet that lists it among its feeds."""
    global _owners
    if outlets is None and _owners is not None:
        return _owners
    if outlets is None:
        from pipeline import registry
        source = registry.load_outlets()
    else:
        source = outlets
    removed = removed_section_feeds(outlets)
    listed: Dict[str, List] = {}
    for o in source:
        for u in o.get("feeds") or []:
            if u in removed and o["id"] != removed[u][0]:
                listed.setdefault(u, []).append((o["id"], o["country"]))
    out = {u: owners[0] for u, owners in listed.items() if len(owners) == 1}
    if outlets is None:
        _owners = out
    return out


def __getattr__(name):
    # REMOVED_SECTION_FEEDS, the name the list had when it was written here, reads the registry's.
    if name == "REMOVED_SECTION_FEEDS":
        return removed_section_feeds()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
# fail_reason of an article this correction gated out. relay.update_existing reads it, so the relay's
# older copy of the item, still tagged with the section, does not make it relevant again.
REASON = "section_feed_removed"


def _terms(raw) -> List:
    try:
        terms = json.loads(raw or "[]") if isinstance(raw, str) else (raw or [])
    except ValueError:
        terms = []
    return terms if isinstance(terms, list) else []


def is_section_term(t) -> bool:
    return isinstance(t, str) and t.startswith("section:")


def decide(row, boiler: Optional[boilerplate.FeedBoilerplate] = None) -> Optional[Dict]:
    """What the gate makes of an article without its section term: {"relevant", "terms", "how"}, or
    None when the article carries no section term. how is "kept_terms" when a China term it already
    had keeps it relevant, "regate" when the keyword gate decided. boiler gives the summary
    boilerplate of the article's feed, which the gate does not read, as in pipeline/fetch_feeds.py;
    run passes one for the whole run."""
    terms = _terms(row["gate_terms"])
    if not any(is_section_term(t) for t in terms):
        return None
    rest = [t for t in terms if not is_section_term(t)]
    weak = gate.weak_terms()
    if any(t not in weak for t in rest):
        return {"relevant": True, "terms": rest, "how": "kept_terms"}
    language = (removed_section_feeds().get(row["feed_url"]) or (None, None, None))[2] or row["language"]
    shingles = boiler.shingles(row["outlet_id"], row["feed_url"]) if boiler is not None else None
    relevant, hits = gate.check(row["title"] or "", row["summary"] or "", language, row["country"], url=row["url"],
                                feed_boilerplate=shingles)
    return {"relevant": bool(relevant), "terms": hits, "how": "regate"}


def _reason(row) -> str:
    removed = removed_section_feeds().get(row["feed_url"]) or (None, "section", None, "", REMOVED_ON)
    kind, on = removed[1], removed[4]
    why = "%s:%s registered as %s, gate rerun on title and summary; was %s" % (REASON, on, kind, row["status"])
    if row["fail_reason"]:
        why += " (%s)" % row["fail_reason"]
    return why


def _empty() -> Dict:
    return {"moved": 0, "moved_relevant": 0, "section_tagged": 0, "kept": 0, "kept_on_terms": 0, "regated_out": 0,
            "labels_withdrawn": {}, "late_labels_withdrawn": 0, "reviewed_regated_out": 0}


def _withdraw(conn, article_id: int, tally: Dict) -> int:
    cats = [r[0] for r in conn.execute(
        "SELECT category FROM classifications WHERE article_id=? AND is_current=1", (article_id,))]
    for c in cats:
        tally[c] = tally.get(c, 0) + 1
    conn.execute("UPDATE classifications SET is_current=0 WHERE article_id=? AND is_current=1", (article_id,))
    return len(cats)


def _lower_discovery(conn, row) -> None:
    keys = row.keys()
    day = (row["day_of_record"] if "day_of_record" in keys else None) or (row["discovered_at"] or "")[:10]
    if not day:
        return
    conn.execute("UPDATE daily_discovery SET gate_relevant=MAX(gate_relevant-1, 0) WHERE date=? AND country=?",
                 (day, row["country"]))
    conn.execute("UPDATE daily_outlet_discovery SET gate_relevant=MAX(gate_relevant-1, 0) WHERE date=? AND outlet_id=?",
                 (day, row["outlet_id"]))


def _rehome(conn, feed: str, owner: tuple, out: Dict, dry_run: bool) -> None:
    """Move the articles the registering outlet stored from `feed` to its owner (section_feed_owners),
    with their outlet-day discovery counts, and the feed's poll records with them. Idempotent: a moved
    article no longer carries the registering outlet."""
    registered = removed_section_feeds()[feed][0]
    oid, country = owner
    rows = conn.execute("SELECT * FROM articles WHERE feed_url=? AND outlet_id=? ORDER BY id", (feed, registered)).fetchall()
    for row in rows:
        t = out.setdefault(feed, _empty())
        t["moved"] += 1
        t["moved_to"] = oid
        rel = 1 if row["gate_relevant"] else 0
        t["moved_relevant"] += rel
        if dry_run:
            continue
        keys = row.keys()
        day = (row["day_of_record"] if "day_of_record" in keys else None) or (row["discovered_at"] or "")[:10]
        conn.execute("UPDATE articles SET outlet_id=?, country=? WHERE id=?", (oid, country, row["id"]))
        conn.execute("""UPDATE daily_outlet_discovery SET discovered=MAX(discovered-1, 0),
                          gate_relevant=MAX(gate_relevant-?, 0) WHERE date=? AND outlet_id=?""", (rel, day, registered))
        conn.execute("""INSERT INTO daily_outlet_discovery(date, outlet_id, country, discovered, gate_relevant) VALUES (?,?,?,1,?)
                        ON CONFLICT(date, outlet_id) DO UPDATE SET discovered=discovered+1,
                          gate_relevant=gate_relevant+excluded.gate_relevant""", (day, oid, country, rel))
        if country != row["country"]:
            conn.execute("""UPDATE daily_discovery SET discovered=MAX(discovered-1, 0),
                              gate_relevant=MAX(gate_relevant-?, 0) WHERE date=? AND country=?""", (rel, day, row["country"]))
            conn.execute("""INSERT INTO daily_discovery(date, country, discovered, gate_relevant) VALUES (?,?,1,?)
                            ON CONFLICT(date, country) DO UPDATE SET discovered=discovered+1,
                              gate_relevant=gate_relevant+excluded.gate_relevant""", (day, country, rel))
    if not dry_run:
        for table in ("feed_polls", "feed_poll_log"):
            conn.execute("UPDATE %s SET outlet_id=?, country=? WHERE feed_url=? AND outlet_id=?" % table,
                         (oid, country, feed, registered))
        conn.execute("UPDATE feed_health SET outlet_id=? WHERE feed_url=? AND outlet_id=?", (oid, feed, registered))


def _rows(conn, feeds) -> List:
    marks = ",".join("?" * len(feeds))
    return conn.execute(
        """SELECT * FROM articles WHERE feed_url IN (%s) AND (gate_terms LIKE '%%"section:%%'
             OR (gate_relevant=0 AND fail_reason LIKE ? AND id IN (SELECT article_id FROM classifications WHERE is_current=1)))
           ORDER BY id""" % marks, list(feeds) + [REASON + ":%"]).fetchall()


def run(conn, feeds=None, dry_run: bool = False) -> Dict:
    """Apply the correction (or, with dry_run, only count it). Returns per feed counts; an empty dict
    when there is nothing left to correct."""
    feeds = list(feeds if feeds is not None else removed_section_feeds())
    out: Dict[str, Dict] = {}
    owners = section_feed_owners()
    for feed in feeds:
        if feed in owners:
            _rehome(conn, feed, owners[feed], out, dry_run)
    # One for the run, computed per outlet on first use. A dry run computes it from the articles and
    # leaves the feed_boilerplate cache alone; on a read only connection nothing is written either way.
    boiler = boilerplate.FeedBoilerplate(conn, cache=not dry_run)
    for row in _rows(conn, feeds):
        t = out.setdefault(row["feed_url"], _empty())
        d = decide(row, boiler)
        if d is None:
            # Gated out by this correction earlier, then labelled by a batch that was already submitted.
            if not dry_run:
                _withdraw(conn, row["id"], t["labels_withdrawn"])
                conn.execute("UPDATE articles SET status='gated_out', llm_pending=0 WHERE id=?", (row["id"],))
            t["late_labels_withdrawn"] += 1
            continue
        t["section_tagged"] += 1
        terms = json.dumps(d["terms"], ensure_ascii=False)
        if d["relevant"] or not row["gate_relevant"]:
            # Stays relevant (its arrival becomes editorial_feed), or was never counted: only the tag goes.
            t["kept"] += 1 if row["gate_relevant"] else 0
            t["kept_on_terms"] += 1 if d["how"] == "kept_terms" else 0
            if not dry_run:
                conn.execute("UPDATE articles SET gate_terms=? WHERE id=?", (terms, row["id"]))
            continue
        t["regated_out"] += 1
        if conn.execute("SELECT 1 FROM human_reviews WHERE article_id=? LIMIT 1", (row["id"],)).fetchone():
            t["reviewed_regated_out"] += 1
        if dry_run:
            for (c,) in conn.execute("SELECT category FROM classifications WHERE article_id=? AND is_current=1", (row["id"],)):
                t["labels_withdrawn"][c] = t["labels_withdrawn"].get(c, 0) + 1
            continue
        _withdraw(conn, row["id"], t["labels_withdrawn"])
        conn.execute("UPDATE articles SET status='gated_out', gate_relevant=0, gate_terms=?, llm_pending=0, fail_reason=? WHERE id=?",
                     (terms, _reason(row), row["id"]))
        _lower_discovery(conn, row)
    if not dry_run:
        conn.commit()
    return out
