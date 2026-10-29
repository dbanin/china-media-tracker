"""Themes version 2026.10.1 (2026-10-28): three themes (leaving the Belt and Road, Chinese overcapacity, the
EU and China trade war) with exclusions and proximity rules, a version re-tag bounded by the export's time
budget, and the crossing of the three themes with outlet leaning (outlets.json theme_cross)."""
import json
import time

import pytest

from pipeline import store, themes

NEW = ["bri_withdrawal", "overcapacity", "eu_china_trade"]


def has(theme, text, lang):
    return theme in themes.tag(text, "", "", lang)


# (theme, language, text, expected). Hits and misses per language, on synthetic sentences.
CASES = [
    # Leaving the Belt and Road: a name of the initiative with a word of leaving within 80 characters.
    ("bri_withdrawal", "en", "Panama left the Belt and Road Initiative in early 2025.", True),
    ("bri_withdrawal", "en", "Italy intends to exit the BRI, the Belt and Road Initiative.", True),
    ("bri_withdrawal", "en", "The Belt and Road Initiative turned ten, and I headed back out on the road.", False),
    ("bri_withdrawal", "en", "Bank BRI said it would withdraw its offer.", False),   # an acronym needs its full name
    ("bri_withdrawal", "en", "Panama left the talks early. " + "The weather was mild and the city was quiet. " * 3 +
     "Later the Belt and Road forum opened.", False),                                   # farther than 80 characters
    ("bri_withdrawal", "es", "Colombia evalúa la salida de la Franja y la Ruta", True),
    ("bri_withdrawal", "es", "Colombia firmó su adhesión a la Franja y la Ruta", False),
    ("bri_withdrawal", "it", "L'Italia annuncia l'uscita dalla Via della Seta", True),
    ("bri_withdrawal", "it", "La Via della Seta compie dieci anni", False),
    ("bri_withdrawal", "ru", "Италия объявила о выходе из инициативы «Пояс и путь»", True),
    ("bri_withdrawal", "ru", "Форум «Пояс и путь» открылся в Пекине", False),
    ("bri_withdrawal", "ko", "이탈리아, 일대일로 탈퇴 공식 통보", True),
    ("bri_withdrawal", "ko", "일대일로 정상포럼 불참", False),                             # skipping a forum is not leaving
    ("bri_withdrawal", "ar", "انسحاب إيطاليا من مبادرة الحزام والطريق", True),
    ("bri_withdrawal", "ar", "منتدى الحزام والطريق في بكين", False),
    # Chinese overcapacity and dumping.
    ("overcapacity", "en", "Beijing rejects Western claims of overcapacity in electric cars", True),
    ("overcapacity", "en", "Chinese mills are dumping steel on European markets", True),
    ("overcapacity", "en", "India extends anti-dumping duties on Chinese steel", True),
    ("overcapacity", "en", "Cheaper Chinese vehicles flood the European market", True),
    ("overcapacity", "en", "Djokovic dumped out of Shanghai Masters by Hurkacz", False),
    ("overcapacity", "en", "Chinese officials condemned the dumping of Fukushima water into the sea", False),
    ("overcapacity", "en", "China imposes anti-dumping duties on brandy from the European Union, Chinese ministry says", False),
    ("overcapacity", "en", "Floods hit southern China", False),
    ("overcapacity", "es", "La sobrecapacidad china preocupa a Bruselas", True),
    ("overcapacity", "es", "China y España firman un acuerdo agrícola", False),
    ("overcapacity", "de", "Brüssel warnt vor Überkapazitäten in China", True),
    ("overcapacity", "de", "Peking empfängt den Kanzler", False),
    ("overcapacity", "ru", "ЕЭК продлила антидемпинговую пошлину на трубы из Китая", True),
    ("overcapacity", "ru", "Китай ввел антидемпинговую пошлину на японский химикат", False),
    ("overcapacity", "ko", "中 철강 공급과잉에 韓 시장 압박", True),
    ("overcapacity", "ko", "메모리 반도체 공급과잉 우려", False),                         # a glut with no China word near it
    ("overcapacity", "ko", "정호영의 밀어내기 득점으로 역전", False),
    ("overcapacity", "tr", "Çin menşeli güneş paneli ithalatına damping önlemi", True),
    # The EU and China trade war.
    ("eu_china_trade", "en", "The EU imposed tariffs on Chinese electric vehicles", True),
    ("eu_china_trade", "en", "The US imposed tariffs on Chinese electric vehicles", False),
    ("eu_china_trade", "en", "EU and China seek to avert a trade war", True),
    ("eu_china_trade", "en", "The US-China trade war hurts EU exporters", False),
    ("eu_china_trade", "en", "China imposes anti-dumping duties on French brandy", True),
    ("eu_china_trade", "en", "China puts tariffs on Canadian pork", False),
    ("eu_china_trade", "en", "China's rare earth export controls hit EU carmakers", True),
    ("eu_china_trade", "en", "Australia opens a rare earth mine", False),
    ("eu_china_trade", "es", "La UE y China buscan evitar una guerra comercial", True),
    ("eu_china_trade", "es", "China y EU mantienen una tregua tras una guerra comercial", False),   # EU is Estados Unidos
    ("eu_china_trade", "de", "Brüssel verhängt Strafzölle auf E-Autos aus China", True),
    ("eu_china_trade", "de", "E-Autos aus China werden in Brüssel ausgestellt", False),
    ("eu_china_trade", "ko", "EU, 중국산 전기차에 상계관세 부과", True),
    ("eu_china_trade", "ko", "중국산 전기차 판매 급증", False),
]


