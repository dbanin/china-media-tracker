"""The correction for articles stored under another language than their feed's (2026-10-12)."""
import json

from pipeline import language_retag, registry, store

HANI_OLD = "https://english.hani.co.kr/rss/"
ARABIC = "https://example-agency.sy/feed/"
ENGLISH = "https://example-agency.sy/en/feed/"
ASIAPLUS = "https://asiaplus.news/feed/"

OUTLETS = [
    {"id": "sy_agency", "language": "en", "country": "SYR", "feeds": [ENGLISH]},
    {"id": "sy_agency_ar", "language": "ar", "country": "SYR", "feeds": [ARABIC]},
    {"id": "kr_hani_en", "language": "en", "country": "KOR", "feeds": ["https://english.hani.co.kr/rss/english_edition/"]},
    {"id": "tj_asiaplus_en", "language": "en", "country": "TJK", "feeds": ["https://asiaplustj.info/en/rss.xml"],
     "release_sections": {"sections": [{"url": "https://asiaplus.news/speczproekty", "kind": "sponsored",
                                        "rejected_feed": ASIAPLUS, "rejected_feed_language": "ru",
                                        "rejected_on": "2026-10-12", "reason": "test"}]}},
    {"id": "fr_one", "language": "fr", "country": "FRA", "feeds": ["https://shared.example/sponsored/rss"]},
    {"id": "fr_one_en", "language": "en", "country": "FRA", "feeds": ["https://shared.example/sponsored/rss"]},
]


def _article(conn, url, feed_url, title, outlet="sy_agency", country="SYR", language="en", relevant=True,
             status="classified", label=("rules", "not_relevant", "residual_not_relevant"), terms=None,
             fail_reason=None, day="2026-10-10"):
    aid = store.insert_discovered(conn, {
        "url": url, "outlet_id": outlet, "country": country, "language": language, "feed_url": feed_url,
        "title": title, "summary": "", "status": status, "gate_relevant": 1 if relevant else 0,
        "gate_terms": terms if terms is not None else ([] if not relevant else ["China"])})
    conn.execute("UPDATE articles SET discovered_at=?, themes=?, fail_reason=? WHERE id=?",
                 (day + "T08:00:00+00:00", json.dumps({"v": "x", "t": ["other"]}), fail_reason, aid))
    store.record_discovery(conn, country, relevant, outlet, day)
    if label:
        store.insert_classification(conn, aid, label[0], label[1], 0.7, reasoning="test",
                                    signatures_fired=[label[2]] if label[2] else [], model_version="m" if label[0] == "llm" else None)
    conn.commit()
    return aid


def _row(conn, aid):
    return conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()


def _labels(conn, aid):
    return [tuple(r) for r in conn.execute(
        "SELECT method, category, is_current FROM classifications WHERE article_id=? ORDER BY id", (aid,))]


def test_language_of_reads_the_registry_in_order():
    langs = language_retag.feed_languages(OUTLETS)
    # the article's own outlet lists the feed
    assert language_retag.language_of("sy_agency", ENGLISH, None, langs) == "en"
    # a feed split off to an outlet of its own language: the one outlet that lists it now
    assert language_retag.language_of("sy_agency", ARABIC, None, langs) == "ar"
    # a rejected section feed whose language is recorded
    assert language_retag.language_of("tj_asiaplus_en", ASIAPLUS, None, langs) == "ru"
    # a feed two outlets of different languages list: the article's own outlet decides, no guess otherwise
    assert language_retag.language_of("fr_one_en", "https://shared.example/sponsored/rss", None, langs) == "en"
    assert language_retag.language_of("xx_other", "https://shared.example/sponsored/rss", None, langs) is None
    # a feed that left the registry, with the English edition's own items kept as they are
    assert language_retag.language_of("kr_hani_en", HANI_OLD, "https://www.hani.co.kr/arti/politics/1.html", langs) == "ko"
    assert language_retag.language_of("kr_hani_en", HANI_OLD,
                                       "https://www.hani.co.kr/arti/english_edition/e_national/2.html", langs) is None
    assert language_retag.language_of("sy_agency", None, None, langs) is None
    assert language_retag.language_of("sy_agency", "https://unknown.example/rss", None, langs) is None


