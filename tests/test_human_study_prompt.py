"""The hand coding study checks the labels the site counts (17 October 2026).

The 12 October sample was drawn when every label in it was on an older classifier prompt, and
compute compared the coders with the label frozen at sample time, so a passing study would have
certified labels being replaced. compute now compares with each article's current model label under
the current prompt and counts what it leaves out; sample --since-prompt redraws from current prompt
labels on the runner's snapshot; a sample can be marked superseded; and a study where the model
called unchecked state sourcing at least ten times and the coders fewer is failed, not inconclusive,
overall and per language."""
import csv
import json
from pathlib import Path

import pytest

from pipeline import agreement, classify_llm, config, export, store

WORDS = {"A": "state_origin", "B": "unchecked_state_sourcing", "C": "independent_journalism", "not_relevant": "not_about_china"}


def _tagged(prompt=None):
    return classify_llm.with_prompt_version("claude-sonnet-5", prompt or classify_llm.PROMPT_VERSION)


def _db(tmp_path, monkeypatch, sizes, cats=("B", "C"), model_version=None):
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
            store.insert_classification(conn, aid, "llm", cats[i % len(cats)], 0.8,
                                        model_version=model_version or _tagged())
    conn.commit()
    return conn


def _fill(conn, path, decide, coder=lambda i: "c%d" % (i % 3 + 1)):
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for i, r in enumerate(rows):
        cat = conn.execute("SELECT category FROM classifications WHERE article_id=? AND is_current=1",
                           (int(r["article_id"]),)).fetchone()[0]
        r["human_category"] = decide(i, r, cat)
        r["coder"] = coder(i)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=agreement.HUMAN_SAMPLE_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def _meta(conn):
    return export.build_meta(conn, [], [], export.build_latest(conn, [], [], population={}))


def _latest_details(conn):
    return json.loads(conn.execute("SELECT details FROM agreement_studies ORDER BY id DESC LIMIT 1").fetchone()[0])


# ---------------------------------------------------------------------------
# compute compares with the current label under the current prompt
# ---------------------------------------------------------------------------

def test_compute_leaves_out_labels_on_an_older_prompt_and_counts_them(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 60})
    out = tmp_path / "reviews" / "human_sample_2026-10-17.csv"
    agreement.cmd_human_sample(conn, out, per_language=40, min_labelled=30)
    _fill(conn, out, lambda i, r, cat: WORDS[cat])
    key = json.loads(out.with_suffix(".key.json").read_text())
    assert key["prompt_version"] == classify_llm.PROMPT_VERSION
    # Five sampled articles are still on an older prompt (an untagged label), and one is now a rule label.
    ids = [it["article_id"] for it in key["items"]]
    for aid in ids[:5]:
        store.insert_classification(conn, aid, "llm", "B", 0.8, model_version="claude-sonnet-5 (session)")
    store.insert_classification(conn, ids[5], "rules", "A", 1.0)
    conn.commit()
    res = agreement.cmd_compute(conn, out, "x")
    assert res["excluded_older_prompt"] == 5 and res["excluded_no_model_label"] == 1
    assert res["n"] == 34 and res["complete"] is True, "a coded row left out still counts as coded"
    d = _latest_details(conn)
    assert d["compared_against"] == "current_label" and d["prompt_version"] == classify_llm.PROMPT_VERSION
    assert d["excluded_older_prompt"] == 5 and d["excluded_no_model_label"] == 1
    # The site says which labels the kappa rests on, under which prompt, and how many were left out.
    hs = _meta(conn)["relay_human_study"]
    assert hs["state"] == "passed" and hs["compared_against"] == "current_label"
    assert hs["prompt_version"] == classify_llm.PROMPT_VERSION and hs["current_prompt_version"] == classify_llm.PROMPT_VERSION
    assert hs["excluded_older_prompt"] == 5 and hs["excluded_no_model_label"] == 1
    assert "current when the study was computed" in hs["labels_compared"]
    assert classify_llm.PROMPT_VERSION in hs["labels_compared"] and "older prompt" in hs["labels_compared"]
    # The human reviews point at the label actually compared.
    cid = conn.execute("SELECT classification_id FROM human_reviews WHERE article_id=?", (ids[10],)).fetchone()[0]
    assert cid == conn.execute("SELECT id FROM classifications WHERE article_id=? AND is_current=1", (ids[10],)).fetchone()[0]


