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
