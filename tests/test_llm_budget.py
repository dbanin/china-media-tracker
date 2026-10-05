"""The monthly budget: cost estimates from token usage, the daily cap it implies, and the model
stage stopping when the budget is spent."""
import datetime as dt
import json
import sqlite3

from pipeline import classify_llm as cl, config, llm_cost, store


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    return conn


def test_call_cost_uses_published_prices_and_batch_discount(monkeypatch):
    monkeypatch.setattr(config, "LLM_PRICES_PER_MTOK", {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30})
    monkeypatch.setattr(config, "LLM_BATCH_DISCOUNT", 0.5)
    assert llm_cost.call_cost(1_000_000, 0) == 3.0
    assert llm_cost.call_cost(0, 1_000_000) == 15.0
    assert llm_cost.call_cost(0, 0, cache_read_tokens=1_000_000) == 0.30
    assert llm_cost.call_cost(1_000_000, 0, batched=True) == 1.5


def test_usage_tokens_reads_cache_fields():
    class Usage:
        input_tokens = 10
        output_tokens = 20
        cache_creation_input_tokens = 30
        cache_read_input_tokens = 40
    t = llm_cost.usage_tokens(Usage())
    assert t == {"input_tokens": 10, "output_tokens": 20, "cache_creation_tokens": 30, "cache_read_tokens": 40}
    assert llm_cost.usage_tokens(None) == {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0}


def test_daily_cap_spreads_what_is_left_over_the_days_remaining(monkeypatch):
    monkeypatch.setattr(config, "LLM_MONTHLY_BUDGET_USD", 30.0)
    monkeypatch.setattr(config, "LLM_COST_PER_CALL_UNBATCHED", 0.02)
    monkeypatch.setattr(config, "LLM_BATCH_DISCOUNT", 0.5)
    conn = _db()
    today = dt.date(2026, 9, 16)      # 15 days left including today
    cap = llm_cost.daily_cap(conn, today)
    assert cap["days_left"] == 15 and cap["cap"] == 100          # 30 / 15 / 0.02
    assert llm_cost.daily_cap(conn, today, batched=True)["cap"] == 200
    # Spending earlier in the month shrinks the cap; spending today does not, so every run in a day
    # sees the same figure.
    store.record_llm_usage(conn, 100, 0, 0, cost_usd=15.0, date="2026-09-10")
    assert llm_cost.daily_cap(conn, today)["cap"] == 50
    store.record_llm_usage(conn, 10, 0, 0, cost_usd=5.0, date="2026-09-16")
    assert llm_cost.daily_cap(conn, today)["cap"] == 50
    # Requests submitted to the Batches API but not collected are priced at the batch estimate.
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version) VALUES (?,?,?,?,?)",
                 ("b1", "2026-09-12T01:00:00+00:00", "submitted", json.dumps(list(range(500))), "m"))
    assert llm_cost.daily_cap(conn, today)["cap"] == 33                # (30 - 15 - 5) / 15 / 0.02
    # Spent past the budget, the cap is zero; a new month starts again.
    store.record_llm_usage(conn, 100, 0, 0, cost_usd=20.0, date="2026-09-11")
    assert llm_cost.daily_cap(conn, today)["cap"] == 0
    assert llm_cost.daily_cap(conn, dt.date(2026, 10, 1))["cap"] == 48       # 30 / 31 / 0.02
    # No budget at all: no cap.
    monkeypatch.setattr(config, "LLM_MONTHLY_BUDGET_USD", None)
    assert llm_cost.daily_cap(conn, today)["cap"] is None


def test_month_to_date_counts_today_and_outstanding_requests(monkeypatch):
    monkeypatch.setattr(config, "LLM_COST_PER_CALL_UNBATCHED", 0.02)
    monkeypatch.setattr(config, "LLM_BATCH_DISCOUNT", 0.5)
    conn = _db()
    store.record_llm_usage(conn, 5, 0, 0, cost_usd=1.0, date="2026-09-16")
    store.record_llm_usage(conn, 5, 0, 0, cost_usd=9.0, date="2026-08-31")
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version) VALUES (?,?,?,?,?)",
                 ("b1", "2026-09-16T01:00:00+00:00", "submitted", json.dumps([1, 2, 3, 4]), "m"))
    m = llm_cost.month_to_date(conn, dt.date(2026, 9, 16))
    assert m == {"month": "2026-09", "recorded_usd": 1.0, "outstanding_requests": 4, "estimated_usd": 1.04}


