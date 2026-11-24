"""The paper's six attributes on every model label from prompt 2026-12-01 (owner decision D16 of 2026-11-24).

The model returns author, commissioner, state affiliation, route, disclosure and verification from the hand
coding's value sets, with short spans for those that rest on the text; the label stores them, the exports carry
them, the category the paper's rule derives from them is checked against the category the model returned, and
pipeline.agreement compares them with hand codes one attribute at a time. Moving PROMPT_VERSION to 2026-12-01
queues no stored label: that prompt extends 2026-10-17 (classify_llm.PROMPT_EXTENDS)."""
import csv
import json
import re
import sqlite3

from pipeline import agreement, classify_llm as cl, config, export, labels, methodology, model_only, second_rater, store
from tests.test_classify_llm import _RecordingBatchApi, _batch_client, _message

OLD = "claude-sonnet-5"
NEW_PROMPT = "2026-12-01"
ATTRS = {"author": "newsroom", "commissioner": "none", "state_affiliation": "no", "route": "ordinary_reporting",
         "disclosure": "none", "verification": "merely_attributed", "author_evidence": None,
         "commissioner_evidence": None, "disclosure_evidence": None,
         "central_claim": "Exports rose 8.7 percent in September, the commerce ministry said."}


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    return conn


def _article(conn, n, status="awaiting_llm", body=True, dup=None):
    url = "https://e.com/s%d" % n
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": "ITA", "language": "it",
                                         "title": "Title %d" % n, "status": status, "gate_relevant": 1})
    if body:
        store.save_body(store.url_hash(url), "Body about China %d" % n)
    if dup:
        conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (dup, aid))
    return aid


def _reply(category="B", issuer_type="none", state_basis="none", **attrs):
    data = {"category": category, "confidence": 0.8, "evidence_quote": "q", "reasoning": "r",
            "china_sources_cited": ["MOFCOM"], "independent_confirmation_present": False,
            "confirmation_evidence": None, "issuer_type": issuer_type, "state_basis": state_basis}
    data.update(ATTRS)
    data.update(attrs)
    return data


def _labelled(conn, n, prompt, when="2026-10-20T00:00:00+00:00", category="B", **kw):
    """A classified article whose current model label was made under `prompt` (None: no prompt tag)."""
    aid = _article(conn, n, status="classified", **kw)
    store.insert_classification(conn, aid, "llm", category, 0.7, evidence_quote="q",
                                model_version=cl.with_prompt_version(OLD, prompt) if prompt else OLD)
    conn.execute("UPDATE classifications SET classified_at=? WHERE article_id=?", (when, aid))
    conn.commit()
    return aid


# ---------------------------------------------------------------------------
# Value sets, schema and prompt
# ---------------------------------------------------------------------------

def test_value_sets_are_the_hand_codings():
    # _recode_brief.md section "The six attributes", plus issuer_nonstate and soe from CODING_NOTES.md.
    assert cl.ATTRIBUTE_VALUES == {
        "author": ("state", "soe", "newsroom", "studio", "agency", "issuer_nonstate", "unknown"),
        "commissioner": ("state", "soe", "publisher", "issuer_nonstate", "none", "unknown"),
        "state_affiliation": ("yes", "no", "uncertain"),
        "route": ("agency_syndication", "paid_release_wire", "sponsored_placement", "signed_contribution",
                  "ordinary_reporting", "other_uncertain"),
        "disclosure": ("names_state_source", "names_wire_or_partner_only", "none"),
        "verification": ("corroborated", "contested", "contextualized", "merely_attributed", "unassessed",
                         "no_official_claim"),
    }
    assert set(cl.PAPER_COLUMNS.values()) == {"AUTHOR", "COMMISSIONER", "STATE_AFFIL", "ROUTE", "DISCLOSURE", "VERIFICATION"}


