"""The relay bundle's size and transport.

A bundle is force-pushed as one parentless commit made in a throwaway repository, so neither the
branch nor the owner's checkout keeps old bundles. Once the runner acknowledges a bundle (relay-ack),
the next one carries only the article rows and shared-feed polls changed since; a runner whose
database went back to an older copy sees the gap, keeps its acknowledgement, and the next bundle
fills it. Rows ingest would never use stay home: unsaturated polls travel without their entry dates,
and a shared outlet's stale gated-out rows are not sent at all.
"""
import datetime as dt
import json
import subprocess

from pipeline import config, relay, store

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
SHARED = {"id": "it_short", "name": "S", "country": "ITA", "language": "it", "feeds": ["https://short.test/rss"],
          "tier": "national", "active": True, "collector": "both"}
OWN = {"id": "in_blocked", "name": "B", "country": "IND", "language": "en", "feeds": [], "tier": "national",
       "active": True, "collector": "self_hosted"}
OUTLETS = [SHARED, OWN]


def _iso(hours_ago):
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


def _add(conn, url, outlet="in_blocked", relevant=True, discovered=None):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": "IND", "language": "en",
                                         "title": "China and India talk trade " + url, "gate_relevant": 1 if relevant else 0,
                                         "status": "queued" if relevant else "gated_out",
                                         "gate_terms": ["China"] if relevant else []})
    if discovered:
        store.update_article(conn, aid, discovered_at=discovered)
    conn.commit()
    return aid


def _age_changes(conn, hours_ago):
    """Every row as if it last changed hours_ago (the relay's runs before this one)."""
    relay.ensure_change_log(conn)
    conn.execute("UPDATE relay_changes SET changed_at=?", (_iso(hours_ago),))
    conn.commit()


def _articles(data):
    return [it for it in relay.iter_bundle(data) if not it.get("_type")]


def _meta(data):
    first = next(iter(relay.iter_bundle(data)))
    assert first["_type"] == "bundle_meta"
    return first


