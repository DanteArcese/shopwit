# Nutrition per Dollar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Food category pages and generic search results can rank by "Protein per $" (cost per 100 g protein) or "Calories per $" (cost per 1,000 kcal), using only Open Food Facts per-100 g / per-100 ml values, and leave every product they can't rank honestly unranked with a reason.

**Architecture:** OFF nutriments (protein, kcal, basis) are fetched with every OFF lookup and stored on `OffEntry`. A pure `normalize/nutrition.py` turns (price, OFF nutrition, pack measure) into costs or reasons, and `catalog/ingest._upsert` stores the result as JSON on each offer (`Offer.nutrition`), so ranking reads only the offer rows it already loads. `browse()` and `group_products()` gain a `rank` argument; the views read it from the `rank` URL param. Existing rows are backfilled through the existing `off-product` budget and size-backfill run, with a settings-driven daily boost for the first week.

**Tech Stack:** Django 5.2, Postgres 17, HTMX, httpx (`MockTransport` recordings), pytest-django. No new dependencies.

**Spec:** Approved design in issue #95 (binding; quoted in the task prompt and summarized under Global Constraints). It amends feature 7 of `docs/superpowers/specs/2026-11-07-unitpricer-design.md` ("cost per 10 g protein" becomes cost per 100 g protein and per 1,000 kcal). Principles 2 (honest ranking) and 3 (never guess) of that spec and §5 of `docs/superpowers/specs/2026-11-08-open-search-design.md` (no comparison across dimensions) still hold.

## Decisions

Plan-level choices the design leaves open.

- **N1 Where costs are computed.** In the normalize layer, as a pure function (`normalize.nutrition.offer_nutrition`). `catalog.ingest._upsert` calls it at write time, like `generic_unit_price`, and stores the result in a new `Offer.nutrition` JSONField. Ranking never computes and never queries per row. Nutrition stays out of `Offer.measures`: measures take part in the product merge (`quantity:conflict`) and in `unit_prices`, and nutrition must do neither. Costs depend only on the offer's own price and pack, so `merge_product` never touches them.
- **N2 Pack amount.** `pack_of(measures, measure_dim, measure_amount)` uses the curated measures first (`mass_g` or `volume_ml`, which category pages already display), else the generic measure when its dimension is `mass` or `volume`. `count`, `oz_labeled` and unknown sizes have no pack, so they can't be ranked by nutrition. Eggs (count) and labeled oz are never converted to grams.
- **N3 Basis.** OFF `nutrition_data_per` decides the basis. `"100g"` works only with a mass pack and `"100ml"` only with a volume pack. Anything else is unranked: a mismatch, `"serving"`, or a missing or unknown value. OFF's `_100g` values are used for the 100 ml basis too, since OFF stores them under those keys. Nothing is ever converted between ml and g, and OFF's own per-serving to per-100 g conversions are not used.
- **N4 Implausible values.** If any stored value is implausible, both metrics are unranked for that offer, because the entry itself is suspect. The rules are protein < 0 or > 100 per 100 g/ml (the protein can't weigh more than the mass it's measured in) and kcal < 0 or > 900. Zero protein or zero kcal is plausible, but it's unranked as "no protein" or "no calories", since the cost would be infinite.
- **N5 Live (Walmart) offers.** These get nutrition only through OFF, by UPC, through the `off_lookup` that ingestion already does. Walmart's own nutrition text is never read. An offer without a UPC or without an `OffEntry` gets `Offer.nutrition == {}` and the reason "no nutrition data from Open Food Facts".
- **N6 URL param `rank`.** Values are `protein` and `kcal`. Absent or any other value means price per unit. It is never saved in prefs or a cookie. The category form's `comparison` select still picks the unit price shown on each row. List "Add" links (M4) never carry `rank`.
- **N7 Generic search.** Within each measure group, rows with a cost come first, ordered by cost. Rows without one follow, unranked, with their reason. The "Size unknown" group can't be ranked by nutrition. The "Rank by" select shows when at least one matched offer has OFF nutrition data (`offer.nutrition != {}`) or `rank` is already set. Facets, the size filter and measure grouping are unchanged. Facet counts never depend on `rank`.
- **N8 Food categories.** A new YAML key `food: true` is synced to `Category.food`. It's set on butter, cheese, milk, yogurt, bread, cooking oil, flour, pasta, peanut butter, protein powder, rice, sugar, orange juice and soda. It's not set on eggs (sold by count, N2), coffee or either water, where nutrition per $ means nothing, or on any household category.
- **N9 Backfill.** It reuses `observed_off.backfill_sizes`, the run that follows each successful Open Prices sync. That run looks up its size candidates first. With the rest of its limit, it then looks up `OffEntry` rows whose nutrition was never checked (`nutrition_checked_at IS NULL`, source `off`, not a 404 placeholder). After the lookups, `refresh_nutrition(changed_upcs)` rewrites `Offer.nutrition` for every offer, live or observed, on those UPCs. The run limit and the `off-product` daily cap are 2,000 while `settings.OFF_PRODUCT_BOOST_UNTIL` (env `UNITPRICER_OFF_PRODUCT_BOOST_UNTIL`, a date) is today or later, and 300 otherwise. The boost is applied when a token is taken and is never written to the `SourceBudget` row, so admin edits stay and the cap goes back to 300 by itself. A boosted run takes about 35 minutes at 1 request per second, once a day, and `budget.stopping` ends it early.
- **N10 Display.** Costs use `format_money` and read "$1.42 per 100 g protein" and "$0.78 per 1,000 kcal". The nutrition line reads "21 g protein per 100 g · 907 g pack" or "380 kcal per 100 g · 907 g pack". It's always metric, to match OFF's basis: g below 1,000, then kg, and ml below 1,000, then L, with kg and L to 2 decimals and trailing zeros dropped. When `rank` is set, one attribution line follows the results: "Nutrition: Open Food Facts", with the ODbL link used on the other OFF notices.

