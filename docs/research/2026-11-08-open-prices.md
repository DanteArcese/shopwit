# Open Prices evaluation (#61)

Date: 2026-11-08. Probes against `https://prices.openfoodfacts.org/api/v1` with
`User-Agent: unitpricer-research/0.1 (+https://github.com/DanteArcese/unitpricer)`, at most 1 request
per second (about 140 requests, including a full pull of the 90-day USD window: 111 pages of 100).

## Recommendation

Add Open Prices as a **separate, once-a-day observed-price sync**, not as a retailer connector.
Each day, re-pull the whole USD window for the last 90 days (about 110 requests at 1 req/s,
about 2 minutes). Keep the rows in their own tables, and turn the latest observation per
(store, barcode) into an `Offer` of kind `observed`. Show these offers in their own "Seen in
stores" section, labelled "seen at <store> on <date>", never mixed into the live ranking. Ingest
US/USD only. The data is ODbL: show the attribution notice next to the section and on the
disclosure page, and offer the derived data (see §6).

US coverage today is small and concentrated. In the last 90 days there were 11,026 US prices at 96
stores, 93% of them from one contributor, mostly in the Bay Area (Mountain View, Sunnyvale, Los
Altos) and New York City. For our categories this gives about **118 milk, 69 egg and 227 coffee
store-product prices**, and **zero** toilet paper or paper towels. It is useful for those metros
and as a test of the observed-price model. It is not national coverage.

## 1. Access

| Item | Finding |
|---|---|
| API docs | Swagger UI at `/api/docs`; the OpenAPI 3.0.3 schema is at `/api/schema?format=json` (475 KB). Read endpoints need no auth (auth is a Bearer token, needed only for writes). |
| Prices list | `GET /api/v1/prices`, returning `{items, page, pages, size, total}`. Page-number pagination: `page`, with `size` capped at **100** (asking for 500 returned 100). |
| Filters used | `currency`, `date`, `date__gte/__lte/__gt/__lt`, `created__gte/__lte`, `type` (`PRODUCT`/`CATEGORY`), `product_code`, `product_code__in`, `location_id`, `location_osm_id` + `location_osm_type`, `location__type` (`OSM`/`ONLINE`), `product__categories_tags__contains/__overlap`, `category_tag`, `labels_tags__contains`, `price_is_discounted`, `proof__type`, `kind`, `duplicate_of__isnull`, `lat`+`lon`+`radius_km`, `order_by` (e.g. `-id`, `-date`, `-created`). |
| Missing filters | **No `updated__gte`** and **no country filter** on prices. An "updated since" cursor is not possible, and the country has to be checked on the nested `location`. |
| Other endpoints | `/prices/{id}`, `/prices/{id}/history`, `/prices/stats`, `/locations`, `/locations/osm/{type}/{id}`, `/products/code/{code}`, `/proofs`. |
| Daily dumps | `prices.openfoodfacts.org/data/{prices,locations,products,proofs}.jsonl.gz`, regenerated daily (prices 22.7 MB gz, locations 1.2 MB, products 20.3 MB, proofs 9.7 MB). Dump rows are flat (ids only, no nested product or location) and give prices as exact strings (`"1.250"`). A Hugging Face Parquet copy also exists. |
| Rate limits | None documented and no rate-limit headers seen. The upstream guidance is to test scripts on pre-prod (`prices.openfoodfacts.net`) first. We follow the OFF etiquette already used in `enrichment/off.py`: 1 req/s, retry with backoff, and a User-Agent naming the app and repo. |
| Environments | Production `prices.openfoodfacts.org`, pre-prod `prices.openfoodfacts.net` (separate tokens). |

**API window vs dumps:** a daily 90-day API window is about 110 small requests. The dumps would
be 44 MB/day of downloads plus a three-way join. The API returns nested product names, quantities
and locations ready to use, and fits the existing `recorded_transport` test harness. Use the
API, and keep the dumps as the fallback if upstream asks us to.

## 2. Price fields (`PriceFull`)

