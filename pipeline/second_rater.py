"""Second rater: re-judge articles the model has already labelled, with a second model.

This is NOT an agreement study. An agreement study compares the machine against a human
coder and measures whether the codebook was applied correctly. This compares one model
against another from the same family and measures only whether they apply the codebook
consistently. Two raters that share a training lineage can agree confidently and both be
wrong, and the errors most likely to be shared are the ones this instrument exists to
avoid: reading a quoted spokesperson as relay, or missing an unattributed official claim.
So a high figure here licenses "the two models are consistent" and nothing about accuracy.

The second rater sees exactly what the first saw: the same prompt, the same headline and
body, no outlet, no country, no first label.

Usage:
  python -m pipeline.second_rater sample --n 200 --model claude-sonnet-5 --out review/second_rater.json
  python -m pipeline.second_rater estimate --n 200        # cost only, no calls
  python -m pipeline.second_rater auto [--submit] [--force]
      collect a finished study batch, and with --submit send a new study through the Batches API
      when one is needed and the monthly budget can pay for it. Never raises: the daily update
      must not depend on it.
"""
import argparse
import calendar
import datetime as dt
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from pipeline import config, llm_cost, store
from pipeline.agreement import MIN_POSITIVE_FOR_KAPPA, cohens_kappa, kappa_shortfall

CATEGORIES = ["A", "B", "C", "not_relevant"]
DEFAULT_SECOND_MODEL = "claude-sonnet-5"


def judged_rows(conn, with_body: bool = False) -> List[Dict]:
    """Articles carrying a current model label. with_body keeps only those whose body is still on
    disk, which is what a study can actually re-judge."""
    rows = conn.execute(
        """SELECT a.id, a.title, a.url_hash, a.country, a.language, c.category, c.confidence,
                  c.evidence_quote, c.model_version
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE c.method='llm' ORDER BY a.id"""
    ).fetchall()
    out = [dict(r) for r in rows]
    if with_body:
        out = [r for r in out if store.body_path(r["url_hash"]).exists()]
    return out


