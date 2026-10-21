"""The account's own usage limit (BadRequestError 400 "You have reached your specified API usage limits.
You will regain access on <date> at 00:00 UTC."): the classify_llm stage and the reliability study
stop calling, finish normally, keep the date, and make no request before it. Every date here is set
from the clock (ten days on), so the tests hold on any day, under tests/conftest.py's fake clock too."""
import datetime as dt
import json
import sqlite3
from pathlib import Path

import anthropic
import httpx

from pipeline import classify_llm as cl, config, second_rater, store

NOW = dt.datetime.now(dt.timezone.utc)
REGAIN = (NOW + dt.timedelta(days=10)).replace(hour=0, minute=0, second=0, microsecond=0)
MESSAGE = ("You have reached your specified API usage limits. You will regain access on %s at 00:00 UTC."
           % REGAIN.date().isoformat())
UNTIL = REGAIN.isoformat()


def _limit_error(cls=anthropic.BadRequestError, status=400, message=MESSAGE, kind="invalid_request_error"):
    body = {"type": "error", "error": {"type": kind, "message": message}}
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages/batches"))
    return cls("Error code: %d - %s" % (status, body), response=response, body=body)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    return conn


def _waiting(conn, n):
    ids = []
    for i in range(n):
        url = "https://e.com/p%d" % i
        ids.append(store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": "ITA", "language": "it",
                                                  "title": "Title %d" % i, "status": "awaiting_llm", "gate_relevant": 1}))
        store.save_body(store.url_hash(url), "Body about China %d" % i)
    conn.commit()
    return ids


class _Client:
    """Raises the account limit on every request, and counts them."""

    def __init__(self, error=None):
        self.calls = 0
        outer = self

        def fail(*a, **k):
            outer.calls += 1
            raise error or _limit_error()

        self.messages = type("M", (), {"create": staticmethod(fail),
                                       "batches": type("B", (), {"create": staticmethod(fail),
                                                                 "retrieve": staticmethod(fail),
                                                                 "results": staticmethod(fail)})()})()


def _last_stage(conn, stage="classify_llm"):
    return conn.execute("SELECT * FROM run_log WHERE stage=? ORDER BY id DESC LIMIT 1", (stage,)).fetchone()


def test_the_exact_error_is_recognised_and_others_are_not():
    assert cl.provider_limit_of(_limit_error()) == {"until": UNTIL, "message": MESSAGE}
    assert cl.provider_limit_of(_limit_error(anthropic.RateLimitError, 429,
                                             "Your spend limit was reached."))["message"] == "Your spend limit was reached."
    billing = cl.provider_limit_of(_limit_error(anthropic.PermissionDeniedError, 403, "Denied.", kind="billing_error"),
                                   now=NOW)
    next_midnight = (NOW + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    assert billing["until"] == next_midnight.isoformat(), "no date named: paused to the next UTC midnight"
    assert cl.provider_limit_of(_limit_error(message="Your credit balance is too low to access the Anthropic API."))
    # Other errors keep their usual handling.
    assert cl.provider_limit_of(_limit_error(message="messages.0.content: field required")) is None
    assert cl.provider_limit_of(_limit_error(anthropic.RateLimitError, 429, "Number of requests has exceeded your rate limit.",
                                             kind="rate_limit_error")) is None
    assert cl.provider_limit_of(ValueError(MESSAGE)) is None
    assert cl.provider_limit_of(anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))) is None


