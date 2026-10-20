"""One placement per underlying release per country for a shared widget, one page at two addresses and
the numbered parts of one story; language editions of one wire release as one underlying item
(pipeline/placements.py). Measured on 2026-10-17: the ten ACM mastheads' AAP release pages turned 135
state origin releases into 1,350 Australian placements, Adnkronos stored seven state origin releases
twice, Italpress's numbered photo parts were 225 of its 532 state origin placements, and Shanghai
Electric's nine PR Newswire editions were nine underlying items."""
import datetime as dt
import json

from pipeline import body_dups, config, copies, export, placements, spikes, store

ACM = ["au_canberratimes", "au_newcastleherald", "au_examiner"]
OUTLETS = [{"id": i, "name": i.split("_")[1].title(), "country": "AUS", "language": "en", "tier": "regional",
            "active": True, "feeds": ["https://%s.test/rss" % i],
            "section_feeds": [{"url": "https://%s.test/press-releases" % i, "kind": "press_release",
                               "section": "https://%s.test/press-releases" % i, "type": "aap_widget"}]}
           for i in ACM]
OUTLETS += [
    {"id": "au_abc", "name": "ABC", "country": "AUS", "language": "en", "tier": "public_broadcaster", "active": True,
     "feeds": ["https://abc.test/rss"]},
    {"id": "it_italpress", "name": "Italpress", "country": "ITA", "language": "it", "tier": "agency", "active": True,
     "feeds": ["https://italpress.test/rss"]},
    {"id": "it_adnkronos", "name": "Adnkronos", "country": "ITA", "language": "it", "tier": "agency", "active": True,
     "feeds": ["https://adnkronos.test/rss"]},
    {"id": "us_prnewswire", "name": "PR Newswire", "country": "USA", "language": "en", "tier": "distribution_wire",
     "active": True, "feeds": ["https://prn.test/rss"]},
]
COUNTRY = {o["id"]: o["country"] for o in OUTLETS}
AAP = "https://newsroom.aap.com.au/releases/%s?masthead=%s"
DAY = "2026-10-05"


def _add(conn, url, outlet, category="A", title=None, day=DAY, author=None, minute=0, human=None):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": COUNTRY[outlet], "language": "en",
                                         "title": title or ("China " + url), "author": author, "status": "fetched",
                                         "gate_relevant": 1})
    assert aid, url
    conn.execute("UPDATE articles SET discovered_at=?, status='classified' WHERE id=?",
                 ("%sT09:%02d:00+00:00" % (day, minute), aid))
    if category:
        cid = store.insert_classification(conn, aid, "rules", category, 1.0, route="wire_credit" if category == "A" else None)
        if human:
            store.insert_human_review(conn, aid, cid, human, "coder")
    return aid


def _db(tmp_path):
    conn = store.connect(tmp_path / "p.db")
    store.sync_outlets(conn, OUTLETS)
    return conn


def _aap(conn, releases=("rel1", "rel2"), category="A"):
    ids = {}
    for rk in releases:
        for o in ACM:
            ids[(rk, o)] = _add(conn, AAP % (rk, o.split("_")[1] + ".com.au"), o, category, title="Xinhua Silk Road: " + rk)
    return ids


