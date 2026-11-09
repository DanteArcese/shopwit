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


@pytest.mark.parametrize(
    "text,dim",
    [
        ("Milk 1-2 gal", "volume"),
        ("Milk 1 - 2 gal", "volume"),
        ("Milk 1–2 gal", "volume"),
        ("Milk 1 to 2 gal", "volume"),
        ("Chicken 1 - 2 lb", "mass"),
        ("Chicken 1-2 lb", "mass"),
        ("Chicken 1–2 lb", "mass"),
        ("Eggs 12-18 ct", "count"),
        ("Eggs 12 to 18 ct", "count"),
        ("Eggs 1–2 dozen", "count"),
    ],
)
def test_numeric_range_is_unparsable(text, dim):
    assert parse_measures([text], dim) == {}


def test_multipack_and_hyphenated_words_still_parse():
    assert parse_measures(["Water 2-pack 64 fl oz"], "volume")["volume_ml"] == pytest.approx(
        2 * 64 * 29.5735, rel=1e-3
    )
    assert parse_measures(["Water 64 fl oz, 2-Pack"], "volume")["volume_ml"] == pytest.approx(
        2 * 64 * 29.5735, rel=1e-3
    )
    assert parse_measures(["Soda 6 x 12 fl oz"], "volume")["volume_ml"] == pytest.approx(
        72 * 29.5735, rel=1e-3
    )
    assert parse_measures(["Soda 2 pk 12 fl oz"], "volume")["volume_ml"] == pytest.approx(
        24 * 29.5735, rel=1e-3
    )
    assert parse_measures(["Soda Pack of 2 12 fl oz"], "volume")["volume_ml"] == pytest.approx(
        24 * 29.5735, rel=1e-3
    )
    assert parse_measures(["Ultra-Filtered Milk 1 gal"], "volume")["volume_ml"] == pytest.approx(3785.41, rel=1e-3)


@pytest.mark.parametrize(
    "text", ["Cereal 1 lb 2 oz", "Cereal 1-lb 2 oz", "Cereal 1 lb. 2 oz.", "Cereal 1 LB 2 OZ"]
)
def test_compound_lb_oz_is_the_sum(text):
    assert parse_measures([text], "mass")["mass_g"] == pytest.approx(510.29, abs=0.01)


def test_compound_kg_g_is_the_sum():
    assert parse_measures(["Rice 1 kg 500 g"], "mass") == {"mass_g": pytest.approx(1500)}


def test_compound_respects_multipack():
    assert parse_measures(["Cereal 2 pack 1 lb 2 oz"], "mass")["mass_g"] == pytest.approx(
        2 * 510.29, abs=0.02
    )


@pytest.mark.parametrize(
    "text,dim",
    [
        ("Cereal 2 oz 1 lb", "mass"),  # smaller unit first
        ("Cereal 1 lb 2 fl oz", "mass"),  # mixed dimension
        ("Cereal 1 oz 2 oz", "mass"),  # same unit twice
        ("Cereal 1 kg 2 oz", "mass"),  # unsupported pair
        ("Juice 1 gal 2 fl oz", "volume"),
        ("Juice 1 qt 1 pt", "volume"),
    ],
)
def test_other_adjacent_pairs_are_absent(text, dim):
    assert parse_measures([text], dim) == {}


def test_separated_second_quantity_is_not_compound():
    assert parse_measures(["Cereal 12 oz (340 g)"], "mass")["mass_g"] == pytest.approx(340.19, abs=0.01)
    # A bare count beside a size is ambiguous (quantity guards): unresolved, not 340.19.
    assert parse_measures(["Cereal 12 oz, 6 ct"], "mass") == {}


@pytest.mark.parametrize(
    "text,dim,expected",
    [
        ("Coffee 12-oz bag", "mass", 340.19),
        ("Sugar 2-lb", "mass", 907.18),
        ("Water 64-fl-oz", "volume", 64 * 29.5735),
        ("Water 64-fl oz", "volume", 64 * 29.5735),
        ("Water 64 fl-oz", "volume", 64 * 29.5735),
    ],
)
def test_hyphenated_unit_forms(text, dim, expected):
    key = "mass_g" if dim == "mass" else "volume_ml"
    assert parse_measures([text], dim)[key] == pytest.approx(expected, rel=1e-3)


@pytest.mark.parametrize("text", ["Juice 1-2 gal", "Juice 1-2-gal", "Coffee 10-12-oz"])
def test_hyphenated_ranges_stay_unknown(text):
    dim = "mass" if "Coffee" in text else "volume"
    assert parse_measures([text], dim) == {}


# --- quantity guards (open-search prerequisite) -------------------------------
def _mass(t):
    return parse_measures([t], "mass")


def _vol(t):
    return parse_measures([t], "volume")


def test_nutrient_grams_not_pack_mass():
    t = "Premier Protein 100% Whey Protein Powder, Chocolate Milkshake, 30g Protein, 24.5 oz"
    assert _mass(t)["mass_g"] == _r(694.56)
    t = "Jif Creamy Peanut Butter, 7g Protein per Serving, 40 oz"
    assert _mass(t)["mass_g"] == _r(1133.98)


