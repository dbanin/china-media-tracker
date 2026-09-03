"""Deterministic Category A detection and LLM routing.

Runs before any LLM call. Transparent and reproducible: every match records
which pattern fired, so any Category A count can be broken down by mechanism.

Decision rule:
  - any strong signature, or weak signatures from two different groups
      -> Category A, confidence 1.0, method rules, no LLM call
  - one weak signature
      -> routed to the LLM as an A candidate
  - no A signature but an official sourcing trigger
      -> routed to the LLM for the B versus C judgement
  - no A signature and no trigger
      -> Category C by rules if the body substantively concerns China
         (at least BODY_RELEVANCE_MIN_HITS gate terms in the body),
         otherwise not_relevant by rules. Both carry confidence below 1.0
         and are sampled by the agreement study like every other label.
"""
import json
import re
import time
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import yaml

from pipeline import config, extract, gate, store

BODY_RELEVANCE_MIN_HITS = 3
HEAD_CHARS = 400
TAIL_CHARS = 600


@lru_cache(maxsize=None)
def load_signatures(path=str(config.SIGNATURES_PATH)) -> Dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    compiled = {"ruleset_version": data["ruleset_version"], "exclusions": [], "groups": {}, "llm_triggers": []}
    for ex in data.get("exclusions", []):
        compiled["exclusions"].append((ex["id"], re.compile(ex["regex"], re.MULTILINE | re.UNICODE)))
    for gname, g in data["groups"].items():
        pats = []
        for p in g["patterns"]:
            pats.append({
                "id": p["id"], "group": gname, "strength": p["strength"],
                "scope": p.get("scope", "body"),
                "re": re.compile(p["regex"], re.MULTILINE | re.UNICODE),
            })
        compiled["groups"][gname] = pats
    for t in data.get("llm_triggers", []):
        compiled["llm_triggers"].append((t["id"], re.compile(t["regex"], re.MULTILINE | re.UNICODE)))
    return compiled


@lru_cache(maxsize=None)
def load_diplomats(path=str(config.DIPLOMATS_PATH)) -> List[str]:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return sorted({p["name"] for p in data.get("personnel", [])})


_TITLE_RE = re.compile(
    r"(?i)\b(ambassador|ambassadeur|ambasciatore|ambasciatrice|embajador|embajadora|embaixador|botschafter|"
    r"consul[- ]general|console generale|consul général|cónsul general|chargé d.affaires|incaricato d.affari|"
    r"spokesperson|spokesman|spokeswoman|portavoce|porte-parole|portavoz|foreign minister|vice foreign minister|"
    r"ministro degli esteri|ministre des affaires étrangères)\b"
)


def apply_exclusions(text: str, sigs: Dict) -> Tuple[str, List[str]]:
    fired = []
    for ex_id, rx in sigs["exclusions"]:
        text, n = rx.subn(" ", text)
        if n:
            fired.append(ex_id)
    return text, fired


def match_signatures(title: str, body: str, author: Optional[str], sigs: Optional[Dict] = None,
                     labels: str = "") -> Dict:
    """Return dict with matches (list of {id, group, strength, scope, span}), exclusions, and decision."""
    sigs = sigs or load_signatures()
    body = body or ""
    author = author or ""
    title = title or ""
    body_clean, excl = apply_exclusions(body, sigs)
    scopes = {
        "author": author,
        "head": body_clean[:HEAD_CHARS],
        "tail": body_clean[-TAIL_CHARS:],
        "body": body_clean,
        "labels": labels or "",
        "labels_head_tail": "\n".join([labels or "", body_clean[:HEAD_CHARS], body_clean[-TAIL_CHARS:]]),
        "any": "\n".join([author, title, body_clean]),
        "any_with_labels": "\n".join([author, title, body_clean, labels or ""]),
    }
    matches = []
    for gname, pats in sigs["groups"].items():
        for p in pats:
            text = scopes.get(p["scope"], body_clean)
            if not text:
                continue
            m = p["re"].search(text)
            if m:
                matches.append({"id": p["id"], "group": gname, "strength": p["strength"],
                                "scope": p["scope"], "span": text[max(0, m.start() - 60):m.end() + 60].strip()})

    # Diplomat list check. Name in the author field plus a diplomatic title anywhere in the
    # author field, head, tail or page labels is strong. Name alone is weak, because a
    # reporter can share a name with a diplomat, so it goes to the LLM as an A candidate.
    for name in load_diplomats():
        if re.search(r"\b%s\b" % re.escape(name), author, re.IGNORECASE):
            around = "\n".join([author, scopes["head"], scopes["tail"], labels or ""])
            if _TITLE_RE.search(around):
                matches.append({"id": "diplomat_list_author", "group": "authored_by_state",
                                "strength": "strong", "scope": "author", "span": author[:120]})
            else:
                matches.append({"id": "diplomat_list_name_only", "group": "authored_by_state",
                                "strength": "weak", "scope": "author", "span": author[:120]})
            break

    strong = [m for m in matches if m["strength"] == "strong"]
    weak_groups = {m["group"] for m in matches if m["strength"] == "weak"}  # hints never count
    if strong or len(weak_groups) >= 2:
        decision = "A"
    elif matches:
        decision = "A_candidate"
    else:
        decision = "none"

    triggers = []
    trig_text = "\n".join([title, body_clean])
    for t_id, rx in sigs["llm_triggers"]:
        m = rx.search(trig_text)
        if m:
            triggers.append({"id": t_id, "span": trig_text[max(0, m.start() - 60):m.end() + 60].strip()})

    return {"matches": matches, "exclusions": excl, "decision": decision, "triggers": triggers,
            "ruleset_version": sigs["ruleset_version"]}


