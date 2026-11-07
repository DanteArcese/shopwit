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
from normalize.types import OffProduct

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
