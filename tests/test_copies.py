"""State copies and the alteration score (pipeline/copies.py), on synthetic data.

Pairing: a reference original, the monitored fallback, a translation, the 30 percent alignment rule,
credit consistency, and the lookalike snapshot surviving an llm_trigger rewrite. Metrics: verbatim,
cut, an added rebuttal and a rewritten headline. Then the export's shape (no body text), the review
file, the blind sample and the hand check maths. Labels never change.
"""
import csv
import datetime as dt
import json

import pytest

from pipeline import config, copies, measures, reference, store

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

PARAS = [
    "China and the European Union agreed on Monday to keep their bilateral trade ties stable and to expand cooperation in green industries and digital trade over the coming year.",
    "The two sides held the talks in Beijing, where senior officials reviewed progress on market access, supply chains and the protection of investment across a range of sectors.",
    "Officials said both sides would set up a working group to address concerns raised by companies about export controls and certification rules for electric vehicles and batteries.",
    "The meeting also covered agricultural exports, cooperation on climate policy and the next round of the high level economic dialogue planned for the spring in Brussels.",
    "Analysts in Beijing said the outcome showed a shared wish to manage differences through dialogue and to keep the largest trading relationship in the world on a steady footing.",
]
ORIGINAL = "\n".join(PARAS)
TITLE = "China, EU agree to keep bilateral trade ties stable and expand cooperation"
SOURCES = {"ref_xinhua_en": {"publisher": "xinhua", "language": "en", "credits": ["xinhua_", "source_xinhua"]},
           "ref_cgtn_en": {"publisher": "cgtn", "language": "en", "credits": ["cgtn_", "china_media_group_credit"]}}


def _iso(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    monkeypatch.setattr(config, "COLLECTION_START", "2000-01-01")
    c = store.connect(tmp_path / "c.db")
    yield c
    c.close()


def _article(conn, url, outlet, title, body=None, hours_ago=1.0, lang="en", country="USA", sigs=None, method="rules",
             route="wire_credit", cited=None, trigger=None, dup_group=None, published_hours_ago=None):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": country, "language": lang,
                                         "title": title, "status": "classified", "gate_relevant": 1})
    pub = _iso(published_hours_ago if published_hours_ago is not None else hours_ago + 0.5)
    conn.execute("UPDATE articles SET discovered_at=?, published_at=?, llm_trigger=?, dup_group_id=? WHERE id=?",
                 (_iso(hours_ago), pub, json.dumps(trigger) if trigger else None, dup_group, aid))
    if body is not None:
        store.save_body(store.url_hash(url), body)
    store.insert_classification(conn, aid, method, "A", 1.0, signatures_fired=sigs or [], china_sources_cited=cited,
                                route=route)
    conn.commit()
    return aid


def _reference(conn, url, source, title, body=None, hours_ago=3.0, lang="en", published=None):
    h = store.url_hash(url)
    cur = conn.execute(
        """INSERT INTO reference_items(source_id, url, url_hash, language, title, published_at, discovered_at, status)
           VALUES (?,?,?,?,?,?,?, 'fetched')""", (source, url, h, lang, title, published or _iso(hours_ago), _iso(hours_ago - 0.2)))
    if body is not None:
        store.save_body(reference.body_key(h), body)   # reference bodies have a key of their own
    conn.commit()
    return cur.lastrowid


def _row(conn, aid):
    r = conn.execute("SELECT * FROM state_copies WHERE copy_id=?", (aid,)).fetchone()
    return dict(r) if r else None


def _run(conn, sources=SOURCES):
    return copies.compute(conn, {}, None, sources=sources)


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------

def test_a_credited_copy_pairs_with_its_reference_original(conn):
    ref = _reference(conn, "https://english.news.cn/1", "ref_xinhua_en", TITLE, ORIGINAL, hours_ago=3,
                     published="%sT%s" % ((NOW - dt.timedelta(hours=3)).date(), "08:05:00+16:00"))
    body = "BEIJING, Oct. 13 (Xinhua) -- " + ORIGINAL + "\n(Photo by Li Ming/Xinhua)"
    aid = _article(conn, "https://paper.example/a", "us_paper", TITLE, body, hours_ago=1, sigs=["xinhua_dateline"])
    out = _run(conn)
    assert out["reference"] == 1 and out["scored"] == 1
    r = _row(conn, aid)
    assert r["original_kind"] == "reference" and r["ref_id"] == ref and r["original_source"] == "ref_xinhua_en"
    assert r["credit_consistent"] == 1 and r["match_method"] == "title"
    assert r["body_state"] == "both" and r["band"] == "verbatim" and r["novel_share"] < 0.03 and r["kept_share"] > 0.97
    # Times for the speed measure: credible times in UTC, their resolution, and the lag.
    assert r["copy_ts_res"] in ("second", "minute", "hour") and r["orig_ts_res"] == "minute"
    assert r["copy_published_at"] and r["original_published_at"] and r["lag_seconds"] is not None
    assert json.loads(r["details"])["alignment"] >= 0.9
    # Labels never change.
    assert conn.execute("SELECT COUNT(*) FROM classifications WHERE is_current=1 AND category='A'").fetchone()[0] == 1


