"""Category B adjudication with the Anthropic API.

An article reaches this stage if it passed the relevance gate, was not
classified A deterministically, and either matched a weak A signature or an
official sourcing trigger. The model sees the headline and body only. It never
sees the outlet name or the country, so it cannot learn a geographic pattern
the map is supposed to discover.

Modes:
  synchronous (default)  one request per article, results stored immediately
  batch (--batch)        submits pending articles to the Message Batches API and
                         collects finished batches on later runs
  dry run                a fake client, no network, for tests and fixtures

The daily call ceiling (config.LLM_DAILY_CALL_CEILING) stops the stage and
records a ceiling event in llm_usage and run_log, so a truncated day is never
mistaken for a quiet day.
"""
import json
import time
from typing import Dict, List, Optional

from pipeline import config, store

CATEGORY_DEFINITIONS = """Category A, state origin. The text was written by an entity of the Chinese state and published essentially unaltered. Xinhua wire copy, CGTN promotional releases, China Daily and Global Times syndication, sponsored or branded placements paid for by Chinese state entities, and signed opinion pieces by Chinese ambassadors and embassy officials. The defining property is that nobody at the receiving publication formed an editorial view about the content.

Category B, unverified relay. The article was written by the local outlet, but it passes on official Chinese sourcing without independent confirmation. A ministry spokesperson is quoted and nothing checks the claim. Xinhua is cited as the authority for a factual assertion with no second source. State media is described as reporting something, and the report is treated as the fact.

Category C, independent journalism. The outlet's own reporting, including reporting that quotes Chinese officials but confirms, contextualizes or contests what they say.

Not relevant. Does not concern China."""

