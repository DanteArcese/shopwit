# Limited public launch runbook

Plan: `docs/superpowers/plans/2026-11-08-limited-public-launch.md` (issue #77). Mark each item with its date when done: `- [x] 2026-11-xx ...`. Server commands run on the droplet in `/srv/unitpricer`. Cloudflare steps go through the Cloudflare API (zone `unitpricer.com`) with the dashboard as fallback.

## A. Before the apex is publicly visible (in order)

- [ ] **Deploy with new `app.env` values.** Edit `/srv/unitpricer/app.env`:
  - `UNITPRICER_SITE_URL=https://unitpricer.com` and `UNITPRICER_PUBLIC_URL=https://unitpricer.com`. Check `unitpricer.E023` requires them to match: the deploy fails its check otherwise.
  - `UNITPRICER_COMING_SOON=Walmart (December)` (set explicitly; unset drops the clause).
  - Unchanged: `UNITPRICER_PUBLIC_HOST`, `UNITPRICER_APP_HOST`, `DJANGO_ALLOWED_HOSTS`, tunnel ingress, the Access application (beta only).
  - Then `deploy/deploy.sh <merged-sha>`. Expect: the cookie rename (`__Host-` prefixes) signs existing beta users out once.
  - Optional: `SENTRY_DSN` (and in Sentry enable "Prevent storing of IP addresses").
  - Check: `curl -sI https://unitpricer.com/ | head -1` is 200; `curl -s -o /dev/null -w '%{http_code}\n' https://unitpricer.com/admin/` is 404.
- [ ] **Purge sample data before enabling any real Walmart key.**
  ```bash
  docker compose run --rm -T web python manage.py purge_sample_data </dev/null          # dry run, read the counts
  docker compose run --rm -T web python manage.py purge_sample_data --apply </dev/null
  ```
  Check: `/c/dairy/milk/` on the apex shows no Walmart rows.
- [ ] **Cloudflare Bot Fight Mode:** `PUT /zones/{zone}/bot_management` with `{"fight_mode": true}`.
- [ ] **Cloudflare rate limit** (free plan, phase `http_ratelimit`): expression `(http.host eq "unitpricer.com" and (starts_with(http.request.uri.path, "/search") or http.request.method eq "POST"))`, characteristics `ip.src` and `cf.colo.id`, `period` 10, `requests_per_period` 30, action `block`, `mitigation_timeout` 10.
  - Check: the uptime monitor still gets 200 from `/healthz` (Bot Fight Mode cannot be skipped on the free plan; if the monitor is challenged, change monitor provider, or turn Bot Fight Mode off and keep only the rate rule). Beta with no token still redirects to the Access login.
- [ ] **HSTS caveat.** `includeSubDomains` is already on in production (default max-age 3600 s, raised to a year in section B), so every `*.unitpricer.com` host must serve HTTPS, including email tracking subdomains (Resend). Confirm none is HTTP-only before enabling.
- [ ] **Monitors.**
  - HTTP monitor on `https://unitpricer.com/healthz` every 5 minutes, expecting `"status": "ok"`, alerting `dante@unitpricer.com`.
  - Heartbeat check (for example Healthchecks.io, daily period, 2 h grace); its ping URL goes in `BACKUP_HEARTBEAT_URL` in `/srv/unitpricer/.env`, then:
    ```bash
    docker compose up -d backup
    docker compose exec backup backup.sh once   # the first ping should arrive
    ```
- [ ] **Restore drill** per `docs/deploy.md`; fill its table row.
- [ ] **Smoke check by hand on the apex:** home, a category, a search, `/sitemap.xml`, `/robots.txt`, `/admin/` returns 404, waitlist signup, confirmation email, POST confirm. On beta: a request with no token goes to the Access login.

## B. Legal gates (maintainer-owned; the announcement waits on both)

- [ ] **ODbL.** Open Food Facts has answered whether our Open Prices store plus normalization is a Derivative Database and whether "free copy on request" satisfies section 4.6 (email drafted separately). Act on the answer (for example publish the `export_open_prices` dump at a public URL) before announcing.
- [ ] **CAN-SPAM.** `UNITPRICER_POSTAL_ADDRESS` in `app.env` holds a real postal address (PO box or registered-agent address).
- [ ] **Terms** reviewed; remove the draft banner in a separate PR.
- [ ] **Announce:** `docker compose run --rm -T web python manage.py send_waitlist_announcement </dev/null` (it refuses without a postal address and URLs).
- [ ] **HSTS, after 7 clean days** (no 5xx bursts, no Access or routing incidents): set `DJANGO_HSTS_SECONDS=31536000` in `app.env`, `docker compose up -d web`, then `curl -sI https://unitpricer.com/ | grep -i strict-transport`. Preload stays off.

## C. Applications after launch (in this order; each needs the live apex)

1. [ ] eBay Developers Program account and eBay Partner Network (EPN).
2. [ ] Impact: Target Partners and the Walmart affiliate (publisher ID to `WALMART_IMPACT_PUBLISHER_ID`; retry the walmart.io key upload alongside). `purge_sample_data` must already have run before the real Walmart key is enabled.
3. [ ] CJ (Costco and other grocery or household advertisers).
4. [ ] Sovrn Commerce: register `unitpricer.com`; site key to `SOVRN_SITE_KEY`; run the merchant audit (roadmap Checkpoint B.5).
5. [ ] Amazon Associates last: the 180-day window for 3 qualifying sales starts at signup, so apply once there is traffic. Tag to `AMAZON_ASSOCIATES_TAG` (turns on the disclosure sentence); request written approval for unit-price comparison (spec section 10).

After each approval, re-read that program's terms against spec section 10 and the disclosure page before adding links.
