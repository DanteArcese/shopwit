# M2: Catalog and Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Postgres catalog that one command fills, idempotently, from the Walmart Affiliate API (driven by recorded responses until live access works), enriched from Open Food Facts, refreshed on a schedule, with an admin review queue whose manual corrections survive re-ingestion.

**Architecture:** A Django project (`config/`) with one app, `catalog/`, which holds models, admin, ingestion and management commands. It sits beside two plain packages: `connectors/` (retailer API clients that return `normalize.RawOffer`) and `enrichment/` (Open Food Facts cache). The pure `normalize/` package from M1 is consumed unchanged except for one loader key (Task 2). No user-facing request ever calls a retailer or OFF.

**Tech Stack:** Python 3.13, `uv`, Django 5.2 LTS, Postgres 17, `psycopg[binary]`, `dj-database-url`, `httpx`, `cryptography`, `pytest-django`, `pgserver` (embedded Postgres for local tests).

**Spec:** `docs/superpowers/specs/2026-11-07-shopwit-design.md`: §5.1 Connectors, §5.3 Catalog, §10 Revenue/terms, §11 Data sources, §13 Error handling, §14 Testing. Research: `docs/research/2026-11-07-sovrn-api.md` §6 (program terms), `docs/research/2026-11-07-open-food-facts.md`. Roadmap tasks 2.1–2.8.

## Global Constraints

- Python with `uv` only (`uv add`, `uv run`); no other installers or virtualenv tools.
- No AI/LLM libraries or calls anywhere.
- `normalize/` stays pure: no Django import, no network, no I/O beyond its two YAML loaders.
- Money is `decimal.Decimal` / `DecimalField(max_digits=10, decimal_places=2)`; unit prices are stored as strings of the 6-dp `Decimal` from `normalize` (never `float`).
- Never guess: unresolved values stay absent and produce `ReviewItem`s.
- **Program terms (spec §5.3, §10):** an offer is hidden once `last_seen_at` is older than `min(FRESHNESS_CUTOFF, connector.max_cache_age)`; default `FRESHNESS_CUTOFF = 24 h`, refresh interval `REFRESH_INTERVAL = 12 h`. **Walmart offers are never written to `PriceHistory`.** Amazon images are never stored. Store the retailer's canonical product URL; affiliate URLs are built at render time (M3), never stored.
- Secrets come only from environment variables. Never read, print or log `.env` contents or credential values. Tests never need real credentials.
- **Tests never touch the network.** Connectors are tested with recorded responses through `httpx.MockTransport`; OFF with fixture files.
- Tests run against real Postgres: CI uses a `postgres:17` service; locally, when `DATABASE_URL` is unset, the test plugin starts an embedded `pgserver` instance. Docker is not required to run tests.
- OFF requests send `User-Agent: shopwit/0.1 (+https://github.com/DanteArcese/shopwit)`, at most 1 request/second, with retry/backoff on 5xx.
- Each task ends with `uv run pytest` green and the pre-commit hooks passing.

## Review Focus

1. **Re-running ingestion on identical data changes nothing.** Product, Offer, PriceHistory and ReviewItem counts stay the same, and `PriceHistory` only gains a row when the price changes. Owned by Task 6.
2. **The same item returned by two search queries in one run is processed once.** Owned by Task 6.
3. **A Walmart item missing `salePrice` or `name` is skipped and counted, never crashes the run, and never becomes an offer.** Owned by Task 4.
4. **A manual override of an ambiguous attribute wins, resolves its review item, and re-ingestion doesn't reopen it.** Owned by Task 8.
5. **UPCs normalize consistently across sources:** Walmart's 12-digit "093966010015" and OFF's 13-digit "0093966010015" identify the same product. Owned by Task 5 (`normalize_upc`) and Task 6 (identity).

---

## File Structure

