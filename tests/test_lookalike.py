"""The lookalike route: an article whose headline, summary or body nearly repeats a state origin item
from another outlet goes to the model as a state origin candidate instead of being labelled by rules."""
import datetime as dt
import json

import pytest

from pipeline import body_dups, classify_llm, classify_rules as cr, config, lookalike, recall_audit, store

TITLE = "Cina, famiglia di tigri siberiane selvatiche avvistata in strada"
CREDITED = ("PECHINO (XINHUA/ITALPRESS) - Una famiglia di tigri siberiane selvatiche e' stata avvistata "
            "su una strada della provincia dello Heilongjiang, nel nord-est della Cina.")
UNCREDITED = ("Una famiglia di tigri siberiane selvatiche e' stata avvistata su una strada della provincia "
              "dello Heilongjiang, nel nord-est della Cina. Gli animali sono stati ripresi da alcuni automobilisti.")


def _iso(days: float) -> str:
    base = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    return (base + dt.timedelta(days=days)).isoformat()


def _add(conn, url, outlet, title, body=None, days=0.0, summary=None, status="fetched", lang="it"):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": "ITA", "language": lang,
                                         "title": title, "summary": summary, "status": status, "gate_relevant": 1})
    conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (_iso(days), aid))
    if body is not None:
        store.save_body(store.url_hash(url), body)
    conn.commit()
    return aid


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    c = store.connect(tmp_path / "l.db")
    c.execute("INSERT INTO outlets(id, name, country, language, tier, active, feeds, updated_at) "
              "VALUES ('it_italpress', 'Italpress', 'ITA', 'it', 'agency', 1, '[]', '2026-10-01')")
    c.commit()
    return c


def _state_origin(conn, aid):
    store.insert_classification(conn, aid, "rules", "A", 1.0, signatures_fired=["xinhua_italpress"])
    conn.commit()


def _ref(aid=1, outlet="it_italpress", title=TITLE, days=0.0, summary="", lang="it"):
    return {"article_id": aid, "outlet_id": outlet, "outlet_name": "Italpress", "language": lang, "title": title,
            "summary": summary, "discovered_at": _iso(days)}


def _row(aid=2, outlet="it_libero", title=TITLE, days=0.0, summary="", lang="it"):
    return {"id": aid, "outlet_id": outlet, "language": lang, "title": title, "summary": summary,
            "discovered_at": _iso(days)}


# ---------------------------------------------------------------------------
# the index
# ---------------------------------------------------------------------------

def test_an_uncredited_headline_matches_the_credited_item_of_another_outlet():
    idx = lookalike.RouteIndex([_ref()])
    m = idx.match(_row())
    assert m == {"method": "title", "similarity": 1.0, "article_id": 1, "outlet_id": "it_italpress",
                 "outlet": "Italpress", "title": TITLE}


def test_the_same_outlet_and_the_article_itself_never_match():
    idx = lookalike.RouteIndex([_ref()])
    assert idx.match(_row(outlet="it_italpress")) is None
    assert idx.match(_row(aid=1, outlet="it_libero")) is None


def test_the_window_runs_from_eleven_days_before_to_eleven_days_after():
    assert (lookalike.WINDOW_BEFORE_DAYS, lookalike.WINDOW_AFTER_DAYS) == (11, 11)
    idx = lookalike.RouteIndex([_ref(days=0)])
    assert idx.match(_row(days=10.9))           # the state origin item came 10.9 days earlier
    assert idx.match(_row(days=-10.9))          # or 10.9 days later
    assert idx.match(_row(days=-8.1))           # the press releases Antara reran about eight days later
    assert idx.match(_row(days=11.1)) is None
    assert idx.match(_row(days=-11.1)) is None  # the later Djokovic story came 12 days after its lookalike
    assert idx.match(_row(days=-12.0)) is None


def test_the_stage_index_reaches_both_edges_of_the_window(conn):
    for n, days in enumerate((-10.9, 10.9, -11.1, 11.1)):
        ref = _add(conn, "https://italpress.example/w%d" % n, "it_italpress", TITLE, CREDITED, days=days, status="classified")
        _state_origin(conn, ref)
    aid = _add(conn, "https://libero.example/w", "it_libero", TITLE, UNCREDITED, days=0)
    looks = lookalike.build_index(conn, [store.get_article(conn, aid)])
    assert sorted(round((r["t"] - lookalike._ts(_iso(0))) / 86400, 1) for r in looks.refs.values()) == [-10.9, 10.9]


