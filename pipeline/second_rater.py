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
  python -m pipeline.second_rater sample --n 200 --model claude-opus-5 --out review/second_rater.json
  python -m pipeline.second_rater estimate --n 200        # cost only, no calls
"""
import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from pipeline import config, store
from pipeline.agreement import cohens_kappa

CATEGORIES = ["A", "B", "C", "not_relevant"]
DEFAULT_SECOND_MODEL = "claude-opus-5"


def judged_rows(conn) -> List[Dict]:
    """Articles carrying a current model label, with the body still on disk."""
    rows = conn.execute(
        """SELECT a.id, a.title, a.url_hash, a.country, a.language, c.category, c.confidence,
                  c.evidence_quote, c.model_version
           FROM articles a JOIN classifications c ON c.article_id=a.id AND c.is_current=1
           WHERE c.method='llm' ORDER BY a.id"""
    ).fetchall()
    return [dict(r) for r in rows]


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
    pairs, tokens_in, tokens_out = [], 0, 0
    for r in rows:
        body = store.load_body(r["url_hash"]) or ""
        if not body:
            continue
        params = dict(classify_llm.request_params(r["title"], body))
        params["model"] = model
        message = client.messages.create(**params)
        data = classify_llm.parse_response(message)
        usage = getattr(message, "usage", None)
        tokens_in += getattr(usage, "input_tokens", 0) or 0
        tokens_out += getattr(usage, "output_tokens", 0) or 0
        if not data or data.get("_error"):
            continue
        pairs.append({
            "article_id": r["id"], "country": r["country"], "language": r["language"],
            "first": {"category": r["category"], "confidence": r["confidence"],
                      "model": r["model_version"], "evidence": r["evidence_quote"]},
            "second": {"category": data["category"], "confidence": data.get("confidence"),
                       "model": getattr(message, "model", None) or model, "evidence": data.get("evidence_quote")},
            "agree": data["category"] == r["category"],
        })
    return pairs, {"input_tokens": tokens_in, "output_tokens": tokens_out, "calls": len(pairs)}


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
        per_language[lang] = {"n": len(binary), "kappa": cohens_kappa(binary)}
    return {
        "method": "model_vs_model",
        "measures": "consistency between two models, not correctness; no human coding has been done",
        "first_model": first_model, "second_model": second_model,
        "n": len(pairs), "n_bc": len(bc),
        "kappa_all": cohens_kappa(both), "kappa_bc": cohens_kappa(bc_binary),
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sample", "estimate"])
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--model", default=DEFAULT_SECOND_MODEL)
    ap.add_argument("--out", default="review/second_rater.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    conn = store.connect()
    if args.cmd == "estimate":
        print(json.dumps(estimate_cost(conn, args.n), indent=1))
        return
    rows = draw(judged_rows(conn), args.n)
    pairs, usage = rejudge(conn, rows, args.model, dry_run=args.dry_run)
    out = summarise(pairs, config.LLM_MODEL, args.model)
    out["usage"] = usage
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("method", "n", "n_bc", "kappa_all", "kappa_bc", "raw_agreement")}, indent=1))
    print("pairs written to", path)


if __name__ == "__main__":
    main()
