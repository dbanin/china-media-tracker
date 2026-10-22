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
too few hours is marked so a sleeping laptop is never mistaken for a quiet country,
up to the day the relay was retired (config.RELAY_RETIRED_ON).
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
from pipeline import placements
from pipeline import model_only

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
    "unattributed": "No signature; model judgement alone (provisional)",
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
    """One row per placement with its current machine label and its latest human label. A stored row
    that is a copy of a placement another row stands for (a shared widget's copy at a second masthead,
    one page at two addresses, a part of a numbered photo series) is left out (pipeline/placements.py)."""
    rows = conn.execute(
        """SELECT a.id, a.country, a.outlet_id, a.language, a.title, a.url, a.published_at, a.discovered_at, a.day_of_record,
                  a.status, a.gate_relevant, a.dup_group_id, a.fail_reason, a.themes, a.gate_terms,
                  c.category AS m_cat, c.method AS m_method, c.confidence AS m_conf, c.evidence_quote,
                  c.reasoning, c.signatures_fired, c.classified_at, c.ruleset_version, c.model_version, c.route,
                  (SELECT human_category FROM human_reviews h WHERE h.article_id=a.id ORDER BY h.id DESC LIMIT 1) AS h_cat
           FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1"""
    ).fetchall()
    shadow = placements.shadowed(conn)
    # A state origin label that rests on the model's judgement alone is counted under its own code, M, while
    # the hand check holds it apart (pipeline/model_only.py, decision D1); the stored label is unchanged.
    held = model_only.held_apart()
    out = []
    for r in rows:
        if r["id"] in shadow:
            continue
        d = dict(r)
        d["m_cat"] = model_only.headline_category(d["m_cat"], d["route"], held, d["m_method"])
        out.append(d)
    return out


def rebuild_rollups(conn, outlets: Optional[List[Dict]] = None) -> Dict:
    store.rebuild_daily_discovery(conn)
    rows = article_rows(conn)
    now = store.utcnow()
    outlets = outlets if outlets is not None else registry.load_outlets()
    top_ids, _ = registry.top_outlets(outlets)
    wire_ids = registry.distribution_wire_ids(outlets)
    wires = {"articles": 0, "A": 0, "B": 0, "C": 0, "not_relevant": 0, "pending": 0, model_only.CODE: 0}
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
            if pending or r["m_cat"] in ("A", "B", "C", model_only.CODE):
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
    # M: state origin by the model's judgement alone, held apart from A (pipeline/model_only.py).
    return {"A": 0, "B": 0, "C": 0, "N": 0, "M": 0, "Ar": 0, "Al": 0, "Ah": 0, "Br": 0, "Bl": 0, "Bh": 0,
            "rev": 0, "cls": 0, "disc": 0, "rel": 0, "fetched": 0, "paywalled": 0, "failed": 0,
            "blocked": 0, "pending": 0, "uniqA": 0, "uniqAB": 0, "tdisc": 0, "ttarget": 0, "tchina": 0, "ta": 0, "tb": 0,
            "polls": 0, "sat": 0, "miss": 0, "mpolls": 0, "mnew": 0}


def _accumulate(target: Dict, cat: str, scope: str, row) -> None:
    if scope != "all":
        return
    short = {"A": "A", "B": "B", "C": "C", "not_relevant": "N", model_only.CODE: "M"}[cat]
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
# mnew is the new items those same polls found, the other half of the share of items missed
# (miss / (miss + mnew), _derive's miss_share): polls before the estimator are left out of both.
# Each row's estimate is rounded once, here, and every figure is a sum of those whole numbers, so the
# outlet, day, country and overall totals add up to one another (rounding each sum on its own gave three
# different totals, 31739, 31750 and a third, for the same polls).
_MISS_SQL = ("SUM(CASE WHEN estimator=:est THEN CAST(ROUND(missed_estimate) AS INTEGER) ELSE 0 END) miss, "
             "SUM(CASE WHEN estimator=:est THEN polls ELSE 0 END) mpolls, "
             "SUM(CASE WHEN estimator=:est THEN new_items ELSE 0 END) mnew")


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


def _feed_poll_days(conn, outlets: Optional[List[Dict]] = None) -> List[Dict]:
    """Per day and country: feed polls, saturated polls, the estimated items missed between polls, the
    polls that estimate covers and the new items they found (mpolls, mnew; see _MISS_SQL). Grouped by
    outlet first, so a distribution wire's polls are counted under the worldwide distribution stratum,
    WLD, like its items, and never under the country it is registered in."""
    wire_ids = registry.distribution_wire_ids(outlets if outlets is not None else registry.load_outlets())
    out: Dict[Tuple[str, str], Dict] = {}
    for r in conn.execute(
            """SELECT date, outlet_id, country, SUM(polls) polls, SUM(saturated) sat, %s
               FROM feed_polls GROUP BY date, outlet_id, country""" % _MISS_SQL,
            {"est": config.MISSED_ESTIMATOR_VERSION}):
        country = registry.WORLDWIDE if r["outlet_id"] in wire_ids else r["country"]
        t = out.setdefault((r["date"], country), {"date": r["date"], "country": country, "polls": 0, "sat": 0,
                                                  "miss": 0.0, "mpolls": 0, "mnew": 0})
        for k in ("polls", "sat", "miss", "mpolls", "mnew"):
            t[k] += r[k] or 0
    return [out[k] for k in sorted(out)]


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
    out = {"articles": 0, "A": 0, "B": 0, "C": 0, "not_relevant": 0, "pending": 0, model_only.CODE: 0}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    shadow = placements.shadowed(conn)
    held = model_only.held_apart()
    for r in conn.execute(
            """SELECT a.id, a.status, c.category cat, c.route, c.method, 1 AS n FROM articles a
               LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
               WHERE a.gate_relevant=1 AND a.outlet_id IN (%s)""" % marks,
            tuple(sorted(ids))):
        if r["id"] in shadow:
            continue
        out["articles"] += r["n"]
        if r["status"] in ("awaiting_llm", "llm_submitted"):
            out["pending"] += r["n"]
        elif r["cat"]:
            out[model_only.headline_category(r["cat"], r["route"], held, r["method"])] += r["n"]
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
    # The model-only placements (M) are China coverage the model judged state origin with no evidence
    # behind it: out of the state origin numerator while held apart, still in the China denominator.
    china = c["A"] + c["B"] + c["C"] + c.get("M", 0)
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
    # The estimated share of the feeds' items missed between polls, over the polls the estimate covers.
    seen = c.get("miss", 0) + c.get("mnew", 0)
    c["miss_share"] = round(c.get("miss", 0) / float(seen), 4) if c.get("mpolls") and seen else None
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
    seven-day window usually cuts it. The relay was retired on config.RELAY_RETIRED_ON, so no day
    after that one is marked: its outlets are inactive, not missing."""
    by_day = relay_hours_by_day(conn)
    if not by_day:
        return []
    today = dt.date.fromisoformat(today or _today())
    if config.RELAY_RETIRED_ON:
        today = min(today, dt.date.fromisoformat(config.RELAY_RETIRED_ON) + dt.timedelta(days=1))
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
    the hosted runner still polls on its own hourly schedule when the relay is silent.

    The relay was retired on config.RELAY_RETIRED_ON: from then on no outlet is relay only or shared,
    the relay is never stale, and dropped_outlets, dropped_by_country, countries_all_inactive and
    countries_partial say what it left behind: the outlets made inactive that day (registry.relay_dropped)
    and the countries where they were every active outlet, or only some."""
    retired = config.RELAY_RETIRED_ON
    relayed = [o for o in outlets if o.get("active") and registry.collector_of(o) == "self_hosted"]
    shared = [] if retired else [o for o in outlets if o.get("active") and registry.is_shared(o)]
    last = conn.execute("SELECT MAX(COALESCE(finished_at, started_at)) FROM relay_runs WHERE ok=1").fetchone()[0]
    hours = stale = None
    if last and not retired:
        t = dt.datetime.fromisoformat(last)
        t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        hours = round((dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600.0, 1)
        stale = hours >= config.RELAY_STALE_HOURS
    elif retired:
        stale = False
    dropped = [o for o in outlets if registry.relay_dropped(o)]
    by_country = defaultdict(int)
    for o in dropped:
        by_country[registry.export_country(o)] += 1
    active_by_country = defaultdict(int)
    for o in outlets:
        if o.get("active"):
            active_by_country[registry.export_country(o)] += 1
    return {"outlets": len(relayed), "countries": sorted({o["country"] for o in relayed}), "last_run": last,
            "shared_outlets": len(shared), "shared_countries": sorted({registry.export_country(o) for o in shared}),
            "hours_since_last_run": hours, "stale": stale, "incomplete_days": relay_incomplete_days(conn),
            "min_hours_per_day": config.RELAY_DAY_MIN_HOURS, "stale_after_hours": config.RELAY_STALE_HOURS,
            "retired_on": retired, "dropped_outlets": len(dropped), "dropped_by_country": dict(sorted(by_country.items())),
            "countries_all_inactive": sorted(c for c in by_country if not active_by_country.get(c)),
            "countries_partial": sorted(c for c in by_country if active_by_country.get(c))}


def language_support(languages, supported) -> str:
    """full when every language has its own term list, none when no language does. English is
    always supported, because every language is also matched on the English terms."""
    langs = [l for l in languages if l]
    if not langs:
        return "none"
    n = sum(1 for l in langs if l == "en" or l in supported)
    return "full" if n == len(langs) else ("partial" if n else "none")


# The words sources/outlets.yaml uses for an outlet left out by a project rule (the Chinese language rule),
# as opposed to one that is inactive because its feed failed.
EXCLUDED_BY_RULE = "excluded by project rule"


def excluded_languages(outlets: List[Dict], languages) -> Dict[str, int]:
    """Languages of a country's registered outlets that a project rule excludes and no active outlet
    reads, with the number of outlets in each: the country's press in that language is not read, so
    its language support is at most partial whatever its active outlets' keyword lists cover."""
    out: Dict[str, int] = defaultdict(int)
    for o in outlets:
        if not o.get("active") and EXCLUDED_BY_RULE in (o.get("inactive_reason") or "") \
                and o.get("language") and o["language"] not in languages:
            out[o["language"]] += 1
    return dict(out)


def feed_frozen(h: Optional[Dict], cutoff: str) -> bool:
    """A feed that answers but whose newest item (feed_health.newest_entry) is older than cutoff, the
    export time less config.FEED_FROZEN_DAYS: CityNews still served 2022 items. Unknown dates are not frozen."""
    return bool(h and h.get("newest_entry") and h["newest_entry"] < cutoff)


def feed_working(h: Optional[Dict], cutoff: str) -> bool:
    """A feed counted as healthy: its last poll succeeded and it is not frozen."""
    return bool(h and h["consecutive_failures"] == 0 and not feed_frozen(h, cutoff))


def frozen_cutoff_now() -> str:
    """The export time less config.FEED_FROZEN_DAYS, in feed_health's timestamp format."""
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=config.FEED_FROZEN_DAYS)).replace(microsecond=0).isoformat()


