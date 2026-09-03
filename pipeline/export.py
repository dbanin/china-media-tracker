"""Rebuild rollups and generate the static JSON the front end reads.

Outputs in docs/data/:
  latest.json          per-country totals, all time and last 30 days, with coverage flags
  daily/YYYY-MM.json   per-day, per-country counts by category and method
  outlets.json         registry with coverage metadata and feed health
  meta.json            last run, kappa, review coverage, ruleset and schema versions, gaps
  articles/ISO3.json   most recent classified articles per country for the country panel
  global_series.json   per-day global totals for the sparkline

A day with an LLM ceiling event is marked in the daily file so a truncated day
is never mistaken for a quiet day.
"""
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from pipeline import config, registry, store

CATEGORIES = ["A", "B", "C", "not_relevant"]


STALE_PUBLISHED_DAYS = 14


def _day(row) -> str:
    """Date attribution. The published date when the feed gave one and it is within
    STALE_PUBLISHED_DAYS before discovery, otherwise the discovery date. Feeds sometimes
    resurface items published years earlier; counting those on their old date would
    rewrite history that was never observed."""
    disc = (row["discovered_at"] or "")[:10]
    pub = (row["published_at"] or "")[:10]
    if pub and disc and len(pub) == 10:
        try:
            pd = dt.date.fromisoformat(pub)
            dd = dt.date.fromisoformat(disc)
            if dd - dt.timedelta(days=STALE_PUBLISHED_DAYS) <= pd <= dd + dt.timedelta(days=1):
                return pub
        except ValueError:
            pass
    return disc


def article_rows(conn) -> List[Dict]:
    """One row per article with its current machine label and its latest human label."""
    rows = conn.execute(
        """SELECT a.id, a.country, a.outlet_id, a.language, a.title, a.url, a.published_at, a.discovered_at,
                  a.status, a.gate_relevant, a.dup_group_id, a.fail_reason,
                  c.category AS m_cat, c.method AS m_method, c.confidence AS m_conf, c.evidence_quote,
                  c.reasoning, c.signatures_fired, c.classified_at, c.ruleset_version, c.model_version,
                  (SELECT human_category FROM human_reviews h WHERE h.article_id=a.id ORDER BY h.id DESC LIMIT 1) AS h_cat
           FROM articles a LEFT JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE a.gate_relevant=1"""
    ).fetchall()
    return [dict(r) for r in rows]


def rebuild_rollups(conn) -> Dict:
    rows = article_rows(conn)
    now = store.utcnow()
    counts = defaultdict(lambda: {"n": 0, "n_rules": 0, "n_llm": 0, "n_reviewed": 0, "groups": set()})
    coverage = defaultdict(lambda: {"discovered": 0, "gate_relevant": 0, "fetched": 0, "paywalled": 0,
                                    "failed": 0, "blocked_robots": 0, "classified": 0, "llm_pending": 0})
    for r in rows:
        d = _day(r)
        cov = coverage[(d, r["country"])]
        cov["gate_relevant"] += 1
        st = r["status"]
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
    # discovered totals per day and country include gated-out items
    for r in conn.execute("SELECT country, published_at, discovered_at FROM articles"):
        d = _day(r)
        coverage[(d, r["country"])]["discovered"] += 1

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
               blocked_robots, classified, llm_pending, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (d, country, cov["discovered"], cov["gate_relevant"], cov["fetched"], cov["paywalled"], cov["failed"],
             cov["blocked_robots"], cov["classified"], cov["llm_pending"], now),
        )
    conn.commit()
    return {"daily_counts": len(counts), "daily_coverage": len(coverage), "articles": len(rows)}


def _empty_country() -> Dict:
    return {"A": 0, "B": 0, "C": 0, "N": 0, "Ar": 0, "Al": 0, "Ah": 0, "Br": 0, "Bl": 0, "Bh": 0,
            "rev": 0, "cls": 0, "disc": 0, "rel": 0, "fetched": 0, "paywalled": 0, "failed": 0,
            "blocked": 0, "pending": 0, "uniqA": 0, "uniqAB": 0}


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


