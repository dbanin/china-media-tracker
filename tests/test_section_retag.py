"""The correction for articles collected through section feeds removed on 2026-10-12."""
import json

from pipeline import classify_rules, export, relay, section_retag, store

SPORT = "https://www.index.hr/sport"
MIRROR = "https://www.mirror.co.uk/rss.xml"
ASIAPLUS = "https://asiaplus.news/feed/"
REAL_SECTION = "https://www.straitstimes.com/paid-press-releases"


def _article(conn, url, feed_url, title, terms, outlet="hr_index", country="HRV", language="hr",
             status="classified", category="not_relevant", day="2026-09-20"):
    aid = store.insert_discovered(conn, {
        "url": url, "outlet_id": outlet, "country": country, "language": language, "feed_url": feed_url,
        "title": title, "summary": "", "status": status, "gate_relevant": 1, "gate_terms": terms})
    conn.execute("UPDATE articles SET discovered_at=? WHERE id=?", (day + "T08:00:00+00:00", aid))
    store.record_discovery(conn, country, True, outlet, day)
    if category:
        store.insert_classification(conn, aid, "rules", category, 0.7, reasoning="test",
                                    signatures_fired=["residual_%s" % category])
    conn.commit()
    return aid


def _row(conn, aid):
    return conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()


def _labels(conn, aid):
    return [tuple(r) for r in conn.execute(
        "SELECT category, is_current FROM classifications WHERE article_id=? ORDER BY id", (aid,))]


def test_a_sport_desk_item_is_gated_out_and_its_label_withdrawn(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, "https://www.index.hr/sport/a", SPORT, "Dinamo pobijedio Hajduk", ["section:sponsored"])
    out = section_retag.run(conn)
    r = _row(conn, aid)
    assert (r["status"], r["gate_relevant"], json.loads(r["gate_terms"])) == ("gated_out", 0, [])
    assert r["fail_reason"].startswith("section_feed_removed:2026-10-12") and "was classified" in r["fail_reason"]
    assert _labels(conn, aid) == [("not_relevant", 0)], "withdrawn, never deleted"
    assert out[SPORT]["regated_out"] == 1 and out[SPORT]["labels_withdrawn"] == {"not_relevant": 1}
    od = conn.execute("SELECT discovered, gate_relevant FROM daily_outlet_discovery").fetchone()
    assert tuple(od) == (1, 0), "discovery stays counted; only the relevant side moves"
    assert export.article_rows(conn) == []


def test_a_china_item_stays_with_its_label_and_arrives_by_the_editorial_feed(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, "https://www.mirror.co.uk/a", MIRROR, "China and Britain sign a trade deal",
                   ["section:partner"], outlet="gb_mirror", country="GBR", language="en", category="C")
    assert classify_rules.arrival_of(_row(conn, aid)["gate_terms"]) == "partner_section"
    out = section_retag.run(conn)
    r = _row(conn, aid)
    assert (r["status"], r["gate_relevant"]) == ("classified", 1)
    assert "China" in json.loads(r["gate_terms"])
    assert classify_rules.arrival_of(r["gate_terms"]) == "editorial_feed"
    assert _labels(conn, aid) == [("C", 1)]
    assert out[MIRROR]["kept"] == 1 and out[MIRROR]["regated_out"] == 0
    od = conn.execute("SELECT gate_relevant FROM daily_outlet_discovery").fetchone()[0]
    assert od == 1


def test_a_china_term_already_stored_keeps_the_item_without_regating(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, "https://www.mirror.co.uk/b", MIRROR, "Nothing in this headline",
                   ["China", "section:partner"], outlet="gb_mirror", country="GBR", language="en")
    out = section_retag.run(conn)
    assert json.loads(_row(conn, aid)["gate_terms"]) == ["China"]
    assert out[MIRROR]["kept_on_terms"] == 1


def test_the_russian_feeds_of_english_outlets_are_gated_in_russian(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, "https://asiaplus.news/a", ASIAPLUS, "Таджикистан и Китай намерены расширить сотрудничество",
                   ["section:sponsored"], outlet="tj_asiaplus_en", country="TJK", language="en")
    section_retag.run(conn)
    r = _row(conn, aid)
    assert r["gate_relevant"] == 1 and "Китай" in json.loads(r["gate_terms"])