SYSTEM_PROMPT = """You are a coder on an academic media study. You place one news article into exactly one of four categories. The definitions below come from the study's codebook and are binding.

""" + CATEGORY_DEFINITIONS + """

The critical distinction is between B and C, and it is a judgement about verification, not about topic or tone. An article highly critical of China that repeats an unverified Xinhua claim is still B. An article sympathetic to China that independently confirms every claim is C. Do not let the tone of the article, its political stance, or the topic move your judgement. Ask only: does official Chinese sourcing carry a factual claim in this article, and does the article independently confirm, contextualize or contest that claim?

When you are genuinely uncertain between B and C, answer C. Under-counting B is the safer error for this study. Answer B only when you can quote a sentence in which an official Chinese source carries a factual assertion that the article treats as fact without any independent confirmation, contextualization or contest.

Answer A only when the text itself is state-authored and unaltered, for example a wire item carrying a Xinhua credit, a sponsored placement by a Chinese state entity, or a signed piece by a Chinese diplomat. An article about state media, or one that quotes state media, is not A.

Answer not_relevant when the article does not concern China in any substantive way. A passing mention does not make an article relevant.

Return a JSON object with these fields and nothing else:
  category: "A", "B", "C" or "not_relevant"
  confidence: a number from 0.0 to 1.0 for how sure you are of the category
  evidence_quote: the single sentence from the article that most determines the label, copied exactly
  reasoning: two sentences at most
  china_sources_cited: list of official Chinese sources the article cites or quotes, for example "Xinhua" or "MOFA spokesperson Lin Jian", empty list if none
  independent_confirmation_present: true if the article independently confirms, contextualizes or contests the official Chinese claims it carries
  confirmation_evidence: the sentence showing that confirmation, contextualization or contest, or null

Worked examples.

Example 1. Headline: "China's grain output hits record high". Text: "BEIJING, Dec. 12 (Xinhua) -- China's grain output reached a record 706 million tonnes in 2024, up 1.6 percent year on year, the National Bureau of Statistics said Friday. The increase was attributed to expanded planting area and higher yields, the bureau said." Answer: {"category": "A", "confidence": 0.98, "evidence_quote": "BEIJING, Dec. 12 (Xinhua) -- China's grain output reached a record 706 million tonnes in 2024, up 1.6 percent year on year, the National Bureau of Statistics said Friday.", "reasoning": "The text carries a Xinhua wire dateline and is wire copy published unaltered. No local editorial judgement is present.", "china_sources_cited": ["Xinhua", "National Bureau of Statistics"], "independent_confirmation_present": false, "confirmation_evidence": null}

Example 2. Headline: "Beijing rejects espionage claims after arrests". Text: "China has dismissed allegations that two nationals detained last week were working for its intelligence services. Foreign ministry spokesperson Mao Ning said on Tuesday the pair were ordinary businessmen and that the accusations were fabricated to smear China. She said the detentions violated the rights of Chinese citizens and demanded their immediate release. The two men were arrested in Manchester on Thursday." Answer: {"category": "B", "confidence": 0.86, "evidence_quote": "Foreign ministry spokesperson Mao Ning said on Tuesday the pair were ordinary businessmen and that the accusations were fabricated to smear China.", "reasoning": "The outlet wrote the article but the only account of the men's status comes from the ministry spokesperson and nothing checks it. No second source, context or contest is offered.", "china_sources_cited": ["MOFA spokesperson Mao Ning"], "independent_confirmation_present": false, "confirmation_evidence": null}

Example 3. Headline: "Beijing rejects espionage claims after arrests". Text: "China has dismissed allegations that two nationals detained last week were working for its intelligence services. Foreign ministry spokesperson Mao Ning said on Tuesday the pair were ordinary businessmen and that the accusations were fabricated to smear China. Court filings seen by this newspaper, however, show one of the men listed a Ministry of State Security address on his 2019 visa application, and a former colleague confirmed he had been introduced to clients as a government liaison. Police declined to comment." Answer: {"category": "C", "confidence": 0.92, "evidence_quote": "Court filings seen by this newspaper, however, show one of the men listed a Ministry of State Security address on his 2019 visa application, and a former colleague confirmed he had been introduced to clients as a government liaison.", "reasoning": "The same spokesperson claim appears, but the outlet contests it with documents and an independent source. The official claim is not treated as fact.", "china_sources_cited": ["MOFA spokesperson Mao Ning"], "independent_confirmation_present": true, "confirmation_evidence": "Court filings seen by this newspaper, however, show one of the men listed a Ministry of State Security address on his 2019 visa application, and a former colleague confirmed he had been introduced to clients as a government liaison."}

Example 4. Headline: "Xinjiang camps hold more than a million, leaked files show". Text: "Internal documents obtained by a consortium of newspapers describe the operation of detention camps in Xinjiang, including instructions to prevent escapes and to score detainees on ideological transformation. The files were authenticated by three independent experts. Beijing has called the camps vocational training centres. A statement from the Chinese embassy said the documents were fabricated and that the region was stable and prosperous. Former detainees interviewed for this story described forced political indoctrination." Answer: {"category": "C", "confidence": 0.95, "evidence_quote": "The files were authenticated by three independent experts.", "reasoning": "This is hostile to China and quotes the embassy, but the reporting rests on independently authenticated documents and interviews. Official Chinese claims are contested, not relayed.", "china_sources_cited": ["Chinese embassy statement"], "independent_confirmation_present": true, "confirmation_evidence": "Former detainees interviewed for this story described forced political indoctrination."}

Example 5. Headline: "China launches three astronauts to space station". Text: "China sent three astronauts to its Tiangong space station on Wednesday, state broadcaster CCTV reported. The Shenzhou-19 craft lifted off from the Jiuquan launch centre at 4:27 am, according to the China Manned Space Agency, which said the launch was a complete success. The crew will stay for six months. The mission is the latest step in China's plan to land astronauts on the moon by 2030, the agency said." Answer: {"category": "B", "confidence": 0.8, "evidence_quote": "The Shenzhou-19 craft lifted off from the Jiuquan launch centre at 4:27 am, according to the China Manned Space Agency, which said the launch was a complete success.", "reasoning": "The outlet wrote a short item in which every factual claim is carried by CCTV and the space agency and treated as fact. Nothing independent confirms or contextualizes them.", "china_sources_cited": ["CCTV", "China Manned Space Agency"], "independent_confirmation_present": false, "confirmation_evidence": null}

Example 6. Headline: "Nepal floods: rescuers reach cut-off villages". Text: "Rescue teams reached villages in Rasuwa district on Sunday after days of landslides. At least 40 people are confirmed dead, according to the Nepalese home ministry. Aid agencies said access remained difficult. Chinese state media reported that six people were also missing on the Tibetan side of the border." Answer: {"category": "C", "confidence": 0.7, "evidence_quote": "Rescue teams reached villages in Rasuwa district on Sunday after days of landslides.", "reasoning": "The article is independent reporting on Nepal with Nepalese and aid agency sourcing. A single attributed line from Chinese state media about the Tibetan side is clearly marked as a report, not treated as established fact, and is peripheral to the story. Uncertain between B and C, so C."}
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["A", "B", "C", "not_relevant"]},
        "confidence": {"type": "number"},
        "evidence_quote": {"type": "string"},
        "reasoning": {"type": "string"},
        "china_sources_cited": {"type": "array", "items": {"type": "string"}},
        "independent_confirmation_present": {"type": "boolean"},
        "confirmation_evidence": {"type": ["string", "null"]},
    },
    "required": ["category", "confidence", "evidence_quote", "reasoning", "china_sources_cited",
                 "independent_confirmation_present", "confirmation_evidence"],
    "additionalProperties": False,
}


def build_user_message(title: str, body: str) -> str:
    """Headline and body only. No outlet, no country, no URL, no author."""
    body = (body or "").strip()
    if len(body) > config.LLM_BODY_CHAR_LIMIT:
        body = body[:config.LLM_BODY_CHAR_LIMIT] + "\n[text truncated at %d characters]" % config.LLM_BODY_CHAR_LIMIT
    return "Headline: %s\n\nArticle text:\n%s\n\nClassify this article. Return only the JSON object." % ((title or "").strip(), body)


def request_params(title: str, body: str) -> Dict:
    return {
        "model": config.LLM_MODEL,
        "max_tokens": config.LLM_MAX_TOKENS,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": build_user_message(title, body)}],
        "output_config": {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    }


def parse_response(message) -> Optional[Dict]:
    """Return the parsed dict, or None with the failure reason in ['_error']."""
    if getattr(message, "stop_reason", None) == "refusal":
        return {"_error": "refusal"}
    text = None
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text = block.text
            break
    if not text:
        return {"_error": "no_text"}
    try:
        data = json.loads(text)
    except ValueError:
        return {"_error": "invalid_json", "_raw": text}
    if data.get("category") not in ("A", "B", "C", "not_relevant"):
        return {"_error": "bad_category", "_raw": text}
    try:
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        data["confidence"] = 0.0
    data["_raw"] = text
    data["_usage"] = getattr(message, "usage", None)
    return data


class DryRunClient:
    """Fake client for tests and dry runs. Never touches the network."""

    class _Messages:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **params):
            self.outer.calls.append(params)
            body = params["messages"][0]["content"]
            cat = self.outer.answer(body)
            payload = {"category": cat, "confidence": 0.9 if cat != "B" else 0.7,
                       "evidence_quote": body.split("\n")[0][:120], "reasoning": "Dry run.",
                       "china_sources_cited": [], "independent_confirmation_present": cat == "C",
                       "confirmation_evidence": None}

            class Block:
                type = "text"
                text = json.dumps(payload)

            class Usage:
                input_tokens = 100
                output_tokens = 50

            class Msg:
                stop_reason = "end_turn"
                content = [Block()]
                usage = Usage()
                model = "dry-run"
            return Msg()

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer or (lambda body: "C")
        self.messages = DryRunClient._Messages(self)


def make_client(dry_run: bool = False):
    if dry_run:
        return DryRunClient()
    import anthropic
    return anthropic.Anthropic()


def _copy_from_dup_group(conn, row) -> bool:
    """If another placement of the same underlying item already has an LLM label, copy it."""
    if not row["dup_group_id"]:
        return False
    other = conn.execute(
        """SELECT c.* FROM classifications c JOIN articles a ON a.id=c.article_id
           WHERE a.dup_group_id=? AND a.id<>? AND c.is_current=1 AND c.method='llm' ORDER BY c.id DESC LIMIT 1""",
        (row["dup_group_id"], row["id"]),
    ).fetchone()
    if not other:
        return False
    store.insert_classification(
        conn, row["id"], "llm", other["category"], other["confidence"],
        evidence_quote=other["evidence_quote"],
        reasoning="[copied from article %d, same near-duplicate group] %s" % (other["article_id"], other["reasoning"] or ""),
        china_sources_cited=json.loads(other["china_sources_cited"] or "[]"),
        independent_confirmation_present=other["independent_confirmation_present"],
        confirmation_evidence=other["confirmation_evidence"], model_version=other["model_version"],
        raw_response=other["raw_response"], ruleset_version=other["ruleset_version"],
    )
    return True


def store_result(conn, article_id: int, data: Dict, model_version: str) -> None:
    store.insert_classification(
        conn, article_id, "llm", data["category"], data["confidence"],
        evidence_quote=data.get("evidence_quote"), reasoning=data.get("reasoning"),
        china_sources_cited=data.get("china_sources_cited") or [],
        independent_confirmation_present=data.get("independent_confirmation_present"),
        confirmation_evidence=data.get("confirmation_evidence"), model_version=model_version,
        raw_response=data.get("_raw"),
    )


def pending_articles(conn, limit: int) -> List:
    return conn.execute(
        "SELECT * FROM articles WHERE status='awaiting_llm' ORDER BY discovered_at ASC LIMIT ?", (limit,)
    ).fetchall()


def run(conn, run_id: str, deadline: Optional[float] = None, batch: bool = False, dry_run: bool = False,
        client=None, max_consecutive_errors: int = 3) -> Dict:
    log_id = store.start_stage(conn, run_id, "classify_llm")
    counts = {"pending": 0, "classified": 0, "copied": 0, "errors": 0, "ceiling_hit": False,
              "calls": 0, "batch_submitted": 0, "batch_collected": 0, "skipped_deadline": 0}
    ceiling = config.LLM_DAILY_CALL_CEILING
    used = store.llm_calls_today(conn)
    remaining = max(0, ceiling - used)
    if client is None:
        try:
            client = make_client(dry_run=dry_run)
        except Exception as exc:
            store.finish_stage(conn, log_id, False, counts, notes="client init failed: %s" % exc)
            counts["error"] = "client_init_failed"
            return counts

    # Near-duplicate copies cost nothing and do not count against the ceiling.
    rows = pending_articles(conn, 100000)
    counts["pending"] = len(rows)
    todo = []
    for r in rows:
        if _copy_from_dup_group(conn, r):
            counts["copied"] += 1
            conn.commit()
        else:
            todo.append(r)
    # In synchronous mode, later members of a group can copy from a member classified in this run,
    # so the copy check runs again inside the loop. Keep group members adjacent.
    todo.sort(key=lambda r: (r["dup_group_id"] or r["id"], r["id"]))

    if batch and not dry_run:
        counts.update(_collect_batches(conn, client))
        if remaining == 0 and todo:
            store.record_llm_usage(conn, 0, 0, 0, ceiling_hit=True)
            counts["ceiling_hit"] = True
        else:
            n = _submit_batch(conn, client, todo[:remaining])
            counts["batch_submitted"] = n
            store.record_llm_usage(conn, n, 0, 0, ceiling_hit=(n < len(todo)))
            counts["ceiling_hit"] = n < len(todo)
        conn.commit()
        store.finish_stage(conn, log_id, True, counts, ceiling_hit=counts["ceiling_hit"],
                           notes="ceiling %d reached; %d left pending" % (ceiling, len(todo) - counts["batch_submitted"]) if counts["ceiling_hit"] else "")
        return counts

    consecutive_errors = 0
    for r in todo:
        if deadline and time.time() > deadline:
            counts["skipped_deadline"] += 1
            continue
        if _copy_from_dup_group(conn, r):
            counts["copied"] += 1
            conn.commit()
            continue
        if remaining <= 0:
            counts["ceiling_hit"] = True
            store.record_llm_usage(conn, 0, 0, 0, ceiling_hit=True)
            conn.commit()
            break
        body = store.load_body(r["url_hash"]) or ""
        if not body:
            store.update_article(conn, r["id"], status="failed", fail_reason="body_missing_for_llm")
            conn.commit()
            continue
        params = request_params(r["title"], body)
        try:
            message = client.messages.create(**params)
        except Exception as exc:
            counts["errors"] += 1
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                store.finish_stage(conn, log_id, False, counts, notes="aborted after repeated API errors: %s" % type(exc).__name__)
                return counts
            time.sleep(min(30, 2 ** consecutive_errors))
            continue
        remaining -= 1
        counts["calls"] += 1
        data = parse_response(message)
        usage = data.get("_usage") if data else None
        store.record_llm_usage(conn, 1, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0)
        if not data or data.get("_error"):
            counts["errors"] += 1
            consecutive_errors += 1
            if data and data.get("_error") == "refusal":
                store.update_article(conn, r["id"], status="failed", fail_reason="llm_refusal")
            conn.commit()
            continue
        consecutive_errors = 0
        store_result(conn, r["id"], data, getattr(message, "model", None) or config.LLM_MODEL)
        counts["classified"] += 1
        conn.commit()
    left = conn.execute("SELECT COUNT(*) FROM articles WHERE status='awaiting_llm'").fetchone()[0]
    store.finish_stage(conn, log_id, True, counts, ceiling_hit=counts["ceiling_hit"],
                       notes=("daily ceiling of %d calls reached with %d articles left pending" % (ceiling, left)) if counts["ceiling_hit"] else "")
    return counts


# ---------------------------------------------------------------------------
# Batch mode
# ---------------------------------------------------------------------------

def _submit_batch(conn, client, rows) -> int:
    if not rows:
        return 0
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    requests = []
    ids = []
    for r in rows:
        body = store.load_body(r["url_hash"]) or ""
        if not body:
            store.update_article(conn, r["id"], status="failed", fail_reason="body_missing_for_llm")
            continue
        requests.append(Request(custom_id=str(r["id"]), params=MessageCreateParamsNonStreaming(**request_params(r["title"], body))))
        ids.append(r["id"])
    if not requests:
        return 0
    batch = client.messages.batches.create(requests=requests)
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version) VALUES (?,?,?,?,?)",
                 (batch.id, store.utcnow(), "submitted", json.dumps(ids), config.LLM_MODEL))
    for aid in ids:
        store.update_article(conn, aid, status="llm_submitted")
    return len(ids)


def _collect_batches(conn, client) -> Dict:
    out = {"batch_collected": 0, "batch_errors": 0}
    for b in conn.execute("SELECT * FROM llm_batches WHERE status='submitted'").fetchall():
        info = client.messages.batches.retrieve(b["batch_id"])
        if info.processing_status != "ended":
            continue
        for result in client.messages.batches.results(b["batch_id"]):
            aid = int(result.custom_id)
            rtype = result.result.type
            if rtype == "succeeded":
                data = parse_response(result.result.message)
                usage = data.get("_usage") if data else None
                store.record_llm_usage(conn, 0, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0)
                if data and not data.get("_error"):
                    store_result(conn, aid, data, getattr(result.result.message, "model", None) or b["model_version"])
                    out["batch_collected"] += 1
                    continue
                store.update_article(conn, aid, status="awaiting_llm")
                out["batch_errors"] += 1
            elif rtype in ("errored", "expired", "canceled"):
                store.update_article(conn, aid, status="awaiting_llm")
                out["batch_errors"] += 1
        conn.execute("UPDATE llm_batches SET status='collected', collected_at=? WHERE batch_id=?", (store.utcnow(), b["batch_id"]))
        conn.commit()
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = store.connect()
    print(json.dumps(run(conn, "manual", batch=args.batch, dry_run=args.dry_run)))
