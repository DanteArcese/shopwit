"""Open Prices client: crowd-sourced shelf prices (no Django import).

Only the public ``/api/v1/prices`` endpoint is read. Prices arrive as JSON
floats, so responses are decoded with ``parse_float=Decimal`` to keep them exact.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from connectors.base import ConnectorError
from enrichment.off import normalize_upc
from normalize.generic import off_category_path
from normalize.types import RawOffer

PRICES_URL = "https://prices.openfoodfacts.org/api/v1/prices"
PRICE_PAGE = "https://prices.openfoodfacts.org/prices/{id}"
USER_AGENT = "unitpricer/0.1 (+https://github.com/DanteArcese/unitpricer)"
PAGE_SIZE = 100
MAX_PAGES = 500
MIN_INTERVAL = 1.0
ATTEMPTS = 3
BACKOFF = (2.0, 4.0)


@dataclass(frozen=True)
class Location:
    osm_type: str
    osm_id: int
    name: str | None
    brand: str | None
    city: str | None
    postcode: str | None
    country_code: str | None
    type: str
    lat: float | None = None
    lon: float | None = None

    @property
    def code(self) -> str:
        return f"osm-{self.osm_type[0].lower()}{self.osm_id}"


@dataclass(frozen=True)
class Observation:
    price_id: int
    type: str
    product_code: str | None
    product_name: str | None
    brands: str | None
    quantity: str | None
    labels: frozenset[str]
    price: Decimal
    currency: str
    is_sale: bool
    date: date
    location: Location | None
    proof_type: str | None
    source: str | None
    duplicate_of: int | None
    categories: tuple[str, ...] = ()


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def _int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _coordinate(value: object, limit: float) -> float | None:
    """A finite number within [-limit, limit], else None (never coerced from text)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = float(value)
    if not math.isfinite(number) or abs(number) > limit:
        return None
    return number


def _parse_location(data: object) -> Location | None:
    if not isinstance(data, dict):
        return None
    osm_id = _int(data.get("osm_id"))
    lat, lon = _coordinate(data.get("osm_lat"), 90), _coordinate(data.get("osm_lon"), 180)
    if lat is None or lon is None:
        lat = lon = None  # half a position is no position
    return Location(
        osm_type=_text(data.get("osm_type")) or "",
        osm_id=osm_id if osm_id is not None else 0,
        name=_text(data.get("osm_name")),
        brand=_text(data.get("osm_brand")),
        city=_text(data.get("osm_address_city")),
        postcode=_text(data.get("osm_address_postcode")),
        country_code=_text(data.get("osm_address_country_code")),
        type=_text(data.get("type")) or "",
        lat=lat,
        lon=lon,
    )


def _quantity(product: dict) -> str | None:
    quantity = _text(product.get("quantity"))
    if quantity:
        return quantity
    amount, unit = product.get("product_quantity"), _text(product.get("product_quantity_unit"))
    if isinstance(amount, bool) or amount is None or unit is None:
        return None
    if isinstance(amount, Decimal) and amount == amount.to_integral_value():
        amount = int(amount)
    return f"{amount} {unit}"


def parse_item(item: object) -> Observation | None:
    """Map one API item; None when id, price or date is missing or malformed."""
    if not isinstance(item, dict):
        return None
    price_id = _int(item.get("id"))
    raw_price = item.get("price")
    if price_id is None or isinstance(raw_price, bool) or raw_price is None:
        return None
    try:
        price = raw_price if isinstance(raw_price, Decimal) else Decimal(str(raw_price))
    except InvalidOperation:
        return None
    if not price.is_finite():
        return None
    raw_date = item.get("date")
    if not isinstance(raw_date, str) or len(raw_date) != 10:
        return None
    try:
        day = date.fromisoformat(raw_date)
    except ValueError:
        return None
    product = item.get("product")
    product = product if isinstance(product, dict) else {}
    tags = product.get("labels_tags")
    labels = frozenset(t for t in tags if isinstance(t, str)) if isinstance(tags, list) else frozenset()
    cat_tags = product.get("categories_tags")
    categories = (
        tuple(t for t in cat_tags if isinstance(t, str)) if isinstance(cat_tags, list) else ()
    )
    proof = item.get("proof")
    code = item.get("product_code")
    return Observation(
        price_id=price_id,
        type=_text(item.get("type")) or "",
        product_code=str(code) if code is not None else None,
        product_name=_text(product.get("product_name")),
        brands=_text(product.get("brands")),
        quantity=_quantity(product),
        labels=labels,
        price=price,
        currency=_text(item.get("currency")) or "",
        is_sale=item.get("price_is_discounted") is True,
        date=day,
        location=_parse_location(item.get("location")),
        proof_type=_text(proof.get("type")) if isinstance(proof, dict) else None,
        source=_text(item.get("source")),
        duplicate_of=_int(item.get("duplicate_of")),
        categories=categories,
    )


def chain_key(location: Location) -> tuple[str, str]:
    """(retailer code, chain name). Brand tag if present, else the location's own name."""
    source = (location.brand or "").strip() or (location.name or "")
    chain_name = re.sub(r"\s+", " ", source).strip()
    slug = chain_name.casefold().replace("&", " and ")
    slug = re.sub(r"['’]", "", slug)
    slug = re.sub(r"[\W_]+", "-", slug).strip("-")
    return f"op-{slug}"[:50], chain_name


