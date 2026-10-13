from pipeline import gate


def test_english_hits():
    ok, terms = gate.check("Xi Jinping meets Putin in Beijing", "", "en")
    assert ok and "Beijing" in terms


def test_italian_hits():
    ok, _ = gate.check("La Cina risponde ai dazi americani", "", "it")
    assert ok


def test_french_hits():
    ok, _ = gate.check("Pékin réagit", "Les autorités chinoises", "fr")
    assert ok


def test_no_word_boundary_language():
    ok, _ = gate.check("中国が新型ミサイルを発射", "", "ja")
    assert ok


def test_negative():
    ok, _ = gate.check("Brisbane storms cause flooding", "Residents advised to stay home", "en")
    assert not ok


def test_short_acronyms_case_sensitive():
    ok, _ = gate.check("Le brie de Meaux", "", "fr")
    assert not ok
    ok, _ = gate.check("BRI projects stall", "", "en")
    assert ok


def test_summary_html_stripped():
    ok, _ = gate.check("Markets", "<p>Shares in <b>Shenzhen</b> suppliers rose</p>", "en")
    assert ok


def test_spanish_sino_is_not_china():
    ok, terms = gate.check("No es una amenaza, sino una oportunidad", "", "es")
    assert not ok and not terms
    ok, terms = gate.check("Sino-American trade talks resume", "", "en")
    assert ok and "Sino-" in terms


def test_weak_terms_need_a_second_term():
    ok, terms = gate.check("TikTok adds a new video feature", "", "en")
    assert not ok and terms == ["TikTok"]
    ok, _ = gate.check("TikTok faces a ban as Beijing objects", "", "en")
    assert ok
    ok, _ = gate.check("Detectives reviewed CCTV after the robbery", "", "en")
    assert not ok


def test_home_territory_terms_ignored_for_local_outlets():
    ok, _ = gate.check("Hong Kong bus crash injures 12", "", "en", "HKG")
    assert not ok
    ok, _ = gate.check("Hong Kong bus crash injures 12", "", "en", "GBR")
    assert ok
    ok, _ = gate.check("Macau casino revenue rises as mainland Chinese visitors return", "", "en", "MAC")
    assert ok


def test_count_reports_occurrences_and_headline():
    distinct, total, in_title = gate.count("China warns Nepal", "China said on Monday. China's ministry added. Nepal thanked Beijing.", "en")
    assert (distinct, total, in_title) == (2, 4, True)


def test_sports_section_needs_more_than_one_summary_term():
    sport = "https://7news.com.au/sport/tennis/de-minaur-through-to-the-quarter-finals-c-1"
    news = "https://7news.com.au/news/world/de-minaur-through-to-the-quarter-finals-c-1"
    title, summary = "De Minaur through to the quarter-finals", "The Australian beat Zverev at the China Open."
    ok, terms = gate.check(title, summary, "en", "AUS", url=sport)
    assert not ok and gate.SPORTS_SECTION_MARK in terms and "China" in terms
    # The same item outside a sports section, or with no url, is admitted as before.
    assert gate.check(title, summary, "en", "AUS", url=news)[0]
    assert gate.check(title, summary, "en", "AUS")[0]
    # A term in the headline, two distinct terms, or a state media name still admits it.
    assert gate.check("China Open: De Minaur through", "The Australian beat Zverev.", "en", "AUS", url=sport)[0]
    assert gate.check(title, "He beat Zverev in Beijing at the China Open.", "en", "AUS", url=sport)[0]
    assert gate.check(title, "The Australian beat Zverev, Xinhua reported.", "en", "AUS", url=sport)[0]


def test_sports_section_reads_the_path_not_the_slug():
    assert gate.sports_section("https://www.donga.com/news/Sports/article/all/20260926/134734152/1")
    assert gate.sports_section("https://www.abc.com.py/deportes/polideportivo/2026/09/19/los-juegos/")
    assert not gate.sports_section("https://example.com/news/world/china-sports-diplomacy")
    assert not gate.sports_section("https://example.com/lifestyle/travel/a-bridge-in-china")
    assert not gate.sports_section(None)


def test_sports_section_one_repeated_summary_term_is_not_two():
    # "China" and "china" are separate list entries over the same words; one span counts once.
    ok, _ = gate.check("Korea beat the hosts", "China fell to Korea. China's coach resigned.", "en",
                       url="https://www.koreatimes.co.kr/sports/20261004/korea-beat-the-hosts")
    assert not ok


def test_sports_sections_block_is_not_a_language():
    assert "sports_sections" not in gate.supported_languages()


# The title_only block (keywords.yaml), read only when the gate has nothing but a title.