| Field | Notes |
|---|---|
| `id` | Stable integer. Price page: `https://prices.openfoodfacts.org/prices/{id}` (frontend route `/prices/:id`). |
| `type` | `PRODUCT` (has `product_code`, a barcode) or `CATEGORY` (no barcode: `category_tag` such as `en:bananas`, plus `price_per`, `origins_tags` and `labels_tags`). |
| `product_code`, `product` | 13-digit codes (EAN-13, UPC-A padded) plus 8-digit codes. Nested `product` is Open Prices' cached OFF record: `product_name`, `brands`, `quantity`, `product_quantity` + `product_quantity_unit`, `categories_tags`, `labels_tags`, `source` (`off`/`opf`/`obf`/`opff`/null). |
| `product_name` | Set only for CATEGORY prices (free text). For PRODUCT prices, use `product.product_name`. |
| `price`, `currency` | Price in `currency` (ISO 4217). **The API sends a JSON float** (`27.7`); parse with `parse_float=Decimal`. |
| `price_per` | `UNIT`, `KILOGRAM` or `LITER`, used only on CATEGORY prices (e.g. bananas per kg). Null on all 500 sampled PRODUCT prices. |
| `price_is_discounted`, `price_without_discount`, `discount_type` | 32% of the US window is discounted (`SALE`, `QUANTITY`, `LOYALTY_PROGRAM`, `SEASONAL`, …). |
| `date` | The day the price was seen (a date, not a time). |
| `location` | `type` `OSM` or `ONLINE`; `osm_type` (`NODE`/`WAY`/`RELATION`) + `osm_id`; `osm_name`, `osm_brand`, `osm_display_name` (full address), `osm_address_city`, `osm_address_postcode`, `osm_address_country_code`, `osm_lat`/`osm_lon`. Some locations have every OSM field null. |
| `proof` | `type`: `PRICE_TAG`, `RECEIPT`, `SHOP_IMPORT`, `GDPR_REQUEST`; plus `owner_consumption`. |
| `duplicate_of` | Set when moderators mark a duplicate (397 in the US window). Skip these rows. |
| `source` | Submitting client, e.g. `Smoothie - OpenFoodFacts (…)`, `Open Prices Web App - /experiments/proof-price-tag-assistant`, `Dobinator - Sprouts`. |
| `created`, `updated` | Timestamps. Edits bump `updated`, but nothing can be filtered on it. |

## 3. Data volume (measured 2026-11-08)

| Slice | Prices |
|---|---|
| All currencies, all time | 342,470 |
| USD, all time | 47,586 (47,068 PRODUCT, 518 CATEGORY, 175 ONLINE) |
| USD, last 12 months (`date >= 2025-11-08`) | 34,072 |
| USD, last 90 days (`date >= 2026-08-10`) | 11,028, of which **11,026 in the US** (1 Cambodia, 1 no country) |
| USD, last 30 days | 3,862 |
| USD created 2026-11-01..08 | 631 (about 80/day) |

US 90-day window, from the full pull:

- **96 stores in 56 cities.** Top cities: Mountain View 4,141, New York 2,467, Sunnyvale 2,017,
  Los Altos 1,517, Rochester Hills 521. Top chains: Safeway 3,254, Draeger's 1,408, Whole Foods
  1,212, Sprouts 1,029, Dumbo Market 1,008, Smart & Final 657, Target 455.
- **39 contributors; one account (`doby`) has 10,290 (93%).** Coverage depends on a few people.
- **Proofs:** 10,649 price tag, 377 receipt. Product price rows: 10,963; category rows: 63, all
  produce.
- **Product data:** 9,500 rows have a product name (87%); 4,525 have `product_quantity` (41%) and
  3,773 have `quantity` text. **About 60% have no size we can use**, so they can be listed but not
  ranked (unranked, with the reason).
- **AI-assisted entry:** 5,680 rows (52%) come from the web app's `/experiments/proof-price-tag-assistant`
  and `/experiments/receipt-assistant`. These pre-fill prices from photo predictions (the schema
  has `PriceTagPrediction` and `ProofProcessWithGemini`), and a human confirms them. See decision D7
  in the plan.

Our five categories (title keyword match on `product.product_name`, rough excludes for plant
milks, chocolate, drinks and creamers; latest price per store and product):

| Category | Store-product prices | With a size | Stores | Without assistant rows |
|---|---|---|---|---|
| milk | 118 | 42 | 27 | 55 (18 sized, 9 stores) |
| eggs | 69 | 42 | 13 | 39 (22 sized, 5 stores) |
| coffee | 227 | 116 | 19 | 92 (47 sized, 7 stores) |
| toilet paper | 0 | 0 | 0 | 0 |
| paper towels | 0 | 0 | 0 | 0 |

The OFF category tags agree roughly: `en:milks` 92, `en:eggs` 32, `en:coffees` 157,
`en:toilet-papers` 0, `en:paper-towels` 1 (all time, USD). We should use our own title
normalizer, not OFF tags. Tags cover only products with OFF categories, and `en:milks` includes
plant milks.

## 4. Freshness

- The median observation is **54 days old**. Only 3,861 of 11,026 (35%) are 30 days old or less,
  and 1,456 are 14 days old or less.
