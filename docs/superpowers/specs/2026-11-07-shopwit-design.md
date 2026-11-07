# shopwit — Design Spec

- **Date:** 2026-11-07
- **Status:** Draft, awaiting review
- **Codename:** `shopwit` (working name; final name and domain are a pre-launch task)

## 1. Summary

shopwit is a free, open-source web app for finding the lowest **price per unit** on groceries and household goods. Its core difference from existing tools is that it compares **every product matching a set of attributes** ("any 2% milk", "any 2-ply toilet paper") rather than the same UPC across stores, and ranks them by a fair, normalized unit price.

Revenue comes mainly from affiliate links. Ads, with a paid ad-free tier, are deferred. Users are never charged for core functionality.

## 2. Problem and competitive context

Existing tools compare the *same product* across stores:

| Product | Approach | Gaps relevant to shopwit |
|---|---|---|
| GroceryChop | UPC match, then fuzzy match; 100+ chains; live per-search scraping | A "2% milk" search returned 275 per-product groups, including whole milk. There is no sort by unit price and no attribute filters (only SNAP, deals, multi-store, store). Free searches take 2–4 min; instant results are Pro at $6.99/mo or $39.99/yr. Prices are shown as "<1h–4mo old". |
| Grocery Dealz | Third-party price data; cart handoff to the grocer | Built around lists and carts; regional (DFW) |
| Basket | Crowdsourced prices; list totals per store | Spotty coverage |
| Flipp | Flyer aggregator | Sale prices only |
| Groceries Tracker | Receipt-scan price history | Only items you already bought |

Search queries like "cheapest toilet paper per sheet" are answered today by static, often outdated articles. Live, data-backed category pages are an SEO opportunity.

## 3. Principles

1. **Compare by spec, not by SKU.** Brand is an optional filter, never a requirement.
2. **Honest ranking.** Ranking depends only on unit price and the user's filters, never on affiliate commission.
3. **Never guess.** Unknown attributes are shown as unknown. Products whose unit price can't be computed are listed but unranked, with the reason.
4. **No AI anywhere.** No LLMs in the product or the ingestion pipeline. Attribute extraction is deterministic.
5. **Fast by default.** Pages are served from a cached catalog. No user-facing request waits on a retailer API.
6. **Free core.** Charging is reserved for a later ad-free tier or for pass-through costs that are genuinely needed.
7. **Locale-neutral.** No hard-coded currency or unit system.

## 4. Scope

### Core release
1. **Category-resolving search.** A query such as "2% milk" resolves to category *Milk* with `fat=2%` pre-applied.
2. **Attribute filters** defined per category (e.g. fat %, organic, ply, size range, brand or store brand), with all state in the URL.
3. **Unit-price ranking** with switchable comparison units, plus labels for unknown values and price age.
4. **Store selector** (the stores and memberships a user has) and **affiliate outbound links**.
5. **Spec-based lists**, with per-store totals and the cheapest one-store vs. split-store options.
6. **Optional accounts** for syncing lists, stores and settings across devices.

### Next (first after core)
7. **Nutrition per dollar** (e.g. cost per 10 g protein) using Open Food Facts. Aimed at fitness shoppers, for example protein powder.

### Later
- Price history, a "good price?" indicator, and email alerts on a spec.
- Effective unit price (Subscribe & Save, shipping thresholds, membership cost).
- Community corrections, and form-based shelf-price submissions for in-store chains (no AI).
- **Decision checkpoint:** whether to use unofficial sources (traced network APIs, HTML scraping) for in-store chains.
- Ads with a paid ad-free tier; PWA; browser extension; ZIP-level pricing; international.

### Non-goals (core release)
Live per-search price fetching, in-store-only chains without an official data source, native mobile apps, and any AI or LLM features.

## 5. Architecture

Stack: **Django + HTMX + Postgres**, with a background worker for ingestion. Developed and hosted locally during development; deployment options (VPS, Vercel, etc.) are an open decision (see §12).

```
 Retailer APIs ──► Connectors ──RawOffer──► Normalizer ──► Catalog (Postgres) ──► Web (Django+HTMX)
  (Amazon,           (one per               (pure functions:    (Category, Product,      (search, filters,
   Walmart,           source, shared         category match,     Offer, PriceHistory,      lists, /go/ links,
   Sovrn, …)          interface)             attributes, units)  ReviewItem)               admin review)
                         ▲
                 scheduled worker (≈ daily per category per connector)
```