def _yesterday():
    return (dt.date.fromisoformat(export._today()) - dt.timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# Shared widgets
# ---------------------------------------------------------------------------

def test_a_shared_widget_release_counts_once_per_country(tmp_path):
    conn = _db(tmp_path)
    ids = _aap(conn)
    _add(conn, "https://abc.test/1", "au_abc")
    export.rebuild_rollups(conn, OUTLETS)
    day = export.build_daily(conn, OUTLETS)["2026-10"]["days"][DAY]
    aus = day["countries"]["AUS"]
    assert aus["A"] == 3, "two releases on three mastheads are two placements, plus ABC's own"
    assert aus["cls"] == 3 and day["routes"]["AUS"] == {"wire_credit": 3}
    assert len(day["underlying_items"]["AUS"]["A"]) == 3
    # every copy is still stored and labelled
    assert conn.execute("SELECT COUNT(*) FROM classifications WHERE is_current=1").fetchone()[0] == 7
    rep = placements.representative_of(conn)
    assert sorted(rep) == sorted(i for k, i in ids.items() if k[1] != ACM[0]), "the first stored copy is counted"


def test_articles_outlets_audit_and_carrier_count_the_placement_once(tmp_path):
    conn = _db(tmp_path)
    day_releases = ("rel1", "rel2", "rel3")
    _aap(conn, day_releases)
    export.rebuild_rollups(conn, OUTLETS)
    arts = export.build_articles(conn, outlets=OUTLETS)["AUS"]
    assert len(arts) == len(day_releases)
    assert all(a["copies"] == 3 and a["shared_widget"] == "aap_release_widget" for a in arts)
    outs = {o["id"]: o for o in export.build_outlets(conn, OUTLETS)["outlets"]}
    assert sum(outs[i]["counts"]["A"] for i in ACM) == 3, "the outlets add up to their country"
    assert sum((outs[i]["not_counted"] or {}).get("A", 0) for i in ACM) == 6
    assert outs["au_abc"]["not_counted"] is None
    written = export.write_audit_files(conn, tmp_path / "audit")
    assert written[DAY] == 9, "the audit keeps every stored row"
    lines = [json.loads(x) for x in (tmp_path / "audit" / ("articles-%s.jsonl" % DAY)).read_text().splitlines()]
    assert sum(1 for x in lines if "counted_as" not in x) == 3
    carriers = export._country_top_carriers(conn, OUTLETS, "2026-09-01", "2026-12-31")
    top = carriers["AUS"]["top_carrier"]
    assert top["id"] == "aap_release_widget" and top["count"] == 3 and top["shared_widget"] is True
    assert top["name"] == "AAP press release pages of 3 ACM mastheads"
    assert top["network"] == "Australian Community Media"


def test_the_disclosure_counts_stored_and_counted_rows(tmp_path):
    conn = _db(tmp_path)
    _aap(conn)
    _aap(conn, releases=("rel9",), category="not_relevant")
    s = placements.summary(conn, OUTLETS)
    w = {x["id"]: x for x in s["shared_widgets"]}["aap_release_widget"]
    assert (w["stored_rows"], w["counted_placements"], w["rows_not_counted"]) == (9, 3, 6)
    assert (w["state_origin_rows"], w["state_origin_counted"]) == (6, 2)
    assert w["outlets"] == sorted(ACM) and w["outlets_showing"] == sorted(ACM)
    assert w["countries"]["AUS"]["state_origin_counted"] == 2
    assert w["network"] == "Australian Community Media" and w["note"]
    assert s["duplicate_placements"]["same_page"]["sets"] == 0
    for text in (w["note"], s["duplicate_placements"]["numbered_series"]["note"],
                 s["duplicate_placements"]["language_editions"]["note"]):
        assert chr(0x2014) not in text and chr(0x2013) not in text


def test_a_reviewed_copy_is_the_one_counted(tmp_path):
    conn = _db(tmp_path)
    _add(conn, AAP % ("relx", "canberratimes.com.au"), "au_canberratimes", "A")
    reviewed = _add(conn, AAP % ("relx", "examiner.com.au"), "au_examiner", "A", human="C")
    assert placements.shadowed(conn) == {reviewed - 1}
    export.rebuild_rollups(conn, OUTLETS)
    day = export.build_daily(conn, OUTLETS)["2026-10"]["days"][DAY]
    assert day["reviewed"]["AUS"]["C"] == 1 and day["countries"]["AUS"]["A"] == 1


# ---------------------------------------------------------------------------
# Country plus WLD equals Both, and the totals reconcile
# ---------------------------------------------------------------------------

def _mixed(tmp_path, day=DAY):
    conn = _db(tmp_path)
    for rk in ("r1", "r2"):
        for o in ACM:
            _add(conn, AAP % (rk, o), o, "A", title="CGTN " + rk, day=day)
    _add(conn, "https://abc.test/c", "au_abc", "C", day=day)
    for n in (1, 2, 3):
        _add(conn, "https://italpress.test/cina-mele-%d/" % n, "it_italpress", "A",
             title="Cina: raccolta delle mele (%d)" % n, day=day)
    _add(conn, "https://www.adnkronos.com/immediapress/globenewswire/cgtn-x_7thzQuDyXyCnbAQFBATs9v", "it_adnkronos", "A",
         title="CGTN x", day=day)
    _add(conn, "https://prn.test/a", "us_prnewswire", "A", title="Blokees Debuts 90+ New Products at Wonder Festival Shanghai", day=day)
    _add(conn, "https://prn.test/b", "us_prnewswire", "A", title="Blokees präsentiert über 90 neue Produkte auf dem Wonder Festival Shanghai", day=day, minute=5)
    _add(conn, "https://prn.test/c", "us_prnewswire", "B", title="Something else", day=day)
    conn.commit()
    return conn


def test_country_plus_wld_equals_both_and_the_totals_reconcile(tmp_path):
    conn = _mixed(tmp_path, _yesterday())
    roll = export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [], population={})
    tot = latest["totals"]["all_time"]
    countries = latest["countries"]
    assert countries["AUS"]["all_time"]["A"] == 2 and countries["ITA"]["all_time"]["A"] == 2
    assert countries["WLD"]["all_time"]["A"] == 2, "editions stay placements"
    assert countries["WLD"]["all_time"]["uniqA"] == 1, "but are one underlying item"
    for k in ("A", "B", "C", "china_total"):
        assert tot[k] == sum(c["all_time"][k] for c in countries.values()), k
    country_only = sum(c["all_time"]["A"] for iso, c in countries.items() if iso != "WLD")
    assert country_only + countries["WLD"]["all_time"]["A"] == tot["A"] == 6
    assert tot["share_a"] == round(6 / 8.0, 4)
    assert roll["distribution_wires"]["A"] == countries["WLD"]["all_time"]["A"] == export._wire_counts(conn, OUTLETS)["A"]
    # the daily files, the sparkline and the audit lines agree with latest
    daily = export.build_daily(conn, OUTLETS)
    series = export.build_global_series(daily)
    assert sum(d["A"] for d in series) == tot["A"] and sum(d["wld"]["A"] for d in series) == 2
    day = daily[_yesterday()[:7]]["days"][_yesterday()]
    assert sum(sum(r.values()) for r in day["routes"].values()) == tot["A"]
    export.write_audit_files(conn, tmp_path / "audit")
    lines = [json.loads(x) for p in sorted((tmp_path / "audit").glob("*.jsonl")) for x in p.read_text().splitlines()]
    assert sum(1 for x in lines if x["category"] == "A" and "counted_as" not in x) == tot["A"]
    arts = export.build_articles(conn, outlets=OUTLETS)
    assert sum(1 for rows in arts.values() for a in rows if a["category"] == "A") == tot["A"]
    findings_world = export._state_origin_by_country(conn, "2000-01-01", "9999-12-31")
    assert sum(findings_world.values()) == tot["A"]
    meta_routes = None
    try:
        meta_routes = export.build_meta(conn, OUTLETS, [], latest, export.build_outlets(conn, OUTLETS))
    except Exception:  # noqa: BLE001  build_meta reads files outside this test's database
        pass
    if meta_routes is not None and "route_signature_totals" in meta_routes:
        assert sum(meta_routes["route_signature_totals"].values()) == tot["A"]
    # the measures read the same placements
    rows = spikes.load_rows(conn, OUTLETS)
    assert sum(len(v) for v in rows.values()) == tot["A"]
    assert len(copies._current_copies(conn)) == tot["A"]