```
pyproject.toml                     # deps; pytest-django + plugin config
manage.py
config/                            # Django project
  __init__.py  settings.py  urls.py  wsgi.py
catalog/                           # the one Django app
  __init__.py  apps.py  models.py  admin.py  forms.py
  categories_sync.py               # YAML → Category/AttributeDef
  identity.py                      # product identity rules
  ingest.py                        # ingest() orchestration
  overrides.py                     # manual attribute overrides
  freshness.py                     # cutoff rules + Offer queryset helpers
  scheduler.py                     # due() / tick() logic
  migrations/
  management/commands/
    sync_categories.py  refresh.py  run_scheduler.py  off_sync.py  off_backfill.py
connectors/                        # plain package, no Django import
  __init__.py  base.py  walmart.py  registry.py  recording.py
enrichment/                        # plain package + one Django model lives in catalog
  __init__.py  off.py              # delta/JSONL parsing, API backfill client, normalize_upc
Dockerfile  compose.yaml           # local stack: db, web, worker
.github/workflows/ci.yml           # add postgres service
tests/
  pgserver_plugin.py               # starts embedded Postgres when DATABASE_URL unset
  catalog/  connectors/  enrichment/
  connectors/recordings/walmart/*.json
  enrichment/fixtures/*.jsonl
```

---

### Task 1: Django project, settings from env, Postgres test harness, local stack (roadmap 2.1)

**Files:**
- Modify: `pyproject.toml` (`uv add django~=5.2 psycopg[binary] dj-database-url`; `uv add --dev pytest-django pgserver`), `.env.example`, `.github/workflows/ci.yml`, `CLAUDE.md` (Commands section)
- Create: `manage.py`, `config/{__init__,settings,urls,wsgi}.py`, `catalog/{__init__,apps}.py`, `tests/pgserver_plugin.py`, `tests/catalog/test_settings.py`, `Dockerfile`, `compose.yaml`

**Interfaces:**
- Produces: settings `DATABASES` from `DATABASE_URL` (via `dj_database_url.parse`); `SECRET_KEY` from `DJANGO_SECRET_KEY` (required unless `DJANGO_DEBUG=1`); `DEBUG` from `DJANGO_DEBUG`; `ALLOWED_HOSTS` from comma-separated `DJANGO_ALLOWED_HOSTS`; `REFRESH_INTERVAL = timedelta(hours=float(env("SHOPWIT_REFRESH_HOURS", 12)))`; `FRESHNESS_CUTOFF = timedelta(hours=float(env("SHOPWIT_FRESHNESS_HOURS", 24)))`; `CATEGORIES_DIR = BASE_DIR / "categories"`; `STORE_BRANDS_FILE = BASE_DIR / "data/store_brands.yaml"`.
- pytest config: `DJANGO_SETTINGS_MODULE = "config.settings"`, `addopts = "-p tests.pgserver_plugin"`. The plugin sets `DATABASE_URL` (and `DJANGO_DEBUG=1`) **before** pytest-django configures Django, starting `pgserver` in `.pytest-pg/` (gitignored) only when `DATABASE_URL` is unset.

- [ ] **Step 1: Write failing tests** in `tests/catalog/test_settings.py`:
  - `test_database_is_postgres`: `connection.vendor == "postgresql"` (`@pytest.mark.django_db`)
  - `test_refresh_and_freshness_defaults`: `settings.REFRESH_INTERVAL == timedelta(hours=12)` and `settings.FRESHNESS_CUTOFF == timedelta(hours=24)`
  - `test_system_checks_pass`: `call_command("check")` raises nothing
- [ ] **Step 2: Run** `uv run pytest tests/catalog -v`. Expected: FAIL (no Django project).
- [ ] **Step 3: Implement** the project, the plugin and the files above. `compose.yaml` has services `db` (`postgres:17`, named volume), `web` (`uv run python manage.py runserver 0.0.0.0:8000`), `worker` (`uv run python manage.py run_scheduler`; the command arrives in Task 7, so the worker service may fail until then) and loads `.env`. Add `DATABASE_URL`, `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `SHOPWIT_REFRESH_HOURS`, `SHOPWIT_FRESHNESS_HOURS` to `.env.example`. CI gets a `postgres:17` service and `DATABASE_URL=postgres://postgres:postgres@localhost:5432/shopwit`. Add `.pytest-pg/` to `.gitignore`. The existing M1 tests must keep running without the database.
- [ ] **Step 4: Run** `uv run pytest -q` with `DATABASE_URL` unset. Expected: all pass, using pgserver. Also run `docker compose config -q` (validates the file without a daemon). Expected: exit 0.
- [ ] **Step 5: Commit** `feat: Django project, env settings, Postgres test harness`