def test_a_reference_body_is_read_under_its_own_key_and_a_monitored_article_at_the_same_address_keeps_its_body(conn):
    url = "https://english.news.cn/20261013/abc/c.html"
    unrelated = "\n".join(["A monitored page at the same address carries other text entirely, about rail freight "
                           "volumes between inland ports and the coast over the summer months."] * 3)
    # A monitored article (not state origin) at the reference item's address, with its own body.
    other = store.insert_discovered(conn, {"url": url, "outlet_id": "xx_mirror", "country": "USA", "language": "en",
                                           "title": "Rail freight", "status": "fetched", "gate_relevant": 1})
    store.save_body(store.url_hash(url), unrelated)
    ref = _reference(conn, url, "ref_xinhua_en", TITLE, ORIGINAL, hours_ago=3)
    assert reference.body_key(store.url_hash(url)) != store.url_hash(url)
    aid = _article(conn, "https://paper.example/a", "us_paper", TITLE, ORIGINAL, hours_ago=1, sigs=["xinhua_dateline"])
    _run(conn)
    r = _row(conn, aid)
    # Read under the bare url_hash the original would be the unrelated text and fail the alignment rule.
    assert r["original_kind"] == "reference" and r["ref_id"] == ref and r["body_state"] == "both"
    assert r["band"] == "verbatim" and json.loads(r["details"])["alignment"] > 0.9
    assert store.load_body(store.url_hash(url)) == unrelated and reference.load_body(store.url_hash(url)) == ORIGINAL
    assert other is not None
    # Without its reference body the pair stands, unscored, and the article's body is not taken for it.
    store.body_path(reference.body_key(store.url_hash(url))).unlink()
    _run(conn)
    r = _row(conn, aid)
    assert r["original_kind"] == "reference" and r["body_state"] == "copy_only" and r["band"] is None


def test_a_reference_of_another_publisher_is_not_credit_consistent(conn):
    _reference(conn, "https://english.news.cn/1", "ref_xinhua_en", TITLE, ORIGINAL, hours_ago=3)
    aid = _article(conn, "https://paper.example/a", "us_paper", TITLE, ORIGINAL, hours_ago=1, sigs=["cgtn_credit"])
    _run(conn)
    r = _row(conn, aid)
    assert r["original_kind"] == "none" and r["none_reason"] == "no_reference_match" and r["ref_id"] is None


def test_the_window_runs_from_eleven_days_before_to_one_day_after(conn):
    _reference(conn, "https://english.news.cn/old", "ref_xinhua_en", TITLE, None, hours_ago=12.5 * 24)
    aid = _article(conn, "https://paper.example/a", "us_paper", TITLE, None, hours_ago=1, sigs=["xinhua_dateline"])
    _run(conn)
    assert _row(conn, aid)["original_kind"] == "none"
    _reference(conn, "https://english.news.cn/ok", "ref_xinhua_en", TITLE, None, hours_ago=10 * 24)
    _run(conn)
    r = _row(conn, aid)
    assert r["original_kind"] == "reference" and r["body_state"] == "neither" and r["band"] is None


def test_under_thirty_percent_aligned_words_rejects_the_candidate(conn):
    other = "\n".join(("A different story about the harvest season in the north, with farmers reporting record yields "
                       "of wheat and corn after a mild summer and steady rainfall across several provinces.",) * 3)
    _reference(conn, "https://english.news.cn/1", "ref_xinhua_en", TITLE, other, hours_ago=3)
    aid = _article(conn, "https://paper.example/a", "us_paper", TITLE, ORIGINAL, hours_ago=1, sigs=["xinhua_dateline"])
    _run(conn)
    r = _row(conn, aid)
    assert r["original_kind"] == "none" and r["none_reason"] == "alignment_rejected"
    assert json.loads(r["details"])["rejected_alignment"] == 1
    # Without the copy's body the candidate cannot be checked and stands, unchecked.
    store.body_path(store.url_hash("https://paper.example/a")).unlink()
    _run(conn)
    r = _row(conn, aid)
    assert r["original_kind"] == "reference" and json.loads(r["details"])["alignment"] is None
    assert r["body_state"] == "original_only"


