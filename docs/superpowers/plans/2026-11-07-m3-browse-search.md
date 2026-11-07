# M3: Browse and Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The first usable UnitPricer: search "2% milk", land on Milk with fat = 2% applied, and see fresh offers ranked by unit price in your unit system, at your selected stores, with compliant affiliate links. It has to work on a phone in the aisle.

**Architecture:** A new Django app, `web/`, holds the views, templates and static files. Query logic lives in plain modules under `catalog/`: `browse.py` (filters and ranking), `units.py` (display conversion), `links.py` (affiliate URLs) and `retailers.py`. Free-text resolution lives in the pure package as `normalize/query.py`, reusing M1's matching and extraction rules. Pages are server-rendered, and all filter state is in the URL. HTMX (vendored, no CDN) swaps the results list when filters change. Nothing in the request path calls a retailer or Open Food Facts.

**Tech Stack:** Django 5.2 templates, HTMX 2.x (vendored static file), hand-written mobile-first CSS (no CSS framework), Postgres JSONB containment queries, pytest-django.

**Spec:** `docs/superpowers/specs/2026-11-07-unitpricer-design.md`: §3 Principles, §4 core items 1–4, §5.4 Web, §6 rules on unknowns, §7 Units, §10 Revenue/terms. Terms research: `docs/research/2026-11-07-sovrn-api.md` §6. Roadmap tasks 3.1–3.7.

## Global Constraints

- Python with `uv` only. No AI/LLM libraries or calls anywhere.
- `normalize/` stays pure: no Django, no network.
- **Never guess (Principle 3).** When an attribute filter is active, a product whose value for it is unknown is **excluded**. When it isn't active, the product is included and the unknown is **labelled** ("ply unknown"). A product without a unit price for the selected comparison goes in a separate **"Can't rank"** list with the reason, never into the ranking.
- **Honest ranking (Principle 2):** the order depends only on unit price (ties broken by price, then product title, then retailer). Affiliate configuration never affects order or inclusion.
- **Freshness:** only offers from `catalog.freshness.fresh_offers(now)` are shown. Stale offers are invisible.
- **Program terms (spec §5.4, §10):**
  - Every shown price has "price as of <time>" next to it.
  - Outbound links go **directly** to the retailer, or to the network's own tracking URL: no shopwit redirect, no intermediate page, no shortener.
  - Affiliate URLs are built at render time and never stored.
  - The affiliate disclosure appears above the fold on every page that shows outbound links.
  - Amazon offers (none exist yet) get the Amazon disclaimer and disclosure sentence, and no stored images.
- **Click counting stores no personal data.** It records the offer id and a timestamp only: no IP, user agent, cookie id or referrer. It uses the `ping` attribute plus a `navigator.sendBeacon` fallback to `POST /click`.
- **No third-party requests from pages.** No CDN, web fonts or analytics; HTMX is served from `web/static/`.
- Money stays `Decimal` until formatting. Unit prices are stored as 6-dp strings (M2) and parsed with `Decimal`.
- Tests never touch the network. Each task ends with `uv run pytest -q` green and the pre-commit hooks passing.

## Review Focus

1. **Unknown values under an active filter.** With `fat=2` active, a milk product with no known fat is excluded. With no fat filter, it appears with "fat unknown". Owned by Task 2.
2. **Stale offers never appear,** even when the same product has a fresh offer elsewhere. Only the fresh offer is shown, and a product with no fresh offers disappears. Owned by Task 2.
3. **Search must not invent filters from "absent" defaults.**
   - "milk" → `dairy/milk` with **no** filters. `organic=false` must not appear, even though organic's `absent: false` would apply to a title.
   - "organic 2% milk" → `fat=2&organic=true`.
   - Owned by Task 4.
4. **Affiliate URLs encode correctly and degrade safely.** A canonical URL containing `?`, `&` or `#` is percent-encoded inside the wrapper. With no affiliate configuration, the link is the plain canonical URL, never a broken or empty link. Owned by Task 6.
5. **Unit display is exact and never shows $0.00 for a non-zero price.** $0.002350/mL displays as $0.0695/fl oz (imperial) and $0.235/100 mL (metric). Tiny unit prices keep enough significant digits. Owned by Task 3.

---

## File Structure

