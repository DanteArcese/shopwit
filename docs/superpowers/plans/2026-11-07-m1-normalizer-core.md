# M1: Normalizer Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pure-Python `normalize` package that turns a raw retailer offer into a category, typed attributes, canonical measures and unit prices, proven by real-title fixtures for 5 categories.

**Architecture:** `normalize/` has no Django, network or database dependency. Every function is pure. Category definitions are YAML files under `categories/` loaded into frozen dataclasses. The pipeline runs: match category → extract attributes (API fields → Open Food Facts labels → patterns → synonyms) → parse measures → compute unit prices for each comparison method the category declares. Anything it can't resolve is reported in `unresolved` and never guessed.

**Tech Stack:** Python 3.13, `uv`, `pytest`, `pyyaml`, `pint`.

**Spec:** `docs/superpowers/specs/2026-11-07-shopwit-design.md` (§3 Principles, §5.2 Normalizer, §6 Category definitions, §7 Units). Roadmap tasks 1.1–1.7 in `docs/roadmap.md`.

## Global Constraints

- Python with `uv` only (`uv add`, `uv run`). No `pip install` or `python -m venv`.
- **No AI/LLM libraries or calls anywhere.** Extraction is deterministic (spec Principle 4).
- `normalize/` must not import Django, open network connections or touch the filesystem, except `load_categories` and `load_store_brands`, which read YAML.
- Money is `decimal.Decimal`, never `float`. Unit prices are `Decimal` quantized to 6 decimal places.
- Canonical measure units: `volume_ml`, `mass_g`, `count`, `rolls`, `sheets`, `area_m2` (all `float`).
- **Never guess** (Principle 3). An unknown value is absent from `attributes` and named in `unresolved`.
- Marketing roll equivalences ("= 72 Regular Rolls") are ignored (spec §6).
- Every category under `categories/` must have a fixtures file with at least 8 cases.
- Each task ends with `uv run pytest` green. The pre-commit hooks (gitleaks, pytest) must pass on commit.

## Review Focus

1. **"oz" in a volume category means fl oz.** "fairlife 2% … 52oz" → `volume_ml ≈ 1537.82`, not a mass. Owned by Task 2.
2. **Percent-like text that isn't fat content.** "100% Lactose Free … 2% Milk" → fat `2`; "a2 Milk Whole Milk" → fat `whole`, never `2`. Owned by Tasks 3 and 6.
3. **Multipacks multiply.** "6 x 12 fl oz", "Pack of 2, 64 oz" and "2 pk 64 fl oz" → total volume. Owned by Task 2.
4. **Conflicting matches within a tier are ambiguous, not first-wins.** "1% and 2%" (patterns) or "Whole Milk & Skim Milk" (synonyms) → fat unresolved as ambiguous. Owned by Task 3.
5. **Roll equivalences are ignored.** "18 Mega Rolls = 72 Regular Rolls" → `rolls == 18`. Owned by Task 2.

---

## File Structure

```
pyproject.toml                    # deps; pytest pythonpath
main.py                           # DELETE (uv init placeholder)
normalize/
  __init__.py                     # re-exports normalize(), load_categories, load_store_brands
  types.py                        # RawOffer, OffProduct, AttrValue, NormalizedOffer, Source
  categories.py                   # AttributeDef, CategoryDef, CategoryError, load_categories()
  quantity.py                     # parse_measures(), Measures, Dimension
  extract.py                      # extract_attributes()
  match.py                        # match_category(), brand_tier(), load_store_brands()
  compare.py                      # Comparison, COMPARISONS, unit_prices()
  pipeline.py                     # normalize()
categories/
  dairy/milk.yaml
  dairy/eggs.yaml
  pantry/coffee.yaml
  household/toilet-paper.yaml
  household/paper-towels.yaml
data/store_brands.yaml
tests/
  normalize/test_categories.py
  normalize/test_quantity.py
  normalize/test_extract.py
  normalize/test_match.py
  normalize/test_compare.py
  normalize/test_pipeline.py
  test_category_fixtures.py       # parametrized over tests/categories/**.yaml
  categories/dairy/milk.yaml      # fixture cases (mirrors categories/ paths)
  categories/dairy/eggs.yaml
  categories/pantry/coffee.yaml
  categories/household/toilet-paper.yaml
  categories/household/paper-towels.yaml
```

