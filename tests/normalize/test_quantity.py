import pytest

from normalize.quantity import parse_measures

A = pytest.approx


def _r(x):
    return A(x, rel=1e-3)


def test_fl_oz():
    assert parse_measures(["52 fl oz"], "volume")["volume_ml"] == _r(1537.82)


def test_bare_oz_in_volume_is_fluid():
    t = "fairlife 2% Reduced-Fat Ultra-Filtered 52oz Milk Bottle"
    assert parse_measures([t], "volume")["volume_ml"] == _r(1537.82)


def test_fz_abbreviation():
    t = "365 Organic Reduced Fat 2% Milk, 32 FZ"
    assert parse_measures([t], "volume")["volume_ml"] == _r(946.35)


def test_litres():
    t = "Lactaid Reduced Fat 2% Milk, 2.8 L"
    assert parse_measures([t], "volume")["volume_ml"] == A(2800)


def test_gallon():
    assert parse_measures(["1 gal"], "volume")["volume_ml"] == _r(3785.41)


def test_multipack_x():
    assert parse_measures(["6 x 12 fl oz"], "volume")["volume_ml"] == _r(2129.29)


def test_pack_of():
    assert parse_measures(["Pack of 2, 64 oz"], "volume")["volume_ml"] == _r(3785.41)


def test_pk():
    assert parse_measures(["2 pk 64 fl oz"], "volume")["volume_ml"] == _r(3785.41)


def test_mass_lb():
    assert parse_measures(["2 lb bag"], "mass")["mass_g"] == _r(907.18)


def test_mass_oz_is_weight():
    assert parse_measures(["16 oz"], "mass")["mass_g"] == _r(453.59)


def test_count_dozen():
    assert parse_measures(["Large White Eggs, 1 dozen"], "count")["count"] == 12


def test_count_ct_and_count():
    assert parse_measures(["12 ct"], "count") == {"count": 12}
    assert parse_measures(["18 count"], "count") == {"count": 18}


def test_count_eggs():
    assert parse_measures(["18 eggs"], "count")["count"] == 18


def test_paper_equivalence_ignored():
    t = (
        "Charmin Ultra Strong Toilet Paper, 18 Mega Rolls = 72 Regular Rolls, "
        "242 sheets per roll, 4 in x 3.92 in"
    )
    m = parse_measures([t], "paper")
    assert m["rolls"] == 18
    assert m["sheets"] == 4356
    assert m["area_m2"] == _r(44.07)


def test_paper_rolls_only():
    m = parse_measures(["Scott 1000 Toilet Paper, 12 Rolls"], "paper")
    assert m == {"rolls": 12}


def test_paper_explicit_area():
    m = parse_measures(["Paper Towels, 6 Rolls, 112.5 sq ft"], "paper")
    assert m["rolls"] == 6
    assert m["area_m2"] == _r(10.452)


def test_first_parseable_text_wins():
    m = parse_measures(["", "64 fl oz", "1 gal"], "volume")
    assert m["volume_ml"] == _r(1892.71)


def test_unparseable_returns_empty():
    assert parse_measures(["Organic Milk"], "volume") == {}


def test_paper_sheets_per_roll_not_taken_as_rolls():
    m = parse_measures(["242 sheets per roll, 12 Rolls"], "paper")
    assert m["rolls"] == 12
    assert m["sheets"] == 2904


def test_paper_no_n_rolls_phrase_returns_empty():
    t = "Scott 1000 Toilet Paper Rolls, 12 Count"
    assert parse_measures([t], "paper") == {}
