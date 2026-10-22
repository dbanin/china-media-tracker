"""Country notes and panel caption after decision D1 as revised (2026-10-22): a state origin label whose
state authorship the evidence cannot settle is undetermined (code U) and excluded from every count, as in
the original study: the model's calls with no checkable marker (route unattributed) and its sponsored
disclosure calls the rules did not confirm, and its uncredited copies of items that are undetermined
themselves (the audit files do not carry the lookalike reference that decides these, so no check here depends
on them). The hand-written notes for Chad, Malaysia, Singapore, Brazil,
Peru, Laos and Hong Kong, the computer FAQ, the side panel caption and the page's Australian duplicate
figures had described the old definitions. Each corrected note states its dates, and every claim it makes
about carriers or timing is checked here against the committed audit files (data/export/articles-*.jsonl),
read with pipeline.model_only's own test for an undetermined label. Only the files dated inside each
note's stated days are read, so later days never move these checks."""
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
    evidence routes and the undetermined set exactly as pipeline.model_only defines it."""
    lo, hi = window
    files = [f for f in sorted(AUDIT.glob("articles-*.jsonl")) if lo <= f.stem[len("articles-"):] <= hi]
    if not files or files[-1].stem != "articles-" + hi:
        pytest.skip("the committed audit files for these days are not in this checkout")
    rows = {"A": [], "U": []}
    for f in files:
        day = f.stem[len("articles-"):]
        for line in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("category") != "A" or r.get("counted_as"):
                continue
            r["_day"] = day
            rows["U" if model_only.is_undetermined(r["category"], r.get("route"), r.get("method")) else "A"].append(r)
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
    m = _by(all_time["U"], "TCD", lambda r: r["outlet_id"])
    assert sum(m.values()) > 0 and m["td_alwihda"] / sum(m.values()) >= 0.9   # "nearly all"
    note = NOTES["TCD"]
    assert "carried mainly by one outlet" not in note and "rests on the model's judgement:" not in note
    assert "from 3 September to 21 October 2026" in note and "Alwihda Info" in note
    assert "undetermined and excluded from every count" in note and "state origin count for those dates is zero" in note
    assert "shown apart as provisional" not in note


@pytest.mark.parametrize("window", ["all_time", "last_30"])
@_local_only
def test_malaysia_carrier_route_and_undetermined_share(window, request):
    rows = request.getfixturevalue(window)
    carriers = _by(rows["A"], "MYS", lambda r: r["outlet_id"])
    routes = _by(rows["A"], "MYS", lambda r: r.get("route"))
    held = _by(rows["U"], "MYS", lambda r: r["outlet_id"])
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
    assert "undetermined, excluded from every count" in note and "provisional" not in note
    assert "Bernama" in note and "https://www.bernama.com/en/news.php?id=2255080" in note


@_local_only
def test_singapore_carriers_in_the_stated_window(last_30, all_time):
    for rows in (last_30, all_time):
        carriers = _by(rows["A"], "SGP", lambda r: r["outlet_id"])
        assert set(carriers) == {"sg_straitstimes", "sg_cna"}           # "carried by two outlets"
    # "Many of Singapore's state origin labels, at both outlets, rest on the model's judgement alone": read
    # from the stored routes alone, kinds (a) and (b). The copies of undetermined items, kind (c), only add to
    # these, and the audit files do not carry the lookalike reference that decides them, so nothing here
    # depends on them (in the 30 days The Straits Times and CNA are about tied once they are left out).
    for rows in (last_30, all_time):
        carriers = _by(rows["A"], "SGP", lambda r: r["outlet_id"])
        held = _by(rows["U"], "SGP", lambda r: r["outlet_id"])
        assert held["sg_straitstimes"] > 0 and held["sg_cna"] > 0
        assert sum(held.values()) > sum(carriers.values())
    note = NOTES["SGP"]
    assert "carried mainly by CNA" not in note and "provisional" not in note
    assert "carried more" not in note and not re.search(r"\d", note)   # count free: the two are about tied
    assert "Singapore's count is carried by two outlets, The Straits Times and CNA (Channel News Asia)." in note
    assert "rest on the model's judgement alone" in note
    assert "uncredited copies of items that are undetermined themselves" in note
    assert "undetermined, excluded from every count" in note
    assert "excluded under the project's language rule" in note and "I found no public agreement to cite" in note
    assert "route" not in note                                         # the stress test rule for this note


@_local_only
def test_peru_has_no_state_origin_and_its_sponsored_calls_are_undetermined(all_time):
    assert not [r for r in all_time["A"] if r["country"] == "PER"]
    sponsored = [r for r in all_time["U"] if r["country"] == "PER" and r.get("route") == "sponsored_disclosure"]
    assert len(sponsored) == 7 and {r["outlet_id"] for r in sponsored} == {"pe_larepublica"}   # "Seven placements at La República"
    assert {r["_day"] for r in sponsored} == {"2026-09-15"}                                     # "all on one day"
    assert all(r["method"] != "rules" for r in sponsored)
    note = NOTES["PER"]
    assert "To 21 October 2026, Peru's state origin count is zero." in note
    assert "Seven placements at La República" in note and "all on one day, 15 September 2026" in note
    assert "undetermined and excluded from every count" in note


@_local_only
def test_brazil_counts_exame_alone_and_its_sponsored_calls_are_undetermined(all_time):
    a = [r for r in all_time["A"] if r["country"] == "BRA"]
    assert a and {r["outlet_id"] for r in a} == {"br_exame"}                                    # "Exame alone"
    assert {r["route"] for r in a} == {"wire_credit", "distribution_stamp"}                     # "wire credits and press release stamps"
    sponsored = [r for r in all_time["U"] if r["country"] == "BRA" and r.get("route") == "sponsored_disclosure"]
    assert len(sponsored) == 5 and Counter(r["outlet_id"] for r in sponsored) == Counter({"br_exame": 4, "br_valor": 1})
    assert all(r["_day"].startswith("2026-09-") for r in sponsored)
    note = NOTES["BRA"]
    assert "Exame alone" in note and "four at Exame and one at Valor, all of which ran in September 2026" in note


@_local_only
def test_laos_counts_the_laotian_times_and_its_sponsored_calls_are_undetermined(all_time):
    a = [r for r in all_time["A"] if r["country"] == "LAO"]
    assert a and {r["outlet_id"] for r in a} == {"la_laotiantimes"}                              # "all of Laos's state origin count"
    assert sum(1 for r in a if r["route"] == "distribution_stamp") / float(len(a)) > 0.5          # "mostly through ... stamps"
    u = [r for r in all_time["U"] if r["country"] == "LAO"]
    assert {r["outlet_id"] for r in u} == {"la_laotiantimes"} and len(u) > 3 * len(a)              # "Many more"
    assert all(r.get("route") == "sponsored_disclosure" for r in u)
    note = NOTES["LAO"]
    assert "From 3 September to 21 October 2026" in note and "undetermined and excluded from every count" in note
    assert "mostly through disclosed sponsored placements" not in note


def test_hong_kong_and_the_computer_faq_say_such_calls_are_undetermined():
    hk = NOTES["HKG"]
    assert "those the model judged state origin with none of these are undetermined and excluded from every count" in hk
    assert "route" not in hk and "provisional" not in hk
    computer = next(f["answer"] for f in CONTENT["faq"] if f["question"] == "Is a computer deciding this?")
    assert "many state origin labels" in computer
    assert "is undetermined: who wrote or commissioned the text cannot be settled" in computer
    assert "excluded from every count and never rejoins state origin" in computer
    assert "until a hand check of such calls passes" not in computer


def test_side_panel_caption_names_no_row_count_and_every_kind_of_row():
    cap = CONTENT["captions"]["side_panel_labels"]
    assert not re.search(r"\b(\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen) rows\b", cap)
    for part in ("state origin, with the underlying items among it", "undetermined items", "excluded from every count", "unchecked state sourcing",
                 "still waiting to be judged", "independent journalism", "not about China", "paywalled",
                 "fetch failed", "robots.txt", "rules stage", "add up to every article that passed the gate"):
        assert part in cap, part
    # The rows the caption describes are the ones the panel renders.
    for label in ("Underlying items among them", "esc(UNDETERMINED_ROW)", "(k.U || 0)", "Paywalled, unread and uncounted",
                  "Fetch failed after retries", "Blocked by the site's robots.txt", "waiting for the rules stage"):
        assert label in APP, label
    # The time chart sits above the breakdown in the panel, as the caption now says.
    assert APP.index('<svg class="mini" id="mini">') < APP.index("'<h3>Breakdown, '")


@_local_only
def test_australian_duplicate_figures_say_which_definition_they_used(all_time):
    assert "Both figures include placements now undetermined and excluded from every count" in INDEX
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
                    model_only.is_undetermined(r["category"], r.get("route"), r.get("method")):
                continue
            per_masthead += 1
            counted += 0 if r.get("counted_as") else 1
    assert counted and 9 <= per_masthead / counted <= 11
    # "in Australia all model calls with no checkable marker": no Australian undetermined label is a sponsored call.
    assert not [r for r in all_time["U"] if r["country"] == "AUS" and r.get("route") == "sponsored_disclosure"]


def test_country_notes_keep_the_site_rules():
    for iso, note in NOTES.items():
        assert "—" not in note and "–" not in note, iso
        assert not re.search(r"\b(we|We|our|Our|us)\b", note), iso
        assert "Category A" not in note and "unattributed" not in note, iso