def test_a_residual_label_made_in_the_wrong_language_is_withdrawn_and_judged_again(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, "https://example-agency.sy/a", ARABIC, "الصين وسوريا توقعان اتفاقية")
    out = language_retag.run(conn, outlets=OUTLETS)
    r = _row(conn, aid)
    assert r["language"] == "ar" and r["themes"] is None, "theme tags go, so the export tags it again in Arabic"
    assert r["status"] == "fetched", "the rules stage picks it up again"
    assert _labels(conn, aid) == [("rules", "not_relevant", 0)], "withdrawn, never deleted"
    assert out["sy_agency"]["corrected"] == 1 and out["sy_agency"]["to"] == "ar"
    assert out["sy_agency"]["residual_withdrawn"] == {"residual_not_relevant": 1} and out["sy_agency"]["requeued"] == 1
    assert language_retag.run(conn, outlets=OUTLETS) == {}, "idempotent"


def test_model_and_state_origin_labels_are_left_alone(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    model = _article(conn, "https://example-agency.sy/m", ARABIC, "خبر", label=("llm", "C", None))
    state = _article(conn, "https://example-agency.sy/s", ARABIC, "شينخوا", label=("rules", "A", "xinhua_credit"))
    out = language_retag.run(conn, outlets=OUTLETS)
    for aid, cat in ((model, "C"), (state, "A")):
        r = _row(conn, aid)
        assert r["language"] == "ar" and r["status"] == "classified"
        assert [l[1:] for l in _labels(conn, aid)] == [(cat, 1)]
    assert out["sy_agency"]["kept_labels"] == 2 and out["sy_agency"]["residual_withdrawn"] == {}


def test_a_gated_out_item_the_gate_passes_in_its_language_is_readmitted(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    china = _article(conn, "https://www.hani.co.kr/arti/politics/1.html", HANI_OLD, "대만 총통, 중국 압박에 대응",
                     outlet="kr_hani_en", country="KOR", relevant=False, status="gated_out", label=None)
    other = _article(conn, "https://www.hani.co.kr/arti/sports/2.html", HANI_OLD, "한화 감독 사임",
                     outlet="kr_hani_en", country="KOR", relevant=False, status="gated_out", label=None)
    english = _article(conn, "https://www.hani.co.kr/arti/english_edition/e_national/3.html", HANI_OLD,
                       "Seoul city council votes", outlet="kr_hani_en", country="KOR", relevant=False,
                       status="gated_out", label=None)
    out = language_retag.run(conn, outlets=OUTLETS)
    r = _row(conn, china)
    assert (r["language"], r["status"], r["gate_relevant"]) == ("ko", "queued", 1)
    assert "중국" in json.loads(r["gate_terms"])
    r = _row(conn, other)
    assert (r["language"], r["status"], r["gate_relevant"]) == ("ko", "gated_out", 0)
    assert _row(conn, english)["language"] == "en", "the English edition's own items stay English"
    assert out["kr_hani_en"]["regated"] == 2 and out["kr_hani_en"]["readmitted"] == 1
    od = conn.execute("SELECT discovered, gate_relevant FROM daily_outlet_discovery WHERE outlet_id='kr_hani_en'").fetchone()
    assert tuple(od) == (3, 1), "discovery is not counted again; the relevant side moves by one"
    assert language_retag.run(conn, outlets=OUTLETS) == {}


def test_section_feed_items_are_left_to_section_retag(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    # still tagged with the section and failing the gate in Russian: section_retag gates it out at the export
    gone = _article(conn, "https://asiaplus.news/a", ASIAPLUS, "В Рушане семьи получили новые дома",
                    outlet="tj_asiaplus_en", country="TJK", terms=["section:sponsored"])
    # passing it: judged again here
    kept = _article(conn, "https://asiaplus.news/b", ASIAPLUS, "Таджикистан и Китай намерены расширить сотрудничество",
                    outlet="tj_asiaplus_en", country="TJK", terms=["section:sponsored"])
    # already gated out by section_retag: never re-admitted here
    done = _article(conn, "https://asiaplus.news/c", ASIAPLUS, "Китай и Таджикистан", outlet="tj_asiaplus_en",
                    country="TJK", relevant=False, status="gated_out", label=None,
                    fail_reason="section_feed_removed:2026-10-12 registered as sponsored")
    out = language_retag.run(conn, outlets=OUTLETS)["tj_asiaplus_en"]
    assert _row(conn, gone)["language"] == "ru" and _labels(conn, gone) == [("rules", "not_relevant", 1)]
    assert _row(conn, kept)["status"] == "fetched" and _labels(conn, kept) == [("rules", "not_relevant", 0)]
    assert (_row(conn, done)["language"], _row(conn, done)["status"]) == ("ru", "gated_out")
    assert out["left_to_section_retag"] == 1 and out["requeued"] == 1 and out["regated"] == 0


def test_dry_run_changes_nothing(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    aid = _article(conn, "https://example-agency.sy/a", ARABIC, "الصين")
    out = language_retag.run(conn, outlets=OUTLETS, dry_run=True)
    assert out["sy_agency"]["residual_withdrawn"] == {"residual_not_relevant": 1}
    r = _row(conn, aid)
    assert (r["language"], r["status"]) == ("en", "classified") and _labels(conn, aid)[0][2] == 1


def test_the_registry_carries_the_corrected_languages():
    by = {o["id"]: o for o in registry.load_outlets()}
    expected = {"az_haqqin": "ru", "tn_tunisiefocus": "fr", "mv_psmnews": "dv", "so_goobjoog": "so",
                "cm_mimimefo": "fr", "mg_newsmada": "mg", "sy_northpress_ar": "ar", "ye_yemenpress_ar": "ar",
                "mc_monacotribune_en": "en", "tj_asiaplus": "ru"}
    for oid, lang in expected.items():
        # sy_northpress_ar was a relay outlet, inactive since the relay was retired on 2026-10-22.
        assert by[oid]["language"] == lang and (by[oid]["active"] or by[oid]["inactive_since"] == "2026-10-22"), oid
    # each split feed is polled once, by the outlet of its language
    for oid, feed in (("sy_northpress_ar", "https://npasyria.com/feed/"), ("ye_yemenpress_ar", "https://ypagency.net/feed/"),
                      ("mc_monacotribune_en", "https://www.monaco-tribune.com/en/feed/"), ("tj_asiaplus", ASIAPLUS)):
        assert [o["id"] for o in by.values() if feed in o["feeds"]] == [oid]
    assert by["sy_northpress_ar"]["collector"] == by["sy_northpress"]["collector"] == "self_hosted"
    # the feeds that served another site's language are no longer polled as English
    assert HANI_OLD not in by["kr_hani_en"]["feeds"] and by["kr_hani_en"]["active"]
    assert not by["hu_telex_en"]["active"] and by["hu_telex_en"]["inactive_reason"]
    assert not any(f in o["feeds"] for o in by.values() if o["active"] for f in language_retag.FEED_LANGUAGES)


# The same item, gated by fetch_feeds as it arrives and by language_retag from the stored row.
FOCUS = "https://example-focus.tn/feed/"
CLARIN_RSS = "https://example-diario.ar/rss/"
CLARIN_MAP = "https://example-diario.ar/sitemap-news.xml"
SAME_GATE = [
    # registered Arabic, corrected to French in place; the feed prints a wrapper naming Pékin on every item
    {"id": "tn_focus", "language": "fr", "country": "TUN", "feeds": [FOCUS]},
    # registered English, corrected to Spanish; its news sitemap beside the RSS
    {"id": "ar_diario", "language": "es", "country": "ARG", "feeds": [CLARIN_RSS], "news_sitemaps": [CLARIN_MAP]},
]
FOCUS_WRAP = "Retrouvez chaque jour toute l'actualité de Pékin et de l'Asie sur notre site et notre application mobile"
FOCUS_LEDE = "le conseil municipal vote le budget des routes du département".split()


def _focus_summary(i):
    return "%s. %s" % (" ".join("%s%d" % (w, i) for w in FOCUS_LEDE), FOCUS_WRAP)


def _fetch_feeds_gate(monkeypatch, conn, outlet, feed_url, feed_type, entries):
    """{url: (gate_relevant, gate_terms)} of `entries` polled from feed_url by fetch_feeds.run."""
    from pipeline import fetch_feeds, registry, section_pages
    result = {"ok": True, "error": None, "entries": entries}
    monkeypatch.setattr(fetch_feeds, "poll_outlet", lambda o: [
        {"outlet": o, "feed_url": feed_url, "kind": registry.EDITORIAL, "result": result, "type": feed_type}])
    monkeypatch.setattr(section_pages, "aap_start", lambda conn, outlets: None)
    fetch_feeds.run(conn, "t", outlets=[outlet], workers=1)
    urls = [e["link"] for e in entries]
    return {r["url"]: (r["gate_relevant"], json.loads(r["gate_terms"])) for r in conn.execute(
        "SELECT url, gate_relevant, gate_terms FROM articles WHERE url IN (%s)" % ",".join("?" * len(urls)), urls)}


def _retag_gate(conn, ids):
    language_retag.run(conn, outlets=SAME_GATE)
    return {url: (_row(conn, aid)["gate_relevant"], json.loads(_row(conn, aid)["gate_terms"]) if _row(conn, aid)["gate_relevant"]
                  else None) for url, aid in ids.items()}


def test_a_wrapper_only_china_term_is_gated_as_fetch_feeds_gates_it(tmp_path, monkeypatch):
    import datetime as dt
    day = dt.date.today().isoformat()
    items = {"https://example-focus.tn/route": ("Le conseil vote le budget des routes", _focus_summary(99)),
             "https://example-focus.tn/contrat": ("Un groupe chinois remporte le contrat", _focus_summary(98))}
    history = [("https://example-focus.tn/old%d" % i, "Ancien article %d" % i, _focus_summary(i)) for i in range(12)]
    # language_retag: the feed's items stored in Arabic and gated out, as the gate read them then
    conn = store.connect(tmp_path / "t.db")
    ids = {}
    for url, title, summary in history + [(u, t, s) for u, (t, s) in items.items()]:
        ids[url] = _article(conn, url, FOCUS, title, outlet="tn_focus", country="TUN", language="ar", relevant=False,
                            status="gated_out", label=None, day=day)
        conn.execute("UPDATE articles SET summary=? WHERE id=?", (summary, ids[url]))
    conn.commit()
    retag = _retag_gate(conn, ids)
    # fetch_feeds: the same feed history in French, then a poll bringing the same two items
    live = store.connect(tmp_path / "live.db")
    for url, title, summary in history:
        aid = _article(live, url, FOCUS, title, outlet="tn_focus", country="TUN", language="fr", relevant=False,
                       status="gated_out", label=None, day=day)
        live.execute("UPDATE articles SET summary=? WHERE id=?", (summary, aid))
    live.commit()
    polled = _fetch_feeds_gate(monkeypatch, live, SAME_GATE[0], FOCUS, "rss",
                               [{"link": u, "title": t, "summary": s} for u, (t, s) in items.items()])
    assert polled == {"https://example-focus.tn/route": (0, []),
                      "https://example-focus.tn/contrat": (1, ["chinois"])}
    assert {u: retag[u] for u in items} == {u: (r, terms if r else None) for u, (r, terms) in polled.items()}
    assert not any(retag[u][0] for u, _t, _s in history), "the wrapper's Pékin re-admits none of the feed's items"


def test_a_sitemap_title_only_term_is_gated_as_fetch_feeds_gates_it(tmp_path, monkeypatch):
    items = {"https://example-diario.ar/economia/soja": "El gigante asiático compra más soja argentina",
             "https://example-diario.ar/deportes/river": "River gana el clásico en el Monumental"}
    conn = store.connect(tmp_path / "t.db")
    ids = {u: _article(conn, u, CLARIN_MAP, t, outlet="ar_diario", country="ARG", language="en", relevant=False,
                       status="gated_out", label=None) for u, t in items.items()}
    retag = _retag_gate(conn, ids)
    live = store.connect(tmp_path / "live.db")
    polled = _fetch_feeds_gate(monkeypatch, live, SAME_GATE[1], CLARIN_MAP, "news_sitemap",
                               [{"link": u, "title": t, "summary": ""} for u, t in items.items()])
    assert polled == {"https://example-diario.ar/economia/soja": (1, ["title_only:gigante asiático"]),
                      "https://example-diario.ar/deportes/river": (0, [])}
    assert retag == {u: (r, terms if r else None) for u, (r, terms) in polled.items()}
    # a stored row does not record how its feed was read: the registry's news_sitemaps say it
    assert language_retag.news_sitemap_feeds(SAME_GATE) == {CLARIN_MAP}
