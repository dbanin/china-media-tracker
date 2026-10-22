"""The codebook of prompt 2026-10-17: who counts as a state issuer (DECISIONS 1 to 4), the issuer_type
the model records and the label stores, the route a model state origin label takes from the basis the
model stated, and the relabel queue that judges model state origin labels again first."""
import json
import sqlite3

from pipeline import classify_llm as cl, config, export, store

OLD = "claude-sonnet-5"


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.SCHEMA)
    return conn


def _article(conn, n, status="awaiting_llm", trigger=None, dup=None):
    url = "https://e.com/i%d" % n
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": "ITA", "language": "it",
                                         "title": "Title %d" % n, "status": status, "gate_relevant": 1})
    store.save_body(store.url_hash(url), "Body about China %d" % n)
    if trigger is not None:
        conn.execute("UPDATE articles SET llm_trigger=? WHERE id=?", (json.dumps(trigger), aid))
    if dup:
        conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (dup, aid))
    return aid


def _reply(category="A", **fields):
    data = {"category": category, "confidence": 0.8, "evidence_quote": "q", "reasoning": "r",
            "china_sources_cited": [], "independent_confirmation_present": False, "confirmation_evidence": None}
    data.update(fields)
    return data


def _message(payload):
    class Block:
        type = "text"
        text = json.dumps(payload)

    class Msg:
        stop_reason = "end_turn"
        content = [Block()]
        usage = None
    return Msg()


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------

def test_prompt_version_is_the_issuer_rule():
    assert cl.PROMPT_VERSION == "2026-10-17"


def test_definitions_name_the_state_issuers_and_the_private_ones():
    d = cl.CATEGORY_DEFINITIONS
    for phrase in (
        # DECISIONS 1: a release is state origin only when a state entity issued or commissioned it.
        "A press release is state origin only when a Chinese state entity issued or commissioned it",
        '"A news report from Xinhuanet"',
        "Being published unaltered is not enough",
        "a private Chinese company, a Hong Kong or Macao private body, a university (a public one included), a charity, "
        "a trade show organiser, an auction house, a casino, a sports club or a foreign organisation is not state origin",
        "it is independent journalism when it substantively concerns China, and not relevant otherwise",
        "A press release that is not state origin is coded here when it substantively concerns China.",
        # DECISIONS 2: state-owned enterprises.
        "A commercial release of a state-owned enterprise (GAC, CRRC, Sinopec, State Grid, China Eastern, Shanghai Electric, "
        "Chery, Zoomlion and the like) is state origin",
        # DECISIONS 3: Hong Kong and Macao.
        "a government department or statutory body of the Hong Kong or Macao Special Administrative Region",
        "the Hong Kong Trade Development Council, the Hong Kong Tourism Board, the ICAC, a Macao government office",
        "concerns China only when it substantively concerns the PRC state or its policy (cross-strait relations, the national "
        "security law, Beijing's role); otherwise it is not relevant",
        # DECISIONS 4: state copy whatever its topic.
        "State text is state origin whatever its topic: a Xinhua story about Nicaragua, run unaltered, is state origin.",
        "State origin text is never coded not relevant, whatever its topic.",
    ):
        assert phrase in d, phrase
        assert phrase in cl.SYSTEM_PROMPT, phrase


def test_prompt_rules_and_worked_examples():
    p = cl.SYSTEM_PROMPT
    assert "Decide in this order. First, who wrote the text" in p
    assert "Name the issuer of the text before you answer A." in p
    assert "however closely the text matches the pattern of a sponsored placement" in p
    assert "vivo, XPENG, Huawei, MINISO, CATL, Tencent, Baidu, Fosun and Pudu are private companies" in p
    for heading in ("Example 7, a private company's release.", "Example 8, a state-owned enterprise's release.",
                    "Example 9, a Hong Kong SAR statutory body.", "Example 10, a Hong Kong university's release.",
                    "Example 11, state copy on a topic other than China."):
        assert heading in p, heading
    # Every worked answer is a full reply under the schema, the new fields included, and agrees with the rule
    # tying issuer_type to category.
    answers = [json.loads(chunk.split("\n")[0]) for chunk in p.split("Answer: ")[1:]]
    assert len(answers) == 11
    for a in answers:
        assert set(a) == set(cl.OUTPUT_SCHEMA["required"]), a
        assert a["issuer_type"] in cl.ISSUER_TYPES and a["state_basis"] in cl.STATE_BASES
        if a["category"] == "A":
            assert a["issuer_type"] in cl.STATE_ISSUER_TYPES and a["state_basis"] != "none"
        else:
            assert a["issuer_type"] in ("private_release", "none") and a["state_basis"] == "none"
    by_example = {n: a for n, a in enumerate(answers, 1)}
    assert (by_example[7]["category"], by_example[7]["issuer_type"]) == ("C", "private_release")
    assert (by_example[8]["category"], by_example[8]["issuer_type"]) == ("A", "state_owned_enterprise")
    assert (by_example[9]["category"], by_example[9]["issuer_type"]) == ("A", "sar_government")
    assert (by_example[10]["category"], by_example[10]["issuer_type"]) == ("not_relevant", "private_release")
    assert (by_example[11]["category"], by_example[11]["issuer_type"]) == ("A", "state_media")
    assert "–" not in p and "—" not in p