def test_a_published_time_is_credible_only_within_the_tracker_period_and_before_discovery():
    item = lambda pub, disc=0.0, dor=None: {"published_at": pub, "discovered_at": _iso(disc), "day_of_record": dor}
    assert lookalike.published_ts(item(_iso(-1))) == lookalike._ts(_iso(-1))
    assert lookalike.published_ts(item(" 2026-09-30 ")) == lookalike._ts("2026-09-30")   # a date alone
    assert lookalike.published_ts(item(_iso(0.5))) is None          # after its discovery
    assert lookalike.published_ts(item("2026-09-02T23:00:00+00:00", 5)) is None   # before the period
    assert lookalike.published_ts(item(None)) is None
    assert lookalike.published_ts(item("yesterday")) is None
    assert lookalike.published_ts(item(_iso(-14.5))) is None        # long before discovery
    assert lookalike.published_ts(item(_iso(-13.9))) is not None
    assert lookalike.published_ts(item(_iso(-20), dor=_iso(-20)[:10])) == lookalike._ts(_iso(-20))   # a backfill read


def _pub(d, days):
    return dict(d, published_at=_iso(days))


def test_two_credible_published_times_decide_the_window():
    # An advertorial whose copies were published a day apart but discovered twenty days apart.
    idx = lookalike.RouteIndex([_pub(_ref(days=-20), -21)])
    assert idx.match(_row(days=0)) is None
    assert idx.match(_pub(_row(days=0), -12))["article_id"] == 1
    # A copy discovered the same day but published twelve days after the credited item is not paired.
    idx = lookalike.RouteIndex([_pub(_ref(days=0), -12.5)])
    assert idx.match(_pub(_row(days=0), -0.5)) is None
    assert idx.match(_row(days=0))["article_id"] == 1   # no published time of its own: by discovery


def test_one_item_without_a_credible_published_time_leaves_both_on_discovery():
    late = dict(_ref(days=-20), published_at="2026-08-27")    # published before the tracker's period
    assert lookalike.RouteIndex([late]).match(_pub(_row(days=0), -12)) is None
    stale = _pub(_ref(days=-20), -40)                         # 20 days before its discovery, no backfill
    assert lookalike.RouteIndex([stale]).match(_pub(_row(days=0), -12)) is None
    backfilled = dict(stale, day_of_record=_iso(-40)[:10])    # the same, brought in by a backfill read
    assert lookalike.RouteIndex([backfilled]).match(_pub(_row(days=-30), -32))["article_id"] == 1


def _published(conn, aid, days, day_of_record=None):
    conn.execute("UPDATE articles SET published_at=?, day_of_record=? WHERE id=?", (_iso(days), day_of_record, aid))
    conn.commit()


def test_the_stage_index_takes_a_state_origin_item_found_late_by_its_publication(conn):
    near = _add(conn, "https://italpress.example/p1", "it_italpress", TITLE, CREDITED, days=13, status="classified")
    _published(conn, near, 1)
    far = _add(conn, "https://italpress.example/p2", "it_italpress", TITLE, CREDITED, days=26, status="classified")
    _published(conn, far, 12)
    backfill = _add(conn, "https://italpress.example/p3", "it_italpress", TITLE, CREDITED, days=40, status="classified")
    _published(conn, backfill, 2, _iso(2)[:10])
    for ref in (near, far, backfill):
        _state_origin(conn, ref)
    aid = _add(conn, "https://libero.example/p", "it_libero", TITLE, UNCREDITED, days=0)
    _published(conn, aid, -0.2)
    looks = lookalike.build_index(conn, [store.get_article(conn, aid)])
    assert sorted(looks.refs) == [near, backfill]
    assert cr.classify_article(conn, store.get_article(conn, aid), looks)["outcome"] == "lookalike"


def test_a_closer_match_outside_the_window_does_not_hide_one_inside_it():
    near = TITLE.replace("in strada", "per strada")
    idx = lookalike.RouteIndex([_ref(aid=1, days=-30), _ref(aid=3, title=near, days=-1)])
    assert idx.match(_row())["article_id"] == 3


