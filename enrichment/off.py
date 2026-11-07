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

from normalize.types import OffProduct

USER_AGENT = "unitpricer/0.1 (+https://github.com/DanteArcese/unitpricer)"
DELTA_BASE_URL = "https://static.openfoodfacts.org/data/delta/"
API_HOSTS = {
    "off": "https://world.openfoodfacts.org",
    "opf": "https://world.openproductsfacts.org",
}
FIELDS = "code,labels_tags,quantity,last_modified_t"
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


def normalize_upc(code: str | None) -> str | None:
    if not code:
        return None
    digits = re.sub(r"\D", "", code)
    if len(digits) == 12:
        return digits.rjust(13, "0")
    if len(digits) in (8, 13, 14):
        return digits
    return None


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
    return OffRecord(upc, source, labels, quantity, last_modified)


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
    return OffProduct(labels=frozenset(entry.labels), quantity=entry.quantity)


def _save(record: OffRecord) -> bool:
    """Upsert unless the cached row is newer. A null timestamp never beats a dated row."""
    from catalog.models import OffEntry

    stored = OffEntry.objects.filter(pk=record.upc).values_list("last_modified", flat=True).first()
    if stored is not None and (record.last_modified is None or stored > record.last_modified):
        return False
    OffEntry.objects.update_or_create(
        upc=record.upc,
        defaults={
            "source": record.source,
            "labels": sorted(record.labels),
            "quantity": record.quantity,
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


def _fetch(
    client: httpx.Client, source: Source, upc: str, sleep: Callable[[float], None]
) -> OffRecord | None | Literal["missing"]:
    """Return a record, "missing" for a 404, or None if the lookup failed (nothing is recorded)."""
    url = f"{API_HOSTS[source]}/api/v2/product/{upc}"
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.get(url, params={"fields": FIELDS})
        except httpx.TransportError:
            response = None
        if response is not None and response.status_code < 500 and response.status_code != 429:
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
                defaults={"source": source, "labels": [], "quantity": None, "last_modified": None},
            )
            stats["not_found"] += 1
        elif result is None:
            stats["failed"] += 1
        else:
            _save(result)
            stats["stored"] += 1
    return stats
