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


def test_parse_response_rejects_bad_category():
    class B: type = "text"; text = '{"category": "D"}'
    class M: stop_reason = "end_turn"; content = [B()]; usage = None
    assert cl.parse_response(M())["_error"] == "bad_category"
    class R: stop_reason = "refusal"; content = []
    assert cl.parse_response(R())["_error"] == "refusal"