def test_schema_requires_the_attributes_first_and_keeps_every_older_field():
    schema = cl.OUTPUT_SCHEMA
    req = schema["required"]
    assert req[:10] == list(cl.ATTRIBUTES) + list(cl.ATTRIBUTE_EVIDENCE)
    for f in ("category", "confidence", "evidence_quote", "reasoning", "china_sources_cited",
              "independent_confirmation_present", "confirmation_evidence", "issuer_type", "state_basis"):
        assert f in req and f in schema["properties"]
    assert list(schema["properties"])[:10] == req[:10], "the model writes the attributes before the category"
    for a, values in cl.ATTRIBUTE_VALUES.items():
        assert schema["properties"][a] == {"type": "string", "enum": list(values)}
    for k in cl.ATTRIBUTE_EVIDENCE:
        assert schema["properties"][k] == {"type": ["string", "null"]}
    assert schema["additionalProperties"] is False and set(schema["properties"]) == set(req)


def test_prompt_defines_the_attributes_and_reads_route_and_disclosure_from_the_text_alone():
    p = cl.SYSTEM_PROMPT
    assert cl.ATTRIBUTE_DEFINITIONS in p and cl.CATEGORY_DEFINITIONS in p
    assert p.index(cl.ATTRIBUTE_DEFINITIONS) < p.index("Return a JSON object")
    assert "You see the headline and the text only, never the outlet, the country or the page address." in p
    assert "Record route and disclosure from what the headline and the text show" in p
    assert "Do not change an attribute to fit the category you chose" in p
    for a, values in cl.ATTRIBUTE_VALUES.items():
        for v in values:
            assert '"%s"' % v in cl.ATTRIBUTE_DEFINITIONS, (a, v)
    assert "–" not in p and "—" not in p
    # The user message is unchanged: headline and text only.
    msg = cl.request_params("Headline", "Body text")["messages"][0]["content"]
    for leak in ("outlet", "country", "http", "url"):
        assert leak not in msg


def test_every_worked_answer_carries_attributes_that_fit_its_category():
    p = cl.SYSTEM_PROMPT
    chunks = p.split("Answer: ")
    answers = [json.loads(c.split("\n")[0]) for c in chunks[1:]]
    assert len(answers) == 11
    for n, a in enumerate(answers, 1):
        assert set(a) == set(cl.OUTPUT_SCHEMA["required"]), n
        assert cl.attribute_disagreement(a) is None and cl.inconsistency_of(a) is None, n
        example = chunks[n - 1][chunks[n - 1].rfind("Example"):]
        for k in cl.ATTRIBUTE_EVIDENCE:
            if a[k] is not None:
                assert a[k] in example, (n, k)
                assert len(a[k].split()) <= 25, (n, k)
    derived = [cl.derived_category(cl.attributes_of(a)) for a in answers]
    assert derived == ["A", "C", "C", "C", "B", "C", "C", "S", "A", "C", "A"]
    assert answers[7]["author"] == "soe" and answers[7]["issuer_type"] == "state_owned_enterprise"


# ---------------------------------------------------------------------------
# The paper's rule and the check
# ---------------------------------------------------------------------------

def test_derived_category_is_the_papers_rule():
    d = lambda **kw: cl.derived_category(dict({"author": "newsroom", "commissioner": "none",
                                               "verification": "no_official_claim"}, **kw))
    assert d(author="state") == "A" and d(commissioner="state", author="studio") == "A"
    assert d(author="state", commissioner="soe") == "A", "state comes before soe"
    assert d(author="soe", commissioner="soe") == "S"
    assert d(commissioner="unknown") == "?" and d(author="unknown", verification="merely_attributed") == "?"
    assert d(verification="merely_attributed") == "B" and d(verification="unassessed") == "B"
    for v in ("corroborated", "contested", "contextualized", "no_official_claim"):
        assert d(verification=v) == "C"
    assert d(author="agency", verification="merely_attributed") == "B"
    assert cl.derived_category(None) is None and cl.derived_category({"author": "state"}) is None