def draw(rows: List[Dict], n: int, seed: str = "second-rater") -> List[Dict]:
    """Stratified by the first rater's category, so the relay versus independent boundary,
    where the disagreement lives, is not swamped by whatever category is most common."""
    rng = random.Random(seed)
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    for v in by_cat.values():
        rng.shuffle(v)
    cats = [c for c in CATEGORIES if by_cat.get(c)]
    if not cats:
        return []
    out, quota = [], max(1, n // len(cats))
    for c in cats:
        out.extend(by_cat[c][:quota])
    leftovers = [r for c in cats for r in by_cat[c][quota:]]
    rng.shuffle(leftovers)
    out.extend(leftovers[: max(0, n - len(out))])
    return out[:n]


def rejudge(conn, rows: List[Dict], model: str, client=None, dry_run: bool = False) -> List[Dict]:
    """Re-judge each row with the second model. Returns one pair per article."""
    from pipeline import classify_llm
    client = client or classify_llm.make_client(dry_run=dry_run)
    pairs, tokens_in, tokens_out, calls, failed = [], 0, 0, 0, 0
    for r in rows:
        body = store.load_body(r["url_hash"]) or ""
        if not body:
            continue
        params = dict(classify_llm.request_params(r["title"], body))
        params["model"] = model
        try:
            message = client.messages.create(**params)
        except Exception:
            failed += 1
            if failed >= 5 and not pairs:
                break       # the API is refusing; stop paying for nothing
            continue
        calls += 1
        tokens = llm_cost.usage_tokens(getattr(message, "usage", None))
        tokens_in += tokens["input_tokens"]
        tokens_out += tokens["output_tokens"]
        if not dry_run:
            # A study spends the same budget as classification, so it is booked in the same place,
            # and booked before the reply is inspected: an answer that does not parse is billed too.
            classify_llm.book_usage(conn, message, calls=1)
            conn.commit()
        data = classify_llm.parse_response(message)
        if not data or data.get("_error"):
            continue
        pairs.append(_pair(r, data, getattr(message, "model", None) or model))
    return pairs, {"input_tokens": tokens_in, "output_tokens": tokens_out, "calls": calls, "failed_calls": failed}


def _pair(row: Dict, data: Dict, second_model: str) -> Dict:
    return {
        "article_id": row["id"], "country": row["country"], "language": row["language"],
        "first": {"category": row["category"], "confidence": row["confidence"],
                  "model": row["model_version"], "evidence": row["evidence_quote"]},
        "second": {"category": data["category"], "confidence": data.get("confidence"),
                   "model": second_model, "evidence": data.get("evidence_quote")},
        "agree": data["category"] == row["category"],
    }


def pair_rows(pairs: List[Dict]) -> List[Dict]:
    """Pairs in the flat shape store.record_model_agreement stores."""
    return [{"article_id": p["article_id"],
             "category_a": p["first"]["category"], "confidence_a": p["first"].get("confidence"), "evidence_a": p["first"].get("evidence"),
             "category_b": p["second"]["category"], "confidence_b": p["second"].get("confidence"), "evidence_b": p["second"].get("evidence")}
            for p in pairs]


def summarise(pairs: List[Dict], first_model: str, second_model: str) -> Dict:
    """Consistency between the two raters. Reported as reliability, never as validity."""
    both = [(p["first"]["category"], p["second"]["category"]) for p in pairs]
    bc = [(a, b) for a, b in both if a in ("B", "C") or b in ("B", "C")]
    bc_binary = [("B" if a == "B" else "notB", "B" if b == "B" else "notB") for a, b in bc]
    disagreements = [p for p in pairs if not p["agree"]]
    per_language = {}
    by_lang = defaultdict(list)
    for p in pairs:
        if p["first"]["category"] in ("B", "C") or p["second"]["category"] in ("B", "C"):
            by_lang[p["language"]].append((p["first"]["category"], p["second"]["category"]))
    for lang, ps in by_lang.items():
        binary = [("B" if a == "B" else "notB", "B" if b == "B" else "notB") for a, b in ps]
        per_language[lang] = {"n": len(binary), "n_b": sum(1 for x, _ in binary if x == "B"),
                              "kappa": cohens_kappa(binary, min_positive=MIN_POSITIVE_FOR_KAPPA, positive="B")}
    same = first_model == second_model
    # A kappa on the relay versus independent distinction needs enough B on both sides. Without it
    # the study is inconclusive and says so, rather than publishing the 1.0 that a sample of all
    # notB used to produce.
    kappa_bc = cohens_kappa(bc_binary, min_positive=MIN_POSITIVE_FOR_KAPPA, positive="B")
    inconclusive = kappa_shortfall(bc_binary) if kappa_bc is None else None
    return {
        "method": "same_model_rerun" if same else "model_vs_model",
        "measures": ("the same model judging the same articles twice, not correctness; it detects randomness in "
                     "that model's own judgement and cannot detect a bias it holds consistently; no human coding has been done"
                     if same else
                     "consistency between two models, not correctness; no human coding has been done"),
        "first_model": first_model, "second_model": second_model,
        "n": len(pairs), "n_bc": len(bc), "n_b_first": sum(1 for a, _ in bc if a == "B"),
        "n_b_second": sum(1 for _, b in bc if b == "B"),
        "kappa_all": cohens_kappa(both), "kappa_bc": kappa_bc,
        "kappa_min_positive": MIN_POSITIVE_FOR_KAPPA, "kappa_bc_inconclusive": inconclusive,
        "raw_agreement": round(sum(1 for p in pairs if p["agree"]) / len(pairs), 4) if pairs else None,
        "per_language_bc": per_language,
        "first_distribution": dict(Counter(a for a, _ in both)),
        "second_distribution": dict(Counter(b for _, b in both)),
        "disagreements": disagreements,
        "pairs": pairs,
    }


def estimate_cost(conn, n: int) -> Dict:
    """Token estimate from what the first rater actually spent, so no figure is a guess."""
    row = conn.execute("SELECT SUM(calls) c, SUM(input_tokens) i, SUM(output_tokens) o FROM llm_usage").fetchone()
    calls, tin, tout = (row["c"] or 0), (row["i"] or 0), (row["o"] or 0)
    if not calls:
        return {"measured": False, "note": "no model calls recorded yet; run the first rater before estimating"}
    return {"measured": True, "sample": n, "per_call_input_tokens": round(tin / calls),
            "per_call_output_tokens": round(tout / calls),
            "estimated_input_tokens": round(tin / calls * n), "estimated_output_tokens": round(tout / calls * n),
            "note": "multiply by the second model's published per token prices; the first rater's own usage is the basis"}


def study_row(summary: Dict) -> Dict:
    """The summary in the shape store.record_model_agreement expects. Flat, and carrying the
    sentence about what it measures, so a row read years later cannot be mistaken for human coding."""
    return {"method": summary["method"], "model_a": summary["first_model"], "model_b": summary["second_model"],
            "sample_size": summary["n"], "kappa_all": summary["kappa_all"], "kappa_bc": summary["kappa_bc"],
            "n_bc": summary["n_bc"],
            # bc_by_language is the key export.build_meta reads for the per language veto.
            "details": {"measures": summary["measures"], "raw_agreement": summary["raw_agreement"],
                        "bc_by_language": summary["per_language_bc"], "pool_size": summary.get("pool_size"),
                        # Why a study reports no kappa, so a stored row cannot be read as a failed study.
                        "kappa_bc_inconclusive": summary.get("kappa_bc_inconclusive"),
                        "kappa_min_positive": summary.get("kappa_min_positive"),
                        "n_b_first": summary.get("n_b_first"), "n_b_second": summary.get("n_b_second"),
                        "first_distribution": summary["first_distribution"],
                        "second_distribution": summary["second_distribution"],
                        "disagreements": len(summary["disagreements"])}}


# ---------------------------------------------------------------------------
# The study that runs by itself
# ---------------------------------------------------------------------------

STUDY_PREFIX = "study-"


def _today(today: Optional[dt.date]) -> dt.date:
    return today or dt.datetime.now(dt.timezone.utc).date()


def study_needed(conn, pool: int) -> Optional[str]:
    """Why a study should run now, or None. Human coding that settled the question needs no model study."""
    human = conn.execute("SELECT kappa_bc FROM agreement_studies ORDER BY id DESC LIMIT 1").fetchone()
    if human and human["kappa_bc"] is not None and human["kappa_bc"] >= config.KAPPA_WARNING_THRESHOLD:
        return None
    last = store.latest_model_agreement(conn)
    if not last:
        return "no study yet"
    if last["model_a"] != config.LLM_MODEL:
        return "the judging model changed"
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last["computed_at"])
    if age.days >= config.RELIABILITY_REFRESH_DAYS:
        return "the last study is %d days old" % age.days
    try:
        before = (json.loads(last["details"] or "{}") or {}).get("pool_size")
    except ValueError:
        before = None
    if before and pool >= 2 * before:
        return "the labelled pool has doubled"
    return None