## Global Constraints

- Python with `uv` only. No new dependencies. No AI/LLM anything. `normalize/` stays pure (no Django, no I/O). `connectors/` imports no Django.
- **Never guess.** Only OFF per-100 g values, or per-100 ml values when the pack is volume and OFF's basis is 100 ml. Per-serving-only, missing or implausible values are unranked with a reason and never estimated. Never convert ml↔g or labeled oz.
- **Honest ranking.** The order depends only on the chosen metric. Ties break by price, then title, then retailer, then the observed tie-break (`browse._tie`) or offer id. There are no health scores, grades or advice anywhere, and no copy beyond what this plan fixes.
- Default ranking stays price per unit. `rank` lives only in the URL: no cookie, session or prefs write.
- Exact copy:
  - Rank options: "Price per unit", "Protein per $", "Calories per $". Select label: "Rank by".
  - Costs: "{money} per 100 g protein" and "{money} per 1,000 kcal".
  - Nutrition used: "{n} g protein per 100 g · {pack} pack" and "{n} kcal per 100 ml · {pack} pack".
  - Attribution: `Nutrition: <a href="https://world.openfoodfacts.org">Open Food Facts</a>, available under the <a href="https://opendatacommons.org/licenses/odbl/1-0/">Open Database License (ODbL)</a>.`
  - Reasons: "no nutrition data from Open Food Facts", "nutrition per serving only", "nutrition basis unknown", "protein not listed", "calories not listed", "nutrition values look wrong", "pack size unknown", "nutrition per 100 g, sold by volume", "nutrition per 100 ml, sold by weight", "no protein", "no calories".
- Tests never touch the network. They use recorded responses or `httpx.MockTransport`.
- Performance: a page with `rank` set makes the same number of queries as without it.
- The ODbL export (`export_open_prices`) and lists (M4) are unchanged.

## Review Focus

1. **Mixed offers on one product.** A product has two offers, and only one has a cost (the other has no UPC in OFF). The product ranks by the offer with the cost, and the row shows that offer's price and retailer. This is pinned in Task 5.
2. **Hostile or odd `rank` values** (`PROTEIN`, `<script>`, repeated `rank=protein&rank=kcal`). The page ranks by price per unit (or by the first value, as `QueryDict.get` returns it), never echoes the raw value, and never errors. Pinned in Task 5.
3. **`?rank=protein` on a non-food category.** The param is ignored, the select is absent and the order is by unit price. Pinned in Task 5.
4. **Price change on refresh.** A cheaper price must lower the stored cost on the next ingest, and a cost from the old price must never stay. Pinned in Task 3.
5. **429 or 5xx during the nutrition backfill.** Nothing is stored, `nutrition_checked_at` stays null and the next run asks again. A 404 sets `nutrition_checked_at`, so a dead barcode doesn't use up the daily cap. Pinned in Task 4.

---

### Task 1: Pure nutrition rules

**Files:**
- Modify: `normalize/types.py` (add `Nutrition`; `OffProduct.nutrition`)
- Create: `normalize/nutrition.py`
- Test: `tests/normalize/test_nutrition.py`

**Interfaces:**
- Produces:
  - `normalize.types.Nutrition` (frozen dataclass): `basis: str | None` (`"100g"`, `"100ml"`, `"serving"` or None), `protein: float | None`, `kcal: float | None`, all per basis as OFF gives them.
  - `OffProduct.nutrition: Nutrition | None = None` (None means never fetched with nutriments).
  - `normalize.nutrition.REASONS`: a dict of the reason strings in Global Constraints, keyed `no_data`, `per_serving`, `basis_unknown`, `protein_missing`, `kcal_missing`, `implausible`, `pack_unknown`, `per_100g_volume`, `per_100ml_mass`, `zero_protein`, `zero_kcal`.
  - `pack_of(measures: Mapping[str, float], measure_dim: str | None, measure_amount: float | None) -> tuple[float | None, float | None]` returns `(mass_g, volume_ml)` (N2).
  - `offer_nutrition(price: Decimal, nutrition: Nutrition | None, measures: Mapping[str, float], measure_dim: str | None, measure_amount: float | None) -> dict` returns `{}` when `nutrition is None`, else exactly these keys: `basis`, `protein`, `kcal`, `pack` (float or None), `pack_unit` (`"g"`, `"ml"` or None), `protein_cost`, `kcal_cost` (6-dp decimal strings or None), `protein_reason`, `kcal_reason` (a `REASONS` value or None). Exactly one of cost or reason is set per metric.

