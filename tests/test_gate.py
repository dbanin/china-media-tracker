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
