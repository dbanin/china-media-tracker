"""Lookalike references and the uncredited copies resting on them (2026-10-22). Under the original study's rule an
uncredited copy is state origin only when the text it repeats is a known state original: no credit, but a close
match with settled state text. So a lookalike reference is a current state origin label a credit supports that is
not undetermined (model_only.reference_sql, which lookalike.credited_reference_sql returns), and an existing model
uncredited copy whose recorded reference is not such a label (undetermined, relabelled, or another uncredited copy)
is undetermined itself at export time (model_only.unsettled_copy_sql, read through route_sql). Stored labels never
change."""
import datetime as dt
import json

import pytest

from pipeline import classify_llm, classify_rules as cr, config, copies, export, lookalike, model_only, store

TITLE = "Cina, famiglia di tigri siberiane selvatiche avvistata in strada"
CREDITED = ("PECHINO (XINHUA/ITALPRESS) - Una famiglia di tigri siberiane selvatiche e' stata avvistata "
            "su una strada della provincia dello Heilongjiang, nel nord-est della Cina.")
UNCREDITED = ("Una famiglia di tigri siberiane selvatiche e' stata avvistata su una strada della provincia "
              "dello Heilongjiang, nel nord-est della Cina. Gli animali sono stati ripresi da alcuni automobilisti.")
OUTLETS = [
    {"id": "it_agency", "name": "Agency", "country": "ITA", "language": "it", "tier": "agency", "active": True,
     "feeds": ["https://agency.test/rss"]},
    {"id": "it_paper", "name": "Paper", "country": "ITA", "language": "it", "tier": "national_daily", "active": True,
     "feeds": ["https://paper.test/rss"]},
    {"id": "sg_paper", "name": "Straits", "country": "SGP", "language": "it", "tier": "national_daily", "active": True,
     "feeds": ["https://straits.test/rss"]},
]
COUNTRY = {o["id"]: o["country"] for o in OUTLETS}
LLM = config.LLM_MODEL + "@" + classify_llm.PROMPT_VERSION


def _yesterday():
    return (dt.date.fromisoformat(export._today()) - dt.timedelta(days=1)).isoformat()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(model_only, "REVIEWS_DIR", tmp_path / "reviews")
    c = store.connect(tmp_path / "r.db")
    store.sync_outlets(c, OUTLETS)
    return c


def _article(conn, url, outlet, title=TITLE, body=None, when=None, status="classified"):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": COUNTRY[outlet], "language": "it",
                                         "title": title, "status": status, "gate_relevant": 1})
    conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (when or "%sT09:00:00+00:00" % _yesterday(), aid))
    if body is not None:
        store.save_body(store.url_hash(url), body)
    conn.commit()
    return aid


def _label(conn, aid, method, category, route=None, sigs=None):
    store.insert_classification(conn, aid, method, category, 0.9, route=route, signatures_fired=sigs,
                                model_version=LLM if method == "llm" else None)
    conn.commit()


def _copy_of(conn, ref, url, outlet="sg_paper", trigger=True):
    """A model uncredited copy whose llm_trigger records `ref` as its lookalike reference."""
    aid = _article(conn, url, outlet)
    if trigger:
        match = {"method": "title", "similarity": 1.0, "article_id": ref, "outlet_id": "it_agency", "outlet": "Agency",
                 "title": TITLE}
        conn.execute("UPDATE articles SET llm_trigger=? WHERE id=?",
                     (json.dumps({"a_candidate": [], "triggers": [], "spans": [], "lookalike": match}), aid))
    _label(conn, aid, "llm", "A", "uncredited_copy")
    return aid


def _route(conn, aid):
    return conn.execute("SELECT %s FROM classifications c WHERE c.article_id=? AND c.is_current=1" % model_only.route_sql("c"),
                        (aid,)).fetchone()[0]


def _export(conn):
    export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [])
    outlets_json = export.build_outlets(conn, OUTLETS)
    meta = export.build_meta(conn, OUTLETS, [], latest, outlets_json)
    return latest, outlets_json, meta


# ---------------------------------------------------------------------------
# which labels may stand as a reference
# ---------------------------------------------------------------------------

def test_the_reference_rule_reuses_the_undetermined_predicate():
    cond, params = lookalike.credited_reference_sql("c")
    assert cond == model_only.reference_sql("c") and params == []
    # the routes a credit supports, the rules labels from before routes, never an undetermined label
    assert "sponsored_disclosure" in cond and "wire_credit" in cond and "c.route IS NULL" in cond
    assert "uncredited_copy" not in cond and "AND NOT (COALESCE(c.method, '') <> 'rules'" in cond


