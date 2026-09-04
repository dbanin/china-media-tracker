"""Golden-file extraction tests on ten saved HTML pages across six languages."""
import gzip
import json
from pathlib import Path

import pytest

from pipeline import extract

FIX = Path(__file__).parent / "fixtures" / "extraction"
GOLDEN = json.loads((FIX / "golden.json").read_text(encoding="utf-8"))


def _norm(t: str) -> str:
    import re
    return re.sub(r"\s+", " ", t.replace("\xa0", " ")).strip()


def _similarity(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


@pytest.mark.parametrize("case", GOLDEN, ids=[c["name"] for c in GOLDEN])
def test_golden_extraction(case):
    with gzip.open(FIX / (case["name"] + ".html.gz"), "rt", encoding="utf-8") as fh:
        html = fh.read()
    expected = (FIX / (case["name"] + ".txt")).read_text(encoding="utf-8")
    ex = extract.extract(html, case["url"])
    assert ex["text"], "no text extracted"
    # trafilatura upgrades may shift boilerplate slightly; require near identity, not byte identity
    assert _similarity(ex["text"], expected) > 0.95, "extraction drifted for %s" % case["name"]
    assert abs(len(_norm(ex["text"])) - len(_norm(expected))) < 0.05 * len(_norm(expected)) + 50
    if case.get("author"):
        assert ex["author"] == case["author"]
    pw, _ = extract.detect_paywall(html, ex["text"])
    assert not pw


def test_languages_covered():
    assert len(GOLDEN) >= 10
    assert len({c["language"] for c in GOLDEN}) >= 5
