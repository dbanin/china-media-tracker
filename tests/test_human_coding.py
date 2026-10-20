"""The hand coding study that decides whether unchecked state sourcing is published.

An outside review (12 October 2026) found that a same model rerun measures stability, not
correctness, yet a passing rerun replaced "provisional" with published counts. Publication now
rests on hand coding alone: a fixed sample, about 200 articles per qualifying language, stratified
by language and within it by the model's category, coded blind by the owner and two research
assistants, with kappa at or above config.KAPPA_WARNING_THRESHOLD overall and per language."""
import csv
import json

from pipeline import agreement, classify_llm, config, export, store


def _db(tmp_path, monkeypatch, sizes, cats=("A", "B", "C", "not_relevant")):
    """A database whose model labelled articles number sizes[lang] per language, categories in rotation."""
    monkeypatch.setattr(agreement, "REVIEWS_DIR", tmp_path / "reviews")
    conn = store.connect(tmp_path / "h.db")
    n = 0
    for lang, size in sizes.items():
        for i in range(size):
            n += 1
            aid = store.insert_discovered(conn, {"url": "https://x.test/%s/%d" % (lang, i), "outlet_id": "o_" + lang,
                                                 "country": "ITA", "language": lang, "title": "t%d" % n,
                                                 "status": "classified", "gate_relevant": 1})
            conn.execute("UPDATE articles SET body_chars=900 WHERE id=?", (aid,))
            # Made under the current classifier prompt: compute compares only with those (since 17 October 2026).
            store.insert_classification(conn, aid, "llm", cats[i % len(cats)], 0.8,
                                        model_version=classify_llm.with_prompt_version("claude-sonnet-5"))
    conn.commit()
    return conn


def _meta(conn):
    return export.build_meta(conn, [], [], export.build_latest(conn, [], [], population={}))


def test_sample_is_fixed_stratified_by_language_and_category(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 80, "es": 40, "fr": 12})
    rows = agreement.human_pool(conn)
    assert agreement.qualifying_languages(rows, 30) == {"en": 80, "es": 40}, "fr is below the minimum"
    picked = agreement.human_sample(rows, per_language=20, min_labelled=30)
    assert len(picked) == 40
    by_lang = {}
    for r in picked:
        by_lang.setdefault(r["language"], []).append(r["category"])
    assert set(by_lang) == {"en", "es"} and all(len(v) == 20 for v in by_lang.values())
    # Equal allocation across the model's categories within each language.
    assert all(v.count(c) == 5 for v in by_lang.values() for c in ("A", "B", "C", "not_relevant"))
    # Seeded: the same database gives the same sample.
    again = agreement.human_sample(agreement.human_pool(conn), per_language=20, min_labelled=30)
    assert [r["id"] for r in again] == [r["id"] for r in picked]


def test_sample_excludes_rule_labels_and_articles_without_a_body(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 10})
    aid = store.insert_discovered(conn, {"url": "https://x.test/rule", "outlet_id": "o", "country": "ITA", "language": "en",
                                         "title": "r", "status": "classified", "gate_relevant": 1})
    conn.execute("UPDATE articles SET body_chars=900 WHERE id=?", (aid,))
    store.insert_classification(conn, aid, "rules", "A", 1.0)
    bodiless = store.insert_discovered(conn, {"url": "https://x.test/nobody", "outlet_id": "o", "country": "ITA", "language": "en",
                                              "title": "n", "status": "classified", "gate_relevant": 1})
    store.insert_classification(conn, bodiless, "llm", "B", 0.8)
    conn.commit()
    ids = {r["id"] for r in agreement.human_pool(conn)}
    assert len(ids) == 10 and aid not in ids and bodiless not in ids