def test_title_only_terms_admit_a_bare_title_only_when_asked():
    title = "GAC Unveils New Brand, Services and Products at 2026 Paris Motor Show"
    assert gate.check(title, "", "en", "USA") == (False, [])
    ok, terms = gate.check(title, "", "en", "USA", title_only=True)
    assert ok and terms == ["title_only:GAC"]
    ok, terms = gate.check("Quzhou commemorates 2,577th anniversary of Confucius' birth", "", "en", "USA", title_only=True)
    assert ok and "title_only:Quzhou" in terms
    assert gate.check("2026 Cross-Strait (Kunshan) Mid-Autumn Lantern Festival Opens", "", "en", "LAO", title_only=True)[0]


def test_title_only_terms_are_not_read_in_the_summary():
    assert not gate.check("Mining machinery business grows", "Zoomlion and Quzhou partners", "en", "USA",
                          title_only=True)[0]


def test_title_only_leaves_out_product_brands_and_keeps_weak_terms_weak():
    assert not gate.check("Hisense Brings AI Companion Suite to Life with a Social Kitchen", "", "en", "USA",
                          title_only=True)[0]
    assert not gate.check("Geely Galaxy M8 specifications revealed", "", "en", "KAZ", title_only=True)[0]
    assert not gate.check("Huawei launches the Mate 90 series", "", "en", "ZAF", title_only=True)[0]


def test_title_only_language_lists():
    assert gate.check("Perú muestra los límites de la ofensiva antichina de Washington", "", "es", "ARG",
                      title_only=True)[0]
    assert gate.check("Pola veka od smrti Mao Cedunga: Njegova senka i dalje se nadvija", "", "sr", "SRB",
                      title_only=True)[0]
    # Kinu is China in the accusative and also "the cinema": capitalised only, as Kina is.
    assert gate.check("POKLON ZA KINU Kako su delovi za F-35 završili u Hong Kongu", "", "sr", "SRB", title_only=True)[0]
    assert gate.check("Irski ministar: vrata za Rusiju i Kinu", "", "sr", "SRB", title_only=True)[0]
    assert not gate.check("Idemo večeras u kinu na novi film", "", "sr", "SRB", title_only=True)[0]
    # The Serbian list is not read for Spanish.
    assert not gate.check("Mao Cedung", "", "es", "ARG", title_only=True)[0]


def test_title_only_home_terms_are_ignored_for_local_outlets():
    title = "Community Creates Mega Artwork for HKTDC 60th Anniversary"
    assert not gate.check(title, "", "en", "HKG", title_only=True)[0]
    assert gate.check(title, "", "en", "IDN", title_only=True)[0]


def test_title_only_term_in_a_sports_path_is_in_the_headline():
    ok, terms = gate.check("Si Đinping gledao finale", "", "sr", "SRB",
                           url="https://www.telegraf.rs/sport/tenis/4412441-si-dinping", title_only=True)
    assert ok and gate.SPORTS_SECTION_MARK not in terms


def test_title_only_block_is_not_a_language():
    assert "title_only" not in gate.supported_languages()
    # Nor does it reach the main lists: a summary naming a title_only term alone admits nothing.
    assert not gate.check("Plant opens", "A new plant in Quzhou for GAC", "en", "USA")[0]


# The main Serbian list carries the other cases of Kina, kineski, Peking and Tajvan, Latin and
# Cyrillic, and Xi Jinping and Mao as Serbian writes them (gate 2026.10.1).
def test_serbian_case_forms_admit_on_the_main_list():
    assert gate.check("POKLON ZA KINU Kako su delovi za F-35 završili u Hong Kongu", "", "sr", "SRB")[0]
    assert gate.check("Irski ministar: vrata za Rusiju i Kinu", "", "sr", "MNE")[0]
    assert gate.check("Evropska industrija traži hitnu akciju EU",
                      "Udruženja traže mere protiv kineske trgovinske politike.", "sr", "SRB")[0]
    assert gate.check("Bivši gradonačelnik osuđen", "Vuhana, kineskog grada u kojem je izbila epidemija", "sr", "SRB")[0]
    assert gate.check("Trgovina sa Kinom raste", "", "sr", "SRB")[0]
    assert gate.check("Од Кине до Европе за 15 дана Средњим коридором", "", "sr", "SRB")[0]
    assert gate.check("Вучић разговарао са кинеским амбасадором", "", "sr", "SRB")[0]
    assert gate.check("Napetosti oko Tajvana", "", "sr", "MNE")[0]
    assert gate.check("Vučić se sastao sa Si Đinpingom", "", "sr", "SRB")[0]
    assert gate.check("Пола века од смрти Мао Цедунга", "", "sr", "SRB")[0]