def test_the_check_matches_derived_and_returned_categories():
    flag = lambda cat, issuer="none", **kw: cl.attribute_disagreement(_reply(cat, issuer_type=issuer, **kw))
    assert flag("B") is None
    assert flag("C") == cl.CATEGORY_DIFFERS_FROM_ATTRIBUTES, "unchecked by the attributes, independent by the category"
    assert flag("C", verification="contextualized") is None
    assert flag("not_relevant", verification="no_official_claim") is None
    assert flag("not_relevant") == cl.CATEGORY_DIFFERS_FROM_ATTRIBUTES
    assert flag("A", "state_media", author="state") is None
    assert flag("C", "none", author="state") == cl.CATEGORY_DIFFERS_FROM_ATTRIBUTES
    assert flag("A", "state_media", author="issuer_nonstate", commissioner="issuer_nonstate",
                verification="no_official_claim") == cl.CATEGORY_DIFFERS_FROM_ATTRIBUTES
    # soe: a state-owned enterprise is state origin by the issuer rule; a state university is not.
    soe = dict(author="soe", commissioner="soe", verification="no_official_claim")
    assert flag("A", "state_owned_enterprise", **soe) is None
    assert flag("not_relevant", "private_release", **soe) is None
    assert flag("A", "state_media", **soe) == cl.CATEGORY_DIFFERS_FROM_ATTRIBUTES
    assert flag("B", "none", **soe) == cl.CATEGORY_DIFFERS_FROM_ATTRIBUTES
    assert flag("C", "none", commissioner="unknown") == cl.ATTRIBUTES_LEAVE_AUTHORSHIP_OPEN
    # A reply from before prompt 2026-12-01, or one missing a deciding attribute, is never flagged.
    old = {k: v for k, v in _reply("C").items() if k not in cl.ATTRIBUTE_VALUES}
    assert cl.attribute_disagreement(old) is None and cl.attributes_of(old) is None
    assert cl.attribute_disagreement(_reply("C", verification="sometimes")) is None


def test_attributes_are_read_from_a_session_line_and_bad_values_are_dropped():
    line = dict(_reply("B"), route="by pigeon")
    attrs = cl.attributes_of({"category": "B", "_raw": json.dumps(line)})
    assert attrs["author"] == "newsroom" and attrs["route"] is None
    assert attrs["evidence"]["central_claim"] == ATTRS["central_claim"] and attrs["evidence"]["author_evidence"] is None


# ---------------------------------------------------------------------------
# Storing, copying, migrating
# ---------------------------------------------------------------------------

