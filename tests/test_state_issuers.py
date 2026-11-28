"""Paid releases read for their own issuer against the registry of state issuers (pipeline/state_issuers.py,
sources/state_issuers.yaml), and the two groups held apart from state origin by the owner's decisions of
2026-11-24: state-owned enterprises and state-run institutions (S, D13) and Hong Kong and Macao SAR government
and statutory bodies (H, D14). A release carrying the shared Beijing contact number and naming no registered
issuer goes to the model (D15)."""
import copy
import datetime as dt
import json

import pytest

from pipeline import classify_llm, classify_rules as cr, config, export, methodology, model_only, state_issuers as si, store


# ---------------------------------------------------------------------------
# The registry and its schema
# ---------------------------------------------------------------------------

def test_the_committed_registry_validates_and_every_entry_has_a_public_source():
    assert si.validate_file() == []
    data = si.load_file()
    groups = {e["group"] for e in data["issuers"]}
    assert groups == {"A", "S", "H"}
    for e in data["issuers"]:
        assert e["source_url"].startswith(("https://", "http://")) and e["basis"] and e["source_title"], e["id"]
    ids = {e["id"] for e in data["issuers"]}
    # The groups the brief names: central state media, the international communication centres, publicity
    # departments, people's governments, party dailies, state-owned and SAR issuers.
    for i in ("xinhua_silk_road", "xinhua_finance", "xinhuanet", "cgtn", "peoples_daily", "global_times", "cicg",
              "china_economic_net", "china_media_group", "smg_yicai", "iqingdao", "injiangsu", "jxicc",
              "international_communication_centre", "publicity_department", "peoples_government", "party_daily",
              "chery", "hong_kong_tourism_board", "macao_sar"):
        assert i in ids, i
    assert si.main(["validate"]) == 0


def _good():
    return copy.deepcopy(si.load_file())


@pytest.mark.parametrize("change, needle", [
    (lambda d: d["issuers"][0].pop("source_url"), "source_url"),
    (lambda d: d["issuers"].append(dict(d["issuers"][0])), "listed twice"),
    (lambda d: d["issuers"][-1].update(issuer_type="government"), "takes issuer type"),
    (lambda d: next(e for e in d["issuers"] if e["group"] == "S").update(kind="sar_body"), "group S"),
    (lambda d: d["issuers"][0].update(patterns=["{name} Tourism Board of somewhere"]), "only the Chinese state uses"),
    (lambda d: d["issuers"][0].update(patterns=["Publicity Department of ({place}"]), "does not compile"),
    (lambda d: d["places"]["cities"].append("Hong Kong"), "not a mainland place"),
    (lambda d: d["issuers"][0].update(basis="A body — with a dash in its basis text."), "dash"),
    (lambda d: d["issuers"].append(dict(d["issuers"][1], id="other_xinhua")), "also listed under"),
])
def test_the_validator_refuses(change, needle):
    d = _good()
    change(d)
    errs = si.validate(d)
    assert errs and any(needle in e for e in errs), errs


# ---------------------------------------------------------------------------
# Reading a release
# ---------------------------------------------------------------------------

WIRE = "NANNING, China, Oct. 3, 2026 (GLOBE NEWSWIRE) -- "


def _read(body, title="", author="", labels="", arrival="editorial_feed"):
    return si.read_release(title, body, author, labels, arrival)


def test_only_a_paid_release_is_read():
    news = "BEIJING, Oct 3 (Reuters) - The Publicity Department of the CPC Nanning Municipal Committee said on Friday ..."
    assert _read(news)["release"] is None and _read(news)["issuer"] is None
    # the same text from a release section, from a wire named in the labels, or under a SOURCE trailer is a release
    assert _read(news, arrival="press_release_section")["release"] == "press_release_section"
    assert _read(news, labels="creator: GlobeNewswire")["release"] == "wire"
    assert _read(news + "\nSOURCE Lanshan Robotics")["release"] == "wire"
    assert _read(news, author="GlobeNewswire")["release"] == "wire"
    assert _read("x", labels="This section is Partnership Content supplied by GlobeNewswire")["release"] == "wire"
    # without arrival match_signatures reads no release at all: the keyword gate is unchanged
    res = cr.match_signatures("", WIRE + "The Nanning Municipal People's Government held a forum.", None)
    assert res["issuer"] is None and res["decision"] != "A"
    res = cr.match_signatures("", WIRE + "The Nanning Municipal People's Government held a forum.", None, arrival="editorial_feed")
    assert res["issuer"]["issuer"] == "peoples_government" and res["decision"] == "A"


