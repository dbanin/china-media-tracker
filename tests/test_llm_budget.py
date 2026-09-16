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
