from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from normalize.categories import CategoryDef
from normalize.match import brand_tier, load_store_brands, match_category
from normalize.types import AttrValue, RawOffer

STORE_BRANDS = frozenset({"kirkland signature", "365", "365 by whole foods market"})


def cat(slug, include, exclude=()):
    return CategoryDef(
        slug=slug, name=slug, parent=None, include=tuple(include), exclude=tuple(exclude),
        dimension="volume", attributes={}, comparison_default="per_volume",
        comparison_alternates=(),
    )


MILK = cat(
    "dairy/milk",
    ["milk"],
    ["almond", "oat", "soy", "coconut", "chocolate", "condensed", "evaporated",
     "powdered", "milk chocolate"],
)
PLANT = cat("dairy/plant-milk", ["almond milk", "oat milk"])
TP = cat("household/toilet-paper", ["toilet paper", "bath tissue"], ["wipes"])
CATS = {c.slug: c for c in (MILK, PLANT, TP)}


def offer(title, brand=None):
    return RawOffer(
        retailer="r", retailer_item_id="1", title=title, price=Decimal("1.00"),
        currency="USD", url="http://x", fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        brand=brand,
    )


def test_matches_milk():
    assert match_category(offer("Garelick Farms Milk, Reduced Fat"), CATS) == "dairy/milk"


def test_excluded_flavored():
    assert match_category(offer("Horizon Organic Chocolate Milk"), CATS) is None


def test_longest_include_wins():
    cats = {**CATS, "dairy/milk": replace(MILK, exclude=())}
    assert match_category(offer("Silk Almond Milk"), cats) == "dairy/plant-milk"


def test_tie_returns_none():
    cats = {"a/x": cat("a/x", ["milk"]), "b/y": cat("b/y", ["milk"])}
    assert match_category(offer("Whole Milk"), cats) is None


def test_excludes_wipes():
    assert match_category(offer("Cottonelle Flushable Wipes, toilet paper companion"), CATS) is None


def test_whole_phrase_only():
    assert match_category(offer("Milkshake mix"), CATS) is None


def test_case_insensitive():
    assert match_category(offer("MILK 2%"), CATS) == "dairy/milk"


def test_no_match():
    assert match_category(offer("Bananas"), CATS) is None


def test_brand_tier_from_brand_field():
    assert brand_tier(offer("x", brand="Kirkland Signature"), STORE_BRANDS) == AttrValue("store_brand", "rule")
    assert brand_tier(offer("x", brand="kirkland signature"), STORE_BRANDS) == AttrValue("store_brand", "rule")
    assert brand_tier(offer("x", brand="Charmin"), STORE_BRANDS) == AttrValue("name_brand", "rule")


def test_brand_tier_from_title_prefix():
    assert brand_tier(offer("365 Organic 2% Milk, 32 fl oz"), STORE_BRANDS) == AttrValue("store_brand", "rule")
    assert brand_tier(offer("365 by Whole Foods Market Milk"), STORE_BRANDS) == AttrValue("store_brand", "rule")


def test_brand_tier_prefix_needs_boundary():
    assert brand_tier(offer("3650 Milk"), STORE_BRANDS) is None


def test_brand_tier_unknown():
    assert brand_tier(offer("Organic 2% Milk, 64 fl oz"), STORE_BRANDS) is None


def test_load_store_brands(tmp_path: Path):
    p = tmp_path / "b.yaml"
    p.write_text("- Great Value\n- up&up\n", encoding="utf-8")
    assert load_store_brands(p) == frozenset({"great value", "up&up"})


def test_shipped_store_brands():
    brands = load_store_brands(Path(__file__).resolve().parents[2] / "data" / "store_brands.yaml")
    assert {"kirkland signature", "365", "presto!"} <= brands


def test_match_double_space_and_nbsp():
    assert match_category(offer("Charmin  Toilet  Paper, 12 rolls"), CATS) == "household/toilet-paper"
    assert match_category(offer("Charmin Toilet\u00a0Paper, 12 rolls"), CATS) == "household/toilet-paper"


def test_match_includes_brand():
    assert match_category(offer("Reduced Fat 2%", brand="Horizon Milk"), CATS) == "dairy/milk"


def test_brand_curly_apostrophe_is_store_brand():
    b = frozenset({"member's mark"})
    assert brand_tier(offer("x", brand="Member\u2019s Mark"), b) == AttrValue("store_brand", "rule")


def test_members_mark_alias_shipped():
    brands = load_store_brands(Path(__file__).resolve().parents[2] / "data" / "store_brands.yaml")
    assert {"member's mark", "members mark"} <= brands


def test_whitespace_only_brand_is_no_brand():
    assert brand_tier(offer("Organic Milk", brand="   "), STORE_BRANDS) is None
    assert brand_tier(offer("365 Organic Milk", brand="  "), STORE_BRANDS) == AttrValue("store_brand", "rule")


def test_leading_space_title_prefix():
    assert brand_tier(offer("  365 Organic 2% Milk"), STORE_BRANDS) == AttrValue("store_brand", "rule")


def test_variety_pack_milk_excluded():
    cats = {"dairy/milk": cat("dairy/milk", ["milk"], ["variety pack", "variety"])}
    assert match_category(offer("Variety Pack Whole Milk and 2% Milk"), cats) is None