@pytest.mark.parametrize("body, issuer, group", [
    (WIRE + "The Nanning Municipal People's Government held a forum.", "peoples_government", "A"),
    (WIRE + "The People's Government of Guangxi Zhuang Autonomous Region held a forum.", "peoples_government", "A"),
    (WIRE + "Publicity Department of the CPC Dingzhou Municipal Committee presents a heritage week.", "publicity_department", "A"),
    (WIRE + "The Jiangxi International Communication Center (JXICC) hosted youth leaders.", "jxicc", "A"),
    (WIRE + "iQingdao reports that the fashion week opened.", "iqingdao", "A"),
    (WIRE + "A news report from Hubei Daily: the expo opened.", "party_daily", "A"),
    (WIRE + "Beijing Municipal Bureau of Agriculture and Rural Affairs opened the conference.", "government_bureau", "A"),
    (WIRE + "Shaanxi International Communication Center presents a film festival.", "international_communication_centre", "A"),
    (WIRE + "Chery Automobile unveiled a new model.", "chery", si.S),
    (WIRE + "China Eastern Airlines (CEAir) showcased its digital tools.", "china_eastern", si.S),
    (WIRE + "Harbin Institute of Technology brings science books to schools.", "state_universities", si.S),
    (WIRE + "The Hong Kong Tourism Board (HKTB) launched a lantern festival.", "hong_kong_tourism_board", si.H),
    (WIRE + "The Macao Government Tourism Office invites travellers to a food festival.", "macao_sar", si.H),
    ("GUIGANG, China, Oct. 9, 2026 (GLOBE NEWSWIRE) -- Bureau of Industry and Information Technology, Gangbei District, "
     "Guigang City reports a new paper industry base.", "district_bureau", "A"),
])
def test_the_dateline_issuer_decides(body, issuer, group):
    hit = _read(body)["issuer"]
    assert hit and hit["issuer"] == issuer and hit["group"] == group and hit["position"] == "dateline", hit


def test_a_district_bureau_needs_a_dateline_in_china():
    body = ("PHOENIX, Arizona, Oct. 9, 2026 (GLOBE NEWSWIRE) -- Bureau of Land Management, Phoenix District reports "
            "a new grazing plan.")
    assert _read(body)["issuer"] is None


def test_a_lower_case_phrase_is_never_a_name():
    assert _read(WIRE + "China mobile gaming firm Lanshan Games launched a title.")["issuer"] is None
    assert _read(WIRE + "China Mobile launched a 6G pilot.")["issuer"]["issuer"] == "telecom_operators"


@pytest.mark.parametrize("body", [
    # mentions inside sentences of a release never name its issuer
    WIRE + "Lanshan Robotics signed an agreement with the Nanning Municipal People's Government.\nSOURCE Lanshan Robotics",
    WIRE + "Lanshan Robotics said the Hong Kong Tourism Board would promote the event.\nMedia contact: press@lanshan.test",
    WIRE + "Lanshan Robotics, a supplier to Chery and China Eastern Airlines, opened a plant.",
    WIRE + "Lanshan Robotics opened a plant.\nThe forum, organised by the Publicity Department of Nanning City, closed on Friday.",
    WIRE + "Lanshan Robotics opened a plant.\nSource: National data from the Hubei Daily survey and others, 2026",
])
def test_a_mention_inside_a_release_never_fires(body):
    out = _read(body)
    assert out["release"] == "wire" and out["issuer"] is None, out["issuers"]


