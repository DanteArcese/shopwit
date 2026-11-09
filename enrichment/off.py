"""Open Food Facts enrichment cache: parsing, delta sync and per-UPC backfill.

Django models are imported lazily so normalize_upc/parse_off_line work without Django.
"""

import gzip
import json
import math
import re
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx

from normalize.types import Nutrition, OffProduct

USER_AGENT = "unitpricer/0.1 (+https://github.com/DanteArcese/unitpricer)"
DELTA_BASE_URL = "https://static.openfoodfacts.org/data/delta/"
API_HOSTS = {
    "off": "https://world.openfoodfacts.org",
    "opf": "https://world.openproductsfacts.org",
}
FIELDS = "code,labels_tags,quantity,last_modified_t,categories_tags,product_quantity_unit,nutriments,nutrition_data_per"
MAX_RETRIES = 3
BACKOFF_SECONDS = 1.0
MIN_INTERVAL_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 60.0

Source = Literal["off", "opf"]


@dataclass(frozen=True)
class OffRecord:
    upc: str
    source: str
    labels: frozenset[str]
    quantity: str | None
    last_modified: datetime | None
    categories: tuple[str, ...] = ()
    quantity_unit: str | None = None
    nutrition: Nutrition | None = None


KJ_PER_KCAL = 4.184


def normalize_upc(code: str | None) -> str | None:
    if not code:
        return None
    digits = re.sub(r"\D", "", code)
    if len(digits) == 12:
        return digits.rjust(13, "0")
    if len(digits) in (8, 13, 14):
        return digits
    return None


def _number(value: object) -> float | None:
    """A finite, non-negative int, float or numeric string; anything else (bools included) is missing."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _kcal(nutriments: dict) -> float | None:
    """kcal per 100: OFF's kcal value, else its kJ value converted exactly (kJ / 4.184)."""
    kcal = _number(nutriments.get("energy-kcal_100g"))
    if kcal is not None:
        return kcal
    kj = _number(nutriments.get("energy-kj_100g"))
    if kj is None:
        unit = nutriments.get("energy_unit")
        if isinstance(unit, str) and unit.strip().lower() == "kj":
            kj = _number(nutriments.get("energy_100g"))
    return kj / KJ_PER_KCAL if kj is not None else None


def _nutrition(data: dict) -> Nutrition | None:
    if "nutriments" not in data and "nutrition_data_per" not in data:
        return None
    nutriments = data.get("nutriments")
    if not isinstance(nutriments, dict):
        nutriments = {}
    basis = data.get("nutrition_data_per")
    basis = basis.strip().lower() if isinstance(basis, str) else None
    if basis not in ("100g", "100ml", "serving"):
        basis = None
    return Nutrition(basis, _number(nutriments.get("proteins_100g")), _kcal(nutriments))


def _record_from_dict(data: object, source: str) -> OffRecord | None:
    if not isinstance(data, dict):
        return None
    code = data.get("code")
    upc = normalize_upc(str(code) if code is not None else None)
    if upc is None:
        return None
    raw_labels = data.get("labels_tags")
    labels = frozenset(t for t in raw_labels if isinstance(t, str)) if isinstance(raw_labels, list) else frozenset()
    quantity = data.get("quantity")
    quantity = quantity.strip() or None if isinstance(quantity, str) else None
    modified = data.get("last_modified_t")
    last_modified = None
    if isinstance(modified, (int, float)) and not isinstance(modified, bool):
        try:
            last_modified = datetime.fromtimestamp(modified, tz=UTC)
        except (OverflowError, OSError, ValueError):
            last_modified = None
    raw_cats = data.get("categories_tags")
    categories = tuple(t for t in raw_cats if isinstance(t, str)) if isinstance(raw_cats, list) else ()
    unit = data.get("product_quantity_unit")
    unit = unit.strip() or None if isinstance(unit, str) else None
    return OffRecord(upc, source, labels, quantity, last_modified, categories, unit, _nutrition(data))


def parse_off_line(line: str, source: Source) -> OffRecord | None:
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except ValueError:
        return None
    return _record_from_dict(data, source)


def _iter_lines(path: Path) -> Iterator[str]:
    with open(path, "rb") as probe:
        gzipped = probe.read(2) == b"\x1f\x8b"
    opener = gzip.open if gzipped else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        yield from handle


def default_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True, transport=transport
    )


def default_sleep(seconds: float) -> None:
    time.sleep(seconds)


def lookup(upc: str | None) -> OffProduct | None:
    from catalog.models import OffEntry

    normalized = normalize_upc(upc)
    if normalized is None:
        return None
    entry = OffEntry.objects.filter(pk=normalized).first()
    if entry is None:
        return None
    nutrition = None
    if entry.nutrition_checked_at is not None:
        nutrition = Nutrition(entry.nutrition_per, entry.protein_100g, entry.kcal_100g)
    return OffProduct(
        labels=frozenset(entry.labels),
        quantity=entry.quantity,
        categories=tuple(entry.categories),
        quantity_unit=entry.quantity_unit,
        nutrition=nutrition,
    )


