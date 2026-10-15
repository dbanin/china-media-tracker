"""Theme tagging across languages, and the tagging pass over a database."""
from pipeline import store, themes


def t(title, lang, summary="", body=""):
    return themes.tag(title, summary, body, lang)


def test_catalog_is_ordered_with_other_last():
    ids = [c["id"] for c in themes.catalog()]
    assert ids[0] == "diplomacy" and ids[-1] == "other" and len(ids) == len(set(ids))


def test_english():
    assert "diplomacy" in t("Xi and Modi meet at the BRICS summit in New Delhi", "en")
    assert set(t("China curbs rare earth exports as tariff fight widens", "en")) >= {"economy", "energy"}
    assert t("Chinese chef opens a restaurant in Lima", "en") == ["other"]


def test_romance_and_german():
    assert set(t("Cina: dazi sull'acciaio, la risposta di Pechino", "it")) >= {"economy", "energy"}
    tib = t("Terremoto en el Tíbet deja 120 muertos", "es")
    assert "disasters" in tib and "rights" not in tib
    assert "culture" in t("Le cinéma chinois, une inspiration pour le Burkina Faso", "fr")
    assert "security" in t("Chinesische Kriegsschiffe vor Taiwan", "de")
    assert "investment" in t("Ferrovia de alta velocidade financiada pela China", "pt")


def test_cyrillic_arabic_and_asian_scripts():
    assert set(t("Военные учения НОАК у берегов Тайваня", "ru")) >= {"security", "taiwan_hk"}
    assert "diplomacy" in t("قمة البريكس في نيودلهي", "ar")
    assert set(t("중국 반도체 수출 규제 강화", "ko")) >= {"technology", "economy"}
    assert set(t("台湾海峡で中国軍が演習", "ja")) >= {"security", "taiwan_hk"}
    assert "disasters" in t("Lũ quét ở Tây Tạng khiến nhiều người thiệt mạng", "vi")
    assert "energy" in t("Hilirisasi nikel dan investasi China di Sulawesi", "ms")


def test_place_names_and_generic_words_do_not_make_a_theme():
    flood = t("Nepal-Tibet floods: rescuers reach the Friendship Bridge as the dam gives way", "en")
    assert "disasters" in flood and "rights" not in flood and "investment" not in flood
    typhoon = t("Defesa Civil alerta para tufão na China", "pt")
    assert "disasters" in typhoon and "security" not in typhoon
    assert "rights" in t("Uyghur forced labour found in supply chains", "en")


def test_romanian_polish_farsi_hindi_thai():
    assert "diplomacy" in t("Summitul BRICS de la New Delhi", "ro")
    assert "security" in t("Chińskie okręty wojenne w pobliżu Tajwanu", "pl")
    assert "energy" in t("صادرات نفت ایران به چین", "fa")
    assert "security" in t("गलवान में चीनी सेना की गतिविधि", "hi")
    assert "disasters" in t("น้ำท่วมหนักในจีนตอนใต้", "th")


def test_acronyms_are_case_sensitive():
    assert "technology" in t("AI chips drive the rally", "en")
    assert "technology" not in t("Ai que saudade da praia", "pt")


def test_the_whole_body_counts():
    """Themes version 2026.09.4: the whole body is read, not only the first 800 characters, which
    favored whatever an article names early."""
    head = "The delegation discussed the summit agenda. "
    assert "diplomacy" in t("A visit", "en", body=head)
    assert "diplomacy" in t("A visit", "en", body="x " * 1000 + "summit")


def test_ensure_tags_and_retags_on_version_change(tmp_path, monkeypatch):
    conn = store.connect(tmp_path / "th.db")
    aid = store.insert_discovered(conn, {"url": "https://x.test/t", "outlet_id": "o", "country": "ITA", "language": "it",
                                         "title": "Cina, vertice con l'Unione europea", "status": "fetched", "gate_relevant": 1})
    conn.commit()
    assert themes.ensure(conn) == 1
    assert themes.themes_of(store.get_article(conn, aid)["themes"]) == ["diplomacy"]
    assert themes.ensure(conn) == 0
    monkeypatch.setattr(themes, "version", lambda: "next")
    assert themes.ensure(conn) == 1