- [ ] **Step 1: Write the failing tests.** One test per never-guess rule. `MASS = ({"mass_g": 907.0}, "mass", 907.0)` and `VOL = ({"volume_ml": 3785.0}, "volume", 3785.0)`.

```python
def test_protein_and_kcal_costs_per_100g():
    n = offer_nutrition(Decimal("2.70"), Nutrition("100g", 21.0, 380.0), *MASS)
    assert n["protein_cost"] == "1.417546" and n["kcal_cost"] == "0.783381"
    assert (n["pack"], n["pack_unit"], n["protein_reason"]) == (907.0, "g", None)

def test_per_100ml_with_volume_pack():
    n = offer_nutrition(Decimal("3.00"), Nutrition("100ml", 3.4, 50.0), *VOL)
    assert (n["protein_cost"], n["kcal_cost"], n["pack_unit"]) == ("2.331183", "1.585205", "ml")

def test_never_fetched_is_empty():
    assert offer_nutrition(Decimal("1"), None, *MASS) == {}

def test_per_serving_only_is_unranked():           # reason per_serving on both
def test_basis_unknown_is_unranked():              # basis None, values present
def test_per_100g_with_volume_pack_never_converts():   # reason per_100g_volume, both costs None
def test_per_100ml_with_mass_pack_never_converts():    # reason per_100ml_mass
def test_missing_protein_ranks_kcal_only():        # protein None → protein_missing; kcal_cost set
def test_missing_kcal_ranks_protein_only():
@pytest.mark.parametrize("protein,kcal", [(100.1, 380), (-1, 380), (21, 900.5), (21, -5)])
def test_implausible_values_unrank_both(protein, kcal):   # both reasons == REASONS["implausible"]
def test_protein_exactly_100_and_kcal_exactly_900_are_plausible():
def test_zero_protein_and_zero_kcal_are_unranked():     # zero_protein / zero_kcal
def test_count_pack_is_unranked():                  # ({"count": 12.0}, "count", 12.0) → pack_unknown
def test_labeled_oz_pack_is_unranked():             # ({}, "oz_labeled", 16.0) → pack_unknown
def test_size_unknown_pack_is_unranked():           # ({}, None, None) → pack_unknown
def test_curated_measures_win_over_generic():       # ({"mass_g": 900.0}, "mass", 907.0) → pack 900.0
def test_generic_measure_used_without_curated():    # ({}, "mass", 907.0) → pack 907.0
```

- [ ] **Step 2: Run the tests to verify they fail.**
Run: `uv run pytest tests/normalize/test_nutrition.py -v`
Expected: FAIL with an `ImportError` on `normalize.nutrition`.

- [ ] **Step 3: Implement `Nutrition`, `OffProduct.nutrition`, `pack_of` and `offer_nutrition`.**
Check in this order and stop at the first that applies:
1. `basis == "serving"`, or `basis` is None or unknown.
2. Implausible values, which apply to both metrics.
3. No pack.
4. A basis/pack mismatch.
5. Per metric: a missing value, then zero, then the cost.

The costs are `price * 10000 / (protein * pack)` and `price * 100000 / (kcal * pack)` in `Decimal` (floats through `Decimal(str(x))`), quantized to `0.000001` like `generic_unit_price`.

- [ ] **Step 4: Run the tests to verify they pass.**
Run: `uv run pytest tests/normalize -q`
Expected: all pass, existing ones included.

- [ ] **Step 5: Commit.**
```bash
git add normalize/types.py normalize/nutrition.py tests/normalize/test_nutrition.py
git commit -m "feat: pure nutrition-per-dollar rules (#95)"
```

---

### Task 2: OFF nutriments on every lookup, stored on OffEntry

**Files:**
- Modify: `enrichment/off.py` (`FIELDS`, `OffRecord`, `_record_from_dict`, `_save`, `lookup`)
- Modify: `connectors/off_search.py` (`FIELDS`)
- Modify: `catalog/models.py` (`OffEntry`, `Offer.nutrition`, `Category.food`)
- Create: `catalog/migrations/0011_nutrition.py`
- Modify: `catalog/observed.py` (`observed_off_product`)
- Rename: every file in `tests/enrichment/recordings/off_product/` and `tests/connectors/recordings/off_search/` (the `fields=` query in the name changes)
- Test: `tests/enrichment/test_off.py`, `tests/catalog/test_observed.py`

