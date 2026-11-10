"""CJ Affiliate product feed connector (GraphQL, no Django import)."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from connectors.base import ConnectorError, SearchResult
from connectors.live import Budget, LiveSearchResult, retry_after
from normalize.types import RawOffer

ENDPOINT = "https://ads.api.cj.com/query"

QUERY = """
query Products($companyId: ID!, $keywords: [String!], $gtin: String, $limit: Int) {
  shoppingProducts(
    companyId: $companyId
    partnerStatus: JOINED
    keywords: $keywords
    gtin: $gtin
    advertiserCountries: ["US"]
    limit: $limit
  ) {
    totalCount
    count
    resultList {
      id advertiserId advertiserName advertiserCountry targetCountry joinedStatus
      title brand gtin link
      price { amount currency }
      salePrice { amount currency }
      salePriceEffectiveDateStart salePriceEffectiveDateEnd
      availability condition unitPricingMeasure size
      googleProductCategory { name }
      productType
    }
  }
}
"""

_MAX_RETAILER = 50


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _decimal(money: object) -> tuple[Decimal, str] | None:
    """(amount, currency) from a CJ money object, or None when missing or unusable."""
    if not isinstance(money, dict):
        return None
    amount, currency = money.get("amount"), money.get("currency")
    if amount is None or not isinstance(currency, str):
        return None
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0:
        return None
    return value, currency.upper()


def _when(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _sale_valid_now(item: dict, now: datetime) -> bool:
    for key, ok in (
        ("salePriceEffectiveDateStart", lambda t: t <= now),
        ("salePriceEffectiveDateEnd", lambda t: now <= t),
    ):
        raw = item.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        parsed = _when(raw)
        if parsed is None or not ok(parsed):
            return False
    return True


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _size_text(*candidates: object) -> str | None:
    """First candidate with both a digit and a letter; never joins fragments (that would guess)."""
    for value in candidates:
        text = _text(value)
        if text and any(ch.isdigit() for ch in text) and any(ch.isalpha() for ch in text):
            return text
    return None


class CjConnector:
    """Plain class (not a dataclass) so repr never exposes credentials."""

    name = "cj"
    budget_keys = ("cj",)
    max_cache_age = timedelta(hours=24)
    refresh_after = timedelta(hours=12)
    records_price_history = False

    def __init__(
        self,
        token: str,
        company_id: str,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._token = token
        self._company_id = company_id
        self._client = client or httpx.Client(timeout=15)
        self._clock = clock

    def __repr__(self) -> str:
        return "CjConnector()"

    def close(self) -> None:
        self._client.close()

    def _request(self, variables: dict) -> httpx.Response:
        try:
            return self._client.post(
                ENDPOINT,
                json={"query": QUERY, "variables": variables},
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"CJ request failed ({type(exc).__name__})") from None

    def _variables(self, limit: int, **extra: object) -> dict:
        return {"companyId": self._company_id, "limit": limit, **extra}

    def _parse(self, resp: httpx.Response) -> SearchResult:
        if resp.status_code != 200:
            raise ConnectorError(f"CJ search failed with HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError:
            raise ConnectorError("CJ search returned an unreadable response") from None
        if not isinstance(body, dict):
            raise ConnectorError("CJ search returned an unexpected response shape")
        if body.get("errors"):
            # No message text: it could echo the company ID.
            raise ConnectorError("CJ search returned GraphQL errors")
        try:
            items = body["data"]["shoppingProducts"]["resultList"]
        except (KeyError, TypeError):
            raise ConnectorError("CJ search returned an unexpected response shape") from None
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ConnectorError("CJ search returned an unexpected response shape")
        now = self._clock()
        offers: list[RawOffer] = []
        skipped = 0
        for item in items:
            offer = self._map(item, now)
            if offer is None:
                skipped += 1
            else:
                offers.append(offer)
        return SearchResult(offers, skipped)

    def search(self, query: str, limit: int = 25) -> SearchResult:
        return self._parse(self._request(self._variables(limit, keywords=[query])))

    def live_search(self, phrase: str, limit: int, budget: Budget) -> LiveSearchResult:
        """One budgeted request; the client's 15 s timeout bounds the job."""
        budget.acquire("cj")
        try:
            resp = self._request(self._variables(limit, keywords=[phrase]))
        except ConnectorError:
            budget.report("cj", None, None)
            raise
        budget.report("cj", resp.status_code, retry_after(resp))
        result = self._parse(resp)
        return LiveSearchResult(offers=result.offers, skipped=result.skipped)

    @staticmethod
    def _gtin_variants(gtin: str) -> list[str]:
        stripped = gtin.lstrip("0")
        variants = [gtin, stripped]
        if stripped:
            variants += [stripped.zfill(n) for n in (12, 13, 14)]
        seen: list[str] = []
        for v in variants:
            if v and v not in seen:
                seen.append(v)
        return seen

    def lookup_gtin(self, gtin: str, budget: Budget | None = None) -> SearchResult:
        """First GTIN spelling that returns results; each try costs one budget token."""
        last = SearchResult([], 0)
        for variant in self._gtin_variants(gtin):
            if budget is not None:
                budget.acquire("cj")
            try:
                resp = self._request(self._variables(10, gtin=variant))
            except ConnectorError:
                if budget is not None:
                    budget.report("cj", None, None)
                raise
            if budget is not None:
                budget.report("cj", resp.status_code, retry_after(resp))
            last = self._parse(resp)
            if last.offers or last.skipped:
                return last
        return last

    def _map(self, item: object, now: datetime) -> RawOffer | None:
        if not isinstance(item, dict):
            return None
        if item.get("joinedStatus") is not True:
            return None
        condition = item.get("condition")
        if condition is not None and not isinstance(condition, str):
            return None
        if condition and condition.strip().lower() != "new":
            return None
        availability = item.get("availability")
        if not isinstance(availability, str) or availability.strip().lower() not in (
            "in stock",
            "in_stock",
        ):
            return None
        for key in ("advertiserCountry", "targetCountry"):
            country = item.get(key)
            if country not in (None, "") and (
                not isinstance(country, str) or country.strip().upper() != "US"
            ):
                return None
        title, link = _text(item.get("title")), _text(item.get("link"))
        advertiser, item_id = _text(item.get("advertiserName")), item.get("id")
        if not title or not link or not advertiser or item_id in (None, ""):
            return None
        if not link.lower().startswith(("http://", "https://")):
            return None
        code = ("cj-" + slug(advertiser))[:_MAX_RETAILER].rstrip("-")
        if code in ("cj", "cj-"):
            return None

        regular = _decimal(item.get("price"))
        if regular is None or regular[1] != "USD":
            return None
        price = regular[0]
        sale = _decimal(item.get("salePrice"))
        if (
            sale is not None
            and sale[1] == "USD"
            and sale[0] < regular[0]
            and _sale_valid_now(item, now)
        ):
            price = sale[0]

        gtin = _text(item.get("gtin"))
        upc = gtin if gtin and gtin.isascii() and gtin.isdigit() else None
        category = None
        gpc = item.get("googleProductCategory")
        if isinstance(gpc, dict):
            category = _text(gpc.get("name"))
        if not category and isinstance(item.get("productType"), list):
            parts = [p.strip() for p in item["productType"] if isinstance(p, str) and p.strip()]
            category = " > ".join(parts) or None
        fields: dict[str, str] = {}
        if category:
            fields["category_path"] = category
        fields["retailer_name"] = advertiser

        return RawOffer(
            retailer=code,
            retailer_item_id=str(item_id),
            title=title,
            price=price,
            currency="USD",
            url=link,
            fetched_at=now,
            upc=upc,
            brand=_text(item.get("brand")),
            size_text=_size_text(item.get("unitPricingMeasure"), item.get("size")),
            fields=fields,
        )
