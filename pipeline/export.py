"""Rebuild rollups and generate the static JSON the front end reads.

Outputs in docs/data/:
  latest.json          per-country totals, all time and last 30 days, with coverage flags,
                       language support, release section search status and warnings
  daily/YYYY-MM.json   per-day, per-country counts by category and method, state origin by
                       route, feed saturation, the model draw allocation and relay collector hours, and the
                       underlying item ids behind the state origin and unchecked state sourcing counts
  outlets.json         registry with coverage metadata, feed health and release section status
  meta.json            last run, kappa, review coverage, ruleset and schema versions, gaps, and
                       the gates that decide what the interface may publish
  articles/ISO3.json   most recent classified articles per country for the country panel;
                       articles/WLD.json holds the worldwide distribution stratum (press release wires)
  global_series.json   per-day global totals for the sparkline, with the worldwide distribution
                       stratum's own part of each day under wld (zeros when absent)
  planted.json         articles whose byline or cited organisation matches a persona or front named
                       in a published investigation, and the recurring bylines diagnostic; never a
                       category, never a count

A day with an LLM ceiling event is marked in the daily file so a truncated day
is never mistaken for a quiet day, and a day on which the relay collector ran for
too few hours is marked so a sleeping laptop is never mistaken for a quiet country.
"""
import datetime as dt
import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from pipeline import agreement, classify_rules, config, extract, gate, llm_cost, planted, registry, second_rater, store
from pipeline import classify_llm
from pipeline import labels as labels_mod
from pipeline import themes as themes_mod
from pipeline import recall_audit

CATEGORIES = ["A", "B", "C", "not_relevant"]


# Kept for the audit files, which still report how far a feed's published date lagged discovery.
STALE_PUBLISHED_DAYS = 14

ROUTE_LABELS = {
    "wire_credit": "Wire credit line or dateline",
    "distribution_stamp": "Press release distribution stamp",
    "sponsored_disclosure": "Sponsored or partner disclosure",
    "diplomatic_byline": "Signed by a Chinese diplomat",
    "uncredited_copy": "Uncredited copy of a credited state item",
    "press_release_section": "Outlet's press release section",
    "sponsored_section": "Outlet's sponsored content section",
    "partner_section": "Outlet's partner content section",
    "unattributed": "No signature; model judgement",
}
ARRIVAL_LABELS = {
    "editorial_feed": "Editorial feed",
    "press_release_section": "Outlet's press release section",
    "sponsored_section": "Outlet's sponsored content section",
    "partner_section": "Outlet's partner content section",
}
RELEASE_STATUSES = ["found", "none_found", "blocked", "unreachable"]
# What a published unchecked state sourcing count rests on, in the words the interface uses. Since 12 October
# 2026 only hand coding publishes. The two model entries describe the stability check, which is still
# run, recorded and shown, and never unlocks publication: a model agreeing with itself or with a
# sibling licenses "the judgement is stable", never "the labels are right".
RELAY_QUALIFIER = {
    "human_coding": "Checked against hand coding of a fixed random sample, stratified by language and, within each language, by the model's category, at a Cohen's kappa of at least the threshold.",
    "model_vs_model": "A stability check, not a basis for publication: two models judged the same articles, which measures whether they apply the codebook the same way, not whether they apply it correctly.",
    "same_model_rerun": "A stability check, not a basis for publication: the same model judged the same articles twice, with the same prompt, which detects randomness in its own judgement and nothing about a bias it holds every time.",
}
CHANGELOG_RULESET = re.compile(r"^## Ruleset (\S+) \((\d{4}-\d{2}-\d{2})\)", re.MULTILINE)
CHANGELOG_GATE = re.compile(r"^## Gate (\S+) \((\d{4}-\d{2}-\d{2})\)", re.MULTILINE)


def _day(row) -> str:
    """The day of record is the UTC day the item was discovered, for every figure.

    It used to be the published date where the feed gave one within STALE_PUBLISHED_DAYS,
    falling back to discovery. That put the numerator of every share on the publication
    axis while its denominator stayed on the discovery axis, because discovery totals are
    written at insert time and are the only per day totals that include gated out items.
    Measured on 2026-09-21, 12.9 percent of relevant articles were attributed to a day other
    than their discovery, and on 1,033 of 2,857 country days the numerator exceeded its own
    denominator. It also manufactured 14 days of history before collection began: the
    published timeline started 2026-08-20 with real article counts and no discovery at all,
    which read as a ramp up that never happened.

    Discovery is also the only date the instrument observes. A publication date is whatever
    the feed asserts, it is missing or wrong often enough to matter, and an item published
    before the tracker existed was still not observed then. Articles keep their published_at
    in the article records and in the audit files, so publication lag stays analysable.

    One exception, decided at insert time and stored in articles.day_of_record: an item a backfill
    read brought in (the AAP release reader's first run reads back weeks) counts on its publication
    day when it was discovered more than config.BACKFILL_LAG_HOURS after it was published, on or after
    config.COLLECTION_START. Its discovery is counted on the same day (store.backfill_day,
    store.record_discovery), so numerator and denominator move together."""
    keys = row.keys()
    if "day_of_record" in keys and row["day_of_record"]:
        return row["day_of_record"]
    return (row["discovered_at"] or "")[:10]


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def article_rows(conn) -> List[Dict]:
    """One row per article with its current machine label and its latest human label."""
    rows = conn.execute(
        """SELECT a.id, a.country, a.outlet_id, a.language, a.title, a.url, a.published_at, a.discovered_at, a.day_of_record,
                  a.status, a.gate_relevant, a.dup_group_id, a.fail_reason, a.themes, a.gate_terms,
                  c.category AS m_cat, c.method AS m_method, c.confidence AS m_conf, c.evidence_quote,
                  c.reasoning, c.signatures_fired, c.classified_at, c.ruleset_version, c.model_version, c.route,
                  (SELECT human_category FROM human_reviews h WHERE h.article_id=a.id ORDER BY h.id DESC LIMIT 1) AS h_cat
           FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1"""
    ).fetchall()
    return [dict(r) for r in rows]


def rebuild_rollups(conn, outlets: Optional[List[Dict]] = None) -> Dict:
    store.rebuild_daily_discovery(conn)
    rows = article_rows(conn)
    now = store.utcnow()
    outlets = outlets if outlets is not None else registry.load_outlets()
    top_ids, _ = registry.top_outlets(outlets)
    wire_ids = registry.distribution_wire_ids(outlets)
    wires = {"articles": 0, "A": 0, "B": 0, "C": 0, "not_relevant": 0, "pending": 0}
    # Underlying items: title groups joined through body text links (pipeline.body_dups).
    from pipeline import body_dups
    items = body_dups.item_groups(conn)
    counts = defaultdict(lambda: {"n": 0, "n_rules": 0, "n_llm": 0, "n_reviewed": 0, "groups": set()})
    coverage = defaultdict(lambda: {"discovered": 0, "gate_relevant": 0, "fetched": 0, "paywalled": 0,
                                    "failed": 0, "blocked_robots": 0, "classified": 0, "llm_pending": 0,
                                    "top_discovered": 0, "top_target": 0, "top_china": 0, "top_a": 0, "top_b": 0})
    for r in rows:
        d = _day(r)
        # A global release service is not a national newsroom, so its items are never inside a
        # country's numerator or denominator. They are counted under the worldwide distribution
        # stratum, WLD, which the interface lists beside the countries and draws nowhere on the map.
        country = r["country"]
        if r["outlet_id"] in wire_ids:
            country = registry.WORLDWIDE
            wires["articles"] += 1
            if r["status"] in ("awaiting_llm", "llm_submitted"):
                wires["pending"] += 1
            elif r["m_cat"]:
                wires[r["m_cat"]] += 1
        cov = coverage[(d, country)]
        cov["gate_relevant"] += 1
        st = r["status"]
        if r["outlet_id"] in top_ids.get(country, set()):
            pending = st in ("awaiting_llm", "llm_submitted")
            if pending or r["m_cat"] in ("A", "B"):
                cov["top_target"] += 1
            if pending or r["m_cat"] in ("A", "B", "C"):
                cov["top_china"] += 1
            if r["m_cat"] == "A":
                cov["top_a"] += 1
            if r["m_cat"] == "B":
                cov["top_b"] += 1
        if st in ("fetched", "classified", "awaiting_llm", "llm_submitted"):
            cov["fetched"] += 1
        if st == "paywalled":
            cov["paywalled"] += 1
        elif st == "failed":
            cov["failed"] += 1
        elif st == "blocked_robots":
            cov["blocked_robots"] += 1
        if st in ("awaiting_llm", "llm_submitted"):
            cov["llm_pending"] += 1
        if r["m_cat"]:
            cov["classified"] += 1
            key = (d, country, r["m_cat"], "all")
            c = counts[key]
            c["n"] += 1
            c["n_rules" if r["m_method"] == "rules" else "n_llm"] += 1
            c["groups"].add(items.get(r["dup_group_id"] or r["id"], r["dup_group_id"] or r["id"]))
            if r["h_cat"]:
                c["n_reviewed"] += 1
                key2 = (d, country, r["h_cat"], "reviewed")
                c2 = counts[key2]
                c2["n"] += 1
                c2["n_reviewed"] += 1
                c2["groups"].add(items.get(r["dup_group_id"] or r["id"], r["dup_group_id"] or r["id"]))
    # discovered totals per day and country include gated-out items. They come from the
    # permanent daily_discovery table, because gated-out rows are pruned from articles.
    # A distribution wire's releases are not part of any country's output, so they are left out of the
    # discovery denominators as well as the numerators; counting them understated every US share. The
    # per outlet table is keyed by the registry country, so the stratum's own denominator, everything
    # the wires themselves published, is derived here from the wire outlet ids rather than in the store.
    for r in conn.execute("SELECT date, outlet_id, country, discovered FROM daily_outlet_discovery"):
        country = registry.WORLDWIDE if r["outlet_id"] in wire_ids else r["country"]
        coverage[(r["date"], country)]["discovered"] += r["discovered"]
        if r["outlet_id"] in top_ids.get(country, set()):
            coverage[(r["date"], country)]["top_discovered"] += r["discovered"]

    conn.execute("DELETE FROM daily_counts")
    conn.execute("DELETE FROM daily_coverage")
    for (d, country, cat, scope), c in counts.items():
        conn.execute(
            """INSERT INTO daily_counts(date, country, category, scope, n, n_rules, n_llm, n_reviewed, n_unique_items, computed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (d, country, cat, scope, c["n"], c["n_rules"], c["n_llm"], c["n_reviewed"], len(c["groups"]), now),
        )
    for (d, country), cov in coverage.items():
        conn.execute(
            """INSERT INTO daily_coverage(date, country, discovered, gate_relevant, fetched, paywalled, failed,
               blocked_robots, classified, llm_pending, computed_at, top_discovered, top_target, top_china, top_a, top_b)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d, country, cov["discovered"], cov["gate_relevant"], cov["fetched"], cov["paywalled"], cov["failed"],
             cov["blocked_robots"], cov["classified"], cov["llm_pending"], now,
             cov["top_discovered"], cov["top_target"], cov["top_china"], cov["top_a"], cov["top_b"]),
        )
    conn.commit()
    # The wires block equals the WLD stratum's totals: it is kept under its old name so nothing that
    # reads it changes, and the stratum is the same figure placed beside the countries.
    return {"daily_counts": len(counts), "daily_coverage": len(coverage), "articles": len(rows),
            "distribution_wire_articles": wires["articles"], "distribution_wires": wires}


def underlying_item_ids(conn, outlets: Optional[List[Dict]] = None, rows: Optional[List[Dict]] = None) -> Dict:
    """Per day and country, the underlying item ids behind the state origin (A) and unchecked state
    sourcing (B) counts: {day: {country: {"A": set, "B": set}}}, wires under the worldwide stratum.

    daily_counts.n_unique_items is distinct within one day and country only, so summing it over a window
    or across countries counted a story placed on several days, or in several countries, once for each.
    A window's figure is the size of the union of these sets instead, taken in the export for latest.json
    and in the browser from the ids the daily files carry. Items are counted per label: an item with a
    state origin placement and an unchecked state sourcing placement is in both sets."""
    from pipeline import body_dups
    items = body_dups.item_groups(conn)
    wire_ids = registry.distribution_wire_ids(outlets if outlets is not None else registry.load_outlets())
    out: Dict = defaultdict(lambda: defaultdict(lambda: {"A": set(), "B": set()}))
    for r in (rows if rows is not None else article_rows(conn)):
        if r["m_cat"] not in ("A", "B"):
            continue
        country = registry.WORLDWIDE if r["outlet_id"] in wire_ids else r["country"]
        g = r["dup_group_id"] or r["id"]
        out[_day(r)][country][r["m_cat"]].add(items.get(g, g))
    return out