def test_schema_requires_issuer_type_and_state_basis():
    props = cl.OUTPUT_SCHEMA["properties"]
    assert props["issuer_type"]["enum"] == ["state_media", "government", "party", "diplomatic", "state_owned_enterprise",
                                            "sar_government", "private_release", "none"]
    assert set(props["state_basis"]["enum"]) == set(cl.BASIS_ROUTES) | {"none"}
    assert {"issuer_type", "state_basis"} <= set(cl.OUTPUT_SCHEMA["required"])
    assert set(cl.BASIS_ROUTES.values()) <= set(__import__("pipeline.classify_rules", fromlist=["x"]).ROUTE_IDS)


def test_the_model_is_still_blind_to_outlet_country_and_url():
    msg = cl.request_params("Headline", "Body text")["messages"][0]["content"]
    for leak in ("outlet", "country", "ITA", "http", "url"):
        assert leak not in msg, leak
    for leak in ("{outlet", "{country", "{url"):
        assert leak not in cl.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Parsing and storing issuer_type
# ---------------------------------------------------------------------------

def test_parse_reads_issuer_type_and_state_basis():
    data = cl.parse_response(_message(_reply(issuer_type="state_owned_enterprise", state_basis="issuer_named")))
    assert data["issuer_type"] == "state_owned_enterprise" and data["state_basis"] == "issuer_named"


def test_parse_an_old_reply_without_the_fields_or_with_unknown_values():
    old = cl.parse_response(_message(_reply("C")))
    assert old["category"] == "C" and old["issuer_type"] is None and old["state_basis"] is None
    odd = cl.parse_response(_message(_reply("A", issuer_type="ministry", state_basis=7)))
    assert odd["issuer_type"] is None and odd["state_basis"] is None


