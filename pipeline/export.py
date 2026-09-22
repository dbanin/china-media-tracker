"""Rebuild rollups and generate the static JSON the front end reads.

Outputs in docs/data/:
  latest.json          per-country totals, all time and last 30 days, with coverage flags,
                       language support, release section search status and warnings
  daily/YYYY-MM.json   per-day, per-country counts by category and method, state origin by
                       route, feed saturation, the model draw allocation and relay collector hours
  outlets.json         registry with coverage metadata, feed health and release section status
  meta.json            last run, kappa, review coverage, ruleset and schema versions, gaps, and
                       the gates that decide what the interface may publish
  articles/ISO3.json   most recent classified articles per country for the country panel
  global_series.json   per-day global totals for the sparkline

A day with an LLM ceiling event is marked in the daily file so a truncated day
is never mistaken for a quiet day, and a day on which the relay collector ran for
too few hours is marked so a sleeping laptop is never mistaken for a quiet country.
"""
import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from pipeline import classify_rules, config, extract, gate, llm_cost, registry, second_rater, store
from pipeline import themes as themes_mod

CATEGORIES = ["A", "B", "C", "not_relevant"]


# Kept for the audit files, which still report how far a feed's published date lagged discovery.
STALE_PUBLISHED_DAYS = 14

ROUTE_LABELS = {
    "wire_credit": "Wire credit line or dateline",
    "distribution_stamp": "Press release distribution stamp",
    "sponsored_disclosure": "Sponsored or partner disclosure",
    "diplomatic_byline": "Signed by a Chinese diplomat",
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
# What a published unchecked state sourcing count rests on, in the words the interface uses. A model versus model study
# licenses "the two models agree", never "the labels are right", and the qualifier travels with the
# number wherever it is shown.
RELAY_QUALIFIER = {
    "human_coding": "Checked against hand coding of a random sample.",
    "model_vs_model": "Consistent between two models, never checked against human coding: this measures whether two raters apply the codebook the same way, not whether they apply it correctly.",
    "same_model_rerun": "Consistent with itself: the same model judged the same articles twice, never checked against human coding or another model. This detects randomness in its own judgement and nothing about a bias it holds every time.",
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
    in the article records and in the audit files, so publication lag stays analysable."""
    return (row["discovered_at"] or "")[:10]


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def article_rows(conn) -> List[Dict]:
    """One row per article with its current machine label and its latest human label."""
    rows = conn.execute(
        """SELECT a.id, a.country, a.outlet_id, a.language, a.title, a.url, a.published_at, a.discovered_at,
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
    counts = defaultdict(lambda: {"n": 0, "n_rules": 0, "n_llm": 0, "n_reviewed": 0, "groups": set()})
    coverage = defaultdict(lambda: {"discovered": 0, "gate_relevant": 0, "fetched": 0, "paywalled": 0,
                                    "failed": 0, "blocked_robots": 0, "classified": 0, "llm_pending": 0,
                                    "top_discovered": 0, "top_target": 0, "top_china": 0, "top_a": 0, "top_b": 0})
    for r in rows:
        d = _day(r)
        # A global release service is not a national newsroom, so its items are counted on their own
        # and never inside a country's numerator or denominator.
        if r["outlet_id"] in wire_ids:
            wires["articles"] += 1
            if r["status"] in ("awaiting_llm", "llm_submitted"):
                wires["pending"] += 1
            elif r["m_cat"]:
                wires[r["m_cat"]] += 1
            continue
        cov = coverage[(d, r["country"])]
        cov["gate_relevant"] += 1
        st = r["status"]
        if r["outlet_id"] in top_ids.get(r["country"], set()):
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
            key = (d, r["country"], r["m_cat"], "all")
            c = counts[key]
            c["n"] += 1
            c["n_rules" if r["m_method"] == "rules" else "n_llm"] += 1
            c["groups"].add(r["dup_group_id"] or r["id"])
            if r["h_cat"]:
                c["n_reviewed"] += 1
                key2 = (d, r["country"], r["h_cat"], "reviewed")
                c2 = counts[key2]
                c2["n"] += 1
                c2["n_reviewed"] += 1
                c2["groups"].add(r["dup_group_id"] or r["id"])
    # discovered totals per day and country include gated-out items. They come from the
    # permanent daily_discovery table, because gated-out rows are pruned from articles.
    for r in conn.execute("SELECT date, country, discovered FROM daily_discovery"):
        coverage[(r["date"], r["country"])]["discovered"] += r["discovered"]
    for r in conn.execute("SELECT date, outlet_id, country, discovered FROM daily_outlet_discovery"):
        if r["outlet_id"] in top_ids.get(r["country"], set()):
            coverage[(r["date"], r["country"])]["top_discovered"] += r["discovered"]

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
    return {"daily_counts": len(counts), "daily_coverage": len(coverage), "articles": len(rows),
            "distribution_wire_articles": wires["articles"], "distribution_wires": wires}


def _empty_country() -> Dict:
    return {"A": 0, "B": 0, "C": 0, "N": 0, "Ar": 0, "Al": 0, "Ah": 0, "Br": 0, "Bl": 0, "Bh": 0,
            "rev": 0, "cls": 0, "disc": 0, "rel": 0, "fetched": 0, "paywalled": 0, "failed": 0,
            "blocked": 0, "pending": 0, "uniqA": 0, "uniqAB": 0, "tdisc": 0, "ttarget": 0, "tchina": 0, "ta": 0, "tb": 0,
            "polls": 0, "sat": 0, "miss": 0}


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


def _feed_poll_days(conn):
    """Per day and country: feed polls, saturated polls, and the estimated items missed between polls."""
    return conn.execute(
        """SELECT date, country, SUM(polls) polls, SUM(saturated) sat, SUM(missed_estimate) miss
           FROM feed_polls GROUP BY date, country"""
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
    """Whether unchecked state sourcing may appear in a published figure at all: either a study has
    cleared it, or no study has run yet and it is shown marked provisional. The interface and the
    files it reads must make this decision the same way, in one place."""
    labels = conn.execute("SELECT COUNT(*) FROM classifications WHERE method='llm' AND is_current=1").fetchone()[0]
    if not labels:
        return False
    human = conn.execute("SELECT kappa_bc FROM agreement_studies ORDER BY id DESC LIMIT 1").fetchone()
    model = store.latest_model_agreement(conn)
    passed = ((human and human["kappa_bc"] is not None and human["kappa_bc"] >= config.KAPPA_WARNING_THRESHOLD)
              or (model and model["kappa_bc"] is not None and model["kappa_bc"] >= config.KAPPA_WARNING_THRESHOLD))
    if passed:
        return True
    return bool(config.RELAY_PROVISIONAL_DISPLAY and not (human or model))


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
    relayed = [o for o in outlets if o.get("active") and registry.collector_of(o) == "self_hosted"]
    last = conn.execute("SELECT MAX(COALESCE(finished_at, started_at)) FROM relay_runs WHERE ok=1").fetchone()[0]
    hours = stale = None
    if last:
        t = dt.datetime.fromisoformat(last)
        t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        hours = round((dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600.0, 1)
        stale = hours >= config.RELAY_STALE_HOURS
    return {"outlets": len(relayed), "countries": sorted({o["country"] for o in relayed}), "last_run": last,
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


def release_summary(outlets: List[Dict]) -> Dict:
    """Active outlets by the status of the search for press release, sponsored and partner sections."""
    active = registry.active_outlets(outlets)
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


def build_latest(conn, outlets: List[Dict], gaps: List[Dict], population: Optional[Dict] = None) -> Dict:
    visible = relay_visible(conn)
    population = population if population is not None else registry.load_population()
    top_ids, ranked = registry.top_outlets(outlets)
    today = dt.datetime.now(dt.timezone.utc).date()
    since30 = (today - dt.timedelta(days=30)).isoformat()
    by_country_outlets = defaultdict(list)
    for o in outlets:
        by_country_outlets[o["country"]].append(o)
    feed_health = {r["feed_url"]: dict(r) for r in conn.execute("SELECT * FROM feed_health")}
    gate_langs = gate.supported_languages()
    theme_langs = themes_mod.languages()
    relay = relay_status(conn, outlets)
    relay_inc30 = [d for d in relay["incomplete_days"] if d >= since30]

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
    for r in _feed_poll_days(conn):
        for target, ok in ((all_time, True), (last30, r["date"] >= since30)):
            if ok:
                t = target[r["country"]]
                t["polls"] += r["polls"] or 0; t["sat"] += r["sat"] or 0; t["miss"] += int(round(r["miss"] or 0))

    countries = {}
    for country, os_ in by_country_outlets.items():
        active = registry.active_outlets(os_)
        # Feed health counts editorial feeds only. A quiet or unreadable sponsored section is not a failing feed,
        # so section feeds are reported beside them and never trip the failing feeds warning.
        entries = [f for o in active for f in registry.feed_entries(o)]
        feeds = [f["url"] for f in entries if f.get("kind", registry.EDITORIAL) == registry.EDITORIAL]
        section_urls = [f["url"] for f in entries if f.get("kind", registry.EDITORIAL) != registry.EDITORIAL]
        feeds_ok = sum(1 for f in feeds if feed_health.get(f) and feed_health[f]["consecutive_failures"] == 0)
        languages = sorted({o["language"] for o in active})
        relayed = sum(1 for o in active if registry.collector_of(o) == "self_hosted")
        entry = {
            "coverage": "monitored" if active else "no_active_outlets",
            "outlets_total": len(os_), "outlets_active": len(active),
            "feeds_total": len(feeds), "feeds_ok": feeds_ok,
            "section_feeds_total": len(section_urls),
            "section_feeds_ok": sum(1 for f in section_urls if feed_health.get(f) and feed_health[f]["consecutive_failures"] == 0),
            "population": population.get(country),
            "top_outlets": len(top_ids.get(country, ())), "top_outlets_ranked": country in ranked,
            "languages": languages,
            "language_support": language_support(languages, gate_langs),
            "theme_language_support": language_support(languages, theme_langs),
            "relay_outlets": relayed,
            "release_sections": release_summary(os_),
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
            entry["warnings"].append({"type": "feed_saturation", "text": "%d percent of feed polls came back as a full window with nothing seen before, so items were lost between polls; an estimated %d were missed" % (round(100.0 * at["sat"] / at["polls"]), at["miss"])})
        if relayed:
            if relay["stale"]:
                entry["warnings"].append({"type": "relay_stale", "text": "%d of this country's outlets are collected from the owner's machine, which has not collected for %.0f hours; recent counts are incomplete" % (relayed, relay["hours_since_last_run"])})
            if relay_inc30:
                entry["warnings"].append({"type": "relay_incomplete", "text": "%d of this country's outlets are collected from the owner's machine, which ran in fewer than %d hours on %d of the last 30 days; counts on those days are incomplete" % (relayed, config.RELAY_DAY_MIN_HOURS, len(relay_inc30))})
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
                                       "theme_language_support": "none", "relay_outlets": 0, "release_sections": release_summary([]),
                                       "all_time": _derive(_empty_country(), 0, visible), "last_30d": _derive(_empty_country(), 0, visible), "warnings": []}

    n_active = len(registry.active_outlets(outlets))
    totals = {"all_time": _derive(_empty_country(), n_active, visible), "last_30d": _derive(_empty_country(), n_active, visible)}
    for scope_key, src in (("all_time", all_time), ("last_30d", last30)):
        t = totals[scope_key]
        for c in src.values():
            for k in _empty_country():
                t[k] += c[k]
        _derive(t, n_active, visible)
    totals["population"] = sum(v["population"] or 0 for v in countries.values() if v["coverage"] == "monitored")
    return {"generated_at": store.utcnow(), "window_start_30d": since30, "countries": countries, "totals": totals}


def build_daily(conn) -> Dict[str, Dict]:
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
    ceiling_days = {r["date"] for r in conn.execute("SELECT date FROM llm_usage WHERE ceiling_hit=1")}
    reviewed = defaultdict(lambda: defaultdict(lambda: {"A": 0, "B": 0, "C": 0, "N": 0}))
    for r in conn.execute("SELECT * FROM daily_counts WHERE scope='reviewed'"):
        reviewed[r["date"]][r["country"]][{"A": "A", "B": "B", "C": "C", "not_relevant": "N"}[r["category"]]] += r["n"]
    sampling = defaultdict(dict)
    for r in conn.execute("SELECT date, country, eligible, drawn FROM llm_sampling"):
        sampling[r["date"]][r["country"]] = [r["eligible"], r["drawn"]]
    relay_hours = relay_hours_by_day(conn)
    relay_incomplete = set(relay_incomplete_days(conn))
    # Theme counts per day and country: theme id -> [all China coverage, state-linked articles, state origin,
    # unchecked state sourcing].
    # Only China coverage counts (state origin, relay, independent, and pending candidates). An article
    # carries every theme it matches, so a country's theme counts can sum to more than its articles.
    theme_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0])))
    # State origin per day and country by the route it arrived by, and labels still carrying an older ruleset.
    routes = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    arrivals = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    older_ruleset = defaultdict(int)
    for r in article_rows(conn):
        pending = r["status"] in ("awaiting_llm", "llm_submitted")
        cat = r["m_cat"]
        d = _day(r)
        if cat and r["ruleset_version"] and r["ruleset_version"] != config.RULESET_VERSION:
            older_ruleset[d] += 1
        if cat == "A":
            routes[d][r["country"]][r["route"] or "unattributed"] += 1
            arrivals[d][r["country"]][classify_rules.arrival_of(r["gate_terms"])] += 1
        if not (pending or cat in ("A", "B", "C")):
            continue
        for t in themes_mod.themes_of(r["themes"]):
            v = theme_counts[d][r["country"]][t]
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
                       "llm_sampling": sampling.get(d, {}),
                       "llm_ceiling_hit": d in ceiling_days,
                       "relay_hours": relay_hours.get(d),
                       "relay_incomplete": d in relay_incomplete,
                       "labels_on_older_ruleset": older_ruleset.get(d, 0)}
        out[month] = {"month": month, "days": days}
    return out