def test_which_current_labels_are_references(conn):
    cases = {"rules_wire": ("rules", "wire_credit", True), "rules_sponsored": ("rules", "sponsored_disclosure", True),
             "rules_before_routes": ("rules", None, True), "llm_wire": ("llm", "wire_credit", True),
             "llm_sponsored": ("llm", "sponsored_disclosure", False), "llm_unattributed": ("llm", "unattributed", False),
             "llm_copy": ("llm", "uncredited_copy", False)}
    ids = {}
    for name, (method, route, _) in cases.items():
        ids[name] = _article(conn, "https://agency.test/%s" % name, "it_agency")
        _label(conn, ids[name], method, "A", route)
    cond, params = lookalike.credited_reference_sql("c")
    got = {r[0] for r in conn.execute("SELECT c.article_id FROM classifications c WHERE c.is_current=1 AND c.category='A' AND %s"
                                      % cond, params)}
    assert got == {ids[n] for n, (_, _, ok) in cases.items() if ok}


# ---------------------------------------------------------------------------
# the rules stage and the sweep
# ---------------------------------------------------------------------------

def test_an_undetermined_reference_routes_no_copy_and_a_rules_sponsored_one_does(conn):
    ref = _article(conn, "https://agency.test/1", "it_agency", body=CREDITED)
    _label(conn, ref, "llm", "A", "sponsored_disclosure")
    aid = _article(conn, "https://straits.test/1", "sg_paper", body=UNCREDITED, status="fetched")
    row = store.get_article(conn, aid)
    assert len(lookalike.build_index(conn, [row])) == 0
    assert cr.classify_article(conn, row)["outcome"] != "lookalike"
    assert store.current_classification(conn, aid)["category"] in ("C", "not_relevant")
    # the rules found the sponsored disclosure on the article: a settled reference, so the copy goes to the model
    _label(conn, ref, "rules", "A", "sponsored_disclosure", ["sponsored_partner_content"])
    other = _article(conn, "https://straits.test/2", "sg_paper", body=UNCREDITED, status="fetched")
    assert cr.classify_article(conn, store.get_article(conn, other))["outcome"] == "lookalike"
    assert json.loads(store.get_article(conn, other)["llm_trigger"])["lookalike"]["article_id"] == ref


def test_the_sweep_follows_the_same_rule(conn):
    when = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    aid = _article(conn, "https://straits.test/s", "sg_paper", body=UNCREDITED, when=(when - dt.timedelta(hours=2)).isoformat())
    _label(conn, aid, "rules", "C")
    ref = _article(conn, "https://agency.test/s", "it_agency", body=CREDITED, when=when.isoformat())
    _label(conn, ref, "llm", "A", "sponsored_disclosure")
    out = cr.sweep_lookalikes(conn)
    assert out["refs"] == 0 and out["routed"] == 0
    assert store.current_classification(conn, aid)["category"] == "C"
    _label(conn, ref, "rules", "A", "sponsored_disclosure", ["sponsored_partner_content"])
    out = cr.sweep_lookalikes(conn)
    assert out["refs"] == 1 and out["routed"] == 1
    assert json.loads(store.get_article(conn, aid)["llm_trigger"])["lookalike"]["article_id"] == ref


# ---------------------------------------------------------------------------
# existing copies at export time
# ---------------------------------------------------------------------------

def test_a_copy_counts_only_while_its_reference_is_settled(conn):
    settled = _article(conn, "https://agency.test/ok", "it_agency")
    _label(conn, settled, "rules", "A", "sponsored_disclosure")
    before_routes = _article(conn, "https://agency.test/old", "it_agency")
    _label(conn, before_routes, "rules", "A")
    model_sponsored = _article(conn, "https://agency.test/u", "it_agency")
    _label(conn, model_sponsored, "llm", "A", "sponsored_disclosure")
    relabelled = _article(conn, "https://agency.test/c", "it_agency")
    _label(conn, relabelled, "rules", "A", "wire_credit")
    _label(conn, relabelled, "llm", "C")
    chained = _copy_of(conn, settled, "https://paper.test/chain", outlet="it_paper")
    ok1 = _copy_of(conn, settled, "https://straits.test/ok1")
    ok2 = _copy_of(conn, before_routes, "https://straits.test/ok2")
    u1 = _copy_of(conn, model_sponsored, "https://straits.test/u1")
    u2 = _copy_of(conn, relabelled, "https://straits.test/u2")
    u3 = _copy_of(conn, chained, "https://straits.test/u3")
    u4 = _copy_of(conn, 999999, "https://straits.test/u4")
    for aid in (chained, ok1, ok2):
        assert _route(conn, aid) == "uncredited_copy"
    for aid in (u1, u2, u3, u4):
        assert _route(conn, aid) == model_only.COPY_OF_UNDETERMINED
        assert model_only.is_undetermined("A", _route(conn, aid), "llm")
        assert model_only.headline_category("A", _route(conn, aid), "llm") == "U"
        assert model_only.counted_route(_route(conn, aid), "llm") == "unattributed"
    # the stored label is unchanged
    assert store.current_classification(conn, u1)["route"] == "uncredited_copy"
    pooled = conn.execute("SELECT c.article_id FROM classifications c WHERE c.is_current=1 AND c.category='A' AND %s"
                          % model_only.undetermined_sql("c")).fetchall()
    assert {r[0] for r in pooled} == {model_sponsored, u1, u2, u3, u4}
    counted = conn.execute("SELECT c.article_id FROM classifications c WHERE c.is_current=1 AND c.category='A'%s"
                           % model_only.evidence_sql("c")).fetchall()
    assert {r[0] for r in counted} == {settled, before_routes, chained, ok1, ok2}