def test_headlines_below_the_threshold_or_too_short_do_not_match():
    idx = lookalike.RouteIndex([_ref(title="Cina, tigri avvistate"), _ref(aid=3, title="Borsa di Milano chiude in rialzo oggi")])
    assert idx.match(_row(title="Cina, tigri avvistate")) is None   # under TITLE_MIN_TOKENS words
    assert idx.match(_row(title="Borsa di Tokyo chiude in calo dopo dati")) is None


def test_a_repeated_feed_summary_matches_under_a_rewritten_headline():
    summary = " ".join("parola%d" % i for i in range(30))
    idx = lookalike.RouteIndex([_ref(summary=summary)])
    m = idx.match(_row(title="Un titolo del tutto diverso per lo stesso testo", summary=summary))
    assert m["method"] == "summary" and m["similarity"] == 1.0


def test_a_repeated_body_matches_by_fingerprint():
    text = " ".join("w%d" % i for i in range(80))
    _, packed = body_dups.fingerprint(text)
    sig = body_dups._SIG.unpack(packed)
    idx = lookalike.RouteIndex([_ref(title="Altro titolo che non somiglia affatto al primo")], {1: sig})
    assert idx.match(_row(), None) is None
    m = idx.match(_row(), sig)
    assert m["method"] == "body" and m["similarity"] == 1.0


def test_recall_audit_uses_the_shared_helpers():
    assert recall_audit.title_set is lookalike.title_set
    assert recall_audit.shingles is lookalike.shingles
    assert recall_audit._Index is lookalike._Index
    assert recall_audit.TITLE_JACCARD == lookalike.TITLE_JACCARD


# ---------------------------------------------------------------------------
# the rules stage
# ---------------------------------------------------------------------------

def test_an_uncredited_copy_goes_to_the_model_instead_of_a_rules_label(conn):
    ref = _add(conn, "https://italpress.example/1", "it_italpress", TITLE, CREDITED, days=-1, status="classified")
    _state_origin(conn, ref)
    aid = _add(conn, "https://libero.example/1", "it_libero", TITLE, UNCREDITED)
    row = store.get_article(conn, aid)
    assert cr.match_signatures(row["title"], UNCREDITED, None)["decision"] == "none"
    out = cr.classify_article(conn, row)
    assert out["outcome"] == "lookalike"
    a = store.get_article(conn, aid)
    assert a["status"] == "awaiting_llm" and a["llm_pending"] == 1
    trig = json.loads(a["llm_trigger"])
    assert trig["a_candidate"] == [] and trig["triggers"] == []
    assert trig["lookalike"] == {"method": "title", "similarity": 1.0, "article_id": ref, "outlet_id": "it_italpress",
                                 "outlet": "Italpress", "title": TITLE}
    assert store.current_classification(conn, aid) is None


def test_without_a_state_origin_counterpart_the_rules_label_stands(conn):
    other = _add(conn, "https://italpress.example/2", "it_italpress", TITLE, CREDITED, days=-1, status="classified")
    store.insert_classification(conn, other, "rules", "C", 0.75)
    aid = _add(conn, "https://libero.example/2", "it_libero", TITLE, UNCREDITED)
    assert cr.classify_article(conn, store.get_article(conn, aid))["outcome"] == "C"


def test_the_stage_counts_lookalikes(conn):
    ref = _add(conn, "https://italpress.example/3", "it_italpress", TITLE, CREDITED, days=-1, status="classified")
    _state_origin(conn, ref)
    _add(conn, "https://libero.example/3", "it_libero", TITLE, UNCREDITED)
    counts = cr.run(conn, "t")
    assert counts["lookalike"] == 1 and counts["C"] == 0


def test_the_sweep_catches_a_copy_labelled_before_its_counterpart(conn):
    aid = _add(conn, "https://libero.example/4", "it_libero", TITLE, UNCREDITED, days=0)
    ref = _add(conn, "https://italpress.example/4", "it_italpress", TITLE, CREDITED, days=0.5)
    # The copy is judged first: nothing to match yet, so the rules label it.
    assert cr.classify_article(conn, store.get_article(conn, aid))["outcome"] == "C"
    assert cr.classify_article(conn, store.get_article(conn, ref))["outcome"] == "A"
    conn.commit()
    out = cr.sweep_lookalikes(conn)
    assert out["routed"] == 1 and out["refs"] == 1
    a = store.get_article(conn, aid)
    assert a["status"] == "awaiting_llm" and json.loads(a["llm_trigger"])["lookalike"]["article_id"] == ref
    assert store.current_classification(conn, aid) is None
    # Routed once only.
    assert cr.sweep_lookalikes(conn)["routed"] == 0


