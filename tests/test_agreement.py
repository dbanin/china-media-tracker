from pipeline import agreement


def test_kappa_perfect():
    assert agreement.cohens_kappa([("A", "A"), ("B", "B"), ("C", "C")]) == 1.0


def test_kappa_chance():
    # two raters, each half A half B, independent: kappa near 0
    pairs = [("A", "A"), ("A", "B"), ("B", "A"), ("B", "B")]
    assert abs(agreement.cohens_kappa(pairs)) < 1e-9


def test_kappa_known_value():
    # 20 items, 13 B/B, 2 B/C, 3 C/B, 2 C/C: po=0.75, pe = (15/20)(16/20)+(5/20)(4/20)=0.65 -> kappa 0.2857
    pairs = [("B", "B")] * 13 + [("B", "C")] * 2 + [("C", "B")] * 3 + [("C", "C")] * 2
    assert abs(agreement.cohens_kappa(pairs) - 0.2857) < 0.001


def test_stratified_sample_balances():
    rows = [{"category": "A", "id": i} for i in range(5)] + [{"category": "B", "id": i} for i in range(100, 150)] + \
           [{"category": "C", "id": i} for i in range(200, 300)] + [{"category": "not_relevant", "id": i} for i in range(300, 400)]
    picked = agreement.stratified_sample(rows, 40, seed=1)
    assert len(picked) == 40
    cats = [p["category"] for p in picked]
    assert cats.count("A") == 5 and cats.count("B") >= 10 and cats.count("C") >= 10


def test_compute_bc_subset():
    items = [{"machine_category": "B", "human_category": "B"}, {"machine_category": "B", "human_category": "C"},
             {"machine_category": "C", "human_category": "C"}, {"machine_category": "A", "human_category": "A"},
             {"machine_category": "not_relevant", "human_category": "not_relevant"}]
    res = agreement.compute_from_pairs(items)
    assert res["n"] == 5 and res["n_bc"] == 3
    assert res["kappa_all"] is not None
    # Two B items from one coder and one from the other cannot support a kappa on B versus C.
    assert res["kappa_bc"] is None and "at least %d" % agreement.MIN_POSITIVE_FOR_KAPPA in res["kappa_bc_inconclusive"]


def test_compute_bc_reports_a_kappa_once_both_coders_used_b_enough():
    m = agreement.MIN_POSITIVE_FOR_KAPPA
    items = [{"machine_category": "B", "human_category": "B"} for _ in range(m)] + \
            [{"machine_category": "B", "human_category": "C"} for _ in range(3)] + \
            [{"machine_category": "C", "human_category": "C"} for _ in range(20)]
    res = agreement.compute_from_pairs(items)
    assert res["kappa_bc"] is not None and res["kappa_bc_inconclusive"] is None
    assert res["kappa_min_positive"] == m


# ---------------------------------------------------------------------------
# A sample that cannot support a kappa reports no kappa, not a flattering one
# ---------------------------------------------------------------------------

def test_a_sample_with_no_b_reports_nothing_rather_than_one():
    """300 pairs of notB against notB used to come back as 1.0, which cleared the publication
    threshold on a study that never tested the relay versus independent distinction."""
    pairs = [("notB", "notB")] * 300
    assert agreement.cohens_kappa(pairs) is None
    assert agreement.cohens_kappa(pairs, min_positive=agreement.MIN_POSITIVE_FOR_KAPPA, positive="B") is None
    assert "single label" in agreement.kappa_shortfall(pairs) or "at least" in agreement.kappa_shortfall(pairs)


def test_one_disagreement_in_300_does_not_withhold_by_scoring_zero():
    """The mirror case used to come back as 0.0 and withhold counts that are fine."""
    pairs = [("notB", "notB")] * 299 + [("B", "notB")]
    assert agreement.cohens_kappa(pairs) == 0.0, "the formula itself is unchanged"
    assert agreement.cohens_kappa(pairs, min_positive=agreement.MIN_POSITIVE_FOR_KAPPA, positive="B") is None
    assert "1 of 300 pairs carry B from the first rater and 0 from the second" in agreement.kappa_shortfall(pairs)


def test_the_minimum_applies_to_both_raters():
    m = agreement.MIN_POSITIVE_FOR_KAPPA
    enough = [("B", "B")] * m + [("notB", "notB")] * 50
    assert agreement.cohens_kappa(enough, min_positive=m, positive="B") is not None
    one_sided = [("B", "notB")] * m + [("notB", "notB")] * 50
    assert agreement.cohens_kappa(one_sided, min_positive=m, positive="B") is None
    assert agreement.cohens_kappa([], min_positive=m, positive="B") is None
