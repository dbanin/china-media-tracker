"""Engagement (pipeline/engagement.py): the URL list for the owner's hand lookups, the strict counts
validator, the idempotent import, and the measure, which stays off until it is enabled and has rows.

Nothing here, or in the module, touches Meta, a login, a token or an API: the owner looks the addresses
up himself and brings back aggregate counts only.
"""
import csv
import json
import re
from pathlib import Path

import pytest

from pipeline import config, engagement as eng, measures, store

OUTLETS = [
    {"id": "it_paper", "name": "Paper", "country": "ITA", "language": "it", "tier": "national", "active": True},
    {"id": "us_wire", "name": "Release Wire", "country": "USA", "language": "en", "tier": "distribution_wire",
     "active": True},
]
URLS = ["https://www.paper.it/cina-1", "https://paper.it/cina-2", "https://wire.example.com/release-3"]
HASHES = [store.url_hash(u) for u in URLS]
HEADER = ["key_type", "key", "platform", "period_start", "period_end", "posts_n", "reactions", "shares", "comments",
          "views", "suppressed", "query_date", "mcl_query_ref"]


def _article(conn, url, outlet, country, day, category="A", route="wire_credit", title="Headline"):
    aid = store.insert_discovered(conn, {"url": url, "outlet_id": outlet, "country": country, "language": "it",
                                         "title": title, "gate_relevant": 1, "day_of_record": day})
    store.insert_classification(conn, aid, "rules", category, 0.9, route=route if category == "A" else None)
    return aid, store.url_hash(url)


@pytest.fixture
def db(tmp_path):
    conn = store.connect(tmp_path / "e.db")
    _article(conn, URLS[0], "it_paper", "ITA", "2026-10-01")
    _article(conn, URLS[1], "it_paper", "ITA", "2026-10-02", route="sponsored_disclosure")
    _article(conn, URLS[2], "us_wire", "USA", "2026-10-02", route="distribution_stamp")
    _article(conn, "https://paper.it/indipendente", "it_paper", "ITA", "2026-10-02", category="C")
    _article(conn, "http://localhost/private", "it_paper", "ITA", "2026-10-02")
    _article(conn, "https://paper.it/older", "it_paper", "ITA", "2026-09-01")
    conn.commit()
    return conn


def _row(**kw):
    r = {"key_type": "url_hash", "key": "0" * 31 + "a", "platform": "facebook", "period_start": "2026-10-01",
         "period_end": "2026-10-14", "posts_n": "12", "reactions": "340", "shares": "21", "comments": "9",
         "views": "", "suppressed": "0", "query_date": "2026-10-14", "mcl_query_ref": "Q2026 10 14 01"}
    r.update(kw)
    return r


def _write(path, rows, header=HEADER):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(h, "") for h in header])
    return path


def _check(rows, header=HEADER, k=None):
    return eng.validate_rows(header, [[r.get(h, "") for h in header] for r in rows], k)


def _messages(rep):
    return " | ".join(m for _, _, m in rep["errors"])


# ---------------------------------------------------------------------------
# The schema and the hard rule
# ---------------------------------------------------------------------------

def test_the_schema_is_the_whitelist():
    s = eng.load_schema()
    assert s["columns"] == HEADER
    assert s["optional"] == ["views"]
    assert eng.ENGAGEMENT_MIN_CELL == 5


def test_no_code_reaches_meta_or_any_network():
    src = (config.ROOT / "pipeline" / "engagement.py").read_text(encoding="utf-8")
    imports = " ".join(re.findall(r"^\s*(?:import|from)\s+([\w.]+)", src, re.M))
    for banned in ("requests", "urllib.request", "http", "socket", "feeds_util", "fetch_articles", "anthropic"):
        assert banned not in imports.split(), banned
    for banned in ("graph.facebook", "access_token", "client_secret", "Authorization"):
        assert banned not in src


# ---------------------------------------------------------------------------
# The validator: each rejection
# ---------------------------------------------------------------------------

def test_a_clean_file_is_accepted_and_parsed():
    rep = _check([_row(), _row(platform="instagram", posts_n="0", reactions="", shares="", comments=""),
                  _row(key="it_paper:2026-10", key_type="outlet_month", views="12000"),
                  _row(platform="instagram", key="1" * 32, posts_n="", reactions="", shares="", comments="",
                       suppressed="1")])
    assert rep["ok"], rep["errors"]
    r = rep["rows"][0]
    assert (r["posts_n"], r["reactions"], r["views"], r["suppressed"]) == (12, 340, None, 0)
    assert rep["rows"][2]["views"] == 12000
    # a url_hash of 32 digits is a hash, not an id
    assert rep["rows"][3]["key"] == "1" * 32