If `pgserver` can't start on this machine, stop and report BLOCKED with the error. Don't switch to SQLite.

---

### Task 2: Models, migrations, category sync (roadmap 2.2)

**Files:**
- Modify: `normalize/categories.py` (optional `search` key), `tests/normalize/test_categories.py`, every `categories/**/*.yaml` (add `search`)
- Create: `catalog/models.py`, `catalog/migrations/0001_initial.py`, `catalog/categories_sync.py`, `catalog/management/commands/sync_categories.py`, `tests/catalog/test_models.py`, `tests/catalog/test_categories_sync.py`

**Interfaces:**
- `CategoryDef` gains `search: tuple[str, ...]`, which defaults to `(name,)` when the key is absent. It holds the retailer search queries used by ingestion.
- Models (all with `created_at`/`updated_at` auto timestamps unless noted):
  - `Category(slug: CharField unique, name, parent_slug: CharField null, dimension, comparison_default, comparison_alternates: JSONField list, search: JSONField list, active: bool default True)`
  - `AttributeDef(category FK, name, type, values: JSONField list)`, unique `(category, name)`
  - `Product(identity: CharField unique, upc: CharField null indexed, category FK null, title, brand null, attributes: JSONField dict {name: {"value": v, "source": s}}, measures: JSONField dict, manual_attributes: JSONField dict {name: value})`, plus a **GIN index on `attributes`** (`GinIndex(fields=["attributes"], opclasses=["jsonb_path_ops"])`)
  - `Offer(product FK, retailer, retailer_item_id, price Decimal(10,2), currency: CharField(3), unit_prices: JSONField dict {method: "0.002350"}, url, image_url: URLField null, last_seen_at: DateTimeField indexed)`, unique `(retailer, retailer_item_id)`
  - `PriceHistory(offer FK, price Decimal(10,2), currency, observed_at)`, no `updated_at`
  - `ReviewItem(product FK, detail: CharField (the `unresolved` string, e.g. "attribute:fat:ambiguous"), status: choices open/resolved, resolved_at null)`, with a partial unique constraint on `(product, detail)` where `status="open"`
  - `IngestionRun(connector, category_slug, started_at, finished_at null, status: choices running/succeeded/failed, fetched, matched, fully_parsed, unresolved, skipped: ints default 0, error: TextField blank)`
- `sync_categories(root: Path) -> SyncResult(created: int, updated: int, deactivated: int)` in `catalog/categories_sync.py`: upserts from `normalize.load_categories(root)`, replaces AttributeDefs to match the files, and sets `active=False` on categories no longer present (never deletes).

- [ ] **Step 1: Write failing tests**
  - `test_categories.py::test_search_defaults_to_name`, `::test_search_loaded`
  - `test_categories_sync.py`: `test_sync_creates_all_repo_categories` (5 created), `test_sync_is_idempotent` (second run created=0, updated=0), `test_removed_category_deactivated_not_deleted`, `test_attribute_defs_replaced`
  - `test_models.py`: `test_offer_unique_per_retailer_item` (IntegrityError), `test_one_open_review_item_per_detail` (IntegrityError on a duplicate open; allowed when the first is resolved)
- [ ] **Step 2: Run** `uv run pytest tests/catalog tests/normalize/test_categories.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The `search` values per category file: milk `["2% milk", "whole milk", "skim milk", "organic milk"]`; eggs `["large eggs", "cage free eggs", "organic eggs"]`; coffee `["ground coffee", "whole bean coffee"]`; toilet paper `["toilet paper"]`; paper towels `["paper towels"]`. Generate the migration with `uv run python manage.py makemigrations catalog`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: catalog models and category sync`

---

### Task 3: Connector interface and recorded-response harness (roadmap 2.3)

**Files:**
- Modify: `pyproject.toml` (`uv add httpx`)
- Create: `connectors/__init__.py`, `connectors/base.py`, `connectors/recording.py`, `connectors/registry.py`, `tests/connectors/test_recording.py`, `tests/connectors/test_registry.py`