def test_every_themes_version_has_a_dated_history_entry(tmp_path):
    """The theme timeline marks each themes version the way ruleset and gate changes are marked on the
    map timeline, so a version bump in themes.yaml without a HISTORY entry fails here."""
    from pipeline import export
    hist = themes.changes()
    versions = [h["version"] for h in hist]
    assert themes.version() == versions[-1]
    assert len(set(versions)) == len(versions)
    assert [h["date"] for h in hist] == sorted(h["date"] for h in hist)
    assert all(h["change"] and "\u2014" not in h["change"] and "\u2013" not in h["change"] for h in hist)
    switch = next(h for h in hist if h["version"] == "2026.09.4")
    assert switch["date"] == "2026-09-15" and "800" in switch["change"]
    conn = store.connect(tmp_path / "m.db")
    meta = export.build_meta(conn, [], [], export.build_latest(conn, [], [], population={}))
    assert meta["themes_changes"] == hist and meta["themes_version"] == themes.version()


# Tags record whether the body was read (since 2026-10-14): an article tagged before its body was
# fetched is tagged again once it arrives, and a body-read tag is never downgraded.

def _relevant(conn, url, title, lang="en", country="ITA", status="fetched", cat=None):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": "o", "country": country, "language": lang,
                                         "title": title, "status": status, "gate_relevant": 1,
                                         "published_at": "2026-09-10T08:00:00+00:00"})
    conn.execute("UPDATE articles SET discovered_at='2026-09-10T09:00:00+00:00' WHERE id=?", (aid,))
    if cat:
        store.insert_classification(conn, aid, "rules", cat, 1.0)
        conn.execute("UPDATE articles SET status='classified' WHERE id=?", (aid,))
    conn.commit()
    return aid


def _stored(conn, aid):
    import json
    return json.loads(store.get_article(conn, aid)["themes"])


def test_an_article_tagged_without_its_body_is_tagged_again_when_the_body_arrives(tmp_path, monkeypatch):
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "b.db")
    aid = _relevant(conn, "https://x.test/late", "A visit to Lima")
    assert themes.ensure(conn) == 1
    assert _stored(conn, aid) == {"v": themes.version(), "t": ["other"], "b": 0}
    assert themes.ensure(conn) == 0, "no body yet: nothing to read again"
    store.save_body(store.get_article(conn, aid)["url_hash"], "The delegation discussed the summit agenda and tariffs.")
    stats = {}
    assert themes.ensure(conn, stats=stats) == 1
    st = _stored(conn, aid)
    assert st["b"] == 1 and set(st["t"]) >= {"diplomacy", "economy"} and "other" not in st["t"]
    assert stats == {"tagged": 1, "with_body": 1, "retagged_with_body": 1, "body_retags_left": 0, "complete": True}
    assert themes.ensure(conn) == 0


def test_a_body_read_tag_is_never_downgraded(tmp_path, monkeypatch):
    import json
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "b.db")
    aid = _relevant(conn, "https://x.test/read", "A visit to Lima")
    h = store.get_article(conn, aid)["url_hash"]
    store.save_body(h, "The delegation discussed the summit agenda.")
    assert themes.ensure(conn) == 1
    before = store.get_article(conn, aid)["themes"]
    assert json.loads(before) == {"v": themes.version(), "t": ["diplomacy"], "b": 1}
    store.body_path(h).unlink()                       # the body file goes away ...
    assert themes.ensure(conn) == 0
    store.save_body(h, "")                            # ... or is empty
    assert themes.ensure(conn) == 0
    assert store.get_article(conn, aid)["themes"] == before


