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
    assert res["kappa_all"] is not None and res["kappa_bc"] is not None