def test_stage_makes_no_calls_once_the_budget_is_spent(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(config, "LLM_MONTHLY_BUDGET_USD", 30.0)
    monkeypatch.setattr(config, "LLM_DAILY_CALL_CEILING", 4000)
    import pytest
    from tests.test_classify_llm import _seed
    conn = _db()
    today = dt.datetime.now(dt.timezone.utc).date()
    if today.day == 1:
        pytest.skip("the cap counts days before today in the month; there are none on the first")
    store.record_llm_usage(conn, 3000, 0, 0, cost_usd=36.0, date=today.replace(day=1).isoformat())
    _seed(conn, 3)
    for i in range(3):
        store.save_body(store.url_hash("https://e.com/%d" % i), "Body %d" % i)
    counts = cl.run(conn, "t", client=cl.DryRunClient())
    assert counts["calls"] == 0 and counts["budget"]["cap"] == 0 and counts.get("budget_bound") is True
    assert counts["ceiling_hit"] is True
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE status='awaiting_llm'").fetchone()[0] == 3
    assert "budget cap" in conn.execute("SELECT notes FROM run_log WHERE stage='classify_llm'").fetchone()[0]


def test_sync_call_records_cost_and_cache_tokens(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(config, "LLM_MONTHLY_BUDGET_USD", 1000.0)
    from tests.test_classify_llm import _seed
    conn = _db()
    ids = _seed(conn, 1)
    store.save_body(store.url_hash("https://e.com/0"), "Body about China")
    cl.run(conn, "t", client=cl.DryRunClient())
    row = conn.execute("SELECT * FROM llm_usage").fetchone()
    assert row["calls"] == 1 and row["input_tokens"] == 100 and row["output_tokens"] == 50
    assert abs(row["cost_usd"] - llm_cost.call_cost(100, 50)) < 1e-9


def test_migration_prices_days_recorded_before_cost_tracking():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    conn.executescript("""
        DROP TABLE llm_usage;
        CREATE TABLE llm_usage (date TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
            ceiling_hit INTEGER NOT NULL DEFAULT 0);
        INSERT INTO llm_usage VALUES ('2026-09-15', 1000, 1, 1, 0);
        INSERT INTO llm_usage VALUES ('2026-09-16', 300, 1, 1, 0);
    """)
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version) VALUES (?,?,?,?,?)",
                 ("b1", "2026-09-16T01:00:00+00:00", "submitted", json.dumps(list(range(100))), "m"))
    store._migrate(conn)
    rows = {r["date"]: r["cost_usd"] for r in conn.execute("SELECT date, cost_usd FROM llm_usage")}
    assert abs(rows["2026-09-15"] - 1000 * config.LLM_MEASURED_COST_PER_CALL) < 1e-9
    assert abs(rows["2026-09-16"] - 200 * config.LLM_MEASURED_COST_PER_CALL) < 1e-9


# The API budget is for what the session route cannot reach: articles listed in
# data/labels/api_first.jsonl first, then articles older than the session grace period; younger
# articles are held for the session.

def _waiting(conn, n, days_old, country="ITA"):
    url = "https://e.com/pool-%d" % n
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": country, "language": "it",
                                         "title": "Title %d" % n, "status": "awaiting_llm", "gate_relevant": 1})
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_old)).replace(microsecond=0).isoformat()
    conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (when, aid))
    conn.commit()
    store.save_body(store.url_hash(url), "Body %d about China" % n)
    return aid, store.url_hash(url)


def _api_first(directory, hashes):
    directory.mkdir(exist_ok=True)
    (directory / "api_first.jsonl").write_text(
        "".join(json.dumps({"url_hash": h, "reason": "HTTP 403", "listed_at": "2026-10-04", "source": "session"}) + "\n"
                for h in hashes), encoding="utf-8")


def _pools_env(monkeypatch, tmp_path, ceiling, grace=3):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "LABELS_DIR", tmp_path / "labels")
    monkeypatch.setattr(config, "LLM_MONTHLY_BUDGET_USD", 100000.0)
    monkeypatch.setattr(config, "LLM_DAILY_CALL_CEILING", ceiling)
    monkeypatch.setattr(config, "LLM_SESSION_GRACE_DAYS", grace)


def _status(conn, aid):
    return conn.execute("SELECT status FROM articles WHERE id=?", (aid,)).fetchone()[0]