### 5.1 Connectors
- Interface: `search(query) -> list[RawOffer]`, `fetch(ids) -> list[RawOffer]`.
- `RawOffer`: retailer, retailer item ID, UPC (optional), title, brand, price, currency, size fields as returned, URL, fetched-at time.
- Credentials are read from environment variables (`.env`, gitignored) and documented in `.env.example`.
- Rate limits and failures are handled per connector. One retailer failing never blocks the others.
- Initial connectors depend on the access verification in §11 (Amazon Creators API, Walmart affiliate API, Sovrn).

### 5.2 Normalizer (pure, no I/O)
For each RawOffer:
1. **Category match** using the category file's include/exclude keywords against title and brand.
2. **Attribute extraction**, in priority order:
   1. structured retailer API fields
   2. Open Food Facts lookup by UPC (categories, quantity, labels such as organic)
   3. rule parsers: the category's regex patterns and synonyms, with optional per-category parser modules for complex cases
   4. unresolved → stored as unknown, and a `ReviewItem` is created
3. **Units:** quantities are parsed and converted with `pint` to base units. The comparison unit price is then computed per the category's rules.
4. **Provenance:** every attribute records its source (`api` / `off` / `rule` / `manual`).

Manual corrections from the admin are stored and override extraction on later refreshes.

### 5.3 Catalog (Postgres)
- `Category`, `AttributeDef`: loaded from the category YAML files; the files are the source of truth.
- `Product`: identity is the UPC when present, otherwise retailer + item ID. Holds the category, brand, `attributes` (JSONB with per-attribute provenance), and normalized quantity. Filtered attributes get expression indexes.
- `Offer`: product × retailer. Holds price, currency, computed unit price in base units, URL, and last-seen time. Offers older than the freshness cutoff (default 72 h, configurable) are hidden.
- `PriceHistory`: an append-only record of price observations (used by later features).
- `ReviewItem`: unresolved categories or attributes awaiting manual review.
- `List`, `ListItem` (spec = category + filters + target quantity, optional pinned product), plus user `Settings`.

### 5.4 Web
- Category pages: a filter sidebar, results ranked by unit price, and URL state (e.g. `/milk?fat=2&organic=any&sort=unit`).
- The search box resolves queries to a category and filters using category synonyms; when nothing resolves, it falls back to full-text product search.
- HTMX handles partial updates when filters change. Pages are server-rendered and indexable.
- `/go/<offer_id>`: logs the click without personal data, applies the affiliate tag, and redirects.
- Django admin: the review queue, manual attribute corrections, and a category overview.

## 6. Category definitions

Each category is a YAML file in `categories/` (e.g. `categories/household/toilet-paper.yaml`). Contributors add or extend categories through pull requests.

```yaml
name: Toilet Paper
parent: household/paper-goods
match:
  include: ["toilet paper", "bath tissue", "bathroom tissue"]
  exclude: ["wipes", "flushable wipe", "holder"]
attributes:
  ply:             {type: int, values: [1, 2, 3], patterns: ['(\d)[- ]?ply']}
  sheets_per_roll: {type: int, patterns: ['(\d+)\s*sheets?\s*(per|/)\s*roll']}
  sheet_area:      {type: area, derive: sheet_dimensions}
  rolls:           {type: int, patterns: ['(\d+)\s*(mega|double|family)?\s*rolls?']}
  brand_tier:      {type: enum, values: [name_brand, store_brand], source: brand}
comparison:
  default: area_x_ply
  alternates: [per_100_sheets, per_roll]
```

**Rules**
- Each category declares a default comparison unit plus alternates the user can switch to:
  - milk: per volume
  - eggs: per egg
  - detergent: per load, falling back to per volume
- Marketing equivalences such as "Mega roll = 4 regular" are ignored. Real quantities (sheets × sheet area) are used instead.
- When a filter is active, products with an unknown value for that attribute are excluded; otherwise they are included. Either way they are labeled.
- Every category ships with a fixtures file (`tests/categories/<name>.yaml`) of real titles and their expected attributes and unit prices. A category without fixtures is not accepted.

