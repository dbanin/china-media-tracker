"""Session labels: applied once, stored exactly as an API reply is, everything else counted and skipped."""
import json
import sqlite3

from pipeline import classify_llm, config, labels, store

SESSION = "claude-sonnet-5 (session, prompt %s)" % classify_llm.PROMPT_VERSION


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    return conn


def _article(conn, n, status="awaiting_llm", trigger=None):
    url = "https://e.test/%d" % n
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": "ITA", "language": "it",
                                         "title": "Title %d" % n, "status": status, "gate_relevant": 1})
    conn.execute("UPDATE articles SET llm_pending=1, llm_trigger=? WHERE id=?", (json.dumps(trigger or {}), aid))
    conn.commit()
    return aid, store.url_hash(url)


def _label(url_hash, category="B", **kw):
    lab = {"url_hash": url_hash, "category": category, "confidence": 0.82,
           "evidence_quote": "The ministry said the launch was a success.", "reasoning": "Relayed without a check.",
           "china_sources_cited": ["China Manned Space Agency"], "independent_confirmation_present": False,
           "confirmation_evidence": None, "model_version": SESSION, "labelled_at": "2026-09-30T10:00:00Z",
           "source": "session"}
    lab.update(kw)
    return lab


def _write(tmp_path, lines, name="2026-09-30.jsonl"):
    d = tmp_path / "labels"
    d.mkdir(exist_ok=True)
    (d / name).write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    return d


def _row(conn, aid):
    return dict(conn.execute("SELECT * FROM classifications WHERE article_id=? AND is_current=1", (aid,)).fetchone())


def test_label_applies_exactly_as_the_api_path(tmp_path):
    conn = _db()
    trig = {"a_candidate": ["xinhua_dateline"]}
    via_file, h1 = _article(conn, 1, trigger=trig)
    via_api, _ = _article(conn, 2, trigger=trig)
    lab_b = _label(h1, "B")
    d = _write(tmp_path, [lab_b])
    counts = labels.ingest(conn, d)
    assert counts["applied"] == 1 and counts["by_category"] == {"B": 1}
    # The same data handed to store_result directly, as classify_llm.run does after an API reply.
    data = {k: lab_b[k] for k in ("category", "confidence", "evidence_quote", "reasoning", "china_sources_cited",
                                  "independent_confirmation_present", "confirmation_evidence")}
    data["_raw"] = json.dumps(lab_b)
    # prompt_version=None, as labels.ingest passes: a session label names its own prompt or none.
    classify_llm.store_result(conn, via_api, data, SESSION, prompt_version=None)
    a, b = _row(conn, via_file), _row(conn, via_api)
    for k in ("id", "article_id", "classified_at"):
        a.pop(k), b.pop(k)
    assert a == b
    assert a["method"] == "llm" and a["model_version"] == SESSION and a["route"] is None
    assert json.loads(a["raw_response"]) == lab_b
    for aid in (via_file, via_api):
        art = conn.execute("SELECT status, llm_pending FROM articles WHERE id=?", (aid,)).fetchone()
        assert (art["status"], art["llm_pending"]) == ("classified", 0)
    run = conn.execute("SELECT * FROM run_log WHERE stage='labels_ingest'").fetchone()
    assert run["ok"] == 1 and json.loads(run["counts"])["applied"] == 1


def test_state_origin_label_gets_the_model_route(tmp_path):
    conn = _db()
    aid, h = _article(conn, 1, trigger={"a_candidate": ["xinhua_dateline"]})
    labels.ingest(conn, _write(tmp_path, [_label(h, "A")]))
    assert _row(conn, aid)["route"] == classify_llm._model_route(conn, aid)