def _distinct_items(item_ids: Dict, since: Optional[str] = None) -> Tuple[Dict[str, Dict[str, set]], Dict[str, set]]:
    """Unions of underlying_item_ids over the days from `since` (all days when None): per country, and
    for the world across every country and the worldwide stratum."""
    per = defaultdict(lambda: {"A": set(), "B": set()})
    world = {"A": set(), "B": set()}
    for d, per_country in item_ids.items():
        if since and d < since:
            continue
        for country, ab in per_country.items():
            for cat in ("A", "B"):
                per[country][cat] |= ab[cat]
                world[cat] |= ab[cat]
    return per, world


def _set_unique(target: Dict, ab: Dict[str, set]) -> None:
    """uniqA is the distinct state origin items, uniqAB the distinct items counted per label, so that
    uniqAB less uniqA is the distinct unchecked state sourcing items, as the interface reads it."""
    target["uniqA"] = len(ab["A"])
    target["uniqAB"] = len(ab["A"]) + len(ab["B"])


def _empty_country() -> Dict:
    return {"A": 0, "B": 0, "C": 0, "N": 0, "Ar": 0, "Al": 0, "Ah": 0, "Br": 0, "Bl": 0, "Bh": 0,
            "rev": 0, "cls": 0, "disc": 0, "rel": 0, "fetched": 0, "paywalled": 0, "failed": 0,
            "blocked": 0, "pending": 0, "uniqA": 0, "uniqAB": 0, "tdisc": 0, "ttarget": 0, "tchina": 0, "ta": 0, "tb": 0,
            "polls": 0, "sat": 0, "miss": 0, "mpolls": 0}


def _accumulate(target: Dict, cat: str, scope: str, row) -> None:
    if scope != "all":
        return
    short = {"A": "A", "B": "B", "C": "C", "not_relevant": "N"}[cat]
    target[short] += row["n"]
    target["cls"] += row["n"]
    target["rev"] += row["n_reviewed"]
    if cat in ("A", "B"):
        target[cat + "r"] += row["n_rules"]
        target[cat + "l"] += row["n_llm"]
        target[cat + "h"] += row["n_reviewed"]
        target["uniqAB"] += row["n_unique_items"]
        if cat == "A":
            target["uniqA"] += row["n_unique_items"]


# The estimated items missed and the polls that estimate covers, from feed_polls rows made by the current
# missed-items estimator only (config.MISSED_ESTIMATOR_VERSION). Polls and saturated polls are counted
# over every row: they were measured, while an older row's estimate inflated batch publishers and short
# windows and cannot be made again.
_MISS_SQL = ("SUM(CASE WHEN estimator=:est THEN missed_estimate ELSE 0 END) miss, "
             "SUM(CASE WHEN estimator=:est THEN polls ELSE 0 END) mpolls")


def missed_estimate_since(conn) -> Optional[str]:
    """The first day on record with polls estimated by the current missed-items estimator, or None."""
    return conn.execute("SELECT MIN(date) FROM feed_polls WHERE estimator=?",
                        (config.MISSED_ESTIMATOR_VERSION,)).fetchone()[0]


def missed_text(miss: int, mpolls: int, polls: int, since: Optional[str]) -> str:
    """The estimated items missed, worded with the polls it covers and the day it starts."""
    if not mpolls:
        return "no estimate of the items missed yet (estimated since %s)" % (since or config.MISSED_ESTIMATOR_VERSION)
    covered = "" if mpolls >= polls else " over %d of these polls" % mpolls
    return "an estimated %d were missed%s (estimated since %s)" % (miss, covered, since or config.MISSED_ESTIMATOR_VERSION)


def _feed_poll_days(conn):
    """Per day and country: feed polls, saturated polls, the estimated items missed between polls and
    the polls that estimate covers (mpolls; see _MISS_SQL)."""
    return conn.execute(
        """SELECT date, country, SUM(polls) polls, SUM(saturated) sat, %s
           FROM feed_polls GROUP BY date, country""" % _MISS_SQL, {"est": config.MISSED_ESTIMATOR_VERSION}
    ).fetchall()


def _reviewed_block(conn, country: Optional[str], since: Optional[str]) -> Dict:
    q = "SELECT category, SUM(n) n FROM daily_counts WHERE scope='reviewed'"
    params = []
    if country:
        q += " AND country=?"; params.append(country)
    if since:
        q += " AND date>=?"; params.append(since)
    q += " GROUP BY category"
    out = {"A": 0, "B": 0, "C": 0, "N": 0}
    for r in conn.execute(q, params):
        out[{"A": "A", "B": "B", "C": "C", "not_relevant": "N"}[r["category"]]] = r["n"]
    return out


def _wire_counts(conn, outlets: List[Dict]) -> Dict:
    ids = registry.distribution_wire_ids(outlets)
    out = {"articles": 0, "A": 0, "B": 0, "C": 0, "not_relevant": 0, "pending": 0}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    for r in conn.execute(
            """SELECT a.status, c.category cat, COUNT(*) n FROM articles a
               LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
               WHERE a.gate_relevant=1 AND a.outlet_id IN (%s) GROUP BY a.status, c.category""" % marks,
            tuple(sorted(ids))):
        out["articles"] += r["n"]
        if r["status"] in ("awaiting_llm", "llm_submitted"):
            out["pending"] += r["n"]
        elif r["cat"]:
            out[r["cat"]] += r["n"]
    return out


def relay_visible(conn) -> bool:
    """Whether unchecked state sourcing may appear in a published figure at all: either the hand coding
    study has cleared it, or it has not reported and the counts are shown marked provisional. The
    interface and the files it reads must make this decision the same way, in one place."""
    labels = conn.execute("SELECT COUNT(*) FROM classifications WHERE method='llm' AND is_current=1").fetchone()[0]
    if not labels:
        return False
    human = agreement.human_study_status(conn)
    if human["passed"]:
        return True
    return bool(config.RELAY_PROVISIONAL_DISPLAY and human["state"] != "failed" and not _relay_unstable(conn))


def _relay_unstable(conn) -> bool:
    """A stability check whose kappa fell below the threshold: the model does not agree with itself, or
    with a second model, so its counts are withheld rather than shown provisional. A passing check
    publishes nothing; only hand coding does. A check made under an older classifier prompt is history
    and withholds nothing (second_rater.study_on_current_prompt)."""
    model = store.latest_model_agreement(conn)
    return bool(model and second_rater.study_on_current_prompt(model)
                and model["kappa_bc"] is not None and model["kappa_bc"] < config.KAPPA_WARNING_THRESHOLD)


def _derive(c: Dict, outlets_active: int, relay_visible: bool = True) -> Dict:
    """Per country derived figures. Anything containing unchecked state sourcing is omitted when that
    label may not be shown, because a share published beside the state origin count and the China
    total gives the withheld number back by subtraction."""
    china = c["A"] + c["B"] + c["C"]
    c["china_total"] = china
    c["share_a"] = round(c["A"] / china, 4) if china else None
    c["per_outlet_a"] = round(c["A"] / outlets_active, 3) if outlets_active else None
    c["share_ab"] = (round((c["A"] + c["B"]) / china, 4) if china else None) if relay_visible else None
    c["per_outlet_ab"] = (round((c["A"] + c["B"]) / outlets_active, 3) if outlets_active else None) if relay_visible else None
    attempted = c["fetched"] + c["paywalled"] + c["failed"] + c["blocked"]
    # Paywalled articles are never classified, so they sit in no count and no denominator.
    c["paywall_share"] = round(c["paywalled"] / attempted, 4) if attempted else None
    c["reviewed_share"] = round(c["rev"] / c["cls"], 4) if c["cls"] else None
    c["share_of_all_target"] = round(c["ttarget"] / c["tdisc"], 5) if c["tdisc"] else None
    c["share_of_all_china"] = round(c["tchina"] / c["tdisc"], 5) if c["tdisc"] else None
    c["share_of_all_a"] = round(c["ta"] / c["tdisc"], 5) if c["tdisc"] else None
    c["saturation_share"] = round(c.get("sat", 0) / c["polls"], 4) if c.get("polls") else None
    return c


# ---------------------------------------------------------------------------
# Relay collector heartbeat, language support, release sections, ruleset history
# ---------------------------------------------------------------------------

def relay_hours_by_day(conn) -> Dict[str, int]:
    """Distinct clock hours per UTC day in which the relay collector completed a discovery pass."""
    hours = defaultdict(set)
    for r in conn.execute("SELECT started_at FROM relay_runs WHERE ok=1"):
        hours[r["started_at"][:10]].add(r["started_at"][11:13])
    return {d: len(h) for d, h in hours.items()}