@pytest.mark.parametrize("number", ["86-10-63074558", "+86 10 6307 4558", "(86)10-63074558", "8610-63074558",
                                    "010-63074558", "+86-10-6307-4558", "86 10 63074558", "+86 (0)10 6307 4558"])
def test_the_shared_contact_number_in_its_common_forms(number):
    assert si.CONTACT_RE.search("Tel: %s" % number), number


@pytest.mark.parametrize("text", ["Tel: 86-10-63074559", "ID 186106307455812", "Order 63074558"])
def test_other_numbers_are_not_the_contact_number(text):
    assert not si.CONTACT_RE.search(text), text


def test_the_contact_number_sends_a_release_to_the_model_and_never_labels_it():
    body = WIRE + "The 2026 friendship conference opened on Friday in a Chinese city.\nMedia contact: Mr. Zeng, Tel 86-10-63074558"
    res = cr.match_signatures("Friendship conference opens", body, None, arrival="editorial_feed")
    assert res["decision"] == "A_candidate"
    assert [m["id"] for m in res["matches"]] == [si.CONTACT_ID]
    assert res["matches"][0]["strength"] == "hint"
    assert cr.route_of(res["matches"]) == "unattributed", "a hint never gives a route"
    # with a registered issuer named, the issuer decides and the number adds nothing
    named = body.replace("The 2026 friendship conference", "The Publicity Department of Nanning City")
    res = cr.match_signatures("", named, None, arrival="editorial_feed")
    assert res["decision"] == "A" and si.CONTACT_ID not in [m["id"] for m in res["matches"]]
    # in a news story the number is nothing
    res = cr.match_signatures("", "Researchers traced the number 86-10-63074558 on many releases.", None, arrival="editorial_feed")
    assert res["decision"] == "none"


def test_a_party_state_issuer_wins_over_a_state_owned_one_and_another_strong_signature_wins_over_both():
    body = WIRE + "China Eastern Airlines opened a route.\nSOURCE Xinhua Silk Road"
    res = cr.match_signatures("", body, None, arrival="editorial_feed")
    assert res["decision"] == "A" and res["issuer"]["issuer"] == "xinhua_silk_road" and res["issuer_type"] == "state_media"
    soe = WIRE + "Chery Automobile opened a plant, Xinhua reported.\n(Xinhua)"
    res = cr.match_signatures("", "BEIJING, Oct. 3 (Xinhua) -- " + soe, None, arrival="editorial_feed")
    assert res["decision"] == "A" and res["issuer_type"] is None, "a wire credit decides state origin, not the issuer"


# ---------------------------------------------------------------------------
# Storing a label and reading it back
# ---------------------------------------------------------------------------

def _article(conn, tmp_path, monkeypatch, body, title="Release", gate_terms=None, url="https://x.test/r"):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "b")
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": "CAN", "language": "en",
                                         "title": title, "status": "fetched", "gate_relevant": 1,
                                         "gate_terms": gate_terms or ["section:press_release"]})
    store.save_body(store.url_hash(url), body)
    conn.commit()
    return aid


@pytest.mark.parametrize("body, outcome, issuer_type, code", [
    (WIRE + "The Publicity Department of Nanning City opened the friendship conference in China.", "A", "party", "A"),
    (WIRE + "Changan Automobile launched a driver assistance pilot in China.", "S", "state_owned_enterprise", "S"),
    (WIRE + "The Hong Kong Tourism Board launched a lantern festival in Hong Kong, China.", "H", "sar_government", "H"),
])
def test_classify_article_stores_the_issuer_and_the_export_reads_its_group(tmp_path, monkeypatch, body, outcome, issuer_type, code):
    conn = store.connect(tmp_path / "c.db")
    aid = _article(conn, tmp_path, monkeypatch, body)
    out = cr.classify_article(conn, store.get_article(conn, aid))
    assert out["outcome"] == outcome
    cur = store.current_classification(conn, aid)
    assert (cur["method"], cur["category"], cur["issuer_type"]) == ("rules", "A", issuer_type)
    assert cur["route"] == "distribution_stamp" and cur["evidence_quote"]
    assert si.SIGNATURE_IDS["dateline"] in json.loads(cur["signatures_fired"])
    route = conn.execute("SELECT %s FROM classifications c WHERE c.id=?" % model_only.route_sql("c"), (cur["id"],)).fetchone()[0]
    assert model_only.headline_category(cur["category"], route, cur["method"]) == code
    assert model_only.headline_category(cur["category"], cur["route"], cur["method"], cur["issuer_type"]) == code


