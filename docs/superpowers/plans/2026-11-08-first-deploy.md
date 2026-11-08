# First Deploy (Gated Beta + Public Waitlist) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** UnitPricer runs in production on one DigitalOcean droplet: `unitpricer.com` is a public, indexable landing page with a double-opt-in waitlist, `beta.unitpricer.com` serves the full app to an allow-list behind Cloudflare Access, and the database is backed up nightly off-box with a rehearsed restore.

**Architecture:** One Docker Compose stack (`web` gunicorn, `worker` `run_scheduler`, `db` postgres:17, `cloudflared`, `backup`) on an Ubuntu 24.04 droplet with no inbound ports except a firewalled SSH. Traffic reaches `web` only through a Cloudflare Tunnel. A host-routing middleware gives the apex host a separate, tiny URLconf (landing, waitlist, privacy, health), so every app path is a 404 there. Images are built and smoke-checked in GitHub Actions and pushed to GHCR; `deploy/deploy.sh` pulls a CI-verified tag, migrates and restarts. Cloudflare, Resend, R2 and droplet setup are operational tasks run by the controller through APIs (Cloudflare MCP, `doctl`, Resend API), with the dashboard as fallback.

**Tech Stack:** Django 5.2, gunicorn, WhiteNoise, PyJWT (Access JWT check), Postgres 17, Docker Compose, GitHub Actions + GHCR, Cloudflare Tunnel / Access / DNS / R2, Resend over SMTP (Django's SMTP backend), rclone (backup image only), `doctl`, cloud-init, pytest-django.

**Spec:** `docs/superpowers/specs/2026-11-07-unitpricer-design.md` (§3 Principles, §10 terms, §12 Hosting, §13 Observability), `docs/research/2026-11-07-hosting-options.md` (recommended option and its 10 setup steps), issue #39 (production readiness), issue #48 (this plan's decisions: host, topology, waitlist). Email and rate-limit names are shared with the M4 plan (`docs/superpowers/plans/2026-11-08-m4-lists-accounts.md` on branch `issue-37-m4-plan`, Task 6); see "Hand-off to M4" at the end.

## Global Constraints

- Python with `uv` only (`uv add`, `uv sync`, `uv run`). No AI/LLM libraries or calls anywhere. License stays AGPL-3.0-or-later; both hosts link to the source code (AGPL §13).
- New dependencies allowed by this plan only: `gunicorn`, `whitenoise`, `pyjwt` (plus `sentry-sdk` only if the Sentry decision is "yes"). No rate-limit, waitlist or email-provider packages.
- **Secrets:** never in the repo, image, workflow logs or chat. Local secrets stay in `.env` (never read; used only via `uv run --env-file .env`). Server secrets live in `/srv/unitpricer/.env` (compose interpolation) and `/srv/unitpricer/app.env` (Django), both mode `600`, owner `deploy`. New variable names go into `.env.example`, blank.
- **Hosts (exact):** public `unitpricer.com`; app `beta.unitpricer.com`; `www.unitpricer.com` 301s to `https://unitpricer.com` with path and query kept. `DJANGO_ALLOWED_HOSTS=unitpricer.com,www.unitpricer.com,beta.unitpricer.com`.
- **Apex serves only:** `/`, `/waitlist/`, `/waitlist/confirm/<token>`, `/waitlist/unsubscribe/<token>`, `/privacy`, `/healthz`, `/robots.txt`, and `/static/…`. Everything else on the apex host is 404. Apex is indexable; every response on the app host carries `X-Robots-Tag: noindex, nofollow`.
- **Email:** Django SMTP backend to `smtp.resend.com`, port **2587** (STARTTLS; DigitalOcean blocks outbound 25/465/587 on new droplets), user `resend`, password = Resend API key. From `UnitPricer <hello@unitpricer.com>`. Every waitlist email carries an unsubscribe link plus `List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers. Plain text only.
- **Send budget:** at most `UNITPRICER_DAILY_EMAIL_CAP` (default **100**, Resend free tier) emails in any rolling 24 h across all kinds; announcements leave **20** of that for confirmations.
- **Waitlist stores only** the email (lowercased), `created_at`, `confirm_sent_at`, `confirmed_at`, `announced_at`. No IP, user agent or name. Unsubscribing deletes the row. Unconfirmed rows are deleted after **7 days** (the confirm-token lifetime).
- **Scanner safety:** GET never changes state. Confirm and unsubscribe links render a page with a POST button; only POST acts. No response reveals whether an address is already on the list.
- **Client IP** is `CF-Connecting-IP` when `UNITPRICER_TRUST_CF_IP=1` (production: only `cloudflared` can reach `web`), else `REMOTE_ADDR`. IPs are used only as hashed rate-limit keys, never stored or logged (gunicorn access log has no IP, no user agent, no query string).
- **Droplet:** Ubuntu 24.04 LTS (`ubuntu-24-04-x64`), `s-1vcpu-2gb`, region `nyc3`, weekly provider backups, monitoring agent on, SSH key-only, root login off, `ufw` deny-incoming except OpenSSH, unattended-upgrades with automatic reboot at **08:00 UTC**, Docker log rotation `max-size 10m`, `max-file 5`, 1 GB swap. No published container ports.
- **Backups:** nightly `pg_dump -Fc` at **03:30 UTC** to R2 bucket `unitpricer-backups`, prefix `pg/`, deleted by an R2 lifecycle rule after **30 days**. Provider weekly backups are the second layer.
- Tests never touch the network. Each code task ends with `uv run pytest -q` green and pre-commit passing. Commit subjects end with `(#39)` for Tasks 1, 2, 7, 8, 9 (production readiness) and with the deploy implementation issue number for the others (open that issue before Task 1, as M3 did with `(#32)`).

## Review Focus

1. **Apex leaks an app path.** Any app URL (category, search, click, settings, disclosure, admin, HTMX partials) requested with `Host: unitpricer.com` must 404, and app templates must never be rendered there (they reverse app-only URL names). Owned by Task 2 (`test_every_app_route_404s_on_apex`).
2. **Mail scanners act on links.** A GET of a confirm or unsubscribe link (Outlook Safe Links, Gmail previews) must not confirm or delete; a POST does; the one-click `List-Unsubscribe-Post` POST works without a CSRF token. Owned by Task 5 (`test_confirm_get_does_not_confirm`, `test_one_click_unsubscribe_without_csrf`).
3. **Every visitor shares one IP behind the tunnel.** `REMOTE_ADDR` is the `cloudflared` container, so a per-IP limit keyed on it throttles everyone at once. Two visitors with different `CF-Connecting-IP` values must have independent limits. Owned by Task 2 (`test_client_ip_prefers_cf_header_when_trusted`) and Task 5 (`test_signup_ip_limit_is_per_visitor`).
4. **Announcements starve confirmations or double-send.** A launch batch must leave 20 sends for confirmations, and a rerun (or a crash mid-batch) must never email anyone twice. Owned by Task 6 (`test_announce_leaves_confirm_reserve`, `test_announce_rerun_sends_no_duplicates`).
5. **Container healthcheck trips over the security settings.** The in-container check is plain HTTP with `Host: unitpricer.com`; with `SECURE_SSL_REDIRECT` on it must still get 200, not 301, or `deploy.sh` reports a healthy deploy as failed. Owned by Task 2 (`test_healthz_plain_http_not_redirected`).

---

## File Structure

```
config/settings.py                    # prod security, hosts, logging, email, cache, WhiteNoise, Access
config/urls.py                        # + healthz, robots.txt, privacy
config/urls_public.py                 # apex URLconf (new)
web/hosts.py                          # HostRoutingMiddleware (new)
web/net.py                            # client_ip() (new)
web/ratelimit.py                      # allow() — M4 reuses it (new)
web/access.py                         # CloudflareAccessMiddleware (new)
web/health.py                         # healthz, robots_txt views (new)
web/checks.py                         # unitpricer.E020/E021 deploy checks (new)
web/templates/404.html
waitlist/                             # new app
  apps.py models.py (+ migrations) tokens.py mail.py views.py urls.py
  management/commands/send_waitlist_announcement.py
  templates/waitlist/  base_public.html landing.html check_inbox.html busy.html
                       confirm.html confirmed.html unsubscribe.html unsubscribed.html
                       link_invalid.html privacy.html
  templates/waitlist/email/  confirm.txt announce.txt
catalog/management/commands/run_scheduler.py   # no duplicate log lines
Dockerfile                            # gunicorn, collectstatic, non-root
deploy/gunicorn.conf.py
deploy/cloud-init.yaml
deploy/deploy.sh
deploy/backup/Dockerfile  deploy/backup/backup.sh
compose.prod.yaml
.github/workflows/ci.yml              # + image job
.env.example, README.md, CLAUDE.md, docs/deploy.md (runbook), docs/roadmap.md, spec §12/§15/§16
tests/deploy/  test_prod_settings.py test_hosts.py test_access.py test_image.py
               test_compose_prod.py test_cloud_init.py test_deploy_script.py test_backup_script.py
tests/waitlist/  test_models_tokens.py test_mail_budget.py test_views.py test_announce.py
```

Tasks 1–10 are code (TDD, merged to `main` before any server work). Tasks 11–15 are operational: no product code, each ends with a verification. Tasks 11 and 12 can start at any time (Resend domain verification takes a while); 13–15 need Tasks 1–10 merged and the image job green.

---

### Task 1: Production settings, logging and `check --deploy` (#39)

**Files:**
- Modify: `config/settings.py`, `catalog/management/commands/run_scheduler.py`, `.env.example`
- Create: `web/checks.py` (registered in `web/apps.py` `ready()`)
- Test: `tests/deploy/__init__.py`, `tests/deploy/test_prod_settings.py`

**Interfaces:**
- Produces settings (env name → setting, default in parentheses):
  - `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`; `SECURE_SSL_REDIRECT = not DEBUG`; `SECURE_REDIRECT_EXEMPT = [r"^healthz$"]`
  - `DJANGO_HSTS_SECONDS` → `SECURE_HSTS_SECONDS` (`3600` when not DEBUG, `0` in DEBUG); `SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG`; `SECURE_HSTS_PRELOAD = False` with `SILENCED_SYSTEM_CHECKS = ["security.W021"]` (preload is a separate, later decision)
  - `SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = not DEBUG`; `SESSION_COOKIE_HTTPONLY = True`; `SECURE_REFERRER_POLICY = "same-origin"`; `X_FRAME_OPTIONS = "DENY"`
  - `DJANGO_CSRF_TRUSTED_ORIGINS` (comma list) → `CSRF_TRUSTED_ORIGINS` (default: `https://<h>` for each `ALLOWED_HOSTS` entry)
  - `UNITPRICER_PUBLIC_HOST` → `PUBLIC_HOST` (`""` = no host routing), `UNITPRICER_APP_HOST` → `APP_HOST` (`""`), `UNITPRICER_PUBLIC_URL` → `PUBLIC_URL` (origin for waitlist links), `UNITPRICER_SITE_URL` → `SITE_URL` (M4's name: origin for app links), `UNITPRICER_TRUST_CF_IP` → `TRUST_CF_IP` (`"1"` → True)
  - Email, with M4's names: `DJANGO_EMAIL_BACKEND` (console when DEBUG else `django.core.mail.backends.smtp.EmailBackend`), `DJANGO_EMAIL_HOST` (`localhost`), `DJANGO_EMAIL_PORT` (`587`), `DJANGO_EMAIL_HOST_USER` (`""`), `DJANGO_EMAIL_HOST_PASSWORD` falling back to `RESEND_API_KEY` (`""`), `DJANGO_EMAIL_USE_TLS` (`"1"`), `EMAIL_TIMEOUT = 20`, `DJANGO_DEFAULT_FROM_EMAIL` (`UnitPricer <hello@unitpricer.com>`), `SERVER_EMAIL = DEFAULT_FROM_EMAIL`
  - `CACHES` = M4's `DatabaseCache`, `LOCATION "unitpricer_cache"` (needs `createcachetable` on deploy)
  - `LOGGING`: one `StreamHandler` to stdout, format `"%(asctime)s %(levelname)s %(name)s %(message)s"`, root level `DJANGO_LOG_LEVEL` (`INFO`); `django.request` at `ERROR` propagates to root; `django.security.DisallowedHost` at `CRITICAL` (bots probing the tunnel)
- `web/checks.py` (deploy-tagged, only when `DEBUG` is off): `unitpricer.E020` if `PUBLIC_HOST` or `APP_HOST` is unset or missing from `ALLOWED_HOSTS`; `unitpricer.E021` if `PUBLIC_URL` is not `https://` + `PUBLIC_HOST`.
- `run_scheduler` attaches its own handler only when `scheduler.logger.hasHandlers()` is False, so production prints each line once.

- [ ] **Step 1: Write failing tests** (`tests/deploy/test_prod_settings.py`; prod settings are exercised in a subprocess like `tests/catalog/test_settings.py`, with a fixture `PROD_ENV` = `DJANGO_DEBUG=0`, a 50-char random `DJANGO_SECRET_KEY`, the exact hosts from Global Constraints, `UNITPRICER_PUBLIC_URL=https://unitpricer.com`, `UNITPRICER_SITE_URL=https://beta.unitpricer.com`, `DATABASE_URL=postgres://x:y@localhost:5432/z`):
  - `test_check_deploy_clean`: `python manage.py check --deploy --fail-level WARNING` exits 0 and prints "System check identified no issues".
  - `test_check_deploy_flags_missing_hosts`: same without `UNITPRICER_PUBLIC_HOST` → non-zero, output contains `unitpricer.E020`.
  - `test_prod_cookie_and_proxy_settings`: printed values: `SESSION_COOKIE_SECURE True`, `CSRF_COOKIE_SECURE True`, `SECURE_PROXY_SSL_HEADER ('HTTP_X_FORWARDED_PROTO', 'https')`, `SECURE_HSTS_SECONDS 3600`, `CSRF_TRUSTED_ORIGINS` contains `https://beta.unitpricer.com`.
  - `test_smtp_settings_from_env`: with `DJANGO_EMAIL_HOST=smtp.resend.com`, `DJANGO_EMAIL_PORT=2587`, `DJANGO_EMAIL_HOST_USER=resend`, `RESEND_API_KEY=re_test` → `EMAIL_BACKEND` is the SMTP backend, port `2587`, password `re_test`, `EMAIL_USE_TLS True`.
  - `test_console_email_backend_in_debug` (in-process; tests run with `DEBUG=1`).
  - `test_logging_goes_to_stdout`: subprocess logs `logging.getLogger("catalog.scheduler").info("hi")` after `django.setup()`; stdout contains `INFO catalog.scheduler hi` exactly once.
  - `test_run_scheduler_logs_once` (`call_command("run_scheduler", "--once")` with `capsys`, no connectors): each scheduler line appears once.
- [ ] **Step 2: Run** `uv run pytest tests/deploy/test_prod_settings.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the settings above; add every new env name to `.env.example` (blank) under "Django / unitpricer", with a comment that production email uses port 2587.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: production security, logging and email settings (#39)`

---

### Task 2: Host routing, health endpoint, robots, client IP (#39)

**Files:**
- Create: `web/hosts.py`, `web/net.py`, `web/ratelimit.py`, `web/health.py`, `config/urls_public.py`, `web/templates/404.html`
- Modify: `config/settings.py` (`MIDDLEWARE`: `web.hosts.HostRoutingMiddleware` right after `SecurityMiddleware`, before `CommonMiddleware`), `config/urls.py` (`healthz`, `robots.txt`)
- Test: `tests/deploy/test_hosts.py`

**Interfaces:**
- `web/hosts.py` `HostRoutingMiddleware`: when `PUBLIC_HOST` is empty, does nothing. Else, by `request.get_host()` without port: `www.<PUBLIC_HOST>` → `HttpResponsePermanentRedirect(f"https://{PUBLIC_HOST}{request.get_full_path()}")`; `PUBLIC_HOST` → `request.urlconf = "config.urls_public"`; anything else (app host, `localhost`) → default URLconf, and the response gets `X-Robots-Tag: noindex, nofollow` when the host is `APP_HOST`.
- `config/urls_public.py`: `""` (name `landing`), `privacy` (name `privacy`), `healthz`, `robots.txt`. Task 2 points `landing` and `privacy` at `web.health.placeholder` (200, "UnitPricer"); Task 5 swaps in `waitlist.views.landing` / `waitlist.views.privacy` and adds `include("waitlist.urls")` at `waitlist/` (namespace `waitlist`).
- `web/health.py`: `healthz(request) -> JsonResponse`: `{"status": "ok", "last_ingestion": <iso of newest IngestionRun.created_at or null>}` with 200 after `SELECT 1`; `{"status": "db_error"}` with 503 when the query raises `DatabaseError`. `Cache-Control: no-store`. `robots_txt(request)`: on `PUBLIC_HOST` `"User-agent: *\nAllow: /\n"`, otherwise `"User-agent: *\nDisallow: /\n"`; `text/plain`.
- `web/net.py`: `client_ip(request) -> str`: `request.META["HTTP_CF_CONNECTING_IP"]` (stripped) when `settings.TRUST_CF_IP` and present, else `REMOTE_ADDR`.
- `web/ratelimit.py`: `allow(scope: str, key: str, limit: int, window: timedelta) -> bool`, exactly M4 Task 6's contract (cache key `f"rl:{scope}:{sha256(SECRET_KEY + key)}"`, `cache.add` then `cache.incr`, False once the count exceeds `limit`).
- `404.html` extends nothing app-specific (plain HTML, "Page not found", link to `/`), so it renders under either URLconf.

- [ ] **Step 1: Write failing tests** (`settings` overridden to `PUBLIC_HOST="unitpricer.com"`, `APP_HOST="beta.unitpricer.com"`, matching `ALLOWED_HOSTS`):
  - `test_every_app_route_404s_on_apex` (Review Focus 1): parametrized over `/c/dairy/milk/`, `/search?q=milk`, `/settings`, `/disclosure`, `/admin/`, `/admin/login/`, `/click` (POST) → 404 with `HTTP_HOST="unitpricer.com"`; the 404 body contains "Page not found".
  - `test_apex_serves_public_routes`: `/`, `/privacy`, `/healthz`, `/robots.txt` → 200 on apex; `/robots.txt` body has `Allow: /`; no `X-Robots-Tag`.
  - `test_app_host_full_app_noindex`: `/` and `/disclosure` on `beta.unitpricer.com` → 200 with `X-Robots-Tag: noindex, nofollow`; robots says `Disallow: /`.
  - `test_www_redirects_to_apex`: `GET /privacy?x=1` on `www.unitpricer.com` → 301 to `https://unitpricer.com/privacy?x=1`.
  - `test_unknown_host_400`: `Host: evil.example` → 400.
  - `test_no_routing_when_public_host_unset`: `PUBLIC_HOST=""` and `testserver` → app home 200.
  - `test_healthz_plain_http_not_redirected` (Review Focus 5): `SECURE_SSL_REDIRECT=True`, plain-HTTP `GET /healthz` on apex → 200 JSON `status == "ok"`, while `GET /privacy` → 301 to https.
  - `test_healthz_db_error_503`: monkeypatch the cursor to raise `DatabaseError` → 503.
  - `test_client_ip_prefers_cf_header_when_trusted` (Review Focus 3): `TRUST_CF_IP=True` + `HTTP_CF_CONNECTING_IP="203.0.113.7"` → `"203.0.113.7"`; `TRUST_CF_IP=False` → `REMOTE_ADDR`.
  - `test_ratelimit_window`: 3 allowed then False for limit 3; a different key is independent.
- [ ] **Step 2: Run** `uv run pytest tests/deploy/test_hosts.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The middleware strips the port before comparing; `get_host()` already enforces `ALLOWED_HOSTS`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: per-host routing, health endpoint and robots (#39)`

---

### Task 3: Cloudflare Access JWT check on the app host

Defense in depth: Access already blocks `beta` at the edge; this rejects any request that reaches `web` for the app host without a valid Access token (misrouted tunnel ingress, Access app deleted by mistake).

**Files:**
- Create: `web/access.py`
- Modify: `config/settings.py` (`MIDDLEWARE` after `HostRoutingMiddleware`; `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` from env vars of the same names), `.env.example`, `pyproject.toml`/`uv.lock` (`uv add pyjwt`)
- Test: `tests/deploy/test_access.py`

**Interfaces:**
- `web/access.py`: `CloudflareAccessMiddleware`. Active only when `CF_ACCESS_AUD` is set and the host is `APP_HOST`; `/healthz` is exempt. Reads `Cf-Access-Jwt-Assertion`, verifies with `jwt.decode(token, key, algorithms=["RS256"], audience=CF_ACCESS_AUD, issuer=f"https://{CF_ACCESS_TEAM_DOMAIN}")`, keys from a module-level `jwt.PyJWKClient(f"https://{CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)` built lazily. Missing or invalid token → `HttpResponseForbidden("Access required")`. The verified `email` claim is put on `request.access_email` (not stored).
- `web/checks.py` gains `unitpricer.E022` (deploy): `APP_HOST` set but `CF_ACCESS_AUD` or `CF_ACCESS_TEAM_DOMAIN` unset. Add `CF_ACCESS_TEAM_DOMAIN=unitpricer.cloudflareaccess.com` and `CF_ACCESS_AUD=test-aud` to Task 1's `PROD_ENV` so `test_check_deploy_clean` stays green.

- [ ] **Step 1: Write failing tests** (generate an RSA key in the test with `cryptography`; monkeypatch `web.access._jwks_client` with a stub whose `get_signing_key_from_jwt` returns the public key):
  - `test_valid_token_passes` → 200 on `beta.unitpricer.com/`; `request.access_email == "me@example.com"`.
  - `test_missing_token_403`, `test_wrong_audience_403`, `test_expired_token_403`, `test_wrong_issuer_403`.
  - `test_apex_and_healthz_not_checked`: apex `/` and beta `/healthz` → 200 with no header.
  - `test_inactive_without_aud`: `CF_ACCESS_AUD=""` → beta `/` 200 with no header (local dev).
- [ ] **Step 2: Run** `uv run pytest tests/deploy/test_access.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: verify Cloudflare Access tokens on the app host`

---

### Task 4: Waitlist model, tokens and budgeted mail

**Files:**
- Create: `waitlist/__init__.py`, `apps.py`, `models.py`, `migrations/0001_initial.py`, `tokens.py`, `mail.py`, `templates/waitlist/email/confirm.txt`
- Modify: `config/settings.py` (`"waitlist"` in `INSTALLED_APPS`; `UNITPRICER_DAILY_EMAIL_CAP` → `DAILY_EMAIL_CAP` default `100`; `WAITLIST_CONFIRM_RESERVE = 20`; `WAITLIST_CONFIRM_MAX_AGE = timedelta(days=7)`), `.env.example`
- Test: `tests/waitlist/__init__.py`, `tests/waitlist/conftest.py` (autouse `cache.clear()`), `tests/waitlist/test_models_tokens.py`, `tests/waitlist/test_mail_budget.py`

**Interfaces:**
- `waitlist/models.py`:
  - `Subscriber(models.Model)`: `email EmailField(max_length=254, unique=True)`, `created_at DateTimeField(auto_now_add=True, db_index=True)`, `confirm_sent_at DateTimeField(null=True)`, `confirmed_at DateTimeField(null=True, db_index=True)`, `announced_at DateTimeField(null=True)`. `purge_unconfirmed(now) -> int` (classmethod): deletes rows with `confirmed_at` null and `created_at < now - WAITLIST_CONFIRM_MAX_AGE`, returns the count.
  - `EmailSend(models.Model)`: `sent_at DateTimeField(db_index=True)`, `kind CharField(16)` (`"confirm"`, `"announce"`; M4 adds `"magic"`). No address. Rows older than 2 days are deleted by `record_send`.
- `waitlist/tokens.py` (Django `signing`, salts `"waitlist.confirm"` / `"waitlist.unsubscribe"`, payload `{"id": pk, "e": email}`):
  - `confirm_token(sub: Subscriber) -> str`; `read_confirm_token(token: str) -> Subscriber | None` (`max_age=WAITLIST_CONFIRM_MAX_AGE`; bad signature, expired, missing row, or email mismatch → None)
  - `unsubscribe_token(sub: Subscriber) -> str`; `read_unsubscribe_token(token: str) -> tuple[bool, Subscriber | None]` (no expiry; first item is "signature valid", so a valid token for an already-deleted row can still say "you're unsubscribed")
- `waitlist/mail.py`:
  - `sends_in_last_day(now) -> int`
  - `send_budgeted(message: EmailMessage, kind: str, now: datetime, reserve: int = 0, connection=None) -> bool`: sends only when `sends_in_last_day(now) < DAILY_EMAIL_CAP - reserve`, then records an `EmailSend`; returns False (logs a warning, sends nothing) when over budget. SMTP exceptions propagate.
  - `waitlist_message(sub: Subscriber, subject: str, template: str, context: dict) -> EmailMessage`: renders the plain-text template with `context | {"unsubscribe_url": f"{PUBLIC_URL}/waitlist/unsubscribe/{unsubscribe_token(sub)}"}`, `to=[sub.email]`, headers `List-Unsubscribe: <unsubscribe_url>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. With `DEBUG` and no `PUBLIC_URL`, the origin is `http://localhost:8000`.
  - `send_confirmation(sub: Subscriber, now: datetime) -> bool`: subject **"Confirm your UnitPricer waitlist signup"**; body from `confirm.txt`: the confirm URL `{PUBLIC_URL}/waitlist/confirm/<token>`, "The link expires in 7 days.", "If you didn't sign up, ignore this email and you won't hear from us again.", then the unsubscribe line. Sets `confirm_sent_at` on success.

- [ ] **Step 1: Write failing tests:**
  - `test_models_tokens.py`: `test_email_unique`; `test_confirm_token_round_trip`; `test_confirm_token_expires` (monkeypatch `django.core.signing.time.time` to T + 7 days + 1 s before reading) → None; `test_confirm_token_rejects_other_salt` (an unsubscribe token is not a confirm token); `test_tampered_token_none`; `test_unsubscribe_token_for_deleted_row` → `(True, None)`; `test_purge_unconfirmed` (8-day-old pending deleted; 8-day-old confirmed and 6-day-old pending kept).
  - `test_mail_budget.py` (`settings.DAILY_EMAIL_CAP = 5`): `test_send_within_budget` (sends, `EmailSend` recorded, `mail.outbox` has 1); `test_over_budget_sends_nothing` (5 recorded in the last 24 h → False, outbox empty); `test_reserve_applies` (3 recorded, `reserve=2` → False; `reserve=0` → True); `test_old_sends_dont_count` (sends 25 h old don't count); `test_confirmation_email_content`: subject exact, body contains `https://unitpricer.com/waitlist/confirm/` and `/waitlist/unsubscribe/`, headers `List-Unsubscribe` and `List-Unsubscribe-Post` exact, `from_email == "UnitPricer <hello@unitpricer.com>"`, no HTML alternative.
- [ ] **Step 2: Run** `uv run pytest tests/waitlist -v`. Expected: FAIL.
- [ ] **Step 3: Implement** (`uv run python manage.py makemigrations waitlist`).
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: waitlist model, signed tokens and budgeted mail`

---

### Task 5: Landing page, signup, confirm, unsubscribe, privacy

**Files:**
- Create: `waitlist/views.py`, `waitlist/urls.py`, `waitlist/templates/waitlist/{base_public,landing,check_inbox,busy,confirm,confirmed,unsubscribe,unsubscribed,link_invalid,privacy}.html`, `waitlist/static/waitlist/landing.css` (or reuse `web/app.css` tokens)
- Modify: `config/urls_public.py` (real views replace Task 2's placeholders), `config/urls.py` (`privacy` also on the app host), `web/templates/web/base.html` footer (Privacy, Source code links)
- Test: `tests/waitlist/test_views.py`

**Interfaces:**
- `waitlist/urls.py` (`app_name = "waitlist"`): `""` → `signup` (POST only, name `signup`), `confirm/<str:token>` → `confirm` (GET page, POST acts), `unsubscribe/<str:token>` → `unsubscribe` (GET page, POST acts, `@csrf_exempt` on POST).
- `landing(request)`: GET renders `landing.html`: `<h1>UnitPricer</h1>`, tagline "Fair price per unit, by spec, not by brand.", 2–3 sentences from the README's "Why" (spec, honest ranking, never guesses, no AI, free and open source), the form (`POST /waitlist/`, `<input type="email" name="email" required autocomplete="email">`, honeypot `<input name="website">` inside a `hidden`-by-CSS block with `aria-hidden="true"` and `tabindex="-1"`, CSRF token, button "Join the waitlist"), the line "We'll email you once to confirm, and again when UnitPricer opens. Nothing else." and a link to `/privacy`. `base_public.html` (not `web/base.html`, which reverses app-only URLs) has `<meta name="description">`, canonical `https://unitpricer.com/`, and a footer: Privacy · Source code (`https://github.com/DanteArcese/unitpricer`, "AGPL-3.0") · `hello@unitpricer.com`. No external assets.
- `signup(request)` order: (1) over `allow("waitlist-ip", client_ip(request), 5, timedelta(hours=1))` → **429** "Too many requests. Try again later."; (2) honeypot non-empty → `check_inbox.html`, nothing stored or sent; (3) email normalized (strip, lowercase, `EmailValidator`, ≤ 254) else re-render landing with "Enter a valid email address." (200); (4) `Subscriber.purge_unconfirmed(now)`; (5) existing confirmed row → `check_inbox.html`, no mail; (6) pending row with `confirm_sent_at` < 10 minutes ago, or `allow("waitlist-email", email, 3, timedelta(days=1))` False → `check_inbox.html`, no mail; (7) otherwise get-or-create the row and `send_confirmation`; if it returns False (budget) and the row is new, delete it and render `busy.html` (**503**, "We can't send confirmation emails right now. Please try again tomorrow."). `check_inbox.html` body is identical for every branch: "Check your inbox. If that address isn't on the list yet, we've sent a link to confirm it."
- `confirm`: invalid token → `link_invalid.html` (200, "This link has expired or isn't valid." + link to `/`). GET → `confirm.html` with a POST form "Confirm my signup". POST → set `confirmed_at` if null (idempotent) → `confirmed.html` ("You're on the list. We'll email you when UnitPricer opens.").
- `unsubscribe`: signature invalid → `link_invalid.html` (404). GET → `unsubscribe.html` with a POST button. POST → delete the row if present → `unsubscribed.html` ("You're unsubscribed and your address has been deleted."), 200 for both present and already-deleted rows.
- `privacy(request)`: `privacy.html` extends `base_public.html` (also served on the app host). Sections, exact headings: "What we collect" (waitlist: email address and signup/confirm/announcement times; beta app: session and settings cookies; no analytics, no ads, no tracking pixels); "Why" (confirm the signup, one launch email); "Who handles it" (DigitalOcean, New York: hosting; Cloudflare: network, security and beta access control; Resend: email delivery; R2: encrypted backups kept 30 days; plus Sentry if enabled); "Cookies" (`csrftoken` on forms only; Cloudflare security cookies; beta: `CF_Authorization`, `sessionid`); "How long" (unconfirmed: deleted after 7 days; confirmed: until you unsubscribe or we close the waitlist; backups age out after 30 days; server logs hold no IP addresses); "Your choices" (unsubscribe link in every email, or email `hello@unitpricer.com` to see or delete your data); "Changes" with "Last updated: <date of deploy>". Final wording is reviewed by the maintainer before Task 15 (legal review proper is roadmap 6.3).

- [ ] **Step 1: Write failing tests** (apex host settings as in Task 2, `PUBLIC_URL="https://unitpricer.com"`):
  - `test_landing_has_form_and_no_external_assets`: 200, form posts to `/waitlist/`, honeypot present, no `http(s)://` in `src=`/`href=` except the GitHub link.
  - `test_signup_creates_pending_and_sends_confirm`: one row, `confirmed_at` None, one mail to the lowercased address.
  - `test_signup_response_identical_for_new_pending_confirmed` (no enumeration): status and body bytes equal for a new, a pending (resend window passed) and a confirmed address.
  - `test_honeypot_stores_and_sends_nothing`.
  - `test_resend_throttled`: a second signup within 10 minutes sends no second mail; 4th in a day sends none.
  - `test_signup_ip_limit_is_per_visitor` (Review Focus 3): `TRUST_CF_IP=True`; 5 signups with `CF-Connecting-IP: 203.0.113.1` pass, the 6th → 429; a request from `203.0.113.2` still passes.
  - `test_budget_exhausted_503_and_no_row`.
  - `test_confirm_get_does_not_confirm` (Review Focus 2) and `test_confirm_post_confirms_idempotent`.
  - `test_expired_confirm_link_invalid`.
  - `test_one_click_unsubscribe_without_csrf` (Review Focus 2): `Client(enforce_csrf_checks=True)` POST with body `List-Unsubscribe=One-Click` → 200, row deleted; GET does not delete.
  - `test_unsubscribe_twice_ok`; `test_bad_unsubscribe_signature_404`.
  - `test_signup_requires_csrf` (`enforce_csrf_checks=True` POST without token → 403).
  - `test_privacy_on_both_hosts`: 200 on apex and app host; contains "hello@unitpricer.com" and "Resend".
- [ ] **Step 2: Run** `uv run pytest tests/waitlist/test_views.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Mobile-first, same CSS conventions as M3 (16px base, ≥ 44px tap targets, dark mode).
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: public landing page and double-opt-in waitlist`

---

### Task 6: Launch-announcement command

**Files:**
- Create: `waitlist/management/__init__.py`, `waitlist/management/commands/__init__.py`, `waitlist/management/commands/send_waitlist_announcement.py`, `waitlist/templates/waitlist/email/announce.txt`
- Modify: `config/settings.py` (`UNITPRICER_POSTAL_ADDRESS` → `POSTAL_ADDRESS`, default `""`), `.env.example`
- Test: `tests/waitlist/test_announce.py`

**Interfaces:**
- `manage.py send_waitlist_announcement --subject TEXT [--template waitlist/email/announce.txt] [--limit N] [--pause 0.6] [--dry-run]`:
  - Refuses (`CommandError`) when `POSTAL_ADDRESS` is empty (CAN-SPAM: commercial email needs a postal address) or `PUBLIC_URL` is unset with `DEBUG` off.
  - Purges unconfirmed rows, then selects `confirmed_at` not null and `announced_at` null, ordered by `confirmed_at, id`.
  - Sends through one opened `get_connection()`, each via `send_budgeted(..., kind="announce", reserve=WAITLIST_CONFIRM_RESERVE)`, stopping at `--limit` (default: no limit beyond the budget) or the first False. Sets `announced_at` on each row immediately after its send succeeds (crash-safe rerun). Sleeps `--pause` seconds between sends (Resend's 2 requests/s limit).
  - On an SMTP exception: stops, prints the count sent so far, exits non-zero.
  - Prints `sent N, remaining M` and, when M > 0, `budget reached; run again tomorrow`.
  - `--dry-run` prints the counts and the first rendered message, sends and records nothing.
- `announce.txt` context: `site_url` (`https://beta.unitpricer.com` until the public launch, then the apex), `unsubscribe_url`, `postal_address`. Footer exact: "You're receiving this because you joined the UnitPricer waitlist at unitpricer.com." / "Unsubscribe: {{ unsubscribe_url }}" / postal address. Body copy is placeholder text ("UnitPricer is open.") the maintainer rewrites at launch.

- [ ] **Step 1: Write failing tests** (`settings.DAILY_EMAIL_CAP = 10`, `WAITLIST_CONFIRM_RESERVE = 3`, `POSTAL_ADDRESS = "PO Box 1, Boston MA"`, `--pause 0`):
  - `test_announce_leaves_confirm_reserve` (Review Focus 4): 12 confirmed → 7 sent; `sent 7, remaining 5`; a confirmation still sends afterwards; an 11th send of any kind does not.
  - `test_announce_rerun_sends_no_duplicates` (Review Focus 4): run, then (next day: age the `EmailSend` rows by 25 h) run again → the 5 remaining get mail, nobody gets two (`Counter` of recipients).
  - `test_announce_skips_pending_and_unsubscribed`.
  - `test_announce_requires_postal_address` → `CommandError`.
  - `test_smtp_failure_stops_and_marks_sent_ones`: a fake connection raising on the 3rd send → 2 rows have `announced_at`, exit non-zero.
  - `test_dry_run_sends_nothing`.
  - `test_announce_email_footer`: body contains the exact footer lines and the postal address; headers carry `List-Unsubscribe`.
- [ ] **Step 2: Run** `uv run pytest tests/waitlist/test_announce.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: budgeted waitlist launch announcement`

---

### Task 7: Production image and GHCR build (#39)

**Files:**
- Modify: `Dockerfile`, `config/settings.py` (`whitenoise.middleware.WhiteNoiseMiddleware` right after `SecurityMiddleware`; `STORAGES["staticfiles"]` = `whitenoise.storage.CompressedManifestStaticFilesStorage` when not DEBUG, Django's default in DEBUG), `.github/workflows/ci.yml`, `pyproject.toml`/`uv.lock` (`uv add gunicorn whitenoise`), `compose.yaml` (dev: keep `runserver`, unchanged behavior)
- Create: `deploy/gunicorn.conf.py`
- Test: `tests/deploy/test_image.py`

**Interfaces:**
- `Dockerfile`: `python:3.13-slim`, uv copied as today, `ENV UV_NO_DEV=1 UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH`, `uv sync --locked --no-dev` before copying the source (layer cache), `RUN DJANGO_SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput`, a system user `app` (uid `10001`) owning `/app`, `USER app`, `LABEL org.opencontainers.image.source=https://github.com/DanteArcese/unitpricer`, `CMD ["gunicorn", "config.wsgi:application", "-c", "deploy/gunicorn.conf.py"]`.
- `deploy/gunicorn.conf.py`: `bind = "0.0.0.0:8000"`, `workers = int(os.environ.get("WEB_CONCURRENCY", 3))`, `timeout = 30`, `graceful_timeout = 20`, `max_requests = 1000`, `max_requests_jitter = 100`, `accesslog = "-"`, `errorlog = "-"`, `access_log_format = '%(t)s %(m)s %(U)s %(s)s %(B)s %(M)sms'` (no IP, user agent or query string).
- `ci.yml` gains job `image` (`needs: test`, `permissions: contents: read, packages: write`): build `.` with `load: true` as `unitpricer:ci`; smoke-run `docker run --rm` with `PROD_ENV`-equivalent `-e` flags `python manage.py check --deploy --fail-level WARNING`; build `deploy/backup` as `unitpricer-backup:ci`; on `push` to `main` only, log in to `ghcr.io` with `GITHUB_TOKEN` and push both with tags `sha-${{ github.sha }}` and `main` to `ghcr.io/dantearcese/unitpricer` and `ghcr.io/dantearcese/unitpricer-backup`. Pin action versions to the current majors, as `ci.yml` does.

- [ ] **Step 1: Write failing tests** (`tests/deploy/test_image.py`, static checks since there is no local Docker daemon):
  - `test_dockerfile_runs_gunicorn_as_non_root`: `CMD` contains `gunicorn`; a `USER app` line follows the last `RUN`; `collectstatic` is run; no `runserver` in `CMD`.
  - `test_whitenoise_after_security_middleware`.
  - `test_static_served_by_whitenoise_in_prod`: one subprocess with `PROD_ENV` runs `collectstatic` into a temp dir (add env `DJANGO_STATIC_ROOT` → `STATIC_ROOT`), then uses Django's test `Client` to GET the manifest's hashed name for `web/app.css` on the apex: 200 with `Cache-Control` containing `max-age=315360000`.
  - `test_gunicorn_access_log_has_no_ip`: `%(h)s`, `%(a)s` and `%(q)s` absent from `access_log_format`.
  - `test_ci_pushes_only_from_main`: `ci.yml` parsed with PyYAML; the `image` job needs `test`; the push step's `if` contains `refs/heads/main` and `push`.
- [ ] **Step 2: Run** `uv run pytest tests/deploy/test_image.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS. Push the branch; the PR's `image` job builds and the `check --deploy` smoke passes (no push from a PR).
- [ ] **Step 5: Commit** `feat: gunicorn + WhiteNoise image built and checked in CI (#39)`

---

### Task 8: Production Compose, cloud-init, deploy script, runbook (#39)

**Files:**
- Create: `compose.prod.yaml`, `deploy/cloud-init.yaml`, `deploy/deploy.sh` (executable), `docs/deploy.md`
- Modify: `README.md` (short "Deploying" pointer to `docs/deploy.md`), `CLAUDE.md` (Commands: deploy line), `docs/roadmap.md` (Checkpoint B item 3 decided), spec §12, §15 item 2, §16 (hosting row: "DigitalOcean droplet + Docker Compose + Cloudflare Tunnel; gated beta, public waitlist")
- Test: `tests/deploy/test_compose_prod.py`, `tests/deploy/test_cloud_init.py`, `tests/deploy/test_deploy_script.py`

**Interfaces:**
- `compose.prod.yaml` (copied to `/srv/unitpricer/compose.yaml`; interpolation from `/srv/unitpricer/.env`):
  - every service: `restart: unless-stopped`, no `ports:`, no `build:`.
  - `db`: `postgres:17`, `POSTGRES_USER/DB: unitpricer`, `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?}`, volume `pgdata`, `pg_isready` healthcheck.
  - `web`: `ghcr.io/dantearcese/unitpricer:${UNITPRICER_IMAGE_TAG:?}`, `env_file: app.env`, `DATABASE_URL: postgres://unitpricer:${POSTGRES_PASSWORD}@db:5432/unitpricer`, depends on healthy `db`, healthcheck `python -c` urllib GET `http://127.0.0.1:8000/healthz` with header `Host: unitpricer.com` (interval 30 s, start period 20 s).
  - `worker`: same image and env, `command: python manage.py run_scheduler`, `stop_grace_period: 60s`.
  - `cloudflared`: `cloudflare/cloudflared:<current release tag, pinned>`, `command: tunnel --no-autoupdate run`, `environment: TUNNEL_TOKEN: ${TUNNEL_TOKEN:?}` only, depends on `web`.
  - `backup`: `ghcr.io/dantearcese/unitpricer-backup:${UNITPRICER_IMAGE_TAG:?}`, `environment` only: `PGHOST=db`, `PGUSER`, `PGDATABASE`, `PGPASSWORD=${POSTGRES_PASSWORD}`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET=unitpricer-backups`, `BACKUP_AT=03:30`, `BACKUP_HEARTBEAT_URL=${BACKUP_HEARTBEAT_URL:-}`.
- `deploy/cloud-init.yaml` (`#cloud-config`): user `deploy` (shell bash, `sudo: ALL=(ALL) NOPASSWD:ALL`, no password); `ssh_pwauth: false`; `disable_root: true`; `swap: {filename: /swapfile, size: 1G}`; `package_update/upgrade: true`; packages `ufw`, `unattended-upgrades`, `ca-certificates`, `curl`; `write_files`: `/etc/ssh/sshd_config.d/60-unitpricer.conf` (`PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin no`, `AllowUsers deploy`), `/etc/docker/daemon.json` (`{"log-driver": "local", "log-opts": {"max-size": "10m", "max-file": "5"}}`), `/etc/apt/apt.conf.d/52unitpricer-upgrades` (`Unattended-Upgrade::Automatic-Reboot "true";` `Unattended-Upgrade::Automatic-Reboot-Time "08:00";`); `runcmd`: copy root's `authorized_keys` to `deploy` (`install -m 600 -o deploy -g deploy`), `ufw default deny incoming`, `ufw allow OpenSSH`, `ufw --force enable`, install `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-compose-plugin` from Docker's apt repository for `noble`, `usermod -aG docker deploy`, `install -d -o deploy -g deploy -m 750 /srv/unitpricer`, `systemctl restart ssh`. No secrets.
- `deploy/deploy.sh [git-ref]` (default `origin/main`), env `UNITPRICER_DEPLOY_HOST` (e.g. `deploy@<ip>`), `set -euo pipefail`:
  1. Resolve the ref to a full SHA; refuse unless `gh run list --commit <sha> --workflow CI --json conclusion,event` shows a successful `push` run (so the `sha-<sha>` images exist).
  2. `scp compose.prod.yaml` → `/srv/unitpricer/compose.yaml`.
  3. Over one `ssh`, in `/srv/unitpricer`: set `UNITPRICER_IMAGE_TAG=sha-<sha>` in `.env` (replace the line), `docker compose pull`, `docker compose run --rm web python manage.py migrate --noinput`, `docker compose run --rm web python manage.py createcachetable`, `docker compose up -d --remove-orphans`, wait up to 90 s for `web` health `healthy` (else print `docker compose logs --tail 100 web` and exit 1), `docker image prune -f`.
  4. Print the deployed SHA. Rollback = `deploy/deploy.sh <older-sha>` (migrations are not reversed; see runbook).
- `docs/deploy.md` runbook sections: Topology; Server files (`.env` keys: `POSTGRES_PASSWORD`, `TUNNEL_TOKEN`, `R2_*`, `BACKUP_HEARTBEAT_URL`, `UNITPRICER_IMAGE_TAG`; `app.env` keys: all Django vars with production values from Task 15); Deploy; Rollback; Logs (`docker compose logs -f web worker`); One-off commands (`docker compose run --rm web python manage.py …`); Backups and restore (Task 9); SSH access and the firewall; Rotating secrets (`SECRET_KEY_FALLBACKS`, Resend key, tunnel token).

- [ ] **Step 1: Write failing tests:**
  - `test_compose_prod.py`: `test_no_published_ports_or_builds`; `test_all_services_restart`; `test_images_pinned` (app images use `${UNITPRICER_IMAGE_TAG:?}`, `cloudflared` not `:latest`, `postgres:17`); `test_secrets_scoped` (`cloudflared` environment keys == `{"TUNNEL_TOKEN"}`; `web`/`worker` `env_file` is `app.env`; no service has both `TUNNEL_TOKEN` and `app.env`); `test_web_healthcheck_hits_healthz_with_apex_host`.
  - `test_cloud_init.py`: first line `#cloud-config`; `ssh_pwauth` False; `disable_root` True; the sshd drop-in contains the 4 lines; `daemon.json` parses with `max-size == "10m"`; `runcmd` contains `ufw default deny incoming` before `ufw --force enable`; no value matching `(?i)token|password=|secret`.
  - `test_deploy_script.py` (fake `ssh`, `scp`, `gh` and `git` on `PATH` that log their argv/stdin to a temp file): `test_refuses_without_green_ci`; `test_orders_pull_migrate_up` (in the remote script, `pull` < `migrate` < `createcachetable` < `up -d`); `test_requires_deploy_host`.
- [ ] **Step 2: Run** `uv run pytest tests/deploy -v`. Expected: FAIL.
- [ ] **Step 3: Implement**, then update docs.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS. In CI, the `image` job also runs `docker compose -f compose.prod.yaml config -q` with dummy interpolation values (add the step) and passes.
- [ ] **Step 5: Commit** `feat: production compose, droplet cloud-init and deploy script (#39)`

---

### Task 9: Nightly backups to R2 (#39)

**Files:**
- Create: `deploy/backup/Dockerfile`, `deploy/backup/backup.sh`
- Modify: `docs/deploy.md` (Backups and restore section), `.env.example` (deploy tooling: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `BACKUP_HEARTBEAT_URL`)
- Test: `tests/deploy/test_backup_script.py`

**Interfaces:**
- `deploy/backup/Dockerfile`: `FROM postgres:17` (matching `pg_dump`), `apt-get install --no-install-recommends rclone curl`, copies `backup.sh`, `ENTRYPOINT ["/usr/local/bin/backup.sh"]`, `CMD ["loop"]`, runs as `postgres`.
- `backup.sh once`: `set -euo pipefail`; dumps `pg_dump -Fc --no-owner` to `/tmp/unitpricer-<UTC %Y%m%dT%H%M%SZ>.dump`; validates with `pg_restore --list` (> /dev/null); uploads with `rclone copyto <file> r2:${R2_BUCKET}/pg/<name>` using env-only rclone config (`RCLONE_CONFIG_R2_TYPE=s3`, `_PROVIDER=Cloudflare`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`, `_ENDPOINT=https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com`, `_NO_CHECK_BUCKET=true`); removes the temp file; when `BACKUP_HEARTBEAT_URL` is set, `curl -fsS -m 10 --retry 3` it. Any failure exits non-zero with no upload and no heartbeat. Logs one line per step to stdout. Retention is the bucket's lifecycle rule, not the script.
- `backup.sh loop`: forever: sleep until the next `BACKUP_AT` UTC, run `once` (a failure is logged; the loop continues). `backup.sh restore-latest <target-db-url>`: downloads the newest `pg/` object and `pg_restore --no-owner --clean --if-exists -d <url>`; used by the drill.

- [ ] **Step 1: Write failing tests** (run `bash deploy/backup/backup.sh once` with fake `pg_dump`, `pg_restore`, `rclone`, `curl` on `PATH` logging their argv):
  - `test_backup_uploads_validated_dump`: rclone called with `copyto` and a destination matching `^r2:unitpricer-backups/pg/unitpricer-\d{8}T\d{6}Z\.dump$`; `pg_restore --list` ran before `rclone`; temp file gone.
  - `test_dump_failure_no_upload_no_heartbeat`: fake `pg_dump` exits 1 → script exit ≠ 0; no `rclone`, no `curl`.
  - `test_invalid_dump_no_upload`: fake `pg_restore` exits 1 → no upload.
  - `test_heartbeat_only_when_configured`.
  - `test_secrets_not_echoed`: script stdout/stderr contain neither the fake secret key nor the password.
- [ ] **Step 2: Run** `uv run pytest tests/deploy/test_backup_script.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: nightly pg_dump to R2 (#39)`

---

### Task 10: Merge gate

- [ ] **Step 1:** `uv run pytest -q` green, pre-commit clean, `uv run python manage.py makemigrations --check --dry-run` → "No changes detected".
- [ ] **Step 2:** Open the PR for Tasks 1–9 (closes #39 and the implementation issue), get review, merge on green CI.
- [ ] **Step 3:** Verify the `image` job on `main` pushed `ghcr.io/dantearcese/unitpricer:sha-<merge sha>` and `ghcr.io/dantearcese/unitpricer-backup:sha-<merge sha>` (`gh api /users/DanteArcese/packages/container/unitpricer/versions --jq '.[0].metadata.container.tags'`). Make both packages **public** (decision 1) in the package settings; the API cannot change visibility, so this step is the user's if not already public. Check: `curl -s -o /dev/null -w '%{http_code}' https://ghcr.io/v2/dantearcese/unitpricer/manifests/main -H "Authorization: Bearer $(curl -s 'https://ghcr.io/token?scope=repository:dantearcese/unitpricer:pull' | jq -r .token)" -H 'Accept: application/vnd.oci.image.index.v1+json'` → `200`.

---

### Task 11 (operational): Email DNS — Resend, SPF/DKIM/DMARC, DNSSEC

No code. Controller via Resend API (`uv run --env-file .env` for `RESEND_API_KEY`) and Cloudflare MCP; the dashboards are the fallback.

- [ ] **Step 1: Record the current mail DNS.** `dig +short MX unitpricer.com`, `dig +short TXT unitpricer.com` → note the Email Routing MX records and the single `v=spf1 include:_spf.mx.cloudflare.net ~all` record. These are not edited by this task.
- [ ] **Step 2: Add the domain in Resend** (`POST https://api.resend.com/domains` `{"name": "unitpricer.com", "region": "us-east-1"}`; if the key is sending-only, the user adds it in the Resend dashboard). Resend returns the records to add; expected shape: `MX send` → `feedback-smtp.us-east-1.amazonses.com` priority 10, `TXT send` → `v=spf1 include:amazonses.com ~all`, `TXT resend._domainkey` → DKIM public key. Use the returned values verbatim.
- [ ] **Step 3: Create those records in Cloudflare DNS**, DNS-only (not proxied). They live on `send.` and `resend._domainkey.`, so the apex SPF and Email Routing MX stay untouched: never add a second apex `v=spf1` record. Cloudflare Email Routing's DKIM selector (`cf2024-1._domainkey` or similar) is distinct from Resend's, so both coexist.
- [ ] **Step 4: DMARC.** Add `TXT _dmarc` = `v=DMARC1; p=none; rua=mailto:hello@unitpricer.com; adkim=r; aspf=r` (replace an existing one rather than adding a second). Raise to `p=quarantine` after two weeks of clean reports (decision 6).
- [ ] **Step 5: DNSSEC.** Enable in Cloudflare (Registrar is Cloudflare, so DS is published automatically). Check: `dig +short DS unitpricer.com` non-empty within an hour.
- [ ] **Step 6: Verify the domain** (`POST /domains/{id}/verify`, then `GET /domains/{id}` → `"status": "verified"`). Create a **sending-only** API key restricted to `unitpricer.com`, named `unitpricer-prod`, for the server (the user copies it into `app.env` in Task 15, or the controller pipes it without echoing).
- [ ] **Step 7: Check.** `dig +short TXT unitpricer.com` still shows exactly one `v=spf1`; `hello@unitpricer.com` still forwards (send a test from another account).

### Task 12 (operational): R2 backup bucket

- [ ] **Step 1:** Create bucket `unitpricer-backups` (location hint ENAM, private, no public access, no custom domain).
- [ ] **Step 2:** Lifecycle rule: delete objects with prefix `pg/` **30 days** after creation (decision 4).
- [ ] **Step 3:** R2 API token "Object Read & Write" scoped to `unitpricer-backups` only (user, in R2 > Manage API tokens, if the API path is unavailable). The user stores `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` in the local `.env`.
- [ ] **Step 4: Check:** the bucket lists 0 objects; the lifecycle rule shows 30 days.

### Task 13 (operational): Provision the droplet

All `doctl` calls run as `uv run --env-file .env doctl …` (doctl reads `DIGITALOCEAN_ACCESS_TOKEN`). Install `doctl` from its GitHub release into `~/.local/bin` if missing.

- [ ] **Step 1: SSH key.** `ssh-keygen -t ed25519 -f ~/.ssh/unitpricer_deploy -C unitpricer-deploy` (passphrase per the user's preference), then `doctl compute ssh-key import unitpricer-deploy --public-key-file ~/.ssh/unitpricer_deploy.pub`.
- [ ] **Step 2: Create.** `doctl compute droplet create unitpricer-prod-1 --region nyc3 --size s-1vcpu-2gb --image ubuntu-24-04-x64 --ssh-keys <fingerprint> --enable-backups --backup-policy-plan weekly --enable-monitoring --enable-ipv6 --tag-name unitpricer --user-data-file deploy/cloud-init.yaml --wait`. Record the IPv4.
- [ ] **Step 3: Cloud Firewall** (decision 2): `doctl compute firewall create --name unitpricer --tag-names unitpricer --inbound-rules "protocol:tcp,ports:22,address:<maintainer IPv4>/32" --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0 protocol:icmp,address:0.0.0.0/0,address:::/0"`. Document in `docs/deploy.md` how to update the allowed IP (`doctl compute firewall add-rules` / `remove-rules`) and that the DigitalOcean web console is the lockout fallback.
- [ ] **Step 4: Monitoring alerts** to the maintainer's email: disk > 80 % (5 m), memory > 90 % (10 m), CPU > 90 % (30 m) via `doctl monitoring alert create --type v1/insights/droplet/{disk_utilization_percent,memory_utilization_percent,cpu} …`.
- [ ] **Step 5: Verify.** `ssh -i ~/.ssh/unitpricer_deploy deploy@<ip> 'cloud-init status --wait && docker compose version && sudo ufw status && swapon --show && cat /etc/docker/daemon.json'` → `status: done`, Compose v2, ufw active with only OpenSSH, 1G swap, the log options. `ssh root@<ip>` and password auth are refused. From another network, `nc -zv -w 5 <ip> 22` times out.

### Task 14 (operational): Cloudflare Tunnel, DNS, redirect, Access

- [ ] **Step 1: Check the apex** for existing `A`/`AAAA`/`CNAME` records (parking) and remove them; leave MX/TXT alone.
- [ ] **Step 2: Tunnel.** Create a remotely managed tunnel `unitpricer-prod` (`config_src: cloudflare`). Ingress config: `unitpricer.com` → `http://web:8000`; `beta.unitpricer.com` → `http://web:8000`; catch-all `http_status:404`. DNS: proxied `CNAME @` and `CNAME beta` → `<tunnel-id>.cfargotunnel.com` (Cloudflare flattens the apex CNAME; if the API refuses it next to the MX records, create the public hostname from the tunnel's dashboard page, which handles it). Fetch the tunnel token and write it straight into `/srv/unitpricer/.env` as `TUNNEL_TOKEN=` over SSH without printing it (fallback: the user pastes it).
- [ ] **Step 3: www.** Proxied `CNAME www` → `unitpricer.com`; Single Redirect rule `http.host eq "www.unitpricer.com"` → dynamic `concat("https://unitpricer.com", http.request.uri.path)`, status 301, preserve query string.
- [ ] **Step 4: Zone settings.** Always Use HTTPS on; minimum TLS 1.2; SSL mode Full (strict); Automatic HTTPS Rewrites on. (Optional: one rate-limiting rule on `POST /waitlist/`, 10 per 10 s per IP, block 10 s.)
- [ ] **Step 5: Access.** Zero Trust organization (free plan; team name `unitpricer` if free, else the user picks; creating the org may need the dashboard). Enable the **One-time PIN** login method. Self-hosted application "UnitPricer beta", domain `beta.unitpricer.com`, session duration **24 h**, instant auth (OTP only). Policy "Allow-list", action Allow, include Emails: the list from decision 3. Record the application **AUD tag** and the team domain (`unitpricer.cloudflareaccess.com`) for `app.env` (`CF_ACCESS_AUD`, `CF_ACCESS_TEAM_DOMAIN`).
- [ ] **Step 6: Verify** (after Task 15 starts `cloudflared`): tunnel status `healthy` with 4 connections; checks are in Task 15.

### Task 15 (operational): Server env, first deploy, smoke test, restore drill, monitors

- [ ] **Step 1: Server env files** (mode 600, owner `deploy`). `.env`: `POSTGRES_PASSWORD` = `openssl rand -hex 24` generated on the server; `TUNNEL_TOKEN` (Task 14); `R2_*` (piped from the local `.env` via `uv run --env-file .env sh -c 'printf …' | ssh … 'cat >> .env'`, never echoed); `BACKUP_HEARTBEAT_URL` (Step 7). `app.env`: `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY` (generated on the server with `python3 -c 'import secrets; print(secrets.token_urlsafe(50))'`), `DJANGO_ALLOWED_HOSTS=unitpricer.com,www.unitpricer.com,beta.unitpricer.com`, `UNITPRICER_PUBLIC_HOST=unitpricer.com`, `UNITPRICER_APP_HOST=beta.unitpricer.com`, `UNITPRICER_PUBLIC_URL=https://unitpricer.com`, `UNITPRICER_SITE_URL=https://beta.unitpricer.com`, `UNITPRICER_TRUST_CF_IP=1`, `DJANGO_HSTS_SECONDS=3600`, `DJANGO_EMAIL_HOST=smtp.resend.com`, `DJANGO_EMAIL_PORT=2587`, `DJANGO_EMAIL_HOST_USER=resend`, `DJANGO_EMAIL_HOST_PASSWORD` (the `unitpricer-prod` sending key), `DJANGO_DEFAULT_FROM_EMAIL=UnitPricer <hello@unitpricer.com>`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `UNITPRICER_POSTAL_ADDRESS` (when decided). No affiliate or Walmart keys yet.
- [ ] **Step 2: Deploy.** `UNITPRICER_DEPLOY_HOST=deploy@<ip> deploy/deploy.sh` → prints the SHA; `web` healthy. Then `docker compose run --rm web python manage.py sync_categories`, `… refresh --connector walmart --recordings tests/connectors/recordings/walmart` (beta has something to show until walmart.io access works), `… createsuperuser` (the maintainer's email).
- [ ] **Step 3: Smoke test** (each line: command → expected):
  - `curl -sI https://unitpricer.com/` → `200`, `strict-transport-security: max-age=3600; includeSubDomains`, no `x-robots-tag`.
  - `curl -s -o /dev/null -w '%{http_code}' https://unitpricer.com/c/dairy/milk/` → `404`; same for `/admin/`, `/search?q=milk`, `/settings`.
  - `curl -s https://unitpricer.com/healthz` → `{"status": "ok", …}`; `curl -s https://unitpricer.com/robots.txt` → `Allow: /`.
  - `curl -sI 'https://www.unitpricer.com/privacy?x=1'` → `301`, `location: https://unitpricer.com/privacy?x=1`. `curl -sI http://unitpricer.com/` → `301` to https.
  - `curl -sI https://beta.unitpricer.com/` → `302` to `unitpricer.cloudflareaccess.com`.
  - Browser (allow-listed email, OTP): beta home and `/c/dairy/milk/` render; response headers include `x-robots-tag: noindex, nofollow`; `/admin/` sign-in works.
  - `nc -zv -w 5 <ip> 80`, `443`, `5432`, `8000` → all time out.
  - Waitlist end to end with the maintainer's address: sign up on the apex → the email arrives (Gmail "Show original": `SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`, `List-Unsubscribe` present, sent via port 2587) → GET the link does not confirm, the button does → `docker compose exec web python manage.py shell -c "from waitlist.models import Subscriber as S; print(S.objects.filter(confirmed_at__isnull=False).count())"` → `1` → Gmail's unsubscribe button (one-click) → count `0`.
  - `docker compose logs --tail 50 web worker` → one line per log record; no IPs in access lines.
- [ ] **Step 4: Backup now.** `docker compose run --rm backup once` → exit 0; the object `pg/unitpricer-<ts>.dump` is listed in R2.
- [ ] **Step 5: Restore drill.** On the droplet: `docker run -d --name restore-drill --network unitpricer_default -e POSTGRES_PASSWORD=drill postgres:17`; `docker compose run --rm backup restore-latest postgres://postgres:drill@restore-drill:5432/postgres`; compare `select count(*)` of `catalog_category`, `catalog_offer`, `waitlist_subscriber` with production; `docker rm -f restore-drill`. Record date, duration and counts in `docs/deploy.md` ("Restore drills" table); repeat quarterly (roadmap 6.2).
- [ ] **Step 6: Uptime monitor** (user creates the account; decision 5): HTTP keyword check on `https://unitpricer.com/healthz` for `"status": "ok"` every 5 min, email alert.
- [ ] **Step 7: Backup heartbeat** (decision 5): a healthchecks.io check with period 1 day, grace 2 h; its ping URL becomes `BACKUP_HEARTBEAT_URL`; redeploy; next morning the check is green.
- [ ] **Step 8: Wrap up.** Raise HSTS to `31536000` after one clean week (edit `app.env`, `docker compose up -d`). Comment on #48 with the smoke-test results; update the roadmap's Checkpoint B item 4 to done for hosting/TLS/backups; affiliate-program registration stays open (decision 7).

---

## Hand-off to M4

This plan lands first and defines names M4 Task 6 planned to add. When M4 executes:
- Reuse, don't re-add: the `DJANGO_EMAIL_*`/`DJANGO_DEFAULT_FROM_EMAIL` settings, `SITE_URL`, `CACHES`, secure-cookie settings (Task 1), and `allow()` from `web/ratelimit.py` instead of `accounts/ratelimit.py` (Task 2, same signature).
- Key per-IP limits on `web.net.client_ip(request)`, not `REMOTE_ADDR` (Review Focus 3).
- Send magic links through `waitlist.mail.send_budgeted(message, kind="magic", now=…)` so sign-in mail shares the 100/day budget.
- Account routes live on the app host only (the apex URLconf has no `account/`); `SITE_URL` is `https://beta.unitpricer.com`. Check id `unitpricer.E010` does not collide with E020–E022.
- M4's default From (`noreply@`) is overridden by `DJANGO_DEFAULT_FROM_EMAIL`; this plan's default is `hello@`.

## Open decisions (defaults used above; change before execution)

1. **GHCR visibility:** public images (default; the source is public AGPL and no secrets are baked in) vs private with a read-only token on the droplet.
2. **SSH exposure:** DigitalOcean Cloud Firewall allowing port 22 from the maintainer's IP only (default) vs SSH through the Cloudflare Tunnel behind Access (zero open ports, but a tunnel outage needs the DO console).
3. **Beta allow-list:** which emails; Access session length 24 h (default).
4. **Backup retention and encryption:** 30 days (default; range was 14–30) in R2, unencrypted beyond R2's at-rest encryption (dumps contain waitlist emails) vs `age`-encrypting dumps with a key kept off the droplet.
5. **Monitoring vendors:** UptimeRobot (or Better Stack) for uptime and healthchecks.io for the backup heartbeat (defaults, free tiers, user accounts); error reporting: Sentry free tier gated on `SENTRY_DSN` with no PII (needs `sentry-sdk` and a privacy-page line), or logs only for now (default).
6. **Email:** From `hello@unitpricer.com` (default; replies reach the maintainer through Email Routing) vs `noreply@`; DMARC `p=none` now, `quarantine` after two weeks.
7. **CAN-SPAM postal address** for the launch email (PO box or virtual mailbox); the announce command refuses to run without one. Affiliate programs (Sovrn, Associates) review a public site, so registration waits for public launch or needs reviewer access to beta.
8. **HSTS:** start at 1 hour, raise to 1 year after a clean week; `preload` not set (default).