```
config/settings.py, config/urls.py        # add "web" app, include web.urls, context processor
normalize/query.py                        # resolve_query() — pure
catalog/
  browse.py                               # FilterSpec, parse_filters(), browse()
  units.py                                # display conversion + formatting
  links.py                                # outbound_url(), disclosure rules
  retailers.py                            # RETAILERS display names, available_retailers()
  models.py (+ migration)                 # ClickEvent
web/
  __init__.py  apps.py  urls.py  views.py  prefs.py  context_processors.py
  templates/web/  base.html  home.html  category.html  _results.html  search_results.html  settings.html  disclosure.html
  static/web/  app.css  app.js  htmx.min.js  (+ HTMX LICENSE)
tests/web/  test_query.py  test_browse.py  test_units.py  test_links.py  test_views_*.py
```

---

### Task 1: Web app skeleton, base layout, preferences (roadmap 3.7 foundation)

**Files:**
- Create: `web/` (`apps.py`, `urls.py`, `views.py` home view, `prefs.py`, `context_processors.py`), `web/templates/web/base.html`, `home.html`, `web/static/web/app.css`, `app.js`, `htmx.min.js` + `HTMX-LICENSE`, `tests/web/test_views_home.py`, `tests/web/test_prefs.py`
- Modify: `config/settings.py` (add `"web"`, the context processor, `SESSION_COOKIE_SAMESITE="Lax"`), `config/urls.py` (`path("", include("web.urls"))`)

**Interfaces:**
- `web/prefs.py`:
  - `@dataclass(frozen=True) Prefs(unit_system: Literal["imperial","metric"], stores: frozenset[str] | None)`. `stores=None` means all retailers.
  - `get_prefs(request) -> Prefs` reads `request.session["prefs"]`. When unset, `unit_system` defaults from `Accept-Language`: **imperial** when the first language tag's region is `US`, `LR` or `MM`; otherwise **metric**.
  - `save_prefs(request, prefs) -> None`
- Context processor `web.context_processors.prefs` exposes `prefs` and `active_categories` (active `Category` rows ordered by name) to every template.
- `base.html` contains:
  - `<meta name="viewport" content="width=device-width, initial-scale=1">`
  - a header with the site name "UnitPricer" and a search form (`GET /search`, input `name="q"`, `type="search"`, `aria-label="Search products"`)
  - a main block, and a footer with a link to `/disclosure`
  - `app.css`, `htmx.min.js` and `app.js` loaded from static. **No external URLs.**
- `home.html` shows the search box prominently and a list of active categories linking to `/c/<slug>/`.

- [ ] **Step 1: Write failing tests:**
  - `test_home_renders_search_and_categories`: after `sync_categories`, GET `/` returns 200, has the search form and links to `/c/dairy/milk/`
  - `test_no_external_asset_urls`: the rendered `/` has no `http://` or `https://` in `src=` or `href=`, except outbound links, which home has none of
  - `test_default_unit_system_from_accept_language`: `en-US` → imperial; `en-GB` → metric; `de-DE` → metric; a missing header → metric
  - `test_prefs_round_trip_in_session`
- [ ] **Step 2: Run** `uv run pytest tests/web -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Download HTMX 2.x `htmx.min.js` once from the official release, at a pinned version recorded in a comment at the top of `app.js`, and commit it with its BSD license file. CSS is mobile-first, with a `@media (min-width: 48rem)` layout for desktop, readable 16px base type, ≥ 44px tap targets and `prefers-color-scheme` dark support.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: web app skeleton and preferences`

---

### Task 2: Filters and ranking (roadmap 3.1–3.2 logic)

**Files:**
- Create: `catalog/browse.py`, `catalog/retailers.py`, `tests/web/test_browse.py`