def test_frozen_comparison_is_the_audit_view_and_records_nothing(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 40})
    out = tmp_path / "reviews" / "human_sample_2026-10-17.csv"
    agreement.cmd_human_sample(conn, out, per_language=30, min_labelled=30)
    _fill(conn, out, lambda i, r, cat: WORDS[cat])
    res = agreement.cmd_compute(conn, out, "x", against="frozen")
    assert res["kappa_bc"] == 1.0 and res["compared_against"] == "frozen"
    assert conn.execute("SELECT COUNT(*) FROM agreement_studies").fetchone()[0] == 0


def test_the_old_sample_compares_nothing_while_its_labels_are_on_older_prompts(tmp_path, monkeypatch):
    """The 12 October case: every label sampled was untagged, so nothing is compared until relabelled."""
    conn = _db(tmp_path, monkeypatch, {"en": 40}, model_version="claude-sonnet-5")
    out = tmp_path / "reviews" / "human_sample_2026-10-12.csv"
    agreement.cmd_human_sample(conn, out, per_language=30, min_labelled=30)
    _fill(conn, out, lambda i, r, cat: WORDS[cat])
    res = agreement.cmd_compute(conn, out, "x")
    assert res["n"] == 0 and res["excluded_older_prompt"] == 30
    assert conn.execute("SELECT COUNT(*) FROM agreement_studies").fetchone()[0] == 0


def test_a_study_stops_passing_when_the_prompt_changes(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 40})
    out = tmp_path / "reviews" / "human_sample_2026-10-17.csv"
    agreement.cmd_human_sample(conn, out, per_language=30, min_labelled=30)
    _fill(conn, out, lambda i, r, cat: WORDS[cat])
    agreement.cmd_compute(conn, out, "x")
    assert _meta(conn)["relay_publishable"] is True
    monkeypatch.setattr(classify_llm, "PROMPT_VERSION", "2099-01-01")
    meta = _meta(conn)
    hs = meta["relay_human_study"]
    assert hs["state"] == "stale" and hs["stale"] is True and hs["passed"] is False
    assert meta["relay_publishable"] is False and meta["relay_provisional"] is True and export.relay_visible(conn) is True
    assert "2099-01-01" in meta["relay_provisional_reason"] and "no longer covers" in meta["relay_provisional_reason"]


def test_a_study_recorded_against_frozen_labels_never_passes(tmp_path, monkeypatch):
    """A row compute wrote before 17 October compared frozen labels and carries no prompt version."""
    conn = _db(tmp_path, monkeypatch, {"en": 10})
    conn.execute("INSERT INTO agreement_studies(computed_at, sample_size, kappa_all, kappa_bc, n_bc, details) VALUES (?,?,?,?,?,?)",
                 ("2026-10-15T00:00:00+00:00", 200, 0.8, 0.8, 120,
                  json.dumps({"method": "human_coding", "sample_complete": True, "compared_against": "frozen"})))
    conn.commit()
    hs = _meta(conn)["relay_human_study"]
    assert hs["state"] == "stale" and hs["passed"] is False and "frozen" in hs["labels_compared"]


def test_a_failed_study_still_withholds_after_the_prompt_changes(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 80})
    out = tmp_path / "reviews" / "human_sample_2026-10-17.csv"
    agreement.cmd_human_sample(conn, out, per_language=60, min_labelled=30)
    _fill(conn, out, lambda i, r, cat: WORDS[cat] if i % 2 else WORDS["C" if cat == "B" else "B"])
    agreement.cmd_compute(conn, out, "x")
    monkeypatch.setattr(classify_llm, "PROMPT_VERSION", "2099-01-01")
    hs = _meta(conn)["relay_human_study"]
    assert hs["state"] == "failed" and hs["stale"] is True and export.relay_visible(conn) is False


# ---------------------------------------------------------------------------
# sample --since-prompt
# ---------------------------------------------------------------------------

