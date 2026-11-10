import dataclasses
from decimal import Decimal

import pytest

from catalog.models import Offer
from tests.catalog.helpers import raw


def _row(price, item, shipping):
    return dataclasses.replace(
        raw(title=f"Acme Milk {item}, 1 L", price=price, item=item, brand="Acme"),
        fields={"shipping": shipping} if shipping is not None else {},
    )


def _order(html, *items):
    return sorted(items, key=lambda i: html.index(f"Acme Milk {i}"))


@pytest.mark.parametrize(
    "shipping, text",
    [("0", "Free shipping"), ("5.99", "+ $5.99 shipping")],
)
def test_row_shows_shipping(client, load, shipping, text):
    load([_row("2.00", "a", shipping)])
    html = client.get("/c/dairy/milk/").content.decode()
    assert text in html


def test_row_without_shipping_says_nothing(client, load):
    load([_row("2.00", "a", None)])
    html = client.get("/c/dairy/milk/").content.decode()
    assert "shipping" not in html.lower().replace("shipping-", "")


def test_ranking_and_unit_price_ignore_shipping(client, load):
    # a is cheaper per unit but costs $50 to ship; b is dearer but ships free.
    load([_row("1.00", "a", "50.00"), _row("1.50", "b", "0")])
    html = client.get("/c/dairy/milk/").content.decode()
    assert _order(html, "a", "b") == ["a", "b"]
    a, b = Offer.objects.get(retailer_item_id="a"), Offer.objects.get(retailer_item_id="b")
    assert (a.price, b.price) == (Decimal("1.00"), Decimal("1.50"))
    assert a.unit_prices == {"per_volume": "0.001000"}
    assert b.unit_prices == {"per_volume": "0.001500"}
    # swap the shipping: order and unit prices stay the same
    load([_row("1.00", "a", "0"), _row("1.50", "b", "50.00")])
    html = client.get("/c/dairy/milk/").content.decode()
    assert _order(html, "a", "b") == ["a", "b"]
    a.refresh_from_db()
    assert a.unit_prices == {"per_volume": "0.001000"}