def test_a_copy_whose_trigger_was_rewritten_keeps_the_snapshotted_reference(conn):
    ref = _article(conn, "https://agency.test/snap", "it_agency")
    _label(conn, ref, "llm", "A", "sponsored_disclosure")
    aid = _copy_of(conn, ref, "https://straits.test/snap", trigger=False)
    assert _route(conn, aid) == "uncredited_copy", "no recorded reference at all: left as stored"
    conn.execute("INSERT INTO state_copies(copy_id, version, computed_at, current, original_kind, details) "
                 "VALUES (?, ?, '2026-10-22T00:00:00+00:00', 1, 'none', ?)",
                 (aid, copies.VERSION, json.dumps({"lookalike": {"article_id": ref, "title": TITLE}})))
    conn.commit()
    assert _route(conn, aid) == model_only.COPY_OF_UNDETERMINED
    conn.execute("UPDATE articles SET llm_trigger='not json' WHERE id=?", (aid,))
    assert _route(conn, aid) == model_only.COPY_OF_UNDETERMINED, "text that is not JSON never breaks the query"


def test_a_copy_of_an_undetermined_item_exports_as_undetermined(conn):
    wire = _article(conn, "https://agency.test/w", "it_agency")
    _label(conn, wire, "rules", "A", "wire_credit")
    sponsored = _article(conn, "https://agency.test/s", "it_agency")
    _label(conn, sponsored, "llm", "A", "sponsored_disclosure")
    good = _copy_of(conn, wire, "https://straits.test/g")
    bad = _copy_of(conn, sponsored, "https://straits.test/b")
    latest, outlets_json, meta = _export(conn)
    sgp, ita = latest["countries"]["SGP"]["all_time"], latest["countries"]["ITA"]["all_time"]
    assert (sgp["A"], sgp["U"], sgp["china_total"]) == (1, 1, 1)
    assert (ita["A"], ita["U"]) == (1, 1)
    tot = latest["totals"]["all_time"]
    assert (tot["A"], tot["U"], tot["china_total"]) == (2, 2, 2)
    straits = {o["id"]: o for o in outlets_json["outlets"]}["sg_paper"]["counts"]
    assert (straits["A"], straits["U"]) == (1, 1)
    ud = meta["undetermined"]
    assert ud["count"] == 2 and ud["by_route"] == {"unattributed": 0, "sponsored_disclosure": 1, "uncredited_copy": 1}
    assert meta["route_totals"]["uncredited_copy"] == 1 and meta["route_totals"]["unattributed"] == 2
    assert sum(meta["route_signature_totals"].values()) == tot["A"]
    assert meta["findings"]["all_time"]["state_origin"] == 2
    assert meta["issuer_type_totals"]["unrecorded"] == 3
    # the article lists: the copy of the undetermined item is listed as U and carries no route
    lists = export.build_articles(conn, outlets=OUTLETS)
    entry = {e["id"]: e for e in lists["SGP"]}
    assert entry[good]["category"] == "A" and entry[good]["route"] == "uncredited_copy"
    assert entry[bad]["category"] == "U" and entry[bad]["route"] is None
    assert "uncredited copies of an item that is not settled state origin" in model_only.methodology_text(ud)
    # the alteration and speed measures read the copy's route the same way
    for aid in (good, bad):
        conn.execute("INSERT INTO state_copies(copy_id, version, computed_at, current, original_kind) "
                     "VALUES (?, ?, '2026-10-22T00:00:00+00:00', 1, 'none')", (aid, copies.VERSION))
    assert [r["copy_id"] for r in copies._rows(conn, {"outlets": OUTLETS})] == [good]


def test_the_rule_is_disclosed():
    from pipeline import methodology
    text = methodology.uncredited_copy_limit_text()
    assert "a copy of an undetermined item is undetermined itself" in text
    assert "still is" not in text
    root = config.ROOT
    for name in ("docs/index.html", "README.md"):
        flat = " ".join((root / name).read_text(encoding="utf-8").split())
        assert "an uncredited copy counts only when the item it repeats is settled state origin" in flat, name
        assert "a copy of an undetermined item is undetermined itself" in flat, name
        assert "did not find still is" not in flat and "although that label is itself undetermined" not in flat, name
    for t in (text,):
        assert "—" not in t and "–" not in t
