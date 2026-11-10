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
      shipping { price { amount currency } country service }
    }
  }
}
"""

_MAX_RETAILER = 50
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")


class CjRequestError(ConnectorError):
    """A failed CJ request. ``retryable`` is False when the failure is specific to the input
    (HTTP 400, or GraphQL ``errors``): asking again with the same input will fail again.
    Everything else (other HTTP statuses, including 401/403/429/5xx) is retryable: the
    caller should stop and try later. Messages are fixed strings, never response text."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


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


def _shipping(options: object) -> str | None:
    """Lowest US (or country-less) USD shipping price as a plain decimal string; zero is
    free shipping. None when no option is usable: unknown, never guessed."""
    if not isinstance(options, list):
        return None
    best: Decimal | None = None
    for option in options:
        if not isinstance(option, dict):
            continue
        country = option.get("country")
        if country not in (None, "") and (
            not isinstance(country, str) or country.strip().upper() != "US"
        ):
            continue
        money = option.get("price")
        if not isinstance(money, dict):
            continue
        amount, currency = money.get("amount"), money.get("currency")
        if amount is None or not isinstance(currency, str) or currency.upper() != "USD":
            continue
        try:
            value = Decimal(str(amount))
        except InvalidOperation:
            continue
        if not value.is_finite() or value < 0:
            continue
        if best is None or value < best:
            best = value
    return None if best is None else format(best, "f")


def _when(value: object, end: bool = False) -> datetime | None:
    """Parse a CJ date or datetime. A date-only ``end`` covers that whole UTC day
    (through 23:59:59.999); a datetime is used as given."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if end and _DATE_ONLY.fullmatch(text):
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999000)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _sale_valid_now(item: dict, now: datetime) -> bool:
    for key, end, ok in (
        ("salePriceEffectiveDateStart", False, lambda t: t <= now),
        ("salePriceEffectiveDateEnd", True, lambda t: now <= t),
    ):
        raw = item.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        parsed = _when(raw, end)
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

    def _products(self, resp: httpx.Response) -> dict:
        """The ``shoppingProducts`` object of a response, or a credential-free error."""
        if resp.status_code != 200:
            raise CjRequestError(
                f"CJ search failed with HTTP {resp.status_code}",
                retryable=resp.status_code != 400,
            )
        try:
            body = resp.json()
        except ValueError:
            raise ConnectorError("CJ search returned an unreadable response") from None
        if not isinstance(body, dict):
            raise ConnectorError("CJ search returned an unexpected response shape")
        if body.get("errors"):
            # No message text: it could echo the company ID.
            raise CjRequestError("CJ search returned GraphQL errors", retryable=False)
        try:
            products = body["data"]["shoppingProducts"]
        except (KeyError, TypeError):
            raise ConnectorError("CJ search returned an unexpected response shape") from None
        if not isinstance(products, dict):
            raise ConnectorError("CJ search returned an unexpected response shape")
        return products

    def _parse(self, resp: httpx.Response) -> SearchResult:
        try:
            items = self._products(resp)["resultList"]
        except KeyError:
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
        result = self._parse(self._budgeted(self._variables(limit, keywords=[phrase]), budget))
        return LiveSearchResult(offers=result.offers, skipped=result.skipped)

    def has_joined_advertisers(self, budget: Budget | None = None) -> bool:
        """Whether any joined US advertiser has products: one budgeted ``limit: 1`` request."""
        resp = self._budgeted(self._variables(1), budget)
        total = self._products(resp).get("totalCount")
        if isinstance(total, bool) or not isinstance(total, int):
            raise ConnectorError("CJ search returned an unexpected response shape")
        return total > 0

    def _budgeted(self, variables: dict, budget: Budget | None) -> httpx.Response:
        if budget is not None:
            budget.acquire("cj")
        try:
            resp = self._request(variables)
        except ConnectorError:
            if budget is not None:
                budget.report("cj", None, None)
            raise
        if budget is not None:
            budget.report("cj", resp.status_code, retry_after(resp))
        return resp

    @staticmethod
    def _gtin_variants(gtin: str) -> list[str]:
        """The 12-digit UPC-A spelling first (CJ's GTINs are usually 12 digits), then the
        input as given, then stripped of leading zeros, then zero-padded to 13 and 14."""
        variants: list[str] = []
        if len(gtin) == 13 and gtin.startswith("0"):
            variants.append(gtin[1:])
        elif len(gtin) == 12:
            variants.append(gtin)
        variants.append(gtin)
        stripped = gtin.lstrip("0")
        variants.append(stripped)
        if stripped:
            variants += [stripped.zfill(n) for n in (13, 14)]
        seen: list[str] = []
        for v in variants:
            if v and v not in seen:
                seen.append(v)
        return seen

    def lookup_gtin(self, gtin: str, budget: Budget | None = None) -> SearchResult:
        """First GTIN spelling that returns results; each try costs one budget token."""
        last = SearchResult([], 0)
        for variant in self._gtin_variants(gtin):
            last = self._parse(self._budgeted(self._variables(10, gtin=variant), budget))
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
        if upc and len(upc) == 14 and upc.startswith("0"):
            upc = upc[1:]  # GTIN-14 "0" + GTIN-13: the 13-digit form links to our products
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
        shipping = _shipping(item.get("shipping"))
        if shipping is not None:
            fields["shipping"] = shipping

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
