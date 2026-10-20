"""A model label made on a stub goes back to the model when the article's body is refreshed.

classify_rules keeps a model label when the body under it is extracted or fetched again, unless the
rules on the new body say state origin or unreadable. The model never read the new text, credit line
included, so when the new body differs materially from the stub (a different body_hash, and the old
body unreadable, extracted under an older reading of its template, or at most half as long) the label
is marked body_refreshed (classify_rules.note_body_refresh) and classify_llm.relabel_candidates queues
the article again, right after the unchecked state sourcing labels. The old label stays current until
the new one arrives.
"""
import json
import sqlite3

from pipeline import classify_llm as cl
from pipeline import classify_rules as cr
from pipeline import config, fetch_articles, store
from tests.test_stale_bodies import ANTARA_URL, PPR_STUB, PPR_TITLE, PPR_URL, _article as _stale_article, _page, db  # noqa: F401

OLD = "claude-sonnet-5"
CURRENT = cl.with_prompt_version(OLD)
ANTARA_TITLE = 'Hangzhou Investment Promotion Bureau: Hangzhou Finds a "Gateway to the Sea" in Hong Kong'
ANTARA_STUB = "Hong Kong is the gateway. " * 30


def _mem():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    return conn


def _labelled(conn, n, when, category="C", model_version=OLD, body="Body about China %d", dup=None,
              refreshed=None, method="llm"):
    url = "https://e.com/r%d" % n
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": "ITA", "language": "it",
                                         "title": "Title %d" % n, "status": "classified", "gate_relevant": 1})
    text = body % n if "%d" in body else body
    store.update_article(conn, aid, status="classified", body_hash=store.save_body(store.url_hash(url), text),
                         body_chars=len(text), dup_group_id=dup)
    store.insert_classification(conn, aid, method, category, 0.7, evidence_quote="q", model_version=model_version)
    conn.execute("UPDATE classifications SET classified_at=?, body_refreshed=? WHERE article_id=?",
                 (when, refreshed, aid))
    conn.commit()
    return aid


def _marker(conn, aid):
    return store.current_classification(conn, aid)["body_refreshed"]


# ---------------------------------------------------------------------------
# Marking
# ---------------------------------------------------------------------------