# ---------------------------------------------------------------------------
# One page at two addresses
# ---------------------------------------------------------------------------

BARE = "https://www.adnkronos.com/immediapress/china-eastern-airlines-lancia-lapp_7thzQuDyXyCnbAQFBATs9v"
DIST = "https://www.adnkronos.com/immediapress/globenewswire/china-eastern-airlines-lancia-lapp_7thzQuDyXyCnbAQFBATs9v"


def test_canonical_url_drops_the_distributor_segment():
    assert store.canonical_url(BARE) == store.canonical_url(DIST)
    assert store.url_hash(BARE) == store.url_hash(DIST)
    other = "https://www.adnkronos.com/immediapress/pr-newswire/another-release_3DhDj4A2yqzo3Y8yLKWTpI"
    assert store.url_hash(other) != store.url_hash(BARE)
    # only the Immediapress item pages, and only on Adnkronos
    assert store.canonical_url("https://www.adnkronos.com/immediapress/eng/") == "https://adnkronos.com/immediapress/eng"
    assert store.canonical_url("https://example.com/immediapress/x/y_7thzQuDyXyCnbAQFBATs9v").endswith("/x/y_7thzQuDyXyCnbAQFBATs9v")


def test_the_second_address_is_not_stored_again(tmp_path):
    conn = _db(tmp_path)
    _add(conn, DIST, "it_adnkronos")
    assert store.insert_discovered(conn, {"url": BARE, "outlet_id": "it_adnkronos", "country": "ITA", "language": "it",
                                          "title": "x", "gate_relevant": 1}) is None