**Interfaces:**
- Consumes: `Nutrition` and `OffProduct.nutrition` (Task 1).
- Produces:
  - `enrichment.off.FIELDS` ends `",nutriments,nutrition_data_per"`. `connectors.off_search.FIELDS` gains the same two fields.
  - `OffRecord.nutrition: Nutrition | None = None`. It is set whenever the source dict has `nutriments` or `nutrition_data_per`.
  - `OffEntry` gains `nutrition_per = CharField(max_length=10, null=True, blank=True)`, `protein_100g = FloatField(null=True, blank=True)`, `kcal_100g = FloatField(null=True, blank=True)` and `nutrition_checked_at = DateTimeField(null=True, blank=True, db_index=True)`.
  - `Offer.nutrition = JSONField(default=dict)` (the `offer_nutrition` dict). `Category.food = BooleanField(default=False)`. All three go in migration `0011_nutrition`.
  - `_save(record)` writes the three nutrition columns and sets `nutrition_checked_at` to now on every write.
  - `lookup(upc)` returns `OffProduct(..., nutrition=Nutrition(entry.nutrition_per, entry.protein_100g, entry.kcal_100g))` when `entry.nutrition_checked_at` is set, else `nutrition=None`.
  - `observed_off_product(entry, ...)` passes `entry.nutrition` through (None when `entry` is None). Open Prices' nested product never supplies nutrition.

- [ ] **Step 1: Write the failing tests** in `tests/enrichment/test_off.py`:

```python
def test_fields_request_nutriments():
    assert off.FIELDS.endswith(",nutriments,nutrition_data_per")
    assert "nutriments" in off_search.FIELDS and "nutrition_data_per" in off_search.FIELDS

def test_record_parses_protein_kcal_and_basis():
    rec = off._record_from_dict({"code": "0068826718115", "nutrition_data_per": "100g",
        "nutriments": {"proteins_100g": 21, "energy-kcal_100g": "380", "proteins_serving": 7}}, "off")
    assert rec.nutrition == Nutrition("100g", 21.0, 380.0)

@pytest.mark.parametrize("value", [True, "n/a", "", float("nan"), float("inf"), None, [1]])
def test_unusable_nutrient_values_are_missing(value):   # protein is None; the record still parses

def test_unknown_basis_is_none():                         # "per-pack" → basis None
def test_save_and_lookup_round_trip_nutrition(db):        # lookup(...).nutrition == Nutrition("100g", 21.0, 380.0)
def test_lookup_before_nutrition_checked_is_none(db):     # an OffEntry row without nutrition_checked_at → nutrition None
def test_delta_line_carries_nutrition(db, tmp_path):      # sync_file stores protein_100g from a JSONL line
```

In `tests/catalog/test_observed.py`, add `test_observed_off_product_keeps_entry_nutrition` and `test_observed_off_product_without_entry_has_no_nutrition`.

- [ ] **Step 2: Run the tests to verify they fail.**
Run: `uv run pytest tests/enrichment/test_off.py tests/catalog/test_observed.py -q`
Expected: the new tests fail. The `FIELDS` assertion and the `nutrition` attribute are missing.

- [ ] **Step 3: Implement the parsing, the models, the migration (`uv run python manage.py makemigrations catalog --name nutrition`), `_save`, `lookup` and `observed_off_product`.**
A nutrient value is used when it's an int, a float or a numeric string (not a bool) and it's finite. Anything else is None. `nutrition_data_per` is lowercased and stripped. Only `"100g"`, `"100ml"` and `"serving"` are kept, and anything else becomes None.

- [ ] **Step 4: Rename the recordings to the new `fields=` value.**
Each file keeps its JSON body. Only the encoded `fields=` part of the name changes, from the old `FIELDS` to the new one. Replace `quote(OLD, safe="")` with `quote(NEW, safe="")` in each file name, using `git mv` in a short `uv run python -c` loop. Then add `"nutrition_data_per": "100g"` and `"nutriments": {"proteins_100g": …, "energy-kcal_100g": …}` to one `off_product` recording, `0068826718115`, so a later task can use it.

- [ ] **Step 5: Run the affected suites.**
Run: `uv run pytest tests/enrichment tests/connectors tests/catalog -q`
Expected: all pass. No recording is missing, and the strict recorded transports raise on any unmatched name.

- [ ] **Step 6: Commit.**
```bash
git add -A enrichment connectors catalog tests
git commit -m "feat: fetch and store OFF nutriments on every lookup (#95)"
```

---

### Task 3: Offer nutrition at write time, the food flag and the refresh helper

**Files:**
- Modify: `catalog/ingest.py` (`_upsert`)
- Create: `catalog/nutrition.py`
- Modify: `normalize/categories.py` (`food` key), `catalog/categories_sync.py`, and these category YAMLs (N8): `dairy/{butter,cheese,milk,yogurt}`, `pantry/{bread,cooking-oil,flour,pasta,peanut-butter,protein-powder,rice,sugar}`, `beverages/{orange-juice,soda}`
- Test: `tests/catalog/test_nutrition.py`, `tests/normalize/test_categories.py`, `tests/catalog/test_categories_sync.py`