def study_state(conn, today: Optional[dt.date] = None) -> Dict:
    """Where the automatic study stands, for the site: waiting_for_budget, submitted, done or not_needed."""
    today = _today(today)
    out = {"auto": config.RELIABILITY_AUTO, "sample": config.RELIABILITY_SAMPLE, "earliest": None}
    if conn.execute("SELECT COUNT(*) FROM llm_batches WHERE kind='study' AND status='submitted'").fetchone()[0]:
        return dict(out, state="submitted")
    pool = len(judged_rows(conn, with_body=True))
    why = study_needed(conn, pool)
    if not why:
        return dict(out, state="done" if store.latest_model_agreement(conn) else "not_needed")
    out["reason"] = why
    plan = study_plan(conn, today, config.RELIABILITY_SAMPLE)
    ok = plan["n"] > 0
    out["blocked"] = plan["reason"]
    if ok:
        out["earliest"] = today.isoformat()
    else:
        # A day later this month may carry the study even when today cannot: the daily cap is what
        # is left spread over the days remaining, so it opens up as the month ends. Failing that,
        # the first of next month, when the budget starts again.
        out["earliest"] = _earliest_study_day(conn, today) or \
            (today.replace(day=1) + dt.timedelta(days=calendar.monthrange(today.year, today.month)[1])).isoformat()
    return dict(out, state="waiting_for_budget" if not ok else "due")


