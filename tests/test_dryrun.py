from pipeline import dryrun


def test_whole_pipeline_dry_run(tmp_path):
    counts = dryrun.run(out_dir=tmp_path / "data")
    assert counts["items"] == 5
    assert counts["relevant"] == 4          # the bridge story is gated out
    assert counts["paywalled"] == 1
    assert counts["fetched"] == 3
    assert counts["rules"]["A"] == 1        # the Xinhua dateline
    assert counts["rules"]["llm"] >= 1      # the spokesperson relay is routed to the LLM
    assert counts["llm"]["classified"] >= 1
    assert counts["categories"].get("A") == 1
    assert counts["categories"].get("B", 0) >= 1
    assert (tmp_path / "data" / "latest.json").exists()
    assert (tmp_path / "data" / "meta.json").exists()


def test_a_china_term_only_in_the_feed_wrapper_is_ignored_as_by_fetch_feeds(tmp_path, monkeypatch):
    import json

    import feedparser

    from pipeline import store
    from tests.test_section_retag import WRAP, _fetch_feeds_decides, _wrapped
    road, rail = "https://fixture.example/local.html", "https://fixture.example/wire.html"
    feed = tmp_path / "feed.xml"
    feed.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Fixture Daily</title>'
        '<item><title>Council approves road budget</title><link>%s</link><description>%s</description></item>'
        '<item><title>Council approves rail budget</title><link>%s</link><description>%s</description></item>'
        '</channel></rss>' % (road, _wrapped(99), rail, "Chinese firm wins the contract. " + WRAP), encoding="utf-8")
    earlier = [{"url": "https://fixture.example/old%d" % i, "title": "Old item %d" % i, "summary": _wrapped(i)}
               for i in range(12)]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(dryrun.tempfile, "mkdtemp", lambda prefix: str(run_dir))
    counts = dryrun.run(out_dir=tmp_path / "data", keep=True, feed=feed, earlier=earlier)
    assert counts["items"] == 2 and counts["relevant"] == 1
    db = store.connect(run_dir / "tracker.db")
    dry = {r["url"]: (r["gate_relevant"], json.loads(r["gate_terms"])) for r in db.execute(
        "SELECT url, gate_relevant, gate_terms FROM articles WHERE url IN (?, ?)", (road, rail))}
    # fetch_feeds: the same outlet, feed and history, polling the same two entries.
    live = store.connect(tmp_path / "live.db")
    for e in earlier:
        store.insert_discovered(live, dict(e, outlet_id="xx_fixture", country="ITA", language="en",
                                           feed_url="fixture://feed.xml", status="gated_out", gate_relevant=0))
    live.commit()
    entries = feedparser.parse(str(feed)).entries
    fetched = _fetch_feeds_decides(monkeypatch, live, {"id": "xx_fixture", "country": "ITA", "language": "en",
                                                       "feeds": ["fixture://feed.xml"]}, "fixture://feed.xml", entries)
    assert dry == fetched
    assert dry[road] == (0, []) and dry[rail][0] == 1 and "Chinese" in dry[rail][1]
