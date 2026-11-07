# Open Food Facts evaluation (#18)

Date: 2026-11-07. Probes run with `User-Agent: shopwit-research/0.1 (+https://github.com/DanteArcese/shopwit)`, about 90 requests at <= 1 per second.

## Recommendation

For M2, ingest the **nightly delta files** (seeded once from the CSV or JSONL dump), filter to the UPCs shopwit has seen, and store a slim row per UPC in shopwit's own DB. Do no live OFF calls in the user path. Use the per-UPC v2 API only for on-demand backfill of a handful of new UPCs. Treat OFF as a low-recall fallback: `en:organic` was missing on 3 of 4 products that are organic by name. Keep title and rule matching primary, as the category YAMLs already do. Extend `OffProduct` with `last_modified` and a small nutrition block when M7 starts; do not add it earlier.

## 1. Access options

| Option | Details |
|---|---|
| Read API v2 | `GET https://world.openfoodfacts.org/api/v2/product/{upc}?fields=...` and `/api/v2/search?...`. No auth for reads. `fields=` keeps responses small. v3 exists for the product endpoint but v2 is what I used. |
| Rate limits (per OFF docs, not measured) | About 100 req/min for product reads, 10 req/min for search, 2 req/min for facet queries. Bulk use is directed to the dumps. |
| Observed reliability | Search returned HTTP 503 ("Page temporarily unavailable") on roughly 40% of first attempts. Product-by-UPC calls were reliable but a few needed a retry. Any API client needs retry/backoff. |
| User-Agent | Required in practice. Use `AppName/Version (contact-url)`. We use a generic client name plus the repo URL, no personal email. |
| JSONL dump | `openfoodfacts-products.jsonl.gz`, 13.4 GB gz, rebuilt daily (Last-Modified 2026-11-07 05:16 UTC). |
| CSV dump | `en.openfoodfacts.org.products.csv.gz`, 1.53 GB gz, rebuilt daily. Tab-separated. |
| Parquet | HuggingFace `openfoodfacts/product-database` `food.parquet`, about 8.0 GB. Best for DuckDB filtering by `code`. |
| MongoDB dump | `openfoodfacts-mongodbdump.gz`, 16.4 GB. Not worth it for us. |
| Deltas | `static.openfoodfacts.org/data/delta/index.txt` lists the last 13 daily files (`openfoodfacts_products_<from>_<to>.json.gz`, unix timestamps). The newest was 43.5 MB gz. Retention is about 2 weeks, so a missed fortnight needs a full reseed. |
| Open Products Facts | Separate database, same API shape on `world.openproductsfacts.org`. Its CSV dump is only 6.4 MB gz. |

## 2. Licensing

- Database: ODbL 1.0. Individual contents: Database Contents License (DbCL). Images: CC BY-SA 3.0/4.0 (not needed; do not copy images).
- Obligations: attribute ("Data from Open Food Facts, ODbL"), link the license, and keep the share-alike for a *derived database* (a "Produced Work", e.g. a ranking page, only needs attribution).
- For shopwit: our UPC-to-attributes table that merges OFF fields is arguably a derived database. Safest design is to keep OFF-derived columns in a separate, clearly labelled table or export, and make that table available under ODbL on request. Rankings shown to users are Produced Works: add a footer credit and per-product "source: OFF" where OFF supplied a value.
- AGPL-3.0 code is independent of the data license; ODbL covers the data only. No conflict. Not legal advice; confirm before launch (open question 1).

## 3. Coverage test

UPCs found through OFF/OPF search (US products), then looked up with the v2 product endpoint. "Org?" = `en:organic` in `labels_tags`. Protein is per 100 g from OFF nutriments.

| UPC | Product | DB | Found | Quantity | Org? | Protein | Modified |
|---|---|---|---|---|---|---|---|
| 0093966010015 | Organic Valley 2% milk | OFF | yes | none | no (no labels) | 3.3 | 2026-01 |
| 0093966002201 | Organic Valley lactose free fat free | OFF | yes | none | yes | 3.75 | 2026-02 |
| 0041383090707 | Lactaid fat free | OFF | yes | none | n/a | 3.3 | 2026-04 |
| 0811620020169 | fairlife whole UF | OFF | yes | 52 fl oz | n/a | 5.4 | 2026-08 |
| 0856312002795 | fairlife chocolate reduced fat | OFF | yes | 52 fl oz | n/a | 5.4 | 2026-11 |
| 0096619000050 | Kirkland 1% milk | OFF | yes | 1 gallon | n/a | 3.75 | 2026-01 |
| 0096619056576 | Kirkland organic whole milk | OFF | yes | none | **no (no labels)** | 3.3 | 2025-05 |
| 0851387007720 | Vital Farms eggs | OFF | yes | none | n/a | 13.6 | 2026-02 |
| 0078742148380 | Great Value eggs | OFF | yes | `12` | n/a | 12 | 2025-09 |
| 0078742127224 | Great Value medium white eggs | OFF | yes | 12 pcs | n/a | 6 (suspect) | 2025-11 |
| 0025500204215 | Folgers Classic Roast | OFF | yes | 865 g | n/a | none | 2026-02 |
| 0025500067636 | Folgers Hazelnut | OFF | yes | 11.5 oz | n/a | none | 2025-05 |
| 0785357023567 | Peet's Major Dickason's | OFF | yes | 18 oz | n/a | none | 2026-01 |
| 0785357024496 | Peet's Alpine Blend "Organic" | OFF | yes | 32 oz | **no** (only en:kosher) | none | 2025-09 |
| 0037000792468 | Charmin Ultra Soft super roll | OPF | yes | none | n/a | n/a | 2020-04 |
| 0037000613275 | Charmin Ultra Soft super plus | OPF | yes | none | n/a | n/a | 2020-04 |
| 0036000476224 | Cottonelle strong bath tissue | OPF | yes | none | n/a | n/a | 2020-04 |
| 0030772167748 | Bounty Select-A-Size | OPF | yes | 906 sheets | n/a | n/a | 2026-03 |
| 0030772087114 | Bounty paper towels | OPF | yes | none, no categories | n/a | n/a | 2025-05 |
| 0054000200427 | Scott 1000 sheets, 1 roll | OPF | yes | none | n/a | n/a | 2020-04 |

