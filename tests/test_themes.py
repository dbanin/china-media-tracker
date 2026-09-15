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


def test_body_head_counts_but_not_the_whole_body():
    head = "The delegation discussed the summit agenda. "
    assert "diplomacy" in t("A visit", "en", body=head)
    assert "diplomacy" not in t("A visit", "en", body="x " * 1000 + "summit")


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