def _earliest_study_day(conn, today: dt.date) -> Optional[str]:
    """The first day left in this month whose allowance could carry a study, assuming no further
    spending. None when no day in the month can."""
    last = calendar.monthrange(today.year, today.month)[1]
    for day in range(today.day, last + 1):
        when = today.replace(day=day)
        if study_plan(conn, when, config.RELIABILITY_SAMPLE)["n"] > 0:
            return when.isoformat()
    return None


# A study never takes more than this share of the calls a day allows. The rule: the study competes
# with classification for one shared allowance, so it may reserve half of a day and no more. It used
# to check only the month's remaining dollars, so on 1 October, where the batched daily cap was 110
# calls, it submitted all 250 of RELIABILITY_SAMPLE, booked them into that day's usage and left the
# classification stage nothing at all.
RELIABILITY_DAY_SHARE = 0.5
# ... and it is not submitted at all below this many re-judgements, because collect_study discards a
# study with fewer than config.RELIABILITY_MIN_PAIRS usable results: a shorter sample would be paid
# for and thrown away. The headroom covers results that come back unusable. Below the floor the
# study waits for a day whose allowance can carry it, which is what the end of a month with budget
# left over looks like: days_left falls to one and the daily cap opens up.
RELIABILITY_SAMPLE_HEADROOM = 1.2


def _min_sample() -> int:
    return int(round(config.RELIABILITY_MIN_PAIRS * RELIABILITY_SAMPLE_HEADROOM))


def _calls_used_on(conn, day: dt.date) -> int:
    row = conn.execute("SELECT calls FROM llm_usage WHERE date=?", (day.isoformat(),)).fetchone()
    return (row["calls"] if row else 0) or 0


def _day_allowance(conn, today: dt.date):
    """Calls left today under the two controls classification obeys, lower binds: the daily call
    ceiling and the monthly budget's daily cap. Returns (allowance, cap)."""
    cap = llm_cost.daily_cap(conn, today, batched=True)
    ceiling = config.LLM_DAILY_CALL_CEILING
    if cap["cap"] is not None:
        ceiling = min(ceiling, cap["cap"])
    return max(0, ceiling - _calls_used_on(conn, today)), cap


def study_plan(conn, today: dt.date, wanted: int) -> Dict:
    """How many re-judgements a study may submit today, and why not when it may not.

    Three bounds, the lowest binds: the sample asked for, RELIABILITY_DAY_SHARE of the calls today
    still allows, and RELIABILITY_MAX_BUDGET_SHARE of the dollars left in the month. A figure below
    _min_sample() submits nothing."""
    allowance, cap = _day_allowance(conn, today)
    day_share = int(allowance * RELIABILITY_DAY_SHARE)
    n = min(wanted, day_share)
    per_call = llm_cost.per_call_estimate(True)
    if cap["cap"] is not None:
        affordable = int(config.RELIABILITY_MAX_BUDGET_SHARE * max(0.0, cap.get("left_usd", 0.0)) / per_call) if per_call else n
        n = min(n, affordable)
    floor = _min_sample()
    out = {"n": n if n >= floor else 0, "cap": cap, "day_allowance": allowance, "day_share": day_share,
           "min_sample": floor, "reason": None}
    if out["n"] == 0:
        if day_share < floor:
            out["reason"] = ("today allows %d model calls under the daily ceiling and the monthly budget cap, of which a "
                             "study may take %d; that is below the %d re-judgements a study needs, so it waits for a day "
                             "whose allowance can carry it" % (allowance, day_share, floor))
        else:
            out["reason"] = "the monthly budget cannot pay for a study of %d re-judgements yet" % floor
    return out


