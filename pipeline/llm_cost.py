"""What the model stage costs, and the daily call cap that keeps a calendar month inside the budget.

Two controls bound model spending. The daily call ceiling (config.LLM_DAILY_CALL_CEILING) is a
count. The monthly budget (config.LLM_MONTHLY_BUDGET_USD) is a sum of dollars, and this module turns
it into a second daily cap: the budget left at the start of today, spread evenly over the days left
in the month, divided by the price of one call. The cap is computed from days before today only, so
it is the same for every run within a day; otherwise the hourly runs would each spend a full day's
allowance. The lower of the two caps binds.

Dollars are estimated from recorded token usage at the published per token prices. Rows written
before cost tracking existed carry no token breakdown for cached prompt tokens, and are priced at
the measured average per call from the first month's bill."""
import calendar
import datetime as dt
from typing import Dict, Optional

from pipeline import config


def call_cost(input_tokens: int, output_tokens: int, cache_creation_tokens: int = 0,
              cache_read_tokens: int = 0, batched: bool = False) -> float:
    """Dollars for one call at the published per token prices, halved for the Batches API."""
    p = config.LLM_PRICES_PER_MTOK
    usd = (input_tokens * p["input"] + output_tokens * p["output"]
           + cache_creation_tokens * p["cache_write"] + cache_read_tokens * p["cache_read"]) / 1e6
    return usd * (config.LLM_BATCH_DISCOUNT if batched else 1.0)


def usage_tokens(usage) -> Dict[str, int]:
    """The four token counts from an SDK usage object, zero where absent."""
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }


def usage_cost(usage, batched: bool = False) -> float:
    t = usage_tokens(usage)
    return call_cost(t["input_tokens"], t["output_tokens"], t["cache_creation_tokens"], t["cache_read_tokens"], batched)


def spent(conn, since: str, before: str) -> float:
    """Estimated dollars recorded on days with since <= date < before."""
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE date>=? AND date<?", (since, before)).fetchone()
    return float(row[0] or 0.0)


def outstanding(conn, since: str, before: str) -> int:
    """Requests submitted to the Batches API on those days whose results have not been collected, so
    whose cost is not recorded yet."""
    import json
    n = 0
    for r in conn.execute("SELECT submitted_at, article_ids FROM llm_batches WHERE status='submitted'"):
        day = (r["submitted_at"] or "")[:10]
        if since <= day < before:
            n += len(json.loads(r["article_ids"] or "[]"))
    return n


def measured_per_call(conn, today: Optional[dt.date] = None) -> Optional[float]:
    """Average recorded cost of a call over the current and previous calendar month, or None when
    fewer than config.LLM_MEASURED_MIN_CALLS calls carry a recorded cost. Rows priced by migration
    (cost_usd set, no token breakdown) count too: they are the first month's measured average."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    first = today.replace(day=1)
    prev = (first - dt.timedelta(days=1)).replace(day=1)
    row = conn.execute("SELECT COALESCE(SUM(calls),0), COALESCE(SUM(cost_usd),0) FROM llm_usage "
                       "WHERE date>=? AND date<? AND cost_usd>0",
                       (prev.isoformat(), (today + dt.timedelta(days=1)).isoformat())).fetchone()
    calls, cost = int(row[0] or 0), float(row[1] or 0.0)
    if calls < config.LLM_MEASURED_MIN_CALLS or cost <= 0:
        return None
    return cost / calls


def per_call_estimate(batched: bool, conn=None, today: Optional[dt.date] = None) -> float:
    """Dollars one call is expected to cost: the measured average when the database has enough
    recorded calls, otherwise the full price constant; halved for the Batches API."""
    base = measured_per_call(conn, today) if conn is not None else None
    if base is None:
        base = config.LLM_COST_PER_CALL_UNBATCHED
    return base * (config.LLM_BATCH_DISCOUNT if batched else 1.0)


def month_to_date(conn, today: Optional[dt.date] = None) -> Dict:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    start = today.replace(day=1).isoformat()
    end = (today + dt.timedelta(days=1)).isoformat()
    recorded = spent(conn, start, end)
    pending = outstanding(conn, start, end)
    return {"month": today.strftime("%Y-%m"), "recorded_usd": round(recorded, 2),
            "outstanding_requests": pending,
            "estimated_usd": round(recorded + pending * per_call_estimate(True, conn, today), 2)}


def daily_cap(conn, today: Optional[dt.date] = None, batched: bool = False) -> Dict:
    """Calls allowed today under the monthly budget. cap is None when no budget is set."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    budget = config.LLM_MONTHLY_BUDGET_USD
    out = {"budget_usd": budget, "month": today.strftime("%Y-%m"), "cap": None}
    if budget is None:
        return out
    start = today.replace(day=1).isoformat()
    before = spent(conn, start, today.isoformat()) + outstanding(conn, start, today.isoformat()) * per_call_estimate(True, conn, today)
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
    left = budget - before
    per_call = per_call_estimate(batched, conn, today)
    out.update({"spent_before_today_usd": round(before, 2), "left_usd": round(left, 2), "days_left": days_left,
                "per_call_usd": round(per_call, 5), "per_call_measured": measured_per_call(conn, today) is not None,
                "cap": int(max(0.0, left) / days_left / per_call)})
    return out