def test_views_column_is_optional():
    head = [h for h in HEADER if h != "views"]
    assert _check([_row()], header=head)["ok"]


def test_extra_columns_are_rejected():
    rep = _check([dict(_row(), post_id="x")], header=HEADER + ["post_id"])
    assert not rep["ok"] and "column not allowed: 'post_id'" in _messages(rep)
    rep = _check([_row()], header=HEADER + ["account"])
    assert not rep["ok"] and "strict whitelist" in _messages(rep)


def test_missing_and_repeated_columns_are_rejected():
    head = [h for h in HEADER if h != "query_date"]
    assert "required column missing: query_date" in _messages(_check([_row()], header=head))
    assert "more than once" in _messages(_check([_row()], header=HEADER + ["shares"]))


@pytest.mark.parametrize("value", ["https://www.facebook.com/story.php?id=1", "facebook.com/somepage", "fb.com/x",
                                   "m.facebook.com", "instagram.com/p/abc", "fb.me/abc", "instagr.am/p/x",
                                   "FACEBOOK.COM"])
def test_platform_links_are_rejected(value):
    rep = _check([_row(mcl_query_ref=value[:64])])
    assert not rep["ok"] and "link" in _messages(rep), value


def test_any_link_or_handle_is_rejected():
    assert "a link" in _messages(_check([_row(mcl_query_ref="see www.example.org")]))
    assert "@ sign" in _messages(_check([_row(mcl_query_ref="by @someone")]))


def test_long_digit_strings_are_rejected():
    rep = _check([_row(mcl_query_ref="post 1234567890123456")])
    assert not rep["ok"] and "digit string longer than 15" in _messages(rep)
    rep = _check([_row(views="1234567890123456")])
    assert "digit string longer than 15" in _messages(rep)
    # fifteen digits is still a count
    assert _check([_row(views="123456789012345")])["ok"]


@pytest.mark.parametrize("col,value", [("period_start", "2026-10-01T10:00"), ("period_end", "2026-10-14 23:59"),
                                       ("query_date", "2026-10-14T00:00:00Z"), ("mcl_query_ref", "run at 10:30"),
                                       ("mcl_query_ref", "run at 9pm")])
def test_times_finer_than_a_day_are_rejected(col, value):
    rep = _check([_row(**{col: value})])
    assert not rep["ok"] and "finer than a day" in _messages(rep)


def test_text_over_64_characters_is_rejected():
    rep = _check([_row(mcl_query_ref="Q" * 65)])
    assert not rep["ok"] and "over 64 characters" in _messages(rep)
    assert _check([_row(mcl_query_ref="Q" * 64)])["ok"]


@pytest.mark.parametrize("n", ["1", "2", "3", "4"])
def test_small_unsuppressed_cells_are_rejected(n):
    rep = _check([_row(posts_n=n)])
    assert not rep["ok"] and "small cell" in _messages(rep)


def test_the_minimum_cell_can_be_raised():
    assert _check([_row(posts_n="5")])["ok"]
    rep = _check([_row(posts_n="9")], k=10)
    assert not rep["ok"] and "between 1 and 9" in _messages(rep)


def test_a_suppressed_row_carries_no_count():
    rep = _check([_row(suppressed="1", posts_n="3", reactions="", shares="", comments="")])
    assert not rep["ok"] and "suppressed row carries no count" in _messages(rep)


def test_other_row_rules():
    assert "posts_n is required" in _messages(_check([_row(posts_n="")]))
    assert "must be 0 or blank" in _messages(_check([_row(posts_n="0", reactions="7")]))
    assert "required when posts_n is given" in _messages(_check([_row(shares="")]))
    assert "before period_start" in _messages(_check([_row(period_start="2026-10-15")]))
    assert "not a calendar day" in _messages(_check([_row(period_end="2026-02-30")]))
    assert "platform must be one of" in _messages(_check([_row(platform="tiktok")]))
    assert "key_type must be one of" in _messages(_check([_row(key_type="post")]))
    assert "same key, platform" in _messages(_check([_row(), _row()]))
    assert "inside its month" in _messages(_check([_row(key_type="outlet_month", key="it_paper:2026-09")]))
    assert "url_hash key" in _messages(_check([_row(key="it_paper:2026-10")]))
    assert "suppressed must be one of" in _messages(_check([_row(suppressed="yes")]))
    assert "no rows" in _messages(_check([]))


