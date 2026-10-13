"""Body text near-duplicate linking (pipeline/body_dups.py), the second pass after title matching."""
import datetime as dt
import random

import pytest

from pipeline import body_dups, config, export, store

_VOCAB = ("ministry officials said the agreement would expand trade between both countries while analysts "
          "warned that tariffs could rise again before the summit next month in the capital where leaders "
          "plan to sign several memoranda covering energy ports railways and digital infrastructure projects "
          "that critics describe as costly and opaque for taxpayers in the region").split()


def _text(seed: int, n: int = 220) -> str:
    rnd = random.Random(seed)
    return " ".join(rnd.choice(_VOCAB) for _ in range(n)) + "."


WIRE = _text(1)
# The same wire copy under another outlet's own lede and closing line: most of the text is shared.
REWRITE = "In a move watched closely across the region, " + WIRE[: int(len(WIRE) * 0.85)] + " Our correspondent adds detail."
OTHER = _text(2)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


def _article(conn, url, outlet, country, title, body, days_ago=0.0, category=None):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": country, "language": "en",
                                         "title": title, "status": "fetched", "gate_relevant": 1})
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).replace(microsecond=0).isoformat()
    row = conn.execute("SELECT url_hash FROM articles WHERE id=?", (aid,)).fetchone()
    h = store.save_body(row["url_hash"], body)
    conn.execute("UPDATE articles SET discovered_at=?, published_at=?, body_hash=?, body_chars=? WHERE id=?",
                 (when, when, h, len(body), aid))
    if category:
        store.insert_classification(conn, aid, "rules", category, 1.0)
    conn.commit()
    return aid


def _links(conn):
    return {(r["a"], r["b"]) for r in conn.execute("SELECT a, b FROM body_links")}


def test_signature_estimates_overlap():
    a = body_dups.signature(body_dups.shingles(WIRE))
    assert body_dups.similarity(a, a) == 1.0
    assert body_dups.similarity(a, body_dups.signature(body_dups.shingles(REWRITE))) >= body_dups.JACCARD_THRESHOLD
    assert body_dups.similarity(a, body_dups.signature(body_dups.shingles(OTHER))) < 0.2
    n, sig = body_dups.fingerprint(WIRE)
    assert n > body_dups.MIN_SHINGLES and len(sig) == 4 * body_dups.SIG_BINS
    assert body_dups.fingerprint("too short to compare")[1] is None


def test_unspaced_scripts_use_character_shingles():
    ja = "中国商務省は輸出規制を強化すると発表した。対象は無人機の部品で、来月から適用される見通しだ。" * 3
    hs = body_dups.shingles(ja)
    assert len(hs) > body_dups.MIN_SHINGLES  # word splitting would leave a handful of long runs


def test_rewritten_headline_is_linked_across_outlets_and_countries(conn):
    a = _article(conn, "https://a.test/1", "us_wire", "USA", "US imposes sanctions on 13 tied to Iran", WIRE)
    b = _article(conn, "https://b.test/1", "sg_paper", "SGP", "Washington targets weapons network", REWRITE, days_ago=1)
    c = _article(conn, "https://c.test/1", "in_paper", "IND", "Something else entirely", OTHER)
    res = body_dups.run(conn)
    assert res["backfill"]["fingerprinted"] == 3
    assert _links(conn) == {(min(a, b), max(a, b))}
    groups = body_dups.item_groups(conn)
    assert groups[a] == groups[b] == min(a, b) and c not in groups
    # Title groups are untouched: labels are copied only along those.
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE dup_group_id IS NOT NULL").fetchone()[0] == 0
    assert body_dups.run(conn)["links"] == 0  # nothing left to check


def test_window_same_outlet_and_short_bodies_are_not_linked(conn):
    _article(conn, "https://a.test/2", "out_a", "USA", "First", WIRE)
    _article(conn, "https://a.test/3", "out_b", "USA", "Eight days later", WIRE, days_ago=8)
    _article(conn, "https://a.test/4", "out_a", "USA", "First, republished", REWRITE, days_ago=0.5)
    _article(conn, "https://a.test/5", "out_c", "USA", "Teaser", "too short to compare " * 3)
    body_dups.run(conn)
    assert _links(conn) == set()


def test_template_bodies_are_never_linked(conn):
    # One outlet returned the same page text under two unrelated headlines: an extraction template.
    t1 = _article(conn, "https://t.test/1", "tmpl", "KOR", "Xi arrives in Pyongyang", WIRE)
    t2 = _article(conn, "https://t.test/2", "tmpl", "KOR", "Seoul budget talks stall over pensions", WIRE)
    x = _article(conn, "https://x.test/1", "other", "JPN", "Unrelated outlet with the same text", WIRE)
    res = body_dups.run(conn)
    assert res["templates"] == 2
    assert _links(conn) == set()
    flagged = {r[0] for r in conn.execute("SELECT article_id FROM body_fingerprints WHERE template=1")}
    assert flagged == {t1, t2} and x not in flagged