def _budget_allows(conn, today: dt.date, n: int):
    """Kept for callers that only want the yes or no. study_plan carries the size and the reason."""
    plan = study_plan(conn, today, n)
    return plan["n"] > 0, plan["cap"]


def _submitted_this_month(conn, today: dt.date) -> bool:
    month = today.strftime("%Y-%m")
    return bool(conn.execute("SELECT COUNT(*) FROM llm_batches WHERE kind='study' AND substr(submitted_at,1,7)=?", (month,)).fetchone()[0])


def submit_study(conn, client, rows: List[Dict], model: str, date: Optional[dt.date] = None) -> int:
    """Send the re-judgements through the Batches API. The second rater sees what the first saw."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    from pipeline import classify_llm
    requests, ids = [], []
    for r in rows:
        body = store.load_body(r["url_hash"]) or ""
        if not body:
            continue
        params = dict(classify_llm.request_params(r["title"], body))
        params["model"] = model
        requests.append(Request(custom_id=STUDY_PREFIX + str(r["id"]), params=MessageCreateParamsNonStreaming(**params)))
        ids.append(r["id"])
    if not requests:
        return 0
    batch = client.messages.batches.create(requests=requests)
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version, kind) VALUES (?,?,?,?,?,?)",
                 (batch.id, store.utcnow(), "submitted", json.dumps(ids), model, "study"))
    # The study takes today's share of the budget, so it is counted where the daily cap looks, and
    # on the same day study_plan read when it sized the sample.
    store.record_llm_usage(conn, len(ids), 0, 0, date=date.isoformat() if date else None)
    conn.commit()
    return len(ids)


def collect_study(conn, client) -> Optional[Dict]:
    """Record a finished study batch. Too few usable results record nothing: a study of a remnant
    would publish a kappa the sample cannot support."""
    from pipeline import classify_llm
    b = conn.execute("SELECT * FROM llm_batches WHERE kind='study' AND status='submitted' ORDER BY submitted_at LIMIT 1").fetchone()
    if not b:
        return None
    info = client.messages.batches.retrieve(b["batch_id"])
    if info.processing_status != "ended":
        return {"state": "running"}
    by_id = {r["id"]: r for r in judged_rows(conn)}
    day = (b["submitted_at"] or "")[:10] or None
    pairs = []
    for result in client.messages.batches.results(b["batch_id"]):
        if result.result.type != "succeeded":
            continue
        message = result.result.message
        classify_llm.book_usage(conn, message, batched=True, date=day)
        data = classify_llm.parse_response(message)
        row = by_id.get(int(str(result.custom_id)[len(STUDY_PREFIX):]))
        if row and data and not data.get("_error"):
            pairs.append(_pair(row, data, getattr(message, "model", None) or b["model_version"]))
    if len(pairs) < config.RELIABILITY_MIN_PAIRS:
        conn.execute("UPDATE llm_batches SET status='failed', collected_at=? WHERE batch_id=?", (store.utcnow(), b["batch_id"]))
        conn.commit()
        return {"state": "too_few_results", "pairs": len(pairs)}
    out = summarise(pairs, config.LLM_MODEL, b["model_version"])
    out["pool_size"] = len(judged_rows(conn, with_body=True))
    store.record_model_agreement(conn, study_row(out), pair_rows(out["pairs"]))
    conn.execute("UPDATE llm_batches SET status='collected', collected_at=? WHERE batch_id=?", (store.utcnow(), b["batch_id"]))
    conn.commit()
    path = config.EXPORT_DIR_AUDIT / "reliability" / ("%s.json" % (day or store.utcnow()[:10]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"state": "recorded", "pairs": len(pairs), "kappa_bc": out["kappa_bc"], "method": out["method"]}


def auto(conn, submit: bool = False, force: bool = False, today: Optional[dt.date] = None, client=None,
         model: str = DEFAULT_SECOND_MODEL) -> Dict:
    """Collect a finished study, and with submit send a new one when it is needed and affordable.
    Never raises: a failure here is logged and the run goes on."""
    log_id = store.start_stage(conn, "auto", "reliability_study")
    out = {"collected": None, "submitted": 0, "skipped": None}
    try:
        today = _today(today)
        outstanding = conn.execute("SELECT COUNT(*) FROM llm_batches WHERE kind='study' AND status='submitted'").fetchone()[0]
        if outstanding or (submit and (force or config.RELIABILITY_AUTO)):
            if client is None:
                from pipeline import classify_llm
                try:
                    client = classify_llm.make_client()
                except classify_llm.NoApiKey:
                    out["skipped"] = "no_api_key"
                    store.finish_stage(conn, log_id, True, out)
                    return out
        if outstanding:
            out["collected"] = collect_study(conn, client)
            if out["collected"] and out["collected"].get("state") == "running":
                out["skipped"] = "study batch still running"
                store.finish_stage(conn, log_id, True, out)
                return out
        if not submit:
            out["skipped"] = out["skipped"] or "collect only"
        elif not (force or config.RELIABILITY_AUTO):
            out["skipped"] = "automatic study is switched off"
        else:
            pool = judged_rows(conn, with_body=True)
            why = "forced" if force else study_needed(conn, len(pool))
            plan = study_plan(conn, today, config.RELIABILITY_SAMPLE)
            out["budget"] = plan["cap"]
            out["plan"] = {k: plan[k] for k in ("n", "day_allowance", "day_share", "min_sample", "reason")}
            if not why:
                out["skipped"] = "no study needed"
            elif len(pool) < config.RELIABILITY_MIN_POOL:
                out["skipped"] = "only %d labelled articles with a body on disk" % len(pool)
            elif _submitted_this_month(conn, today) and not force:
                out["skipped"] = "a study was already submitted this month"
            elif plan["n"] <= 0:
                out["skipped"] = plan["reason"]
            else:
                out["reason"] = why
                # Never more than the day's share, so the classification stage still has calls today.
                out["submitted"] = submit_study(conn, client, draw(pool, plan["n"]), model, date=today)
        store.finish_stage(conn, log_id, True, out)
    except Exception as exc:       # the daily update must never depend on the study
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        try:
            store.finish_stage(conn, log_id, False, out, notes=out["error"])
        except Exception:
            pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sample", "estimate", "auto"])
    ap.add_argument("--submit", action="store_true", help="auto: submit a study when one is needed and affordable")
    ap.add_argument("--force", action="store_true", help="auto: submit even when no study is due")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--model", default=DEFAULT_SECOND_MODEL)
    ap.add_argument("--out", default="review/second_rater.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--record", action="store_true", help="store the study and its pairs in the database")
    args = ap.parse_args(argv)
    conn = store.connect()
    if args.cmd == "estimate":
        print(json.dumps(estimate_cost(conn, args.n), indent=1))
        return
    if args.cmd == "auto":
        print(json.dumps(auto(conn, submit=args.submit, force=args.force, model=args.model), indent=1, default=str))
        return
    rows = draw(judged_rows(conn, with_body=True), args.n)
    pairs, usage = rejudge(conn, rows, args.model, dry_run=args.dry_run)
    out = summarise(pairs, config.LLM_MODEL, args.model)
    out["usage"] = usage
    out["pool_size"] = len(judged_rows(conn, with_body=True))
    if args.record:
        store.record_model_agreement(conn, study_row(out), pair_rows(out["pairs"]))
        conn.commit()
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("method", "n", "n_bc", "kappa_all", "kappa_bc", "raw_agreement")}, indent=1))
    print("pairs written to", path)


if __name__ == "__main__":
    main()
