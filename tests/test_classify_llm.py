import datetime as dt
import json
import sqlite3

from pipeline import classify_llm as cl, config, store


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    return conn


def test_prompt_contains_definitions_verbatim():
    for phrase in (
        "Category A, state origin. The text was written by an entity of the Chinese state and published essentially unaltered.",
        "Category B, unverified relay. The article was written by the local outlet, but it passes on official Chinese sourcing without independent confirmation.",
        "Category C, independent journalism. The outlet's own reporting, including reporting that quotes Chinese officials but confirms, contextualizes or contests what they say.",
        "Not relevant. Does not concern China.",
        "verification, not about topic or tone",
        "answer C",
    ):
        assert phrase in cl.SYSTEM_PROMPT, phrase


def test_prompt_has_required_examples():
    assert cl.SYSTEM_PROMPT.count('"category": "A"') >= 1
    assert cl.SYSTEM_PROMPT.count('"category": "B"') >= 1
    assert cl.SYSTEM_PROMPT.count('"category": "C"') >= 2


def test_model_not_shown_outlet_or_country():
    p = cl.request_params("Headline", "Body text")
    blob = json.dumps(p)
    for leak in ("outlet", "country", "ITA", "Corriere", "http"):
        assert leak not in p["messages"][0]["content"], leak
    assert "Headline" in blob and "Body text" in blob
    assert p["model"] == config.LLM_MODEL


def test_body_truncated_at_limit():
    msg = cl.build_user_message("t", "x" * (config.LLM_BODY_CHAR_LIMIT + 500))
    assert "truncated" in msg and len(msg) < config.LLM_BODY_CHAR_LIMIT + 200


def _seed(conn, n, dup=False):
    ids = []
    for i in range(n):
        aid = store.insert_discovered(conn, {"url": "https://e.com/%d" % i, "outlet_id": "o", "country": "ITA",
                                             "language": "it", "title": "Title %d" % i, "status": "awaiting_llm", "gate_relevant": 1})
        ids.append(aid)
    if dup:
        for aid in ids:
            conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (ids[0], aid))
    return ids


def test_dry_run_classifies_and_records_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    ids = _seed(conn, 3)
    for i, aid in enumerate(ids):
        store.save_body(store.url_hash("https://e.com/%d" % i), "Body about China %d" % i)
    client = cl.DryRunClient(answer=lambda body: "B")
    counts = cl.run(conn, "t", client=client)
    assert counts["classified"] == 3 and counts["calls"] == 3
    assert store.llm_calls_today(conn) == 3
    row = store.current_classification(conn, ids[0])
    assert row["category"] == "B" and row["method"] == "llm" and row["raw_response"]


