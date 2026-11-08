# Open-Ended Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any search that doesn't resolve to a curated category gets a generic results page: held products matched by text, grouped by measure, ranked by a generic unit price, and filtered only by structured data. New phrases also fetch live in the background and stream in by polling, and uncurated phrases are counted anonymously so the maintainer knows what to curate next.

**Architecture:** A pure `normalize/generic.py` gives every offer one generic measure (volume, mass, labeled oz, count, or unknown) at ingest, and ingestion now keeps offers that match no category as generic offers. A stored `tsvector` and a trigram index on `Offer` feed `catalog/generic.py`, which matches, groups, filters and finds near-miss categories in Python over at most 500 products. The search view renders from held data, records demand and enqueues `FetchJob`s. A second worker thread claims those jobs, paces every HTTP request through DB-backed token buckets (`SourceBudget`) and writes results through the normal ingestion path. The page polls via HTMX for up to 15 s.

**Tech Stack:** Django 5.2 (`django.contrib.postgres`: `SearchVector`, `GeneratedField`, `TrigramExtension`, `trigram_word_similar`), Postgres 17 with `pg_trgm`, HTMX, httpx (`MockTransport` recordings), pytest-django. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-08-open-search-design.md` (binding; D1–D15 in §2). It amends `docs/superpowers/specs/2026-11-07-unitpricer-design.md` (§13 of the open-search spec lists every change). Spec issue #65, plan issue #67.

**Depends on:**
- **#63 postal-code radius.** This plan assumes #63 has merged to `main` first. It had not merged when this plan was written (2026-11-08; `origin/main` at `c85feac`). The interfaces below are taken from branch `issue-63-postal-radius` (`1c0cb3e`): `Store.lat`/`Store.lon`, migration `catalog/migrations/0006_store_coordinates.py`, `catalog.geo.Near`, `within(near, lat, lon)`, `bounding_box(near)`, `browse(..., near=Near | None)` with `BrowseResult.hidden_far`/`hidden_unplaced`, `web.views._location(prefs) -> tuple[dict, Near | None]`, and `Prefs.postal_code`/`radius`. If #63 changes these before it merges, update Task 3's migration number and Task 6's radius step to match.
- **Category-expansion Task 1 (quantity guards).** Spec §14.2 makes it a prerequisite, because the generic measure reuses those guards. See "Prerequisite" below.

## Decisions

These are plan-level choices the spec leaves open. Each follows from the spec. None changes a D1–D15 decision.

- **P1 Live source name vs budget keys.** The spec gives `LiveSearchSource.name` as "also the SourceBudget key", but `off-prices` makes requests to two budgeted hosts (`off-search`, `openprices`). So `name` keys `FetchJob.source` (`walmart`, `off-prices`), and a new attribute `budget_keys: tuple[str, ...]` lists the `SourceBudget` rows the source draws from (`("walmart",)`, `("off-search", "openprices")`). `Budget.acquire(key)` takes the row key, as spec §6.3 writes it (`Budget.acquire(source)`). An enqueue is skipped when any of the source's budget rows is blocked or over its daily cap.
- **P2 Connectors stay Django-free.** The `Budget` protocol, the `BudgetExhausted` exception, `LiveSearchSource` and `LiveSearchResult` live in a new `connectors/live.py`. The DB implementation is `catalog.budget.DbBudget`.
- **P3 Scheduled work waits, but not forever.** Scheduled ingestion (Walmart) and the daily Open Prices sync draw tokens from the same rows through `DbBudget.wait(key, max_wait=60.0)`. If no token is available within 60 s (a daily cap or a long block), the run fails with `"budget exhausted"` and the next tick retries it. This keeps a capped source from stalling the Open Prices sync in the same loop.
- **P4 A product whose offers disagree on dimension is "Size unknown".** This is within one section (live or seen-in-stores). Example: one offer is `oz_labeled`, another is `volume`. The reason is "sizes disagree between stores". Picking one would be a guess, and a row may never sit in two groups.
- **P5 Near-miss order.** Held-result candidates come first, by product count descending. Typo candidates follow, by similarity descending, then slug. Duplicates are removed and at most 3 are shown. The two scores are on different scales, so they are not merged numerically.
- **P6 Demand and enqueue run only on full-page renders.** A first visit records demand and enqueues jobs. HTMX facet changes, "Show all", polls and history restores do not.
- **P7 Size facet.** A bound for dimension D (`vol_min` etc.) keeps only offers with `measure_dim == D` within the bound. This follows spec §5.3, "unknown under a filter": an offer in another dimension has no volume. The bounds use the same `SIZE_TOLERANCE` as category pages.
- **P8 OFF structured unit.** `OffEntry` and `OffProduct` gain `quantity_unit` (OFF `product_quantity_unit`, e.g. `"g"`, `"ml"`) so that `generic_measure(..., structured_unit=...)` can be fed as spec §5.2 describes. It is used only when the OFF quantity is the deciding input and its unit is a bare oz: `"g"` makes it mass, `"ml"` makes it volume. Anything else is ignored.
- **P9 Backfill inputs.** `Offer` stores no `size_text`, so `backfill_generic` computes live offers from the title and the OFF quantity. Observed offers also use their observation's `quantity`. Live offers get exact values on their next refresh (≤ 12 h). No `size_text` column is added.
- **P10 Budget seeding.** At worker start, `seed_budgets()` creates missing `SourceBudget` rows from settings and never overwrites existing rows, so admin edits persist. A changed setting applies to an existing row only after an admin edit or a deleted row. The README says so.
- **P11 OFF category path wording.** Spec §5.3 says hyphens are "shown as spaces", and the §15 test is `["en:dairies", "en:milks", "fr:laits"]` → `"Open Food Facts/Dairies/Milks"`. Rule: drop `en:`, replace `-` with a space, and capitalize the first letter. So `en:semi-skimmed-milks` → "Semi skimmed milks". The rule wins over the one hyphenated example in §5.3's prose.
- **P12 Personal phrases with separators.** A digit run separated only by single spaces, hyphens, dots or parentheses counts as one run. So `555-123-4567` and `4111 1111 1111 1111` are personal. This is the Review Focus 5 tightening of spec §4. `16.9 fl oz` (3 digits) and `2 x 12 pack` (letters between) are not.

## Global Constraints

- Python with `uv` only. No new dependencies. No AI/LLM libraries or calls anywhere (Principle 4). AGPL-3.0-or-later.
- **Never guess (Principle 3).** Generic mode never reads an attribute out of a title. A bare "oz" is its own dimension `oz_labeled`, never converted or moved into weight or volume (D8). No number is compared across dimensions (D3).
- **Honest ranking (Principle 2).** Text relevance picks the population (≤ 500 products) and never orders rows. Rows order by generic unit price, with ties broken by price, then title, then retailer, then offer id.
- **No request waits on a retailer API (Principle 5, as clarified).** All HTTP to sources happens in the worker's fetch thread.
- `normalize/` stays pure (no Django, network or filesystem beyond the existing loaders). `connectors/` imports no Django.
- Tests never touch the network. They use recorded responses (`connectors/recording.py`) or synthetic rows. No keys in CI.
- Search and poll GETs set no cookies (M3). Worker logs name the job id and source, never the phrase.
- Privacy: `SearchDemand` and `FetchJob` hold no user, IP, session, user agent or referrer. The per-client throttle uses `web.ratelimit.allow` with `web.net.client_ip`.
- Freshness: live offers only through `fresh_offers(now)`, observed offers only through `fresh_observed(now)`, both site currency only. Walmart data never enters `PriceHistory`, the Open Prices tables or the ODbL export.
- Exact copy (spec §5.2, §5.5, §6.5, §11, §12):
  - Group headings: "By volume ({n})", "By weight ({n})", "Per oz as labeled ({n})", "By count ({n})", "Size unknown ({n})".
  - Labeled-oz note: "Sold by \"oz\" without saying weight or fluid ounces, so these are compared only with each other."
  - Page note: "Matched by text and ranked by price per unit. For precise filters, pick a category."
  - Near-miss link: "{Category}: more filters →". Curated-product link: "In {Category} →".
  - Search status messages:
    - "Showing close matches for '{q}'"
    - "Showing the 500 closest matches; add words to narrow"
    - "checking {sources}…"
    - "updated with {n} new products"
  - Source status messages:
    - "{Source} didn't respond; showing saved prices."
    - "Live search is busy; showing saved prices."
    - "Still checking {sources}. Reload in a minute for more."
  - Empty states: "No saved prices match '{q}'" and "Nothing found. Try fewer words or browse a category."
  - Disclosure: "When you search for something we haven't seen, we ask Walmart and Open Food Facts for it. We send only the search words."
  - Source display names: `walmart` → "Walmart", `off-prices` → "Open Food Facts".
- Unit display is unified (D12). Metric is per L and per kg, imperial is per fl oz and per oz, and labeled oz is "oz (as labeled)" in both systems.
- Each task ends with `uv run pytest -q` green and the pre-commit hooks passing. Commit subjects end with the open-search implementation issue number (opened from #67 when execution starts; written `(#N)` below).

## Review Focus

1. **One product, two dimensions.** Walmart lists a UPC as "64 oz" (`oz_labeled`) and another live retailer lists it as "0.5 gal" (`volume`). The product must not appear in both groups or be ranked by either; it is "Size unknown" with "sizes disagree between stores" (P4). Owned by Task 5 (`test_product_with_conflicting_dimensions_is_unknown`).
2. **Degenerate queries.** Stopwords only ("the"), punctuation only ("!!!"), an unbalanced quote (`"milk`), a lone `-`, a single letter, emoji and 200 characters of text each return 200 with the empty state, never a 500. `websearch_to_tsquery` can yield an empty query and the trigram path must accept it. Owned by Task 5 (`test_degenerate_queries_never_error`) and Task 6 (`test_degenerate_queries_render`).
3. **The query echoed into HTMX attributes.** `q` holds `"><script>`, `&poll=1`, or `%26store%3Dx`. The poller's `hx-get` URL and the status line are escaped and URL-encoded, so a query can never add parameters or markup. Owned by Task 11 (`test_poller_url_escapes_query`).
4. **One bad job kills the fetch thread.** A source raises an unexpected exception, or the DB connection drops mid-job. The job is marked `failed` with the type name, and the loop goes on to the next job. After a `connection.close()` the thread reconnects. Owned by Task 8 (`test_fetch_loop_survives_job_error`).
5. **Personal data with separators.** Phone numbers and card numbers written with dashes or spaces (`555-123-4567`, `4111 1111 1111 1111`) slip past a plain "5+ digit run" rule. They must be personal (P12): not stored and not sent. `2% milk 1 gallon` and `16.9 fl oz 24 pack` are not personal. Owned by Task 2 (`test_personal_phrase_with_separators`).

---

## File Structure

```
normalize/generic.py              # GenericMeasure, generic_measure, normalize_phrase, is_personal,
                                  #   off_category_path, trigram_similarity (new, pure)
normalize/quantity.py             # + parse_generic(text) -> per-text generic parses
normalize/match.py                # + brand_tier_from_field(brand, store_brands)
normalize/types.py                # OffProduct + categories, quantity_unit
catalog/units.py                  # DISPLAY metric per L / per kg; generic display helpers
catalog/models.py                 # Offer generic fields + search_vector; OffEntry/OpenPriceObservation
                                  #   categories; FetchJob, SourceBudget, SearchDemand
catalog/migrations/0007_open_search.py
catalog/ingest.py                 # _upsert generic fields; ingest keeps generic; ingest_live (new)
catalog/observed.py               # keeps generic observed; upsert_observations (extracted)
catalog/merge.py                  # no ReviewItem for category NULL
catalog/generic.py                # match_offers, group_products, GenericFilters, facets, near_misses (new)
catalog/budget.py                 # token bucket (pure) + DbBudget + seed_budgets (new)
catalog/fetch.py                  # enqueue, claim, run_job, housekeeping, fetch_loop (new)
catalog/demand.py                 # record_demand, prune_demand, curate_next, warm_round (new)
catalog/scheduler.py              # starts fetch threads; tick runs housekeeping + warm round
catalog/admin.py                  # SourceBudget (editable), FetchJob (read-only), CurateNext (read-only)
catalog/management/commands/backfill_generic.py, bench_search.py (new)
connectors/live.py                # Budget, BudgetExhausted, LiveSearchSource, LiveSearchResult (new)
connectors/off_search.py          # OffPricesSource (new)
connectors/walmart.py             # category_path field; live_search
connectors/open_prices.py         # Observation.categories; fetch_codes; budget-paced requests
connectors/registry.py            # live_sources()
enrichment/off.py                 # OffRecord + categories, quantity_unit; FIELDS
data/labels.yaml                  # label allowlist (new)
web/search.py                     # generic view, poll handling, status text (new)
web/views.py                      # search() delegates; disclosure copy
web/templates/web/generic.html, _generic_results.html, _generic_group.html, _generic_facets.html,
                  _poller.html    # (new; search_results.html removed)
config/settings.py, .env.example, README.md, CLAUDE.md, docs/roadmap.md, main spec §16
tests/normalize/test_generic.py, tests/normalize/generic_fixtures.yaml
tests/catalog/test_generic_match.py, test_budget.py, test_fetch.py, test_demand.py, test_ingest_generic.py
tests/connectors/test_off_search.py, recordings/off_search/*, recordings/open_prices/codes_*.json
tests/web/test_generic_search.py, test_polling.py
```

---

### Prerequisite: Quantity guards (category-expansion Task 1)

Run **Task 1 of `docs/superpowers/plans/2026-11-08-category-expansion.md` exactly as written** (nutrient grams, bare count beside a size, container counts, extra count nouns) before Task 2 here, and skip it if `main` already has `_NUTRIENT_RE` in `normalize/quantity.py`. Its commit references both `#38` and `(#N)`. This plan does not repeat its steps. Task 2 below relies on its behaviour: "5.3 oz, 4 Count" gives no measure, and "16.9 fl oz, 40 Count Bottles" multiplies.

---

### Task 1: Unified unit display (D12)

**Files:**
- Modify: `catalog/units.py:29-32` (`DISPLAY`)
- Test: `tests/web/test_units.py`, `tests/web/test_views_category.py` (lines asserting "100 mL"/"100 g")

**Interfaces:**
- Produces: `DISPLAY[("per_volume", "metric")] == ("L", Decimal("1000"))`, `DISPLAY[("per_mass", "metric")] == ("kg", Decimal("1000"))`. Imperial entries, paper, sheets, rolls and count are unchanged. The category page's comparison switcher labels follow automatically.

- [ ] **Step 1: Update the tests first.** In `test_units.py`: `shown("0.002350", "per_volume", "metric") == ("$2.35", "L")` and `shown("0.010000", "per_mass", "metric") == ("$10.00", "kg")`. In `test_views_category.py`: replace `"/ 100 mL"` with `"/ L"`, and update the values they pin × 10.
- [ ] **Step 2: Run** `uv run pytest tests/web/test_units.py tests/web/test_views_category.py -v`. Expected: FAIL on the changed assertions.
- [ ] **Step 3: Change the two `DISPLAY` entries.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS. `grep -rn '100 mL\|100 g' web catalog tests` finds nothing.
- [ ] **Step 5: Commit** `feat: unify metric unit prices to per L and per kg (#N)`

---

### Task 2: Generic measure and phrase normalization (pure)

**Files:**
- Create: `normalize/generic.py`, `tests/normalize/test_generic.py`, `tests/normalize/generic_fixtures.yaml`
- Modify: `normalize/quantity.py` (add `parse_generic`), `normalize/match.py` (add `brand_tier_from_field`), `normalize/types.py` (`OffProduct`), `catalog/units.py` (generic display helpers)
- Test: `tests/web/test_units.py` (generic display)

**Interfaces:**
- Produces (`normalize/quantity.py`): `parse_generic(text: str) -> dict[str, float]` for one already-folded text. Keys are those found among `volume_ml`, `mass_g`, `oz_labeled`, `count`, plus `has_size_unit: 1.0` when any volume, mass or oz token is present even if it failed to parse. The parsers:
  - **Volume** uses `_VOLUME_UNITS` without the bare-`oz` entry.
  - **Mass** uses `_MASS_UNITS` without `oz|ounces?`, so "16 oz" is no mass. The compound follower check in `_measure` still accepts "1 lb 2 oz" through `_MASS_RE`.
  - **Labeled oz** is a bare `oz|ounces?` on `_strip_fl_oz(text)`, target unit `"ounce"`, with multipack multipliers. It skips any match inside the span of a lb+oz compound that mass consumed.
  - **Count** uses `_count` but accepts only the nouns `ct|count|pieces?|pcs|eggs?|dozen`, so the category-expansion nouns (`bags|pods|tabs|…`) yield no generic count. It also yields nothing when the text contains `rolls?` or `sheets?`.
  - All the existing guards apply (ranges, compounds, the prerequisite's nutrient/bare-count rules).
- Produces (`normalize/generic.py`):
  - `Dimension = Literal["volume", "mass", "oz_labeled", "count"]`. `DIMENSION_ORDER: tuple[Dimension, ...] = ("volume", "mass", "oz_labeled", "count")`.
  - `@dataclass(frozen=True) GenericMeasure(dimension: Dimension | None, amount: float | None, reason: str | None)`.
  - `generic_measure(texts: Sequence[str], structured_unit: str | None = None) -> GenericMeasure`. Texts are `[size_text, title, off_quantity]`, with missing entries passed as `""`. For each text in order, `p = parse_generic(fold(text))`:
    - If two or more of `volume_ml`, `mass_g` and `oz_labeled` are present, return unknown "more than one kind of size".
    - If exactly one is present, return it. One exception: when this is the third (OFF) text, the kind is `oz_labeled`, and `structured_unit` is `"g"` or `"ml"`, it becomes mass (oz → g via pint `ounce`) or volume (oz → mL via `fluid_ounce`).
    - Else, if `count` is present and `has_size_unit` is absent, return count.
    - Else, try the next text.
    - When nothing decides, return unknown "size unknown".
  - `generic_unit_price(price: Decimal, measure: GenericMeasure) -> Decimal | None`: `(price / Decimal(str(amount))).quantize(Decimal("0.000001"))`, or `None` when the dimension is `None`.
  - `normalize_phrase(text: str) -> str`: `fold(text)`, whitespace collapsed, cut to 100 characters, stripped.
  - `is_personal(phrase: str) -> bool`: `"@" in phrase`, or `re.search(r"\d(?:[\s\-.()]?\d){4,}", phrase)` (P12).
  - `off_category_path(tags: Sequence[str]) -> str | None`. Keep only `en:` tags, in their given order. Drop the prefix, replace `-` with a space, and capitalize the first letter. Join under `"Open Food Facts/"`. Return `None` when no `en:` tag remains. Cut to 500 characters.
  - `trigram_similarity(a: str, b: str) -> float`: pg_trgm's `similarity()` in Python. Lower-case, split on non-alphanumerics, pad each word as `"  w "`, take the set of trigrams, and return `|A∩B| / |A∪B|` (0.0 when both are empty).
- Produces (`normalize/match.py`): `brand_tier_from_field(brand: str | None, store_brands: frozenset[str]) -> AttrValue | None`. This is `brand_tier` without the title-prefix fallback, so a missing or blank brand gives `None`.
- Produces (`normalize/types.py`): `OffProduct` gains `categories: tuple[str, ...] = ()` and `quantity_unit: str | None = None`.
- Produces (`catalog/units.py`):
  - `GENERIC_COMPARISON: dict[str, str] = {"volume": "per_volume", "mass": "per_mass", "count": "per_count", "oz_labeled": "per_oz_labeled"}`.
  - `DISPLAY` gains `("per_oz_labeled", "imperial"|"metric"): ("oz (as labeled)", ONE)`.
  - `display_generic_size(dimension: str, amount: float, unit_system: str) -> str`: volume, mass and count through `display_size`; `oz_labeled` → `f"{_num(amount)} oz"`.

- [ ] **Step 1: Write the failing tests** in `tests/normalize/test_generic.py`:
  - `test_bare_oz_own_group`: these titles give these measures.

    | Title | Dimension | Amount or reason |
    |---|---|---|
    | "Great Value 2% Milk, 64 oz" | `oz_labeled` | 64 |
    | "Barilla Spaghetti, 16 oz" | `oz_labeled` | 16 |
    | "Net Wt 16 oz" | `oz_labeled` | 16 |
    | "64 fl oz" | `volume` | ≈1892.71 |
    | "16 oz (454 g)" | `None` | "more than one kind of size" |
    | "16.9 fl oz (500 g)" | `None` | "more than one kind of size" |
    | "1 lb 2 oz" | `mass` | ≈510.29 |
    | "6 x 16 oz" | `oz_labeled` | 96 |
  - `test_first_deciding_input_wins`: `["", "Milk 1 gal", "2 L"]` → volume ≈3785.41; `["12 ct", "Eggs 1 dozen"]` → count 12.
  - `test_count_needs_no_size_unit`: `["Chobani Greek Yogurt, 5.3 oz, 4 Count"]` → `None`, "size unknown" (never count 4). `["Bounty Paper Towels, 6 Double Rolls, 12 Count"]` → `None`. `["Tide Pods, 81 Count"]` → count 81. `["Tide Pods, 81 Pods"]` → `None`.
  - `test_structured_unit_only_for_off_input`: `["", "", "16 oz"]` with `structured_unit="g"` → mass ≈453.59; with `"ml"` → volume ≈473.18; `["16 oz"]` with `"g"` → `oz_labeled` 16.
  - `test_generic_unit_price`: $3.00 for volume 1892.70592 → `Decimal("0.001585")`; unknown → `None`.
  - `test_generic_fixtures`: parametrized over `generic_fixtures.yaml`. That file holds ≥ 30 real titles across food and household, each with `texts`, an optional `structured_unit`, and `expected: {dimension, amount}` (approx 0.01) or `{reason}`. Include ≥ 5 bare-oz titles, ≥ 3 multipacks, ≥ 3 paper goods, ≥ 3 nutrient-gram titles and ≥ 3 counts.
  - `test_normalize_phrase`: `"  Oat   MILK "` → `"oat milk"`; 150 × `"a"` → 100 characters.
  - `test_is_personal`: `"bob@example.com"` → True; `"90210 milk"` → True; `"milk 2%"` → False; `"1234 milk"` → False.
  - `test_personal_phrase_with_separators` (Review Focus 5): `"555-123-4567"`, `"(555) 123 4567"`, `"4111 1111 1111 1111"`, `"555.123.4567"` → True; `"2% milk 1 gallon"`, `"16.9 fl oz 24 pack"`, `"2 x 12 pack"` → False.
  - `test_off_category_path`: `["en:dairies", "en:milks", "fr:laits"]` → `"Open Food Facts/Dairies/Milks"`; `["en:semi-skimmed-milks"]` → `"Open Food Facts/Semi skimmed milks"`; `["fr:laits"]` → `None`.
  - `test_trigram_similarity`: `("tolet paper", "toilet paper") >= 0.4`; `("milk", "milk") == 1.0`; `("", "") == 0.0`; `("xyz", "milk") < 0.4`.
  - `test_brand_tier_from_field`: brand `"Great Value"` → store brand; `None` with a title-only store brand → `None`.
  - In `tests/web/test_units.py`: `test_generic_display`: `display_unit_price(Decimal("0.05"), "per_oz_labeled", "metric") == (Decimal("0.05"), "oz (as labeled)")`; `display_generic_size("oz_labeled", 64.0, "metric") == "64 oz"`.
- [ ] **Step 2: Run** `uv run pytest tests/normalize/test_generic.py tests/web/test_units.py -v`. Expected: FAIL (`ImportError`).
- [ ] **Step 3: Implement** the functions above. `normalize/quantity.py`'s existing `parse_measures` behaviour must not change.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS, including every `tests/categories/**` fixture.
- [ ] **Step 5: Commit** `feat: generic measure, phrase normalization and display (#N)`

---

### Task 3: Schema: generic offer fields, search indexes, queue/budget/demand models

**Files:**
- Modify: `catalog/models.py`, `catalog/admin.py`, `config/settings.py`, `.env.example`
- Create: `catalog/migrations/0007_open_search.py` (depends on #63's `0006_store_coordinates`)
- Test: `tests/catalog/test_models.py`

**Interfaces:**
- Produces on `Offer`:
  - `category_path = CharField(max_length=500, null=True, blank=True)`
  - `labels = JSONField(default=list)`
  - `measure_dim = CharField(max_length=10, null=True, blank=True)`
  - `measure_amount = FloatField(null=True, blank=True)`
  - `measure_reason = CharField(max_length=100, null=True, blank=True)`
  - `generic_unit_price = DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)`
  - `search_vector`: a `GeneratedField(expression=SearchVector("title", weight="A", config="english") + SearchVector("brand", weight="B", config="english") + SearchVector("category_path", weight="C", config="english"), output_field=SearchVectorField(), db_persist=True)`. If Postgres rejects the expression as not immutable, add the column with `RunSQL` (`GENERATED ALWAYS AS (setweight(to_tsvector('english'::regconfig, coalesce(title,'')),'A') || … ) STORED`) and map it with `SearchVectorField(editable=False, null=True)`, plus a test that `.save()` never writes it.
- Produces indexes:
  - `GinIndex(fields=["search_vector"], name="offer_search_gin")`
  - `GinIndex(fields=["title"], opclasses=["gin_trgm_ops"], name="offer_title_trgm")`
  - `Index(fields=["measure_dim", "generic_unit_price"], name="offer_measure_price")`
  - `Index(fields=["kind", "currency", "last_seen_at"], name="offer_fresh")`
  - `TrigramExtension()` is the migration's first operation.
- Produces on `OffEntry`: `categories = JSONField(default=list)`, `quantity_unit = CharField(max_length=10, null=True, blank=True)` (P8). On `OpenPriceObservation`: `categories = JSONField(default=list)`.
- Produces the models of spec §7.2 verbatim:
  - **`FetchJob`**: `Status` choices, fields `source`, `phrase`, `origin` (`search` | `list` | `warm`), `status`, `created_at`, `not_before`, `started_at`, `finished_at` (indexed), `attempts`, `result_count`, `error`.
    - Constraints: `UniqueConstraint(fields=["source", "phrase"], condition=Q(status__in=["queued", "running"]), name="uniq_active_fetchjob")` and `Index(fields=["not_before", "created_at"], condition=Q(status="queued"), name="fetchjob_due")`.
    - `FetchJob.ACTIVE = ("queued", "running")` and `FetchJob.TERMINAL = ("succeeded", "failed", "expired", "skipped")`.
  - **`SourceBudget`**, with fields as in §7.2.
  - **`SearchDemand`**, with fields as in §7.2.
- Produces settings (blank entries plus a default comment in `.env.example`):
  - `LIVE_OFF_ENABLED` ← `UNITPRICER_LIVE_OFF == "1"` (False)
  - `FETCH_THREADS` ← `UNITPRICER_FETCH_THREADS` (1, clamped to 1..2)
  - `WARM_INTERVAL` ← `UNITPRICER_WARM_HOURS` (`timedelta(hours=12)`)
  - `LABELS_FILE = BASE_DIR / "data/labels.yaml"`
  - `SOURCE_BUDGETS: dict[str, dict]`, keyed `off-search`, `openprices`, `walmart`, each `{"capacity": float, "refill_per_second": float, "daily_limit": int | None}`. Each key reads `UNITPRICER_BUDGET_<KEY>_CAPACITY`, `_REFILL_PER_SECOND` and `_DAILY_LIMIT`, where `<KEY>` is uppercased with `-` → `_`. Defaults:

    | Key | Capacity | Refill per second | Daily limit |
    |---|---|---|---|
    | `off-search` | 10 | 10/60 | none |
    | `openprices` | 5 | 1.0 | none |
    | `walmart` | 5 | 1.0 | 5000 |

    An empty `_DAILY_LIMIT` means none.
- Admin: `SourceBudget` editable, listing every field. `FetchJob` read-only (`ReadOnlyAdmin`), listing id, source, origin, status, created_at, finished_at, result_count and error. Do not show `phrase` in the list display, and do not log it. `SearchDemand` comes in Task 11.

- [ ] **Step 1: Write failing tests** in `tests/catalog/test_models.py`:
  - `test_search_vector_generated`: an offer titled "Oat Milk Barista", brand "Oatly", path "Food/Dairy Alternatives". `Offer.objects.filter(search_vector=SearchQuery("oatly", config="english"))` finds it, and updating the title to "Soy Drink" makes `"oat"` stop matching after `refresh_from_db()`.
  - `test_trigram_extension_and_index`: `Offer.objects.filter(title__trigram_word_similar="tolet paper")` runs and finds "Charmin Toilet Paper"; `pg_extension` has `pg_trgm`.
  - `test_one_active_job_per_source_phrase`: two `FetchJob(source="walmart", phrase="oat milk")` in `queued` raise `IntegrityError`; a `succeeded` one plus a `queued` one is allowed.
  - `test_settings_budget_defaults`: `settings.SOURCE_BUDGETS["off-search"] == {"capacity": 10.0, "refill_per_second": pytest.approx(1/6), "daily_limit": None}` and walmart's `daily_limit == 5000`.
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_models.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the models and settings. Run `uv run python manage.py makemigrations catalog -n open_search`, then put `TrigramExtension()` first in the migration. Register the admins.
- [ ] **Step 4: Run** `uv run pytest -q` and `uv run python manage.py makemigrations --check --dry-run`. Expected: PASS, "No changes detected".
- [ ] **Step 5: Commit** `feat: open-search schema: generic offer fields, search indexes, job/budget/demand models (#N)`

---

### Task 4: Ingestion keeps generic offers; backfill

**Files:**
- Modify:
  - `catalog/ingest.py` (`_upsert`; `ingest` at line 125)
  - `catalog/observed.py` (line 215; extract `upsert_observations`)
  - `catalog/merge.py`
  - `connectors/walmart.py` (`_map`)
  - `connectors/open_prices.py` (`parse_item`, `Observation`, `to_raw_offer`)
  - `enrichment/off.py` (`OffRecord`, `_record_from_dict`, `FIELDS`, `lookup`, `_save`)
- Create: `catalog/management/commands/backfill_generic.py`, `tests/catalog/test_ingest_generic.py`
- Test: `tests/catalog/test_ingest.py`, `tests/catalog/test_observed.py`, `tests/connectors/test_walmart.py`, `tests/enrichment/test_off.py`

**Interfaces:**
- Consumes: `generic_measure`, `generic_unit_price`, `off_category_path`, `brand_tier_from_field` (Task 2); the Task 3 fields.
- Produces:
  - `RawOffer.fields["category_path"]`. Walmart's `_map` copies `item["categoryPath"]` when it is a non-empty string; Open Prices `to_raw_offer` sets `off_category_path(obs.categories)` when not `None`.
  - `Observation.categories: tuple[str, ...]` from `product.categories_tags` (strings only). It is stored in `OpenPriceObservation.categories`.
  - `OffRecord` gains `categories: tuple[str, ...]` and `quantity_unit: str | None`, read from `categories_tags` and `product_quantity_unit`. `FIELDS` adds both. `_save` and `lookup` carry them to `OffEntry` and `OffProduct`.
  - `_upsert(raw, normalized, model_cache, now, touched, *, records_price_history, extra_defaults=None, off: OffProduct | None = None)`.
    - It accepts `normalized.category is None`: the product is created with `category=None`, and the offer gets `category=None`.
    - For generic offers, `attributes` is `{"brand_tier": {...}}` when `brand_tier_from_field` gives one, else `{}`, and `unit_prices={}`, `unresolved=["category"]`.
    - Every offer, curated or not, gets these generic fields:
      - `measure_*` from `generic_measure([raw.size_text or "", raw.title, off.quantity if off else ""], off.quantity_unit if off else None)`
      - `generic_unit_price`
      - `labels=sorted(off.labels) if off else []`
      - `category_path = raw.fields.get("category_path") or off_category_path(off.categories if off else ())`
  - `ingest()` no longer skips `normalized.category is None`. It writes those offers through `_upsert` and counts them in neither `matched` nor `skipped`.
  - `upsert_observations(kept: list[Observation], now: datetime, *, off_lookup, run: IngestionRun | None) -> tuple[set[int], set[int], int]` (touched product ids, kept offer pks, skipped count). This is the store + `OpenPriceObservation` + latest-per-(store, UPC) offer writing, extracted from `_write`, with uncategorized observations kept as generic observed offers. `_write` calls it and keeps its deletion logic. Task 9 calls it with no deletions.
  - `merge_product`: when the merged `locked.category` is `None`, the unresolved list passed to `_sync_review_items` is `[]`.
  - `ingest_live(result: LiveSearchResult, source_name: str, now: datetime) -> int` (in `catalog/ingest.py`; `LiveSearchResult` comes from Task 8's `connectors/live.py`, so this function lands in Task 8). It is listed here so the write path is reviewed with the rest of ingestion.
  - `manage.py backfill_generic [--batch 1000]`: recomputes the generic fields for every offer in pk batches (P9). It prints `updated=<n>` and is idempotent.

- [ ] **Step 1: Write failing tests** in `tests/catalog/test_ingest_generic.py`, using the existing `FakeConnector` helpers:
  - `test_scheduled_ingest_keeps_unmatched`: a milk search returns "Oatly Oat Milk, 64 fl oz" (excluded by milk's rules). It is stored with `category=None`, `measure_dim="volume"`, `generic_unit_price` set, and `attributes` holding only `brand_tier` from the brand field.
  - `test_curated_offer_gets_generic_fields`: a 2% milk offer keeps its curated `unit_prices` and also gets `measure_dim="volume"` and a `generic_unit_price` equal to `price / volume_ml`.
  - `test_generic_product_opens_no_review_item`: after ingest and merge, the generic product has no `ReviewItem`.
  - `test_walmart_category_path`: ingesting the recorded Walmart `milk` response stores `category_path == "Food/Dairy, Eggs & Cheese/Milk"`.
  - `test_labels_and_off_path_from_off_entry`: with an `OffEntry(labels=["en:organic"], categories=["en:dairies", "en:milks"])` for the UPC, the offer has `labels == ["en:organic"]`, and `category_path == "Open Food Facts/Dairies/Milks"` when the raw offer has no path.
  - `test_observed_sync_keeps_generic`: in `tests/catalog/test_observed.py`, an observation with no matching category becomes an observed offer with `category=None` and `measure_dim` set. ODbL export tests stay green.
  - `test_walmart_never_in_price_history_generic`: a generic Walmart offer writes no `PriceHistory`.
  - `test_backfill_generic_idempotent`: null the generic fields on two offers, run the command twice, and both runs print `updated=2`, with the values equal to ingest's.
  - In `tests/enrichment/test_off.py`, `test_record_reads_categories_and_unit`.
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_ingest_generic.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes. Pass `off=` from both `ingest()` and `upsert_observations()` (the observed path's `off` is `off_lookup(upc) or OffProduct(labels=obs.labels, quantity=obs.quantity, categories=obs.categories)`).
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS. Existing ingest tests that asserted unmatched offers were dropped are updated to assert they are generic, and only those change.
- [ ] **Step 5: Commit** `feat: keep unmatched offers as generic offers with generic measures (#N)`

---

### Task 5: Text matching, trigram fallback and measure grouping

**Files:**
- Create: `catalog/generic.py`, `tests/catalog/test_generic_match.py`, `catalog/management/commands/bench_search.py`

**Interfaces:**
- Consumes: Task 3 fields and indexes; `fresh_offers`, `fresh_observed`; `DIMENSION_ORDER` (Task 2).
- Produces (`catalog/generic.py`):
  - Constants: `MATCH_CAP = 500`, `FUZZY_BELOW = 5`, `GROUP_PAGE = 25`, `SIZE_UNKNOWN = "size unknown"`, `SIZES_DISAGREE = "sizes disagree between stores"`.
  - `@dataclass(frozen=True) Match(offers: list[Offer], fuzzy: bool, capped: bool)`.
  - `match_offers(q: str, now: datetime, *, observed: bool = False, stores: frozenset[str] | None = None) -> Match`. `q` is the folded query (`fold(q)`, spec §4); callers fold it.
    - The population is `fresh_observed(now)` or `fresh_offers(now)`, restricted to `retailer__in=stores` when set.
    - Text match: `search_vector=SearchQuery(q, config="english", search_type="websearch")`. Take the top `MATCH_CAP` product ids by `Max(SearchRank)`.
    - When fewer than `FUZZY_BELOW` products match, add products whose offers have `title__trigram_word_similar=fold(q)`, ranked by `TrigramWordSimilarity`, up to the cap, and set `fuzzy=True` if any were added.
    - Finally, fetch every population offer of the chosen products with `select_related("product", "product__category", "store")`.
    - The query count is fixed: at most 3 queries regardless of the number of results.
    - Empty or degenerate `q` gives an empty match, never an exception.
  - `@dataclass(frozen=True) GenericRow(product: Product, offer: Offer, unit_price: Decimal | None, reason: str | None)`.
  - `@dataclass(frozen=True) MeasureGroup(dimension: str, rows: list[GenericRow], total: int)`.
  - `@dataclass(frozen=True) GenericResult(groups: list[MeasureGroup], unknown: list[GenericRow], total_products: int)`.
  - `group_products(offers: Iterable[Offer]) -> GenericResult`. This is pure Python over offers.
    - **Per product:**
      - Collect the distinct non-null `measure_dim` of its offers.
      - Exactly one dimension → the product joins that group. Its row is its offer with the lowest `(generic_unit_price, price, retailer, offer.id)`; observed ties use `browse._tie`.
      - None → "Size unknown". The row is the cheapest offer by `(price, retailer, id)` with that offer's `measure_reason`.
      - More than one → "Size unknown" with `SIZES_DISAGREE` (P4).
    - **Group order:** rows by `(unit_price, offer.price, product.title, offer.retailer, offer.id)`. Groups by product count descending, ties in `DIMENSION_ORDER`. Unknown rows by `(offer.price, product.title, offer.retailer, offer.id)`. Rows are not truncated here.
- `manage.py bench_search [--offers 100000] [--keep]`:
  - Refuses unless `DEBUG` (like `demo_data`).
  - Seeds deterministic offers (`random.Random(65)`, retailer `"bench"`, titles from a fixed vocabulary across 4 dimensions).
  - Runs `match_offers` plus `group_products` for a fixed list of 50 phrases, 5 times each.
  - Prints `p50=<ms> p95=<ms>` and deletes the bench rows unless `--keep`.
  - Target: p95 < 300 ms (spec §10). It is run by hand before merge, never in CI.

- [ ] **Step 1: Write failing tests** in `tests/catalog/test_generic_match.py`, with synthetic offers through a helper `make_generic_offer(title, price, *, dim, amount, retailer="fake", brand=None, path=None, labels=(), kind="live", age_hours=1)`:
  - `test_text_match_all_terms`: "oat milk" finds "Oatly Oat Milk" and not "Whole Milk"; "oatly" matches by brand; "dairy alternatives" matches by path.
  - `test_trigram_fallback_marks_fuzzy`: "tolet paper" with only "Charmin Toilet Paper" held gives that product and `fuzzy=True`. "milk" with 6 milks gives `fuzzy=False`.
  - `test_cap_500`: 510 matching products gives 500 products and `capped=True`.
  - `test_freshness_and_stores`: a 25 h Walmart offer is absent; `stores=frozenset({"other"})` excludes `fake`; observed offers appear only with `observed=True`.
  - `test_group_order_and_ranking`: 3 volume, 2 mass, 1 count products give group order volume, mass, count. Within volume, rows are ascending by unit price, and a price tie breaks by price then title.
  - `test_cheapest_offer_per_product`: two retailers' offers for one product give one row, using the lower generic unit price.
  - `test_product_with_conflicting_dimensions_is_unknown` (Review Focus 1): a product with one `oz_labeled` offer and one `volume` offer appears only in `unknown`, with `SIZES_DISAGREE`, and in no group.
  - `test_degenerate_queries_never_error` (Review Focus 2): for each of `"the"`, `"!!!"`, `'"milk'`, `"-"`, `"a"`, `"🥛"` and `"x" * 200`, `match_offers` returns a `Match` without raising.
  - `test_match_query_count_fixed`: `django_assert_num_queries` gives the same count for 3 matching products and for 60, and it is ≤ 3.
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_generic_match.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `catalog/generic.py` (matching and grouping only) and `bench_search`.
- [ ] **Step 4: Run** `uv run pytest -q`. Then, locally with `DJANGO_DEBUG=1`, run `uv run --env-file .env python manage.py bench_search`. Expected: tests PASS, and the printed p95 is < 300 (record it in the PR description).
- [ ] **Step 5: Commit** `feat: generic text match with trigram fallback and measure groups (#N)`

---

### Task 6: Generic results page

**Files:**
- Create: `web/search.py`, `web/templates/web/generic.html`, `_generic_results.html`, `_generic_group.html`, `tests/web/test_generic_search.py`
- Modify: `web/views.py` (`search` delegates; remove the old full-text block and the `SearchVector` imports), `catalog/browse.py` (extract the radius step), `web/static/web/app.css`
- Delete: `web/templates/web/search_results.html`
- Test: `tests/web/test_views_search.py` (old full-text assertions move to the generic page)

**Interfaces:**
- Consumes: `match_offers`, `group_products`, `GROUP_PAGE` (Task 5); `display_unit_price`, `GENERIC_COMPARISON`, `display_generic_size` (Tasks 1–2); `trigram_similarity`, `normalize_phrase` (Task 2); `resolve_query`, `_active_definitions`, `get_prefs`, `split_stores`, `_store_options`, `available_retailers` (M3); `_location(prefs)` and `Near` (#63).
- Produces (`catalog/browse.py`): `limit_to_radius(offers: list[Offer], near: Near | None) -> tuple[list[Offer], int, int]` (kept offers, hidden_far, hidden_unplaced products). It is extracted from #63's inline code in `browse()`. `browse()` calls it, and #63's tests stay unchanged. The SQL bounding-box pre-filter stays in `browse()`. The generic page passes already-matched offers, which are at most a few thousand.
- Produces (`catalog/generic.py`): `@dataclass(frozen=True) NearMiss(slug: str, name: str)` and `near_misses(folded_q: str, offers: Iterable[Offer], definitions: Mapping[str, CategoryDef], names: Mapping[str, str]) -> list[NearMiss]`. Held-result candidates are active categories with ≥ 2 matched products, ordered by count. Typo candidates are categories where `trigram_similarity(folded_q, x) >= 0.4` for the name or any `match.include` phrase, ordered by best similarity. The final order follows P5, capped at 3.
- Produces (`web/search.py`):
  - `generic_results(request, q: str) -> HttpResponse`. This is the §4 flow steps 1–3 here, steps 4–6 in Tasks 11–12.
    - Live groups come from `match_offers(q, now, stores=live_stores)`, and observed from `match_offers(q, now, observed=True, stores=chain_stores)` followed by `limit_to_radius(..., near)`.
    - Rows are presented with title, brand, `display_generic_size`, price, "{unit price} / {label}", store, "price as of", `{% outbound %}`, and the Amazon disclaimer. `has_outbound=True` on the full page, as on category pages, so the affiliate disclosure banner shows.
    - A curated product's row links "In {Category} →" to `/c/<slug>/`.
    - Each group shows its first `GROUP_PAGE` rows. A "Show all {n}" link (`hx-get` with `show=<dimension>`) returns `_generic_group.html` with every row, up to the cap.
    - The near-miss link target is `/c/<slug>/?q_rest=<folded q>`.
    - "Seen in stores" is grouped the same way, with the ODbL notice and #63's hidden-far note.
    - The template is `generic.html` for full renders, `_generic_results.html` with `oob=True` for HTMX swaps, and `_generic_group.html` for `show`.
    - `Vary` is `HX-Request`, `HX-History-Restore-Request`, `Accept-Language`, as on category pages.
  - `status_text(total: int, *, fuzzy: bool, capped: bool, q: str, live: str | None) -> str`: the status line from the Global Constraints copy. `live` is filled by Task 11.
- `views.search`: after `resolve_query` finds no category, `return generic_results(request, q)`. The curated redirect is unchanged.

- [ ] **Step 1: Write failing tests** in `tests/web/test_generic_search.py`:
  - `test_measure_grouping` (spec D7): fl oz, g and ct products render as "By volume (2)", "By weight (1)", "By count (1)" in that order. A "16.9 fl oz (500 g)" product is under "Size unknown" with "more than one kind of size". Each group's rows show only its own unit label.
  - `test_bare_oz_own_group` (web half): "Great Value 2% Milk, 64 oz" and "Barilla Spaghetti, 16 oz" render under "Per oz as labeled (2)" with the note copy and "/ oz (as labeled)", in both the imperial and metric sessions. Neither appears under volume or weight.
  - `test_unit_display_unified`: a milk offer shows the same "/ L" price on `/c/dairy/milk/` and on the generic page under a metric session, and the same "/ fl oz" price under imperial.
  - `test_curated_query_still_redirects`: `q=2% milk` → 302 to `/c/dairy/milk/?fat=2` (the existing tests in `test_views_search.py` stay as they are).
  - `test_near_miss_links`: two held milk products under "oat milk" → "Milk: more filters →". `q=tolet paper` with nothing held → a "Toilet paper: more filters →" link to `/c/household/toilet-paper/?q_rest=tolet+paper`. At most 3 links.
  - `test_curated_row_links_category`: a 2% milk row shows "In Milk →".
  - `test_seen_in_stores_generic`: a generic observed offer shows under "Seen in stores" with the ODbL notice and never in the live groups. With a saved postal code, a store outside the radius is hidden and counted.
  - `test_generic_freshness`: a 25 h old Walmart generic offer is absent.
  - `test_show_all_group`: 30 volume products show 25 and "Show all 30". `?show=volume` with `HX-Request` returns all 30 and only that group.
  - `test_search_sets_no_cookies`: a generic search GET has no `Set-Cookie`.
  - `test_apex_has_no_search`: on the public host, `/search?q=x` → 404.
  - `test_degenerate_queries_render` (Review Focus 2): each degenerate query from Task 5 → 200 with "No saved prices match".
  - `test_generic_view_query_count`: the query count is the same for 3 and for 60 matching products, and ≤ 15 (with no session).
- [ ] **Step 2: Run** `uv run pytest tests/web/test_generic_search.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `web/search.py`, `near_misses`, `limit_to_radius` and the templates (reuse `_row.html`, `_observed_row.html` and `_status.html` with generic context keys), then delete `search_results.html`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: generic results page grouped by measure, with near-miss categories (#N)`

---

### Task 7: Data-derived facets

**Files:**
- Create: `data/labels.yaml`, `web/templates/web/_generic_facets.html`
- Modify: `catalog/generic.py`, `web/search.py`, `web/templates/web/_generic_results.html`, `generic.html`
- Test: `tests/catalog/test_generic_facets.py`, `tests/web/test_generic_search.py`

**Interfaces:**
- Consumes: `Offer.brand`, `labels`, `category_path`, `measure_*`, `retailer`; `load_store_brands`, `brand_tier_from_field`; `size_to_canonical` (M3); `SIZE_TOLERANCE` (browse).
- `data/labels.yaml`: a mapping of tag → display name. Initial entries: `en:organic: Organic`, `en:gluten-free: Gluten-free`, `en:vegan: Vegan`, `en:vegetarian: Vegetarian`.
- Produces (`catalog/generic.py`):
  - `@dataclass(frozen=True) GenericFilters`, with fields:
    - `brands: frozenset[str]` (folded)
    - `brand_tier: str | None`
    - `labels: frozenset[str]`
    - `path: str | None`
    - `sizes: Mapping[str, tuple[float | None, float | None]]` (dimension → canonical min/max)
  - `parse_generic_filters(params: QueryDict, unit_system: str, allowlist: Mapping[str, str]) -> GenericFilters`.
    - Params: `brand` (repeatable), `brand_tier` (in `BRAND_TIERS`), `label` (repeatable, allowlisted only), `path`, and `vol_min`/`vol_max`, `mass_min`/`mass_max`, `oz_min`/`oz_max`, `count_min`/`count_max`.
    - Volume and mass bounds are in display units, converted with `size_to_canonical`. Labeled oz is in oz in both systems, and count is items.
    - Unknown or invalid values are dropped, never an error. The store filter is the existing `store` param handled by the view.
  - `apply_filters(offers: Iterable[Offer], f: GenericFilters, store_brands: frozenset[str]) -> list[Offer]`:
    - Brand: `fold(offer.brand)` must be in `f.brands`.
    - Brand tier: the tier of `brand_tier_from_field(offer.brand, …)` must equal `f.brand_tier`.
    - Labels: every label in `f.labels` must be in `offer.labels`.
    - Path: `offer.category_path == f.path` or starts with `f.path + "/"`.
    - Sizes: a bound for dimension D keeps only `measure_dim == D` within the bounds × (1 ∓ `SIZE_TOLERANCE`) (P7).
    - An unknown value under an active filter is excluded.
  - `@dataclass(frozen=True) Facet(name: str, options: list[tuple[str, str, int]], active: bool)`. Options are (value, label, product count). For a size facet, the options are `[("min", …), ("max", …)]` as the observed range in display units.
  - `facets(offers: list[Offer], f: GenericFilters, store_brands, allowlist, unit_system) -> list[Facet]`.
    - Each facet is computed over the offers that pass every *other* active filter.
    - It shows when there are ≥ 2 distinct known values counted by product, or when it is active.
    - **Brand:** grouped by folded brand. Each value is shown as its most common original spelling, with ties broken alphabetically.
    - **Brand tier:** as for brand.
    - **Labels:** per allowlisted tag, shown when ≥ 1 product has it and ≥ 1 doesn't. The label is shown with "from Open Food Facts".
    - **Path:** the distinct next segments under the selected prefix.
    - **Size:** one facet per dimension present with ≥ 2 distinct amounts.
- The view applies the filters before `group_products` (live and observed alike), renders the facets beside or above the groups, and swaps them out-of-band on HTMX changes. All state is in the URL.

- [ ] **Step 1: Write failing tests:**
  - `test_facet_needs_two_values` (spec D7):
    - A single brand shows no brand facet, and two brands show it.
    - With `brand=oatly` active and one value left, the facet still shows with a clear link.
    - A product with no brand is not counted as a value.
    - The `en:organic` label is offered only when some products have it and some don't.
  - `test_generic_never_invents_attribute` (spec D7):
    - An uncategorized offer "Organic Gluten Free Oat Milk" ingested through `ingest()` with no `OffEntry` has `attributes == {}`, `labels == []`, and does not match `label=en:organic`.
    - With an `OffEntry(labels=["en:organic"])` and a re-ingest, it matches.
    - A brand-less "Great Value Oat Milk" generic offer has no `brand_tier` and is excluded under `brand_tier=store_brand`.
  - `test_unknown_excluded_under_filter`: under `brand=oatly`, a brand-less product is excluded; without the filter it is included.
  - `test_path_drill_down`: paths "Food/Dairy/Milk" and "Food/Dairy/Cheese" with no `path` show "Food". `path=Food` shows "Dairy", and `path=Food/Dairy` shows "Milk" and "Cheese". An "Open Food Facts/Dairies" path never appears under "Food".
  - `test_size_bounds`:
    - `vol_min=60&vol_max=70` (imperial fl oz) keeps a 64 fl oz product.
    - It drops a 128 fl oz product, every mass and labeled-oz product, and volume products of unknown size.
    - `oz_min=10` keeps 16 labeled oz in both unit systems.
  - `test_bad_facet_params_ignored`: `label=en:made-up`, `brand_tier=gold` and `vol_min=abc` → 200, same results as with no params.
  - `test_repeatable_labels_mean_all`: `label=en:organic&label=en:vegan` keeps only products with both.
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_generic_facets.py tests/web/test_generic_search.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the facet code, `labels.yaml`, the template and the view wiring.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS. The view query-count test from Task 6 still holds.
- [ ] **Step 5: Commit** `feat: structured-data facets on generic results (#N)`

---

### Task 8: Source budgets, fetch queue and worker fetch loop

**Files:**
- Create: `connectors/live.py`, `catalog/budget.py`, `catalog/fetch.py`, `tests/catalog/test_budget.py`, `tests/catalog/test_fetch.py`
- Modify: `catalog/ingest.py` (`ingest_live`; `ingest` takes a budget), `catalog/scheduler.py`, `catalog/management/commands/run_scheduler.py`, `connectors/open_prices.py` (`OpenPricesClient(budget_wait=…)`), `connectors/registry.py` (`live_sources`, `live_source_info`), `catalog/observed.py` (the daily sync passes `budget_wait`)

**Interfaces:**
- Produces (`connectors/live.py`, no Django):
  - `class BudgetExhausted(Exception)`, with `wait: float` (seconds).
  - `class Budget(Protocol)`:
    - `acquire(self, key: str) -> None` raises `BudgetExhausted`.
    - `report(self, key: str, status: int | None, retry_after: float | None = None) -> None`. `status=None` means a transport error.
  - `@dataclass(frozen=True) LiveSearchResult`, with fields `offers: list[RawOffer]`, `observations: list[Observation]`, `off_records: list[OffRecord]` and `skipped: int`. Every field has an empty default.
  - `class LiveSearchSource(Protocol)`:
    - `name: str`, `budget_keys: tuple[str, ...]`, `max_cache_age: timedelta`, `refresh_after: timedelta`.
    - `live_search(self, phrase: str, limit: int, budget: Budget) -> LiveSearchResult`.
    - `close(self) -> None`.
- Produces (`catalog/budget.py`):
  - `@dataclass(frozen=True) Bucket(capacity: float, refill_per_second: float, tokens: float, refilled_at: datetime, daily_limit: int | None, daily_used: int, day: date, blocked_until: datetime | None, consecutive_failures: int)`.
  - `take(b: Bucket, now: datetime) -> tuple[Bucket, float]`. This is pure. It returns `(new_bucket, 0.0)` on grant, or `(b_refilled, wait_seconds)` on refusal.
    - On a new UTC day, `daily_used` resets to 0 first.
    - Blocked → refuse, and the wait is until `blocked_until`.
    - At the daily cap → refuse, and the wait is until the next UTC midnight.
    - Otherwise refill `min(capacity, tokens + elapsed × refill)`, and take 1 if `tokens >= 1`. If not, the wait is `(1 − tokens) / refill`.
  - `backoff(b: Bucket, now: datetime, status: int | None, retry_after: float | None) -> Bucket`. This is pure.
    - `status` of 429, 503 or `None` sets `blocked_until = now + max(min(5 × 2**consecutive_failures, 900), retry_after or 0)` and adds 1 to `consecutive_failures`.
    - Any 2xx resets both.
    - Other statuses leave the bucket unchanged.
  - `class DbBudget` (implements `Budget`): `acquire` and `report` run `select_for_update()` on the `SourceBudget` row inside `transaction.atomic()` and apply `take`/`backoff`. A missing row raises `KeyError`.
    - `wait(key: str, *, max_wait: float = 60.0, sleep=time.sleep, stop: threading.Event | None = None) -> None` loops on `acquire`. When the total wait would exceed `max_wait`, it raises `BudgetExhausted` (P3).
    - `blocked(key: str, now: datetime) -> bool` is True when blocked or at the daily cap.
  - `seed_budgets() -> None`: `get_or_create` each `settings.SOURCE_BUDGETS` row with `tokens=capacity` (P10).
- Produces (`catalog/fetch.py`):
  - Constants: `QUEUE_LIMIT = 200`, `CLIENT_JOBS_PER_MINUTE = 10`, `VISITOR_TTL = timedelta(seconds=120)`, `STUCK_AFTER = timedelta(minutes=5)`, `RETENTION = timedelta(days=7)`, `LIVE_LIMIT = 25`.
  - `enqueue(phrase: str, origin: str, now: datetime, sources: Mapping[str, SourceInfo], *, client_key: str | None) -> list[FetchJob]`. It returns the active jobs for the phrase (new or reused). For each source, it skips when:
    - the phrase is personal;
    - a `succeeded` job for (source, phrase) has `finished_at >= now − refresh_after`;
    - any of the source's budget keys is `blocked`;
    - `QUEUE_LIMIT` queued jobs exist;
    - or `client_key` is given and `web.ratelimit.allow("fetch-jobs", client_key, 10, timedelta(minutes=1))` is False (checked per new job; no `client_key` means no throttle, as for warm jobs).

    Otherwise it inserts `FetchJob(status=queued, not_before=now)`. An `IntegrityError` from the partial unique constraint means another request won, so it reuses that job. The whole call is one short `transaction.atomic()`.
  - `claim(now: datetime) -> FetchJob | None`:
    - Filter `status=queued, not_before<=now`.
    - Order by `Case(When(origin="warm", then=1), default=0)` then `created_at`, with `select_for_update(skip_locked=True)`, first row.
    - Set `running`, `started_at=now`, and add 1 to `attempts`.
  - `run_job(job: FetchJob, sources: Mapping[str, LiveSearchSource], budget: Budget, now: datetime) -> None`:
    - Calls `source.live_search(job.phrase, LIVE_LIMIT, budget)` and then `ingest_live(result, source.name, now)`.
    - On success, the job ends `succeeded` with `result_count` set.
    - `BudgetExhausted` sends the job back to `queued` with `not_before = now + wait` and `attempts` minus 1.
    - Any other exception marks it `failed` with `error=type(exc).__name__`.
    - `finished_at` is set on every terminal state.
    - A source missing from `sources` gives `skipped`.
  - `housekeeping(now: datetime) -> dict[str, int]`:
    - Expires `search`/`list` jobs still queued at `created_at <= now − VISITOR_TTL`.
    - Resets `running` jobs with `started_at <= now − STUCK_AFTER`: to `queued` when `attempts < 2`, else to `failed` with `"stuck"`.
    - Deletes terminal jobs with `finished_at < now − RETENTION`.
  - `fetch_loop(stop: threading.Event, *, sources: Mapping[str, LiveSearchSource], budget: Budget, poll: float = 1.0, now=timezone.now) -> None`:
    - Repeats until `stop` is set: `claim`, then `run_job` (or `stop.wait(poll)` when there is nothing to claim).
    - Every exception is caught, logged as `"fetch job=%s source=%s failed: %s"` with the id, source and type name, and followed by `django.db.close_old_connections()`.
    - On exit it calls `connection.close()`.
- Produces (`catalog/ingest.py`): `ingest_live(result: LiveSearchResult, source_name: str, now: datetime) -> int`.
  - Each live `RawOffer` is normalized with `off_lookup(raw.upc)`, then written with `_upsert` (Task 4, `kind=live`, `records_price_history=False`) whether or not it is curated.
  - `result.off_records` are saved with `enrichment.off._save`.
  - `result.observations` pass `skip_reason` (site country and currency, assisted setting, today) and go to `upsert_observations(kept, now, off_lookup=…, run=None)`.
  - Touched products are merged.
  - It returns the number of distinct touched products. One bad offer is skipped, never fatal.
- `ingest(connector, category, now, *, off_lookup=…, budget: DbBudget | None = None)`: when `budget` is given and `connector.name in settings.SOURCE_BUDGETS`, it calls `budget.wait(connector.name)` before each search. `BudgetExhausted` fails the run with `"budget exhausted"`.
- `OpenPricesClient(..., budget_wait: Callable[[str], None] | None = None)`: when `budget_wait` is set, it calls `budget_wait("openprices")` instead of `_pace()` before each request. The daily sync passes `DbBudget().wait`.
- `registry.live_source_info(environ=os.environ, *, live_off: bool = False) -> dict[str, SourceInfo]`, where `SourceInfo` is `@dataclass(frozen=True) SourceInfo(name: str, budget_keys: tuple[str, ...], refresh_after: timedelta)` in `connectors/live.py`. It reads class attributes without constructing anything (like `freshness.max_cache_age`), so the web process opens no HTTP client. It covers the same sources `live_sources` would build: Walmart when its required env vars are set, and `off-prices` when `live_off`. `enqueue`, the search view and `warm_round` take `Mapping[str, SourceInfo]`. A built `LiveSearchSource` has the same attributes, so it also satisfies the mapping.
- `registry.live_sources(environ=os.environ, *, live_off: bool = False) -> dict[str, LiveSearchSource]` (worker only). It includes `available(environ)` entries that have `live_search` (Walmart, once Task 10 adds it), and `off-prices` when `live_off` is True (Task 9 adds the class). Until those tasks land it returns `{}`.
- `scheduler.run_loop(...)`:
  - At start it calls `seed_budgets()`.
  - When `fetch=True` (the default from `run_scheduler`; tests pass `False`), it starts `settings.FETCH_THREADS` daemon threads running `fetch_loop(stop, sources=live_sources(live_off=settings.LIVE_OFF_ENABLED), budget=DbBudget())`.
  - It joins them on exit and closes the sources.
  - `tick()` also calls `housekeeping(now)`.
  - SIGTERM sets the shared `stop`, which ends both.

- [ ] **Step 1: Write failing tests:**
  - `tests/catalog/test_budget.py::test_rate_limiter` (spec D7):
    - A bucket with capacity 10 refilling 10/min grants 10 at t0.
    - It refuses the 11th with `wait == approx(6.0)`, and grants again at t0 + 6 s.
    - `backoff` with 429 and `retry_after=120` blocks for 120 s; a 503 with `consecutive_failures=3` blocks for 40 s; a 200 resets.
    - With `daily_limit=2`, the third take is refused until the next UTC midnight, and is granted at 00:00:01 UTC.
  - `test_two_threads_one_token`: a `SourceBudget` with `tokens=1.0` and `refill_per_second=0`. Two threads call `DbBudget().acquire("walmart")` at once (a `threading.Barrier`). Exactly one succeeds and one raises `BudgetExhausted`. Mark it `@pytest.mark.django_db(transaction=True)`.
  - `test_wait_gives_up_after_max_wait`: a capped row and a fake sleep make `wait(..., max_wait=60)` raise `BudgetExhausted` without sleeping more than 60 s in total.
  - `tests/catalog/test_fetch.py`:
    - `test_enqueue_dedup_concurrent` (transaction=True, two threads, one active job).
    - `test_refresh_after_skip`.
    - `test_personal_phrase_not_enqueued`.
    - `test_blocked_source_not_enqueued`.
    - `test_queue_limit`: the 201st job is not created.
    - `test_client_throttle`: the 11th new job in a minute for one client is not created.
    - `test_visitor_before_warm`: an older warm job and a newer search job → `claim` returns the search job.
    - `test_skip_locked_two_threads` (transaction=True): 2 threads and 2 jobs → each claims a different job.
    - `test_budget_wait_requeues`: a fake source raising `BudgetExhausted(5)` → the job is `queued` with `not_before == now + 5 s`.
    - `test_expiry_and_stuck_reset`: a search job queued 121 s → `expired`; a warm job queued 121 s stays. A job running 6 min with `attempts=1` → `queued`; with `attempts=2` → `failed`. A 7-day-old finished job is deleted.
    - `test_fetch_loop_survives_job_error` (Review Focus 4): the first job's source raises `RuntimeError` and the second succeeds. After running the loop until both are terminal, the first is `failed` with error `"RuntimeError"` and the second is `succeeded`. A `connection.close()` inside the first job's source does not stop the second.
    - `test_ingest_live_writes_curated_and_generic`: a `LiveSearchResult` with a 2% milk and an oat milk raw offer stores one curated and one generic offer, and no `PriceHistory` rows.
    - `test_scheduled_ingest_draws_walmart_budget`: with a walmart row of `tokens=0` and `refill_per_second=0`, `ingest(FakeConnector(name="walmart"), …, budget=DbBudget())` fails with "budget exhausted" (fake sleep).
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_budget.py tests/catalog/test_fetch.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the modules, the scheduler wiring and the `OpenPricesClient` hook. The existing Open Prices client tests keep using `_pace` (no `budget_wait`).
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS, including the existing scheduler tests with `fetch=False`.
- [ ] **Step 5: Commit** `feat: DB token-bucket budgets, fetch job queue and worker fetch loop (#N)`

---

### Task 9: OFF text search → Open Prices barcode source (behind `UNITPRICER_LIVE_OFF`, off by default)

**Files:**
- Create: `connectors/off_search.py`, `tests/connectors/test_off_search.py`, `tests/connectors/recordings/off_search/` (a search response for `oat milk`, a 503 then a 200, an empty result), `tests/connectors/recordings/open_prices/` (a `product_code__in` response)
- Modify: `connectors/open_prices.py` (`fetch_codes`), `connectors/registry.py`, `docs/research/2026-11-07-open-food-facts.md` (a "Text search" section)

**Interfaces:**
- Consumes: `Budget`, `BudgetExhausted`, `LiveSearchResult` (Task 8); `normalize_upc`, `OffRecord`, `_record_from_dict` (`enrichment/off.py`); `parse_item` (Open Prices).
- Produces:
  - `OpenPricesClient.fetch_codes(codes: Sequence[str], since: date, currency: str, budget: Budget) -> list[Observation]`. One request: `budget.acquire("openprices")`, then `GET PRICES_URL` with `product_code__in=",".join(codes)`, `currency`, `date__gte`, `type=PRODUCT`, `location__type=OSM`, `duplicate_of__isnull=true`, `order_by=-id`, `size=100`, `page=1`. It calls `budget.report("openprices", status, retry_after)`, and a non-200 raises `ConnectorError`. The codes are at most 50.
  - `class OffPricesSource`:
    - Class attributes: `name = "off-prices"`, `budget_keys = ("off-search", "openprices")`, `max_cache_age = timedelta(days=90)`, `refresh_after = timedelta(hours=24)`.
    - `__init__(self, client: httpx.Client | None = None, prices: OpenPricesClient | None = None, today: Callable[[], date] = …)`.
    - `live_search(phrase, limit, budget)`:
      1. Make one OFF text search with `budget.acquire("off-search")` before each attempt. A 503 is retried at most twice, each retry taking its own token. `report` is called after each response.
      2. Normalize the codes and drop unparseable ones.
      3. Call `fetch_codes` (skipped when there are no codes).
      4. Return `LiveSearchResult(observations=…, off_records=…, skipped=…)`.
    - The User-Agent is `enrichment.off.USER_AGENT`.
  - `registry.live_sources(..., live_off=True)` includes `"off-prices": OffPricesSource()`.
- Endpoint: confirmed in Step 1. The expected request is `GET https://world.openfoodfacts.org/cgi/search.pl` with `search_terms=<phrase>`, `json=1`, `page_size=50`, `fields=code,product_name,brands,quantity,product_quantity,product_quantity_unit,labels_tags,categories_tags`, and a US-only filter: `tagtype_0=countries`, `tag_contains_0=contains`, `tag_0=united-states`. If Step 1 shows that `/api/v2/search` accepts free text, use it and update this line in the PR.

- [ ] **Step 1: Confirm and record the endpoint (manual, once).**
  - Using the app User-Agent, make at most 12 requests over at least 2 minutes (1 per 10 s) for 3 phrases ("oat milk", "dish soap", "zzzz-nothing").
  - Save trimmed responses as recordings. Use `recording_name` to name them, keep 5 products, and strip any user fields.
  - Write down any rate-limit headers and every 429 or 503 seen in the research doc's new "Text search (measured 2026-11-xx)" section.
  - Do not probe beyond the documented ~10 req/min. If the headers state a different limit, change the `off-search` defaults in `config/settings.py` and `.env.example` to match (D10).
  - Record one Open Prices `product_code__in` response for the codes returned.
- [ ] **Step 2: Write failing tests** in `tests/connectors/test_off_search.py`, using a strict recorded transport and a fake `Budget` that records `acquire`/`report` calls:
  - `test_search_then_prices`: "oat milk" returns `off_records` with labels and categories, and `observations` parsed from the Open Prices recording. `acquire` is called with `["off-search", "openprices"]`, in that order.
  - `test_off_503_retried_twice_each_with_token`: 503 then 200 → success with 2 `off-search` acquires. 503 three times → `ConnectorError`, with 3 acquires and `report(…, 503)` called three times.
  - `test_no_codes_no_prices_request`: the empty result → no Open Prices request, an empty result, and `skipped == 0`.
  - `test_budget_exhausted_propagates`: a fake budget raising `BudgetExhausted(3)` on `off-search` → it propagates, and no HTTP request is made.
  - `test_us_only_and_fields_params`: the recorded request carries the US filter and exactly the `fields` list above.
  - `test_live_off_off_by_default`: `live_sources({})` and `live_source_info({})` have no `off-prices`; with `live_off=True` both have it, and `live_source_info` gives `budget_keys == ("off-search", "openprices")`.
  - `tests/catalog/test_fetch.py::test_off_prices_job_end_to_end`:
    - `run_job` with `OffPricesSource` over the recordings writes `OpenPriceObservation` rows and observed offers (generic or curated) and updates `OffEntry.labels` and `categories`.
    - The job's `result_count` equals the distinct products touched.
    - With prices absent, the job ends `succeeded` with `result_count == 0` and `OffEntry` still updated.
- [ ] **Step 3: Run** `uv run pytest tests/connectors/test_off_search.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement** `connectors/off_search.py`, `fetch_codes` and the registry entry.
- [ ] **Step 5: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 6: Commit** `feat: Open Food Facts text search to Open Prices live source, off by default (#N)`

---

### Task 10: Walmart live search adapter

**Files:**
- Modify: `connectors/walmart.py`, `connectors/registry.py`
- Create: `tests/connectors/recordings/walmart/` (one search response for `oat milk` containing `categoryPath`, trimmed from the documented sample format, plus a 429 response with `Retry-After`)
- Test: `tests/connectors/test_walmart.py`, `tests/catalog/test_fetch.py`

**Interfaces:**
- Consumes: `Budget`, `LiveSearchResult` (Task 8); `_map` with `category_path` (Task 4).
- Produces:
  - `WalmartConnector` attributes: `budget_keys = ("walmart",)`, `refresh_after = timedelta(hours=12)`. `max_cache_age` (24 h) and `records_price_history` (False) are unchanged.
  - `search()` is split into `_request(params) -> httpx.Response` (it raises `ConnectorError` on a transport error) and `_parse(resp) -> SearchResult`. `search()` behaves exactly as before.
  - `live_search(self, phrase: str, limit: int, budget: Budget) -> LiveSearchResult`:
    - Calls `budget.acquire("walmart")`, then `_request`.
    - A transport error → `report("walmart", None)`, then raise.
    - Otherwise → `report("walmart", resp.status_code, <Retry-After as float or None>)`, then `_parse`.
    - Returns `LiveSearchResult(offers=…, skipped=…)`.
  - `live_sources()` includes `walmart` whenever `available()` builds it. Without `WALMART_*` it is absent (spec §11).

- [ ] **Step 1: Write failing tests:**
  - `test_live_search_maps_category_path`: the recorded `oat milk` response gives offers with `fields["category_path"]` set. The budget sees `acquire("walmart")` once and `report("walmart", 200, None)`.
  - `test_live_search_429_reports_retry_after`: the 429 recording → `ConnectorError`, with `report("walmart", 429, 30.0)`.
  - `test_search_unchanged`: the existing `test_walmart.py` tests pass untouched.
  - `test_walmart_absent_without_credentials`: `live_sources({})` and `live_source_info({})` have no `walmart`. With the test key env from `test_registry.py`, both have it, and `live_source_info` constructs no `WalmartConnector` (patch the class to fail on `__init__`).
  - `tests/catalog/test_fetch.py::test_walmart_job_end_to_end`: `run_job` with a recorded Walmart source writes live offers with `last_seen_at == now` and no `PriceHistory`. At `now + 25 h`, `fresh_offers` no longer returns them.
- [ ] **Step 2: Run** `uv run pytest tests/connectors/test_walmart.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the split, `live_search` and the attributes.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Walmart live search source (#N)`

---

### Task 11: Enqueue from the page and HTMX polling

**Files:**
- Create: `web/templates/web/_poller.html`, `tests/web/test_polling.py`
- Modify: `web/search.py`, `web/templates/web/generic.html`, `_generic_results.html`, `web/templates/web/disclosure.html`

**Interfaces:**
- Consumes: `enqueue`, `FetchJob.ACTIVE`/`TERMINAL` (Tasks 3, 8); `live_source_info(live_off=settings.LIVE_OFF_ENABLED)` (Task 8; no clients are built in the web process); `client_ip`; `normalize_phrase`, `is_personal`.
- Produces (`web/search.py`):
  - `POLL_WINDOW = 15` (seconds).
  - `@dataclass(frozen=True) JobState(source: str, status: str, finished_at: datetime | None, result_count: int)`.
  - `should_poll(jobs: Sequence[JobState], start: float, now: float) -> bool`. It is False when `start > now`, when `now >= start + POLL_WINDOW`, or when no job is in `FetchJob.ACTIVE`. Otherwise it is True.
  - `live_status(jobs: Sequence[JobState], polling: bool, enqueue_refused: bool) -> str | None`, using the Global Constraints copy:
    - Polling with active jobs → "checking {names joined with ' and '}…".
    - Any succeeded job with `result_count > 0` → "updated with {sum} new products".
    - A failed job → "{Source} didn't respond; showing saved prices."
    - Stopped by timeout with active jobs → "Still checking {names}. Reload in a minute for more."
    - `enqueue_refused` (blocked, queue full or throttled) → "Live search is busy; showing saved prices."
  - **Full render** (not HTMX, not personal, apex excluded by URLconf):
    - After the page's own queries, call `enqueue(normalize_phrase(q), "search", now, sources, client_key=client_ip(request))`.
    - When any returned job is active, render `_poller.html` with `hx-get="{% url 'search' %}?{{ poll_query }}"`, `hx-trigger="every 2s"`, `hx-swap="outerHTML"`. `poll_query` is built with `urlencode` from `q`, the active facet params, `poll=<int(now)>` and `seen=<int(now)>`.
  - **Poll request** (`HX-Request` and `poll` present):
    1. In one query, load `JobState`s for the phrase where the status is active or `finished_at >= start`.
    2. If `should_poll` is True and no job has `finished_at > seen`, return `_poller.html` alone with `seen` updated. That is ≤ 2 queries, with no search, demand or enqueue.
    3. Otherwise re-run the search and return the poller plus out-of-band results, status and facets.
    4. When `should_poll` is False, render the poller **without** `hx-trigger` (the stop), with the final status.

    Invalid `poll` or `seen` values (non-integer, or `start` more than 15 s in the past or in the future) stop polling at once.
  - The disclosure page gains the exact disclosure sentence from Global Constraints.

- [ ] **Step 1: Write failing tests** in `tests/web/test_polling.py`:
  - `test_polling_stop_condition` (spec D7): `should_poll` is True with a queued job at `now = start + 3`; False when all jobs are terminal; False at `now = start + 15`; False with `start = now + 5`. The final rendered poller has no `hx-trigger`.
  - `test_first_render_enqueues_and_polls`: with a fake live source registered, `GET /search?q=oat milk` creates one `FetchJob(origin="search")`, and the HTML contains `hx-trigger="every 2s"` and "checking Walmart…".
  - `test_idle_poll_is_cheap`: with the job still queued, the poll response is only the poller, made in ≤ 2 queries (`django_assert_max_num_queries(2)`), with no new `FetchJob` and no `SearchDemand` change.
  - `test_poll_after_job_finishes_swaps_results`: mark the job succeeded with `result_count=3` and add 3 offers. The next poll returns OOB results containing them, plus "updated with 3 new products".
  - `test_timeout_message`: a poll at `start + 16` with a still-queued job → no `hx-trigger`, and "Still checking Walmart. Reload in a minute for more."
  - `test_failed_source_message`: a failed job → "Walmart didn't respond; showing saved prices."
  - `test_busy_when_refused`: the source's budget is blocked → no poller, and "Live search is busy; showing saved prices."
  - `test_nothing_found_after_polling`: nothing held → "No saved prices match 'zzz'" and "checking Walmart…". The final poll after the job succeeds with `result_count=0` shows "Nothing found. Try fewer words or browse a category."
  - `test_personal_phrase_no_job_no_poller`: `q=555-123-4567 milk` → held results, no `FetchJob`, no poller.
  - `test_poll_sets_no_cookies`: neither the first render nor a poll sets cookies.
  - `test_poller_url_escapes_query` (Review Focus 3): for `q='"><script>x</script>'`, `q='milk&poll=1'` and `q='milk%26store%3Dx'`, the poller's `hx-get` holds exactly one `poll=` and one `q=` parameter (parsed with `urllib.parse.parse_qs` from the attribute), the `q` value round-trips, and no raw `<script>` is in the HTML.
  - `test_htmx_facet_change_does_not_enqueue` (P6): an `HX-Request` facet swap creates no job.
- [ ] **Step 2: Run** `uv run pytest tests/web/test_polling.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the enqueue call, the poll branch, `_poller.html`, the status wiring and the disclosure copy.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS. Task 6's query-count bound for the full render is raised by at most the enqueue's queries, and is still the same for 3 and 60 products.
- [ ] **Step 5: Commit** `feat: background fetch on new searches with HTMX polling (#N)`

---

### Task 12: Demand counting, "curate next" and warm refresh

**Files:**
- Create: `catalog/demand.py`, `tests/catalog/test_demand.py`
- Modify: `web/search.py` (record demand), `catalog/scheduler.py` (prune and warm round in `tick`), `catalog/admin.py` (curate-next admin)

**Interfaces:**
- Consumes: `SearchDemand`, `FetchJob` (Task 3); `enqueue` (Task 8); `match_offers` (Task 5); `is_personal`.
- Produces (`catalog/demand.py`):
  - Constants: `LIST_MIN_DAYS = 3`, `PRUNE_AFTER = timedelta(days=30)`, `MAX_PHRASES = 50_000`, `WARM_TOP = 50`.
  - `record_demand(phrase: str, today: date) -> None`: one `INSERT … ON CONFLICT (phrase) DO UPDATE SET count = count + 1, days_seen = days_seen + (excluded.last_seen > search_demand.last_seen)::int, last_seen = GREATEST(…)` statement via `connection.cursor()`. A new row has `count=1`, `days_seen=1`, `last_seen=today`. It is a no-op for empty or personal phrases.
  - `prune_demand(today: date) -> int`:
    - Deletes rows with `days_seen < 3` and `last_seen < today − 30 days`.
    - If more than `MAX_PHRASES` rows remain, deletes the lowest `(days_seen, last_seen)` rows down to `MAX_PHRASES`.
    - Returns the number deleted.
  - `curate_next() -> QuerySet[SearchDemand]`: `days_seen >= 3`, ordered by `-days_seen`, `-count`, `-last_seen`.
  - `warm_due(now: datetime) -> bool`: True when no `FetchJob(origin="warm")` was created within `settings.WARM_INTERVAL`, or when none exist and `curate_next()` has rows.
  - `warm_round(now: datetime, sources) -> int`:
    - Marks queued warm jobs `expired`.
    - Calls `enqueue(phrase, "warm", now, sources, client_key=None)` for the top `WARM_TOP` non-personal phrases of `curate_next()`.
    - Returns the number of jobs created.
- Search view: on a full generic render with a non-personal phrase, call `record_demand(normalize_phrase(q), now.date())` (P6). It is never called on polls, HTMX swaps, curated redirects or the apex.
- `tick()`: calls `prune_demand(now.date())`, which is idempotent and so is safe on every tick, and runs `warm_round` when `warm_due(now)` (sources from `live_source_info`).
- Admin: a `CurateNext` proxy model of `SearchDemand`, read-only, with queryset `curate_next()`.
  - It lists phrase, days_seen, count, last_seen, "held results" (`len({o.product_id for o in match_offers(phrase, now).offers})`) and "top store path" (the most common non-null `category_path` among those offers, ties alphabetical).
  - 50 rows per page. The raw `SearchDemand` is not registered, so phrases seen on fewer than 3 days never show.

- [ ] **Step 1: Write failing tests** in `tests/catalog/test_demand.py`:
  - `test_counts_once_per_render_never_per_poll`: one generic GET → `count == 1`; three polls → still 1; a curated redirect → no row; a personal phrase → no row.
  - `test_days_seen_once_per_day`: two renders on day 1 and one on day 2 → `count == 3`, `days_seen == 2`.
  - `test_prune_thresholds` (D15): `days_seen=2` last seen 31 days ago → deleted; 29 days ago → kept; `days_seen=3` last seen 60 days ago → kept. With `MAX_PHRASES` patched to 2 and 3 rows, the lowest `(days_seen, last_seen)` row is deleted.
  - `test_admin_hides_under_three_days`: as a superuser, the curate-next changelist shows the 3-day phrase and not the 2-day one, and the 3-day phrase shows its held-result count and top path.
  - `test_warm_round_top_50`: 60 eligible phrases plus one personal one with high counts → 50 jobs per source, `origin="warm"`, the personal phrase excluded, ordered by the curate-next order. A phrase fetched successfully within `refresh_after` is skipped.
  - `test_warm_due_every_12h`: after a round at t0, `warm_due(t0 + 11h)` is False and `warm_due(t0 + 12h)` is True. A new round expires the previous round's still-queued warm jobs.
  - `test_demand_rows_hold_no_personal_fields`: `SearchDemand` field names are exactly `{"id", "phrase", "count", "days_seen", "last_seen"}`, and `FetchJob` has no field matching `ip|user|session|agent|referer`.
- [ ] **Step 2: Run** `uv run pytest tests/catalog/test_demand.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `catalog/demand.py`, the view call, the `tick` wiring and the admin.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: anonymous search demand, curate-next admin and warm refresh (#N)`

---

### Task 13: Docs, roadmap and spec bookkeeping

**Files:**
- Modify: `docs/roadmap.md`, `docs/superpowers/specs/2026-11-07-unitpricer-design.md` (§16 decision log only), `README.md`, `CLAUDE.md`, `CONTRIBUTING.md` (how generic mode relates to adding a category), `.env.example` (check that every new variable is present)

**Interfaces:** none (docs only).

- [ ] **Step 1: Roadmap.**
  - Add the milestone **"Open search (#65)"** between Checkpoint B and M4, with `**Plan:** [plans/2026-11-08-open-search.md]` and these tasks:
    1. Generic measure and phrase normalization, plus unified units
    2. Schema and backfill
    3. Generic results page with facets and near-misses
    4. Fetch queue, budgets and worker thread
    5. OFF → Open Prices live source (off by default)
    6. Walmart live source (when credentials arrive)
    7. Demand counting, "curate next" and warm refresh
  - Update the overview diagram so that M3 → ⛳ B → Open search → M4.
  - In M4's table, change 4.1 to "spec = curated category + filters, or generic query + facets + dimension; target quantity". Under M5, change 5.2 to "Household categories first; food categories from 'curate next' demand (see the open-search spec §14.2)".
  - Remove "Live per-search price fetching" from any non-goal list.
- [ ] **Step 2: Main spec §16:** add the decision-log line "Open-ended search (#65): generic tier with measure groups, structured facets, background live fetch, anonymous demand counts; curated categories as upgrades. See `2026-11-08-open-search-design.md`." No other main-spec text changes (the open-search spec states that it wins where they differ).
- [ ] **Step 3: README / CLAUDE.md commands:**
  - `uv run --env-file .env python manage.py backfill_generic`, run once after migrating to 0007.
  - `manage.py bench_search` (DEBUG only; local p95 check).
  - `UNITPRICER_LIVE_OFF=1` turns on the OFF → Open Prices live source.
  - Budgets live in the admin, and a changed `UNITPRICER_BUDGET_*` setting applies only to new rows (P10).
  - The worker now also runs the fetch thread(s).
- [ ] **Step 4: Run** `uv run pytest -q` (doc links aren't tested, but the pre-commit hooks run). Expected: PASS.
- [ ] **Step 5: Commit** `docs: open search in roadmap, README and decision log (#N)`

---

## Plan amendments (to apply when those plans are next executed; not edited here)

### M4 Lists and accounts (`docs/superpowers/plans/2026-11-08-m4-lists-accounts.md`), per spec §14.1

1. **Header:**
   - Add the open-search spec to **Spec**.
   - Add "Lands after open search (#67); depends on `catalog/generic.py`, `Offer.measure_dim`/`measure_amount`, and `catalog.fetch.enqueue`."
2. **Global Constraints:** add "Generic items never resolve on an attribute read from a title. They use only the open-search text match and structured facets, and only offers whose `measure_dim` equals the item's dimension."
3. **Review Focus:** add item 6, "A generic item never resolves to an offer outside its dimension or to an offer matched only by an attribute read from the title. Owned by Task 4 (`test_generic_item_stays_in_dimension`)."
4. **File Structure:** `catalog/generic.py  # + generic_matching_offers()`.
5. **Task 1:**
   - Add `generic_matching_offers(query: str, facets: Mapping[str, list[str]], dimension: str, stores: frozenset[str] | None, now: datetime) -> list[Offer]` in `catalog/generic.py`. It runs `match_offers(query, now, stores=stores)`, then `apply_filters(parse_generic_filters(QueryDict from facets, …))`, keeping `measure_dim == dimension`.
   - Add `GENERIC_TARGET_UNITS`, with entries of the form dimension → unit code → (label, canonical per unit):
     - volume: `floz`, `gal`, `ml`, `l` (canonical mL)
     - mass: `oz`, `lb`, `g`, `kg` (g)
     - count: `ct`
     - oz_labeled: `oz` ("oz (as labeled)", 1)
   - Add `generic_target_to_canonical(amount, unit_code, dimension) -> Decimal`, with the same rejections as `target_to_canonical`.
   - Make `target_units_for("oz_labeled", any) == ["oz"]`.
   - Add tests `test_generic_matching_equals_group_population` (the same product ids as the generic page's group for that dimension) and `test_generic_target_units`.
6. **Task 2:** `fit_offer(offer, measure_key, target)` reads the package size from `offer.measure_amount` when `measure_key` is one of `volume`, `mass`, `oz_labeled` or `count` and `offer.measure_dim == measure_key`, and otherwise from `offer.measures[measure_key]` as before. The duck-typed `offer` gains `.measure_dim` and `.measure_amount`. Add `test_fit_generic_measure_amount`.
7. **Task 3:**
   - `ItemSpec` gains `kind: Literal["curated", "generic"] = "curated"`, `query: str = ""` (normalized phrase) and `facets: Mapping[str, tuple[str, ...]] = {}`. `category: str | None`. For generic items, `target_measure` is the dimension name.
   - `to_json`/`from_json` round-trip `kind`, and data without `kind` reads as curated.
   - `ListItem`:
     - `category_slug` becomes `null=True, blank=True`.
     - Add `kind = CharField(10, default="curated")`, `query = CharField(100, blank=True)` and `facets = JSONField(default=dict)`.
     - Add `CheckConstraint(condition=Q(kind="curated", category_slug__isnull=False) | (Q(kind="generic") & ~Q(query="")), name="listitem_kind_fields")`.
   - Add tests `test_item_spec_generic_round_trip`, `test_legacy_json_reads_as_curated` and `test_listitem_kind_check_constraint`.
8. **Task 4:**
   - `resolve_item` branches on `kind`:
     - A generic item resolves over `generic_matching_offers`.
     - An unknown facet key gives "Filter no longer valid: <key>".
     - "This category is no longer available" and the curated filter reasons apply to curated items only.
     - "No current prices match at your stores" and "Size unknown for every match" apply to both.
   - `resolve_list` calls `catalog.fetch.enqueue(item.query, "list", now, live_source_info(live_off=settings.LIVE_OFF_ENABLED), client_key=None)` once per distinct generic phrase, under normal dedup and budgets. There is no poll on the list page.
   - Add tests `test_generic_item_stays_in_dimension` (Review Focus 6), `test_generic_unknown_facet_unresolved` and `test_list_view_enqueues_list_jobs`.
9. **Task 5:**
   - Each generic measure group gets an "Add to list" link: `GET /lists/add?kind=generic&q=<phrase>&dim=<dimension>&<facet params>`.
   - `lists:add` validates `dim` against the generic dimensions and the facets with `parse_generic_filters` (400 on an invalid key in the POST). It pre-fills the target from the group's top row `measure_amount` in the viewer's first unit, and summarizes the item as "'<phrase>' · <facet labels> · by <volume|weight|oz as labeled|count>".
   - Add test `test_add_generic_item_from_group`.
10. **Exit criteria:** add "From a generic results page, 'Add to list' on the volume group creates a generic item that resolves on the list page."

### Category expansion (`docs/superpowers/plans/2026-11-08-category-expansion.md`), per spec §14.2

1. **Header:**
   - Change **Goal** to "Grow the curated catalog with the household categories that need special units, then with top-demand categories chosen from 'curate next', each with ≥ 8 real-title fixtures."
   - Add the open-search spec §14.2 to **Spec**.
2. **Task 1:** add the note "Executed first, as the open-search prerequisite (#67). Skip if already merged."
3. **Order of work:** Task 1 → Task 2 (`laundry`) → Task 3 (`tissue`) → Task 8 (facial tissue, trash bags) → Task 9 (laundry detergent, dish soap, dishwasher detergent).
4. **Tasks 4–7 (food batches):**
   - Add a gate at the top of each: "Gated. Proceed per category only after `SearchDemand` has ≥ 4 weeks of data, when the category's phrases rank in the top 10 of 'curate next' **and** it needs a precise attribute (fat %, roast, grain …) or a special unit. Mass- and volume-priced foods with no such need stay generic."
   - The derived-count change to `test_sync_categories_command_prints_counts` (now in Task 4 Step 2) moves to Task 8 Step 2, since Task 8 is now the first category batch to run.
5. **Global Constraints:** change "(Task 4)" in the `created=5` line to "(the first category batch that runs, now Task 8)".
6. **Exit criteria:**
   - Replace "25 categories" with "the household categories above (facial tissue, trash bags, laundry detergent, dish soap, dishwasher detergent) plus any top-demand categories chosen at review, each with ≥ 8 fixtures".
   - Replace "`created=20` … (25 on an empty one)" with "`created=<number of new category files>`".
7. **D12 display:** Tasks 2–3 add `per_load` and `sheets_x_ply` `DISPLAY` entries, which are unaffected. Any test in those tasks that pins "/ 100 mL" or "/ 100 g" uses "/ L" or "/ kg" instead.

---

## Exit criteria

- Every D7 named test exists and passes: `test_measure_grouping`, `test_bare_oz_own_group`, `test_facet_needs_two_values`, `test_rate_limiter`, `test_polling_stop_condition`, `test_generic_never_invents_attribute`. So does every Review Focus test. `uv run pytest` is green locally and in CI.
- With `demo_data` loaded and a fake or recorded live source, `/search?q=oat milk` renders measure groups and facets, polls for at most 15 s, and records one `SearchDemand` row.
- `bench_search` p95 < 300 ms at 100,000 offers, recorded in the final PR.
- `/c/dairy/milk/` shows metric prices per L.
- No new dependencies. `normalize/` and `connectors/` import no Django.
