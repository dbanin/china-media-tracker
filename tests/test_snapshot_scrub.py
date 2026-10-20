"""The public database snapshot is a scrubbed copy: no bylines, summaries, raw model output, coder
names or email addresses, and still a database the runner can restore from (DECISIONS 7)."""
import json
import sqlite3

import pytest

from pipeline import labels, snapshot_scrub, store


def _build(path):
    conn = store.connect(path)
    aid = store.insert_discovered(conn, {
        "url": "https://news.test/a?contact=desk@news.test", "outlet_id": "it_x", "country": "ITA", "language": "it",
        "title": "China and Italy sign a port deal", "summary": "Write to jane.doe@news.test for the full story.",
        "author": "Jane Doe", "status": "fetched", "gate_relevant": 1})
    store.insert_classification(conn, aid, "llm", "B", 0.7, evidence_quote="Officials said, per press@gov.test, it was fine.",
                                model_version="m", raw_response=json.dumps({"category": "B", "labelled_at": "2026-10-01"}))
    other = store.insert_discovered(conn, {"url": "https://news.test/b", "outlet_id": "it_x", "country": "ITA",
                                           "language": "it", "title": "t", "status": "fetched", "gate_relevant": 1})
    store.insert_classification(conn, other, "llm", "C", 0.9, model_version="m", raw_response='{"category": "C", "why": "quokka rationale"}')
    cid = conn.execute("SELECT id FROM classifications WHERE article_id=?", (aid,)).fetchone()[0]
    store.insert_human_review(conn, aid, cid, "B", "Maria Rossi", note="checked with Luca")
    store.insert_human_review(conn, other, None, "C", "Paolo Bianchi")
    store.insert_human_review(conn, other, None, "C", "Maria Rossi")
    store.insert_human_review(conn, other, None, "C", "c3")
    from pipeline import doctrine
    doctrine.ensure_snippet_table(conn)
    conn.execute("INSERT INTO doctrine_snippets(article_id, family, version, from_body, matches) VALUES (?,?,?,?,?)",
                 (aid, "fam", "v1", 1, json.dumps([["win-win", "a wombat excerpt of the article body itself"]])))
    conn.commit()
    conn.close()


def test_scrubbed_copy(tmp_path):
    src, dst = tmp_path / "tracker.db", tmp_path / "snapshot.db"
    _build(src)
    before = src.read_bytes()
    counts = snapshot_scrub.scrub(src, dst)
    assert src.read_bytes() == before, "the working database is never changed"
    assert counts["articles.author"] == 1 and counts["articles.summary"] == 1
    raw = dst.read_bytes()
    assert counts["dropped.doctrine_snippets"] == 1
    for secret in (b"wombat excerpt", b"Jane Doe", b"jane.doe@news.test", b"Maria Rossi", b"Paolo Bianchi", b"press@gov.test", b"quokka rationale"):
        assert secret not in raw, secret        # vacuumed: not left in free pages either
    conn = store.connect(dst)                    # the runner can open and migrate it
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='doctrine_snippets'").fetchone()
    a = conn.execute("SELECT author, summary, url, url_hash FROM articles ORDER BY id").fetchone()
    assert a["author"] is None and a["summary"] is None
    # a URL and its hash are left alone, so they still match
    assert a["url"] == "https://news.test/a?contact=desk@news.test" and a["url_hash"] == store.url_hash(a["url"])
    quotes = [r[0] for r in conn.execute("SELECT evidence_quote FROM classifications ORDER BY id")]
    assert quotes[0] == "Officials said, per %s, it was fine." % snapshot_scrub.EMAIL_MARK
    raws = [r[0] for r in conn.execute("SELECT raw_response FROM classifications ORDER BY id")]
    assert raws == [json.dumps({"labelled_at": "2026-10-01"}), None]
    # session labels are still told apart from API labels
    assert labels.session_label_counts(conn)["total"] == 1
    # coders become stable pseudonyms, so agreement between coders can still be computed
    revs = [tuple(r) for r in conn.execute("SELECT reviewer, note FROM human_reviews ORDER BY id")]
    assert revs == [("coder_1", None), ("coder_2", None), ("coder_1", None), ("c3", None)]
    assert conn.execute("SELECT value FROM snapshot_info WHERE key='scrub_version'").fetchone()[0] == snapshot_scrub.SCRUB_VERSION


def test_never_scrubs_in_place(tmp_path):
    src = tmp_path / "tracker.db"
    _build(src)
    with pytest.raises(ValueError):
        snapshot_scrub.scrub(src, src)


def test_email_pattern():
    s = snapshot_scrub.scrub_emails
    assert s("mail a.b+c@mail.example.org now") == "mail %s now" % snapshot_scrub.EMAIL_MARK
    assert s("https://medium.com/@writer and @handle") == "https://medium.com/@writer and @handle"
    assert s(None) is None and s(3) == 3
