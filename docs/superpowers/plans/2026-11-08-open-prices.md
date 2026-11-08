# Open Prices (Observed Shelf Prices) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Category pages gain a "Seen in stores" section of US shelf prices from Open Prices, ranked by unit price and labelled "seen at <store> on <date>". The data is synced once a day, kept apart from live offers, and attributed under the ODbL.

**Architecture:** A Django-free client (`connectors/open_prices.py`) pages the Open Prices prices API for the last 90 days of USD prices and yields `Observation`s. A daily sync job (`catalog/observed.py`) stores the observations and their stores in separate tables. It then turns the latest observation per (store, UPC) into an `Offer` with `kind="observed"`, using the existing normalizer and `_upsert`, and merges products. Freshness is split: `fresh_offers` stays live-only, so search, lists and outbound links are untouched, and a new `fresh_observed` filters by observation date. Browse ranks each kind separately.

**Tech Stack:** Django 5.2, httpx (`MockTransport` recordings), Postgres, pytest-django. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-07-unitpricer-design.md` (§3 Principles, §5.1–5.4, §7, §11, §14) and `docs/research/2026-11-08-open-prices.md` (endpoints, fields, volumes, ODbL). Issue #61.

## Decisions

Decisions marked **(confirm)** need the user to sign off before Task 1. The rest follow from the spec and the research.

- **D1 Observed, not live (confirm).** Open Prices rows are dated shelf prices at one store. They become `Offer(kind="observed", observed_on=<date>)`, shown only in their own "Seen in stores" section on category pages, ranked by unit price within that section. They never enter the live ranking, free-text search results, lists or click tracking. The UI says "seen at <store> on <date>", never "price as of".
- **D2 Freshness by observation date.** Observed offers show while `observed_on >= today − UNITPRICER_OBSERVED_MAX_AGE_DAYS` (default **90**). The 24 h affiliate cutoff does not apply. Hiding is based on age, not on sync time, so a failed sync never hides good data and stale data still ages out.
- **D3 Stores are OSM locations (confirm).** A store is one OSM location: code `osm-<n|w|r><osm_id>` (e.g. `osm-n1392117416`), shown as "<osm_name>, <city>" (e.g. "Safeway, Mountain View"). It goes in the store selector next to retailers (96 US locations today). Locations with no `osm_name`, and `ONLINE` locations, are skipped. The alternative of grouping by chain (`osm_brand`) hides price differences between locations, so it is not used.
- **D4 No outbound shop link.** An observed row links to its Open Prices price page (`https://prices.openfoodfacts.org/prices/<id>`, label "Price on Open Prices"), plain `rel="noopener noreferrer"`, with no ping, no affiliate mark and no click event.
- **D5 Normalization.** Barcode prices only (`type=PRODUCT`). Each becomes a `RawOffer`: title `product.product_name`, brand `product.brands`, size `product.quantity`, else `"{product_quantity} {product_quantity_unit}"`, UPC from `normalize_upc(product_code)`. It goes through the existing `normalize()`. `product.labels_tags` serves as the `OffProduct` when no `OffEntry` exists. Rows with no product name or an invalid code are skipped. `CATEGORY` prices (`price_per` KILOGRAM/UNIT/LITER) are skipped until a produce category exists.
- **D6 Currency.** New site setting `UNITPRICER_CURRENCY` (default `USD`). Both `fresh_offers` and `fresh_observed` return only offers in that currency. There is no FX. Open Prices is ingested only in that currency.
- **D7 AI-assisted entries excluded by default (confirm).** 52% of US rows come from Open Prices' price-tag and receipt "assistant" flows, where a model pre-fills the price from a photo and a person confirms it. Principle 4 forbids AI in *our* pipeline, and we run none. To be safe, rows whose `source` contains `/experiments/` are skipped unless `UNITPRICER_OPEN_PRICES_ASSISTED=1`. Including them roughly doubles coverage (milk 55→118, coffee 92→227).
- **D8 Scope: US/USD only.** Set by `UNITPRICER_OPEN_PRICES_COUNTRY` (default `US`) and `UNITPRICER_CURRENCY`. Another country later is a settings change, not code.
- **D9 Sync cadence.** Once every `UNITPRICER_OPEN_PRICES_HOURS` (default 24), only when `UNITPRICER_OPEN_PRICES=1`. Each sync re-pulls the whole 90-day window (`currency`, `date__gte`, `type=PRODUCT`, `location__type=OSM`, `duplicate_of__isnull=true`, `order_by=-id`, `size=100`), which is about 110 requests at ≥ 1 s spacing. There is no `updated__gte` filter, so a full window re-pull is the only way to pick up edits and deletions. Rows missing from a **successful** pull are deleted. A failed pull deletes nothing.
- **D10 Sale prices** are kept and labelled "sale price". `price_without_discount` is not shown.
- **D11 No `PriceHistory` writes.** The observation table already holds the window's history.
- **D12 License (confirm before public launch).** Open Prices data lives in its own tables (`Store`, `OpenPriceObservation`) plus observed `Offer` rows. The ODbL §4.3 notice is shown under the section and on the disclosure page. Under §4.6 we offer the method (link to the AGPL source and category YAMLs) and a JSONL export (`export_open_prices`) of the observations plus each observed offer's own attributes and measures, never Walmart-sourced values. A public dump URL waits for the public launch (the beta is gated). The merged `Product.attributes` question is shared with OFF and needs a legal check.