def test_the_change_log_sees_inserts_updates_and_deletes(tmp_path):
    conn = store.connect(tmp_path / "relay.db")
    a = _add(conn, "https://blocked.test/a")
    relay.ensure_change_log(conn)              # created now: the row already there counts as changed now
    h = store.url_hash("https://blocked.test/a")
    assert conn.execute("SELECT changed_at FROM relay_changes WHERE url_hash=?", (h,)).fetchone()[0] >= _iso(0.01)
    _age_changes(conn, 5)
    b = _add(conn, "https://blocked.test/b")
    # a gate re-admission or a section retag changes a row without touching its dates; it is seen all the same
    conn.execute("UPDATE articles SET gate_relevant=1, status='queued' WHERE id=?", (a,))
    conn.commit()
    got = dict(conn.execute("SELECT url_hash, changed_at FROM relay_changes").fetchall())
    assert got[h] >= _iso(0.01) and got[store.url_hash("https://blocked.test/b")] >= _iso(0.01)
    # a field the bundle does not carry is not a change
    _age_changes(conn, 5)
    conn.execute("UPDATE articles SET audit_keep=1 WHERE id=?", (b,))
    conn.commit()
    assert conn.execute("SELECT MAX(changed_at) FROM relay_changes").fetchone()[0] == _iso(5)
    conn.execute("DELETE FROM articles WHERE id=?", (b,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM relay_changes").fetchone()[0] == 1
    # a lost trigger marks everything changed again rather than letting changes go unseen
    _age_changes(conn, 5)
    conn.execute("DROP TRIGGER %s" % relay.UPDATE_TRIGGER)
    relay.ensure_change_log(conn)
    assert conn.execute("SELECT changed_at FROM relay_changes").fetchone()[0] >= _iso(0.01)
    assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")} >= set(relay.CHANGE_TRIGGERS)


def test_delta_since_starts_a_margin_before_the_ack_and_falls_back_to_the_window():
    ack = _iso(2)
    assert relay.delta_since(ack, now=NOW) == _iso(2 + relay.ACK_MARGIN_HOURS)
    assert relay.delta_since(None, now=NOW) is None
    assert relay.delta_since("not a time", now=NOW) is None
    assert relay.delta_since("2026-10-12T10:00:00", now=NOW) is None          # no time zone: not trusted
    assert relay.delta_since(_iso(24 * relay.BUNDLE_DAYS), now=NOW) is None   # older than the window
    assert relay.delta_since(_iso(-5), now=NOW) is None                       # from the future


def test_after_an_ack_a_bundle_carries_only_what_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    src = store.connect(tmp_path / "relay.db")
    ids = [_add(src, "https://blocked.test/%d" % i) for i in range(3)]
    first = relay.build_bundle(src, outlets=OUTLETS)
    assert _meta(first)["since"] is None and len(_articles(first)) == 3
    dst = store.connect(tmp_path / "main.db")
    counts = relay.ingest(dst, first, outlets=OUTLETS)
    assert counts["bundle"] == "window" and counts["acked_through"] == _meta(first)["built_at"]

    # an hour later: the relay fetched one article; the other two have not changed since the first bundle
    _age_changes(src, 3)
    store.update_article(src, ids[1], status="fetched", fetched_at=store.utcnow(),
                         body_hash=store.save_body(store.url_hash("https://blocked.test/1"), "Body about China."),
                         body_chars=17)
    src.commit()
    ack = relay._ack_through(dst)
    second = relay.build_bundle(src, ack=ack, outlets=OUTLETS)
    assert _meta(second)["since"] == relay.delta_since(ack)
    assert [a["url"] for a in _articles(second)] == ["https://blocked.test/1"]
    assert _articles(second)[0]["body"]
    counts = relay.ingest(dst, second, outlets=OUTLETS)
    assert counts["bundle"] == "changes" and "bundle_gap_after" not in counts
    assert counts["updated"] == 1 and counts["acked_through"] >= ack
    assert dst.execute("SELECT status FROM articles WHERE url='https://blocked.test/1'").fetchone()[0] == "fetched"
    # the whole window again (no ack on the relay's side) changes nothing on the runner
    again = relay.ingest(dst, relay.build_bundle(src, outlets=OUTLETS), outlets=OUTLETS)
    assert again["inserted"] == 0 and again["updated"] == 0


def test_a_runner_that_went_back_keeps_its_ack_and_the_next_bundle_fills_the_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    src = store.connect(tmp_path / "relay.db")
    _add(src, "https://blocked.test/old")
    _age_changes(src, 5)
    _add(src, "https://blocked.test/new")
    # the relay was told of a bundle this database does not hold (it came back from an older cache)
    delta = relay.build_bundle(src, ack=_iso(1), outlets=OUTLETS)
    assert [a["url"] for a in _articles(delta)] == ["https://blocked.test/new"]
    dst = store.connect(tmp_path / "main.db")
    counts = relay.ingest(dst, delta, outlets=OUTLETS)
    assert counts["inserted"] == 1 and counts["bundle_gap_after"] is None and "acked_through" not in counts
    assert relay._ack_through(dst) is None            # push_ack sends "through": null: the next bundle is whole
    counts = relay.ingest(dst, relay.build_bundle(src, ack=relay._ack_through(dst), outlets=OUTLETS), outlets=OUTLETS)
    assert counts["inserted"] == 1 and counts["bundle"] == "window" and counts["acked_through"]
    assert dst.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 2
    # an ack older than the bundle's start is a gap too, and does not move
    store.update_article(src, 1, title="changed")
    src.commit()
    dst.execute("UPDATE relay_ack SET through=?", (_iso(6),))
    dst.commit()
    counts = relay.ingest(dst, relay.build_bundle(src, ack=_iso(1), outlets=OUTLETS), outlets=OUTLETS)
    assert counts["bundle_gap_after"] == _iso(6) and relay._ack_through(dst) == _iso(6)


def test_unsaturated_polls_travel_without_entry_dates_grouped_by_feed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    src = store.connect(tmp_path / "relay.db")
    times = [_iso(h) for h in (3, 4, 5)]
    for feed, h, sat in (("https://b.test/rss", 2, False), ("https://a.test/rss", 2, True),
                         ("https://b.test/rss", 1, True), ("https://a.test/rss", 1, False)):
        store.log_feed_poll(src, feed, "self_hosted", _iso(h), "it_short", "ITA", 3, 1, times, _iso(h + 1), sat, 0.5 if sat else 0)
    src.commit()
    events = [it for it in relay.iter_bundle(relay.build_bundle(src, outlets=OUTLETS)) if it.get("_type") == "feed_poll_event"]
    assert [(e["feed_url"], e["polled_at"]) for e in events] == [
        ("https://a.test/rss", _iso(2)), ("https://a.test/rss", _iso(1)), ("https://b.test/rss", _iso(2)), ("https://b.test/rss", _iso(1))]
    assert [json.loads(e["times"]) for e in events] == [sorted(times), [], [], sorted(times)]
    # after an ack, only the polls logged since its margin travel
    later = [it for it in relay.iter_bundle(relay.build_bundle(src, ack=_iso(0.5 - relay.ACK_MARGIN_HOURS + 1), outlets=OUTLETS))
             if it.get("_type") == "feed_poll_event"]
    assert [e["polled_at"] for e in later] == [_iso(1), _iso(1)]


def test_stale_gated_out_rows_of_a_shared_outlet_stay_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    src = store.connect(tmp_path / "relay.db")
    old = _iso(24 * (config.GATED_OUT_RETENTION_DAYS - 1) + 2)
    _add(src, "https://short.test/old-gated", outlet="it_short", relevant=False, discovered=old)
    _add(src, "https://short.test/new-gated", outlet="it_short", relevant=False, discovered=_iso(5))
    _add(src, "https://short.test/old-relevant", outlet="it_short", relevant=True, discovered=old)
    _add(src, "https://blocked.test/old-gated", outlet="in_blocked", relevant=False, discovered=old)
    sent = {a["url"] for a in _articles(relay.build_bundle(src, outlets=OUTLETS))}
    assert sent == {"https://short.test/new-gated", "https://short.test/old-relevant", "https://blocked.test/old-gated"}
    # what ingest makes of it is what it made of the whole set: it skipped the stale shared row anyway
    dst = store.connect(tmp_path / "main.db")
    relay.ingest(dst, relay.build_bundle(src, outlets=OUTLETS), outlets=OUTLETS)
    assert {r[0] for r in dst.execute("SELECT url FROM articles")} == sent


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True).stdout.decode().strip()


def test_bundles_and_acks_are_single_commits_that_leave_the_checkout_alone(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    _git("init", "--bare", "--quiet", str(remote))
    _git("init", "--quiet", str(checkout))
    _git("remote", "add", "origin", str(remote), cwd=checkout)
    monkeypatch.setattr(config, "ROOT", checkout)
    assert relay.fetch_ack() is None                      # no acknowledgement yet: the bundle is whole

    for payload in (b"first bundle", b"second bundle"):
        commit = relay.push_bundle(payload)
    assert _git("rev-parse", "refs/heads/relay", cwd=remote) == commit
    assert _git("rev-list", "--count", "refs/heads/relay", cwd=remote) == "1"
    assert _git("cat-file", "-p", "refs/heads/relay:%s" % relay.BUNDLE_NAME, cwd=remote) == "second bundle"
    # nothing of either bundle reached the checkout: no objects, no remote-tracking ref, no reflog
    assert _git("count-objects", cwd=checkout).startswith("0 objects")
    assert _git("for-each-ref", cwd=checkout) == ""
    # the runner reads it as before
    assert relay.fetch_bundle() == b"second bundle"

    conn = store.connect(tmp_path / "main.db")
    out = relay.push_ack(conn)
    assert out["through"] is None and relay.fetch_ack() is None
    relay._advance_ack(conn, {"built_at": _iso(1), "since": None})
    out = relay.push_ack(conn)
    assert relay.fetch_ack() == _iso(1)
    assert relay.push_ack(conn)["commit"] == out["commit"]          # an unchanged ack is the same commit
    assert _git("rev-list", "--count", "refs/heads/%s" % relay.ACK_BRANCH, cwd=remote) == "1"