def body_relevance_hits(title: str, body: str, language: str) -> int:
    """How many distinct gate terms appear in the body. Used only for the residual C / not_relevant call."""
    _, terms = gate.check(title, body, language)
    return len(terms)


def labels_for(row) -> str:
    """Page chrome labels from the saved raw HTML, empty when the HTML is not on disk."""
    p = row["raw_html_path"]
    if not p:
        return ""
    import gzip
    full = config.ROOT / p
    if not full.exists():
        return ""
    try:
        with gzip.open(full, "rt", encoding="utf-8") as fh:
            return extract.page_labels(fh.read())
    except Exception:
        return ""


def classify_article(conn, row) -> Dict:
    body = store.load_body(row["url_hash"]) or ""
    res = match_signatures(row["title"], body, row["author"], labels=labels_for(row))
    fired = [m["id"] for m in res["matches"]]
    if res["decision"] == "A":
        evidence = res["matches"][0]["span"]
        store.insert_classification(
            conn, row["id"], "rules", "A", 1.0, evidence_quote=evidence,
            reasoning="Deterministic signature match: %s" % ", ".join(fired),
            signatures_fired=fired, ruleset_version=res["ruleset_version"],
        )
        return {"outcome": "A"}
    if res["decision"] == "A_candidate" or res["triggers"]:
        trig = {"a_candidate": fired, "triggers": [t["id"] for t in res["triggers"]],
                "spans": [t["span"] for t in res["triggers"]][:3] + [m["span"] for m in res["matches"]][:2]}
        # A rules label from an earlier ruleset is withdrawn, not deleted; the LLM supplies the next one.
        conn.execute("UPDATE classifications SET is_current=0 WHERE article_id=? AND method='rules'", (row["id"],))
        store.update_article(conn, row["id"], status="awaiting_llm", llm_pending=1, llm_trigger=json.dumps(trig))
        return {"outcome": "llm"}
    hits = body_relevance_hits(row["title"], body, row["language"])
    if hits >= BODY_RELEVANCE_MIN_HITS:
        store.insert_classification(
            conn, row["id"], "rules", "C", 0.75,
            reasoning="No state-origin signature and no official sourcing trigger; body mentions %d distinct China terms." % hits,
            signatures_fired=["residual_c"], ruleset_version=res["ruleset_version"],
        )
        return {"outcome": "C"}
    store.insert_classification(
        conn, row["id"], "rules", "not_relevant", 0.7,
        reasoning="No state-origin signature, no official sourcing trigger, and only %d distinct China terms in the body." % hits,
        signatures_fired=["residual_not_relevant"], ruleset_version=res["ruleset_version"],
    )
    return {"outcome": "not_relevant"}


def run(conn, run_id: str, deadline: Optional[float] = None, status: str = "fetched") -> Dict:
    log_id = store.start_stage(conn, run_id, "classify_rules")
    rows = store.articles_by_status(conn, status, limit=100000)
    counts = {"input": len(rows), "A": 0, "llm": 0, "C": 0, "not_relevant": 0, "skipped_deadline": 0}
    for r in rows:
        if deadline and time.time() > deadline:
            counts["skipped_deadline"] += 1
            continue
        try:
            out = classify_article(conn, r)
            counts[out["outcome"]] += 1
        except Exception as exc:
            store.update_article(conn, r["id"], status="failed", fail_reason="classify_exception:%s" % type(exc).__name__)
            counts["failed"] = counts.get("failed", 0) + 1
        conn.commit()
    store.finish_stage(conn, log_id, True, counts)
    return counts


def reclassify(conn, run_id: str, since: Optional[str] = None) -> Dict:
    """Reclassify forward under the current ruleset. Earlier rows stay, marked not current."""
    q = "SELECT a.* FROM articles a WHERE a.status IN ('classified','awaiting_llm')"
    params = []
    if since:
        q += " AND a.discovered_at >= ?"
        params.append(since)
    rows = conn.execute(q, params).fetchall()
    counts = {"input": len(rows), "A": 0, "llm": 0, "C": 0, "not_relevant": 0}
    for r in rows:
        cur = store.current_classification(conn, r["id"])
        if cur and cur["ruleset_version"] == config.RULESET_VERSION:
            counts["already_current"] = counts.get("already_current", 0) + 1
            continue
        if cur and cur["method"] == "llm":
            # keep LLM judgements unless the rules now say A
            body = store.load_body(r["url_hash"]) or ""
            res = match_signatures(r["title"], body, r["author"], labels=labels_for(r))
            if res["decision"] != "A":
                continue
        out = classify_article(conn, r)
        counts[out["outcome"]] += 1
        conn.commit()
    return counts


if __name__ == "__main__":
    import sys
    conn = store.connect()
    if len(sys.argv) > 1 and sys.argv[1] == "reclassify":
        print(json.dumps(reclassify(conn, "manual", since=sys.argv[2] if len(sys.argv) > 2 else None)))
    else:
        print(json.dumps(run(conn, "manual")))