def test_since_prompt_draws_only_current_prompt_labels(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 60})
    old = [r[0] for r in conn.execute("SELECT article_id FROM classifications WHERE is_current=1 ORDER BY article_id LIMIT 20")]
    for aid in old:
        store.insert_classification(conn, aid, "llm", "B", 0.8, model_version="claude-sonnet-5 (session, prompt 2026-09-01)")
    conn.commit()
    out = tmp_path / "reviews" / "human_sample_2026-10-17.csv"
    res = agreement.cmd_human_sample(conn, out, per_language=30, min_labelled=30, since_prompt=classify_llm.PROMPT_VERSION)
    assert res["n"] == 30 and res["pool_labels_on_older_prompt"] == 20
    key = json.loads(out.with_suffix(".key.json").read_text())
    assert key["since_prompt"] == classify_llm.PROMPT_VERSION and key["prompt_version"] == classify_llm.PROMPT_VERSION
    assert not {it["article_id"] for it in key["items"]} & set(old)
    assert agreement.latest_sample_info()["since_prompt"] == classify_llm.PROMPT_VERSION


def test_since_prompt_refuses_a_stale_database_an_empty_pool_and_an_existing_sample(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 40})
    out = tmp_path / "reviews" / "human_sample_2026-10-17.csv"
    # A local copy: the newest model label is days old (this 12 October snapshot, read on or after the 17th).
    conn.execute("UPDATE classifications SET classified_at='2026-10-12T09:15:51+00:00'")
    conn.commit()
    res = agreement.cmd_human_sample(conn, out, per_language=30, min_labelled=30, since_prompt=classify_llm.PROMPT_VERSION)
    assert res["refused"] == "stale_database" and res["age_days"] > agreement.SAMPLE_MAX_DB_AGE_DAYS and not out.exists()
    monkeypatch.setattr(agreement, "newest_model_label_age_days", lambda c, now=None: 0.2)
    res = agreement.cmd_human_sample(conn, out, per_language=30, min_labelled=30, since_prompt="2099-01-01")
    assert res["refused"] == "empty_pool" and not out.exists()
    assert agreement.cmd_human_sample(conn, out, per_language=30, min_labelled=30)["n"] == 30
    again = agreement.cmd_human_sample(conn, out, per_language=30, min_labelled=30, since_prompt=classify_llm.PROMPT_VERSION)
    assert again["refused"] == "exists", "a sample coders may be filling in is never overwritten"


def test_label_age_reads_the_newest_model_label(tmp_path, monkeypatch):
    import datetime as dt
    conn = _db(tmp_path, monkeypatch, {"en": 3})
    conn.execute("UPDATE classifications SET classified_at='2026-10-12T09:15:51+00:00'")
    conn.commit()
    now = dt.datetime(2026, 10, 17, 9, 15, 51, tzinfo=dt.timezone.utc)
    assert abs(agreement.newest_model_label_age_days(conn, now) - 5.0) < 1e-6
    assert agreement.newest_model_label_age_days(store.connect(tmp_path / "empty.db")) is None