def test_issuer_type_is_stored_on_the_label_and_on_the_session_path(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    api = _article(conn, 1)
    cl.store_result(conn, api, _reply("C", issuer_type="private_release", state_basis="none"), OLD)
    assert store.current_classification(conn, api)["issuer_type"] == "private_release"
    # A session label reaches store_result without the field (pipeline.labels._data) but with its line in _raw.
    session = _article(conn, 2)
    line = dict(_reply("A", issuer_type="sar_government", state_basis="issuer_named"), url_hash="h")
    data = {k: v for k, v in line.items() if k not in ("issuer_type", "state_basis", "url_hash")}
    data["_raw"] = json.dumps(line)
    cl.store_result(conn, session, data, OLD + " (session, prompt %s)" % cl.PROMPT_VERSION, prompt_version=None)
    cur = store.current_classification(conn, session)
    assert cur["issuer_type"] == "sar_government" and cur["route"] == "unattributed"
    # A label from before the field: NULL, never a guess.
    legacy = _article(conn, 3)
    cl.store_result(conn, legacy, _reply("B", _raw=json.dumps(_reply("B"))), OLD)
    assert store.current_classification(conn, legacy)["issuer_type"] is None


def test_the_column_is_added_to_an_older_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(store.SCHEMA.replace("    issuer_type TEXT,                 -- model labels only: who issued the text "
                                            "(classify_llm.ISSUER_TYPES); NULL before prompt 2026-10-17\n", ""))
    assert "issuer_type" not in {r[1] for r in conn.execute("PRAGMA table_info(classifications)")}
    conn.close()
    conn = store.connect(path)
    assert "issuer_type" in {r[1] for r in conn.execute("PRAGMA table_info(classifications)")}


def test_dry_run_and_api_path_store_the_issuer(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    aid = _article(conn, 1, trigger={"a_candidate": ["sponsored_en"], "triggers": [], "spans": []})
    cl.run(conn, "t", client=cl.DryRunClient(answer=lambda body: "A"))
    cur = store.current_classification(conn, aid)
    assert cur["category"] == "A" and cur["issuer_type"] == "state_media"
    assert cur["route"] == "wire_credit", "from the basis the model stated, not the weak signature that sent it"


# ---------------------------------------------------------------------------
# The route of a model state origin label
# ---------------------------------------------------------------------------

def test_route_comes_from_the_stated_basis_never_a_weak_signature(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    weak = {"a_candidate": ["diplomat_list_name_only"], "triggers": [], "spans": []}

    def route(n, trigger=weak, **fields):
        aid = _article(conn, n, trigger=trigger)
        cl.store_result(conn, aid, _reply("A", **fields), OLD)
        return store.current_classification(conn, aid)["route"]

    # The GAC factory release the audit found under diplomatic_byline: an SOE release names no diplomat.
    assert route(1, issuer_type="state_owned_enterprise", state_basis="issuer_named") == "unattributed"
    assert route(2, issuer_type="state_media", state_basis="credit_line") == "wire_credit"
    assert route(3, issuer_type="state_media", state_basis="distribution_stamp") == "distribution_stamp"
    assert route(4, issuer_type="government", state_basis="sponsored_disclosure") == "sponsored_disclosure"
    assert route(5, issuer_type="diplomatic", state_basis="diplomatic_byline") == "diplomatic_byline"
    # A diplomatic byline under an issuer that is not diplomatic is not taken at its word.
    assert route(6, issuer_type="state_owned_enterprise", state_basis="diplomatic_byline") == "unattributed"
    # A reply from before state_basis: unattributed, not the signature's route.
    assert route(7) == "unattributed"
    # context_match stands only when the lookalike check sent the article.
    assert route(8, issuer_type="state_media", state_basis="context_match") == "unattributed"
    look = {"a_candidate": [], "triggers": [], "spans": [], "lookalike": {"title": "t", "outlet": "x", "article_id": 1}}
    assert route(9, trigger=look, issuer_type="state_media", state_basis="context_match") == "uncredited_copy"
    assert route(10, trigger=look, issuer_type="state_media", state_basis="issuer_named") == "uncredited_copy"
    assert route(11, trigger=look, issuer_type="state_media", state_basis="credit_line") == "wire_credit"
    # Not state origin: no route at all.
    aid = _article(conn, 12, trigger=weak)
    cl.store_result(conn, aid, _reply("C", issuer_type="private_release", state_basis="none"), OLD)
    assert store.current_classification(conn, aid)["route"] is None


def test_a_copied_label_keeps_its_route_and_issuer(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    head = _article(conn, 1, status="classified", dup="g",
                    trigger={"a_candidate": [], "triggers": ["xinhua_cited"], "spans": []})
    cl.store_result(conn, head, _reply("A", issuer_type="state_owned_enterprise", state_basis="issuer_named"), OLD)
    sib = _article(conn, 2, dup="g", trigger={"a_candidate": ["diplomat_list_name_only"], "triggers": [], "spans": []})
    assert cl._copy_from_dup_group(conn, store.get_article(conn, sib)) is True
    cur = store.current_classification(conn, sib)
    assert (cur["category"], cur["route"], cur["issuer_type"]) == ("A", "unattributed", "state_owned_enterprise")


# ---------------------------------------------------------------------------
# The relabel queue
# ---------------------------------------------------------------------------

def test_model_state_origin_labels_on_weak_routes_are_relabelled_first(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    from pipeline import registry
    monkeypatch.setattr(registry, "load_outlets", lambda *a, **k: [])   # the queue only asks who can refetch
    conn = _db()

    def label(n, when, category, route=None, model_version=OLD):
        aid = _article(conn, n, status="classified")
        store.insert_classification(conn, aid, "llm", category, 0.7, model_version=model_version, route=route)
        conn.execute("UPDATE classifications SET classified_at=? WHERE article_id=?", (when, aid))
        return aid

    b_before_rule = label(1, "2026-09-01T00:00:00+00:00", "B")
    b_under_rule = label(2, "2026-10-13T00:00:00+00:00", "B", model_version=OLD + " (prompt 2026-10-12)")
    a_wire = label(3, "2026-09-02T00:00:00+00:00", "A", route="wire_credit")
    a_unattr = label(4, "2026-10-14T00:00:00+00:00", "A", route="unattributed")
    a_sponsored = label(5, "2026-09-20T00:00:00+00:00", "A", route="sponsored_disclosure")
    a_uncredited = label(6, "2026-10-15T00:00:00+00:00", "A", route="uncredited_copy", model_version=OLD + " (prompt 2026-10-12)")
    a_byline = label(7, "2026-09-03T00:00:00+00:00", "A", route="diplomatic_byline")
    not_rel = label(8, "2026-09-04T00:00:00+00:00", "not_relevant")
    rules_a = _article(conn, 9, status="classified")
    store.insert_classification(conn, rules_a, "rules", "A", 1.0, route="unattributed")
    current = label(10, "2026-09-05T00:00:00+00:00", "A", route="unattributed", model_version=cl.with_prompt_version(OLD))
    conn.commit()
    order = [r["id"] for r in cl.relabel_candidates(conn)]
    assert order == [a_sponsored, a_unattr, a_uncredited, b_before_rule, b_under_rule, a_wire, a_byline, not_rel]
    assert rules_a not in order and current not in order


def test_meta_and_exports_carry_issuer_type(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    soe = _article(conn, 1)
    cl.store_result(conn, soe, _reply("A", issuer_type="state_owned_enterprise", state_basis="issuer_named"), OLD)
    priv = _article(conn, 2)
    cl.store_result(conn, priv, _reply("C", issuer_type="private_release", state_basis="none"), OLD)
    legacy = _article(conn, 3)
    store.insert_classification(conn, legacy, "llm", "B", 0.7, model_version=OLD)
    rules = _article(conn, 4)
    store.insert_classification(conn, rules, "rules", "C", 1.0)
    conn.commit()
    totals = export.issuer_type_totals(conn)
    assert list(totals["by_type"]) == list(cl.ISSUER_TYPES)
    # A release that only names its issuer has no credit, stamp, disclosure or byline: route unattributed, so
    # it is counted under U, undetermined and excluded from every count (D1 as revised).
    assert totals["by_type"]["state_owned_enterprise"] == {"U": 1}
    assert totals["by_type"]["private_release"] == {"C": 1}
    assert totals["by_type"]["sar_government"] == {} and totals["unrecorded"] == 1
    audit = tmp_path / "audit"
    export.write_audit_files(conn, audit)
    lines = [json.loads(l) for f in sorted(audit.glob("articles-*.jsonl")) for l in f.read_text().splitlines()]
    by_id = {l["id"]: l for l in lines}
    assert by_id[soe]["issuer_type"] == "state_owned_enterprise" and by_id[priv]["issuer_type"] == "private_release"
    assert "issuer_type" not in by_id[legacy], "a line without the field stays as it was"


def test_site_articles_carry_issuer_type(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    sar = _article(conn, 1)
    cl.store_result(conn, sar, _reply("A", issuer_type="sar_government", state_basis="issuer_named"), OLD)
    conn.commit()
    entries = [e for rows in export.build_articles(conn, outlets=[]).values() for e in rows]
    assert [(e["id"], e["issuer_type"]) for e in entries] == [(sar, "sar_government")]


# ---------------------------------------------------------------------------
# Inconsistent replies
# ---------------------------------------------------------------------------

def test_inconsistency_of_each_combination():
    for issuer in cl.ISSUER_TYPES:
        for cat in ("A", "B", "C", "not_relevant"):
            got = cl.inconsistency_of(_reply(cat, issuer_type=issuer))
            if cat == "A":
                want = cl.STATE_ORIGIN_WITHOUT_STATE_ISSUER if issuer in ("private_release", "none") else None
            else:
                want = cl.STATE_ISSUER_WITHOUT_STATE_ORIGIN if issuer in cl.STATE_ISSUER_TYPES else None
            assert got == want, (cat, issuer, got)
    # A reply from before the field, or with an unknown value, is never inconsistent.
    assert cl.inconsistency_of(_reply("A")) is None
    assert cl.inconsistency_of(_reply("A", issuer_type="ministry")) is None


def test_an_inconsistent_state_origin_reply_never_counts_as_state_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    aid = _article(conn, 1, trigger={"a_candidate": ["sponsored_en"], "triggers": [], "spans": []})
    flaw = cl.store_result(conn, aid, _reply("A", issuer_type="private_release", state_basis="sponsored_disclosure",
                                             reasoning="Not a state entity, but matches the sponsored placement pattern."), OLD)
    assert flaw == cl.STATE_ORIGIN_WITHOUT_STATE_ISSUER
    cur = store.current_classification(conn, aid)
    assert (cur["category"], cur["route"], cur["issuer_type"], cur["needs_review"]) == (
        "C", None, "private_release", cl.STATE_ORIGIN_WITHOUT_STATE_ISSUER)
    assert cur["reasoning"].startswith("[inconsistent reply: state origin with issuer type private_release")
    assert conn.execute("SELECT COUNT(*) FROM classifications WHERE category='A'").fetchone()[0] == 0
    # The other way round: the category stands as returned, never raised to state origin.
    other = _article(conn, 2)
    assert cl.store_result(conn, other, _reply("B", issuer_type="government", state_basis="none"), OLD) == \
        cl.STATE_ISSUER_WITHOUT_STATE_ORIGIN
    cur = store.current_classification(conn, other)
    assert (cur["category"], cur["needs_review"]) == ("B", cl.STATE_ISSUER_WITHOUT_STATE_ORIGIN)
    # A consistent reply carries no marker.
    fine = _article(conn, 3)
    assert cl.store_result(conn, fine, _reply("A", issuer_type="state_media", state_basis="credit_line"), OLD) is None
    assert store.current_classification(conn, fine)["needs_review"] is None
    conn.commit()
    assert export.inconsistent_labels(conn) == {"total": 2, "by_reason": {
        cl.STATE_ORIGIN_WITHOUT_STATE_ISSUER: 1, cl.STATE_ISSUER_WITHOUT_STATE_ORIGIN: 1}}
    audit = tmp_path / "audit"
    export.write_audit_files(conn, audit)
    lines = {l["id"]: l for f in audit.glob("articles-*.jsonl") for l in map(json.loads, f.read_text().splitlines())}
    assert lines[aid]["needs_review"] == cl.STATE_ORIGIN_WITHOUT_STATE_ISSUER and "needs_review" not in lines[fine]
    site = {e["id"]: e for rows in export.build_articles(conn, outlets=[]).values() for e in rows}
    assert site[aid]["needs_review"] == cl.STATE_ORIGIN_WITHOUT_STATE_ISSUER and site[fine]["needs_review"] is None


def test_an_inconsistent_reply_is_counted_not_retried_and_its_copy_keeps_the_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    head = _article(conn, 1, dup="g")
    store.update_article(conn, head, status="classified")
    cl.store_result(conn, head, _reply("A", issuer_type="none", state_basis="issuer_named"), OLD)
    sib = _article(conn, 2, dup="g")
    assert cl._copy_from_dup_group(conn, store.get_article(conn, sib)) is True
    cur = store.current_classification(conn, sib)
    assert (cur["category"], cur["needs_review"]) == ("C", cl.STATE_ORIGIN_WITHOUT_STATE_ISSUER)

    class Inconsistent(cl.DryRunClient):
        pass
    client = Inconsistent()
    payload = _reply("A", issuer_type="private_release", state_basis="issuer_named")
    client.messages.create = lambda **params: (client.calls.append(params), _message(payload))[1]
    fresh = _article(conn, 3)
    counts = cl.run(conn, "t", client=client)
    assert len(client.calls) == 1, "one call, no retry"
    assert counts["classified"] == 1 and counts["inconsistent"] == 1
    assert store.current_classification(conn, fresh)["category"] == "C"


def test_the_batch_collector_stores_an_inconsistent_reply_for_review_without_resubmitting(tmp_path, monkeypatch):
    from tests.test_classify_llm import _batch_client, _message as _billed, _submitted_batch
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path)
    conn = _db()
    ids = [_article(conn, 1), _article(conn, 2)]
    _submitted_batch(conn, ids)
    bad = json.dumps(_reply("A", issuer_type="none", state_basis="issuer_named"))
    good = json.dumps(_reply("A", issuer_type="government", state_basis="issuer_named"))
    out = cl._collect_batches(conn, _batch_client([(ids[0], "succeeded", _billed("end_turn", bad)),
                                                   (ids[1], "succeeded", _billed("end_turn", good))]))
    assert out["batch_collected"] == 2 and out["inconsistent"] == 1
    assert [store.current_classification(conn, i)["category"] for i in ids] == ["C", "A"]
    assert conn.execute("SELECT COUNT(*) FROM llm_batches").fetchone()[0] == 1, "nothing resubmitted"
