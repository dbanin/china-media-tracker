"""Placements and distinct underlying items, stated apart for every headline count (round of 2026-11-24).

A reviewer read one release run by five papers of one chain as five rows of inflation. Every headline count
now carries both numbers, per country and worldwide, with the state origin on the paid release route
(placements.PAID_RELEASE_ROUTES) apart: latest.json (uniqB, Ap, uniqAp beside uniqA), the daily files'
underlying_items (Ap), meta.findings.items, meta.underlying_items, the methodology and both CSV exports."""
import datetime as dt
import inspect
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import body_dups, export, fetch_feeds, methodology, placements, store

ROOT = Path(__file__).resolve().parent.parent

OUTLETS = [
    {"id": "ca_post", "name": "Post", "country": "CAN", "language": "en", "feeds": ["https://post.test/rss"], "tier": "national_daily", "active": True},
    {"id": "ca_sun", "name": "Sun", "country": "CAN", "language": "en", "feeds": ["https://sun.test/rss"], "tier": "national_daily", "active": True},
    {"id": "ca_prov", "name": "Province", "country": "CAN", "language": "en", "feeds": ["https://prov.test/rss"], "tier": "national_daily", "active": True},
    {"id": "ca_fin", "name": "Financial", "country": "CAN", "language": "en", "feeds": ["https://fin.test/rss"], "tier": "national_daily", "active": True},
    {"id": "wire", "name": "Wire", "country": "USA", "language": "en", "feeds": ["https://wire.test/rss"], "tier": "distribution_wire", "active": True},
]


def _day(days_ago):
    return (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days_ago)).isoformat()


def _article(conn, url, outlet, country, category, days_ago, group=None, route=None, method="rules"):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": country, "language": "en",
                                         "title": "China release " + url, "status": "fetched", "gate_relevant": 1})
    conn.execute("UPDATE articles SET discovered_at=?, status='classified', dup_group_id=? WHERE id=?",
                 (_day(days_ago) + "T09:00:00+00:00", group, aid))
    store.insert_classification(conn, aid, method, category, 1.0, route=route if category == "A" else None)
    conn.commit()
    return aid


def _seed(tmp_path):
    """One paid release run by four Canadian papers of one chain and by a wire (joined by a body link), a
    second Canadian state origin item by wire credit, one unchecked state sourcing item at two papers, and an
    undetermined model call, which is in no count."""
    conn = store.connect(tmp_path / "u.db")
    x = _article(conn, "https://post.test/x", "ca_post", "CAN", "A", 3, route="distribution_stamp")
    for o in ("sun", "prov", "fin"):
        _article(conn, "https://%s.test/x" % o, "ca_" + o, "CAN", "A", 3, group=x, route="distribution_stamp")
    w = _article(conn, "https://wire.test/x", "wire", "USA", "A", 4, route="distribution_stamp")
    conn.execute("INSERT INTO body_links(a, b, similarity, version, created_at) VALUES (?,?,?,?,?)",
                 (min(x, w), max(x, w), 0.9, body_dups.FINGERPRINT_VERSION, store.utcnow()))
    _article(conn, "https://sun.test/y", "ca_sun", "CAN", "A", 2, route="wire_credit")
    b = _article(conn, "https://post.test/b", "ca_post", "CAN", "B", 1)
    _article(conn, "https://sun.test/b", "ca_sun", "CAN", "B", 1, group=b)
    _article(conn, "https://fin.test/u", "ca_fin", "CAN", "A", 1, route="unattributed", method="llm")
    # An old placement of the paid release outside the 30 day block.
    _article(conn, "https://post.test/old", "ca_post", "CAN", "A", 45, route="distribution_stamp")
    conn.commit()
    return conn, x


def test_latest_states_placements_items_and_the_paid_route(tmp_path):
    conn, _x = _seed(tmp_path)
    export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [{"country": "ATA", "reason": "no outlet"}], population={})
    can = latest["countries"]["CAN"]
    at, l30 = can["all_time"], can["last_30d"]
    assert (at["A"], at["uniqA"], at["Ap"], at["uniqAp"]) == (6, 3, 5, 2), "four papers of one release are one item"
    assert (l30["A"], l30["uniqA"], l30["Ap"], l30["uniqAp"]) == (5, 2, 4, 1)
    assert (at["B"], at["uniqB"], at["uniqAB"]) == (2, 1, at["uniqA"] + 1)
    assert at["U"] == 1, "the undetermined call is its own figure and in no item count"
    wld = latest["countries"]["WLD"]["all_time"]
    assert (wld["A"], wld["uniqA"], wld["Ap"], wld["uniqAp"]) == (1, 1, 1, 1)
    world = latest["totals"]["last_30d"]
    assert (world["A"], world["uniqA"], world["Ap"], world["uniqAp"]) == (6, 2, 5, 1), \
        "the wire's copy is the Canadian release, one item worldwide"
    gap = latest["countries"]["ATA"]["all_time"]
    assert all(gap[k] == 0 for k in ("uniqA", "uniqB", "uniqAB", "Ap", "uniqAp")), "every block carries every key"


