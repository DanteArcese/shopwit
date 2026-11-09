# Limited public launch runbook

Plan: `docs/superpowers/plans/2026-11-08-limited-public-launch.md` (issue #77). Mark each item with its date when done: `- [x] 2026-11-xx ...`. Server commands run on the droplet in `/srv/unitpricer`. Cloudflare steps go through the Cloudflare API (zone `unitpricer.com`) with the dashboard as fallback.

## A. Before the apex is publicly visible (in order)

- [ ] **Cloudflare Bot Fight Mode** (before the deploy, so the apex is never public without it): `PUT /zones/{zone}/bot_management` with `{"fight_mode": true}`.
- [ ] **Cloudflare rate limit** (before the deploy; free plan, phase `http_ratelimit`). The free plan allows one rate-limiting rule and its expressions can only match on the URI path, so the rule is path-only:
  ```
  (starts_with(http.request.uri.path, "/search") or starts_with(http.request.uri.path, "/waitlist/") or http.request.uri.path in {"/click" "/feedback" "/settings"} or starts_with(http.request.uri.path, "/account/") or starts_with(http.request.uri.path, "/lists/"))
  ```
  Characteristics `ip.src` and `cf.colo.id`, `period` 10, `requests_per_period` 30, action `block`, `mitigation_timeout` 10 (30 requests per 10 s per IP, blocked for 10 s). With no host condition it also applies to `beta.unitpricer.com`, which is fine (Access is in front there too).
  - Check: the uptime monitor still gets 200 from `/healthz` (Bot Fight Mode cannot be skipped on the free plan; if the monitor is challenged, change monitor provider, or turn Bot Fight Mode off and keep only the rate rule). Beta with no token still redirects to the Access login.
- [ ] **HSTS caveat.** `includeSubDomains` is already on in production (default max-age 3600 s, raised to a year in section B), so every `*.unitpricer.com` host must serve HTTPS, including email tracking subdomains (Resend). Confirm none is HTTP-only before the deploy.
- [ ] **`app.env` changes.** Edit `/srv/unitpricer/app.env` (shared by `web` and `worker`):
  - `UNITPRICER_SITE_URL=https://unitpricer.com` and `UNITPRICER_PUBLIC_URL=https://unitpricer.com`. Check `unitpricer.E023` requires them to match; `deploy.sh` runs `manage.py check --deploy --fail-level ERROR` before `migrate`, so a mismatch stops the deploy there.
  - `UNITPRICER_COMING_SOON=Walmart (December)`. There is no default: set it explicitly, or leave it unset to drop the "coming: ..." clause.
  - Unchanged: `UNITPRICER_PUBLIC_HOST`, `UNITPRICER_APP_HOST`, `DJANGO_ALLOWED_HOSTS`, `CF_ACCESS_AUD` (with `UNITPRICER_APP_HOST` set and `DEBUG` off, an empty `CF_ACCESS_AUD` makes every non-public host answer 403), tunnel ingress, the Access application (beta only).
  - Optional: `SENTRY_DSN` (and in Sentry enable "Prevent storing of IP addresses"). A malformed DSN only logs a warning and leaves Sentry off.
- [ ] **Deploy:** `deploy/deploy.sh <merged-sha>`. Note the pre-launch SHA first (for rollback). Expect the cookie rename (`__Host-` prefixes) to sign existing beta users out once. Anonymous beta users also lose their session lists and saved settings (they lived in the old session); lists and settings saved to an account survive and come back on sign-in.
- [ ] **Purge sample data, immediately after the deploy and before the smoke checks** (and before enabling any real Walmart key):
  ```bash
  docker compose run --rm -T web python manage.py purge_sample_data </dev/null          # dry run, read the counts
  docker compose run --rm -T web python manage.py purge_sample_data --apply </dev/null
  ```
  Check: `/c/dairy/milk/` on the apex shows no Walmart rows.
- [ ] **Smoke checks.**
  - `curl -sI https://unitpricer.com/ | head -1` is 200; `curl -s -o /dev/null -w '%{http_code}\n' https://unitpricer.com/admin/` is 404.
  - By hand on the apex: home, a category, a search, `/sitemap.xml`, `/robots.txt`, `/admin/` returns 404, store-announcements signup, confirmation email, POST confirm. On beta: a request with no token goes to the Access login.
- [ ] **Monitors.**
  - HTTP monitor on `https://unitpricer.com/healthz` every 5 minutes, expecting `"status": "ok"`, alerting `dante@unitpricer.com`.
  - Heartbeat check (for example Healthchecks.io, daily period, 2 h grace); its ping URL goes in `BACKUP_HEARTBEAT_URL` in `/srv/unitpricer/.env`, then:
    ```bash
    docker compose up -d backup
    docker compose exec backup backup.sh once   # the first ping should arrive
    ```
- [ ] **Restore drill** per `docs/deploy.md`; fill its table row.

### Rollback

`deploy/deploy.sh <pre-launch-sha>` (see `docs/deploy.md`, Rollback, for migrations). The pre-launch code emails links on the beta host, so also set `UNITPRICER_SITE_URL=https://beta.unitpricer.com` in `app.env` (the deploy's check does not enforce E023 on that code). The cookie rename reverses: everyone is signed out once more, and anonymous session lists and settings made since launch are lost again. The purge is not undone (sample data stays gone).

## B. Legal gates (maintainer-owned; the announcement waits on both)

- [ ] **ODbL.** Open Food Facts has answered whether our Open Prices store plus normalization is a Derivative Database and whether "free copy on request" satisfies section 4.6 (email drafted separately). Act on the answer (for example publish the `export_open_prices` dump at a public URL) before announcing.
- [ ] **CAN-SPAM.** `UNITPRICER_POSTAL_ADDRESS` in `app.env` holds a real postal address (PO box or registered-agent address).
- [x] **Terms** finalized 2026-11-09 (Massachusetts law, accounts 13+, courts + small claims; #84).
- [ ] **Announce:** `docker compose run --rm -T web python manage.py send_waitlist_announcement </dev/null` (it refuses without a postal address and URLs).
- [ ] **HSTS, after 7 clean days** (no 5xx bursts, no Access or routing incidents): set `DJANGO_HSTS_SECONDS=31536000` in `app.env`, `docker compose up -d web`, then `curl -sI https://unitpricer.com/ | grep -i strict-transport`. Preload stays off.

## C. Applications after launch (in this order; each needs the live apex)

1. [ ] eBay Developers Program account and eBay Partner Network (EPN).
2. [ ] Impact: Target Partners and the Walmart affiliate (publisher ID to `WALMART_IMPACT_PUBLISHER_ID`; retry the walmart.io key upload alongside). `purge_sample_data` must already have run before the real Walmart key is enabled.
3. [ ] CJ (Costco and other grocery or household advertisers).
4. [ ] Sovrn Commerce: register `unitpricer.com`; site key to `SOVRN_SITE_KEY`; run the merchant audit (roadmap Checkpoint B.5).
5. [ ] Amazon Associates last: the 180-day window for 3 qualifying sales starts at signup, so apply once there is traffic. Tag to `AMAZON_ASSOCIATES_TAG` (turns on the disclosure sentence); request written approval for unit-price comparison (spec section 10).

After each approval, re-read that program's terms against spec section 10 and the disclosure page before adding links.