def _save(record: OffRecord) -> bool:
    """Upsert unless the cached row is newer. A null timestamp never beats a dated row."""
    from catalog.models import OffEntry

    stored = OffEntry.objects.filter(pk=record.upc).values_list("last_modified", flat=True).first()
    if stored is not None and (record.last_modified is None or stored > record.last_modified):
        return False
    n = record.nutrition
    OffEntry.objects.update_or_create(
        upc=record.upc,
        defaults={
            "nutrition_per": n.basis if n else None,
            "protein_100g": n.protein if n else None,
            "kcal_100g": n.kcal if n else None,
            "nutrition_checked_at": datetime.now(UTC),
            "source": record.source,
            "labels": sorted(record.labels),
            "quantity": record.quantity,
            "quantity_unit": record.quantity_unit[:10] if record.quantity_unit else None,
            "categories": list(record.categories),
            "last_modified": record.last_modified,
        },
    )
    return True


def known_upcs() -> set[str]:
    from catalog.models import Product

    upcs = set()
    for value in Product.objects.exclude(upc__isnull=True).values_list("upc", flat=True).iterator():
        normalized = normalize_upc(value)
        if normalized:
            upcs.add(normalized)
    return upcs


def sync_file(path: Path, source: Source = "off") -> int:
    """Stream a gz or plain JSONL file and upsert rows for known UPCs. Returns rows written."""
    known = known_upcs()
    written = 0
    for line in _iter_lines(Path(path)):
        record = parse_off_line(line, source)
        if record is not None and record.upc in known:
            written += _save(record)
    return written


def _delta_sort_key(name: str) -> tuple[int, str]:
    match = re.search(r"_(\d+)_(\d+)\.", name)
    return (int(match.group(2)) if match else -1, name)


def sync_latest_delta(client: httpx.Client | None = None) -> tuple[str, int]:
    """Download the newest delta listed in the index, sync it, and delete the download."""
    if client is None:
        with default_client() as owned:
            return sync_latest_delta(owned)
    index = client.get(DELTA_BASE_URL + "index.txt")
    index.raise_for_status()
    names = [line.strip() for line in index.text.splitlines() if line.strip()]
    if not names:
        raise ValueError("delta index is empty")
    name = max(names, key=_delta_sort_key)
    with tempfile.TemporaryDirectory(prefix="off-delta-") as tmp:
        target = Path(tmp) / Path(name).name
        with client.stream("GET", DELTA_BASE_URL + name) as response:
            response.raise_for_status()
            with open(target, "wb") as out:
                for chunk in response.iter_bytes():
                    out.write(chunk)
        return name, sync_file(target, "off")


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None and response.status_code == 429:
        try:
            seconds = float(response.headers["Retry-After"])
        except (KeyError, ValueError):
            seconds = None
        if seconds is not None and math.isfinite(seconds):
            return min(max(seconds, MIN_INTERVAL_SECONDS), MAX_RETRY_AFTER_SECONDS)
    return max(BACKOFF_SECONDS * 2**attempt, MIN_INTERVAL_SECONDS)


def product_url(source: Source, upc: str) -> str:
    return f"{API_HOSTS[source]}/api/v2/product/{upc}"


def retryable(response: httpx.Response | None) -> bool:
    """A transport error, a 429 or a 5xx: worth asking again later."""
    return response is None or response.status_code >= 500 or response.status_code == 429


def read_product(
    response: httpx.Response, source: Source, upc: str
) -> OffRecord | None | Literal["missing"]:
    """A non-retryable product response: a record, "missing" (404 or status 0), or None."""
    if response.status_code == 404:
        return "missing"
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("status") == 0 or "product" not in body:
        return "missing"
    product = dict(body["product"]) if isinstance(body["product"], dict) else {}
    product["code"] = upc
    return _record_from_dict(product, source) or None


def _fetch(
    client: httpx.Client, source: Source, upc: str, sleep: Callable[[float], None]
) -> OffRecord | None | Literal["missing"]:
    """Return a record, "missing" for a 404, or None if the lookup failed (nothing is recorded)."""
    url = product_url(source, upc)
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.get(url, params={"fields": FIELDS})
        except httpx.TransportError:
            response = None
        if not retryable(response):
            return read_product(response, source, upc)
        if attempt < MAX_RETRIES:
            sleep(_retry_delay(response, attempt))
    return None


def backfill(
    limit: int = 50,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, int]:
    from catalog.models import OffEntry, Product

    if client is None:
        with default_client() as owned:
            return backfill(limit, owned, sleep)
    sleep = sleep or default_sleep
    existing = set(OffEntry.objects.values_list("pk", flat=True))
    stats = {"stored": 0, "not_found": 0, "failed": 0}
    seen: set[str] = set()
    requests_made = 0
    products = Product.objects.exclude(upc__isnull=True).select_related("category").order_by("pk")
    for product in products.iterator():
        if requests_made >= limit:
            break
        upc = normalize_upc(product.upc)
        if upc is None or upc in existing or upc in seen:
            continue
        seen.add(upc)
        household = product.category is not None and product.category.slug.startswith("household/")
        source: Source = "opf" if household else "off"
        if requests_made:
            sleep(MIN_INTERVAL_SECONDS)
        requests_made += 1
        result = _fetch(client, source, upc, sleep)
        if result == "missing":
            OffEntry.objects.update_or_create(
                upc=upc,
                defaults={
                    "source": source,
                    "labels": [],
                    "quantity": None,
                    "last_modified": None,
                    "nutrition_checked_at": datetime.now(UTC),
                },
            )
            stats["not_found"] += 1
        elif result is None:
            stats["failed"] += 1
        else:
            _save(result)
            stats["stored"] += 1
    return stats
