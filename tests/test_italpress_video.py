"""Italpress Xinhua videos and their uncredited Italian copies (2026-11-24).

An Italpress video page carries a short description that ends with the slash credit and a video source line,
"(XINHUA/ITALPRESS)" then "(Fonte video: Xinhua)"; Libero, Il Tempo and QN run the same video under the same
headline without either. The registry reads the Xinhua video category feed (sources/outlets.yaml, it_italpress),
so the original is stored and labelled by the rules, and a copy is then sent to the model by its headline. The
texts below are written for the test; they are not taken from any article."""
import json

import pytest

from pipeline import classify_rules as cr, config, store

TITLE = "Cina, una gru di prova solleva un ponte sul fiume Azzurro"
VIDEO = ("Una gru di prova ha sollevato una campata di un ponte sul fiume Azzurro, nella provincia dello Hubei, "
         "durante i lavori per una nuova linea ferroviaria. (XINHUA/ITALPRESS)\nmec/lcr\n(Fonte video: Xinhua)")
COPY = ("Una gru di prova ha sollevato una campata di un ponte sul fiume Azzurro, nella provincia dello Hubei, "
        "durante i lavori per una nuova linea ferroviaria in Cina.")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    c = store.connect(tmp_path / "v.db")
    yield c
    c.close()


def _add(conn, url, outlet, body):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": "ITA", "language": "it",
                                         "title": TITLE, "summary": "", "status": "fetched", "gate_relevant": 1})
    store.save_body(store.url_hash(url), body)
    conn.commit()
    return aid


def test_the_video_credit_is_a_distribution_stamp_the_rules_read():
    res = cr.match_signatures(TITLE, VIDEO, "Italpresswp")
    assert res["decision"] == "A"
    assert cr.route_of(res["matches"]) == "distribution_stamp"
    assert cr.match_signatures(TITLE, COPY, None)["decision"] != "A"


def test_a_copy_labelled_first_goes_to_the_model_once_the_original_is_stored(conn):
    copy = _add(conn, "https://libero.example/news/pillole/1/cina-una-gru/", "it_libero", COPY)
    assert cr.classify_article(conn, store.get_article(conn, copy))["outcome"] == "C"
    original = _add(conn, "https://italpress.example/cina-una-gru-di-prova/", "it_italpress", VIDEO)
    assert cr.classify_article(conn, store.get_article(conn, original))["outcome"] == "A"
    conn.commit()
    assert cr.sweep_lookalikes(conn)["routed"] == 1
    a = store.get_article(conn, copy)
    match = json.loads(a["llm_trigger"])["lookalike"]
    assert a["status"] == "awaiting_llm" and (match["method"], match["article_id"]) == ("title", original)
