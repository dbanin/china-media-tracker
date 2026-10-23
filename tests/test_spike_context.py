"""What an unusual day was about, and the news beside it: the automatic context the export computes for
every flagged day (pipeline/spikes.py day_context, counts of themes and terms only), and the researched
notes in sources/spike_context.yaml (pipeline/spike_context.py: the validator, the window, the wording),
carried by the export into docs/data/spikes.json and by docs/content.json for older data files."""
import copy
import datetime as dt
import json
import re
from pathlib import Path

import pytest
import yaml

from pipeline import export, measures, spike_context, spikes, store

ROOT = Path(__file__).resolve().parents[1]
START = "2026-09-01"

OUTLETS = [
    {"id": "it_paper", "name": "Paper", "country": "ITA", "language": "it", "feeds": ["https://paper.test/rss"], "tier": "national_daily", "active": True},
    {"id": "it_news", "name": "News", "country": "ITA", "language": "it", "feeds": ["https://news.test/rss"], "tier": "national_daily", "active": True},
    {"id": "us_wire", "name": "Wire", "country": "USA", "language": "en", "feeds": ["https://wire.test/rss"], "tier": "distribution_wire", "active": True},
]


def _day(n):
    return (dt.date.fromisoformat(START) + dt.timedelta(days=n)).isoformat()


SPIKE = _day(29)


def _good():
    return {"window": {"days_before": 3, "days_after": 1}, "spikes": [
        {"country": "ITA", "date": SPIKE, "researched": SPIKE, "notes": [
            {"scope": "china", "event": "A financial forum opened in Beijing with the central bank governor speaking.",
             "event_date": _day(27), "event_end": _day(30), "source_title": "Forum keynote (Example Bank)",
             "source_url": "https://example.org/forum", "confidence": "confirmed", "basis": "Two of the day's items name the forum."},
            {"scope": "bilateral", "result": spike_context.NO_MATCH, "searched": "Italy and China news of that week."}]}]}


# ---------------------------------------------------------------------------
# The validator
# ---------------------------------------------------------------------------

def test_the_committed_file_validates_and_every_note_is_found_or_says_nothing_was():
    data = spike_context.load_file()
    assert spike_context.validate_file() == []
    assert data["spikes"], "the researched spikes are recorded"
    for s in data["spikes"]:
        for n in s["notes"]:
            if "result" in n:
                assert n["result"] == "no matching event found" and n["searched"]
            else:
                assert n["source_url"].startswith("https://") and n["source_title"] and n["confidence"] in ("confirmed", "plausible")
                assert spike_context.in_window(s["date"], n["event_date"], n.get("event_end"), data["window"])


def test_a_good_file_passes():
    assert spike_context.validate(_good()) == []


@pytest.mark.parametrize("change, needle", [
    (lambda d: d["spikes"][0].update(date="2026-02-30"), "not a real date"),
    (lambda d: d["spikes"][0]["notes"][0].update(event_date="2026-13-01"), "not a real date"),
    (lambda d: d["spikes"][0]["notes"][0].pop("source_url"), "source_url"),
    (lambda d: d["spikes"][0]["notes"][0].update(source_url="http://example.org/x"), "source_url"),
    (lambda d: d["spikes"][0]["notes"][0].update(event_date=_day(20), event_end=_day(21)), "outside the window"),
    (lambda d: d["spikes"][0]["notes"][0].update(event_date=_day(28), event_end=_day(26)), "event_end is before"),
    (lambda d: d["spikes"][0]["notes"][0].update(event="A forum opened in Beijing — with speeches."), "dash"),
    (lambda d: d["spikes"][0]["notes"][0].update(basis="Named - in two items."), "spaced hyphen"),
    (lambda d: d["spikes"][0]["notes"][0].update(basis='It said "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen".'), "quote of 16 words"),
    (lambda d: d["spikes"][0]["notes"][0].update(event="A forum opened. The governor spoke at it."), "one plain sentence"),
    (lambda d: d["spikes"][0]["notes"][0].update(confidence="caused"), "confidence"),
    (lambda d: d["spikes"][0]["notes"][1].update(result="probably a forum"), "notes/1"),
    (lambda d: d["spikes"].append(copy.deepcopy(d["spikes"][0])), "listed twice"),
    (lambda d: d["spikes"][0].update(researched=_day(10)), "researched before"),
])
def test_the_validator_refuses(change, needle):
    d = _good()
    change(d)
    errs = spike_context.validate(d)
    assert errs and any(needle in e for e in errs), errs


def test_a_quote_of_fifteen_words_is_allowed_and_a_multi_day_event_counts_on_any_of_its_days():
    d = _good()
    d["spikes"][0]["notes"][0]["basis"] = 'It said "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".'
    d["spikes"][0]["notes"][0].update(event_date=_day(20), event_end=_day(26))   # runs into the window's first day
    assert spike_context.validate(d) == []


def test_unquoted_yaml_dates_are_read_as_strings(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(_good(), sort_keys=False).replace("'", ""), encoding="utf-8")
    data = spike_context.load_file(p)
    assert data["spikes"][0]["date"] == SPIKE and spike_context.validate(data) == []