def test_a_batch_run_hitting_the_limit_completes_and_later_runs_skip_the_api(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    ids = _waiting(conn, 3)
    client = _Client()
    counts = cl.run(conn, "r1", batch=True, client=client)     # used to raise out of _submit_batch
    assert client.calls == 1
    assert counts["skipped"] == "provider_limit" and counts["provider_limit"]["until"] == UNTIL
    assert counts["pending"] == 3
    assert [store.get_article(conn, i)["status"] for i in ids] == ["awaiting_llm"] * 3
    assert conn.execute("SELECT COUNT(*) FROM llm_batches").fetchone()[0] == 0
    stage = _last_stage(conn)
    assert stage["ok"] == 1 and UNTIL in stage["notes"]
    assert cl.active_provider_limit(conn)["until"] == UNTIL
    # The next run, before the date: no request at all, not even one that fails.
    again = _Client()
    counts = cl.run(conn, "r2", batch=True, client=again)
    assert again.calls == 0 and counts["skipped"] == "provider_limit" and _last_stage(conn)["ok"] == 1
    sync = _Client()
    assert cl.run(conn, "r3", client=sync)["skipped"] == "provider_limit" and sync.calls == 0
    # After the date, calls resume.
    assert cl.active_provider_limit(conn, now=REGAIN + dt.timedelta(seconds=1)) is None
    conn.execute("UPDATE llm_provider_limit SET until=?", ((NOW - dt.timedelta(days=18)).isoformat(),))
    fake = cl.DryRunClient()
    counts = cl.run(conn, "r4", client=fake)
    assert len(fake.calls) == 3 and counts["classified"] == 3 and "skipped" not in counts


def test_the_synchronous_path_stops_at_the_first_limit_error(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(cl.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("no retry pause")))
    conn = _db()
    _waiting(conn, 4)
    client = _Client()
    counts = cl.run(conn, "s1", client=client)
    assert client.calls == 1 and counts["errors"] == 0 and counts["provider_limit"]["until"] == UNTIL
    assert counts["pending"] == 4 and _last_stage(conn)["ok"] == 1
    assert cl.active_provider_limit(conn)


def test_an_other_api_error_keeps_its_behaviour(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(cl.time, "sleep", lambda s: None)
    conn = _db()
    _waiting(conn, 4)
    client = _Client(_limit_error(message="messages.0.content: field required"))
    counts = cl.run(conn, "s1", client=client)
    assert client.calls == 3 and counts["errors"] == 3 and _last_stage(conn)["ok"] == 0
    assert cl.active_provider_limit(conn) is None
    # The batch path still lets an unrelated error out, as before.
    try:
        cl.run(conn, "s2", batch=True, client=_Client(_limit_error(message="messages.0.content: field required")))
    except anthropic.BadRequestError:
        pass
    else:
        raise AssertionError("an unrelated batch error is not swallowed")


def test_collecting_a_batch_into_the_limit_pauses_the_run(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    ids = _waiting(conn, 2)
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version, kind) "
                 "VALUES ('b1', ?, 'submitted', ?, 'm', 'classify')", (store.utcnow(), json.dumps(ids[:1])))
    store.update_article(conn, ids[0], status="llm_submitted")
    conn.commit()
    client = _Client()
    counts = cl.run(conn, "c1", batch=True, client=client)
    assert client.calls == 1 and counts["skipped"] == "provider_limit" and _last_stage(conn)["ok"] == 1
    assert conn.execute("SELECT status FROM llm_batches").fetchone()[0] == "submitted", "collected once access returns"


def test_the_reliability_study_records_and_respects_the_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    # A study batch waiting to be collected: collecting it meets the limit, which is kept, not a failure.
    conn.execute("INSERT INTO llm_batches(batch_id, submitted_at, status, article_ids, model_version, kind) "
                 "VALUES ('s1', ?, 'submitted', '[]', 'm', 'study')", (store.utcnow(),))
    conn.commit()
    client = _Client()
    out = second_rater.auto(conn, client=client)
    assert client.calls == 1 and out["skipped"] == "provider_limit" and "error" not in out
    assert _last_stage(conn, "reliability_study")["ok"] == 1 and cl.active_provider_limit(conn)["until"] == UNTIL
    later = _Client()
    out = second_rater.auto(conn, submit=True, force=True, client=later)
    assert later.calls == 0 and out["skipped"] == "provider_limit"


def test_rejudge_stops_at_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    rows = []
    for i in range(3):
        url = "https://e.com/r%d" % i
        store.save_body(store.url_hash(url), "Body about China")
        rows.append({"id": i, "url_hash": store.url_hash(url), "title": "T", "llm_trigger": None})
    client = _Client()
    pairs, stats = second_rater.rejudge(conn, rows, "m", client=client)
    assert pairs == [] and client.calls == 1 and cl.active_provider_limit(conn)


def test_meta_carries_the_flag():
    src = (Path(__file__).resolve().parent.parent / "pipeline" / "export.py").read_text(encoding="utf-8")
    assert "provider_limit=classify_llm.active_provider_limit(conn)" in src
    conn = _db()
    assert cl.active_provider_limit(conn) is None, "a database without the table has no limit"
    cl.record_provider_limit(conn, {"until": UNTIL, "message": MESSAGE})
    got = cl.active_provider_limit(conn, now=NOW)
    assert got["until"] == UNTIL and got["message"] == MESSAGE and got["recorded_at"]


def test_a_raised_limit_is_noticed_within_the_recheck_window(monkeypatch, tmp_path):
    """The owner can raise the limit before the date in the message, so a limit recorded more than
    PROVIDER_LIMIT_RECHECK ago lets one run try again: refused, it is recorded afresh; served, cleared."""
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(cl.time, "sleep", lambda s: None)
    conn = _db()
    _waiting(conn, 3)
    cl.record_provider_limit(conn, {"until": UNTIL, "message": MESSAGE})
    old = (NOW - cl.PROVIDER_LIMIT_RECHECK - dt.timedelta(hours=1)).isoformat()
    conn.execute("UPDATE llm_provider_limit SET recorded_at=?", (old,))
    assert cl.active_provider_limit(conn)["until"] == UNTIL, "the site still shows the pause"
    assert cl.active_provider_limit(conn, recheck=True) is None, "but a stage that would call tries again"
    # Still refused: one request, the limit recorded afresh, and the next run makes none.
    refused = _Client()
    counts = cl.run(conn, "p1", client=refused)
    assert refused.calls == 1 and counts["provider_limit"]["until"] == UNTIL
    assert cl.active_provider_limit(conn, recheck=True)["until"] == UNTIL
    again = _Client()
    assert cl.run(conn, "p2", client=again)["skipped"] == "provider_limit" and again.calls == 0
    # Raised meanwhile: the request goes through and the limit is cleared.
    conn.execute("UPDATE llm_provider_limit SET recorded_at=?", (old,))
    served = cl.DryRunClient()
    counts = cl.run(conn, "p3", client=served)
    assert counts["classified"] == 3 and "provider_limit" not in counts
    assert cl.active_provider_limit(conn) is None
    assert conn.execute("SELECT COUNT(*) FROM llm_provider_limit").fetchone()[0] == 0
