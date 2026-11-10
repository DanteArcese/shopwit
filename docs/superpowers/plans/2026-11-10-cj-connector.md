# CJ Connector Implementation Plan (issue #111)

> Executed subagent-driven: one implementer per task, a task review after each, a whole-branch review at the end.

**Goal:** show online offers, priced per unit and linked through CJ Affiliate, from the CJ advertisers we have joined.

**Design (approved in chat 2026-11-10, "Go for it"):**
- Only offers from **joined** advertisers.
- Only **new** items.
- Shipping is never folded into the unit price. v1 doesn't display shipping; Walmart works the same way today.
- The connector ships enabled whenever its env vars are set. It returns nothing until an advertiser approves us, because it asks only for joined advertisers.

**Architecture:** `connectors/cj.py`, a `CjConnector` registered in `connectors/registry.py`, provides three entry points:
- `search` for scheduled category refreshes;
- `live_search` for open-ended search;
- `lookup_gtin`, used by a new barcode backfill for products we already show.

Offers use retailer codes `cj-<advertiser slug>`. Outbound links for those codes use CJ's deep-link format with `CJ_WEBSITE_ID`. Links are built at render time and never stored, the same as every other route.

**Tech:** Django 5.2, Postgres, httpx, Python 3.13 via uv.

## Global constraints
- Never read `.env`.
- Never print, log or put a credential, the CID or the website ID in an exception message or repr.
- The test fixtures in `tests/connectors/recordings/cj/` are real CJ responses captured on 2026-11-10 with IDs scrubbed. Use them, and don't write fixtures by hand when a real one exists.
- No AI anything. Never guess a value: a missing or invalid field means the offer is skipped.
- Tests never touch the network. `tests/` has no `__init__.py`, so test file basenames must be unique.
- Commit subjects end with `(#111)` and carry the session trailer.

## CJ API facts (verified live 2026-11-10)
- **Request:** `POST https://ads.api.cj.com/query`, header `Authorization: Bearer <CJ_PERSONAL_ACCESS_TOKEN>`, JSON body `{"query": ..., "variables": {...}}`. Use GraphQL variables; never interpolate input into the query string.
- **Query:** `shoppingProducts(companyId: ID!, partnerStatus: JOINED|NOT_JOINED, keywords: [String!], gtin: String, advertiserCountries: [String!], limit: Int, offset: Int)`. It returns `{ totalCount count resultList { ... } }`.
- **Fields used:**
  - identity: `id advertiserId advertiserName advertiserCountry targetCountry joinedStatus`
  - product: `title brand gtin link`
  - pricing: `price{amount currency} salePrice{amount currency} salePriceEffectiveDateStart salePriceEffectiveDateEnd`
  - state and size: `availability` ("in stock"), `condition` ("New"), `unitPricingMeasure`, `size` ("1LB")
  - category: `googleProductCategory{name}`, `productType` (list)
- **Errors:** a validation error returns HTTP 400 with text. A GraphQL error can return 200 with an `errors` array.
- **Rate limit:** CJ's GraphQL limit is not documented. Budget it conservatively: key `cj`, capacity 2, 0.5 req/s, 3,000 a day.
- **Links:** `linkCode(pid)` exists on products, but links are never stored. Use the deep link `https://www.anrdoezrs.net/links/<CJ_WEBSITE_ID>/type/dlg/<url-encoded product url>`. Mark it, like the Walmart Impact constants, "verify against a real joined link before relying on it".

### Task 1: `connectors/cj.py` + registry + budget
- `CjConnector(token, company_id, client=None)`:
  - Class attributes: `name="cj"`, `budget_keys=("cj",)`, `max_cache_age=24h`, `refresh_after=12h`, `records_price_history=False`.
  - Methods: `close()`; a repr that hides everything; `search(query, limit=25) -> SearchResult` (keywords=[query], JOINED, US); `live_search(phrase, limit, budget) -> LiveSearchResult` (one budgeted request, the same pattern as Walmart); `lookup_gtin(gtin, budget=None) -> SearchResult`.
  - `lookup_gtin` tries the variants in this order: as given, `lstrip("0")`, zero-padded to 12, 13 and 14 digits. It stops at the first variant that returns results, and each try costs one budget token when a budget is given.
- Map each product to a `RawOffer`. Skip it (counted in `skipped`) unless all of these hold:
  - `joinedStatus` is true;
  - condition is "new" (case-insensitive) or empty;
  - availability is "in stock" or "in_stock" (case-insensitive);
  - the advertiser country and target country are US or absent;
  - the title is non-empty and `link` is an http(s) URL;
  - the price currency is USD.
