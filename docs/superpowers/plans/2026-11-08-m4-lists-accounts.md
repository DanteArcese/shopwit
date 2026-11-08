# M4: Lists and Accounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shopping lists made of specs ("any 2% milk, 1 gal"), resolved to the cheapest fresh offers at the visitor's stores, with per-store totals, the cheapest single store, the cheapest split and a whole-package quantity fit, usable anonymously and synced through optional email accounts that also keep the visitor's settings.

**Architecture:** Two new Django apps. `lists/` holds the spec types, a storage layer with one interface over two backends (session JSON for anonymous visitors, `ShoppingList`/`ListItem` rows for accounts), pure package-fit and basket-optimizer modules, the resolver, and the list views. `accounts/` holds email-and-password and magic-link sign-in on stock `django.contrib.auth`, rate limiting, saved settings (`UserSettings`) and the session-to-account merge run on `user_logged_in`. `catalog/browse.py` gains a reusable "matching offers" query and a filter round-trip, and `catalog/units.py` gains target-quantity units. Resolution reuses M3's filter semantics exactly, so a list item matches what its category page shows.

**Tech Stack:** Django 5.2 (`django.contrib.auth`, sessions, `DatabaseCache`, `django.core.mail`), HTMX already vendored (not needed for lists), Postgres, pytest-django. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-07-unitpricer-design.md`: §3 Principles, §4 core items 5–6, §5.3 (`List`, `ListItem`, `Settings`), §7 Units, §8 Lists, §9 Accounts and settings, §10 terms. Roadmap tasks 4.1–4.6. Builds on M3 (`docs/superpowers/plans/2026-11-07-m3-browse-search.md`).

## Global Constraints

- Python with `uv` only. No AI/LLM libraries or calls. **No new dependencies**: no third-party auth providers, no `django-allauth`, no rate-limit package.
- **Never guess (Principle 3).** A list item resolves with exactly M3's filter rules: under an active filter, a product with an unknown value is excluded. An item that can't resolve is shown with its reason, never silently dropped or widened. A stored filter that is no longer valid for its category makes the item unresolved ("filter no longer valid: fat"); it is never ignored.
- **Honest ranking (Principle 2):** every choice (offer per store, single store, split) depends only on price, quantity and the user's spec and stores. Affiliate configuration never affects choice or order. Ties break deterministically, never by retailer commission.
- **Freshness:** only offers from `catalog.freshness.fresh_offers(now)` take part in resolution or totals. A stale offer is invisible even when it is the cheapest.
- **Whole packages only.** Quantity fit buys `n` whole packages, `n` an `int ≥ 1`, never fractional. `MAX_PACKAGES = 24`.
- **Money is `Decimal`** end to end. Totals are never summed across currencies: when the chosen offers span more than one currency, totals, single store and split are withheld with "Can't total: prices in more than one currency".
- **Terms (spec §5.4, §10):** every price on the list page shows "price as of <time>"; outbound links use M3's `{% outbound %}` tag (direct, render-time affiliate URLs); the affiliate disclosure banner shows on the list page; Amazon offers keep their disclaimer.
- **Browsing stays cookie-free.** Category and search GETs for a visitor without a session set no cookies, as in M3. Session and CSRF cookies start only once the visitor opens a form (add to list, lists, settings, sign in).
- **Accounts store only:** email (as `User.username` and `User.email`, lowercased), password hash (optional), `last_login`/`date_joined` (Django's), lists and settings. No names, IPs, user agents or tracking ids are stored. Rate-limit counters live in the cache under salted SHA-256 keys and expire.
- **Security:** every state change is POST with CSRF. Magic-link tokens: 32 bytes from `secrets.token_urlsafe`, stored only as SHA-256, expire after **15 minutes**, single use, consumed only by POST. No response reveals whether an email has an account. `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` are on when `DEBUG` is off; `django.contrib.auth.login` rotates the session key.
- **Email** goes through `django.core.mail` with settings from env; the console backend is the default when `DEBUG` is on. Credentials only in `.env`; names in `.env.example`. Never read `.env`.
- Tests never touch the network. Each task ends with `uv run pytest -q` green and the pre-commit hooks passing. Commit subjects end with the M4 implementation issue number, as M3's did (`(#32)`).

## Review Focus

1. **Float noise in exact package multiples.** A 128 fl oz target with 64 fl oz packages (stored as `1892.70592` mL floats, target quantized to 3 dp) needs exactly 2 packages, never 3; 1 gal of 1.89 L packages needs 3. Owned by Task 2 (`test_exact_multiple_not_rounded_up`).
2. **A stale offer must not leak into totals,** including when it is the cheapest one for an item, and a store whose only offers for an item are stale counts that item as missing. Owned by Task 4 (`test_stale_offer_excluded_from_totals`).
3. **Session → account merge is idempotent.** Signing in twice, or replaying the merge, creates no duplicate lists or items, and lists already in the account are untouched. Owned by Task 7 (`test_merge_idempotent`, `test_merge_keeps_existing_account_lists`).
4. **Magic links survive mail-scanner prefetch but nothing else.** A GET of the link (scanners, previews) does not consume it; the first POST does; a second POST, or one after 15 minutes, fails with the same "expired or already used" page. Owned by Task 6 (`test_get_does_not_consume`, `test_token_single_use`, `test_token_expires`).
5. **List ids are not capabilities.** Another account's or another session's list id returns 404 for every list route, GET and POST. Owned by Task 5 (`test_other_owners_list_404`).

---

## File Structure

```
config/settings.py, config/urls.py         # add "lists", "accounts"; email, cache, session, auth settings
.env.example, CLAUDE.md                     # email + site URL vars; createcachetable in first-run steps
catalog/
  browse.py                                 # + matching_offers(), spec_params(), parse_filters_strict()
  units.py                                  # + TARGET_UNITS, target_to_canonical(), default_target()
lists/
  __init__.py  apps.py  models.py (+ migrations)   # ShoppingList, ListItem
  spec.py                                   # ItemSpec, ListSpec, JSON round-trip, edit helpers
  store.py                                  # ListStore protocol, SessionListStore, AccountListStore, store_for()
  fit.py                                    # packages_needed(), Fit, fit_offer() — pure
  basket.py                                 # store_totals(), cheapest_single_store(), cheapest_split() — pure
  resolve.py                                # resolve_item(), resolve_list()
  views.py  urls.py
  templates/lists/  index.html  detail.html  add.html  _item.html
accounts/
  __init__.py  apps.py  models.py (+ migrations)   # MagicLinkToken, UserSettings
  emails.py                                 # normalize_email()
  ratelimit.py                              # allow()
  magic.py                                  # issue_token(), peek_token(), consume_token(), send_magic_link()
  merge.py                                  # merge_session_into_account(), signal receiver
  checks.py  views.py  urls.py
  templates/accounts/  sign_in.html  link_sent.html  magic_confirm.html  magic_invalid.html  account.html  magic_link_email.txt
web/
  prefs.py                                  # Prefs.comparisons; account-backed get_prefs/save_prefs
  views.py                                  # category default comparison; add-to-list link; settings comparisons
  templates/web/  base.html  _results.html  _add_link.html  settings.html
tests/lists/  test_filter_roundtrip.py  test_target_units.py  test_fit.py  test_basket.py  test_spec_store.py  test_resolve.py  test_views_lists.py
tests/accounts/  test_magic.py  test_views_auth.py  test_ratelimit.py  test_settings_security.py  test_merge.py  test_saved_settings.py
```

---

### Task 1: Filter round-trip, matching offers, target units (catalog groundwork for 4.1–4.2)

**Files:**
- Modify: `catalog/browse.py`, `catalog/units.py`
- Test: `tests/lists/__init__.py`, `tests/lists/conftest.py` (milk/tp/load fixtures as in `tests/web/test_browse.py`), `tests/lists/test_filter_roundtrip.py`, `tests/lists/test_target_units.py`

**Interfaces:**
- Consumes: `FilterSpec`, `parse_filters`, `browse`, `fresh_offers` (M3); `ML_PER_FL_OZ`, `G_PER_OZ`, `display_size` (M3 `catalog/units.py`).
- Produces (`catalog/browse.py`):
  - `matching_offers(category: Category, spec: FilterSpec, stores: frozenset[str] | None, now: datetime) -> list[Offer]`: steps 1–4 of `browse()` (fresh, stores, attribute and brand-tier containment, size range), `select_related("product")`. `browse()` is refactored to call it; its behaviour and tests are unchanged.
  - `spec_params(spec: FilterSpec) -> dict[str, str]`: the inverse of `parse_filters` on canonical params. Bools as `"true"`/`"false"`, ints and enums as `str`, `size_min`/`size_max` as `repr(float)`, `brand_tier` when set; unfiltered keys absent.
  - `parse_filters_strict(category: Category, params: Mapping[str, str]) -> tuple[FilterSpec, tuple[str, ...]]`: like `parse_filters`, but also returns the sorted keys that were present and not `"any"` yet did not parse: unknown attribute names, invalid values, a bad `brand_tier`, a non-numeric size. Other keys are not expected in stored filters and also count as invalid.
- Produces (`catalog/units.py`):
  - `TARGET_UNITS: dict[str, dict[str, tuple[str, str, Decimal]]]`, dimension → unit code → `(label, measure_key, canonical_per_unit)`:
    - volume: `floz` ("fl oz", `volume_ml`, `ML_PER_FL_OZ`), `gal` ("gal", `volume_ml`, `ML_PER_FL_OZ * 128`), `ml` ("mL", `volume_ml`, 1), `l` ("L", `volume_ml`, 1000)
    - mass: `oz` ("oz", `mass_g`, `G_PER_OZ`), `lb` ("lb", `mass_g`, `G_PER_OZ * 16`), `g` ("g", `mass_g`, 1), `kg` ("kg", `mass_g`, 1000)
    - count: `ct` ("ct", `count`, 1)
    - paper: `rolls` ("rolls", `rolls`, 1), `sheets` ("sheets", `sheets`, 1)
  - `target_units_for(dimension: str, unit_system: str) -> list[str]`: imperial volume `["floz", "gal"]`, metric `["l", "ml"]`; imperial mass `["oz", "lb"]`, metric `["g", "kg"]`; count and paper the same in both systems.
  - `target_to_canonical(amount: Decimal, unit_code: str, dimension: str) -> tuple[str, Decimal]`: returns `(measure_key, amount × factor quantized to 0.001)`. Raises `ValueError` for a unit not in `TARGET_UNITS[dimension]`, a non-finite amount, `amount <= 0`, or a canonical amount above `Decimal("1000000")`.
  - `default_target(measures: Mapping[str, float], dimension: str, unit_system: str) -> tuple[Decimal, str] | None`: one package of the given offer in the viewer's first unit for the dimension (paper: rolls if known, else sheets), rounded to 2 dp; `None` when the measure is missing. Used to pre-fill the add form.
  - `display_target(measure_key: str, amount: Decimal, dimension: str, unit_system: str) -> str`: delegates to `display_size({measure_key: float(amount)}, …)`.

- [ ] **Step 1: Write failing tests**
  - `test_spec_params_round_trip`: for milk `{"fat": "2", "organic": "true", "brand_tier": "store_brand", "size_min": "946.353"}`, `parse_filters(milk, spec_params(parse_filters(milk, p))) == parse_filters(milk, p)`; same for toilet paper `{"ply": "2"}` with `spec_params(...) == {"ply": "2"}`.
  - `test_strict_reports_invalid`: `parse_filters_strict(milk, {"fat": "banana", "colour": "red", "organic": "true"})` → spec has only `organic=True`, invalid `("colour", "fat")`.
  - `test_strict_any_is_not_invalid`: `{"fat": "any"}` → no filters, no invalid keys.
  - `test_matching_offers_equals_browse_population`: with the M3 milk fixture rows, the product ids from `matching_offers` equal those in `browse().ranked + unranked` for the same spec and stores.
  - `test_target_to_canonical`: `(Decimal("128"), "floz", "volume")` → `("volume_ml", Decimal("3785.412"))`; `(1, "gal")` → same; `(Decimal("2"), "l")` → `("volume_ml", Decimal("2000.000"))`; `(12, "ct", "count")` → `("count", Decimal("12.000"))`; `(4, "rolls", "paper")` → `("rolls", …)`.
  - `test_target_rejects`: unit `"floz"` for dimension `"mass"`, amount `0`, `-1`, `NaN`, `Infinity` and `Decimal("1e9")` each raise `ValueError`.
  - `test_default_target`: `{"volume_ml": 1892.70592}` imperial → `(Decimal("64.00"), "floz")`; metric → `(Decimal("1.89"), "l")`; missing measure → `None`.
- [ ] **Step 2: Run** `uv run pytest tests/lists -v`. Expected: FAIL (`ImportError`).
- [ ] **Step 3: Implement** the functions above; `browse()` now calls `matching_offers()`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS, including every M3 browse test.
- [ ] **Step 5: Commit** `feat: filter round-trip, matching offers and target units`

---

### Task 2: Package fit and basket optimizer (pure logic for 4.3)

**Files:**
- Create: `lists/__init__.py`, `lists/fit.py`, `lists/basket.py`
- Test: `tests/lists/test_fit.py`, `tests/lists/test_basket.py`

`lists/` becomes a Django app in Task 3; these two modules import nothing from Django.

**Interfaces:**
- Produces (`lists/fit.py`):
  - `MAX_PACKAGES = 24`; `FIT_TOLERANCE = Decimal("0.000001")` (relative).
  - `packages_needed(target: Decimal, size: Decimal) -> int | None`: the smallest whole `n ≥ 1` with `n × size ≥ target × (1 − FIT_TOLERANCE)`; `None` when `size <= 0` or `n > MAX_PACKAGES`.
  - `@dataclass(frozen=True) Fit(offer: Any, packages: int, package_size: Decimal, total_price: Decimal, total_amount: Decimal, surplus: Decimal)`. `offer` is duck-typed: `.price: Decimal`, `.currency`, `.measures: Mapping[str, float]`, `.retailer`, `.title`, `.retailer_item_id`, `.last_seen_at`.
  - `fit_offer(offer, measure_key: str, target: Decimal) -> Fit | None`: `None` when `offer.measures` lacks `measure_key` or `packages_needed` is `None`. `package_size = Decimal(str(measures[key]))`, `total_price = price × packages`, `surplus = max(0, total_amount − target)`.
  - `fit_key(fit: Fit) -> tuple`: `(total_price, surplus, packages, offer.title, offer.retailer, offer.retailer_item_id)`. The best fit is the `min` by this key.
- Produces (`lists/basket.py`), all keyed by string item ids and retailer codes, costs as `Decimal`:
  - `@dataclass(frozen=True) StoreTotal(store: str, total: Decimal, covered: tuple[str, ...], missing: tuple[str, ...])`: `total` sums the covered items only.
  - `@dataclass(frozen=True) Split(total: Decimal, stores: tuple[str, ...], assignments: Mapping[str, str])`: item id → store.
  - `store_totals(costs: Mapping[str, Mapping[str, Decimal]], stores: Sequence[str]) -> list[StoreTotal]`: one per store in `stores` order; `costs[item][store]` is that store's best fit price.
  - `cheapest_single_store(totals: Sequence[StoreTotal]) -> StoreTotal | None`: minimum `(total, store)` among stores with no missing items; `None` when none covers everything.
  - `cheapest_split(costs, stores: Sequence[str], max_stores: int | None) -> Split | None`: the cheapest assignment of every item in `costs` to a store, using at most `max_stores` distinct stores (`None` = unlimited). Tie order: lower total, then fewer stores, then the sorted store tuple. `None` when no allowed store set covers every item.

  Algorithm (not determined by the tests alone):
  ```
  if max_stores is None or max_stores >= len(stores):
      assign each item to min((cost, store)) over its stores        # per-item minimum is optimal
  else:
      for k in 1..max_stores, for subset in itertools.combinations(sorted(stores), k):
          skip unless every item has a cost at some store in subset
          assign each item to min((cost, store)) within subset; total = sum
      keep min by (total, len(used stores), tuple(sorted(used stores)))
  stores in Split = the stores actually used
  ```

- [ ] **Step 1: Write failing tests**
  - `test_packages_never_fractional`: for targets `1..500` mL and sizes `{1, 3, 7.5, 64, 473.176}`, `packages_needed` returns an `int`, `n × size ≥ target × (1 − tol)` and `(n − 1) × size < target × (1 − tol)`.
  - `test_exact_multiple_not_rounded_up` (Review Focus 1): `packages_needed(Decimal("3785.412"), Decimal(str(1892.70592))) == 2`; `packages_needed(Decimal("3785.412"), Decimal("1890")) == 3`; `packages_needed(Decimal("12.000"), Decimal("12")) == 1`.
  - `test_too_many_packages_is_none`: target 25, size 1 → `None`; size 0 → `None`.
  - `test_fit_prefers_one_128_over_two_64`: offers 64 fl oz at $3.00 and 128 fl oz at $5.48, target 128 fl oz: best is `packages=1`, `total_price=Decimal("5.48")`; the other fit is `packages=2`, `total_price=Decimal("6.00")`.
  - `test_fit_tie_prefers_less_surplus`: equal total price, the fit with less surplus wins.
  - `test_fit_missing_measure_is_none`.
  - `test_store_totals_and_missing`: items `a`, `b`; store `x` has both, `y` only `a` → `x` total = a+b, `y` total = a, `y.missing == ("b",)`.
  - `test_single_store_requires_full_coverage`, `test_single_store_none_when_no_store_covers`.
  - `test_split_unlimited_is_per_item_min`.
  - `test_split_max_stores_two`: three items cheapest at three different stores; with `max_stores=2` the result uses 2 stores and equals the hand-computed optimum.
  - `test_split_matches_brute_force`: seeded `random.Random(4)`, 200 cases of 1–5 items × 1–4 stores with random costs and random gaps; compare `cheapest_split(…, k)` for `k ∈ {1, 2, None}` against enumerating every item→store assignment (`itertools.product`) filtered to ≤ k distinct stores. Totals equal, and both are `None` together.
  - `test_split_tie_prefers_fewer_stores`.
- [ ] **Step 2: Run** `uv run pytest tests/lists/test_fit.py tests/lists/test_basket.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with `Decimal` only.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: whole-package quantity fit and split-store optimizer`

---

### Task 3: List specs, models and storage (roadmap 4.1, 4.4)

**Files:**
- Create: `lists/apps.py`, `lists/models.py`, `lists/migrations/0001_initial.py`, `lists/spec.py`, `lists/store.py`
- Modify: `config/settings.py` (`"lists"` in `INSTALLED_APPS`; `SESSION_COOKIE_AGE = 60 * 60 * 24 * 60`)
- Test: `tests/lists/test_spec_store.py`

**Interfaces:**
- Consumes: `spec_params`, `target_to_canonical` (Task 1) to build items in tests.
- Produces (`lists/spec.py`):
  - `MAX_LISTS = 20`, `MAX_ITEMS = 100`, `MAX_NAME = 80`, `DEFAULT_NAME = "My list"`.
  - `@dataclass(frozen=True) ItemSpec(id: str, category: str, filters: Mapping[str, str], target_measure: str, target_amount: Decimal, pinned_product: int | None = None)`: `id` is `uuid4().hex`; `category` is the slug; `filters` are canonical params from `spec_params`.
  - `@dataclass(frozen=True) ListSpec(id: str, name: str, items: tuple[ItemSpec, ...], updated_at: datetime)`.
  - `new_id() -> str`; `new_list(name: str, now: datetime) -> ListSpec` (blank name → `DEFAULT_NAME`, trimmed to `MAX_NAME`).
  - `with_item(spec, item, now) -> ListSpec` (raises `ListLimitError` at `MAX_ITEMS`), `without_item(spec, item_id, now)`, `replace_item(spec, item, now)`, `renamed(spec, name, now)`.
  - `to_json(spec: ListSpec) -> dict` / `from_json(data: object) -> ListSpec | None`: Decimals as strings, datetimes as ISO 8601. `from_json` returns `None` for malformed data and drops malformed items rather than raising.
  - `class ListLimitError(Exception)`.
- Produces (`lists/models.py`):
  - `ShoppingList(TimestampedModel)`: `user` FK `settings.AUTH_USER_MODEL` `on_delete=CASCADE` `related_name="shopping_lists"`, `uid` `CharField(32)`, `name` `CharField(80)`. `UniqueConstraint(fields=["user", "uid"], name="uniq_shoppinglist_user_uid")`.
  - `ListItem(TimestampedModel)`: `shopping_list` FK `CASCADE` `related_name="items"`, `uid` `CharField(32)`, `position` `PositiveIntegerField`, `category_slug` `CharField(100)`, `filters` `JSONField(default=dict)`, `target_measure` `CharField(20)`, `target_amount` `DecimalField(max_digits=14, decimal_places=3)`, `pinned_product` FK `catalog.Product` `null=True` `on_delete=SET_NULL`. `UniqueConstraint(fields=["shopping_list", "uid"], name="uniq_listitem_list_uid")`; ordering `["position"]`.
  - The category is stored by slug, not FK: categories are deactivated, never deleted, and resolution reports an inactive one.
- Produces (`lists/store.py`):
  - `class ListStore(Protocol)`: `lists() -> list[ListSpec]` (most recently updated first), `get(list_id: str) -> ListSpec | None`, `save(spec: ListSpec) -> None` (raises `ListLimitError` for a 21st list), `delete(list_id: str) -> None`.
  - `SessionListStore(session)`: `session["lists"]` is a list of `to_json` dicts. Reading never modifies the session (so it never creates one).
  - `AccountListStore(user)`: `save` upserts the `ShoppingList` by `(user, uid)` and replaces its items by `uid` in one `transaction.atomic()`, deleting items no longer in the spec and writing `position` from tuple order.
  - `store_for(request) -> ListStore`: `AccountListStore(request.user)` when authenticated, else `SessionListStore(request.session)`.

- [ ] **Step 1: Write failing tests**
  - `test_json_round_trip`: a list with two items (one pinned) survives `from_json(to_json(x)) == x`.
  - `test_from_json_tolerates_garbage`: `from_json(None)`, `from_json({"id": 1})` → `None`; a list with one malformed item keeps the good one.
  - `test_session_store_round_trip_and_order`.
  - `test_session_store_read_does_not_modify_session`: `SessionListStore(s).lists()` and `.get("x")` leave `s.modified` False.
  - `test_account_store_round_trip`: save, reload in a new store instance, equal; items keep order.
  - `test_account_store_replaces_removed_items`: saving a spec without an item deletes its row.
  - `test_account_store_scoped_to_user`: user B's store can't `get` user A's list id.
  - `test_limits`: a 21st list raises `ListLimitError` in both stores; a 101st item raises in `with_item`.
  - `test_name_defaults_and_trims`.
- [ ] **Step 2: Run** `uv run pytest tests/lists/test_spec_store.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**, then `uv run python manage.py makemigrations lists`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: list specs with session and account storage`

---

### Task 4: List resolution (roadmap 4.2, 4.3)

**Files:**
- Create: `lists/resolve.py`
- Test: `tests/lists/test_resolve.py`

**Interfaces:**
- Consumes: `matching_offers`, `parse_filters_strict` (Task 1); `fit_offer`, `fit_key`, `Fit` (Task 2); `store_totals`, `cheapest_single_store`, `cheapest_split`, `StoreTotal`, `Split` (Task 2); `ItemSpec`, `ListSpec` (Task 3); `fresh_offers`, `available_retailers`.
- Produces:
  - `@dataclass(frozen=True) ItemResolution(item: ItemSpec, category: Category | None, by_store: Mapping[str, Fit], best: Fit | None, alternatives: tuple[Fit, ...], reason: str | None)`.
  - `resolve_item(item: ItemSpec, stores: frozenset[str], now: datetime) -> ItemResolution`:
    1. Active `Category` by slug, else reason `"This category is no longer available"`.
    2. `parse_filters_strict(category, item.filters)`; any invalid key → reason `"Filter no longer valid: <keys joined by ', '>"`.
    3. Candidates: `matching_offers(category, spec, stores, now)`; when `item.pinned_product` is set, only that product's offers, and filters are not applied (the pin is the user's explicit choice of product) but category, freshness and stores are.
    4. Fit each candidate with `fit_offer(offer, item.target_measure, item.target_amount)`. Offers in a different `offer.currency` than the cheapest fit's are still fitted; currency is handled in `resolve_list`.
    5. `by_store[retailer]` = min fit by `fit_key` per retailer; `best` = min over `by_store`.
    6. `alternatives`: at `best.offer.retailer`, the best fit for each other distinct `package_size`, sorted by `fit_key`, at most 2. This is the "2 × 64 oz vs 1 × 128 oz" line.
    7. Reasons when `best` is `None`, first that applies: pinned and no fresh offer at these stores → `"The pinned product has no current price at your stores"`; no candidates → `"No current prices match at your stores"`; candidates but none sized for the target measure → `"Size unknown for every match"`; otherwise → `"Needs more than 24 packages"`.
  - `@dataclass(frozen=True) ListResolution(items: list[ItemResolution], stores: tuple[str, ...], totals: list[StoreTotal], single: StoreTotal | None, split: Split | None, max_split_stores: int | None, currency: str | None, mixed_currency: bool, oldest_price_at: datetime | None)`.
  - `resolve_list(spec: ListSpec, stores: frozenset[str] | None, now: datetime, max_split_stores: int | None = 2) -> ListResolution`:
    - `stores=None` means `available_retailers(now)`; `stores` in the result are sorted by display name.
    - `costs[item.id][store] = by_store[store].total_price` for resolved items only.
    - Currencies of all `by_store` fits: exactly one → compute `totals`, `single`, `split` and set `currency`; more than one → `mixed_currency=True`, `totals=[]`, `single=split=None`.
    - `oldest_price_at`: min `last_seen_at` over the fits used by `single` and `split`.

