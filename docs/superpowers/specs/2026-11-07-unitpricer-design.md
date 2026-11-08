# UnitPricer — Design Spec

- **Date:** 2026-11-07
- **Status:** Draft, awaiting review
- **Name:** **UnitPricer** (`unitpricer.com`), renamed from the codename `shopwit` after M2 (#26)

## 1. Summary

UnitPricer is a free, open-source web app for finding the lowest **price per unit** on groceries and household goods. Its core difference from existing tools is that it compares **every product matching a set of attributes** ("any 2% milk", "any 2-ply toilet paper") rather than the same UPC across stores, and ranks them by a fair, normalized unit price.

Revenue comes mainly from affiliate links. Ads, with a paid ad-free tier, are deferred. Users are never charged for core functionality.

## 2. Problem and competitive context

Existing tools compare the *same product* across stores:

| Product | Approach | Gaps relevant to UnitPricer |
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
- Price history, a "good price?" indicator, and email alerts on a spec. **Walmart data is excluded** (Walmart's terms bar price tracking and alerting); other sources only where their terms allow.
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
   Walmart,           source, shared         category match,     Offer, PriceHistory,      lists, direct links,
   Sovrn, …)          interface)             attributes, units)  ReviewItem)               admin review)
                         ▲
                 scheduled worker (≈ every 12 h per category per connector)
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
- `Product`: identity is the UPC when present, otherwise retailer + item ID. Holds the category, brand, `attributes` (JSONB with per-attribute provenance), and normalized quantity. `attributes` gets a GIN index (`jsonb_path_ops`) for containment filters; per-attribute expression indexes are added only if query plans show a need (M5 performance pass).
- `Offer`: product × retailer. Holds price, currency, computed unit price in base units, URL, and last-seen time. Offers older than the freshness cutoff (default **24 h**, configurable, never above any source's terms limit) are hidden. Sources refresh about every 12 h so normal operation stays well inside the cutoff.
- Observed offers (`Offer.kind="observed"`, #61): dated shelf prices from Open Prices, one per store and UPC (the latest observation wins), stored with `observed_on`, `is_sale` and a `Store` (one per OSM location, grouped by chain for the store filter as `op-<chain slug>`). They are shown only in the "Seen in stores" section of category pages, ranked by unit price there, and never enter the live ranking, search, lists or click tracking. They stay visible while `observed_on` is within `UNITPRICER_OBSERVED_MAX_AGE_DAYS` (default **90**); the 24 h cutoff applies to live offers only. The raw rows live in `OpenPriceObservation`. Both freshness queries return only the site currency (`UNITPRICER_CURRENCY`, default USD).
- `PriceHistory`: an append-only record of price observations (used by later features). Only sources whose terms allow price tracking are recorded; **Walmart offers are never written here**.
- Amazon product images are never stored; only the ASIN is kept, and images load directly from Amazon.
- `ReviewItem`: unresolved categories or attributes awaiting manual review.
- `List`, `ListItem` (spec = category + filters + target quantity, optional pinned product), plus user `Settings`.

### 5.4 Web
- Category pages: a filter sidebar, results ranked by unit price, and URL state (e.g. `/milk?fat=2&organic=any&sort=unit`).
- The search box resolves queries to a category and filters using category synonyms; when nothing resolves, it falls back to full-text product search.
- HTMX handles partial updates when filters change. Pages are server-rendered and indexable.
- **Every price shows "price as of <time>"** next to it, plus the Amazon disclaimer on Amazon offers ("Product prices and availability are accurate as of the date/time indicated and are subject to change.").
- **Outbound links go directly to the retailer** (or to the affiliate network's own tracking link, e.g. `sovrn.co`). There is no UnitPricer redirect, intermediary page or link shortener: Amazon disqualifies links via an intermediate site, and Walmart bans shortened or obscured links and intermediate pages. Affiliate URLs are built at render time from the offer and `.env` config, so changing a tag is a config change. Clicks are counted with the HTML `ping` attribute / `navigator.sendBeacon` to a small endpoint that stores no personal data.
- Django admin: the review queue, manual attribute corrections, and a category overview.

## 6. Category definitions

Each category is a YAML file in `categories/` (e.g. `categories/household/toilet-paper.yaml`). Contributors add or extend categories through pull requests.

```yaml
name: Toilet Paper
parent: household/paper-goods
match:
  include: ["toilet paper", "bath tissue", "bathroom tissue"]
  exclude: ["wipes", "flushable wipe", "holder"]
dimension: paper          # quantity parser yields rolls, sheets, area
attributes:
  ply: {type: int, values: [1, 2, 3], patterns: ['(?<![\d.])([123])[- ]?ply']}
comparison:
  default: area_x_ply
  alternates: [per_100_sheets, per_roll]
```

**Rules**
- Each category declares a `dimension` (`volume`, `mass`, `count` or `paper`) that tells the quantity parser what to extract. Paper goods yield rolls, total sheets and total area.
- An optional `search` list gives the retailer search queries ingestion runs for the category (default: the category name).
- `brand_tier` (store brand vs. name brand) is a built-in attribute for every category. It is derived from a shared `data/store_brands.yaml` list.
- Each category declares a default comparison unit plus alternates the user can switch to:
  - milk: per volume
  - eggs: per egg
  - detergent: per load, falling back to per volume
- Marketing equivalences such as "Mega roll = 4 regular" are ignored. Real quantities (sheets × sheet area) are used instead.
- When a filter is active, products with an unknown value for that attribute are excluded; otherwise they are included. Either way they are labeled.
- Every category ships with a fixtures file (`tests/categories/<name>.yaml`) of real titles and their expected attributes and unit prices. A category without fixtures is not accepted.

The initial catalog covers about 25 high-frequency categories (e.g. milk, eggs, butter, bread, coffee, rice, pasta, toilet paper, paper towels, facial tissue, laundry detergent, dish soap, trash bags, protein powder).

## 7. Units, currency, locale

- Quantities and unit prices are stored in canonical metric units (mL, g, count, m²). Display converts them to the user's **metric or imperial** setting; the default comes from the browser locale and can be changed anytime.
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
- An affiliate disclosure is shown above the fold on every page with affiliate links, and next to outbound links. Amazon pages also carry "As an Amazon Associate I earn from qualifying purchases."
- Program terms constrain the design (see `docs/research/2026-11-07-sovrn-api.md` §6): data cached at most 24 h, timestamped prices, direct links, no Walmart price tracking, no stored Amazon images. Amazon also restricts analyzing or repurposing its product data without approval, so written approval for unit-price comparison is requested once UnitPricer is an Associate. Terms for Target, Instacart, Costco and Sovrn are not public and get reviewed on approval.
- Commission never affects ranking (Principle 2).
- Keys and IDs (Sovrn key and secret, Associates tag, etc.) live only in `.env`.

## 11. Data sources and risks

The core release uses **official sources only**.

| Source | Status | Risk to verify first |
|---|---|---|
| Amazon Creators API (successor to PA-API 5, retired May 2026) | Official | Requires an Associates account with 10 qualifying sales in the past 30 days. Unavailable until the live site has traffic. |
| Walmart affiliate API | Official — **first connector (Checkpoint A)** | Key upload on walmart.io currently fails (503); built against documented sample responses until it works. National, not per-store, pricing. |
| Sovrn Commerce | Official (affiliate) — **link router only** | Spike (#17): product search returns poor grocery results with no UPC/size/brand fields, so it is not a data source. Its network includes Walmart, Target, Amazon, Costco, Instacart, Stop & Shop, Shaw's; affiliability to retest with UnitPricer's own site key. |
| Open Food Facts / Open Products Facts | Open data (ODbL) | Enrichment only (#18): seed from dump, daily deltas filtered to known UPCs; household items live in Open Products Facts. `en:organic` is positive-only. Whether a merged attribute table is a derived database needs a legal check. |
| Open Prices (Open Food Facts) | Open data (ODbL), US/USD only, #61 | Crowdsourced dated shelf prices, shown as "seen at <store> on <date>" in their own section, never as live prices. Pulled daily (full 90-day window, rows missing from a successful pull are deleted). Data stays in its own tables, with the ODbL notice under the section and on the disclosure page. Under ODbL 4.6 we offer the method (our AGPL source) and a JSONL export (`export_open_prices`) of the observations plus each observed offer's own normalization, never retailer-sourced values; a public dump URL waits for the public launch. 52% of US rows are AI-assisted entries confirmed by a person (`UNITPRICER_OPEN_PRICES_ASSISTED=0` drops them). Legal check before public launch. |

Costco, Shaw's, Stop & Shop and Target have no official price APIs. They are out of scope until the decision checkpoint in §4.

## 12. Hosting

- Local development: Docker Compose (web, worker, Postgres).
- Production: a DigitalOcean droplet running Docker Compose (web, worker, Postgres, backup) behind a Cloudflare Tunnel; no published ports. The apex serves a public waitlist; the app host is a gated beta behind Cloudflare Access. Runbook: `docs/deploy.md`.

## 13. Error handling and observability

- Connector failures are isolated per retailer. Stale offers age out through the freshness cutoff.
- A parse failure becomes a `ReviewItem` and never fails the job.
- Each refresh run logs counts per connector and category (fetched, matched, fully parsed, unresolved), so a retailer changing its title formats shows up as a drop in the parse rate.

## 14. Testing

- Normalizer: unit tests plus category fixture tests (real titles → expected attributes and unit prices). This is the highest-coverage area.
- Connectors: tested against recorded API responses. No live calls and no keys in CI.
- Web: Django tests for search resolution, filter and URL round-trips, ranking order, unknown handling, list resolution, direct affiliate-link construction, "price as of" timestamps, and the click-ping endpoint.
- The repo's pre-commit hooks (gitleaks, pytest) and CI stay green.

## 15. Open decisions

1. ~~Final product name and domain~~ Decided: UnitPricer, unitpricer.com.
2. ~~Production hosting~~ Decided: DigitalOcean droplet + Docker Compose + Cloudflare Tunnel.
3. Unofficial data sources for in-store chains (after the core release).
4. Whether Amazon and Walmart honor links routed through Sovrn's tracking redirect (ask Sovrn at Checkpoint B).

## 16. Decision log

| Decision | Choice |
|---|---|
| Build it? | Yes, with a narrow official-data first version |
| Platform | Responsive web app |
| Stack | Django + HTMX + Postgres + worker (chosen on merit: ingestion fit, admin for curation, SEO) |
| Categorization | Curated YAML categories with typed attributes; rules-based extraction; **never AI** |
| Data sourcing | Official APIs first; unofficial sources decided later |
| Prices | Cached and refreshed about every 12 h (24 h cutoff), not live per search; "price as of" shown on every price |
| Revenue | Affiliate primary (Sovrn plus direct programs); ads later |
| Outbound links | Direct to retailer or network tracking link; no redirect, shortener or intermediate page; clicks counted by ping/beacon |
| First connector (Checkpoint A) | Walmart Affiliate API, built against documented sample responses until walmart.io access works; Sovrn as link router |
| Units | Metric/imperial user setting; locale-neutral storage |
| Hosting | DigitalOcean droplet + Docker Compose + Cloudflare Tunnel; gated beta, public waitlist |
| Name | **UnitPricer** (unitpricer.com); renamed from codename `shopwit` (#26) |
| In-store observed prices (#61) | Open Prices rows become dated `observed` offers in a separate "Seen in stores" section, 90-day window by observation date, chain-grouped store filter, ODbL attribution and export; ranking and live surfaces are untouched |
| Open-ended search (#65) | Generic tier with measure groups, structured facets, background live fetch, anonymous demand counts; curated categories as upgrades. See `2026-11-08-open-search-design.md`. |