def test_daily_ceiling_stops_and_records(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(config, "LLM_DAILY_CALL_CEILING", 2)
    conn = _db()
    ids = _seed(conn, 4)
    for i in range(4):
        store.save_body(store.url_hash("https://e.com/%d" % i), "Body %d" % i)
    counts = cl.run(conn, "t", client=cl.DryRunClient())
    assert counts["calls"] == 2 and counts["ceiling_hit"] is True
    assert conn.execute("SELECT ceiling_hit FROM llm_usage").fetchone()[0] == 1
    assert conn.execute("SELECT ceiling_hit FROM run_log WHERE stage='classify_llm'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE status='awaiting_llm'").fetchone()[0] == 2


def test_dup_group_copies_without_call(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    ids = _seed(conn, 3, dup=True)
    for i in range(3):
        store.save_body(store.url_hash("https://e.com/%d" % i), "Body")
    client = cl.DryRunClient(answer=lambda body: "B")
    counts = cl.run(conn, "t", client=client)
    assert counts["calls"] == 1 and counts["copied"] == 2 and counts["classified"] == 1


def _seed_dup_group_plus_solo(conn, dup_prefix, solo_prefix):
    """Three articles sharing one dup_group_id, plus one unrelated article, each with a distinct
    URL and a saved body. Returns (dup_ids, solo_id)."""
    urls = ["https://%s.com/%d" % (dup_prefix, i) for i in range(3)] + ["https://%s.com/0" % solo_prefix]
    ids = []
    for u in urls:
        aid = store.insert_discovered(conn, {"url": u, "outlet_id": "o", "country": "ITA", "language": "it",
                                              "title": u, "status": "awaiting_llm", "gate_relevant": 1})
        ids.append(aid)
        store.save_body(store.url_hash(u), "Body for %s" % u)
    dup_ids, solo_id = ids[:3], ids[3]
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id IN (?,?,?)", (dup_ids[0], *dup_ids))
    conn.commit()
    return dup_ids, solo_id


def test_dup_group_race_collapses_to_one_representative(monkeypatch, tmp_path):
    """Diagnosis (recommendation 3): several still-unlabelled members of one dup_group_id drawn
    into the same run had no label yet to copy from at submission time, so all went out as
    separate real calls -- 138 avoidable calls, 3.5% of a month's volume. The to-do list is now
    collapsed to one representative per group before draw(), and the held-back siblings are
    settled through the existing copy path as soon as the representative is classified, in the
    same run."""
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    dup_ids, solo_id = _seed_dup_group_plus_solo(conn, "dup-sync", "solo-sync")
    client = cl.DryRunClient(answer=lambda body: "B")
    counts = cl.run(conn, "t", client=client)

    # Only the representative and the unrelated article were actually sent to the model.
    assert counts["calls"] == 2 and len(client.calls) == 2
    assert store.llm_calls_today(conn) == 2, "the ceiling/budget counters see two calls, not four"
    assert counts["copied"] == 2

    # All four settle, and the three duplicates carry the same label.
    cats = [store.current_classification(conn, aid)["category"] for aid in dup_ids + [solo_id]]
    assert cats == ["B", "B", "B", "B"]
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE status='awaiting_llm'").fetchone()[0] == 0


class _RecordingBatchApi:
    """Records what was submitted; retrieve/results are unused here (a separate call collects)."""

    def __init__(self):
        self.created = []

    def create(self, requests):
        self.created.append(requests)
        return type("B", (), {"id": "b1"})()


def test_batch_submission_collapses_dup_group_and_settles_on_collection(monkeypatch, tmp_path):
    """Same race as the synchronous case, but for the batch submission path: the batch API sends
    every request in the submission at once, so a held-back sibling must never be included in the
    payload. Once the batch collects, the sibling settles through the same copy path without a
    separate run."""
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    dup_ids, solo_id = _seed_dup_group_plus_solo(conn, "dup-batch", "solo-batch")

    recorder = _RecordingBatchApi()
    client = type("C", (), {"messages": type("M", (), {"batches": recorder})()})()
    counts = cl.run(conn, "t", client=client, batch=True)

    submitted_ids = {int(req["custom_id"]) for req in recorder.created[0]}
    assert len(submitted_ids) == 2 and counts["batch_submitted"] == 2, "only one representative, not all three duplicates"
    assert solo_id in submitted_ids
    representative = next(i for i in submitted_ids if i != solo_id)
    assert representative in dup_ids

    good = ('{"category": "B", "confidence": 0.7, "evidence_quote": "q", "reasoning": "r", '
            '"china_sources_cited": [], "independent_confirmation_present": false, "confirmation_evidence": null}')
    out = cl._collect_batches(conn, _batch_client([(representative, "succeeded", _message("end_turn", good)),
                                                    (solo_id, "succeeded", _message("end_turn", good))]))
    assert out["batch_collected"] == 2 and out["copied"] == 2, "the two held-back siblings settle on collection"

    cats = [store.current_classification(conn, aid)["category"] for aid in dup_ids + [solo_id]]
    assert cats == ["B", "B", "B", "B"]
    assert store.llm_calls_today(conn) == 2, "the ceiling counts two calls for the whole group of four"
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE status='awaiting_llm'").fetchone()[0] == 0


def test_submitted_batches_are_collected_by_any_later_run(monkeypatch, tmp_path):
    """A batch submitted by one run has to be collected by the next run, which is synchronous.
    Collecting only inside the batch path left submissions sitting unclaimed."""
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    conn.execute("""INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version)
                    VALUES ('b1', '2026-09-16T00:00:00+00:00', 'submitted', '[1]', 'claude-sonnet-5')""")
    conn.commit()
    seen = {}

    def fake_collect(conn, client):
        seen["called"] = True
        return {"batch_collected": 1}

    monkeypatch.setattr(cl, "_collect_batches", fake_collect)
    counts = cl.run(conn, "t", client=cl.DryRunClient())
    assert seen.get("called") and counts["batch_collected"] == 1


def test_no_collection_attempt_when_nothing_is_outstanding(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    monkeypatch.setattr(cl, "_collect_batches", lambda conn, client: (_ for _ in ()).throw(AssertionError("should not collect")))
    cl.run(conn, "t", client=cl.DryRunClient())


def test_parse_response_rejects_bad_category():
    class B: type = "text"; text = '{"category": "D"}'
    class M: stop_reason = "end_turn"; content = [B()]; usage = None
    assert cl.parse_response(M())["_error"] == "bad_category"
    class R: stop_reason = "refusal"; content = []
    assert cl.parse_response(R())["_error"] == "refusal"


# ---------------------------------------------------------------------------
# What a reply costs is recorded whether or not it parsed
# ---------------------------------------------------------------------------

class _Usage:
    input_tokens = 1500
    output_tokens = 1200
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


def _message(stop_reason="end_turn", text='{"category": "B"}', usage=_Usage()):
    block = type("Block", (), {"type": "text", "text": text})()
    return type("Msg", (), {"stop_reason": stop_reason, "content": [block], "usage": usage, "model": "m"})()


class _UnusableClient:
    """Every reply comes back unusable, with the usage the API billed attached to it."""

    def __init__(self, stop_reason="max_tokens", text="{\"category\": \"B\", \"evidence_quote\": \"it never ends"):
        self.calls = 0
        self.stop_reason, self.text = stop_reason, text
        outer = self

        class _Messages:
            def create(self, **params):
                outer.calls += 1
                return _message(outer.stop_reason, outer.text)
        self.messages = _Messages()


def test_parse_response_carries_usage_on_every_path():
    """The API bills a refusal, a truncated reply and malformed JSON like any other call."""
    for msg in (_message("refusal", ""), _message("max_tokens", "{"), _message("end_turn", "not json"),
                _message("end_turn", '{"category": "D"}'), _message("end_turn", '{"category": "B", "confidence": 1}')):
        data = cl.parse_response(msg)
        assert data["_usage"] is not None, data.get("_error")
        assert cl.llm_cost.usage_tokens(data["_usage"])["output_tokens"] == 1200


def test_truncated_replies_are_billed_not_free(monkeypatch, tmp_path):
    """A reply cut off at max_tokens burns the whole output budget and is the most expensive kind
    of failure; it used to be recorded at zero tokens and zero dollars."""
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(config, "LLM_MONTHLY_BUDGET_USD", 1000.0)
    conn = _db()
    _seed(conn, 3)
    for i in range(3):
        store.save_body(store.url_hash("https://e.com/%d" % i), "Body %d about China" % i)
    counts = cl.run(conn, "t", client=_UnusableClient())
    assert counts["errors"] == 3 and counts["error_kinds"] == {"truncated": 3} and counts["classified"] == 0
    row = conn.execute("SELECT * FROM llm_usage").fetchone()
    assert row["calls"] == 3 and row["input_tokens"] == 4500 and row["output_tokens"] == 3600
    assert abs(row["cost_usd"] - 3 * cl.llm_cost.call_cost(1500, 1200)) < 1e-9
    # and a refusal, which also fails the article
    conn2 = _db()
    _seed(conn2, 1)
    store.save_body(store.url_hash("https://e.com/0"), "Body about China")
    cl.run(conn2, "t", client=_UnusableClient(stop_reason="refusal", text=""))
    assert conn2.execute("SELECT SUM(cost_usd) FROM llm_usage").fetchone()[0] > 0
    assert conn2.execute("SELECT status FROM articles").fetchone()[0] == "failed"


class _FakeBatchApi:
    """A Batches API whose results come back in the shapes the collector has to survive."""

    def __init__(self, results, retrieve_error=None):
        self._results, self.retrieve_error = results, retrieve_error

    def retrieve(self, batch_id):
        if self.retrieve_error:
            raise self.retrieve_error
        return type("I", (), {"processing_status": "ended"})()

    def results(self, batch_id):
        for custom_id, rtype, message in self._results:
            result = type("X", (), {"type": rtype, "message": message})()
            yield type("R", (), {"custom_id": str(custom_id), "result": result})()


def _batch_client(results, retrieve_error=None):
    return type("C", (), {"messages": type("M", (), {"batches": _FakeBatchApi(results, retrieve_error)})()})()


def _submitted_batch(conn, ids, day="2026-09-16T01:50:36+00:00"):
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version, kind) "
                 "VALUES ('b1',?,'submitted',?,'claude-sonnet-5','classify')", (day, json.dumps(ids)))
    for aid in ids:
        store.update_article(conn, aid, status="llm_submitted")
    conn.commit()


def test_batch_results_are_billed_even_when_they_do_not_parse(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    ids = _seed(conn, 3)
    _submitted_batch(conn, ids)
    good = '{"category": "B", "confidence": 0.7, "evidence_quote": "q", "reasoning": "r", "china_sources_cited": [], ' \
           '"independent_confirmation_present": false, "confirmation_evidence": null}'
    client = _batch_client([(ids[0], "succeeded", _message("end_turn", good)),
                            (ids[1], "succeeded", _message("max_tokens", "{")),
                            (ids[2], "errored", None)])
    out = cl._collect_batches(conn, client)
    assert out["batch_collected"] == 1 and out["batch_errors"] == 2
    assert out["batch_error_kinds"] == {"truncated": 1, "errored": 1}
    row = conn.execute("SELECT * FROM llm_usage WHERE date='2026-09-16'").fetchone()
    # Both messages the API returned are booked, at the batch price; the errored result carried none.
    assert row["input_tokens"] == 3000 and row["output_tokens"] == 2400
    assert abs(row["cost_usd"] - 2 * cl.llm_cost.call_cost(1500, 1200, batched=True)) < 1e-9
    left = [r[0] for r in conn.execute("SELECT status FROM articles ORDER BY id")]
    assert left == ["classified", "awaiting_llm", "awaiting_llm"]


def test_a_collection_failure_does_not_end_the_run(monkeypatch, tmp_path):
    """The workflow step runs under pipefail: an exception here skipped export, the commit, the
    snapshot and the Pages deploy, and the site stopped updating."""
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    ids = _seed(conn, 2)
    for i in range(2):
        store.save_body(store.url_hash("https://e.com/%d" % i), "Body %d" % i)
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version) "
                 "VALUES ('bx', '2026-09-16T00:00:00+00:00', 'submitted', '[]', 'm')")
    conn.commit()

    def boom(conn, client):
        raise RuntimeError("batch results expired")

    monkeypatch.setattr(cl, "_collect_batches", boom)
    counts = cl.run(conn, "t", client=cl.DryRunClient())
    assert "batch results expired" in counts["collect_error"]
    assert counts["classified"] == 2, "the rest of the stage still ran"
    assert conn.execute("SELECT ok FROM run_log WHERE stage='classify_llm'").fetchone()[0] == 1


