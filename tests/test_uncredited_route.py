"""The uncredited_copy route: a state origin label the model gave to an article the lookalike check
sent (classify_rules.route_lookalike) is stored under its own route, not as unattributed."""
import json
import re
from pathlib import Path

from pipeline import classify_llm, classify_rules as cr, export, store

ROOT = Path(__file__).resolve().parent.parent
MATCH = {"method": "title", "similarity": 0.91, "article_id": 7, "outlet_id": "italpress", "outlet": "Italpress",
         "title": "Cina, la produzione di grano sale a un record"}
REPLY = {"category": "A", "confidence": 0.8, "evidence_quote": "La produzione di grano sale", "reasoning": "Wire text.",
         "china_sources_cited": [], "independent_confirmation_present": False, "confirmation_evidence": None, "_raw": "{}"}


def _db(tmp_path):
    return store.connect(tmp_path / "r.db")


def _article(conn, n):
    return store.insert_discovered(conn, {"url": "https://e.test/%d" % n, "outlet_id": "o", "country": "ITA",
                                          "language": "it", "title": "Grano %d" % n, "status": "fetched", "gate_relevant": 1})


def test_model_route_values():
    assert cr.model_route(json.dumps({"a_candidate": [], "triggers": [], "spans": [], "lookalike": MATCH})) == "uncredited_copy"
    assert cr.model_route(json.dumps({"a_candidate": ["xinhua_dateline"], "triggers": []})) == "wire_credit"
    assert cr.model_route(json.dumps({"a_candidate": [], "triggers": ["xinhua_cited"]})) == "unattributed"
    assert cr.model_route(None) == "unattributed" and cr.model_route("not json") == "unattributed"
    assert cr.ROUTE_IDS[-2:] == ["uncredited_copy", "unattributed"]
    assert export.ROUTE_LABELS["uncredited_copy"]


def test_model_label_on_a_lookalike_gets_uncredited_copy(tmp_path):
    conn = _db(tmp_path)
    aid = _article(conn, 1)
    cr.route_lookalike(conn, aid, MATCH)
    classify_llm.store_result(conn, aid, dict(REPLY), "claude-test")
    assert store.current_classification(conn, aid)["route"] == "uncredited_copy"
    # A model label on an article an official sourcing trigger sent stays unattributed.
    other = _article(conn, 2)
    store.update_article(conn, other, status="awaiting_llm", llm_pending=1,
                         llm_trigger=json.dumps({"a_candidate": [], "triggers": ["xinhua_cited"], "spans": []}))
    classify_llm.store_result(conn, other, dict(REPLY), "claude-test")
    assert store.current_classification(conn, other)["route"] == "unattributed"


def test_ensure_routes_backfills_uncredited_copy(tmp_path):
    conn = _db(tmp_path)
    look, plain, empty = _article(conn, 1), _article(conn, 2), _article(conn, 3)
    cr.route_lookalike(conn, look, MATCH)
    cr.route_lookalike(conn, empty, MATCH)
    store.update_article(conn, plain, llm_trigger=json.dumps({"a_candidate": [], "triggers": ["xinhua_cited"]}))
    for aid in (look, plain):
        store.insert_classification(conn, aid, "llm", "A", 0.8, route="unattributed")
    store.insert_classification(conn, empty, "llm", "A", 0.8)   # a copied label, no route yet
    conn.commit()
    assert cr.ensure_routes(conn) == 2
    assert store.current_classification(conn, look)["route"] == "uncredited_copy"
    assert store.current_classification(conn, empty)["route"] == "uncredited_copy"
    assert store.current_classification(conn, plain)["route"] == "unattributed"
    assert cr.ensure_routes(conn) == 0


def test_route_is_named_in_the_interface():
    content = json.loads((ROOT / "docs" / "content.json").read_text(encoding="utf-8"))
    r = content["routes"]["uncredited_copy"]
    assert r["plain_name"] == "Uncredited copy"
    assert r["how_it_arrives"].startswith("State text that another outlet ran with a credit line, republished here without it, judged by the model")
    app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    order = re.search(r"ROUTE_CARD_ORDER = \[([^\]]*)\]", app).group(1)
    assert '"uncredited_copy"' in order
    assert "carry official Chinese sourcing" not in app and "pieces carrying official Chinese sourcing" not in app
    assert "Articles that mention a Chinese state source" not in content["categories"]["official_chinese_sourcing_verification_pending"]["definition"]
