import re
from pathlib import Path

import pytest

from normalize.categories import CategoryError, load_categories

MINIMAL = """\
name: Milk
parent: null
dimension: volume
match:
  include: ["milk"]
  exclude: ["chocolate milk"]
attributes:
  fat:
    type: enum
    values: [whole, skim]
    patterns: ["(whole|skim)"]
    synonyms:
      whole: ["full fat"]
comparison:
  default: per_volume
  alternates: []
"""


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_loads_minimal_category(tmp_path):
    write(tmp_path, "dairy/milk.yaml", MINIMAL)
    cats = load_categories(tmp_path)
    cat = cats["dairy/milk"]
    assert cat.slug == "dairy/milk"
    assert cat.dimension == "volume"
    assert cat.comparison_default == "per_volume"
    assert cat.include == ("milk",)
    pat = cat.attributes["fat"].patterns[0]
    assert pat.flags & re.IGNORECASE
    assert pat.search("WHOLE")


def test_rejects_unknown_top_level_key(tmp_path):
    write(tmp_path, "dairy/milk.yaml", MINIMAL + "bogus: 1\n")
    with pytest.raises(CategoryError, match="dairy/milk.yaml"):
        load_categories(tmp_path)


def test_rejects_invalid_regex(tmp_path):
    bad = MINIMAL.replace('"(whole|skim)"', r'"(\d"')
    write(tmp_path, "dairy/milk.yaml", bad)
    with pytest.raises(CategoryError):
        load_categories(tmp_path)


def test_rejects_unknown_comparison(tmp_path):
    write(tmp_path, "dairy/milk.yaml", MINIMAL.replace("per_volume", "per_banana"))
    with pytest.raises(CategoryError):
        load_categories(tmp_path)


def test_enum_requires_values(tmp_path):
    bad = MINIMAL.replace("    values: [whole, skim]\n", "").replace(
        '    synonyms:\n      whole: ["full fat"]\n', ""
    )
    write(tmp_path, "dairy/milk.yaml", bad)
    with pytest.raises(CategoryError):
        load_categories(tmp_path)


def test_synonym_key_must_be_declared_value(tmp_path):
    bad = MINIMAL.replace("      whole:", '      "3":')
    write(tmp_path, "dairy/milk.yaml", bad)
    with pytest.raises(CategoryError):
        load_categories(tmp_path)


def test_pattern_needs_one_capture_group(tmp_path):
    bad = MINIMAL.replace('"(whole|skim)"', r'"\d ply"')
    write(tmp_path, "dairy/milk.yaml", bad)
    with pytest.raises(CategoryError):
        load_categories(tmp_path)


def test_int_synonym_keys_normalized(tmp_path):
    text = """\
name: Toilet paper
dimension: paper
attributes:
  ply:
    type: int
    values: [1, 2]
    synonyms:
      "2": ["double"]
comparison:
  default: area_x_ply
"""
    write(tmp_path, "paper/tp.yaml", text)
    cat = load_categories(tmp_path)["paper/tp"]
    assert cat.attributes["ply"].synonyms == {2: ("double",)}
    assert cat.parent is None
    assert cat.comparison_alternates == ()


def test_search_defaults_to_name(tmp_path):
    write(tmp_path, "dairy/milk.yaml", MINIMAL)
    assert load_categories(tmp_path)["dairy/milk"].search == ("Milk",)


def test_search_loaded(tmp_path):
    write(tmp_path, "dairy/milk.yaml", MINIMAL + 'search: ["2% milk", "whole milk"]\n')
    assert load_categories(tmp_path)["dairy/milk"].search == ("2% milk", "whole milk")


def test_loads_laundry_dimension(tmp_path):
    text = MINIMAL.replace("dimension: volume", "dimension: laundry")
    text = text.replace("default: per_volume\n  alternates: []", "default: per_load\n  alternates: [per_volume]")
    write(tmp_path, "household/laundry.yaml", text)
    cat = load_categories(tmp_path)["household/laundry"]
    assert cat.dimension == "laundry"
    assert (cat.comparison_default, cat.comparison_alternates) == ("per_load", ("per_volume",))


def test_loads_tissue_dimension(tmp_path):
    text = MINIMAL.replace("dimension: volume", "dimension: tissue")
    text = text.replace("default: per_volume\n  alternates: []", "default: sheets_x_ply\n  alternates: [per_100_sheets]")
    write(tmp_path, "household/tissue.yaml", text)
    cat = load_categories(tmp_path)["household/tissue"]
    assert cat.dimension == "tissue"
    assert (cat.comparison_default, cat.comparison_alternates) == ("sheets_x_ply", ("per_100_sheets",))


def test_load_categories_reuses_parsed_files(tmp_path, monkeypatch):
    import normalize.categories as C

    write(tmp_path, "dairy/milk.yaml", MINIMAL)
    first = load_categories(tmp_path)
    calls = []
    real = C._load_file
    monkeypatch.setattr(C, "_load_file", lambda *a: calls.append(a) or real(*a))
    second = load_categories(tmp_path)
    assert second == first and second is not first
    assert calls == []


def test_load_categories_rereads_changed_files(tmp_path):
    write(tmp_path, "dairy/milk.yaml", MINIMAL)
    assert load_categories(tmp_path)["dairy/milk"].include == ("milk",)
    write(tmp_path, "dairy/milk.yaml", MINIMAL.replace('["milk"]', '["milk", "whole milk"]'))
    assert load_categories(tmp_path)["dairy/milk"].include == ("milk", "whole milk")
    write(tmp_path, "dairy/eggs.yaml", MINIMAL.replace("Milk", "Eggs"))
    assert set(load_categories(tmp_path)) == {"dairy/milk", "dairy/eggs"}


def test_food_flag_parses_and_defaults_false(tmp_path):
    write(tmp_path, "dairy/milk.yaml", MINIMAL)
    write(tmp_path, "dairy/other.yaml", MINIMAL + "food: true\n")
    cats = load_categories(tmp_path)
    assert cats["dairy/milk"].food is False
    assert cats["dairy/other"].food is True


@pytest.mark.parametrize("bad", ["'yes'", "1", "null", "[true]"])
def test_food_flag_must_be_bool(tmp_path, bad):
    write(tmp_path, "dairy/milk.yaml", MINIMAL + f"food: {bad}\n")
    with pytest.raises(CategoryError, match="food"):
        load_categories(tmp_path)


FOOD_SLUGS = {
    "dairy/butter", "dairy/cheese", "dairy/milk", "dairy/yogurt",
    "pantry/bread", "pantry/cooking-oil", "pantry/flour", "pantry/pasta",
    "pantry/peanut-butter", "pantry/protein-powder", "pantry/rice", "pantry/sugar",
    "beverages/orange-juice", "beverages/soda",
}


def test_repo_food_categories_are_exactly_the_agreed_set():
    root = Path(__file__).resolve().parents[2] / "categories"
    assert {slug for slug, d in load_categories(root).items() if d.food} == FOOD_SLUGS