def test_old_format_tags_without_b_count_as_title_only(tmp_path, monkeypatch):
    import json
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "b.db")
    v = themes.version()
    with_body = _relevant(conn, "https://x.test/old1", "A visit to Lima")
    no_body = _relevant(conn, "https://x.test/old2", "Summit in Lima")
    old = {with_body: json.dumps({"v": v, "t": ["other"]}), no_body: json.dumps({"v": v, "t": ["diplomacy"]})}
    for aid, s in old.items():
        conn.execute("UPDATE articles SET themes=? WHERE id=?", (s, aid))
    conn.commit()
    assert themes.themes_of(old[no_body]) == ["diplomacy"]
    store.save_body(store.get_article(conn, with_body)["url_hash"], "Rare earth export curbs.")
    assert themes.ensure(conn) == 1, "only the old tag whose body now exists is made again"
    assert _stored(conn, with_body) == {"v": v, "t": ["economy", "energy"], "b": 1}
    assert store.get_article(conn, no_body)["themes"] == old[no_body], "no body: the old tag is left as it is"
    # Unreadable or foreign stored values are tagged afresh, and themes_of reads them as Other.
    for bad in ("not json", "[1, 2]", "null"):
        conn.execute("UPDATE articles SET themes=? WHERE id=?", (bad, no_body))
        assert themes.themes_of(bad) == ["other"]
        assert themes.ensure(conn) == 1 and _stored(conn, no_body)["b"] == 0


def test_the_body_retag_stops_at_its_deadline_but_missing_tags_are_always_made(tmp_path, monkeypatch):
    import time
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "b.db")
    first = _relevant(conn, "https://x.test/d1", "A visit to Lima")
    assert themes.ensure(conn) == 1
    store.save_body(store.get_article(conn, first)["url_hash"], "The summit agenda.")
    second = _relevant(conn, "https://x.test/d2", "Summit in Lima")
    stats = {}
    assert themes.ensure(conn, body_deadline=time.monotonic() - 1, stats=stats) == 1
    assert _stored(conn, second)["t"] == ["diplomacy"] and _stored(conn, first)["b"] == 0
    assert stats["body_retags_left"] == 1 and stats["complete"] is False
    # language_retag clears the column to have an article tagged again: that is a missing tag, always made.
    conn.execute("UPDATE articles SET themes=NULL WHERE id=?", (second,))
    conn.commit()
    assert themes.ensure(conn, body_deadline=time.monotonic() - 1) == 1 and _stored(conn, second)["b"] == 0
    assert themes.ensure(conn) == 1 and _stored(conn, first) == {"v": themes.version(), "t": ["diplomacy"], "b": 1}


def test_the_export_is_unchanged_for_articles_whose_tags_do_not_change(tmp_path, monkeypatch):
    """Adding b to a stored tag, or reading a body that adds no theme, changes nothing in the daily files
    or the article lists; a body that adds a theme changes only that article."""
    import json
    from pipeline import config, export
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "e.db")
    same = _relevant(conn, "https://x.test/e1", "Cina, vertice a Pechino", lang="it", cat="A")
    late = _relevant(conn, "https://x.test/e2", "Cina, un ristorante a Roma", lang="it", cat="C")
    v = themes.version()
    for aid in (same, late):   # tags in the format stored before b existed
        conn.execute("UPDATE articles SET themes=? WHERE id=?",
                     (json.dumps({"v": v, "t": themes.tag(store.get_article(conn, aid)["title"], "", "", "it")}), aid))
    conn.commit()
    export.rebuild_rollups(conn, [])
    daily0, arts0 = export.build_daily(conn), export.build_articles(conn)
    store.save_body(store.get_article(conn, same)["url_hash"], "Un incontro tra i due leader.")
    assert themes.ensure(conn) == 1 and _stored(conn, same) == {"v": v, "t": ["diplomacy"], "b": 1}
    conn.execute("UPDATE articles SET themes=? WHERE id=?", (json.dumps({"v": v, "t": ["other"], "b": 0}), late))
    conn.commit()
    assert export.build_daily(conn) == daily0 and export.build_articles(conn) == arts0
    store.save_body(store.get_article(conn, late)["url_hash"], "Il turismo cinese in Italia.")
    assert themes.ensure(conn) == 1
    day = export.build_daily(conn)["2026-09"]["days"]["2026-09-10"]["themes"]["ITA"]
    day0 = daily0["2026-09"]["days"]["2026-09-10"]["themes"]["ITA"]
    assert day["diplomacy"] == day0["diplomacy"] and "other" not in day and day["culture"] == day0["other"]
    arts = {a["id"]: a for a in export.build_articles(conn)["ITA"]}
    arts0 = {a["id"]: a for a in arts0["ITA"]}
    assert arts[same] == arts0[same] and arts[late]["themes"] == ["culture"]
