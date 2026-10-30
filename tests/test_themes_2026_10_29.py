"""Themes version 2026.10.2 (2026-10-29): the three themes of 2026.10.1 corrected after every one of their hits
in the snapshot and the runner's audit files was read. Fixtures are the misfires and misses found there
(headlines and sentences of the tracked outlets, shortened), plus the guarantees around them: the eleven
earlier themes untouched, the version and its history entry, and progress() naming the changed themes."""
import hashlib
import json

import pytest

from pipeline import themes

NEW = ["bri_withdrawal", "overcapacity", "eu_china_trade"]


def has(theme, text, lang):
    return theme in themes.tag(text, "", "", lang)


# (theme, language, text, expected); False rows were hits under 2026.10.1, True rows misses (or kept hits).
CASES = [
    # Overcapacity: Volkswagen's own European plants.
    ("overcapacity", "en", "Volkswagen's supervisory board approved a plan that will include cutting another 50,000 jobs "
     "in its attempt to counter painful tariffs, overcapacity and aggressive Chinese rivals.", False),
    ("overcapacity", "es", "El Consejo de Supervisión del Grupo Volkswagen aprueba el plan: existe una sobrecapacidad de "
     "producción de 500.000 vehículos en Europa.", False),
    ("overcapacity", "ko", "폭스바겐 차종 절반 가량 축소 중국차 공세·과잉설비 부담", False),
    ("overcapacity", "en", "Others point to Chinese government support for its domestic carmakers which many in Europe say "
     "has unfairly led to overcapacity in the global car industry.", True),
    # China's own anti-dumping cases, and other countries' cases.
    ("overcapacity", "en", "Last week, China launched an anti-dumping investigation into imports from the EU of "
     "p-nitrotoluene, a Chinese industry complaint said.", False),
    ("overcapacity", "es", "China abre una investigación 'antidumping' sobre un químico importado de la UE. El Ministerio "
     "de Comercio chino indicó", False),
    ("overcapacity", "pt", "O Japão avalia o impacto das tarifas antidumping impostas pela China às importações de "
     "diclorossilano, dizem as autoridades chinesas", False),
    ("overcapacity", "ro", "China deschide o investigație antidumping asupra importurilor de p-nitrotoluen din UE", False),
    ("overcapacity", "ru", "Пекин продлил антидемпинговые проверки в отношении США, сообщило Министерство коммерции "
     "Китайской Народной Республики", False),
    ("overcapacity", "ar", "بكين تفتح تحقيقاً بشأن إغراق السوق الصينية بصادرات كيميائية أوروبية", False),
    ("overcapacity", "ar", "منتجو الأرز يحذرون من انهيار الزراعة الوطنية بسبب إغراق السوق بالمستورد", False),
    ("overcapacity", "ar", "مخاوف أوروبية من إغراق السوق المحلية بسيارات صينية مدعومة", True),
    ("overcapacity", "vi", "Australia xem xét gia hạn thuế chống bán phá giá thép mạ kẽm từ Việt Nam", False),
    ("overcapacity", "vi", "Gia hạn thời gian trả lời câu hỏi điều tra chống bán phá giá thép từ Trung Quốc", True),
    ("overcapacity", "ko", "中, EU산 화학물질 반덤핑 조사…중국산 전기차 갈등 속", False),
    ("overcapacity", "ko", "무역위원회, 중국산 아연도금냉연에 최대 33.67％ 덤핑방지관세", True),
    # Other industries and other words.
    ("overcapacity", "pt", "o artista aposta em uma superprodução visual e tecnológica no show na China", False),
    ("overcapacity", "ms", "Dekad yang lalu telah membawa lambakan pilihan pada setiap peringkat harga, kata syarikat China", False),
    ("overcapacity", "ko", "딥시크, AI 모델 성능 높이고 비용 줄였다…미 빅테크에 ‘저가 공세’", False),
    ("overcapacity", "ko", "“중국산 저가 공세, 기술력으로 극복”⋯칠곡군, 말레이시아서 수출계약", True),
    ("overcapacity", "en", "China is letting a wave of cheap money flow across its borders", False),
    # Misses found in the audit files.
    ("overcapacity", "en", "Europe’s China shock is hollowing out industry and shifting politics to the right", True),
    ("overcapacity", "de", "Was der China-Schock für den industriellen Kern bedeutet", True),
    ("overcapacity", "es", "México impone cuota adicional a lonas chinas para toldos y carpas por dumping", True),
    ("overcapacity", "pt", "Governo identifica dumping chinês em dois insumos usados pela siderurgia", True),
    ("overcapacity", "it", "La chimica ha perso competitività, pesano energia e dumping Cina", True),
    ("overcapacity", "de", "welche Maßnahmen er gegen Chinas Dumpingpreise fordert", True),
    # The EU and China trade war: misses found in the audit files.
    ("eu_china_trade", "en", "EU urges UK to raise tariffs on Chinese cars to avoid ‘made in Europe’ barriers", True),
    ("eu_china_trade", "en", "EU Prepares Import Cap on Chinese Hybrid Cars to Balance Trade", True),
    ("eu_china_trade", "en", "China launches anti-dumping probe into EU chemical imports ahead of trade talks", True),
    ("eu_china_trade", "de", "Handel: China leitet Antidumping-Verfahren gegen EU-Chemieprodukt ein", True),
    ("eu_china_trade", "ko", "중국 정부가 유럽연합(EU)산 화학 중간체인 파라-나이트로톨루엔에 대한 반덤핑 조사를 시작했다", True),
    ("eu_china_trade", "en", "Beijing warns of retaliation if Europe imposes curbs on Chinese businesses, the European Union said", True),
    # ...and still not the United States, Canada or Spanish EU.
    ("eu_china_trade", "en", "Trump puts tariffs on Chinese drones; those produced by the US get a break", False),
    ("eu_china_trade", "es", "Los aranceles de EU a China entran en vigor", False),
    ("eu_china_trade", "en", "Trump will block certain Canadian dairy imports; the European Commission did not respond", False),
    ("eu_china_trade", "nl", "Het handelsconflict tussen de VS en China escaleerde, ook de EU kreeg heffingen", False),
]


