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


def test_default_second_rater_is_sonnet_and_a_rerun_says_so():
    assert sr.DEFAULT_SECOND_MODEL == "claude-sonnet-5"
    pairs = [{"article_id": 1, "country": "ITA", "language": "it", "agree": True,
              "first": {"category": "B"}, "second": {"category": "B"}}]
    same = sr.summarise(pairs, "claude-sonnet-5", "claude-sonnet-5")
    assert same["method"] == "same_model_rerun"
    assert "same model" in same["measures"] and "randomness" in same["measures"]
    assert "cannot detect a bias" in same["measures"]
    diff = sr.summarise(pairs, "claude-sonnet-5", "claude-opus-5")
    assert diff["method"] == "model_vs_model"
    from pipeline import export
    assert "same_model_rerun" in export.RELAY_QUALIFIER
    assert "another model" in export.RELAY_QUALIFIER["same_model_rerun"]


# ---------------------------------------------------------------------------
# Recording, and the study that runs by itself
# ---------------------------------------------------------------------------
import datetime as dt

from pipeline import llm_cost


def test_main_record_stores_both_raters_labels(tmp_path, monkeypatch):
    """The pairs rejudge returns are nested; the table is flat. Recording used to raise after the calls were paid for."""
    conn = _db(tmp_path, monkeypatch)
    _judged(conn, n=3)
    pairs, _ = sr.rejudge(conn, sr.judged_rows(conn, with_body=True), "claude-sonnet-5",
                          client=classify_llm.DryRunClient(answer=lambda body: "C"), dry_run=True)
    out = sr.summarise(pairs, "claude-sonnet-5", "claude-sonnet-5")
    sid = store.record_model_agreement(conn, sr.study_row(out), sr.pair_rows(out["pairs"]))
    rows = conn.execute("SELECT category_a, category_b FROM model_agreement_labels WHERE study_id=?", (sid,)).fetchall()
    assert len(rows) == 3 and all((r["category_a"], r["category_b"]) == ("B", "C") for r in rows)
    details = json.loads(conn.execute("SELECT details FROM model_agreement_studies WHERE id=?", (sid,)).fetchone()[0])
    assert "bc_by_language" in details, "the key export reads for the per language veto"


def test_rejudge_books_its_calls_against_the_budget(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch)
    _judged(conn, n=2)
    sr.rejudge(conn, sr.judged_rows(conn), "claude-sonnet-5", client=classify_llm.DryRunClient())
    row = conn.execute("SELECT calls, cost_usd FROM llm_usage").fetchone()
    assert row["calls"] == 2 and row["cost_usd"] > 0


class _FakeBatches:
    def __init__(self, answer="B", succeed=True):
        self.created, self.answer, self.succeed = [], answer, succeed

    def create(self, requests):
        self.created.append(requests)
        return type("B", (), {"id": "batch_study_1"})()

    def retrieve(self, batch_id):
        return type("I", (), {"processing_status": "ended"})()

    def results(self, batch_id):
        for req in self.created[-1]:
            payload = {"category": self.answer, "confidence": 0.8, "evidence_quote": "q", "reasoning": "r",
                       "china_sources_cited": [], "independent_confirmation_present": False, "confirmation_evidence": None}
            msg = type("M", (), {"stop_reason": "end_turn", "model": "claude-sonnet-5",
                                 "content": [type("T", (), {"type": "text", "text": json.dumps(payload)})()],
                                 "usage": type("U", (), {"input_tokens": 1000, "output_tokens": 100,
                                                         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})()})()
            kind = "succeeded" if self.succeed else "errored"
            yield type("R", (), {"custom_id": req["custom_id"], "result": type("X", (), {"type": kind, "message": msg})()})()


class _FakeClient:
    def __init__(self, **kw):
        self.messages = type("Msgs", (), {"batches": _FakeBatches(**kw)})()


def _auto_setup(tmp_path, monkeypatch, n=12):
    conn = _db(tmp_path, monkeypatch)
    _judged(conn, n=n)
    monkeypatch.setattr(config, "RELIABILITY_MIN_POOL", 10)
    monkeypatch.setattr(config, "RELIABILITY_MIN_PAIRS", 8)
    monkeypatch.setattr(config, "RELIABILITY_SAMPLE", 10)
    monkeypatch.setattr(config, "EXPORT_DIR_AUDIT", tmp_path / "export")
    monkeypatch.setattr(config, "LLM_MONTHLY_BUDGET_USD", 30.0)
    return conn


