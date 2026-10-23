"""Session labels under prompt 2026-10-17 carry issuer_type and state_basis, and name the model that made them.

A session label is stored through classify_llm.store_result exactly as an API reply is, so its state_basis
decides its route (BASIS_ROUTES) and its issuer_type is recorded and checked (inconsistency_of). The pass
may be run by a model other than the API's config.LLM_MODEL; its model_version names that model, is kept
as written and counts as made under the current prompt.
"""
import json
import sqlite3

from pipeline import classify_llm, config, labels, model_only, second_rater, store

OPUS = "claude-opus-5-5 (session, prompt 2026-10-17)"


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    return conn


def _article(conn, n, trigger=None):
    url = "https://e.test/fields/%d" % n
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": "ITA", "language": "it",
                                         "title": "Title %d" % n, "status": "awaiting_llm", "gate_relevant": 1})
    conn.execute("UPDATE articles SET llm_pending=1, llm_trigger=? WHERE id=?", (json.dumps(trigger or {}), aid))
    conn.commit()
    return aid, store.url_hash(url)


def _label(url_hash, category="A", drop=(), **kw):
    lab = {"url_hash": url_hash, "category": category, "confidence": 0.9,
           "evidence_quote": "BEIJING, Oct. 20 (Xinhua) -- The ministry said output rose.",
           "reasoning": "Wire copy run unaltered.", "china_sources_cited": ["Xinhua"],
           "independent_confirmation_present": False, "confirmation_evidence": None,
           "issuer_type": "state_media", "state_basis": "credit_line",
           "model_version": OPUS, "labelled_at": "2026-10-22T21:00:00Z", "source": "session"}
    lab.update(kw)
    for f in drop:
        lab.pop(f)
    return lab


def _write(tmp_path, lines, name="session-2026-10-22.jsonl"):
    d = tmp_path / "labels"
    d.mkdir(exist_ok=True)
    (d / name).write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    return d


def _current(conn, aid):
    row = store.current_classification(conn, aid)
    return dict(row) if row is not None else None


def _message(payload):
    class Block:
        type = "text"
        text = json.dumps(payload)

    class Msg:
        stop_reason = "end_turn"
        content = [Block()]
        usage = None
    return Msg()


def test_session_state_origin_with_a_credit_line_is_stored_as_wire_credit_with_its_issuer(tmp_path):
    conn = _db()
    aid, h = _article(conn, 1)
    counts = labels.ingest(conn, _write(tmp_path, [_label(h)]))
    assert counts["applied"] == 1 and counts["malformed"] == 0 and counts["inconsistent"] == 0
    cur = _current(conn, aid)
    assert (cur["category"], cur["route"], cur["issuer_type"], cur["needs_review"]) == \
        ("A", "wire_credit", "state_media", None)
    assert not model_only.is_undetermined(cur["category"], cur["route"], cur["method"])


def test_the_fields_are_handed_to_store_result_not_left_to_the_raw_line():
    lab = _label("h" * 32, issuer_type="sar_government", state_basis="issuer_named")
    lab["_raw"] = "{}"   # nothing to fall back on
    data = labels._data(lab)
    assert data["issuer_type"] == "sar_government" and data["state_basis"] == "issuer_named"
    old = labels._data(dict(_label("h" * 32, drop=("issuer_type", "state_basis")), _raw="{}"))
    assert old["issuer_type"] is None and old["state_basis"] is None, "as parse_response gives an old reply"


def test_issuer_named_gives_an_undetermined_state_origin(tmp_path):
    conn = _db()
    aid, h = _article(conn, 1)
    labels.ingest(conn, _write(tmp_path, [_label(h, issuer_type="government", state_basis="issuer_named")]))
    cur = _current(conn, aid)
    assert (cur["category"], cur["route"], cur["issuer_type"]) == ("A", "unattributed", "government")
    assert model_only.is_undetermined(cur["category"], cur["route"], cur["method"])


