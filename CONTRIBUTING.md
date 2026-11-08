# Contributing to UnitPricer

Thanks for helping. The most useful contribution is a new or improved **category**; the guide
for that is below. Read the [README](README.md) first for setup.

## Ground rules

- **No AI or LLM code or dependencies** in the product or the ingestion pipeline. Extraction is
  deterministic: API fields, Open Food Facts labels, regex patterns and synonyms.
- **Never guess.** If an attribute or quantity can't be determined from the data, leave it
  unresolved. Do not add rules that fill in a plausible value, and never assert a guessed value in a fixture.
- **Honest ranking.** Ranking depends only on unit price and the user's filters. Nothing about
  affiliate commission may influence order or which products are shown.
- **uv only** for Python. Use `uv add`, `uv sync` and `uv run`; do not use the plain `pip` installer or `python -m venv`.
- **Secrets only in `.env`** (gitignored). Add new variable names, without values, to
  `.env.example`. Never commit a key, and never paste keys or tokens into issues or PRs.

## Development workflow

1. **Open or pick a GitHub issue first**, for bugs and planned changes alike.
2. Work on a branch named `issue-<n>-<short-slug>` (for example `issue-42-butter-category`).
3. **Add or update tests with every change**, and keep `uv run pytest` passing.
4. Install the pre-commit hooks once: `uv run pre-commit install`. They run
   [gitleaks](https://github.com/gitleaks/gitleaks) (it must be on your PATH) and the test suite on each commit.
5. Open a pull request that closes the issue (`Closes #<n>`).

Tests need PostgreSQL; see the README. Run one file with `uv run pytest tests/test_category_fixtures.py`.

## Adding a category

A category is two files that must be added together:

- `categories/<group>/<slug>.yaml`: the definition. The category's slug is its path without
  the extension, for example `dairy/butter`.
- `tests/categories/<group>/<slug>.yaml`: fixtures with real product titles. A category without
  at least 8 fixtures fails the test suite.

### 1. The category file

```yaml
name: Butter
parent: dairy
dimension: mass
match:
  include: ["butter"]
  exclude: [peanut, almond, cashew, "butter beans", buttermilk, popcorn, cookies]
attributes:
  salt:
    type: enum
    values: [salted, unsalted]
    synonyms:
      unsalted: [unsalted, sweet cream]
      salted: [salted]
  organic:
    type: bool
    synonyms: {true: [organic]}
    off_label: en:organic
    absent: false
comparison:
  default: per_mass
search: ["salted butter", "unsalted butter"]
```

Unknown keys are errors, and the loader (`normalize/categories.py`) reports the file and the
problem. Every key:

| Key | Meaning |
|---|---|
| `name` | Required. Display name. |
| `parent` | Optional string used for grouping, for example `household/paper-goods`. |
| `dimension` | Required. What the quantity parser extracts: `volume` (ml), `mass` (g), `count` (items) or `paper` (rolls, sheets, area). |
| `match.include` | Phrases that put an offer in this category. Matched as whole words against title and brand, ignoring case and accents. The longest matching phrase wins across categories, and a tie matches nothing. |
| `match.exclude` | Phrases that veto the category when present (look-alikes such as "peanut butter"). Matched exactly as written. |
| `attributes` | Map of attribute name to definition (below). `brand_tier` (`store_brand` or `name_brand`) is built in for every category. |
| `comparison.default` | Required. The default comparison unit. |
| `comparison.alternates` | Optional list of other units the user can switch to. |
| `search` | Optional retailer search queries ingestion runs for the category. Defaults to `name`. |

Valid comparison names: `per_volume`, `per_mass`, `per_count`, `area_x_ply`, `per_100_sheets`,
`per_roll`. Pick ones that make sense for the `dimension`.

Each attribute takes these keys:

| Key | Meaning |
|---|---|
| `type` | Required: `int`, `enum` or `bool`. |
| `values` | The allowed values. Required for `enum`; an optional list of integers for `int`; unused for `bool`. Enum values are strings, so quote numeric ones (`"2"`). |
| `patterns` | Regexes, case-insensitive, each with **exactly one capture group**. The captured text is the value. Patterns win over synonyms. |
| `synonyms` | Map of value to a list of phrases that imply it. Keys must be declared `values` (for `bool`, `true` or `false`). |
| `off_label` | `bool` only. An Open Food Facts label (for example `en:organic`) that sets the attribute to true. |
| `absent` | `bool` only. The value to use when nothing else resolves it (for example `organic` is `false` when the title doesn't say organic). Leave it out when absence proves nothing, and the attribute stays unresolved. |

Values are looked up in this order: structured retailer API fields, Open Food Facts labels,
patterns, synonyms, then `absent`. Anything still unknown is reported as unresolved, never guessed.

### 2. The fixtures file

A list of cases. Use **at least 8 real titles** copied from retailer listings, including awkward
ones (multipacks, missing sizes, look-alikes that must not match). The two cases below show the
format; a real file needs the full set.

```yaml
- title: "Land O Lakes Unsalted Butter, 16 oz"
  brand: Land O Lakes          # optional; drives brand_tier
  price: "5.48"                # optional, default "1.00"
  expect:
    category: dairy/butter
    attributes: {salt: unsalted, organic: false, brand_tier: name_brand}
    measures: {mass_g: 453.59}
    unit_prices: {per_mass: "0.012081"}
    unit_prices_exact: true
    unresolved: []
- title: "Peanut Butter, Creamy, 16 oz"
  expect:
    category: null             # correctly excluded
```

Case keys: `title` (required), `price`, `brand`, `size_text`, `fields` (structured API fields, as a
map of attribute name to value), `off_labels` and `off_quantity` (Open Food Facts data), and
`expect`. Keys under `expect`:

- `category`: the slug, or `null` when the offer must not match any category.
- `attributes`: a subset of attributes to check; each named one must exist and equal the value.
  Enum values are strings, `int` attributes are integers, `bool` attributes are `true`/`false`.
- `measures`: expected measures (`volume_ml`, `mass_g`, `count`, `rolls`, `sheets`, `area_m2`),
  compared approximately (0.1%).
- `unit_prices`: expected unit prices as quoted decimal strings, compared exactly.
- `unit_prices_exact: true`: also require that **no other** unit prices were computed.
- `unresolved`: the exact list of things the normalizer could not resolve, such as `quantity`,
  `attribute:<name>`, `attribute:brand_tier` or `unit_price:<comparison>`. Order doesn't matter,
  but the set must match exactly. Use `[]` to assert that everything resolved.

A case that omits `unresolved` or `unit_prices_exact` doesn't check those, so use them on the
cases where they are the point. `tests/categories/household/toilet-paper.yaml` has good models.

### 3. Run the tests

```bash
uv run pytest tests/test_category_fixtures.py
uv run pytest        # then the whole suite
```

The suite checks that every fixture file matches a category, every category has a fixtures file,
and each has at least 8 cases. Once merged, `sync_categories` loads the category into the
database (`uv run --env-file .env python manage.py sync_categories`); the YAML files are the
source of truth.

### Tips

- **Synonyms vs. patterns.** Use synonyms for fixed phrases ("lactose free", "cage-free"). Use
  patterns when the value is a number or a word needs context, such as `([12])\s?%` for fat, or a
  lookbehind so "large" doesn't fire inside "extra large" (see `categories/dairy/eggs.yaml`).
- **Keep look-alikes out with `exclude`.** Plant milks, "milk chocolate", "peanut butter",
  "egg noodles": add the word to `exclude`, and add a `category: null` fixture proving it.
- **Never assert guessed values.** If the title doesn't say, list the attribute in `unresolved`
  instead of picking a value. If the honest result is unresolved, that is the correct expectation.
- Run the normalizer on a title to get the unit price, then sanity-check the number by hand
  before putting it in a fixture.

## Store brands

`data/store_brands.yaml` is a flat list of store-brand names (for example `Kirkland Signature`,
`Great Value`). An offer is a `store_brand` when its brand field matches an entry, or, when no brand
is given, when its title starts with one. Matching ignores case and accents. To add one, append
the name on its own line (`- Good & Gather`) and add or update a fixture that expects
`brand_tier: store_brand` for it.

## Code layout

| Path | What lives there |
|---|---|
| `normalize/` | Pure, no-I/O normalizer: category loader and validation (`categories.py`), matching and brand tier (`match.py`), quantity parsing (`quantity.py`), attribute extraction (`extract.py`), comparison methods (`compare.py`), and the `normalize()` pipeline (`pipeline.py`). |
| `categories/` | One YAML file per category; the slug is the path. |
| `tests/categories/` | Fixture files, one per category, run by `tests/test_category_fixtures.py`. |
| `connectors/` | Retailer connectors behind a common interface, plus a recorded-response harness so CI makes no live calls. |
| `enrichment/` | Open Food Facts client and caching. |
| `catalog/` | Django app: models, ingestion, scheduler, and management commands (`sync_categories`, `refresh`, `run_scheduler`, `demo_data`, `off_sync`, `off_backfill`). |
| `web/` | Django views, URLs, templates and static files (browse, search, disclosure, settings). |
| `config/` | Django settings and project URLs. |
| `data/` | Shared data such as `store_brands.yaml`. |
| `docs/` | The [roadmap](docs/roadmap.md), design spec, plans and research notes. |

## Licensing of contributions

UnitPricer is licensed under AGPL-3.0-or-later. By submitting a contribution you agree that it is
licensed under the same terms (inbound = outbound). You keep the copyright to your work; there is
no contributor license agreement to sign.
