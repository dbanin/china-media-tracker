"""What the model stage costs, and the daily call cap that keeps a calendar month inside the budget.

Two controls bound model spending. The daily call ceiling (config.LLM_DAILY_CALL_CEILING) is a
count. The monthly budget (config.LLM_MONTHLY_BUDGET_USD) is a sum of dollars, and this module turns
it into a second daily cap: the budget left at the start of today, spread evenly over the days left
in the month, divided by the price of one call. The cap is computed from days before today only, so
it is the same for every run within a day; otherwise the hourly runs would each spend a full day's
allowance. The lower of the two caps binds.

Dollars are estimated from recorded token usage at the published per token prices, halved for the
Batches API. The price of one call is measured from recorded tokens at the full price and
discounted once for batched calls (measured_per_call, per_call_estimate). Rows written before cost
tracking existed carry no token breakdown for cached prompt tokens, and are priced at the measured
average per call from the first month's bill."""
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


def _days_with_tokens_missing(conn) -> set:
    """Days whose usage row carries calls that have no tokens behind them yet, or ever. A batch is
    booked in two halves: at submission, its request count is added to that day's calls with no
    tokens; at collection, its tokens and cost are added to the same day with no calls. While a
    batch is still 'submitted' its day shows the calls but not the tokens, and the tokens of a
    batch marked 'failed' (expired uncollected) never arrive. Either way the day's tokens per call
    read low, so the day is kept out of the measurement."""
    return {(r["submitted_at"] or "")[:10]
            for r in conn.execute("SELECT submitted_at FROM llm_batches WHERE status IN ('submitted', 'failed')")}


def measured_per_call(conn, today: Optional[dt.date] = None) -> Optional[float]:
    """The full price of one call, measured from the tokens recorded in the current and previous
    calendar month: call_cost over the summed token counts at the published prices, unbatched,
    divided by the calls. None when fewer than config.LLM_MEASURED_MIN_CALLS calls qualify.

    The rate is measured from tokens, not from cost_usd, because cost_usd is booked at the batch
    price for batched calls; averaging it would give a half price rate that per_call_estimate then
    discounts again. Only rows that carry the full token breakdown count: input and output tokens
    and the cached prompt tokens (cache_creation_tokens + cache_read_tokens > 0, which every call
    of the cached classifier prompt produces). Rows priced by the migration, written before cost
    tracking existed, carry no cached prompt tokens (the columns hold the migration default of 0),
    so pricing their tokens would understate the full rate; they are ignored, as are rows with no
    calls.

    Today is left out, as are days with a batch still 'submitted' or marked 'failed': a batch's
    calls are booked on the submission day when it is submitted, and its tokens and cost only when
    it is collected (classify_llm._collect_batches, second_rater), so a half collected day would
    drag the rate down. Leaving today out also keeps the cap the same for every run within a day."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    first = today.replace(day=1)
    prev = (first - dt.timedelta(days=1)).replace(day=1)
    skip = _days_with_tokens_missing(conn)
    calls = inp = outp = cw = cr = 0
    for r in conn.execute("SELECT date, calls, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens "
                          "FROM llm_usage WHERE date>=? AND date<? AND calls>0",
                          (prev.isoformat(), today.isoformat())):
        if r["date"] in skip:
            continue
        if (r["input_tokens"] or 0) + (r["output_tokens"] or 0) <= 0:
            continue
        if (r["cache_creation_tokens"] or 0) + (r["cache_read_tokens"] or 0) <= 0:
            continue
        calls += int(r["calls"] or 0)
        inp += int(r["input_tokens"] or 0)
        outp += int(r["output_tokens"] or 0)
        cw += int(r["cache_creation_tokens"] or 0)
        cr += int(r["cache_read_tokens"] or 0)
    if calls < config.LLM_MEASURED_MIN_CALLS:
        return None
    full = call_cost(inp, outp, cw, cr, batched=False)
    if full <= 0:
        return None
    return full / calls


def per_call_full_price(conn=None, today: Optional[dt.date] = None) -> float:
    """Dollars one synchronous call is expected to cost: the measured full price rate when the
    database has enough recorded calls, otherwise config.LLM_COST_PER_CALL_UNBATCHED."""
    base = measured_per_call(conn, today) if conn is not None else None
    return base if base is not None else config.LLM_COST_PER_CALL_UNBATCHED


def per_call_estimate(batched: bool, conn=None, today: Optional[dt.date] = None) -> float:
    """Dollars one call is expected to cost: the full price (measured or constant), discounted
    exactly once for the Batches API."""
    return per_call_full_price(conn, today) * (config.LLM_BATCH_DISCOUNT if batched else 1.0)


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
    measured = measured_per_call(conn, today)
    full = measured if measured is not None else config.LLM_COST_PER_CALL_UNBATCHED
    per_call = full * (config.LLM_BATCH_DISCOUNT if batched else 1.0)
    out.update({"spent_before_today_usd": round(before, 2), "left_usd": round(left, 2), "days_left": days_left,
                "per_call_usd": round(per_call, 5), "per_call_full_usd": round(full, 5),
                "per_call_measured": measured is not None,
                "cap": int(max(0.0, left) / days_left / per_call)})
    return out
