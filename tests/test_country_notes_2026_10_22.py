"""Country notes and panel caption after decision D1 (2026-10-22): state origin that rests on the model's
judgement alone (route unattributed, code M) is held apart from state origin. The hand-written notes for
Chad, Malaysia, Singapore, Brazil, Peru and Hong Kong, the computer FAQ, the side panel caption and the
page's Australian duplicate figures had described the old definition. Each corrected note states its
dates, and every claim it makes about carriers or timing is checked here against the committed audit
files (data/export/articles-*.jsonl), read with pipeline.model_only's own test for a model-only label.
Only the files dated inside each note's stated days are read, so later days never move these checks."""
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from pipeline import model_only

ROOT = Path(__file__).resolve().parents[1]
CONTENT = json.loads((ROOT / "docs" / "content.json").read_text(encoding="utf-8"))
NOTES = CONTENT["country_notes"]
INDEX = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
AUDIT = ROOT / "data" / "export"
# The audit files are rewritten as labels are redone, so a dated claim can move; a test failing in the
# pipeline workflow turns that day's export into a collect run. The checks against the data run locally.
_local_only = pytest.mark.skipif(os.environ.get("GITHUB_ACTIONS") == "true",
                                 reason="reads audit files that relabelling rewrites; run locally")

ALL_TIME = ("2026-09-03", "2026-10-21")   # "from 3 September to 21 October 2026"
LAST_30 = ("2026-09-22", "2026-10-21")    # "the 30 complete days ending 21 October 2026"


def _placements(window):
    """Current state origin placements in the window, each counted once (no counted_as), split into the
    evidence routes and the model-only set exactly as pipeline.model_only defines it."""
    lo, hi = window
    files = [f for f in sorted(AUDIT.glob("articles-*.jsonl")) if lo <= f.stem[len("articles-"):] <= hi]
    if not files or files[-1].stem != "articles-" + hi:
        pytest.skip("the committed audit files for these days are not in this checkout")
    rows = {"A": [], "M": []}
    for f in files:
        day = f.stem[len("articles-"):]
        for line in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("category") != "A" or r.get("counted_as"):
                continue
            r["_day"] = day
            rows["M" if model_only.is_model_only(r["category"], r.get("route"), r.get("method")) else "A"].append(r)
    return rows


def _by(rows, iso, key):
    return Counter(key(r) for r in rows if r["country"] == iso)


@pytest.fixture(scope="module")
def all_time():
    return _placements(ALL_TIME)


@pytest.fixture(scope="module")
def last_30():
    return _placements(LAST_30)


@_local_only
def test_chad_has_no_state_origin_with_evidence_and_its_note_says_so(all_time):
    assert sum(_by(all_time["A"], "TCD", lambda r: 1).values()) == 0
    m = _by(all_time["M"], "TCD", lambda r: r["outlet_id"])
    assert sum(m.values()) > 0 and m["td_alwihda"] / sum(m.values()) >= 0.9   # "nearly all"
    note = NOTES["TCD"]
    assert "carried mainly by one outlet" not in note and "rests on the model's judgement:" not in note
    assert "from 3 September to 21 October 2026" in note and "Alwihda Info" in note
    assert "shown apart as provisional" in note and "state origin count for those dates is zero" in note


@pytest.mark.parametrize("window", ["all_time", "last_30"])
@_local_only
def test_malaysia_carrier_route_and_model_only_share(window, request):
    rows = request.getfixturevalue(window)
    carriers = _by(rows["A"], "MYS", lambda r: r["outlet_id"])
    routes = _by(rows["A"], "MYS", lambda r: r.get("route"))
    held = _by(rows["M"], "MYS", lambda r: r["outlet_id"])
    n = sum(carriers.values())
    assert carriers.most_common(1)[0][0] == "my_utusan" and carriers["my_bernama"] == 0
    assert routes["distribution_stamp"] / n > 0.5                     # "mostly through the press-release-stamp route"
    assert sum(held.values()) >= 3 * n                                 # "several times as many"
    assert held["my_utusan"] / sum(held.values()) > 0.5                # "most of them also at Utusan"


def test_malaysia_note_no_longer_counts_the_unattributed_route():
    note = NOTES["MYS"]
    assert "unattributed" not in note and "in this window" not in note
    assert "from 3 September to 21 October 2026" in note and "mostly through the press-release-stamp route" in note
    assert "rest on the model's judgement alone" in note and "not in its state origin count" in note
    assert "Bernama" in note and "https://www.bernama.com/en/news.php?id=2255080" in note