**Interfaces:**
- `catalog/retailers.py`: `RETAILERS: dict[str, str] = {"walmart": "Walmart", "amazon": "Amazon", "target": "Target", "costco": "Costco", "shaws": "Shaw's", "stopandshop": "Stop & Shop"}`; `display_name(code) -> str` falls back to `code.title()`; `available_retailers(now) -> list[str]` returns the distinct retailers with fresh offers, sorted by display name.
- `@dataclass(frozen=True) FilterSpec(attributes: Mapping[str, object], size_min: float | None, size_max: float | None, brand_tier: str | None)`, where `attributes` maps an attribute name to a required value.
- `parse_filters(category: Category, params: Mapping[str, str]) -> FilterSpec`. Each `AttributeDef` name is a GET param:
  - enum: a value in `values`
  - bool: `"true"`/`"false"`
  - int: digits within `values` when declared
  - `"any"` or absent means not filtered
  - invalid values are ignored, so a hand-edited URL never errors

  `brand_tier` accepts `name_brand`/`store_brand`. `parse_filters` reads `size_min`/`size_max` as already-canonical numbers (mL, g, count or m²). The category view (Task 5) converts the user's display-unit inputs with `catalog.units.size_to_canonical` (Task 3) before calling it, so Task 2 has no dependency on Task 3.
- `@dataclass(frozen=True) Row(product: Product, offer: Offer, unit_price: Decimal | None, unknown: tuple[str, ...], reason: str | None)`
- `@dataclass(frozen=True) BrowseResult(ranked: list[Row], unranked: list[Row], comparison: str, total_products: int)`
- `browse(category, spec: FilterSpec, comparison: str | None, stores: frozenset[str] | None, now: datetime, limit: int = 50) -> BrowseResult`:
  1. Take `fresh_offers(now)` restricted to the category's products, plus `stores` when not None.
  2. Apply each attribute filter as `Product.attributes__contains={name: {"value": v}}`, which uses the GIN index. Products missing the attribute are excluded automatically.
  3. Apply `brand_tier` the same way.
  4. Apply the size range against the offer's `measures[canonical key]`.
  5. Per product, keep its **cheapest fresh offer by unit price** for `comparison`, defaulting to `category.comparison_default`; a comparison not in default + alternates falls back to the default. Products whose offers have no unit price for it go to `unranked` with `reason`: the offer's `unit_price:<comparison>` or `quantity` unresolved entry made human-readable ("size unknown", "ply unknown"), else "can't compute price per <unit>".
  6. Sort `ranked` by `(unit_price, price, product.title, retailer)`.
  7. Fill `unknown` with the category's attribute names that are *not* filtered and absent from `product.attributes`, for labels.

- [ ] **Step 1: Write failing tests** (use the `tests/catalog/helpers.py` FakeConnector + `ingest` to build real data):
  - `test_rank_by_unit_price`: three milks at different unit prices, ranked ascending
  - `test_unknown_excluded_when_filtered_and_labelled_otherwise` (Review Focus 1)
  - `test_stale_offers_never_shown` (Review Focus 2): a product with one stale and one fresh offer shows only the fresh one; a product with only stale offers is absent
  - `test_unranked_lists_reason`: a milk with no size goes in unranked with "size unknown"
  - `test_store_filter`
  - `test_alternate_comparison_and_invalid_falls_back`: toilet paper with `per_roll` vs the default `area_x_ply`
  - `test_invalid_params_ignored`: `fat=banana` gives no filter
  - `test_ties_break_deterministically`
  - `test_brand_tier_filter`
- [ ] **Step 2: Run** `uv run pytest tests/web/test_browse.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: filters and unit-price ranking`

---

### Task 3: Units and display formatting (roadmap 3.5)

**Files:**
- Create: `catalog/units.py`, `tests/web/test_units.py`

**Interfaces:**
- `DISPLAY: dict[tuple[str, str], tuple[str, Decimal]]` maps `(comparison, unit_system)` to `(label, factor)`, where `factor` converts a price per canonical unit into a price per displayed unit:
  - `("per_volume","imperial")` → `("fl oz", Decimal("29.5735"))`; `("per_volume","metric")` → `("100 mL", Decimal("100"))`
  - `("per_mass","imperial")` → `("oz", Decimal("28.3495"))`; `("per_mass","metric")` → `("100 g", Decimal("100"))`
  - `per_count` → `("each", 1)` for both systems
  - `("area_x_ply","imperial")` → `("100 sq ft·ply", Decimal("9.290304"))`; `("area_x_ply","metric")` → `("m²·ply", Decimal("1"))`
  - `per_100_sheets` → `("100 sheets", 1)` and `per_roll` → `("roll", 1)` for both systems
