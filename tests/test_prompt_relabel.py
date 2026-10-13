"""The prompt version recorded with every model label, and the queue that judges labels made under an
older prompt again without ever leaving an article unlabelled."""
import datetime as dt
import json
import sqlite3

from pipeline import classify_llm as cl, config, labels, store
from tests.test_classify_llm import _RecordingBatchApi, _batch_client, _message

OLD = "claude-sonnet-5"
GOOD = ('{"category": "C", "confidence": 0.8, "evidence_quote": "q", "reasoning": "r", '
        '"china_sources_cited": [], "independent_confirmation_present": false, "confirmation_evidence": null}')


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    return conn


def _article(conn, n, status="awaiting_llm", body=True, dup=None):
    url = "https://e.com/r%d" % n
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": "ITA", "language": "it",
                                         "title": "Title %d" % n, "status": status, "gate_relevant": 1})
    if body:
        store.save_body(store.url_hash(url), "Body about China %d" % n)
    if dup:
        conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (dup, aid))
    return aid


def _labelled(conn, n, when, model_version=OLD, category="B", **kw):
    """A classified article whose current label is a model label made at `when`."""
    aid = _article(conn, n, status="classified", **kw)
    store.insert_classification(conn, aid, "llm", category, 0.7, evidence_quote="q", model_version=model_version)
    conn.execute("UPDATE classifications SET classified_at=? WHERE article_id=?", (when, aid))
    conn.commit()
    return aid


def _allow(monkeypatch, n):
    monkeypatch.setattr(cl, "relabel_allowance", lambda ceiling, used, now, share=None: n)


# ---------------------------------------------------------------------------
# Recording the prompt version
# ---------------------------------------------------------------------------

def test_prompt_tag_helpers():
    assert cl.with_prompt_version("claude-sonnet-5") == "claude-sonnet-5 (prompt %s)" % cl.PROMPT_VERSION
    tagged = "claude-sonnet-5 (session, prompt %s)" % cl.PROMPT_VERSION
    assert cl.with_prompt_version(tagged) == tagged, "a label that names its prompt keeps it"
    assert cl.with_prompt_version("claude-sonnet-5", None) == "claude-sonnet-5"
    assert cl.prompt_version_of(tagged) == cl.PROMPT_VERSION and cl.on_current_prompt(tagged)
    assert cl.prompt_version_of(OLD) is None and not cl.on_current_prompt(OLD)
    assert not cl.on_current_prompt("claude-sonnet-5 (prompt 2026-01-01)")
    assert not cl.on_current_prompt(None)