---

### Task 1: Core types and category loader (roadmap 1.1)

**Files:**
- Modify: `pyproject.toml` (deps via `uv add pyyaml pint`; add `[tool.pytest.ini_options] pythonpath = ["."]`)
- Delete: `main.py`
- Create: `normalize/__init__.py`, `normalize/types.py`, `normalize/categories.py`
- Test: `tests/normalize/test_categories.py`

**Interfaces:**
- Produces, in `normalize/types.py`:
  - `Source = Literal["api", "off", "rule", "manual"]`
  - `@dataclass(frozen=True) RawOffer(retailer: str, retailer_item_id: str, title: str, price: Decimal, currency: str, url: str, fetched_at: datetime, upc: str | None = None, brand: str | None = None, size_text: str | None = None, fields: Mapping[str, str] = field(default_factory=dict))`
  - `@dataclass(frozen=True) OffProduct(labels: frozenset[str], quantity: str | None = None)` (labels use Open Food Facts tags, e.g. `"en:organic"`)
  - `@dataclass(frozen=True) AttrValue(value: str | int | bool, source: Source)`
  - `@dataclass(frozen=True) NormalizedOffer(raw: RawOffer, category: str | None, attributes: dict[str, AttrValue], measures: dict[str, float], unit_prices: dict[str, Decimal], unresolved: tuple[str, ...])`
- Produces, in `normalize/categories.py`:
  - `Dimension = Literal["volume", "mass", "count", "paper"]`
  - `@dataclass(frozen=True) AttributeDef(name: str, type: Literal["int", "enum", "bool"], values: tuple[str | int, ...] = (), patterns: tuple[re.Pattern, ...] = (), synonyms: Mapping[str | int | bool, tuple[str, ...]] = {}, off_label: str | None = None, absent: bool | None = None)`. `absent` is the value used for a `bool` attribute when nothing matches: `False`, or `None` meaning unknown.
  - `@dataclass(frozen=True) CategoryDef(slug: str, name: str, parent: str | None, include: tuple[str, ...], exclude: tuple[str, ...], dimension: Dimension, attributes: Mapping[str, AttributeDef], comparison_default: str, comparison_alternates: tuple[str, ...])`
  - `class CategoryError(ValueError)` with the message prefixed by the file path
  - `load_categories(root: Path) -> dict[str, CategoryDef]`. Slug = path relative to `root` without `.yaml`, using `/` (e.g. `"dairy/milk"`).
  - `KNOWN_COMPARISONS: frozenset[str] = {"per_volume", "per_mass", "per_count", "area_x_ply", "per_100_sheets", "per_roll"}`. `compare.py` imports this set and must define exactly these.

YAML keys: `name`, `parent`, `dimension`, `match.include`, `match.exclude`, `attributes.<name>.{type, values, patterns, synonyms, off_label, absent}`, `comparison.default`, `comparison.alternates`.

- [ ] **Step 1: Write failing tests** in `tests/normalize/test_categories.py`, using `tmp_path` YAML files:
  - `test_loads_minimal_category`: a file `dairy/milk.yaml` loads with `slug == "dairy/milk"`, `dimension == "volume"`, `comparison_default == "per_volume"`, and patterns compiled case-insensitively.
  - `test_rejects_unknown_top_level_key`: raises `CategoryError`, and `"dairy/milk.yaml"` appears in the message.
  - `test_rejects_invalid_regex`: pattern `"(\d"` → `CategoryError`.
  - `test_rejects_unknown_comparison`: `default: per_banana` → `CategoryError`.
  - `test_enum_requires_values`: an `enum` attribute without `values` → `CategoryError`.
  - `test_synonym_key_must_be_declared_value`: an enum synonym key `"3"` not in `values` → `CategoryError`.
  - `test_pattern_needs_one_capture_group`: pattern `"\d ply"` → `CategoryError`.
- [ ] **Step 2: Run** `uv run pytest tests/normalize/test_categories.py -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the types and `load_categories`. Use `yaml.safe_load`, and compile with `re.compile(p, re.IGNORECASE)`. Enum and int synonym keys are normalized to the declared value's type (YAML may give `2` or `"2"`).
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: core types and category loader`