def test_cli_since_prompt_alone_means_the_current_prompt(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(agreement.store, "connect", lambda *a, **k: None)
    monkeypatch.setattr(agreement, "cmd_human_sample",
                        lambda conn, out, *a, **k: seen.update(k, out=out) or {"n": 1})
    agreement.main(["sample", "--since-prompt", "--out", str(tmp_path / "s.csv")])
    assert seen["since_prompt"] == classify_llm.PROMPT_VERSION
    agreement.main(["sample", "--since-prompt", "2026-10-17", "--out", str(tmp_path / "s.csv")])
    assert seen["since_prompt"] == "2026-10-17"
    monkeypatch.setattr(agreement, "cmd_human_sample", lambda conn, out, *a, **k: {"n": 0, "refused": "stale_database"})
    with pytest.raises(SystemExit):
        agreement.main(["sample", "--since-prompt"])


# ---------------------------------------------------------------------------
# A superseded sample
# ---------------------------------------------------------------------------

def _supersede(path):
    key = json.loads(path.read_text())
    key["superseded"] = {"on": "2026-10-17", "reason": "every label it drew was on an older prompt"}
    path.write_text(json.dumps(key, indent=1))


def test_a_superseded_sample_is_not_current_and_is_never_recorded(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 40})
    out = tmp_path / "reviews" / "human_sample_2026-10-12.csv"
    agreement.cmd_human_sample(conn, out, per_language=30, min_labelled=30)
    _supersede(out.with_suffix(".key.json"))
    assert agreement.latest_sample_info() is None
    meta = _meta(conn)
    hs = meta["relay_human_study"]
    assert hs["state"] == "sample_superseded" and hs["sample"] is None
    assert hs["superseded_samples"][0]["file"] == out.name
    reason = meta["relay_provisional_reason"]
    assert "set aside before coding began" in reason and "every label it drew was on an older prompt" in reason
    assert classify_llm.PROMPT_VERSION in reason and meta["relay_provisional"] is True
    _fill(conn, out, lambda i, r, cat: WORDS[cat])
    res = agreement.cmd_compute(conn, out, "x")
    assert res["superseded"] is True and conn.execute("SELECT COUNT(*) FROM agreement_studies").fetchone()[0] == 0
    # A newer sample, drawn later, is the current one.
    new = tmp_path / "reviews" / "human_sample_2026-10-20.csv"
    agreement.cmd_human_sample(conn, new, per_language=30, min_labelled=30)
    assert agreement.latest_sample_info()["file"] == new.name
    assert _meta(conn)["relay_human_study"]["state"] == "sample_drawn"


def test_the_committed_12_october_sample_is_marked_superseded():
    root = Path(__file__).resolve().parents[1] / "data" / "reviews"
    key = json.loads((root / "human_sample_2026-10-12.key.json").read_text(encoding="utf-8"))
    assert key["superseded"]["on"] == "2026-10-17" and "older" in key["superseded"]["reason"]
    assert len(key["items"]) == 1400 and "category" not in key["items"][0], "the key stays blind and whole"
    assert all(s["file"] != "human_sample_2026-10-12.csv" or s.get("superseded_on")
               for s in agreement.superseded_samples(root))
    info = agreement.latest_sample_info(root)
    assert info is None or info["file"] != "human_sample_2026-10-12.csv"
    guide = (root / "CODING_GUIDE.md").read_text(encoding="utf-8")
    assert "Do not start on `human_sample_2026-10-12.csv`" in guide and "--since-prompt" in guide
    assert "pseudonymous coder id" in guide and "your name or initials" not in guide


# ---------------------------------------------------------------------------
# The model calls B often, the coders rarely: failed, overall and per language
# ---------------------------------------------------------------------------

def test_model_b_without_coder_b_is_failed_not_inconclusive():
    m = agreement.MIN_POSITIVE_FOR_KAPPA
    items = ([{"machine_category": "B", "human_category": "C"}] * (m + 2) + [{"machine_category": "B", "human_category": "B"}] * 3
             + [{"machine_category": "C", "human_category": "C"}] * 40)
    res = agreement.compute_from_pairs(items)
    assert res["kappa_bc"] is None and res["kappa_bc_inconclusive"] is None
    assert "the model placed 15 of 55" in res["kappa_bc_failed"] and "coders 3" in res["kappa_bc_failed"]


def test_too_few_b_from_the_model_stays_inconclusive_whatever_the_coders_did():
    m = agreement.MIN_POSITIVE_FOR_KAPPA
    items = ([{"machine_category": "C", "human_category": "B"}] * (m + 2) + [{"machine_category": "B", "human_category": "B"}] * 3
             + [{"machine_category": "C", "human_category": "C"}] * 40)
    res = agreement.compute_from_pairs(items)
    assert res["kappa_bc"] is None and res["kappa_bc_failed"] is None and res["kappa_bc_inconclusive"]


def test_overall_failure_withholds_the_counts(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 60})
    out = tmp_path / "reviews" / "human_sample_2026-10-17.csv"
    agreement.cmd_human_sample(conn, out, per_language=40, min_labelled=30)
    _fill(conn, out, lambda i, r, cat: WORDS["C"])
    res = agreement.cmd_compute(conn, out, "x")
    assert res["kappa_bc"] is None and res["kappa_bc_failed"]
    meta = _meta(conn)
    hs = meta["relay_human_study"]
    assert hs["state"] == "failed" and "fewer than the 10" in hs["failed_reason"]
    assert meta["relay_publishable"] is False and meta["relay_provisional"] is False
    assert meta["relay_study_inconclusive"] is False and export.relay_visible(conn) is False


