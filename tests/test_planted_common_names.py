"""A byline match on a common persona name (common_name: true) is held for confirmation and never
published without a second signal: an outlet or a date window from the list, or a front of the same
operation."""
import copy

import pytest
import yaml

from pipeline import config, planted, store

BASE = {
    "version": "t1",
    "operations": [
        {"id": "op_bogus", "name": "Bogus Bylines", "origin": "IR", "reported_by": "Example Lab",
         "report_url": "https://example.test/r", "reported_on": "2026-10-08", "summary": "Invented bylines."},
        {"id": "op_front", "name": "Dark Front", "origin": "RU", "reported_by": "Example Lab",
         "report_url": "https://example.test/r2", "reported_on": "2026-10-08", "summary": "A false front."},
    ],
    "personas": [
        {"id": "jenny_williams", "name": "Jenny Williams", "operation": "op_bogus", "common_name": True},
        {"id": "mia_clark", "name": "Mia Clark", "operation": "op_front", "common_name": True},
        {"id": "ervin_b_hoskins", "name": "Ervin B. Hoskins", "operation": "op_bogus"},
    ],
    "fronts": [
        {"id": "src", "name": "Social Research Center", "operation": "op_front", "domains": ["srcenter.example"]},
    ],
}


def _use(tmp_path, monkeypatch, data):
    p = tmp_path / ("personas_%s.yaml" % data["version"])
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(config, "PERSONAS_PATH", p)
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")


def _article(conn, i, author, outlet="us_x", published=None, body=None, url=None):
    aid = store.insert_discovered(conn, {"url": url or "https://news.test/%d" % i, "outlet_id": outlet, "country": "USA",
                                         "language": "en", "title": "Story %d" % i, "author": author, "status": "fetched",
                                         "gate_relevant": 1, "published_at": published})
    if body is not None:
        row = store.get_article(conn, aid)
        store.update_article(conn, aid, body_hash=store.save_body(row["url_hash"], body), body_chars=len(body),
                             fetched_at=store.utcnow())
    conn.commit()
    return aid


def _published(conn):
    return sorted((f["article_id"], f["detector"], f["persona_id"] or f["front_id"]) for f in planted.build_export(conn)["flags"])


def test_a_common_name_alone_is_held_and_never_published(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, BASE)
    conn = store.connect(tmp_path / "t.db")
    held = _article(conn, 1, "Jenny Williams")
    head = _article(conn, 2, None, body="Opinion\n\nBy Jenny Williams\n\nThe port deal.")
    rare = _article(conn, 3, "Ervin Hoskins")
    counts = planted.scan(conn)
    assert counts["pending"] == 2 and counts["flagged"] == 1
    assert _published(conn) == [(rare, "author_field", "ervin_b_hoskins")]
    meta = planted.meta_block(conn)
    assert meta["flags_total"] == 1 and meta["articles_total"] == 1
    pending = {(r["article_id"], r["detector"]) for r in planted.pending_rows(conn)}
    assert pending == {(held, "author_field_pending"), (head, "byline_head_pending")}


def test_a_front_of_the_same_operation_confirms_it(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, BASE)
    conn = store.connect(tmp_path / "t.db")
    cited = _article(conn, 1, "Mia Clark", body="A study by the Social Research Center found that the port deal ...")
    on_front = _article(conn, 2, "Mia Clark", url="https://www.srcenter.example/report")
    # a front of another operation is no second signal for Jenny Williams
    other = _article(conn, 3, "Jenny Williams", body="The Social Research Center said so.")
    planted.scan(conn)
    assert _published(conn) == [(cited, "author_field", "mia_clark"), (cited, "front_cited", "src"),
                                (on_front, "author_field", "mia_clark"), (other, "front_cited", "src")]


def test_an_outlet_or_window_from_the_list_confirms_it(tmp_path, monkeypatch):
    data = copy.deepcopy(BASE)
    data["personas"][0]["outlets"] = ["us_monitor", "monitor.example"]
    data["operations"][0]["active_from"] = "2025-07-01"
    data["operations"][0]["active_to"] = "2025-12-31"
    _use(tmp_path, monkeypatch, data)
    conn = store.connect(tmp_path / "t.db")
    by_id = _article(conn, 1, "Jenny Williams", outlet="us_monitor")
    by_host = _article(conn, 2, "Jenny Williams", url="https://news.monitor.example/a")
    in_window = _article(conn, 3, "Jenny Williams", published="2025-09-01T08:00:00+00:00")
    outside = _article(conn, 4, "Jenny Williams", published="2026-09-01T08:00:00+00:00")
    counts = planted.scan(conn)
    assert counts["flagged"] == 3 and counts["pending"] == 1
    assert [a for a, _, _ in _published(conn)] == [by_id, by_host, in_window]
    assert [r["article_id"] for r in planted.pending_rows(conn)] == [outside]


def test_a_flag_written_before_the_guard_leaves_the_site(tmp_path, monkeypatch):
    plain = copy.deepcopy(BASE)
    plain["personas"][0].pop("common_name")
    _use(tmp_path, monkeypatch, plain)
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, 1, "Jenny Williams")
    planted.scan(conn)
    assert _published(conn) == [(aid, "author_field", "jenny_williams")]
    _use(tmp_path, monkeypatch, BASE)            # the name is marked common
    assert _published(conn) == []
    assert planted.meta_block(conn)["flags_total"] == 0


def test_the_real_list_marks_the_common_names():
    lst = planted.load_personas(config.ROOT / "sources" / "personas.yaml")
    common = {p["id"] for p in lst["personas"] if p["common_name"]}
    assert {"jenny_williams", "alice_johnson", "michael_harrison", "sophia_gonzalez", "mia_clark"} <= common
    assert "ervin_b_hoskins" not in common


@pytest.mark.parametrize("url,ok", [("https://srcenter.example/x", True), ("https://a.srcenter.example/x", True),
                                    ("https://notsrcenter.example/x", False), ("not a url", False)])
def test_front_domain_match_is_by_whole_host(url, ok):
    lst = {"operations": BASE["operations"], "fronts": [{"operation": "op_front", "domains_n": ["srcenter.example"]}]}
    ps = {"operation": "op_front"}
    assert (planted.second_signal(ps, lst, "x", url, None, None, []) == "front") is ok
