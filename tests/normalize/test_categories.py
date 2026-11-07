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
        "    synonyms:\n      whole: [\"full fat\"]\n", ""
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
