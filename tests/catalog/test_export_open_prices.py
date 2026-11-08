import gzip
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.core.management import call_command

from catalog.categories_sync import sync_categories
from catalog.models import Offer, Product
from catalog.observed import sync_open_prices
from tests.catalog.test_observed import NOW, UPC, FakeClient, loc, no_off, obs


@pytest.fixture(autouse=True)
def cats(db):
    sync_categories(settings.CATEGORIES_DIR)


def export(tmp_path):
    out = tmp_path / "op.jsonl.gz"
    call_command("export_open_prices", out=str(out))
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def synced(*observations):
    sync_open_prices(FakeClient(observations), NOW, off_lookup=no_off)


def test_export_lines(tmp_path):
    synced(
        obs(1, "4.99", location=loc(1)),
        obs(2, "2.50", location=loc(2), title="Mystery Gizmo", upc="0811620020176"),
    )
    lines = export(tmp_path)
    assert len(lines) == 3
    header, rows = lines[0], {r["price_id"]: r for r in lines[1:]}
    assert header["license"] == "ODbL-1.0"
    assert header["source"] == "https://prices.openfoodfacts.org"
    assert header["method"] == "https://github.com/DanteArcese/unitpricer"
    assert header["generated_at"]
    matched = rows[1]
    assert matched["price"] == "4.99" and matched["currency"] == "USD"
    assert matched["upc"] == UPC and matched["labels"] == ["en:organic"]
    assert matched["observed_on"] == (NOW - timedelta(days=5)).date().isoformat()
    assert matched["store"] == {
        "code": "osm-n1", "name": "Safeway", "city": "Mountain View", "postcode": "94040",
        "country_code": "US", "osm_type": "NODE", "osm_id": 1,
    }
    assert matched["normalized"]["category"] == "dairy/milk"
    assert matched["normalized"]["measures"]
    assert "normalized" not in rows[2]


def test_export_never_includes_live_offers(tmp_path):
    synced(obs(1, "4.99"))
    product = Product.objects.get(upc=UPC)
    product.attributes = {"zz_product_marker": True}
    product.save()
    Offer.objects.create(
        product=product, retailer="walmart", retailer_item_id="W1", price=Decimal("9.99"),
        currency="USD", url="https://example.com/w", title="Walmart Milk", last_seen_at=NOW,
        attributes={"zz_live_marker": True}, measures={"volume_ml": 123456.5},
        unit_prices={"per_gal": "7.654321"}, category=product.category,
    )
    lines = export(tmp_path)
    text = json.dumps(lines).lower()
    assert "walmart" not in text and "zz_live_marker" not in text
    assert "zz_product_marker" not in text and "123456.5" not in text and "7.654321" not in text
    assert all("unit_prices" not in json.dumps(line) for line in lines)
    normalized = lines[1]["normalized"]
    observed = Offer.objects.get(kind="observed")
    assert normalized["attributes"] == observed.attributes
    assert normalized["measures"] == observed.measures


def test_older_observation_has_no_normalized(tmp_path):
    synced(obs(1, "4.49", days=10), obs(2, "4.99", days=2))
    rows = {r["price_id"]: r for r in export(tmp_path)[1:]}
    assert "normalized" in rows[2] and "normalized" not in rows[1]