- [ ] **Step 1: Write failing tests** (build data with `ingest` + `FakeConnector`, two retailers set with `raw(retailer="fake")` and `raw(retailer="other")`; a stale offer is ingested with `now=NOW - timedelta(hours=25)`):
  - `test_cheapest_qualifying_offer_per_item`: a 2% milk item resolves to the cheapest 2% milk for a 1 gal target, never a whole milk that is cheaper per mL.
  - `test_unknown_excluded_under_filter`: a `fat=2` item never resolves to a milk whose fat is unknown, even when it is the cheapest.
  - `test_stale_offer_excluded_from_totals` (Review Focus 2): the cheapest offer for an item is 25 h old at store `other`; it is absent from `by_store`, store `other` lists the item as missing, and `split.total` uses the fresh offer at `fake`.
  - `test_quantity_fit_alternatives`: 64 fl oz at $3.00 and 128 fl oz at $5.48 at the same store, target 1 gal → `best.packages == 1`, `alternatives[0].packages == 2`, `alternatives[0].total_price == Decimal("6.00")`.
  - `test_pinned_product`: the pin wins over a cheaper match; a pin whose product has only stale offers → the pinned reason.
  - `test_reasons`: inactive category; stored `{"fat": "7"}` → "Filter no longer valid: fat"; no offers at the selected stores; a milk with no size → "Size unknown for every match".
  - `test_store_selection_limits_candidates`: `stores=frozenset({"other"})` never yields a `fake` offer.
  - `test_single_and_split`: two items, each cheaper at a different store; `single` is the cheaper full-coverage store, `split.total < single.total`, `split.stores == ("fake", "other")` in some order.
  - `test_mixed_currency_not_summed`: one item priced in USD at `fake` and CAD at `other` → `mixed_currency` True, `totals == []`.
