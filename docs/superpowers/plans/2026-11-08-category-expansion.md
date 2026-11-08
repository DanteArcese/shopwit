# Category Expansion (5 → 25) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grow the curated catalog with the household categories that need special units, then with top-demand categories chosen from 'curate next', each with ≥ 8 real-title fixtures.

**Architecture:** The first three tasks change `normalize/` and the display layer: they add guards to the mass/volume/count parsers, a `laundry` dimension with a `per_load` comparison, and a `tissue` dimension with a `sheets_x_ply` comparison. Each one ships with its tests before any category relies on it. The next six tasks are data only: they add category YAML files and fixture files in batches of related categories. Each batch also adds search-resolution tests and must leave every existing fixture green. Matching stays as it is: the longest include phrase wins, any exclude vetoes, and offer titles get no plural folding.

**Tech Stack:** Python 3.13, `uv`, `pytest`, `pyyaml`, `pint`; Django only for the display plumbing in `catalog/units.py`, `catalog/browse.py` and `web/views.py`.

**Spec:** `docs/superpowers/specs/2026-11-07-unitpricer-design.md` (§3 Principles, §5.2 Normalizer, §6 Category definitions, §7 Units). Open-ended search spec `docs/superpowers/specs/2026-11-08-open-search-design.md` §14.2. Roadmap task 5.2 in `docs/roadmap.md`, issue #38.

## Global Constraints

- **Never guess** (Principle 3). An unknown value stays out of `attributes`/`measures` and is named in `unresolved`. When a title can be read two ways (a per-item or total size, pods vs loads, a capacity range), the answer is unresolved.
- **No AI/LLM libraries or calls anywhere.** Extraction is regex, synonyms and pint only.
- `normalize/` stays pure: no Django, network or filesystem access beyond `load_categories`/`load_store_brands`.
- Money is `Decimal`; unit prices are quantized to 6 places (`Decimal("0.000001")`).
- Every file under `categories/` has `tests/categories/<same path>.yaml` with **≥ 8 cases** (`test_every_category_has_fixtures`).
- The 5 existing categories' fixtures stay green and unedited. The only existing test that may change is the hard-coded `created=5` in `tests/catalog/test_categories_sync.py` (the first category batch that runs, now Task 8).
- Category YAML keys stay within `_TOP_KEYS`/`_ATTR_KEYS` in `normalize/categories.py`. No new keys.
- Marketing equivalences ("= 72 Regular Rolls") stay ignored (spec §6).
- Each task ends with `uv run pytest` green. The pre-commit hooks (gitleaks, pytest) must pass. Commit messages reference `#38`.

## Review Focus

1. **Nutrient grams are not the pack size.** "Premier Protein 100% Whey Protein Powder, …, 30g Protein, 24.5 oz" gives `mass_g ≈ 694.56`, never 30; the same goes for "7g Protein per Serving" on peanut butter and "20g Protein" on yogurt. Owned by Task 1: `test_nutrient_grams_not_pack_mass`.
2. **A bare "N Count" next to a size is ambiguous, but a count of containers is not.** "Chobani … 5.3 oz, 4 Count" leaves quantity unresolved (never 150 g, never a guessed 600 g). "16.9 fl oz, 40 Count Bottles" gives 40 × 16.9 fl oz. Owned by Task 1: `test_bare_count_beside_size_is_unresolved`, `test_container_count_multiplies`.
3. **A bare "oz" on laundry detergent follows the form.** "Liquid …, 105 oz" is a volume (3105.22 mL). "Powder …, 143 oz" is a weight (4053.98 g). With neither word there is no volume or mass, only loads. Owned by Task 2: `test_laundry_bare_oz_follows_form`.
4. **A title that names two categories goes to the right one, and never to a tie.** "King Arthur Unbleached Bread Flour" is flour, not bread. "Jovial Organic Brown Rice Pasta Penne" is pasta. "Rice Dream … Rice Milk" matches nothing. Owned by Task 5: fixtures `pantry/flour:1`, `pantry/pasta:4`, `pantry/rice:8` and `test_bread_flour_is_flour`.
5. **An attribute phrase inside a longer phrase.** "Nature's Own 100% Whole Wheat Bread" is `grain: whole_wheat`, not ambiguous with "wheat". Owned by Task 5: fixture `pantry/bread:1`. The same pattern-beats-synonym fix repeats in later tasks, each with its own fixture: `olive` vs "extra virgin olive oil" (`pantry/cooking-oil:0`, `:3`), `whey` vs "whey protein isolate" (`pantry/protein-powder:2`), and "from concentrate" vs "not from concentrate" (`beverages/orange-juice:2`, `:3`).

---

## File Structure

```
normalize/
  categories.py      # Dimension += "laundry", "tissue"; KNOWN_COMPARISONS += per_load, sheets_x_ply
  quantity.py        # Task 1 guards; _laundry (Task 2); _tissue (Task 3)
  compare.py         # per_load, sheets_x_ply
catalog/
  units.py           # DISPLAY rows, display_size for laundry/tissue
  browse.py          # SIZE_KEYS, COMPARISON_ATTRS
web/views.py         # SIZE_UNITS rows; counted dimensions skip the unit system
categories/
  dairy/{butter,yogurt,cheese}.yaml
  pantry/{bread,rice,pasta,flour,peanut-butter,cooking-oil,sugar,protein-powder}.yaml
  beverages/{bottled-water,sparkling-water,orange-juice,soda}.yaml
  household/{facial-tissue,trash-bags,laundry-detergent,dish-soap,dishwasher-detergent}.yaml
tests/
  normalize/test_quantity.py, test_compare.py, test_categories.py
  web/test_units.py, test_query.py, test_views_category.py
  catalog/test_categories_sync.py
  categories/<same paths as categories/>.yaml
docs/roadmap.md      # 7.3 wording (Task 6)
```

## Order of work

Task 1 → Task 2 (`laundry`) → Task 3 (`tissue`) → Task 8 (facial tissue, trash bags) → Task 9 (laundry detergent, dish soap, dishwasher detergent). Tasks 4–7 (food batches) are gated; see each task.

## The category list and why

Five exist already: milk, eggs, coffee, toilet paper and paper towels. Twenty are new, for 25 in total, the "about 25" in spec §6. All 14 categories spec §6 names are covered. The others were chosen because they are bought often, come in many pack sizes, and are sold by a size printed in the title, so a unit price changes the choice. Each needed no data beyond the title. Personal care (toothpaste, shampoo, body wash) is deferred: titles mix "oz" for weight and for volume within the same category, and it is not on the §6 list.

| Batch (task) | Categories (dimension → default comparison) |
|---|---|
| Dairy extras (4) | butter (mass → per_mass), yogurt (mass), cheese (mass) |
| Grains and bakery (5) | bread (mass), rice (mass), pasta (mass), flour (mass) |
| Pantry staples (6) | peanut butter (mass), cooking oil (volume → per_volume), sugar (mass), protein powder (mass) |
| Beverages (7) | bottled water, sparkling water, orange juice, soda (all volume) |
| Household paper and bags (8) | facial tissue (tissue → sheets_x_ply, alt per_100_sheets), trash bags (count → per_count) |
| Cleaning and laundry (9) | laundry detergent (laundry → per_load, alts per_volume, per_mass, per_count), dish soap (volume), dishwasher detergent (count) |

