"""The second rater measures consistency between two models, never correctness."""
import json
import sqlite3

from pipeline import classify_llm, config, second_rater as sr, store


def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "t.db")
    return conn


def _judged(conn, n=8, category="B"):
    for i in range(n):
        url = "https://example.com/%s-%d" % (category, i)
        aid = store.insert_discovered(conn, {"url": url, "outlet_id": "it_x", "country": "ITA", "language": "it",
                                             "title": "Headline %d" % i, "gate_relevant": 1, "status": "fetched"})
        store.save_body(store.url_hash(url), "Body text about China, item %d. " % i * 20)
        store.insert_classification(conn, aid, "llm", category, 0.8, evidence_quote="quote %d" % i,
                                    model_version="claude-sonnet-5")
    conn.commit()


def test_draw_is_stratified_by_the_first_label():
    rows = [{"id": i, "category": c, "language": "it"} for i, c in
            enumerate(["A"] * 4 + ["B"] * 4 + ["C"] * 40 + ["not_relevant"] * 20)]
    drawn = sr.draw(rows, 12)
    counts = {c: sum(1 for r in drawn if r["category"] == c) for c in sr.CATEGORIES}
    assert len(drawn) == 12
    # The relay boundary must not be swamped by the commonest category.
    assert counts["B"] == 3 and counts["C"] == 3 and counts["A"] == 3


def test_second_rater_sees_the_same_prompt_and_no_outlet_or_country(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    _judged(conn, n=2)
    client = classify_llm.DryRunClient(answer=lambda body: "C")
    rows = sr.judged_rows(conn)
    pairs, usage = sr.rejudge(conn, rows, "claude-opus-5", client=client)
    assert len(pairs) == 2 and usage["calls"] == 2
    sent = client.calls[0]
    assert sent["model"] == "claude-opus-5"
    assert sent["system"] == classify_llm.request_params("t", "b")["system"]
    body = sent["messages"][0]["content"]
    assert "Headline 0" in body
    for leak in ("it_x", "ITA", "Italy", "claude-sonnet-5"):
        assert leak not in body
    assert pairs[0]["first"]["category"] == "B" and pairs[0]["second"]["category"] == "C"
    assert pairs[0]["agree"] is False


def test_summary_states_what_it_measures_and_lists_disagreements():
    pairs = [{"article_id": 1, "country": "ITA", "language": "it", "agree": True,
              "first": {"category": "B"}, "second": {"category": "B"}},
             {"article_id": 2, "country": "ITA", "language": "it", "agree": False,
              "first": {"category": "B"}, "second": {"category": "C"}},
             {"article_id": 3, "country": "IDN", "language": "id", "agree": True,
              "first": {"category": "C"}, "second": {"category": "C"}}]
    s = sr.summarise(pairs, "claude-sonnet-5", "claude-opus-5")
    assert s["method"] == "model_vs_model"
    assert "not correctness" in s["measures"] and "no human coding" in s["measures"]
    assert s["first_model"] == "claude-sonnet-5" and s["second_model"] == "claude-opus-5"
    assert s["n"] == 3 and s["n_bc"] == 3
    assert s["raw_agreement"] == 0.6667
    assert len(s["disagreements"]) == 1 and s["disagreements"][0]["article_id"] == 2
    assert s["per_language_bc"]["it"]["n"] == 2
    # Nothing in the artifact may call this an agreement study.
    assert "agreement study" not in json.dumps(s).lower()


def test_cost_estimate_refuses_to_guess_without_measured_usage(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    assert sr.estimate_cost(conn, 200)["measured"] is False
    store.record_llm_usage(conn, 10, 30000, 2000)
    conn.commit()
    est = sr.estimate_cost(conn, 200)
    assert est["measured"] is True
    assert est["per_call_input_tokens"] == 3000 and est["per_call_output_tokens"] == 200
    assert est["estimated_input_tokens"] == 600000


def test_study_row_is_flat_and_keeps_what_it_measures():
    s = sr.summarise([{"article_id": 1, "country": "ITA", "language": "it", "agree": False,
                       "first": {"category": "B"}, "second": {"category": "C"}}],
                     "claude-sonnet-5", "claude-opus-5")
    row = sr.study_row(s)
    assert row["method"] == "model_vs_model"
    assert row["model_a"] == "claude-sonnet-5" and row["model_b"] == "claude-opus-5"
    assert row["sample_size"] == 1 and row["n_bc"] == 1
    assert "not correctness" in row["details"]["measures"]
    assert row["details"]["disagreements"] == 1