def test_the_command_line_report(tmp_path, capsys):
    good = _write(tmp_path / "good.csv", [_row()])
    assert eng.main(["validate", str(good)]) == 0
    out = capsys.readouterr().out
    assert "ACCEPTED: 1 rows" in out and "sha256" in out
    bad = _write(tmp_path / "bad.csv", [_row(posts_n="2"), _row(platform="instagram", mcl_query_ref="facebook.com/x")])
    assert eng.main(["validate", str(bad)]) == 1
    out = capsys.readouterr().out
    assert "REJECTED: 2 problems. Nothing was imported." in out
    assert "line 2, column posts_n: small cell" in out and "line 3, column mcl_query_ref" in out


# ---------------------------------------------------------------------------
# The URL list
# ---------------------------------------------------------------------------

def test_the_url_export(db, tmp_path):
    path, n = eng.write_urls(db, "2026-10-01", "2026-10-13", tmp_path, OUTLETS)
    assert path.name == "urls_2026-10-01_2026-10-13.csv"
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == eng.URLS_FIELDS
    # state origin only, in the range, public addresses only
    assert n == 3 and [r["url_hash"] for r in rows] == HASHES
    assert {r["domain"] for r in rows} == {"paper.it", "wire.example.com"}
    # a distribution wire is listed under WLD, as in the export
    assert [r["country"] for r in rows] == ["ITA", "ITA", "WLD"]
    assert [r["route"] for r in rows] == ["wire_credit", "sponsored_disclosure", "distribution_stamp"]
    assert rows[0]["date"] == "2026-10-01" and rows[0]["headline"] == "Headline" and rows[0]["article_id"]
    with pytest.raises(ValueError):
        eng.write_urls(db, "2026-10-13", "2026-10-01", tmp_path, OUTLETS)


def test_public_url():
    assert eng._public_url("https://www.paper.it/a") == "paper.it"
    for bad in ("http://localhost/x", "https://10.0.0.1/x", "ftp://paper.it/x", "https://user:pw@paper.it/x",
                "https://www.facebook.com/x", "https://intranet/x", "not a url"):
        assert eng._public_url(bad) is None, bad


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def _counts(db, tmp_path, name="counts_2026-10.csv", posts="12"):
    h1, h2, h3 = HASHES
    return _write(tmp_path / name, [
        _row(key=h1, posts_n=posts), _row(key=h1, platform="instagram", posts_n="0", reactions="", shares="", comments=""),
        _row(key=h2, posts_n="", reactions="", shares="", comments="", suppressed="1"),
        _row(key=h3, posts_n="150", reactions="5000", shares="400", comments="80", views="90000"),
        _row(key="f" * 32, posts_n="7", reactions="1", shares="0", comments="0"),
        _row(key_type="outlet_month", key="it_paper:2026-10", posts_n="40", reactions="900", shares="50", comments="30"),
    ])


def test_import_is_idempotent(db, tmp_path):
    path = _counts(db, tmp_path)
    rep = eng.import_file(db, path, outlets=OUTLETS)
    assert rep["ok"] and rep["stored"] == {"inserted": 6, "unchanged": 0, "replaced": 0}
    assert any("not a current state origin article" in w for w in rep["warnings"])
    sha = rep["sha256"]
    assert {r[0] for r in db.execute("SELECT file_sha256 FROM engagement_aggregates")} == {sha}
    again = eng.import_file(db, path, outlets=OUTLETS)
    assert again["stored"] == {"inserted": 0, "unchanged": 6, "replaced": 0}
    assert db.execute("SELECT COUNT(*) FROM engagement_aggregates").fetchone()[0] == 6
    # a corrected file replaces the cells it changes, and only those
    fixed = _counts(db, tmp_path, "counts_2026-10b.csv", posts="13")
    rep2 = eng.import_file(db, fixed, outlets=OUTLETS)
    assert rep2["stored"] == {"inserted": 0, "unchanged": 5, "replaced": 1}
    assert db.execute("SELECT posts_n, file_sha256 FROM engagement_aggregates WHERE key=? AND platform='facebook'",
                      (HASHES[0],)).fetchone()[:] == (13, rep2["sha256"])


