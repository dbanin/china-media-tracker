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
    ok, _ = gate.check("Markets", "<p>Shares in <b>Huawei</b> suppliers rose</p>", "en")
    assert ok
