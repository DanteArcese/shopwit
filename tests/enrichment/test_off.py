import gzip
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from django.core.management import call_command

from catalog.models import OffEntry, Product
from enrichment import off
from enrichment.off import OffRecord, normalize_upc, parse_off_line
from connectors import off_search
from normalize.types import Nutrition, OffProduct

FIXTURE = Path(__file__).parent / "fixtures" / "delta_sample.jsonl"


def test_normalize_upc():
    assert normalize_upc("093966010015") == "0093966010015"
    assert normalize_upc("0093966010015") == "0093966010015"
    assert normalize_upc("abc") is None
    assert normalize_upc("12345") is None
    assert normalize_upc(None) is None
    assert normalize_upc("12345670") == "12345670"
    assert normalize_upc("0-93966-01001-5") == "0093966010015"


def test_parse_off_line():
    line = FIXTURE.read_text().splitlines()[0]
    assert parse_off_line(line, "off") == OffRecord(
        upc="0093966010015",
        source="off",
        labels=frozenset({"en:organic", "en:no-gluten"}),
        quantity="1.89 L",
        last_modified=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_parse_off_line_tolerates_missing_and_bad_data():
    lines = FIXTURE.read_text().splitlines()
    null_labels = parse_off_line(lines[1], "off")
    assert null_labels.labels == frozenset()
    no_quantity = parse_off_line(lines[3], "opf")
    assert no_quantity.quantity is None and no_quantity.source == "opf"
    assert parse_off_line(lines[4], "off") is None
    assert parse_off_line("not json", "off") is None
    assert parse_off_line("", "off") is None


@pytest.mark.django_db
def test_off_sync_only_known_upcs():
    Product.objects.create(identity="a", title="A", upc="093966010015")  # un-normalized
    Product.objects.create(identity="b", title="B", upc="0811620020169")
    Product.objects.create(identity="c", title="C", upc="9999999999999")
    Product.objects.create(identity="d", title="D")
    call_command("off_sync", file=str(FIXTURE))
    assert set(OffEntry.objects.values_list("pk", flat=True)) == {"0093966010015", "0811620020169"}
    entry = OffEntry.objects.get(pk="0093966010015")
    assert entry.source == "off"
    assert sorted(entry.labels) == ["en:no-gluten", "en:organic"]
    assert entry.quantity == "1.89 L"


@pytest.mark.django_db
def test_off_sync_reads_gzip_and_source(tmp_path):
    Product.objects.create(identity="a", title="A", upc="0093966010015")
    gz = tmp_path / "delta.json.gz"
    gz.write_bytes(gzip.compress(FIXTURE.read_bytes()))
    call_command("off_sync", file=str(gz), source="opf")
    assert OffEntry.objects.get().source == "opf"


@pytest.mark.django_db
def test_off_sync_requires_one_mode():
    from django.core.management import CommandError

    with pytest.raises(CommandError):
        call_command("off_sync")


@pytest.mark.django_db
def test_lookup_returns_off_product():
    OffEntry.objects.create(upc="0093966010015", source="off", labels=["en:organic"], quantity="1 L")
    assert off.lookup("093966010015") == OffProduct(labels=frozenset({"en:organic"}), quantity="1 L")
    assert off.lookup("0000000000000") is None
    assert off.lookup(None) is None
    assert off.lookup("abc") is None


@pytest.mark.django_db
def test_latest_delta_downloads_newest_file(monkeypatch):
    Product.objects.create(identity="a", title="A", upc="0093966010015")
    payload = gzip.compress(FIXTURE.read_bytes())
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        assert request.headers["user-agent"] == off.USER_AGENT
        if request.url.path.endswith("index.txt"):
            return httpx.Response(
                200,
                text="openfoodfacts_products_100_200.json.gz\nopenfoodfacts_products_200_300.json.gz\n",
            )
        return httpx.Response(200, content=payload)

    real = off.default_client
    monkeypatch.setattr(off, "default_client", lambda: real(transport=httpx.MockTransport(handler)))
    call_command("off_sync", latest_delta=True, stdout=io.StringIO())
    assert requested == [
        "https://static.openfoodfacts.org/data/delta/index.txt",
        "https://static.openfoodfacts.org/data/delta/openfoodfacts_products_200_300.json.gz",
    ]
    assert OffEntry.objects.get().upc == "0093966010015"


def _line(code, modified, quantity="1 L"):
    return json.dumps({"code": code, "labels_tags": [], "quantity": quantity, "last_modified_t": modified})


def _sync_lines(tmp_path, *lines):
    path = tmp_path / "f.jsonl"
    path.write_text("\n".join(lines) + "\n")
    off.sync_file(path)


@pytest.mark.django_db
def test_older_record_does_not_overwrite_newer(tmp_path):
    Product.objects.create(identity="a", title="A", upc="0093966010015")
    _sync_lines(tmp_path, _line("0093966010015", 2000, "new"))
    _sync_lines(tmp_path, _line("0093966010015", 1000, "old"))
    assert OffEntry.objects.get().quantity == "new"


@pytest.mark.django_db
def test_null_timestamp_does_not_wipe_dated(tmp_path):
    Product.objects.create(identity="a", title="A", upc="0093966010015")
    _sync_lines(tmp_path, _line("0093966010015", 2000, "dated"))
    _sync_lines(tmp_path, _line("0093966010015", None, "undated"))
    entry = OffEntry.objects.get()
    assert entry.quantity == "dated" and entry.last_modified is not None


@pytest.mark.django_db
def test_real_record_replaces_stored_404_entry(tmp_path):
    Product.objects.create(identity="a", title="A", upc="0093966010015")
    OffEntry.objects.create(upc="0093966010015", source="off", labels=[], quantity=None, last_modified=None)
    _sync_lines(tmp_path, _line("0093966010015", 1000, "real"))
    assert OffEntry.objects.get().quantity == "real"


@pytest.mark.django_db
def test_within_file_duplicates_keep_newest(tmp_path):
    Product.objects.create(identity="a", title="A", upc="0093966010015")
    _sync_lines(
        tmp_path,
        _line("0093966010015", 2000, "newest"),
        _line("0093966010015", 1000, "stale"),
    )
    assert OffEntry.objects.get().quantity == "newest"


def test_record_reads_categories_and_unit():
    line = json.dumps(
        {
            "code": "0093966010015",
            "quantity": "1 L",
            "categories_tags": ["en:dairies", 3, "fr:laits"],
            "product_quantity_unit": "ml",
        }
    )
    record = parse_off_line(line, "off")
    assert record.categories == ("en:dairies", "fr:laits")
    assert record.quantity_unit == "ml"
    bare = parse_off_line(json.dumps({"code": "0093966010015"}), "off")
    assert bare.categories == () and bare.quantity_unit is None
    assert "categories_tags" in off.FIELDS and "product_quantity_unit" in off.FIELDS


@pytest.mark.django_db
def test_save_and_lookup_carry_categories_and_unit():
    off._save(OffRecord("0093966010015", "off", frozenset(), "1 L", None, ("en:milks",), "ml"))
    got = off.lookup("0093966010015")
    assert got.categories == ("en:milks",) and got.quantity_unit == "ml"


def test_fields_request_nutriments():
    assert off.FIELDS.endswith(",nutriments,nutrition_data_per")
    assert "nutriments" in off_search.FIELDS and "nutrition_data_per" in off_search.FIELDS


def _rec(nutriments=None, per="100g"):
    data = {"code": "0068826718115"}
    if nutriments is not None:
        data["nutriments"] = nutriments
    if per is not None:
        data["nutrition_data_per"] = per
    return off._record_from_dict(data, "off")


def test_record_parses_protein_kcal_and_basis():
    rec = off._record_from_dict(
        {
            "code": "0068826718115",
            "nutrition_data_per": "100g",
            "nutriments": {"proteins_100g": 21, "energy-kcal_100g": "380", "proteins_serving": 7},
        },
        "off",
    )
    assert rec.nutrition == Nutrition("100g", 21.0, 380.0)


def test_record_without_nutrition_keys_has_none():
    assert off._record_from_dict({"code": "0068826718115"}, "off").nutrition is None


def test_kj_only_is_converted_exactly():
    rec = _rec({"proteins_100g": 1, "energy-kj_100g": 1046})
    assert rec.nutrition.kcal == 1046 / 4.184 == 250.0


def test_energy_100g_used_only_when_unit_is_kj():
    assert _rec({"energy_100g": 418.4, "energy_unit": "kJ"}).nutrition.kcal == 418.4 / 4.184
    assert _rec({"energy_100g": 418.4, "energy_unit": "kcal"}).nutrition.kcal is None
    assert _rec({"energy_100g": 418.4}).nutrition.kcal is None


def test_kcal_preferred_over_kj():
    rec = _rec({"energy-kcal_100g": 100, "energy-kj_100g": 9999, "energy_100g": 9999, "energy_unit": "kJ"})
    assert rec.nutrition.kcal == 100.0


def test_unusable_kcal_falls_back_to_kj():
    assert _rec({"energy-kcal_100g": "n/a", "energy-kj_100g": 418.4}).nutrition.kcal == 418.4 / 4.184


@pytest.mark.parametrize("value", [True, "n/a", "", float("nan"), float("inf"), None, [1]])
def test_unusable_nutrient_values_are_missing(value):
    rec = _rec({"proteins_100g": value, "energy-kcal_100g": value, "energy-kj_100g": value})
    assert rec is not None
    assert rec.nutrition == Nutrition("100g", None, None)


def test_unknown_basis_is_none():
    assert _rec({"proteins_100g": 1}, per="per-pack").nutrition == Nutrition(None, 1.0, None)
    assert _rec({"proteins_100g": 1}, per=" 100ML ").nutrition.basis == "100ml"
    assert _rec({"proteins_100g": 1}, per="serving").nutrition.basis == "serving"
    assert _rec({"proteins_100g": 1}, per=None).nutrition.basis is None


def test_non_dict_nutriments_is_missing():
    assert _rec([1, 2], per="100g").nutrition == Nutrition("100g", None, None)


@pytest.mark.django_db
def test_save_and_lookup_round_trip_nutrition():
    rec = _rec({"proteins_100g": 21, "energy-kcal_100g": 380})
    assert off._save(rec)
    entry = OffEntry.objects.get(pk="0068826718115")
    assert (entry.nutrition_per, entry.protein_100g, entry.kcal_100g) == ("100g", 21.0, 380.0)
    assert entry.nutrition_checked_at is not None
    assert off.lookup("0068826718115").nutrition == Nutrition("100g", 21.0, 380.0)


@pytest.mark.django_db
def test_lookup_before_nutrition_checked_is_none():
    OffEntry.objects.create(upc="0068826718115", source="off", protein_100g=5.0)
    assert off.lookup("0068826718115").nutrition is None


@pytest.mark.django_db
def test_delta_line_carries_nutrition(tmp_path):
    Product.objects.create(identity="m", title="M", upc="0093966010015")
    line = json.dumps(
        {
            "code": "0093966010015",
            "last_modified_t": 1767225600,
            "nutrition_data_per": "100g",
            "nutriments": {"proteins_100g": 3.4},
        }
    )
    f = tmp_path / "d.jsonl"
    f.write_text(line + "\n")
    assert off.sync_file(f) == 1
    assert OffEntry.objects.get(pk="0093966010015").protein_100g == 3.4