def test_a_translation_never_pairs_across_languages_and_falls_back_to_the_first_monitored_copy(conn):
    it = "\n".join(["La Cina e l'Unione europea hanno concordato lunedi di mantenere stabili i legami commerciali "
                    "bilaterali e di ampliare la cooperazione nelle industrie verdi e nel commercio digitale.",
                    "Le due parti hanno tenuto i colloqui a Pechino, dove alti funzionari hanno esaminato i progressi "
                    "sull'accesso al mercato, le catene di approvvigionamento e la tutela degli investimenti."])
    _reference(conn, "https://english.news.cn/1", "ref_xinhua_en", TITLE, ORIGINAL, hours_ago=5)
    title_it = "Cina e Ue concordano di mantenere stabili i legami commerciali bilaterali"
    first = _article(conn, "https://italpress.example/1", "it_italpress", title_it, "PECHINO (XINHUA) - " + it,
                     hours_ago=3, lang="it", country="ITA", sigs=["xinhua_italian_credit"], dup_group=None)
    second = _article(conn, "https://libero.example/1", "it_libero", title_it, it + "\n(ITALPRESS).",
                      hours_ago=1, lang="it", country="ITA", sigs=["xinhua_italian_credit"], dup_group=first)
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (first, first))
    conn.commit()
    _run(conn)
    a, b = _row(conn, first), _row(conn, second)
    assert a["original_kind"] == "none" and a["none_reason"] == "translation"
    assert json.loads(a["details"])["translation"] is True
    assert b["original_kind"] == "monitored" and b["original_article_id"] == first and b["original_source"] == "it_italpress"
    assert b["match_method"] == "dup_group" and b["credit_consistent"] == 1
    assert json.loads(b["details"])["translation"] is True
    assert b["band"] == "verbatim" and b["body_state"] == "both"
    exp = copies.build_export(conn, {"export_dir": conn_dir(conn)})
    assert exp["coverage"]["translation"] == 2 and exp["coverage"]["none_by_reason"] == {"translation": 1}


def conn_dir(conn):
    from pathlib import Path
    return Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent / "out" / "data"


def test_the_lookalike_snapshot_survives_an_llm_trigger_rewrite(conn):
    credited = _article(conn, "https://wire.example/1", "us_wire", TITLE, ORIGINAL, hours_ago=4, sigs=["xinhua_dateline"])
    match = {"method": "title", "similarity": 0.9, "article_id": credited, "outlet_id": "us_wire", "outlet": "Wire", "title": TITLE}
    copy = _article(conn, "https://blog.example/1", "us_blog", TITLE + " today", ORIGINAL, hours_ago=1, method="llm",
                    route="uncredited_copy", trigger={"a_candidate": [], "triggers": [], "spans": [], "lookalike": match})
    _run(conn, sources={})
    r = _row(conn, copy)
    assert r["original_kind"] == "monitored" and r["original_article_id"] == credited
    assert r["match_method"] == "lookalike_title" and json.loads(r["details"])["lookalike"]["article_id"] == credited
    # A reclassification rewrites llm_trigger; the row, paired again, keeps the match.
    conn.execute("UPDATE articles SET llm_trigger=? WHERE id=?", (json.dumps({"triggers": []}), copy))
    conn.commit()
    _run(conn, sources={})
    r = _row(conn, copy)
    assert r["original_kind"] == "monitored" and r["original_article_id"] == credited
    assert json.loads(r["details"])["lookalike"]["article_id"] == credited
    assert json.loads(r["details"])["credits"] == ["xinhua"]
    # The credited first copy has no earlier copy and no reference source: none, with the reason.
    assert _row(conn, credited)["none_reason"] == "no_reference_source"


def test_a_withdrawn_label_keeps_its_row_with_current_zero(conn):
    aid = _article(conn, "https://paper.example/a", "us_paper", TITLE, None, hours_ago=1, sigs=["xinhua_dateline"])
    _run(conn)
    assert _row(conn, aid)["current"] == 1
    store.insert_classification(conn, aid, "llm", "C", 0.9)
    conn.commit()
    out = _run(conn)
    assert out["withdrawn"] == 1 and _row(conn, aid)["current"] == 0
    assert copies.build_export(conn, {"export_dir": conn_dir(conn)})["coverage"]["copies"] == 0


def test_compute_stops_at_the_deadline_and_resumes(conn):
    ids = [_article(conn, "https://paper.example/%d" % i, "us_paper", TITLE + str(i), None, hours_ago=1 + i,
                    sigs=["xinhua_dateline"]) for i in range(3)]
    import time
    out = copies.compute(conn, {}, time.monotonic() - 1, sources=SOURCES)
    assert out["stopped_deadline"] and out["paired"] == 0 and out["remaining"] == 3
    out = copies.compute(conn, {}, None, sources=SOURCES)
    assert out["paired"] == 3 and out["remaining"] == 0
    assert all(_row(conn, i)["body_state"] == "no_original" for i in ids)