def relay_incomplete_days(conn, today: Optional[str] = None) -> List[str]:
    """Complete UTC days, from the first full day with a heartbeat, on which the relay collector
    ran in fewer than RELAY_DAY_MIN_HOURS hours. The first day is skipped because the bundle's
    seven-day window usually cuts it."""
    by_day = relay_hours_by_day(conn)
    if not by_day:
        return []
    today = dt.date.fromisoformat(today or _today())
    d = dt.date.fromisoformat(min(by_day)) + dt.timedelta(days=1)
    out = []
    while d < today:
        if by_day.get(d.isoformat(), 0) < config.RELAY_DAY_MIN_HOURS:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def relay_status(conn, outlets: List[Dict]) -> Dict:
    """outlets and countries are the relay only outlets, which no other collector polls; a silent relay
    leaves them uncollected. shared_outlets and shared_countries are the collector: both outlets, which
    the hosted runner still polls on its own hourly schedule when the relay is silent."""
    relayed = [o for o in outlets if o.get("active") and registry.collector_of(o) == "self_hosted"]
    shared = [o for o in outlets if o.get("active") and registry.is_shared(o)]
    last = conn.execute("SELECT MAX(COALESCE(finished_at, started_at)) FROM relay_runs WHERE ok=1").fetchone()[0]
    hours = stale = None
    if last:
        t = dt.datetime.fromisoformat(last)
        t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        hours = round((dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600.0, 1)
        stale = hours >= config.RELAY_STALE_HOURS
    return {"outlets": len(relayed), "countries": sorted({o["country"] for o in relayed}), "last_run": last,
            "shared_outlets": len(shared), "shared_countries": sorted({registry.export_country(o) for o in shared}),
            "hours_since_last_run": hours, "stale": stale, "incomplete_days": relay_incomplete_days(conn),
            "min_hours_per_day": config.RELAY_DAY_MIN_HOURS, "stale_after_hours": config.RELAY_STALE_HOURS}


def language_support(languages, supported) -> str:
    """full when every language has its own term list, none when no language does. English is
    always supported, because every language is also matched on the English terms."""
    langs = [l for l in languages if l]
    if not langs:
        return "none"
    n = sum(1 for l in langs if l == "en" or l in supported)
    return "full" if n == len(langs) else ("partial" if n else "none")


def release_summary(outlets: List[Dict], active: Optional[List[Dict]] = None) -> Dict:
    """Active outlets by the status of the search for press release, sponsored and partner sections.
    `active` is the list to summarise when it is not the registry's own idea of active, as for the
    worldwide distribution stratum, whose outlets are the wires."""
    active = registry.active_outlets(outlets) if active is None else active
    out = {s: 0 for s in RELEASE_STATUSES}
    out["not_searched"] = 0
    for o in active:
        st = (o.get("release_sections") or {}).get("status")
        out[st if st in RELEASE_STATUSES else "not_searched"] += 1
    out["section_feeds"] = sum(len(o.get("section_feeds") or []) for o in active)
    out["outlets_active"] = len(active)
    return out


def _arrival_totals(conn) -> Dict[str, int]:
    """Current state origin labels by where the article was collected."""
    out = defaultdict(int)
    for r in conn.execute(
        """SELECT a.gate_terms FROM classifications c JOIN articles a ON a.id=c.article_id
           WHERE c.is_current=1 AND c.category='A'"""
    ):
        out[classify_rules.arrival_of(r["gate_terms"])] += 1
    return dict(out)


def _changelog_changes(pattern, path: Path = None) -> List[Dict]:
    path = path or (config.ROOT / "CHANGELOG.md")
    if not path.exists():
        return []
    found = pattern.findall(path.read_text(encoding="utf-8"))
    return sorted(({"version": v, "date": d} for v, d in found), key=lambda x: x["date"])


def ruleset_changes(path: Path = None) -> List[Dict]:
    """Dates the signature ruleset changed, so the timeline can mark a relabelling."""
    return _changelog_changes(CHANGELOG_RULESET, path)


def gate_changes(path: Path = None) -> List[Dict]:
    """Dates the relevance gate changed. A gate change moves the boundary of what is collected and
    applies only to items discovered after it, because gate decisions are never revisited."""
    return _changelog_changes(CHANGELOG_GATE, path)


def _country_top_carriers(conn, outlets: List[Dict], since30: str, until: Optional[str] = None) -> Dict[str, Dict]:
    """Per country, the outlet and the route that carried most of its state origin count on the days
    since30 <= day < until (no end when until is None), so a map tooltip can say what carried the number.
    build_latest passes the 30 complete days of complete_window_30d, the window the map opens on, so the
    partial export day is in neither the count nor the figure beside it. Distribution wires are
    counted under the worldwide distribution stratum, exactly as every country figure places them.
    A route with no signature at all is not a route (see route_signature_totals in build_meta) and
    is never reported here as one."""
    wire_ids = registry.distribution_wire_ids(outlets)
    outlet_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    route_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in conn.execute(
            """SELECT a.country, a.outlet_id, c.route FROM articles a JOIN classifications c
               ON c.article_id=a.id AND c.is_current=1
               WHERE c.category='A' AND COALESCE(a.day_of_record, substr(a.discovered_at, 1, 10))>=?
               AND COALESCE(a.day_of_record, substr(a.discovered_at, 1, 10))<?""", (since30, until or "9999-12-31")):
        country = registry.WORLDWIDE if r["outlet_id"] in wire_ids else r["country"]
        outlet_counts[country][r["outlet_id"]] += 1
        route_counts[country][r["route"] or "unattributed"] += 1
    names = {o["id"]: o["name"] for o in outlets}
    out = {}
    for country in set(outlet_counts) | set(route_counts):
        oc = outlet_counts.get(country, {})
        rc = dict(route_counts.get(country, {}))
        rc.pop("unattributed", None)
        top_o = max(oc, key=lambda k: (oc[k], k)) if oc else None
        top_r = max(rc, key=lambda k: (rc[k], k)) if rc else None
        out[country] = {
            "top_carrier": {"id": top_o, "name": names.get(top_o, top_o), "count": oc[top_o]} if top_o else None,
            "top_route": {"id": top_r, "count": rc[top_r]} if top_r else None,
        }
    return out


def build_latest(conn, outlets: List[Dict], gaps: List[Dict], population: Optional[Dict] = None) -> Dict:
    visible = relay_visible(conn)
    population = population if population is not None else registry.load_population()
    top_ids, ranked = registry.top_outlets(outlets)
    today = dt.datetime.now(dt.timezone.utc).date()
    since30 = (today - dt.timedelta(days=30)).isoformat()
    # Distribution wires are grouped under WLD, the worldwide distribution stratum, never under the
    # country they are registered in.
    by_country_outlets = defaultdict(list)
    for o in outlets:
        by_country_outlets[registry.export_country(o)].append(o)
    feed_health = {r["feed_url"]: dict(r) for r in conn.execute("SELECT * FROM feed_health")}
    gate_langs = gate.supported_languages()
    theme_langs = themes_mod.languages()
    relay = relay_status(conn, outlets)
    relay_inc30 = [d for d in relay["incomplete_days"] if d >= since30]
    carriers = _country_top_carriers(conn, outlets, since30, today.isoformat())

    all_time = defaultdict(_empty_country)
    last30 = defaultdict(_empty_country)
    for r in conn.execute("SELECT * FROM daily_counts"):
        _accumulate(all_time[r["country"]], r["category"], r["scope"], r)
        if r["date"] >= since30:
            _accumulate(last30[r["country"]], r["category"], r["scope"], r)
    for r in conn.execute("SELECT * FROM daily_coverage"):
        for target, ok in ((all_time, True), (last30, r["date"] >= since30)):
            if not ok:
                continue
            t = target[r["country"]]
            t["disc"] += r["discovered"]; t["rel"] += r["gate_relevant"]; t["fetched"] += r["fetched"]
            t["paywalled"] += r["paywalled"]; t["failed"] += r["failed"]; t["blocked"] += r["blocked_robots"]
            t["pending"] += r["llm_pending"]
            t["tdisc"] += r["top_discovered"]; t["ttarget"] += r["top_target"]; t["tchina"] += r["top_china"]; t["ta"] += r["top_a"]; t["tb"] += r["top_b"]
    miss_since = missed_estimate_since(conn)
    for r in _feed_poll_days(conn):
        for target, ok in ((all_time, True), (last30, r["date"] >= since30)):
            if ok:
                t = target[r["country"]]
                t["polls"] += r["polls"] or 0; t["sat"] += r["sat"] or 0; t["miss"] += int(round(r["miss"] or 0))
                t["mpolls"] += r["mpolls"] or 0
    # Underlying items are distinct over the whole window, not summed day by day.
    item_ids = underlying_item_ids(conn, outlets)
    unique_at, world_at = _distinct_items(item_ids)
    unique_30, world_30 = _distinct_items(item_ids, since30)
    for country, ab in unique_at.items():
        _set_unique(all_time[country], ab)
    for country, ab in unique_30.items():
        _set_unique(last30[country], ab)

    countries = {}
    for country, os_ in by_country_outlets.items():
        worldwide = country == registry.WORLDWIDE
        active = registry.active_wires(os_) if worldwide else registry.active_outlets(os_)
        # Feed health counts editorial feeds only. A quiet or unreadable sponsored section is not a failing feed,
        # so section feeds are reported beside them and never trip the failing feeds warning.
        entries = [f for o in active for f in registry.feed_entries(o)]
        feeds = [f["url"] for f in entries if f.get("kind", registry.EDITORIAL) == registry.EDITORIAL]
        section_urls = [f["url"] for f in entries if f.get("kind", registry.EDITORIAL) != registry.EDITORIAL]
        feeds_ok = sum(1 for f in feeds if feed_health.get(f) and feed_health[f]["consecutive_failures"] == 0)
        languages = sorted({o["language"] for o in active})
        relayed = sum(1 for o in active if registry.collector_of(o) == "self_hosted")
        shared = sum(1 for o in active if registry.is_shared(o))
        entry = {
            "coverage": "monitored" if active else "no_active_outlets",
            "outlets_total": len(os_), "outlets_active": len(active),
            "feeds_total": len(feeds), "feeds_ok": feeds_ok,
            "section_feeds_total": len(section_urls),
            "section_feeds_ok": sum(1 for f in section_urls if feed_health.get(f) and feed_health[f]["consecutive_failures"] == 0),
            # The stratum has no population.
            "population": None if worldwide else population.get(country),
            "top_outlets": len(top_ids.get(country, ())), "top_outlets_ranked": country in ranked,
            "languages": languages,
            # Every release is matched on its own language and on English, and the stratum is not a
            # country whose press could be unreadable, so its support is recorded as full.
            "language_support": "full" if worldwide else language_support(languages, gate_langs),
            "theme_language_support": "full" if worldwide else language_support(languages, theme_langs),
            "relay_outlets": relayed, "shared_outlets": shared,
            "release_sections": release_summary(os_, active if worldwide else None),
            "top_carrier": carriers.get(country, {}).get("top_carrier"),
            "top_route": carriers.get(country, {}).get("top_route"),
            "all_time": _derive(dict(all_time[country]), len(active), visible),
            "last_30d": _derive(dict(last30[country]), len(active), visible),
            "reviewed_all_time": _reviewed_block(conn, country, None),
            "reviewed_last_30d": _reviewed_block(conn, country, since30),
            "warnings": [],
        }
        at = entry["all_time"]
        if feeds and (len(feeds) - feeds_ok) / float(len(feeds)) >= config.FEED_FAILURE_WARNING_SHARE:
            entry["warnings"].append({"type": "feeds_failing", "text": "%d of %d feeds are failing" % (len(feeds) - feeds_ok, len(feeds))})
        pw = at["paywall_share"]
        if pw is not None and pw >= config.PAYWALL_FLAG_SHARE and at["rel"] >= 10:
            entry["warnings"].append({"type": "paywalled", "text": "%d percent of retrieved articles were paywalled and could not be read. They are missing from every count, but they stay in the share of monitored output denominator, which counts everything the outlets published, so that share is a lower bound here and counts are not comparable to other countries" % round(pw * 100)})
        attempted = at["fetched"] + at["paywalled"] + at["failed"] + at["blocked"]
        if attempted >= 10 and (at["failed"] + at["blocked"]) / float(attempted) >= config.PAYWALL_FLAG_SHARE:
            entry["warnings"].append({"type": "fetch_failing", "text": "%d percent of article fetches failed or were blocked by robots.txt; counts understate this country" % round(100.0 * (at["failed"] + at["blocked"]) / attempted)})
        pending = at["pending"]
        judged_china = at["A"] + at["B"] + at["C"]
        if pending >= 5 and pending >= 0.25 * max(at["rel"], 1):
            entry["warnings"].append({"type": "llm_backlog", "text": "%d articles carrying official Chinese sourcing are awaiting the verification judgement, so the unchecked state sourcing count is a floor" % pending})
        # A country whose unjudged pile rivals what has been judged cannot be compared with one whose
        # backlog is small: the difference between them is partly the backlog, not the world. This is
        # the state the whole map is in while the model budget is spent.
        if pending >= 5 and pending >= 0.5 * max(judged_china, 1):
            entry["warnings"].append({"type": "not_comparable", "text": "%d articles are still unjudged against %d judged, so this country's unchecked state sourcing figure is not comparable with countries whose backlog is smaller" % (pending, judged_china)})
        if at["polls"] >= 10 and at["sat"] / float(at["polls"]) >= config.FEED_SATURATION_WARNING_SHARE:
            entry["warnings"].append({"type": "feed_saturation", "text": "%d percent of feed polls came back as a full window with nothing seen before, so items were lost between polls; %s" % (round(100.0 * at["sat"] / at["polls"]), missed_text(at["miss"], at["mpolls"], at["polls"], miss_since))})
        # Only relay only outlets go uncollected while the relay is silent. Shared outlets are still
        # polled by the hosted runner, so their counts are not incomplete, only exposed to its gaps.
        if relayed:
            if relay["stale"]:
                entry["warnings"].append({"type": "relay_stale", "text": "%d of this country's outlets are collected only from the owner's machine, which has not collected for %.0f hours; recent counts for them are incomplete" % (relayed, relay["hours_since_last_run"])})
            if relay_inc30:
                entry["warnings"].append({"type": "relay_incomplete", "text": "%d of this country's outlets are collected only from the owner's machine, which ran in fewer than %d hours on %d of the last 30 days; counts on those days are incomplete" % (relayed, config.RELAY_DAY_MIN_HOURS, len(relay_inc30))})
        if shared and relay["stale"]:
            entry["warnings"].append({"type": "relay_stale_shared", "text": "%d of this country's outlets are also polled from the owner's machine, which has not collected for %.0f hours; they fall back to the hosted runner's hourly polls alone, which GitHub often runs late, so their short feeds may lose items between polls" % (shared, relay["hours_since_last_run"])})
        if active and entry["language_support"] == "none":
            entry["warnings"].append({"type": "unsupported_language", "text": "no keyword list exists for %s, so only international and English terms are matched; an empty map here says little" % ", ".join(languages)})
        if not active:
            entry["warnings"].append({"type": "no_active_outlets", "text": "all registered outlets are inactive"})
        countries[country] = entry
    for g in gaps:
        if g["country"] not in countries:
            countries[g["country"]] = {"coverage": "gap", "gap_reason": g["reason"], "outlets_total": 0, "outlets_active": 0,
                                       "feeds_total": 0, "feeds_ok": 0, "population": population.get(g["country"]),
                                       "top_outlets": 0, "top_outlets_ranked": False, "languages": [], "language_support": "none",
                                       "theme_language_support": "none", "relay_outlets": 0, "shared_outlets": 0, "release_sections": release_summary([]),
                                       "top_carrier": None, "top_route": None,
                                       "all_time": _derive(_empty_country(), 0, visible), "last_30d": _derive(_empty_country(), 0, visible), "warnings": []}

    # World totals include the worldwide distribution stratum: its items reached audiences. Its
    # outlets are not in the per outlet denominator, which counts the newsrooms monitored.
    n_active = len(registry.active_outlets(outlets))
    totals = {"all_time": _derive(_empty_country(), n_active, visible), "last_30d": _derive(_empty_country(), n_active, visible)}
    for scope_key, src in (("all_time", all_time), ("last_30d", last30)):
        t = totals[scope_key]
        for c in src.values():
            for k in _empty_country():
                t[k] += c[k]
        # A story placed in several countries is one underlying item in the world figure.
        _set_unique(t, world_at if scope_key == "all_time" else world_30)
        _derive(t, n_active, visible)
    totals["population"] = sum(v["population"] or 0 for v in countries.values() if v["coverage"] == "monitored")
    return {"generated_at": store.utcnow(), "window_start_30d": since30, "countries": countries, "totals": totals}


def build_daily(conn, outlets: Optional[List[Dict]] = None) -> Dict[str, Dict]:
    months = defaultdict(lambda: {"days": defaultdict(lambda: defaultdict(_empty_country))})
    for r in conn.execute("SELECT * FROM daily_counts"):
        d = r["date"]
        _accumulate(months[d[:7]]["days"][d][r["country"]], r["category"], r["scope"], r)
    for r in conn.execute("SELECT * FROM daily_coverage"):
        d = r["date"]
        t = months[d[:7]]["days"][d][r["country"]]
        t["disc"] += r["discovered"]; t["rel"] += r["gate_relevant"]; t["fetched"] += r["fetched"]
        t["paywalled"] += r["paywalled"]; t["failed"] += r["failed"]; t["blocked"] += r["blocked_robots"]
        t["pending"] += r["llm_pending"]
        t["tdisc"] += r["top_discovered"]; t["ttarget"] += r["top_target"]; t["tchina"] += r["top_china"]; t["ta"] += r["top_a"]; t["tb"] += r["top_b"]
    for r in _feed_poll_days(conn):
        t = months[r["date"][:7]]["days"][r["date"]][r["country"]]
        t["polls"] += r["polls"] or 0; t["sat"] += r["sat"] or 0; t["miss"] += int(round(r["miss"] or 0))
        t["mpolls"] += r["mpolls"] or 0
    ceiling_days = {r["date"] for r in conn.execute("SELECT date FROM llm_usage WHERE ceiling_hit=1")}
    reviewed = defaultdict(lambda: defaultdict(lambda: {"A": 0, "B": 0, "C": 0, "N": 0}))
    for r in conn.execute("SELECT * FROM daily_counts WHERE scope='reviewed'"):
        reviewed[r["date"]][r["country"]][{"A": "A", "B": "B", "C": "C", "not_relevant": "N"}[r["category"]]] += r["n"]
    sampling = defaultdict(dict)
    for r in conn.execute("SELECT date, country, eligible, drawn FROM llm_sampling"):
        sampling[r["date"]][r["country"]] = [r["eligible"], r["drawn"]]
    relay_hours = relay_hours_by_day(conn)
    relay_incomplete = set(relay_incomplete_days(conn))
    # Theme counts per day and country: theme id -> [all China coverage, combined slot, state origin,
    # unchecked state sourcing]. The combined slot (state origin, unchecked state sourcing and candidates
    # still waiting for the model) is kept so the file layout does not change; no measure reads it.
    # Only China coverage counts (state origin, relay, independent, and pending candidates). An article
    # carries every theme it matches, so a country's theme counts can sum to more than its articles.
    theme_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0])))
    # State origin per day and country by the route it arrived by, and labels still carrying an older ruleset.
    routes = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    arrivals = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    older_ruleset = defaultdict(int)
    # Model labels made under an older classifier prompt (classify_llm.PROMPT_VERSION), still current
    # while they wait to be judged again; marked on the timeline like labels on an older ruleset.
    older_prompt = defaultdict(int)
    wire_ids = registry.distribution_wire_ids(outlets if outlets is not None else registry.load_outlets())
    rows = article_rows(conn)
    # The ids behind each day's underlying item counts, so the browser can count each item once over any
    # window and across countries; summing the per day figures counted a story once per day it appeared.
    item_ids = underlying_item_ids(conn, outlets, rows)
    for r in rows:
        # A wire's items are the worldwide distribution stratum, never a country's.
        country = registry.WORLDWIDE if r["outlet_id"] in wire_ids else r["country"]
        pending = r["status"] in ("awaiting_llm", "llm_submitted")
        cat = r["m_cat"]
        d = _day(r)
        if cat and r["ruleset_version"] and r["ruleset_version"] != config.RULESET_VERSION:
            older_ruleset[d] += 1
        if cat and r["m_method"] == "llm" and not classify_llm.on_current_prompt(r["model_version"]):
            older_prompt[d] += 1
        if cat == "A":
            routes[d][country][r["route"] or "unattributed"] += 1
            arrivals[d][country][classify_rules.arrival_of(r["gate_terms"])] += 1
        if not (pending or cat in ("A", "B", "C")):
            continue
        for t in themes_mod.themes_of(r["themes"]):
            v = theme_counts[d][country][t]
            v[0] += 1
            if pending or cat in ("A", "B"):
                v[1] += 1
            if cat == "A":
                v[2] += 1
            if cat == "B":
                v[3] += 1
    for d in list(theme_counts) + list(routes):
        months[d[:7]]["days"][d]  # a day that has theme or route counts always gets an entry, even with no rollup rows
    out = {}
    for month, m in months.items():
        days = {}
        for d, per_country in sorted(m["days"].items()):
            days[d] = {"countries": {c: v for c, v in per_country.items()},
                       "reviewed": {c: v for c, v in reviewed.get(d, {}).items()},
                       "themes": {c: {t: v for t, v in ts.items()} for c, ts in theme_counts.get(d, {}).items()},
                       "routes": {c: dict(rs) for c, rs in routes.get(d, {}).items()},
                       "arrivals": {c: dict(xs) for c, xs in arrivals.get(d, {}).items()},
                       "underlying_items": {c: {cat: sorted(ids) for cat, ids in ab.items() if ids}
                                            for c, ab in sorted(item_ids.get(d, {}).items())},
                       "llm_sampling": sampling.get(d, {}),
                       "llm_ceiling_hit": d in ceiling_days,
                       "relay_hours": relay_hours.get(d),
                       "relay_incomplete": d in relay_incomplete,
                       "labels_on_older_ruleset": older_ruleset.get(d, 0),
                       "labels_on_older_prompt": older_prompt.get(d, 0)}
        out[month] = {"month": month, "days": days}
    return out


