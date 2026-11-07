"""Data-driven checks: every case in tests/categories/**/*.yaml against normalize()."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from normalize import load_categories, load_store_brands, normalize
from normalize.types import OffProduct, RawOffer

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "categories"
FETCHED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)

CATEGORIES = load_categories(ROOT / "categories")
STORE_BRANDS = load_store_brands(ROOT / "data" / "store_brands.yaml")


def _cases():
    params = []
    for path in sorted(FIXTURES.rglob("*.yaml")):
        slug = path.relative_to(FIXTURES).with_suffix("").as_posix()
        for index, case in enumerate(yaml.safe_load(path.read_text(encoding="utf-8")) or []):
            params.append(pytest.param(case, index, id=f"{slug}:{index}"))
    return params


def _build(case, index):
    raw = RawOffer(
        retailer="fixture",
        retailer_item_id=str(index),
        title=case["title"],
        price=Decimal(str(case.get("price", "1.00"))),
        currency="USD",
        url="https://example.invalid",
        fetched_at=FETCHED_AT,
        brand=case.get("brand"),
        size_text=case.get("size_text"),
        fields=case.get("fields") or {},
    )
    off = None
    if "off_labels" in case or "off_quantity" in case:
        off = OffProduct(
            labels=frozenset(case.get("off_labels") or ()), quantity=case.get("off_quantity")
        )
    return raw, off


def test_fixture_files_map_to_categories_and_slugs():
    for path in sorted(FIXTURES.rglob("*.yaml")):
        slug = path.relative_to(FIXTURES).with_suffix("").as_posix()
        assert slug in CATEGORIES, f"fixture file {path} has no category {slug}"
        for index, case in enumerate(yaml.safe_load(path.read_text(encoding="utf-8")) or []):
            category = case["expect"]["category"]
            assert category is None or category == slug, f"{slug}:{index} expects {category}"


@pytest.mark.parametrize("case,index", _cases())
def test_fixture(case, index):
    raw, off = _build(case, index)
    result = normalize(raw, CATEGORIES, STORE_BRANDS, off)
    expect = case["expect"]

    assert result.category == expect["category"]
    for name, value in (expect.get("attributes") or {}).items():
        assert name in result.attributes, f"missing attribute {name}"
        assert result.attributes[name].value == value, name
    for key, value in (expect.get("measures") or {}).items():
        assert result.measures.get(key) == pytest.approx(value, rel=1e-3), key
    for key, value in (expect.get("unit_prices") or {}).items():
        assert result.unit_prices.get(key) == Decimal(str(value)), key
    if expect.get("unit_prices_exact"):
        assert set(result.unit_prices) == set(expect.get("unit_prices") or {})
    if "unresolved" in expect:
        assert sorted(result.unresolved) == sorted(expect["unresolved"])


def test_every_category_has_fixtures():
    assert CATEGORIES, "no categories found"
    for slug in CATEGORIES:
        path = FIXTURES / f"{slug}.yaml"
        assert path.exists(), f"missing fixtures for {slug}"
        cases = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        assert len(cases) >= 8, f"{slug} has {len(cases)} fixtures, need at least 8"