def test_a_kept_model_label_on_a_stale_stub_is_marked_and_relabelled_on_the_release(db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(fetch_articles, "process_article", lambda f, r: (_ for _ in ()).throw(AssertionError("no fetch")))
    aid = _stale_article(db, ANTARA_URL, "id_antara_en", ANTARA_TITLE, ANTARA_STUB, html=_page("en_id_antara_release"),
                         label=("llm", "C", "2026.09.9"))
    db.execute("UPDATE classifications SET model_version=? WHERE article_id=?", (CURRENT, aid))
    db.commit()
    monkeypatch.setattr(cl, "relabel_allowance", lambda ceiling, used, now, share=None: 5)
    out = cr.reclassify(db, "t")
    assert out["stale_body"] == 1
    cur = store.current_classification(db, aid)
    # The rules do not say state origin on the release, so the model label stays current, marked.
    assert (cur["method"], cur["category"], cur["model_version"]) == ("llm", "C", CURRENT)
    assert cur["body_refreshed"].split(" ", 1)[1].startswith("older extraction (Antara English lead paragraph)")
    # On the current prompt, and queued all the same.
    assert [r["id"] for r in cl.relabel_candidates(db)] == [aid]
    seen = []
    counts = cl.run(db, "t", client=cl.DryRunClient(answer=lambda body: seen.append(body) or "A"))
    assert counts["relabel_body_refreshed"] == 1 and counts["relabelled"] == 1
    assert "(ANTARA/PRNewswire)" in seen[0], "the model reads the refreshed body"
    new = store.current_classification(db, aid)
    assert new["category"] == "A" and new["body_refreshed"] is None and cl.on_current_prompt(new["model_version"])
    old = db.execute("SELECT * FROM classifications WHERE article_id=? AND is_current=0 AND method='llm'", (aid,)).fetchone()
    assert old["category"] == "C" and old["body_refreshed"], "the stub label is kept, not current"
    assert cl.relabel_candidates(db) == []


def test_a_stale_stub_fetched_again_marks_the_label(db, monkeypatch):  # noqa: F811
    aid = _stale_article(db, PPR_URL, "sg_straitstimes", PPR_TITLE, PPR_STUB, label=("llm", "C", config.RULESET_VERSION))

    def refetch(factory, row):
        text = "Xinhua Silk Road: Chinese youths explore Mazu culture\nBEIJING, Sept. 29, 2026 /PRNewswire/ -- " + "x " * 300
        store.update_article(db, row["id"], status="fetched", body_hash=store.save_body(row["url_hash"], text),
                             body_chars=len(text), extract_version=config.EXTRACT_VERSION)
        db.commit()
        return {"id": row["id"], "status": "fetched"}
    monkeypatch.setattr(fetch_articles, "process_article", refetch)
    assert "/PRNewswire/" in cr.ensure_body(db, store.get_article(db, aid))
    assert "older extraction (Straits Times paid release payload)" in _marker(db, aid)


def test_a_missing_body_fetched_again_as_the_same_text_or_a_light_edit_leaves_the_label(db, monkeypatch):  # noqa: F811
    url = "https://www.example.com/a-story"
    body = "An ordinary story about China. " * 20
    aid = _stale_article(db, url, "o", "A story", body, label=("llm", "C", config.RULESET_VERSION))
    h = store.url_hash(url)
    store.body_path(h).unlink()   # a runner that did not fetch it
    texts = [body, body + " Updated."]

    def refetch(factory, row):
        text = texts.pop(0)
        store.update_article(db, row["id"], status="fetched", body_hash=store.save_body(h, text), body_chars=len(text))
        db.commit()
        return {"id": row["id"], "status": "fetched"}
    monkeypatch.setattr(fetch_articles, "process_article", refetch)
    monkeypatch.setattr("pipeline.registry.is_mine", lambda outlet: True)
    for _ in range(2):
        assert cr.ensure_body(db, store.get_article(db, aid))
        assert _marker(db, aid) is None
        store.body_path(h).unlink()


def test_note_body_refresh_needs_a_different_hash_and_a_stub(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _mem()
    aid = _labelled(conn, 1, "2026-10-01T00:00:00+00:00", body="x" * 300)
    a = store.get_article(conn, aid)
    old = (a["body_hash"], a["body_chars"])

    def replace(text):
        store.update_article(conn, aid, body_hash=store.save_body(a["url_hash"], text), body_chars=len(text))

    assert cr.note_body_refresh(conn, aid, old, "unreadable: no running text") == 0, "same body: nothing to judge again"
    replace("y" * 400)
    assert cr.note_body_refresh(conn, aid, old) == 0, "a different body of about the same length is not a stub"
    assert _marker(conn, aid) is None
    replace("y" * 600)
    assert cr.note_body_refresh(conn, aid, old) == 1
    assert "short body (300 of 600 characters)" in _marker(conn, aid)
    replace("z" * 310)
    assert cr.note_body_refresh(conn, aid, old, "unreadable: a template page") == 1
    assert _marker(conn, aid).endswith(" unreadable: a template page")
    # A rules label is never marked: the rules read the new body already.
    rules = _labelled(conn, 2, "2026-10-01T00:00:00+00:00", body="x" * 100, method="rules")
    r = store.get_article(conn, rules)
    store.update_article(conn, rules, body_hash=store.save_body(r["url_hash"], "w" * 900), body_chars=900)
    assert cr.note_body_refresh(conn, rules, (r["body_hash"], r["body_chars"])) == 0


def test_a_short_body_read_again_from_its_saved_page_marks_the_label(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _mem()
    aid = _labelled(conn, 1, "2026-10-01T00:00:00+00:00", body="Short standfirst about China.")
    row = store.get_article(conn, aid)
    long_text = "A paragraph of running text about China and its trade. " * 30
    monkeypatch.setattr(cr.extract, "extract", lambda html, url: {"text": long_text})
    monkeypatch.setattr(cr.extract, "detect_paywall", lambda html, text: (False, None))
    monkeypatch.setattr(cr.extract, "detect_unreadable", lambda html, text, title=None: (False, None))
    assert cr.reread_saved(conn, row, "<html></html>", short=store.load_body(row["url_hash"])) == long_text
    assert _marker(conn, aid).endswith(" short body")


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------

def test_refreshed_labels_come_right_after_unchecked_state_sourcing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _mem()
    mark = "2026-10-12T00:00:00+00:00 older extraction (x)"
    a_old = _labelled(conn, 1, "2026-09-01T00:00:00+00:00", category="A")
    c_official = _labelled(conn, 2, "2026-09-02T00:00:00+00:00", category="C")
    conn.execute("UPDATE classifications SET china_sources_cited=?, independent_confirmation_present=0 WHERE article_id=?",
                 (json.dumps(["MOFA"]), c_official))
    refreshed_new = _labelled(conn, 3, "2026-10-10T00:00:00+00:00", category="C", model_version=CURRENT, refreshed=mark)
    refreshed_old = _labelled(conn, 4, "2026-09-03T00:00:00+00:00", category="not_relevant", refreshed=mark)
    b = _labelled(conn, 5, "2026-09-20T00:00:00+00:00", category="B")
    b_refreshed = _labelled(conn, 6, "2026-09-21T00:00:00+00:00", category="B", model_version=CURRENT, refreshed=mark)
    _labelled(conn, 7, "2026-09-04T00:00:00+00:00", category="C", model_version=CURRENT)   # current, not refreshed
    conn.commit()
    # A model state origin label with no route yet counts as unattributed, and those come first (RELABEL_PRIORITY).
    assert [r["id"] for r in cl.relabel_candidates(conn)] == [a_old, b, b_refreshed, refreshed_old, refreshed_new, c_official]


def test_a_refreshed_relabel_copies_only_a_sibling_label_made_since(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(cl, "relabel_allowance", lambda ceiling, used, now, share=None: 5)
    conn = _mem()
    mark = "2026-10-12T06:00:00+00:00 older extraction (x)"
    head = _labelled(conn, 1, "2026-10-12T05:00:00+00:00", category="C", model_version=CURRENT, refreshed=mark)
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (head, head))
    before = _labelled(conn, 2, "2026-10-12T05:30:00+00:00", category="C", model_version=CURRENT, dup=head)
    conn.commit()
    row = cl.relabel_candidates(conn)[0]
    assert row["id"] == head
    assert cl._copy_from_dup_group(conn, row, current_prompt_only=True, refreshed_after=cl._refreshed_at(row)) is False
    conn.execute("UPDATE classifications SET classified_at='2026-10-12T07:00:00+00:00' WHERE article_id=?", (before,))
    assert cl._copy_from_dup_group(conn, row, current_prompt_only=True, refreshed_after=cl._refreshed_at(row)) is True
    assert store.current_classification(conn, head)["body_refreshed"] is None


def test_a_label_on_a_stub_is_never_copied_and_one_relabel_settles_the_group(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    monkeypatch.setattr(cl, "relabel_allowance", lambda ceiling, used, now, share=None: 0)
    conn = _mem()
    mark = "2026-01-02T00:00:00+00:00 older extraction (x)"
    head = _labelled(conn, 1, "2026-01-01T00:00:00+00:00", category="C", model_version=CURRENT, refreshed=mark)
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (head, head))
    sib = _labelled(conn, 2, "2026-01-01T00:00:00+00:00", category="C", model_version=CURRENT, dup=head, refreshed=mark)
    fresh = store.insert_discovered(conn, {"url": "https://e.com/new", "outlet_id": "o", "country": "ITA", "language": "it",
                                           "title": "New", "status": "awaiting_llm", "gate_relevant": 1})
    store.save_body(store.url_hash("https://e.com/new"), "Body about China, new")
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (head, fresh))
    conn.commit()
    # The new placement does not take a label that rests on a stub.
    assert cl._copy_from_dup_group(conn, store.get_article(conn, fresh)) is False
    client = cl.DryRunClient(answer=lambda body: "A")
    counts = cl.run(conn, "t", client=client)
    # One call for the new article; its label then settles both refreshed siblings for free.
    assert len(client.calls) == 1 and counts["classified"] == 1 and counts["relabel_copied"] == 2
    for aid in (head, sib, fresh):
        cur = store.current_classification(conn, aid)
        assert cur["category"] == "A" and cur["body_refreshed"] is None
    assert cl.relabel_candidates(conn) == []