**Interfaces:**
- Consumes: `offer_nutrition`, `REASONS` (Task 1), `OffProduct.nutrition` and the `Offer.nutrition`, `OffEntry` and `Category.food` fields (Task 2).
- Produces:
  - `_upsert` writes `"nutrition": offer_nutrition(raw.price, off.nutrition if off else None, normalized.measures, measure.dimension, measure.amount)` for curated and generic offers alike.
  - `CategoryDef.food: bool = False`. A non-bool value raises `CategoryError`. `sync_categories` writes `Category.food`.
  - `catalog.nutrition.RANKS = {"unit": "Price per unit", "protein": "Protein per $", "kcal": "Calories per $"}`.
  - `parse_rank(raw: str | None) -> str` returns `"protein"`, `"kcal"` or `"unit"`. The match is exact, so `"PROTEIN"` gives `"unit"`.
  - `nutrition_cost(offer: Offer, rank: str) -> Decimal | None` and `nutrition_reason(offer: Offer, rank: str) -> str`. The reason is `REASONS["no_data"]` when `offer.nutrition == {}`.
  - `refresh_nutrition(upcs: Iterable[str], chunk: int = 1000) -> int` recomputes `Offer.nutrition` for every offer whose `product.upc` is in `upcs`, from the current `OffEntry` row. It writes only changed rows (`bulk_update(["nutrition"])`) and returns how many changed. It makes one `OffEntry` query and one `Offer` query per chunk.

- [ ] **Step 1: Write the failing tests** in `tests/catalog/test_nutrition.py`, using `tests/catalog/helpers.py`'s `raw()` and an `off_lookup` stub that returns an `OffProduct` with `nutrition`:

```python
def test_ingest_stores_costs_for_curated_offer(db): ...      # protein-powder offer, 907 g, 21 g/100 g → nutrition["protein_cost"] == "1.417546"
def test_ingest_stores_costs_for_generic_offer(db): ...      # no category; generic measure mass 907 g
def test_ingest_without_off_entry_stores_empty(db): ...      # off_lookup → None → offer.nutrition == {}
def test_price_change_recomputes_cost(db): ...               # re-ingest at 1.35 → protein_cost == "0.708773"
def test_refresh_nutrition_updates_live_and_observed_offers(db, django_assert_max_num_queries): ...
    # 2 offers on one UPC (one live, one observed); OffEntry gains nutrition; refresh → both set; ≤ 3 queries + 1 update
def test_refresh_nutrition_leaves_other_upcs_alone(db): ...
def test_parse_rank():
    assert [parse_rank(v) for v in ("protein", "kcal", "unit", "PROTEIN", "<x>", None)] == \
        ["protein", "kcal", "unit", "unit", "unit", "unit"]
def test_nutrition_reason_no_data(db): ...                   # offer.nutrition == {} → "no nutrition data from Open Food Facts"
```

Add `test_food_flag_parses_and_defaults_false` and `test_food_flag_must_be_bool` to `tests/normalize/test_categories.py`. Add `test_sync_writes_food_flag` to `tests/catalog/test_categories_sync.py`. Add one test asserting the exact N8 set: `{slug for slug, d in load_categories(CATEGORIES_DIR).items() if d.food}` equals the 14 slugs.

- [ ] **Step 2: Run the tests to verify they fail.**
Run: `uv run pytest tests/catalog/test_nutrition.py tests/normalize/test_categories.py tests/catalog/test_categories_sync.py -q`
Expected: FAIL. `catalog.nutrition` is missing, and `food` is an unknown top-level key.

- [ ] **Step 3: Implement the `_upsert` field, `catalog/nutrition.py`, the `food` key (add it to `_TOP_KEYS`) and the sync.** Then set `food: true` in the 14 YAML files.

- [ ] **Step 4: Run the full suite.**
Run: `uv run pytest -q`
Expected: all pass, including `tests/test_category_fixtures.py`.

- [ ] **Step 5: Commit.**
```bash
git add catalog normalize categories tests
git commit -m "feat: store nutrition costs on offers; food flag on categories (#95)"
```

---

### Task 4: Backfill existing OffEntry rows through the off-product budget

**Files:**
- Modify: `config/settings.py` (`OFF_PRODUCT_BOOST_UNTIL`, `OFF_PRODUCT_BOOST_DAILY`)
- Modify: `catalog/budget.py` (boosted daily cap, never persisted)
- Modify: `catalog/observed_off.py` (`run_limit`, `nutrition_candidates`, `backfill_sizes`, `_store`, `Stats`)
- Modify: `catalog/management/commands/off_observed_sizes.py` (default `--limit` from `run_limit`)
- Modify: `.env.example`, `docs/deploy.md`, `CLAUDE.md` (one line under Open Prices: the boost env var)
- Test: `tests/catalog/test_budget.py`, `tests/catalog/test_observed_off.py`, `tests/catalog/test_settings.py`

