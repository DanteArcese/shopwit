# Limited Public Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `unitpricer.com` serves the full app publicly and indexably, with honest coverage, legal pages and abuse protection, so affiliate programs and API providers can review a live site; `beta.unitpricer.com` stays gated staging.

**Architecture:** Host routing flips: the apex gets every app route except the admin (`config/urls_public.py` becomes the app URLconf without `admin/`), the beta host keeps `config/urls.py` (the same routes plus `admin/`) behind Cloudflare Access with `noindex`. The waitlist landing page is retired; its double-opt-in signup becomes a cookie-free "notify me about new stores" block on the home page. Sample Walmart data is purged by a dry-run-first management command, and recorded data can no longer be loaded with `DEBUG` off. Cloudflare rules, monitors, HSTS and the affiliate applications are operational steps in a new `docs/launch.md` runbook, gated on two maintainer-owned legal checks.

**Tech Stack:** Django 5.2, HTMX, Postgres 17, pytest-django, Cloudflare (Tunnel, Access, WAF rate limiting, Bot Fight Mode) via API, an external uptime monitor; `sentry-sdk` only if the maintainer approves Task 7.

**Spec:** `docs/superpowers/specs/2026-11-07-unitpricer-design.md` (§5.3, §5.4, §10 affiliate terms, §11 ODbL, §12 hosting), `docs/superpowers/specs/2026-11-08-open-search-design.md` (§14.5 apex host, cookie-free search), `docs/research/2026-11-07-sovrn-api.md` §6 (program terms), `docs/research/2026-11-08-open-prices.md` §6 (ODbL), issue #74 (agreed scope, 2026-11-08). Assumes M4 (lists and accounts, #73) is merged: `lists/` and `account/` routes exist in `config/urls.py` and `UNITPRICER_SITE_URL` drives emailed links.

## Global Constraints