**Interfaces:**
- `connectors/base.py`:
  - `class Connector(Protocol)`, with:
    - `name: str`
    - `max_cache_age: timedelta`
    - `records_price_history: bool`
    - `def search(self, query: str, limit: int = 25) -> SearchResult`
  - `@dataclass(frozen=True) SearchResult(offers: list[RawOffer], skipped: int)`
  - `class ConnectorError(Exception)`, raised for HTTP or auth failures with a message that contains **no credentials and no full URLs with query strings**
- `connectors/recording.py`:
  - `recorded_transport(directory: Path) -> httpx.MockTransport`. It serves `<directory>/<METHOD>_<path with / → _>__<sorted query as k=v&…, with secrets dropped>.json` files that contain `{"status": int, "json": …}`. An unmatched request raises an `AssertionError` that names the expected file.
  - `REDACTED_PARAMS = {"publisherId", "apiKey", "key", "secret"}` are excluded from the file name.
- `connectors/registry.py`:
  - `available(environ: Mapping[str, str] = os.environ) -> dict[str, Connector]` returns only the connectors whose required env vars are all set.
  - `get(name: str, environ=os.environ, client: httpx.Client | None = None) -> Connector`.

- [ ] **Step 1: Write failing tests:** `test_recorded_transport_serves_file`, `test_unmatched_request_names_expected_file`, `test_secret_params_not_in_filename`, `test_available_requires_all_env_vars` (a Walmart entry is present only when `WALMART_CONSUMER_ID`, `WALMART_PRIVATE_KEY` and `WALMART_KEY_VERSION` are set; use a fake environ dict).
- [ ] **Step 2: Run** `uv run pytest tests/connectors -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The registry may reference `connectors.walmart.WalmartConnector`, which arrives in Task 4. Until then, register by import path and test `available()` with a stub entry in the test.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: connector interface and recorded-response harness`

---

### Task 4: Walmart Affiliate API connector (roadmap 2.4)

**Files:**
- Modify: `pyproject.toml` (`uv add cryptography`), `connectors/registry.py`
- Create: `connectors/walmart.py`, `tests/connectors/test_walmart.py`, `tests/connectors/recordings/walmart/*.json`

