"""Changes made after the September 2026 methodology review: the gate, the model draw, routes and
arrivals, feed saturation, the relay heartbeat, per language agreement and the publication gates."""
import datetime as dt
import json

from pipeline import agreement, classify_llm as cl, classify_rules as cr, config, export, fetch_feeds, gate, relay, store

UTC = dt.timezone.utc


def test_state_origin_signature_passes_the_gate_without_a_china_term():
    ok, terms = gate.check("Investment week opens with record deals",
                           "PR Newswire -- The Information Office of the Haikou Municipal Government announced the programme.", "en")
    assert ok and any(t.startswith("state_origin_signature:") for t in terms)
    ok, _ = gate.check("Acme opens a new plant", "PR Newswire -- Acme Corp announced a new plant in Ohio.", "en")
    assert not ok, "a bare wire stamp is a hint, not a state origin decision"


def test_provinces_pass_the_gate():
    assert gate.check("Jiangxi delegation visits Jakarta", "", "id")[0]
    assert gate.check("Shanxi province struggles to diversify away from coal", "", "en")[0]


def test_home_phrases_for_singapore_and_malaysia():
    assert not gate.check("Chinese New Year bazaar returns to Chinatown", "", "en", "SGP")[0]
    assert gate.check("Chinese New Year bazaar returns to Chinatown", "", "en", "GBR")[0]
    assert gate.check("Chinese New Year travel: Beijing eases visa rules", "", "en", "SGP")[0]
    assert not gate.check("Malaysian Chinese Association elects a new president", "", "en", "MYS")[0]
    assert not gate.check("Tahun Baru Cina disambut meriah", "", "ms", "MYS")[0]
    distinct, total, _ = gate.count("Chinese community leaders meet", "The Chinese community gathered at the temple.", "en", "SGP")
    assert (distinct, total) == (0, 0)


def test_draw_gives_every_country_the_same_fraction():
    rows = [{"country": c, "id": "%s%d" % (c, i)} for c, n in (("IND", 60), ("USA", 30), ("FJI", 10)) for i in range(n)]
    chosen, alloc = cl.draw(rows, 50, "seed")
    assert len(chosen) == 50 and alloc == {"FJI": [10, 5], "IND": [60, 30], "USA": [30, 15]}
    assert cl.draw(rows, 50, "seed")[0] == chosen
    few, alloc = cl.draw(rows, 7, "seed")
    assert len(few) == 7 and alloc == {"FJI": [10, 1], "IND": [60, 4], "USA": [30, 2]}
    assert cl.draw(rows, 500, "seed")[1]["IND"] == [60, 60]


def test_binding_ceiling_records_the_draw(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    monkeypatch.setattr(config, "LLM_DAILY_CALL_CEILING", 3)
    conn = store.connect(tmp_path / "d.db")
    for i, country in enumerate(["ITA"] * 4 + ["KEN"] * 2):
        url = "https://e.com/%d" % i
        store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": country, "language": "en",
                                       "title": "T%d" % i, "status": "awaiting_llm", "gate_relevant": 1})
        store.save_body(store.url_hash(url), "Body %d" % i)
    conn.commit()
    counts = cl.run(conn, "t", client=cl.DryRunClient())
    assert counts["calls"] == 3 and counts["ceiling_hit"] is True
    rows = {r["country"]: (r["eligible"], r["drawn"]) for r in conn.execute("SELECT * FROM llm_sampling")}
    assert rows == {"ITA": (4, 2), "KEN": (2, 1)}


def test_route_and_arrival():
    matches = [{"group": "sponsored_placement", "strength": "weak"}, {"group": "distribution_stamp", "strength": "strong"},
               {"group": "credit_dateline", "strength": "hint"}]
    assert cr.route_of(matches) == "distribution_stamp"
    assert cr.route_of([{"group": "credit_dateline", "strength": "hint"}]) == "unattributed"
    assert cr.route_of_ids(["xinhua_dateline"]) == "wire_credit"
    assert cr.route_of_ids(["diplomat_list_author"]) == "diplomatic_byline"
    assert cr.route_of_ids(["retired_pattern"]) == "unattributed"
    assert cr.arrival_of('["section:press_release"]') == "press_release_section"
    assert cr.arrival_of('["China"]') == "editorial_feed" and cr.arrival_of(None) == "editorial_feed"


def test_rules_label_stores_its_route_and_old_labels_are_backfilled(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    conn = store.connect(tmp_path / "r.db")
    url = "https://x.test/grain"
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": "ITA", "language": "en",
                                         "title": "Grain output rises", "status": "fetched", "gate_relevant": 1})
    store.save_body(store.url_hash(url), "BEIJING, Sept. 2 (Xinhua) -- China's grain output rose to a record.")
    conn.commit()
    assert cr.classify_article(conn, store.get_article(conn, aid))["outcome"] == "A"
    assert store.current_classification(conn, aid)["route"] == "wire_credit"
    conn.execute("UPDATE classifications SET route=NULL")
    assert cr.ensure_routes(conn) == 1
    assert store.current_classification(conn, aid)["route"] == "wire_credit"


def test_saturation_and_missed_estimate():
    now = dt.datetime(2026, 9, 10, 14, tzinfo=UTC)
    times = ["2026-09-10T12:00:00+00:00", "2026-09-10T13:00:00+00:00", "2026-09-10T14:00:00+00:00"]
    prev = {"last_ok": "2026-09-10T10:00:00+00:00"}
    assert fetch_feeds.saturation(prev, 3, 3, times, now) == (True, 2.0)
    assert fetch_feeds.saturation(prev, 3, 2, times, now) == (False, 0.0)
    assert fetch_feeds.saturation(None, 3, 3, times, now) == (False, 0.0)
    assert fetch_feeds.saturation({"last_ok": "2026-09-10T12:30:00+00:00"}, 3, 3, times, now) == (True, 0.0)
    assert fetch_feeds.saturation(prev, 3, 3, [], now) == (True, 0.0)