def _cap_partial(support: str, excluded: Dict[str, int]) -> str:
    return "partial" if support == "full" and excluded else support


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
           WHERE c.is_current=1 AND c.category='A'%s""" % model_only.evidence_sql()
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


TIED_NAMES_MAX = 5


def _leader(counts: Dict[str, int], total: int) -> Optional[Dict]:
    """The largest of counts and how far it leads, so a sentence built on it can say "most" only above half
    of total and report a tie as a tie (DECISIONS 6): id (the tie is broken by id, as before, so the
    field stays stable), count, total, share (count over total, the denominator every placement in the
    window, including any left out of counts), tied (how many entries share the top count, 1 when
    nobody does) and tied_ids (those entries, sorted). None when counts is empty."""
    if not counts:
        return None
    top = max(counts, key=lambda k: (counts[k], k))
    tied_ids = sorted(k for k, v in counts.items() if v == counts[top])
    return {"id": top, "count": counts[top], "total": total,
            "share": round(counts[top] / float(total), 4) if total else None,
            "tied": len(tied_ids), "tied_ids": tied_ids}


def _carrier_entry(oc: Dict[str, int], outlets: List[Dict]) -> Optional[Dict]:
    """The leading outlet of oc (_leader over every outlet's placements) with its name, and, when every
    tied outlet belongs to one network (an outlet's optional "network" field, the mastheads one owner
    syndicates across), that network's name, so a tie reads "spread evenly across 10 ACM papers"."""
    lead = _leader(oc, sum(oc.values()))
    if not lead:
        return None
    by_id = {o["id"]: o for o in outlets}
    nets = {(by_id.get(i) or {}).get("network") for i in lead["tied_ids"]}
    lead["name"] = (by_id.get(lead["id"]) or {}).get("name", lead["id"])
    lead["network"] = nets.pop() if len(nets) == 1 and None not in nets else None
    # A tie of many is reported by its size and network; only a short tie is named outlet by outlet.
    lead["tied_ids"] = lead["tied_ids"][:TIED_NAMES_MAX]
    lead["tied_names"] = [(by_id.get(i) or {}).get("name", i) for i in lead["tied_ids"]]
    return lead


def _route_entry(rc: Dict[str, int]) -> Optional[Dict]:
    """The leading signed route of rc. A route with no signature ("unattributed") is not a route and never
    leads, but it stays in the denominator and is reported beside it as unattributed, so "most" is said
    only when the route carried over half of every placement, signed or not."""
    signed = {k: v for k, v in rc.items() if k != "unattributed"}
    lead = _leader(signed, sum(rc.values()))
    if lead:
        lead["unattributed"] = rc.get("unattributed", 0)
    return lead


def _country_top_carriers(conn, outlets: List[Dict], since30: str, until: Optional[str] = None) -> Dict[str, Dict]:
    """Per country, the outlet and the route that carried most of its state origin count on the days
    since30 <= day < until (no end when until is None), so a map tooltip can say what carried the number.
    build_latest passes the 30 complete days of complete_window_30d, the window the map opens on, so the
    partial export day is in neither the count nor the figure beside it. Distribution wires are
    counted under the worldwide distribution stratum, exactly as every country figure places them.
    A route with no signature at all is not a route (see route_signature_totals in build_meta) and
    is never reported here as one, but it stays in the route's denominator (_route_entry). Each entry
    carries its count, the window's total, its share and its ties (_leader), so the tooltip says
    "mostly" only above half and a tie as a tie. A placement on a shared widget counts once, under the
    widget (placements.carrier_id), whose entry carries shared_widget true."""
    wire_ids = registry.distribution_wire_ids(outlets)
    shadow = placements.shadowed(conn)
    carriers = outlets + placements.carrier_outlets(outlets)
    outlet_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    route_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in conn.execute(
            """SELECT a.id, a.url, a.country, a.outlet_id, c.route FROM articles a JOIN classifications c
               ON c.article_id=a.id AND c.is_current=1
               WHERE c.category='A'%s AND COALESCE(a.day_of_record, substr(a.discovered_at, 1, 10))>=?
               AND COALESCE(a.day_of_record, substr(a.discovered_at, 1, 10))<?""" % model_only.evidence_sql(),
            (since30, until or "9999-12-31")):
        if r["id"] in shadow:
            continue
        country = registry.WORLDWIDE if r["outlet_id"] in wire_ids else r["country"]
        outlet_counts[country][placements.carrier_id(r)] += 1
        route_counts[country][r["route"] or "unattributed"] += 1
    out = {}
    widget_ids = {w["id"] for w in carriers if w.get("shared_widget")}
    for country in set(outlet_counts) | set(route_counts):
        out[country] = {
            "top_carrier": _carrier_entry(dict(outlet_counts.get(country, {})), carriers),
            "top_route": _route_entry(dict(route_counts.get(country, {}))),
        }
        if out[country]["top_carrier"] and out[country]["top_carrier"]["id"] in widget_ids:
            out[country]["top_carrier"]["shared_widget"] = True
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
    frozen_cutoff = frozen_cutoff_now()
    # Outlets that delivered at least one item, relevant or not, in the last 30 days (outlets_working).
    delivered30 = {r[0] for r in conn.execute(
        "SELECT outlet_id FROM daily_outlet_discovery WHERE date>=? GROUP BY outlet_id HAVING SUM(discovered)>0",
        (since30,))}
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
    for r in _feed_poll_days(conn, outlets):
        for target, ok in ((all_time, True), (last30, r["date"] >= since30)):
            if ok:
                t = target[r["country"]]
                t["polls"] += r["polls"] or 0; t["sat"] += r["sat"] or 0; t["miss"] += int(round(r["miss"] or 0))
                t["mpolls"] += r["mpolls"] or 0; t["mnew"] += r["mnew"] or 0
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
        # A frozen feed (feed_frozen) answers with old items and counts as failing, here and in the warnings.
        feeds_frozen = sum(1 for f in feeds if feed_frozen(feed_health.get(f), frozen_cutoff))
        feeds_ok = sum(1 for f in feeds if feed_working(feed_health.get(f), frozen_cutoff))
        # An outlet is working when one of its editorial feeds works and it delivered items in the last 30 days.
        working = [o for o in active if o["id"] in delivered30 and any(
            feed_working(feed_health.get(f["url"]), frozen_cutoff) for f in registry.feed_entries(o)
            if f.get("kind", registry.EDITORIAL) == registry.EDITORIAL)]
        languages = sorted({o["language"] for o in active})
        excluded = {} if worldwide else excluded_languages(os_, languages)
        relayed = sum(1 for o in active if registry.collector_of(o) == "self_hosted")
        # Since the relay was retired no outlet is polled from the owner's machine, collector: both included.
        shared = 0 if config.RELAY_RETIRED_ON else sum(1 for o in active if registry.is_shared(o))
        dropped = sum(1 for o in os_ if registry.relay_dropped(o))
        entry = {
            "coverage": "monitored" if active else "no_active_outlets",
            "outlets_total": len(os_), "outlets_active": len(active),
            "feeds_total": len(feeds), "feeds_ok": feeds_ok, "feeds_frozen": feeds_frozen,
            "outlets_working": len(working), "no_working_outlet": bool(active) and not working,
            "excluded_languages": excluded,
            "section_feeds_total": len(section_urls),
            "section_feeds_ok": sum(1 for f in section_urls if feed_health.get(f) and feed_health[f]["consecutive_failures"] == 0),
            # The stratum has no population.
            "population": None if worldwide else population.get(country),
            "top_outlets": len(top_ids.get(country, ())), "top_outlets_ranked": country in ranked,
            "languages": languages,
            # Every release is matched on its own language and on English, and the stratum is not a
            # country whose press could be unreadable, so its support is recorded as full.
            # A language excluded by project rule (excluded_languages) makes full support partial.
            "language_support": "full" if worldwide else _cap_partial(language_support(languages, gate_langs), excluded),
            "theme_language_support": "full" if worldwide else _cap_partial(language_support(languages, theme_langs), excluded),
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
            frozen_note = (", %d of them frozen, answering with no item newer than %d days" % (feeds_frozen, config.FEED_FROZEN_DAYS)
                           if feeds_frozen else "")
            entry["warnings"].append({"type": "feeds_failing", "text": "%d of %d feeds are failing%s" % (len(feeds) - feeds_ok, len(feeds), frozen_note)})
        pw = at["paywall_share"]
        if pw is not None and pw >= config.PAYWALL_FLAG_SHARE and at["rel"] >= 10:
            entry["warnings"].append({"type": "paywalled", "text": "%d percent of retrieved articles were paywalled and could not be read. They are missing from every count, but they stay in the share of monitored output denominator, which counts everything the outlets published, so that share is a lower bound here and counts are not comparable to other countries" % round(pw * 100)})
        attempted = at["fetched"] + at["paywalled"] + at["failed"] + at["blocked"]
        if attempted >= 10 and (at["failed"] + at["blocked"]) / float(attempted) >= config.PAYWALL_FLAG_SHARE:
            entry["warnings"].append({"type": "fetch_failing", "text": "%d percent of article fetches failed or were blocked by robots.txt; counts understate this country" % round(100.0 * (at["failed"] + at["blocked"]) / attempted)})
        # Every reason an article could not be read, together: paywalls, failed fetches and robots.txt
        # blocks each below their own threshold can still leave half a country unread (Ireland, 2026-10-17).
        unread = at["paywalled"] + at["failed"] + at["blocked"]
        if attempted >= 10 and unread / float(attempted) >= config.UNREADABLE_WARNING_SHARE:
            entry["warnings"].append({"type": "unreadable", "text": "%d percent of this country's China relevant articles could not be read (%d paywalled, %d failed, %d blocked by robots.txt, of %d attempted); they are missing from every count, so counts understate this country and are not comparable to other countries" % (round(100.0 * unread / attempted), at["paywalled"], at["failed"], at["blocked"], attempted)})
        pending = at["pending"]
        judged_china = at["A"] + at["B"] + at["C"] + at.get("M", 0)
        if pending >= 5 and pending >= 0.25 * max(at["rel"], 1):
            entry["warnings"].append({"type": "llm_backlog", "text": "%d articles carrying official Chinese sourcing are awaiting the verification judgement, so the unchecked state sourcing count is a floor" % pending})
        # A country whose unjudged pile rivals what has been judged cannot be compared with one whose
        # backlog is small: the difference between them is partly the backlog, not the world. This is
        # the state the whole map is in while the model budget is spent.
        if pending >= 5 and pending >= 0.5 * max(judged_china, 1):
            entry["warnings"].append({"type": "not_comparable", "text": "%d articles are still unjudged against %d judged, so this country's unchecked state sourcing figure is not comparable with countries whose backlog is smaller" % (pending, judged_china)})
        # Warned on the estimated share of items missed (config.FEED_MISSED_WARNING_SHARE), which is what a
        # reader loses; the share of saturated polls alone understated it and still warns on its own.
        ms = at["miss_share"]
        missed_high = at["mpolls"] >= 10 and ms is not None and ms >= config.FEED_MISSED_WARNING_SHARE
        sat_high = at["polls"] >= 10 and at["sat"] / float(at["polls"]) >= config.FEED_SATURATION_WARNING_SHARE
        if missed_high or (sat_high and ms is not None):
            entry["warnings"].append({"type": "feed_saturation", "text": "an estimated %d percent of this country's feed items were lost between polls: %s, against %d found; %d percent of its feed polls came back as a full window with nothing seen before, so its counts understate what its outlets published" % (round(100.0 * ms), missed_text(at["miss"], at["mpolls"], at["polls"], miss_since), at["mnew"], round(100.0 * at["sat"] / at["polls"]))})
        elif sat_high:
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
        # The relay's outlets, made inactive the day it was retired: a gap from that day on, never a decline.
        if dropped and active:
            entry["warnings"].append({"type": "relay_stopped", "text": "%d of this country's outlets refuse the crawler from its server addresses and were collected from the owner's network until %s, when that collection stopped; they are inactive since, so counts from that day cover its other %d active outlets only" % (dropped, config.RELAY_RETIRED_ON, len(active))})
        elif dropped:
            entry["warnings"].append({"type": "relay_stopped", "text": "every outlet monitored here refuses the crawler from its server addresses and was collected from the owner's network until %s, when that collection stopped; %s inactive since, so nothing here is monitored from that day" % (config.RELAY_RETIRED_ON, "it is" if dropped == 1 else "they are")})
        if active and entry["language_support"] == "none":
            entry["warnings"].append({"type": "unsupported_language", "text": "no keyword list exists for %s, so only international and English terms are matched; an empty map here says little" % ", ".join(languages)})
        if not active:
            entry["warnings"].append({"type": "no_active_outlets", "text": "all registered outlets are inactive"})
        if active and excluded:
            entry["warnings"].append({"type": "language_excluded", "text": "%d registered outlets publishing in %s are excluded by project rule, so this country's press in %s is not read and its figures cover its other languages only" % (sum(excluded.values()), ", ".join(sorted(excluded)), "that language" if len(excluded) == 1 else "those languages")})
        if active and not working:
            entry["warnings"].append({"type": "no_working_outlet", "text": "none of the %d active outlets delivered an item through a working feed in the last 30 days, so nothing is being collected here; its zeros are absence of data, not absence of coverage" % len(active)})
        elif active and not worldwide and entry["last_30d"]["rel"] < config.MIN_RELEVANT_ITEMS_30D:
            entry["warnings"].append({"type": "few_items", "text": "only %d China relevant items in the last 30 days, too few to compare with other countries: a share or a rank here can move on a handful of articles" % entry["last_30d"]["rel"]})
        countries[country] = entry
    # A gaps file entry applies wherever no active outlet exists: to a country with no outlet at all, and
    # to one whose registered outlets are all inactive (Bouvet Island), which keeps its stated reason. A
    # country with an active outlet is monitored whatever the gaps file says (registry tests keep it so).
    for g in gaps:
        if g["country"] in countries and not countries[g["country"]]["outlets_active"]:
            countries[g["country"]]["coverage"] = "gap"
            countries[g["country"]]["gap_reason"] = g["reason"]
        elif g["country"] not in countries:
            countries[g["country"]] = {"coverage": "gap", "gap_reason": g["reason"], "outlets_total": 0, "outlets_active": 0,
                                       "feeds_total": 0, "feeds_ok": 0, "feeds_frozen": 0, "outlets_working": 0,
                                       "no_working_outlet": False, "excluded_languages": {},
                                       "population": population.get(g["country"]),
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
    # last_30d runs from window_start_30d through window_end_30d, both included, and the last of them is the
    # export day, still partial (window_end_30d_partial). The findings, the map and the ranked list read the
    # 30 complete days before it instead (complete_window_30d, lastCompleteDay in docs/app.js).
    return {"generated_at": store.utcnow(), "window_start_30d": since30, "window_end_30d": today.isoformat(),
            "window_end_30d_partial": True, "countries": countries, "totals": totals}


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
    for r in _feed_poll_days(conn, outlets):
        t = months[r["date"][:7]]["days"][r["date"]][r["country"]]
        t["polls"] += r["polls"] or 0; t["sat"] += r["sat"] or 0; t["miss"] += int(round(r["miss"] or 0))
        t["mpolls"] += r["mpolls"] or 0; t["mnew"] += r["mnew"] or 0
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
        elif cat == model_only.CODE:
            # Held apart from state origin: the route filter's model judgement option draws them as a
            # provisional layer of their own, and they are in no arrival count.
            routes[d][country][model_only.ROUTE] += 1
        if not (pending or cat in ("A", "B", "C", model_only.CODE)):
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


SERIES_KEYS = ("A", "B", "C", "N", "M", "rel", "paywalled", "pending")


def build_global_series(daily: Dict[str, Dict]) -> List[Dict]:
    series = []
    for month in sorted(daily):
        for d, day in sorted(daily[month]["days"].items()):
            tot = {"date": d, "A": 0, "B": 0, "C": 0, "N": 0, "M": 0, "rel": 0, "paywalled": 0, "pending": 0,
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
    held = model_only.held_apart()
    for r in conn.execute(
        """SELECT a.outlet_id, c.category, c.method, c.route, COUNT(*) n,
                  SUM(CASE WHEN EXISTS(SELECT 1 FROM human_reviews h WHERE h.article_id=a.id) THEN 1 ELSE 0 END) rev
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 GROUP BY a.outlet_id, c.category, c.method, c.route"""
    ):
        t = per_outlet[r["outlet_id"]]
        # A model-only state origin label is counted as M while held apart (pipeline/model_only.py).
        cat = model_only.headline_category(r["category"], r["route"], held, r["method"])
        short = {"A": "A", "B": "B", "C": "C", "not_relevant": "N", model_only.CODE: "M"}[cat]
        t[short] += r["n"]; t["cls"] += r["n"]; t["rev"] += r["rev"]
        if cat in ("A", "B"):
            t[cat + ("r" if r["method"] == "rules" else "l")] += r["n"]
    # A copy another row stands for (pipeline/placements.py) is not this outlet's placement: it leaves the
    # counts, so that the outlets' counts add up to their country's, and is listed under not_counted.
    not_counted = defaultdict(lambda: {"A": 0, "B": 0, "C": 0, "N": 0, "M": 0})
    shadow = sorted(placements.shadowed(conn))
    for i in range(0, len(shadow), 900):
        part = shadow[i:i + 900]
        for r in conn.execute(
                """SELECT a.outlet_id, c.category, c.method, c.route,
                          EXISTS(SELECT 1 FROM human_reviews h WHERE h.article_id=a.id) rev
                   FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
                   WHERE a.id IN (%s)""" % ",".join("?" * len(part)), part):
            t = per_outlet[r["outlet_id"]]
            cat = model_only.headline_category(r["category"], r["route"], held, r["method"])
            short = {"A": "A", "B": "B", "C": "C", "not_relevant": "N", model_only.CODE: "M"}[cat]
            t[short] -= 1; t["cls"] -= 1; t["rev"] -= 1 if r["rev"] else 0
            if cat in ("A", "B"):
                t[cat + ("r" if r["method"] == "rules" else "l")] -= 1
            not_counted[r["outlet_id"]][short] += 1
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
        # Rows stored now (a prune removes gated out rows), not the items discovered: that is "disc" below.
        t["stored"] = r["disc"]; t["rel"] = r["rel"] or 0; t["fetched"] = r["fetched"] or 0
        t["paywalled"] = r["paywalled"] or 0; t["failed"] = r["failed"] or 0; t["blocked"] = r["blocked"] or 0
        t["pending"] = r["pending"] or 0
    # Items discovered, from the discovery counters the daily files and latest.json read, so an outlet's
    # disc is the same quantity as a country's.
    for r in conn.execute("SELECT outlet_id, SUM(discovered) disc FROM daily_outlet_discovery GROUP BY outlet_id"):
        per_outlet[r["outlet_id"]]["disc"] = r["disc"] or 0
    for r in conn.execute("SELECT outlet_id, SUM(polls) polls, SUM(saturated) sat, %s FROM feed_polls GROUP BY outlet_id"
                          % _MISS_SQL, {"est": config.MISSED_ESTIMATOR_VERSION}):
        t = per_outlet[r["outlet_id"]]
        t["polls"] = r["polls"] or 0; t["sat"] = r["sat"] or 0; t["miss"] = int(round(r["miss"] or 0))
        t["mpolls"] = r["mpolls"] or 0; t["mnew"] = r["mnew"] or 0
    out = []
    cutoff = frozen_cutoff_now()
    for o in outlets:
        feeds = []
        for fe in registry.feed_entries(o):
            h = feed_health.get(fe["url"])
            kind = fe.get("kind", registry.EDITORIAL)
            # An editorial feed answering with old items only is frozen and not ok (feed_frozen); a quiet
            # section feed is not a failing one, so the freeze is not applied to it.
            frozen = kind == registry.EDITORIAL and feed_frozen(h, cutoff)
            feeds.append({"url": fe["url"], "kind": kind, "ok": bool(h and h["consecutive_failures"] == 0) and not frozen,
                          "last_ok": h["last_ok"] if h else None, "last_error": h["last_error"] if h else None,
                          "consecutive_failures": h["consecutive_failures"] if h else None,
                          "newest_entry": h.get("newest_entry") if h else None, "frozen": frozen})
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
                    # Copies counted through another outlet's row (pipeline/placements.py); None when there are none.
                    "not_counted": dict(not_counted[o["id"]]) if o["id"] in not_counted else None,
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


ORDER = {"A": 0, "B": 1, model_only.CODE: 2, "pending": 3, "C": 4}


def build_articles(conn, per_country: int = 80, outlets: Optional[List[Dict]] = None) -> Dict[str, List[Dict]]:
    """Per country: state origin first, then unchecked state sourcing, then articles carrying official
    Chinese sourcing whose verification judgement is pending, then independent journalism."""
    rows = conn.execute(
        """SELECT a.id, a.country, a.outlet_id, a.title, a.url, a.published_at, a.discovered_at, a.day_of_record, a.dup_group_id,
                  a.status, a.llm_trigger, a.themes, a.gate_terms,
                  c.category, c.method, c.confidence, c.evidence_quote, c.reasoning, c.signatures_fired, c.model_version,
                  c.ruleset_version, c.china_sources_cited, c.route, c.issuer_type, c.needs_review,
                  (SELECT human_category FROM human_reviews h WHERE h.article_id=a.id ORDER BY h.id DESC LIMIT 1) AS human_category
           FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 AND (c.category IN ('A','B','C') OR a.status IN ('awaiting_llm','llm_submitted'))
           ORDER BY COALESCE(a.published_at, a.discovered_at) DESC"""
    ).fetchall()
    out = defaultdict(list)
    wire_ids = registry.distribution_wire_ids(outlets if outlets is not None else registry.load_outlets())
    from pipeline import body_dups
    items = body_dups.item_groups(conn)
    # One entry per placement: a copy another row stands for is left out, and the row that stands for
    # copies says how many stored rows it counts for (copies) and, on a shared widget, which one.
    shadow = placements.shadowed(conn)
    copies = placements.copies_of(conn)
    held = model_only.held_apart()
    for r in rows:
        if r["id"] in shadow:
            continue
        # A release wire's items are not a country's articles: they are listed under WLD.
        country = registry.WORLDWIDE if r["outlet_id"] in wire_ids else r["country"]
        pending = r["category"] is None
        # A model-only state origin label is listed as M while held apart (pipeline/model_only.py).
        cat = "pending" if pending else model_only.headline_category(r["category"], r["route"], held, r["method"])
        entry = {
            "id": r["id"], "outlet_id": r["outlet_id"], "title": r["title"], "url": r["url"],
            "date": _day(r), "category": cat, "human_category": r["human_category"],
            "provenance": "human" if r["human_category"] else ("rules" if pending else r["method"]),
            "confidence": r["confidence"],
            "evidence_quote": _cap_quote(r["evidence_quote"], (json.loads(r["signatures_fired"] or "[]") or [None])[0]),
            "reasoning": r["reasoning"], "signatures": json.loads(r["signatures_fired"] or "[]"),
            "sources": json.loads(r["china_sources_cited"] or "[]"),
            "model": r["model_version"], "ruleset": r["ruleset_version"], "dup_group": items.get(r["dup_group_id"] or r["id"], r["dup_group_id"]),
            "themes": themes_mod.themes_of(r["themes"]),
            "route": r["route"] if cat == "A" else None,
            "issuer_type": r["issuer_type"],
            "needs_review": r["needs_review"],
            "arrival": classify_rules.arrival_of(r["gate_terms"]),
        }
        if r["id"] in copies:
            entry["copies"] = copies[r["id"]]
            widget = placements.widget_of(r["url"])
            if widget:
                entry["shared_widget"] = widget["id"]
        if pending and r["llm_trigger"]:
            try:
                trig = json.loads(r["llm_trigger"])
            except ValueError:
                trig = {}
            entry["sources"] = _trigger_sources(trig.get("triggers", []))
            entry["evidence_quote"] = _cap_quote((trig.get("spans") or [None])[0],
                                                 ((trig.get("triggers") or []) + (trig.get("a_candidate") or []) or [None])[0])
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


# Evidence quotes published on the site and in the audit files are cut to about this many words (a
# character of a script written without spaces counts half), so a quote stays a quotation and never a
# passage: a few model and rules quotes ran to hundreds of words. The database keeps the whole quote.
QUOTE_MAX_WORDS = 40


def _cap_quote(quote: Optional[str], pattern_id: Optional[str] = None, max_words: int = QUOTE_MAX_WORDS) -> Optional[str]:
    """quote cut to at most max_words words on word boundaries, with an ellipsis where text was left
    out. When pattern_id names the signature or trigger that matched inside the quote, the cut keeps
    the matched phrase whole and centres on it (_trim_to_words), as the worked examples do; otherwise
    the quote keeps its opening words. A quote within the limit is returned as it is."""
    if not quote or not isinstance(quote, str):
        return quote
    tokens = list(_SNIPPET_TOKEN.finditer(quote))
    if sum(0.5 if _NO_SPACE_CHAR.match(m.group()) else 1.0 for m in tokens) <= max_words:
        return quote
    span, left, right = _unmark_span(quote)
    try:
        offset = _match_offset(pattern_id, span) if pattern_id else None
    except Exception:  # noqa: BLE001  a pattern that cannot be read again only loses the centring
        offset = None
    if offset and sum(0.5 if _NO_SPACE_CHAR.match(m.group()) else 1.0
                      for m in _SNIPPET_TOKEN.finditer(span[offset[0]:offset[1]])) > max_words - 2:
        offset = None           # a proximity match longer than the cap itself: the opening words are quoted
    if offset:
        snippet, _, _ = _trim_to_words(span, offset[0], offset[1], max_words=max_words,
                                       cut_left=left == "cut", cut_right=right == "cut",
                                       gap_left=left == "word", gap_right=right == "word")
        return snippet
    words = list(_SNIPPET_TOKEN.finditer(span))
    lead = left is not None
    budget, used, last = max_words - 1 - (1 if lead else 0), 0.0, None
    for i, m in enumerate(words):
        c = 0.5 if _NO_SPACE_CHAR.match(m.group()) else 1.0
        if used + c > budget:
            break
        if left == "cut" and i == 0 and len(words) > 1:
            continue            # a word cut off at the stored span's edge is not quoted
        used += c
        last = m
    if last is None:
        return quote
    start = words[1].start() if left == "cut" and len(words) > 1 else words[0].start()
    body = span[start:last.end()]
    prefix = (_ELLIPSIS + ("" if _NO_SPACE_CHAR.match(body[:1]) else " ")) if lead else ""
    suffix = ("" if _NO_SPACE_CHAR.match(body[-1:]) else " ") + _ELLIPSIS
    return prefix + body + suffix


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
               WHERE c.category=?%s AND c.ruleset_version=? AND a.status<>'paywalled'
               ORDER BY COALESCE(a.day_of_record, a.discovered_at) DESC""" % (model_only.evidence_sql() if cat == "A" else ""),
            (cat, config.RULESET_VERSION),
        ).fetchall()
        picked = []
        seen_countries, seen_outlets = set(), set()
        shadow = placements.shadowed(conn)
        for r in rows:
            if r["outlet_id"] in wire_ids or r["id"] in shadow:
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
        # Outlets with no sourced owner are not a group a reader can compare against, so they never lead.
        if g != "unassessed" and rate is not None and (top is None or rate > top["per_outlet_rate"]):
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
    in that window, its distinct underlying items, the outlet that carried the most of it and the signed
    route that carried the most of it, each with its share and ties (_carrier_entry, _route_entry). The
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
    # A copy another row stands for is not counted, and a shared widget's placement counts under the
    # widget, as in _country_top_carriers.
    shadow = placements.shadowed(conn)
    carriers = outlets + placements.carrier_outlets(outlets)
    for r in conn.execute(
            """SELECT a.id, a.url, a.outlet_id, c.route FROM articles a JOIN classifications c
               ON c.article_id=a.id AND c.is_current=1
               WHERE a.country=? AND c.category='A'%s AND COALESCE(a.day_of_record, substr(a.discovered_at, 1, 10))>=?
               AND COALESCE(a.day_of_record, substr(a.discovered_at, 1, 10))<?%s""" % (model_only.evidence_sql(), where), params):
        if r["id"] in shadow:
            continue
        outlet_counts[placements.carrier_id(r)] += 1
        route_counts[r["route"] or "unattributed"] += 1
    carrier = _carrier_entry(dict(outlet_counts), carriers)
    if carrier and any(w["id"] == carrier["id"] for w in carriers if w.get("shared_widget")):
        carrier["shared_widget"] = True
    route = _route_entry(dict(route_counts))
    # top_outlet keeps id and name first and top_route stays the route id, as before; the counts, shares
    # and ties beside them are new, and a front end that predates them reads only the old fields.
    return {
        "iso": top_iso,
        "state_origin": by_country[top_iso],
        "underlying": _underlying_in_window(conn, outlets, top_iso, since30, until),
        "share_of_world": round(by_country[top_iso] / world, 4) if world else None,
        "top_outlet": carrier,
        "top_route": route["id"] if route else None,
        "top_route_detail": route,
    }


def _underlying_in_window(conn, outlets: List[Dict], iso: str, start: str, end: str) -> Optional[int]:
    """The distinct state origin underlying items of one country on the days start <= day < end, the
    figure written beside its placements: the union of underlying_item_ids over those days, so whatever
    that function counts as one item (syndicated copies, one item over several days) counts once here.
    None when it cannot be computed, and the sentence then leaves the items out rather than guess."""
    try:
        rows = [r for r in article_rows(conn) if r["country"] == iso]
        ids = underlying_item_ids(conn, outlets, rows)
    except Exception:
        return None
    items = set()
    for d, per_country in ids.items():
        if start <= d < end:
            items |= (per_country.get(iso) or {}).get("A", set())
    return len(items)


def _model_only_findings(conn, tot: Dict, wld_all: Dict, start30: str, until30: str) -> Dict:
    """findings.model_only: the model-only placements all time and in the 30 complete days of last_30d, each
    with its worldwide wires part, from the same daily_counts rows (code M) the daily files carry."""
    mo30 = {r[0]: r[1] or 0 for r in conn.execute(
        "SELECT country, SUM(n) FROM daily_counts WHERE scope='all' AND category=? AND date>=? AND date<? GROUP BY country",
        (model_only.CODE, start30, until30))}
    return {"all_time": tot.get("M", 0), "all_time_wires": wld_all.get("M", 0),
            "last_30d": sum(mo30.values()), "last_30d_wires": mo30.get(registry.WORLDWIDE, 0),
            "status": model_only.status(), "label": model_only.LABEL}


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
        # State origin by the model's judgement alone (pipeline/model_only.py): its own figure, never in the
        # totals, shares and top country above; zeros once the hand check has passed and it has rejoined them.
        "model_only": _model_only_findings(conn, tot, wld_all, start30, until30),
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


# What became of every article that passed the relevance gate, one part per article status, on the
# same basis as articles_gate_relevant (every stored row with gate_relevant=1), so the parts add up to
# it exactly. A row in status fetched has a body and waits for the rules stage, unless its pages are
# fetched by another collector (the owner's machine) and its text never reached this one: the relay
# bundle carries no body unless it is sealed, and the runner may not fetch that outlet itself
# (classify_rules.ensure_body), so such a row stays unread.
GATE_ACCOUNTING_PARTS = ("classified", "paywalled", "failed", "blocked_robots", "fetched_waiting_rules",
                         "fetched_text_not_delivered", "waiting_for_model", "not_yet_fetched", "other")
_GATE_STATUS_PART = {"classified": "classified", "paywalled": "paywalled", "failed": "failed",
                     "blocked_robots": "blocked_robots", "awaiting_llm": "waiting_for_model",
                     "llm_submitted": "waiting_for_model", "queued": "not_yet_fetched",
                     "discovered": "not_yet_fetched"}


def gate_accounting(conn, outlets: Optional[List[Dict]] = None) -> Dict:
    """meta.gate_accounting: {"total", "parts": {part: n}, "other_statuses": {status: n},
    "copies_counted_once"}. sum(parts) == total == articles_gate_relevant. copies_counted_once is the
    stored rows the site's tables count as one placement with another row (pipeline/placements.py), so
    the site's own gate total, summed from the daily files, is total less that number."""
    outlets = outlets if outlets is not None else registry.load_outlets()
    parts = dict.fromkeys(GATE_ACCOUNTING_PARTS, 0)
    other: Dict[str, int] = {}
    for r in conn.execute("SELECT status, COUNT(*) n FROM articles WHERE gate_relevant=1 AND status<>'fetched' GROUP BY status"):
        part = _GATE_STATUS_PART.get(r["status"])
        if part:
            parts[part] += r["n"]
        else:
            parts["other"] += r["n"]
            other[r["status"] or "none"] = r["n"]
    for r in conn.execute("SELECT outlet_id, url_hash FROM articles WHERE gate_relevant=1 AND status='fetched'"):
        stranded = not registry.is_mine(r["outlet_id"], outlets) and not (r["url_hash"] and store.body_path(r["url_hash"]).exists())
        parts["fetched_text_not_delivered" if stranded else "fetched_waiting_rules"] += 1
    # Relay articles settled as failed once the relay was retired (classify_rules.settle_relay_dropped): their
    # text never reached the runner, so they stay in that part rather than in the failed fetches.
    dropped = conn.execute("SELECT COUNT(*) FROM articles WHERE gate_relevant=1 AND status='failed' AND fail_reason=?",
                           (classify_rules.RELAY_DROPPED_FAIL,)).fetchone()[0]
    parts["failed"] -= dropped
    parts["fetched_text_not_delivered"] += dropped
    shadow = placements.shadowed(conn)
    copies = 0
    ids = sorted(shadow)
    for i in range(0, len(ids), 900):
        part_ids = ids[i:i + 900]
        copies += conn.execute("SELECT COUNT(*) FROM articles WHERE gate_relevant=1 AND id IN (%s)"
                               % ",".join("?" * len(part_ids)), part_ids).fetchone()[0]
    return {"total": sum(parts.values()), "parts": parts, "other_statuses": other, "copies_counted_once": copies}


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
    # A hand coding study also withholds a language where the model placed at least MIN_POSITIVE_FOR_KAPPA
    # articles in unchecked state sourcing and the coders fewer (agreement.kappa_failure): no kappa can be
    # reported there, and that is the failure the study exists to catch, not an inconclusive result.
    withheld_languages = sorted(
        lang for lang, v in by_language.items()
        if ((v.get("n") or 0) >= config.KAPPA_MIN_LANGUAGE_ITEMS and v.get("kappa") is not None
            and v["kappa"] < config.KAPPA_WARNING_THRESHOLD)
        or (kappa and v.get("failed")))
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
    # A copy another row stands for is one placement with it (pipeline/placements.py), as on the site.
    shadow = sorted(placements.shadowed(conn))
    for i in range(0, len(shadow), 900):
        part = shadow[i:i + 900]
        for r in conn.execute("SELECT route, COUNT(*) FROM classifications WHERE is_current=1 AND category='A' "
                              "AND article_id IN (%s) GROUP BY route" % ",".join("?" * len(part)), part):
            route_totals[r[0] or "unattributed"] -= r[1]
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
        # Countries latest.json shows as coverage gaps, not the length of the gaps file: a country there
        # that has an active outlet is monitored and is no gap (Western Sahara and Kiribati were both).
        "countries_in_gaps": sum(1 for v in latest["countries"].values() if v.get("coverage") == "gap"),
        # The site marks the data stale when generated_at is older than this (config.DATA_STALE_HOURS).
        "stale_after_hours": config.DATA_STALE_HOURS,
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
        "gaps": [g for g in gaps if (latest["countries"].get(g["country"]) or {}).get("coverage") == "gap"],
        "articles_discovered": conn.execute("SELECT COALESCE(SUM(discovered), 0) FROM daily_discovery").fetchone()[0],
        "first_discovered": first_discovered,
        # Items counted again after a prune before the fix, in articles_discovered and every per country
        # discovery denominator; None for a database begun after it (rediscovery).
        "rediscovery": rediscovery(conn),
        "articles_gate_relevant": conn.execute("SELECT COUNT(*) FROM articles WHERE gate_relevant=1").fetchone()[0],
        "articles_classified": cls_total,
        # Every gate passed article by what became of it; the parts add up to articles_gate_relevant.
        "gate_accounting": gate_accounting(conn, outlets),
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
        # shared_widgets and duplicate_placements: the stored rows counted as one placement, and the language
        # editions counted as one underlying item, with their counts (pipeline/placements.py).
        **placements.summary(conn, outlets),
        "llm_budget": dict(llm_cost.month_to_date(conn),
                           budget_usd=config.LLM_MONTHLY_BUDGET_USD,
                           daily_cap_today=(cap := llm_cost.daily_cap(conn, batched=True)["cap"]),
                           # The cap is set from days before today, so the calls made today come off it.
                           calls_left_today=None if cap is None else max(0, min(cap, config.LLM_DAILY_CALL_CEILING) - store.llm_calls_today(conn)),
                           total_usd=round(llm_cost.spent(conn, "0000", "9999"), 2),
                           # {"until", "message", "recorded_at"} while the account's own usage limit pauses the model stage, else None.
                           provider_limit=classify_llm.active_provider_limit(conn)),
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
        "routes": [{"id": r, "label": ROUTE_LABELS.get(r, r) if r != model_only.ROUTE or model_only.held_apart()
                    else "No signature; model judgement, confirmed by a hand check"} for r in classify_rules.ROUTE_IDS],
        "route_totals": route_totals,
        # Current labels per issuer type (classify_llm.ISSUER_TYPES) and category, every type listed, so the
        # site can show state-owned enterprise and Hong Kong and Macao government releases as their own
        # lines and count the private releases that are not state origin. Model labels made before the
        # field existed are counted apart, as unrecorded.
        "issuer_types": list(classify_llm.ISSUER_TYPES),
        "issuer_type_totals": issuer_type_totals(conn),
        # Current model labels whose reply contradicted itself (classify_llm.inconsistency_of), by reason. A
        # state origin reply with no state issuer is stored as independent journalism, never as state origin.
        "inconsistent_labels": inconsistent_labels(conn),
        # A state origin label with no signature is not a route. It is the model asserting state origin
        # where the deterministic layer found nothing, which is a different kind of evidence and is
        # reported as its own stratum rather than as one more row in the route table. uncredited_copy
        # is a route: the lookalike check matched the article to a credited state origin item.
        "route_signature_totals": {k: v for k, v in route_totals.items() if k != "unattributed"},
        # {count, count_last_30d, status, held_apart, check, reason, rule}: the same set as
        # route_totals["unattributed"], held out of state origin until its hand check passes (decision D1).
        "model_only_state_origin": model_only.meta_block(conn, *complete_window_30d(latest)),
        "arrivals": [{"id": r, "label": ARRIVAL_LABELS.get(r, r)} for r in classify_rules.ARRIVAL_IDS],
        "arrival_totals": _arrival_totals(conn),
        "relay_collector": relay_status(conn, outlets),
        # missed_estimate is only from the polls the current estimator made (estimated_polls, from
        # missed_estimate_since); polls and saturated are over every poll on record.
        "feed_saturation": {"polls": sat[0], "saturated": sat[1], "missed_estimate": int(round(sat[2] or 0)),
                            "estimated_polls": sat[3] or 0, "missed_estimate_since": missed_estimate_since(conn),
                            "estimator": config.MISSED_ESTIMATOR_VERSION,
                            "warning_share": config.FEED_SATURATION_WARNING_SHARE,
                            # The new items the estimated polls found, and the share missed of missed plus found.
                            "estimated_new_items": sat[4] or 0,
                            "missed_share": (round((sat[2] or 0) / float((sat[2] or 0) + (sat[4] or 0)), 4)
                                             if sat[3] and (sat[2] or 0) + (sat[4] or 0) else None),
                            "missed_warning_share": config.FEED_MISSED_WARNING_SHARE},
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
            "A": ("State origin. Text authored or commissioned by a Chinese state entity and published without substantive editorial "
                  "alteration, whatever its topic. State media, government, party and diplomatic bodies, state-owned enterprises and "
                  "the Hong Kong and Macao governments and statutory bodies are state entities; a press release from any other "
                  "issuer is not state origin, even when it is published unaltered."),
            "B": ("Unchecked state sourcing. Written by the local outlet, and its central content is a factual claim "
                  "attributed to a Chinese official or state source (an assertion of fact about events, numbers or actions), "
                  "passed on without independent confirmation. A stated position, policy, warning, threat, opinion or denial "
                  "is not a factual claim, and reporting one is independent journalism even without outside comment."),
            "C": ("Independent journalism. Not state origin and not unchecked state sourcing. It includes the outlet's own "
                  "reporting; copy from news agencies that are not Chinese state entities, such as Reuters or AP; reporting "
                  "of a position, policy, warning, threat, opinion or denial attributed to a Chinese official or state "
                  "source, with or without outside comment; reporting that quotes Chinese officials and confirms, "
                  "contextualizes or contests what they say; and a press release from an issuer that is not a Chinese "
                  "state entity, when it substantively concerns China."),
            "not_relevant": ("Does not concern China in any substantive way. A Hong Kong, Macao or Taiwan domestic story counts "
                             "as China coverage only when it substantively concerns the PRC state or its policy."),
        },
    }


def stamp_data(data, stamp: Dict):
    """data with the export stamp (schema_version, generated_at) added, as a new object; an array, an
    empty object (a measure that is off) and anything else is returned as it is. global_series.json and
    articles/ISO3.json are arrays and carry no stamp: the site checks the object files, meta.json first."""
    return dict(data, **stamp) if isinstance(data, dict) and data else data


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


def inconsistent_labels(conn) -> Dict:
    """{"total": n, "by_reason": {reason: n}} over the current labels marked needs_review."""
    by_reason = {r[0]: r[1] for r in conn.execute(
        "SELECT needs_review, COUNT(*) FROM classifications WHERE is_current=1 AND needs_review IS NOT NULL GROUP BY needs_review")}
    return {"total": sum(by_reason.values()), "by_reason": by_reason}


def issuer_type_totals(conn) -> Dict:
    """Current labels per issuer type and category, {"by_type": {type: {category: n}}, "unrecorded": n}:
    every type in classify_llm.ISSUER_TYPES is listed, and unrecorded counts the current model labels
    that carry no issuer type (made under a prompt before 2026-10-17)."""
    by_type = {t: {} for t in classify_llm.ISSUER_TYPES}
    unrecorded = 0
    held = model_only.held_apart()
    for r in conn.execute("""SELECT issuer_type, category, method, COUNT(*), route FROM classifications WHERE is_current=1
                             GROUP BY issuer_type, category, method, route"""):
        if r[0] in by_type:
            # A model-only state origin label is counted under M while held apart (pipeline/model_only.py).
            cat = model_only.headline_category(r[1], r[4], held, r[2])
            by_type[r[0]][cat] = by_type[r[0]].get(cat, 0) + r[3]
        elif r[2] == "llm":
            unrecorded += r[3]
    return {"by_type": by_type, "unrecorded": unrecorded}


def write_audit_files(conn, audit_dir: Path = config.EXPORT_DIR_AUDIT) -> Dict:
    """Append-only audit trail committed to git: every current classification with its article's
    metadata, one JSON line per article, in monthly files rewritten in full each export."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """SELECT a.id, a.url, a.url_hash, a.outlet_id, a.country, a.language, a.title, a.published_at, a.discovered_at, a.day_of_record,
                  a.status, a.dup_group_id, a.themes, a.gate_terms, c.category, c.method, c.confidence, c.evidence_quote, c.reasoning,
                  c.signatures_fired, c.china_sources_cited, c.model_version, c.ruleset_version, c.classified_at, c.route, c.issuer_type, c.needs_review,
                  (SELECT human_category FROM human_reviews h WHERE h.article_id=a.id ORDER BY h.id DESC LIMIT 1) AS human_category
           FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 ORDER BY a.id"""
    ).fetchall()
    by_day = defaultdict(list)
    # A copy another row stands for (pipeline/placements.py) names that row, so the audit lines add up to
    # the counts; the field is absent on every other line.
    rep = placements.representative_of(conn)
    for r in rows:
        it = dict(r)
        if r["id"] in rep:
            it["counted_as"] = rep[r["id"]]
        if it.get("evidence_quote"):
            it["evidence_quote"] = _cap_quote(it["evidence_quote"], (json.loads(it.get("signatures_fired") or "[]") or [None])[0])
        if not it.get("day_of_record"):
            it.pop("day_of_record", None)   # set only on backfilled items; existing lines stay unchanged
        if not it.get("issuer_type"):
            it.pop("issuer_type", None)     # set only on model labels from prompt 2026-10-17; older lines stay unchanged
        if not it.get("needs_review"):
            it.pop("needs_review", None)    # set only on an inconsistent model reply
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


class ExportShrinkError(RuntimeError):
    """The export would publish fewer discovered articles than the export it replaces."""


def _staging_paths(export_dir: Path) -> Tuple[Path, Path]:
    return (export_dir.with_name("." + export_dir.name + ".staging"),
            export_dir.with_name("." + export_dir.name + ".old"))


def stage_export_dir(export_dir: Path) -> Path:
    """A staging copy of export_dir, beside it on the same filesystem, that the export writes into and
    swap_in_export_dir puts in its place. It starts as a copy, so what this export does not write (a
    measure's side file kept from the export before) stays as it was. An export_dir a crash left half
    swapped (only the .old copy there) is put back first."""
    import shutil
    staging, old = _staging_paths(export_dir)
    if not export_dir.exists() and old.exists():
        os.replace(old, export_dir)
    if staging.exists():
        shutil.rmtree(staging)
    if export_dir.exists():
        shutil.copytree(export_dir, staging)
    else:
        staging.mkdir(parents=True)
    return staging


def swap_in_export_dir(staging: Path, export_dir: Path) -> None:
    """The written staging directory in place of export_dir: two renames, so a reader of the
    directory sees the whole previous export or the whole new one, never a mix of the two."""
    import shutil
    _, old = _staging_paths(export_dir)
    if old.exists():
        shutil.rmtree(old)
    if export_dir.exists():
        os.replace(export_dir, old)
    os.replace(staging, export_dir)
    shutil.rmtree(old, ignore_errors=True)


# The drop in articles_discovered an export may show, as a share: a relay bundle replaces its outlets'
# day rows wholesale and can move the total down a little. The same allowance as scripts/export_guard.py,
# which checks the database against the branch tip before the export step.
SHRINK_ALLOWANCE = 0.005


def check_discovered_floor(meta: Dict, export_dir: Path, withdrawn: int = 0, allow_shrink: bool = False) -> None:
    """Refuse an export whose articles_discovered falls more than SHRINK_ALLOWANCE below the one in
    export_dir/meta.json, less what this run's junk feed correction took off (withdrawn). A database come
    back from an older cache or snapshot would otherwise publish smaller figures over larger ones.
    allow_shrink, or TRACKER_EXPORT_ALLOW_SHRINK=1 in the environment, lets a deliberate correction through."""
    if allow_shrink or os.environ.get("TRACKER_EXPORT_ALLOW_SHRINK") == "1":
        return
    try:
        with open(Path(export_dir) / "meta.json", encoding="utf-8") as fh:
            before = int(json.load(fh).get("articles_discovered") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return          # no previous export to compare with
    floor = int((before - max(0, int(withdrawn or 0))) * (1 - SHRINK_ALLOWANCE))
    now = int(meta.get("articles_discovered") or 0)
    if now < floor:
        raise ExportShrinkError(
            "articles_discovered would fall from %d to %d (%d withdrawn by junk feeds this run): the database "
            "looks older than the published export; nothing was written (TRACKER_EXPORT_ALLOW_SHRINK=1 to "
            "publish it anyway)" % (before, now, withdrawn))


def run(conn, run_id: str = "export", export_dir: Path = config.EXPORT_DIR,
        audit_dir: Path = config.EXPORT_DIR_AUDIT, methodology_path: Optional[Path] = None,
        prune: bool = True, allow_shrink: bool = False) -> Dict:
    """Write every generated file. All output locations are parameters so tests never touch
    the repository's own files. methodology_path defaults to METHODOLOGY.md at the root, and
    the same text is copied into the site so the interface can link it. prune=False leaves the
    irreversible housekeeping for a later call, once the files have been checked.

    Every file is written into a staging copy of export_dir that replaces it at the end
    (stage_export_dir, swap_in_export_dir): an export that fails part way (a full disk) leaves the
    published directory as it was. An export that would lower articles_discovered is refused before
    anything is written (check_discovered_floor; allow_shrink lets it through)."""
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
    themes_stats: Dict = {}
    tagged = themes_mod.ensure(conn, stats=themes_stats)
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
    check_discovered_floor(meta, export_dir, allow_shrink=allow_shrink,
                           withdrawn=sum(int((t or {}).get("discovered_withdrawn") or 0) for t in (junk or {}).values()))
    # The data files the site reads first (latest, daily, outlets, meta, examples) carry this export's
    # schema_version and generated_at, the values meta.json carries, so the site can tell a file left from
    # another export (a stale cache, a deploy half published) by comparing them on load (stamp_data). The
    # measure side files and planted.json keep their own shapes and are left as they were.
    stamp = {"schema_version": config.SCHEMA_VERSION, "generated_at": meta["generated_at"]}

    def put(path: Path, data) -> None:
        write_json(path, stamp_data(data, stamp))

    # The research measures (pipeline/measures.py): report only, each in its own try/except, never a stop.
    from pipeline import measures
    out = stage_export_dir(export_dir)
    try:
        # ctx keeps the real export_dir: copies.review_path tells the site's own export by it
        meta["measures"] = measures.run_all_quietly(conn, out, {
            "daily": daily, "latest": latest, "outlets": outlets, "outlets_json": outlets_json, "meta": meta,
            "export_dir": export_dir}, write_json=write_json)
        put(out / "latest.json", latest)
        for month, m in daily.items():
            put(out / "daily" / ("%s.json" % month), m)
        # A month that no longer has any days must not be left behind. Moving the day of record to the
        # discovery day emptied August, and a stale file would sit in the published directory forever.
        for stale in (out / "daily").glob("*.json"):
            if stale.stem not in daily:
                stale.unlink()
        put(out / "global_series.json", series)
        put(out / "outlets.json", outlets_json)
        # One file per country in latest.json, empty where nothing is classified, so the page never 404s.
        for country in latest["countries"]:
            put(out / "articles" / ("%s.json" % country), articles.get(country, []))
        put(out / "meta.json", meta)
        put(out / "examples.json", examples)
        # A failure in the planted export is printed and leaves an empty planted.json, never a stop.
        write_json(out / "planted.json", planted.build_export_quietly(conn))
    except BaseException:
        import shutil
        shutil.rmtree(out, ignore_errors=True)
        raise
    swap_in_export_dir(out, export_dir)
    counts = {"planted": planted_counts,"countries": len(latest["countries"]), "months": len(daily), "days": len(series), "articles_files": len(articles),
              "pruned_gated_out": pruned, "themes_tagged": tagged, "routes_filled": routed, "authors_cleaned": authors_cleaned, "audit_files": write_audit_files(conn, audit_dir)}
    counts.update(roll)
    if themes_stats.get("body_retags_left"):
        # Title-only theme tags whose body has arrived, left for the next export (pipeline/themes.py).
        counts["themes_body_retags_left"] = themes_stats["body_retags_left"]
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