def test_old_rows_are_not_paired_again_unless_the_version_changes(conn, monkeypatch):
    aid = _article(conn, "https://paper.example/a", "us_paper", TITLE, None, hours_ago=20 * 24, sigs=["xinhua_dateline"])
    assert _run(conn)["paired"] == 1
    assert _run(conn)["todo"] == 0
    monkeypatch.setattr(copies, "VERSION", copies.VERSION + "x")
    assert _run(conn)["paired"] == 1 and _row(conn, aid)["version"] == copies.VERSION


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_metric_fixtures():
    v = copies.metrics("ROME (ITALPRESS) - " + ORIGINAL + "\nfsc/abr", ORIGINAL, TITLE, TITLE, "en", "USA")
    assert v["band"] == "verbatim" and v["novel_share"] == 0 and v["kept_share"] == 1 and v["headline_similarity"] == 1
    cut = copies.metrics("\n".join(PARAS[:2]), ORIGINAL, TITLE, TITLE, "en", "USA")
    assert cut["band"] == "verbatim" and cut["kept_share"] < 0.45 and cut["added_paragraphs"] == 0
    rebuttal = ("However, critics and human rights groups in the United States accused Beijing of using the talks as "
                "propaganda, and opposition lawmakers said the promises on market access had been made before and not kept.")
    added = copies.metrics(ORIGINAL + "\n" + rebuttal, ORIGINAL, TITLE, TITLE, "en", "USA")
    assert added["added_paragraphs"] == 1 and added["added_words"] > 25 and added["kept_share"] == 1
    assert added["cue_counterpoint"] >= 3 and added["cue_local"] >= 1
    assert added["band"] == "moderate" and abs(added["novel_share"] - added["added_words"] / float(added["copy_words"])) < 1e-3
    head = copies.metrics(ORIGINAL, ORIGINAL, "Beijing woos Brussels as trade tensions simmer", TITLE, "en", "USA")
    assert head["headline_similarity"] < 0.3 and head["band"] == "verbatim"
    # A rewritten paragraph counts as rewritten, not added.
    rew = PARAS[:]
    rew[1] = "The two sides held the talks in Beijing, where ministers argued at length about tariffs on steel and solar panels."
    r = copies.metrics("\n".join(rew), ORIGINAL, TITLE, TITLE, "en", "USA")
    assert r["rewritten_share"] > 0 and r["added_paragraphs"] == 0 and r["band"] in ("light", "moderate")
    assert copies.metrics("Too short.", ORIGINAL) is None


def test_normalisation_drops_credits_datelines_menus_and_template_lines():
    text = "\n".join(["English", "Français", "BEIJING, Oct. 13 (Xinhua) -- " + PARAS[0], "(Photo by Li Ming/Xinhua)",
                      "Source: Xinhua", PARAS[1], "■", "Follow our channel on WhatsApp", PARAS[2], PARAS[3],
                      "Related Stories", "Another headline"])
    out = copies.normalise(text, {"follow our channel on whatsapp"})
    assert out == [PARAS[0], PARAS[1], PARAS[2], PARAS[3]]
    bodies = ["Line A\nshared footer"] * 3 + ["Line B\nshared footer", "Line C"] * 1
    assert copies.template_lines_of(bodies) == {"shared footer", "line a"}
    assert copies.template_lines_of(bodies[:3]) == set()
    # One story run five times is not a template.
    assert copies.template_lines_of([("same story", "Line A\nshared footer")] * 5) == set()


@pytest.mark.parametrize("pub,res", [("2026-10-12T08:15:30+00:00", "second"), ("2026-10-12T08:15:00+00:00", "minute"),
                                     ("2026-10-12T08:00:00+00:00", "hour"), ("2026-10-12T00:00:00+08:00", "date"),
                                     ("2026-10-12", "date"), ("", "none"), ("2026-10-20T08:00:00+00:00", "none")])
def test_time_resolution(pub, res, monkeypatch):
    monkeypatch.setattr(config, "COLLECTION_START", "2000-01-01")
    assert copies.ts_resolution({"published_at": pub, "discovered_at": "2026-10-13T00:00:00+00:00"}) == res


def test_bands():
    assert [copies.band_of(x) for x in (0, 0.0299, 0.03, 0.1499, 0.15, 0.3999, 0.4, 1)] == [
        "verbatim", "verbatim", "light", "light", "moderate", "moderate", "substantial", "substantial"]
    assert copies.band_of(None) is None