def test_sample_csv_is_blind(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 40})
    out = tmp_path / "reviews" / "human_sample_2026-10-12.csv"
    res = agreement.cmd_human_sample(conn, out, per_language=16, min_labelled=30)
    assert res["n"] == 16 and res["languages"] == {"en": {"pool": 40, "sampled": 16}}
    with open(out, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == agreement.HUMAN_SAMPLE_COLUMNS
    assert {"article_id", "url", "outlet", "language", "title", "human_category", "coder", "note"} <= set(rows[0])
    assert all(r["human_category"] == "" and r["coder"] == "" for r in rows)
    text = out.read_text(encoding="utf-8") + out.with_suffix(".key.json").read_text(encoding="utf-8")
    assert "not_relevant" not in text and "category" not in json.loads(out.with_suffix(".key.json").read_text())["items"][0]
    info = agreement.latest_sample_info()
    assert info["file"] == out.name and info["languages"] == {"en": 16}


def _fill(conn, path, decide, skip=()):
    """Fill the sample CSV, answering each row with decide(index, row, model_category)."""
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for i, r in enumerate(rows):
        if i in skip:
            continue
        cat = conn.execute("SELECT category FROM classifications WHERE article_id=? AND is_current=1", (int(r["article_id"]),)).fetchone()[0]
        r["human_category"] = decide(i, r, cat)
        r["coder"] = "ra%d" % (i % 3)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=agreement.HUMAN_SAMPLE_COLUMNS)
        w.writeheader()
        w.writerows(rows)


WORDS = {"A": "state_origin", "B": "unchecked_state_sourcing", "C": "independent_journalism", "not_relevant": "not_about_china"}


def test_compute_reads_coder_words_records_per_language_and_publishes(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 60, "es": 60})
    out = tmp_path / "reviews" / "human_sample_2026-10-12.csv"
    agreement.cmd_human_sample(conn, out, per_language=48, min_labelled=30)
    meta = _meta(conn)
    assert meta["relay_provisional"] is True and meta["relay_publishable"] is False
    assert meta["relay_human_study"]["state"] == "sample_drawn" and "being coded" in meta["relay_provisional_reason"]

    # Coded but not finished: not recorded unless asked, and an interim study publishes nothing.
    _fill(conn, out, lambda i, r, cat: WORDS[cat], skip={0})
    res = agreement.cmd_compute(conn, out, "hand_coder")
    assert res["complete"] is False
    assert conn.execute("SELECT COUNT(*) FROM agreement_studies").fetchone()[0] == 0
    agreement.cmd_compute(conn, out, "hand_coder", partial=True)
    meta = _meta(conn)
    assert meta["relay_human_study"]["state"] == "incomplete" and meta["relay_publishable"] is False
    assert meta["relay_provisional"] is True and "95 of 96" in meta["relay_provisional_reason"]

    # Finished, in agreement: published, on hand coding, with the per language results stored.
    _fill(conn, out, lambda i, r, cat: WORDS[cat])
    res = agreement.cmd_compute(conn, out, "hand_coder")
    assert res["complete"] is True and res["kappa_bc"] == 1.0
    details = json.loads(conn.execute("SELECT details FROM agreement_studies ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert set(details["bc_by_language"]) == {"en", "es"} and details["sample_complete"] is True
    assert details["coders"] == ["ra0", "ra1", "ra2"] and details["method"] == "human_coding"
    assert conn.execute("SELECT COUNT(DISTINCT reviewer) FROM human_reviews WHERE reviewer LIKE 'ra%'").fetchone()[0] == 3
    meta = _meta(conn)
    assert meta["relay_publishable"] is True and meta["relay_provisional"] is False
    assert meta["relay_basis"] == "human_coding" and "stratified by language" in meta["relay_qualifier"]
    assert meta["relay_human_study"]["passed"] is True and export.relay_visible(conn) is True


def test_a_language_the_coders_read_differently_is_withheld(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 80, "es": 80}, cats=("B", "C"))
    out = tmp_path / "reviews" / "human_sample_2026-10-12.csv"
    agreement.cmd_human_sample(conn, out, per_language=60, min_labelled=30)
    # English agrees; in Spanish the coders reverse a quarter of the model's calls.
    _fill(conn, out, lambda i, r, cat: WORDS[cat] if r["language"] == "en" or i % 4 else WORDS["C" if cat == "B" else "B"])
    agreement.cmd_compute(conn, out, "hand_coder")
    meta = _meta(conn)
    by_lang = meta["kappa"]["bc_by_language"]
    assert by_lang["en"]["kappa"] == 1.0 and by_lang["es"]["kappa"] < config.KAPPA_WARNING_THRESHOLD
    assert meta["kappa"]["bc"] >= config.KAPPA_WARNING_THRESHOLD and meta["relay_publishable"] is True
    assert meta["relay_withheld_languages"] == ["es"]


def test_failed_human_study_withholds(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 80}, cats=("B", "C"))
    out = tmp_path / "reviews" / "human_sample_2026-10-12.csv"
    agreement.cmd_human_sample(conn, out, per_language=60, min_labelled=30)
    _fill(conn, out, lambda i, r, cat: WORDS[cat] if i % 2 else WORDS["C" if cat == "B" else "B"])
    agreement.cmd_compute(conn, out, "hand_coder")
    meta = _meta(conn)
    assert meta["relay_human_study"]["state"] == "failed"
    assert meta["relay_publishable"] is False and meta["relay_provisional"] is False
    assert export.relay_visible(conn) is False


