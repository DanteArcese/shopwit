"""Open Food Facts text search, then Open Prices for the barcodes it finds (no Django import).

The search words are the only thing sent. Off by default (``UNITPRICER_LIVE_OFF``).
Holds no per-request state on the instance, and each worker thread builds its own.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date, timedelta

import httpx

from connectors.base import ConnectorError
from connectors.live import Budget, LiveSearchResult, retry_after
from connectors.open_prices import MAX_CODES, OpenPricesClient
from enrichment.off import USER_AGENT, OffRecord, _record_from_dict, normalize_upc

SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
FIELDS = (
    "code,product_name,brands,quantity,product_quantity,product_quantity_unit,"
    "labels_tags,categories_tags"
)
PAGE_SIZE = 50
ATTEMPTS = 3  # one try and two retries on a 503
TIMEOUT = 20.0
JOB_DEADLINE = 90.0  # seconds; far under the fetch queue's STUCK_AFTER (5 min)
CURRENCY = "USD"


class _Prepaid:
    """A budget whose first ``acquire(key)`` spends a token already taken."""

    def __init__(self, budget: Budget, key: str) -> None:
        self._budget = budget
        self._key = key
        self._paid = True

    def acquire(self, key: str) -> None:
        if key == self._key and self._paid:
            self._paid = False
            return
        self._budget.acquire(key)

    def report(self, key: str, status: int | None, retry_after: float | None = None) -> None:
        self._budget.report(key, status, retry_after)


class OffPricesSource:
    name = "off-prices"
    budget_keys = ("off-search", "openprices")
    max_cache_age = timedelta(days=90)
    refresh_after = timedelta(hours=24)

    def __init__(
        self,
        client: httpx.Client | None = None,
        prices: OpenPricesClient | None = None,
        today: Callable[[], date] = date.today,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        self._prices = prices or OpenPricesClient(client=self._client)
        self._today = today
        self._clock = clock

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
        self._prices.close()

    def _search(self, phrase: str, budget: Budget, deadline: float) -> list[object]:
        params = {
            "search_terms": phrase,
            "json": "1",
            "page_size": str(PAGE_SIZE),
            "fields": FIELDS,
            "tagtype_0": "countries",
            "tag_contains_0": "contains",
            "tag_0": "united-states",
        }
        status = 0
        for _attempt in range(ATTEMPTS):
            if self._clock() >= deadline:
                raise ConnectorError("Open Food Facts search ran past its deadline")
            budget.acquire("off-search")
            try:
                resp = self._client.get(SEARCH_URL, params=params)
            except httpx.HTTPError as exc:
                budget.report("off-search", None, None)
                raise ConnectorError(
                    f"Open Food Facts search failed ({type(exc).__name__})"
                ) from None
            status = resp.status_code
            budget.report("off-search", status, retry_after(resp))
            if status == 503:  # the shared backoff paces the retry through acquire
                continue
            break
        if status != 200:
            raise ConnectorError(f"Open Food Facts search failed: HTTP {status}")
        try:
            data = json.loads(resp.content)
        except ValueError:
            raise ConnectorError("Open Food Facts returned unreadable data") from None
        products = data.get("products") if isinstance(data, dict) else None
        if not isinstance(products, list):
            raise ConnectorError("Open Food Facts returned an unexpected shape")
        return products

    def live_search(self, phrase: str, limit: int, budget: Budget) -> LiveSearchResult:
        deadline = self._clock() + JOB_DEADLINE
        # Take the prices token first: a shortage then requeues the job before the OFF search,
        # instead of after it, where the retry would repeat the search. (With no barcodes found
        # the token goes unused: one second of the 1 req/s bucket.)
        budget.acquire("openprices")
        products = self._search(phrase, budget, deadline)
        records: dict[str, OffRecord] = {}
        skipped = 0
        for product in products:
            record = _record_from_dict(product, "off")
            if record is None or normalize_upc(record.upc) is None:
                skipped += 1
            elif record.upc not in records:
                records[record.upc] = record
        codes = list(records)[: min(MAX_CODES, limit) if limit > 0 else MAX_CODES]
        observations = []
        if codes:
            if self._clock() >= deadline:
                raise ConnectorError("Open Prices lookup ran past its deadline")
            since = self._today() - self.max_cache_age
            observations = self._prices.fetch_codes(
                codes, since, CURRENCY, _Prepaid(budget, "openprices")
            )
        return LiveSearchResult(
            observations=observations, off_records=list(records.values()), skipped=skipped
        )