def test_credits():
    assert copies.credits_of(["xinhua_dateline", "cgtn_credit"]) == {"xinhua", "cgtn"}
    assert copies.credits_of([], ["People's Daily", "Ministry"]) == {"peoples_daily"}
    assert copies.credits_of([], [], json.dumps({"triggers": ["cites_global_times"]})) == {"global_times"}
    assert copies.credits_of(["wire_stamp_alone"]) == set()
    assert copies.credit_consistent({"cmg"}, [], "cgtn")
    assert not copies.credit_consistent({"xinhua"}, [], "cgtn")
    assert copies.credit_consistent(set(), ["source_xinhua"], None, ["source_xinhua"])
    # A source that carries another publisher's copy (People's Daily English runs Xinhua) is consistent with it.
    assert copies.credit_consistent({"xinhua"}, [], "peoples_daily", ["peoples_daily_", "xinhua_"])
    assert not copies.credit_consistent({"xinhua"}, [], "peoples_daily", ["peoples_daily_"])
    assert copies.credits_of(["china_news_service_credit"]) == {"china_news_service"}
    # A press release headed by its state issuer names that publisher; Xinhua Silk Road is not Xinhua's newsroom.
    assert copies.credits_of(["wire_release_headline_issuer"], title="CGTN: How China uses digital tech") == {"cgtn"}
    assert copies.credits_of([], title="People's Daily: a report") == {"peoples_daily"}
    assert copies.credits_of([], title="Xinhua Silk Road: Tea expo opens") == set()
    assert copies.credits_of([], title="Xinhua reports growth") == set()


def test_the_directly_credited_publisher_wins_over_a_source_that_carries_its_copy(conn):
    sources = dict(SOURCES, ref_peoples_daily_en={"publisher": "peoples_daily", "language": "en",
                                                  "credits": ["peoples_daily_", "xinhua_"]})
    pd = _reference(conn, "https://en.people.cn/1", "ref_peoples_daily_en", TITLE, ORIGINAL, hours_ago=4)
    aid = _article(conn, "https://paper.example/a", "us_paper", TITLE, ORIGINAL, hours_ago=1, sigs=["xinhua_dateline"])
    _run(conn, sources)
    assert _row(conn, aid)["ref_id"] == pd
    xh = _reference(conn, "https://english.news.cn/1", "ref_xinhua_en", TITLE, ORIGINAL, hours_ago=3)
    _run(conn, sources)
    assert _row(conn, aid)["ref_id"] == xh and _row(conn, aid)["original_source"] == "ref_xinhua_en"


# ---------------------------------------------------------------------------
# Export, review file, sample and hand check
# ---------------------------------------------------------------------------

def _scored_corpus(conn, n=6):
    _reference(conn, "https://english.news.cn/1", "ref_xinhua_en", TITLE, ORIGINAL, hours_ago=30)
    ids = []
    for i in range(n):
        extra = "" if i % 2 else "\n" + ("The minister of trade in Washington told reporters that the agreement would be "
                                         "studied closely by the administration, which has its own concerns. ") * 7
        ids.append(_article(conn, "https://paper.example/%d" % i, "us_wire" if i == 0 else "us_paper", TITLE,
                            ORIGINAL + extra, hours_ago=2 + i, sigs=["xinhua_dateline"]))
    return ids


def test_export_shape_and_no_body_text(conn):
    _scored_corpus(conn)
    _run(conn)
    outlets = [{"id": "us_paper", "name": "US Paper", "country": "USA", "tier": "national"},
               {"id": "us_wire", "name": "PR Wire", "country": "USA", "tier": "distribution_wire"}]
    ctx = {"export_dir": conn_dir(conn), "outlets": outlets}
    exp = copies.build_export(conn, ctx)
    for k in ("generated_at", "version", "bands", "band_limits", "totals", "coverage", "by_route", "by_country",
              "by_outlet", "by_source", "by_language", "recall"):
        assert k in exp
    assert exp["bands"] == ["verbatim", "light", "moderate", "substantial"]
    assert exp["coverage"]["reference"] == 6 and exp["coverage"]["scored"] == 6
    assert {g["id"] for g in exp["by_country"]} == {"USA", "WLD"}
    # Six copies of one original are one original and one underlying item: no outlet is ranked on it,
    # no group median is given, and the item weighted shares count the item once.
    t = exp["totals"]
    assert t["scored"] == 6 and t["originals"] == 1 and t["items"] == 1 and exp["by_outlet"] == []
    assert t["item_shares"] == {"verbatim": 0.5, "light": 0.0, "moderate": 0.0, "substantial": 0.5}
    assert t["scored_reference"] == 6 and t["scored_monitored"] == 0 and t["against"]["reference"]["items"] == 1
    assert all(g["median_novel"] is None and g["originals"] == 1 for g in exp["by_route"])
    assert exp["by_source"][0]["id"] == "ref_xinhua_en"
    assert exp["by_route"][0]["id"] == "wire_credit" and exp["by_route"][0]["label"]
    assert sum(exp["by_route"][0]["bands"].values()) == 6
    rec = exp["recall"][0]
    assert rec["source"] == "ref_xinhua_en" and rec["eligible"] == 6 and rec["recall"] == 1.0
    dumped = json.dumps(exp)
    assert PARAS[0][:40] not in dumped and "https://paper.example" not in dumped and TITLE not in dumped
    review = conn_dir(conn).parent / "audit" / "alteration_review.csv"
    rows = list(csv.DictReader(open(review, encoding="utf-8")))
    assert len(rows) == exp["totals"]["bands"]["substantial"] == 3
    assert rows[0]["headline"] == TITLE and rows[0]["original_url"] == "https://english.news.cn/1"
    assert all(PARAS[0][:40] not in v for r in rows for v in r.values() if v)
    s = copies.meta_summary(conn, ctx)
    assert s["copies"] == 6 and s["reference"] == 6 and s["scored"] == 6
    assert s["scored_reference"] == 6 and s["originals"] == 1 and s["items"] == 1
    assert s["reference_sources"]["ref_xinhua_en"]["fetched"] == 1 and "reference_sources" in exp