@pytest.mark.parametrize("theme,lang,text,expected", CASES)
def test_terms_hit_and_miss_per_language(theme, lang, text, expected):
    assert has(theme, text, lang) is expected, (theme, lang, text)


def test_the_new_themes_are_read_in_the_body_and_the_summary_too():
    assert "overcapacity" in themes.tag("A visit", "", "x " * 2000 + "Beijing denies overcapacity", "en")
    assert "eu_china_trade" in themes.tag("Talks", "<p>The EU and China seek to avert a trade war</p>", "", "en")


def test_the_version_bump():
    assert themes.version() == "2026.10.1"
    last = themes.changes()[-1]
    assert last["version"] == "2026.10.1" and last["date"] == "2026-10-28" and last["added"] == NEW
    ids = [c["id"] for c in themes.catalog()]
    assert ids[-4:] == NEW + ["other"]
    assert themes.cross_themes() == NEW
    # The eleven earlier themes keep plain terms, so their tags cannot change with this version.
    data = themes.load()
    assert all(not (t.get("exclude") or t.get("near")) for t in data["themes"] if t["id"] not in NEW)
    assert len(data["themes"]) == 14


def test_every_new_term_list_covers_only_the_theme_languages():
    langs = {l for t in themes.load()["themes"] if t["id"] not in NEW for l in t["terms"]}
    for t in themes.load()["themes"]:
        if t["id"] in NEW:
            for terms in themes._term_lists(t):
                assert set(terms) <= langs | {"all"}, (t["id"], set(terms) - langs)


def _article(conn, url, title, outlet="o1", cat=None, status="fetched"):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": "ITA", "language": "en", "title": title,
                                         "status": status, "gate_relevant": 1, "published_at": "2026-10-20T08:00:00+00:00"})
    if cat:
        store.insert_classification(conn, aid, "rules", cat, 1.0)
        conn.execute("UPDATE articles SET status='classified' WHERE id=?", (aid,))
    conn.commit()
    return aid


def test_a_version_retag_stops_at_the_deadline_and_resumes_newest_first(tmp_path, monkeypatch):
    from pipeline import config
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "v.db")
    old_id = _article(conn, "https://x.test/1", "Beijing denies overcapacity")
    new_id = _article(conn, "https://x.test/2", "EU and China seek to avert a trade war")
    for aid in (old_id, new_id):   # tags stored under the version before, which had no overcapacity theme
        conn.execute("UPDATE articles SET themes=? WHERE id=?", (json.dumps({"v": "2026.09.4", "t": ["other"], "b": 0}), aid))
    fresh = _article(conn, "https://x.test/3", "Summit in Lima")   # untagged: always tagged
    stats = {}
    assert themes.ensure(conn, body_deadline=time.monotonic() - 1, stats=stats) == 1
    assert stats["version_retags_left"] == 2 and stats["complete"] is False
    assert json.loads(store.get_article(conn, fresh)["themes"])["v"] == "2026.10.1"
    assert themes.themes_of(store.get_article(conn, old_id)["themes"]) == ["other"], "the older tag counts as it stands"
    p = themes.progress(conn)
    assert p == {"version": "2026.10.1", "since": "2026-10-28", "added": NEW, "relevant": 3, "current": 1, "complete": False}
    stats = {}
    assert themes.ensure(conn, stats=stats) == 2 and stats["retagged_version"] == 2 and stats["complete"] is True
    assert "overcapacity" in themes.themes_of(store.get_article(conn, old_id)["themes"])
    assert "eu_china_trade" in themes.themes_of(store.get_article(conn, new_id)["themes"])
    assert themes.progress(conn)["complete"] is True


def test_the_crossing_counts_china_coverage_per_outlet(tmp_path, monkeypatch):
    from pipeline import config, export
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "c.db")
    _article(conn, "https://x.test/a", "Beijing denies overcapacity", cat="A")
    _article(conn, "https://x.test/b", "EU tariffs on Chinese electric vehicles and overcapacity", cat="C")
    _article(conn, "https://x.test/c", "Summit in Lima", cat="B")
    _article(conn, "https://x.test/d", "Overcapacity, but not about China", cat="not_relevant")   # not China coverage
    _article(conn, "https://x.test/e", "Panama left the Belt and Road", status="awaiting_llm")     # a candidate counts
    _article(conn, "https://x.test/f", "Overcapacity at the other outlet", outlet="o2", cat="A")
    themes.ensure(conn)
    cross = themes.cross_counts(export.article_rows(conn))
    assert cross["o1"] == {"china": 4, "A": 1, "cur": 4,
                           "t": {"bri_withdrawal": [1, 0], "overcapacity": [2, 1], "eu_china_trade": [1, 0]}}
    assert cross["o2"]["t"]["overcapacity"] == [1, 1]
    outlets = [{"id": "o1", "name": "One", "country": "ITA", "language": "en", "tier": "national", "active": True},
               {"id": "o3", "name": "Three", "country": "ITA", "language": "en", "tier": "national", "active": True}]
    out = {o["id"]: o for o in export.build_outlets(conn, outlets)["outlets"]}
    assert out["o1"]["theme_cross"] == cross["o1"] and out["o3"]["theme_cross"] is None
    meta = export.build_meta(conn, outlets, [], export.build_latest(conn, outlets, [], population={}))
    assert meta["themes_cross"] == NEW and meta["themes_progress"]["complete"] is True