@pytest.mark.parametrize("theme,lang,text,expected", CASES)
def test_corrections_found_in_the_data(theme, lang, text, expected):
    assert has(theme, text, lang) is expected, (theme, lang, text)


def test_the_eleven_earlier_themes_are_byte_for_byte_those_of_2026_09_4():
    # The definitions of the eleven earlier themes (and the language settings they read) hash the same as in
    # themes version 2026.09.4 and 2026.10.1; with the plain matcher unchanged, their tags cannot change.
    d = themes.load()
    old = [t for t in d["themes"] if t["id"] not in NEW]
    keys = {k: d.get(k) for k in ("language_aliases", "substring_languages", "other")}
    digest = hashlib.sha256(json.dumps([old, keys], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert len(old) == 11
    assert digest == "259aec0dcd0a35b9f8b96a1eca5da5b9586095b62ee8b00a105fc228b256b775"


def test_version_history_and_progress_name_the_changed_themes(tmp_path, monkeypatch):
    from pipeline import config, store
    assert themes.version() == "2026.10.2"
    last = themes.changes()[-1]
    assert last["version"] == "2026.10.2" and last["date"] == "2026-10-29"
    assert sorted(last["changed"]) == sorted(NEW) and "added" not in last
    assert "–" not in last["change"] and "—" not in last["change"]
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "p.db")
    p = themes.progress(conn)
    assert p["changed"] == last["changed"] and p["added"] == [] and p["relevant"] == 0 and p["complete"] is True


def test_every_rule_has_terms_and_lists_only_theme_languages():
    langs = {l for t in themes.load()["themes"] if t["id"] not in NEW for l in t["terms"]}
    for t in themes.load()["themes"]:
        for rule in t.get("near") or []:
            assert rule.get("terms") and int(rule["window"]) > 0, t["id"]
            for lst in [rule["terms"], rule.get("avoid") or {}, rule.get("exclude") or {}] + list(rule["context"]):
                assert set(lst) <= langs | {"all"}, (t["id"], set(lst) - langs)


def test_a_large_version_retag_runs_in_budgeted_slices_newest_first_and_completes(tmp_path, monkeypatch):
    # 1,200 articles stored under the version before, a clock that advances one second per reading, and a
    # budget of 500 seconds: three exports re-tag them, each newest first from where the last stopped; the
    # older tag counts as it stands until then, progress() follows, and nothing is tagged twice.
    from pipeline import config, store
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "big.db")
    old = json.dumps({"v": "2026.10.1", "t": ["economy"], "b": 0})
    ids = []
    for i in range(1200):
        aid = store.insert_discovered(conn, {"url": "https://x.test/%d" % i, "outlet_id": "o1", "country": "ITA",
                                             "language": "en", "title": "Beijing denies overcapacity %d" % i,
                                             "status": "fetched", "gate_relevant": 1,
                                             "published_at": "2026-10-20T08:00:00+00:00"})
        ids.append(aid)
    conn.execute("UPDATE articles SET themes=?", (old,))
    conn.commit()
    clock = {"t": 0.0}

    def tick():
        clock["t"] += 1.0
        return clock["t"]
    # The fake clock is given to the themes module alone (not to the time module every other library reads),
    # so the slices depend only on the number of readings, never on how loaded the machine is.
    import types
    monkeypatch.setattr(themes, "time", types.SimpleNamespace(monotonic=tick))
    done, lefts, first_slice = [], [], None
    for _ in range(3):
        stats = {}
        n = themes.ensure(conn, body_deadline=clock["t"] + 500, stats=stats)
        done.append(n)
        lefts.append(stats["version_retags_left"])
        p = themes.progress(conn)
        assert p["current"] == sum(done) and p["complete"] is (sum(done) == 1200)
        if first_slice is None:
            first_slice = {r["id"] for r in conn.execute("SELECT id, themes FROM articles")
                           if json.loads(r["themes"])["v"] == themes.version()}
    assert sum(done) == 1200 and done[0] > 0 and done[1] > 0 and lefts[-1] == 0, (done, lefts)
    tags = {r["id"]: json.loads(r["themes"]) for r in conn.execute("SELECT id, themes FROM articles")}
    assert all(t["v"] == themes.version() and "overcapacity" in t["t"] for t in tags.values())   # the stored older tags are replaced
    # Newest first: after the first slice the re-tagged ones are exactly the highest ids.
    assert first_slice == set(sorted(ids, reverse=True)[:done[0]]) and 0 < done[0] < 1200
    assert themes.ensure(conn, stats={}) == 0   # nothing left: a fourth export writes nothing