def test_one_unreadable_batch_does_not_stop_the_others(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    ids = _seed(conn, 1)
    _submitted_batch(conn, ids)
    out = cl._collect_batches(conn, _batch_client([], retrieve_error=RuntimeError("503")))
    assert out["collect_errors"] and "503" in out["collect_errors"][0]
    assert conn.execute("SELECT status FROM llm_batches").fetchone()[0] == "submitted", "retried on a later run"


# ---------------------------------------------------------------------------
# Batches that will never be collected
# ---------------------------------------------------------------------------

def test_a_stale_batch_gives_its_articles_back(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    ids = _seed(conn, 2)
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=cl.BATCH_EXPIRY_DAYS + 1)).isoformat()
    _submitted_batch(conn, ids, day=old)
    assert cl.pending_articles(conn, 10) == [], "llm_submitted is invisible to the pending list"
    out = cl.expire_stale_batches(conn)
    assert out == {"batches_expired": 1, "articles_returned": 2, "orphans_returned": 0}
    assert conn.execute("SELECT status FROM llm_batches").fetchone()[0] == "failed"
    assert len(cl.pending_articles(conn, 10)) == 2


def test_a_fresh_batch_is_left_alone_and_orphans_come_back(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    ids = _seed(conn, 3)
    _submitted_batch(conn, ids[:2], day=dt.datetime.now(dt.timezone.utc).isoformat())
    store.update_article(conn, ids[2], status="llm_submitted")   # its batch row is gone
    conn.commit()
    out = cl.expire_stale_batches(conn)
    assert out == {"batches_expired": 0, "articles_returned": 0, "orphans_returned": 1}
    assert conn.execute("SELECT status FROM llm_batches").fetchone()[0] == "submitted"
    assert [r["id"] for r in cl.pending_articles(conn, 10)] == [ids[2]]