Misses to note: the search for Horizon, Folgers-by-name, Kirkland coffee, and Scott/Charmin by name returned nothing or 503 on the first attempt. Horizon was never found in this sample, so a Horizon lookup is the first follow-up. Toilet paper and paper towels are **not** in OFF; they live in Open Products Facts, which needs a second lookup.

## 4. Data quality

- **Found:** 20/20 of UPCs that I selected from search results, which biases coverage upward. Real cold-start coverage by arbitrary shopwit UPCs is unmeasured (open question 2). Several UPCs in OFF carry EAN-13 form with a leading 0; normalise to 13 digits before lookup.
- **Quantity present:** 10/20 overall; 9/14 food and 1/6 household. All 10 are parseable (`52 fl oz`, `1 gallon`, `865 g`, `11.5 oz`, `906 sheets`, `12 pcs`, bare `12`). OFF's derived `product_quantity` plus `product_quantity_unit` was present for 6 of the 7 with a mass or volume, so prefer it over parsing. Package-count, pack-size and sheet counts for toilet paper and towels are mostly absent, so OFF will not rescue unit-price there.
- **`en:organic` reliability:** zero false positives in this sample, but recall is poor. Of 4 products that are organic by name (Organic Valley 2%, OV lactose-free, Kirkland organic whole, Peet's "Organic"), only 1 carried `en:organic`. Use it as a positive-only signal: tag present means organic is true, tag absent says nothing. That matches the `absent: false` fallback in `categories/dairy/milk.yaml`, so for organic the OFF label must never override a title match and must not be used to set `false`.
- **Labels field:** `labels_tags` can be `null`, `[]`, or missing; treat all as empty. It also holds lots of noise (`en:no-lactose`, `en:50-less-sugar` on a fairlife).
- **Nutrition:** protein and energy present on 11/14 foods (all milk and eggs, no coffee, which is expected). Per-100 g values look user-entered and one egg row is inconsistent (6 g protein vs 12 g for the same brand). Fine for M7 as a hint, not as authoritative.
- **Staleness:** food records mostly modified within the last 12 months (oldest sampled 2025-05). Household records from OPF are often 2020-era imports and basically frozen.

## 5. Recommendation detail

- **API vs dump:** a catalog of thousands of UPCs refreshed daily does not suit the API (search 503s, 10 req/min search cap, no bulk). Seed once from the Parquet or CSV (1.5 to 8 GB, one-time), filter by `code`, then apply the daily delta (about 45 MB) filtering to known UPCs. For OPF, the whole CSV is 6.4 MB, so just download it daily.
- **Cache:** table `off_product(upc, source[off|opf], labels, quantity, product_quantity, product_quantity_unit, nutriments_json, last_modified, fetched_at)`. Misses are cached too (`found=false`, re-check weekly via the delta). Normalizer reads only this table, preserving purity.
- **API use:** only for one-off lookups when a new UPC appears between dump refreshes, with 1 req/s, retry with backoff, and the same User-Agent.
- **`OffProduct`:** keep `labels` and `quantity` for M2. Add optional `product_quantity: float | None` plus `unit`, and `last_modified: date | None`. For M7 add `protein_100g: float | None` and `energy_kcal_100g: float | None` (or a `nutriments: Mapping[str, float]`). Add a `source` field (`off`|`opf`) for attribution.

## Open questions

1. Is a merged UPC attribute table a "derived database" under ODbL for our hosting model? Needs a decision or legal check.
2. Coverage against shopwit's real UPC list is unknown; run the join after the first scraper batch.
3. Horizon and Kirkland coffee were not found via search; confirm by UPC.
4. Is delta retention (about 2 weeks) acceptable, or do we also keep weekly full snapshots?
5. Should we contribute corrections back to OFF (labels, quantity)?

## Sources

- https://openfoodfacts.github.io/openfoodfacts-server/api/
- https://world.openfoodfacts.org/data
- https://static.openfoodfacts.org/data/delta/index.txt
- https://huggingface.co/datasets/openfoodfacts/product-database
- https://world.openfoodfacts.org/terms-of-use
- https://opendatacommons.org/licenses/odbl/1-0/
- https://world.openproductsfacts.org/