**Conventions for the category blocks below.** Each block is the exact file content in compact YAML; copy it as is. `organic: *organic` stands for the block from `categories/dairy/milk.yaml`, written out in full in each file (anchors don't cross files): `organic: {type: bool, synonyms: {true: [organic, o organics]}, off_label: en:organic, absent: false}`. Fixture blocks are the exact fixture files in the existing format, one case per line. Fixture IDs (`slug:index`, zero-based) are the named tests.

---

### Task 1: Quantity guards for mass, volume and count

Executed first, as the open-search prerequisite (#67). Skip if already merged.

**Files:**
- Modify: `normalize/quantity.py`
- Test: `tests/normalize/test_quantity.py`

**Interfaces:**
- Consumes: `parse_measures(texts, dimension)` as it stands.
- Produces: the same signature. These behavior changes are relied on by Tasks 4–9.
  - Mass parsing ignores nutrient amounts.
  - In mass and volume parsing, a bare count greater than 1 with no pack wording makes the text unparseable.
  - "N cans/bottles" and "N count/ct cans/bottles" multiply.
  - The `count` nouns gain `bags|pacs|pods|tabs|tablets`.

- [ ] **Step 1: Write the failing tests** in `tests/normalize/test_quantity.py` (use `_r` for approx):
  - `test_nutrient_grams_not_pack_mass`:
    - `["Premier Protein 100% Whey Protein Powder, Chocolate Milkshake, 30g Protein, 24.5 oz"]`, mass → `mass_g == _r(694.56)`
    - `["Jif Creamy Peanut Butter, 7g Protein per Serving, 40 oz"]` → `_r(1133.98)`
  - `test_nutrient_grams_with_words_between`: `["Orgain Organic Vegan Protein Powder, 21g Plant Based Protein, 2.03 lb"]` → `_r(920.79)`
  - `test_nutrient_grams_only_is_unresolved`: `["Whey Protein, 25g Protein, 0g Sugar"]`, mass → `{}`
  - `test_plain_grams_still_mass`:
    - `["Kerrygold Pure Irish Butter, 227 g"]` → `mass_g == _r(227)`
    - `["Myprotein Impact Whey, 907 g Whey Protein"]` → `_r(907)`
  - `test_bare_count_beside_size_is_unresolved`:
    - `["Chobani Vanilla Greek Yogurt, 5.3 oz, 4 Count"]`, mass → `{}`
    - `["Horizon Organic Whole Milk, 8 fl oz, 12 ct"]`, volume → `{}`
  - `test_one_count_is_harmless`: `["Folgers Classic Roast Ground Coffee, 12 oz, 1 Count"]`, mass → `_r(340.19)`
  - `test_pack_wins_over_bare_count`: `["Yoplait Original Yogurt, 6 oz, 8 Pack, 8 Count"]`, mass → `_r(1360.78)`
  - `test_container_count_multiplies`:
    - `["16.9 fl oz, 40 Count Bottles"]` → `_r(19991.7)`
    - `["12 fl oz, 12 Count Cans"]` → `_r(4258.59)`
    - `["12 fl oz, 24 Cans"]` → `_r(8517.18)`
  - `test_pack_beats_container`: `["12 fl oz, 12 Pack Cans"]` → `_r(4258.59)`
  - `test_count_nouns`: `"26 Bags"` → 26, `"94 Tabs"` → 94, `"60 Pods"` → 60, `"81 Pacs"` → 81, `"82 Tablets"` → 82 (all `count`)
- [ ] **Step 2: Run** `uv run pytest tests/normalize/test_quantity.py -v`. Expected: the new tests FAIL. For example, the nutrient test gets `mass_g == 30.0` and the bare-count test gets `150.25`.
- [ ] **Step 3: Implement in `normalize/quantity.py`:**
  - `_NUTRIENT_RE = re.compile(rf"{_NUM}\s*g(?:rams?)?\s+(?:of\s+|plant[- ]based\s+|added\s+|total\s+)?(?:protein|sugars?|fib(?:er|re)|carbs?|carbohydrates?|fat)\b", re.IGNORECASE)`. `_mass` blanks its matches (like `_strip_fl_oz`) before searching. The words allowed between the grams and the nutrient are a closed set, so "907 g Whey Protein" is still a pack mass.
  - `_CONTAINERS_RE = re.compile(r"\b(\d+)\s*(?:(?:count|ct)\s+)?(?:cans|bottles)\b", re.IGNORECASE)`. `_multiplier` tries the existing order (`N x` prefix, `pack of N`, `N pack`), then this. It always returns one factor and never multiplies two.
  - `_BARE_COUNT_RE = re.compile(rf"{_START}(\d+)\s*(?:ct|count)\b(?!\s+(?:cans|bottles)\b)", re.IGNORECASE)`. In `_measure`, when `_multiplier(...) == 1` and this matches a value > 1, return `None`.
  - Add `bags|pacs|pods|tabs|tablets` to `_COUNT_RE`'s noun alternation.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS, including every existing `tests/categories/**` fixture.
- [ ] **Step 5: Commit** `feat: quantity guards for nutrient grams, bare counts, containers (#38)`

---

### Task 2: `laundry` dimension and `per_load` comparison

**Files:**
- Modify: `normalize/categories.py`, `normalize/quantity.py`, `normalize/compare.py`, `catalog/units.py`, `catalog/browse.py`, `web/views.py`
- Test: `tests/normalize/test_quantity.py`, `tests/normalize/test_compare.py`, `tests/normalize/test_categories.py`, `tests/web/test_units.py`, `tests/web/test_views_category.py`

**Interfaces:**
- Produces:
  - `Dimension = Literal["volume", "mass", "count", "paper", "laundry"]`
  - `KNOWN_COMPARISONS` adds `"per_load"`
  - `COMPARISONS["per_load"] = Comparison("per_load", "load", _measure("loads"))`
  - `parse_measures(texts, "laundry") -> Measures` with any subset of `{"loads", "volume_ml", "mass_g", "count"}`. **Unlike the other dimensions, laundry merges across texts:** each key comes from the earliest text that yields it. It returns `{}` when no text yields any key.
  - `catalog/units.py`:
    - `DISPLAY[("per_load", s)] = ("load", ONE)` for both systems
    - `display_size(m, "laundry", s)` joins the present parts with `" · "`: loads (`"64 loads"`), then the volume or mass formatted as today, then `"81 ct"`
  - `catalog/browse.py`: `SIZE_KEYS["laundry"] = "loads"`
  - `web/views.py`:
    - `SIZE_UNITS["laundry"] = {"imperial": ("loads", "loads"), "metric": ("loads", "loads")}`
    - `COUNTED_DIMENSIONS = frozenset({"count", "laundry"})` replaces the `dimension != "count"` check in `_size_system`
    - `size_to_canonical` already passes these values through unchanged

Laundry rules (in `_laundry(text)`):
- loads: `{_NUM}\s*(?:total\s+)?(?:loads?|lds)\b(?:\s+total\b)?`, times `_multiplier`. When the match contains "total" ("128 Total Loads", "128 loads total"), it is not multiplied.
- volume: the explicit volume units only (fl oz, fz, gal, qt, pt, mL, L) → `volume_ml`, times `_multiplier`.
- mass: lb, kg, g, plus the nutrient strip from Task 1 → `mass_g`, times `_multiplier`.
- bare `oz` (not preceded by `fl`): the word `liquid` in the text gives fluid ounces, and `powder`/`powdered` gives weight ounces. With neither word, or both, it is skipped.
- count: `_COUNT_RE` → `count`. Pods are **not** loads.

- [ ] **Step 1: Write the failing tests:**
  - `test_laundry_liquid_loads_and_volume`: `["Tide Original Liquid Laundry Detergent, 92 fl oz, 64 Loads"]` → `loads == 64`, `volume_ml == _r(2720.76)`, no `mass_g`
  - `test_laundry_bare_oz_follows_form`:
    - `"Arm & Hammer Clean Burst Liquid Laundry Detergent, 105 oz, 70 Loads"` → `volume_ml _r(3105.22)`, no `mass_g`
    - `"Tide Original Powder Laundry Detergent, 143 oz, 102 Loads"` → `mass_g _r(4053.98)`, no `volume_ml`
    - `"Purex Laundry Detergent, Mountain Breeze, 50 oz, 33 Loads"` → `== {"loads": 33}`
  - `test_laundry_pods_count_is_not_loads`: `["Tide PODS Laundry Detergent Pacs, Original, 81 Count"]` → `== {"count": 81}`
  - `test_laundry_merges_texts_earliest_wins`: `["92 fl oz", "Tide Liquid 50 Loads, 64 fl oz"]` → `volume_ml _r(2720.76)`, `loads == 50`
  - `test_laundry_multipack_and_total`:
    - `["Tide Liquid, 46 fl oz, 32 Loads, Pack of 2"]` → `loads == 64`, `volume_ml _r(2720.76)`
    - `["Tide Liquid, 2 Pack, 128 Total Loads"]` → `loads == 128`
  - `test_laundry_unparseable`: `["Tide Laundry Detergent"]` → `{}`
  - `test_per_load_price` (test_compare.py): price `Decimal("12.97")`, `{"loads": 64}`, a category with default `per_load` → `{"per_load": Decimal("0.202656")}`
  - `test_loads_laundry_dimension` (test_categories.py): a tmp YAML with `dimension: laundry`, `default: per_load`, `alternates: [per_volume]` loads
  - `test_display_per_load` (tests/web/test_units.py):
    - `display_unit_price(Decimal("0.2"), "per_load", "imperial") == (Decimal("0.2"), "load")`
    - `display_size({"loads": 64, "volume_ml": 2720.76}, "laundry", "imperial") == "64 loads · 92 fl oz"`
    - `display_size({"count": 81}, "laundry", "metric") == "81 ct"`
  - `test_laundry_size_unit_ignores_system` (tests/web/test_views_category.py): `_size_system("laundry", "loads", "metric") == "metric"`
- [ ] **Step 2: Run** `uv run pytest tests/normalize tests/web/test_units.py tests/web/test_views_category.py -v`. Expected: FAIL (`laundry` is not a known dimension, and `per_load` is unknown).
- [ ] **Step 3: Implement** as specified. In `parse_measures`, branch on `dimension == "laundry"` to merge; leave the other dimensions on first-text-wins.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS (`test_compare_keys_match_known` still holds).
- [ ] **Step 5: Commit** `feat: laundry dimension and per-load comparison (#38)`

---

### Task 3: `tissue` dimension and `sheets_x_ply` comparison

**Files:**
- Modify: `normalize/categories.py`, `normalize/quantity.py`, `normalize/compare.py`, `catalog/units.py`, `catalog/browse.py`, `web/views.py`
- Test: `tests/normalize/test_quantity.py`, `tests/normalize/test_compare.py`, `tests/web/test_units.py`

**Interfaces:**
- Consumes: `_multiplier`, `_CNT`, `_START` (existing); `COUNTED_DIMENSIONS` (Task 2).
- Produces:
  - `Dimension` adds `"tissue"`; `KNOWN_COMPARISONS` adds `"sheets_x_ply"`
  - `COMPARISONS["sheets_x_ply"] = Comparison("sheets_x_ply", "100 sheets·ply", …)`. The denominator is `sheets × int(ply) / 100`, or `None` when either is missing. `per_100_sheets` is reused as is.
  - `parse_measures(texts, "tissue") -> {"sheets": float, "boxes"?: float}` (required key `sheets`; first text wins)
  - `catalog/units.py`:
    - `DISPLAY[("sheets_x_ply", s)] = ("100 sheets·ply", ONE)`
    - `display_size(m, "tissue", s)` → `"8 boxes · 960 sheets"`, or `"144 sheets"` when boxes is missing
  - `catalog/browse.py`: `SIZE_KEYS["tissue"] = "sheets"` and `COMPARISON_ATTRS["sheets_x_ply"] = {"ply"}`
  - `web/views.py`: `SIZE_UNITS["tissue"]` is `("sheets", "sheets")` for both systems; `COUNTED_DIMENSIONS` adds `"tissue"`

Tissue rules (in `_tissue(text)`), where `T = (?:facial\s+)?(?:tissues?|sheets?)`. The first rule that yields sheets wins:
1. Total: `{_CNT}\s*{T}\s+(?:total|in\s+all)\b` or `{_CNT}\s+total\s+{T}` gives `sheets` and wins.
2. Boxes: `{_START}(\d+)\s+(?:(?:flat|cube|upright|family|mini|travel|rectangular|square|tall)\s+)*boxes?\b`.
3. Per box: `{_CNT}\s*(?:{T}|ct|count)\s*(?:per|/)\s*box\b`, multiplied by boxes. With no box count, a multipack (`pack of N`, `N pack`, `N x`) supplies the box count, and only here.
4. Bare `{_CNT}\s*{T}\b` beside a box count is per box (the toilet-paper rule for bare sheets beside rolls). With no box count and no multipack it is the total. With a multipack and no "per box" it gives `{}`.
5. Bare `N count|ct`: with no box count and no multipack it gives `sheets = N`; otherwise `{}`.
6. Emit `boxes` only when stated (rule 2) or derived (rule 3). `sheets == 0` gives `{}`.

- [ ] **Step 1: Write the failing tests:**
  - `test_tissue_total_wins`: `"Kleenex Ultra Soft Facial Tissues, 3-Ply, 8 Flat Boxes, 120 Tissues per Box (960 Tissues Total)"` → `{"boxes": 8, "sheets": 960}`; `"… 18 Flat Boxes, 144 Tissues per Box (2,592 Total Tissues)"` → `sheets == 2592`
  - `test_tissue_per_box_times_boxes`: `"Puffs Plus Lotion Facial Tissues, 2-Ply, 6 Family Boxes, 124 Facial Tissues per Box"` → `{"boxes": 6, "sheets": 744}`
  - `test_tissue_single_box`: `"Kleenex Trusted Care Facial Tissues, 2-Ply, 144 Tissues"` → `{"sheets": 144}`
  - `test_tissue_bare_beside_boxes_is_per_box`: `"Great Value Facial Tissues, 2-Ply, 4 Cube Boxes, 70 Tissues"` → `{"boxes": 4, "sheets": 280}`
  - `test_tissue_pack_with_per_box`: `"Kleenex Facial Tissue, 160 Tissues per Box, Pack of 3"` → `{"boxes": 3, "sheets": 480}`
  - `test_tissue_ambiguous_counts_unresolved`: `"Puffs Basic Facial Tissues, 3 Pack, 128 Count"` → `{}`; `"Scotties Facial Tissue, 2-Ply, 10 Boxes, 1,200 Count"` → `{}`
  - `test_tissue_ply_not_a_count`: `"Kleenex Facial Tissue, 3-Ply"` → `{}`
  - `test_sheets_x_ply` (test_compare.py): price `Decimal("15.99")`, `{"sheets": 960}`, ply `3`, category default `sheets_x_ply`, alternate `per_100_sheets` → `{"sheets_x_ply": Decimal("0.555208"), "per_100_sheets": Decimal("1.665625")}`; without ply, only `per_100_sheets`
  - `test_display_tissue` (tests/web/test_units.py):
    - `display_size({"boxes": 8, "sheets": 960}, "tissue", "imperial") == "8 boxes · 960 sheets"`
    - `display_unit_price(Decimal("0.5"), "sheets_x_ply", "metric") == (Decimal("0.5"), "100 sheets·ply")`
- [ ] **Step 2: Run** `uv run pytest tests/normalize tests/web/test_units.py -v`. Expected: FAIL (`tissue` is not a known dimension).
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: tissue dimension and sheets-x-ply comparison (#38)`

---

### Task 4: Dairy extras: butter, yogurt, cheese

Gated. Proceed per category only after `SearchDemand` has ≥ 4 weeks of data, when the category's phrases rank in the top 10 of 'curate next' **and** it needs a precise attribute (fat %, roast, grain …) or a special unit. Mass- and volume-priced foods with no such need stay generic.

**Files:**
- Create: `categories/dairy/{butter,yogurt,cheese}.yaml`, `tests/categories/dairy/{butter,yogurt,cheese}.yaml`
- Modify: `tests/catalog/test_categories_sync.py` (`test_sync_categories_command_prints_counts`), `tests/web/test_query.py`

**Interfaces:**
- Consumes: Task 1 (yogurt cases 6–7 need the nutrient and bare-count guards).

Look-alikes these files handle:
- butter vs peanut/nut/cookie/apple butter, "Bread & Butter Pickle", spreads, "with Canola Oil" blends and popcorn
- yogurt vs frozen, covered, drinks and makers
- cheese vs cream cheese, crackers ("Cracker Barrel" must still match: the exclude is the plural `crackers`), string cheese, sticks and mac & cheese
- "Colby & Monterey Jack" stays ambiguous on purpose rather than guessing

```yaml
# categories/dairy/butter.yaml
name: Butter
dimension: mass
match:
  include: [butter]
  exclude: [peanut butter, almond butter, cashew butter, sunflower butter, nut butter, cookie butter, apple butter, cocoa butter, shea butter, body butter, cookies, crackers, popcorn, beans, lettuce, chicken, knife, dish, flavored, spread, spreadable, canola, olive oil, ghee, pickle, pickles, bread & butter, bread and butter, not butter, candy, toffee, syrup, sauce]
attributes:
  salted: {type: enum, values: [salted, unsalted], synonyms: {salted: [salted, lightly salted], unsalted: [unsalted]}}
  grass_fed: {type: bool, synonyms: {true: [grass-fed, grass fed]}, absent: false}
  organic: *organic
comparison: {default: per_mass}
search: [salted butter, unsalted butter]
```

```yaml
# categories/dairy/yogurt.yaml
name: Yogurt
dimension: mass
match:
  include: [yogurt, yoghurt, skyr]
  exclude: [frozen, covered, coated, melts, bites, chips, raisins, pretzels, drinkable, drink, smoothie, starter, culture, dog, bar, bars, tubes, go-gurt, dip, dressing, mask, granola, maker, pops, cereal]
attributes:
  greek: {type: bool, synonyms: {true: [greek]}, absent: false}
  fat: {type: enum, values: [nonfat, lowfat, whole], synonyms: {nonfat: [nonfat, non-fat, fat free, fat-free, 0%], lowfat: [low fat, lowfat, low-fat], whole: [whole milk, full fat]}}
  plain: {type: bool, synonyms: {true: [plain, unflavored]}, absent: false}
  organic: *organic
comparison: {default: per_mass}
search: [greek yogurt, plain yogurt]
```

```yaml
# categories/dairy/cheese.yaml
name: Cheese
dimension: mass
match:
  include: [shredded cheese, cheddar, mozzarella, monterey jack, colby jack, pepper jack, swiss cheese, provolone, parmesan, mexican blend, mexican style, italian blend]
  exclude: [cream cheese, sauce, mac, macaroni, crackers, cheez, goldfish, cheetos, dip, spread, string cheese, sticks, pizza, bites, crisps, popcorn, soup, dressing, puffs, chips, snack, queso, powder, bagel, bread, ravioli, seasoning, cheesecake, pasta, biscuits]
attributes:
  form: {type: enum, values: [shredded, block, sliced, grated], synonyms: {shredded: [shredded, shreds], block: [block, chunk, brick], sliced: [sliced, slices], grated: [grated]}}
  variety: {type: enum, values: [cheddar, mozzarella, monterey_jack, colby_jack, pepper_jack, swiss, provolone, parmesan, mexican_blend, italian_blend], synonyms: {cheddar: [cheddar], mozzarella: [mozzarella], monterey_jack: [monterey jack], colby_jack: [colby jack, colby-jack, colby & monterey jack, colby and monterey jack], pepper_jack: [pepper jack], swiss: [swiss], provolone: [provolone], parmesan: [parmesan, parmigiano], mexican_blend: [mexican blend, mexican style], italian_blend: [italian blend, italian style]}}
  organic: *organic
comparison: {default: per_mass}
search: [shredded cheese, cheddar cheese, mozzarella cheese]
```

- [ ] **Step 1: Write the fixtures:**

```yaml
# tests/categories/dairy/butter.yaml
- {title: 'Land O Lakes Salted Butter, 4 Sticks, 16 oz', brand: Land O Lakes, price: '5.48', expect: {category: dairy/butter, attributes: {salted: salted, grass_fed: false, organic: false, brand_tier: name_brand}, measures: {mass_g: 453.59}, unit_prices: {per_mass: '0.012081'}, unresolved: []}}
- {title: 'Great Value Unsalted Sweet Cream Butter, 4 Sticks, 1 lb', brand: Great Value, expect: {category: dairy/butter, attributes: {salted: unsalted, brand_tier: store_brand}, measures: {mass_g: 453.59}}}
- {title: 'Kerrygold Pure Irish Grass-Fed Butter, 8 oz', expect: {category: dairy/butter, attributes: {grass_fed: true}, measures: {mass_g: 226.8}, unresolved: ['attribute:salted', 'attribute:brand_tier']}}
- {title: 'Organic Valley Organic Salted Butter, 1 lb', expect: {category: dairy/butter, attributes: {salted: salted, organic: true}, measures: {mass_g: 453.59}}}
- {title: 'Kirkland Signature Unsalted Butter, 4 x 1 lb', brand: Kirkland Signature, expect: {category: dairy/butter, attributes: {salted: unsalted, brand_tier: store_brand}, measures: {mass_g: 1814.37}}}
- {title: 'Challenge Lightly Salted Butter, 8 oz', expect: {category: dairy/butter, attributes: {salted: salted}, measures: {mass_g: 226.8}}}
- {title: 'Vlasic Bread & Butter Pickle Chips, 24 fl oz', expect: {category: null}}
- {title: 'I Can''t Believe It''s Not Butter! Original Spread, 15 oz', expect: {category: null}}
- {title: 'Land O Lakes Spreadable Butter with Canola Oil, 15 oz', expect: {category: null}}
- {title: 'Orville Redenbacher''s Movie Theater Butter Popcorn, 6 ct', expect: {category: null}}
```

```yaml
# tests/categories/dairy/yogurt.yaml
- {title: 'Chobani Plain Non-Fat Greek Yogurt, 32 oz', brand: Chobani, price: '5.97', expect: {category: dairy/yogurt, attributes: {greek: true, fat: nonfat, plain: true, organic: false, brand_tier: name_brand}, measures: {mass_g: 907.18}, unit_prices: {per_mass: '0.006581'}, unresolved: []}}
- {title: 'Fage Total 0% Plain Greek Yogurt, 35.3 oz', expect: {category: dairy/yogurt, attributes: {greek: true, fat: nonfat, plain: true}, measures: {mass_g: 1000.74}}}
- {title: 'Great Value Plain Nonfat Yogurt, 32 oz', brand: Great Value, expect: {category: dairy/yogurt, attributes: {greek: false, fat: nonfat, brand_tier: store_brand}, measures: {mass_g: 907.18}}}
- {title: 'Siggi''s Icelandic Skyr Vanilla Nonfat Yogurt, 24 oz', expect: {category: dairy/yogurt, attributes: {greek: false, fat: nonfat, plain: false}, measures: {mass_g: 680.39}}}
- {title: 'Stonyfield Organic Whole Milk Plain Yogurt, 32 oz', expect: {category: dairy/yogurt, attributes: {fat: whole, organic: true, plain: true}, measures: {mass_g: 907.18}}}
- {title: 'Yoplait Original Strawberry Low Fat Yogurt, 6 oz, 8 Pack', expect: {category: dairy/yogurt, attributes: {fat: lowfat}, measures: {mass_g: 1360.78}}}
- {title: 'Oikos Pro Vanilla Greek Yogurt, 20g Protein, 32 oz', expect: {category: dairy/yogurt, attributes: {greek: true}, measures: {mass_g: 907.18}}}
- {title: 'Chobani Vanilla Greek Yogurt, 5.3 oz, 4 Count', brand: Chobani, expect: {category: dairy/yogurt, attributes: {greek: true}, measures: {}, unresolved: ['attribute:fat', quantity, 'unit_price:per_mass']}}
- {title: 'Yogurt Covered Pretzels, 7 oz', expect: {category: null}}
- {title: 'Activia Probiotic Dailies Strawberry Yogurt Drink, 3.1 fl oz, 8 Pack', expect: {category: null}}
- {title: Euro Cuisine Automatic Yogurt Maker, expect: {category: null}}
```

```yaml
# tests/categories/dairy/cheese.yaml
- {title: 'Kraft Mexican Style Four Cheese Blend Shredded Cheese, 8 oz', brand: Kraft, price: '2.98', expect: {category: dairy/cheese, attributes: {form: shredded, variety: mexican_blend, organic: false, brand_tier: name_brand}, measures: {mass_g: 226.8}, unit_prices: {per_mass: '0.013140'}, unresolved: []}}
- {title: 'Great Value Finely Shredded Mild Cheddar Cheese, 8 oz', brand: Great Value, expect: {category: dairy/cheese, attributes: {form: shredded, variety: cheddar, brand_tier: store_brand}, measures: {mass_g: 226.8}}}
- {title: 'Tillamook Medium Cheddar Cheese Block, 2 lb', expect: {category: dairy/cheese, attributes: {form: block, variety: cheddar}, measures: {mass_g: 907.18}}}
- {title: 'Sargento Sliced Provolone Cheese, 12 Slices, 8 oz', expect: {category: dairy/cheese, attributes: {form: sliced, variety: provolone}, measures: {mass_g: 226.8}}}
- {title: 'Kraft Grated Parmesan Cheese, 8 oz', expect: {category: dairy/cheese, attributes: {form: grated, variety: parmesan}, measures: {mass_g: 226.8}}}
- {title: 'Kirkland Signature Shredded Mozzarella, 5 lb', brand: Kirkland Signature, expect: {category: dairy/cheese, attributes: {form: shredded, variety: mozzarella, brand_tier: store_brand}, measures: {mass_g: 2267.96}}}
- {title: 'Sargento Shredded Colby & Monterey Jack Cheese, 8 oz', brand: Sargento, expect: {category: dairy/cheese, attributes: {form: shredded}, unresolved: ['attribute:variety:ambiguous']}}
- {title: 'Cracker Barrel Extra Sharp White Cheddar Cheese, 8 oz', expect: {category: dairy/cheese, attributes: {variety: cheddar}, measures: {mass_g: 226.8}}}
- {title: 'Philadelphia Original Cream Cheese, 8 oz', expect: {category: null}}
- {title: 'Cheez-It Original Cheddar Crackers, 12.4 oz', expect: {category: null}}
- {title: 'Farm Rich Breaded Mozzarella Sticks, 22 oz', expect: {category: null}}
```

- [ ] **Step 2: Add query tests.** (The sync-count test change moved to Task 8 Step 2b.) Add `tests/web/test_query.py::test_dairy_extras`, parametrized:
  - `"unsalted butter"` → `("dairy/butter", {"salted": "unsalted"})`
  - `"greek yogurt"` → `("dairy/yogurt", {"greek": True})`
  - `"shredded cheddar"` → `("dairy/cheese", {"form": "shredded", "variety": "cheddar"})`
  - `"peanut butter"` → category not `"dairy/butter"`
- [ ] **Step 3: Run** `uv run pytest tests/test_category_fixtures.py tests/web/test_query.py -v`. Expected: FAIL (no category files yet).
- [ ] **Step 4: Write the three category files.** Run `uv run pytest -v`. Expected: PASS, with all existing fixtures unchanged. If a fixture fails, fix the category YAML, not the expectation, unless the expectation contradicts the never-guess rule.
- [ ] **Step 5: Commit** `feat: butter, yogurt, cheese categories with fixtures (#38)`

---

### Task 5: Grains and bakery: bread, rice, pasta, flour

Gated. Proceed per category only after `SearchDemand` has ≥ 4 weeks of data, when the category's phrases rank in the top 10 of 'curate next' **and** it needs a precise attribute (fat %, roast, grain …) or a special unit. Mass- and volume-priced foods with no such need stay generic.

**Files:**
- Create: `categories/pantry/{bread,rice,pasta,flour}.yaml`, `tests/categories/pantry/{bread,rice,pasta,flour}.yaml`
- Modify: `tests/web/test_query.py`

**Interfaces:**
- Consumes: Task 1 (no case here depends on it, but the bare-count guard must not regress these).

Look-alikes:
- bread vs crumbs, mixes, flour, garlic bread, "Bread & Butter" pickles and bread makers
- rice vs rice milk, cakes, Krispies, Ready Rice pouches, instant and seasoned mixes, rice pasta and noodles
- pasta vs sauce, mac & cheese, SpaghettiOs and spaghetti squash
- flour vs flour tortillas and flour-sack towels

Two attribute fixes:
- Bread: `wheat` is a pattern with a "whole " lookbehind so "whole wheat" isn't ambiguous.
- Rice: the named varieties (jasmine, basmati, arborio) are a pattern, so they beat the grain-length synonyms ("Long Grain Jasmine" is jasmine).

Instant and microwave rice are excluded, since their weight isn't comparable with dry rice.

```yaml
# categories/pantry/bread.yaml
name: Bread
dimension: mass
match:
  include: [bread]
  exclude: [crumbs, panko, flour, mix, machine, maker, knife, box, pudding, garlic bread, bread & butter, bread and butter, pickles, banana, zucchini, pumpkin, monkey bread, dough, frozen, croutons, stuffing, bowl, naan, pita, flatbread, sticks, pan, pans, proofing, basket, bag, bags, dog, bird]
attributes:
  grain: {type: enum, values: [white, wheat, whole_wheat, multigrain, sourdough, rye], patterns: ['(?<!\bwhole )(?<!\bwhole-)\b(wheat)\b'], synonyms: {white: [white], whole_wheat: [whole wheat, whole-wheat], multigrain: [multigrain, multi-grain, 12 grain, 15 grain, whole grains], sourdough: [sourdough], rye: [rye]}}
  gluten_free: {type: bool, synonyms: {true: [gluten free, gluten-free]}, absent: false}
  organic: *organic
comparison: {default: per_mass}
search: [white bread, wheat bread, sandwich bread]
```

```yaml
# categories/pantry/rice.yaml
name: Rice
dimension: mass
match:
  include: [rice]
  exclude: [milk, drink, cakes, cake, krispies, rice-a-roni, roni, vinegar, noodles, flour, crackers, paper, cauliflower, pudding, fried rice, bran, wine, cooker, chex, cereal, ready rice, microwavable, microwave, cups, pouch, pouches, instant, minute, pilaf, seasoned, mix, sides, flavored, spanish rice, pasta, chips, puffs, snack, bowl, bowls, frozen, oil, syrup, rice water, shampoo, mask, dog, cat, soup, beans, sake, protein]
attributes:
  variety: {type: enum, values: [long_grain, medium_grain, short_grain, jasmine, basmati, arborio], patterns: [\b(jasmine|basmati|arborio)\b], synonyms: {long_grain: [long grain, long-grain], medium_grain: [medium grain, medium-grain, calrose], short_grain: [short grain, short-grain, sushi rice]}}
  brown: {type: bool, synonyms: {true: [brown]}, absent: false}
  organic: *organic
comparison: {default: per_mass}
search: [white rice, brown rice, jasmine rice]
```

```yaml
# categories/pantry/pasta.yaml
name: Pasta
dimension: mass
match:
  include: [pasta, spaghetti, penne, macaroni, elbows, rotini, linguine, fettuccine, rigatoni, farfalle, angel hair, egg noodles, lasagna, ziti]
  exclude: [sauce, marinara, salad, cheese, spaghettios, squash, chef boyardee, helper, sides, meal, kit, dinner, skillet, frozen, maker, pot, strainer, colander, server, ravioli, tortellini, soup, seasoning, zucchini, shirataki, ramen, instant, bake, canned]
attributes:
  shape: {type: enum, values: [spaghetti, penne, elbows, rotini, linguine, fettuccine, rigatoni, farfalle, angel_hair, egg_noodles, lasagna, ziti], synonyms: {spaghetti: [spaghetti, thin spaghetti, spaghettini], penne: [penne, penne rigate, mostaccioli], elbows: [elbows, elbow, elbow macaroni], rotini: [rotini], linguine: [linguine], fettuccine: [fettuccine, fettucine], rigatoni: [rigatoni], farfalle: [farfalle, bow ties, bow tie], angel_hair: [angel hair, capellini], egg_noodles: [egg noodles], lasagna: [lasagna, lasagne], ziti: [ziti]}}
  whole_grain: {type: bool, synonyms: {true: [whole grain, whole wheat]}, absent: false}
  gluten_free: {type: bool, synonyms: {true: [gluten free, gluten-free]}, absent: false}
comparison: {default: per_mass}
search: [spaghetti, penne pasta, elbow macaroni]
```

```yaml
# categories/pantry/flour.yaml
name: Flour
dimension: mass
match:
  include: [flour, bread flour]
  exclude: [tortilla, tortillas, towel, towels, sifter, canister, container, mix, pancake, gravy, breading, seasoned, dog, play]
attributes:
  type: {type: enum, values: [all_purpose, bread, whole_wheat, self_rising, cake, almond, coconut, rice], synonyms: {all_purpose: [all purpose, all-purpose], bread: [bread flour], whole_wheat: [whole wheat, white whole wheat], self_rising: [self rising, self-rising], cake: [cake flour], almond: [almond flour, almond meal], coconut: [coconut flour], rice: [rice flour]}}
  unbleached: {type: bool, synonyms: {true: [unbleached]}, absent: false}
  gluten_free: {type: bool, synonyms: {true: [gluten free, gluten-free]}, absent: false}
  organic: *organic
comparison: {default: per_mass}
search: [all purpose flour, bread flour]
```

- [ ] **Step 1: Write the fixtures:**

```yaml
# tests/categories/pantry/bread.yaml
- {title: 'Nature''s Own Honey Wheat Bread, 20 oz Loaf', brand: Nature's Own, price: '3.48', expect: {category: pantry/bread, attributes: {grain: wheat, gluten_free: false, organic: false, brand_tier: name_brand}, measures: {mass_g: 566.99}, unit_prices: {per_mass: '0.006138'}, unresolved: []}}
- {title: 'Nature''s Own 100% Whole Wheat Bread, 20 oz Loaf', expect: {category: pantry/bread, attributes: {grain: whole_wheat}, measures: {mass_g: 566.99}}}
- {title: 'Wonder Classic White Sandwich Bread, 20 oz', expect: {category: pantry/bread, attributes: {grain: white}, measures: {mass_g: 566.99}}}
- {title: 'Great Value White Sandwich Bread, 20 oz', brand: Great Value, expect: {category: pantry/bread, attributes: {grain: white, brand_tier: store_brand}}}
- {title: 'Dave''s Killer Bread 21 Whole Grains and Seeds Organic Bread, 27 oz', expect: {category: pantry/bread, attributes: {grain: multigrain, organic: true}, measures: {mass_g: 765.44}}}
- {title: 'Canyon Bakehouse Gluten Free Heritage Style Whole Grain Bread, 24 oz', expect: {category: pantry/bread, attributes: {gluten_free: true}, measures: {mass_g: 680.39}, unresolved: ['attribute:grain', 'attribute:brand_tier']}}
- {title: 'Pepperidge Farm Farmhouse Sourdough Bread, 24 oz Loaf', expect: {category: pantry/bread, attributes: {grain: sourdough}, measures: {mass_g: 680.39}}}
- {title: 'Progresso Italian Style Bread Crumbs, 15 oz', expect: {category: null}}
- {title: 'Pillsbury Quick Bread & Muffin Mix, Banana, 14 oz', expect: {category: null}}
- {title: 'Cole''s Garlic Bread, Frozen, 10 oz', expect: {category: null}}
```

```yaml
# tests/categories/pantry/rice.yaml
- {title: 'Mahatma Jasmine Rice, 5 lb', brand: Mahatma, price: '7.48', expect: {category: pantry/rice, attributes: {variety: jasmine, brown: false, organic: false, brand_tier: name_brand}, measures: {mass_g: 2267.96}, unit_prices: {per_mass: '0.003298'}, unresolved: []}}
- {title: 'Great Value Long Grain Enriched Rice, 20 lb', brand: Great Value, expect: {category: pantry/rice, attributes: {variety: long_grain, brand_tier: store_brand}, measures: {mass_g: 9071.85}}}
- {title: 'Lundberg Organic Short Grain Brown Rice, 2 lb', expect: {category: pantry/rice, attributes: {variety: short_grain, brown: true, organic: true}, measures: {mass_g: 907.18}}}
- {title: 'Royal Basmati Rice, 15 lb', expect: {category: pantry/rice, attributes: {variety: basmati}, measures: {mass_g: 6803.89}}}
- {title: 'Three Ladies Brand Long Grain Jasmine Rice, 25 lb', expect: {category: pantry/rice, attributes: {variety: jasmine}, measures: {mass_g: 11339.81}}}
- {title: 'Botan Calrose Rice, 5 lb', expect: {category: pantry/rice, attributes: {variety: medium_grain}, measures: {mass_g: 2267.96}}}
- {title: 'Ben''s Original Ready Rice Jasmine, 8.5 oz Pouch', expect: {category: null}}
- {title: 'Quaker Lightly Salted Rice Cakes, 4.47 oz', expect: {category: null}}
- {title: 'Rice Dream Original Organic Rice Milk, 64 fl oz', expect: {category: null}}
- {title: 'Kellogg''s Rice Krispies Breakfast Cereal, 12 oz', expect: {category: null}}
```

```yaml
# tests/categories/pantry/pasta.yaml
- {title: 'Barilla Spaghetti Pasta, 16 oz', brand: Barilla, price: '1.68', expect: {category: pantry/pasta, attributes: {shape: spaghetti, whole_grain: false, gluten_free: false, brand_tier: name_brand}, measures: {mass_g: 453.59}, unit_prices: {per_mass: '0.003704'}, unresolved: []}}
- {title: 'Great Value Penne Rigate, 32 oz', brand: Great Value, expect: {category: pantry/pasta, attributes: {shape: penne, brand_tier: store_brand}, measures: {mass_g: 907.18}}}
- {title: 'Barilla Elbow Macaroni, 16 oz', expect: {category: pantry/pasta, attributes: {shape: elbows}, measures: {mass_g: 453.59}}}
- {title: 'Banza Chickpea Pasta Rotini, Gluten Free, 8 oz', expect: {category: pantry/pasta, attributes: {shape: rotini, gluten_free: true}, measures: {mass_g: 226.8}}}
- {title: 'Jovial Organic Brown Rice Pasta Penne Rigate, Gluten Free, 12 oz', expect: {category: pantry/pasta, attributes: {shape: penne, gluten_free: true}, measures: {mass_g: 340.19}}}
- {title: 'Ronzoni Healthy Harvest Whole Grain Thin Spaghetti, 13.25 oz', expect: {category: pantry/pasta, attributes: {shape: spaghetti, whole_grain: true}, measures: {mass_g: 375.63}}}
- {title: 'Pennsylvania Dutch Wide Egg Noodles, 12 oz', expect: {category: pantry/pasta, attributes: {shape: egg_noodles}, measures: {mass_g: 340.19}}}
- {title: 'De Cecco Linguine No. 7, 1 lb', expect: {category: pantry/pasta, attributes: {shape: linguine}, measures: {mass_g: 453.59}}}
- {title: 'Prego Traditional Pasta Sauce, 24 oz', expect: {category: null}}
- {title: 'Kraft Original Macaroni & Cheese Dinner, 7.25 oz', expect: {category: null}}
- {title: 'SpaghettiOs Original Canned Pasta, 15.8 oz', expect: {category: null}}
```

```yaml
# tests/categories/pantry/flour.yaml
- {title: 'Gold Medal All-Purpose Flour, 5 lb', brand: Gold Medal, price: '3.12', expect: {category: pantry/flour, attributes: {type: all_purpose, unbleached: false, gluten_free: false, organic: false, brand_tier: name_brand}, measures: {mass_g: 2267.96}, unit_prices: {per_mass: '0.001376'}, unresolved: []}}
- {title: 'King Arthur Unbleached Bread Flour, 5 lb', expect: {category: pantry/flour, attributes: {type: bread, unbleached: true}, measures: {mass_g: 2267.96}}}
- {title: 'Great Value All Purpose Flour, 5 lb', brand: Great Value, expect: {category: pantry/flour, attributes: {type: all_purpose, brand_tier: store_brand}}}
- {title: 'Bob''s Red Mill Super-Fine Almond Flour, 16 oz', expect: {category: pantry/flour, attributes: {type: almond}, measures: {mass_g: 453.59}}}
- {title: 'King Arthur Whole Wheat Flour, 5 lb', expect: {category: pantry/flour, attributes: {type: whole_wheat}}}
- {title: 'Bob''s Red Mill Gluten Free 1-to-1 Baking Flour, 22 oz', expect: {category: pantry/flour, attributes: {gluten_free: true}, measures: {mass_g: 623.69}, unresolved: ['attribute:type', 'attribute:brand_tier']}}
- {title: 'White Lily Self Rising Flour, 5 lb', expect: {category: pantry/flour, attributes: {type: self_rising}}}
- {title: 'Swans Down Cake Flour, 32 oz', expect: {category: pantry/flour, attributes: {type: cake}, measures: {mass_g: 907.18}}}
- {title: 'Mission Flour Tortillas, Soft Taco, 10 ct', expect: {category: null}}
- {title: 'Aunt Martha''s Flour Sack Towels, 5 Pack', expect: {category: null}}
```

- [ ] **Step 2: Add query tests.**
  - `tests/web/test_query.py::test_bread_flour_is_flour`: `r("bread flour") == QueryResolution("pantry/flour", {"type": "bread"}, "")`
  - `test_grains`, parametrized:
    - `"whole wheat bread"` → `("pantry/bread", {"grain": "whole_wheat"})`
    - `"jasmine rice"` → `("pantry/rice", {"variety": "jasmine"})`
    - `"brown rice"` → `("pantry/rice", {"brown": True})`
    - `"egg noodles"` → `("pantry/pasta", {"shape": "egg_noodles"})`
  - Extend `test_excluded_queries_not_rewritten` with `"rice milk"`, and assert `r("rice milk").category is None`.
- [ ] **Step 3: Run** `uv run pytest tests/test_category_fixtures.py tests/web/test_query.py -v`. Expected: FAIL.
- [ ] **Step 4: Write the four category files.** Run `uv run pytest -v`. Expected: PASS. In particular, `pantry/flour:1`, `pantry/pasta:4`, `pantry/rice:8` and `pantry/bread:1` pass (Review Focus 4 and 5).
- [ ] **Step 5: Commit** `feat: bread, rice, pasta, flour categories with fixtures (#38)`

---

### Task 6: Pantry staples: peanut butter, cooking oil, sugar, protein powder

Gated. Proceed per category only after `SearchDemand` has ≥ 4 weeks of data, when the category's phrases rank in the top 10 of 'curate next' **and** it needs a precise attribute (fat %, roast, grain …) or a special unit. Mass- and volume-priced foods with no such need stay generic.

**Files:**
- Create: `categories/pantry/{peanut-butter,cooking-oil,sugar,protein-powder}.yaml` and their fixture files
- Modify: `tests/web/test_query.py`, `docs/roadmap.md` (task 7.3)

**Interfaces:**
- Consumes: Task 1 (nutrient grams: `pantry/peanut-butter:3`, `pantry/protein-powder:1`, `:3`; bare count: `pantry/peanut-butter:4`).

Look-alikes:
- peanut butter vs cups, crackers, PB2 powder and candy
- cooking oil vs sprays, "Tuna in Olive Oil" and "Mayonnaise with Olive Oil"
- sugar: includes are specific phrases ("granulated sugar", "brown sugar", …), not bare "sugar", so flavors like "Maple & Brown Sugar" oatmeal and Pop-Tarts are excluded and sweetener blends vetoed
- protein powder vs shakes, bars and shaker bottles

Protein powder compares **per mass only**. Protein per dollar (servings × grams per serving) is left to M7, which has Open Food Facts nutrition. Titles state servings too rarely and inconsistently to parse without guessing.

```yaml
# categories/pantry/peanut-butter.yaml
name: Peanut Butter
dimension: mass
match:
  include: [peanut butter]
  exclude: [cups, cup, cookies, cookie, cracker, crackers, pretzel, pretzels, powder, powdered, pb2, bar, bars, candy, chips, morsels, dog, treats, cereal, puffs, filled, granola, ice cream, sandwich, pie, fudge, jelly, wafer, wafers, bites, balls, protein powder]
attributes:
  texture: {type: enum, values: [creamy, crunchy], synonyms: {creamy: [creamy, smooth], crunchy: [crunchy, chunky, extra crunchy, super chunk]}}
  natural: {type: bool, synonyms: {true: [natural]}, absent: false}
  organic: *organic
comparison: {default: per_mass}
search: [creamy peanut butter, crunchy peanut butter]
```

```yaml
# categories/pantry/cooking-oil.yaml
name: Cooking Oil
dimension: volume
match:
  include: [olive oil, vegetable oil, canola oil, avocado oil, corn oil, peanut oil, sunflower oil, cooking oil]
  exclude: [spray, mist, mister, sprayer, tuna, sardines, salmon, anchovies, mayo, mayonnaise, dressing, chips, crackers, olives, soap, shampoo, conditioner, lotion, skin, hair, body, ear, baby, dispenser, infused, truffle, popcorn, hummus, pesto, with olive oil, in olive oil, made with, margarine, spread, blend, butter, cruet, pourer]
attributes:
  type: {type: enum, values: [extra_virgin_olive, olive, vegetable, canola, avocado, corn, peanut, sunflower], patterns: ['(?<!\bvirgin )\b(olive) oil\b'], synonyms: {extra_virgin_olive: [extra virgin, extra-virgin, evoo], vegetable: [vegetable oil], canola: [canola], avocado: [avocado], corn: [corn oil], peanut: [peanut oil], sunflower: [sunflower]}}
  organic: *organic
comparison: {default: per_volume}
search: [olive oil, vegetable oil, canola oil]
```

```yaml
# categories/pantry/sugar.yaml
name: Sugar
dimension: mass
match:
  include: [granulated sugar, cane sugar, white sugar, brown sugar, powdered sugar, confectioners sugar, confectioners' sugar, turbinado sugar, baking sugar]
  exclude: [oatmeal, pop-tarts, pop tarts, cereal, syrup, candy, bacon, ham, glaze, seasoning, rub, scrub, lip, cinnamon, maple, blend, stevia, monk fruit, sweetener, substitute, cookies, cookie, frosted, icing, frosting, gum, sugar free, sugar-free, zero sugar, no sugar, keto, wax, packets]
attributes:
  type: {type: enum, values: [granulated, brown, powdered, raw], synonyms: {granulated: [granulated, white sugar, extra fine], brown: [brown sugar, light brown, dark brown], powdered: [powdered, confectioners, confectioners', 10x], raw: [turbinado, raw cane, demerara, raw sugar]}}
  organic: *organic
comparison: {default: per_mass}
search: [granulated sugar, brown sugar, powdered sugar]
```

```yaml
# categories/pantry/protein-powder.yaml
name: Protein Powder
dimension: mass
match:
  include: [protein powder, whey protein, plant protein, casein protein, pea protein]
  exclude: [bar, bars, shake, shakes, ready to drink, drink, cookies, chips, pancake, waffle, puffs, cereal, shaker, bottle, scoop, cups, pudding, ice cream, snack, bites, pasta, dog, collagen, peanut butter, oatmeal, coffee, creamer, water, packets, single serve]
attributes:
  type: {type: enum, values: [whey, whey_isolate, casein, plant], patterns: ['\b(whey)\b(?! isolate)(?! protein isolate)'], synonyms: {whey_isolate: [whey isolate, whey protein isolate], casein: [casein], plant: [plant protein, plant-based, plant based, pea protein, vegan]}}
  organic: {type: bool, synonyms: {true: [organic]}, off_label: 'en:organic', absent: false}
comparison: {default: per_mass}
search: [whey protein powder, plant protein powder]
```

- [ ] **Step 1: Write the fixtures:**

```yaml
# tests/categories/pantry/peanut-butter.yaml
- {title: 'Jif Creamy Peanut Butter, 40 oz', brand: Jif, price: '6.98', expect: {category: pantry/peanut-butter, attributes: {texture: creamy, natural: false, organic: false, brand_tier: name_brand}, measures: {mass_g: 1133.98}, unit_prices: {per_mass: '0.006155'}, unresolved: []}}
- {title: 'Skippy Super Chunk Peanut Butter, 16.3 oz', expect: {category: pantry/peanut-butter, attributes: {texture: crunchy}, measures: {mass_g: 462.1}}}
- {title: 'Smucker''s Natural Creamy Peanut Butter, 16 oz', expect: {category: pantry/peanut-butter, attributes: {texture: creamy, natural: true}, measures: {mass_g: 453.59}}}
- {title: 'Jif Creamy Peanut Butter, 7g Protein per Serving, 40 oz', expect: {category: pantry/peanut-butter, attributes: {texture: creamy}, measures: {mass_g: 1133.98}}}
- {title: 'Justin''s Classic Peanut Butter Squeeze Packs, 1.15 oz, 10 Count', brand: Justin's, expect: {category: pantry/peanut-butter, measures: {}, unresolved: ['attribute:texture', quantity, 'unit_price:per_mass']}}
- {title: 'Crazy Richard''s 100% Natural Crunchy Peanut Butter, 16 oz', expect: {category: pantry/peanut-butter, attributes: {texture: crunchy, natural: true}}}
- {title: '365 by Whole Foods Market Organic Creamy Peanut Butter, 16 oz', brand: 365 by Whole Foods Market, expect: {category: pantry/peanut-butter, attributes: {texture: creamy, organic: true, brand_tier: store_brand}}}
- {title: 'Reese''s Peanut Butter Cups, Milk Chocolate, 1.5 oz', expect: {category: null}}
- {title: 'PB2 Powdered Peanut Butter, 6.5 oz', expect: {category: null}}
- {title: 'Ritz Peanut Butter Cracker Sandwiches, 8 ct', expect: {category: null}}
```

```yaml
# tests/categories/pantry/cooking-oil.yaml
- {title: 'Pompeian Robust Extra Virgin Olive Oil, 68 fl oz', brand: Pompeian, price: '21.98', expect: {category: pantry/cooking-oil, attributes: {type: extra_virgin_olive, organic: false, brand_tier: name_brand}, measures: {volume_ml: 2011.0}, unit_prices: {per_volume: '0.010930'}, unresolved: []}}
- {title: 'Great Value Canola Oil, 1 gal', brand: Great Value, expect: {category: pantry/cooking-oil, attributes: {type: canola, brand_tier: store_brand}, measures: {volume_ml: 3785.41}}}
- {title: 'Crisco Pure Vegetable Oil, 48 oz', expect: {category: pantry/cooking-oil, attributes: {type: vegetable}, measures: {volume_ml: 1419.53}}}
- {title: 'Bertolli Classico Olive Oil, 33.8 fl oz', expect: {category: pantry/cooking-oil, attributes: {type: olive}, measures: {volume_ml: 999.59}}}
- {title: 'Chosen Foods 100% Pure Avocado Oil, 1 L', expect: {category: pantry/cooking-oil, attributes: {type: avocado}, measures: {volume_ml: 1000}}}
- {title: 'Mazola Corn Oil, 40 fl oz', expect: {category: pantry/cooking-oil, attributes: {type: corn}, measures: {volume_ml: 1182.94}}}
- {title: 'Kirkland Signature Organic Extra Virgin Olive Oil, 2 L', brand: Kirkland Signature, expect: {category: pantry/cooking-oil, attributes: {type: extra_virgin_olive, organic: true, brand_tier: store_brand}, measures: {volume_ml: 2000}}}
- {title: 'Pompeian Extra Virgin Olive Oil Cooking Spray, 7 oz', expect: {category: null}}
- {title: 'StarKist Chunk Light Tuna in Olive Oil, 5 oz', expect: {category: null}}
- {title: 'Hellmann''s Mayonnaise Dressing with Olive Oil, 30 fl oz', expect: {category: null}}
```

```yaml
# tests/categories/pantry/sugar.yaml
- {title: 'Domino Premium Pure Cane Granulated Sugar, 4 lb', brand: Domino, price: '3.98', expect: {category: pantry/sugar, attributes: {type: granulated, organic: false, brand_tier: name_brand}, measures: {mass_g: 1814.37}, unit_prices: {per_mass: '0.002194'}, unresolved: []}}
- {title: 'C&H Pure Cane Light Brown Sugar, 2 lb', expect: {category: pantry/sugar, attributes: {type: brown}, measures: {mass_g: 907.18}}}
- {title: 'Great Value Pure Granulated Sugar, 4 lb', brand: Great Value, expect: {category: pantry/sugar, attributes: {type: granulated, brand_tier: store_brand}}}
- {title: 'Domino Confectioners Powdered Sugar, 2 lb', expect: {category: pantry/sugar, attributes: {type: powdered}, measures: {mass_g: 907.18}}}
- {title: 'Wholesome Organic Turbinado Raw Cane Sugar, 1.5 lb', expect: {category: pantry/sugar, attributes: {type: raw, organic: true}, measures: {mass_g: 680.39}}}
- {title: 'Imperial Sugar Dark Brown Sugar, 2 lb', expect: {category: pantry/sugar, attributes: {type: brown}}}
- {title: 'Florida Crystals Organic Cane Sugar, 2 lb', expect: {category: pantry/sugar, attributes: {organic: true}, unresolved: ['attribute:type', 'attribute:brand_tier']}}
- {title: 'Quaker Instant Oatmeal, Maple & Brown Sugar, 10 Packets', expect: {category: null}}
- {title: 'Pop-Tarts Frosted Brown Sugar Cinnamon Toaster Pastries, 8 ct', expect: {category: null}}
- {title: 'Truvia Cane Sugar Blend, 1.5 lb', expect: {category: null}}
```

```yaml
# tests/categories/pantry/protein-powder.yaml
- {title: 'Optimum Nutrition Gold Standard 100% Whey Protein Powder, Double Rich Chocolate, 5 lb', brand: Optimum Nutrition, price: '74.99', expect: {category: pantry/protein-powder, attributes: {type: whey, organic: false, brand_tier: name_brand}, measures: {mass_g: 2267.96}, unit_prices: {per_mass: '0.033065'}, unresolved: []}}
- {title: 'Premier Protein 100% Whey Protein Powder, Chocolate Milkshake, 30g Protein, 24.5 oz', expect: {category: pantry/protein-powder, attributes: {type: whey}, measures: {mass_g: 694.56}}}
- {title: 'Dymatize ISO100 Hydrolyzed Whey Protein Isolate Powder, Gourmet Vanilla, 5 lb', expect: {category: pantry/protein-powder, attributes: {type: whey_isolate}, measures: {mass_g: 2267.96}}}
- {title: 'Orgain Organic Vegan Protein Powder, Creamy Chocolate Fudge, 21g Plant Based Protein, 2.03 lb', expect: {category: pantry/protein-powder, attributes: {type: plant, organic: true}, measures: {mass_g: 920.79}}}
- {title: 'Naked Casein Micellar Casein Protein Powder, Unflavored, 5 lb', expect: {category: pantry/protein-powder, attributes: {type: casein}, measures: {mass_g: 2267.96}}}
- {title: 'Body Fortress 100% Whey Protein Powder, Vanilla, 1.78 lb', expect: {category: pantry/protein-powder, attributes: {type: whey}, measures: {mass_g: 807.39}}}
- {title: 'Isopure Zero Carb Whey Protein Isolate Powder, Unflavored, 3 lb', expect: {category: pantry/protein-powder, attributes: {type: whey_isolate}, measures: {mass_g: 1360.78}}}
- {title: 'Premier Protein Shake, Chocolate, 11 fl oz, 12 Pack', expect: {category: null}}
- {title: 'BlenderBottle Classic Shaker Bottle for Protein Powder, 28 oz', expect: {category: null}}
- {title: 'Quest Protein Powder Cookies & Cream Protein Bars, 12 ct', expect: {category: null}}
```

- [ ] **Step 2: Add `tests/web/test_query.py::test_pantry_staples`**, parametrized:
  - `"olive oil"` → `("pantry/cooking-oil", {"type": "olive"})`
  - `"extra virgin olive oil"` → `("pantry/cooking-oil", {"type": "extra_virgin_olive"})`
  - `"brown sugar"` → `("pantry/sugar", {"type": "brown"})`
  - `"whey protein isolate"` → `("pantry/protein-powder", {"type": "whey_isolate"})`
  - `"peanut butter"` → `("pantry/peanut-butter", {})`
- [ ] **Step 3: Run** `uv run pytest tests/test_category_fixtures.py tests/web/test_query.py -v`. Expected: FAIL.
- [ ] **Step 4: Write the four category files.** In `docs/roadmap.md`, change task 7.3 to "High-protein categories beyond protein powder (added in 5.2, per mass); a 'fitness' landing page". Run `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: peanut butter, cooking oil, sugar, protein powder categories (#38)`

---

### Task 7: Beverages: bottled water, sparkling water, orange juice, soda

Gated. Proceed per category only after `SearchDemand` has ≥ 4 weeks of data, when the category's phrases rank in the top 10 of 'curate next' **and** it needs a precise attribute (fat %, roast, grain …) or a special unit. Mass- and volume-priced foods with no such need stay generic.

**Files:**
- Create: `categories/beverages/{bottled-water,sparkling-water,orange-juice,soda}.yaml` and their fixture files
- Modify: `tests/web/test_query.py`

**Interfaces:**
- Consumes: Task 1 (container counts: `beverages/bottled-water:1`, `beverages/sparkling-water:6`, `beverages/soda:5`).

Look-alikes:
- soda vs **baking soda**, washing soda, soda crackers and SodaStream
- "Club Soda" goes to sparkling water: soda excludes it, and sparkling water includes it
- bottled water vs anything sparkling, filter pitchers and dog fountains
- orange juice vs frozen concentrate and "juice beverage"

The OJ `concentrate` enum uses a pattern with a `(?<!\bnot )` lookbehind, so "Not From Concentrate" never reads as "From Concentrate". It is never inferred from brand names: Tropicana Pure Premium without the phrase stays unresolved (`beverages/orange-juice:0`).

```yaml
# categories/beverages/bottled-water.yaml
name: Bottled Water
dimension: volume
match:
  include: [spring water, purified water, drinking water, bottled water, alkaline water, distilled water, artesian water]
  exclude: [sparkling, carbonated, seltzer, flavored, infused, enhancer, drops, filter, filtration, pitcher, dispenser, cooler, reusable, stainless, tumbler, insulated, dog, wipes, coconut, sports drink, tea]
attributes:
  water_type: {type: enum, values: [spring, purified, alkaline, distilled, artesian], synonyms: {spring: [spring water], purified: [purified], alkaline: [alkaline], distilled: [distilled], artesian: [artesian]}}
comparison: {default: per_volume}
search: [spring water, purified water]
```

```yaml
# categories/beverages/sparkling-water.yaml
name: Sparkling Water
dimension: volume
match:
  include: [sparkling water, seltzer, seltzer water, club soda, soda water, mineral water, carbonated water, sparkling spring water]
  exclude: [hard seltzer, spiked, alcohol, vodka, maker, sodastream, machine, co2, cylinder, drops, dispenser]
attributes:
  unflavored: {type: bool, synonyms: {true: [unflavored, original, plain, pure, club soda]}, absent: false}
comparison: {default: per_volume}
search: [sparkling water, seltzer water]
```

```yaml
# categories/beverages/orange-juice.yaml
name: Orange Juice
dimension: volume
match:
  include: [orange juice]
  exclude: [frozen, drink, cocktail, punch, soda, pops, gummies, vodka, mimosa, smoothie, powder, candy, beverage, juice boxes]
attributes:
  pulp: {type: enum, values: [no_pulp, some_pulp, high_pulp], synonyms: {no_pulp: [no pulp, pulp free, pulp-free], some_pulp: [some pulp, homestyle], high_pulp: [lots of pulp, high pulp, grovestand, extra pulp]}}
  concentrate: {type: enum, values: [concentrate, not_from_concentrate], patterns: ['(?<!\bnot )\bfrom (concentrate)\b'], synonyms: {not_from_concentrate: [not from concentrate]}}
  organic: *organic
comparison: {default: per_volume}
search: [orange juice]
```

```yaml
# categories/beverages/soda.yaml
name: Soda
dimension: volume
match:
  include: [soda, soda pop, soft drink, cola]
  exclude: [baking soda, club soda, soda water, washing soda, caustic soda, soda ash, crackers, soda bread, sodastream, maker, syrup, mix, sparkling water, seltzer, lip balm, gummies, candy, cake, glass, glasses, dispenser, can holder, koozie]
attributes:
  diet: {type: bool, synonyms: {true: [diet, zero sugar, zero, sugar free, sugar-free]}, absent: false}
  caffeine_free: {type: bool, synonyms: {true: [caffeine free, caffeine-free]}, absent: false}
comparison: {default: per_volume}
search: [cola, soda pop]
```

- [ ] **Step 1: Write the fixtures:**

```yaml
# tests/categories/beverages/bottled-water.yaml
- {title: 'Poland Spring 100% Natural Spring Water, 16.9 fl oz, 24 Pack Bottles', brand: Poland Spring, price: '8.98', expect: {category: beverages/bottled-water, attributes: {water_type: spring, brand_tier: name_brand}, measures: {volume_ml: 11995.0}, unit_prices: {per_volume: '0.000749'}, unresolved: []}}
- {title: 'Great Value Purified Drinking Water, 16.9 fl oz, 40 Count Bottles', brand: Great Value, expect: {category: beverages/bottled-water, attributes: {water_type: purified, brand_tier: store_brand}, measures: {volume_ml: 19991.7}}}
- {title: 'Nestle Pure Life Purified Water, 8 fl oz, 24 Pack', expect: {category: beverages/bottled-water, attributes: {water_type: purified}, measures: {volume_ml: 5678.12}}}
- {title: 'Great Value Distilled Water, 1 gal', expect: {category: beverages/bottled-water, attributes: {water_type: distilled}, measures: {volume_ml: 3785.41}}}
- {title: 'Essentia Ionized Alkaline Water, 1.5 L', expect: {category: beverages/bottled-water, attributes: {water_type: alkaline}, measures: {volume_ml: 1500}}}
- {title: 'Fiji Natural Artesian Water, 16.9 fl oz, 6 Pack', expect: {category: beverages/bottled-water, attributes: {water_type: artesian}, measures: {volume_ml: 2998.75}}}
- {title: 'Arrowhead Mountain Spring Water, 101.4 fl oz', expect: {category: beverages/bottled-water, attributes: {water_type: spring}, measures: {volume_ml: 2998.76}}}
- {title: 'PUR Plus Drinking Water Filter Pitcher, 11 Cup', expect: {category: null}}
- {title: 'Dog Drinking Water Fountain, 2.4 L', expect: {category: null}}
```

```yaml
# tests/categories/beverages/sparkling-water.yaml
- {title: 'LaCroix Sparkling Water, Lime, 12 fl oz, 12 Pack Cans', brand: LaCroix, price: '5.98', expect: {category: beverages/sparkling-water, attributes: {unflavored: false, brand_tier: name_brand}, measures: {volume_ml: 4258.59}, unit_prices: {per_volume: '0.001404'}, unresolved: []}}
- {title: 'Polar Original Seltzer Water, 1 L', expect: {category: beverages/sparkling-water, attributes: {unflavored: true}, measures: {volume_ml: 1000}}}
- {title: 'Canada Dry Club Soda, 12 fl oz, 12 Pack', expect: {category: beverages/sparkling-water, attributes: {unflavored: true}, measures: {volume_ml: 4258.59}}}
- {title: 'Topo Chico Mineral Water, 12 fl oz, 12 Pack Glass Bottles', expect: {category: beverages/sparkling-water, measures: {volume_ml: 4258.59}}}
- {title: 'Poland Spring Sparkling Spring Water, Lemon, 16.9 fl oz, 8 Pack', expect: {category: beverages/sparkling-water, measures: {volume_ml: 3998.34}}}
- {title: 'Great Value Sparkling Water, Black Cherry, 33.8 fl oz', brand: Great Value, expect: {category: beverages/sparkling-water, attributes: {brand_tier: store_brand}, measures: {volume_ml: 999.59}}}
- {title: 'Perrier Carbonated Mineral Water, 16.9 fl oz, 24 Count Bottles', expect: {category: beverages/sparkling-water, measures: {volume_ml: 11995.0}}}
- {title: 'Bubly Sparkling Water, Grapefruit, 12 fl oz Cans, 8 Pack', expect: {category: beverages/sparkling-water, measures: {volume_ml: 2839.06}}}
- {title: 'White Claw Hard Seltzer Variety Pack, 12 fl oz, 12 ct', expect: {category: null}}
- {title: SodaStream Terra Sparkling Water Maker, expect: {category: null}}
```

```yaml
# tests/categories/beverages/orange-juice.yaml
- {title: 'Tropicana Pure Premium No Pulp 100% Orange Juice, 52 fl oz', brand: Tropicana, price: '4.98', expect: {category: beverages/orange-juice, attributes: {pulp: no_pulp, organic: false, brand_tier: name_brand}, measures: {volume_ml: 1537.82}, unit_prices: {per_volume: '0.003238'}, unresolved: ['attribute:concentrate']}}
- {title: 'Simply Orange Pulp Free Orange Juice, 52 fl oz', expect: {category: beverages/orange-juice, attributes: {pulp: no_pulp}, measures: {volume_ml: 1537.82}}}
- {title: 'Florida''s Natural Orange Juice with Some Pulp, Not From Concentrate, 52 fl oz', expect: {category: beverages/orange-juice, attributes: {pulp: some_pulp, concentrate: not_from_concentrate}}}
- {title: 'Great Value 100% Orange Juice From Concentrate, 64 fl oz', brand: Great Value, expect: {category: beverages/orange-juice, attributes: {concentrate: concentrate, brand_tier: store_brand}, measures: {volume_ml: 1892.71}}}
- {title: 'Tropicana Pure Premium Lots of Pulp Orange Juice, 89 fl oz', expect: {category: beverages/orange-juice, attributes: {pulp: high_pulp}, measures: {volume_ml: 2632.04}}}
- {title: 'Kirkland Signature Organic Orange Juice, 6 x 59 fl oz', brand: Kirkland Signature, expect: {category: beverages/orange-juice, attributes: {organic: true, brand_tier: store_brand}, measures: {volume_ml: 10469.03}}}
- {title: 'Simply Orange High Pulp Orange Juice, Not From Concentrate, 89 fl oz', expect: {category: beverages/orange-juice, attributes: {pulp: high_pulp, concentrate: not_from_concentrate}}}
- {title: 'Minute Maid Frozen Orange Juice Concentrate, 12 fl oz', expect: {category: null}}
- {title: 'Tropicana Trop50 Orange Juice Beverage, 52 fl oz', expect: {category: null}}
```

```yaml
# tests/categories/beverages/soda.yaml
- {title: 'Coca-Cola Soda Pop, 12 fl oz, 12 Pack Cans', brand: Coca-Cola, price: '7.48', expect: {category: beverages/soda, attributes: {diet: false, caffeine_free: false, brand_tier: name_brand}, measures: {volume_ml: 4258.59}, unit_prices: {per_volume: '0.001756'}, unresolved: []}}
- {title: 'Diet Coke Soda Pop, 12 fl oz, 12 Pack Cans', expect: {category: beverages/soda, attributes: {diet: true}, measures: {volume_ml: 4258.59}}}
- {title: 'Coca-Cola Zero Sugar Soda Pop, 2 L', expect: {category: beverages/soda, attributes: {diet: true}, measures: {volume_ml: 2000}}}
- {title: 'Pepsi Cola Soda Pop, 12 fl oz, 24 Pack Cans', expect: {category: beverages/soda, attributes: {diet: false}, measures: {volume_ml: 8517.18}}}
- {title: 'Sprite Lemon Lime Soda, 2 Liters', expect: {category: beverages/soda, measures: {volume_ml: 2000}}}
- {title: 'Great Value Cola Soda, 12 fl oz, 12 Count Cans', brand: Great Value, expect: {category: beverages/soda, attributes: {brand_tier: store_brand}, measures: {volume_ml: 4258.59}}}
- {title: 'Caffeine Free Diet Coke Soda, 12 fl oz, 12 Pack Cans', expect: {category: beverages/soda, attributes: {diet: true, caffeine_free: true}}}
- {title: 'Dr Pepper Soda, 16.9 fl oz, 6 Pack Bottles', expect: {category: beverages/soda, measures: {volume_ml: 2998.75}}}
- {title: 'Arm & Hammer Pure Baking Soda, 1 lb', expect: {category: null}}
- {title: 'Nabisco Premium Soda Crackers, 16 oz', expect: {category: null}}
```

- [ ] **Step 2: Add `tests/web/test_query.py::test_beverages`**, parametrized:
  - `"club soda"` → `("beverages/sparkling-water", {"unflavored": True})`
  - `"diet soda"` → `("beverages/soda", {"diet": True})`
  - `"spring water"` → `("beverages/bottled-water", {"water_type": "spring"})`
  - `"orange juice not from concentrate"` → `("beverages/orange-juice", {"concentrate": "not_from_concentrate"})`

  Add `test_baking_soda_unmatched`: `r("baking soda").category is None`.
- [ ] **Step 3: Run** `uv run pytest tests/test_category_fixtures.py tests/web/test_query.py -v`. Expected: FAIL.
- [ ] **Step 4: Write the four category files.** Run `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: water, sparkling water, orange juice, soda categories (#38)`

---

### Task 8: Household paper and bags: facial tissue, trash bags

**Files:**
- Create: `categories/household/{facial-tissue,trash-bags}.yaml` and their fixture files
- Modify: `tests/web/test_query.py`

**Interfaces:**
- Consumes: Task 3 (`tissue` dimension, `sheets_x_ply`) and Task 1 (`bags` count noun: `household/trash-bags:3`).

The facial-tissue includes are "facial/face tissue(s)" only. A bare "tissues" would claim "Bath Tissues" toilet paper, because offer titles get no plural folding.

The trash-bag capacity is an `int` attribute in gallons, not a quantity, since a trash bag's "13 Gallon" is its size, not its contents. The pattern's lookbehind `(?<![\d.-])` leaves a range like "8-10 Gallon" unresolved (`household/trash-bags:5`).

```yaml
# categories/household/facial-tissue.yaml
name: Facial Tissue
parent: household/paper-goods
dimension: tissue
match:
  include: [facial tissue, facial tissues, face tissue, face tissues]
  exclude: [cover, holder, tissue paper, gift, toilet, bath, wipes]
attributes:
  ply: {type: int, values: [1, 2, 3], patterns: ['(?<![\d.])([123])[- ]?ply'], synonyms: {1: [single ply], 2: [double ply]}}
  lotion: {type: bool, synonyms: {true: [lotion]}, absent: false}
comparison: {default: sheets_x_ply, alternates: [per_100_sheets]}
search: [facial tissue]
```

```yaml
# categories/household/trash-bags.yaml
name: Trash Bags
dimension: count
match:
  include: [trash bags, trash bag, garbage bags, garbage bag, can liners, kitchen bags, wastebasket bags, lawn & leaf bags, lawn and leaf bags, contractor bags]
  exclude: [holder, dispenser, rack, dog, poop, pet waste, diaper pail]
attributes:
  capacity_gal: {type: int, patterns: ['(?<![\d.-])(\d{1,2})[- ]?(?:gal|gallons?)\b']}
  drawstring: {type: bool, synonyms: {true: [drawstring]}, absent: false}
comparison: {default: per_count}
search: [13 gallon trash bags, 30 gallon trash bags]
```

- [ ] **Step 1: Write the fixtures:**

```yaml
# tests/categories/household/facial-tissue.yaml
- {title: 'Kleenex Ultra Soft Facial Tissues, 3-Ply, 8 Flat Boxes, 120 Tissues per Box (960 Tissues Total)', brand: Kleenex, price: '15.99', expect: {category: household/facial-tissue, attributes: {ply: 3, lotion: false, brand_tier: name_brand}, measures: {boxes: 8, sheets: 960}, unit_prices: {sheets_x_ply: '0.555208', per_100_sheets: '1.665625'}, unresolved: []}}
- {title: 'Puffs Plus Lotion Facial Tissues, 2-Ply, 6 Family Boxes, 124 Facial Tissues per Box', expect: {category: household/facial-tissue, attributes: {ply: 2, lotion: true}, measures: {boxes: 6, sheets: 744}}}
- {title: 'Kleenex Trusted Care Facial Tissues, 2-Ply, 144 Tissues', expect: {category: household/facial-tissue, attributes: {ply: 2}, measures: {sheets: 144}}}
- {title: 'Great Value Facial Tissues, 2-Ply, 4 Cube Boxes, 70 Tissues', brand: Great Value, expect: {category: household/facial-tissue, attributes: {ply: 2, brand_tier: store_brand}, measures: {boxes: 4, sheets: 280}}}
- {title: 'Kleenex Facial Tissue, 160 Tissues per Box, Pack of 3', brand: Kleenex, price: '9.99', expect: {category: household/facial-tissue, measures: {boxes: 3, sheets: 480}, unit_prices_exact: true, unit_prices: {per_100_sheets: '2.081250'}, unresolved: ['attribute:ply', 'unit_price:sheets_x_ply']}}
- {title: 'Kleenex Expressions Facial Tissues, 2-Ply, 18 Flat Boxes, 144 Tissues per Box (2,592 Total Tissues)', expect: {category: household/facial-tissue, attributes: {ply: 2}, measures: {boxes: 18, sheets: 2592}}}
- {title: 'Puffs Basic Facial Tissues, 3 Pack, 128 Count', brand: Puffs, expect: {category: household/facial-tissue, measures: {}, unresolved: ['attribute:ply', quantity, 'unit_price:sheets_x_ply']}}
- {title: 'Scotties Facial Tissue, 2-Ply, 10 Boxes, 1,200 Count', brand: Scotties, expect: {category: household/facial-tissue, attributes: {ply: 2}, measures: {}, unresolved: [quantity, 'unit_price:sheets_x_ply']}}
- {title: 'Facial Tissue Box Cover, Rectangular, Wood', expect: {category: null}}
- {title: 'Wall Mount Facial Tissue Holder, Brushed Nickel', expect: {category: null}}
```

```yaml
# tests/categories/household/trash-bags.yaml
- {title: 'Glad ForceFlex Tall Kitchen Drawstring Trash Bags, 13 Gallon, 110 Count', brand: Glad, price: '18.97', expect: {category: household/trash-bags, attributes: {capacity_gal: 13, drawstring: true, brand_tier: name_brand}, measures: {count: 110}, unit_prices: {per_count: '0.172455'}, unresolved: []}}
- {title: 'Hefty Ultra Strong Large Trash Bags, 30 Gallon, 74 Count', expect: {category: household/trash-bags, attributes: {capacity_gal: 30, drawstring: false}, measures: {count: 74}}}
- {title: 'Amazon Basics Tall Kitchen Drawstring Trash Bags, 13 Gallon, 120 Count (Pack of 1)', brand: Amazon Basics, expect: {category: household/trash-bags, attributes: {capacity_gal: 13, brand_tier: store_brand}, measures: {count: 120}}}
- {title: 'Glad Small Kitchen Trash Bags, 4 Gallon, 26 Bags', expect: {category: household/trash-bags, attributes: {capacity_gal: 4}, measures: {count: 26}}}
- {title: 'Hefty Strong Lawn & Leaf Bags, 39 Gallon, 38 Count', expect: {category: household/trash-bags, attributes: {capacity_gal: 39}, measures: {count: 38}}}
- {title: 'simplehuman Code J Custom Fit Drawstring Trash Bags, 8-10 Gallon, 50 Count', brand: simplehuman, expect: {category: household/trash-bags, attributes: {drawstring: true}, measures: {count: 50}, unresolved: ['attribute:capacity_gal']}}
- {title: 'Kirkland Signature Drawstring Kitchen Trash Bags, 13 Gallon, 200 Count', brand: Kirkland Signature, expect: {category: household/trash-bags, attributes: {capacity_gal: 13, brand_tier: store_brand}, measures: {count: 200}}}
- {title: 'Glad OdorShield Tall Kitchen Drawstring Trash Bags, Febreze Fresh Clean, 13 Gallon, 80 Count', expect: {category: household/trash-bags, attributes: {capacity_gal: 13, drawstring: true}, measures: {count: 80}}}
- {title: 'Under Sink Trash Bag Holder, Stainless Steel', expect: {category: null}}
- {title: Trash Bag Dispenser Roll Holder for Grocery Bags, expect: {category: null}}
```

- [ ] **Step 2: Add `tests/web/test_query.py::test_household_paper_and_bags`**, parametrized:
  - `"3 ply facial tissue"` → `("household/facial-tissue", {"ply": 3})`
  - `"13 gallon trash bags"` → `("household/trash-bags", {"capacity_gal": 13})`

  Also assert `r("paper towels").category == "household/paper-towels"`, which is unchanged.
- [ ] **Step 3: Run** `uv run pytest tests/test_category_fixtures.py tests/web/test_query.py -v`. Expected: FAIL.
- [ ] **Step 4: Write the two category files.** Run `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: facial tissue and trash bag categories (#38)`

---

- [ ] **Step 2b (moved from Task 4): Make the sync-count test derive its number.** In `test_sync_categories_command_prints_counts`, assert `f"created={len(load_categories(settings.CATEGORIES_DIR))} updated=0 deactivated=0"`. That is the root the command reads. Task 8 is now the first category batch to run.

### Task 9: Cleaning and laundry: laundry detergent, dish soap, dishwasher detergent

**Files:**
- Create: `categories/household/{laundry-detergent,dish-soap,dishwasher-detergent}.yaml` and their fixture files
- Modify: `tests/web/test_query.py`

**Interfaces:**
- Consumes: Task 2 (`laundry`, `per_load`) and Task 1 (`pacs|pods|tabs|tablets` count nouns).

Spec §6 says detergent is "per load, falling back to per volume". This plan reads the fallback as the alternates a user can switch to (`per_volume`, `per_mass`, `per_count`). It does not mix units in one ranking. An offer with no stated loads is unranked under `per_load`, labeled "can't compute price per load", and ranked under the others.

Dishwasher gel and powder are excluded: their size is a weight with no dose count. Pods and tablets compare per count. Dish soap excludes "dishwasher", and the dishwasher includes all contain "dishwasher", so the two never collide.

```yaml
# categories/household/laundry-detergent.yaml
name: Laundry Detergent
dimension: laundry
match:
  include: [laundry detergent, laundry soap, laundry pacs, laundry pods]
  exclude: [stain remover, fabric softener, softener, dryer sheets, booster, scent beads, in-wash, sanitizer, basket, hamper, dispenser, pretreater, pen, wipes]
attributes:
  form: {type: enum, values: [liquid, powder, pods, sheets], synonyms: {liquid: [liquid], powder: [powder, powdered], pods: [pods, pacs, pac, flings], sheets: [sheets, strips]}}
  he: {type: bool, synonyms: {true: [he, high efficiency]}, absent: false}
  free_clear: {type: bool, synonyms: {true: [free & clear, free and clear, free clear, free + clear]}, absent: false}
comparison: {default: per_load, alternates: [per_volume, per_mass, per_count]}
search: [laundry detergent, laundry detergent pods]
```

```yaml
# categories/household/dish-soap.yaml
name: Dish Soap
dimension: volume
match:
  include: [dish soap, dishwashing liquid, dish liquid, dishwashing soap, dish spray, liquid dish soap, dish washing liquid]
  exclude: [dishwasher, dispenser, brush, sponge, scrubber, holder, caddy, rack, wand, tray]
attributes:
  antibacterial: {type: bool, synonyms: {true: [antibacterial, anti-bacterial]}, absent: false}
  free_clear: {type: bool, synonyms: {true: [free & clear, free and clear, free clear, free + clear]}, absent: false}
comparison: {default: per_volume}
search: [dish soap]
```

```yaml
# categories/household/dishwasher-detergent.yaml
name: Dishwasher Detergent
dimension: count
match:
  include: [dishwasher detergent, dishwasher pods, dishwasher pacs, dishwasher tablets, dishwasher tabs]
  exclude: [gel, powder, cleaner, rinse aid]
attributes:
  free_clear: {type: bool, synonyms: {true: [free & clear, free and clear, free clear, free + clear, fragrance free]}, absent: false}
comparison: {default: per_count}
search: [dishwasher pods, dishwasher detergent]
```

- [ ] **Step 1: Write the fixtures:**

```yaml
# tests/categories/household/laundry-detergent.yaml
- {title: 'Tide Original Liquid Laundry Detergent, 92 fl oz, 64 Loads', brand: Tide, price: '12.97', expect: {category: household/laundry-detergent, attributes: {form: liquid, he: false, free_clear: false, brand_tier: name_brand}, measures: {loads: 64, volume_ml: 2720.76}, unit_prices: {per_load: '0.202656', per_volume: '0.004767'}, unresolved: []}}
- {title: 'Gain Original Scent HE Liquid Laundry Detergent, 154 fl oz, 107 Loads', expect: {category: household/laundry-detergent, attributes: {form: liquid, he: true}, measures: {loads: 107, volume_ml: 4554.32}}}
- {title: 'Tide PODS Laundry Detergent Pacs, Original, 81 Count', brand: Tide, price: '21.97', expect: {category: household/laundry-detergent, attributes: {form: pods}, measures: {count: 81}, unit_prices_exact: true, unit_prices: {per_count: '0.271235'}, unresolved: ['unit_price:per_load']}}
- {title: 'Arm & Hammer Clean Burst Liquid Laundry Detergent, 105 oz, 70 Loads', expect: {category: household/laundry-detergent, attributes: {form: liquid}, measures: {loads: 70, volume_ml: 3105.22}}}
- {title: 'Tide Original Powder Laundry Detergent, 143 oz, 102 Loads', expect: {category: household/laundry-detergent, attributes: {form: powder}, measures: {loads: 102, mass_g: 4053.98}}}
- {title: 'all Free Clear Liquid Laundry Detergent, 88 fl oz, 58 Loads', expect: {category: household/laundry-detergent, attributes: {form: liquid, free_clear: true}, measures: {loads: 58, volume_ml: 2602.47}}}
- {title: 'Earth Breeze Laundry Detergent Sheets, Fresh Scent, 60 Loads', expect: {category: household/laundry-detergent, attributes: {form: sheets}, measures: {loads: 60}}}
- {title: 'Purex Laundry Detergent, Mountain Breeze, 50 oz, 33 Loads', brand: Purex, expect: {category: household/laundry-detergent, measures: {loads: 33}, unresolved: ['attribute:form']}}
- {title: 'OxiClean White Revive Laundry Detergent Booster, 3 lb', expect: {category: null}}
- {title: 'Wall Mounted Laundry Detergent Dispenser, 2 Pack', expect: {category: null}}
```

```yaml
# tests/categories/household/dish-soap.yaml
- {title: 'Dawn Ultra Dishwashing Liquid Dish Soap, Original Scent, 56 fl oz', brand: Dawn, price: '8.97', expect: {category: household/dish-soap, attributes: {antibacterial: false, free_clear: false, brand_tier: name_brand}, measures: {volume_ml: 1656.12}, unit_prices: {per_volume: '0.005416'}, unresolved: []}}
- {title: 'Dawn Ultra Dish Soap, Original, 28 oz, 3 Pack', expect: {category: household/dish-soap, measures: {volume_ml: 2484.18}}}
- {title: 'Palmolive Ultra Strength Liquid Dish Soap, 32.5 fl oz', expect: {category: household/dish-soap, measures: {volume_ml: 961.14}}}
- {title: 'Great Value Ultra Antibacterial Dish Soap, Orange Scent, 75 fl oz', brand: Great Value, expect: {category: household/dish-soap, attributes: {antibacterial: true, brand_tier: store_brand}, measures: {volume_ml: 2218.01}}}
- {title: 'Seventh Generation Free & Clear Dish Liquid, 19 fl oz', expect: {category: household/dish-soap, attributes: {free_clear: true}, measures: {volume_ml: 561.9}}}
- {title: 'Dawn Powerwash Dish Spray, Fresh Scent, 16 fl oz', expect: {category: household/dish-soap, measures: {volume_ml: 473.18}}}
- {title: 'Mrs. Meyer''s Clean Day Liquid Dish Soap, Lemon Verbena, 16 fl oz', expect: {category: household/dish-soap, measures: {volume_ml: 473.18}}}
- {title: 'Method Dish Soap, Pink Grapefruit, 18 fl oz', expect: {category: household/dish-soap, measures: {volume_ml: 532.32}}}
- {title: 'Sponge and Dish Soap Holder, Kitchen Sink Caddy', expect: {category: null}}
- {title: 'Dish Soap Dispenser with Sponge Holder, Stainless', expect: {category: null}}
```

```yaml
# tests/categories/household/dishwasher-detergent.yaml
- {title: 'Cascade Complete ActionPacs Dishwasher Detergent, Fresh Scent, 78 Count', brand: Cascade, price: '19.97', expect: {category: household/dishwasher-detergent, attributes: {free_clear: false, brand_tier: name_brand}, measures: {count: 78}, unit_prices: {per_count: '0.256026'}, unresolved: []}}
- {title: 'Finish Quantum Dishwasher Detergent Tablets, 82 Count', expect: {category: household/dishwasher-detergent, measures: {count: 82}}}
- {title: 'Finish All in 1 Dishwasher Detergent, Powerball, 94 Tabs', expect: {category: household/dishwasher-detergent, measures: {count: 94}}}
- {title: 'Cascade Platinum Plus ActionPacs Dishwasher Detergent Pods, 62 Count', expect: {category: household/dishwasher-detergent, measures: {count: 62}}}
- {title: 'Great Value Dishwasher Detergent Pacs, Lemon Scent, 85 Count', brand: Great Value, expect: {category: household/dishwasher-detergent, attributes: {brand_tier: store_brand}, measures: {count: 85}}}
- {title: 'Seventh Generation Free & Clear Dishwasher Detergent Packs, 45 Count', expect: {category: household/dishwasher-detergent, attributes: {free_clear: true}, measures: {count: 45}}}
- {title: 'Kirkland Signature Dishwasher Detergent Pacs, 115 Count', brand: Kirkland Signature, expect: {category: household/dishwasher-detergent, attributes: {brand_tier: store_brand}, measures: {count: 115}}}
- {title: 'Lemi Shine Dishwasher Pods, 60 Pods', expect: {category: household/dishwasher-detergent, measures: {count: 60}}}
- {title: 'Cascade Complete Gel Dishwasher Detergent, 75 oz', expect: {category: null}}
- {title: 'Cascade Complete Powder Dishwasher Detergent, 75 oz', expect: {category: null}}
```

- [ ] **Step 2: Add `tests/web/test_query.py::test_cleaning`**, parametrized:
  - `"laundry detergent pods"` → `("household/laundry-detergent", {"form": "pods"})`
  - `"free and clear laundry detergent"` → `("household/laundry-detergent", {"free_clear": True})`
  - `"dish soap"` → `("household/dish-soap", {})`
  - `"dishwasher pods"` → `("household/dishwasher-detergent", {})`
- [ ] **Step 3: Run** `uv run pytest tests/test_category_fixtures.py tests/web/test_query.py -v`. Expected: FAIL.
- [ ] **Step 4: Write the three category files.** Run `uv run pytest -v`. Expected: PASS. `load_categories` now returns 25 slugs, and `test_every_category_has_fixtures` passes.
- [ ] **Step 5: Commit** `feat: laundry, dish soap, dishwasher detergent categories (#38)`

---

## D12 display note

Tasks 2–3 add `per_load` and `sheets_x_ply` `DISPLAY` entries, which are unaffected by open search. Any test in those tasks that pins "/ 100 mL" or "/ 100 g" uses "/ L" or "/ kg" instead.

## Exit criteria (roadmap 5.2)

- `uv run pytest` is green, with the household categories above (facial tissue, trash bags, laundry detergent, dish soap, dishwasher detergent) plus any top-demand categories chosen at review, each with ≥ 8 fixtures, and every Review Focus item pinned by the named test or fixture ID.
- The original 5 categories' fixture files are byte-identical to `main`.
- `uv run --env-file .env python manage.py sync_categories` reports `created=<number of new category files>`.
- `normalize/` still imports no Django, network or AI libraries.
