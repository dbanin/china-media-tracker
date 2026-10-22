"""Every article that passed the relevance gate is accounted for (meta.gate_accounting), on the basis of
articles_gate_relevant, so the parts add up to it exactly; the site's gate total, summed from the daily
files, is that less the copies counted once. And the rules stage always gets a slice of a full run
(pipeline/run.py CLASSIFY_RESERVE_SECONDS): from 2026-10-20 it was skipped whenever discovery and the fetch
used the budget, and fetched articles waited for it."""
import types

from pipeline import config, export, methodology, run, store

OUTLETS = [
    {"id": "it_a", "name": "Italian Outlet", "country": "ITA", "language": "it", "feeds": [], "tier": "national", "active": True},
    {"id": "pk_relay", "name": "Relay Outlet", "country": "PAK", "language": "en", "feeds": [], "tier": "national",
     "active": True, "collector": "self_hosted"},
]
_seq = [0]


def _art(conn, status, outlet="it_a", country="ITA", gate=1, label=None):
    _seq[0] += 1
    aid = store.insert_discovered(conn, {"url": "https://x.test/%s/%d" % (outlet, _seq[0]), "outlet_id": outlet,
                                         "country": country, "language": "it", "title": "China story %d" % _seq[0],
                                         "status": status, "gate_relevant": gate})
    if label:
        store.insert_classification(conn, aid, "rules", label, 0.8, route="wire_credit" if label == "A" else None)
    return aid


def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "g.db")
    for cat in ("A", "C", "C", "not_relevant"):
        _art(conn, "classified", label=cat)
    for st in ("paywalled", "paywalled", "failed", "blocked_robots", "awaiting_llm", "llm_submitted", "queued"):
        _art(conn, st)
    _art(conn, "fetched")                                         # a body to read, waiting for the rules stage
    _art(conn, "fetched", outlet="pk_relay", country="PAK")       # fetched on the owner's machine, text withheld
    delivered = _art(conn, "fetched", outlet="pk_relay", country="PAK")
    store.save_body(store.get_article(conn, delivered)["url_hash"], "The body arrived in a sealed bundle.")
    _art(conn, "junk_feed")                                       # any other status is its own part
    _art(conn, "gated_out", gate=0)                               # outside the gate: in no part
    conn.commit()
    return conn


def _meta(conn):
    export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [])
    return latest, export.build_meta(conn, OUTLETS, [], latest)


