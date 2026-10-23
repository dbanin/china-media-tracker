"""Country notes that name a carrier or a route but no dates (Indonesia, Italy, South Africa, Guyana, Mali),
checked against every committed audit file after the export of 2026-10-23, which applied 1,300 session labels
to articles that were waiting for the model. Read with pipeline.model_only's own test for an undetermined
label; rows with counted_as are copies and are skipped. Each check reads the shape of a claim (largest
carrier, majority route), not a count, so more labels do not break it while the claim stays true."""
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from pipeline import model_only

ROOT = Path(__file__).resolve().parents[1]
NOTES = json.loads((ROOT / "docs" / "content.json").read_text(encoding="utf-8"))["country_notes"]
AUDIT = ROOT / "data" / "export"
_local_only = pytest.mark.skipif(os.environ.get("GITHUB_ACTIONS") == "true",
                                 reason="reads audit files that relabelling rewrites; run locally")


@pytest.fixture(scope="module")
def counted():
    files = sorted(AUDIT.glob("articles-*.jsonl"))
    if not files:
        pytest.skip("the committed audit files are not in this checkout")
    rows = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("category") != "A" or r.get("counted_as"):
                continue
            if model_only.headline_category(r["category"], r.get("route"), r.get("method")) == "A":
                rows.append(r)
    return rows


def _of(rows, iso):
    rs = [r for r in rows if r["country"] == iso]
    assert rs, iso
    return rs, Counter(r["outlet_id"] for r in rs), Counter(r.get("route") for r in rs)


@_local_only
def test_indonesia_is_carried_by_antara_mostly_through_wire_credits(counted):
    rs, outlets, routes = _of(counted, "IDN")
    assert (outlets["id_antara"] + outlets["id_antara_en"]) / float(len(rs)) > 0.5    # "carried mainly by ... Antara"
    assert routes["wire_credit"] / float(len(rs)) > 0.5                             # "mostly through the wire-credit route"
    assert "carried mainly by the national news agency Antara" in NOTES["IDN"]


@_local_only
def test_italy_is_mostly_italpress(counted):
    rs, outlets, _ = _of(counted, "ITA")
    assert outlets["it_italpress"] / float(len(rs)) > 0.5                           # "Most of Italy's state-origin count"
    assert NOTES["ITA"].startswith("Most of Italy's state-origin count comes from one outlet, Italpress")


@_local_only
def test_south_africa_largest_carrier_is_iol(counted):
    _, outlets, _ = _of(counted, "ZAF")
    top = outlets.most_common(2)
    assert top[0][0] == "za_iol" and (len(top) == 1 or top[0][1] > top[1][1])       # "largest carrier", no tie
    assert NOTES["ZAF"].startswith("South Africa's largest carrier is IOL")


@_local_only
def test_guyana_is_almost_entirely_village_voice_mostly_through_wire_credits(counted):
    rs, outlets, routes = _of(counted, "GUY")
    assert outlets["gy_villagevoice"] / float(len(rs)) >= 0.9                       # "almost entirely one outlet"
    assert routes["wire_credit"] / float(len(rs)) > 0.5
    assert "almost entirely one outlet, Village Voice News, mostly through the wire-credit route" in NOTES["GUY"]


@_local_only
def test_mali_is_carried_mainly_by_malijet_mostly_through_wire_credits(counted):
    rs, outlets, routes = _of(counted, "MLI")
    assert outlets["ml_malijet"] / float(len(rs)) > 0.5
    assert routes["wire_credit"] / float(len(rs)) > 0.5
    assert "carried mainly by Malijet, mostly through the wire-credit route" in NOTES["MLI"]