def test_serbian_cinema_cases_are_not_china():
    # Kinu and Kinom, Кину and Кином are also the accusative and instrumental of kino, a cinema.
    assert not gate.check("Idemo večeras u kinu na novi film", "", "sr", "SRB")[0]
    assert not gate.check("Premijera filma", "Gužva pred kinom u centru grada", "sr", "MNE")[0]
    assert not gate.check("Сутра у кину нова домаћа комедија", "Пред кином ће бити и концерт", "sr", "SRB")[0]
    assert not gate.check("Program kina za vikend", "Repertoar kine i kini", "sr", "SRB")[0]


def test_serbian_forms_left_out_of_the_main_list():
    # Mostly the Beijing tennis tournament, the Shanghai Masters and athletes on stored items.
    assert not gate.check("Đoković igra za finale Pekinga", "", "sr", "SRB")[0]
    assert not gate.check("Velika iznenađenja u Šangaju", "", "sr", "SRB")[0]
    assert not gate.check("Kineskinja napravila čudo na US Openu", "", "sr", "SRB")[0]


# The Croatian and Bosnian lists carry the same case forms, Xi Jinping and Mao as each writes them,
# and for Bosnian the Cyrillic forms too (gate 2026.10.1).
def test_croatian_and_bosnian_case_forms_admit_on_the_main_list():
    assert gate.check("Bezos: Moramo pobijediti Kinu u svemirskoj utrci", "", "hr", "HRV")[0]
    assert gate.check("SAD upozorile Kinu zbog aktivnosti", "", "bs", "BIH")[0]
    assert gate.check("Trgovina s Kinom raste", "", "hr", "HRV")[0]
    assert gate.check("Spiegel - Bivši obavještajac održavao kontakte s kineskim generalom", "", "bs", "BIH")[0]
    assert gate.check("Europska industrija", "suočena je sa sve snažnijom kineskom konkurencijom", "hr", "HRV")[0]
    assert gate.check("Napetosti oko Tajvana", "", "hr", "HRV")[0]
    assert gate.check("Sastanak sa Si Đinpingom", "", "hr", "HRV")[0]
    assert gate.check("Sastanak sa Si Đinpingom", "", "bs", "BIH")[0]
    assert gate.check("Pola stoljeća od smrti Mao Ce-tunga", "", "hr", "HRV")[0]
    assert gate.check("Pola stoljeća od smrti Mao Zedonga", "", "bs", "BIH")[0]
    assert gate.check("Од Кине до Европе за 15 дана", "", "bs", "BIH")[0]
    assert gate.check("Разговор са кинеским амбасадором", "", "bs", "BIH")[0]
    # Croatian is written in Latin script only; the Cyrillic forms are not on its list.
    assert not gate.check("Разговор са кинеским амбасадором", "", "hr", "HRV")[0]


def test_croatian_and_bosnian_cinema_cases_are_not_china():
    # capitalised is one list for every language, so the cinema guard on Kinu and Kinom holds here too.
    assert not gate.check("Idemo večeras u kinu na novi film", "", "hr", "HRV")[0]
    assert not gate.check("Premijera filma", "Gužva pred kinom u centru grada", "hr", "HRV")[0]
    assert not gate.check("Idemo večeras u kinu na novi film", "", "bs", "BIH")[0]
    assert not gate.check("Premijera filma", "Gužva pred kinom u centru grada", "bs", "BIH")[0]
    assert not gate.check("Сутра у кину нова комедија", "Пред кином ће бити и концерт", "bs", "BIH")[0]
    assert not gate.check("Program kina za vikend", "Repertoar kine i kini", "bs", "BIH")[0]
    assert gate.check("ZA KINU", "", "hr", "HRV")[0]


def test_croatian_and_bosnian_forms_left_out_of_the_main_list():
    # Mostly the Beijing and Shanghai tennis tournaments and athletes on stored hr and bs items.
    for lang, ctry in (("hr", "HRV"), ("bs", "BIH")):
        assert not gate.check("Vekić u šesnaestini finala Pekinga", "", lang, ctry)[0]
        assert not gate.check("Đoković ispao u prvom kolu Šangaja", "", lang, ctry)[0]
        assert not gate.check("Pavić i Arevalo ispali u Šangaju", "", lang, ctry)[0]
        assert not gate.check("Nevjerojatna Kineskinja (13) osvojila treće zlato", "", lang, ctry)[0]
        assert not gate.check("Kinezi se ne šale", "", lang, ctry)[0]