def test_an_invalid_file_reaches_nothing(tmp_path, capsys):
    p = tmp_path / "bad.yaml"
    bad = _good()
    bad["spikes"][0]["notes"][0].update(event_date=_day(1), event_end=_day(2))
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    s = [{"country": "ITA", "date": SPIKE}]
    assert spike_context.attach(s, p) == 0 and "news" not in s[0]
    assert "not exported" in capsys.readouterr().err


def test_the_command_line(capsys):
    assert spike_context.main(["validate"]) == 0 and "ok" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The site's copy and the wording
# ---------------------------------------------------------------------------

def test_content_json_carries_exactly_the_validated_notes():
    content = json.loads((ROOT / "docs" / "content.json").read_text(encoding="utf-8"))
    sn = content["spike_news"]
    assert sn["notes"] == spike_context.site_notes(), "docs/content.json spike_news is out of step with sources/spike_context.yaml"
    assert "timing, not cause" in sn["caveat"] and "single event that wire covered" in sn["caveat"]
    js = (ROOT / "docs" / "m_spikes.js").read_text(encoding="utf-8")
    m = re.search(r'var CAVEAT = "([^"]+)";', js)
    assert m and m.group(1) == sn["caveat"], "the page's default caveat matches content.json"


def test_the_page_says_coincides_with_and_never_a_cause():
    js = (ROOT / "docs" / "m_spikes.js").read_text(encoding="utf-8")
    for heading in ("What the items were about", "Other China coverage that day", "In the news", '"Coincides with: "'):
        assert heading in js
    assert not re.search(r"caused by|because of|led to|triggered", js.split("/* ---- What the day was about")[1], re.I)
    text = json.dumps(json.loads((ROOT / "docs" / "content.json").read_text(encoding="utf-8"))["spike_news"], ensure_ascii=False)
    assert not re.search(r"caused|because of|triggered|led to", text, re.I)
    assert not re.search("[–—]", text + js)


def test_the_methodology_paragraph():
    t = spikes.methodology_text({"summary": {"tests": 100, "spikes": 2, "expected_false_flags": 0.1}})
    last = t.split("\n\n")[-1]
    assert "coincides with" in last and "timing, not cause" in last and "sources/spike_context.yaml" in last
    assert "single event that wire covered" in last and "no matching event was found" in last
    assert not re.search("[–—]", t) and "Category" not in t


# ---------------------------------------------------------------------------
# The automatic context, exported from a database
# ---------------------------------------------------------------------------

def _article(conn, n, day, outlet, title, terms, theme_ids, category=None, route="wire_credit"):
    aid = store.insert_discovered(conn, {"url": "https://paper.test/%d" % n, "outlet_id": outlet, "country": "ITA",
                                         "language": "it", "title": title, "status": "fetched", "gate_relevant": 1})
    conn.execute("UPDATE articles SET discovered_at=?, status='classified', gate_terms=?, themes=? WHERE id=?",
                 (day + "T09:00:00+00:00", json.dumps(terms), json.dumps({"v": "x", "t": theme_ids, "b": 1}), aid))
    if category:
        store.insert_classification(conn, aid, "rules", category, 1.0, route=route if category == "A" else None)
    return aid


def _seed(conn):
    n = 0
    for i in range(30):
        d = _day(i)
        # an ordinary day: one state origin item and three other China articles that say "Cina" and "Roma"
        k = 12 if i == 29 else (2 if i == 28 else 1)
        for j in range(k):
            n += 1
            outlet = "it_paper" if j % 3 else "it_news"
            if i >= 28:
                title = "Financial Street Forum %s apre a Pechino" % ("annuale" if j % 2 else "globale")
                _article(conn, n, d, outlet, title, ["Cina", "Pechino", "section:press_release"], ["economy"], "A")
            else:
                _article(conn, n, d, outlet, "Cina notizia %d" % n, ["Cina"], ["diplomacy"], "A")
        for j in range(3):
            n += 1
            if i == 29:
                title = "Hormuz petroliera colpita, la Cina chiede calma %d" % j if j < 2 else "Private headline Financial Street Forum oggi"
                _article(conn, n, d, "it_paper", title, ["Cina"], ["security"] if j < 2 else ["economy"], "C")
            else:
                _article(conn, n, d, "it_paper", "Roma e Cina, giornata %d" % n, ["Cina"], ["diplomacy"], "C")
        for o, c in (("it_paper", "ITA"), ("it_news", "ITA"), ("us_wire", "USA")):
            conn.execute("INSERT OR REPLACE INTO daily_outlet_discovery(date, outlet_id, country, discovered, gate_relevant) VALUES (?,?,?,50,1)", (d, o, c))
    conn.commit()
    export.rebuild_rollups(conn, OUTLETS)