def test_a_section_release_with_only_the_contact_number_reaches_the_model_even_without_china_terms(tmp_path, monkeypatch):
    conn = store.connect(tmp_path / "n.db")
    body = WIRE + "The friendship conference opened on Friday.\nMedia contact: Ms. Zhu, Tel: 86-10-63074558"
    aid = _article(conn, tmp_path, monkeypatch, body, title="Friendship conference opens")
    out = cr.classify_article(conn, store.get_article(conn, aid))
    assert out["outcome"] == "llm"
    trig = json.loads(store.get_article(conn, aid)["llm_trigger"])
    assert trig["a_candidate"] == [si.CONTACT_ID] and any("63074558" in s for s in trig["spans"])


def test_reclassify_replaces_a_model_label_with_the_rules_issuer(tmp_path, monkeypatch):
    conn = store.connect(tmp_path / "r.db")
    body = WIRE + "The Hong Kong Tourism Board launched a lantern festival in Hong Kong, China."
    aid = _article(conn, tmp_path, monkeypatch, body)
    conn.execute("UPDATE articles SET status='classified' WHERE id=?", (aid,))
    store.insert_classification(conn, aid, "llm", "C", 0.8, ruleset_version="2026.10.1",
                                model_version=config.LLM_MODEL + "@" + classify_llm.PROMPT_VERSION)
    conn.commit()
    monkeypatch.setattr(config, "RULESET_VERSION", "2099.1.1")
    cr.reclassify(conn, "t")
    cur = store.current_classification(conn, aid)
    assert (cur["method"], cur["category"], cur["issuer_type"]) == ("rules", "A", "sar_government")


def test_model_labels_of_state_owned_and_sar_issuers_are_read_apart():
    assert model_only.headline_category("A", "unattributed", "llm", "state_owned_enterprise") == si.S
    assert model_only.headline_category("A", "wire_credit", "llm", "sar_government") == si.H
    assert model_only.headline_category("A", "wire_credit", "llm", "state_media") == "A"
    assert model_only.headline_category("C", None, "llm", "state_owned_enterprise") == "C"
    assert model_only.headline_category("A", si.READ_ROUTES[si.S], "llm") == si.S
    assert model_only.counted_route(si.READ_ROUTES[si.H], "llm") is None
    assert model_only.is_set_apart("A", si.READ_ROUTES[si.S], "llm") and model_only.is_set_apart("A", "unattributed", "llm")
    assert not model_only.is_set_apart("A", "wire_credit", "llm")
    assert not model_only.is_undetermined("A", si.READ_ROUTES[si.S], "llm"), "held apart, not undetermined"


# ---------------------------------------------------------------------------
# The export: in no count, on their own lines
# ---------------------------------------------------------------------------

OUTLETS = [{"id": "ca_paper", "name": "Paper", "country": "CAN", "language": "en", "tier": "national_daily",
            "active": True, "feeds": ["https://paper.test/rss"]}]


def _yesterday():
    return (dt.date.fromisoformat(export._today()) - dt.timedelta(days=1)).isoformat()