def test_partial_state_keeps_older_tags_counting_as_they_stand(tmp_path, monkeypatch):
    # During the re-tag, an article on the older version keeps its tags (the theme it had, or none), so the
    # daily theme counts mix versions until complete; progress() says so and the site prints C.themesRetagNote.
    from pipeline import config, store
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn = store.connect(tmp_path / "part.db")
    a = store.insert_discovered(conn, {"url": "https://x.test/a", "outlet_id": "o1", "country": "ITA", "language": "en",
                                       "title": "Volkswagen battles overcapacity in its European plants", "status": "fetched",
                                       "gate_relevant": 1, "published_at": "2026-10-01T08:00:00+00:00"})
    conn.execute("UPDATE articles SET themes=? WHERE id=?",
                 (json.dumps({"v": "2026.10.1", "t": ["overcapacity"], "b": 0}), a))
    conn.commit()
    themes.ensure(conn, body_deadline=0, stats={})   # deadline already passed: nothing re-read
    assert themes.themes_of(store.get_article(conn, a)["themes"]) == ["overcapacity"]
    assert themes.progress(conn)["complete"] is False
    themes.ensure(conn, stats={})
    assert "overcapacity" not in themes.themes_of(store.get_article(conn, a)["themes"])
    assert themes.progress(conn)["complete"] is True
