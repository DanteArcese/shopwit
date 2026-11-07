"""Walmart Affiliate API connector (no Django import)."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from connectors.base import ConnectorError, SearchResult
from normalize.types import RawOffer

SEARCH_URL = (
    "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/search"
)


def load_private_key(text: str):
    """Load an RSA key from a PEM or single-line Base64 DER (PKCS#8)."""
    text = text.strip()
    try:
        if text.startswith("-----BEGIN"):
            return serialization.load_pem_private_key(text.encode(), password=None)
        return serialization.load_der_private_key(base64.b64decode(text), password=None)
    except Exception:
        # Deliberately no chaining: the original message could echo key material.
        raise ConnectorError("Walmart private key could not be parsed") from None


def sign(consumer_id: str, timestamp_ms: str, key_version: str, private_key) -> str:
    message = f"{consumer_id}\n{timestamp_ms}\n{key_version}\n".encode()
    signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


class WalmartConnector:
    """Plain class (not a dataclass) so repr never exposes credentials."""

    name = "walmart"
    max_cache_age = timedelta(hours=24)
    records_price_history = False

    def __init__(
        self,
        consumer_id: str,
        private_key: str,
        key_version: str,
        publisher_id: str | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._consumer_id = consumer_id
        self._key = load_private_key(private_key)
        self._key_version = key_version
        self._publisher_id = publisher_id
        self._client = client or httpx.Client(timeout=15)
        self._clock = clock

    def __repr__(self) -> str:
        return "WalmartConnector()"

    def _sign(self, timestamp_ms: str) -> str:
        return sign(self._consumer_id, timestamp_ms, self._key_version, self._key)

    def _headers(self) -> dict[str, str]:
        ts = str(int(self._clock() * 1000))
        return {
            "WM_CONSUMER.ID": self._consumer_id,
            "WM_CONSUMER.INTIMESTAMP": ts,
            "WM_SEC.KEY_VERSION": self._key_version,
            "WM_SEC.AUTH_SIGNATURE": self._sign(ts),
        }

    def search(self, query: str, limit: int = 25) -> SearchResult:
        params = {"query": query, "numItems": str(limit)}
        if self._publisher_id:
            params["publisherId"] = self._publisher_id
        try:
            resp = self._client.get(SEARCH_URL, params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"Walmart request failed ({type(exc).__name__})"
            ) from None
        if resp.status_code != 200:
            raise ConnectorError(f"Walmart search failed with HTTP {resp.status_code}")
        try:
            items = resp.json().get("items") or []
        except (ValueError, AttributeError):
            raise ConnectorError(
                "Walmart search returned an unreadable response"
            ) from None
        fetched_at = datetime.now(UTC)
        offers: list[RawOffer] = []
        skipped = 0
        for item in items:
            offer = self._map(item, fetched_at)
            if offer is None:
                skipped += 1
            else:
                offers.append(offer)
        return SearchResult(offers, skipped)

    @staticmethod
    def _map(item: dict, fetched_at: datetime) -> RawOffer | None:
        name, item_id, sale = (
            item.get("name"),
            item.get("itemId"),
            item.get("salePrice"),
        )
        url = item.get("productUrl")
        if not name or item_id is None or sale is None or not url:
            return None
        try:
            price = Decimal(str(sale))
        except InvalidOperation:
            return None
        if not price.is_finite() or price <= 0:
            return None
        return RawOffer(
            retailer="walmart",
            retailer_item_id=str(item_id),
            title=name,
            price=price,
            currency="USD",
            url=url,
            fetched_at=fetched_at,
            upc=item.get("upc"),
            brand=item.get("brandName"),
            size_text=item.get("size"),
        )