@_local_only
def test_singapore_carriers_in_the_stated_window(last_30, all_time):
    for rows in (last_30, all_time):
        carriers = _by(rows["A"], "SGP", lambda r: r["outlet_id"])
        assert set(carriers) == {"sg_straitstimes", "sg_cna"}           # "carried by two outlets"
    carriers = _by(last_30["A"], "SGP", lambda r: r["outlet_id"])
    assert carriers["sg_straitstimes"] > carriers["sg_cna"]            # "The Straits Times carried more"
    held = _by(last_30["M"], "SGP", lambda r: r["outlet_id"])
    assert sum(held.values()) > sum(carriers.values())                 # "more ... rested on the model's judgement alone"
    assert held["sg_cna"] / sum(held.values()) > 0.5                    # "most of them at CNA"
    note = NOTES["SGP"]
    assert "carried mainly by CNA" not in note
    assert "The Straits Times and CNA (Channel News Asia)" in note
    assert "in the 30 complete days ending 21 October 2026 The Straits Times carried more" in note
    assert "route" not in note                                         # the stress test rule for this note


@_local_only
def test_peru_placements_all_ran_on_one_day_before_the_window(all_time, last_30):
    rows = [r for r in all_time["A"] if r["country"] == "PER"]
    assert rows and {r["outlet_id"] for r in rows} == {"pe_larepublica"}
    assert {r["route"] for r in rows} == {"sponsored_disclosure"}
    assert {r["_day"] for r in rows} == {"2026-09-15"}
    assert not [r for r in last_30["A"] if r["country"] == "PER"]
    note = NOTES["PER"]
    assert "mostly through" not in note
    assert "To 21 October 2026" in note and "all ran on one day, 15 September 2026" in note


@_local_only
def test_brazil_sponsored_placements_all_ran_in_september(all_time):
    carriers = _by(all_time["A"], "BRA", lambda r: r["outlet_id"])
    assert carriers.most_common(1)[0][0] == "br_exame" and carriers["br_exame"] / sum(carriers.values()) > 0.5
    sponsored = [r["_day"] for r in all_time["A"] if r["country"] == "BRA" and r.get("route") == "sponsored_disclosure"]
    assert sponsored and all(d.startswith("2026-09-") for d in sponsored)
    assert "all of which ran in September 2026" in NOTES["BRA"]


def test_hong_kong_and_the_computer_faq_say_model_only_calls_are_held_apart():
    hk = NOTES["HKG"]
    assert "those the model judged state origin with none of these are shown apart as provisional" in hk
    assert "route" not in hk
    computer = next(f["answer"] for f in CONTENT["faq"] if f["question"] == "Is a computer deciding this?")
    assert "many state origin labels" in computer
    assert "is shown apart as provisional and kept out of state origin until a hand check of such calls passes" in computer


def test_side_panel_caption_names_no_row_count_and_every_kind_of_row():
    cap = CONTENT["captions"]["side_panel_labels"]
    assert not re.search(r"\b(\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen) rows\b", cap)
    for part in ("state origin, with the underlying items among it", "model's judgement alone", "unchecked state sourcing",
                 "still waiting to be judged", "independent journalism", "not about China", "paywalled",
                 "fetch failed", "robots.txt", "rules stage", "add up to every article that passed the gate"):
        assert part in cap, part
    # The rows the caption describes are the ones the panel renders.
    for label in ("Underlying items among them", "modelOnlyShown(k.M)", "Paywalled, unread and uncounted",
                  "Fetch failed after retries", "Blocked by the site's robots.txt", "waiting for the rules stage"):
        assert label in APP, label
    # The time chart sits above the breakdown in the panel, as the caption now says.
    assert APP.index('<svg class="mini" id="mini">') < APP.index("'<h3>Breakdown, '")


@_local_only
def test_australian_duplicate_figures_say_which_definition_they_used(all_time):
    assert "Both figures include placements resting on the model's judgement alone" in INDEX
    assert "without them the fall is also tenfold" in INDEX
    # Placements per masthead against placements counted once, evidence routes only, 3 September to 15 October.
    lo, hi = "2026-09-03", "2026-10-15"
    per_masthead = counted = 0
    for f in sorted(AUDIT.glob("articles-*.jsonl")):
        if not lo <= f.stem[len("articles-"):] <= hi:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("country") != "AUS" or r.get("category") != "A" or \
                    model_only.is_model_only(r["category"], r.get("route"), r.get("method")):
                continue
            per_masthead += 1
            counted += 0 if r.get("counted_as") else 1
    assert counted and 9 <= per_masthead / counted <= 11


def test_country_notes_keep_the_site_rules():
    for iso, note in NOTES.items():
        assert "—" not in note and "–" not in note, iso
        assert not re.search(r"\b(we|We|our|Our|us)\b", note), iso
        assert "Category A" not in note and "unattributed" not in note, iso