def _export(tmp_path, monkeypatch, news_yaml=None):
    conn = store.connect(tmp_path / "s.db")
    _seed(conn)
    if news_yaml is not None:
        monkeypatch.setattr(spike_context, "PATH", news_yaml)
    ctx = {"daily": export.build_daily(conn, OUTLETS), "latest": {"window_start_30d": _day(0)}, "outlets": OUTLETS,
           "meta": {"first_discovered": _day(0)}, "export_dir": tmp_path}
    cfg = tmp_path / "m.yaml"
    cfg.write_text("- {id: spikes, label: Unusual days, module: pipeline.spikes, enabled: true, min_inputs: 15}\n")
    written = {}
    blocks = measures.run_all_quietly(conn, tmp_path, ctx, write_json=lambda p, d: written.__setitem__(p.name, d),
                                      config_path=cfg, reviews_dir=tmp_path / "reviews")
    assert "error" not in blocks["spikes"], blocks["spikes"]
    return written["spikes.json"]


def test_the_export_says_what_the_day_was_about(tmp_path, monkeypatch):
    p = tmp_path / "news.yaml"
    p.write_text(yaml.safe_dump(_good(), sort_keys=False), encoding="utf-8")
    d = _export(tmp_path, monkeypatch, p)
    s = [x for x in d["spikes"] if x["date"] == SPIKE and x["country"] == "ITA"][0]
    cx = s["context"]
    # the 28 days before: four articles a day, five on the day before
    assert cx["version"] == spikes.CONTEXT_VERSION and cx["baseline_articles"] == 27 * 4 + 5
    st = cx["state"]
    assert st["placements"] == 12 and st["themes"][0] == {"id": "economy", "label": "Trade and economy", "n": 12}
    terms = {t["term"]: t["n"] for t in st["terms"]}
    # what was particular to the day, not what every day says
    assert terms["Financial Street"] == 12 and terms["Pechino"] == 12 and terms["Forum annuale"] == 6
    assert "Cina" not in terms and not any(t.startswith("section:") for t in terms)
    # a word is not listed beside a pair that holds it as often
    assert "Financial" not in terms and "Street" not in terms
    assert all(t["n"] >= spikes.TERM_MIN_DF and t["lift"] >= spikes.TERM_LIFT for t in st["terms"])
    assert cx["day_before"]["date"] == _day(28) and cx["day_before"]["placements"] == 2
    o = cx["other"]
    assert o["articles"] == 3 and {t["id"] for t in o["themes"]} == {"security", "economy"}
    oterms = {t["term"]: t["n"] for t in o["terms"]}
    assert oterms.get("Hormuz") == 2 and "Roma" not in oterms and "Cina" not in oterms
    assert "Financial" not in oterms and "Forum" not in oterms, "a term one article carries is not listed"
    assert all(" " not in t for t in oterms), "single words only for the other coverage"
    assert cx["shared_terms"] == ["Financial Street", "Street Forum"]
    # the researched note rides along, and the method says how terms are picked and how news is worded
    assert s["news"]["notes"][0]["event"].startswith("A financial forum") and s["news"]["researched"] == SPIKE
    assert "coincides with" in d["method"]["news"]["wording"] and d["method"]["news"]["window"] == {"days_before": 3, "days_after": 1}
    assert d["method"]["context"]["term_lift"] == spikes.TERM_LIFT
    assert not re.search("[–—]", json.dumps(d, ensure_ascii=False))


def test_no_text_leaks_beyond_what_the_site_already_publishes(tmp_path, monkeypatch):
    d = _export(tmp_path, monkeypatch, tmp_path / "none.yaml")
    s = [x for x in d["spikes"] if x["date"] == SPIKE][0]
    assert "news" not in s, "a day not researched carries no news"
    text = json.dumps(s["context"], ensure_ascii=False)
    # the other coverage is never quoted: none of its headlines appears, only counted single words
    assert "Private" not in text and "headline" not in text and "petroliera colpita" not in text.lower()
    for part in ("state", "day_before"):
        assert all(len(t["term"].split()) <= 2 for t in s["context"][part]["terms"])
    # the context holds no headline at all: the spike's own headlines stay where they were (top_items)
    own = {i["title"] for i in s["items"]} | {i["title"] for i in s["top_items"]}
    assert not any(h in text for h in own)
    assert len(s["top_items"]) <= spikes.TOP_N and len(s["items"]) <= spikes.MAX_ITEMS
    assert set(s["context"]) == {"version", "state", "day_before", "other", "shared_terms", "baseline_articles"}


def test_a_failing_context_never_stops_the_side_file(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("no such column")
    monkeypatch.setattr(spikes, "day_context", boom)
    d = _export(tmp_path, monkeypatch, tmp_path / "none.yaml")
    assert d["spikes"] and all("context" not in s for s in d["spikes"])
    assert "not built" in capsys.readouterr().err


def test_the_terms_of_a_headline():
    t = spikes.article_terms("Financial Street Forum's annual conference opens in Beijing", json.dumps(["China", "section:sponsored"]))
    assert t["china"] == "China" and "section:sponsored" not in t
    assert t["financial street"] == "Financial Street" and t["forum"] == "Forum" and "in" not in t and "opens" in t
    assert spikes.article_terms(None, "not json") == {}