def test_passing_same_model_rerun_leaves_counts_provisional(tmp_path, monkeypatch):
    """The review's case: a rerun at the same prompt agreeing with itself used to publish."""
    conn = _db(tmp_path, monkeypatch, {"en": 10})
    rerun = {"method": "same_model_rerun", "model_a": "claude-sonnet-5", "model_b": "claude-sonnet-5",
             "sample_size": 225, "kappa_all": 0.81, "kappa_bc": 0.62, "n_bc": 116, "details": {}}
    store.record_model_agreement(conn, rerun, [])
    meta = _meta(conn)
    assert meta["relay_publishable"] is False and meta["relay_basis"] is None
    assert meta["relay_provisional"] is True and meta["relay_human_study"]["state"] == "not_started"
    assert meta["relay_reliability"]["method"] == "same_model_rerun" and meta["relay_reliability"]["kappa_bc"] == 0.62
    reason = meta["relay_provisional_reason"]
    assert "0.6" in reason and "about 200 articles in each language" in reason and "model's current category" in reason


def test_inconclusive_human_study_stays_provisional(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 10})
    conn.execute("INSERT INTO agreement_studies(computed_at, sample_size, kappa_all, kappa_bc, n_bc, details) VALUES (?,?,?,?,?,?)",
                 ("2026-10-20T00:00:00+00:00", 200, 0.8, None, 30,
                  json.dumps({"sample_complete": True, "kappa_bc_inconclusive": "3 of 30 pairs carry B"})))
    conn.commit()
    meta = _meta(conn)
    assert meta["relay_study_inconclusive"] is True and meta["relay_provisional"] is True
    assert "3 of 30 pairs carry B" in meta["relay_provisional_reason"]


def test_compute_against_current_label(tmp_path, monkeypatch):
    """compute compares with each article's current label by default; the frozen labels are the audit view."""
    conn = _db(tmp_path, monkeypatch, {"en": 40}, cats=("B", "C"))
    out = tmp_path / "reviews" / "human_sample_2026-10-12.csv"
    agreement.cmd_human_sample(conn, out, per_language=30, min_labelled=30)
    _fill(conn, out, lambda i, r, cat: WORDS[cat])
    # Reclassification under the current prompt flips every label; the frozen comparison still agrees,
    # the current one, the default, does not.
    for (aid, cat) in conn.execute("SELECT article_id, category FROM classifications WHERE is_current=1").fetchall():
        store.insert_classification(conn, aid, "llm", "C" if cat == "B" else "B", 0.8,
                                    model_version=classify_llm.with_prompt_version("claude-sonnet-5"))
    conn.commit()
    assert agreement.cmd_compute(conn, out, "x", record=False, against="frozen")["kappa_bc"] == 1.0
    assert agreement.cmd_compute(conn, out, "x", record=False)["kappa_bc"] == -1.0


def test_coder_category_accepts_words_and_codes():
    assert agreement.coder_category("Unchecked state sourcing") == "B"
    assert agreement.coder_category("not-about-china") == "not_relevant"
    assert agreement.coder_category("c") == "C" and agreement.coder_category("") is None
    assert agreement.coder_category("maybe") is None