def test_the_parts_add_up_to_the_gate_total(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    latest, meta = _meta(conn)
    ga = meta["gate_accounting"]
    assert set(ga["parts"]) == set(export.GATE_ACCOUNTING_PARTS)
    assert sum(ga["parts"].values()) == ga["total"] == meta["articles_gate_relevant"] == 15
    assert ga["parts"] == {"classified": 4, "paywalled": 2, "failed": 1, "blocked_robots": 1,
                           "fetched_waiting_rules": 2, "fetched_text_not_delivered": 1, "waiting_for_model": 2,
                           "not_yet_fetched": 1, "other": 1}
    assert ga["other_statuses"] == {"junk_feed": 1}
    # The same basis as the classified count beside it.
    assert ga["parts"]["classified"] == meta["articles_classified"]
    assert ga["copies_counted_once"] == 0


def test_the_site_rows_add_up_to_the_gate_total_of_the_daily_files(tmp_path, monkeypatch):
    """The rows the side panel prints (docs/app.js gateRows) from the summed daily counts add up to their
    rel, which is the meta total less the copies counted once."""
    conn = _db(tmp_path, monkeypatch)
    latest, meta = _meta(conn)
    t = latest["totals"]["all_time"]
    waiting = t["fetched"] - t["cls"] - t["pending"]
    unfetched = t["rel"] - t["fetched"] - t["paywalled"] - t["failed"] - t["blocked"]
    # M, model judgement held apart from state origin (pipeline/model_only.py), is a labelled row too.
    assert t["A"] + t["B"] + t["C"] + t["N"] + t.get("M", 0) == t["cls"]
    assert waiting == 3 and unfetched == 2                        # the queued row and the junk feed row
    assert (t["cls"] + t["pending"] + waiting + t["paywalled"] + t["failed"] + t["blocked"] + unfetched == t["rel"]
            == meta["gate_accounting"]["total"] - meta["gate_accounting"]["copies_counted_once"])


def test_methodology_states_the_accounting(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    latest, meta = _meta(conn)
    text = methodology.gate_accounting_text(meta)
    assert text.startswith("15 articles passed the relevance gate: 4 classified; 2 paywalled")
    assert "1 fetched on the owner's machine whose text never reached the runner" in text
    assert "These parts add up to 15." in text
    assert methodology.gate_accounting_text({}) == "not recorded in this export"


def test_classify_keeps_a_slice_of_a_full_run(monkeypatch):
    seen = []

    def stage(name):
        return lambda conn, run_id, args, deadline: seen.append((name, deadline)) or {}
    monkeypatch.setattr(run, "cmd_discover", stage("discover"))
    monkeypatch.setattr(run, "cmd_fetch", stage("fetch"))
    monkeypatch.setattr(run, "cmd_classify", stage("classify"))
    monkeypatch.setattr(run.store, "connect", lambda *a, **k: types.SimpleNamespace(commit=lambda: None, close=lambda: None))
    monkeypatch.setattr(run.time, "time", lambda: 1000.0)
    run.main(["all", "--budget-minutes", "35"])
    end = 1000.0 + 35 * 60
    assert seen == [("discover", end - run.CLASSIFY_RESERVE_SECONDS), ("fetch", end - run.CLASSIFY_RESERVE_SECONDS),
                    ("classify", end)]
    seen.clear()
    run.main(["fetch", "--budget-minutes", "20"])                  # a stage by itself keeps the whole budget
    assert seen == [("fetch", 1000.0 + 20 * 60)]


def test_classify_runs_when_discovery_used_the_rest_of_the_budget(monkeypatch):
    clock = {"t": 1000.0}
    seen = []

    def discover(conn, run_id, args, deadline):
        clock["t"] = deadline + 10                                 # discovery ran past its own deadline
        seen.append("discover")
        return {}
    monkeypatch.setattr(run, "cmd_discover", discover)
    monkeypatch.setattr(run, "cmd_fetch", lambda *a: seen.append("fetch") or {})
    monkeypatch.setattr(run, "cmd_classify", lambda *a: seen.append("classify") or {})
    monkeypatch.setattr(run.store, "connect", lambda *a, **k: types.SimpleNamespace(commit=lambda: None, close=lambda: None))
    monkeypatch.setattr(run.time, "time", lambda: clock["t"])
    summary = run.main(["all", "--budget-minutes", "35"])
    assert seen == ["discover", "classify"] and set(summary) == {"discover", "classify"}


def test_the_site_prints_the_rows_and_says_they_add_up():
    app = (config.ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    for words in ("Fetch failed after retries or page unreadable, unread and uncounted",
                  "Blocked by the site's robots.txt, unread and uncounted",
                  "Fetched, waiting for the rules stage, not yet counted", "Not yet fetched, unread and uncounted",
                  "Passed the relevance gate", "These rows add up to the ", "gateRows(t)", "gateRows(k)"):
        assert words in app, words
    # The old row that ran four counts together, "fetched" among them meaning every article read, is gone.
    assert "Fetched / paywalled / failed / robots" not in app


def test_every_share_in_the_findings_states_its_basis():
    import json
    templates = json.loads((config.ROOT / "docs" / "content.json").read_text(encoding="utf-8"))["findings_template"]
    share = [t for t in templates if "{state_share_of_china}" in t]
    top = [t for t in templates if "{top_country_share}" in t]
    assert share and all("{state_origin_count} of {china_total_count}" in t for t in share)
    assert top and all("{top_country_share} of {top_country_world}" in t for t in top)