def test_the_review_file_never_lands_in_the_repository_from_another_export(tmp_path):
    assert copies.review_path({"export_dir": tmp_path / "p" / "data"}) == tmp_path / "p" / "audit" / "alteration_review.csv"
    assert copies.review_path({"export_dir": config.EXPORT_DIR}) == config.EXPORT_DIR_AUDIT / "alteration_review.csv"


def test_draw_sample_is_blind_and_score_check_scores_it(conn, tmp_path):
    _scored_corpus(conn)
    _run(conn)
    csv_path, key_path = copies.draw_sample(conn, 8, "s1", tmp_path / "reviews")
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    key = json.load(open(key_path))
    assert key["measure_id"] == "alteration" and key["measure_version"] == copies.VERSION and key["per_band"] == 2
    assert set(rows[0]) == set(copies.SAMPLE_FIELDS) and "band" not in rows[0] and "novel_share" not in rows[0]
    assert len(rows) == len(key["pairs"]) == 4   # two scored bands with two pairs each
    check = copies.score_check(rows, key)
    assert check["complete"] is False and check["passed"] is None and check["coded"] == 0
    for r in rows:
        r["same_text"], r["coder_band"] = "yes", key["pairs"][r["sample_id"]]["band"]
    check = copies.score_check(rows, key)
    assert check["complete"] and check["passed"] and check["value"] == 1.0 and check["pair_precision"] == 1.0
    # Through the gate, as the export reads it.
    status, chk, _ = measures.assess(copies, "alteration", tmp_path / "reviews")
    assert status == "provisional" and chk["state"] == "incomplete"


def test_score_check_maths():
    bands = copies.BANDS
    key = {"pairs": {"P%d" % i: {"band": b} for i, b in enumerate(bands * 5)}}
    rows = [{"sample_id": "P%d" % i, "same_text": "yes", "coder_band": b} for i, b in enumerate(bands * 5)]
    assert copies.score_check(rows, key)["value"] == 1.0
    # One band step off everywhere: quadratic weights keep kappa high.
    shift = {"verbatim": "light", "light": "moderate", "moderate": "substantial", "substantial": "moderate"}
    off = [dict(r, coder_band=shift[r["coder_band"]]) if i % 4 == 0 else r for i, r in enumerate(rows)]
    k = copies.score_check(off, key)
    assert 0.6 < k["kappa"] < 1 and k["passed"] is True
    # Opposite ends fail.
    flip = {"verbatim": "substantial", "light": "moderate", "moderate": "light", "substantial": "verbatim"}
    bad = copies.score_check([dict(r, coder_band=flip[r["coder_band"]]) for r in rows], key)
    assert bad["kappa"] == -1.0 and bad["passed"] is False and bad["statistic"] == "quadratic weighted kappa"
    # Three wrong pairs in twenty: precision 0.85 fails whatever the kappa.
    wrong = [dict(r, same_text="no", coder_band="") if i < 3 else r for i, r in enumerate(rows)]
    w = copies.score_check(wrong, key)
    assert w["statistic"] == "pair precision" and w["value"] == 0.85 and w["passed"] is False and w["complete"]
    # A known value: two raters on 0..3.
    assert copies.quadratic_weighted_kappa([0, 1, 2, 3, 0, 1], [0, 1, 2, 3, 1, 1]) == pytest.approx(1 - 6 / 74.0, abs=1e-4)
    assert copies.quadratic_weighted_kappa([], []) is None
    assert copies.quadratic_weighted_kappa([1, 1], [1, 1]) == 1.0