- [ ] **Step 2: Run** `uv run pytest tests/lists/test_resolve.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Query per item; lists are small (≤ 100 items) and the catalog is cached, so no batching yet.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: resolve list specs to fresh offers with totals`

---

### Task 5: List pages and add-to-list (roadmap 4.3–4.4 UI)

**Files:**
- Create: `lists/views.py`, `lists/urls.py`, `lists/templates/lists/index.html`, `detail.html`, `add.html`, `_item.html`, `web/templates/web/_add_link.html`
- Modify: `config/urls.py` (`path("lists/", include("lists.urls"))`), `web/views.py` (category context: `add_url`), `web/templates/web/_results.html` (include `_add_link.html`), `web/templates/web/base.html` (header "Lists" link), `web/static/web/app.css`
- Test: `tests/lists/test_views_lists.py`

**Interfaces:**
- Consumes: `store_for`, `ListSpec`/`ItemSpec` helpers (Task 3); `resolve_list` (Task 4); `spec_params`, `parse_filters_strict` (Task 1); `TARGET_UNITS`, `target_units_for`, `target_to_canonical`, `default_target`, `display_target` (Task 1); M3 `get_prefs`, `safe_next`, `display_name`, `format_money`, `{% outbound %}`.
- Routes (`app_name = "lists"`, ids match `[0-9a-f]{32}`; any id not in `store_for(request)` → **404** on every route):
  - `GET /lists/` (`lists:index`): the visitor's lists with item counts and a "New list" form, or an empty state pointing to category pages.
  - `POST /lists/new` (`lists:create`): `name` → creates, redirects to detail.
  - `GET /lists/add?category=<slug>&<canonical filter params>` (`lists:add`): 404 for an unknown or inactive category; a summary of the spec ("Milk · fat 2% · organic"), a target amount (pre-filled from `default_target` of the top-ranked offer for that spec, else empty), a unit `<select>` of `target_units_for`, and a list `<select>` (existing lists, plus "New list", default the most recently updated). Filter keys are carried as hidden inputs.
  - `POST /lists/add`: re-parses the filters with `parse_filters_strict` (400 when any key is invalid), converts the target with `target_to_canonical` (re-render with "Enter an amount greater than 0" on `ValueError`), creates `DEFAULT_NAME` when `list=new` or the visitor has none, appends the item, redirects to the list detail. `ListLimitError` re-renders with "This list is full (100 items)" or "You have the maximum of 20 lists".
  - `GET /lists/<id>/` (`lists:detail`): `?store=` (repeatable) overrides `prefs.stores`; `?split=1|2|3|all` (default `2`, invalid → `2`) sets `max_split_stores`.
  - `POST /lists/<id>/rename`, `POST /lists/<id>/delete`.
  - `POST /lists/<id>/items/<item>/target` (amount + unit), `/remove`, `/pin` (`product` = id of a product in the item's category, else 400), `/unpin`.
- `_add_link.html` lives inside `_results.html`, so HTMX filter changes re-render it. It is a plain link to `lists:add` with the category and `spec_params(spec)` of the filters currently shown. No form, no CSRF token, so browsing stays cookie-free.
- `detail.html` shows:
  - per item: a label (category name + filters), the target via `display_target`, and the best fit as "2 × 64 fl oz at Walmart: $6.00" with "price as of <time>", the outbound link, the alternatives ("or 1 × 128 fl oz: $5.48"), a pin/unpin button, edit-target and remove forms; or the item's reason
  - a per-store table: store, total, and "missing N" when it lacks items
  - "Cheapest single store: <store> <total>", or "No single store has every item"
  - "Cheapest split (up to N stores): <total>" with each store's items and "saves <single − split>" when a single store exists; a `split` selector
  - "Can't total: prices in more than one currency" when `mixed_currency`
  - the disclosure banner (`has_outbound=True`) and the Amazon sentence when an Amazon offer is shown

- [ ] **Step 1: Write failing tests**
  - `test_category_page_has_add_link_with_current_filters`: the HTMX partial for `/c/dairy/milk/?fat=2` contains a link to `/lists/add?category=dairy%2Fmilk&fat=2`.
  - `test_browsing_sets_no_cookies`: anonymous GETs of `/c/dairy/milk/` (full page and HTMX partial) have no `Set-Cookie` header.
  - `test_add_creates_session_list_and_item`: POST add → a 302 to the detail; the session holds one list named "My list" with the canonical target (128 fl oz → `3785.412`).
  - `test_add_rejects_invalid_filter_and_bad_amount`: `fat=banana` → 400; amount `0` → re-rendered form with the error.
  - `test_detail_shows_fit_totals_single_split`: two items at two stores → the page shows "2 × 64 fl oz", a store table, "Cheapest single store" and "Cheapest split".
  - `test_detail_price_as_of_and_disclosure`: every fit has a `<time datetime=…>` "price as of" and the banner is present.
  - `test_unresolved_item_shows_reason`.
  - `test_edit_target_pin_unpin_remove_rename_delete`.
  - `test_other_owners_list_404` (Review Focus 5): a list id from client A returns 404 to client B (anonymous) and to a signed-in user C, for detail and each POST route; for user-owned lists, another user and an anonymous client both get 404.
  - `test_post_requires_csrf`: with `Client(enforce_csrf_checks=True)`, a POST without a token → 403.
  - `test_pin_rejects_product_from_other_category`.
- [ ] **Step 2: Run** `uv run pytest tests/lists/test_views_lists.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Templates use Django autoescaping and no `|safe` on data. Forms work without JavaScript.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: list pages and add-to-list from the category page`

---

### Task 6: Accounts: email and password, magic link, security settings (roadmap 4.5)

**Files:**
- Create: `accounts/__init__.py`, `apps.py`, `models.py`, `migrations/0001_initial.py`, `emails.py`, `ratelimit.py`, `magic.py`, `checks.py`, `views.py`, `urls.py`, `templates/accounts/*.html`, `templates/accounts/magic_link_email.txt`
- Modify: `config/settings.py`, `config/urls.py` (`path("account/", include("accounts.urls"))`), `web/templates/web/base.html` (header "Sign in" or "Account"), `.env.example`, `CLAUDE.md` (first-run steps add `createcachetable`)
- Test: `tests/accounts/__init__.py`, `tests/accounts/conftest.py` (autouse `cache.clear()`), `tests/accounts/test_magic.py`, `test_views_auth.py`, `test_ratelimit.py`, `test_settings_security.py`

**Settings** (`config/settings.py`):
- `INSTALLED_APPS += ["accounts"]`
- `EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", console if DEBUG else smtp)`, `EMAIL_HOST = env("DJANGO_EMAIL_HOST", "localhost")`, `EMAIL_PORT = int(env("DJANGO_EMAIL_PORT", 587))`, `EMAIL_HOST_USER = env("DJANGO_EMAIL_HOST_USER", "")`, `EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_HOST_PASSWORD", "")`, `EMAIL_USE_TLS = env("DJANGO_EMAIL_USE_TLS", "1") == "1"`, `DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", "UnitPricer <noreply@unitpricer.com>")`
- `SITE_URL = env("UNITPRICER_SITE_URL")`: the origin used in emailed links, never the request's `Host`
- `MAGIC_LINK_TTL = timedelta(minutes=15)`
- `CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "unitpricer_cache"}}` (shared across web processes; the test database creates it automatically)
- `SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = not DEBUG`, `SESSION_COOKIE_HTTPONLY = True`, `LOGIN_URL = "accounts:sign_in"`
- `.env.example`: `UNITPRICER_SITE_URL=` and the `DJANGO_EMAIL_*` and `DJANGO_DEFAULT_FROM_EMAIL` names, blank, with a comment that the console backend prints mail in dev.

**Interfaces:**
- `accounts/checks.py`: `unitpricer.E010` when `DEBUG` is off and `SITE_URL` is unset or not `https://`.
- `accounts/emails.py`: `normalize_email(raw: str) -> str | None`: strip, lowercase, Django `EmailValidator`, at most 150 characters (the `User.username` limit); else `None`.
- `accounts/ratelimit.py`: `allow(scope: str, key: str, limit: int, window: timedelta) -> bool`. It counts in the cache under `f"rl:{scope}:{sha256(SECRET_KEY + key)}"` with the window as the timeout (`cache.add` then `cache.incr`) and returns False once the count exceeds `limit`. Limits used: magic link per email **3 / 15 min**, per IP **10 / hour**; password sign-in failures per email **5 / 15 min**, per IP **20 / hour**. The IP is `REMOTE_ADDR`.
- `accounts/models.py`: `MagicLinkToken(email EmailField(max_length=150), token_hash CharField(64, unique), next_path CharField(500, default="/lists/"), created_at DateTimeField(auto_now_add, db_index), expires_at DateTimeField, used_at DateTimeField(null))`.
- `accounts/magic.py`:
  - `issue_token(email: str, next_path: str, now: datetime) -> str`: creates the row and returns the raw token. It also deletes rows whose `expires_at` is more than a day old, so stored emails don't accumulate.
  - `peek_token(raw: str, now: datetime) -> MagicLinkToken | None`: unused and unexpired, without consuming.
  - `consume_token(raw: str, now: datetime) -> MagicLinkToken | None`: one conditional `UPDATE … SET used_at=now WHERE token_hash=… AND used_at IS NULL AND expires_at > now`; returns the row only if exactly one row changed.
  - `send_magic_link(email: str, next_path: str, now: datetime) -> None`: issues and mails `{SITE_URL}/account/magic/<token>` (with `DEBUG` and no `SITE_URL`, `http://localhost:8000`), subject "Your UnitPricer sign-in link", body from `magic_link_email.txt`: the link, "It works once and expires in 15 minutes.", "If you didn't ask for it, ignore this email."
- Routes (`app_name = "accounts"`):
  - `GET /account/sign-in?next=` (`sign_in`): two forms, "Email me a sign-in link" (email) and "Sign in with a password" (email + password), with the copy "No account? Enter your email for a sign-in link; that creates one."
  - `POST /account/sign-in`: password sign-in. Rate-limited on failures; any failure (unknown email, wrong password, no usable password, limited) re-renders with the same "Email or password is incorrect." and status 200. Success: `login()`, redirect to `safe_next`.
  - `POST /account/magic-link` (`magic_request`): an over-limit IP gets **429** "Too many requests. Try again later.". Otherwise, an invalid email re-renders with "Enter a valid email address."; a valid one sends the link unless the per-email limit is hit, then **always** renders `link_sent.html` with the same body ("If that address can receive email, a sign-in link is on its way.").
  - `GET /account/magic/<token>` (`magic`): a valid token renders `magic_confirm.html` with a POST "Sign in" button; an invalid one renders `magic_invalid.html`. Neither consumes the token.
  - `POST /account/magic/<token>`: `consume_token`; on success, get or create the `User` with `username=email=token.email` (new users get `set_unusable_password()`), `login(request, user, backend="django.contrib.auth.backends.ModelBackend")`, redirect to `safe_next(token.next_path)`. On failure, `magic_invalid.html` ("This link has expired or was already used." plus a link to request a new one).
  - `POST /account/sign-out` (`sign_out`): `logout()`, redirect `/`.
  - `GET /account/` (`account`, `@login_required`): email, a set-password form (`SetPasswordForm` when the user has no usable password, else `PasswordChangeForm`), sign-out, and delete.
  - `POST /account/password`: on success, `update_session_auth_hash`.
  - `POST /account/delete`: requires the typed confirmation `delete`; deletes the user (cascading lists, settings), logs out, redirects `/`.

- [ ] **Step 1: Write failing tests**
  - `test_magic.py`:
    - `test_token_stored_hashed`: the raw token appears in no column; `token_hash == sha256(raw).hexdigest()`.
    - `test_token_single_use` (Review Focus 4): the first `consume_token` returns the row, the second returns `None`.
    - `test_token_expires`: consume at `issued + 15 min + 1 s` → `None`; at `+ 14 min` → row.
    - `test_concurrent_consume_only_one_wins`: two consumes of one token, only one returns a row.
    - `test_old_tokens_purged_on_issue`.
  - `test_views_auth.py`:
    - `test_magic_link_email_sent_with_site_url`: `mail.outbox[0]` body contains `https://unitpricer.test/account/magic/` (with `settings.SITE_URL` overridden), and the `Host` header doesn't change it.
    - `test_get_does_not_consume` (Review Focus 4): GET the link twice, then POST → signed in.
    - `test_post_consumes_and_creates_user`: a new email → a `User` with an unusable password; the session has `_auth_user_id`; the session key changed.
    - `test_used_or_expired_link_shows_invalid_page`.
    - `test_no_user_enumeration`: the magic-request responses for an existing and a non-existent email have identical status and body; password sign-in with an unknown email and with a wrong password give identical status and body.
    - `test_password_sign_in_and_safe_next`: `next=https://evil.example/` → redirect `/`.
    - `test_magic_link_only_account_can_set_password`.
    - `test_sign_out_requires_post`, `test_delete_account_cascades_lists`.
    - `test_auth_posts_require_csrf`.
  - `test_ratelimit.py`:
    - `test_magic_per_email_limit_silent`: the 4th request for one email in 15 min sends no mail and returns the same `link_sent` page.
    - `test_magic_per_ip_limit_429`.
    - `test_password_failures_limited`: after 5 failures, even the correct password is refused with the same message until the window passes (advance with `cache.clear()`).
  - `test_settings_security.py`:
    - `test_secure_cookies_when_not_debug`: run a settings import in a subprocess with `DJANGO_DEBUG=0`, as `tests/catalog/test_settings.py` does.
    - `test_console_email_backend_in_debug`.
    - `test_site_url_check`: `E010` is raised with `DEBUG=False` and no `SITE_URL`, and with an `http://` URL.
- [ ] **Step 2: Run** `uv run pytest tests/accounts -v`. Expected: FAIL.
- [ ] **Step 3: Implement**, then `uv run python manage.py makemigrations accounts`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: accounts with password and magic-link sign-in`

---

### Task 7: Saved settings and session-to-account merge (roadmap 4.5 merge, 4.6)

**Files:**
- Create: `accounts/merge.py`, `accounts/migrations/0002_usersettings.py`
- Modify: `accounts/models.py`, `accounts/apps.py` (connect the receiver in `ready()`), `web/templates/web/base.html` (render `messages`), `web/prefs.py`, `web/views.py` (category default comparison; settings page comparisons), `web/templates/web/settings.html`
- Test: `tests/accounts/test_merge.py`, `tests/accounts/test_saved_settings.py`; update `tests/web/test_prefs.py` for the new field

**Interfaces:**
- Consumes: `SessionListStore`, `AccountListStore`, `ListLimitError` (Task 3); `MagicLinkToken` sign-in and password sign-in (Task 6); M3 `Prefs`, `get_prefs`, `save_prefs`.
- Produces:
  - `UserSettings(models.Model)`: `user` `OneToOneField(AUTH_USER_MODEL, CASCADE, related_name="unitpricer_settings")`, `unit_system` `CharField(8, blank=True)` (blank = follow `Accept-Language`), `stores` `JSONField(null=True)` (`None` = all), `comparisons` `JSONField(default=dict)` (category slug → comparison name), `updated_at` `auto_now`.
  - `web/prefs.py`:
    - `Prefs` gains `comparisons: Mapping[str, str] = field(default_factory=dict)`.
    - `get_prefs(request)`: when authenticated, reads `UserSettings` (missing row = defaults); otherwise the session, as in M3. Session `prefs["comparisons"]` is a dict of str → str; anything else is ignored.
    - `save_prefs(request, prefs)`: when authenticated, upserts `UserSettings`; otherwise the session.
  - Category view: `comparison = request.GET.get("comparison") or prefs.comparisons.get(cat.slug)`. `browse()` already falls back to the category default for an invalid value.
  - Settings page: one "Compare by" `<select name="cmp:<slug>">` per active category, offering "Default (<label>)" plus the category's comparisons. Unknown slugs and comparisons outside the category's default + alternates are dropped on save; "Default" removes the key.
  - `accounts/merge.py`:
    - `@dataclass(frozen=True) MergeResult(lists_added: int, items_added: int, lists_skipped: int)`.
    - `merge_session_into_account(session, user) -> MergeResult`:
      - **Lists:** for each session `ListSpec`, if the account has a list with that `uid`, add only the items whose `uid` isn't in it yet (append, keep the account's order); otherwise save it as a new account list. Lists past `MAX_LISTS`, or items past `MAX_ITEMS`, are skipped and counted, never raising. Then `session.pop("lists")`.
      - **Prefs:** account values win; account fields that are unset (`unit_system == ""`, no row) take the session's; `comparisons` = session ∪ account, account winning on conflicts. Then `session.pop("prefs")`.
      - Runs in one `transaction.atomic()`.
    - `on_user_logged_in(sender, request, user, **kwargs)`: calls the merge and, when `lists_skipped > 0`, adds a `messages.warning` "Some lists from this device didn't fit in your account (20 lists, 100 items each)."

- [ ] **Step 1: Write failing tests**
  - `test_merge.py`:
    - `test_merge_moves_session_lists` (end to end): an anonymous client adds an item, signs in by magic link (the link opened in the same browser), and `/lists/` shows the list; the session no longer holds `lists`.
    - `test_merge_idempotent` (Review Focus 3): calling `merge_session_into_account` twice with the same session data (re-seeded between calls) leaves `ShoppingList` and `ListItem` counts unchanged after the first.
    - `test_merge_keeps_existing_account_lists`: the account's lists, names and items are unchanged; session lists are added beside them.
    - `test_merge_same_uid_adds_only_new_items`.
    - `test_merge_respects_limits`: an account with 19 lists plus 3 session lists → 1 added, 2 skipped, and the warning message is present.
    - `test_password_sign_in_also_merges`.
  - `test_saved_settings.py`:
    - `test_settings_persist_across_devices`: user saves metric + `{"walmart"}` on client A; a fresh client B signing in sees metric labels and the store selection.
    - `test_prefs_merge_account_wins_session_fills_gaps`: account `unit_system=""` with session imperial → imperial; account metric with session imperial → metric; comparisons union with the account winning.
    - `test_default_comparison_used_on_category_page`: saved `{"household/toilet-paper": "per_roll"}` → the page ranks per roll with no `comparison` param; `?comparison=per_100_sheets` overrides.
    - `test_invalid_saved_comparison_falls_back`.
    - `test_settings_page_drops_unknown_category_and_comparison`.
    - `test_anonymous_settings_still_session_only`: M3 settings tests stay green, with no `UserSettings` rows created.
- [ ] **Step 2: Run** `uv run pytest tests/accounts/test_merge.py tests/accounts/test_saved_settings.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**, then `uv run python manage.py makemigrations accounts`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: saved settings and session-to-account merge on sign-in`

---

## M4 exit criteria

- With `demo_data` loaded: from `/c/dairy/milk/?fat=2`, "Add to list" with 1 gal creates an anonymous list. Its page shows the whole-package fit, per-store totals, the cheapest single store and the cheapest split, each price with "price as of" and a direct link.
- Requesting a magic link prints the email to the console. Opening it in the same browser and pressing "Sign in" moves the list and settings into the account, and a second browser signed in to the same account sees them.
- Every Review Focus item has a named test. `uv run pytest` is green locally and in CI; `manage.py check` passes with `DEBUG=1`, and with `DEBUG=0` once `UNITPRICER_SITE_URL` is set.
- No new dependencies. Accounts store only email, password hash, Django's login timestamps, lists and settings.