## Global Constraints

- Python with `uv` only. No new dependencies. No AI/LLM libraries or calls. AGPL-3.0-or-later.
- Tests never touch the network. Open Prices responses come from trimmed real recordings in `tests/connectors/recordings/open_prices/`, with the `owner` field removed from every item and nested object.
- Politeness: User-Agent `unitpricer/0.1 (+https://github.com/DanteArcese/unitpricer)` (same as `enrichment/off.py`), ≥ 1 s between requests, retry on 429/5xx with backoff 2 s then 4 s (3 attempts), and at most 500 pages per sync.
- `ConnectorError` messages carry no full URLs with query strings (matches `connectors/base.py`).
- Exact copy: section heading **"Seen in stores"**; intro **"Shelf prices people recorded in stores. Not live: prices may have changed since."**; row line **"seen at {store} on {Mon D, YYYY}"**; link label **"Price on Open Prices"**; notice **"Contains information from <a href="https://prices.openfoodfacts.org">Open Prices</a>, which is made available here under the <a href="https://opendatacommons.org/licenses/odbl/1-0/">Open Database License (ODbL)</a>."**
- `IngestionRun` rows for this sync use `connector="openprices"`, `category_slug="all"`.
- Each task ends with `uv run pytest -q` green and pre-commit passing. Commit subjects end with `(#61)`.

## Review Focus

1. **Observed prices leak into live surfaces.** A 60-day-old Bay Area shelf price must never appear in the live ranking, free-text search, the click endpoint or (later) list resolution. Owned by Task 1 (`test_fresh_offers_excludes_observed`) and Task 4 (`test_search_excludes_observed`, `test_live_ranking_excludes_observed`).
2. **A failed or partial sync deletes data.** A 503 on page 40 must leave every stored observation and observed offer in place. Owned by Task 3 (`test_failed_fetch_deletes_nothing`).
3. **Float prices round wrong.** The API sends `4.99` as a JSON float, and some prices have 3 decimals (`1.250`, `2.999`). Prices must be exact `Decimal`s; a price not exact at 2 places is skipped (reason `precision`), never rounded. Owned by Task 2 (`test_price_parsed_exactly`, `test_three_decimal_price_skipped`).
4. **Same product, same store, several dates.** Only the latest observation (ties: higher `price_id`) becomes the offer, and the offer moves to newer prices on later syncs. Owned by Task 3 (`test_latest_observation_per_store_and_upc_wins`).
5. **Odd rows upstream.** Non-US locations priced in USD (Ecuador, Cambodia), all-null OSM locations, dates in the future, and duplicates must be skipped with a counted reason, never stored. Owned by Task 2 (`test_skip_reasons`).

---

## File Structure

