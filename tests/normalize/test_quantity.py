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


def test_paper_bare_sheets_read_as_per_roll_when_rolls_present():
    m = parse_measures(["Bath Tissue, 2-Ply, 380 Sheets, 30 Rolls"], "paper")
    assert m["rolls"] == 30
    assert m["sheets"] == 11400


def test_paper_bare_sheets_without_rolls_not_guessed():
    assert parse_measures(["Paper Towels, 380 Sheets"], "paper") == {}


def test_paper_thousands_separator_with_total():
    m = parse_measures(["Bath Tissue 1,000 sheets total, 12 Rolls"], "paper")
    assert m["rolls"] == 12
    assert m["sheets"] == 1000


def test_paper_thousands_separator_bare_is_per_roll():
    m = parse_measures(["12 Rolls, 2,400 Sheets"], "paper")
    assert m["sheets"] == 28800


def test_paper_total_sheets_not_multiplied():
    m = parse_measures(["12 Rolls, 2,400 sheets total"], "paper")
    assert m["sheets"] == 2400


def test_paper_zero_sheets_never_emitted():
    m = parse_measures(["12 Rolls, 0 sheets"], "paper")
    assert "sheets" not in m


def test_paper_hyphenated_sheet_token_not_sheet_count():
    m = parse_measures(["3-sheet Paper Towels, 6 Rolls"], "paper")
    assert "sheets" not in m


def test_fraction_gallon():
    assert parse_measures(["Horizon Organic Whole Milk, 1/2 gal"], "volume")[
        "volume_ml"
    ] == _r(1892.71)


def test_half_dozen():
    assert parse_measures(["Large Eggs, 1/2 Dozen"], "count")["count"] == A(6)


def test_fraction_pound():
    assert parse_measures(["Coffee 3/4 lb"], "mass")["mass_g"] == _r(340.19)


def test_mixed_number_gallon():
    assert parse_measures(["Milk 1 1/2 gal"], "volume")["volume_ml"] == _r(5678.12)


def test_comma_grouped_ml():
    assert parse_measures(["Milk 1,500 mL"], "volume")["volume_ml"] == A(1500)


def test_comma_grouped_grams():
    assert parse_measures(["Coffee 1,000 g"], "mass")["mass_g"] == A(1000)


def test_zero_quantity_is_unresolved():
    assert parse_measures(["Coffee 0 g"], "mass") == {}


def test_unparsable_number_before_dozen_is_unresolved():
    assert parse_measures(["Eggs 1,5 Dozen"], "count") == {}


def test_quart_pint_ml_kg_g():
    assert parse_measures(["Cream 1 qt"], "volume")["volume_ml"] == _r(946.35)
    assert parse_measures(["Cream 1 pt"], "volume")["volume_ml"] == _r(473.18)
    assert parse_measures(["Milk 750 mL"], "volume")["volume_ml"] == A(750)
    assert parse_measures(["Flour 2 kg"], "mass")["mass_g"] == A(2000)
    assert parse_measures(["Sugar 500 g"], "mass")["mass_g"] == A(500)


def test_paper_multipack_is_unresolved():
    assert parse_measures(["Toilet Paper 12 Rolls 2-Ply, 2 Pack"], "paper") == {}
    assert parse_measures(["Toilet Paper, Pack of 2, 12 Rolls"], "paper") == {}
    assert parse_measures(["Toilet Paper 2 x 12 Rolls"], "paper") == {}


def test_vulgar_fractions_are_not_multiplied():
    assert parse_measures(["Milk ½ gal"], "volume")["volume_ml"] == _r(1892.71)
    assert parse_measures(["Milk 1½ gal"], "volume")["volume_ml"] == _r(5678.12)
    assert parse_measures(["Eggs ½ Dozen"], "count")["count"] == 6
    assert parse_measures(["Coffee ¾ lb"], "mass")["mass_g"] == _r(340.19)


def test_paper_sheet_dimensions_are_not_multipack():
    out = parse_measures(
        ["Bounty Select-A-Size Paper Towels, 6 Double Rolls, 11 x 10.4 in"], "paper"
    )
    assert out["rolls"] == 6
    assert "area_m2" not in out  # no sheet count: area would be a guess
    out = parse_measures(["Paper Towels, 6 Double Rolls, 11x10.4 in"], "paper")
    assert out["rolls"] == 6
    out = parse_measures(['Paper Towels, 6 Rolls, 11 x 10.4"'], "paper")
    assert out["rolls"] == 6
    assert parse_measures(["Toilet Paper 12 Rolls 2-Ply, 2 Pack"], "paper") == {}


def test_paper_area_present_with_sheets_and_dimensions():
    out = parse_measures(
        ["Paper Towels, 6 Double Rolls, 100 sheets per roll, 11 x 10.4 in"], "paper"
    )
    assert out["rolls"] == 6 and out["area_m2"] > 0


def test_dozen_after_word_punctuation():
    assert parse_measures(["Grade A Large Eggs, Dozen"], "count")["count"] == 12
    assert parse_measures(["Eggs. Dozen"], "count")["count"] == 12
    assert parse_measures(["Eggs 1,5 Dozen"], "count") == {}


def test_hyphen_before_fraction_is_unparsable():
    assert parse_measures(["Milk 1-\u00bd gal"], "volume") == {}
    assert parse_measures(["Milk 1-1/2 gal"], "volume") == {}


def test_double_prime_inch_sheet_size():
    out = parse_measures(
        ["Paper Towels, 6 Rolls, 100 sheets per roll, 11 x 10.4\u2033"], "paper"
    )
    assert out["rolls"] == 6 and out["sheets"] == 600
    assert out["area_m2"] == pytest.approx(600 * 11 * 10.4 * 0.00064516, rel=1e-3)


def test_paren_before_dozen_is_unresolved():
    assert parse_measures(["Eggs (12) Dozen"], "count") == {}