SERIES_KEYS = ("A", "B", "C", "N", "rel", "paywalled", "pending")


def build_global_series(daily: Dict[str, Dict]) -> List[Dict]:
    series = []
    for month in sorted(daily):
        for d, day in sorted(daily[month]["days"].items()):
            tot = {"date": d, "A": 0, "B": 0, "C": 0, "N": 0, "rel": 0, "paywalled": 0, "pending": 0,
                   "countries_with_A": 0, "llm_ceiling_hit": day["llm_ceiling_hit"],
                   "relay_incomplete": day.get("relay_incomplete", False),
                   "labels_on_older_ruleset": day.get("labels_on_older_ruleset", 0),
                   "labels_on_older_prompt": day.get("labels_on_older_prompt", 0)}
            # The stratum's own part of the day sits beside the totals, so the interface can show the
            # country outlets and the wires apart by subtraction. Zeros when the day has no stratum.
            wld = {k: 0 for k in SERIES_KEYS}
            for c, v in day["countries"].items():
                # World totals include the worldwide distribution stratum; the count of countries
                # with state origin does not, because the stratum is not a country.
                for k in SERIES_KEYS:
                    tot[k] += v[k]
                    if c == registry.WORLDWIDE:
                        wld[k] += v[k]
                if v["A"] and c != registry.WORLDWIDE:
                    tot["countries_with_A"] += 1
            tot["wld"] = wld
            series.append(tot)
    return series


def build_outlets(conn, outlets: List[Dict]) -> Dict:
    feed_health = {r["feed_url"]: dict(r) for r in conn.execute("SELECT * FROM feed_health")}
    per_outlet = defaultdict(_empty_country)
    for r in conn.execute(
        """SELECT a.outlet_id, c.category, c.method, COUNT(*) n,
                  SUM(CASE WHEN EXISTS(SELECT 1 FROM human_reviews h WHERE h.article_id=a.id) THEN 1 ELSE 0 END) rev
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 GROUP BY a.outlet_id, c.category, c.method"""
    ):
        t = per_outlet[r["outlet_id"]]
        short = {"A": "A", "B": "B", "C": "C", "not_relevant": "N"}[r["category"]]
        t[short] += r["n"]; t["cls"] += r["n"]; t["rev"] += r["rev"]
        if r["category"] in ("A", "B"):
            t[r["category"] + ("r" if r["method"] == "rules" else "l")] += r["n"]
    for r in conn.execute(
        """SELECT outlet_id, COUNT(*) disc, SUM(gate_relevant) rel,
                  SUM(CASE WHEN status IN ('fetched','classified','awaiting_llm','llm_submitted') THEN 1 ELSE 0 END) fetched,
                  SUM(CASE WHEN status='paywalled' THEN 1 ELSE 0 END) paywalled,
                  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
                  SUM(CASE WHEN status='blocked_robots' THEN 1 ELSE 0 END) blocked,
                  SUM(CASE WHEN status IN ('awaiting_llm','llm_submitted') THEN 1 ELSE 0 END) pending
           FROM articles WHERE status<>'junk_feed' GROUP BY outlet_id"""
    ):
        t = per_outlet[r["outlet_id"]]
        t["disc"] = r["disc"]; t["rel"] = r["rel"] or 0; t["fetched"] = r["fetched"] or 0
        t["paywalled"] = r["paywalled"] or 0; t["failed"] = r["failed"] or 0; t["blocked"] = r["blocked"] or 0
        t["pending"] = r["pending"] or 0
    for r in conn.execute("SELECT outlet_id, SUM(polls) polls, SUM(saturated) sat, %s FROM feed_polls GROUP BY outlet_id"
                          % _MISS_SQL, {"est": config.MISSED_ESTIMATOR_VERSION}):
        t = per_outlet[r["outlet_id"]]
        t["polls"] = r["polls"] or 0; t["sat"] = r["sat"] or 0; t["miss"] = int(round(r["miss"] or 0))
        t["mpolls"] = r["mpolls"] or 0
    out = []
    for o in outlets:
        feeds = []
        for fe in registry.feed_entries(o):
            h = feed_health.get(fe["url"])
            feeds.append({"url": fe["url"], "kind": fe.get("kind", registry.EDITORIAL), "ok": bool(h and h["consecutive_failures"] == 0),
                          "last_ok": h["last_ok"] if h else None, "last_error": h["last_error"] if h else None,
                          "consecutive_failures": h["consecutive_failures"] if h else None})
        # A wire carries country WLD in the export only: it is listed and counted under the worldwide
        # distribution stratum, while sources/outlets.yaml keeps the country it is registered in.
        out.append({"id": o["id"], "name": o["name"], "country": registry.export_country(o), "language": o["language"],
                    "tier": o["tier"], "active": o["active"], "notes": o.get("notes"),
                    "audience_rank": o.get("audience_rank"),
                    "political_leaning": o.get("political_leaning") or "unassessed", "leaning_source": o.get("leaning_source"),
                    "leaning_second_source": o.get("leaning_second_source"),
                    "leaning_source_weak": registry.leaning_source_weak(o),
                    "ownership": o.get("ownership") or "unassessed", "ownership_source": o.get("ownership_source"),
                    "government_stance": o.get("government_stance") or "unassessed", "stance_source": o.get("stance_source"),
                    "stance_as_of": o.get("stance_as_of"),
                    "inactive_reason": o.get("inactive_reason"), "feeds": feeds, "counts": dict(per_outlet[o["id"]]),
                    "collector": registry.collector_of(o),
                    "release_sections": (o.get("release_sections") or {}).get("status") or "not_searched"})
    return {"generated_at": store.utcnow(), "outlets": out}