**Interfaces:**
- Consumes: `refresh_nutrition` (Task 3), the `OffEntry.nutrition_checked_at` field and the `FIELDS` change (Task 2).
- Produces:
  - `settings.OFF_PRODUCT_BOOST_UNTIL: date | None` comes from `UNITPRICER_OFF_PRODUCT_BOOST_UNTIL` (ISO `YYYY-MM-DD`). Unset or blank gives None, and anything else raises `ImproperlyConfigured`. `settings.OFF_PRODUCT_BOOST_DAILY: int` comes from `UNITPRICER_OFF_PRODUCT_BOOST_DAILY`, defaults to 2000 and must be positive.
  - `catalog.budget.boosted_limit(key: str, stored: int | None, now: datetime) -> int | None` returns `max(stored, OFF_PRODUCT_BOOST_DAILY)` when `key == "off-product"`, `stored is not None`, and the UTC day of `now` is on or before `OFF_PRODUCT_BOOST_UNTIL`. Otherwise it returns `stored`. `DbBudget.acquire` and `DbBudget.blocked` apply it to the bucket they check, and the bucket written back keeps the stored `daily_limit`.
  - `observed_off.run_limit(now: datetime) -> int` returns `OFF_PRODUCT_BOOST_DAILY` while the boost is active, else `DEFAULT_LIMIT` (300).
  - `observed_off.nutrition_candidates(limit: int, exclude: set[str]) -> list[tuple[str, Source]]` picks `OffEntry` rows with `nutrition_checked_at IS NULL`, `source == "off"` and `last_modified IS NOT NULL` (so no 404 placeholders), not in `exclude`, ordered by `pk`, and returns `(upc, "off")` pairs.
  - `backfill_sizes(now, budget, *, limit: int | None = None, ...)`. When `limit` is None it uses `run_limit(now)`. It looks up `candidates(now, limit)` first, then `nutrition_candidates(limit - len(sizes), {u for u, _ in sizes})`. It ends with `renormalize(...)` and then `refresh_nutrition(stats.changed)`, where `stats.changed` also includes UPCs whose only change was nutrition. `Stats.nutrition` counts the nutrition candidates looked up, and `line()` includes it.
  - `_store` also sets `nutrition_checked_at=now` on "missing", on "rejected", and when the cached row is newer. A retryable failure (`None`) writes nothing.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/catalog/test_budget.py
def test_boost_raises_off_product_cap_without_persisting(db, settings):
    settings.OFF_PRODUCT_BOOST_UNTIL = date(2026, 11, 16); settings.OFF_PRODUCT_BOOST_DAILY = 2000
    # off-product row daily_limit=300, daily_used=300, now 2026-11-10 → acquire succeeds; row.daily_limit still 300
def test_boost_ends_after_until_day(db, settings): ...       # now 2026-11-17 → BudgetExhausted at 300
def test_boost_never_touches_other_sources(db, settings): ... # walmart at its cap stays exhausted
def test_boost_keeps_unlimited_unlimited(db, settings): ...   # stored None → None

# tests/catalog/test_observed_off.py (MockTransport client, fake budget like the existing tests)
def test_nutrition_candidates_skip_checked_opf_and_placeholders(db): ...
def test_sizes_first_then_nutrition_within_limit(db): ...     # 2 size + 5 nutrition candidates, limit 4 → 2 + 2 requested
def test_backfill_stores_nutrition_and_refreshes_offers(db): ...  # live Walmart offer on the UPC gets protein_cost
def test_backfill_429_leaves_row_unchecked(db): ...           # nutrition_checked_at stays None; stats.failed == 1
def test_backfill_404_marks_checked(db): ...                  # nutrition_checked_at set; existing labels/quantity kept
def test_run_limit_boost(settings): ...                       # 2000 through the until day, 300 after