def _add(conn, n, category, method="llm", route=None, issuer_type=None, prefix="x"):
    for i in range(n):
        url = "https://paper.test/%s-%s-%s-%s-%d" % (prefix, category, method, issuer_type, i)
        aid = store.insert_discovered(conn, {"url": url, "outlet_id": "ca_paper", "country": "CAN", "language": "en",
                                             "title": "China release %s %d" % (prefix, i), "status": "fetched",
                                             "gate_relevant": 1})
        conn.execute("UPDATE articles SET discovered_at=?, status='classified' WHERE id=?",
                     ("%sT09:00:00+00:00" % _yesterday(), aid))
        store.insert_classification(conn, aid, method, category, 0.9, route=route, issuer_type=issuer_type,
                                    model_version=(config.LLM_MODEL + "@" + classify_llm.PROMPT_VERSION) if method == "llm" else None)
    conn.commit()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    from pipeline import model_only as mo
    d = tmp_path / "reviews"
    d.mkdir()
    monkeypatch.setattr(mo, "REVIEWS_DIR", d)
    c = store.connect(tmp_path / "e.db")
    store.sync_outlets(c, OUTLETS)
    _add(c, 3, "A", "rules", "distribution_stamp", "party")                     # party-state issuers: state origin
    _add(c, 2, "A", "rules", "distribution_stamp", "state_owned_enterprise")    # S by the rules
    _add(c, 1, "A", "llm", "unattributed", "state_owned_enterprise")            # S by the model (not U)
    _add(c, 4, "A", "rules", "distribution_stamp", "sar_government")            # H by the rules
    _add(c, 1, "A", "llm", "wire_credit", "sar_government")                     # H by the model
    _add(c, 2, "A", "llm", "unattributed")                                      # U
    _add(c, 5, "C", "rules")
    return c


def test_state_owned_and_sar_releases_are_in_no_count_and_no_denominator(conn):
    export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [])
    daily = export.build_daily(conn, OUTLETS)
    outlets_json = export.build_outlets(conn, OUTLETS)
    meta = export.build_meta(conn, OUTLETS, [], latest, outlets_json)
    k = latest["countries"]["CAN"]["all_time"]
    assert (k["A"], k["S"], k["H"], k["U"], k["C"]) == (3, 3, 5, 2, 5)
    assert k["china_total"] == k["A"] + k["B"] + k["C"] == 8
    assert k["share_a"] == round(3 / 8.0, 4)
    tot = latest["totals"]["all_time"]
    assert (tot["A"], tot["S"], tot["H"]) == (3, 3, 5)
    day = daily[_yesterday()[:7]]["days"][_yesterday()]
    assert (day["countries"]["CAN"]["S"], day["countries"]["CAN"]["H"]) == (3, 5)
    assert day["routes"]["CAN"] == {"distribution_stamp": 3, "unattributed": 2}, "S and H are in no route"
    by_id = {o["id"]: o for o in outlets_json["outlets"]}
    assert (by_id["ca_paper"]["counts"]["S"], by_id["ca_paper"]["counts"]["H"]) == (3, 5)
    assert meta["route_totals"]["distribution_stamp"] == 3 and "state_owned_issuer" not in meta["route_totals"]
    sep = meta["separate_issuers"]
    assert sep["groups"]["S"]["count"] == 3 and sep["groups"]["H"]["count"] == 5
    assert sep["groups"]["S"]["by_method"] == {"rules": 2, "model": 1}
    assert sep["decided_on"] == "2026-11-24" and sep["excluded_from_counts"] and not sep["rejoins_state_origin"]
    assert meta["undetermined"]["count"] == 2, "a model state-owned label is held apart, not undetermined"
    f = meta["findings"]
    assert f["all_time"]["state_origin"] == 3 and f["all_time"]["china_total"] == 8
    assert f["separate_issuers"]["S"]["all_time"] == 3 and f["separate_issuers"]["H"]["all_time"] == 5
    assert f["separate_issuers"]["S"]["last_30d"] == 3
    it = meta["issuer_type_totals"]["by_type"]
    assert it["state_owned_enterprise"] == {"S": 3} and it["sar_government"] == {"H": 5}
    # the evidence query of state origin leaves them out
    n = conn.execute("SELECT COUNT(*) FROM classifications c WHERE c.is_current=1 AND c.category='A'%s"
                     % model_only.evidence_sql("c")).fetchone()[0]
    assert n == 3
    # the methodology says what they are, that they are held apart, and when the owner decided it
    text = methodology.issuer_text(meta)
    assert "2026-11-24" in text and "held apart" in text and "Category" not in text
    assert "3 placements were held apart as state-owned issuer releases and 5 as SAR" in text
    for bad in ("–", "—", "Category S", "Category H"):
        assert bad not in text