def test_it_runs_once_and_leaves_real_sections_alone(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    gone = _article(conn, "https://www.index.hr/sport/b", SPORT, "Rukometaši slavili", ["section:sponsored"])
    real = _article(conn, "https://www.straitstimes.com/r", REAL_SECTION, "Company launches product",
                    ["section:press_release"], outlet="sg_st", country="SGP", language="en")
    assert section_retag.run(conn)
    before = [tuple(_row(conn, i)) for i in (gone, real)] + [_labels(conn, gone)]
    assert section_retag.run(conn) == {}
    assert [tuple(_row(conn, i)) for i in (gone, real)] + [_labels(conn, gone)] == before
    assert classify_rules.arrival_of(_row(conn, real)["gate_terms"]) == "press_release_section"


def test_dry_run_counts_and_changes_nothing(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, "https://www.index.hr/sport/c", SPORT, "Košarkaši izgubili", ["section:sponsored"])
    before = tuple(_row(conn, aid))
    out = section_retag.run(conn, dry_run=True)
    assert out[SPORT]["regated_out"] == 1 and out[SPORT]["labels_withdrawn"] == {"not_relevant": 1}
    assert tuple(_row(conn, aid)) == before and _labels(conn, aid) == [("not_relevant", 1)]


def test_a_label_collected_after_the_correction_is_withdrawn_too(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, "https://www.index.hr/sport/d", SPORT, "Vaterpolisti prvaci", ["section:sponsored"],
                   status="llm_submitted", category=None)
    section_retag.run(conn)
    # the batch it was in comes back with a model label
    store.insert_classification(conn, aid, "llm", "not_relevant", 0.9, model_version="m")
    conn.execute("UPDATE articles SET status='classified' WHERE id=?", (aid,))
    conn.commit()
    out = section_retag.run(conn)
    assert out[SPORT]["late_labels_withdrawn"] == 1
    assert _row(conn, aid)["status"] == "gated_out" and _labels(conn, aid) == [("not_relevant", 0)]
    assert section_retag.run(conn) == {}


def test_prune_keeps_a_gated_out_item_that_holds_labels(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    labelled = _article(conn, "https://www.index.hr/sport/e", SPORT, "Nogometaši remizirali", ["section:sponsored"],
                        day="2020-01-01")
    plain = store.insert_discovered(conn, {"url": "https://www.index.hr/x", "outlet_id": "hr_index", "country": "HRV",
                                           "language": "hr", "status": "gated_out", "gate_relevant": 0})
    conn.execute("UPDATE articles SET discovered_at='2020-01-01T00:00:00+00:00', audit_keep=0")
    conn.commit()
    section_retag.run(conn)
    conn.execute("UPDATE articles SET audit_keep=0")
    assert store.prune_gated_out(conn, days=3) == 1, "the foreign keys would refuse the statement otherwise"
    assert _row(conn, labelled) is not None and _row(conn, plain) is None


def test_the_relay_does_not_readmit_a_corrected_item(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, "https://news.liga.net/ua/a", "https://news.liga.net/ua/all/rss.xml", "Погода у Києві",
                   ["section:press_release"], outlet="ua_liga", country="UKR", language="uk", category=None,
                   status="queued")
    section_retag.run(conn)
    row = conn.execute("SELECT id, status, gate_relevant, country, title, fail_reason FROM articles WHERE id=?",
                       (aid,)).fetchone()
    assert row["status"] == "gated_out"
    item = {"url_hash": "x", "gate_relevant": 1, "gate_terms": '["section:press_release"]', "status": "fetched",
            "feed_url": "https://news.liga.net/ua/all/rss.xml"}
    assert relay.update_existing(conn, row, item) is None
    assert _row(conn, aid)["gate_relevant"] == 0


SEVENNEWS = "https://7news.com.au/lifestyle/sponsored/feed"


def test_the_7news_your_feed_items_face_the_gate_like_its_editorial_feed(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    tennis = _article(conn, "https://7news.com.au/sport/tennis/a-c-1", SEVENNEWS,
                      "Alex de Minaur gifted walkover as Novak Djokovic suffers shock loss", ["section:sponsored"],
                      outlet="au_7news", country="AUS", language="en", category="C")
    china = _article(conn, "https://7news.com.au/news/world/b-c-2", SEVENNEWS, "China warns Australia over trade",
                     ["section:sponsored"], outlet="au_7news", country="AUS", language="en", category="C")
    out = section_retag.run(conn)
    assert _row(conn, tennis)["status"] == "gated_out" and _labels(conn, tennis) == [("C", 0)]
    assert _row(conn, tennis)["fail_reason"].startswith("section_feed_removed:2026-10-12 registered as sponsored")
    assert classify_rules.arrival_of(_row(conn, china)["gate_terms"]) == "editorial_feed"
    assert out[SEVENNEWS]["regated_out"] == 1 and out[SEVENNEWS]["kept"] == 1


def test_no_removed_feed_is_still_registered_as_a_section_feed():
    from pipeline import registry
    by_id = {o["id"]: o for o in registry.load_outlets()}
    for url, (outlet, *_rest) in section_retag.REMOVED_SECTION_FEEDS.items():
        assert outlet in by_id, outlet
        assert url not in {f["url"] for f in by_id[outlet].get("section_feeds") or []}, url


# A wrapper the feed prints on every item, with the only China term of the items below.
WRAP = "Read more of our China and Asia coverage on the website every day of the week"
LEDE = "council approves budget for road repairs across the district".split()


def _wrapped(i):
    return "%s. %s" % (" ".join("%s%d" % (w, i) for w in LEDE), WRAP)


def _fetch_feeds_decides(monkeypatch, conn, outlet, feed_url, entries):
    """{url: (gate_relevant, gate_terms)} of `entries` polled from feed_url by fetch_feeds.run."""
    from pipeline import fetch_feeds, registry, section_pages
    result = {"ok": True, "error": None, "entries": entries}
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": feed_url, "kind": registry.EDITORIAL, "result": result}])
    monkeypatch.setattr(section_pages, "aap_start", lambda conn, outlets: None)
    fetch_feeds.run(conn, "t", outlets=[outlet], workers=1)
    urls = [e["link"] for e in entries]
    return {r["url"]: (r["gate_relevant"], json.loads(r["gate_terms"])) for r in conn.execute(
        "SELECT url, gate_relevant, gate_terms FROM articles WHERE url IN (%s)" % ",".join("?" * len(urls)), urls)}


def test_a_china_term_only_in_the_feed_wrapper_is_ignored_as_by_fetch_feeds(tmp_path, monkeypatch):
    items = {"https://www.mirror.co.uk/road": ("Council approves road budget", _wrapped(99)),
             "https://www.mirror.co.uk/rail": ("Council approves rail budget", "Chinese firm wins the contract. " + WRAP)}
    # The retag: the feed's items, all collected under the section tag, the two above among them.
    conn = store.connect(tmp_path / "t.db")
    for i in range(12):
        _article(conn, "https://www.mirror.co.uk/old%d" % i, MIRROR, "Old item %d" % i, ["section:partner"],
                 outlet="gb_mirror", country="GBR", language="en", category=None)
    ids = {}
    for url, (title, summary) in items.items():
        ids[url] = _article(conn, url, MIRROR, title, ["section:partner"], outlet="gb_mirror", country="GBR",
                            language="en", category=None)
        conn.execute("UPDATE articles SET summary=? WHERE id=?", (summary, ids[url]))
    for i in range(12):
        conn.execute("UPDATE articles SET summary=? WHERE url=?", (_wrapped(i), "https://www.mirror.co.uk/old%d" % i))
    conn.commit()
    road = ids["https://www.mirror.co.uk/road"]
    assert section_retag.decide(_row(conn, road))["relevant"], "read whole, the wrapper's China term keeps it"
    dry = section_retag.run(conn, dry_run=True)
    assert dry[MIRROR]["regated_out"] == 13 and dry[MIRROR]["kept"] == 1
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='feed_boilerplate'").fetchone() is None, \
        "a dry run writes no cache"
    section_retag.run(conn)
    retag = {url: (_row(conn, i)["gate_relevant"], json.loads(_row(conn, i)["gate_terms"])) for url, i in ids.items()}
    # fetch_feeds: the same feed history, then a poll bringing the same two items.
    live = store.connect(tmp_path / "live.db")
    for i in range(12):
        _article(live, "https://www.mirror.co.uk/old%d" % i, MIRROR, "Old item %d" % i, [], outlet="gb_mirror",
                 country="GBR", language="en", category=None)
        live.execute("UPDATE articles SET summary=? WHERE url=?", (_wrapped(i), "https://www.mirror.co.uk/old%d" % i))
    live.commit()
    entries = [{"link": url, "title": title, "summary": summary} for url, (title, summary) in items.items()]
    fetched = _fetch_feeds_decides(monkeypatch, live, {"id": "gb_mirror", "country": "GBR", "language": "en",
                                                       "feeds": [MIRROR]}, MIRROR, entries)
    assert retag == fetched
    assert retag["https://www.mirror.co.uk/road"] == (0, [])
    assert retag["https://www.mirror.co.uk/rail"][0] == 1 and "Chinese" in retag["https://www.mirror.co.uk/rail"][1]
    assert _row(conn, road)["status"] == "gated_out"


def test_tj_asiaplus_polls_the_rejected_feed_and_takes_over_what_the_section_poll_stored(tmp_path, monkeypatch):
    """Asia-Plus's Russian feed is a rejected section feed on Asia-Plus English and the editorial feed of
    tj_asiaplus. What tj_asiaplus polls is kept and gated in Russian; what the section poll stored under the
    English outlet moves to tj_asiaplus once, is gated in Russian there, and every item is counted once."""
    from pipeline import discover_sections as ds, language_retag, registry
    by_id = {o["id"]: o for o in registry.load_outlets()}
    ru, en = by_id["tj_asiaplus"], by_id["tj_asiaplus_en"]
    assert ru["active"] and ru["language"] == "ru" and ASIAPLUS in ru["feeds"]
    assert ASIAPLUS in {s.get("rejected_feed") for s in en["release_sections"]["sections"]}
    assert ASIAPLUS not in en["feeds"] + [f["url"] for f in en.get("section_feeds") or []]
    assert ds._url_key(ASIAPLUS) in ds.rejections(en)[1] and not ds.rejections(ru)[1], "the rejection is the English outlet's"
    assert section_retag.section_feed_owners()[ASIAPLUS] == ("tj_asiaplus", "TJK")
    conn = store.connect(tmp_path / "t.db")
    china = _article(conn, "https://asiaplus.news/old1", ASIAPLUS, "Таджикистан и Китай намерены расширить сотрудничество",
                     ["section:sponsored"], outlet="tj_asiaplus_en", country="TJK", language="en")
    other = _article(conn, "https://asiaplus.news/old2", ASIAPLUS, "В Рушане семьи получили новые дома",
                     ["section:sponsored"], outlet="tj_asiaplus_en", country="TJK", language="en")
    english = _article(conn, "https://asiaplustj.info/en/a", "https://asiaplustj.info/en/rss.xml",
                       "China and Tajikistan sign a trade deal", ["China"], outlet="tj_asiaplus_en", country="TJK",
                       language="en", category="C")
    entries = [{"link": "https://asiaplus.news/new1", "title": "Китай построит в Душанбе новую школу", "summary": ""},
               {"link": "https://asiaplus.news/new2", "title": "В Хороге открылся новый рынок", "summary": ""},
               {"link": "https://asiaplus.news/old2", "title": "В Рушане семьи получили новые дома", "summary": ""}]
    polled = _fetch_feeds_decides(monkeypatch, conn, ru, ASIAPLUS, entries)
    assert polled["https://asiaplus.news/new1"][0] == 1 and "Китай" in polled["https://asiaplus.news/new1"][1]
    assert polled["https://asiaplus.news/new2"] == (0, [])
    assert not any(section_retag.is_section_term(t) for u in ("https://asiaplus.news/new1", "https://asiaplus.news/new2")
                   for t in polled[u][1]), "polled as an editorial feed, not a section"
    new = {u: tuple(conn.execute("SELECT * FROM articles WHERE url=?", (u,)).fetchone())
           for u in ("https://asiaplus.news/new1", "https://asiaplus.news/new2")}
    assert {r[0] for r in conn.execute("SELECT outlet_id FROM articles WHERE url LIKE 'https://asiaplus.news/new%'")} == {"tj_asiaplus"}

    out = section_retag.run(conn)
    assert (out[ASIAPLUS]["moved"], out[ASIAPLUS]["moved_to"], out[ASIAPLUS]["kept"], out[ASIAPLUS]["regated_out"]) == \
        (2, "tj_asiaplus", 1, 1)
    assert {u: tuple(conn.execute("SELECT * FROM articles WHERE url=?", (u,)).fetchone()) for u in new} == new, \
        "what tj_asiaplus polls itself is left as it is"
    assert (_row(conn, china)["outlet_id"], _row(conn, china)["gate_relevant"]) == ("tj_asiaplus", 1)
    assert "Китай" in json.loads(_row(conn, china)["gate_terms"])
    assert (_row(conn, other)["outlet_id"], _row(conn, other)["status"]) == ("tj_asiaplus", "gated_out")
    assert _row(conn, english)["outlet_id"] == "tj_asiaplus_en"
    od = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT outlet_id, SUM(discovered), SUM(gate_relevant) FROM daily_outlet_discovery GROUP BY outlet_id")}
    assert od == {"tj_asiaplus_en": (1, 1), "tj_asiaplus": (4, 2)}, "each item counted once, under the outlet that owns it"

    lr = language_retag.run(conn)
    assert "tj_asiaplus_en" not in lr and lr["tj_asiaplus"]["corrected"] == 2 and lr["tj_asiaplus"]["to"] == "ru"
    assert _row(conn, china)["language"] == "ru" and _row(conn, china)["status"] == "fetched"
    assert _labels(conn, china) == [("not_relevant", 0)], "the English residual label is judged again in Russian"
    assert section_retag.run(conn) == {} and language_retag.run(conn) == {}
    _fetch_feeds_decides(monkeypatch, conn, ru, ASIAPLUS, entries)
    assert {r[0]: r[1] for r in conn.execute(
        "SELECT outlet_id, SUM(discovered) FROM daily_outlet_discovery GROUP BY outlet_id")} == {"tj_asiaplus_en": 1, "tj_asiaplus": 4}