# tests/catalog/test_settings.py
def test_boost_until_parses_iso_date / test_boost_until_rejects_garbage / test_boost_daily_must_be_positive
```

- [ ] **Step 2: Run the tests to verify they fail.**
Run: `uv run pytest tests/catalog/test_budget.py tests/catalog/test_observed_off.py tests/catalog/test_settings.py -q`
Expected: the new tests fail. `boosted_limit`, `run_limit` and `nutrition_candidates` are missing.

- [ ] **Step 3: Implement the settings, `boosted_limit`, its use in `acquire` and `blocked`, `run_limit`, `nutrition_candidates`, and the `backfill_sizes`, `_store` and command changes.**
In `acquire`, wrap `take` so the boosted value is swapped back before `_apply` compares buckets: `lambda b: _restore(take(replace(b, daily_limit=boosted_limit(key, b.daily_limit, now)), now), b.daily_limit)`.

- [ ] **Step 4: Document the boost.**
- `.env.example` gets `UNITPRICER_OFF_PRODUCT_BOOST_UNTIL=` and `UNITPRICER_OFF_PRODUCT_BOOST_DAILY=`, with a comment: "first week after #95 deploys: the deploy date + 7 days".
- `docs/deploy.md` gets the same note, and says the boosted run takes about 35 minutes once a day.
- `CLAUDE.md` gets one sentence after the `off_observed_sizes` mention.

- [ ] **Step 5: Run the full suite.**
Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit.**
```bash
git add config catalog .env.example docs/deploy.md CLAUDE.md tests
git commit -m "feat: backfill OFF nutrition with a first-week off-product boost (#95)"
```

---

### Task 5: Category pages rank by protein or calories per $

**Files:**
- Modify: `catalog/browse.py` (`Row.cost`, `BrowseResult.rank`, `browse(..., rank=)`)
- Modify: `catalog/units.py` (`display_nutrition_cost`, `display_nutrition_used`)
- Modify: `web/views.py` (`category`, `_present`)
- Modify: `web/templates/web/category.html`, `_results.html`, `_row.html`, `_observed_row.html`
- Test: `tests/web/test_browse.py`, `tests/web/test_units.py`, `tests/web/test_views_category.py`

**Interfaces:**
- Consumes: `parse_rank`, `RANKS`, `nutrition_cost`, `nutrition_reason` (Task 3), `Category.food` and `Offer.nutrition` (Tasks 2–3).
- Produces:
  - `Row.cost: Decimal | None = None` and `BrowseResult.rank: str = "unit"`.
  - `browse(..., rank: str = "unit")`. With `rank` set to `protein` or `kcal`, each product's offer is the one with the lowest `nutrition_cost`. Ranked rows sort by `(cost, price, title, retailer, _tie)`. A product with no costed offer is unranked: its cheapest offer is shown, with `reason = nutrition_reason(offer, rank)`. `Row.unit_price` is still filled for display. With `rank == "unit"`, the behavior is byte-for-byte what it is today.
  - `display_nutrition_cost(cost: Decimal, rank: str, currency: str) -> str` returns, for example, "$1.42 per 100 g protein" or "$0.78 per 1,000 kcal".
  - `display_nutrition_used(nutrition: Mapping, rank: str) -> str` returns, for example, "21 g protein per 100 g · 907 g pack" or "50 kcal per 100 ml · 3.79 L pack". Numbers drop trailing zeros, and kcal is rounded to a whole number.
  - `_present(row, comparison, unit_system, dimension, rank="unit")` adds `nutrition_cost` and `nutrition_used` (both "" unless `rank != "unit"` and the row has a cost).
  - The category view context gains `rank`, `ranks` (a list of `{"value", "label"}` from `RANKS`, present only when `cat.food`) and `rank_label` (lowercased label, used in "None of these can be ranked by …").

- [ ] **Step 1: Write the failing tests.**

```python
# tests/web/test_browse.py
def test_rank_protein_orders_by_cost_not_unit_price(db): ...   # cheaper-per-kg powder with less protein ranks lower
def test_rank_protein_unranked_rows_carry_nutrition_reason(db): ...  # per-serving offer → "nutrition per serving only"
def test_mixed_offers_rank_by_the_costed_offer(db): ...         # Review Focus 1
def test_rank_ties_break_by_price_title_retailer(db): ...
def test_rank_unit_is_unchanged(db): ...                        # same rows/order as without the argument
def test_observed_rank_kcal(db): ...                            # browse(observed=True, rank="kcal") ranks observed offers

# tests/web/test_units.py
def test_display_nutrition_cost(): assert display_nutrition_cost(Decimal("1.417546"), "protein", "USD") == "$1.42 per 100 g protein"
def test_display_nutrition_used_mass_and_volume(): ...          # "21 g protein per 100 g · 907 g pack"; "50 kcal per 100 ml · 3.79 L pack"