- Price rule: use `salePrice` when it is USD, greater than 0, less than `price`, and the effective dates are absent or include now (UTC). Otherwise use `price`. Skip if the price is missing, not finite or not positive.
- Mapped fields:
  - `retailer`: `"cj-" + slug(advertiserName)`. The slug is lowercase, each run of non-alphanumerics becomes "-", leading and trailing "-" are stripped, and the whole code is at most 50 characters.
  - `retailer_item_id`: `str(id)`.
  - `url`: `link`.
  - `upc`: the gtin when it is all digits, otherwise None.
  - `brand`: `brand`.
  - `size_text`: `unitPricingMeasure`, else `size`.
  - `fields`: `{"category_path": googleProductCategory.name or " > ".join(productType)}` when non-empty, and `{"retailer_name": advertiserName}`.
- Errors: non-200 raises `ConnectorError("CJ search failed with HTTP <status>")`. A 200 with `errors` raises `ConnectorError("CJ search returned GraphQL errors")`, with no message text, because it could echo the CID. Unreadable JSON and an unexpected shape also raise `ConnectorError`. Transport errors raise `ConnectorError("CJ request failed (<ExceptionType>)")` from None.
- Registry: `SPECS["cj"] = Spec("connectors.cj:CjConnector", {"token": "CJ_PERSONAL_ACCESS_TOKEN", "company_id": "CJ_PUBLISHER_ID"})`.
- Settings: `SOURCE_BUDGETS["cj"] = _budget("cj", 2, 0.5, 3000)`, with a comment.
- Check how `RawOffer.fields` keys are consumed (`catalog/ingest.py` `_upsert` and normalize). If `retailer_name` would break or be dropped, keep it out of `fields` and say so in the report. Task 2 then uses the slug fallback.
- Tests (`tests/connectors/test_cj.py`):
  - Build a `MockTransport` that serves the fixture `body` objects.
  - Assert that variables are used and the token goes only in the Authorization header.
  - Joined filter: the not-joined fixtures map to zero offers with the real connector. Add one test that flips `joinedStatus` to true on a copy of a fixture product to check the mapping fields.
  - Sale-price rules, including dates; each skip rule; gtin variant order and stopping.
  - Error handling with no secret text (fake token "tok-SECRET", CID "9999999" never appear in messages or repr).
  - `live_search` budget acquire and report; registry build from env.

### Task 2: links, names, disclosure
- `catalog/links.py`: for an offer whose retailer starts with `cj-` and `affiliate["cj_website_id"]` is set, route to the deep link above, checked before the Sovrn fallback.
  - Add `"cj_website_id": env("CJ_WEBSITE_ID")` to `AFFILIATE` and `CJ_WEBSITE_ID=` to `.env.example`, with a comment: the CJ website (property) PID, not the CID.
  - `_PROGRAMS` gains `("cj_website_id", "CJ Affiliate")`.
- `catalog/retailers.py` `display_name`: for `cj-` codes, look up a `CJ_RETAILERS` dict with these entries: cj-walgreens "Walgreens", cj-iherb "iHerb", cj-thrive-market "Thrive Market", cj-bj-s-wholesale-club "BJ's Wholesale Club", cj-vitacost-com "Vitacost". Otherwise humanize the slug: drop "cj-", turn "-" into spaces, title-case.
- Tests: the link route; precedence over Sovrn; no route without the website ID (then Sovrn or plain applies); display names; the disclosure or privacy page lists "CJ Affiliate" when configured (find where `affiliate_programs` is rendered).

### Task 3: barcode lookups for products we already show
- New model `CjGtinCheck(upc CharField pk max 14, checked_at DateTime indexed, found int)` with a migration.
- `catalog/cj_gtin.py` `backfill(connector, budget, now, limit, stopping=None) -> Stats`:
  - Candidates: distinct UPCs of fresh observed offers (`kind` observed), curated categories first, then newest `observed_on`. Exclude UPCs checked in the last 7 days.
  - For each candidate, call `lookup_gtin` (budgeted), write the result via `ingest_live(LiveSearchResult(offers=...), "cj", now)`, and upsert `CjGtinCheck`.
  - A `ConnectorError` stops the run, and the failed UPC is not marked checked. `BudgetExhausted` or `Stopping` ends the run cleanly.
- Management command `cj_gtin_lookup [--limit N]`, default 300.
- Scheduler: run it once a day after the Open Prices sync, next to the OFF backfill (`catalog/scheduler.py`), only when `cj` is in `registry.available()`. Log one line, the same way the OFF backfill does.
- `docs/deploy.md`: a short section on CJ (env vars, the joined-only behaviour, the budget, the daily barcode run).
- Tests: candidate order and the 7-day exclusion; written offers land as `cj-*` offers linked to the existing product by UPC; stats; error and stop behaviour; the scheduler hook runs only when cj is available.

## Review focus
1. No credential, CID or website ID in logs, messages, repr, fixtures or test output.
2. Not-joined products never become offers. Used, refurbished or out-of-stock products never become offers.
3. Sale price is used only when it is valid now. Never mix currencies.
4. A `cj-*` offer whose size is unknown stays unranked, like any other offer. Never guess a size.
5. Barcode lookups never exceed the budget, and never mark a UPC checked when the request failed.
