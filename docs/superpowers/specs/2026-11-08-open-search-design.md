# Open-Ended Search — Design Spec

- **Date:** 2026-11-08
- **Status:** Draft; open questions resolved by the maintainer 2026-11-08 (D8–D15)
- **Issue:** #65
- **Amends:** [`2026-11-07-unitpricer-design.md`](2026-11-07-unitpricer-design.md) (the main spec). Where the two disagree, this spec wins for search, and §13 lists every section it changes.
- **Affects approved plans:** [`plans/2026-11-08-m4-lists-accounts.md`](../plans/2026-11-08-m4-lists-accounts.md) and [`plans/2026-11-08-category-expansion.md`](../plans/2026-11-08-category-expansion.md) (§14).

## 1. Summary

Today a search either resolves to one of five curated categories or falls back to a bare full-text list of titles with prices and no unit price. Anything outside the curated set is a dead end, and adding categories one by one cannot keep up with what people search for.

This spec adds a second tier. **Any query gets a useful answer:** products the catalog already holds, matched by text and ranked by a generic unit price inside groups by measure (weight, volume, count), with filters taken only from structured data. New queries also **fetch live** from sources that support text search and stream into the page within seconds. Curated categories stay as the precise tier, offered as an upgrade ("Milk: more filters →"). Searches that land in the generic tier are counted anonymously so the maintainer can see which categories to curate next.

The principles of the main spec still hold. Ranking is honest (Principle 2). Generic mode never guesses: it shows no attribute it would have to read out of a title (Principle 3). No AI anywhere (Principle 4). No page waits on a retailer API, because live results arrive by polling after the page has rendered (Principle 5, clarified in §13).

## 2. Decisions (approved 2026-11-08, binding)