- Submissions lag the shelf date by a median of 3 days, with a 90th percentile of 49 days
  (receipts and price-tag photos uploaded in batches). A daily sync is plenty, and a 90-day
  display window keeps most of the data. A 30-day window would hide two thirds of it.
- Prices are per store and per day. They are not live and not national. Showing them as
  "price as of" would be wrong.

## 5. Matching and normalization

- **Barcode prices:** `product_code` normalizes with the existing `enrichment.off.normalize_upc`
  (8 and 13 digits pass; 12 digits are padded). Our `Product` identity is `upc:<13>`, so an
  observed price joins any existing Walmart product with the same UPC.
- **Title and size:** build a `RawOffer` with `title = product.product_name`, `brand =
  product.brands`, and `size_text = product.quantity`, or `"{product_quantity} {product_quantity_unit}"`
  when the text is missing. Then use the normal deterministic normalizer.
- **Labels:** `product.labels_tags` is OFF's own labels. Pass it as an `OffProduct` when there is
  no `OffEntry` row, so `en:organic` keeps working as a positive-only signal.
- **CATEGORY prices** (`price_per` `KILOGRAM`/`UNIT`/`LITER`) are all produce today. None of our
  categories need them. Map them later, when a produce category exists: `KILOGRAM` to mass,
  `LITER` to volume, `UNIT` to count.

## 6. Licensing (ODbL 1.0)

- The upstream guide (`docs/guides/data.md`, `API.md`) says: "comply with the OdBL licence,
  mentioning the source of your data, and ensuring to avoid combining non free data you can't
  release legally as open data". The API's own code is AGPL-3.0, like ours.
- **Produced Work** (§1): "a work … resulting from using … the Contents (via a search or other
  query)". Our ranked pages are Produced Works. §4.3 requires a notice; this example wording
  satisfies it: *"Contains information from [Open Prices](https://prices.openfoodfacts.org), which
  is made available here under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/)."*
- **Derivative Database** (§1, §4.4): extracting "a Substantial part of the Contents in a new
  Database" is a Derivative Database. Our store of US prices (all of them, refreshed daily) is
  one. It is Publicly Used because our pages are Produced Works from it (§4.4c). So:
  - It must be under ODbL only (§4.4a), and **must not get incompatible Contents added** (§4.4d).
    Walmart API data must therefore never be written into the Open Prices tables or into the
    export.
  - §4.6 requires us to offer, free over the internet, either the whole Derivative Database or
    "the method of making the alterations … (such as an algorithm)". Our normalizer and category
    YAMLs are public AGPL code, which covers the method. We also offer a JSONL export of the
    observation rows plus our normalized fields for them, which is cheap and leaves no room for
    doubt.
- **Collective Database** (§1): the Database "in unmodified form as part of a collection of
  independent databases". Our Open Prices tables are modified (filtered and normalized), so this
  does not apply. Keeping them **separate tables** still keeps the Derivative Database cleanly
  bounded, apart from Walmart data.
- **Grey area:** `Product.attributes` merges values across the offers for a UPC, so Walmart and
  Open Prices can both feed one product row. This is the same open question the OFF research
  raised for OFF (open question 1). The export covers Open Prices rows and the fields we derive
  from them only. Get a legal check before public launch. This is not legal advice.
- The AGPL code license and the ODbL data license are independent, so there is no conflict.

## Open questions

1. Is AI-assisted entry (52% of US rows) acceptable under "no AI anywhere"? Our pipeline uses no
   AI, but the data was pre-filled by a model and confirmed by a human.
2. With prices this concentrated by metro, should the store filter list single locations (96 now)
   or chains?
3. Get a legal check on the merged `Product.attributes` question (shared with OFF).
4. Should we contribute back (for example, US barcodes we see without OFF names)? The upstream
   guide asks that products added through their SDK be contributed back; we add none.

## Sources

- https://prices.openfoodfacts.org/api/docs and `/api/schema?format=json` (OpenAPI 3.0.3)
- https://github.com/openfoodfacts/open-prices (`API.md`, `docs/guides/data.md`)
- https://github.com/openfoodfacts/open-prices-frontend (`src/router.js`, `/prices/:id`)
- https://prices.openfoodfacts.org/data/prices.jsonl.gz (HEAD: 22,744,751 bytes, 2026-11-07)
- https://opendatacommons.org/licenses/odbl/1-0/ (§1 definitions, §4.3, §4.4, §4.6)
- `docs/research/2026-11-07-open-food-facts.md` (OFF licensing, etiquette)