def test_store_result_keeps_the_attributes_and_flags_a_disagreement(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    good, bad = _article(conn, 1), _article(conn, 2)
    assert cl.store_result(conn, good, _reply("B"), OLD) is None
    row = store.current_classification(conn, good)
    assert [row[c] for c in cl.ATTRIBUTE_COLUMNS.values()] == [
        "newsroom", "none", "no", "ordinary_reporting", "none", "merely_attributed"]
    assert json.loads(row["attr_evidence"])["central_claim"] == ATTRS["central_claim"]
    assert row["needs_review"] is None and cl.attributes_of_row(row)["verification"] == "merely_attributed"
    assert cl.store_result(conn, bad, _reply("C"), OLD) == cl.CATEGORY_DIFFERS_FROM_ATTRIBUTES
    row = store.current_classification(conn, bad)
    assert row["category"] == "C", "the category is kept as returned"
    assert row["needs_review"] == cl.CATEGORY_DIFFERS_FROM_ATTRIBUTES
    assert row["reasoning"].startswith("[attributes give B by the paper's rule, category C kept for review]")
    # An issuer inconsistency comes first; the attributes are still stored.
    both = _article(conn, 3)
    assert cl.store_result(conn, both, _reply("A", "none", author="newsroom"), OLD) == cl.STATE_ORIGIN_WITHOUT_STATE_ISSUER
    row = store.current_classification(conn, both)
    assert row["category"] == "C" and row["attr_author"] == "newsroom"
    # A reply without attributes stores none.
    legacy = _article(conn, 4)
    cl.store_result(conn, legacy, {k: v for k, v in _reply("B").items() if k not in ATTRS}, OLD)
    row = store.current_classification(conn, legacy)
    assert all(row[c] is None for c in cl.ATTRIBUTE_COLUMNS.values()) and row["attr_evidence"] is None
    assert export.inconsistent_labels(conn)["by_reason"] == {cl.CATEGORY_DIFFERS_FROM_ATTRIBUTES: 1,
                                                             cl.STATE_ORIGIN_WITHOUT_STATE_ISSUER: 1}


def test_the_run_counts_attribute_reviews_apart_and_a_copy_keeps_the_attributes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    head = _article(conn, 1)
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (head, head))
    sib = _article(conn, 2, dup=head)
    counts = cl.run(conn, "t", client=cl.DryRunClient(answer=lambda body: "B"))
    assert counts["calls"] == 1 and counts["copied"] == 1 and not counts.get("attribute_review")
    a, b = store.current_classification(conn, head), store.current_classification(conn, sib)
    assert a["attr_verification"] == b["attr_verification"] == "merely_attributed"
    assert a["attr_evidence"] == b["attr_evidence"]


def test_the_columns_are_added_to_an_older_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    old_schema = re.sub(r"    -- model labels from prompt 2026-12-01 only.*?attr_evidence TEXT,\n", "", store.SCHEMA, flags=re.S)
    assert "attr_author" not in old_schema
    conn.executescript(old_schema)
    conn.close()
    conn = store.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(classifications)")}
    assert set(cl.ATTRIBUTE_COLUMNS.values()) | {"attr_evidence"} <= cols
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    assert "classifications.attr_author" in done and "classifications.attr_evidence" in done
    conn.close()
    store.connect(path).close()     # a second connection finds them and changes nothing


# ---------------------------------------------------------------------------
# D16: the prompt change queues no stored label
# ---------------------------------------------------------------------------

def _bump(monkeypatch, version=NEW_PROMPT):
    """What the orchestrator's edit of PROMPT_VERSION does: the constant, and the defaults bound from it."""
    monkeypatch.setattr(cl, "PROMPT_VERSION", version)
    monkeypatch.setattr(cl.with_prompt_version, "__defaults__", (version,))
    monkeypatch.setattr(cl.store_result, "__defaults__", (version,))


def _mixed(conn):
    """Labels as the snapshot of 2026-11-24 holds them: none tagged, 2026-10-12 and 2026-10-17."""
    ids = {"untagged": _labelled(conn, 1, None, "2026-09-20T00:00:00+00:00"),
           "p1012": _labelled(conn, 2, "2026-10-12", "2026-10-13T00:00:00+00:00"),
           "p1017a": _labelled(conn, 3, "2026-10-17", "2026-10-18T00:00:00+00:00"),
           "p1017b": _labelled(conn, 4, "2026-10-17", "2026-11-12T00:00:00+00:00", category="A")}
    return ids