- `display_unit_price(unit_price: Decimal, comparison: str, unit_system: str) -> tuple[Decimal, str]`
- `format_money(amount: Decimal, currency: str) -> str`. USD shows `$`; other currencies show the code. It uses 2 decimals when ≥ $1, and otherwise **3 significant digits**, so it never rounds a non-zero amount to `$0.00`. Examples: `Decimal("0.0695")` → `$0.0695`, `Decimal("0.00123")` → `$0.00123`, `Decimal("3.5")` → `$3.50`.
- `size_to_canonical(value: Decimal, dimension: str, unit_system: str) -> float` converts the size filter inputs: imperial volume fl oz to mL, mass oz to g, paper sq ft to m²; metric volume L to mL, mass kg to g.
- `display_size(measures: dict, dimension: str, unit_system: str) -> str`. Examples: "64 fl oz" / "1.89 L", "12 ct", "18 rolls · 4,356 sheets".

- [ ] **Step 1: Write failing tests** (Review Focus 5):
  - `test_volume_imperial_and_metric`: 0.002350/mL → $0.0695/fl oz and $0.235/100 mL
  - `test_mass`, `test_count`, `test_area_imperial`
  - `test_never_zero_for_nonzero`
  - `test_format_money_two_decimals_over_a_dollar`
  - `test_size_to_canonical_round_trip`
  - `test_display_size_variants`
- [ ] **Step 2: Run** `uv run pytest tests/web/test_units.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with `Decimal` only; no `float` in the money paths.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: unit display and money formatting`

---

### Task 4: Query resolution (roadmap 3.3 logic)

**Files:**
- Create: `normalize/query.py`, `tests/web/test_query.py`

**Interfaces:**
- `@dataclass(frozen=True) QueryResolution(category: str | None, filters: dict[str, object], leftover: str)`
- `resolve_query(text: str, categories: Mapping[str, CategoryDef]) -> QueryResolution`:
  - It folds the text with `normalize.text.fold`.
  - It picks the category with M1's include/exclude/longest-phrase rules (`match_category` on a `RawOffer` built from the query, or a shared helper extracted from it). An ambiguous or empty result gives `category=None`.
  - It extracts filters with the M1 pattern and synonym rules for that category, **ignoring `absent` defaults and API/OFF sources**. Only explicitly matched values become filters, and ambiguous ones are dropped.
  - `leftover` is the query with the matched include phrase and the filter phrases removed, whitespace collapsed. It feeds the full-text fallback.
  - Plural tolerance: "eggs" and "egg" both resolve. If a category's include phrase ends in "s", the query may omit the "s", and vice versa.

- [ ] **Step 1: Write failing tests** (Review Focus 3):
  - `test_milk_no_filters`: "milk" → `dairy/milk`, `{}`
  - `test_two_percent`: "2% milk" → `{"fat": "2"}`
  - `test_organic_two_percent`: → `{"fat": "2", "organic": True}`
  - `test_reduced_fat`: "reduced fat milk" → `{"fat": "2"}`
  - `test_two_ply_toilet_paper`: "2 ply toilet paper" → `household/toilet-paper`, `{"ply": 2}`
  - `test_large_eggs`: "large eggs" and "large egg" → `dairy/eggs`, `{"size": "large"}`
  - `test_unmatched`: "garden hose" → `None`
  - `test_ambiguous_filter_dropped`: "1% 2% milk" → milk, no fat filter
  - `test_leftover`: "kirkland 2% milk" → leftover "kirkland"
- [ ] **Step 2: Run** `uv run pytest tests/web/test_query.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Refactor shared M1 internals only where needed, keeping the M1 tests green.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: free-text query resolution`

---

### Task 5: Category page, search page, settings page (roadmap 3.1–3.5 UI)

**Files:**
- Create: `web/templates/web/category.html`, `_results.html`, `search_results.html`, `settings.html`; views in `web/views.py`; routes in `web/urls.py`; `tests/web/test_views_category.py`, `test_views_search.py`, `test_views_settings.py`

**Interfaces / routes:**
- `GET /c/<path:slug>/`: 404 for an unknown or inactive slug. It renders `category.html`:
  - a filter form: GET method, one control per attribute plus brand tier, a size range in display units, the comparison selector (default + alternates with display labels) and a store multi-select
  - the results partial `_results.html`
  - an `hx-get` with `hx-target="#results"` and `hx-push-url="true"` on change, so URLs stay shareable and the back button works
  - Requests with `HX-Request: true` return only `_results.html`.