---

### Task 2: Quantity parsing into canonical measures (roadmap 1.2)

**Files:**
- Create: `normalize/quantity.py`
- Test: `tests/normalize/test_quantity.py`

**Interfaces:**
- Consumes: `Dimension` from Task 1.
- Produces: `Measures = dict[str, float]` and `parse_measures(texts: Sequence[str], dimension: Dimension) -> Measures`. `texts` is tried in order (size_text, title, OFF quantity), and the first text that yields the dimension's required keys wins. Returns `{}` if none do.
  - `volume` → `{"volume_ml": …}`; `mass` → `{"mass_g": …}`; `count` → `{"count": …}`
  - `paper` → `{"rolls": …}` always, plus `"sheets"` (rolls × sheets per roll) and `"area_m2"` when derivable. Area comes from sheet dimensions × total sheets, or from an explicit total such as "112.5 sq ft".

Rules:
- Use `pint` for conversions. US customary units: `fl oz` = 29.5735 mL, `gal` = 3785.41 mL, `qt`, `pt`, `L`, `mL`; `oz` (mass) = 28.3495 g, `lb`, `g`, `kg`.
- In a `volume` category, a bare `oz` is fl oz. `FZ` and `fl. oz.` are fl oz.
- Multipacks multiply: `N x Q`, `N-pack`/`N pk`/`Pack of N` combined with Q.
- Counts: `12 ct`, `18 count`, `dozen` (12), `18 eggs`.
- Paper: rolls come from the first `N <adjective>* rolls` phrase. Anything after `=` (an equivalence claim) is ignored. Sheets from `N sheets per roll`, `N sheets/roll`; sheet size from `W in x H in` or `W x H in`.

- [ ] **Step 1: Write failing tests** (approx comparisons with `pytest.approx(rel=1e-3)`):
  - `test_fl_oz`: `["52 fl oz"]`, volume → `volume_ml ≈ 1537.82`
  - `test_bare_oz_in_volume_is_fluid`: `["fairlife 2% Reduced-Fat Ultra-Filtered 52oz Milk Bottle"]` → `volume_ml ≈ 1537.82`
  - `test_fz_abbreviation`: `["365 Organic Reduced Fat 2% Milk, 32 FZ"]` → `volume_ml ≈ 946.35`
  - `test_litres`: `["Lactaid Reduced Fat 2% Milk, 2.8 L"]` → `volume_ml == 2800`
  - `test_gallon`: `["1 gal"]` → `≈ 3785.41`
  - `test_multipack_x`: `["6 x 12 fl oz"]` → `≈ 2129.29`
  - `test_pack_of`: `["Pack of 2, 64 oz"]`, volume → `≈ 3785.41`
  - `test_pk`: `["2 pk 64 fl oz"]` → `≈ 3785.41`
  - `test_mass_lb`: `["2 lb bag"]`, mass → `mass_g ≈ 907.18`
  - `test_count_dozen`: `["Large White Eggs, 1 dozen"]`, count → `count == 12`
  - `test_count_eggs`: `["18 eggs"]` → `18`
  - `test_paper_equivalence_ignored`: `["Charmin Ultra Strong Toilet Paper, 18 Mega Rolls = 72 Regular Rolls, 242 sheets per roll, 4 in x 3.92 in"]`, paper → `rolls == 18`, `sheets == 4356`, `area_m2 ≈ 44.07`
  - `test_paper_rolls_only`: `["Scott 1000 Toilet Paper, 12 Rolls"]` → `{"rolls": 12}`, with no sheets or area
  - `test_first_parseable_text_wins`: `["", "64 fl oz", "1 gal"]` → `volume_ml ≈ 1892.71` (the empty first text is skipped, and the second text wins over the third)
  - `test_unparseable_returns_empty`: `["Organic Milk"]`, volume → `{}`
- [ ] **Step 2: Run** `uv run pytest tests/normalize/test_quantity.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `parse_measures` with compiled regexes per unit family and a single `pint.UnitRegistry` at module level.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: canonical quantity parsing`

---

### Task 3: Attribute extraction (roadmap 1.3)

**Files:**
- Create: `normalize/extract.py`
- Test: `tests/normalize/test_extract.py`