def test_the_prompt_change_queues_no_stored_label(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    ids = _mixed(conn)
    assert cl.PROMPT_EXTENDS[NEW_PROMPT] == "2026-10-17"
    _bump(monkeypatch, "2026-10-17")
    before = [r["id"] for r in cl.relabel_candidates(conn)]
    _bump(monkeypatch)
    after = [r["id"] for r in cl.relabel_candidates(conn)]
    assert after == before == [ids["untagged"], ids["p1012"]], "the queue the 2026-10-17 change made, and nothing more"
    for k in ("p1017a", "p1017b"):
        mv = store.current_classification(conn, ids[k])["model_version"]
        assert cl.on_current_prompt(mv) and not cl.on_latest_prompt(mv)
    assert cl.on_current_prompt(cl.with_prompt_version(OLD)) and cl.prompt_version_of(cl.with_prompt_version(OLD)) == NEW_PROMPT
    # A prompt change that does alter the categories still queues every older label, as before.
    _bump(monkeypatch, "2099-01-01")
    assert {r["id"] for r in cl.relabel_candidates(conn)} == set(ids.values())


def test_after_the_change_a_run_judges_new_articles_and_leaves_stored_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    _bump(monkeypatch)
    monkeypatch.setattr(cl, "relabel_allowance", lambda ceiling, used, now, share=None: 100)
    conn = _db()
    ids = _mixed(conn)
    fresh = _article(conn, 9)
    before = {k: store.current_classification(conn, v)["id"] for k, v in ids.items()}
    counts = cl.run(conn, "t", client=cl.DryRunClient(answer=lambda body: "C"))
    assert counts["classified"] == 1 and counts["relabel_eligible"] == 2 and counts["relabel_sent"] == 2
    for k in ("p1017a", "p1017b"):
        assert store.current_classification(conn, ids[k])["id"] == before[k], "a 2026-10-17 label is not judged again"
    row = store.current_classification(conn, fresh)
    assert cl.prompt_version_of(row["model_version"]) == NEW_PROMPT and row["attr_author"] == "newsroom"
    assert cl.relabel_candidates(conn) == []


def test_batch_mode_after_the_change_submits_no_stored_label_and_collects_both_shapes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(cl, "relabel_allowance", lambda ceiling, used, now, share=None: 100)
    conn = _db()
    ids = _mixed(conn)
    # A batch submitted under 2026-10-17, still out when the prompt changes.
    late = _article(conn, 8)
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version, kind) "
                 "VALUES ('old', '2026-11-30T23:00:00+00:00', 'submitted', ?, ?, 'classify')",
                 (json.dumps([late]), cl.with_prompt_version(OLD, "2026-10-17")))
    store.update_article(conn, late, status="llm_submitted")
    conn.commit()
    _bump(monkeypatch)
    fresh = _article(conn, 9)
    recorder = _RecordingBatchApi()
    client = type("C", (), {"messages": type("M", (), {"batches": recorder})()})()
    monkeypatch.setattr(cl, "_collect_batches", lambda conn, client: {})    # collected below, by hand
    counts = cl.run(conn, "t", client=client, batch=True)
    submitted = {int(r["custom_id"]) for r in recorder.created[0]}
    assert submitted == {fresh, ids["untagged"], ids["p1012"]} and counts["relabel_sent"] == 2
    params = recorder.created[0][0]["params"]
    assert params["output_config"]["format"]["schema"] == cl.OUTPUT_SCHEMA
    new_batch = conn.execute("SELECT model_version FROM llm_batches WHERE batch_id='b1'").fetchone()[0]
    assert cl.prompt_version_of(new_batch) == NEW_PROMPT
    monkeypatch.undo()
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    _bump(monkeypatch)
    old_reply = {k: v for k, v in _reply("C", verification="no_official_claim").items() if k not in ATTRS}
    conn.execute("UPDATE llm_batches SET status='collected' WHERE batch_id='b1'")
    out = cl._collect_batches(conn, _batch_client([(late, "succeeded", _message("end_turn", json.dumps(old_reply)))]))
    assert out["batch_collected"] == 1
    row = store.current_classification(conn, late)
    assert cl.prompt_version_of(row["model_version"]) == "2026-10-17" and row["attr_author"] is None
    assert late not in {r["id"] for r in cl.relabel_candidates(conn)}, "collected after the change, not queued"
    conn.execute("UPDATE llm_batches SET status='submitted' WHERE batch_id='b1'")
    out = cl._collect_batches(conn, _batch_client([(fresh, "succeeded", _message("end_turn", json.dumps(_reply("C"))))]))
    row = store.current_classification(conn, fresh)
    assert out["attribute_review"] == 1 and row["needs_review"] == cl.CATEGORY_DIFFERS_FROM_ATTRIBUTES
    assert cl.prompt_version_of(row["model_version"]) == NEW_PROMPT and row["attr_verification"] == "merely_attributed"