def build_global_series(daily: Dict[str, Dict]) -> List[Dict]:
    series = []
    for month in sorted(daily):
        for d, day in sorted(daily[month]["days"].items()):
            tot = {"date": d, "A": 0, "B": 0, "C": 0, "N": 0, "rel": 0, "paywalled": 0, "pending": 0,
                   "countries_with_A": 0, "llm_ceiling_hit": day["llm_ceiling_hit"],
                   "relay_incomplete": day.get("relay_incomplete", False),
                   "labels_on_older_ruleset": day.get("labels_on_older_ruleset", 0)}
            for c, v in day["countries"].items():
                for k in ("A", "B", "C", "N", "rel", "paywalled", "pending"):
                    tot[k] += v[k]
                if v["A"]:
                    tot["countries_with_A"] += 1
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
           FROM articles GROUP BY outlet_id"""
    ):
        t = per_outlet[r["outlet_id"]]
        t["disc"] = r["disc"]; t["rel"] = r["rel"] or 0; t["fetched"] = r["fetched"] or 0
        t["paywalled"] = r["paywalled"] or 0; t["failed"] = r["failed"] or 0; t["blocked"] = r["blocked"] or 0
        t["pending"] = r["pending"] or 0
    for r in conn.execute("SELECT outlet_id, SUM(polls) polls, SUM(saturated) sat, SUM(missed_estimate) miss FROM feed_polls GROUP BY outlet_id"):
        t = per_outlet[r["outlet_id"]]
        t["polls"] = r["polls"] or 0; t["sat"] = r["sat"] or 0; t["miss"] = int(round(r["miss"] or 0))
    out = []
    for o in outlets:
        feeds = []
        for fe in registry.feed_entries(o):
            h = feed_health.get(fe["url"])
            feeds.append({"url": fe["url"], "kind": fe.get("kind", registry.EDITORIAL), "ok": bool(h and h["consecutive_failures"] == 0),
                          "last_ok": h["last_ok"] if h else None, "last_error": h["last_error"] if h else None,
                          "consecutive_failures": h["consecutive_failures"] if h else None})
        out.append({"id": o["id"], "name": o["name"], "country": o["country"], "language": o["language"],
                    "tier": o["tier"], "active": o["active"], "notes": o.get("notes"),
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


def build_articles(conn, per_country: int = 80) -> Dict[str, List[Dict]]:
    """Per country: state origin first, then unchecked state sourcing, then articles carrying official
    Chinese sourcing whose verification judgement is pending, then independent journalism."""
    rows = conn.execute(
        """SELECT a.id, a.country, a.outlet_id, a.title, a.url, a.published_at, a.discovered_at, a.dup_group_id,
                  a.status, a.llm_trigger, a.themes, a.gate_terms,
                  c.category, c.method, c.confidence, c.evidence_quote, c.reasoning, c.signatures_fired, c.model_version,
                  c.ruleset_version, c.china_sources_cited, c.route,
                  (SELECT human_category FROM human_reviews h WHERE h.article_id=a.id ORDER BY h.id DESC LIMIT 1) AS human_category
           FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 AND (c.category IN ('A','B','C') OR a.status IN ('awaiting_llm','llm_submitted'))
           ORDER BY COALESCE(a.published_at, a.discovered_at) DESC"""
    ).fetchall()
    out = defaultdict(list)
    for r in rows:
        pending = r["category"] is None
        cat = "pending" if pending else r["category"]
        entry = {
            "id": r["id"], "outlet_id": r["outlet_id"], "title": r["title"], "url": r["url"],
            "date": _day(r), "category": cat, "human_category": r["human_category"],
            "provenance": "human" if r["human_category"] else ("rules" if pending else r["method"]),
            "confidence": r["confidence"], "evidence_quote": r["evidence_quote"],
            "reasoning": r["reasoning"], "signatures": json.loads(r["signatures_fired"] or "[]"),
            "sources": json.loads(r["china_sources_cited"] or "[]"),
            "model": r["model_version"], "ruleset": r["ruleset_version"], "dup_group": r["dup_group_id"],
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
        out[r["country"]].append(entry)
    for country in out:
        # Newest first inside each category, categories in ORDER. Two stable sorts.
        out[country].sort(key=lambda e: e["date"] or "", reverse=True)
        out[country].sort(key=lambda e: ORDER.get(e["category"], 9))
        out[country] = out[country][:per_country]
    return out


def build_meta(conn, outlets: List[Dict], gaps: List[Dict], latest: Dict) -> Dict:
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
    settled = bool(kappa and kappa["kappa_bc"] is not None and kappa["kappa_bc"] >= config.KAPPA_WARNING_THRESHOLD)
    # A second model re-judging the same articles is a reliability study, not a validation one: it
    # shows two raters applying the codebook the same way, and two models of one family can share a
    # bias neither reveals. It can unlock publication, but what unlocked it is recorded and shown.
    model_study = store.latest_model_agreement(conn)
    model_details = {}
    if model_study and model_study["details"]:
        try:
            model_details = json.loads(model_study["details"]) or {}
        except ValueError:
            model_details = {}
    reliable = bool(model_study and model_study["kappa_bc"] is not None
                    and model_study["kappa_bc"] >= config.KAPPA_WARNING_THRESHOLD)
    relay_reliability = {
        "method": model_study["method"], "model_a": model_study["model_a"], "model_b": model_study["model_b"],
        "n": model_study["sample_size"], "kappa_all": model_study["kappa_all"], "kappa_bc": model_study["kappa_bc"],
        "n_bc": model_study["n_bc"], "computed_at": model_study["computed_at"],
        "by_category": model_details.get("kappa_by_category"), "bc_by_language": model_details.get("bc_by_language"),
        "threshold": config.KAPPA_WARNING_THRESHOLD,
    } if model_study else None
    # Provisional is the state before any study: the model has labelled articles and nothing has checked
    # them yet. Once a study of either kind has produced a kappa the counts are published or withheld.
    # A study that ran counts as having been done even when it could not produce a figure, otherwise the
    # interface says "nothing has checked them yet" while relay_study says the study is finished.
    any_study = bool(kappa or model_study)
    inconclusive = bool((kappa or model_study) and not settled and not reliable
                        and not (kappa and kappa["kappa_bc"] is not None) and not (model_study and model_study["kappa_bc"] is not None))
    # Which study, if any, is holding the gate open. Never "validated": no human has coded anything.
    relay_basis = "human_coding" if settled else ((model_study["method"] or "model_vs_model") if reliable else None)
    by_language = (details.get("bc_by_language") if settled else model_details.get("bc_by_language")) or {}
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
    route_totals = {r[0] or "unattributed": r[1] for r in conn.execute(
        "SELECT route, COUNT(*) FROM classifications WHERE is_current=1 AND category='A' GROUP BY route")}
    sat = conn.execute("SELECT COALESCE(SUM(polls), 0), COALESCE(SUM(saturated), 0), COALESCE(SUM(missed_estimate), 0) FROM feed_polls").fetchone()
    return {
        "schema_version": config.SCHEMA_VERSION,
        "ruleset_version": config.RULESET_VERSION,
        "llm_model": config.LLM_MODEL,
        "generated_at": store.utcnow(),
        "last_runs": last_runs,
        "last_successful_run": max(last_runs.values()) if last_runs else None,
        "outlets_total": len(outlets), "outlets_active": len(active),
        "countries_monitored": len(countries_active),
        "registry_unevenness": registry.registry_summary(outlets)["unevenness"],
        "countries_in_gaps": len(gaps),
        "themes": themes_mod.catalog(),
        "themes_version": themes_mod.version(),
        "theme_languages": sorted(themes_mod.languages()),
        "gate_languages": len(gate.supported_languages()),
        "population_source": registry.population_source(),
        "top_outlets_per_country": registry.TOP_OUTLETS_PER_COUNTRY,
        "countries_with_audience_ranks": registry.top_outlets(outlets)[1],
        "min_outlets_for_output_share": config.MIN_OUTLETS_FOR_OUTPUT_SHARE,
        "gaps": gaps,
        "articles_discovered": conn.execute("SELECT COALESCE(SUM(discovered), 0) FROM daily_discovery").fetchone()[0],
        "first_discovered": (conn.execute("SELECT MIN(discovered_at) FROM articles").fetchone()[0] or "")[:10] or None,
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
        "relay_publishable": bool(llm_labels) and (settled or reliable),
        # Real counts, marked as one model's unchecked judgement, until the first study reports.
        "relay_provisional": bool(config.RELAY_PROVISIONAL_DISPLAY and llm_labels and not (settled or reliable) and not any_study),
        # A study ran and the sample could not support a figure. Not provisional, not certified.
        "relay_study_inconclusive": inconclusive,
        "relay_study": second_rater.study_state(conn),
        "relay_basis": relay_basis,
        "relay_reliability": relay_reliability,
        # No article has been read by a person. Said plainly here so nothing downstream has to infer it.
        "relay_human_coded": bool(reviewed_total),
        "relay_qualifier": RELAY_QUALIFIER.get(relay_basis),
        "relay_withheld_languages": withheld_languages,
        "llm_calls_total": llm["calls"] or 0,
        "llm_labels_total": llm_labels,
        "llm_ceiling_days": ceiling_days,
        "llm_calls_by_day": calls_by_day,
        "llm_daily_ceiling": config.LLM_DAILY_CALL_CEILING,
        # Spending, estimated from recorded token usage at published prices, and the calls today's
        # share of the monthly budget pays for at the batch price the scheduled runs use.
        # Global release distribution services, counted apart from every country. They are polled and
        # classified like any outlet; what they are not is a national media market.
        "distribution_wires": {
            "outlets": sorted(registry.distribution_wire_ids(outlets)),
            "counts": _wire_counts(conn, outlets),
            "note": "Global press release distribution services. Their releases are written by their issuers and pushed worldwide, so they are counted here and never inside a country.",
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
        # reported as its own stratum rather than as one more row in the route table.
        "route_signature_totals": {k: v for k, v in route_totals.items() if k != "unattributed"},
        "model_only_state_origin": route_totals.get("unattributed", 0),
        "arrivals": [{"id": r, "label": ARRIVAL_LABELS.get(r, r)} for r in classify_rules.ARRIVAL_IDS],
        "arrival_totals": _arrival_totals(conn),
        "relay_collector": relay_status(conn, outlets),
        "feed_saturation": {"polls": sat[0], "saturated": sat[1], "missed_estimate": int(round(sat[2])),
                            "warning_share": config.FEED_SATURATION_WARNING_SHARE},
        "release_sections": release_summary(outlets),
        "categories": {
            "A": "State origin. Text written by an entity of the Chinese state and published essentially unaltered.",
            "B": "Unchecked state sourcing. Written by the local outlet but passes on official Chinese sourcing without independent confirmation.",
            "C": "Independent journalism. The outlet's own reporting, including reporting that quotes Chinese officials but confirms, contextualizes or contests what they say.",
            "not_relevant": "Does not concern China.",
        },
    }


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def write_audit_files(conn, audit_dir: Path = config.EXPORT_DIR_AUDIT) -> Dict:
    """Append-only audit trail committed to git: every current classification with its article's
    metadata, one JSON line per article, in monthly files rewritten in full each export."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """SELECT a.id, a.url, a.url_hash, a.outlet_id, a.country, a.language, a.title, a.published_at, a.discovered_at,
                  a.status, a.dup_group_id, a.themes, a.gate_terms, c.category, c.method, c.confidence, c.evidence_quote, c.reasoning,
                  c.signatures_fired, c.china_sources_cited, c.model_version, c.ruleset_version, c.classified_at, c.route,
                  (SELECT human_category FROM human_reviews h WHERE h.article_id=a.id ORDER BY h.id DESC LIMIT 1) AS human_category
           FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1 ORDER BY a.id"""
    ).fetchall()
    by_day = defaultdict(list)
    for r in rows:
        by_day[_day(r)].append(dict(r))
    written = {}
    for day, items in by_day.items():
        path = audit_dir / ("articles-%s.jsonl" % day)
        body = "".join(json.dumps(it, ensure_ascii=False, sort_keys=True) + "\n" for it in items)
        # Only rewrite a day that changed. A month in one file meant a fresh multi megabyte blob in
        # every export commit, twice a day, and a repository that grew by that much each time.
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")
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
    pruned = store.prune_gated_out(conn) if prune else 0
    tagged = themes_mod.ensure(conn)
    routed = classify_rules.ensure_routes(conn)
    authors_cleaned = extract.clean_stored_authors(conn)
    outlets = registry.load_outlets()
    gaps = registry.load_gaps()
    roll = rebuild_rollups(conn, outlets)
    latest = build_latest(conn, outlets, gaps)
    daily = build_daily(conn)
    series = build_global_series(daily)
    outlets_json = build_outlets(conn, outlets)
    articles = build_articles(conn)
    meta = build_meta(conn, outlets, gaps, latest)
    write_json(export_dir / "latest.json", latest)
    for month, m in daily.items():
        write_json(export_dir / "daily" / ("%s.json" % month), m)
    write_json(export_dir / "global_series.json", series)
    write_json(export_dir / "outlets.json", outlets_json)
    # One file per country in latest.json, empty where nothing is classified, so the page never 404s.
    for country in latest["countries"]:
        write_json(export_dir / "articles" / ("%s.json" % country), articles.get(country, []))
    write_json(export_dir / "meta.json", meta)
    counts = {"countries": len(latest["countries"]), "months": len(daily), "days": len(series), "articles_files": len(articles),
              "pruned_gated_out": pruned, "themes_tagged": tagged, "routes_filled": routed, "authors_cleaned": authors_cleaned, "audit_files": write_audit_files(conn, audit_dir)}
    counts.update(roll)
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