**Interfaces:**
- `WalmartConnector(consumer_id: str, private_key: str, key_version: str, publisher_id: str | None = None, client: httpx.Client | None = None)`. `private_key` is single-line Base64 DER (PKCS#8) or PEM. It has `name = "walmart"`, `max_cache_age = timedelta(hours=24)` and **`records_price_history = False`**.
- `sign(consumer_id: str, timestamp_ms: str, key_version: str, private_key) -> str`: Base64 of RSA PKCS#1 v1.5 SHA-256 over `f"{consumer_id}\n{timestamp_ms}\n{key_version}\n"`.
- Request headers: `WM_CONSUMER.ID`, `WM_CONSUMER.INTIMESTAMP`, `WM_SEC.KEY_VERSION`, `WM_SEC.AUTH_SIGNATURE`.
- Search: `GET https://developer.api.walmart.com/api-proxy/service/affil/product/v2/search` with `query`, `numItems`, and `publisherId` when set.
- Mapping per item to `RawOffer`:
  - `retailer="walmart"`, `retailer_item_id=str(itemId)`, `title=name`, `price=Decimal(str(salePrice))`, `currency="USD"`
  - `url=productUrl` (the canonical Walmart URL; never the tracking URL)
  - `upc=upc`, `brand=brandName`, `size_text=size`, `fetched_at` = response time (UTC)
  - An item missing `name`, `itemId` or a positive `salePrice` is skipped and counted in `SearchResult.skipped`.

**Before writing the recordings,** read Walmart's Affiliate API docs on walmart.io (search, items and response schemas) and record the field names actually documented. Build the recordings from the documented example responses and label each file with `"_source": "walmart.io docs example (synthetic)"`. If a documented field name differs from the names above, follow the docs and note it in the report.

- [ ] **Step 1: Write failing tests:**
  - `test_signature_verifies_with_public_key`: generate a throwaway RSA key in the test, sign, then verify with `cryptography`
  - `test_search_maps_items_to_raw_offers`: a recording with 3 items asserts every `RawOffer` field for one item
  - `test_items_missing_price_or_name_are_skipped` (Review Focus 3): a recording with 4 items, 2 of them invalid, gives `len(offers) == 2` and `skipped == 2`
  - `test_http_error_raises_connector_error_without_secrets`: a 401 recording raises `ConnectorError`, and the consumer ID and signature don't appear in `str(exc)`
  - `test_accepts_pem_and_base64_der_keys`
  - `test_registry_builds_walmart_from_env`
- [ ] **Step 2: Run** `uv run pytest tests/connectors -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Walmart Affiliate API connector`

---

### Task 5: Open Food Facts enrichment cache (roadmap 2.5)

**Files:**
- Modify: `catalog/models.py` (+ migration)
- Create: `enrichment/__init__.py`, `enrichment/off.py`, `catalog/management/commands/off_sync.py`, `catalog/management/commands/off_backfill.py`, `tests/enrichment/test_off.py`, `tests/catalog/test_off_commands.py`, `tests/enrichment/fixtures/delta_sample.jsonl`

**Interfaces:**
- `normalize_upc(code: str | None) -> str | None` in `enrichment/off.py`. It keeps digits only. A length of 12 is left-padded to 13; lengths 8, 13 and 14 are kept; any other length returns `None`. So "093966010015" becomes "0093966010015".
- `parse_off_line(line: str, source: Literal["off", "opf"]) -> OffRecord | None`, where `@dataclass(frozen=True) OffRecord(upc: str, source: str, labels: frozenset[str], quantity: str | None, last_modified: datetime | None)`. It reads `code`, `labels_tags`, `quantity` and `last_modified_t`.
- Model `OffEntry(upc: CharField primary key, source, labels: JSONField list, quantity: CharField null, last_modified: DateTimeField null, fetched_at)`.
- `lookup(upc: str | None) -> OffProduct | None` in `enrichment/off.py`, Django-aware and imported lazily. It reads `OffEntry` for the normalized UPC and returns `OffProduct(labels=frozenset(labels), quantity=quantity)`.
- `off_sync --file PATH [--source off|opf]`: streams a gz or plain JSONL file and upserts only UPCs already present on some `Product.upc`. `off_sync --latest-delta` downloads the newest file listed in `https://static.openfoodfacts.org/data/delta/index.txt`.
- `off_backfill [--limit 50]`: for products with a UPC and no `OffEntry`, calls `GET https://world.openfoodfacts.org/api/v2/product/{upc}?fields=code,labels_tags,quantity,last_modified_t`, and for non-food categories (`household/*`) the same path on `world.openproductsfacts.org`. It allows at most 1 request/second, retries 5xx up to 3 times with backoff, and records a 404 as an `OffEntry` with empty labels, so it isn't re-queried every run.
- The HTTP client is injectable, so tests use `httpx.MockTransport`, and the sleep function is injectable, so tests don't wait.

- [ ] **Step 1: Write failing tests:**
  - `test_normalize_upc` (Review Focus 5): "093966010015" → "0093966010015"; "0093966010015" unchanged; "abc" → None; "12345" → None
  - `test_parse_off_line`, using real field names from the fixture
  - `test_off_sync_only_known_upcs`: the fixture has 5 lines, 2 of them matching products, so 2 `OffEntry` rows result
  - `test_lookup_returns_off_product`
  - `test_backfill_uses_opf_for_household`
  - `test_backfill_retries_5xx_then_records`
  - `test_backfill_records_404_and_skips_next_time`
  - `test_backfill_sends_user_agent`
- [ ] **Step 2: Run** `uv run pytest tests/enrichment tests/catalog/test_off_commands.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The delta and dump files are never committed. The fixture is a handful of real-shaped lines.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Open Food Facts enrichment cache`

---

### Task 6: Ingestion job (roadmap 2.6)

**Files:**
- Create: `catalog/identity.py`, `catalog/ingest.py`, `catalog/overrides.py` (`apply` returns `normalized` unchanged until Task 8), `tests/catalog/test_ingest.py`

**Interfaces:**
- `product_identity(raw: RawOffer) -> str` in `catalog/identity.py`: `f"upc:{normalize_upc(raw.upc)}"` when the UPC normalizes, else `f"{raw.retailer}:{raw.retailer_item_id}"`.
- `ingest(connector: Connector, category: Category, now: datetime, *, off_lookup=enrichment.off.lookup) -> IngestionRun` in `catalog/ingest.py`. It:
  1. creates an `IngestionRun(status="running")`
  2. runs `connector.search(q)` for each `q` in `category.search`, deduplicating offers by `(retailer, retailer_item_id)` across queries (Review Focus 2)
  3. for each offer, calls `normalize.normalize(raw, categories, store_brands, off=off_lookup(raw.upc))`, with categories and store brands loaded once per run from settings paths. If the result has no category, it counts the offer as unmatched and moves on. A product matched to a *different* category than the run's is still upserted under its own category.
  4. upserts the `Product` by identity (title, brand, upc, category, attributes, measures), then **applies manual overrides** via `catalog.overrides.apply(product, normalized)` (an identity function until Task 8)
  5. upserts the `Offer` by `(retailer, retailer_item_id)`: price, currency, unit_prices as strings, url, and `last_seen_at=now`. `image_url` is left empty. Images aren't needed in M2, and Amazon images must never be stored.
  6. appends a `PriceHistory` row **only if** `connector.records_price_history` and (no previous row or the price changed)
  7. syncs `ReviewItem`s: it opens one per current `unresolved` entry and resolves open ones no longer present
  8. fills in the run counters: `fetched` (unique offers), `matched`, `fully_parsed` (matched with empty unresolved), `unresolved` (matched with any unresolved) and `skipped` (summed from `SearchResult.skipped`). It sets `status="succeeded"` and `finished_at`.
  9. on a `ConnectorError`, sets `status="failed"` with the error text and re-raises nothing. Everything runs in one transaction per offer, so a bad offer can't poison the run.

- [ ] **Step 1: Write failing tests.** Use a `FakeConnector` in the test (returning fixed `SearchResult`s per query) with real repo categories synced:
  - `test_ingest_creates_products_offers_and_stats`
  - `test_reingest_is_idempotent` (Review Focus 1)
  - `test_price_change_appends_history_only_when_allowed`: with `records_price_history=True` a change gives 2 rows; with `False` it gives 0 rows
  - `test_duplicate_item_across_queries_processed_once` (Review Focus 2)
  - `test_unresolved_creates_review_items_and_resolution_closes_them`
  - `test_upc_identity_merges_across_retailers`: the same UPC from two fake retailers gives 1 product and 2 offers
  - `test_connector_error_marks_run_failed`
  - `test_off_lookup_feeds_normalizer`: an `off_lookup` returning `en:organic` makes the product organic
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_ingest.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: ingestion job`

---

### Task 7: Refresh command, scheduler and freshness (roadmap 2.7)

**Files:**
- Create: `catalog/freshness.py`, `catalog/scheduler.py`, `catalog/management/commands/refresh.py`, `catalog/management/commands/run_scheduler.py`, `tests/catalog/test_freshness.py`, `tests/catalog/test_scheduler.py`

**Interfaces:**
- `cutoff_for(connector_name: str, now: datetime) -> datetime` in `catalog/freshness.py` returns `now - min(settings.FRESHNESS_CUTOFF, max_cache_age(connector_name))`. Retailers without a registered connector fall back to the setting.
- `fresh_offers(now: datetime) -> QuerySet[Offer]` returns only offers with `last_seen_at >= cutoff_for(offer.retailer, now)`. Implement it with a per-retailer `Q` union.
- `due(now: datetime, connectors: Mapping[str, Connector]) -> list[tuple[str, str]]` in `catalog/scheduler.py` returns `(connector_name, category_slug)` pairs for active categories whose last *succeeded* `IngestionRun` is missing or older than `settings.REFRESH_INTERVAL`.
- `tick(now: datetime, connectors=None) -> list[IngestionRun]` runs `ingest` for every due pair, where `connectors` defaults to `registry.available()`.
- `refresh [--connector NAME] [--category SLUG] [--recordings DIR]`: runs immediately, ignoring the interval. `--recordings` builds the connector with `recorded_transport(DIR)`, which supports the offline demo before walmart.io access works.
- `run_scheduler [--once] [--poll-seconds 300]`: loops `tick(now)` and sleeps between ticks. It logs one line per run with the counters only (no credentials), and exits cleanly on SIGTERM.

- [ ] **Step 1: Write failing tests:**
  - `test_cutoff_uses_minimum_of_setting_and_connector`: with the setting at 48 h and Walmart at 24 h, the cutoff is 24 h
  - `test_fresh_offers_hides_stale_per_retailer`
  - `test_due_after_interval_only`
  - `test_failed_run_does_not_count_as_refreshed`
  - `test_tick_runs_ingest_for_due_pairs` (with a fake connector)
  - `test_refresh_command_with_recordings`: `call_command("refresh", connector="walmart", category="dairy/milk", recordings=<Task 4 recordings dir>)` creates offers
  - `test_run_scheduler_once`
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_freshness.py tests/catalog/test_scheduler.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** If Task 4's recordings don't cover the milk `search` queries, add recordings for them, built from the documented response shape and labeled synthetic.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: refresh command, scheduler and freshness`

---

### Task 8: Admin review queue and manual overrides (roadmap 2.8)

**Files:**
- Create: `catalog/admin.py`, `catalog/forms.py`, `tests/catalog/test_overrides.py`, `tests/catalog/test_admin.py`
- Modify: `catalog/overrides.py` (the real implementation replaces the Task 6 identity function)

**Interfaces:**
- `apply(product: Product, normalized: NormalizedOffer) -> NormalizedOffer` in `catalog/overrides.py`. For each `name, value` in `product.manual_attributes`, it sets `attributes[name] = AttrValue(value, "manual")` and drops every `unresolved` entry that starts with `f"attribute:{name}"`. It then recomputes `unit_prices` with `normalize.compare.unit_prices(raw.price, category_def, measures, attributes)` and drops `unit_price:*` entries that now resolve. The result is a new `NormalizedOffer`.
- `validate_overrides(category: Category, data: Mapping[str, object]) -> dict[str, object]` in `catalog/forms.py`. It checks each key against the category's `AttributeDef`s (name, type, declared values; the built-in `brand_tier` allows `name_brand`/`store_brand`) and raises `ValidationError` naming the bad key.
- Admin:
  - `ReviewItemAdmin`: list display product, category, detail, status, created; filters status, detail prefix and category; search on product title; action "mark resolved".
  - `ProductAdmin`: the `manual_attributes` form uses `validate_overrides`. On save it re-applies overrides to the product and all its offers' `unit_prices` immediately, without waiting for the next ingest.
  - `OfferAdmin`, `IngestionRunAdmin` (read-only) and `CategoryAdmin` (read-only, since the files are the source of truth).

- [ ] **Step 1: Write failing tests:**
  - `test_override_wins_and_resolves_review_item` (Review Focus 4): a product with `attribute:fat:ambiguous`, overridden to `fat="2"`, gives `attributes["fat"] == {"value": "2", "source": "manual"}`, and the review item is resolved
  - `test_reingest_does_not_reopen_overridden_item` (Review Focus 4)
  - `test_override_recomputes_unit_prices` (setting ply on a toilet-paper product adds `area_x_ply`)
  - `test_invalid_override_rejected`
  - `test_admin_pages_load` (an admin client GETs each changelist and the review queue with status 200)
  - `test_admin_save_reapplies_overrides`
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_overrides.py tests/catalog/test_admin.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: admin review queue and manual overrides`

---

## M2 exit criteria

- `uv run python manage.py sync_categories && uv run python manage.py refresh --connector walmart --recordings tests/connectors/recordings/walmart` fills a local catalog for the categories the recordings cover. Re-running it changes no counts.
- The review queue in the admin shows what didn't resolve, and overrides survive re-ingestion.
- `uv run pytest` passes locally without Docker and in CI against Postgres. Every Review Focus item has a named test.
- No test touches the network. No credential value appears in code, logs, recordings or test output.