def test_title_group_and_body_link_join(conn):
    a1 = _article(conn, "https://a.test/6", "o1", "FRA", "Same headline here today", WIRE)
    a2 = _article(conn, "https://a.test/7", "o2", "FRA", "Same headline here today", OTHER)
    b = _article(conn, "https://a.test/8", "o3", "BEL", "Rewritten", REWRITE)
    store.set_dup_group(conn, a1, a1)
    store.set_dup_group(conn, a2, a1)
    conn.commit()
    body_dups.run(conn)
    groups = body_dups.item_groups(conn)
    assert groups.get(a1, a1) == groups.get(b, b) == min(a1, b)


def test_missing_body_is_marked_and_not_retried(conn, tmp_path):
    aid = _article(conn, "https://m.test/1", "o1", "USA", "Gone", WIRE)
    row = conn.execute("SELECT url_hash FROM articles WHERE id=?", (aid,)).fetchone()
    store.body_path(row["url_hash"]).unlink()
    assert body_dups.run(conn)["backfill"]["missing_body"] == 1
    assert body_dups.run(conn)["backfill"]["todo"] == 0
    assert conn.execute("SELECT shingles FROM body_fingerprints WHERE article_id=?", (aid,)).fetchone()[0] == -1


def test_backfill_stops_at_deadline(conn):
    for i in range(3):
        _article(conn, "https://d.test/%d" % i, "o%d" % i, "USA", "t%d" % i, _text(10 + i))
    res = body_dups.run(conn, deadline=0.0)
    assert res["backfill"]["stopped_deadline"] and res["backfill"]["fingerprinted"] == 0
    assert body_dups.stats(conn)["bodies_without_fingerprint"] == 3


def test_refetched_body_drops_old_links(conn):
    _article(conn, "https://r.test/1", "o1", "USA", "A", WIRE)
    b = _article(conn, "https://r.test/2", "o2", "CAN", "B", REWRITE)
    body_dups.run(conn)
    assert _links(conn)
    body_dups.record(conn, conn.execute("SELECT * FROM articles WHERE id=?", (b,)).fetchone(), OTHER)
    assert _links(conn) == set()
    body_dups.link_pending(conn)
    assert _links(conn) == set()


def test_export_counts_underlying_items_through_body_links(conn):
    a = _article(conn, "https://e.test/1", "o1", "ITA", "Wire headline one", WIRE, category="A")
    b = _article(conn, "https://e.test/2", "o2", "ITA", "A rewritten headline", REWRITE, category="A")
    c = _article(conn, "https://e.test/3", "o3", "DEU", "German placement", WIRE, category="A")
    export.rebuild_rollups(conn, [])
    before = {r["country"]: (r["n"], r["n_unique_items"]) for r in
              conn.execute("SELECT country, SUM(n) n, SUM(n_unique_items) n_unique_items FROM daily_counts "
                           "WHERE category='A' AND scope='all' GROUP BY country")}
    assert before == {"ITA": (2, 2), "DEU": (1, 1)}
    body_dups.run(conn)
    export.rebuild_rollups(conn, [])
    after = {r["country"]: (r["n"], r["n_unique_items"]) for r in
             conn.execute("SELECT country, SUM(n) n, SUM(n_unique_items) n_unique_items FROM daily_counts "
                          "WHERE category='A' AND scope='all' GROUP BY country")}
    # Placements (the category counts) do not move; the Italian pair becomes one underlying item,
    # and the link to Germany leaves Germany's figure as it was.
    assert after == {"ITA": (2, 1), "DEU": (1, 1)}
    listed = {e["id"]: e["dup_group"] for rows in export.build_articles(conn, outlets=[]).values() for e in rows}
    assert listed[a] == listed[b] == listed[c] == min(a, b, c)


def test_fetch_stage_fingerprints_where_the_body_is_saved(conn, tmp_path, monkeypatch):
    from pipeline import extract, fetch_articles
    aid = store.insert_discovered(conn, {"url": "https://f.test/1", "outlet_id": "o1", "country": "USA", "language": "en",
                                         "title": "t", "status": "queued", "gate_relevant": 1})
    conn.commit()
    row = conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()
    monkeypatch.setattr(fetch_articles, "can_fetch", lambda url: (True, "no_robots"))
    monkeypatch.setattr(fetch_articles, "get_page", lambda url: ("<html></html>", 200, None))
    monkeypatch.setattr(extract, "extract", lambda html, url: {"text": WIRE, "author": None, "date": None})
    monkeypatch.setattr(extract, "detect_paywall", lambda html, text: (False, None))
    monkeypatch.setattr(extract, "page_labels", lambda html, url=None: None)
    monkeypatch.setattr(store, "save_raw_html", lambda h, html: "raw")
    out = fetch_articles.process_article(lambda: store.connect(tmp_path / "t.db"), row)
    assert out["status"] == "fetched"
    fp = conn.execute("SELECT shingles, sig, checked_at FROM body_fingerprints WHERE article_id=?", (aid,)).fetchone()
    assert fp["shingles"] > body_dups.MIN_SHINGLES and fp["sig"] is not None and fp["checked_at"] is None


def test_methodology_states_the_translation_limit():
    from pipeline import methodology
    text = methodology.body_dup_text()
    assert "Translations are not matched" in text and "machine translation" in text
    assert "\u2014" not in text and "\u2013" not in text