def test_methodology_text_is_plain():
    t = copies.methodology_text({"status": "provisional", "summary": {"copies": 10, "reference": 2, "monitored": 3, "scored": 4,
                                                                    "scored_reference": 0, "scored_monitored": 4,
                                                                    "originals": 2, "items": 2}})
    # No reference pair scored: the text never says the copies were set beside the state text.
    assert "beside the original state text" not in t and "No copy has yet been scored against the state outlet's own text" in t
    assert "4 had both texts available to score: 0 against a reference original and 4 against an earlier monitored copy" in t
    assert "2 distinct originals and 2 distinct underlying items" in t and "media group" in t
    assert "without substantive editorial alteration" in t and "report only" in t
    assert "\u2013" not in t and "\u2014" not in t and " - " not in t and "--" not in t
    assert "Category" not in t and "verbatim" in t and "substantial" in t
    assert t.count("\n\n") >= 3


def test_the_module_offers_the_whole_interface(conn):
    for f in measures.REQUIRED:
        assert callable(getattr(copies, f))
    assert copies.MEASURE_ID == "alteration"
    assert copies.inputs_present(conn) == 0
    spec = next(m for m in measures.load_config() if m["id"] == "alteration")
    assert spec["module"] == "pipeline.copies" and spec["enabled"]


# ---------------------------------------------------------------------------
# Networks, distinct originals and items, cleaners (F6a)
# ---------------------------------------------------------------------------

NETWORK_OUTLETS = [{"id": "au_newcastleherald", "name": "Newcastle Herald", "country": "AUS", "network": "acm"},
                   {"id": "au_canberratimes", "name": "The Canberra Times", "country": "AUS", "network": "acm"},
                   {"id": "au_illawarramercury", "name": "Illawarra Mercury", "country": "AUS", "network": "acm"},
                   {"id": "us_paper", "name": "US Paper", "country": "USA"}]


def test_an_earlier_copy_at_a_sister_outlet_of_the_same_network_is_never_the_original(conn):
    first = _article(conn, "https://newcastleherald.example/1", "au_newcastleherald", TITLE, ORIGINAL, hours_ago=5,
                     country="AUS", sigs=["xinhua_dateline"])
    conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (first, first))
    sister = _article(conn, "https://canberratimes.example/1", "au_canberratimes", TITLE, ORIGINAL, hours_ago=3,
                      country="AUS", sigs=["xinhua_dateline"], dup_group=first)
    other = _article(conn, "https://paper.example/1", "us_paper", TITLE, ORIGINAL, hours_ago=1, sigs=["xinhua_dateline"],
                     dup_group=first)
    conn.commit()
    ctx = {"outlets": NETWORK_OUTLETS, "export_dir": conn_dir(conn)}
    copies.compute(conn, ctx, None, sources=SOURCES)
    a, b, c = _row(conn, first), _row(conn, sister), _row(conn, other)
    assert a["original_kind"] == "none"
    assert b["original_kind"] == "none" and json.loads(b["details"])["sister_only"] == 1
    assert c["original_kind"] == "monitored" and c["original_article_id"] == first and c["band"] == "verbatim"
    exp = copies.build_export(conn, ctx)
    assert exp["coverage"]["sister_only"] == 1 and exp["coverage"]["monitored"] == 1
    assert exp["totals"]["scored_monitored"] == 1 and exp["totals"]["scored_reference"] == 0
    # Without the network field the sister's earlier copy would have been taken (recent copies are paired again).
    copies.compute(conn, {"outlets": [dict(o, network=None) for o in NETWORK_OUTLETS]}, None, sources=SOURCES)
    assert _row(conn, sister)["original_kind"] == "monitored" and _row(conn, sister)["original_article_id"] == first


def test_network_map_reads_the_registry_and_the_context():
    assert copies.network_map({"outlets": NETWORK_OUTLETS}) == {"au_newcastleherald": "acm", "au_canberratimes": "acm",
                                                               "au_illawarramercury": "acm"}
    reg = copies.network_map(None)
    assert reg["au_canberratimes"] == reg["au_bendigoadvertiser"] == "acm"
    assert reg["ca_nationalpost"] == reg["ca_torontosun"] == "postmedia" and reg["ca_torontostar"] == "torstar"
    assert reg["au_smh"] == "nine" and reg["au_heraldsun"] == "news_corp"
    assert copies.same_network(reg, "au_smh", "au_theage") and not copies.same_network(reg, "au_smh", "au_canberratimes")
    assert not copies.same_network(reg, "it_corriere", "it_repubblica")      # no network recorded: never the same