@pytest.mark.parametrize("days,routed", [(10.5, 1), (11.5, 0)])
def test_the_sweep_catches_a_counterpart_discovered_days_later_within_the_window(conn, days, routed):
    # The uncredited copy was labelled by rules days ago; the credited copy arrives and is labelled now.
    aid = _add(conn, "https://libero.example/7", "it_libero", TITLE, UNCREDITED, days=0)
    assert cr.classify_article(conn, store.get_article(conn, aid))["outcome"] == "C"
    ref = _add(conn, "https://italpress.example/7", "it_italpress", TITLE, CREDITED, days=days)
    assert cr.classify_article(conn, store.get_article(conn, ref))["outcome"] == "A"
    conn.commit()
    out = cr.sweep_lookalikes(conn)
    assert out["refs"] == 1 and out["routed"] == routed and out["checked"] == routed


def test_the_sweep_pairs_a_backfilled_counterpart_by_publication(conn):
    # The uncredited copy ran on day 0; a backfill read finds the credited copy, published the same
    # day, twenty days later.
    aid = _add(conn, "https://libero.example/8", "it_libero", TITLE, UNCREDITED, days=0)
    _published(conn, aid, -0.1)
    assert cr.classify_article(conn, store.get_article(conn, aid))["outcome"] == "C"
    ref = _add(conn, "https://italpress.example/8", "it_italpress", TITLE, CREDITED, days=20)
    _published(conn, ref, 0.2, _iso(0.2)[:10])
    assert cr.classify_article(conn, store.get_article(conn, ref))["outcome"] == "A"
    conn.commit()
    out = cr.sweep_lookalikes(conn)
    assert out["refs"] == 1 and out["routed"] == 1 and out["checked"] == 1
    assert json.loads(store.get_article(conn, aid)["llm_trigger"])["lookalike"]["article_id"] == ref


def test_the_sweep_ignores_old_state_origin_labels(conn):
    aid = _add(conn, "https://libero.example/5", "it_libero", TITLE, UNCREDITED)
    ref = _add(conn, "https://italpress.example/5", "it_italpress", TITLE, CREDITED, status="classified")
    store.insert_classification(conn, aid, "rules", "C", 0.75)
    _state_origin(conn, ref)
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=cr.LOOKALIKE_SWEEP_HOURS + 1)
    assert cr.sweep_lookalikes(conn, now=later) == {"refs": 0, "checked": 0, "routed": 0, "stopped_deadline": False}


# ---------------------------------------------------------------------------
# the model's input
# ---------------------------------------------------------------------------

def test_the_model_sees_the_matched_headline_and_outlet_but_not_the_articles_own(conn):
    ref = _add(conn, "https://italpress.example/6", "it_italpress", TITLE, CREDITED, days=-1, status="classified")
    _state_origin(conn, ref)
    aid = _add(conn, "https://libero.example/6", "it_libero", TITLE, UNCREDITED)
    cr.classify_article(conn, store.get_article(conn, aid))
    row = store.get_article(conn, aid)
    ctx = classify_llm.lookalike_context(row)
    msg = classify_llm.request_params(row["title"], UNCREDITED, ctx)["messages"][0]["content"]
    assert "Italpress" in msg and msg.count(TITLE) == 2
    assert "it_libero" not in msg and "libero" not in msg.lower() and "ITA" not in msg
    assert msg.startswith("Headline: ") and msg.endswith("Classify this article. Return only the JSON object.")


def test_other_articles_get_the_unchanged_message():
    assert classify_llm.build_user_message("t", "b") == classify_llm.build_user_message("t", "b", None)
    assert "Context" not in classify_llm.build_user_message("t", "b")
    assert classify_llm.lookalike_context({"llm_trigger": json.dumps({"triggers": ["cites_xinhua"]})}) is None


def test_the_prompt_says_an_uncredited_state_text_is_still_state_origin():
    assert "State text republished without its credit line is still state origin" in classify_llm.SYSTEM_PROMPT