def test_daily_files_carry_the_paid_route_ids(tmp_path):
    conn, x = _seed(tmp_path)
    export.rebuild_rollups(conn, OUTLETS)
    daily = export.build_daily(conn, OUTLETS)
    days = {d: e for m in daily.values() for d, e in m["days"].items()}
    three = days[_day(3)]
    assert three["underlying_items"]["CAN"] == {"A": [x], "Ap": [x]}
    assert three["routes"]["CAN"] == {"distribution_stamp": 4}, "the paid placements are the day's route tally"
    assert "Ap" not in days[_day(2)]["underlying_items"]["CAN"], "a wire credit item is not a paid release"


def test_findings_items_under_each_distribution(tmp_path):
    conn, _x = _seed(tmp_path)
    export.rebuild_rollups(conn, OUTLETS)
    index = export.item_index(conn, OUTLETS)
    block = export.items_block(index, None, None, visible=True)
    assert block["both"] == {"state_origin": 7, "state_origin_items": 3, "unchecked": 2, "unchecked_items": 1,
                             "paid_release": 6, "paid_release_items": 2}
    assert block["country"]["state_origin"] == 6 and block["country"]["state_origin_items"] == 3
    assert block["wires"] == {"state_origin": 1, "state_origin_items": 1, "unchecked": 0, "unchecked_items": 0,
                              "paid_release": 1, "paid_release_items": 1}
    # An item carried by a country outlet and a wire is one item in each part and one in both.
    assert block["country"]["state_origin_items"] + block["wires"]["state_origin_items"] == block["both"]["state_origin_items"] + 1
    hidden = export.items_block(index, None, None, visible=False)["both"]
    assert hidden["unchecked"] is None and hidden["unchecked_items"] is None and hidden["state_origin"] == 7
    since = _day(29)
    assert export.items_block(index, since, None)["both"]["paid_release"] == 5
    assert export.item_index(conn, OUTLETS) is index, "one pass per export"


def test_the_findings_and_meta_carry_the_items(tmp_path):
    conn, _x = _seed(tmp_path)
    export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [], population={})
    findings = export.build_findings(conn, OUTLETS, latest, export.build_outlets(conn, OUTLETS), _day(45))
    items = findings["items"]
    assert items["all_time"]["both"]["state_origin"] == findings["all_time"]["state_origin"] == 7, "no placement on the export day"
    assert items["all_time"]["window_end"] == items["last_30d"]["window_end"] == _day(1)
    assert items["last_30d"]["both"]["state_origin"] == findings["last_30d"]["state_origin"]
    assert items["last_30d"]["window_start"] == findings["last_30d"]["window_start"]
    tc = findings["top_country"]
    assert tc["iso"] == "CAN" and tc["underlying"] == 2 and tc["paid_release"] == 4 and tc["paid_release_items"] == 1
    meta = export.underlying_items_meta(conn, OUTLETS, visible=True)
    assert meta["rule"] == placements.item_rule() and meta["paid_key"] == "Ap"
    so = meta["all_time"]["state_origin"]
    assert (so["placements"], so["items"], so["items_placed_more_than_once"], so["placements_in_those_items"]) == (7, 3, 1, 5)
    top = meta["largest"]["state_origin"][0]
    assert (top["placements"], top["outlets"], top["countries"]) == (5, 5, ["CAN", "WLD"])
    assert top["first_outlet"] == "Wire" and top["first_day"] == _day(4), "the first placement names the item"
    assert [e["outlets"] for e in meta["largest"]["state_origin"]] == sorted((e["outlets"] for e in meta["largest"]["state_origin"]), reverse=True)
    assert "unchecked" not in export.underlying_items_meta(conn, OUTLETS, visible=False)["largest"]
    json.dumps(meta)


def test_labels_held_out_of_every_count_have_no_items():
    """Generic over codes: undetermined (U) and any group held apart like it (S, H) is in no item count."""
    rows = [{"id": i, "m_cat": cat, "route": "distribution_stamp", "outlet_id": "o", "country": "CAN", "dup_group_id": None,
             "discovered_at": "2026-11-20T00:00:00+00:00", "day_of_record": None, "title": "t"}
            for i, cat in enumerate(["U", "S", "H", "C", "not_relevant", None, "A"], start=1)]
    ids, n, groups = export._item_pass(rows, {}, set())
    assert dict(ids["2026-11-20"]["CAN"]) == {"A": {7}, "B": set(), "Ap": {7}}
    assert dict(n["2026-11-20"]["CAN"]) == {"A": 1, "Ap": 1} and list(groups) == [7]