def _legacy_pair(tmp_path):
    """Rows stored before the change: each address under the hash the old canonicaliser gave it."""
    conn = _db(tmp_path)
    a = _add(conn, BARE, "it_adnkronos")
    b = _add(conn, "https://www.adnkronos.com/immediapress/x-placeholder_3DhDj4A2yqzo3Y8yLKWTpI", "it_adnkronos")
    lone = _add(conn, "https://www.adnkronos.com/immediapress/y-placeholder_4xMywbZzv9QtrXXKkqvY2O", "it_adnkronos")
    import hashlib

    def old(u):
        return hashlib.sha256(u.replace("https://www.", "https://").encode("utf-8")).hexdigest()[:32]
    conn.execute("UPDATE articles SET url=?, url_hash=? WHERE id=?", (DIST, old(DIST), b))
    lone_url = "https://www.adnkronos.com/immediapress/globenewswire/lone_4xMywbZzv9QtrXXKkqvY2O"
    conn.execute("UPDATE articles SET url=?, url_hash=? WHERE id=?", (lone_url, old(lone_url), lone))
    conn.execute("DELETE FROM schema_migrations WHERE name='articles.url_hash.adnkronos_immediapress'")
    conn.commit()
    return conn, a, b, lone, lone_url


def test_stored_rows_are_corrected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BODIES_DIR", tmp_path / "bodies")
    conn, a, b, lone, lone_url = _legacy_pair(tmp_path)
    store.save_body(conn.execute("SELECT url_hash FROM articles WHERE id=?", (lone,)).fetchone()[0], "the body")
    conn.close()
    conn = store.connect(tmp_path / "p.db")   # the migration runs once
    h = dict(conn.execute("SELECT id, url_hash FROM articles").fetchall())
    assert h[lone] == store.url_hash(lone_url), "a row without a twin takes the new hash"
    assert store.load_body(h[lone]) == "the body", "and its body file goes with it"
    assert h[b] != store.url_hash(DIST) and h[a] == store.url_hash(BARE), "a twin keeps its own (the hash is unique)"
    assert store.insert_discovered(conn, {"url": lone_url, "outlet_id": "it_adnkronos", "country": "ITA",
                                          "language": "it", "title": "x", "gate_relevant": 1}) is None
    # the pair counts once
    assert placements.shadowed(conn) == {b}
    export.rebuild_rollups(conn, OUTLETS)
    day = export.build_daily(conn, OUTLETS)["2026-10"]["days"][DAY]
    assert day["countries"]["ITA"]["A"] == 2
    s = placements.summary(conn, OUTLETS)["duplicate_placements"]["same_page"]
    assert (s["sets"], s["stored_rows"], s["state_origin_rows"], s["state_origin_counted"]) == (1, 2, 2, 1)


