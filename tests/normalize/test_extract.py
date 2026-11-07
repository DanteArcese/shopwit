import re
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from normalize.categories import AttributeDef, CategoryDef
from normalize.extract import extract_attributes
from normalize.types import AttrValue, OffProduct, RawOffer


@pytest.fixture
def milk():
    fat = AttributeDef(
        name="fat",
        type="enum",
        values=("skim", "1", "2", "whole"),
        patterns=(re.compile(r"(?<![\d.])([12])\s?%", re.IGNORECASE),),
        synonyms={
            "skim": ("skim", "fat free", "nonfat"),
            "1": ("low fat", "lowfat"),
            "2": ("reduced fat",),
            "whole": ("whole milk", "vitamin d milk"),
        },
    )
    organic = AttributeDef(
        name="organic",
        type="bool",
        synonyms={True: ("organic", "o organics")},
        off_label="en:organic",
        absent=False,
    )
    return CategoryDef(
        slug="dairy/milk",
        name="Milk",
        parent=None,
        include=(),
        exclude=(),
        dimension="volume",
        attributes={"fat": fat, "organic": organic},
        comparison_default="per_volume",
        comparison_alternates=(),
    )


def offer(title, fields=None, size_text=None, brand=None):
    return RawOffer(
        retailer="r",
        retailer_item_id="1",
        title=title,
        price=Decimal("1.00"),
        currency="USD",
        url="http://x",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        size_text=size_text,
        brand=brand,
        fields=fields or {},
    )


def test_pattern_beats_synonym(milk):
    attrs, _ = extract_attributes(
        offer("Market Basket 100% Lactose Free Lowfat 2% Milk"), milk, None
    )
    assert attrs["fat"] == AttrValue("2", "rule")


def test_a2_brand_is_not_two_percent(milk):
    attrs, _ = extract_attributes(offer("a2 Milk Whole Milk"), milk, None)
    assert attrs["fat"].value == "whole"


def test_synonym_reduced_fat(milk):
    attrs, _ = extract_attributes(offer("O Organics Milk, Reduced Fat"), milk, None)
    assert attrs["fat"].value == "2"
    assert attrs["organic"] == AttrValue(True, "rule")


def test_ambiguous_pattern(milk):
    attrs, unresolved = extract_attributes(offer("Variety pack 1% and 2% milk"), milk, None)
    assert "fat" not in attrs
    assert "attribute:fat:ambiguous" in unresolved


def test_ambiguous_synonym(milk):
    attrs, unresolved = extract_attributes(offer("Whole Milk & Skim Milk Duo"), milk, None)
    assert "fat" not in attrs
    assert "attribute:fat:ambiguous" in unresolved


def test_whole_foods_is_not_whole_milk(milk):
    attrs, unresolved = extract_attributes(
        offer("365 by Whole Foods Market Organic Reduced Fat Milk"), milk, None
    )
    assert attrs["fat"].value == "2"
    assert "attribute:fat:ambiguous" not in unresolved


def test_api_field_wins(milk):
    attrs, _ = extract_attributes(offer("Milk 2%", fields={"fat": "1"}), milk, None)
    assert attrs["fat"] == AttrValue("1", "api")


def test_invalid_api_field_falls_through(milk):
    attrs, _ = extract_attributes(offer("Milk 2%", fields={"fat": "bogus"}), milk, None)
    assert attrs["fat"] == AttrValue("2", "rule")


def test_size_text_is_searched(milk):
    attrs, _ = extract_attributes(offer("Milk", size_text="2% 1 gal"), milk, None)
    assert attrs["fat"].value == "2"


def test_off_label(milk):
    off = OffProduct(labels=frozenset({"en:organic"}))
    attrs, _ = extract_attributes(offer("Milk"), milk, off)
    assert attrs["organic"] == AttrValue(True, "off")


def test_absent_default(milk):
    attrs, _ = extract_attributes(offer("Garelick Farms Milk, Reduced Fat"), milk, None)
    assert attrs["organic"] == AttrValue(False, "rule")


def test_unknown_reported(milk):
    attrs, unresolved = extract_attributes(offer("Milk 64 fl oz"), milk, None)
    assert "fat" not in attrs
    assert "attribute:fat" in unresolved


@pytest.mark.parametrize("bad", [None, "  ", ""])
def test_empty_api_field_falls_through(milk, bad):
    attrs, _ = extract_attributes(offer("Milk 2%", fields={"fat": bad}), milk, None)
    assert attrs["fat"] == AttrValue("2", "rule")


def test_api_enum_value_is_stripped(milk):
    attrs, _ = extract_attributes(offer("Milk", fields={"fat": " whole "}), milk, None)
    assert attrs["fat"] == AttrValue("whole", "api")


def test_pattern_capture_is_case_insensitive_for_enums():
    size = AttributeDef(
        name="size",
        type="enum",
        values=("medium", "large"),
        patterns=(re.compile(r"\b(large)\b", re.IGNORECASE),),
    )
    category = CategoryDef(
        slug="dairy/eggs", name="Eggs", parent=None, include=(), exclude=(),
        dimension="count", attributes={"size": size},
        comparison_default="per_count", comparison_alternates=(),
    )
    attrs, _ = extract_attributes(offer("Grade A LARGE Eggs"), category, None)
    assert attrs["size"] == AttrValue("large", "rule")


def test_brand_field_feeds_lactose_free(milk):
    lf = AttributeDef(
        name="lactose_free", type="bool", synonyms={True: ("lactaid",)}, absent=False
    )
    cat = CategoryDef(**{**milk.__dict__, "attributes": {"lactose_free": lf}})
    attrs, _ = extract_attributes(
        offer("Reduced Fat 2% Milk, 1 gal", brand="Lactaid"), cat, None
    )
    assert attrs["lactose_free"] == AttrValue(True, "rule")


def test_brand_field_feeds_organic(milk):
    attrs, _ = extract_attributes(offer("Milk, Reduced Fat", brand="O Organics"), milk, None)
    assert attrs["organic"] == AttrValue(True, "rule")


def test_synonym_folds_whitespace_and_quotes(milk):
    attrs, _ = extract_attributes(offer("Reduced\u00a0 Fat  Milk"), milk, None)
    assert attrs["fat"].value == "2"


def test_title_brand_join_cannot_yield_fat(milk):
    attrs, _ = extract_attributes(offer("Whole", brand="Milk Co"), milk, None)
    assert "fat" not in attrs
