"""export.run writes into a staging copy of the export directory and swaps it in at the end, so a full
disk part way leaves the published files as they were (the 2026-10-17 robustness audit found docs/data
half rewritten); and it refuses to publish fewer discovered articles than the export it replaces."""
import errno
import hashlib

import pytest

from pipeline import config, export, store


def _snapshot(d):
    return {str(p.relative_to(d)): hashlib.md5(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


def _export(conn, tmp_path, **kw):
    return export.run(conn, "test", export_dir=tmp_path / "out", audit_dir=tmp_path / "audit",
                      methodology_path=tmp_path / "M.md", prune=False, **kw)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.delenv("TRACKER_EXPORT_ALLOW_SHRINK", raising=False)
    c = store.connect(db)
    for _ in range(3):
        store.record_discovery(c, "ITA", True, "it_x", "2026-10-01")
    c.commit()
    yield c
    c.close()


def test_a_full_disk_part_way_leaves_the_published_directory_as_it_was(conn, tmp_path, monkeypatch):
    _export(conn, tmp_path)
    out = tmp_path / "out"
    (out / "kept_by_hand.txt").write_text("not written by the export")
    before = _snapshot(out)
    store.record_discovery(conn, "ITA", True, "it_x", "2026-10-02")
    conn.commit()
    real, n = export.json.dump, {"c": 0}

    def dump(*a, **k):
        n["c"] += 1
        if n["c"] == 4:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(*a, **k)
    monkeypatch.setattr(export.json, "dump", dump)
    with pytest.raises(OSError):
        _export(conn, tmp_path)
    assert _snapshot(out) == before
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".out")) == []
    monkeypatch.setattr(export.json, "dump", real)
    _export(conn, tmp_path)
    after = _snapshot(out)
    assert after["kept_by_hand.txt"] == before["kept_by_hand.txt"]
    assert after["meta.json"] != before["meta.json"]
    assert not list(out.rglob("*.tmp")) and not (tmp_path / ".out.staging").exists() and not (tmp_path / ".out.old").exists()


def test_a_directory_left_half_swapped_is_put_back(tmp_path):
    out, old = tmp_path / "out", tmp_path / ".out.old"
    old.mkdir()
    (old / "meta.json").write_text("{}")
    staging = export.stage_export_dir(out)
    assert (out / "meta.json").exists() and (staging / "meta.json").exists() and not old.exists()


def test_an_export_that_would_lower_articles_discovered_is_refused(conn, tmp_path, monkeypatch):
    _export(conn, tmp_path)
    out = tmp_path / "out"
    before = _snapshot(out)
    conn.execute("UPDATE daily_outlet_discovery SET discovered=1")     # a database come back from an older copy
    conn.commit()
    with pytest.raises(export.ExportShrinkError):
        _export(conn, tmp_path)
    assert _snapshot(out) == before
    monkeypatch.setenv("TRACKER_EXPORT_ALLOW_SHRINK", "1")
    _export(conn, tmp_path)
    monkeypatch.delenv("TRACKER_EXPORT_ALLOW_SHRINK")
    conn.execute("UPDATE daily_outlet_discovery SET discovered=0")
    conn.commit()
    _export(conn, tmp_path, allow_shrink=True)


def test_the_floor_allows_what_junk_feeds_withdrew_this_run_and_the_relay_allowance(tmp_path):
    (tmp_path / "meta.json").write_text('{"articles_discovered": 10000}')
    export.check_discovered_floor({"articles_discovered": 10000}, tmp_path)
    export.check_discovered_floor({"articles_discovered": 9950}, tmp_path)           # within half a percent
    with pytest.raises(export.ExportShrinkError):
        export.check_discovered_floor({"articles_discovered": 9949}, tmp_path)
    export.check_discovered_floor({"articles_discovered": 9850}, tmp_path, withdrawn=100)
    with pytest.raises(export.ExportShrinkError):
        export.check_discovered_floor({"articles_discovered": 9849}, tmp_path, withdrawn=100)
    export.check_discovered_floor({"articles_discovered": 1}, tmp_path / "absent")      # nothing to compare with
    (tmp_path / "meta.json").write_text("not json")
    export.check_discovered_floor({"articles_discovered": 1}, tmp_path)
