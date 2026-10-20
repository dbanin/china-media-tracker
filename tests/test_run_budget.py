"""pipeline/run.py cmd_fetch: the reference fetch takes reference.FETCH_SLICE_SECONDS even when the run's
budget is spent, so the article fetch and the passes after it stop that much before the deadline and the
slice fits inside the 35 minute budget."""
import types

from pipeline import body_dups, fetch_articles, planted, reference, run


def test_the_reference_slice_fits_inside_the_fetch_budget(monkeypatch):
    seen = {}

    def note(name, deadline):
        seen[name] = deadline
        return {}
    monkeypatch.setattr(fetch_articles, "run", lambda conn, run_id, deadline=None, limit=None: note("fetch", deadline))
    monkeypatch.setattr(planted, "scan_quietly", lambda conn, deadline=None: note("planted", deadline))
    monkeypatch.setattr(body_dups, "run", lambda conn, deadline=None: note("body_links", deadline))
    monkeypatch.setattr(reference, "run_stage", lambda conn, stage, deadline=None: note("reference", deadline))
    monkeypatch.setattr(run.time, "time", lambda: 1000.0)
    monkeypatch.setattr(run.time, "monotonic", lambda: 50.0)
    run.cmd_fetch(None, "r", types.SimpleNamespace(limit=None), 3100.0)
    early = 3100.0 - reference.FETCH_SLICE_SECONDS
    assert seen["fetch"] == seen["body_links"] == early
    assert seen["planted"] == 50.0 + (early - 1000.0)       # the scan's monotonic deadline, as converted
    assert seen["reference"] == 3100.0                     # its own slice ends at the run's deadline


def test_no_deadline_stays_no_deadline(monkeypatch):
    seen = {}
    monkeypatch.setattr(fetch_articles, "run", lambda conn, run_id, deadline=None, limit=None: seen.update(f=deadline) or {})
    monkeypatch.setattr(planted, "scan_quietly", lambda conn, deadline=None: {})
    monkeypatch.setattr(body_dups, "run", lambda conn, deadline=None: {})
    monkeypatch.setattr(reference, "run_stage", lambda conn, stage, deadline=None: seen.update(r=deadline) or {})
    run.cmd_fetch(None, "r", types.SimpleNamespace(limit=None), None)
    assert seen == {"f": None, "r": None}