def test_staleness_follows_the_categories_not_the_prompt_date(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    _mixed(conn)
    older = lambda: sum(1 for r in conn.execute("SELECT model_version FROM classifications WHERE method='llm' AND is_current=1")
                        if not cl.on_current_prompt(r[0]))
    _bump(monkeypatch, "2026-10-17")
    assert older() == 2
    _bump(monkeypatch)
    assert older() == 2, "the timeline and meta.labels_on_older_prompt do not grow"
    daily = export.build_daily(conn, outlets=[])
    assert sum(day["labels_on_older_prompt"] for m in daily.values() for day in m["days"].values()) == 2
    # The stability check pairs one prompt's labels only: none of the stored labels after the change.
    assert second_rater.judged_rows(conn, current_prompt=True) == []
    assert cl.same_categories("2026-10-17", NEW_PROMPT) and not cl.same_categories("2026-10-12", NEW_PROMPT)
    assert not cl.same_categories(None, NEW_PROMPT)


def test_a_human_study_and_a_hand_check_stay_current_across_the_change(tmp_path, monkeypatch):
    monkeypatch.setattr(agreement, "REVIEWS_DIR", tmp_path / "reviews")
    conn = store.connect(tmp_path / "h.db")
    _bump(monkeypatch, "2026-10-17")
    conn.execute("INSERT INTO agreement_studies(computed_at, sample_size, kappa_all, kappa_bc, n_bc, details) VALUES (?,?,?,?,?,?)",
                 ("2026-11-20T00:00:00+00:00", 200, 0.8, 0.8, 120,
                  json.dumps({"method": "human_coding", "sample_complete": True, "compared_against": "current_label",
                              "prompt_version": "2026-10-17"})))
    conn.commit()
    assert agreement.human_study_status(conn)["state"] == "passed"
    _bump(monkeypatch)
    status = agreement.human_study_status(conn)
    assert status["state"] == "passed" and status["stale"] is False
    _bump(monkeypatch, "2099-01-01")
    assert agreement.human_study_status(conn)["state"] == "stale"
    # The undetermined hand check: a sample drawn under 2026-10-17 stays current under 2026-12-01.
    reviews = tmp_path / "mo"
    reviews.mkdir()
    (reviews / "undetermined_a_sample_2026-11-20.csv").write_text("sample_id,article_id,verdict\n")
    (reviews / "undetermined_a_sample_2026-11-20.key.json").write_text(json.dumps({"prompt_version": "2026-10-17", "items": []}))
    _bump(monkeypatch)
    assert model_only.assess(reviews)["check"]["state"] != "stale"
    _bump(monkeypatch, "2099-01-01")
    assert model_only.assess(reviews)["check"]["state"] == "stale"


# ---------------------------------------------------------------------------
# Session labels
# ---------------------------------------------------------------------------

def _session(url_hash, prompt, **kw):
    lab = dict(_reply("B"), url_hash=url_hash, model_version="claude-opus-5-5 (session, prompt %s)" % prompt,
               labelled_at="2026-12-02", source="session")
    lab.update(kw)
    return lab


def test_session_labels_under_the_new_prompt_must_carry_the_attributes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    _bump(monkeypatch)
    conn = _db()
    a1, a2, a3, a4 = (_article(conn, n, body=False) for n in (1, 2, 3, 4))
    h = lambda n: store.url_hash("https://e.com/s%d" % n)
    no_attrs = {k: v for k, v in _session(h(2), NEW_PROMPT).items() if k not in ATTRS}
    assert "missing author" in labels.validate(no_attrs)
    assert "route" in labels.validate(_session(h(3), NEW_PROMPT, route="by pigeon"))
    assert labels.validate({k: v for k, v in _session(h(4), "2026-10-17").items() if k not in ATTRS}) is None
    lines = [_session(h(1), NEW_PROMPT, category="C"), no_attrs,
             {k: v for k, v in _session(h(4), "2026-10-17").items() if k not in ATTRS}]
    d = tmp_path / "labels"
    d.mkdir()
    (d / "s.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    counts = labels.ingest(conn, d)
    assert counts["applied"] == 2 and counts["malformed"] == 1 and counts["older_prompt"] == 0
    assert counts["attribute_review"] == 1
    row = store.current_classification(conn, a1)
    assert row["needs_review"] == cl.CATEGORY_DIFFERS_FROM_ATTRIBUTES and row["attr_author"] == "newsroom"
    assert store.current_classification(conn, a4)["attr_author"] is None, "a 2026-10-17 session label is applied"


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

def test_audit_files_and_article_records_carry_the_attributes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    new = _article(conn, 1)
    cl.store_result(conn, new, _reply("B"), OLD)
    legacy = _article(conn, 2)
    store.insert_classification(conn, legacy, "llm", "C", 0.7, model_version=OLD)
    conn.commit()
    audit = tmp_path / "audit"
    export.write_audit_files(conn, audit)
    lines = {l["id"]: l for f in audit.glob("articles-*.jsonl") for l in map(json.loads, f.read_text().splitlines())}
    want = {"author": "newsroom", "commissioner": "none", "state_affiliation": "no", "route": "ordinary_reporting",
            "disclosure": "none", "verification": "merely_attributed", "derived_category": "B"}
    assert lines[new]["attributes"] == want
    assert "attributes" not in lines[legacy] and not any(k.startswith("attr_") for l in lines.values() for k in l)
    assert ATTRS["central_claim"] not in json.dumps(lines), "the spans stay in the database"
    entries = {e["id"]: e for rows in export.build_articles(conn, outlets=[]).values() for e in rows}
    assert entries[new]["attributes"] == want and "attributes" not in entries[legacy]


def test_methodology_says_route_and_disclosure_come_from_the_text_alone():
    text = methodology.attributes_text()
    assert "From the prompt of 2026-12-01 the model also records" in text
    assert "route and disclosure are read from the headline and the text alone" in text
    assert "are not judged again" in text and "marked for review" in text
    assert "–" not in text and "—" not in text and "Category A" not in text


# ---------------------------------------------------------------------------
# Attribute by attribute agreement with hand codes
# ---------------------------------------------------------------------------

def _sheet(path, rows, header):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(header)
        w.writerows(rows)


def test_attribute_agreement_against_hand_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    arts = [_article(conn, n) for n in range(1, 9)]
    # Six labels with attributes: three unchecked, three independent; the model codes newsroom throughout.
    for i, aid in enumerate(arts[:6]):
        v = "merely_attributed" if i < 3 else "contextualized"
        cl.store_result(conn, aid, _reply("B" if i < 3 else "C", verification=v), OLD)
    store.insert_classification(conn, arts[6], "llm", "C", 0.7, model_version=OLD)      # no attributes
    store.insert_classification(conn, arts[7], "rules", "A", 1.0)                       # a rules label
    conn.commit()
    header = ["ID", "TEXT_SOURCE", "AUTHOR", "COMMISSIONER", "STATE_AFFIL", "ROUTE", "DISCLOSURE", "DISCLOSURE_QUOTE",
              "CENTRAL_CLAIM", "VERIFICATION", "VERIFICATION_EVIDENCE", "DERIVED_CAT", "POWER", "TERMS", "WORDS", "NOTES"]
    hv = ["merely_attributed", "unassessed", "contextualized", "contextualized", "no_official_claim", "merely_attributed",
          "no_official_claim", "no_official_claim"]
    hc = ["B", "B", "C", "C", "C", "B", "C", "A"]
    rows = [["X-%d" % i, "fetched", "newsroom" if i != 7 else "state", "none", "no", "ordinary_reporting",
             "explicit_paid_or_sponsored" if i == 0 else "none", "", "", hv[i], "", hc[i], "", "", "", ""]
            for i in range(8)]
    rows.append(["X-99", "fetched", "newsroom", "none", "no", "ordinary_reporting", "none", "", "", "unassessed", "",
                 "B", "", "", "", ""])                                                   # in no key
    rows.append(["X-98", "fetched", "", "", "", "", "", "", "", "", "", "?", "", "", "", ""])  # nothing coded
    codes = tmp_path / "codes.tsv"
    _sheet(codes, rows, header)
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"rows": [{"ext_id": "X-%d" % i, "tracker_article_id": arts[i]} for i in range(8)]
                              + [{"ext_id": "X-98", "tracker_article_id": 999}]}))
    res = agreement.cmd_attributes(conn, codes, [key], tmp_path / "out" / "attr.json")
    assert res["sheet"]["counts"] == {"rows": 10, "no_article_id": 1, "no_attributes": 1, "read": 8}
    assert res["compared"] == 6 and res["excluded"] == {"label_without_attributes": 1, "rules_label": 1}
    assert res["outside_codebook"] == {"disclosure": 1}
    a = res["attributes"]
    assert a["author"]["n"] == 6 and a["author"]["agreement"] == 1.0 and a["author"]["kappa"] is None
    assert a["disclosure"]["n"] == 5
    # Model: ma ma ma ctx ctx ctx; hand: ma ua ctx ctx noc ma, so 2 of 6 alike; unchecked: u u u o o o against u u o o o u.
    v = a["verification"]
    assert v["n"] == 6 and abs(v["agreement"] - 2 / 6.0) < 1e-9
    split = a[agreement.VERIFICATION_SPLIT]
    assert split["confusion"] == {"unchecked": {"unchecked": 2, "other": 1}, "other": {"other": 2, "unchecked": 1}}
    assert abs(split["kappa"] - agreement.cohens_kappa([("u", "u"), ("u", "u"), ("u", "o"), ("o", "o"), ("o", "o"), ("o", "u")])) < 1e-9
    assert a["derived_category"]["n"] == 6 and abs(a["derived_category"]["agreement"] - 4 / 6.0) < 1e-9
    assert res["category"]["n"] == 8, "every current label against the hand coded category"
    assert json.loads((tmp_path / "out" / "attr.json").read_text())["compared"] == 6
    assert "verification: n=6" in capsys.readouterr().out
    assert conn.execute("SELECT COUNT(*) FROM agreement_studies").fetchone()[0] == 0, "records nothing"


def test_a_sheet_naming_the_article_itself_needs_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    aid = _article(conn, 1)
    cl.store_result(conn, aid, _reply("B"), OLD)
    path = tmp_path / "hand.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ext_id", "tracker_article_id", "author", "commissioner", "state_affil", "route", "disclosure",
                    "verification", "category"])
        w.writerow(["H-1", aid, "Newsroom", "none", "no", "ordinary_reporting", "none", "unassessed", "B"])
        w.writerow(["H-2", aid, "agency", "none", "no", "", "", "", ""])        # the same article again
    items, stats = agreement.load_hand_codes(path)
    assert stats["duplicate_article"] == 1 and items[0]["attributes"]["author"] == "newsroom"
    res = agreement.compare_attributes(conn, items)
    assert res["attributes"]["verification"]["confusion"] == {"merely_attributed": {"unassessed": 1}}
    assert res["attributes"][agreement.VERIFICATION_SPLIT]["agreement"] == 1.0