- Python with `uv` only. No AI/LLM libraries or calls. No new dependencies except `sentry-sdk`, and only in Task 7 if approved.
- Secrets stay in `.env` / `/srv/unitpricer/app.env`; never read `.env`. New variable names go in `.env.example`, blank.
- **Hosts (exact):** apex `unitpricer.com` = the public app, indexable. `beta.unitpricer.com` = the same app plus `/admin/`, behind Cloudflare Access, every response `X-Robots-Tag: noindex, nofollow`, `robots.txt` `Disallow: /`. `www.unitpricer.com` → 301 to the apex, path and query kept (unchanged). Tunnel ingress unchanged; the Access application stays on beta only.
- **Access middleware stays fail-closed:** every host except `PUBLIC_HOST` and `www.PUBLIC_HOST` needs a valid Access JWT; the in-container `/healthz` probe exemption is unchanged.
- **`/admin/` exists only on the beta host** (404 on the apex).
- **Browsing GETs set no cookies** on either host: home, category (full and HTMX partial), search, poll, disclosure, about, terms, privacy, sitemap, robots. The home-page waitlist form carries no CSRF token.
- **Waitlist unchanged in substance:** double opt-in, same rate limits (5 signups/IP/hour, 3 sends/email/day, 10-minute resend window), same daily email cap, same honeypot, same token lifetimes, no new stored fields.
- **No sample data in production:** `refresh --recordings` and `demo_data` refuse when `DEBUG` is off.
- Empty curated-category copy (exact): `No live prices yet — coming: {UNITPRICER_COMING_SOON}.` with `UNITPRICER_COMING_SOON` default `Walmart (December)`; when the setting is blank the sentence is `No live prices yet.`
- Contacts (exact): `hello@unitpricer.com` (general), `dante@unitpricer.com` (maintainer).
- Terms of use page carries, at the top, exactly: `Draft — pending review by the maintainer and legal counsel. Not yet in effect.` until the maintainer removes it.
- Amazon sentence (exact, Amazon-required): `As an Amazon Associate I earn from qualifying purchases.` Shown on the disclosure page only when `AMAZON_ASSOCIATES_TAG` is set.
- The launch announcement email is not sent until both legal gates in `docs/launch.md` are checked (ODbL answer from Open Food Facts; `UNITPRICER_POSTAL_ADDRESS` set to a real address).
- Before Task 1, open an implementation issue ("Limited public launch", refs #74). Commit subjects end with `(#<that issue>)`. Each code task ends with `uv run pytest -q` green.

## Review Focus

1. **Beta-only things leaking to the apex.** `/admin/` and `/admin/login/` on the apex must 404, not redirect to a login form; the beta `X-Robots-Tag` must never appear on apex responses, and apex responses must never carry `Disallow: /`. Test: Task 2 `test_admin_404s_on_apex`, `test_apex_app_routes_indexable`.
2. **Access still fails closed on beta.** After the routing flip, a beta request with no token, a bad token, or a JWKS failure is 403 on every route, including `/`, `/waitlist/` POST and `/admin/`; an unknown host is still 400. Test: Task 2 `test_beta_routes_need_token` (parametrized over the new apex route list).
3. **Sample data reaching the public.** A purge that misses generic (uncategorized) sample offers, or deletes a product an Open Prices observation still uses. Test: Task 1 `test_purge_keeps_shared_products_and_real_offers`, `test_purge_covers_generic_offers`.
4. **Waitlist double opt-in changed by the move.** Moving the form to the home page and dropping its CSRF token must not let a signup confirm without the emailed POST, and must not let a cross-site page POST signups. Test: Task 4 `test_home_signup_still_needs_confirm`, `test_signup_rejects_foreign_origin`.
5. **Cookies on browsing GETs.** The new home sections, the waitlist block and the sitemap must not start a session or set `csrftoken`. Test: Task 4 `test_home_sets_no_cookies` and Task 6 `test_public_pages_set_no_cookies`.
6. **Public search now drives live fetches.** Anonymous apex searches enqueue live-source jobs; the existing per-source budgets and per-client queue limits are the cost cap. Bots are excluded from demand and enqueue by `BOT_UA`, and robots.txt disallows `/search`. No new test (existing open-search tests cover budgets); reviewers check robots and the Cloudflare rule in Task 8.

---

### Task 1: Purge recorded sample data; refuse recordings with DEBUG off

**Files:**
- Create: `catalog/sample.py`, `catalog/management/commands/purge_sample_data.py`
- Modify: `catalog/management/commands/refresh.py`
- Test: `tests/catalog/test_purge_sample.py`; update `tests/catalog/test_scheduler.py`, `tests/catalog/test_final_fixes.py`, `tests/catalog/test_observed.py` (set `settings.DEBUG = True` where they call `refresh` with `recordings=`)

**Interfaces:**
- Produces: `sample_item_ids(directory: Path) -> set[str]` (every `json["items"][*]["itemId"]` across `*.json` in the directory, as `str`; raises `CommandError` if the directory is missing or yields no ids). `purge_sample(ids: set[str], apply: bool) -> tuple[int, int]` returning `(offers, products)` counts. Command `purge_sample_data [--recordings DIR] [--apply]`, default DIR `settings.BASE_DIR / "tests/connectors/recordings/walmart"`, default is a dry run.

- [ ] **Step 1: Write the failing tests**

```python
def test_refresh_recordings_refuses_without_debug(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG is off"):
        call_command("refresh", connector="walmart", recordings=str(RECORDINGS))
    assert not Offer.objects.exists()

def test_purge_dry_run_deletes_nothing(loaded_sample):  # fixture: DEBUG=True refresh from recordings
    before = Offer.objects.count()
    out = run("purge_sample_data")
    assert "dry run" in out and f"offers={before}" in out
    assert Offer.objects.count() == before

def test_purge_keeps_shared_products_and_real_offers(loaded_sample):
    sample = Offer.objects.filter(retailer="walmart").first()
    observed = make_observed_offer(product=sample.product)          # Open Prices on the same product
    real = make_offer(retailer="walmart", retailer_item_id="999999999")  # not in the recordings
    run("purge_sample_data", "--apply")
    assert not Offer.objects.filter(retailer="walmart").exclude(pk=real.pk).exists()
    assert Offer.objects.filter(pk__in=[observed.pk, real.pk]).count() == 2
    assert Product.objects.filter(pk=sample.product_id).exists()

def test_purge_covers_generic_offers(loaded_sample):
    g = make_offer(retailer="walmart", retailer_item_id=next(iter(sample_item_ids(RECORDINGS))), category=None, kind="live")
    run("purge_sample_data", "--apply")
    assert not Offer.objects.filter(pk=g.pk).exists()

def test_purge_missing_dir_errors(tmp_path):
    with pytest.raises(CommandError):
        call_command("purge_sample_data", recordings=str(tmp_path / "nope"))
```

`make_offer` / `make_observed_offer` come from `tests/catalog/helpers.py` (extend it if a kwarg is missing).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/catalog/test_purge_sample.py -v`
Expected: FAIL (unknown command `purge_sample_data`; refresh loads offers with DEBUG off).

- [ ] **Step 3: Implement**

`refresh.handle`: when `recordings` is given and `settings.DEBUG` is false, raise `CommandError("--recordings loads sample data; it refuses to run when DEBUG is off.")` before building anything. `purge_sample` runs in `transaction.atomic()`: offers = `Offer.objects.filter(retailer="walmart", kind=Offer.Kind.LIVE, retailer_item_id__in=ids)`; products = those offers' `product_id`s that have no other offer left after deletion. On a dry run, compute both counts and delete nothing. Output: `offers=N products=M (dry run: nothing deleted)` or `offers=N products=M deleted`. `IngestionRun` rows are left as history. Then add `refresh` with `recordings=` callers in the three existing test files to `settings.DEBUG = True`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/catalog -q`
Expected: PASS

- [ ] **Step 5: Commit** — `feat: purge_sample_data command; refresh --recordings needs DEBUG (#N)`

---

### Task 2: Apex serves the app; admin stays on beta

**Files:**
- Modify: `config/urls_public.py`, `config/urls.py`, `web/hosts.py` (docstring only), `web/access.py` (comment only), `web/health.py` (`robots_txt`), `web/checks.py`
- Test: `tests/deploy/test_hosts.py`, `tests/deploy/test_access.py`, `tests/web/test_generic_search.py` (replace `test_apex_has_no_search`), `tests/waitlist/test_views.py` (landing tests move in Task 4)

**Interfaces:**
- Produces: `config.urls_public.urlpatterns` = every route currently in `config/urls.py` except `admin/`, plus `path("waitlist/", include("waitlist.urls"))`. `config.urls.urlpatterns = [path("admin/", admin.site.urls)] + urls_public.urlpatterns`. Later tasks add public routes to `config/urls_public.py` only. Check id `unitpricer.E023`.

- [ ] **Step 1: Write the failing tests**

In `tests/deploy/test_hosts.py`, replace `APP_ROUTES`/`test_every_app_route_404s_on_apex` with:

```python
APEX_ROUTES = [  # every non-admin route, incl. M4's lists and account pages
    ("get", "/", {}), ("get", "/c/dairy/milk/", {}), ("get", "/c/dairy/milk/", {"HTTP_HX_REQUEST": "true"}),
    ("get", "/search?q=milk", {}), ("get", "/settings", {}), ("get", "/disclosure", {}),
    ("get", "/feedback", {}), ("get", "/privacy", {}), ("get", "/lists/", {}), ("get", "/account/sign-in", {}),
]

@pytest.mark.parametrize("method,path,extra", APEX_ROUTES)
def test_apex_app_routes_indexable(client, method, path, extra):
    r = getattr(client, method)(path, HTTP_HOST=PUBLIC, **extra)
    assert r.status_code == 200 and "X-Robots-Tag" not in r

@pytest.mark.parametrize("path", ["/admin/", "/admin/login/"])
def test_admin_404s_on_apex(client, path):
    assert client.get(path, HTTP_HOST=PUBLIC).status_code == 404

def test_admin_on_beta(client):
    assert client.get("/admin/login/", HTTP_HOST=APP).status_code == 200

def test_robots_per_host(client):
    apex = client.get("/robots.txt", HTTP_HOST=PUBLIC).content.decode()
    assert "Allow: /" in apex and "Sitemap: https://unitpricer.com/sitemap.xml" in apex
    for p in ("/search", "/settings", "/lists/", "/account/", "/waitlist/", "/click"):
        assert f"Disallow: {p}" in apex
    assert client.get("/robots.txt", HTTP_HOST=APP).content.decode() == "User-agent: *\nDisallow: /\n"
```

Use the real M4 sign-in path in `APEX_ROUTES` (whatever `accounts/urls.py` names it). In `tests/deploy/test_access.py` add `test_beta_routes_need_token` parametrized over `APEX_ROUTES` paths plus `/admin/` and `POST /waitlist/`: no token → 403, bad audience → 403, JWKS error → 403; and `test_apex_routes_need_no_token` over `APEX_ROUTES` → not 403. In `tests/deploy/test_prod_settings.py` (or a new `tests/web/test_checks.py`) add `test_site_url_must_be_public_url`: `DEBUG=False`, `SITE_URL="https://beta.unitpricer.com"` → `E023`; `SITE_URL == PUBLIC_URL` → no `E023`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/deploy -q`
Expected: FAIL (apex app routes 404; robots has no Disallow lines; no E023).

- [ ] **Step 3: Implement**

Move routes as in Interfaces. Remove `name="landing"` (Task 4 retires the view). `robots_txt` on `PUBLIC_HOST` returns `User-agent: *`, `Allow: /`, one `Disallow:` line per path above, a blank line, then `Sitemap: {PUBLIC_URL}/sitemap.xml`; other hosts unchanged. `HostRoutingMiddleware` logic is unchanged (apex → `config.urls_public`, beta → `config.urls` with the header); update its docstring to "Serve the public app on PUBLIC_HOST and the app plus admin elsewhere". `CloudflareAccessMiddleware` logic is unchanged; update its comment to say the apex is the public app. `web/checks.py`: `check_site_url` (deploy tag) raises `unitpricer.E023` "SITE_URL must equal PUBLIC_URL: emailed links point at the public host." when `not DEBUG and PUBLIC_HOST and SITE_URL != PUBLIC_URL`. Update `tests/deploy/conftest.py` prod env so `UNITPRICER_SITE_URL=https://unitpricer.com`, and `tests/waitlist/test_announce.py` if it asserts the beta URL.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: PASS (landing-page tests in `tests/waitlist/test_views.py` may fail until Task 4; mark them `xfail(reason="Task 4")` only if Task 4 is not done in the same branch before review — preferred: do Task 4 next and remove the marks there).

- [ ] **Step 5: Commit** — `feat: serve the app on the apex; admin stays on beta (#N)`

---

### Task 3: Terms, About, disclosure by configured programs, footer

**Files:**
- Create: `web/templates/web/terms.html`, `web/templates/web/about.html`
- Modify: `web/views.py` (`terms`, `about`, `disclosure`), `config/urls_public.py`, `web/templates/web/disclosure.html`, `web/templates/web/base.html` (footer)
- Test: `tests/web/test_pages.py`

**Interfaces:**
- Produces: URL names `terms` (`/terms`), `about` (`/about`). `affiliate_programs() -> list[str]` in `catalog/links.py`: display names of programs whose setting is non-empty, in order `Amazon Associates Program` (`amazon_associates_tag`), `Walmart's affiliate program through Impact` (`walmart_impact_publisher_id`), `Sovrn Commerce` (`sovrn_site_key`).

- [ ] **Step 1: Write the failing tests**

```python
def test_terms_marked_draft(client):
    body = client.get("/terms").content.decode()
    assert "Draft — pending review by the maintainer and legal counsel. Not yet in effect." in body

def test_about_names_contacts(client):
    body = client.get("/about").content.decode()
    assert "mailto:hello@unitpricer.com" in body and "mailto:dante@unitpricer.com" in body

def test_amazon_sentence_only_with_tag(client, settings):
    settings.AFFILIATE = {**settings.AFFILIATE, "amazon_associates_tag": None}
    body = client.get("/disclosure").content.decode()
    assert "As an Amazon Associate" not in body and "Amazon Associates Program" not in body
    settings.AFFILIATE = {**settings.AFFILIATE, "amazon_associates_tag": "unitpricer-20"}
    body = client.get("/disclosure").content.decode()
    assert "As an Amazon Associate I earn from qualifying purchases." in body

def test_disclosure_lists_no_programs_when_none_configured(client, settings):
    settings.AFFILIATE = {k: None for k in settings.AFFILIATE}
    assert "does not currently participate in any affiliate program" in client.get("/disclosure").content.decode()

def test_footer_links(client):
    body = client.get("/").content.decode()
    for href in ("/disclosure", "/privacy", "/terms", "/about", "/feedback"):
        assert f'href="{href}"' in body
```

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/web/test_pages.py -v` → FAIL (404s).

- [ ] **Step 3: Implement**

Terms sections (headings fixed, prose short and plain): the draft banner above; What UnitPricer is (a free, open-source price-per-unit comparison); Prices are not offers (shown "as of" a time, may be wrong, check the retailer; in-store prices are crowd-sourced observations); Affiliate links (link to `/disclosure`); Acceptable use (no scraping beyond robots.txt, no automated bulk searches, no abuse of forms); Accounts and lists (M4: you may delete your account; we may remove abusive accounts); Open data and code (ODbL notice for Open Prices/Open Food Facts data, AGPL-3.0 for the code, link to the repo); No warranty and limitation of liability; Changes and contact (`hello@unitpricer.com`). About: who runs it (one person, the maintainer, named as in the maintainer decision below), why (fair unit prices, commission-blind ranking, no AI), how it's funded (affiliate links, link to `/disclosure`), where data comes from (link to "Stores we cover" on `/`), contact both addresses, source code link. Disclosure: replace the hard-coded program sentence with `affiliate_programs()` joined in prose (or the "does not currently participate" sentence), the Amazon sentence wrapped in `{% if amazon_tag %}`, and the "Live search" paragraph built from `live_source_info(live_off=settings.LIVE_OFF_ENABLED)` names (Walmart only when its credentials are set). Footer: Disclosure, Privacy, Terms, About, Feedback, Source code, Settings.

- [ ] **Step 4: Run tests** — `uv run pytest tests/web -q` → PASS
- [ ] **Step 5: Commit** — `feat: terms (draft), about, config-driven disclosure, footer (#N)`

---

### Task 4: Home-page "notify me" block replaces the landing page; privacy for a public app

**Files:**
- Create: `waitlist/templates/waitlist/_notify.html`, `waitlist/templates/waitlist/notify.html`
- Delete: `waitlist/templates/waitlist/landing.html`, `waitlist/templates/waitlist/base_public.html`, `waitlist/static/waitlist/landing.css` (move any form rules still used into `web/static/web/app.css`)
- Modify: `waitlist/views.py` (drop `landing`; `signup` changes below), every `waitlist/templates/waitlist/*.html` (extend `web/base.html`), `waitlist/templates/waitlist/privacy.html`, `web/templates/web/home.html`
- Test: `tests/waitlist/test_views.py`, `tests/web/test_views_home.py`

**Interfaces:**
- Consumes: Task 2's apex URLconf (`/waitlist/` is routed on both hosts).
- Produces: `waitlist/_notify.html` include (form `method="post" action="/waitlist/"`, no `{% csrf_token %}`, same `email` input and `website` honeypot). `waitlist/notify.html` (full page wrapping `_notify.html`, used to re-show the form with `error`).

- [ ] **Step 1: Write the failing tests**

```python
def test_home_has_notify_block(client):
    body = client.get("/", HTTP_HOST=PUBLIC).content.decode()
    assert "Notify me about new stores" in body and 'action="/waitlist/"' in body
    assert "csrfmiddlewaretoken" not in body

def test_home_sets_no_cookies(client):
    r = client.get("/", HTTP_HOST=PUBLIC)
    assert not r.cookies

def test_home_signup_still_needs_confirm(client, mailoutbox):
    client.post("/waitlist/", {"email": "a@example.com"}, HTTP_HOST=PUBLIC)
    sub = Subscriber.objects.get(email="a@example.com")
    assert sub.confirmed_at is None and len(mailoutbox) == 1
    client.get(confirm_url_from(mailoutbox[0]), HTTP_HOST=PUBLIC)
    sub.refresh_from_db(); assert sub.confirmed_at is None   # GET never confirms

def test_signup_rejects_foreign_origin(client):
    r = client.post("/waitlist/", {"email": "a@example.com"}, HTTP_HOST=PUBLIC, HTTP_ORIGIN="https://evil.example")
    assert r.status_code == 403 and not Subscriber.objects.exists()

def test_signup_same_origin_or_no_origin_ok(client):
    for origin in ({"HTTP_ORIGIN": "https://unitpricer.com"}, {}):
        assert client.post("/waitlist/", {"email": "b@example.com"}, HTTP_HOST=PUBLIC, **origin).status_code == 200

def test_invalid_email_reshows_form(client):
    body = client.post("/waitlist/", {"email": "nope"}, HTTP_HOST=PUBLIC).content.decode()
    assert "Enter a valid email address." in body and 'action="/waitlist/"' in body
```

Keep every existing signup/confirm/unsubscribe/rate-limit/budget test, re-pointed from the landing page to `/waitlist/`; delete tests of `GET /` rendering the landing page.

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/waitlist tests/web/test_views_home.py -v` → FAIL

- [ ] **Step 3: Implement**

`signup` becomes `@csrf_exempt` and, first thing, returns `HttpResponseForbidden()` when an `Origin` header is present and is not `f"https://{normalize_host(request)}"` (or `http://` when `DEBUG`). Everything after is unchanged. The invalid-email branch renders `waitlist/notify.html`. Block copy: heading "Notify me about new stores", text "We're adding stores. Leave your email and we'll write when a new one goes live: one confirmation email now, then only store news. Unsubscribe in one click." plus a link to `/privacy`. Privacy page: drop the "Beta app:" prefixes; waitlist purpose becomes "store announcements"; the cookies list states that browsing sets none, `sessionid`/`csrftoken` start only when you save settings, use a list, sign in or send feedback, and `CF_Authorization` applies only to the staging host; keep M4's account wording. Existing confirmed subscribers are unchanged.

- [ ] **Step 4: Run tests** — `uv run pytest -q` → PASS (remove any Task 2 `xfail` marks)
- [ ] **Step 5: Commit** — `feat: notify-me block on the home page; retire the landing page (#N)`

---

### Task 5: Honest coverage — "Stores we cover", "Seen in stores near you", empty-category copy

**Files:**
- Create: `catalog/coverage.py`, `web/templates/web/_coverage.html`
- Modify: `web/views.py` (`home`, `category` context), `web/templates/web/home.html`, `web/templates/web/_results.html`, `config/settings.py` (`COMING_SOON`), `.env.example`
- Test: `tests/catalog/test_coverage.py`, `tests/web/test_views_home.py`, `tests/web/test_views_category.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) Coverage(live: list[str], in_store: list[tuple[str, int]])` and `stores_we_cover(now: datetime) -> Coverage`: `live` = `display_name` of retailers with fresh live offers (via `fresh_offers`), sorted; `in_store` = `(chain_name, store_count)` for chains with fresh observed offers (via `fresh_observed`), sorted by name. `nearby_categories(now: datetime, near: Near) -> list[tuple[Category, int]]`: active curated categories with fresh observed offers at stores `within(near, ...)`, with the offer count, sorted by count descending then name, at most 8. `settings.COMING_SOON = env("UNITPRICER_COMING_SOON", "Walmart (December)")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_stores_we_cover_from_data(db, now):
    make_offer(retailer="walmart", last_seen_at=now)                       # fresh live
    make_offer(retailer="amazon", last_seen_at=now - timedelta(days=3))    # stale: excluded
    make_observed_offer(chain="Market Basket", store_code="s1"); make_observed_offer(chain="Market Basket", store_code="s2")
    cov = stores_we_cover(now)
    assert cov.live == ["Walmart"] and cov.in_store == [("Market Basket", 2)]

def test_home_lists_coverage_and_zip_prompt(client, db):
    make_observed_offer(chain="Market Basket")
    body = client.get("/").content.decode()
    assert "Stores we cover" in body and "Market Basket" in body
    assert "Seen in stores near you" in body and "Set ZIP code" in body

def test_home_nearby_categories_with_zip(client, db, saved_zip):  # session with a known ZIP
    make_observed_offer(category="dairy/milk", near_zip=True)
    assert 'href="/c/dairy/milk/"' in client.get("/").content.decode().split("Seen in stores near you")[1]

def test_home_says_when_no_live_prices(client, db):
    assert "No live online prices yet" in client.get("/").content.decode()

def test_empty_category_says_coming(client, db, settings):
    settings.COMING_SOON = "Walmart (December)"
    assert "No live prices yet — coming: Walmart (December)." in client.get("/c/dairy/milk/").content.decode()
    settings.COMING_SOON = ""
    body = client.get("/c/dairy/milk/").content.decode()
    assert "No live prices yet." in body and "coming:" not in body

def test_filtered_empty_category_keeps_clear_filters(client, db, milk_offer):
    assert "Clear the filters" in client.get("/c/dairy/milk/?fat=skim").content.decode()
```

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/catalog/test_coverage.py tests/web/test_views_home.py tests/web/test_views_category.py -v` → FAIL

- [ ] **Step 3: Implement**

Home order: hero search (unchanged), "Seen in stores near you" (reuse `_location.html` for the ZIP prompt; with a known location, links to `nearby_categories` as "Milk · 12 prices"), "Browse categories" (unchanged), "Stores we cover" (`_coverage.html`: "Online, live prices: …" or "No live online prices yet — coming: {COMING_SOON}."; "In stores, shelf prices people recorded (Open Prices): Chain (N stores), …" with the ODbL notice line from the disclosure page), then the Task 4 notify block. `home` reads prefs with `get_prefs` (never creates a session). `_results.html`: in the "no ranked, no unranked, no active filters" branch, show the empty-category sentence from Global Constraints whether or not observed prices exist; the filtered branch is unchanged.

- [ ] **Step 4: Run tests** — `uv run pytest -q` → PASS
- [ ] **Step 5: Commit** — `feat: data-driven store coverage and honest empty states (#N)`

---

### Task 6: SEO — sitemap, meta description, canonical, noindex on utility pages

**Files:**
- Create: `web/seo.py`, `web/templates/web/sitemap.xml`
- Modify: `config/urls_public.py`, `web/templates/web/base.html`, `web/templates/web/{home,category,generic,disclosure,about,terms,settings}.html`, `waitlist/templates/waitlist/privacy.html`, M4 list/account base templates
- Test: `tests/web/test_seo.py`

**Interfaces:**
- Consumes: URL names `home`, `category`, `about`, `privacy`, `terms`, `disclosure` (Task 3).
- Produces: `sitemap_xml(request) -> HttpResponse` (`application/xml`), URL `/sitemap.xml`. Base-template blocks `description`, `canonical_path` (default `request.path`), `robots` (default empty).

- [ ] **Step 1: Write the failing tests**

```python
def test_sitemap_lists_public_pages(client, db, milk):
    r = client.get("/sitemap.xml", HTTP_HOST=PUBLIC)
    body = r.content.decode()
    assert r["Content-Type"].startswith("application/xml")
    for path in ("/", "/c/dairy/milk/", "/about", "/privacy", "/terms", "/disclosure"):
        assert f"<loc>https://unitpricer.com{path}</loc>" in body
    assert "/search" not in body and "beta." not in body

def test_canonical_drops_query(client, db, milk):
    body = client.get("/c/dairy/milk/?fat=2&sort=unit").content.decode()
    assert '<link rel="canonical" href="https://unitpricer.com/c/dairy/milk/">' in body

def test_pages_have_meta_description(client, db, milk):
    for path in ("/", "/c/dairy/milk/", "/about", "/terms", "/privacy"):
        assert '<meta name="description" content="' in client.get(path).content.decode()

@pytest.mark.parametrize("path", ["/search?q=milk", "/settings", "/lists/"])
def test_utility_pages_noindex(client, db, path):
    assert '<meta name="robots" content="noindex">' in client.get(path).content.decode()

@pytest.mark.parametrize("path", ["/", "/c/dairy/milk/", "/search?q=milk", "/about", "/terms", "/privacy", "/disclosure", "/sitemap.xml", "/robots.txt"])
def test_public_pages_set_no_cookies(client, db, milk, path):
    assert not client.get(path, HTTP_HOST=PUBLIC).cookies
```

- [ ] **Step 2: Run them to verify they fail** — `uv run pytest tests/web/test_seo.py -v` → FAIL

- [ ] **Step 3: Implement**

Sitemap URLs use `settings.PUBLIC_URL` (fallback `request.build_absolute_uri("/")` origin when unset, for dev), never the request host on beta. Category entries: every active `Category`, `lastmod` = date of its newest fresh offer when there is one. Canonical: `{PUBLIC_URL}{canonical_path}`, query always dropped (filter combinations canonicalize to the category page). Descriptions: home "Compare groceries and household goods by price per unit, ranked only by unit price. Free and open source."; category "Cheapest {name} by price per {comparison unit}, from {live retailers or "in-store prices"}."; others one fixed sentence each. `noindex` meta on search, generic results, settings, lists, account and feedback pages.

- [ ] **Step 4: Run tests** — `uv run pytest -q` → PASS
- [ ] **Step 5: Commit** — `feat: sitemap, canonical URLs and meta descriptions (#N)`

---

### Task 7 (only if the maintainer approves Sentry): error reporting with no PII

**Files:**
- Create: `config/sentry.py`
- Modify: `config/settings.py` (call `init_sentry()` at the end), `.env.example` (`SENTRY_DSN=`), `pyproject.toml`/`uv.lock` (`uv add sentry-sdk`), `docs/deploy.md`
- Test: `tests/deploy/test_sentry.py`

**Interfaces:**
- Produces: `init_sentry(dsn: str | None) -> bool` (no-op returning `False` when `dsn` is empty); `scrub_event(event: dict, hint: dict) -> dict` used as `before_send`.

- [ ] **Step 1: Write the failing tests**

```python
def test_no_dsn_no_init():
    assert init_sentry("") is False

def test_scrub_removes_pii():
    event = {"request": {"query_string": "q=my+address", "cookies": {"sessionid": "x"},
             "headers": {"Cookie": "x", "Cf-Connecting-Ip": "1.2.3.4", "User-Agent": "ua"},
             "data": {"email": "a@b.c"}, "url": "https://unitpricer.com/search?q=x"},
             "user": {"ip_address": "1.2.3.4"}}
    out = scrub_event(event, {})
    assert "query_string" not in out["request"] and "cookies" not in out["request"] and "data" not in out["request"]
    assert out["request"]["url"] == "https://unitpricer.com/search" and "user" not in out
    assert set(out["request"]["headers"]) <= {"User-Agent"}
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/deploy/test_sentry.py -v` → FAIL
- [ ] **Step 3: Implement** — `sentry_sdk.init(dsn, integrations=[DjangoIntegration()], send_default_pii=False, traces_sample_rate=0, before_send=scrub_event)`. Privacy page "Who handles it" gains "Sentry: error reports (no IP, email, cookies or search words)" only when this task ships.
- [ ] **Step 4: Run tests** — `uv run pytest -q` → PASS
- [ ] **Step 5: Commit** — `feat: optional Sentry error reporting without PII (#N)`

---

### Task 8: Launch runbook and docs; operational steps

**Files:**
- Create: `docs/launch.md`
- Modify: `docs/deploy.md` (topology paragraph, `app.env` values, purge and monitor sections), `docs/roadmap.md` (link this plan under M6; mark 6.3/6.4 partly done), `docs/superpowers/specs/2026-11-07-unitpricer-design.md` §12 and `docs/superpowers/specs/2026-11-08-open-search-design.md` §14.5 (apex now serves the app), `CLAUDE.md` (`purge_sample_data`, recordings need `DEBUG=1`), `.env.example` (`UNITPRICER_COMING_SOON`)
- Test: none (docs); verify with `uv run pytest -q` and `uv run python manage.py check --deploy` under the prod test env.

`docs/launch.md` has three ordered checklists. The controller runs the Cloudflare steps through the Cloudflare API (MCP), dashboard as fallback, and records each done item's date.

**A. Before DNS-visible launch (in order)**
- [ ] Deploy the branch to production with `app.env` changes: `UNITPRICER_SITE_URL=https://unitpricer.com` (E023), `UNITPRICER_COMING_SOON=Walmart (December)`; `UNITPRICER_PUBLIC_HOST`, `UNITPRICER_APP_HOST`, `DJANGO_ALLOWED_HOSTS`, tunnel ingress and the Access app unchanged.
- [ ] `docker compose run --rm -T web python manage.py purge_sample_data </dev/null` (dry run), check the counts, then again with `--apply`. Confirm `/c/dairy/milk/` on the apex shows no Walmart rows.
- [ ] Cloudflare (zone `unitpricer.com`): turn on Bot Fight Mode (`PUT /zones/{zone}/bot_management` `{"fight_mode": true}`); add the free-plan rate-limiting rule in phase `http_ratelimit`: expression `(http.host eq "unitpricer.com" and (starts_with(http.request.uri.path, "/search") or http.request.method eq "POST"))`, characteristics `ip.src` + `cf.colo.id`, `period 10`, `requests_per_period 30`, action `block`, `mitigation_timeout 10`. Then confirm: the uptime monitor still gets 200 from `/healthz` (Bot Fight Mode cannot be skipped on the free plan; if the monitor is challenged, switch monitor provider or turn Bot Fight Mode off and keep only the rate rule), and beta still prompts for Access.
- [ ] Monitors: an HTTP monitor on `https://unitpricer.com/healthz` every 5 minutes expecting `"status": "ok"`, alerting `dante@unitpricer.com`; a heartbeat check (e.g. Healthchecks.io, daily period, 2 h grace) whose ping URL goes in `BACKUP_HEARTBEAT_URL` in `/srv/unitpricer/.env`, then `docker compose up -d backup` and `docker compose exec backup backup.sh once` to see the first ping.
- [ ] Run the restore drill in `docs/deploy.md` and fill its table row.
- [ ] Smoke-check by hand on the apex: home, a category, a search, sitemap, robots, `/admin/` → 404, waitlist signup → confirm email → POST confirm; on beta: no-token request → Access login.

**B. Legal gates (maintainer-owned; the announcement waits on both)**
- [ ] ODbL: Open Food Facts has answered whether our Open Prices store plus our normalization is a Derivative Database and whether "free copy on request" satisfies §4.6 (email drafted separately). Act on the answer (e.g. publish the `export_open_prices` dump at a public URL) before announcing.
- [ ] CAN-SPAM: `UNITPRICER_POSTAL_ADDRESS` holds a real postal address (PO box or registered-agent address) in `app.env`.
- [ ] Terms reviewed; remove the draft banner in a separate PR.
- [ ] Then: `send_waitlist_announcement` (it already refuses without a postal address and URLs).
- [ ] After 7 clean days (no 5xx bursts, no Access or routing incidents): set `DJANGO_HSTS_SECONDS=31536000` in `app.env`, `docker compose up -d web`, check the `Strict-Transport-Security` header. Preload stays off.

**C. Applications after launch (in this order; each needs the live apex)**
1. [ ] eBay Developers Program account and eBay Partner Network (EPN) application.
2. [ ] Impact: Target Partners and Walmart affiliate (Walmart publisher ID → `WALMART_IMPACT_PUBLISHER_ID`; retry walmart.io key upload alongside).
3. [ ] CJ (Costco and other grocery/household advertisers).
4. [ ] Sovrn Commerce: register `unitpricer.com` as a site; site key → `SOVRN_SITE_KEY`; run the merchant audit (roadmap Checkpoint B.5).
5. [ ] Amazon Associates last: the 180-day window to make 3 qualifying sales starts at signup, so apply only once there is traffic; tag → `AMAZON_ASSOCIATES_TAG` (turns on the disclosure sentence); request written approval for unit-price comparison (spec §10).
After each approval, re-read that program's terms against spec §10 and the disclosure page before adding links.

- [ ] **Commit** — `docs: launch runbook, deploy and spec updates for the public apex (#N)`

---

## Decisions for the maintainer (defaults used above)

1. **Waitlist CSRF:** the home-page form has no CSRF token so browsing stays cookie-free; `signup` is `csrf_exempt` with an `Origin` same-host check (double opt-in, honeypot and rate limits unchanged). Alternative: link to a separate `/waitlist/` page that sets `csrftoken`.
2. **Waitlist purpose:** one list; existing confirmed subscribers get the launch announcement, new signups are for "new stores" news. Privacy wording changes accordingly; no new field.
3. **Admin on beta only** (404 on the apex).
4. **robots.txt on the apex** disallows `/search`, `/settings`, `/lists/`, `/account/`, `/waitlist/`, `/click`, and search/utility pages carry `noindex`, to keep crawlers from spending live-fetch budgets.
5. **About page name:** says the site is run by Dante Arcese (as on GitHub); confirm or give the wording.
6. **Rate rule numbers:** 30 requests per 10 s per IP on `/search` and POSTs, block 10 s (free-plan limits); Bot Fight Mode on.
7. **Sentry (Task 7):** skip unless approved.
8. **Monitor providers:** an HTTP uptime monitor plus Healthchecks.io for the backup heartbeat; any free provider is fine.
9. **Disclosure lists only configured programs** and shows the Amazon sentence only when `AMAZON_ASSOCIATES_TAG` is set.