def test_the_audit_files_keep_the_labels_with_their_evidence(conn, tmp_path):
    export.write_audit_files(conn, tmp_path / "audit")
    lines = [json.loads(l) for p in sorted((tmp_path / "audit").glob("*.jsonl")) for l in p.read_text().splitlines()]
    kept = [l for l in lines if l.get("issuer_type") in ("state_owned_enterprise", "sar_government")]
    assert len(kept) == 8 and all(l["category"] == "A" for l in kept), "stored as they were made"


def test_no_dash_and_no_category_letters_in_the_new_text():
    for text in (si.methodology_text(None), si.methodology_text({"groups": {"S": {"count": 2}, "H": {"count": 1}}}),
                 si.LABELS[si.S], si.LABELS[si.H]):
        assert "–" not in text and "—" not in text and "Category" not in text
    raw = si.PATH.read_text(encoding="utf-8")
    assert "–" not in raw and "—" not in raw


def test_a_line_that_lists_several_issuers_names_the_first_and_a_sentence_never_does():
    trailer = ("\nSource: Publicity Department of the CPC Dingzhou Municipal Committee, Dingzhou Municipal Culture, "
               "Radio, Television and Tourism Bureau and Dingzhou Converged Media Center")
    hit = _read(WIRE.replace("NANNING", "DINGZHOU") + "A heritage week opened." + trailer)["issuer"]
    assert hit["issuer"] == "publicity_department" and hit["position"] == "source_trailer"
    for line in ("\nThe Publicity Department of Nanning City noted that winners receive subsidies",
                 "\nHong Kong Tourism Board, the agency that runs the festival, said visitors were welcome"):
        assert _read(WIRE + "A conference opened." + line)["issuer"] is None, line


def test_a_real_release_is_read_for_its_issuer():
    """The Antara copy of a Hangzhou release (tests/fixtures/extraction): its headline and Source line name a
    municipal bureau, which the paper's hand coding also codes as the state issuer."""
    import gzip
    from pathlib import Path
    from pipeline import extract
    html = gzip.open(Path(__file__).parent / "fixtures" / "extraction" / "en_id_antara_release.html.gz", "rt").read()
    body = extract.extract(html, "https://en.antaranews.com/news/1")["text"]
    title = 'Hangzhou Investment Promotion Bureau: Hangzhou Finds a "Gateway to the Sea" in Hong Kong'
    res = cr.match_signatures(title, body, None, arrival="editorial_feed")
    assert res["decision"] == "A" and res["issuer"]["issuer"] == "government_bureau" and res["issuer_type"] == "government"
    assert {"state_issuer_header", "state_issuer_source_trailer"} <= {h["position"] and si.SIGNATURE_IDS[h["position"]]
                                                                      for h in si.read_release(title, body, "", "", "editorial_feed")["issuers"]}
    # without the issuer reading the release was only a candidate for the model
    assert cr.match_signatures(title, body, None)["decision"] == "A_candidate"


def test_an_export_holding_every_category_passes_the_workflow_schema_check(conn, tmp_path, monkeypatch):
    # Every run from 2026-11-25 failed at the workflow's schema check once a real S label reached the
    # site files, because the check's category list lacked S and H; the committed site data had no S item,
    # so no test saw it. Here the export holds A, C, U, S and H and the same check runs on what it writes.
    from tests.test_export import validate_export_dir
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "e.db")
    out = tmp_path / "out"
    export.run(conn, "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M.md")
    cats = {a["category"] for f in (out / "articles").glob("*.json") for a in json.loads(f.read_text())}
    assert {"A", "C", model_only.CODE, si.S, si.H} <= cats
    validate_export_dir(out)