def _derive(c: Dict, outlets_active: int) -> Dict:
    china = c["A"] + c["B"] + c["C"]
    c["china_total"] = china
    c["share_ab"] = round((c["A"] + c["B"]) / china, 4) if china else None
    c["share_a"] = round(c["A"] / china, 4) if china else None
    c["per_outlet_ab"] = round((c["A"] + c["B"]) / outlets_active, 3) if outlets_active else None
    c["per_outlet_a"] = round(c["A"] / outlets_active, 3) if outlets_active else None
    attempted = c["fetched"] + c["paywalled"] + c["failed"] + c["blocked"]
    c["paywall_share"] = round(c["paywalled"] / attempted, 4) if attempted else None
    c["reviewed_share"] = round(c["rev"] / c["cls"], 4) if c["cls"] else None
    return c


def build_latest(conn, outlets: List[Dict], gaps: List[Dict]) -> Dict:
    today = dt.datetime.now(dt.timezone.utc).date()
    since30 = (today - dt.timedelta(days=30)).isoformat()
    by_country_outlets = defaultdict(list)
    for o in outlets:
        by_country_outlets[o["country"]].append(o)
    feed_health = {r["feed_url"]: dict(r) for r in conn.execute("SELECT * FROM feed_health")}

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

    countries = {}
    for country, os_ in by_country_outlets.items():
        active = [o for o in os_ if o["active"]]
        feeds = [f for o in active for f in o["feeds"]]
        feeds_ok = sum(1 for f in feeds if feed_health.get(f) and feed_health[f]["consecutive_failures"] == 0)
        entry = {
            "coverage": "monitored" if active else "no_active_outlets",
            "outlets_total": len(os_), "outlets_active": len(active),
            "feeds_total": len(feeds), "feeds_ok": feeds_ok,
            "all_time": _derive(dict(all_time[country]), len(active)),
            "last_30d": _derive(dict(last30[country]), len(active)),
            "reviewed_all_time": _reviewed_block(conn, country, None),
            "reviewed_last_30d": _reviewed_block(conn, country, since30),
            "warnings": [],
        }
        if feeds and (len(feeds) - feeds_ok) / float(len(feeds)) >= config.FEED_FAILURE_WARNING_SHARE:
            entry["warnings"].append({"type": "feeds_failing", "text": "%d of %d feeds are failing" % (len(feeds) - feeds_ok, len(feeds))})
        pw = entry["all_time"]["paywall_share"]
        if pw is not None and pw >= config.PAYWALL_FLAG_SHARE and entry["all_time"]["rel"] >= 10:
            entry["warnings"].append({"type": "paywalled", "text": "%d percent of retrieved articles were paywalled; counts are not comparable to other countries" % round(pw * 100)})
        at = entry["all_time"]
        attempted = at["fetched"] + at["paywalled"] + at["failed"] + at["blocked"]
        if attempted >= 10 and (at["failed"] + at["blocked"]) / float(attempted) >= config.PAYWALL_FLAG_SHARE:
            entry["warnings"].append({"type": "fetch_failing", "text": "%d percent of article fetches failed or were blocked by robots.txt; counts understate this country" % round(100.0 * (at["failed"] + at["blocked"]) / attempted)})
        pending = entry["all_time"]["pending"]
        if pending >= 5 and pending >= 0.25 * max(entry["all_time"]["rel"], 1):
            entry["warnings"].append({"type": "llm_backlog", "text": "%d articles are awaiting model classification, so A plus B is a floor" % pending})
        if not active:
            entry["warnings"].append({"type": "no_active_outlets", "text": "all registered outlets are inactive"})
        countries[country] = entry
    for g in gaps:
        if g["country"] not in countries:
            countries[g["country"]] = {"coverage": "gap", "gap_reason": g["reason"], "outlets_total": 0, "outlets_active": 0,
                                       "feeds_total": 0, "feeds_ok": 0, "all_time": _derive(_empty_country(), 0),
                                       "last_30d": _derive(_empty_country(), 0), "warnings": []}

    totals = {"all_time": _derive(_empty_country(), sum(1 for o in outlets if o["active"])),
              "last_30d": _derive(_empty_country(), sum(1 for o in outlets if o["active"]))}
    for scope_key, src in (("all_time", all_time), ("last_30d", last30)):
        t = totals[scope_key]
        for c in src.values():
            for k in _empty_country():
                t[k] += c[k]
        _derive(t, sum(1 for o in outlets if o["active"]))
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
    ceiling_days = {r["date"] for r in conn.execute("SELECT date FROM llm_usage WHERE ceiling_hit=1")}
    reviewed = defaultdict(lambda: defaultdict(lambda: {"A": 0, "B": 0, "C": 0, "N": 0}))
    for r in conn.execute("SELECT * FROM daily_counts WHERE scope='reviewed'"):
        reviewed[r["date"]][r["country"]][{"A": "A", "B": "B", "C": "C", "not_relevant": "N"}[r["category"]]] += r["n"]
    out = {}
    for month, m in months.items():
        days = {}
        for d, per_country in sorted(m["days"].items()):
            days[d] = {"countries": {c: v for c, v in per_country.items()},
                       "reviewed": {c: v for c, v in reviewed.get(d, {}).items()},
                       "llm_ceiling_hit": d in ceiling_days}
        out[month] = {"month": month, "days": days}
    return out


