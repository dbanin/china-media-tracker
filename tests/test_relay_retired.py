"""The relay on the owner's machine was retired on 2026-10-22 (owner decision D3).

Collecting outlets that refuse the crawler from another network works around their access control, so the
relay stopped: its outlets (collector: self_hosted) stay registered, for their history, and are inactive;
nothing reactivates them or moves an outlet to the relay; the workflow no longer ingests or acknowledges a
relay bundle and never handles RELAY_BUNDLE_KEY; the export stops calling the relay silent, stops marking
days after it, and says what it left behind; the methodology discloses the gap.
"""
import datetime as dt
import inspect

import yaml

from pipeline import config, export, methodology, registry, store
from pipeline import validate_sources as v

WORKFLOWS = config.ROOT / ".github" / "workflows"
REASON = "blocks the crawler from its server addresses; collection from another network stopped on 2026-10-22 (relay retired)"


def test_no_active_outlet_is_self_hosted():
    outlets = registry.load_outlets()
    assert not [o["id"] for o in outlets if o.get("active") and registry.collector_of(o) == "self_hosted"]
    assert not [o["id"] for o in registry.collectable(outlets, "hosted") if registry.collector_of(o) == "self_hosted"]
    # what the relay seeded and fetched for itself (relay.seed_refused, run_relay): nothing is left
    assert not [o for o in registry.collectable(outlets, "self_hosted") if registry.fetcher_of(o) == "self_hosted"]


def test_the_relay_outlets_are_kept_inactive_with_the_reason_and_the_day():
    outlets = registry.load_outlets()
    dropped = [o for o in outlets if registry.relay_dropped(o)]
    assert len(dropped) == 123
    for o in dropped:
        assert o["active"] is False and o["inactive_reason"] == REASON and o["inactive_since"] == "2026-10-22", o["id"]
        assert o["collector"] == "self_hosted", o["id"]          # kept: the history says how it was collected
    assert config.RELAY_RETIRED_ON == "2026-10-22"
    by_id = {o["id"]: o for o in outlets}
    for oid in ("ck_cinews", "ki_bpa", "it_agi", "ng_sun", "us_seattletimes"):
        assert registry.relay_dropped(by_id[oid]), oid
    # the registry file states it in the same words for every one of them
    text = (config.ROOT / "sources" / "outlets.yaml").read_text(encoding="utf-8")
    assert text.count("inactive_reason: '%s'" % REASON) == 123


def test_the_countries_left_unmonitored_are_the_cook_islands_and_kiribati():
    outlets = registry.load_outlets()
    active = {registry.export_country(o) for o in outlets if o.get("active")}
    dropped = {registry.export_country(o) for o in outlets if registry.relay_dropped(o)}
    assert sorted(dropped - active) == ["COK", "KIR"]
    assert len(dropped & active) == 87


def test_the_validator_never_moves_an_outlet_to_the_relay_or_reactivates_one():
    src = inspect.getsource(v)
    assert "SELF_HOSTED" not in src and '"active"] = True' not in src and "fetch_bundle" not in src
    o = {"id": "x", "feeds": ["u"], "active": False, "collector": "self_hosted", "inactive_reason": REASON,
         "inactive_since": "2026-10-22"}
    assert v.apply_decisions([o], {"x": ("deactivate", "dead (auto)")}, "2026-10-29") == []
    assert o["inactive_reason"] == REASON