def test_the_stated_title_rule_is_the_code_that_links():
    assert inspect.signature(store.recent_titles).parameters["days"].default == placements.TITLE_WINDOW_DAYS
    words = fetch_feeds.normalize_title("Xi at UN: aid for all")
    assert words == ["aid", "for", "all"] and placements.TITLE_WORD_MIN_CHARS == 3
    src = inspect.getsource(fetch_feeds.link_near_duplicates)
    assert "len(toks) < %d" % placements.TITLE_MIN_WORDS in src
    assert "len(w) > %d" % (placements.TITLE_WORD_MIN_CHARS - 1) in inspect.getsource(fetch_feeds.normalize_title)
    r = placements.item_rule()
    assert r["title"]["jaccard"] == fetch_feeds.TITLE_JACCARD_THRESHOLD
    assert (r["body"]["window_days"], r["body"]["jaccard"], r["body"]["min_shingles"]) == (
        body_dups.WINDOW_DAYS, body_dups.JACCARD_THRESHOLD, body_dups.MIN_SHINGLES)
    assert r["editions"]["window_hours"] == placements.EDITION_WINDOW_HOURS


def test_the_methodology_says_what_an_item_is_and_how_it_can_mis_merge():
    r = placements.item_rule()
    text = methodology.underlying_items_text({})
    for phrase in ("An underlying item is the text behind its placements", "Over merging.", "Under merging.",
                   "within %d days" % r["title"]["window_days"], "at least %d percent of their distinct words" % round(r["title"]["jaccard"] * 100),
                   "within %d days of each other" % r["body"]["window_days"], "at least %d percent" % round(r["body"]["jaccard"] * 100),
                   "within %d hours" % r["editions"]["window_hours"], "paid press release"):
        assert phrase in text, phrase
    site = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    para = site[site.index("<strong>Placements and underlying items.</strong>"):]
    para = para[:para.index("</p>")]
    for phrase in ("in one country within %d days" % r["title"]["window_days"], "at least %d percent of their distinct words" % round(r["title"]["jaccard"] * 100),
                   "at least %d of them" % r["title"]["min_words"], "within %d days, overlap by an estimated %d percent" % (r["body"]["window_days"], round(r["body"]["jaccard"] * 100)),
                   "within %d hours" % r["editions"]["window_hours"], "over merge", "under merge"):
        assert phrase in para, phrase
    for t in (text, para):
        assert not re.search("[–—]", t) and "Category A" not in t and "Category B" not in t


def test_the_methodology_states_this_exports_figures():
    meta = {"underlying_items": {"all_time": {"state_origin": {"placements": 2312, "items": 1907, "items_placed_more_than_once": 208,
                                                               "placements_in_those_items": 613},
                                              "paid_release": {"placements": 719, "items": 563}},
                                 "largest": {"state_origin": [{"placements": 9, "outlets": 8, "countries": ["AUS", "ITA", "WLD"], "first_day": "2026-11-08"}]}}}
    text = methodology.underlying_items_text(meta)
    assert "state origin is 2,312 placements of 1,907 underlying items" in text
    assert "719 placements of 563 items came as paid press releases" in text
    assert "placed 9 times by 8 outlets in 2 countries and on the worldwide wires" in text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_items_smoke(tmp_path):
    stub = ('var document = {readyState: "loading", addEventListener: function () {}, getElementById: function () { return null; },'
            ' querySelector: function () { return null; }, querySelectorAll: function () { return []; }};')
    files = [ROOT / "docs" / "vendor" / "d3.v7.min.js", ROOT / "docs" / "compute.js", ROOT / "docs" / "app.js", ROOT / "tests" / "smoke_unique_items.js"]
    shim = tmp_path / "shim.js"
    shim.write_text("const fs = require('fs'), vm = require('vm');\n"
                    "const ctx = {console: console, Date: Date}; ctx.self = ctx; ctx.window = ctx; vm.createContext(ctx);\n"
                    "vm.runInContext(%r, ctx);\n" % stub
                    + "".join("vm.runInContext(fs.readFileSync(%r, 'utf8'), ctx);\n" % str(f) for f in files), encoding="utf-8")
    r = subprocess.run([shutil.which("node"), str(shim)], capture_output=True, text=True)
    assert r.returncode == 0 and "unique items smoke ok" in r.stdout, r.stdout + r.stderr


def test_all_time_items_end_on_the_last_complete_day(tmp_path):
    """The site's all time cards count through the last complete day; the export's block does too, so the
    card can state it beside its own number before every month has loaded."""
    conn, _x = _seed(tmp_path)
    _article(conn, "https://fin.test/today", "ca_fin", "CAN", "A", 0, route="distribution_stamp")
    export.rebuild_rollups(conn, OUTLETS)
    latest = export.build_latest(conn, OUTLETS, [], population={})
    findings = export.build_findings(conn, OUTLETS, latest, export.build_outlets(conn, OUTLETS), _day(45))
    assert findings["all_time"]["state_origin"] == 8
    assert findings["items"]["all_time"]["both"]["state_origin"] == 7 and findings["items"]["all_time"]["both"]["paid_release"] == 6
    assert latest["totals"]["all_time"]["Ap"] == 7, "latest.json keeps the export day, as its A does"