TRIGGER_NAMES = {
    "cites_xinhua": "Xinhua", "cites_cgtn": "CGTN", "cites_global_times": "Global Times", "cites_china_daily": "China Daily",
    "cites_cctv": "CCTV", "cites_peoples_daily": "People's Daily", "cites_china_media_group": "China Media Group or China News Service",
    "mofa_spokesperson": "Foreign ministry spokesperson", "chinese_embassy_quoted": "Chinese embassy",
    "named_chinese_official_spokesperson": "Named Chinese official spokesperson",
}


def _trigger_sources(trigger_ids):
    out = []
    for t in trigger_ids:
        if t.startswith("state_media_reported"):
            name = "Chinese state media, as reported"
        else:
            name = TRIGGER_NAMES.get(t, t)
        if name not in out:
            out.append(name)
    return out


ORDER = {"A": 0, "B": 1, "pending": 2, "C": 3}


def build_articles(conn, per_country: int = 80, outlets: Optional[List[Dict]] = None) -> Dict[str, List[Dict]]:
    """Per country: state origin first, then unchecked state sourcing, then articles carrying official
    Chinese sourcing whose verification judgement is pending, then independent journalism."""
    rows = conn.execute(
        """SELECT a.id, a.country, a.outlet_id, a.title, a.url, a.published_at, a.discovered_at, a.day_of_record, a.dup_group_id,
                  a.status, a.llm_trigger, a.themes, a.gate_terms,
                  c.category, c.method, c.confidence, c.evidence_quote, c.reasoning, c.signatures_fired, c.model_version,
                  c.ruleset_version, c.china_sources_cited, c.route,
                  (SELECT human_category FROM human_reviews h WHERE h.article_id=a.id ORDER BY h.id DESC LIMIT 1) AS human_category
           FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 AND (c.category IN ('A','B','C') OR a.status IN ('awaiting_llm','llm_submitted'))
           ORDER BY COALESCE(a.published_at, a.discovered_at) DESC"""
    ).fetchall()
    out = defaultdict(list)
    wire_ids = registry.distribution_wire_ids(outlets if outlets is not None else registry.load_outlets())
    from pipeline import body_dups
    items = body_dups.item_groups(conn)
    for r in rows:
        # A release wire's items are not a country's articles: they are listed under WLD.
        country = registry.WORLDWIDE if r["outlet_id"] in wire_ids else r["country"]
        pending = r["category"] is None
        cat = "pending" if pending else r["category"]
        entry = {
            "id": r["id"], "outlet_id": r["outlet_id"], "title": r["title"], "url": r["url"],
            "date": _day(r), "category": cat, "human_category": r["human_category"],
            "provenance": "human" if r["human_category"] else ("rules" if pending else r["method"]),
            "confidence": r["confidence"], "evidence_quote": r["evidence_quote"],
            "reasoning": r["reasoning"], "signatures": json.loads(r["signatures_fired"] or "[]"),
            "sources": json.loads(r["china_sources_cited"] or "[]"),
            "model": r["model_version"], "ruleset": r["ruleset_version"], "dup_group": items.get(r["dup_group_id"] or r["id"], r["dup_group_id"]),
            "themes": themes_mod.themes_of(r["themes"]),
            "route": r["route"] if cat == "A" else None,
            "arrival": classify_rules.arrival_of(r["gate_terms"]),
        }
        if pending and r["llm_trigger"]:
            try:
                trig = json.loads(r["llm_trigger"])
            except ValueError:
                trig = {}
            entry["sources"] = _trigger_sources(trig.get("triggers", []))
            entry["evidence_quote"] = (trig.get("spans") or [None])[0]
            entry["signatures"] = trig.get("a_candidate", [])
            entry["reasoning"] = ("Carries official Chinese sourcing. Whether the claim is independently checked is the "
                                  "verification judgement, which has not run yet.")
            if trig.get("a_candidate") and not trig.get("triggers"):
                # Sent by one weak signature alone, with no official sourcing trigger.
                entry["reasoning"] = ("Carries a weak state origin signature. Whether it is state origin is the model's "
                                      "judgement, which has not run yet.")
            if trig.get("lookalike"):
                # Sent by pipeline/lookalike.py: no credit or sourcing in the text, a near repeat of a state origin item.
                entry["reasoning"] = ("Nearly repeats an item another outlet ran as state origin. Whether this article is "
                                      "that state text, unaltered, is the model's judgement, which has not run yet.")
        out[country].append(entry)
    for country in out:
        # Newest first inside each category, categories in ORDER. Two stable sorts.
        out[country].sort(key=lambda e: e["date"] or "", reverse=True)
        out[country].sort(key=lambda e: ORDER.get(e["category"], 9))
        out[country] = out[country][:per_country]
    return out


# ---------------------------------------------------------------------------
# Worked examples: the short matched span that produced a label, for the reader's "why this one"
# ---------------------------------------------------------------------------

def _pattern_index() -> Dict[str, "re.Pattern"]:
    """Every compiled signature and LLM trigger pattern, keyed by id, so a stored evidence span can
    be searched again to recover where inside it the matched phrase sits. The offsets are never
    stored on the classification itself, only the span text around the match."""
    sigs = classify_rules.load_signatures()
    idx = {}
    for pats in sigs["groups"].values():
        for p in pats:
            idx[p["id"]] = p["re"]
    for t_id, rx in sigs["llm_triggers"]:
        idx[t_id] = rx
    return idx


def _diplomat_offset(span: str) -> Optional[Tuple[int, int]]:
    """The diplomat_list_author and diplomat_list_name_only signatures are not regex patterns; they
    fire on a name from pipeline/diplomats.yaml found in the author field. Recover the name's own
    position in the stored span the same way match_signatures found it."""
    for name in classify_rules.load_diplomats():
        m = re.search(classify_rules.name_pattern(name), span, re.IGNORECASE)
        if m:
            return m.start(), m.end()
    return None


def _match_offset(pattern_id: str, span: str) -> Optional[Tuple[int, int]]:
    if pattern_id in ("diplomat_list_author", "diplomat_list_name_only"):
        return _diplomat_offset(span)
    rx = _pattern_index().get(pattern_id)
    if not rx:
        return None
    m = rx.search(span)
    return (m.start(), m.end()) if m else None


# Scripts written without spaces between words: Thai, Lao, Myanmar, Khmer, CJK punctuation, kana,
# CJK ideographs and the full and half width forms. A snippet is cut on characters there, since
# there is no space to find a word boundary by.
_NO_SPACE = ("\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff\u3001-\u303f\u3040-\u30ff\u3400-\u4dbf"
             "\u4e00-\u9fff\uf900-\ufaff\uff01-\uff9f")
# Combining vowel and tone marks of those scripts stay on the character they sit on.
_NO_SPACE_MARKS = "".join(chr(c) for c in list(range(0x0e00, 0x0f00)) + list(range(0x1000, 0x10a0))
                          + list(range(0x1780, 0x1800)) + [0x3099, 0x309a]
                          if unicodedata.category(chr(c)).startswith("M"))
_SNIPPET_TOKEN = re.compile(r"[%s][%s]*|[^\s%s]+" % (_NO_SPACE, _NO_SPACE_MARKS, _NO_SPACE))
_NO_SPACE_CHAR = re.compile(r"[%s]" % _NO_SPACE)
# The rules keep this many characters of context on each side of a match (classify_rules.match_signatures
# and its llm trigger spans). Spans stored before classify_rules.evidence_span carry no edge marks, so
# one whose match sits this far from an edge was cut there, possibly inside a word; one that stops
# short reached the start or end of the text or a space. Newer spans mark each edge where text was
# left out (_unmark_span), and an edge they leave unmarked is the text's own start or end, or a
# character window in a script written without spaces.
_SPAN_CONTEXT = classify_rules.SPAN_CONTEXT
_SENTENCE_END = re.compile("[.!?\u3002\uff01\uff1f][\"'\u201d\u2019\u00bb)\\]\u300d\u300f]*$")
_ELLIPSIS = "\u2026"


def _trim_to_words(text: str, start: int, end: int, max_words: int = 25,
                   cut_left: bool = False, cut_right: bool = False,
                   gap_left: bool = False, gap_right: bool = False) -> Tuple[str, int, int]:
    """Cut text to at most max_words words, keeping the matched [start, end) span inside the
    result, with the leftover word budget split evenly around it. cut_left and cut_right say the
    text was itself cut from a longer one at that edge, possibly inside a word: a partial word
    there is dropped, unless the match reaches into it. The snippet always starts and ends on a
    word boundary and carries an ellipsis (counted as a word) on each side where text was left
    out. gap_left and gap_right say text was left out at that edge on a word boundary: the edge
    gets its ellipsis and no word is dropped. In scripts written without spaces a character counts
    as half a word and is a boundary of its own. Returns the snippet and the matched span's offsets inside it."""
    words = [(m.start(), m.end()) for m in _SNIPPET_TOKEN.finditer(text)]
    if not words:
        return text, start, end

    def no_space(i):
        return bool(_NO_SPACE_CHAR.match(text, words[i][0]))

    first, last = 0, len(words) - 1
    if cut_left and last > first and words[first][1] <= start:
        tok = text[words[first][0]:words[first][1]]
        whole = no_space(first) and not unicodedata.category(tok[0]).startswith("M")
        whole = whole or tok[0] in "\"'\u201c\u2018\u00ab([" or \
            (tok[0].isupper() and not (len(tok) > 1 and tok[1].isupper()))
        if not whole:
            first += 1
    lead = cut_left or gap_left
    ended = False
    if (cut_right or gap_right) and last > first and words[last][0] >= end:
        tok = text[words[last][0]:words[last][1]]
        ended = bool(_SENTENCE_END.search(tok))
        if cut_right and not (no_space(last) or ended):
            last -= 1
    trail = (cut_right or gap_right) and not ended
    lo_i = next((i for i in range(first, last + 1) if words[i][1] > start), first)
    hi_i = next((i for i in range(first, last + 1) if words[i][0] >= end), last + 1) - 1
    hi_i = max(hi_i, lo_i)

    def cost(i):
        return 0.5 if no_space(i) else 1.0

    budget = max(0.0, max_words - sum(cost(i) for i in range(lo_i, hi_i + 1)) - lead - trail)
    before = budget // 2

    def take(indices, allowance, marked):
        if sum(cost(i) for i in indices) <= allowance:
            return len(indices), marked
        allowance -= 0 if marked else 1
        n, used = 0, 0.0
        for i in indices:
            if used + cost(i) > allowance:
                break
            used += cost(i)
            n += 1
        return n, True

    n_before, lead = take(list(range(lo_i - 1, first - 1, -1)), before, lead)
    n_after, trail = take(list(range(hi_i + 1, last + 1)), budget - before, trail)
    lo, hi = lo_i - n_before, hi_i + n_after
    prefix = (_ELLIPSIS + ("" if no_space(lo) else " ")) if lead else ""
    suffix = (("" if no_space(hi) else " ") + _ELLIPSIS) if trail else ""
    snippet = prefix + text[words[lo][0]:words[hi][1]] + suffix
    shift = len(prefix) - words[lo][0]
    return snippet, start + shift, end + shift