def test_every_basis_is_routed_as_the_api_path_routes_it(tmp_path):
    conn = _db()
    cases = [("credit_line", "state_media"), ("distribution_stamp", "state_media"),
             ("sponsored_disclosure", "government"), ("diplomatic_byline", "diplomatic"),
             ("diplomatic_byline", "state_media"), ("context_match", "state_media"), ("none", "party")]
    lines, ids = [], []
    for n, (basis, issuer) in enumerate(cases):
        aid, h = _article(conn, 100 + n)
        ids.append(aid)
        lines.append(_label(h, issuer_type=issuer, state_basis=basis))
    labels.ingest(conn, _write(tmp_path, lines))
    for n, (aid, (basis, issuer)) in enumerate(zip(ids, cases)):
        via_api, _ = _article(conn, 200 + n)
        reply = {k: v for k, v in lines[n].items() if k in classify_llm.OUTPUT_SCHEMA["properties"]}
        classify_llm.store_result(conn, via_api, classify_llm.parse_response(_message(reply)), OPUS, prompt_version=None)
        a, b = _current(conn, aid), _current(conn, via_api)
        assert (a["route"], a["issuer_type"], a["needs_review"]) == (b["route"], b["issuer_type"], b["needs_review"])
    assert [_current(conn, aid)["route"] for aid in ids] == [
        "wire_credit", "distribution_stamp", "sponsored_disclosure", "diplomatic_byline",
        "unattributed", "unattributed", "unattributed"]


def test_an_inconsistent_session_label_is_flagged_as_an_api_reply_is(tmp_path):
    conn = _db()
    a_none, h1 = _article(conn, 1)
    b_state, h2 = _article(conn, 2)
    counts = labels.ingest(conn, _write(tmp_path, [
        _label(h1, "A", issuer_type="none", state_basis="credit_line"),
        _label(h2, "B", issuer_type="state_media", state_basis="none")]))
    assert counts["applied"] == 2 and counts["inconsistent"] == 2
    first, second = _current(conn, a_none), _current(conn, b_state)
    # State origin with no state issuer: stored as independent journalism, no route, left for review.
    assert (first["category"], first["route"], first["needs_review"]) == \
        ("C", None, classify_llm.STATE_ORIGIN_WITHOUT_STATE_ISSUER)
    assert first["reasoning"].startswith("[inconsistent reply: state origin with issuer type none")
    # A state issuer on another category: the category is kept, never raised to A.
    assert (second["category"], second["needs_review"]) == ("B", classify_llm.STATE_ISSUER_WITHOUT_STATE_ORIGIN)


def test_a_current_prompt_label_without_the_fields_is_malformed(tmp_path):
    conn = _db()
    ids, lines = [], []
    for n, drop in enumerate((("issuer_type",), ("state_basis",), ("issuer_type", "state_basis"))):
        aid, h = _article(conn, n + 1)
        ids.append(aid)
        lines.append(_label(h, drop=drop))
    bad_issuer, h4 = _article(conn, 4)
    bad_basis, h5 = _article(conn, 5)
    lines += [_label(h4, issuer_type="ministry"), _label(h5, state_basis=None)]
    later, h6 = _article(conn, 6)
    lines.append(_label(h6, drop=("state_basis",), model_version="claude-opus-5-5 (session, prompt 2099-01-01)"))
    counts = labels.ingest(conn, _write(tmp_path, lines))
    assert counts["malformed"] == 6 and counts["applied"] == 0 and counts["older_prompt"] == 0
    assert "missing issuer_type, required from prompt 2026-10-17" in counts["malformed_samples"][0]
    assert "missing issuer_type, state_basis" in counts["malformed_samples"][2]
    assert "issuer_type 'ministry' is not one of" in counts["malformed_samples"][3]
    for aid in ids + [bad_issuer, bad_basis, later]:
        assert _current(conn, aid) is None
        art = conn.execute("SELECT status, llm_pending FROM articles WHERE id=?", (aid,)).fetchone()
        assert (art["status"], art["llm_pending"]) == ("awaiting_llm", 1), "left waiting, never a guessed route"


def test_an_older_prompt_label_without_the_fields_still_loads_as_before(tmp_path):
    conn = _db()
    untagged, h1 = _article(conn, 1)
    older, h2 = _article(conn, 2)
    lines = [_label(h1, "B", drop=("issuer_type", "state_basis"), model_version="claude-sonnet-5 (session)"),
             _label(h2, "A", drop=("issuer_type", "state_basis"),
                    model_version="claude-sonnet-5 (session, prompt 2026-10-12)")]
    for lab in lines:
        assert labels.validate(lab) is None
    d = _write(tmp_path, lines)
    loaded, stats = labels.load_files(d)
    assert len(loaded) == 2 and stats["malformed"] == 0
    counts = labels.ingest(conn, d)
    assert counts["malformed"] == 0 and counts["older_prompt"] == 2 and counts["applied"] == 0
    for aid in (untagged, older):
        assert _current(conn, aid) is None