- `_results.html` shows, per ranked row:
  - the product title (from offer data) and brand
  - the size (`display_size`)
  - the price (`format_money`)
  - the unit price with its label, e.g. "$0.0695 / fl oz"
  - the retailer display name
  - "price as of <localized time>" from `offer.last_seen_at`, rendered as `<time datetime=…>`
  - unknown labels ("fat unknown")
  - the outbound link (Task 6)

  It then shows a "Can't rank (N)" section listing unranked rows with reasons, and an empty state: "No fresh prices match these filters", with a link to clear the filters.
- `GET /search?q=…`: `resolve_query` with the active categories.
  - If it resolves to a category, **302** to `/c/<slug>/?<filters>` (bools as `true`, plus `q_rest=<leftover>` when non-empty, which the category page shows as a dismissible "also matching ‘…’" note, without filtering on it in M3).
  - If not, render `search_results.html` with a full-text fallback over product titles that have fresh offers (`SearchVector`/`SearchQuery` from `django.contrib.postgres`, add `django.contrib.postgres` to `INSTALLED_APPS`), up to 25 results.
  - An empty `q` redirects to `/`.
- `GET/POST /settings`: the unit system radio and the store checkboxes, listing `available_retailers` with "All stores" for None. It saves with `save_prefs` (CSRF-protected) and redirects back to `next`, which must be same-site. **Reject an absolute or external `next`** by falling back to `/`.

- [ ] **Step 1: Write failing tests:**
  - `test_category_page_lists_ranked_rows`
  - `test_filter_url_round_trip`: GET with `fat=2&organic=true` keeps the controls checked and the results filtered
  - `test_htmx_request_returns_partial`
  - `test_price_as_of_present_for_each_row`
  - `test_unknown_label_shown`
  - `test_cant_rank_section`
  - `test_unknown_slug_404`
  - `test_search_redirects_to_category_with_filters`: "organic 2% milk" → `/c/dairy/milk/?fat=2&organic=true`
  - `test_search_fallback_full_text`
  - `test_search_empty_redirects_home`
  - `test_settings_saves_and_rejects_external_next`
  - `test_metric_user_sees_metric_labels`
- [ ] **Step 2: Run** `uv run pytest tests/web -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Templates escape everything with Django defaults and use no `|safe` on data.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: category, search and settings pages`

---

### Task 6: Outbound affiliate links, click beacon, disclosures (roadmap 3.6)

**Files:**
- Create: `catalog/links.py`, `web/templates/web/disclosure.html`, `tests/web/test_links.py`, `tests/web/test_click.py`
- Modify: `catalog/models.py` (+ migration: `ClickEvent`), `web/views.py`, `web/urls.py`, `_results.html`, `base.html`, `web/static/web/app.js`, `.env.example`

**Interfaces:**
- Settings: `AFFILIATE = {"walmart_impact_publisher_id": env("WALMART_IMPACT_PUBLISHER_ID"), "sovrn_site_key": env("SOVRN_SITE_KEY"), "amazon_associates_tag": env("AMAZON_ASSOCIATES_TAG")}`. Add `SOVRN_SITE_KEY=` to `.env.example`, with a comment saying it's the *site* key from the Sovrn dashboard, not the API key.
- `outbound_url(offer: Offer, affiliate: Mapping[str, str | None]) -> str` picks the first route that applies:
  1. Walmart with `walmart_impact_publisher_id`: `https://goto.walmart.com/c/{pubid}/568844/9383?veh=aff&u={quote(offer.url, safe="")}`. These are the Impact campaign path values from Walmart's documented `productTrackingUrl`; keep them in named constants with a comment citing the docs.
  2. Amazon with `amazon_associates_tag`: the canonical URL with `tag=<tag>` added or replaced.
  3. Any retailer with `sovrn_site_key`: `https://sovrn.co?key={key}&u={quote(offer.url, safe="")}`.
  4. Otherwise the canonical `offer.url` unchanged.

  It returns a URL whose host is the retailer's or the network's, never ours.