def test_a_rejected_file_imports_nothing(db, tmp_path):
    bad = _write(tmp_path / "counts_bad.csv", [_row(key=HASHES[0]), _row(key=HASHES[1], posts_n="3")])
    rep = eng.import_file(db, bad)
    assert not rep["ok"] and "stored" not in rep
    assert db.execute("SELECT COUNT(*) FROM engagement_aggregates").fetchone()[0] == 0
    assert eng.main(["import", str(bad), "--db", str(tmp_path / "e.db")]) == 1


def test_committed_files_reach_the_runner_through_compute(db, tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "DATA_DIR", tmp_path)
    _counts(db, tmp_path)
    _write(tmp_path / "counts_zz_bad.csv", [_row(posts_n="2")])
    _write(tmp_path / "urls_2026-10-01_2026-10-13.csv", [_row()])   # a urls file is never imported
    assert eng.inputs_present(db) == 6           # waiting in the committed file, nothing written yet
    assert db.execute("SELECT COUNT(*) FROM engagement_aggregates").fetchone()[0] == 0
    out = eng.compute(db, {}, float("inf"))
    assert (out["files"], out["inserted"], out["rejected"]) == (1, 6, ["counts_zz_bad.csv"])
    assert eng.compute(db, {}, float("inf"))["unchanged"] == 6
    assert eng.inputs_present(db) == 6
    assert eng.compute(db, {}, 0)["stopped_at_deadline"] is True


# ---------------------------------------------------------------------------
# The measure through the gate
# ---------------------------------------------------------------------------

def _yaml(tmp_path, enabled):
    p = tmp_path / "measures.yaml"
    p.write_text("- id: engagement\n  label: Engagement\n  module: pipeline.engagement\n  enabled: %s\n  min_inputs: 1\n"
                 % ("true" if enabled else "false"), encoding="utf-8")
    return p


def test_off_as_shipped_and_off_with_no_rows(db, tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "DATA_DIR", tmp_path / "none")
    spec = next(m for m in measures.load_config() if m["id"] == "engagement")
    assert spec["enabled"] is False and spec["module"] == "pipeline.engagement"
    st = measures.status(db)["engagement"]
    assert st["status"] == "off" and "switched off" in st["reason"]
    st = measures.status(db, config_path=_yaml(tmp_path, True))["engagement"]
    assert st["status"] == "off" and st["reason"] == "waiting for inputs: 0 of at least 1"
    out_dir = tmp_path / "out"
    (out_dir).mkdir()
    (out_dir / "engagement.json").write_text("{}")
    blocks = measures.run_all_quietly(db, out_dir, {"outlets": OUTLETS}, config_path=_yaml(tmp_path, True),
                                      reviews_dir=tmp_path / "reviews")
    assert blocks["engagement"]["status"] == "off" and not (out_dir / "engagement.json").exists()


def test_export_shape(db, tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "DATA_DIR", tmp_path)
    _counts(db, tmp_path)
    out_dir = tmp_path / "out"
    blocks = measures.run_all_quietly(db, out_dir, {"outlets": OUTLETS}, config_path=_yaml(tmp_path, True),
                                      reviews_dir=tmp_path / "reviews")
    b = blocks["engagement"]
    assert b["status"] == "provisional" and b["inputs_present"] == 6 and b["file"] == "engagement.json", b
    assert b["summary"]["urls_looked_up"] == 3 and b["summary"]["urls_found"] == 3 and b["summary"]["cells"] == 6
    data = json.loads((out_dir / "engagement.json").read_text())
    assert set(data) == {"generated_at", "version", "min_cell", "platforms", "query_dates", "period", "coverage",
                         "totals", "by_route", "by_outlet", "by_country", "outlet_months"}
    cov = data["coverage"]
    # three state origin articles in the period were looked up and all three found (one only as suppressed);
    # the unknown hash is not state origin and is left out
    assert (cov["state_origin_urls"], cov["looked_up"], cov["found"], cov["share_found"]) == (3, 3, 3, 1.0)
    assert cov["no_longer_state_origin"] == 1 and cov["looked_up_share"] == 1.0
    t = data["totals"]
    assert t["all"]["posts_n"] == 162 and t["all"]["reactions"] == 5340 and t["all"]["suppressed"] == 1
    assert t["facebook"]["urls"] == 3 and t["instagram"]["urls"] == 1 and t["instagram"]["found"] == 0
    assert t["all"]["views"] == 90000 and t["all"]["views_given"] == 1
    countries = {c["id"]: c for c in data["by_country"]}
    assert set(countries) == {"ITA", "WLD"} and countries["WLD"]["posts_n"] == 150
    assert countries["ITA"]["urls"] == 2 and countries["ITA"]["found"] == 2 and countries["ITA"]["share_found"] == 1.0
    routes = {r["id"]: r for r in data["by_route"]}
    assert routes["distribution_stamp"]["label"] and routes["sponsored_disclosure"]["suppressed"] == 1
    assert data["by_outlet"][0]["id"] == "us_wire" and data["by_outlet"][0]["country"] == "WLD"
    assert data["outlet_months"] == [{"outlet_id": "it_paper", "name": "Paper", "country": "ITA", "month": "2026-10",
                                      "platform": "facebook", "suppressed": False, "posts_n": 40, "reactions": 900,
                                      "shares": 50, "comments": 30, "views": None, "query_date": "2026-10-14"}]
    # never an address, a headline or a single article's key
    text = json.dumps(data)
    assert "http" not in text and "Headline" not in text and HASHES[0] not in text
    # China is never a country here
    assert "CHN" not in countries