def test_the_committed_older_label_files_load_as_before(tmp_path):
    for name in ("session-2026-09-30.jsonl", "session-2026-10-01.jsonl", "session-2026-10-04.jsonl"):
        (tmp_path / name).write_bytes((config.LABELS_DIR / name).read_bytes())
    loaded, stats = labels.load_files(tmp_path)
    assert stats["files"] == 3 and stats["malformed"] == 0 and len(loaded) == stats["lines"] > 0
    assert not any(classify_llm.on_current_prompt(l["model_version"]) for l in loaded)


def test_a_session_label_from_another_model_is_accepted_and_counted_current(tmp_path):
    assert config.LLM_MODEL != labels.model_of(OPUS)
    assert labels.validate(_label("a" * 32)) is None
    assert classify_llm.prompt_version_of(OPUS) == "2026-10-17" and classify_llm.on_current_prompt(OPUS)
    assert classify_llm.with_prompt_version(OPUS) == OPUS, "never tagged a second time"
    conn = _db()
    opus, h1 = _article(conn, 1)
    sonnet, h2 = _article(conn, 2)
    api, _ = _article(conn, 3)
    sonnet_mv = labels.session_model_version()
    counts = labels.ingest(conn, _write(tmp_path, [_label(h1, "C", issuer_type="none", state_basis="none"),
                                                   _label(h2, "C", issuer_type="none", state_basis="none",
                                                          model_version=sonnet_mv)]))
    assert counts["applied"] == 2 and counts["older_prompt"] == 0 and counts["malformed"] == 0
    assert _current(conn, opus)["model_version"] == OPUS, "stored as written"
    classify_llm.store_result(conn, api, {"category": "C", "confidence": 0.9, "issuer_type": "none",
                                          "state_basis": "none", "_raw": json.dumps({"category": "C"})},
                              config.LLM_MODEL)
    # Counted as a session label under its own model, never as an API label.
    assert labels.session_label_counts(conn) == {"total": 2, "by_model_version": {OPUS: 1, sonnet_mv: 1}}
    # Not queued for the API again: the export's labels_on_older_prompt reads the same test.
    older = [r[0] for r in conn.execute("SELECT model_version FROM classifications WHERE method='llm' AND is_current=1")
             if not classify_llm.on_current_prompt(r[0])]
    assert older == []
    # The stability check pairs the classifier model's own labels only, its session labels included.
    pool = {r["id"] for r in second_rater.judged_rows(conn, current_prompt=True)}
    assert pool == {sonnet, api}
    assert {r["id"] for r in second_rater.judged_rows(conn)} == {opus, sonnet, api}


def test_a_session_label_naming_only_the_api_model_is_still_malformed():
    assert "API model's own name" in labels.validate(_label("a" * 32, model_version=config.LLM_MODEL))
    assert "does not name the session" in labels.validate(
        _label("a" * 32, model_version=classify_llm.with_prompt_version("claude-opus-5-5")))


def test_model_of_strips_the_session_and_prompt_tags():
    assert labels.model_of(OPUS) == "claude-opus-5-5"
    assert labels.model_of("claude-sonnet-5 (prompt 2026-10-12)") == "claude-sonnet-5"
    assert labels.model_of("claude-sonnet-5") == "claude-sonnet-5"
    assert labels.model_of(None) is None and labels.model_of(" ") is None


def test_export_prompt_names_the_model_that_makes_the_labels(tmp_path):
    info = labels.export_prompt(tmp_path, model="claude-opus-5-5")
    assert info["model_version"] == "claude-opus-5-5 (session, prompt %s)" % classify_llm.PROMPT_VERSION
    assert info["model_version"] in (tmp_path / labels.PROMPT_FILE).read_text(encoding="utf-8")
    assert labels.validate(_label("a" * 32, model_version=info["model_version"])) is None
    assert labels.export_prompt(tmp_path)["model_version"] == labels.session_model_version()
    schema = json.loads((tmp_path / labels.SCHEMA_FILE).read_text(encoding="utf-8"))
    assert set(labels.SCHEMA_FIELDS) <= set(schema["required"])


def test_main_export_prompt_takes_the_model(tmp_path, capsys):
    assert labels.main(["export-prompt", "--out", str(tmp_path), "--model", "claude-opus-5-5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["model_version"] == "claude-opus-5-5 (session, prompt %s)" % classify_llm.PROMPT_VERSION