# ---------------------------------------------------------------------------
# Numbered series
# ---------------------------------------------------------------------------

def test_series_base():
    assert placements.series_base("Cina: campi e coltivazioni nell'Heilongjiang (3)") == "cina: campi e coltivazioni nell'heilongjiang"
    assert placements.series_base("Cina, i vigili del fuoco soccorrono gli abitanti") is None
    assert placements.series_base("COMUNICADO: IAB Tech Lab presenta AAMP 3.0 (2)") == "comunicado: iab tech lab presenta aamp 3.0"


def test_the_parts_of_one_story_are_one_placement(tmp_path):
    conn = _db(tmp_path)
    for n in (3, 2, 1):
        _add(conn, "https://italpress.test/cina-campi-%d/" % n, "it_italpress", title="Cina: campi e coltivazioni (%d)" % n)
    _add(conn, "https://italpress.test/cina-campi-1-b/", "it_italpress", title="Cina: campi e coltivazioni (1)", day="2026-10-06")
    _add(conn, "https://adnkronos.test/cina-campi-2/", "it_adnkronos", title="Cina: campi e coltivazioni (2)")
    _add(conn, "https://italpress.test/solo-2/", "it_italpress", title="Cina: un'altra storia (2)")
    export.rebuild_rollups(conn, OUTLETS)
    days = export.build_daily(conn, OUTLETS)["2026-10"]["days"]
    assert days[DAY]["countries"]["ITA"]["A"] == 3, "one story, the same title at another outlet, a lone part"
    assert days["2026-10-06"]["countries"]["ITA"]["A"] == 1, "a part on another day is not merged"
    s = placements.summary(conn, OUTLETS)["duplicate_placements"]["numbered_series"]
    assert (s["sets"], s["stored_rows"], s["rows_not_counted"]) == (1, 3, 2)


# ---------------------------------------------------------------------------
# Language editions
# ---------------------------------------------------------------------------

