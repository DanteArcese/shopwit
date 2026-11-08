# Kroger Public API spike (#58)

Date checked: 2026-11-08. Sources read in a browser (developer.kroger.com is a JS app; plain fetch returns an empty shell). No account was created, no credentials were used, no API calls were made. No secrets appear below. Anything marked **unverified** was not confirmed from a Kroger primary page.

## Verdict

**(c) Do not build a data connector under the current public terms. Kroger link-out only, pending written permission.**
The technical fit is good (structured UPC, brand, size, regular and promo price per store, product page URI). The terms are the problem. Kroger's "Acceptable Use of Public APIs" lists as **prohibited** for the Products API: "Comparing products/prices among other retailers", "Manipulating product data in any way", and "Systematically scraping or gathering response data to create a database". The Terms of Service add: no "permanent copies" and no cached copies "longer than permitted by the cache header", and no use of the APIs "to perform competitive, comparative or any other type of analysis to develop marketing or business strategies directly or indirectly targeting Kroger customers". UnitPricer's core feature (rank offers across retailers by unit price, store them in Postgres) conflicts with each of these. Kroger is not suitable as a connector without a separate written agreement.

## 1. Auth

| Item | Finding |
|---|---|
| Grant | OAuth2 Client Credentials for product and location data (no customer login). Authorization Code (+PKCE) is only for Cart and Identity. |
| Token endpoint | `POST https://api.kroger.com/v1/connect/oauth2/token`, header `Authorization: Basic base64(client_id:client_secret)`, form body `grant_type=client_credentials&scope=product.compact` |
| Scope for products | `product.compact` (scope name appears in the docs' sample token and in third-party clients; the Products reference page did not list scopes in the text I could read). Locations needs no separate scope in third-party examples (**unverified**). |
| Token lifetime | `expires_in: 1800` (30 min) in the official service-to-service example. Refresh by requesting a new token. |
| Environments | Production `https://api.kroger.com/v1/`; certification `https://api-ce.kroger.com/v1/` (testing only, "do not rely on it for complete and accurate data"). Credentials are locked to the environment chosen at app registration; register twice for both. |
| Registration | Create a developer account (email verification), then register an application to get client ID and secret. No application review for the public APIs is described in the Quick Start. Whether production access is gated by review: **unverified**. |
| Credential rule | ToS 4.b: "Developer credentials may not be embedded in open source projects." Fine for us only if they stay in `.env` and never in the repo. |

## 2. Products API (v1.3.0 current; 1.2.4 also listed)

`GET /v1/products` and `GET /v1/products/{id}`.

| Parameter | Notes |
|---|---|
| `filter.term` | Fuzzy search on description; result order can change between identical requests |
| `filter.brand` | Brand match |
| `filter.productId` | Exact, 13-digit; multiple IDs per request recommended to save quota |
| `filter.locationId` | 8-character store ID; required for price, fulfillment, aisle and stock |
| `filter.fulfillment` | e.g. `csp` (curbside) |
| `filter.limit`, `filter.start` | Default 10 per page; a "milk" search matched 3,104 products in the docs example |

Response fields (per docs and sample): `productId`, `upc`, `description`, `brand`, `categories[]`, `images[]`, `taxonomies[]`, `temperature`, `aisleLocations[]`, `itemInformation`, `productPageURI`, `countryOrigin`, and `items[]`. Under `items[]` (location-dependent): `price` (regular and promo), `nationalPrice` (regular and promo; absent for some items), fulfillment booleans `instore`, `shiptohome`, `delivery`, `curbside`, and `inventory.stockLevel` (HIGH, LOW, TEMPORARILY_OUT_OF_STOCK, sometimes omitted). The `size` string and `soldBy` field live on `items[]` in the schema (**unverified**: the reference text I could read did not show the field names; confirm against the OpenAPI download at https://developer.kroger.com/api-products/api/product-api-public).

- Price **requires** `filter.locationId`. Without it you get catalog data only. `nationalPrice` also needs a location in the request.
- "instore" means the store has sold the item, not that it is in stock.
- No per-unit price field was seen; unit price would come from our normalizer (size string, UPC enrichment via Open Food Facts).
- Mapping to `RawOffer`: `productId`/`upc` to retailer item ID/UPC, `description` to title, `brand`, promo-else-regular `price`, size string to size fields, `productPageURI` plus chain domain to URL.

## 3. Locations API

- `GET /v1/locations` with `filter.zipCode.near`, `filter.lat.near` and `filter.lon.near`, `filter.latLong.near`, `filter.radiusInMiles` (default 10), `filter.limit` (max 9,999), `filter.chain`, `filter.department` (parameter names from third-party clients; **unverified** on the reference page). Returns `locationId` (8 chars), `chain`, name, address, hours, geolocation, departments.
- `GET /v1/chains` returns all Kroger Co. banners and each chain's `domain`, needed to build product URLs (`productPageURI` is only a path).
- Banners (from the branding page logo set): Kroger, Ralphs, Fred Meyer, QFC, Smith's, Fry's, King Soopers, Dillons, Baker's, City Market, Food 4 Less, Foods Co, Gerbes, Harris Teeter, Jay C, Mariano's, Metro Market, Pay Less, Pick 'n Save, Ruler, Copps, Owen's, Roundy's, Home Chef. Albertsons-owned banners (Safeway, Shaw's, Stop & Shop) are not Kroger and are not covered.
- Terms: tracking, sharing or storing data about a **customer's** location is prohibited, and location data must be shown unaltered.

## 4. Rate limits

- Products: **10,000 calls/day**, counted per endpoint, shared by search and details (docs: "Products API Overview").
- Locations: **1,600 calls/day per endpoint** (locations, chains, departments each).
- Applied equally to all clients; no per-client increases. The window "resets 24 hours after the first call to an endpoint". Partner APIs have no limits (partner program is separate and by agreement).
- ToS 2.d: exceeding limits needs Kroger's express consent; Kroger may decline or add terms or charges.
- Capacity is ample for us: one reference store and 50 categories at 25 results/page is about hundreds of calls per refresh.

## 5. Terms

Sources: https://developer.kroger.com/terms (Terms of Service, last modified 2019-04-09), https://developer.kroger.com/documentation/public/getting-started/acceptable-use, https://developer.kroger.com/documentation/public/getting-started/branding (updated 2021-04-28), https://developer.kroger.com/documentation/api-products/public/products/overview. There is no separate "API license agreement"; `/license` is a BSD-style notice for sample code.

| Topic | What the terms say | Impact on UnitPricer |
|---|---|---|
| Price comparison | Prohibited: "Comparing products/prices among other retailers." | **Blocker.** Our whole product ranks across retailers. |
| Competitive analysis | ToS 4.a: no "competitive, comparative or any other type of analysis to develop marketing or business strategies directly or indirectly targeting Kroger customers" | Ambiguous; a shopper-facing comparison site arguably targets Kroger customers. |
| Caching and retention | ToS 5.e: no scraping, building databases, "permanent copies", or caches "longer than permitted by the cache header". Acceptable Use: no "systematically scraping or gathering response data to create a database", including bots or crawlers. | **Blocker for our catalog/PriceHistory model.** The cache-header value was not measured (no calls made). Periodic bulk ingestion of many categories looks like the prohibited pattern. |
| Data modification | Display product data "exactly as returned"; do not change name, description or price. Omitting irrelevant fields is allowed. | Our normalizer must not rewrite titles or prices shown to users; show Kroger's text verbatim, derive unit price separately. |
| Customer-behavior data | No tracking, sharing or storing data derived from customer searches or frequently viewed products | Search analytics on Kroger queries must not be stored. |
| Commercial use | Not forbidden in the ToS text; no fee schedule. But ToS 2.d lets Kroger add terms or charges for use beyond limits. Partnership statements need written approval; primary logos are only allowed for Add to Cart when **not** monetized. | We monetize (affiliate), so no Kroger logos. The "integration" logo is allowed but must not imply partnership. |
| Attribution | ToS 6.b: show attribution "required by Kroger as described in the documentation"; none specific found. Do not remove Kroger notices or links to its terms. | Link ToS; optional integration logo. |
| Images | Display in original form: no cropping, filters, overlays or text. Content may carry third-party IP (ToS 5.a). No stated storage rule beyond the no-copies clause. | Hotlink only, unmodified. Do not store. |
| Product links | Build URL as `https://{chain domain}{productPageURI}`; the docs example includes a `cid=dis.api.tpi_products-api_...` tracking parameter. | Keep the `cid` parameter intact. Direct links fit our no-redirect rule. |
| Branding | App name must not contain "Kroger", sound like it, or imply endorsement. | "UnitPricer" is fine; "kroger" connector name is internal only. |
| Sublicense | ToS 4.a: no sublicensing; no client that functions substantially like the APIs | Our public site must not re-expose the API (no open JSON API over Kroger data). |
| Open source | Credentials may not be embedded in open source projects. | OK via `.env`. Recorded test fixtures from real responses may also be a "permanent copy" (**unverified**; use synthetic fixtures). |
| Registration | Developer account and registered app; Kroger may monitor use and suspend without notice. | Low barrier, high revocation risk. |

### Affiliate program

- Kroger runs an affiliate program, but the only evidence I found is third-party (**unverified**, no Kroger primary page): FlexOffers lists "Kroger SHIP" (non-perishable pantry and household) at about 3.2%; LinkClicky lists CJ Affiliate at about 2%; one review mentions VigLink (now Sovrn). Kroger job postings (2026-02) mention growing the affiliate program via an outside agency.
- The 2026-11-07 Sovrn spike found Kroger in the Sovrn network and in Price Comparison results (e.g. Folgers 30.5 oz at $7.99), so a Kroger link can likely be monetized through Sovrn without touching the developer API. Link Check `affiliatable` for kroger.com should be retested with UnitPricer's own site key. Perishable grocery likely earns nothing (SHIP-only scope is **unverified**).
- Affiliate and API terms are separate contracts. Being an affiliate does not grant API data rights.

## 6. Recommendation

1. **Do not implement a `kroger` connector against the public API now.** The Acceptable Use page prohibits cross-retailer price comparison and database building, which is the product. Record this in the spec decision log and §11 as: "Kroger: official API exists, public terms prohibit our use; link-only."
2. **Seek written permission before any build.** Email APISupport@kroger.com (address from the API reference) and use the Partnership Request on the branding page. Ask in writing: (a) may UnitPricer display Kroger regular/promo price next to other retailers' prices; (b) may we retain prices for N hours or days, and what is the cache-header value; (c) does the Partner API (no rate limits) apply to a small affiliate comparison site; (d) logo use; (e) interaction with affiliate links. If Kroger agrees, the sections below apply.
3. **If permitted, shape of the connector** (maps to `connectors.base.Connector`: `name="kroger"`, `search(query, limit)`):
   - One reference store, chosen by the owner (a single `KROGER_LOCATION_ID` in `.env`, found once via `/locations?filter.zipCode.near=`). Price is store-specific, so the UI must say "price at <store name, address>" and the spec's "national, not per-store" assumption does not hold for Kroger. A second store would double quota use and storage; defer.
   - Token: client credentials with `product.compact`, cached in memory, refreshed at about 25 min (1,800 s lifetime). Errors raise `ConnectorError` without echoing the Basic header.
   - `max_cache_age` set to the smaller of the cache-header value and 24 h (default assumption 12 h; **unverified**). Treat as no-retention until Kroger states otherwise: `records_price_history = False`, same as Walmart.
   - Refresh on demand or on the 12 h schedule, per category, only for terms in our category list; never crawl the catalog by letter or UPC range (that is the "systematically gathering" pattern). Use `filter.productId` batches to refresh known items instead of re-searching.
   - Store only what a `RawOffer`/`Offer` needs: productId (identity), UPC, title/brand/size verbatim, price, fetched-at, product URL. No images (hotlink the API's image URL unmodified), no aisle or stock data.
   - Link: `https://{chain domain}{productPageURI}` built at render time, with the `cid` parameter preserved; if monetized, wrap with the Sovrn `sovrn.co` link only after confirming Kroger and Sovrn allow it.
   - Show "price as of <time>" and the store. Do not alter descriptions.
   - Fixtures: synthetic only, not recorded production responses.
4. **Fallback that needs no API terms:** keep Kroger as a Sovrn-monetized outbound link on store/brand pages, with no Kroger prices in the catalog.

## What the owner must do

1. Create a developer account at https://developer.kroger.com (email verification) and register an application in the **production** environment (name must not include "Kroger"). Put `KROGER_CLIENT_ID`, `KROGER_CLIENT_SECRET` and `KROGER_LOCATION_ID` in `.env`; add the names (no values) to `.env.example`. Do not commit them.
2. Before registering for real use, send the permission request in item 2 above and wait for the answer. Registration itself does not grant comparison rights.
3. Optionally apply to the Kroger affiliate program via CJ or FlexOffers (verify the network on their page), or retest kroger.com via Sovrn Link Check with the UnitPricer site key.
4. Decide whether a single reference store is acceptable for a nationwide site.

## Open questions
- The cache-header value on `/v1/products` responses (needs one call with credentials).
- Whether `filter.term` reaches the products behind `product.compact` only, or other scopes are required for `items[]` price (the reference page text did not enumerate scopes).
- The exact `items[].size` and `soldBy` field names (download the OpenAPI spec).
- Whether the Partner API, which has no rate limits, has different data terms.
- Real affiliate network, rates and category exclusions for Kroger.

## Sources (all checked 2026-11-08)
- https://developer.kroger.com/documentation/public/getting-started/quick-start
- https://developer.kroger.com/documentation/public/getting-started/apis
- https://developer.kroger.com/documentation/public/getting-started/acceptable-use
- https://developer.kroger.com/documentation/public/getting-started/branding
- https://developer.kroger.com/documentation/public/security/service-to-service
- https://developer.kroger.com/documentation/api-products/public/products/overview
- https://developer.kroger.com/documentation/api-products/public/products/product-search
- https://developer.kroger.com/documentation/api-products/public/locations/overview
- https://developer.kroger.com/api-products/api/product-api-public
- https://developer.kroger.com/terms
- https://developer.kroger.com/license
- Third party, unverified: https://pypi.org/project/kroget/, https://www.flexoffers.com/affiliate-programs/kroger-affiliate-program, https://linkclicky.com/affiliate-program/kroger/