def test_auto_waits_while_the_month_is_over_budget(tmp_path, monkeypatch):
    conn = _auto_setup(tmp_path, monkeypatch)
    store.record_llm_usage(conn, 3000, 0, 0, cost_usd=38.0, date="2026-09-16")
    client = _FakeClient()
    out = sr.auto(conn, submit=True, today=dt.date(2026, 9, 20), client=client)
    assert out["submitted"] == 0 and "budget" in out["skipped"] and not client.messages.batches.created
    state = sr.study_state(conn, dt.date(2026, 9, 20))
    assert state["state"] == "waiting_for_budget" and state["earliest"] == "2026-10-01"


def test_auto_submits_once_the_budget_reopens_then_records_the_study(tmp_path, monkeypatch):
    conn = _auto_setup(tmp_path, monkeypatch)
    store.record_llm_usage(conn, 3000, 0, 0, cost_usd=38.0, date="2026-09-16")
    client = _FakeClient(answer="B")
    today = dt.date(2026, 10, 1)
    out = sr.auto(conn, submit=True, today=today, client=client)
    assert out["submitted"] == 10 and out["reason"] == "no study yet"
    batch = conn.execute("SELECT kind, status, article_ids FROM llm_batches").fetchone()
    assert batch["kind"] == "study" and batch["status"] == "submitted"
    assert client.messages.batches.created[0][0]["custom_id"].startswith("study-")
    assert llm_cost.outstanding(conn, "2026-01-01", "2099-01-01") == 10, "the study counts against the budget until collected"
    # Classification must never try to collect a study batch: its ids are not article ids.
    assert classify_llm._collect_batches(conn, client) == {"batch_collected": 0, "batch_errors": 0}
    out2 = sr.auto(conn, submit=True, today=today, client=client)
    assert out2["collected"]["state"] == "recorded" and out2["collected"]["method"] == "same_model_rerun"
    assert out2["submitted"] == 0, "one study a month"
    study = store.latest_model_agreement(conn)
    assert study["sample_size"] == 10 and study["method"] == "same_model_rerun"
    assert conn.execute("SELECT COUNT(*) FROM model_agreement_labels").fetchone()[0] == 10
    assert conn.execute("SELECT status FROM llm_batches").fetchone()[0] == "collected"
    assert list((tmp_path / "export" / "reliability").glob("*.json")), "the study is kept as an audit file"
    assert conn.execute("SELECT SUM(cost_usd) FROM llm_usage WHERE date >= '2026-10-01' OR date = ?", (store.utcnow()[:10],)).fetchone()[0] > 0
    assert sr.study_state(conn, today)["state"] == "done"


def test_too_few_results_record_no_study(tmp_path, monkeypatch):
    conn = _auto_setup(tmp_path, monkeypatch)
    client = _FakeClient(succeed=False)
    assert sr.auto(conn, submit=True, today=dt.date(2026, 10, 1), client=client)["submitted"] == 10
    out = sr.auto(conn, submit=False, today=dt.date(2026, 10, 1), client=client)
    assert out["collected"]["state"] == "too_few_results"
    assert store.latest_model_agreement(conn) is None
    assert conn.execute("SELECT status FROM llm_batches").fetchone()[0] == "failed"


def test_a_study_without_enough_b_is_inconclusive_not_perfect(tmp_path, monkeypatch):
    """Both raters answering notB on every pair is the expected case, not a freak one: the prompt
    says answer C when uncertain. It used to produce a kappa of 1.0 and publish every relay count."""
    pairs = [{"article_id": i, "country": "ITA", "language": "it", "agree": True,
              "first": {"category": "C"}, "second": {"category": "C"}} for i in range(300)]
    s = sr.summarise(pairs, "claude-sonnet-5", "claude-opus-5")
    assert s["kappa_bc"] is None and s["n_bc"] == 300
    assert "at least %d" % sr.MIN_POSITIVE_FOR_KAPPA in s["kappa_bc_inconclusive"]
    assert sr.study_row(s)["details"]["kappa_bc_inconclusive"] == s["kappa_bc_inconclusive"]
    # one disagreement in 300 is no better a basis, and must not withhold counts by scoring zero
    pairs[0] = dict(pairs[0], first={"category": "B"}, agree=False)
    assert sr.summarise(pairs, "claude-sonnet-5", "claude-opus-5")["kappa_bc"] is None
    # with enough B on both sides there is something to measure again
    m = sr.MIN_POSITIVE_FOR_KAPPA
    for i in range(m):
        pairs[i] = dict(pairs[i], first={"category": "B"}, second={"category": "B"}, agree=True)
    s2 = sr.summarise(pairs, "claude-sonnet-5", "claude-opus-5")
    assert s2["kappa_bc"] is not None and s2["kappa_bc_inconclusive"] is None
    assert s2["n_b_first"] == m and s2["n_b_second"] == m