def test_second_ingest_applies_nothing(tmp_path):
    conn = _db()
    aid, h = _article(conn, 1)
    d = _write(tmp_path, [_label(h)])
    assert labels.ingest(conn, d)["applied"] == 1
    again = labels.ingest(conn, d)
    assert again["applied"] == 0 and again["already_classified"] == 1
    assert conn.execute("SELECT COUNT(*) FROM classifications WHERE article_id=?", (aid,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM run_log WHERE stage='labels_ingest'").fetchone()[0] == 2


def test_already_classified_and_not_awaiting_are_skipped(tmp_path):
    conn = _db()
    done, h_done = _article(conn, 1)
    store.insert_classification(conn, done, "rules", "A", 1.0, signatures_fired=["xinhua_dateline"])
    # A current label wins even if the status says the article is still waiting.
    conn.execute("UPDATE articles SET status='awaiting_llm' WHERE id=?", (done,))
    gated, h_gated = _article(conn, 2, status="gated_out")
    submitted, h_sub = _article(conn, 3, status="llm_submitted")
    counts = labels.ingest(conn, _write(tmp_path, [_label(h_done, "C"), _label(h_gated), _label(h_sub)]))
    assert counts["applied"] == 0
    assert counts["already_classified"] == 1 and counts["not_awaiting"] == 2
    assert _row(conn, done)["method"] == "rules" and _row(conn, done)["category"] == "A"
    assert conn.execute("SELECT status FROM articles WHERE id=?", (gated,)).fetchone()[0] == "gated_out"


def test_malformed_lines_counted_and_skipped(tmp_path):
    conn = _db()
    aid, h = _article(conn, 1)
    _, h2 = _article(conn, 2)
    _, h3 = _article(conn, 3)
    lines = [
        "{not json",
        _label(h2, "D"),                                   # bad category
        _label(h3, model_version=config.LLM_MODEL),        # indistinguishable from an API label
        "",                                                # blank lines are not lines
        _label(h),
    ]
    counts = labels.ingest(conn, _write(tmp_path, lines))
    assert counts["files"] == 1 and counts["lines"] == 4
    assert counts["malformed"] == 3 and len(counts["malformed_samples"]) == 3
    assert counts["applied"] == 1 and _row(conn, aid)["category"] == "B"


def test_unknown_url_hash_counted(tmp_path):
    conn = _db()
    counts = labels.ingest(conn, _write(tmp_path, [_label("0" * 32)]))
    assert counts["unknown"] == 1 and counts["applied"] == 0


def test_missing_directory_is_not_an_error(tmp_path):
    conn = _db()
    counts = labels.ingest(conn, tmp_path / "absent")
    assert counts["files"] == 0 and counts["applied"] == 0


def test_dup_siblings_settled_but_own_labels_win(tmp_path):
    conn = _db()
    first, h1 = _article(conn, 1)
    second, h2 = _article(conn, 2)
    third, _ = _article(conn, 3)
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id IN (?,?,?)", (first, first, second, third))
    counts = labels.ingest(conn, _write(tmp_path, [_label(h1, "B"), _label(h2, "C")]))
    assert counts["applied"] == 2 and counts["copied"] == 1
    assert _row(conn, second)["category"] == "C"
    assert _row(conn, third)["category"] in ("B", "C") and _row(conn, third)["model_version"] == SESSION


def test_session_label_counts(tmp_path):
    conn = _db()
    aid, h = _article(conn, 1)
    api, _ = _article(conn, 2)
    labels.ingest(conn, _write(tmp_path, [_label(h)]))
    classify_llm.store_result(conn, api, {"category": "C", "confidence": 0.9, "_raw": json.dumps({"category": "C"})}, config.LLM_MODEL)
    assert labels.session_label_counts(conn) == {"total": 1, "by_model_version": {SESSION: 1}}


def test_main_prints_counts(tmp_path, monkeypatch, capsys):
    db = tmp_path / "t.db"
    conn = store.connect(db)
    _, h = _article(conn, 1)
    monkeypatch.setattr(store, "connect", lambda *a, **k: conn)
    labels.main(["ingest", "--dir", str(_write(tmp_path, [_label(h)]))])
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] == 1
    assert set(out) >= {"files", "lines", "malformed", "applied", "already_classified", "not_awaiting", "unknown",
                        "older_prompt", "by_category"}


def test_api_first_loader_skips_malformed_lines(tmp_path):
    good = {"url_hash": "a" * 32, "reason": "HTTP 403", "listed_at": "2026-10-04", "source": "session"}
    lines = [
        good,
        "{not json",
        {"url_hash": "b" * 32, "reason": "robots.txt", "source": "session"},          # no listed_at
        dict(good, url_hash="c" * 32, listed_at="yesterday"),                           # not an ISO date
        "",
        dict(good, url_hash=" " + "d" * 32 + " ", reason="paywall", listed_at="2026-10-04T08:00:00Z"),
    ]
    d = _write(tmp_path, lines, name=labels.API_FIRST_FILE)
    stats = {}
    assert labels.load_api_first(d, stats=stats) == {"a" * 32: "HTTP 403", "d" * 32: "paywall"}
    assert stats["lines"] == 5 and stats["malformed"] == 3 and len(stats["malformed_samples"]) == 3
    assert labels.load_api_first(tmp_path / "absent") == {}


def test_api_first_file_is_never_ingested_as_labels(tmp_path):
    conn = _db()
    _, h = _article(conn, 1)
    d = _write(tmp_path, [{"url_hash": h, "reason": "HTTP 403", "listed_at": "2026-10-04", "source": "session"}],
               name=labels.API_FIRST_FILE)
    counts = labels.ingest(conn, d)
    assert counts["files"] == 0 and counts["lines"] == 0 and counts["malformed"] == 0 and counts["applied"] == 0


# ---------------------------------------------------------------------------
# Session labels under the current prompt
# ---------------------------------------------------------------------------

def test_session_model_version_names_session_and_current_prompt():
    mv = labels.session_model_version()
    assert mv == "%s (session, prompt %s)" % (config.LLM_MODEL, classify_llm.PROMPT_VERSION)
    # The session marker and the prompt tag are read together by the same parser the relabel queue uses.
    assert classify_llm.prompt_version_of(mv) == classify_llm.PROMPT_VERSION
    assert classify_llm.on_current_prompt(mv) and labels.validate(_label("a" * 32, model_version=mv)) is None
    # Stored unchanged: a label that names its prompt is not tagged a second time.
    assert classify_llm.with_prompt_version(mv) == mv


def test_model_version_must_name_the_session(tmp_path):
    conn = _db()
    aid, h = _article(conn, 1)
    _, h2 = _article(conn, 2)
    api_like = classify_llm.with_prompt_version(config.LLM_MODEL)    # what an API label carries
    counts = labels.ingest(conn, _write(tmp_path, [_label(h2, model_version=api_like), _label(h)]))
    assert counts["malformed"] == 1 and "does not name the session" in counts["malformed_samples"][0]
    assert counts["applied"] == 1 and _row(conn, aid)["model_version"] == SESSION


def test_older_prompt_session_labels_counted_and_skipped(tmp_path):
    conn = _db()
    current, h1 = _article(conn, 1)
    untagged, h2 = _article(conn, 2)
    older, h3 = _article(conn, 3)
    lines = [_label(h1), _label(h2, model_version="claude-sonnet-5 (session)"),
             _label(h3, model_version="claude-sonnet-5 (session, prompt 2026-01-01)")]
    d = _write(tmp_path, lines)
    counts = labels.ingest(conn, d)
    assert counts["applied"] == 1 and counts["older_prompt"] == 2 and counts["malformed"] == 0
    assert _row(conn, current)["model_version"] == SESSION
    for aid in (untagged, older):
        art = conn.execute("SELECT status, llm_pending FROM articles WHERE id=?", (aid,)).fetchone()
        assert (art["status"], art["llm_pending"]) == ("awaiting_llm", 1), "left in the queue for the current prompt"
        assert store.current_classification(conn, aid) is None
    # Not fatal, and a second ingest counts them the same way without inserting anything.
    again = labels.ingest(conn, d)
    assert again["older_prompt"] == 2 and again["already_classified"] == 1 and again["applied"] == 0
    assert conn.execute("SELECT ok FROM run_log WHERE stage='labels_ingest'").fetchall()[-1][0] == 1


def test_older_prompt_is_only_counted_for_a_label_that_would_apply(tmp_path):
    conn = _db()
    done, h = _article(conn, 1)
    store.insert_classification(conn, done, "rules", "A", 1.0, signatures_fired=["xinhua_dateline"])
    counts = labels.ingest(conn, _write(tmp_path, [_label(h, model_version="claude-sonnet-5 (session)"),
                                                   _label("0" * 32, model_version="claude-sonnet-5 (session)")]))
    assert counts["already_classified"] == 1 and counts["unknown"] == 1 and counts["older_prompt"] == 0


def test_prompt_bump_makes_yesterdays_session_labels_older(tmp_path, monkeypatch):
    conn = _db()
    aid, h = _article(conn, 1)
    monkeypatch.setattr(classify_llm, "PROMPT_VERSION", "2099-01-01")
    counts = labels.ingest(conn, _write(tmp_path, [_label(h)]))    # tagged with the 2026-10-12 prompt
    assert counts["older_prompt"] == 1 and counts["applied"] == 0
    assert labels.session_model_version() == "%s (session, prompt 2099-01-01)" % config.LLM_MODEL


def test_export_prompt_writes_the_live_prompt_and_schema(tmp_path):
    out = tmp_path / "pass" / "prompt"
    info = labels.export_prompt(out)
    version = classify_llm.PROMPT_VERSION
    assert info["prompt_version"] == version and info["model_version"] == labels.session_model_version()
    text = (out / labels.PROMPT_FILE).read_text(encoding="utf-8")
    header, prompt = text.split("\n---\n\n", 1)
    assert prompt == classify_llm.SYSTEM_PROMPT, "the prompt is copied unchanged"
    assert header.startswith("# Classifier system prompt, prompt version %s\n" % version)
    assert "prompt_version: %s" % version in header and labels.session_model_version() in header
    assert info["system_prompt_sha256"] in header
    schema = json.loads((out / labels.SCHEMA_FILE).read_text(encoding="utf-8"))
    comment = schema.pop("$comment")
    assert schema == classify_llm.OUTPUT_SCHEMA
    assert "prompt version %s" % version in comment and info["output_schema_sha256"] in comment


def test_export_prompt_follows_the_code_not_an_old_copy(tmp_path, monkeypatch):
    labels.export_prompt(tmp_path)
    monkeypatch.setattr(classify_llm, "PROMPT_VERSION", "2099-01-01")
    monkeypatch.setattr(classify_llm, "SYSTEM_PROMPT", "A changed prompt.")
    info = labels.export_prompt(tmp_path)
    text = (tmp_path / labels.PROMPT_FILE).read_text(encoding="utf-8")
    assert text.endswith("\n---\n\nA changed prompt.") and "prompt version 2099-01-01" in text
    assert info["model_version"].endswith("(session, prompt 2099-01-01)")


def test_main_export_prompt_needs_no_database(tmp_path, monkeypatch, capsys):
    def no_db(*a, **k):
        raise AssertionError("export-prompt must not open the database")
    monkeypatch.setattr(store, "connect", no_db)
    assert labels.main(["export-prompt", "--out", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["prompt_version"] == classify_llm.PROMPT_VERSION
    assert (tmp_path / labels.PROMPT_FILE).is_file() and (tmp_path / labels.SCHEMA_FILE).is_file()
    try:
        labels.main(["export-prompt"])
        raise AssertionError("expected a usage error")
    except SystemExit as exc:
        assert exc.code == 2
