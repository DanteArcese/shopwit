import re
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from normalize import normalize
from normalize.categories import AttributeDef, CategoryDef


@pytest.fixture
def categories():
    fat = AttributeDef(
        name="fat",
        type="enum",
        values=("skim", "1", "2", "whole"),
        patterns=(re.compile(r"(?<![\d.])([12])\s?%", re.IGNORECASE),),
        synonyms={"skim": ("skim",), "1": ("low fat",), "2": ("reduced fat",), "whole": ("whole milk",)},
    )
    milk = CategoryDef(
        slug="dairy/milk",
        name="Milk",
        parent=None,
        include=("milk",),
        exclude=(),
        dimension="volume",
        attributes={"fat": fat},
        comparison_default="per_volume",
        comparison_alternates=(),
    )
    return {"dairy/milk": milk}


def offer(title, price="6.58", brand=None):
    from normalize.types import RawOffer

    return RawOffer(
        retailer="r",
        retailer_item_id="1",
        title=title,
        price=Decimal(price),
        currency="USD",
        url="http://x",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        brand=brand,
    )


def test_pipeline_milk_end_to_end(categories):
    raw = offer("Lactaid Reduced Fat 2% Milk, 2.8 L", brand="Lactaid")
    n = normalize(raw, categories, frozenset())
    assert n.category == "dairy/milk"
    assert n.attributes["fat"].value == "2"
    assert n.attributes["brand_tier"].value == "name_brand"
    assert n.measures["volume_ml"] == 2800
    assert n.unit_prices["per_volume"] == Decimal("0.002350")
    assert n.unresolved == ()


def test_pipeline_unmatched(categories):
    n = normalize(offer("Garden Hose 50 ft"), categories, frozenset())
    assert n.category is None
    assert n.unresolved == ("category",)


def test_pipeline_no_quantity(categories):
    n = normalize(offer("Organic Valley 2% Milk"), categories, frozenset())
    assert "quantity" in n.unresolved
    assert "unit_price:per_volume" in n.unresolved
    assert "attribute:brand_tier" in n.unresolved
    assert n.unit_prices == {}
