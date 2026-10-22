"""The publication threshold for unchecked state sourcing is kappa 0.7 (owner decision D6, 22 October 2026).

An outside review noted that 0.6 is only moderate agreement and that the original study's coders
reached 0.91. config.KAPPA_WARNING_THRESHOLD is the single source: the export copies it into
meta.kappa_warning_threshold and the provisional reason, the methodology reads it from meta, and the
site's static texts and JavaScript fallbacks must say the same number."""
import json
import re
from pathlib import Path

from pipeline import agreement, config, export, methodology, store
from tests.test_human_coding import WORDS, _db, _fill, _meta

ROOT = Path(__file__).resolve().parents[1]
THR = "%.1f" % config.KAPPA_WARNING_THRESHOLD


def test_threshold_is_0_7():
    assert config.KAPPA_WARNING_THRESHOLD == 0.7 and THR == "0.7"


def test_meta_threshold_provisional_reason_and_site_text_agree(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 10})
    meta = _meta(conn)
    assert meta["kappa_warning_threshold"] == config.KAPPA_WARNING_THRESHOLD == 0.7
    assert meta["relay_human_study"]["threshold"] == 0.7
    assert meta["relay_provisional"] is True
    assert "is at least %s, with the coders" % THR in meta["relay_provisional_reason"]
    assert "0.6" not in meta["relay_provisional_reason"]

    index = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "independent journalism judgement is at least %s, with the coders" % THR in index
    assert "a rerun below %s withholds the counts" % THR in index
    content = (ROOT / "docs" / "content.json").read_text(encoding="utf-8")
    json.loads(content)
    assert "reaches a Cohen's kappa of %s." % THR in content
    assert "versus independent journalism is at least %s, with the coders" % THR in content
    compute = (ROOT / "docs" / "compute.js").read_text(encoding="utf-8")
    assert "independent journalism at kappa %s or above." % THR in compute
    guide = (ROOT / "data" / "reviews" / "CODING_GUIDE.md").read_text(encoding="utf-8")
    assert "independent journalism is at least %s\n(`config.KAPPA_WARNING_THRESHOLD`)" % THR in guide
    for name, text in (("index.html", index), ("content.json", content), ("compute.js", compute), ("CODING_GUIDE.md", guide)):
        assert not re.search(r"(kappa|at least|below|of) 0\.6(?!\d)", text), name

    # app.js reads meta.kappa_warning_threshold; every fallback for a missing meta is the same number.
    app = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    fallbacks = re.findall(r"kappa_warning_threshold\)?\s*\|\|\s*([0-9.]+)", app)
    assert len(fallbacks) >= 7 and set(fallbacks) == {THR}


def _study(tmp_path, monkeypatch, reverse_every):
    """A finished hand coding study on B and C in which the coders reverse every nth model call."""
    conn = _db(tmp_path, monkeypatch, {"en": 80}, cats=("B", "C"))
    out = tmp_path / "reviews" / "human_sample_2026-10-22.csv"
    agreement.cmd_human_sample(conn, out, per_language=48, min_labelled=30)
    _fill(conn, out, lambda i, r, cat: WORDS["C" if cat == "B" else "B"] if i % reverse_every == 0 else WORDS[cat])
    res = agreement.cmd_compute(conn, out, "hand_coder")
    return conn, res


def test_a_kappa_between_0_6_and_0_7_now_withholds(tmp_path, monkeypatch):
    conn, res = _study(tmp_path, monkeypatch, 6)
    assert res["complete"] is True and 0.6 <= res["kappa_bc"] < 0.7, res["kappa_bc"]
    meta = _meta(conn)
    assert meta["relay_human_study"]["state"] == "failed"
    assert meta["relay_publishable"] is False and meta["relay_provisional"] is False


def test_a_kappa_at_or_above_0_7_publishes(tmp_path, monkeypatch):
    conn, res = _study(tmp_path, monkeypatch, 8)
    assert res["complete"] is True and res["kappa_bc"] >= 0.7, res["kappa_bc"]
    meta = _meta(conn)
    assert meta["relay_human_study"]["state"] == "passed"
    assert meta["relay_publishable"] is True and meta["relay_basis"] == "human_coding"


def test_a_stability_rerun_between_0_6_and_0_7_withholds_the_provisional_counts(tmp_path, monkeypatch):
    """The stability check shares the threshold: a current rerun below it withholds the provisional counts."""
    conn = _db(tmp_path, monkeypatch, {"en": 10})
    store.record_model_agreement(conn, {"method": "same_model_rerun", "model_a": "claude-sonnet-5",
                                        "model_b": "claude-sonnet-5", "sample_size": 225, "kappa_all": 0.81,
                                        "kappa_bc": 0.65, "n_bc": 116, "details": {}}, [])
    meta = _meta(conn)
    assert meta["relay_provisional"] is False and meta["relay_publishable"] is False


def test_methodology_says_why_0_7(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, {"en": 10})
    meta = _meta(conn)
    path = tmp_path / "M.md"
    db = tmp_path / "m.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    out = tmp_path / "out"
    export.run(store.connect(db), "test", export_dir=out, audit_dir=tmp_path / "audit", methodology_path=tmp_path / "M0.md")
    full_meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    full_latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    full_meta.update({k: meta[k] for k in ("relay_measured", "relay_provisional", "relay_provisional_reason",
                                           "relay_publishable", "relay_human_study", "kappa_warning_threshold")})
    assert full_meta["relay_measured"] is True
    methodology.write(full_meta, full_latest, path=path)
    text = path.read_text(encoding="utf-8")
    assert ("The threshold is kappa 0.7, raised from 0.6 on 22 October 2026: 0.6 is only moderate agreement on the "
            "usual reading of kappa, 0.7 is substantial agreement, and the coders in the original study reached 0.91."
            in text)
    assert "is at least 0.7, with the coders" in text and "kappa below 0.7, overall or in a language" in text