def test_nutrient_grams_with_words_between():
    t = "Orgain Organic Vegan Protein Powder, 21g Plant Based Protein, 2.03 lb"
    assert _mass(t)["mass_g"] == _r(920.79)


def test_nutrient_grams_only_is_unresolved():
    assert _mass("Whey Protein, 25g Protein, 0g Sugar") == {}


def test_plain_grams_still_mass():
    assert _mass("Kerrygold Pure Irish Butter, 227 g")["mass_g"] == _r(227)
    assert _mass("Myprotein Impact Whey, 907 g Whey Protein")["mass_g"] == _r(907)


def test_bare_count_beside_size_is_unresolved():
    assert _mass("Chobani Vanilla Greek Yogurt, 5.3 oz, 4 Count") == {}
    assert _vol("Horizon Organic Whole Milk, 8 fl oz, 12 ct") == {}


def test_one_count_is_harmless():
    t = "Folgers Classic Roast Ground Coffee, 12 oz, 1 Count"
    assert _mass(t)["mass_g"] == _r(340.19)


def test_pack_wins_over_bare_count():
    assert _mass("Yoplait Original Yogurt, 6 oz, 8 Pack, 8 Count")["mass_g"] == _r(1360.78)


def test_container_count_multiplies():
    assert _vol("16.9 fl oz, 40 Count Bottles")["volume_ml"] == _r(19991.7)
    assert _vol("12 fl oz, 12 Count Cans")["volume_ml"] == _r(4258.59)
    assert _vol("12 fl oz, 24 Cans")["volume_ml"] == _r(8517.18)


def test_pack_beats_container():
    assert _vol("12 fl oz, 12 Pack Cans")["volume_ml"] == _r(4258.59)


@pytest.mark.parametrize(
    "text,n", [("26 Bags", 26), ("94 Tabs", 94), ("60 Pods", 60), ("81 Pacs", 81), ("82 Tablets", 82)]
)
def test_count_nouns(text, n):
    assert parse_measures([text], "count") == {"count": n}


def test_compound_weight_and_hyphenated_units_unchanged():
    assert _mass("Roast Beef, 1 lb 2 oz")["mass_g"] == _r(510.29)
    assert _mass("Jar 12-oz")["mass_g"] == _r(340.19)


def test_nutrient_guard_exempts_descriptors():
    assert _mass("Cottage Cheese 500 g Fat Free")["mass_g"] == _r(500)
    assert _mass("Syrup 250 g Sugar Free")["mass_g"] == _r(250)
    assert _mass("Whey 907 g Protein Powder")["mass_g"] == _r(907)


def test_count_before_size_still_unresolved_and_one_count_ok():
    assert _mass("Yogurt 12 ct, 8 oz") == {}
    assert _mass("Coffee 12 oz, 1 Count")["mass_g"] == _r(340.19)


# --- laundry -----------------------------------------------------------------
def test_laundry_liquid_loads_and_volume():
    m = parse_measures(["Tide Original Liquid Laundry Detergent, 92 fl oz, 64 Loads"], "laundry")
    assert m["loads"] == 64
    assert m["volume_ml"] == _r(2720.76)
    assert "mass_g" not in m


def test_laundry_bare_oz_follows_form():
    m = parse_measures(
        ["Arm & Hammer Clean Burst Liquid Laundry Detergent, 105 oz, 70 Loads"], "laundry"
    )
    assert m["volume_ml"] == _r(3105.22) and "mass_g" not in m
    m = parse_measures(["Tide Original Powder Laundry Detergent, 143 oz, 102 Loads"], "laundry")
    assert m["mass_g"] == _r(4053.98) and "volume_ml" not in m
    m = parse_measures(["Purex Laundry Detergent, Mountain Breeze, 50 oz, 33 Loads"], "laundry")
    assert m == {"loads": 33}
    m = parse_measures(["Liquid or Powder Laundry Detergent, 50 oz, 33 Loads"], "laundry")
    assert m == {"loads": 33}


def test_laundry_pods_count_is_not_loads():
    m = parse_measures(["Tide PODS Laundry Detergent Pacs, Original, 81 Count"], "laundry")
    assert m == {"count": 81}


def test_laundry_merges_texts_earliest_wins():
    m = parse_measures(["92 fl oz", "Tide Liquid 50 Loads, 64 fl oz"], "laundry")
    assert m["volume_ml"] == _r(2720.76)
    assert m["loads"] == 50


def test_laundry_multipack_and_total():
    m = parse_measures(["Tide Liquid, 46 fl oz, 32 Loads, Pack of 2"], "laundry")
    assert m["loads"] == 64 and m["volume_ml"] == _r(2720.76)
    assert parse_measures(["Tide Liquid, 2 Pack, 128 Total Loads"], "laundry")["loads"] == 128
    assert parse_measures(["Tide Liquid, 2 Pack, 128 loads total"], "laundry")["loads"] == 128