def test_methodology_text():
    t = eng.methodology_text({"status": "provisional"})
    assert "reach, not supply" in t and "lower bounds" in t and "1 to 4 posts" in t
    assert "\u2014" not in t and "\u2013" not in t and " - " not in t
    assert "Category" not in t
    assert len(t.split("\n\n")) == 4


def test_hand_check_sample_and_score(db, tmp_path):
    eng.import_file(db, _counts(db, tmp_path))
    csv_path, key_path = eng.draw_sample(db, 60, "s1", tmp_path / "reviews")
    key = json.loads(key_path.read_text())
    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    # the unknown hash is not sampled; the coder sees the address, never the stored figures
    assert len(rows) == 5 and key["n"] == 5 and key["measure_version"] == eng.VERSION
    assert list(rows[0]) == eng.SAMPLE_FIELDS
    assert all(r["posts_band"] == "" for r in rows)
    assert "posts_n" not in rows[0] and "reactions" not in rows[0]
    assert sorted(key["answers"].values()) == sorted(["5_19", "none", "few", "100_plus", "20_99"])
    by = {r["sample_id"]: r for r in rows}
    assert all(by[s]["url"].startswith("https://") for s, a in key["answers"].items() if by[s]["key_type"] == "url_hash")
    # incomplete, then complete and agreeing, then one disagreement in five
    assert eng.score_check(rows, key)["coded"] == 0 and eng.score_check(rows, key)["passed"] is None
    for r in rows:
        r["posts_band"] = key["answers"][r["sample_id"]]
    res = eng.score_check(rows, key)
    assert (res["value"], res["passed"], res["n"], res["coded"]) == (1.0, True, 5, 5)
    rows[0]["posts_band"] = "none" if key["answers"][rows[0]["sample_id"]] != "none" else "few"
    res = eng.score_check(rows, key)
    assert res["value"] == 0.8 and res["passed"] is True
    rows[1]["posts_band"] = "lots"           # not a band: not coded
    assert eng.score_check(rows, key)["coded"] == 4
    # the gate scores it as the export would
    st, chk, _ = measures.assess(eng, "engagement", tmp_path / "reviews")
    assert st == "provisional" and chk["state"] == "incomplete"


def test_sample_is_at_most_twenty(db, tmp_path):
    hs = []
    for i in range(30):
        _, h = _article(db, "https://paper.it/x%d" % i, "it_paper", "ITA", "2026-10-03")
        hs.append(h)
    db.commit()
    eng.import_file(db, _write(tmp_path / "counts_many.csv", [_row(key=h) for h in hs]))
    csv_path, _ = eng.draw_sample(db, 60, "s2", tmp_path / "reviews")
    with open(csv_path, encoding="utf-8") as fh:
        assert len(list(csv.DictReader(fh))) == eng.SAMPLE_N == 20


def test_bands():
    assert [eng.band_of(n, 0) for n in (0, 5, 19, 20, 99, 100)] == ["none", "5_19", "5_19", "20_99", "20_99", "100_plus"]
    assert eng.band_of(None, 1) == "few"