def test_the_workflow_no_longer_ingests_the_relay_or_handles_its_key():
    wf = yaml.safe_load((WORKFLOWS / "pipeline.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["pipeline"]["steps"]
    assert not [s.get("name") for s in steps if "pipeline.relay" in (s.get("run") or "")]
    assert not [s.get("name") for s in steps if "RELAY_BUNDLE_KEY" in (s.get("env") or {})]
    for f in ("pipeline.yml", "tests.yml"):
        assert "secrets.RELAY_BUNDLE_KEY" not in (WORKFLOWS / f).read_text(encoding="utf-8"), f
    for script in ("db_restore.sh", "db_snapshot.sh", "db_pull.sh"):
        body = (config.ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert "RELAY_BUNDLE_KEY" not in body and ".enc\"" not in body, script
    assert "RETIRED on 2026-10-22" in (config.ROOT / "scripts" / "relay.sh").read_text(encoding="utf-8")


def _o(oid, country, collector="hosted", active=True, **kw):
    return dict({"id": oid, "name": oid, "country": country, "language": "it", "feeds": [], "tier": "national",
                 "active": active, "collector": collector}, **kw)


OUTLETS = [_o("it_a", "ITA"), _o("it_shared", "ITA", "both"),
           _o("it_relay", "ITA", "self_hosted", False, inactive_reason=REASON, inactive_since="2026-10-22"),
           _o("ck_relay", "COK", "self_hosted", False, inactive_reason=REASON, inactive_since="2026-10-22"),
           _o("ck_old", "COK", "self_hosted", False, inactive_reason="every feed failed (auto)", inactive_since="2026-10-05")]


def _db(tmp_path, hours_ago=200, days=()):
    conn = store.connect(tmp_path / "r.db")
    t = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO relay_runs(started_at, finished_at, ok) VALUES (?,?,1)", (t, t))
    for d in days:
        conn.execute("INSERT INTO relay_runs(started_at, finished_at, ok) VALUES (?,?,1)", (d + "T05:00:00+00:00",) * 2)
    conn.commit()
    return conn


def test_relay_status_after_retirement_says_what_it_left_and_is_never_stale(tmp_path):
    rc = export.relay_status(_db(tmp_path), OUTLETS)
    assert (rc["outlets"], rc["shared_outlets"], rc["countries"], rc["shared_countries"]) == (0, 0, [], [])
    assert rc["stale"] is False and rc["hours_since_last_run"] is None and rc["last_run"]
    assert rc["retired_on"] == "2026-10-22" and rc["dropped_outlets"] == 2
    assert rc["dropped_by_country"] == {"COK": 1, "ITA": 1}
    assert rc["countries_all_inactive"] == ["COK"] and rc["countries_partial"] == ["ITA"]


def test_no_day_after_the_retirement_is_marked_incomplete(tmp_path):
    conn = _db(tmp_path, days=("2026-10-01", "2026-10-02"))
    days = export.relay_incomplete_days(conn, "2026-11-20")
    assert days and max(days) <= "2026-10-22" and "2026-10-15" in days
    assert "2026-10-23" not in days and "2026-11-19" not in days


def test_countries_warn_of_the_stop_and_never_of_a_silent_relay(tmp_path):
    latest = export.build_latest(_db(tmp_path, hours_ago=300), OUTLETS, [], population={})
    ita, cok = latest["countries"]["ITA"], latest["countries"]["COK"]
    types = lambda e: {w["type"]: w["text"] for w in e["warnings"]}
    assert not any(w["type"].startswith("relay_stale") for c in latest["countries"].values() for w in c["warnings"])
    assert (ita["relay_outlets"], ita["shared_outlets"]) == (0, 0)
    assert types(ita)["relay_stopped"].startswith("1 of this country's outlets refuse the crawler from its server addresses")
    assert "until 2026-10-22" in types(ita)["relay_stopped"] and "its other 2 active outlets only" in types(ita)["relay_stopped"]
    assert cok["coverage"] == "no_active_outlets"
    assert types(cok)["relay_stopped"].startswith("every outlet monitored here refuses the crawler")
    assert "it is inactive since, so nothing here is monitored from that day" in types(cok)["relay_stopped"]


def test_known_limits_disclose_the_gap_with_every_country_it_touches():
    outlets = registry.load_outlets()
    text = methodology.known_limits_text({}, outlets)
    gap = methodology.relay_gap_text(outlets)
    assert gap and gap in text
    assert "the relay was stopped on 2026-10-22" in gap and "That left 123 outlets inactive in 89 countries." in gap
    assert "Every monitored outlet was one of them in Cook Islands (1) and Kiribati (1), so they are no longer monitored" in gap
    assert "87 countries keep other outlets but lose some" in gap and "Italy 2 of 23" in gap and "United States 3 of 44" in gap
    assert chr(0x2013) not in gap and chr(0x2014) not in gap


def test_the_methodology_states_the_stop_in_its_table_and_its_collection_section(tmp_path, monkeypatch):
    import json
    db = tmp_path / "m.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    out = tmp_path / "out"
    export.run(store.connect(db), "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M0.md")
    meta = json.loads((out / "meta.json").read_text())
    latest = json.loads((out / "latest.json").read_text())
    assert meta["relay_collector"]["retired_on"] == "2026-10-22" and meta["relay_collector"]["stale"] is False
    meta["relay_collector"] = {"retired_on": "2026-10-22", "dropped_outlets": 123, "dropped_by_country": {"COK": 1, "KIR": 1, "ITA": 2},
                               "countries_all_inactive": ["COK", "KIR"], "last_run": "2026-10-22T05:00:00+00:00",
                               "incomplete_days": ["2026-10-10"]}
    path = tmp_path / "M.md"
    methodology.write(meta, latest, path=path)
    flat = " ".join(path.read_text(encoding="utf-8").split())
    assert ("| Relay collector on the owner's machine, retired | stopped on 2026-10-22; 123 outlets it alone collected made "
            "inactive, in 3 countries, every monitored outlet in 2 of them; last pass 2026-10-22T05:00:00+00:00; "
            "1 incomplete days up to then |") in flat
    assert "The relay was stopped on 2026-10-22, and its outlets are inactive since (Known limits); no day after it is marked" in flat
    assert "Outlets that refuse the crawler from its server addresses are not collected." in flat


# ---------------------------------------------------------------------------
# relay articles fetched on the owner's machine whose text never reached the runner
# ---------------------------------------------------------------------------

def _settle_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "COLLECTOR", "hosted")
    conn = store.connect(tmp_path / "s.db")
    ids = {}
    for name, outlet, status, gate in (("stranded", "it_relay", "fetched", 1), ("stranded2", "ck_relay", "fetched", 1),
                                       ("with_body", "it_relay", "fetched", 1), ("hosted", "it_a", "fetched", 1),
                                       ("classified", "it_relay", "classified", 1), ("old", "ck_old", "fetched", 1)):
        ids[name] = store.insert_discovered(conn, {"url": "https://x.test/%s" % name, "outlet_id": outlet, "country": "ITA",
                                                   "language": "it", "title": "China %s" % name, "status": status,
                                                   "gate_relevant": gate})
    store.save_body(store.get_article(conn, ids["with_body"])["url_hash"], "The body arrived.")
    conn.commit()
    return conn, ids


def test_relay_articles_with_no_text_are_settled_as_failed_once_and_never_retried(tmp_path, monkeypatch):
    from pipeline import classify_rules, fetch_articles
    conn, ids = _settle_db(tmp_path, monkeypatch)
    assert classify_rules.settle_relay_dropped(conn, OUTLETS) == 3
    st = {k: tuple(conn.execute("SELECT status, fail_reason FROM articles WHERE id=?", (v,)).fetchone()) for k, v in ids.items()}
    for k in ("stranded", "stranded2", "old"):
        assert st[k] == ("failed", "relay_dropped_text_not_delivered"), k
    assert st["with_body"] == ("fetched", None) and st["hosted"] == ("fetched", None) and st["classified"][0] == "classified"
    assert classify_rules.settle_relay_dropped(conn, OUTLETS) == 0                  # deterministic: once
    assert not classify_rules.RELAY_DROPPED_FAIL.startswith(fetch_articles.TRANSIENT_FAIL_PREFIXES)
    assert ids["stranded"] not in {r["id"] for r in fetch_articles.retry_candidates(conn)}
    monkeypatch.setattr(config, "COLLECTOR", "self_hosted")
    conn.execute("UPDATE articles SET status='fetched', fail_reason=NULL WHERE id=?", (ids["stranded"],))
    assert classify_rules.settle_relay_dropped(conn, OUTLETS) == 0                  # only on the runner


def test_settled_relay_articles_stay_text_not_delivered_in_the_gate_accounting(tmp_path, monkeypatch):
    from pipeline import classify_rules
    conn, ids = _settle_db(tmp_path, monkeypatch)
    before = export.gate_accounting(conn, OUTLETS)
    classify_rules.settle_relay_dropped(conn, OUTLETS)
    after = export.gate_accounting(conn, OUTLETS)
    assert before["total"] == after["total"] == sum(after["parts"].values()) == 6
    assert before["parts"]["fetched_text_not_delivered"] == after["parts"]["fetched_text_not_delivered"] == 3
    assert after["parts"]["failed"] == 0 and after["parts"]["fetched_waiting_rules"] == 2