The initial catalog covers about 25 high-frequency categories (e.g. milk, eggs, butter, bread, coffee, rice, pasta, toilet paper, paper towels, facial tissue, laundry detergent, dish soap, trash bags, protein powder).

## 7. Units, currency, locale

- Quantities and unit prices are stored in base SI units. Display converts them to the user's **metric or imperial** setting; the default comes from the browser locale and can be changed anytime.
- Currency is stored per offer and never assumed. The core release is US-only in data coverage but not in code.

## 8. Lists

- A list item is a spec: category, filters, and target quantity, with an optional pinned product.
- The view resolves each item to the cheapest qualifying fresh offer at the user's selected stores. It shows per-store totals, the cheapest single store, and the cheapest split, with a quantity fit (e.g. 2 × 64 oz vs. 1 × 128 oz).
- Anonymous lists live in the session or browser. Signing in saves them to the account.

## 9. Accounts and settings

- Optional. Django auth with email and password, plus magic-link sign-in.
- Settings: unit system, selected stores and memberships, default comparison units per category.
- Anonymous use has every feature except cross-device sync.

## 10. Revenue: affiliate links

- Affiliate is the primary revenue source. Ads are deferred to a later phase.
- Routing:
  - Amazon uses its own Associates program, since sub-affiliate networks generally can't monetize it.
  - Other retailers go through **Sovrn** where it covers them, otherwise through the retailer's direct program (e.g. Walmart or Target via Impact).
- An affiliate disclosure is shown site-wide and next to outbound links.
- Commission never affects ranking (Principle 2).
- Keys and IDs (Sovrn key and secret, Associates tag, etc.) live only in `.env`.

## 11. Data sources and risks

The core release uses **official sources only**.

| Source | Status | Risk to verify first |
|---|---|---|
| Amazon Creators API (successor to PA-API 5, retired May 2026) | Official | Access may require qualifying Associates sales. That can create a chicken-and-egg problem at launch. |
| Walmart affiliate API | Official | Whether it's still open to new developers; its rate limits; national (not per-store) pricing |
| Sovrn Price Comparison API | Official (affiliate) | Whether it covers grocery and household merchants, and whether it returns the size data needed for unit prices |
| Open Food Facts | Open data (ODbL) | Enrichment only. A redistributed derived database must be shared under ODbL. |

Costco, Shaw's, Stop & Shop and Target have no official price APIs. They are out of scope until the decision checkpoint in §4.

## 12. Hosting

- Local development: Docker Compose (web, worker, Postgres).
- Production hosting is **undecided**. Options (VPS, Vercel, a PaaS, etc.) will be compared before the first deploy.

## 13. Error handling and observability

- Connector failures are isolated per retailer. Stale offers age out through the freshness cutoff.
- A parse failure becomes a `ReviewItem` and never fails the job.
- Each refresh run logs counts per connector and category (fetched, matched, fully parsed, unresolved), so a retailer changing its title formats shows up as a drop in the parse rate.

## 14. Testing

- Normalizer: unit tests plus category fixture tests (real titles → expected attributes and unit prices). This is the highest-coverage area.
- Connectors: tested against recorded API responses. No live calls and no keys in CI.
- Web: Django tests for search resolution, filter and URL round-trips, ranking order, unknown handling, list resolution, and `/go/` redirects.
- The repo's pre-commit hooks (gitleaks, pytest) and CI stay green.

## 15. Open decisions

1. Final product name and domain (pre-launch). Shortlist so far: CheapestPer, FairPer, LeastPer, UnitSavvy, BasketSage, ShopWit, ShopKeen.
2. Production hosting (before first deploy).
3. Whether the GitHub repo is created public from the start (it will be open source).
4. Unofficial data sources for in-store chains (after the core release).

## 16. Decision log

| Decision | Choice |
|---|---|
| Build it? | Yes, with a narrow official-data first version |
| Platform | Responsive web app |
| Stack | Django + HTMX + Postgres + worker (chosen on merit: ingestion fit, admin for curation, SEO) |
| Categorization | Curated YAML categories with typed attributes; rules-based extraction; **never AI** |
| Data sourcing | Official APIs first; unofficial sources decided later |
| Prices | Cached and refreshed on a schedule, not live per search |
| Revenue | Affiliate primary (Sovrn plus direct programs); ads later |
| Units | Metric/imperial user setting; locale-neutral storage |
| Name | Codename `shopwit` for now |