def _rows(titles, author=None, start=0, step=1):
    return [{"id": 100 + i, "title": t, "author": author,
             "discovered_at": "2026-10-04T%02d:%02d:00+00:00" % (7 + (start + i * step) // 60, (start + i * step) % 60)}
            for i, t in enumerate(titles)]


def _groups(links):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            x = parent[x]
        return x
    for a, b in links:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    out = {}
    for x in list(parent):
        out.setdefault(find(x), set()).add(x)
    return sorted(sorted(g) for g in out.values())


BLOKEES = ["Blokees Debuts 90+ New Products at Wonder Festival Shanghai 2026",
           "Blokees predstavuje na Wonder Festival Shanghai 2026 viac ako 90 nových produktov",
           "Blokees präsentiert über 90 neue Produkte auf dem Wonder Festival Shanghai 2026",
           "Blokees dévoile plus de 90 nouveaux produits au Wonder Festival Shanghai 2026",
           "Blokees представляет более 90 новых продуктов на фестивале Wonder Festival Shanghai 2026",
           "Blokees presenta más de 90 nuevos productos en el Wonder Festival Shanghai 2026",
           "Blokees prezentuje ponad 90 nowych produktów na Wonder Festival Shanghai 2026"]


def test_the_editions_of_one_release_are_linked():
    assert _groups(placements.link_editions(_rows(BLOKEES))) == [list(range(100, 107))]
    farizon = ["Geely Farizon debutará en IAA Transportation 2026 con modelos totalmente eléctricos",
               "Geely Farizon feiert auf der IAA Transportation 2026 Premiere mit vollelektrischen Modellen",
               "Geely Farizon fait ses débuts au salon IAA Transportation 2026 avec des modèles 100 % électriques",
               "Geely Farizon to Premiere at IAA Transportation 2026 with All-Electric Models"]
    assert _groups(placements.link_editions(_rows(farizon, author="Farizon New Energy Commercial Vehicle Group"))) == [[100, 101, 102, 103]]


def test_two_releases_of_one_issuer_are_not_merged():
    # Foton: two releases about the same show; the Spanish edition of the second must not join the first.
    titles = ["IAA Will Kick-off in Hannover -- Unveiling FOTON's Systematic Global Expansion Blueprint",
              "IAA comenzará en Hannover: se revelará el plan estratégico de expansión global de FOTON",
              "¡Todo listo! FOTON llega a Hannover IAA con su gama completa de productos"]
    groups = _groups(placements.link_editions(_rows(titles, author="Foton Motor", step=300)))
    assert groups == [[100, 101]], "one Spanish edition per release"
    # Shanghai Electric's half year results are not its turbine order, and a title sharing only the
    # issuer's name is left alone.
    se = ["Shanghai Electric Secures First Overseas Heavy-Duty Gas Turbine Order for 500 MW Malaysian Project",
          "Shanghai Electric décroche sa première commande à l'international de turbines à gaz de forte puissance pour un projet malaisien de 500 MW",
          "Shanghai Electric Reports 16.6% Revenue Growth in H1 2026 as New Orders Reach CNY 100.39 Billion",
          "Shanghai Electric annonce une hausse de 16,6 % de son chiffre d'affaires au premier semestre de 2026, avec un volume de nouvelles commandes atteignant 100,39 milliards de yuans",
          "Shanghai Electric consigue su primer pedido internacional de turbinas de gas de gran potencia"]
    assert _groups(placements.link_editions(_rows(se, author="Shanghai Electric"))) == [[100, 101], [102, 103]]


def test_editions_far_apart_or_of_unknown_language_are_not_linked():
    far = _rows(BLOKEES[:2])
    far[1]["discovered_at"] = "2026-10-07T09:00:00+00:00"
    assert placements.link_editions(far) == []
    assert placements.link_editions(_rows(["Blokees Wonder Festival 90", "BLOKEES WONDER FESTIVAL 90"])) == []
    assert placements.link_editions(_rows(BLOKEES[:2], author=None) + []) != []
    other = _rows(BLOKEES[:1]) + [{"id": 200, "title": BLOKEES[2], "author": "Someone",
                                   "discovered_at": "2026-10-04T07:01:00+00:00"}]
    other[0]["author"] = "Blokees"
    assert placements.link_editions(other) == [], "two different issuers"


def test_editions_are_one_underlying_item_and_stay_placements(tmp_path):
    conn = _db(tmp_path)
    for i, t in enumerate(BLOKEES):
        _add(conn, "https://prn.test/blokees-%d" % i, "us_prnewswire", "A", title=t, minute=i)
    groups = body_dups.item_groups(conn)
    assert len({groups.get(i, i) for i in range(1, 8)}) == 1
    export.rebuild_rollups(conn, OUTLETS)
    day = export.build_daily(conn, OUTLETS)["2026-10"]["days"][DAY]
    assert day["countries"]["WLD"]["A"] == 7
    assert len(day["underlying_items"]["WLD"]["A"]) == 1
    s = placements.summary(conn, OUTLETS)["duplicate_placements"]["language_editions"]
    assert (s["groups"], s["rows"], s["state_origin_groups"], s["state_origin_rows"]) == (1, 7, 1, 7)


def test_edition_language():
    assert placements.edition_language(BLOKEES[0]) == "en"
    assert placements.edition_language(BLOKEES[2]) == "de"
    assert placements.edition_language(BLOKEES[4]) == "cyrl"
    assert placements.edition_language(BLOKEES[6]) == "pl"
    assert placements.edition_language("ASMAX") is None