# ---------------------------------------------------------------------------
# A study never spends more of a day than the day allows
# ---------------------------------------------------------------------------

def test_a_study_never_takes_more_calls_than_the_day_allows(tmp_path, monkeypatch):
    """On 1 October the batched daily cap is 110 calls and the sample is 250: the study used to
    submit all 250, book them into the day and leave classification nothing."""
    conn = _db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "LLM_MONTHLY_BUDGET_USD", 30.0)
    monkeypatch.setattr(config, "LLM_COST_PER_CALL_UNBATCHED", 0.0175)
    monkeypatch.setattr(config, "RELIABILITY_SAMPLE", 250)
    monkeypatch.setattr(config, "RELIABILITY_MIN_PAIRS", 150)
    # Pinned, because config reads it from the environment: CI sets the repository variable to
    # 4000, which leaves room for the study on the last day, while the default of 600 does not.
    # The test then depended on where it ran, and failed only in CI.
    monkeypatch.setattr(config, "LLM_DAILY_CALL_CEILING", 600)
    today = dt.date(2026, 10, 1)
    plan = sr.study_plan(conn, today, config.RELIABILITY_SAMPLE)
    assert plan["day_allowance"] == 110 and plan["day_share"] == 55
    assert plan["n"] == 0 and "waits for a day" in plan["reason"]
    # The end of a month with budget left over is such a day: what is left is spread over one day.
    late = dt.date(2026, 10, 31)
    plan_late = sr.study_plan(conn, late, config.RELIABILITY_SAMPLE)
    assert plan_late["n"] == 250 and plan_late["day_allowance"] >= 500
    # and a study submitted earlier in the day counts against it, so the two stages share one cap
    store.record_llm_usage(conn, 520, 0, 0, date=late.isoformat())
    assert sr.study_plan(conn, late, config.RELIABILITY_SAMPLE)["n"] == 0


def test_auto_submits_only_what_the_day_can_carry(tmp_path, monkeypatch):
    conn = _auto_setup(tmp_path, monkeypatch)       # sample 10, min pairs 8, so the floor is 10
    monkeypatch.setattr(config, "LLM_DAILY_CALL_CEILING", 30)
    client = _FakeClient(answer="B")
    today = dt.date(2026, 10, 1)
    out = sr.auto(conn, submit=True, today=today, client=client)
    assert out["submitted"] == 10 and out["plan"]["day_share"] == 15
    used = conn.execute("SELECT calls FROM llm_usage WHERE date=?", (today.isoformat(),)).fetchone()[0]
    assert used == 10, "the study books its calls where the daily cap looks"
    assert conn.execute("SELECT COUNT(*) FROM llm_batches").fetchone()[0] == 1
    # classification still has the rest of the day
    assert config.LLM_DAILY_CALL_CEILING - used == 20


def test_a_day_too_small_for_a_study_waits_for_a_better_one(tmp_path, monkeypatch):
    conn = _auto_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "LLM_DAILY_CALL_CEILING", 12)   # a study may take 6, its floor is 10
    client = _FakeClient()
    out = sr.auto(conn, submit=True, today=dt.date(2026, 10, 1), client=client)
    assert out["submitted"] == 0 and not client.messages.batches.created
    assert "waits for a day" in out["skipped"]
    state = sr.study_state(conn, dt.date(2026, 10, 1))
    assert state["state"] == "waiting_for_budget"
    assert state["earliest"] == "2026-10-01" or state["earliest"] > "2026-10-01"


def test_auto_never_raises(tmp_path, monkeypatch):
    conn = _auto_setup(tmp_path, monkeypatch)

    class Broken:
        class messages:
            class batches:
                @staticmethod
                def create(requests):
                    raise RuntimeError("api down")
    out = sr.auto(conn, submit=True, today=dt.date(2026, 10, 1), client=Broken())
    assert "api down" in out["error"] and out["submitted"] == 0