def test_distinct_originals_and_items_in_the_export(conn):
    # Two state texts: one run by four outlets (one original, one item), one by two.
    outlets = [{"id": "o%d" % i, "name": "Outlet %d" % i, "country": "USA"} for i in range(6)]
    texts = [(TITLE, ORIGINAL), ("Beijing hosts forum on rural revitalisation and green farming in the west",
                                  "\n".join(p.replace("China", "The province") for p in PARAS))]
    ids = []
    for t, (title, body) in enumerate(texts):
        first = None
        for j in range(4 if t == 0 else 2):
            aid = _article(conn, "https://o%d.example/%d" % (j + 2 * t, t), "o%d" % (j + 2 * t), title, body,
                           hours_ago=10 - j, sigs=["xinhua_dateline"], dup_group=first)
            if first is None:
                first = aid
                conn.execute("UPDATE articles SET dup_group_id=? WHERE id=?", (aid, aid))
            ids.append(aid)
    conn.commit()
    ctx = {"outlets": outlets, "export_dir": conn_dir(conn)}
    copies.compute(conn, ctx, None, sources=SOURCES)
    exp = copies.build_export(conn, ctx)
    t = exp["totals"]
    assert t["scored"] == 4 and t["originals"] == 2 and t["items"] == 2
    assert exp["coverage"]["items"] == 2 and exp["coverage"]["originals"] == 2 and exp["coverage"]["paired_items"] == 2
    assert t["item_shares"]["verbatim"] == 1.0 and t["item_bands"]["verbatim"] == 2.0
    assert t["against"]["monitored"]["scored"] == 4 and t["against"]["reference"]["scored"] == 0
    rows = copies.attach_items(conn, [dict(r) for r in conn.execute("SELECT s.*, a.dup_group_id FROM state_copies s JOIN articles a ON a.id = s.copy_id")])
    assert len({r["item_key"] for r in rows}) == 2 and {r["orig_key"] for r in rows if r["orig_key"]} == {"a%d" % ids[0], "a%d" % ids[4]}


def test_item_summary_weighs_each_item_once():
    rows = [{"item_key": "x", "orig_key": "a1", "band": "verbatim", "novel_share": 0.0, "kept_share": 1.0, "copy_id": i}
            for i in range(9)] + [{"item_key": "y", "orig_key": "a2", "band": "substantial", "novel_share": 0.6,
                                   "kept_share": 0.5, "copy_id": 10}]
    s = copies.item_summary(rows)
    assert s["scored"] == 10 and s["items"] == 2 and s["originals"] == 2
    assert s["bands"]["verbatim"] == 9 and s["item_shares"] == {"verbatim": 0.5, "light": 0.0, "moderate": 0.0, "substantial": 0.5}
    assert s["median_novel_items"] == 0.3
    assert copies.item_summary([])["item_shares"]["verbatim"] is None


@pytest.mark.parametrize("line,out", [
    ("BEIJING, Oct. 13 (Xinhua) -- China and the EU agreed", "China and the EU agreed"),
    ("ROMA (ITALPRESS) - Il testo", "Il testo"),
    ("Jakarta (ANTARA) - Indonesia said", "Indonesia said"),
    ("NEW YORK (AP) \u2014 The talks", "The talks"),
    ("Roma, 13 ott. (Adnkronos) - Il governo", "Il governo"),
    # Text that only looks like a dateline is kept: words in brackets, lower case, a sentence before them.
    ("The minister (who spoke) - and others said", "The minister (who spoke) - and others said"),
    ("Officials said on Monday in the capital of the province (ap) - that", "Officials said on Monday in the capital of the province (ap) - that"),
    ("He said (in Beijing) - that trade would grow", "He said (in Beijing) - that trade would grow"),
])
def test_the_dateline_rule_is_case_sensitive_and_short(line, out):
    assert copies.normalise(line + " " + "word " * 12)[0] == (out + " " + "word " * 12).strip()
    # A short opening paragraph with a dateline is text, not a credit line.
    assert copies.normalise(line)[0] == out


@pytest.mark.parametrize("line,credit", [
    ("(Photo by Li Ming/Xinhua)", True), ("Source: Xinhua", True), ("Photo: AP", True), ("AFP", True), ("(ANSA)", True),
    ("Xinhua/Li Ming", True), ("\u00a9 Xinhua", True), ("Fonte: Agenzia Nova", True), ("By Xinhua", True), ("fsc/abr", True),
    ("Asked by AP, she said no.", False), ("ap", False), ("The agi of nova", False), ("CNA reporters said so.", False),
    ("By the way the AP is great", False), ("Reporting by AP showed that the island ports were closed today.", False),
    ("Apa kata Xinhua?", False), ("The cna of the matter", False),
])
def test_credit_lines_need_an_agency_in_a_credits_place(line, credit):
    assert copies.is_credit_line(line) is credit