def skip_reason(
    obs: Observation, *, country: str, currency: str, include_assisted: bool, today: date
) -> str | None:
    if obs.type != "PRODUCT":
        return "type"
    if obs.duplicate_of is not None:
        return "duplicate"
    if obs.currency != currency:
        return "currency"
    loc = obs.location
    if loc is None or not loc.name:
        return "no_location"
    if loc.type == "ONLINE":
        return "online"
    if loc.type != "OSM" or not loc.osm_type or not loc.osm_id:
        return "no_location"
    if chain_key(loc)[0] == "op-":  # name is only punctuation: no usable chain slug
        return "no_location"
    if (loc.country_code or "").upper() != country.upper():
        return "country"
    if not include_assisted and obs.source and "/experiments/" in obs.source:
        return "assisted"
    if normalize_upc(obs.product_code) is None:
        return "no_code"
    if not obs.product_name:
        return "no_name"
    if obs.date > today:
        return "future_date"
    if obs.price <= 0:
        return "price"
    if obs.price != obs.price.quantize(Decimal("0.01")):
        return "precision"
    return None


def to_raw_offer(obs: Observation, fetched_at: datetime) -> RawOffer:
    """Callers must have checked skip_reason() is None."""
    location = obs.location
    upc = normalize_upc(obs.product_code)
    assert location is not None and upc is not None and obs.product_name is not None
    return RawOffer(
        retailer=chain_key(location)[0],
        retailer_item_id=f"{upc}@{location.code}",
        title=obs.product_name,
        price=obs.price,
        currency=obs.currency,
        url=PRICE_PAGE.format(id=obs.price_id),
        fetched_at=fetched_at,
        upc=upc,
        brand=obs.brands,
        size_text=obs.quantity,
        fields={"category_path": path} if (path := off_category_path(obs.categories)) else {},
    )


def _retry_after(resp: httpx.Response) -> float | None:
    """A Retry-After given in seconds; anything else (an HTTP date, garbage) is None."""
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


class OpenPricesClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        budget_wait: Callable[[str], None] | None = None,
        budget_report: Callable[..., None] | None = None,
    ) -> None:
        # With budget_wait (a DbBudget's wait), requests draw from the shared "openprices"
        # bucket instead of this client's own 1 req/s pacing.
        self._budget_wait = budget_wait
        # With budget_report (a DbBudget's report), every response status (None for a
        # transport error) feeds the shared row's backoff, which then paces the retry.
        self._budget_report = budget_report
        self._client = client or httpx.Client(
            timeout=30, headers={"User-Agent": USER_AGENT}
        )
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.unparsed = 0
        self.unparsed_ids: set[int] = set()

    def close(self) -> None:
        self._client.close()

    def _pace(self) -> None:
        if self._last is not None:
            wait = MIN_INTERVAL - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def _get_page(self, params: dict[str, str], page: int) -> dict:
        status = 0
        for attempt in range(ATTEMPTS):
            if self._budget_wait is not None:
                self._budget_wait("openprices")
            else:
                self._pace()
            try:
                resp = self._client.get(PRICES_URL, params={**params, "page": str(page)})
                status = resp.status_code
            except httpx.HTTPError as exc:
                if self._budget_report is not None:
                    self._budget_report("openprices", None, None)
                raise ConnectorError(
                    f"Open Prices request failed ({type(exc).__name__}) on page {page}"
                ) from None
            if self._budget_report is not None:
                self._budget_report("openprices", status, _retry_after(resp))
            if status == 429 or status >= 500:
                if attempt < ATTEMPTS - 1 and self._budget_report is None:
                    self._sleep(BACKOFF[attempt])  # with a budget, its backoff paces the retry
                continue
            break
        if status != 200:
            raise ConnectorError(f"Open Prices request failed: HTTP {status} on page {page}")
        try:
            data = json.loads(resp.content, parse_float=Decimal)
        except ValueError:
            raise ConnectorError(f"Open Prices returned unreadable data on page {page}") from None
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ConnectorError(f"Open Prices returned an unexpected shape on page {page}")
        return data

    def fetch_window(self, since: date, currency: str) -> list[Observation]:
        params = {
            "currency": currency,
            "date__gte": since.isoformat(),
            "type": "PRODUCT",
            "location__type": "OSM",
            "duplicate_of__isnull": "true",
            "order_by": "-id",
            "size": str(PAGE_SIZE),
        }
        # Offset paging stays: the documented filters (docs/research/2026-11-08-open-prices.md)
        # have no id__lt/id__gt, so keyset paging is not available. order_by=-id keeps
        # pages stable except when new rows arrive mid-pull; rows then shift by one
        # page, which the `seen` de-dupe below absorbs (a missed row is re-fetched next sync).
        seen: set[int] = set()
        out: list[Observation] = []
        page = 1
        while True:
            data = self._get_page(params, page)
            pages = _int(data.get("pages")) or 0
            if pages > MAX_PAGES:
                raise ConnectorError(f"Open Prices window exceeds {MAX_PAGES} pages")
            for item in data["items"]:
                obs = parse_item(item)
                if obs is None:
                    self.unparsed += 1
                    bad_id = _int(item.get("id")) if isinstance(item, dict) else None
                    if bad_id is not None:
                        self.unparsed_ids.add(bad_id)
                elif obs.price_id not in seen:
                    seen.add(obs.price_id)
                    out.append(obs)
            if page >= pages:
                return out
            page += 1