# tests/web/test_views_category.py
def test_food_category_shows_rank_select(client, db): ...       # "Rank by", the three exact labels
def test_non_food_category_ignores_rank(client, db): ...        # Review Focus 3: no select; order by unit price
@pytest.mark.parametrize("qs", ["rank=PROTEIN", "rank=%3Cscript%3E", "rank=protein&rank=kcal"])
def test_odd_rank_values(client, db, qs): ...                   # Review Focus 2: 200; raw value not in body
def test_rank_row_shows_cost_nutrition_and_attribution(client, db): ...  # exact strings from Global Constraints
def test_rank_sets_no_cookie_and_no_prefs(client, db): ...      # no Set-Cookie; session prefs unchanged
def test_rank_htmx_partial_keeps_rank(client, db): ...          # HX-Request → _results.html with ranked rows
def test_add_link_never_carries_rank(client, db): ...           # lists add_url has no "rank="
def test_rank_adds_no_queries(client, db, django_assert_num_queries): ...  # same count with and without ?rank=protein
```

- [ ] **Step 2: Run the tests to verify they fail.**
Run: `uv run pytest tests/web/test_browse.py tests/web/test_units.py tests/web/test_views_category.py -q`
Expected: the new tests fail. `browse()` has no `rank` argument.

- [ ] **Step 3: Implement the `browse` rank path, the display helpers, the view and `_present`.**
The view reads `rank = parse_rank(params.get("rank")) if cat.food else "unit"` and passes it to both `browse` calls (live and observed).

- [ ] **Step 4: Update the templates.**
- `category.html` gets a `<label>Rank by <select name="rank">` inside the filters form, after "Compare by", rendered only when `ranks`. It has three options with values `unit`, `protein` and `kcal`.
- `_row.html` and `_observed_row.html` show `row.nutrition_cost` next to the unit price, and `row.nutrition_used` in `.result-fine`, when set.
- `_results.html` uses `rank_label` in the empty-ranked sentence when `rank != "unit"`. It also adds the exact attribution paragraph (class `note attribution`) once, after the observed section, when `rank != "unit"`.

- [ ] **Step 5: Run the web and catalog suites.**
Run: `uv run pytest tests/web tests/catalog -q`
Expected: all pass, including `tests/web/test_accessibility.py`.

- [ ] **Step 6: Commit.**
```bash
git add catalog/browse.py catalog/units.py web tests
git commit -m "feat: rank food category pages by protein or calories per dollar (#95)"
```

---

### Task 6: Generic search ranks by nutrition within each measure group

**Files:**
- Modify: `catalog/generic.py` (`GenericRow.cost`, `MeasureGroup.ranked_count`, `group_products(offers, rank=)`)
- Modify: `web/search.py` (`generic_results`, `_groups`, `_present_row`, `_settings_next`)
- Modify: `web/templates/web/_generic_facets.html`, `_generic_group.html`, `_generic_results.html`
- Modify: `docs/superpowers/specs/2026-11-07-unitpricer-design.md` (feature 7 wording, plus a feature-table row for #95)
- Test: `tests/catalog/test_generic_match.py` (group tests), `tests/web/test_generic_search.py`, `tests/web/test_polling.py`

**Interfaces:**
- Consumes: `parse_rank`, `nutrition_cost`, `nutrition_reason`, `RANKS` (Task 3), and `display_nutrition_cost`, `display_nutrition_used` and `_present(..., rank=)` (Task 5).
- Produces:
  - `GenericRow.cost: Decimal | None = None` and `MeasureGroup.ranked_count: int` (equal to `len(rows)` when `rank == "unit"`).
  - `group_products(offers, rank: str = "unit") -> GenericResult`. Grouping by dimension is unchanged. With `rank` set, each product's offer within its dimension is the one with the lowest cost, falling back to `_unit_key`. Rows with a cost come first, sorted by `(cost, price, title, retailer, id)`. Rows without a cost follow, sorted by `(price, title, retailer, id)`, with `reason = nutrition_reason(offer, rank)`. The unknown-size group is unchanged.
  - The search context gains `rank`, `ranks` (shown when `rank != "unit"` or any of the matched live or seen offers has `offer.nutrition`) and `rank_label`.
  - `_settings_next` keeps `rank`. Facet and clear URLs keep it already through `_query_string`. `_add_url` never includes it.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/catalog/test_generic_match.py
def test_group_rank_protein_costed_first_then_unranked(db): ...    # within "mass": costed by cost, then {} offers with reason
def test_group_rank_never_mixes_dimensions(db): ...                 # mass and volume groups ranked separately
def test_group_rank_unit_unchanged(db): ...
def test_group_rank_count_and_oz_labeled_all_unranked(db): ...      # reason "pack size unknown"

# tests/web/test_generic_search.py
def test_rank_select_only_when_off_nutrition_exists(client, db): ... # no offer with nutrition → no "Rank by"
def test_generic_rank_protein_renders_cost_and_attribution(client, db): ...
def test_generic_rank_kept_in_facet_and_clear_urls(client, db): ...
def test_show_all_keeps_rank(client, db): ...                        # Review Focus: keep_qs carries rank
def test_generic_add_url_has_no_rank(client, db): ...
def test_generic_rank_adds_no_queries(client, db, django_assert_num_queries): ...
# tests/web/test_polling.py
def test_poll_with_rank_renders_ranked_groups(client, db): ...       # the select sits in #facets, which the poll includes
```

- [ ] **Step 2: Run the tests to verify they fail.**
Run: `uv run pytest tests/catalog/test_generic_match.py tests/web/test_generic_search.py tests/web/test_polling.py -q`
Expected: the new tests fail. `group_products()` has no `rank` argument.

- [ ] **Step 3: Implement `group_products(rank=)`.** Then thread `rank = parse_rank(request.GET.get("rank"))` through `generic_results`, `_groups` and `_present_row`.

- [ ] **Step 4: Update the templates.**
- `_generic_facets.html` puts the "Rank by" select at the top, inside `#facets`, so the form, the polls and the OOB swap all carry it. It renders when `ranks`, even if `facets` is empty.
- `_generic_group.html` renders `group.rows[:ranked_count]` in the `<ol>`. The rest go in a `<ul class="results unranked">`, after an `<h3>` "Can't rank by {{ rank_label }}".
- `_generic_results.html` adds the attribution paragraph once when `rank != "unit"`.

- [ ] **Step 5: Update the spec.**
Feature 7 of the main spec now reads "Nutrition per dollar (#95): rank food categories and generic results by cost per 100 g protein or per 1,000 kcal, from Open Food Facts per-100 g values only." Add a feature-table row matching the existing rows.

- [ ] **Step 6: Run the full suite.**
Run: `uv run pytest -q`
Expected: all pass. The query-count tests above stand in for the search benchmark.

- [ ] **Step 7: Commit.**
```bash
git add catalog/generic.py web docs/superpowers/specs/2026-11-07-unitpricer-design.md tests
git commit -m "feat: nutrition ranking within generic search measure groups (#95)"
```