- `link_attrs(offer, affiliate) -> dict` returns `{"href": …, "rel": "sponsored noopener noreferrer" or "noopener noreferrer", "target": "_blank", "ping": "/click?o=<offer.pk>"}`. "sponsored" applies only when an affiliate route was used.
- Model `ClickEvent(offer FK on_delete=SET_NULL null, retailer CharField, created_at auto)`, **with no other fields**.
- `POST /click?o=<id>`: CSRF-exempt, because it's a beacon or ping with no body. It records a `ClickEvent` for an existing offer id, returns 204, ignores unknown ids (still 204) and rate-limits nothing in M3. Stored data: the offer id and retailer only.
- `app.js`: on click of `a[data-offer]`, calls `navigator.sendBeacon('/click?o=…')` when the browser lacks `ping` support (feature-detect `HTMLAnchorElement.prototype.ping`). It never delays navigation.
- Disclosure:
  - `base.html` renders an above-the-fold banner whenever the page contains outbound links (`has_outbound` context flag): "We may earn a commission when you buy through links on this site. It never affects ranking."
  - Amazon rows add "Product prices and availability are accurate as of the date/time indicated and are subject to change." The page adds "As an Amazon Associate I earn from qualifying purchases." whenever an Amazon offer is shown.
  - `/disclosure` is a full page explaining the affiliate relationships, the commission-blind ranking, the price freshness and the Open Food Facts attribution: "Some product data from Open Food Facts, available under the ODbL."

- [ ] **Step 1: Write failing tests** (Review Focus 4):
  - `test_walmart_impact_url_encodes_canonical`, using a canonical URL with `?a=1&b=2#x`
  - `test_sovrn_fallback`
  - `test_amazon_tag_added_or_replaced`
  - `test_no_config_returns_canonical`
  - `test_sponsored_rel_only_when_affiliate`
  - `test_click_records_only_offer_and_retailer`: `ClickEvent` field names are exactly `{id, offer, retailer, created_at}`, and the request's IP and UA aren't stored
  - `test_click_unknown_id_204`
  - `test_disclosure_banner_present_when_links_shown`
  - `test_amazon_disclaimer_when_amazon_offer`
  - `test_no_redirect_route_exists`: resolving `/go/1` gives 404
- [ ] **Step 2: Run** `uv run pytest tests/web/test_links.py tests/web/test_click.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: direct affiliate links, click beacon, disclosures`

---

### Task 7: Responsive and accessibility pass, demo data (roadmap 3.7)

**Files:**
- Modify: `web/static/web/app.css`, templates, `CLAUDE.md` (how to run the demo)
- Create: `tests/web/test_accessibility.py`, `catalog/management/commands/demo_data.py`

**Interfaces:**
- `demo_data` command: runs `sync_categories`, then `refresh --connector walmart --recordings tests/connectors/recordings/walmart`, then prints the URL to open. It's for local dogfooding only. It refuses to run when `DEBUG` is False.
- The mobile layout puts the filters in a `<details>` drawer above the results (open by default on desktop via CSS). Results are a single-column card list; the search box stays reachable at the top of every page.
- Accessibility:
  - every form control has a label
  - result prices are announced as "price $X, $Y per fl oz"
  - focus styles are visible
  - colour contrast is at least 4.5:1 for text (check the CSS variable pairs)
  - `lang="en"` on `<html>`
  - one `<h1>` per page

- [ ] **Step 1: Write failing tests:**
  - `test_every_input_has_label` (parse rendered category and settings pages with `html.parser`, and match `<input>`/`<select>` ids to `<label for>`)
  - `test_single_h1`
  - `test_html_lang`
  - `test_demo_data_refuses_without_debug`
- [ ] **Step 2: Run** `uv run pytest tests/web/test_accessibility.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Also do a manual check with the Playwright browser at 390×844 and 1280×800 against `demo_data`. Record screenshots in the task report, but don't commit them.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: responsive layout, accessibility, demo data`

---

## M3 exit criteria

- With `uv run --env-file .env python manage.py demo_data` then `runserver`, searching "organic 2% milk" lands on the Milk page with those filters applied. It shows fresh offers ranked by unit price in the visitor's unit system, with "price as of" times, unknown labels, a "Can't rank" section and compliant direct links. It works at phone width.
- Every Review Focus item has a named test. `uv run pytest` is green locally and in CI.
- No page loads third-party resources. No outbound link passes through a UnitPricer URL. Clicks store only the offer id and retailer.

This sets up roadmap **Checkpoint B**: two weeks of dogfooding, then hosting and the first deploy.