def test_a_language_where_only_the_model_sees_b_is_withheld_whatever_its_size(tmp_path, monkeypatch):
    # English agrees throughout; in Spanish, 15 articles (10 B by the model), the coders find no B at all.
    conn = _db(tmp_path, monkeypatch, {"en": 80, "es": 15}, cats=("B", "B", "C"))
    out = tmp_path / "reviews" / "human_sample_2026-10-17.csv"
    agreement.cmd_human_sample(conn, out, per_language=80, min_labelled=10)
    _fill(conn, out, lambda i, r, cat: WORDS[cat] if r["language"] == "en" else WORDS["C"])
    agreement.cmd_compute(conn, out, "x")
    meta = _meta(conn)
    es = meta["kappa"]["bc_by_language"]["es"]
    assert es["kappa"] is None and es["failed"] is True and es["n_b"] == 10 and es["n_b_human"] == 0
    assert es["n"] < config.KAPPA_MIN_LANGUAGE_ITEMS
    assert meta["relay_human_study"]["state"] == "passed" and meta["relay_withheld_languages"] == ["es"]


def test_a_stability_check_language_flag_vetoes_nothing(tmp_path, monkeypatch):
    """Only the hand coding study's failed flag withholds a language; a model study's is not read."""
    conn = _db(tmp_path, monkeypatch, {"it": 5})
    strong = {"method": "model_vs_model", "model_a": "claude-sonnet-5", "model_b": "claude-opus-5",
              "sample_size": 300, "kappa_all": 0.8, "kappa_bc": 0.74, "n_bc": 120,
              "details": {"prompt_version": classify_llm.PROMPT_VERSION,
                          "bc_by_language": {"it": {"kappa": None, "n": 30, "failed": True}}}}
    store.record_model_agreement(conn, strong, [])
    assert _meta(conn)["relay_withheld_languages"] == []


# ---------------------------------------------------------------------------
# Pseudonymous coder ids
# ---------------------------------------------------------------------------

def test_a_name_in_the_coder_column_blocks_recording(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 40})
    out = tmp_path / "reviews" / "human_sample_2026-10-17.csv"
    agreement.cmd_human_sample(conn, out, per_language=30, min_labelled=30)
    _fill(conn, out, lambda i, r, cat: WORDS[cat], coder=lambda i: "Jane Doe" if i == 3 else "c1")
    res = agreement.cmd_compute(conn, out, "x")
    assert res["bad_coders"] == ["Jane Doe"]
    assert conn.execute("SELECT COUNT(*) FROM agreement_studies").fetchone()[0] == 0
    _fill(conn, out, lambda i, r, cat: WORDS[cat])
    agreement.cmd_compute(conn, out, "x")
    assert _latest_details(conn)["coders"] == ["c1", "c2", "c3"]
    assert agreement.CODER_ID.match("ra12") and not agreement.CODER_ID.match("DB") and not agreement.CODER_ID.match("daniel")


def test_the_minimum_reads_as_one_per_rater_not_b_plus_c(tmp_path, monkeypatch):
    """"At least 10 articles on each side" read as 10 B plus 10 C; the rule names both raters."""
    conn = _db(tmp_path, monkeypatch, {"en": 10})
    reason = _meta(conn)["relay_provisional_reason"]
    assert "articles on each side" not in reason
    assert "the coders and the model each placing at least 10 articles in unchecked state sourcing" in reason
    short = agreement.kappa_shortfall([("B", "notB")] * 3 + [("notB", "notB")] * 20)
    assert "on each side" not in short and "each rater must place at least 10 articles in unchecked state sourcing" in short
    guide = (Path(__file__).resolve().parents[1] / "data" / "reviews" / "CODING_GUIDE.md").read_text(encoding="utf-8")
    assert "on each side" not in guide