def _unmark_span(span: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Take off the edge marks classify_rules.evidence_span puts on a stored span. Returns the bare
    text and, for the left and right edge, None when unmarked, "word" when text was left out there on
    a word boundary (the ellipsis set off by a space), or "cut" when the edge falls inside a long
    token (the ellipsis right against the text)."""
    left = right = None
    mark = classify_rules.SPAN_MARK
    if len(span) > 1 and span.startswith(mark):
        left = "word" if span[1].isspace() else "cut"
        span = span[1:].lstrip()
    if len(span) > 1 and span.endswith(mark):
        right = "word" if span[-2].isspace() else "cut"
        span = span[:-1].rstrip()
    return span, left, right


_SNIPPET_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)


def _shorten_urls(snippet: str, start: int, end: int) -> Tuple[str, int, int]:
    """Replace each bare URL in a quoted snippet with its host, so a press release that ends in a
    long attachment link reads as "globenewswire.com" and cannot run off a phone screen as one
    unbreakable word. Trailing sentence punctuation stays outside the URL. When the highlighted
    match lies inside the host (a distribution stamp such as "globenewswire" matched within the
    link), the highlight moves onto the host; when it touches any other part of the URL, that URL is
    left whole so the highlight still covers exactly what the pattern matched. Returns the new
    snippet and the match offsets inside it."""
    out, pos, new_len = [], 0, 0
    new_start, new_end = start, end
    for m in _SNIPPET_URL.finditer(snippet):
        url = m.group(0).rstrip(".,;:!?)]}")
        u_start, u_end = m.start(), m.start() + len(url)
        try:
            host = urlsplit(url if "://" in url else "http://" + url).hostname or ""
        except ValueError:
            host = ""
        host = host[4:] if host.startswith("www.") else host
        if not host:
            continue
        h_at = url.lower().find(host)
        h_start = u_start + h_at if h_at >= 0 else -1
        overlaps = u_end > start and u_start < end
        inside_host = h_start >= 0 and h_start <= start and end <= h_start + len(host)
        if overlaps and not inside_host:
            continue
        before = snippet[pos:u_start]
        out.append(before)
        new_len += len(before)
        if overlaps:
            new_start, new_end = new_len + (start - h_start), new_len + (end - h_start)
        elif u_end <= start:
            shift = (u_end - u_start) - len(host)
            new_start, new_end = new_start - shift, new_end - shift
        out.append(snippet[h_start:h_start + len(host)] if h_start >= 0 else host)
        new_len += len(host)
        pos = u_end
    out.append(snippet[pos:])
    return "".join(out), new_start, new_end


def _tell(category: str, signatures_fired, evidence_quote: Optional[str], llm_trigger_json) -> Optional[Dict]:
    """The short matched span that produced a state origin or unchecked state sourcing label, cut to
    at most 25 words, with the offsets of the matched phrase inside it so the front end can
    highlight it. For state origin this is the deterministic signature recorded on the
    classification; for unchecked state sourcing, which the model judges freely, it is the official
    sourcing trigger, or lacking one the weak signature, that routed the article to the model in the
    first place: the article's llm_trigger field, still on the row after judgement. Returns None
    when no matched span can be recovered, so the caller skips the article rather than showing a
    fabricated highlight."""
    if category == "A":
        ids = signatures_fired or []
        span = evidence_quote
        if not ids or not span:
            return None
        pattern_id = ids[0]
    else:
        try:
            trig = json.loads(llm_trigger_json or "{}") or {}
        except ValueError:
            trig = {}
        triggers = trig.get("triggers") or []
        a_candidate = trig.get("a_candidate") or []
        spans = trig.get("spans") or []
        pattern_id, span = (triggers[0], spans[0]) if triggers and spans else \
            ((a_candidate[0], spans[0]) if a_candidate and spans else (None, None))
        if not pattern_id or not span:
            return None
    span, left, right = _unmark_span(span)
    offset = _match_offset(pattern_id, span)
    if not offset:
        return None
    marked = bool(left or right)

    def raw_cut(gap: int, edge: str) -> bool:
        # An unmarked edge of a span stored before the marks, or a character window in a script
        # written without spaces: cut where the match sits a full window from it.
        return gap >= _SPAN_CONTEXT and (not marked or bool(_NO_SPACE_CHAR.match(edge)))

    snippet, s, e = _trim_to_words(span, offset[0], offset[1],
                                   cut_left=left == "cut" or (not left and raw_cut(offset[0], span[:1])),
                                   cut_right=right == "cut" or (not right and raw_cut(len(span) - offset[1],
                                                                                      span[-1:])),
                                   gap_left=left == "word", gap_right=right == "word")
    snippet, s, e = _shorten_urls(snippet, s, e)
    return {"snippet": snippet, "start": s, "end": e}


def build_examples(conn, outlets: Optional[List[Dict]] = None, limit: int = 6) -> Dict:
    """Up to `limit` recent, varied worked examples for state origin, and for unchecked state
    sourcing when it may be shown: different countries and different outlets, most recent first,
    current ruleset labels only. Paywalled articles are never classified, so they are absent here in
    any case; the status check guards that directly rather than relying on it. Distribution wires
    are excluded: the examples show placements in newsrooms, and the worldwide distribution stratum
    is not one."""
    outlets = outlets if outlets is not None else registry.load_outlets()
    wire_ids = registry.distribution_wire_ids(outlets)
    names = {o["id"]: o["name"] for o in outlets}
    visible = relay_visible(conn)
    out = {"generated_at": store.utcnow()}
    for cat in (["A", "B"] if visible else ["A"]):
        rows = conn.execute(
            """SELECT a.id, a.title, a.url, a.outlet_id, a.country, a.discovered_at, a.day_of_record, a.llm_trigger,
                      c.evidence_quote, c.signatures_fired, c.route
               FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
               WHERE c.category=? AND c.ruleset_version=? AND a.status<>'paywalled'
               ORDER BY COALESCE(a.day_of_record, a.discovered_at) DESC""",
            (cat, config.RULESET_VERSION),
        ).fetchall()
        picked = []
        seen_countries, seen_outlets = set(), set()
        for r in rows:
            if r["outlet_id"] in wire_ids:
                continue
            if r["country"] in seen_countries or r["outlet_id"] in seen_outlets:
                continue
            sigs = json.loads(r["signatures_fired"] or "[]") if cat == "A" else None
            tell = _tell(cat, sigs, r["evidence_quote"], r["llm_trigger"])
            if not tell:
                continue
            picked.append({
                "id": r["id"], "headline": r["title"], "outlet_id": r["outlet_id"],
                "outlet_name": names.get(r["outlet_id"], r["outlet_id"]), "country": r["country"],
                "url": r["url"], "date": _day(r),
                "route": r["route"] if cat == "A" else None, "tell": tell,
            })
            seen_countries.add(r["country"])
            seen_outlets.add(r["outlet_id"])
            if len(picked) >= limit:
                break
        out[cat] = picked
    return out


# ---------------------------------------------------------------------------
# meta.findings: the small "what we found" summary the front end turns into a box
# ---------------------------------------------------------------------------

OWNERSHIP_GROUPS = ["private", "public_service", "state_funded_international", "state", "party", "unassessed"]


def by_ownership_findings(outlets_json: Dict) -> Dict:
    """State origin output by the publishing outlet's ownership, all time, active outlets only,
    distribution wires excluded: the same figures docs/app.js leanModel computes for the ownership
    axis with measure "a", read here to match rather than reimplemented differently."""
    groups = {g: {"articles": 0, "outlets": 0} for g in OWNERSHIP_GROUPS}
    for o in outlets_json["outlets"]:
        if not o["active"] or o["tier"] == "distribution_wire":
            continue
        key = o.get("ownership") or "unassessed"
        if key not in groups:
            key = "unassessed"
        groups[key]["articles"] += (o.get("counts") or {}).get("A", 0)
        groups[key]["outlets"] += 1
    rows = []
    top = None
    for g in OWNERSHIP_GROUPS:
        v = groups[g]
        rate = round(v["articles"] / v["outlets"], 3) if v["outlets"] else None
        rows.append({"id": g, "articles": v["articles"], "outlets": v["outlets"], "per_outlet_rate": rate})
        if rate is not None and (top is None or rate > top["per_outlet_rate"]):
            top = rows[-1]
    return {"groups": rows, "top": {"id": top["id"], "per_outlet_rate": top["per_outlet_rate"]} if top else None}


def complete_window_30d(latest: Dict) -> Tuple[str, str]:
    """The 30 complete days before the export day, as (first day, day after the last). The export runs
    before its own UTC day ends, so that day is partial; latest.json's last_30d keeps it, but the
    findings' 30 day figures leave it out so they match the window the site's map and ranked list open
    on (lastCompleteDay in docs/app.js)."""
    start = latest["window_start_30d"]
    return start, (dt.date.fromisoformat(start) + dt.timedelta(days=30)).isoformat()


def _state_origin_by_country(conn, start: str, end: str) -> Dict[str, int]:
    """State origin per country (the stratum under WLD) on the days start <= day < end, from the same
    daily_counts rows the daily files carry, so a sum here equals the site's sum over those days."""
    out: Dict[str, int] = defaultdict(int)
    for r in conn.execute("SELECT country, SUM(n) n FROM daily_counts WHERE scope='all' AND category='A' AND date>=? AND date<? GROUP BY country",
                          (start, end)):
        out[r["country"]] += r["n"] or 0
    return out


def _top_country_last30(conn, outlets: List[Dict], latest: Dict) -> Optional[Dict]:
    """The country with the most state origin articles in the last 30 complete days (complete_window_30d,
    so the partial export day is left out, as on the site's map): its share of the world's state origin
    in that window, the outlet that carried most of it, and the route that carried most of it. The
    worldwide distribution stratum (WLD) is part of the world total, since its releases reached
    audiences, but it is not a country and is never the top country. The wire exclusion below guards
    the article query, whose rows still carry the wires' registry country. A route with no signature at
    all is not counted as a route, matching route_signature_totals in build_meta."""
    since30, until = complete_window_30d(latest)
    world = 0
    by_country = {}
    for iso, a in _state_origin_by_country(conn, since30, until).items():
        world += a
        if a and iso != registry.WORLDWIDE:
            by_country[iso] = a
    if not by_country:
        return None
    top_iso = max(by_country, key=lambda k: (by_country[k], k))
    wire_ids = registry.distribution_wire_ids(outlets)
    where = ""
    params: List = [top_iso, since30, until]
    if wire_ids:
        where = " AND a.outlet_id NOT IN (%s)" % ",".join("?" * len(wire_ids))
        params += sorted(wire_ids)
    outlet_counts, route_counts = defaultdict(int), defaultdict(int)
    for r in conn.execute(
            """SELECT a.outlet_id, c.route FROM articles a JOIN classifications c
               ON c.article_id=a.id AND c.is_current=1
               WHERE a.country=? AND c.category='A' AND COALESCE(a.day_of_record, substr(a.discovered_at, 1, 10))>=?
               AND COALESCE(a.day_of_record, substr(a.discovered_at, 1, 10))<?%s""" % where, params):
        outlet_counts[r["outlet_id"]] += 1
        route_counts[r["route"] or "unattributed"] += 1
    route_counts.pop("unattributed", None)
    names = {o["id"]: o["name"] for o in outlets}
    top_outlet_id = max(outlet_counts, key=lambda k: (outlet_counts[k], k)) if outlet_counts else None
    top_route_id = max(route_counts, key=lambda k: (route_counts[k], k)) if route_counts else None
    return {
        "iso": top_iso,
        "state_origin": by_country[top_iso],
        "share_of_world": round(by_country[top_iso] / world, 4) if world else None,
        "top_outlet": {"id": top_outlet_id, "name": names.get(top_outlet_id, top_outlet_id)} if top_outlet_id else None,
        "top_route": top_route_id,
    }


def build_findings(conn, outlets: List[Dict], latest: Dict, outlets_json: Dict, first_discovered: Optional[str]) -> Dict:
    """The small "what we found" summary the front end turns into a box: since when, the all time
    state origin, unchecked state sourcing and independent totals and the state share of China
    coverage, the country carrying most state origin in the last 30 days, and state origin output by
    the publishing outlet's ownership."""
    visible = relay_visible(conn)
    tot = latest["totals"]["all_time"]
    # china_total is A + B + C across every country and the worldwide distribution stratum: the
    # same denominator the interface already uses for "share of its China coverage" (_derive above).
    china_total = tot["china_total"]
    # The stratum's part of each total, written beside it, so the site's Distribution toggle can show
    # the country outlets alone (total minus wires) or the wires alone without a second export. An
    # export with no stratum writes zeros.
    wld = latest["countries"].get(registry.WORLDWIDE) or {}
    wld_all = wld.get("all_time") or _derive(_empty_country(), 0, visible)
    start30, until30 = complete_window_30d(latest)
    so30 = _state_origin_by_country(conn, start30, until30)
    return {
        "since": first_discovered,
        "all_time": {
            "state_origin": tot["A"],
            "unchecked": tot["B"] if visible else None,
            "independent": tot["C"],
            "china_total": china_total,
            "state_share_of_china": round(tot["A"] / china_total, 4) if china_total else None,
            "state_origin_wires": wld_all.get("A", 0),
            "unchecked_wires": wld_all.get("B", 0) if visible else None,
            "independent_wires": wld_all.get("C", 0),
            "china_total_wires": wld_all.get("china_total", 0),
        },
        # The world's state origin in the last 30 complete days and the wires' part of it: the
        # denominator of the top country's share of the world, which the toggle recomputes without the
        # wires. The same days as top_country, so the partial export day is in neither.
        "last_30d": {
            "state_origin": sum(so30.values()),
            "state_origin_wires": so30.get(registry.WORLDWIDE, 0),
            "window_start": start30,
            "window_end": (dt.date.fromisoformat(until30) - dt.timedelta(days=1)).isoformat(),
        },
        "top_country": _top_country_last30(conn, outlets, latest),
        "by_ownership": by_ownership_findings(outlets_json),
        "generated_at": store.utcnow(),
    }


def rediscovery(conn) -> Optional[Dict]:
    """The repeats in the discovery counts before pruned addresses were remembered (config.SEEN_URL_*):
    an item a feed kept listing was counted again after every prune, and those days stay as counted.
    fixed_from is the day after the first run that remembered them (store schema_migrations,
    "seen_urls.remembering"): items pruned before that run and still listed come back once more on its
    day, so the day itself still carries repeats. config.REDISCOVERY_FIXED_FROM when the database has no
    such row. None when no discovery is counted before that first run, as in a database begun after it."""
    applied = None
    try:
        r = conn.execute("SELECT applied_at FROM schema_migrations WHERE name='seen_urls.remembering'").fetchone()
        applied = (r[0] or "")[:10] if r else None
    except Exception:
        applied = None
    if applied:
        fixed_from = (dt.date.fromisoformat(applied) + dt.timedelta(days=1)).isoformat()
    else:
        applied = fixed_from = config.REDISCOVERY_FIXED_FROM
    first = conn.execute("SELECT MIN(date) FROM daily_discovery").fetchone()[0]
    if not first or first >= applied:
        return None
    return {"fixed_from": fixed_from, "estimated_share": config.REDISCOVERY_ESTIMATED_SHARE,
            "measured_floor": config.REDISCOVERY_MEASURED_FLOOR, "measured_of": config.REDISCOVERY_MEASURED_OF,
            "window_share": config.REDISCOVERY_WINDOW_SHARE}


def build_meta(conn, outlets: List[Dict], gaps: List[Dict], latest: Dict, outlets_json: Optional[Dict] = None) -> Dict:
    outlets_json = outlets_json if outlets_json is not None else build_outlets(conn, outlets)
    last_runs = {}
    for r in conn.execute("SELECT stage, MAX(finished_at) f FROM run_log WHERE ok=1 GROUP BY stage"):
        last_runs[r["stage"]] = r["f"]
    # This export is still running when the metadata is built, so record it as now rather
    # than reporting the previous export's time.
    last_runs["export"] = store.utcnow()
    kappa = conn.execute("SELECT * FROM agreement_studies ORDER BY id DESC LIMIT 1").fetchone()
    details = {}
    if kappa and kappa["details"]:
        try:
            details = json.loads(kappa["details"]) or {}
        except ValueError:
            details = {}
    cls_total = conn.execute("SELECT COUNT(*) FROM classifications WHERE is_current=1").fetchone()[0]
    reviewed_total = conn.execute("SELECT COUNT(DISTINCT article_id) FROM human_reviews").fetchone()[0]
    llm = conn.execute("SELECT SUM(calls) calls, SUM(ceiling_hit) ceilings FROM llm_usage").fetchone()
    # Labels the model actually produced. Not the same as calls: a batch submission records its
    # requests immediately but returns nothing until a later run collects it, and a call that errored
    # or was refused produces no label either. Relay is measured when judgements exist, not when
    # requests were sent.
    llm_labels = conn.execute("SELECT COUNT(*) FROM classifications WHERE method='llm' AND is_current=1").fetchone()[0]
    # A capped day is one where more articles were waiting than the daily cap allowed, so the draw was
    # bound. It does not mean the cap was spent: a run can stop at its time budget first, as on
    # 2026-09-16, when the draw was capped at 600 against 3,710 waiting and only 279 calls were made.
    ceiling_days = [r["date"] for r in conn.execute("SELECT date FROM llm_usage WHERE ceiling_hit=1 ORDER BY date")]
    calls_by_day = {r["date"]: r["calls"] for r in conn.execute("SELECT date, calls FROM llm_usage ORDER BY date")}
    active = registry.active_outlets(outlets)
    countries_active = sorted({o["country"] for o in active})
    tot = latest["totals"]["all_time"]
    attempted = tot["fetched"] + tot["paywalled"] + tot["failed"] + tot["blocked"]
    paywall_countries = [c for c, v in latest["countries"].items()
                         if any(w["type"] == "paywalled" for w in v.get("warnings", []))]
    # Only hand coding publishes unchecked state sourcing: kappa at or above the threshold on unchecked
    # sourcing versus independent journalism, with enough positives on each side, on a sample coded to
    # the end. Owner's decision, 12 October 2026, after an outside review.
    human_study = agreement.human_study_status(conn)
    settled = human_study["passed"]
    # A model re-judging the same articles, itself or a sibling, is a stability check, not a validation
    # one: the same prompt and the same training agree with themselves almost always, and two models of
    # one family can share a bias neither reveals. It is recorded and shown and never unlocks
    # publication; a check that fails withholds the provisional counts.
    model_study = store.latest_model_agreement(conn)
    model_details = {}
    if model_study and model_study["details"]:
        try:
            model_details = json.loads(model_study["details"]) or {}
        except ValueError:
            model_details = {}
    # A check made under an older classifier prompt is stale: its first labels are no longer the ones
    # counted, and it may pair labels from two prompts, which measures the prompt change. It is shown
    # as history (relay_reliability_history) and is not the current stability figure: it withholds
    # nothing and vetoes no language.
    model_stale = bool(model_study) and not second_rater.study_on_current_prompt(model_study)
    unstable = bool(model_study and not model_stale and model_study["kappa_bc"] is not None
                    and model_study["kappa_bc"] < config.KAPPA_WARNING_THRESHOLD)
    model_block = {
        "method": model_study["method"], "model_a": model_study["model_a"], "model_b": model_study["model_b"],
        "n": model_study["sample_size"], "kappa_all": model_study["kappa_all"], "kappa_bc": model_study["kappa_bc"],
        "n_bc": model_study["n_bc"], "computed_at": model_study["computed_at"],
        "by_category": model_details.get("kappa_by_category"), "bc_by_language": model_details.get("bc_by_language"),
        "threshold": config.KAPPA_WARNING_THRESHOLD,
        "role": "stability_check", "note": RELAY_QUALIFIER.get(model_study["method"] or "model_vs_model"),
        "prompt_version": second_rater.study_prompt_version(model_study), "stale": model_stale,
    } if model_study else None
    relay_reliability = None if model_stale else model_block
    # Provisional is every state before the hand coding study passes, unless that study failed or the
    # stability check did, which withhold. A hand coding study that is unfinished or could not produce a
    # kappa leaves the counts provisional, and says why.
    human_failed = human_study["state"] == "failed"
    provisional = bool(config.RELAY_PROVISIONAL_DISPLAY and llm_labels and not settled and not human_failed and not unstable)
    inconclusive = human_study["state"] == "inconclusive"
    # What the published counts rest on. Hand coding or nothing.
    relay_basis = "human_coding" if settled else None
    # The per language veto reads the hand coding study once there is one, and the stability check before.
    by_language = (details.get("bc_by_language") if kappa else
                   (None if model_stale else model_details.get("bc_by_language"))) or {}
    withheld_languages = sorted(
        lang for lang, v in by_language.items()
        if (v.get("n") or 0) >= config.KAPPA_MIN_LANGUAGE_ITEMS and v.get("kappa") is not None
        and v["kappa"] < config.KAPPA_WARNING_THRESHOLD)
    from pipeline.classify_rules import REFETCH_ATTEMPT_LIMIT
    unreclassifiable = conn.execute(
        """SELECT COUNT(*) FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE c.ruleset_version <> ? AND a.fetch_attempts >= ?""",
        (config.RULESET_VERSION, REFETCH_ATTEMPT_LIMIT),
    ).fetchone()[0]
    mix = {r[0]: r[1] for r in conn.execute("SELECT ruleset_version, COUNT(*) FROM classifications WHERE is_current=1 GROUP BY ruleset_version")}
    # World totals by route, the worldwide distribution stratum included, so a route filtered world
    # figure on the site and this table are the same sum.
    # Every route is listed, a route nothing has reached yet (uncredited_copy, say) at zero.
    route_totals = dict.fromkeys(classify_rules.ROUTE_IDS, 0)
    for r in conn.execute(
            "SELECT route, COUNT(*) FROM classifications WHERE is_current=1 AND category='A' GROUP BY route"):
        route_totals[r[0] or "unattributed"] = route_totals.get(r[0] or "unattributed", 0) + r[1]
    sat = conn.execute("SELECT COALESCE(SUM(polls), 0), COALESCE(SUM(saturated), 0), %s FROM feed_polls" % _MISS_SQL,
                       {"est": config.MISSED_ESTIMATOR_VERSION}).fetchone()
    first_discovered = (conn.execute("SELECT MIN(discovered_at) FROM articles").fetchone()[0] or "")[:10] or None
    return {
        "schema_version": config.SCHEMA_VERSION,
        "findings": build_findings(conn, outlets, latest, outlets_json, first_discovered),
        "ruleset_version": config.RULESET_VERSION,
        "llm_model": config.LLM_MODEL,
        "llm_prompt_version": classify_llm.PROMPT_VERSION,
        "labels_on_older_prompt": sum(1 for r in conn.execute(
            "SELECT model_version FROM classifications WHERE method='llm' AND is_current=1")
            if not classify_llm.on_current_prompt(r[0])),
        "generated_at": store.utcnow(),
        "last_runs": last_runs,
        "last_successful_run": max(last_runs.values()) if last_runs else None,
        "outlets_total": len(outlets), "outlets_active": len(active),
        "countries_monitored": len(countries_active),
        "registry_unevenness": registry.registry_summary(outlets)["unevenness"],
        "countries_in_gaps": len(gaps),
        "themes": themes_mod.catalog(),
        "themes_version": themes_mod.version(),
        # Themes versions and their dates, marked on the theme timeline (pipeline/themes.py HISTORY).
        "themes_changes": themes_mod.changes(),
        "theme_languages": sorted(themes_mod.languages()),
        "gate_languages": len(gate.supported_languages()),
        "population_source": registry.population_source(),
        "top_outlets_per_country": registry.TOP_OUTLETS_PER_COUNTRY,
        "countries_with_audience_ranks": registry.top_outlets(outlets)[1],
        "min_outlets_for_output_share": config.MIN_OUTLETS_FOR_OUTPUT_SHARE,
        "gaps": gaps,
        "articles_discovered": conn.execute("SELECT COALESCE(SUM(discovered), 0) FROM daily_discovery").fetchone()[0],
        "first_discovered": first_discovered,
        # Items counted again after a prune before the fix, in articles_discovered and every per country
        # discovery denominator; None for a database begun after it (rediscovery).
        "rediscovery": rediscovery(conn),
        "articles_gate_relevant": conn.execute("SELECT COUNT(*) FROM articles WHERE gate_relevant=1").fetchone()[0],
        "articles_classified": cls_total,
        "official_sourcing_pending": conn.execute("SELECT COUNT(*) FROM articles WHERE status IN ('awaiting_llm','llm_submitted')").fetchone()[0],
        "official_sourcing_pending_countries": conn.execute("SELECT COUNT(DISTINCT country) FROM articles WHERE status IN ('awaiting_llm','llm_submitted')").fetchone()[0],
        "articles_reviewed": reviewed_total,
        "review_coverage": round(reviewed_total / cls_total, 4) if cls_total else 0.0,
        "paywall_share": round(tot["paywalled"] / attempted, 4) if attempted else None,
        "paywall_flagged_countries": sorted(paywall_countries),
        "paywalled_in_denominator": False,
        "kappa": {"all": kappa["kappa_all"], "bc": kappa["kappa_bc"], "n": kappa["sample_size"], "n_bc": kappa["n_bc"],
                  "computed_at": kappa["computed_at"], "by_category": details.get("kappa_by_category"),
                  "bc_by_language": details.get("bc_by_language")} if kappa else None,
        "kappa_warning_threshold": config.KAPPA_WARNING_THRESHOLD,
        "kappa_min_language_items": config.KAPPA_MIN_LANGUAGE_ITEMS,
        "b_counts_settled": settled,
        # The interface publishes unchecked state sourcing counts only when this is true. Until the verification
        # stage has run, relay is not measured at all and is shown as such, never as zero.
        "relay_measured": bool(llm_labels),
        "relay_publishable": bool(llm_labels) and settled,
        # Real counts, marked as one model's unchecked judgement, until the hand coding study passes.
        "relay_provisional": provisional,
        "relay_provisional_reason": agreement.provisional_reason(human_study) if provisional else None,
        # The hand coding study ran and the sample could not support a figure. The counts stay provisional.
        "relay_study_inconclusive": inconclusive,
        "relay_human_study": human_study,
        "relay_study": second_rater.study_state(conn),
        "relay_basis": relay_basis,
        "relay_reliability": relay_reliability,
        # The last stability check when it was made under an older classifier prompt: history, not current.
        "relay_reliability_history": model_block if model_stale else None,
        # Whether any article has been read and coded by a person. Said plainly here so nothing downstream has to infer it.
        "relay_human_coded": bool(reviewed_total),
        "relay_qualifier": RELAY_QUALIFIER.get(relay_basis),
        "relay_withheld_languages": withheld_languages,
        "llm_calls_total": llm["calls"] or 0,
        "llm_labels_total": llm_labels,
        # The part of llm_labels_total the same model gave outside the API, in an interactive session
        # at no API cost, and imported by pipeline.labels. Included above, not added to it.
        "session_labels": labels_mod.session_label_counts(conn),
        "llm_ceiling_days": ceiling_days,
        "llm_calls_by_day": calls_by_day,
        "llm_daily_ceiling": config.LLM_DAILY_CALL_CEILING,
        # Spending, estimated from recorded token usage at published prices, and the calls today's
        # share of the monthly budget pays for at the batch price the scheduled runs use.
        # Global release distribution services, counted apart from every country. They are polled and
        # classified like any outlet; what they are not is a national media market. These counts
        # equal the worldwide distribution stratum, WLD, in latest.json: the block keeps its name so
        # nothing that reads it changes.
        "distribution_wires": {
            "outlets": sorted(registry.distribution_wire_ids(outlets)),
            "stratum": registry.WORLDWIDE,
            "counts": _wire_counts(conn, outlets),
            "note": "Global press release distribution services. Their releases are written by their issuers and pushed worldwide, so they are counted as the worldwide distribution stratum (WLD in latest.json), never attributed to the United States and never added into any country.",
        },
        "llm_budget": dict(llm_cost.month_to_date(conn),
                           budget_usd=config.LLM_MONTHLY_BUDGET_USD,
                           daily_cap_today=(cap := llm_cost.daily_cap(conn, batched=True)["cap"]),
                           # The cap is set from days before today, so the calls made today come off it.
                           calls_left_today=None if cap is None else max(0, min(cap, config.LLM_DAILY_CALL_CEILING) - store.llm_calls_today(conn)),
                           total_usd=round(llm_cost.spent(conn, "0000", "9999"), 2)),
        "llm_sampling_days": [r[0] for r in conn.execute("SELECT DISTINCT date FROM llm_sampling ORDER BY date")],
        "paywall_flag_share": config.PAYWALL_FLAG_SHARE,
        "ruleset_changes": ruleset_changes(),
        "gate_version": config.GATE_VERSION,
        "gate_changes": gate_changes(),
        "gate_applies_forward_only": True,
        "ruleset_mix": mix,
        # A label whose article can no longer be retrieved cannot be reclassified: the body is gone
        # and the fetch attempts are spent, so reclassify selects it forever and never changes it.
        # Completion therefore means everything retrievable is done, and the stuck count is published
        # rather than left to look like a backlog that is still moving.
        "labels_unreclassifiable": unreclassifiable,
        "reclassification_complete": set(mix) <= {config.RULESET_VERSION} or (
            sum(n for v, n in mix.items() if v != config.RULESET_VERSION) <= unreclassifiable),
        "routes": [{"id": r, "label": ROUTE_LABELS.get(r, r)} for r in classify_rules.ROUTE_IDS],
        "route_totals": route_totals,
        # A state origin label with no signature is not a route. It is the model asserting state origin
        # where the deterministic layer found nothing, which is a different kind of evidence and is
        # reported as its own stratum rather than as one more row in the route table. uncredited_copy
        # is a route: the lookalike check matched the article to a credited state origin item.
        "route_signature_totals": {k: v for k, v in route_totals.items() if k != "unattributed"},
        "model_only_state_origin": route_totals.get("unattributed", 0),
        "arrivals": [{"id": r, "label": ARRIVAL_LABELS.get(r, r)} for r in classify_rules.ARRIVAL_IDS],
        "arrival_totals": _arrival_totals(conn),
        "relay_collector": relay_status(conn, outlets),
        # missed_estimate is only from the polls the current estimator made (estimated_polls, from
        # missed_estimate_since); polls and saturated are over every poll on record.
        "feed_saturation": {"polls": sat[0], "saturated": sat[1], "missed_estimate": int(round(sat[2] or 0)),
                            "estimated_polls": sat[3] or 0, "missed_estimate_since": missed_estimate_since(conn),
                            "estimator": config.MISSED_ESTIMATOR_VERSION,
                            "warning_share": config.FEED_SATURATION_WARNING_SHARE},
        "release_sections": release_summary(outlets),
        # Byline flags against the persona list. A match, cited to a published report, never a
        # finding, and nothing in the categories or counts above depends on it. A failure there is
        # printed and becomes an empty block; it never stops the export.
        "planted": planted.meta_block_quietly(conn),
        # The candidate gate's miss rate, from the latest hand coded recall audit, or None when none has
        # run: the share of articles the rules settled without the model that are state origin or
        # unchecked state sourcing (pipeline/recall_audit.py).
        "candidate_recall": recall_audit.latest_quietly(conn),
        "categories": {
            "A": "State origin. Text authored or commissioned by a Chinese state entity and published without substantive editorial alteration.",
            "B": ("Unchecked state sourcing. Written by the local outlet, and its central content is a factual claim "
                  "attributed to a Chinese official or state source (an assertion of fact about events, numbers or actions), "
                  "passed on without independent confirmation. A stated position, policy, warning, threat, opinion or denial "
                  "is not a factual claim, and reporting one is independent journalism even without outside comment."),
            "C": ("Independent journalism. The outlet's own reporting, including reporting that quotes Chinese officials but "
                  "confirms, contextualizes or contests what they say, and reporting of a position, policy, warning, threat, "
                  "opinion or denial attributed to a Chinese official or state source, with or without outside comment."),
            "not_relevant": "Does not concern China.",
        },
    }


def write_json(path: Path, data) -> None:
    """Write via a same-directory temp file and rename onto path, so a reader never observes a
    half-written file: os.replace is atomic on both POSIX and Windows, unlike a direct open("w")."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_audit_files(conn, audit_dir: Path = config.EXPORT_DIR_AUDIT) -> Dict:
    """Append-only audit trail committed to git: every current classification with its article's
    metadata, one JSON line per article, in monthly files rewritten in full each export."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """SELECT a.id, a.url, a.url_hash, a.outlet_id, a.country, a.language, a.title, a.published_at, a.discovered_at, a.day_of_record,
                  a.status, a.dup_group_id, a.themes, a.gate_terms, c.category, c.method, c.confidence, c.evidence_quote, c.reasoning,
                  c.signatures_fired, c.china_sources_cited, c.model_version, c.ruleset_version, c.classified_at, c.route,
                  (SELECT human_category FROM human_reviews h WHERE h.article_id=a.id ORDER BY h.id DESC LIMIT 1) AS human_category
           FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 ORDER BY a.id"""
    ).fetchall()
    by_day = defaultdict(list)
    for r in rows:
        it = dict(r)
        if not it.get("day_of_record"):
            it.pop("day_of_record", None)   # set only on backfilled items; existing lines stay unchanged
        by_day[_day(r)].append(it)
    written = {}
    for day, items in by_day.items():
        path = audit_dir / ("articles-%s.jsonl" % day)
        body = "".join(json.dumps(it, ensure_ascii=False, sort_keys=True) + "\n" for it in items)
        # Only rewrite a day that changed. A month in one file meant a fresh multi megabyte blob in
        # every export commit, twice a day, and a repository that grew by that much each time.
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            tmp = path.with_name(path.name + ".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(body)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        written[day] = len(items)
    # The monthly files this replaced would otherwise sit in the tree forever, stale.
    for old in audit_dir.glob("articles-[0-9][0-9][0-9][0-9]-[0-9][0-9].jsonl"):
        old.unlink()
    return written


def housekeeping(conn) -> Dict:
    """The irreversible part of an export: dropping gated out rows past their retention and
    compacting the file. Kept apart from writing the files so it can run after the generated
    files have been validated and committed, never before. A failed check must not leave the
    database advanced and the site stale."""
    pruned = store.prune_gated_out(conn)
    store.vacuum(conn)
    return {"pruned_gated_out": pruned, "vacuumed": True}


def run(conn, run_id: str = "export", export_dir: Path = config.EXPORT_DIR,
        audit_dir: Path = config.EXPORT_DIR_AUDIT, methodology_path: Optional[Path] = None,
        prune: bool = True) -> Dict:
    """Write every generated file. All output locations are parameters so tests never touch
    the repository's own files. methodology_path defaults to METHODOLOGY.md at the root, and
    the same text is copied into the site so the interface can link it. prune=False leaves the
    irreversible housekeeping for a later call, once the files have been checked."""
    log_id = store.start_stage(conn, run_id, "export")
    # Items from feeds that stopped carrying their outlet's news (registry junk_feed) are withdrawn with
    # their labels, and their discovery taken off the per day counts the rollups read, before the prune
    # (pipeline/junk_feeds.py).
    from pipeline import junk_feeds
    junk = junk_feeds.run(conn)
    pruned = store.prune_gated_out(conn) if prune else 0
    # Articles collected through the section feeds removed on 2026-10-12 lose the section tag and
    # face the keyword gate; nothing is left to do once that has run (pipeline/section_retag.py).
    from pipeline import section_retag
    retagged = section_retag.run(conn)
    tagged = themes_mod.ensure(conn)
    routed = classify_rules.ensure_routes(conn)
    authors_cleaned = extract.clean_stored_authors(conn)
    planted_counts = planted.scan_quietly(conn)
    outlets = registry.load_outlets()
    gaps = registry.load_gaps()
    roll = rebuild_rollups(conn, outlets)
    latest = build_latest(conn, outlets, gaps)
    daily = build_daily(conn, outlets)
    series = build_global_series(daily)
    outlets_json = build_outlets(conn, outlets)
    articles = build_articles(conn, outlets=outlets)
    examples = build_examples(conn, outlets=outlets)
    meta = build_meta(conn, outlets, gaps, latest, outlets_json)
    write_json(export_dir / "latest.json", latest)
    for month, m in daily.items():
        write_json(export_dir / "daily" / ("%s.json" % month), m)
    # A month that no longer has any days must not be left behind. Moving the day of record to the
    # discovery day emptied August, and a stale file would sit in the published directory forever.
    for stale in (export_dir / "daily").glob("*.json"):
        if stale.stem not in daily:
            stale.unlink()
    write_json(export_dir / "global_series.json", series)
    write_json(export_dir / "outlets.json", outlets_json)
    # One file per country in latest.json, empty where nothing is classified, so the page never 404s.
    for country in latest["countries"]:
        write_json(export_dir / "articles" / ("%s.json" % country), articles.get(country, []))
    write_json(export_dir / "meta.json", meta)
    write_json(export_dir / "examples.json", examples)
    # A failure in the planted export is printed and leaves an empty planted.json, never a stop.
    write_json(export_dir / "planted.json", planted.build_export_quietly(conn))
    counts = {"planted": planted_counts,"countries": len(latest["countries"]), "months": len(daily), "days": len(series), "articles_files": len(articles),
              "pruned_gated_out": pruned, "themes_tagged": tagged, "routes_filled": routed, "authors_cleaned": authors_cleaned, "audit_files": write_audit_files(conn, audit_dir)}
    counts.update(roll)
    if retagged:
        counts["section_retag"] = retagged
    if junk:
        counts["junk_feeds"] = junk
    store.finish_stage(conn, log_id, True, counts)
    if prune:
        store.vacuum(conn)
    from pipeline import methodology
    mpath = methodology_path or (config.ROOT / "METHODOLOGY.md")
    methodology.write(meta, latest, mpath)
    methodology.write(meta, latest, export_dir.parent / "METHODOLOGY.md")
    counts["methodology"] = True
    return counts


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="export", choices=["export", "housekeeping"])
    ap.add_argument("--no-prune", action="store_true", help="leave pruning and vacuuming for the housekeeping command")
    args = ap.parse_args()
    conn = store.connect()
    print(json.dumps(housekeeping(conn) if args.cmd == "housekeeping" else run(conn, prune=not args.no_prune)))