def test_api_label_records_the_prompt_version(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    aid = _article(conn, 1)
    counts = cl.run(conn, "t", client=cl.DryRunClient(answer=lambda body: "B"))
    assert counts["classified"] == 1 and counts["prompt_version"] == cl.PROMPT_VERSION
    row = store.current_classification(conn, aid)
    assert row["model_version"] == "dry-run (prompt %s)" % cl.PROMPT_VERSION
    assert cl.on_current_prompt(row["model_version"])


def test_session_label_keeps_the_model_version_it_names(tmp_path):
    conn = _db()
    a1 = _article(conn, 1, body=False)
    a2 = _article(conn, 2, body=False)
    base = {"category": "C", "confidence": 0.8, "evidence_quote": "q", "reasoning": "r", "china_sources_cited": [],
            "independent_confirmation_present": False, "confirmation_evidence": None,
            "labelled_at": "2026-10-13", "source": "session"}
    tagged_mv = labels.session_model_version()
    assert tagged_mv == "claude-sonnet-5 (session, prompt %s)" % cl.PROMPT_VERSION
    lines = [dict(base, url_hash=store.url_hash("https://e.com/r1"), model_version="claude-sonnet-5 (session)"),
             dict(base, url_hash=store.url_hash("https://e.com/r2"), model_version=tagged_mv)]
    (tmp_path / "s.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    counts = labels.ingest(conn, tmp_path)
    assert counts["applied"] == 1 and counts["older_prompt"] == 1 and counts["malformed"] == 0
    # No prompt named, so it counts as an older prompt: skipped, and the article keeps waiting.
    assert store.current_classification(conn, a1) is None
    assert store.get_article(conn, a1)["status"] == "awaiting_llm"
    assert store.current_classification(conn, a2)["model_version"] == tagged_mv
    assert labels.session_label_counts(conn) == {"total": 1, "by_model_version": {tagged_mv: 1}}


# ---------------------------------------------------------------------------
# The relabel queue
# ---------------------------------------------------------------------------

def test_candidates_are_older_prompt_model_labels_oldest_first(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    newer = _labelled(conn, 1, "2026-10-01T00:00:00+00:00")
    oldest = _labelled(conn, 2, "2026-09-16T00:00:00+00:00", model_version="claude-sonnet-5 (session)")
    _labelled(conn, 3, "2026-09-10T00:00:00+00:00", model_version=cl.with_prompt_version(OLD))  # current prompt
    rules = _article(conn, 4, status="classified")
    store.insert_classification(conn, rules, "rules", "A", 1.0)
    # No body on disk and no fetch attempts left: it would block the head of the queue forever.
    stuck = _labelled(conn, 5, "2026-09-01T00:00:00+00:00", body=False)
    conn.execute("UPDATE articles SET fetch_attempts=9 WHERE id=?", (stuck,))
    failed = _labelled(conn, 6, "2026-09-02T00:00:00+00:00")
    store.update_article(conn, failed, fail_reason=cl.RELABEL_FAILED + "refusal")
    in_batch = _labelled(conn, 7, "2026-09-03T00:00:00+00:00")
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version) "
                 "VALUES ('b', '2026-10-12T00:00:00+00:00', 'submitted', ?, 'm')", (json.dumps([in_batch]),))
    assert [r["id"] for r in cl.relabel_candidates(conn)] == [oldest, newer]


def test_candidates_come_in_priority_order_then_oldest_first(monkeypatch, tmp_path):
    """Unchecked state sourcing first, then independent journalism labels with official Chinese sourcing
    and no independent confirmation, then state origin, then the rest; oldest label first within each."""
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    assert cl.RELABEL_PRIORITY == ("B", "body_refreshed", "C_official_unconfirmed", "A")
    conn = _db()

    def label(n, when, category, trigger=None, cited=None, confirmed=None, dup=None):
        aid = _article(conn, n, status="classified", dup=dup)
        if trigger is not None:
            conn.execute("UPDATE articles SET llm_trigger=? WHERE id=?", (json.dumps(trigger), aid))
        store.insert_classification(conn, aid, "llm", category, 0.7, evidence_quote="q", model_version=OLD,
                                    china_sources_cited=cited, independent_confirmation_present=confirmed)
        conn.execute("UPDATE classifications SET classified_at=? WHERE article_id=?", (when, aid))
        conn.commit()
        return aid

    sourcing = {"a_candidate": [], "triggers": ["mofa_spokesperson"], "spans": ["s"]}
    not_rel = label(1, "2026-09-16T00:00:00+00:00", "not_relevant")
    a_old = label(2, "2026-09-17T00:00:00+00:00", "A")
    c_confirmed = label(3, "2026-09-18T00:00:00+00:00", "C", trigger=sourcing, cited=["Xinhua"], confirmed=True)
    c_trigger = label(4, "2026-09-19T00:00:00+00:00", "C", trigger=sourcing, confirmed=False)
    c_plain = label(5, "2026-09-20T00:00:00+00:00", "C", trigger={"a_candidate": [], "triggers": []}, confirmed=False)
    b_new = label(6, "2026-10-05T00:00:00+00:00", "B")
    c_cited = label(7, "2026-09-21T00:00:00+00:00", "C", cited=["MOFA spokesperson Mao Ning"], confirmed=False)
    a_new = label(8, "2026-10-06T00:00:00+00:00", "A")
    b_old = label(9, "2026-09-25T00:00:00+00:00", "B")
    assert [r["id"] for r in cl.relabel_candidates(conn)] == [
        b_old, b_new, c_trigger, c_cited, a_old, a_new, not_rel, c_confirmed, c_plain]


def test_a_near_duplicate_group_is_relabelled_at_its_highest_priority(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    other_b = _labelled(conn, 1, "2026-09-20T00:00:00+00:00")
    old_c = _labelled(conn, 2, "2026-09-16T00:00:00+00:00", category="C", dup="g")
    newer_b = _labelled(conn, 3, "2026-10-01T00:00:00+00:00", dup="g")
    pool = cl._collapse_dup_groups(cl.relabel_candidates(conn))
    # The group's unchecked state sourcing member stands for it among the first tier; one call settles both.
    assert [r["id"] for r in pool] == [other_b, newer_b]
    assert old_c not in [r["id"] for r in pool]


def test_relabel_allowance_is_a_paced_share_of_the_day():
    day = dt.datetime(2026, 10, 12, tzinfo=dt.timezone.utc)
    assert cl.relabel_allowance(480, 0, day, share=0.5) == 10          # the first hour
    assert cl.relabel_allowance(480, 0, day.replace(hour=12), share=0.5) == 130
    assert cl.relabel_allowance(480, 0, day.replace(hour=23, minute=30), share=0.5) == 240
    assert cl.relabel_allowance(480, 200, day.replace(hour=23, minute=30), share=0.5) == 40
    assert cl.relabel_allowance(480, 300, day.replace(hour=23), share=0.5) == 0
    assert config.LLM_RELABEL_DAY_SHARE == 0.5


def test_new_articles_first_then_relabels_and_the_old_label_stays(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(config, "LLM_DAILY_CALL_CEILING", 3)
    _allow(monkeypatch, 5)
    conn = _db()
    fresh = _article(conn, 1)
    old = [_labelled(conn, 10 + i, "2026-09-%02dT00:00:00+00:00" % (16 + i)) for i in range(4)]
    counts = cl.run(conn, "t", client=cl.DryRunClient(answer=lambda body: "C"))
    # Three calls: the new article first, then the two oldest older-prompt labels.
    assert counts["calls"] == 3 and counts["classified"] == 1
    assert counts["relabel_sent"] == 2 and counts["relabelled"] == 2 and counts["relabel_eligible"] == 4
    assert cl.on_current_prompt(store.current_classification(conn, fresh)["model_version"])
    for aid in old[:2]:
        cur = store.current_classification(conn, aid)
        assert cur["category"] == "C" and cl.on_current_prompt(cur["model_version"])
        # The older label is kept, not deleted, and is no longer current.
        assert conn.execute("SELECT COUNT(*) FROM classifications WHERE article_id=? AND is_current=0", (aid,)).fetchone()[0] == 1
    for aid in old[2:]:
        cur = store.current_classification(conn, aid)
        assert cur["category"] == "B" and cur["model_version"] == OLD, "still waiting, still labelled"
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE status<>'classified'").fetchone()[0] == 0
    assert cl.relabel_calls_today(conn, dt.datetime.now(dt.timezone.utc).date().isoformat()) == 2
    # A ceiling bound by relabels is not a truncated day: every new article was judged.
    assert counts["ceiling_hit"] is False


def test_relabel_share_cap_and_calls_already_made_today(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(config, "LLM_DAILY_CALL_CEILING", 100)
    conn = _db()
    for i in range(6):
        _labelled(conn, 10 + i, "2026-09-%02dT00:00:00+00:00" % (16 + i))
    seen = []
    monkeypatch.setattr(cl, "relabel_allowance",
                        lambda ceiling, used, now, share=None: seen.append((ceiling, used)) or max(0, 2 - used))
    first = cl.run(conn, "t1", client=cl.DryRunClient())
    second = cl.run(conn, "t2", client=cl.DryRunClient())
    assert first["relabel_sent"] == 2 and second["relabel_sent"] == 0
    assert seen == [(100, 0), (100, 2)], "the second run sees what the first sent today"


def test_a_failed_relabel_keeps_the_label_and_is_not_retried(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    _allow(monkeypatch, 5)
    conn = _db()
    aid = _labelled(conn, 1, "2026-09-16T00:00:00+00:00")

    class Refuses:
        class messages:
            @staticmethod
            def create(**params):
                return _message("refusal", "")
    counts = cl.run(conn, "t", client=Refuses())
    assert counts["relabel_sent"] == 1 and counts["relabelled"] == 0
    art = store.get_article(conn, aid)
    assert art["status"] == "classified" and art["fail_reason"] == cl.RELABEL_FAILED + "refusal"
    assert store.current_classification(conn, aid)["model_version"] == OLD
    assert cl.relabel_candidates(conn) == []


def test_one_relabel_settles_its_near_duplicate_group(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    _allow(monkeypatch, 5)
    conn = _db()
    head = _labelled(conn, 1, "2026-09-16T00:00:00+00:00")
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (head, head))
    sibs = [_labelled(conn, 2 + i, "2026-09-17T00:00:00+00:00", dup=head) for i in range(2)]
    client = cl.DryRunClient(answer=lambda body: "C")
    counts = cl.run(conn, "t", client=client)
    assert len(client.calls) == 1 and counts["relabel_sent"] == 1 and counts["relabel_copied"] == 2
    for aid in [head] + sibs:
        cur = store.current_classification(conn, aid)
        assert cur["category"] == "C" and cl.on_current_prompt(cur["model_version"])
    assert cl.relabel_candidates(conn) == []
    # Nothing left to do: a second run makes no call and copies nothing.
    again = cl.run(conn, "t2", client=client)
    assert len(client.calls) == 1 and again["relabel_copied"] == 0


def test_a_new_article_copies_an_older_label_but_a_relabel_does_not(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    _allow(monkeypatch, 0)
    conn = _db()
    head = _labelled(conn, 1, "2026-09-16T00:00:00+00:00")
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (head, head))
    fresh = _article(conn, 2, dup=head)
    old_sib = _labelled(conn, 3, "2026-09-17T00:00:00+00:00", dup=head)
    assert cl._copy_from_dup_group(conn, store.get_article(conn, old_sib), current_prompt_only=True) is False
    counts = cl.run(conn, "t", client=cl.DryRunClient())
    assert counts["copied"] == 1 and counts["calls"] == 0
    assert store.current_classification(conn, fresh)["model_version"] == OLD


def test_batch_relabel_keeps_status_and_label_until_collected(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    _allow(monkeypatch, 5)
    conn = _db()
    fresh = _article(conn, 1)
    old = [_labelled(conn, 10 + i, "2026-09-%02dT00:00:00+00:00" % (16 + i)) for i in range(2)]
    recorder = _RecordingBatchApi()
    client = type("C", (), {"messages": type("M", (), {"batches": recorder})()})()
    counts = cl.run(conn, "t", client=client, batch=True)
    assert counts["batch_submitted"] == 3 and counts["relabel_sent"] == 2 and counts["grace_sent"] == 1
    assert store.get_article(conn, fresh)["status"] == "llm_submitted"
    for aid in old:
        assert store.get_article(conn, aid)["status"] == "classified"
        assert store.current_classification(conn, aid)["model_version"] == OLD
    batch = conn.execute("SELECT model_version FROM llm_batches").fetchone()
    assert cl.prompt_version_of(batch["model_version"]) == cl.PROMPT_VERSION
    assert cl.relabel_candidates(conn) == [], "articles waiting in a batch are not offered again"

    out = cl._collect_batches(conn, _batch_client([(fresh, "succeeded", _message("end_turn", GOOD)),
                                                   (old[0], "succeeded", _message("end_turn", GOOD)),
                                                   (old[1], "errored", None)]))
    assert out["batch_collected"] == 2 and out["batch_errors"] == 1
    assert cl.on_current_prompt(store.current_classification(conn, old[0])["model_version"])
    failed = store.get_article(conn, old[1])
    assert failed["status"] == "classified" and failed["fail_reason"] == cl.RELABEL_FAILED + "errored"
    assert store.current_classification(conn, old[1])["model_version"] == OLD


def test_a_batch_submitted_under_an_older_prompt_stores_untagged_labels(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    aid = _article(conn, 1)
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version, kind) "
                 "VALUES ('b1', '2026-10-11T00:00:00+00:00', 'submitted', ?, ?, 'classify')", (json.dumps([aid]), OLD))
    store.update_article(conn, aid, status="llm_submitted")
    cl._collect_batches(conn, _batch_client([(aid, "succeeded", _message("end_turn", GOOD))]))
    mv = store.current_classification(conn, aid)["model_version"]
    assert mv == "m" and not cl.on_current_prompt(mv), "judged under the old prompt, so queued again"


def test_timeline_marks_days_with_labels_on_an_older_prompt(monkeypatch, tmp_path):
    from pipeline import export
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    _labelled(conn, 1, "2026-09-16T00:00:00+00:00")
    _labelled(conn, 2, "2026-09-16T00:00:00+00:00", model_version=cl.with_prompt_version(OLD))
    daily = export.build_daily(conn, outlets=[])
    days = [day for m in daily.values() for day in m["days"].values()]
    assert sum(d["labels_on_older_prompt"] for d in days) == 1
    series = export.build_global_series(daily)
    assert sum(p["labels_on_older_prompt"] for p in series) == 1