def build_global_series(daily: Dict[str, Dict]) -> List[Dict]:
    series = []
    for month in sorted(daily):
        for d, day in sorted(daily[month]["days"].items()):
            tot = {"date": d, "A": 0, "B": 0, "C": 0, "N": 0, "rel": 0, "paywalled": 0, "pending": 0,
                   "countries_with_A": 0, "llm_ceiling_hit": day["llm_ceiling_hit"]}
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
                  SUM(CASE WHEN status='blocked_robots' THEN 1 ELSE 0 END) blocked
           FROM articles GROUP BY outlet_id"""
    ):
        t = per_outlet[r["outlet_id"]]
        t["disc"] = r["disc"]; t["rel"] = r["rel"] or 0; t["fetched"] = r["fetched"] or 0
        t["paywalled"] = r["paywalled"] or 0; t["failed"] = r["failed"] or 0; t["blocked"] = r["blocked"] or 0
    out = []
    for o in outlets:
        feeds = []
        for f in o["feeds"]:
            h = feed_health.get(f)
            feeds.append({"url": f, "ok": bool(h and h["consecutive_failures"] == 0), "last_ok": h["last_ok"] if h else None,
                          "last_error": h["last_error"] if h else None, "consecutive_failures": h["consecutive_failures"] if h else None})
        out.append({"id": o["id"], "name": o["name"], "country": o["country"], "language": o["language"],
                    "tier": o["tier"], "active": o["active"], "notes": o.get("notes"),
                    "inactive_reason": o.get("inactive_reason"), "feeds": feeds, "counts": dict(per_outlet[o["id"]])})
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
    """Per country: state origin first, then unverified relay, then articles carrying official
    Chinese sourcing whose verification judgement is pending, then independent journalism."""
    rows = conn.execute(
        """SELECT a.id, a.country, a.outlet_id, a.title, a.url, a.published_at, a.discovered_at, a.dup_group_id,
                  a.status, a.llm_trigger,
                  c.category, c.method, c.confidence, c.evidence_quote, c.reasoning, c.signatures_fired, c.model_version,
                  c.ruleset_version, c.china_sources_cited,
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
        out[country].sort(key=lambda e: (ORDER.get(e["category"], 9), e["date"]), reverse=False)
        out[country].sort(key=lambda e: ORDER.get(e["category"], 9))
        out[country] = out[country][:per_country]
    return out