**Interfaces:**
- Consumes: `AttributeDef`, `CategoryDef` (Task 1); `RawOffer`, `OffProduct`, `AttrValue` (Task 1).
- Produces: `extract_attributes(raw: RawOffer, category: CategoryDef, off: OffProduct | None) -> tuple[dict[str, AttrValue], list[str]]`. It returns the attributes found and the unresolved entries (`"attribute:<name>"` or `"attribute:<name>:ambiguous"`).

Order per attribute (first hit wins; later steps aren't consulted):
1. `raw.fields[name]`, if present and valid for the type and values → `source="api"`
2. `off_label in off.labels` (bool attributes only) → `True`, `source="off"`
3. `patterns`, run over `title + " " + size_text`: collect all distinct captured values. Exactly one → that value; more than one → ambiguous.
4. `synonyms`: whole-phrase, case-insensitive matches (`\b…\b`), collecting all distinct values. Exactly one → that value; more than one → ambiguous.
5. Nothing found: a bool with `absent` not `None` → `absent`, `source="rule"`; otherwise unresolved.

Steps 3–4 give `source="rule"`. Values are cast to the attribute's type, and a value not in `values` (when `values` is declared) is ignored.

- [ ] **Step 1: Write failing tests** with an inline milk `CategoryDef` built in a fixture. It has `fat` (enum `[skim, "1", "2", whole]`, pattern `(?<![\d.])([12])\s?%`, synonyms `{"skim": ["skim", "fat free", "nonfat"], "1": ["low fat", "lowfat"], "2": ["reduced fat"], "whole": ["whole milk", "vitamin d milk"]}`; bare "whole" would match "Whole Foods") and `organic` (bool, synonyms `{true: ["organic", "o organics"]}`, `off_label: "en:organic"`, `absent: false`):
  - `test_pattern_beats_synonym`: "Market Basket 100% Lactose Free Lowfat 2% Milk" → fat `"2"` (the pattern hit stops before synonyms; "100%" is not captured)
  - `test_a2_brand_is_not_two_percent`: "a2 Milk Whole Milk" → fat `"whole"`
  - `test_synonym_reduced_fat`: "O Organics Milk, Reduced Fat" → fat `"2"`, organic `True`
  - `test_ambiguous_pattern`: "Variety pack 1% and 2% milk" → no fat, `"attribute:fat:ambiguous"` in unresolved
  - `test_ambiguous_synonym`: "Whole Milk & Skim Milk Duo" → `"attribute:fat:ambiguous"`
  - `test_whole_foods_is_not_whole_milk`: "365 by Whole Foods Market Organic Reduced Fat Milk" → fat `"2"`, not ambiguous
  - `test_api_field_wins`: `fields={"fat": "1"}` with "2%" in the title → fat `"1"`, `source == "api"`
  - `test_off_label`: no "organic" in the title, `off.labels={"en:organic"}` → organic `True`, `source == "off"`
  - `test_absent_default`: "Garelick Farms Milk, Reduced Fat" → organic `False`, `source == "rule"`
  - `test_unknown_reported`: "Milk 64 fl oz" → no fat, `"attribute:fat"` in unresolved
- [ ] **Step 2: Run** `uv run pytest tests/normalize/test_extract.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `extract_attributes`.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: deterministic attribute extraction`

---

### Task 4: Category matching and brand tier (roadmap 1.4)

**Files:**
- Create: `normalize/match.py`, `data/store_brands.yaml`
- Test: `tests/normalize/test_match.py`

**Interfaces:**
- Consumes: `CategoryDef`, `RawOffer` (Task 1).
- Produces:
  - `match_category(raw: RawOffer, categories: Mapping[str, CategoryDef]) -> str | None`. A category is a candidate when any `include` phrase matches the title as a whole phrase (case-insensitive) and no `exclude` phrase does. With multiple candidates, the longest matched include phrase wins; a tie → `None`.
  - `load_store_brands(path: Path) -> frozenset[str]`, lowercased.
  - `brand_tier(raw: RawOffer, store_brands: frozenset[str]) -> AttrValue | None`. When `raw.brand` is set, it gives `store_brand` if it's in the list, else `name_brand`. Without a brand, a title that starts with a store brand gives `store_brand`; otherwise `None` (unknown). `source="rule"`.

`data/store_brands.yaml` starts with: Kirkland Signature, Great Value, Equate, Market Pantry, Good & Gather, up&up, Amazon Basics, Happy Belly, Presto!, 365 by Whole Foods Market, 365, Member's Mark, Nature's Promise, Stop & Shop, O Organics, Signature Select, Lucerne, Market Basket, Food Club.

- [ ] **Step 1: Write failing tests**, using inline categories: milk (include `["milk"]`, exclude `["almond", "oat", "soy", "coconut", "chocolate", "condensed", "evaporated", "powdered", "milk chocolate"]`), plant milk (include `["almond milk", "oat milk"]`), toilet paper (include `["toilet paper", "bath tissue"]`, exclude `["wipes"]`):
  - `test_matches_milk`: "Garelick Farms Milk, Reduced Fat" → `"dairy/milk"`
  - `test_excluded_flavored`: "Horizon Organic Chocolate Milk" → `None`
  - `test_longest_include_wins`: with milk's exclude list emptied for this test, "Silk Almond Milk" → plant milk
  - `test_excludes_wipes`: "Cottonelle Flushable Wipes, toilet paper companion" → `None`
  - `test_brand_tier_from_brand_field`: `brand="Kirkland Signature"` → `store_brand`; `brand="Charmin"` → `name_brand`
  - `test_brand_tier_from_title_prefix`: no brand, "365 Organic 2% Milk, 32 fl oz" → `store_brand`
  - `test_brand_tier_unknown`: no brand, "Organic 2% Milk, 64 fl oz" → `None`
- [ ] **Step 2: Run** `uv run pytest tests/normalize/test_match.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `match.py` and `data/store_brands.yaml`.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: category matching and brand tier`

---

### Task 5: Comparison methods and pipeline (roadmap 1.5)

**Files:**
- Create: `normalize/compare.py`, `normalize/pipeline.py`; update `normalize/__init__.py` exports
- Test: `tests/normalize/test_compare.py`, `tests/normalize/test_pipeline.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces:
  - `@dataclass(frozen=True) Comparison(name: str, unit_label: str, denominator: Callable[[Measures, Mapping[str, AttrValue]], float | None])`
  - `COMPARISONS: dict[str, Comparison]`, whose keys equal `KNOWN_COMPARISONS`:
    - `per_volume`: `volume_ml`, label `"mL"`
    - `per_mass`: `mass_g`, `"g"`
    - `per_count`: `count`, `"each"`
    - `area_x_ply`: `area_m2 × ply`, `"m²·ply"`
    - `per_100_sheets`: `sheets / 100`, `"100 sheets"`
    - `per_roll`: `rolls`, `"roll"`

    The denominator is `None` when a needed measure or attribute is missing, or when it's ≤ 0.
  - `unit_prices(price: Decimal, category: CategoryDef, measures: Measures, attributes: Mapping[str, AttrValue]) -> dict[str, Decimal]`: one entry per method in `[default, *alternates]` whose denominator isn't `None`, equal to `(price / Decimal(str(d))).quantize(Decimal("0.000001"))`.
  - `normalize(raw: RawOffer, categories: Mapping[str, CategoryDef], store_brands: frozenset[str], off: OffProduct | None = None) -> NormalizedOffer`. The steps:
    1. `match_category`; on `None`, return early with `unresolved=("category",)`.
    2. `extract_attributes`.
    3. Add `brand_tier`, or `"attribute:brand_tier"` to unresolved.
    4. `parse_measures([raw.size_text or "", raw.title, off.quantity if off else ""], category.dimension)`; if it's empty, add `"quantity"`.
    5. `unit_prices`; if the default method has no price, add `"unit_price:<default>"`.

- [ ] **Step 1: Write failing tests:**
  - `test_compare_keys_match_known`: `set(COMPARISONS) == KNOWN_COMPARISONS`
  - `test_per_volume_price`: price `Decimal("5.99")`, `volume_ml=1892.705` → `per_volume == Decimal("0.003165")`
  - `test_area_x_ply_needs_ply`: area present but no ply → `area_x_ply` absent and `per_roll` present
  - `test_zero_denominator_skipped`: `volume_ml=0` → `{}`
  - `test_pipeline_milk_end_to_end`: "Lactaid Reduced Fat 2% Milk, 2.8 L" at $6.58 with a milk category → `category == "dairy/milk"`, fat `"2"`, `measures["volume_ml"] == 2800`, `unit_prices["per_volume"] == Decimal("0.002350")`, `brand_tier` `name_brand` (brand="Lactaid")
  - `test_pipeline_unmatched`: "Garden Hose 50 ft" → `category is None`, `unresolved == ("category",)`
  - `test_pipeline_no_quantity`: "Organic Valley 2% Milk" → `"quantity"` and `"unit_price:per_volume"` in unresolved, and `unit_prices == {}`
- [ ] **Step 2: Run** `uv run pytest tests/normalize/test_compare.py tests/normalize/test_pipeline.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `compare.py` and `pipeline.py`, and export `normalize`, `load_categories` and `load_store_brands` from `normalize/__init__.py`.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: comparison methods and normalize pipeline`

---

### Task 6: Fixture harness and first categories: milk, eggs, coffee (roadmap 1.6)

**Files:**
- Create: `categories/dairy/milk.yaml`, `categories/dairy/eggs.yaml`, `categories/pantry/coffee.yaml`
- Create: `tests/test_category_fixtures.py`, `tests/categories/dairy/milk.yaml`, `tests/categories/dairy/eggs.yaml`, `tests/categories/pantry/coffee.yaml`

**Interfaces:**
- Consumes: `normalize`, `load_categories`, `load_store_brands` (Task 5).
- Produces: the fixture-file format that every future category uses:

```yaml
- title: "Lactaid Reduced Fat 2% Milk, 2.8 L"
  brand: Lactaid            # optional; also size_text, fields, off_labels, off_quantity
  price: "6.58"
  expect:
    category: dairy/milk
    attributes: {fat: "2", organic: false, brand_tier: name_brand}   # subset match
    measures: {volume_ml: 2800}                                      # approx, rel=1e-3
    unit_prices: {per_volume: "0.002350"}                            # optional
    unresolved: []                                                   # exact, when given
```

Category content:
- **milk:**
  - `dimension: volume`
  - include `["milk"]`, with the exclude list from Task 4
  - attributes:
    - `fat` (as in Task 3)
    - `organic` (bool, synonyms `["organic", "o organics"]`, `off_label: en:organic`, `absent: false`)
    - `lactose_free` (bool, synonyms `["lactose free", "lactose-free", "lactaid"]`, `absent: false`)
    - `ultra_filtered` (bool, synonyms `["ultra-filtered", "ultra filtered", "fairlife"]`, `absent: false`)
  - comparison `per_volume`
- **eggs:**
  - `dimension: count`
  - include `["eggs"]`, exclude `["egg whites", "liquid egg", "egg noodles", "chocolate", "easter"]`
  - attributes:
    - `size` (enum `[medium, large, extra_large, jumbo]`; synonyms; "extra large"/"xl" → `extra_large`)
    - `housing` (enum `[caged, cage_free, free_range, pasture_raised]`)
    - `organic` (as milk)
  - comparison `per_count`
- **coffee:**
  - `dimension: mass`
  - include `["coffee"]`, exclude `["k-cup", "k cup", "pods", "instant", "creamer", "cold brew", "coffee filter", "coffee maker"]`
  - attributes:
    - `form` (enum `[whole_bean, ground]`)
    - `decaf` (bool, synonyms `["decaf", "decaffeinated"]`, `absent: false`)
    - `organic`
  - comparison `per_mass`

- [ ] **Step 1: Write `tests/test_category_fixtures.py`.**
  - It parametrizes over every case in `tests/categories/**/*.yaml`, with ID `<slug>:<index>`.
  - It builds a `RawOffer` with defaults `retailer="fixture"`, `retailer_item_id=str(index)`, `currency="USD"`, `url="https://example.invalid"` and a fixed `fetched_at`, then checks `expect` as described.
  - Add `test_every_category_has_fixtures`: every slug from `load_categories(Path("categories"))` has a fixture file with at least 8 cases.
- [ ] **Step 2: Run** `uv run pytest tests/test_category_fixtures.py -v`. Expected: FAIL (no categories or fixtures yet; the coverage test fails once the category files exist).
- [ ] **Step 3: Write the three category files and at least 8 fixtures each.** Milk fixtures must include these exact titles, with their expectations:
  - "Organic Valley® Grassmilk® Reduced Fat Organic 2% Milk" → fat 2, organic true, `"quantity"` unresolved
  - "Lactaid Reduced Fat 2% Milk, 2.8 L" → fat 2, lactose_free true, 2800 mL
  - "a2 Milk Whole Milk" → fat whole
  - "O Organics Milk, Reduced Fat, 2% Milkfat" → fat 2, organic true, brand_tier store_brand
  - "365 by Whole Foods Market, Organic Reduced Fat 2% Milk, 32 FZ" → 946.35 mL, store_brand
  - "Market Basket 100% Lactose Free Lowfat 2% Milk" → fat 2, lactose_free true
  - "fairlife 2% Reduced-Fat Ultra-Filtered 52oz Milk Bottle" → fat 2, ultra_filtered true, 1537.82 mL
  - "Horizon Organic Chocolate Milk, 8 fl oz" → `category: null`
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS. If a real title fails, fix the category YAML (synonyms or patterns) rather than the test expectation, unless the expectation itself is wrong.
- [ ] **Step 5: Commit** `feat: milk, eggs, coffee categories with fixtures`

---

### Task 7: Paper-goods categories: toilet paper, paper towels (roadmap 1.7)

**Files:**
- Create: `categories/household/toilet-paper.yaml`, `categories/household/paper-towels.yaml`
- Create: `tests/categories/household/toilet-paper.yaml`, `tests/categories/household/paper-towels.yaml`

**Interfaces:**
- Consumes: the fixture format and harness (Task 6); `paper` measures (Task 2).

Category content:
- **toilet paper:** exactly the example in spec §6. `dimension: paper`; `ply` (int `[1, 2, 3]`, pattern `(?<![\d.])([123])[- ]?ply`, synonyms `{1: ["single ply"], 2: ["double ply"]}`); default `area_x_ply`, alternates `[per_100_sheets, per_roll]`.
- **paper towels:**
  - include `["paper towel", "paper towels"]`, exclude `["holder", "dispenser"]`
  - `ply` as above
  - `select_a_size` (bool, synonyms `["select-a-size", "select a size", "pick-a-size", "choose-a-sheet"]`, `absent: false`)
  - default `area_x_ply`, alternates `[per_100_sheets, per_roll]`

- [ ] **Step 1: Write at least 8 fixtures per category.** Toilet-paper fixtures must include:
  - "Charmin Ultra Strong Toilet Paper, 18 Mega Rolls = 72 Regular Rolls, 242 sheets per roll, 4 in x 3.92 in", price 24.99. Expect `rolls: 18`, `sheets: 4356`, `area_m2 ≈ 44.07`, ply unresolved, and `per_roll` and `per_100_sheets` present but `area_x_ply` absent.
  - The same title with "2-Ply" added → `area_x_ply` present.
  - "Scott 1000 Toilet Paper, 12 Rolls, 1-Ply" → ply 1, `rolls: 12`, only `per_roll` present, and `"unit_price:area_x_ply"` unresolved.
  - "Kirkland Signature Bath Tissue, 2-Ply, 380 Sheets, 30 Rolls" → store_brand, `sheets: 11400`.
- [ ] **Step 2: Run** `uv run pytest tests/test_category_fixtures.py -v`. Expected: FAIL (`test_every_category_has_fixtures` fails until both fixture files exist, and the paper parsing gaps surface).
- [ ] **Step 3: Write both category files.** Adjust Task 2's paper parsing if real titles expose formats it misses, such as "380 Sheets" with no "per roll", read as sheets per roll when `rolls` is present. Add a matching `tests/normalize/test_quantity.py` case for every parser change.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: PASS, with 5 categories and ≥ 8 fixtures each.
- [ ] **Step 5: Commit** `feat: toilet paper and paper towel categories with fixtures`

---

## M1 exit criteria

- `uv run pytest` passes, with every Review Focus item covered by a named test.
- `normalize/` imports nothing from Django, makes no network calls and contains no AI libraries.
- Adding a new category takes only a YAML file plus a fixtures file. Contributors document this in roadmap task 0.5's CONTRIBUTING.