def test_laundry_mass_and_nutrient_strip():
    m = parse_measures(["Dropps Laundry Powder, 3 lb, 48 Loads"], "laundry")
    assert m["mass_g"] == _r(1360.78) and m["loads"] == 48


def test_laundry_unparseable():
    assert parse_measures(["Tide Laundry Detergent"], "laundry") == {}
    assert parse_measures([], "laundry") == {}


def _tissue(text):
    return parse_measures([text], "tissue")


def test_tissue_total_wins():
    t = "Kleenex Ultra Soft Facial Tissues, 3-Ply, 8 Flat Boxes, 120 Tissues per Box (960 Tissues Total)"
    assert _tissue(t) == {"boxes": 8, "sheets": 960}
    t = "Kleenex Ultra Soft Facial Tissues, 3-Ply, 18 Flat Boxes, 144 Tissues per Box (2,592 Total Tissues)"
    assert _tissue(t)["sheets"] == 2592


def test_tissue_per_box_times_boxes():
    t = "Puffs Plus Lotion Facial Tissues, 2-Ply, 6 Family Boxes, 124 Facial Tissues per Box"
    assert _tissue(t) == {"boxes": 6, "sheets": 744}


def test_tissue_single_box():
    assert _tissue("Kleenex Trusted Care Facial Tissues, 2-Ply, 144 Tissues") == {"sheets": 144}


def test_tissue_bare_beside_boxes_is_per_box():
    t = "Great Value Facial Tissues, 2-Ply, 4 Cube Boxes, 70 Tissues"
    assert _tissue(t) == {"boxes": 4, "sheets": 280}


def test_tissue_pack_with_per_box():
    t = "Kleenex Facial Tissue, 160 Tissues per Box, Pack of 3"
    assert _tissue(t) == {"boxes": 3, "sheets": 480}


def test_tissue_ambiguous_counts_unresolved():
    assert _tissue("Puffs Basic Facial Tissues, 3 Pack, 128 Count") == {}
    assert _tissue("Scotties Facial Tissue, 2-Ply, 10 Boxes, 1,200 Count") == {}
    assert _tissue("Puffs Facial Tissues, 3 Pack, 128 Tissues") == {}


def test_tissue_ply_not_a_count():
    assert _tissue("Kleenex Facial Tissue, 3-Ply") == {}


def test_tissue_bare_count_single_box_and_zero():
    assert _tissue("Facial Tissue 200 ct") == {"sheets": 200}
    assert _tissue("Facial Tissue, 0 Tissues") == {}


def test_laundry_concentration_is_not_a_multiplier():
    m = parse_measures(["Tide Liquid HE 2X 64 loads"], "laundry")
    assert m == {"loads": 64}
    m = parse_measures(["Tide 2x 64 Loads 92 fl oz"], "laundry")
    assert m["loads"] == 64 and m["volume_ml"] == _r(2720.76)
    m = parse_measures(["Tide Ultra 3X Concentrated 64 Loads, 92 fl oz"], "laundry")
    assert m["loads"] == 64 and m["volume_ml"] == _r(2720.76)


def test_laundry_explicit_pack_context_still_multiplies():
    m = parse_measures(["Tide Liquid 2 x 92 fl oz bottles, 64 Loads"], "laundry")
    assert m["volume_ml"] == _r(2 * 2720.76) and m["loads"] == 128
    m = parse_measures(["Tide Liquid 2X, 92 fl oz, 64 Loads, 2 Pack"], "laundry")
    assert m["volume_ml"] == _r(2 * 2720.76) and m["loads"] == 128


def test_laundry_up_to_loads_never_guessed():
    m = parse_measures(["Tide up to 64 loads, 92 fl oz"], "laundry")
    assert "loads" not in m and m["volume_ml"] == _r(2720.76)
    assert "loads" not in parse_measures(["Up to 64 loads Tide"], "laundry")
    assert "loads" not in parse_measures(["Tide up-to 64 loads"], "laundry")


def test_laundry_ambiguous_nx_before_size_is_dropped():
    m = parse_measures(["Tide 2X 92 fl oz"], "laundry")
    assert m == {}
    m = parse_measures(["Tide Liquid 2X 92 fl oz, 64 Loads"], "laundry")
    assert m == {"loads": 64}
    assert "mass_g" not in parse_measures(["Tide Powder 2x 3 lb, 48 Loads"], "laundry")
    assert parse_measures(["Tide Liquid HE 2X 64 loads"], "laundry") == {"loads": 64}


def test_laundry_explicit_pack_wording_multiplies():
    m = parse_measures(["Tide 2 x 92 fl oz bottles"], "laundry")
    assert m == {"volume_ml": _r(2 * 2720.76)}
    assert parse_measures(["Tide 92 fl oz, 2 pack"], "laundry")["volume_ml"] == _r(2 * 2720.76)
    assert parse_measures(["Tide 92 fl oz, Twin Pack"], "laundry")["volume_ml"] == _r(2 * 2720.76)
    assert parse_measures(["Tide 92 fl oz, Pack of 3"], "laundry")["volume_ml"] == _r(3 * 2720.76)