| # | Decision |
|---|---|
| D1 | Query → existing curated resolver. A curated match redirects to the category page as today. Anything else goes to a **generic results page**: Postgres full-text search over title, brand and store category path, with typo tolerance (`pg_trgm`), over all held products, curated ones included. Near-miss curated categories are offered as "<Category>: more filters →". |
| D2 | New searches **fetch live and stream in.** A DB-backed job queue is picked up by the existing worker within about 1–2 s. The page shows held results, then polls via HTMX every ~2 s for up to ~15 s. Connectors gain an optional text-search capability: Walmart search (when credentials arrive, expected around Dec 3), and Open Food Facts text search → products → Open Prices lookups by barcode. Per-source rate limits live in the DB, with backoff and queueing. Fetched data is cached within each source's terms (Walmart ≤ 24 h, "price as of" shown). |
| D3 | **Generic unit price** from the parsed package size: weight per oz / 100 g, volume per fl oz / L, count per item, following the user's unit setting (metric display later unified to per kg / per L by D12). Results are shown as separate ranked groups by measure, largest group first. "Size unknown" is listed unranked. Never convert between dimensions. |
| D4 | **Data-derived facets only from structured fields:** brand, size range, store, labels (OFF `labels_tags` such as organic or gluten-free; store-brand flags from APIs), store category path. A facet shows only when the results have ≥ 2 distinct values. Generic mode never infers attributes from titles. |
| D5 | Curated categories remain as **upgrades** (special units, precise attributes). **Demand counting:** unmatched search phrases are stored only as normalized phrase + count + distinct days + last-seen date, with no user, IP or session. Phrases seen on too few days are pruned. The admin has a "curate next" ranked list. |
| D6 | **Lands before M4.** M4 list items may be a curated spec or a generic query + target size. Category expansion shrinks to top-demand categories, household first until demand data exists. Postal-code radius (#63) and the feedback form (#57) proceed independently. |
| D7 | **Testing** uses recorded or synthetic fixtures only, with named tests for measure grouping, the facet ≥ 2 rule, the rate limiter, the polling stop condition, and "generic mode never invents an attribute". |
| D8 | **Bare "oz"** (no "fl") gets its own ranked group, **"per oz (as labeled)"**. It is never converted and never inferred to be weight or volume, and no category-path heuristic is used (§5.2). |
| D9 | The **daily Open Prices sync, keeping unmatched rows as generic observed offers, is the primary Open Prices path** (§7.3). The live OFF → Open Prices barcode lookup sits behind `UNITPRICER_LIVE_OFF`, **off by default** (§6.1). |
| D10 | **OFF text search** budget defaults to the documented ~10 req/min; the real limit is measured during implementation and the default adjusted (§6.3). |
| D11 | **Walmart** budget defaults to 1 req/s and 5,000 requests/day until real credentials and their documented limits are in hand (§6.3). |
| D12 | **Units are unified everywhere:** metric per kg and per L, imperial per oz and per fl oz, on generic results **and on curated category pages**, which change from per 100 g / per 100 mL (§5.2, §13.1). |
| D13 | The **OFF category tree** serves as the category path for OFF and Open Prices rows, which have no store path (§5.3, §7.1). |
| D14 | The worker **re-fetches the top 50 demand phrases every 12 h**, within the shared budgets and after visitor jobs (§6.6). |
| D15 | **Demand thresholds:** a phrase is listed in "curate next" after 3 distinct days and pruned 30 days after it was last seen, however many days it was seen (§9; amended 2026-11-08 by user ruling, was "when it has fewer than 3 days"). |

## 3. Goals and non-goals

### Goals
1. Every query that doesn't resolve to a curated category shows ranked, comparable results in under 300 ms of server time from held data (§10).
2. Queries the catalog has never seen fill in from live sources within ~15 s, without blocking the page.
3. Comparisons stay honest: an item is ranked only against items measured in the same dimension, and only by the price per unit of that dimension.
4. The maintainer can see which uncurated phrases people search for, without the app learning anything about who searched.

### Non-goals
- Inferring attributes (fat %, ply, organic, flavour) from titles in generic mode. That is what curated categories are for.
- Converting between dimensions (e.g. fl oz of milk to oz of weight via a density), or deciding whether a bare "oz" is weight or volume (D8).
- Paper-goods measures (rolls, sheets, area) in generic mode. Those need curated categories.
- Live fetching on the apex host, on category pages, or for observed in-store prices beyond what §6.4 describes.
- Ranking by text relevance. Text decides which products match; unit price decides their order.

## 4. Search flow

```
GET /search?q=…
  │
  ├─ resolve_query(q, active curated categories)   (unchanged, normalize/query.py)
  │     └─ category found ──► 302 /c/<slug>/?<filters>&q_rest=…   (unchanged)
  │
  └─ no category ──► generic results page (200, rendered from held data)
        1. full-text + trigram match over fresh offers (§5.1)
        2. group by measure, rank by generic unit price (§5.2)
        3. facets from structured fields (§5.3); near-miss curated categories (§5.4)
        4. record demand for the normalized phrase (§9)
        5. enqueue FetchJobs for sources that need refreshing for this phrase (§6.2)
        6. if any job for the phrase is queued or running: include the poller (§6.5)
```

- The query is trimmed and cut to 200 characters as today (`MAX_QUERY_LENGTH`).
- **Normalized phrase** (`normalize.generic.normalize_phrase`, pure): `fold()` from `normalize/text.py`, runs of whitespace collapsed, cut to 100 characters. This phrase is the key for demand rows and fetch jobs, and the text sent to live sources. The page's own matching uses the folded query.
- **Phrases that look personal** are never stored or sent anywhere: any phrase containing `@`, or a run of 5 or more digits (phone numbers, postal codes, card numbers), skips demand counting and live fetching. It still gets held results.
- Search GETs stay **cookie-free**, as in M3: the view reads preferences from an existing session but never creates one, and neither do the polling requests.

## 5. Generic results

### 5.1 Matching

- **Population:** `fresh_offers(now)` for live offers (§8), restricted to the visitor's selected live stores when set. Observed offers get their own section from `fresh_observed(now)` (§8).
- **Text match:** `Offer.search_vector` (a stored generated `tsvector`, §7.1) with weights title A, brand B, category path C, under the `english` configuration, queried with `websearch_to_tsquery('english', q)`. Every term must match (websearch default AND).
- **Typo tolerance:** when the text match finds fewer than 5 products, offers whose title is trigram-similar to the folded query (`pg_trgm` `word_similarity(q, title) >= 0.6`, using the `<%` operator against a `gin_trgm_ops` index) are added. The page then says "Showing close matches for '<q>'" so a fuzzy result is never presented as exact.
- **Cap:** at most 500 products, the top 500 by `ts_rank` (trigram similarity for fuzzy matches). When the cap applies the status line says "Showing the 500 closest matches; add words to narrow". Relevance only picks the population; it never orders rows.
- **Product rows:** offers are grouped by product as `browse()` does. A product's row uses its cheapest offer by generic unit price, ties broken by price, retailer and then offer id (deterministic, never by commission).
- **Curated products are included.** They are matched and ranked exactly like generic ones, using their generic measure (§5.2), never their curated attributes. Their row carries a small link "In <Category> →" to the category page.

### 5.2 Generic measure and measure groups

Each offer gets **one generic measure** at ingest, computed by a new pure module `normalize/generic.py`:

```python
@dataclass(frozen=True)
class GenericMeasure:
    dimension: Literal["volume", "mass", "oz_labeled", "count"] | None
    amount: float | None          # canonical: mL, g, labeled oz or items
    reason: str | None            # why dimension is None, e.g. "size unknown"

def generic_measure(texts: Sequence[str], structured_unit: str | None = None) -> GenericMeasure
```

- **Inputs, in order:** the source's size field (`RawOffer.size_text`), the title, then the OFF quantity. The first input that yields a size decides; later inputs are not consulted. The structured unit is OFF's `product_quantity_unit` when known; it only applies when the OFF quantity is the input that decides.
- **Volume** is recognized only from unambiguous volume units: fl oz, gal, qt, pt, mL, L. **Mass** only from lb, kg, g, and from oz in a compound with lb ("1 lb 2 oz"). **Count** only from ct, count, pieces, pcs, eggs, dozen. These reuse the regexes and guards in `normalize/quantity.py` (ranges, compounds, multipacks), including the guards the category-expansion plan's Task 1 adds (nutrient grams such as "30g Protein" are never the pack size; a bare "N Count" next to a size is ambiguous).
- **A bare "oz" gets its own dimension, `oz_labeled` (D8).** A curated volume category may read it as fluid ounces because the category says so. Generic mode has no such context: "64 oz" milk is a volume and "16 oz" pasta is a weight. So a bare "oz" (not "fl oz", not part of "lb … oz") is kept exactly as labeled: amount = the labeled ounces (with multipack multipliers applied as for other units), ranked only against other bare-oz offers in the group "per oz (as labeled)". It is never converted to grams or millilitres, never moved into the weight or volume group, and no store category path, word such as "net wt", or other context is used to decide which it is.
- **One dimension per offer.** Mass, volume or labeled oz wins over count (a "12 Count" beside "16.9 fl oz" is the container count, handled by the multipack rules). When more than one of volume, mass and labeled oz parse from the deciding input ("16.9 fl oz (500 g)", "16 oz (454 g)"), the measure is unknown, reason "more than one kind of size". Never pick one.
- **Paper goods** (rolls, sheets) and other measures have no generic dimension. Such offers are "Size unknown" in generic mode, and the near-miss link (§5.4) points to the curated category that can rank them.
- **Generic unit price** = `price / amount`, stored per canonical unit (per mL, per g, per labeled oz, per item) as a `Decimal` quantized to 6 places, like curated unit prices.

**Display**, following the visitor's unit system:

| Dimension | Imperial | Metric |
|---|---|---|
| volume | per fl oz | per L |
| mass | per oz | per kg |
| oz_labeled | per oz (as labeled) | per oz (as labeled) |
| count | each | each |

These are the unified display units of D12, shared with curated category pages (§13.1). Labeled oz shows the same in both systems, because converting it would require knowing whether it is weight or volume.

**Groups.** The page shows one ranked group per dimension present: "By volume (n)", "By weight (n)", "Per oz as labeled (n)", "By count (n)". Groups are ordered by product count, largest first, ties in the fixed order volume, weight, labeled oz, count. The labeled-oz group carries a one-line note: "Sold by \"oz\" without saying weight or fluid ounces, so these are compared only with each other." Within a group, rows rank by generic unit price ascending; ties by price, title, retailer. Each group shows its first 25 rows with a "Show all n" HTMX link (up to the 500 cap). **"Size unknown"** comes last: unranked, ordered by price, each row with its reason. A row never appears in two groups, and no number is ever compared across groups.

### 5.3 Facets

Facets come only from structured fields that a source supplied as data, never from title text.

| Facet | Source field | Notes |
|---|---|---|
| Brand | `Offer.brand` (the source's brand field) | Folded for grouping, shown as the most common spelling. No brand → unknown. |
| Brand tier | `brand_tier` from the **brand field** matched against `data/store_brands.yaml` | Generic mode drops the title-prefix fallback that `normalize.match.brand_tier` has today. No brand field → unknown. |
| Size range | `Offer.measure_amount` within the active group | One min/max pair per dimension (`vol_min`/`vol_max`, `mass_min`/`mass_max`, `oz_min`/`oz_max`, `count_min`/`count_max`; labeled oz bounds are in oz in both unit systems), written in display units and converted as `size_min`/`size_max` are today. |
| Store | `Offer.retailer` (live) and chain code (observed) | The existing store selector and `store` param, same semantics as category pages. |
| Labels | `Offer.labels`: OFF `labels_tags` for the product's UPC (from `OffEntry`, the OFF search result, or the Open Prices nested product) | Only labels on an allowlist in a new `data/labels.yaml` (tag → display name, e.g. `en:organic` → "Organic", `en:gluten-free` → "Gluten-free"), so OFF's noisy tags stay out. **Positive only:** a label filter keeps offers that carry the tag; a missing tag never means "not organic". Shown with "from Open Food Facts". |
| Category path | `Offer.category_path`: the store's own path (Walmart `categoryPath`, e.g. "Food/Dairy, Eggs & Cheese/Milk"), or for OFF and Open Prices rows the OFF category tree (D13) | Drill-down by prefix: the facet lists the distinct next segments under the selected prefix. OFF paths are built from the product's `categories_tags` in OFF's broad-to-specific order, `en:` prefixes dropped and hyphens shown as spaces, and stored under a leading "Open Food Facts" segment (e.g. "Open Food Facts/Dairies/Milks/Semi-skimmed milks") so the two taxonomies never mix at the top level. Tags in other languages than `en:` are skipped. |

**The ≥ 2 rule.** A facet shows only when the products matching every *other* active facet have at least 2 distinct known values for it. Unknown is not a value. For labels, each allowlisted label is its own yes/unknown choice: it is offered only when at least one product carries it and at least one doesn't, since otherwise filtering by it changes nothing. A facet that is currently active always shows, with a clear link, even if only one value remains.

**Unknown under a filter.** As on category pages (main spec §6), when a facet is active, products with an unknown value for it are excluded; otherwise they are included.

**URL state.** All facet state is in the URL (`brand`, `brand_tier`, `store`, `label`, `path`, size bounds), repeatable where it makes sense (`label=en:organic&label=en:vegan` means both). Unknown values for a param are ignored, never an error. HTMX swaps the results on change as on category pages.

### 5.4 Near-miss curated categories

Up to 3 curated categories are offered above the results as "<Category>: more filters →", linking to `/c/<slug>/?q_rest=<folded query>`. Candidates, merged and ordered by score:

1. **Held results:** active categories that at least 2 matched products belong to, scored by that product count.
2. **Typo match:** active categories whose name or any `match.include` phrase is trigram-similar to the folded query (`similarity >= 0.4`), computed in Python over the loaded category definitions (≤ a few hundred phrases). This catches "tolet paper" when `resolve_query` misses it.

### 5.5 Page layout

- Heading "Results for '<q>'"; affiliate disclosure and Amazon line as on today's search page.
- A status line (`aria-live="polite"`): "<n> products" plus the live state, e.g. "checking Walmart and Open Food Facts…", "updated with 12 new products", "Walmart didn't respond; showing saved prices" (§11).
- Near-miss links, then facets beside (desktop) or above (phone) the groups, reusing the category page's filter components.
- The groups (§5.2), each row with title, brand, size, price, generic unit price, store, "price as of <time>" and the direct outbound link (`{% outbound %}`), plus the Amazon disclaimer on Amazon offers.
- **"Seen in stores"** (observed offers, §8) below the live groups, grouped by measure the same way, with the ODbL notice, exactly as on category pages.
- A note under the heading: "Matched by text and ranked by price per unit. For precise filters, pick a category." No attribute "unknown" labels are shown in generic mode, because generic mode has no attributes.

## 6. Live fetch

### 6.1 Text-search capability

The `Connector` Protocol in `connectors/base.py` already has `search(query, limit)`. Live search adds an optional capability, declared by a separate Protocol so that connectors without it need no change:

```python
class LiveSearchSource(Protocol):
    name: str                 # also the SourceBudget key
    max_cache_age: timedelta  # terms limit on holding its data
    refresh_after: timedelta  # skip a new fetch for a phrase fetched this recently
    def live_search(self, phrase: str, limit: int, budget: Budget) -> LiveSearchResult: ...
```

- `budget.acquire()` must be called before **every HTTP request** the source makes, so multi-request sources (OFF then Open Prices) are paced per request (§6.3).
- `LiveSearchResult` carries live `RawOffer`s, observed `Observation`s (Open Prices), OFF product records (labels, quantity) for the `OffEntry` cache, and the skipped count.
- `connectors/registry.py` gains `live_sources()`: the available sources that implement the capability and are enabled by settings.

**Walmart** (`walmart`): `live_search` calls the existing `search(phrase, limit=25)` and adds `categoryPath` to `RawOffer.fields`. `max_cache_age` 24 h and `refresh_after` 12 h (matching the scheduled refresh). Offers are never written to `PriceHistory` (`records_price_history = False`, unchanged). Available only once `WALMART_*` credentials are set; until then it is simply absent from `live_sources()`.

**Open Food Facts → Open Prices** (`off-prices`), behind `UNITPRICER_LIVE_OFF=1`, **off by default** (D9). The primary Open Prices path is the daily window sync, which keeps unmatched rows as generic observed offers (§7.3) and so already holds every US price from the last 90 days. This live lookup adds only prices submitted since the last daily pull, plus OFF labels and categories; turn it on if that gap matters in practice:
1. One OFF text search for the phrase, US products only, `fields=` limited to code, name, brands, quantity, `product_quantity`, `product_quantity_unit`, `labels_tags`, `categories_tags`, page size 50. The research doc lists `/api/v2/search` but did not confirm free-text terms on it, so the implementation plan first confirms which endpoint takes free text (the legacy `/cgi/search.pl?search_terms=…&json=1` does), records a fixture, and measures the real rate limit (D10).
2. Barcodes from the result, normalized with `enrichment.off.normalize_upc`, go to Open Prices in one request: `GET /api/v1/prices?product_code__in=<codes>&currency=USD&date__gte=<90 days ago>&size=100`.
3. Observations pass the existing `skip_reason()` rules and are written exactly as the daily sync writes them (`OpenPriceObservation`, then observed offers), so ODbL handling and the 90-day window are unchanged.
4. OFF records update `OffEntry` (labels, quantity, categories), which also improves enrichment for those UPCs elsewhere.
5. OFF products with no Open Prices row produce no `Product` and no offer: there is nothing to rank.
`max_cache_age` follows the observed rules (§8); `refresh_after` 24 h, since the daily window sync also covers these rows.

### 6.2 Fetch jobs

- **Enqueue** (in the search view, after the page's own queries, in one short transaction): for each live source, skip when the phrase is personal (§4); skip when a `FetchJob` for `(source, phrase)` succeeded within `refresh_after`; skip when one is already queued or running (a partial unique constraint makes concurrent enqueues safe, and the loser reuses the existing job); skip when the source is blocked or out of daily quota (§6.3) or when the global queue already holds 200 queued jobs. Otherwise insert a `FetchJob(status=queued)`.
- **Per-client throttle:** at most 10 new jobs per minute per client, using the existing `web.ratelimit.allow` (salted SHA-256 of the client address in the cache, expiring with the window; nothing in the database). Over the limit, the page shows held results and no poller.
- **Pickup:** the existing worker process (`manage.py run_scheduler`) starts a second thread, the **fetch loop**, alongside the 12-hourly refresh loop. Every 1 s it claims the oldest due job with `SELECT … WHERE status='queued' AND not_before <= now() ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1`, marks it running and runs it. So a job starts within ~1–2 s when the source has budget. The thread has its own DB connection, shares the stop event with the main loop (SIGTERM ends both), and is never blocked by a long category refresh or the Open Prices window sync. Up to 2 fetch threads may run (setting `UNITPRICER_FETCH_THREADS`, default 1); `SKIP LOCKED` keeps them from taking the same job.
- **Run:** `source.live_search(phrase, …)`, then `ingest_live(result, source, now)` (§7.3), recording `result_count` (new or updated products) and `finished_at`. Errors record the exception type name only, as ingestion does today.
- **Expiry and recovery:** a visitor job (`origin` `search` or `list`) still queued 120 s after creation is marked `expired` (warm jobs expire per §6.6) (the visitor has long stopped polling, and the budget is better spent on current searches). A job left `running` for more than 5 minutes (a crashed worker) is reset to `queued` once, then marked `failed`. Finished jobs older than 7 days are deleted daily.

### 6.3 Rate limits (`SourceBudget`)

One row per source, shared by on-demand jobs and the scheduled refresh, so the two never add up past a source's limit. A token bucket refilled continuously:

| Source key | Capacity | Refill | Daily cap | Basis |
|---|---|---|---|---|
| `off-search` | 10 | 10 per minute | none | OFF documents about 10 req/min for search (research doc §1; documented, not measured). The implementation measures the real limit and adjusts this default (D10). |
| `openprices` | 5 | 1 per second | none | No documented limit; we keep the 1 req/s etiquette already used by the daily sync, which draws from the same bucket. |
| `walmart` | 5 | 1 per second | 5,000 | Conservative defaults (D11) until real credentials arrive and the limits documented for our key are known; then the settings are updated. |

- `Budget.acquire(source)` locks the row (`SELECT … FOR UPDATE`), refills by elapsed time, and takes a token when one is available. When none is available it returns the wait. The fetch loop then re-queues the job with `not_before = now + wait` and moves on rather than sleeping, so one starved source never stalls jobs for another. Scheduled ingestion waits and retries instead, since nobody is watching it.
- **Backoff:** an HTTP 429 or 503, or a transport error, sets `blocked_until = now + min(5 s × 2^failures, 15 min)`, or the `Retry-After` value when it is longer, and increments `consecutive_failures`. A success resets it. Inside a job, an OFF 503 is retried at most twice (the research saw 503 on about 40% of first search attempts), each retry taking its own token.
- Capacity, refill and caps are settings (`UNITPRICER_BUDGET_<SOURCE>_…`), seeded into the rows at startup and editable in the admin.

### 6.4 Caching within terms

- Fetched live offers are ordinary `Offer` rows with `last_seen_at = fetch time`, so `fresh_offers()` hides them at `min(FRESHNESS_CUTOFF, source max_cache_age)`: **24 h for Walmart**. Every price shows "price as of <time>".
- A repeat search for the same phrase within `refresh_after` reuses held data and does not fetch, so popular phrases cost one request per source per 12 h.
- Observed rows follow the existing 90-day rule (§8).
- Walmart data never enters `PriceHistory`, the Open Prices tables, or the ODbL export (unchanged rules from the main spec and the Open Prices plan).

### 6.5 Streaming into the page (polling)

- When at least one job for the phrase is queued or running after enqueue, the page includes a **poller**: `<div id="poller" hx-get="/search?q=…&<facets>&poll=<start>" hx-trigger="every 2s" hx-swap="outerHTML">`, where `<start>` is the server's render time in epoch seconds.
- A poll request (`HX-Request` with `poll`) first checks the jobs for the phrase. When none has finished since the previous poll it returns the poller unchanged, without re-running the search, so idle polls are cheap. Otherwise it re-runs the search and returns the poller plus the results, status line and facets as out-of-band swaps (the category page already uses OOB swaps for its status line).
- **Stop condition** (pure function `should_poll(jobs, start, now)`): polling stops when every job for the phrase is in a terminal state (`succeeded`, `failed`, `expired`, `skipped`), or when `now >= start + 15 s`, whichever comes first. When it stops, the response renders the poller **without** `hx-trigger`, so the browser makes no further requests. This does not depend on HTMX's status-286 handling. A `start` more than 15 s in the past or in the future also stops polling at once.
- On stop by timeout with jobs still pending, the status line says "Still checking <source>. Reload in a minute for more." The job keeps running, and a reload uses its results.
- Polling never records demand and never enqueues jobs.

### 6.6 Warm refresh of top-demand phrases (D14)

- Every 12 h (`UNITPRICER_WARM_HOURS`, default 12) the worker's refresh loop takes the **top 50** phrases by the "curate next" order (§9), excluding personal-looking phrases, and enqueues one `FetchJob` per live source and phrase with `origin = warm`, under the same dedup (`refresh_after`), daily cap and queue rules as visitor jobs. Warm jobs do not count toward the per-client throttle and record no demand.
- The fetch loop claims **visitor jobs first** (`origin = search` or `list`), then warm jobs, so warm refresh only spends budget visitors are not using. Warm jobs never expire at 120 s; they expire when the next warm round starts.
- Because the top phrases are refreshed every 12 h and live offers stay fresh for 24 h, their held results never lapse between visits while the source is up.

## 7. Data model changes

### 7.1 Changed models

**`Product`** — `category` is already nullable (`null=True`, `SET_NULL`); no schema change. Semantics: `NULL` now means a generic product rather than an orphan. `merge_product` must not open a `ReviewItem` for a product whose category is `NULL`: the "category" unresolved entry is expected in generic mode, and the review queue stays for curated problems.

**`Offer`** — `category` is already nullable. New columns:

| Field | Type | Notes |
|---|---|---|
| `category_path` | `CharField(500, null)` | The store's own path, or the OFF category tree for OFF and Open Prices rows (D13); structured fields only. |
| `labels` | `JSONField(default=list)` | OFF label tags for the UPC, allowlisted or not (the facet filters against the allowlist). |
| `measure_dim` | `CharField(10, null)` | `volume`, `mass`, `oz_labeled`, `count`, or `NULL` when the size is unknown. |
| `measure_amount` | `FloatField(null)` | Canonical mL, g, labeled oz or items. |
| `measure_reason` | `CharField(100, null)` | Why `measure_dim` is `NULL`. |
| `generic_unit_price` | `DecimalField(14, 6, null)` | Price per canonical unit. |
| `search_vector` | generated stored `tsvector` | `setweight(to_tsvector('english', coalesce(title,'')), 'A') ‖ setweight(…brand…, 'B') ‖ setweight(…category_path…, 'C')`. Use Django's `GeneratedField` with `db_persist=True` if it accepts the expression as immutable; otherwise add the column with `RunSQL` and map it as a read-only field. |

Indexes: GIN on `search_vector`; GIN `gin_trgm_ops` on `title`; B-tree on `(measure_dim, generic_unit_price)`; B-tree on `(kind, currency, last_seen_at)` for the freshness filter. The `pg_trgm` extension is created with Django's `TrigramExtension` migration operation. It ships with the `postgres:17` image and with Debian/Ubuntu `postgresql` (contrib is included), which the test plugin uses.

**`OffEntry`** gains `categories` (`JSONField(default=list)`, OFF `categories_tags`), filled by the dump/delta sync and by OFF search results. **`OpenPriceObservation`** gains `categories` from the nested product's `categories_tags`. Both are OFF/ODbL data in tables that already hold it.

A management command `backfill_generic` computes the generic measure, unit price and labels for existing offers; it is idempotent and runs once after the migration.

### 7.2 New models (in `catalog`)

```python
class FetchJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"; RUNNING = "running"; SUCCEEDED = "succeeded"
        FAILED = "failed"; EXPIRED = "expired"; SKIPPED = "skipped"
    source = CharField(30)                  # SourceBudget key
    phrase = CharField(100)                 # normalized phrase (§4)
    origin = CharField(10, default="search")  # search, list or warm (§6.6); claim order
    status = CharField(10, default=QUEUED)
    created_at = DateTimeField(auto_now_add=True)
    not_before = DateTimeField()            # = created_at, or later after a budget wait
    started_at = DateTimeField(null=True)
    finished_at = DateTimeField(null=True, db_index=True)
    attempts = PositiveSmallIntegerField(default=0)
    result_count = IntegerField(default=0)
    error = CharField(200, blank=True)      # exception type name only
    # UniqueConstraint(source, phrase) where status in (queued, running)
    # Partial index on (not_before, created_at) where status = queued

class SourceBudget(models.Model):
    source = CharField(30, primary_key=True)
    capacity = FloatField()
    refill_per_second = FloatField()
    tokens = FloatField()
    refilled_at = DateTimeField()
    daily_limit = IntegerField(null=True)
    daily_used = IntegerField(default=0)
    day = DateField()                       # UTC day daily_used counts
    blocked_until = DateTimeField(null=True)
    consecutive_failures = PositiveSmallIntegerField(default=0)

class SearchDemand(models.Model):
    phrase = CharField(100, unique=True)    # normalized phrase only
    count = PositiveIntegerField(default=0)
    days_seen = PositiveIntegerField(default=0)
    last_seen = DateField(db_index=True)
```

None of the three has a user, IP, session, user agent or request time finer than a day (`SearchDemand`). `FetchJob` timestamps are needed to run the queue and are deleted after 7 days.

### 7.3 Ingestion changes

- **`ingest_live(result, source, now)`** (new, `catalog/ingest.py`): each `RawOffer` goes through the curated `normalize()` first. When it matches a curated category it is written exactly as scheduled ingestion writes it today. Otherwise it is written as a **generic offer**: `category=NULL`, `attributes={}` apart from `brand_tier` from the brand field, `unit_prices={}`, `unresolved=["category"]`. Both kinds get the generic fields of §7.1.
- **Scheduled ingestion** (`ingest()`) stops dropping offers that match no category (today's `if normalized.category is None: continue`) and writes them as generic offers. A category's search for "milk" that returns oat milk excluded by milk's rules now keeps it as a generic product, so it is searchable.
- **Open Prices daily sync** (`catalog/observed.py`) likewise keeps uncategorized observations as generic observed offers instead of skipping them. Its ODbL boundary is unchanged: these are still Open Prices rows and normalizations of them.
- `generic_measure` and the labels lookup run for every offer, curated or not, in `_upsert`.

## 8. Freshness

- **Live generic offers** use `fresh_offers(now)` unchanged: hidden after `min(UNITPRICER_FRESHNESS_HOURS (24 h), the source's max_cache_age)`. For Walmart that is 24 h. A generic result is never shown older than a curated one would be.
- **Observed generic offers** use `fresh_observed(now)` unchanged: visible while `observed_on` is within `UNITPRICER_OBSERVED_MAX_AGE_DAYS` (default 90). They appear only in the "Seen in stores" section of the generic page, ranked by generic unit price there, labelled "seen at <store> on <date>", never mixed into the live groups, the click endpoint or totals.
- Both freshness queries keep returning only the site currency.

## 9. Demand counting and "curate next"

- **When:** once per generic results page render (not per poll, not on curated redirects, not on the apex host), for a non-personal phrase (§4).
- **What:** `SearchDemand` upsert on `phrase`: `count += 1`; when `last_seen < today` then `days_seen += 1` and `last_seen = today`. One statement (`INSERT … ON CONFLICT … DO UPDATE`). Days are UTC dates.
- **Pruning** (daily, in the worker; thresholds per D15): delete every phrase whose `last_seen` is older than 30 days, whatever its `days_seen` (user ruling 2026-11-08); when more than 50,000 rows remain, delete the rows with the fewest `days_seen`, then oldest `last_seen`, down to 50,000.
- **"Curate next"** (Django admin, read-only): phrases with `days_seen >= 3`, ranked by `days_seen` desc, then `count` desc, then `last_seen` desc. Each row shows the held-result count for the phrase now and its most common store category path, as a hint for which category to write. Phrases seen on fewer than 3 days are never listed, so a one-off search that slipped past the personal-phrase filter does not surface.
- **Not counted:** curated matches (the category already exists), empty queries, and personal-looking phrases. Phrases longer than 100 characters are cut to 100 by normalization and then counted.

## 10. Performance targets

- **Generic page: < 300 ms server time on held data at ~100,000 offers**, measured at p95 over a fixed list of 50 phrases on a local Postgres 17 with synthetic data (`manage.py bench_search`, which seeds 100k offers deterministically and prints p50/p95). This command is run before merge and after index changes. It is not a CI assertion, since CI timing is noisy.
- CI instead pins the shape: `django_assert_max_num_queries` on the generic view (a fixed number of queries regardless of result count), and the poll response with no new job results makes at most 2 queries.
- How it gets there: GIN text match and trigram indexes narrow to ≤ 500 products before any Python work. Grouping, facet counts and near-misses run in Python over that set (≤ a few thousand offers). Generic measures and unit prices are precomputed at ingest, never at request time.
- **Enqueue** adds at most one short transaction. **Pickup** is ≤ 2 s at the p95 when the source has budget.

## 11. Failure modes

| Situation | Behaviour |
|---|---|
| A source is down (transport error, 5xx) | The job fails after its retries; `SourceBudget` backoff applies; other sources' jobs are unaffected. Status line: "<Source> didn't respond; showing saved prices." Held results stay on the page. |
| Quota exhausted or source blocked | No job is enqueued, so there is no poller. Status line: "Live search is busy; showing saved prices." Scheduled refresh waits for tokens. |
| Slow fetch (> 15 s) | Polling stops at 15 s with "Still checking <source>. Reload in a minute for more." The job completes and its results are held for the next visit. |
| Queue full (200 queued) or client throttled | No new job; held results only, with the busy message. |
| Worker down | Jobs are not picked up; polling stops at 15 s; unstarted jobs expire after 120 s; a crashed `running` job is reset once after 5 min. The site keeps serving held data. |
| OFF finds products but Open Prices has no prices for them | Nothing new to show; the job succeeds with `result_count = 0`; OFF labels still go to `OffEntry`. |
| A fetched offer fails to normalize or write | Skipped and counted, as in scheduled ingestion; one bad offer never fails the job. |
| Nothing matches at all | "No saved prices match '<q>'" plus near-miss links and, while polling, "checking <sources>…". After polling stops with nothing found: "Nothing found. Try fewer words or browse a category." |
| Credentials missing (Walmart before Dec 3) | Walmart is absent from `live_sources()`; the page names only the sources that are checking. |

## 12. Privacy

- `SearchDemand` holds only phrase, count, distinct days and last-seen date. `FetchJob` holds phrase, source and queue timestamps, and is deleted after 7 days. Neither has a user, IP, session, user agent or referrer, and no table links a phrase to a visit.
- The per-client job throttle lives only in the cache, under a salted SHA-256 key that expires within a minute (the existing `web.ratelimit` pattern).
- Phrases that look personal (`@`, 5+ digit runs) are neither stored nor sent to any source.
- Phrases are sent to live sources from the server, with the app's User-Agent and no visitor data. The disclosure page says so: "When you search for something we haven't seen, we ask Walmart and Open Food Facts for it. We send only the search words."
- Search and poll requests set no cookies (unchanged M3 behaviour). Worker logs name the job id and source, never the phrase.

## 13. Amendments to the main spec

| Main spec section | Change |
|---|---|
| §3 Principle 5 "Fast by default" | Clarified: no request *waits* on a retailer API. Pages render from held data; new searches fetch in the background and arrive by polling. |
| §4 Core release, item 1 | Search resolves to a curated category when it can, otherwise to generic results (this spec). |
| §4 Non-goals | "Live per-search price fetching" is removed. Background fetching per new phrase, within source terms and rate limits, is in scope. |
| §5 Architecture | The worker gains a fetch-job thread (§6.2). |
| §5.1 Connectors | Optional `LiveSearchSource` capability (§6.1). Rate limits move to shared DB budgets (§6.3). |
| §5.2 Normalizer | New pure `normalize/generic.py` (generic measure, phrase normalization). Unmatched offers are kept as generic offers rather than dropped. |
| §5.3 Catalog | New fields and models of §7. Generic products have `category = NULL` and open no `ReviewItem`. |
| §5.4 Web | The full-text fallback becomes the generic results page (§5), with measure groups, structured facets, near-miss links and polling. |
| §7 Units | Unit-price display is unified (D12): metric per L and per kg, imperial per fl oz and per oz, on every page (§13.1). |
| §8 Lists | A list item is a curated spec or a generic query + target size (§14.1). |
| §11 Data sources | Open Food Facts gains a rate-limited text-search use, off the request path, alongside the dump and deltas. |
| §14 Testing | Adds the tests in §15. |
| §16 Decision log | Adds "Open-ended search (#65): generic tier with measure groups, structured facets, background live fetch, anonymous demand counts; curated categories as upgrades." |

### 13.1 Change to existing category pages (D12)

Curated category pages switch their metric unit-price display from per 100 mL and per 100 g to **per L** and **per kg**. Imperial stays per fl oz and per oz. Stored unit prices (per canonical mL and g) do not change; only the display factors do:

- `catalog/units.py` `DISPLAY`: `("per_volume", "metric")` becomes `("L", 1000)` (was `("100 mL", 100)`), and `("per_mass", "metric")` becomes `("kg", 1000)` (was `("100 g", 100)`). The comparison-unit labels in the category page's switcher follow automatically.
- Generic results use the same table, so a product shows the same unit price on both pages.
- Tests that pin the old labels or values are updated in the same PR: the display tests for `display_unit_price`, and the category-page tests in `tests/web/` that assert "/ 100 mL" or "/ 100 g" text, now assert "/ L" and "/ kg" with values × 10. A new test `test_unit_display_unified` checks that a milk offer shows the same per-L price on its category page and on the generic page.
- Paper, sheet, roll and count units are unchanged.

## 14. Impact on approved plans

### 14.1 M4 Lists and accounts

Open search lands first; M4 then changes as follows. Everything else in the plan stands.

- **`ItemSpec`** gains `kind: Literal["curated", "generic"]`. Curated items are unchanged. Generic items carry `query` (normalized phrase), `facets` (canonical facet params: brand, brand tier, store category path, labels), `target_measure` limited to the generic dimensions (`volume`, `mass`, `oz_labeled`, `count`; a labeled-oz target is entered in oz and fitted only against labeled-oz offers), and `target_amount`. `category` is `None` for generic items. `to_json`/`from_json` round-trip `kind`, and data without `kind` is read as curated (no migration of session data needed).
- **`ListItem`**: `category_slug` becomes nullable; add `kind` (`CharField(10)`, default `curated`), `query` (`CharField(100)`, blank), `facets` (`JSONField`, default `{}`). A check constraint: a curated row has a `category_slug`, a generic row has a `query`.
- **Task 1** adds `generic_matching_offers(query, facets, dimension, stores, now) -> list[Offer]`: the §5.1 match and §5.3 facet filters over fresh live offers, limited to offers whose `measure_dim` equals the item's dimension. It shares code with the generic view so a list item matches what the generic page shows for that group.
- **Task 2 (fit)** uses `measure_amount` for generic offers; whole packages and `MAX_PACKAGES` unchanged.
- **Task 4 (resolve)**: a generic item resolves over `generic_matching_offers`. Reasons are unchanged where they apply ("No current prices match at your stores", "Size unknown for every match"); "This category is no longer available" and "Filter no longer valid" apply to curated items only. An unknown facet key in a stored generic item makes it unresolved ("Filter no longer valid: <key>"), the same never-widen rule as curated filters.
- **Task 5 (add-to-list)**: each measure group on the generic page gets an "Add to list" link carrying `kind=generic`, the phrase, the active facets and the group's dimension; the add form pre-fills the target from the group's top row.
- **Viewing a list** enqueues fetch jobs (`origin = list`) for its generic items' phrases under the normal dedup, budget and throttle rules, but the list page does not poll. New results appear on the next view.
- **Review Focus** gains: "a generic item never resolves to an offer outside its dimension or to an offer matched only by an attribute read from the title" (`test_generic_item_stays_in_dimension`).

### 14.2 Category expansion

- **Task 1 (quantity guards) is kept and moves first**, because the generic measure (§5.2) reuses those guards. It becomes a prerequisite of the open-search implementation.
- The 20 new categories shrink to **top-demand categories**. Until `SearchDemand` has at least 4 weeks of data, only the household batches proceed: Task 2 (`laundry` dimension), Task 3 (`tissue` dimension), Task 8 (facial tissue, trash bags) and Task 9 (laundry detergent, dish soap, dishwasher detergent). These need special units (loads, sheets × ply) that generic mode cannot provide.
- Food batches (Tasks 4–7) wait for "curate next". A food category is curated when its phrases rank in the top 10 of "curate next" **and** it needs something generic mode lacks: a precise attribute (fat %, roast, grain) or a special unit. Mass- and volume-priced foods with no such need may stay generic.
- The exit criterion "25 categories" becomes "the household categories above plus any top-demand categories chosen at review, each with ≥ 8 fixtures".

### 14.3 Independent work

Postal-code radius (#63) and the feedback form (#57) are unaffected and can proceed in parallel. When #63 lands, its radius applies to observed offers on the generic page the same way it applies on category pages.

### 14.4 Roadmap

`docs/roadmap.md` gains an "Open search (#65)" milestone between Checkpoint B and M4, with tasks: generic measure and phrase normalization; schema and backfill; generic results page with facets and near-misses; fetch queue, budgets and worker thread; OFF → Open Prices live source; Walmart live source (when credentials arrive); demand counting and "curate next". It is updated with the implementation plan, not in this spec PR.

### 14.5 Apex host

Unaffected. The apex serves `config/urls_public.py` (waitlist, privacy, health), which has no search route. The generic page, polling and the admin list exist only on the app host. Demand is recorded only from app-host searches. The worker's fetch loop is host-independent.

## 15. Testing

All tests use recorded responses (`connectors/recording.py`) or synthetic rows. No live calls and no keys in CI.

**Required named tests (D7):**
- `test_measure_grouping` (`tests/web/test_generic_search.py`): fl oz, g and ct products fall into separate groups, ordered largest first; a "16.9 fl oz (500 g)" title is "Size unknown" with "more than one kind of size"; no unit price is ever compared across groups.
- `test_bare_oz_own_group` (`tests/normalize/test_generic.py`, `tests/web/test_generic_search.py`): "Great Value 2% Milk, 64 oz" and "Barilla Spaghetti, 16 oz" are both `oz_labeled` with amounts 64 and 16; "Net Wt 16 oz" is still `oz_labeled` (never inferred to weight); "64 fl oz" is volume; "16 oz (454 g)" is "more than one kind of size"; on the page the labeled-oz group is separate from weight and volume and shows "per oz (as labeled)" in both unit systems.
- `test_facet_needs_two_values`: a single brand in the results shows no brand facet; two brands show it; an active facet stays visible with one value; unknown is not counted as a value; a label is offered only when some results have it and some don't.
- `test_rate_limiter` (`tests/catalog/test_budget.py`): a bucket of capacity 10 refilling 10/min grants 10, refuses the 11th with the right wait, grants again after 6 s; `blocked_until` honours `Retry-After`; the daily cap resets at the UTC day boundary; two threads cannot take the same last token (`SELECT … FOR UPDATE`).
- `test_polling_stop_condition` (`tests/web/test_polling.py`): `should_poll` is true while a job is queued or running and `now < start + 15 s`; false when all jobs are terminal, at 15 s, or with a `start` in the future; the final response has no `hx-trigger`.
- `test_generic_never_invents_attribute`: an uncategorized offer titled "Organic Gluten Free Oat Milk" with no OFF labels has `attributes == {}`, no organic or gluten-free label, and does not match `label=en:organic`; with OFF `en:organic` it does. A brand-less "Great Value …" generic offer has no `brand_tier` (no title fallback).

**Also:**
- Normalizer: phrase normalization and the personal-phrase filter; generic measure fixtures (`tests/normalize/generic_fixtures.yaml`, ≥ 30 real titles across food and household, with expected dimension, amount or reason).
- Ingestion: `ingest_live` writes curated matches as curated and the rest as generic; scheduled ingest keeps unmatched offers; no `ReviewItem` for generic products; Walmart never in `PriceHistory`.
- Warm refresh: the top 50 phrases by the curate-next order are enqueued every 12 h with `origin = warm`, personal phrases excluded, `refresh_after` respected; visitor jobs are claimed before warm jobs.
- OFF category path: `categories_tags` `["en:dairies", "en:milks", "fr:laits"]` becomes "Open Food Facts/Dairies/Milks"; the facet never mixes it with Walmart's top-level segments.
- Units: `test_unit_display_unified` and the updated category-page display tests (§13.1).
- Queue: enqueue dedup under concurrency (the partial unique constraint); `refresh_after` skip; expiry at 120 s; stuck-job reset; `SKIP LOCKED` pickup with two threads.
- Sources: Walmart `live_search` against existing recordings plus one with `categoryPath`; OFF search and Open Prices `product_code__in` against new recordings, including an OFF 503 then success.
- Web: curated queries still redirect (existing tests unchanged); near-miss links for a held-result category and for a typo; Seen-in-stores section for generic observed offers with the ODbL notice; freshness (a 25 h Walmart generic offer is hidden); search and poll set no cookies; the apex host has no `/search`.
- Demand: counting once per render and never per poll; `days_seen` increments once per day; pruning thresholds; the admin list hides phrases under 3 days.
- `django_assert_max_num_queries` bounds on the generic view and idle polls (§10).

## 16. Open questions

None remain. The eight questions raised in the first draft were answered by the maintainer on 2026-11-08 and are recorded as D8–D15 in §2:

| Question | Decision |
|---|---|
| Bare "oz" in generic mode | D8: own group "per oz (as labeled)", never converted or inferred |
| Is the live OFF → Open Prices path worth it? | D9: behind a setting, off by default; the daily sync is primary |
| OFF text-search endpoint and limit | D10: default ~10 req/min as documented; measure during implementation |
| Walmart limits | D11: 1 req/s and 5,000/day until real credentials |
| Metric volume display | D12: unified per L / per kg and per fl oz / per oz everywhere |
| OFF category tree as a category path | D13: yes, for OFF and Open Prices rows |
| Warm refresh of top-demand phrases | D14: top 50 every 12 h within budgets |
| Demand thresholds | D15: listed after 3 distinct days, pruned 30 days after last seen |