```
connectors/open_prices.py            # Django-free client, Observation/Location, parse/skip/to_raw_offer (new)
catalog/models.py                    # Offer.kind/observed_on/is_sale; Store; OpenPriceObservation
catalog/migrations/0005_open_prices.py
catalog/freshness.py                 # fresh_offers live-only + currency; fresh_observed (new fn)
catalog/retailers.py                 # display_name resolves osm- codes via Store; available_retailers adds stores
catalog/ingest.py                    # _upsert: records_price_history + extra_defaults keywords
catalog/observed.py                  # sync_open_prices, due (new)
catalog/scheduler.py                 # tick runs the Open Prices sync when due
catalog/browse.py                    # browse(..., observed=False)
catalog/management/commands/open_prices_sync.py     # (new)
catalog/management/commands/export_open_prices.py   # (new)
config/settings.py, .env.example
web/views.py                         # category: observed section; disclosure unchanged view
web/templates/web/_results.html, _observed_row.html (new), disclosure.html
README.md, CLAUDE.md, CONTRIBUTING.md, spec §5.3/§11/§16
tests/connectors/test_open_prices.py, tests/connectors/recordings/open_prices/*.json
tests/catalog/test_observed.py, test_freshness.py, test_export_open_prices.py
tests/web/test_observed_view.py
```

---

### Task 1: Models, settings, split freshness (#61)

**Files:**
- Modify: `catalog/models.py`, `config/settings.py`, `.env.example`, `catalog/freshness.py`, `catalog/retailers.py`, `catalog/admin.py` (register `Store`, `OpenPriceObservation` read-only list)
- Create: `catalog/migrations/0005_open_prices.py`
- Test: `tests/catalog/test_freshness.py`, `tests/catalog/test_models.py`

**Interfaces:**
- Produces models:
  - `Offer.kind = CharField(max_length=10, choices=[("live","live"),("observed","observed")], default="live", db_index=True)`, `Offer.observed_on = DateField(null=True, blank=True)`, `Offer.is_sale = BooleanField(default=False)`.
  - `Store(TimestampedModel)`: `code` (CharField 50, unique), `name` (200), `city` (200, null), `postcode` (20, null), `country_code` (2), `osm_type` (10), `osm_id` (BigIntegerField). `display` property → `"{name}, {city}"`, or `name` when there is no city.
  - `OpenPriceObservation(models.Model)`: `price_id` (BigIntegerField, primary key), `store` (FK Store, CASCADE, related_name `observations`), `upc` (14), `product_name` (500), `brands` (200, null), `quantity` (200, null), `labels` (JSON list), `price` (Decimal 10,2), `currency` (3), `is_sale` (bool), `observed_on` (DateField, indexed), `proof_type` (20, null), `source` (200, null), `fetched_at` (DateTimeField).
- Produces settings: `SITE_CURRENCY` ← `UNITPRICER_CURRENCY` (`"USD"`); `OBSERVED_MAX_AGE` ← `UNITPRICER_OBSERVED_MAX_AGE_DAYS` (`timedelta(days=90)`); `OPEN_PRICES_ENABLED` ← `UNITPRICER_OPEN_PRICES == "1"` (False); `OPEN_PRICES_COUNTRY` ← `UNITPRICER_OPEN_PRICES_COUNTRY` (`"US"`); `OPEN_PRICES_INTERVAL` ← `UNITPRICER_OPEN_PRICES_HOURS` (`timedelta(hours=24)`); `OPEN_PRICES_INCLUDE_ASSISTED` ← `UNITPRICER_OPEN_PRICES_ASSISTED == "1"` (False).
- Produces `fresh_offers(now) -> QuerySet[Offer]` (unchanged signature): now also `kind="live"` and `currency=SITE_CURRENCY`. Produces `fresh_observed(now) -> QuerySet[Offer]`: `kind="observed"`, `observed_on >= (now - OBSERVED_MAX_AGE).date()`, `currency=SITE_CURRENCY`.
- Produces `retailers.display_name(code)`: for `osm-` codes, returns `Store.display` (or the code if no such store). `available_retailers(now)` returns live retailers followed by observed store codes, each group sorted by display name.

