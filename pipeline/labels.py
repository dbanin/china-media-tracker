"""Model labels produced outside the API, imported by the hosted runner.

When the monthly API budget is spent, articles waiting for the verification judgement pile up at
status awaiting_llm. The owner can then have the same model judge them with the identical system
prompt (classify_llm.SYSTEM_PROMPT) in an interactive session on the owner's machine, at no API
cost. The session is given what the API path gives the model, the headline and the body, and
nothing else: never the outlet, the country or the URL, so the model still cannot learn a
geographic pattern the map is supposed to discover.

The session writes its answers to data/labels/*.jsonl, one JSON object per line, and those files
are committed to the repository. This module is the other half:

  ingest  runs on the hosted runner at the start of every collect job, right after the relay
          ingest. It reads every labels file and applies a label only to an article that is still
          waiting for the model and has no current classification. It stores the label through
          classify_llm.store_result, the same call the API path uses, so the classification row,
          the article status and every table downstream look exactly as they would after an API
          reply; near-duplicate siblings still waiting are then settled from it the same way.
          Anything else (an article already classified, gated out, submitted to a batch, or not in
          the database at all) is counted and skipped, so a file can be ingested any number of
          times and nothing is inserted twice.

The labels carry their own model_version, which names the session (for example
"claude-sonnet-5 (session)"), and the label's own JSON line is kept as the raw response, so a label
imported this way can always be told apart from one the API returned. They are model labels like
any other: they count as such in every figure and are sampled by the reliability study under the
same rules. Nothing here books API usage, because none was spent.

Line format (every field required):
  {"url_hash": "...", "category": "A|B|C|not_relevant", "confidence": 0.0..1.0,
   "evidence_quote": "...", "reasoning": "...", "china_sources_cited": ["..."],
   "independent_confirmation_present": true|false, "confirmation_evidence": "..." or null,
   "model_version": "claude-sonnet-5 (session)", "labelled_at": "2026-09-30" or an ISO datetime,
   "source": "session"}

The same directory holds api_first.jsonl, which is not a labels file and is never ingested. It
lists the waiting articles whose text the session could not fetch on the owner's machine (HTTP 403,
robots.txt, a paywall, a page that is gone), so they can only be judged by the API from the runner's
own copy of the text. classify_llm.run offers these to the API first and holds back other articles
younger than config.LLM_SESSION_GRACE_DAYS for the session route. load_api_first reads it.

  {"url_hash": "...", "reason": "HTTP 403", "listed_at": "2026-10-04", "source": "session"}

Usage:
  python -m pipeline.labels ingest
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pipeline import classify_llm, config, store

STAGE = "labels_ingest"
CATEGORIES = ("A", "B", "C", "not_relevant")
FIELDS = ("url_hash", "category", "confidence", "evidence_quote", "reasoning", "china_sources_cited",
          "independent_confirmation_present", "confirmation_evidence", "model_version", "labelled_at", "source")
# Enough examples of a bad line to diagnose a file without printing all of it.
MALFORMED_SAMPLES = 5
# The list of articles the API takes first; it lives beside the labels but holds none.
API_FIRST_FILE = "api_first.jsonl"
API_FIRST_FIELDS = ("url_hash", "reason", "listed_at", "source")


def _parse_when(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for parse in (dt.date.fromisoformat, dt.datetime.fromisoformat):
        try:
            parse(text)
            return True
        except ValueError:
            continue
    return False


def validate(obj) -> Optional[str]:
    """Why a parsed line is not a usable label, or None when it is."""
    if not isinstance(obj, dict):
        return "not a JSON object"
    missing = [f for f in FIELDS if f not in obj]
    if missing:
        return "missing " + ", ".join(missing)
    if not isinstance(obj["url_hash"], str) or not obj["url_hash"].strip():
        return "url_hash is not a non-empty string"
    if obj["category"] not in CATEGORIES:
        return "category %r is not one of %s" % (obj["category"], "|".join(CATEGORIES))
    conf = obj["confidence"]
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
        return "confidence is not a number from 0 to 1"
    for f in ("evidence_quote", "reasoning", "source"):
        if not isinstance(obj[f], str):
            return "%s is not a string" % f
    cited = obj["china_sources_cited"]
    if not isinstance(cited, list) or not all(isinstance(s, str) for s in cited):
        return "china_sources_cited is not a list of strings"
    if not isinstance(obj["independent_confirmation_present"], bool):
        return "independent_confirmation_present is not true or false"
    if obj["confirmation_evidence"] is not None and not isinstance(obj["confirmation_evidence"], str):
        return "confirmation_evidence is not a string or null"
    mv = obj["model_version"]
    if not isinstance(mv, str) or not mv.strip():
        return "model_version is not a non-empty string"
    # A label from outside the API must be distinguishable from one the API returned.
    if mv.strip() == config.LLM_MODEL:
        return "model_version %r is the API model's own name; name the session, e.g. %r" % (mv, config.LLM_MODEL + " (session)")
    if not _parse_when(obj["labelled_at"]):
        return "labelled_at is not an ISO date or datetime"
    return None


def load_files(directory: Optional[Path] = None) -> Tuple[List[Dict], Dict]:
    """Every label in directory/*.jsonl (config.LABELS_DIR by default), in file then line order.

    Returns (labels, stats). Each label is the parsed object plus '_raw', its own line of JSON, and
    '_where', file:line. Blank lines are ignored; a line that does not parse or does not validate is
    counted in stats['malformed'] and skipped, never fatal."""
    directory = Path(directory) if directory is not None else config.LABELS_DIR
    stats = {"files": 0, "lines": 0, "malformed": 0, "malformed_samples": []}
    labels = []
    if not directory.is_dir():
        return labels, stats
    for path in sorted(directory.glob("*.jsonl")):
        if path.name == API_FIRST_FILE:
            continue
        stats["files"] += 1
        with open(path, encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                text = line.strip()
                if not text:
                    continue
                stats["lines"] += 1
                where = "%s:%d" % (path.name, n)
                try:
                    obj = json.loads(text)
                    reason = validate(obj)
                except ValueError as exc:
                    reason = "invalid JSON (%s)" % exc
                if reason:
                    stats["malformed"] += 1
                    if len(stats["malformed_samples"]) < MALFORMED_SAMPLES:
                        stats["malformed_samples"].append("%s: %s" % (where, reason))
                    continue
                obj["url_hash"] = obj["url_hash"].strip()
                obj["model_version"] = obj["model_version"].strip()
                obj["_raw"] = text
                obj["_where"] = where
                labels.append(obj)
    return labels, stats


def _validate_api_first(obj) -> Optional[str]:
    """Why a parsed api_first line is not usable, or None when it is."""
    if not isinstance(obj, dict):
        return "not a JSON object"
    missing = [f for f in API_FIRST_FIELDS if f not in obj]
    if missing:
        return "missing " + ", ".join(missing)
    if not isinstance(obj["url_hash"], str) or not obj["url_hash"].strip():
        return "url_hash is not a non-empty string"
    for f in ("reason", "source"):
        if not isinstance(obj[f], str):
            return "%s is not a string" % f
    if not _parse_when(obj["listed_at"]):
        return "listed_at is not an ISO date or datetime"
    return None


def load_api_first(directory: Optional[Path] = None, stats: Optional[Dict] = None) -> Dict[str, str]:
    """The articles the API should take first, from directory/api_first.jsonl (config.LABELS_DIR by
    default), as {url_hash: reason}; the first line for a url_hash wins. A missing file is an empty
    list. Blank lines are ignored; a line that does not parse or does not validate is counted and
    skipped, never fatal. When stats is given it is filled with lines, malformed and
    malformed_samples."""
    directory = Path(directory) if directory is not None else config.LABELS_DIR
    st = stats if stats is not None else {}
    st.update({"lines": 0, "malformed": 0, "malformed_samples": []})
    out = {}
    path = directory / API_FIRST_FILE
    if not path.is_file():
        return out
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            text = line.strip()
            if not text:
                continue
            st["lines"] += 1
            try:
                obj = json.loads(text)
                reason = _validate_api_first(obj)
            except ValueError as exc:
                reason = "invalid JSON (%s)" % exc
            if reason:
                st["malformed"] += 1
                if len(st["malformed_samples"]) < MALFORMED_SAMPLES:
                    st["malformed_samples"].append("%s:%d: %s" % (path.name, n, reason))
                continue
            out.setdefault(obj["url_hash"].strip(), obj["reason"])
    return out


def _data(label: Dict) -> Dict:
    """The label in the shape classify_llm.parse_response hands to store_result."""
    return {
        "category": label["category"],
        "confidence": float(label["confidence"]),
        "evidence_quote": label["evidence_quote"],
        "reasoning": label["reasoning"],
        "china_sources_cited": list(label["china_sources_cited"]),
        "independent_confirmation_present": label["independent_confirmation_present"],
        "confirmation_evidence": label["confirmation_evidence"],
        "_raw": label["_raw"],
    }


def ingest(conn, directory: Optional[Path] = None, now: Optional[dt.datetime] = None) -> Dict:
    """Apply every label whose article is still waiting for the model, once. Records one run_log
    row (stage labels_ingest) with the counts, and returns them."""
    now = now or dt.datetime.now(dt.timezone.utc)
    log_id = store.start_stage(conn, "labels-%s" % now.strftime("%Y%m%dT%H%M%SZ"), STAGE)
    counts = {"files": 0, "lines": 0, "malformed": 0, "applied": 0, "already_classified": 0,
              "not_awaiting": 0, "unknown": 0, "copied": 0, "by_category": {}}
    try:
        labels, stats = load_files(directory)
        counts.update({k: stats[k] for k in ("files", "lines", "malformed")})
        if stats["malformed_samples"]:
            counts["malformed_samples"] = stats["malformed_samples"]
        applied = []
        for label in labels:
            row = conn.execute("SELECT id, status FROM articles WHERE url_hash=?", (label["url_hash"],)).fetchone()
            if row is None:
                counts["unknown"] += 1
                continue
            # An article with a current label is never relabelled from a file, whatever its status,
            # which is also what makes a second ingest of the same file a no-op.
            if store.current_classification(conn, row["id"]) is not None:
                counts["already_classified"] += 1
                continue
            if row["status"] != "awaiting_llm":
                counts["not_awaiting"] += 1
                continue
            # The same call the API path makes after a reply parses: a current llm classification,
            # the route of a state origin label, and the article moved to status classified.
            classify_llm.store_result(conn, row["id"], _data(label), label["model_version"])
            conn.commit()
            applied.append(row["id"])
            counts["applied"] += 1
            counts["by_category"][label["category"]] = counts["by_category"].get(label["category"], 0) + 1
        # Near-duplicate siblings still waiting take the label of the one that got it, through the
        # same path classify_llm.run uses. Done after every file has been applied, so a sibling that
        # has a label of its own in the files keeps its own rather than a copy.
        for aid in applied:
            counts["copied"] += classify_llm._settle_pending_dup_siblings(conn, aid)
    except Exception as exc:
        conn.commit()   # keep the labels already applied
        store.finish_stage(conn, log_id, False, counts, notes="%s: %s" % (type(exc).__name__, str(exc)[:300]))
        raise
    store.finish_stage(conn, log_id, True, counts)
    return counts


def session_label_counts(conn) -> Dict:
    """Current model labels that came in through this module rather than from the API, told apart
    by their raw response: the label's own line, which carries labelled_at, a field the API's output
    schema does not allow. Near-duplicate copies of such a label carry the same raw response and
    are counted with it."""
    rows = conn.execute(
        """SELECT model_version, COUNT(*) n FROM classifications
           WHERE is_current=1 AND method='llm'
             AND json_extract(CASE WHEN json_valid(raw_response) THEN raw_response END, '$.labelled_at') IS NOT NULL
           GROUP BY model_version ORDER BY model_version""").fetchall()
    by_model = {(r["model_version"] or "unrecorded"): r["n"] for r in rows}
    return {"total": sum(by_model.values()), "by_model_version": by_model}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["ingest"])
    ap.add_argument("--dir", type=Path, default=None, help="directory of *.jsonl label files (default data/labels)")
    args = ap.parse_args(argv)
    conn = store.connect()
    counts = ingest(conn, args.dir)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