def build_meta(conn, outlets: List[Dict], gaps: List[Dict], latest: Dict) -> Dict:
    last_runs = {}
    for r in conn.execute("SELECT stage, MAX(finished_at) f FROM run_log WHERE ok=1 GROUP BY stage"):
        last_runs[r["stage"]] = r["f"]
    kappa = conn.execute("SELECT * FROM agreement_studies ORDER BY id DESC LIMIT 1").fetchone()
    cls_total = conn.execute("SELECT COUNT(*) FROM classifications WHERE is_current=1").fetchone()[0]
    reviewed_total = conn.execute("SELECT COUNT(DISTINCT article_id) FROM human_reviews").fetchone()[0]
    llm = conn.execute("SELECT SUM(calls) calls, SUM(ceiling_hit) ceilings FROM llm_usage").fetchone()
    ceiling_days = [r["date"] for r in conn.execute("SELECT date FROM llm_usage WHERE ceiling_hit=1 ORDER BY date")]
    active = [o for o in outlets if o["active"]]
    countries_active = sorted({o["country"] for o in active})
    tot = latest["totals"]["all_time"]
    attempted = tot["fetched"] + tot["paywalled"] + tot["failed"] + tot["blocked"]
    paywall_countries = [c for c, v in latest["countries"].items()
                         if any(w["type"] == "paywalled" for w in v.get("warnings", []))]
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
        "gaps": gaps,
        "articles_discovered": conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
        "first_discovered": (conn.execute("SELECT MIN(discovered_at) FROM articles").fetchone()[0] or "")[:10] or None,
        "articles_gate_relevant": conn.execute("SELECT COUNT(*) FROM articles WHERE gate_relevant=1").fetchone()[0],
        "articles_classified": cls_total,
        "official_sourcing_pending": conn.execute("SELECT COUNT(*) FROM articles WHERE status IN ('awaiting_llm','llm_submitted')").fetchone()[0],
        "official_sourcing_pending_countries": conn.execute("SELECT COUNT(DISTINCT country) FROM articles WHERE status IN ('awaiting_llm','llm_submitted')").fetchone()[0],
        "articles_reviewed": reviewed_total,
        "review_coverage": round(reviewed_total / cls_total, 4) if cls_total else 0.0,
        "paywall_share": round(tot["paywalled"] / attempted, 4) if attempted else None,
        "paywall_flagged_countries": sorted(paywall_countries),
        "kappa": {"all": kappa["kappa_all"], "bc": kappa["kappa_bc"], "n": kappa["sample_size"], "n_bc": kappa["n_bc"],
                  "computed_at": kappa["computed_at"]} if kappa else None,
        "kappa_warning_threshold": config.KAPPA_WARNING_THRESHOLD,
        "b_counts_settled": bool(kappa and kappa["kappa_bc"] is not None and kappa["kappa_bc"] >= config.KAPPA_WARNING_THRESHOLD),
        "llm_calls_total": llm["calls"] or 0,
        "llm_ceiling_days": ceiling_days,
        "llm_daily_ceiling": config.LLM_DAILY_CALL_CEILING,
        "paywall_flag_share": config.PAYWALL_FLAG_SHARE,
        "categories": {
            "A": "State origin. Text written by an entity of the Chinese state and published essentially unaltered.",
            "B": "Unverified relay. Written by the local outlet but passes on official Chinese sourcing without independent confirmation.",
            "C": "Independent journalism. The outlet's own reporting, including reporting that quotes Chinese officials but confirms, contextualizes or contests what they say.",
            "not_relevant": "Does not concern China.",
        },
    }


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def run(conn, run_id: str = "export", export_dir: Path = config.EXPORT_DIR) -> Dict:
    log_id = store.start_stage(conn, run_id, "export")
    outlets = registry.load_outlets()
    gaps = registry.load_gaps()
    roll = rebuild_rollups(conn)
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
    counts = {"countries": len(latest["countries"]), "months": len(daily), "days": len(series), "articles_files": len(articles)}
    counts.update(roll)
    store.finish_stage(conn, log_id, True, counts)
    try:
        from pipeline import methodology
        methodology.write(meta, latest)
        counts["methodology"] = True
    except ImportError:
        counts["methodology"] = False
    return counts


if __name__ == "__main__":
    conn = store.connect()
    print(json.dumps(run(conn)))