def test_api_first_article_is_sent_before_an_older_grace_article(monkeypatch, tmp_path):
    _pools_env(monkeypatch, tmp_path, ceiling=1)
    conn = _db()
    older, _ = _waiting(conn, 1, days_old=10)
    listed, h = _waiting(conn, 2, days_old=4)
    _api_first(tmp_path / "labels", [h])
    counts = cl.run(conn, "t", client=cl.DryRunClient())
    assert counts["calls"] == 1 and counts["ceiling_hit"] is True
    assert _status(conn, listed) == "classified" and _status(conn, older) == "awaiting_llm"
    assert (counts["api_first_eligible"], counts["api_first_sent"]) == (1, 1)
    assert (counts["grace_eligible"], counts["grace_sent"]) == (1, 0)
    assert counts["held_for_session"] == 0 and counts["draw"]["eligible"] == 2
    logged = json.loads(conn.execute("SELECT counts FROM run_log WHERE stage='classify_llm'").fetchone()[0])
    assert logged["api_first_sent"] == 1 and logged["grace_eligible"] == 1


def test_listed_article_is_not_held_however_young(monkeypatch, tmp_path):
    _pools_env(monkeypatch, tmp_path, ceiling=10)
    conn = _db()
    listed, h = _waiting(conn, 1, days_old=0)
    _api_first(tmp_path / "labels", [h])
    counts = cl.run(conn, "t", client=cl.DryRunClient())
    assert _status(conn, listed) == "classified" and counts["held_for_session"] == 0


def test_article_younger_than_grace_is_held_for_the_session(monkeypatch, tmp_path):
    _pools_env(monkeypatch, tmp_path, ceiling=10)
    conn = _db()
    young, _ = _waiting(conn, 1, days_old=1)
    old, _ = _waiting(conn, 2, days_old=10)
    client = cl.DryRunClient()
    counts = cl.run(conn, "t", client=client)
    assert counts["held_for_session"] == 1 and counts["grace_eligible"] == 1 and counts["api_first_eligible"] == 0
    assert counts["calls"] == 1 and counts["grace_sent"] == 1 and len(client.calls) == 1
    assert _status(conn, young) == "awaiting_llm" and _status(conn, old) == "classified"
    # Held articles are not truncated by the ceiling, so holding them is not a ceiling event.
    assert counts["ceiling_hit"] is False
    # Turning the hold off sends it.
    monkeypatch.setattr(config, "LLM_SESSION_GRACE_DAYS", 0)
    counts = cl.run(conn, "t2", client=client)
    assert counts["held_for_session"] == 0 and _status(conn, young) == "classified"


def test_no_list_and_all_articles_past_grace_behaves_as_before(monkeypatch, tmp_path):
    _pools_env(monkeypatch, tmp_path, ceiling=3)
    conn = _db()
    for n in range(6):
        _waiting(conn, n, days_old=10, country="ITA" if n % 2 else "FRA")
    assert not (tmp_path / "labels" / "api_first.jsonl").exists()
    # What the stage drew before the pools existed: one stratified draw over every waiting article.
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    expected, allocation = cl.draw(cl.pending_articles(conn, 100000), 3, "llm-draw-%s" % today)
    counts = cl.run(conn, "t", client=cl.DryRunClient())
    classified = {r[0] for r in conn.execute("SELECT id FROM articles WHERE status='classified'")}
    assert classified == {r["id"] for r in expected}
    assert counts["calls"] == 3 and counts["ceiling_hit"] is True
    assert counts["draw"] == {"eligible": 6, "drawn": 3, "countries": 2}
    assert (counts["api_first_eligible"], counts["grace_eligible"], counts["held_for_session"]) == (0, 6, 0)
    assert counts["grace_sent"] == 3 and counts["api_first_sent"] == 0
    sampled = {r["country"]: [r["eligible"], r["drawn"]] for r in conn.execute("SELECT * FROM llm_sampling")}
    assert sampled == allocation


def test_batch_submission_counts_each_pool(monkeypatch, tmp_path):
    _pools_env(monkeypatch, tmp_path, ceiling=2)
    from tests.test_classify_llm import _RecordingBatchApi
    conn = _db()
    _waiting(conn, 1, days_old=10)
    _waiting(conn, 2, days_old=10)
    listed, h = _waiting(conn, 3, days_old=5)
    _waiting(conn, 4, days_old=1)
    _api_first(tmp_path / "labels", [h])
    recorder = _RecordingBatchApi()
    client = type("C", (), {"messages": type("M", (), {"batches": recorder})()})()
    counts = cl.run(conn, "t", client=client, batch=True)
    submitted = {int(req["custom_id"]) for req in recorder.created[0]}
    assert len(submitted) == 2 and listed in submitted
    assert (counts["api_first_sent"], counts["grace_sent"], counts["held_for_session"]) == (1, 1, 1)
    assert counts["batch_submitted"] == 2 and counts["ceiling_hit"] is True