def test_feed_polls_reach_the_daily_file_and_warn(tmp_path):
    conn = store.connect(tmp_path / "f.db")
    for i in range(12):
        store.record_feed_poll(conn, "o", "ITA", "https://x.test/feed", 20, 20 if i < 6 else 3, i < 6, 4.0 if i < 6 else 0.0)
    conn.commit()
    today = dt.datetime.now(UTC).date().isoformat()
    day = export.build_daily(conn)[today[:7]]["days"][today]["countries"]["ITA"]
    assert (day["polls"], day["sat"], day["miss"]) == (12, 6, 24)
    outlets = [{"id": "o", "name": "O", "country": "ITA", "language": "it", "feeds": ["https://x.test/feed"], "tier": "regional", "active": True}]
    latest = export.build_latest(conn, outlets, [], population={})
    assert any(w["type"] == "feed_saturation" for w in latest["countries"]["ITA"]["warnings"])


def test_relay_runs_travel_in_the_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    src = store.connect(tmp_path / "relay.db")
    store.finish_stage(src, store.start_stage(src, "relay-x", "discover"), True, {})
    dst = store.connect(tmp_path / "main.db")
    counts = relay.ingest(dst, relay.build_bundle(src))
    assert counts["relay_runs"] == 1 and dst.execute("SELECT ok FROM relay_runs").fetchone()[0] == 1
    assert relay.ingest(dst, relay.build_bundle(src))["relay_runs"] == 1
    assert dst.execute("SELECT COUNT(*) FROM relay_runs").fetchone()[0] == 1


def test_relay_incomplete_days(tmp_path):
    conn = store.connect(tmp_path / "h.db")
    for day, hours in (("2026-09-10", 14), ("2026-09-11", 3), ("2026-09-12", 20)):
        for h in range(hours):
            conn.execute("INSERT INTO relay_runs(started_at, finished_at, ok) VALUES (?,?,1)",
                         ("%sT%02d:05:00+00:00" % (day, h), "%sT%02d:20:00+00:00" % (day, h)))
    conn.commit()
    assert export.relay_incomplete_days(conn, today="2026-09-14") == ["2026-09-11", "2026-09-13"]


def test_agreement_by_category_and_language():
    items = [{"machine_category": m, "human_category": h, "language": lang} for m, h, lang in (
        ("B", "B", "it"), ("B", "C", "it"), ("C", "C", "it"), ("C", "B", "fr"), ("A", "A", "fr"), ("B", "B", "fr"))]
    res = agreement.compute_from_pairs(items)
    assert res["bc_by_language"]["it"]["n"] == 3 and res["bc_by_language"]["fr"]["n"] == 2
    assert set(res["kappa_by_category"]) == {"A", "B", "C"} and res["kappa_by_category"]["A"]["kappa"] == 1.0


def test_relay_is_withheld_until_measured_and_settled(tmp_path):
    conn = store.connect(tmp_path / "m.db")
    meta = export.build_meta(conn, [], [], export.build_latest(conn, [], [], population={}))
    assert meta["relay_measured"] is False and meta["relay_publishable"] is False and meta["paywalled_in_denominator"] is False
    store.record_llm_usage(conn, 5, 0, 0)
    conn.execute("INSERT INTO agreement_studies(computed_at, sample_size, kappa_all, kappa_bc, n_bc, details) VALUES (?,?,?,?,?,?)",
                 ("2026-09-15T00:00:00+00:00", 100, 0.8, 0.7, 40,
                  json.dumps({"bc_by_language": {"it": {"kappa": 0.3, "n": 25}, "fr": {"kappa": 0.2, "n": 5}}})))
    conn.commit()
    meta = export.build_meta(conn, [], [], export.build_latest(conn, [], [], population={}))
    assert meta["relay_publishable"] is True and meta["relay_withheld_languages"] == ["it"]


def test_language_support_and_ruleset_history():
    langs = gate.supported_languages()
    assert export.language_support(["pap"], langs) == "none"
    assert export.language_support(["en", "pap"], langs) == "partial"
    assert export.language_support(["it", "en"], langs) == "full"
    assert {"version": "2026.09.5", "date": "2026-09-14"} in export.ruleset_changes()


def test_gate_version_is_separate_from_the_ruleset(tmp_path):
    """A gate change moves what is collected, not how anything is labelled, so it carries its own
    version and never triggers reclassification."""
    assert {"version": config.GATE_VERSION, "date": "2026-09-15"} in export.gate_changes()
    assert {"version": config.RULESET_VERSION, "date": "2026-09-15"} in export.ruleset_changes()
    # The two series are read from their own headings and never pick up each other's entries, even
    # when a gate change and a ruleset change happen to carry the same version number on one day.
    assert not any(c["version"] == "2026.09.5" for c in export.gate_changes())
    assert {"version": "2026.09.5", "date": "2026-09-14"} in export.ruleset_changes()
    conn = store.connect(tmp_path / "g.db")
    meta = export.build_meta(conn, [], [], export.build_latest(conn, [], [], population={}))
    assert meta["gate_version"] == config.GATE_VERSION and meta["gate_applies_forward_only"] is True
    assert meta["gate_changes"] and meta["ruleset_version"] == config.RULESET_VERSION


def test_themes_read_the_whole_body():
    body = "x " * 600 + "The warships held drills near Taiwan."
    assert "security" in cr_themes_tag(body)


def cr_themes_tag(body):
    from pipeline import themes
    return themes.tag("Regional news", "", body, "en")
