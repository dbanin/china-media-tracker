"""Session labels: applied once, stored exactly as an API reply is, everything else counted and skipped."""
import json
import sqlite3

from pipeline import classify_llm, config, labels, store

SESSION = "claude-sonnet-5 (session)"


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
    classify_llm.store_result(conn, via_api, data, SESSION)
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
    assert set(out) >= {"files", "lines", "malformed", "applied", "already_classified", "not_awaiting", "unknown", "by_category"}
