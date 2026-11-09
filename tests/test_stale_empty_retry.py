"""Pages that found no text under an extraction their template has since changed are fetched once more."""
from pipeline import config, extract, fetch_articles, lookalike, store


def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "EXTRACT_VERSION", "2026.11.1")
    monkeypatch.setattr(extract, "EXTRACTION_CHANGES", (
        ("2026.10.1", "https://old.test/", "an older change"),
        ("2026.11.1", "https://www.chosun.test/", "Arc story payload"),
    ))
    conn = store.connect(tmp_path / "s.db")
    ids = {}
    rows = (
        ("chosun", "https://www.chosun.test/a", "extraction_empty", None, 1, 1),
        ("chosun_now", "https://www.chosun.test/b", "extraction_empty", "2026.11.1", 1, 1),
        ("chosun_spent", "https://www.chosun.test/c", "extraction_empty", None, 3, 1),
        ("chosun_irrelevant", "https://www.chosun.test/d", "extraction_empty", None, 1, 0),
        ("chosun_403", "https://www.chosun.test/e", "http_403", None, 1, 1),
        ("old_before", "https://old.test/a", "extraction_empty", None, 1, 1),
        ("old_after", "https://old.test/b", "extraction_empty", "2026.10.1", 1, 1),
        ("unlisted", "https://other.test/a", "extraction_empty", None, 1, 1),
    )
    for name, url, reason, version, attempts, gate in rows:
        ids[name] = store.insert_discovered(conn, {"url": url, "outlet_id": "kr_x", "country": "KOR", "language": "ko",
                                                   "title": "China %s" % name, "status": "failed", "gate_relevant": gate})
        store.update_article(conn, ids[name], fail_reason=reason, extract_version=version,
                             fetch_attempts=attempts, discovered_at="2026-09-10T00:00:00+00:00")
    conn.commit()
    return conn, ids


def test_only_relevant_empty_pages_of_a_changed_template_are_taken_once_whatever_their_age(tmp_path, monkeypatch):
    conn, ids = _db(tmp_path, monkeypatch)
    got = {r["id"] for r in fetch_articles.retry_candidates(conn)}
    assert got == {ids["chosun"], ids["old_before"]}
    assert len(fetch_articles.retry_candidates(conn)) == 2      # one row each, even under several entries


def test_a_page_that_fails_again_under_the_current_extraction_is_not_taken_again(tmp_path, monkeypatch):
    conn, ids = _db(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda u: (True, None))
    monkeypatch.setattr(fetch_articles, "get_page", lambda u: (calls.append(u) or "<html><body>menu</body></html>", 200, None))
    row = store.get_article(conn, ids["chosun"])
    out = fetch_articles.process_article(lambda: store.connect(tmp_path / "s.db"), row)
    assert out["reason"] == "extraction_empty" and calls == ["https://www.chosun.test/a"]
    conn2 = store.connect(tmp_path / "s.db")
    assert store.get_article(conn2, ids["chosun"])["extract_version"] == "2026.11.1"
    assert ids["chosun"] not in {r["id"] for r in fetch_articles.retry_candidates(conn2)}


def test_lookalike_times_ignore_values_that_are_not_date_strings():
    assert lookalike._ts(1731100000) is None and lookalike._ts(b"2026") is None
    assert lookalike._ts("2026-11-09T10:00:00Z") is not None