- [ ] **Step 1: Write failing tests** in `tests/catalog/test_freshness.py` (extend `make_offer` with `kind`, `observed_on` and `currency` keywords):
  - `test_fresh_offers_excludes_observed`: a live and an observed offer, both just seen; `fresh_offers(NOW)` returns only the live one.
  - `test_fresh_observed_by_date_not_sync_time`: with `OBSERVED_MAX_AGE=90 days`, observed offers with `observed_on` 90 days before NOW and 91 days before NOW, both `last_seen_at` 30 days ago: only the 90-day one is returned.
  - `test_currency_filter`: under `override_settings(SITE_CURRENCY="USD")`, a live EUR offer and an observed EUR offer are excluded.
  - `test_available_retailers_includes_observed_stores` (with a `Store(code="osm-n1", name="Safeway", city="Mountain View")`): result is `["walmart", "osm-n1"]`, and `display_name("osm-n1") == "Safeway, Mountain View"`.
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_freshness.py -v`. Expected: FAIL (no `kind` field).
- [ ] **Step 3: Implement** the models, migration (`uv run python manage.py makemigrations catalog -n open_prices`), settings, freshness and retailers changes. Add the six env names to `.env.example` (blank, under "Open Prices", with one comment line per default).
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS. Existing freshness and browse tests still pass because `kind` defaults to `live` and their offers are USD.
- [ ] **Step 5: Commit** `feat: observed offers, stores and split freshness (#61)`

---

### Task 2: Open Prices client (#61)

**Files:**
- Create: `connectors/open_prices.py`, `tests/connectors/test_open_prices.py`, `tests/connectors/recordings/open_prices/` (two pages of a window, a 503 page, built from trimmed real items with `owner` removed)

**Interfaces:**
- Constants: `PRICES_URL = "https://prices.openfoodfacts.org/api/v1/prices"`, `PRICE_PAGE = "https://prices.openfoodfacts.org/prices/{id}"`, `USER_AGENT` (Global Constraints), `PAGE_SIZE = 100`, `MAX_PAGES = 500`, `MIN_INTERVAL = 1.0`.
- `@dataclass(frozen=True) Location`: `osm_type: str`, `osm_id: int`, `name: str | None`, `city: str | None`, `postcode: str | None`, `country_code: str | None`, `type: str`; property `code -> str` = `f"osm-{osm_type[0].lower()}{osm_id}"`.
- `@dataclass(frozen=True) Observation`: `price_id: int`, `type: str`, `product_code: str | None`, `product_name: str | None`, `brands: str | None`, `quantity: str | None` (D5 fallback already applied), `labels: frozenset[str]`, `price: Decimal`, `currency: str`, `is_sale: bool`, `date: date`, `location: Location | None`, `proof_type: str | None`, `source: str | None`, `duplicate_of: int | None`.
- `parse_item(item: dict) -> Observation | None`: None when `id`, `price` or `date` is missing or malformed.
- `skip_reason(obs: Observation, *, country: str, currency: str, include_assisted: bool, today: date) -> str | None`. Reasons are checked in this order: `type` (not PRODUCT), `duplicate`, `currency`, `no_location` (None, or no name), `online`, `country`, `assisted` (`"/experiments/" in source` and not included), `no_code` (`normalize_upc` is None), `no_name`, `future_date` (`date > today`), `price` (≤ 0), `precision` (`price != price.quantize(Decimal("0.01"))`).
- `to_raw_offer(obs: Observation, fetched_at: datetime) -> RawOffer`: `retailer=location.code`, `retailer_item_id=normalize_upc(code)`, `upc` the same, `title=product_name`, `brand=brands`, `size_text=quantity`, `price`, `currency`, `url=PRICE_PAGE.format(id=price_id)`.
- `class OpenPricesClient` (plain class): `__init__(self, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic)`, `fetch_window(self, since: date, currency: str) -> list[Observation]`, `close(self) -> None`. Query params: `currency`, `date__gte=since.isoformat()`, `type=PRODUCT`, `location__type=OSM`, `duplicate_of__isnull=true`, `order_by=-id`, `size=100`, `page=n`. It stops when `page >= pages`, de-duplicates by `price_id`, decodes JSON with `json.loads(response.content, parse_float=Decimal)`, and raises `ConnectorError("Open Prices request failed: HTTP <status> on page <n>")` after 3 attempts, or `ConnectorError("Open Prices window exceeds 500 pages")`. Unparseable items are dropped and counted in `self.unparsed`.

- [ ] **Step 1: Write failing tests:**
  - `test_fetch_window_pages_and_params`: a recorded transport (strict) with page 1 (`pages: 2`) and page 2 returns all items once, a duplicated id across pages appears once, every request carries the User-Agent, and `sleep` was called with ≥ 1.0 between requests (fake clock).
  - `test_price_parsed_exactly`: item `"price": 4.99` gives `Decimal("4.99")`, not `Decimal(4.99)`.
  - `test_three_decimal_price_skipped`: `1.255` gives `skip_reason == "precision"`; `1.250` is kept as `Decimal("1.25")`.
  - `test_skip_reasons` (parametrized): `type=CATEGORY`→`type`; `duplicate_of=5`→`duplicate`; country `EC`→`country`; all-null OSM location→`no_location`; source `Open Prices Web App - /experiments/proof-price-tag-assistant`→`assisted` (and `None` with `include_assisted=True`); code `"abc"`→`no_code`; date tomorrow→`future_date`; a good row→`None`.
  - `test_quantity_fallback`: `quantity` null, `product_quantity=1500`, `product_quantity_unit="ml"` gives `"1500 ml"`; both null gives `None`.
  - `test_to_raw_offer`: retailer `osm-n1392117416`, item id and UPC `"0811620020169"`, url `https://prices.openfoodfacts.org/prices/123`.
  - `test_server_error_retries_then_raises`: a 503 recording three times gives `ConnectorError`, with no `?` in the message, and `sleep` called with 2 then 4.
- [ ] **Step 2: Run** `uv run pytest tests/connectors/test_open_prices.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `connectors/open_prices.py` (no Django import), following `connectors/walmart.py` for the client and error style.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Open Prices client with recorded responses (#61)`

---

### Task 3: Daily sync job, command and scheduler hook (#61)

**Files:**
- Create: `catalog/observed.py`, `catalog/management/commands/open_prices_sync.py`, `tests/catalog/test_observed.py`
- Modify: `catalog/ingest.py` (`_upsert` keywords), `catalog/scheduler.py`, `tests/catalog/test_scheduler.py`

**Interfaces:**
- Consumes: Task 1 models and settings; Task 2 `OpenPricesClient`, `skip_reason`, `to_raw_offer`.
- `_upsert(raw, normalized, model_cache, now, touched, *, records_price_history: bool, extra_defaults: Mapping[str, object] | None = None) -> NormalizedOffer`: the `connector` parameter is replaced by `records_price_history`, `ingest()` passes `connector.records_price_history`, and `extra_defaults` is merged into the `update_or_create` defaults. Live offers pass `{"kind": "live"}`.
- `observed.CONNECTOR = "openprices"`; `observed.due(now: datetime) -> bool`: no SUCCEEDED `IngestionRun(connector="openprices")` started within `OPEN_PRICES_INTERVAL`.
- `observed.sync_open_prices(client: OpenPricesClient, now: datetime, *, off_lookup=enrichment.off.lookup) -> IngestionRun`. In order:
  1. Create a RUNNING run.
  2. `fetch_window((now - OBSERVED_MAX_AGE).date(), SITE_CURRENCY)`. A `ConnectorError` or any other exception marks the run FAILED (message, or type name) and returns with **no deletes**.
  3. Apply `skip_reason`. Skipped rows plus `client.unparsed` go into `run.skipped`.
  4. Upsert `Store` by code and `OpenPriceObservation` by `price_id`, then delete every `OpenPriceObservation` whose `price_id` was not kept, and stores left with no observations.
  5. Pick the latest observation per `(store, upc)` (max `(date, price_id)`). For each, `normalize(to_raw_offer(...), categories, store_brands, off=off_lookup(upc) or OffProduct(labels=obs.labels, quantity=obs.quantity))`. Skip when the category is None. Otherwise `_upsert(..., records_price_history=False, extra_defaults={"kind": "observed", "observed_on": obs.date, "is_sale": obs.is_sale})`.
  6. Delete `Offer(kind="observed")` rows not written in this run, adding their product ids to `touched`.
  7. `merge_product` for every touched product. Counts: `fetched` = rows kept, `matched` = rows written, plus `fully_parsed` and `unresolved` as in `ingest()`.
- `open_prices_sync` command: `--recordings DIR` (uses `recorded_transport(DIR)`), prints `status=… fetched=… matched=… skipped=…`.
- `scheduler.tick(now, connectors=None)`: after the connector pairs, when `OPEN_PRICES_ENABLED and observed.due(now)`, builds an `OpenPricesClient()`, runs the sync (catching exceptions like the pairs do), logs it with `log_run`, appends it to the result, and closes the client.

- [ ] **Step 1: Write failing tests** (`tests/catalog/test_observed.py`, a fake client returning fixed `Observation`s built with a helper, categories loaded via `sync_categories` like `test_ingest.py`):
  - `test_sync_creates_store_observation_and_observed_offer`: one Safeway whole-milk observation (`quantity "1 gal"`, `$4.99`, dated NOW−5 days) gives `Store` `osm-n1`, one `OpenPriceObservation`, and `Offer(kind="observed", retailer="osm-n1", observed_on=…, url=…/prices/1)` with `unit_prices` set and the product category `dairy/milk`.
  - `test_latest_observation_per_store_and_upc_wins`: the same UPC and store on two dates ($4.49 older, $4.99 newer) gives one offer at $4.99. A re-sync where the newer row has gone moves the offer to $4.49.
  - `test_removed_upstream_is_deleted_on_success`: sync A then sync B without the row: the observation and offer are deleted and the product is re-merged.
  - `test_failed_fetch_deletes_nothing`: after a good sync, a client raising `ConnectorError` gives a FAILED run, and the observation and offer counts are unchanged.
  - `test_unmatched_title_stored_but_no_offer`: "Fairlife protein drink" is stored as an observation and creates no offer.
  - `test_walmart_and_observed_share_product`: an existing Walmart offer for the same UPC is untouched (`kind="live"`), and both offers point at one `Product`.
  - `test_due_respects_interval` and, in `test_scheduler.py`, `test_tick_runs_open_prices_when_enabled` (monkeypatch `OpenPricesClient` to the fake; with `OPEN_PRICES_ENABLED=False` it is not called).
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_observed.py tests/catalog/test_scheduler.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the `_upsert` change (update the `ingest()` call), `catalog/observed.py`, the command and the scheduler hook.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS, including the unchanged `test_ingest.py`.
- [ ] **Step 5: Commit** `feat: daily Open Prices sync into observed offers (#61)`

---

### Task 4: "Seen in stores" on category pages (#61)

**Files:**
- Modify: `catalog/browse.py`, `web/views.py` (`category`), `web/templates/web/_results.html`
- Create: `web/templates/web/_observed_row.html`, `tests/web/test_observed_view.py`

**Interfaces:**
- `browse(category, spec, comparison, stores, now, limit=50, *, observed: bool = False) -> BrowseResult`: the base queryset is `fresh_observed(now)` when `observed`, else `fresh_offers(now)`. Everything else (filters, store filter, grouping per product, ranking) is unchanged.
- The `category` view calls `browse` twice and adds `observed_ranked`, `observed_unranked`, `observed_total` to the context. Each presented row gains `store` (`display_name(offer.retailer)`), `observed_on` and `is_sale`.
- `_observed_row.html`: title, brand, size, price (plus a `<span class="sale">sale price</span>` when `is_sale`), unit price, `seen at {{ row.store }} on <time datetime="YYYY-MM-DD">{{ observed_on|date:"M j, Y" }}</time>`, and the plain link "Price on Open Prices" (no ping, no `data-offer`).
- `_results.html`: after the live sections, when `observed_total > 0`, a `<section class="observed">` with an `<h2>Seen in stores</h2>`, the intro copy, a ranked `<ol>` and an unranked "Can't rank" `<ul>`, and the ODbL notice `<p class="note attribution">`, all exact copy from Global Constraints. The empty-state message for live results is unchanged.

- [ ] **Step 1: Write failing tests** (`tests/web/test_observed_view.py`, using the `tests/web/conftest.py` fixtures plus an observed offer at store `osm-n1` "Safeway, Mountain View"):
  - `test_observed_section_renders`: GET `/c/dairy/milk/` contains "Seen in stores", "seen at Safeway, Mountain View on Nov 3, 2026", a link to `https://prices.openfoodfacts.org/prices/1`, and the ODbL notice with both hrefs.
  - `test_observed_link_has_no_ping_or_affiliate_mark`: the observed `<a>` has no `ping` attribute, and the section has no "(affiliate link)".
  - `test_live_ranking_excludes_observed`: a cheaper observed offer does not appear inside `ol.results` of the live section.
  - `test_search_excludes_observed`: a free-text search matching only the observed product's title shows no result.
  - `test_store_filter_applies_to_observed`: `?store=walmart` hides the observed section; `?store=osm-n1` shows it and empties the live list.
  - `test_no_section_without_observed`: with no observed offers, "Seen in stores" and the notice are absent.
  - `test_sale_label`: `is_sale=True` renders "sale price".
- [ ] **Step 2: Run** `uv run pytest tests/web/test_observed_view.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the browse keyword, view context, templates and minimal CSS (`.observed`, `.sale`) in the existing stylesheet.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS, including `tests/web/test_accessibility.py`.
- [ ] **Step 5: Commit** `feat: show Open Prices observations as "Seen in stores" (#61)`

---

### Task 5: License compliance, export and docs (#61)

**Files:**
- Create: `catalog/management/commands/export_open_prices.py`, `tests/catalog/test_export_open_prices.py`
- Modify: `web/templates/web/disclosure.html`, `README.md` ("Data and attribution", architecture sketch), `CLAUDE.md` (commands), `CONTRIBUTING.md` (command table), spec §5.3 (observed offers, freshness), §11 (Open Prices row), §16 (decision log row), `tests/web/test_views_*` for the disclosure test

**Interfaces:**
- `export_open_prices --out PATH` writes gzip JSONL. There is one line per `OpenPriceObservation`: `price_id`, `upc`, `product_name`, `brands`, `quantity`, `labels`, `price` (string), `currency`, `is_sale`, `observed_on`, `proof_type`, `store` {`code`, `name`, `city`, `postcode`, `country_code`, `osm_type`, `osm_id`}, and, when this observation backs an observed offer, `normalized` {`category`, `attributes`, `measures`} taken from that `Offer`'s own fields. The first line is a header object: `{"license": "ODbL-1.0", "source": "https://prices.openfoodfacts.org", "generated_at": …, "method": "https://github.com/DanteArcese/unitpricer"}`. Nothing is read from live offers, `Product`, or `unit_prices`.
- Disclosure page "Data sources" gains: the Open Prices ODbL notice (same copy); "The Open Prices data we show is a derivative database under the ODbL. The method we use to build it is our public source code (linked), and a copy of the data is available free on request at hello@unitpricer.com."; and a sentence that observed prices are not live and are shown for up to 90 days after they were seen.

- [ ] **Step 1: Write failing tests:**
  - `test_export_lines`: after a sync fixture (one matched, one unmatched observation), the file has 3 lines, the header has `license == "ODbL-1.0"`, the matched line has `normalized.category == "dairy/milk"`, the unmatched line has no `normalized`, and the price is the string `"4.99"`.
  - `test_export_never_includes_live_offers`: with a Walmart live offer on the same UPC, no line contains `"walmart"` and no line has `unit_prices`.
  - `test_disclosure_has_open_prices_notice`: `/disclosure` contains the ODbL notice hrefs and "available free on request".
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_export_open_prices.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the command and the template copy. Update README, CLAUDE.md (one line: `uv run --env-file .env python manage.py open_prices_sync [--recordings tests/connectors/recordings/open_prices]`), CONTRIBUTING and spec §5.3/§11/§16 to match D1–D12.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Open Prices ODbL export and attribution (#61)`

---

## After the tasks

- Production: set `UNITPRICER_OPEN_PRICES=1` in `/srv/unitpricer/app.env` and redeploy. Confirm that the first `ingest connector=openprices category=all status=succeeded` log line shows `fetched` of about 5,000 (11,000 if D7 is flipped), and that `/c/dairy/milk/` on the beta host shows the section.
- Follow-ups (new issues, not this plan): a public ODbL dump URL at public launch; produce categories using `CATEGORY` prices and `price_per`; grouping stores by chain or nearby location if the store list grows unwieldy; the shared legal check on merged `Product.attributes`.
