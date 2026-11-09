# Deploying UnitPricer

First deploy and day-to-day operations. Files: `compose.prod.yaml`, `deploy/cloud-init.yaml`, `deploy/deploy.sh`.

## Topology

One DigitalOcean droplet (Ubuntu 24.04, `s-1vcpu-2gb`, `nyc3`) running Docker Compose: `db` (Postgres 17), `web` (gunicorn), `worker` (`run_scheduler`), `cloudflared` and `backup`. No container publishes a port. Traffic arrives through the Cloudflare tunnel **`unitpricer-prod`** (remotely managed; ingress is the apex `unitpricer.com` and `beta.unitpricer.com` only, both to `http://web:8000`; the apex serves the public app without `/admin/`, and beta serves the same app plus `/admin/` behind Access with `noindex`; `www.unitpricer.com` is a Cloudflare 301 redirect rule to the apex, and the app's own www redirect is only a fallback). The DigitalOcean firewall is named **`unitpricer`**. The app host `beta.unitpricer.com` sits behind the Cloudflare Access application **"UnitPricer beta"**; its allow-list is managed in Cloudflare Zero Trust (Access > Applications), not in this repo.

Create the droplet with `deploy/cloud-init.yaml` as user data, your SSH key attached, daily backups and monitoring on.

## Server files

Both live in `/srv/unitpricer`, mode `600`, owner `deploy`. Never commit them.

- `.env` (compose interpolation): `POSTGRES_PASSWORD`, `TUNNEL_TOKEN`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (all optional until R2 is enabled), `BACKUP_HEARTBEAT_URL` (optional), `UNITPRICER_IMAGE_TAG` (written by `deploy.sh`).
- `app.env` (Django, passed to `web` and `worker`): `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS=unitpricer.com,www.unitpricer.com,beta.unitpricer.com`, `UNITPRICER_PUBLIC_HOST`, `UNITPRICER_APP_HOST`, `UNITPRICER_PUBLIC_URL` and `UNITPRICER_SITE_URL` (both `https://unitpricer.com` in production; check E023), `UNITPRICER_COMING_SOON` (set explicitly, for example `Walmart (December)`), `UNITPRICER_POSTAL_ADDRESS`, `UNITPRICER_TRUST_CF_IP=1`, `UNITPRICER_DAILY_EMAIL_CAP`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `DJANGO_EMAIL_*` (Resend SMTP, `smtp.resend.com` port 2587, user `resend`, password = Resend API key), `DJANGO_DEFAULT_FROM_EMAIL`. `DATABASE_URL` is set by compose. Names are listed in `.env.example`. `web` and `worker` share this one file (both use `env_file: app.env`), so a change applies to both; restart both after editing it (`docker compose up -d web worker`).
- Open search (optional, in `app.env`): `UNITPRICER_LIVE_OFF=1` turns on the Open Food Facts → Open Prices live source (default off). `UNITPRICER_FETCH_THREADS` (1 or 2, default 1) sets the worker's fetch threads, and `UNITPRICER_WARM_HOURS` (default 12) the warm-refresh interval.
- Source budgets (optional, in `app.env`): `UNITPRICER_BUDGET_<OFF_SEARCH|OPENPRICES|WALMART|OFF_PRODUCT>_{CAPACITY,REFILL_PER_SECOND,DAILY_LIMIT}`. They seed a budget row only when it is first created; later changes go in the admin. `_DAILY_LIMIT` unset or blank keeps the default cap (Walmart 5000 per day, OFF product lookups 300 per day, the others none), a number sets the cap, and `unlimited` (any case) removes it. Any other value stops startup with a configuration error. `_CAPACITY` must be at least 1. Open Prices defaults to capacity 1 at 1 per second (no burst), the pace the daily sync has always kept; a 429, 503 or transport error from it blocks the shared row with backoff.

## First-deploy bootstrap

On the droplet, as `deploy`, in `/srv/unitpricer`:

```bash
install -m 600 /dev/null .env
install -m 600 /dev/null app.env
echo "POSTGRES_PASSWORD=$(openssl rand -hex 32)" >> .env   # hex is URL-safe (it goes into DATABASE_URL)
```

Then add `TUNNEL_TOKEN` and the other keys below with an editor. Images come from GHCR, which may be private and whose visibility cannot be changed through the API. Either make the `unitpricer` package public yourself (GitHub > Packages > package settings > Change visibility), or use build-on-server mode below.

The `unitpricer-backup` image is built from `deploy/backup` by CI (pushed from main) or by build-on-server mode.

## Deploy

```bash
export UNITPRICER_DEPLOY_HOST=deploy@<droplet-ip>   # optional: UNITPRICER_DEPLOY_KEY (default ~/.ssh/unitpricer_deploy)
deploy/deploy.sh            # origin/main
deploy/deploy.sh <git-ref>
```

The script runs `git fetch origin`, then refuses unless GitHub Actions workflow `CI` has a successful `push` run on `main` for that commit (which pushed the `sha-<sha>` image). It installs that commit's own `compose.prod.yaml` (so rollbacks get the old file), sets `UNITPRICER_IMAGE_TAG`, pulls, runs `manage.py check --deploy --fail-level ERROR` on the new image (an error, such as `unitpricer.E023`, stops the deploy before `migrate` and before the running containers are replaced), runs `migrate` and `createcachetable`, starts the stack, waits up to 90 s for `web` to be healthy (printing its logs and failing otherwise), prunes images and prints the SHA.

### Build-on-server mode

`UNITPRICER_BUILD_ON_SERVER=1 deploy/deploy.sh [ref]` skips pulling the app images. The green-CI check still applies. On the droplet it runs `docker build -t ghcr.io/dantearcese/unitpricer:sha-<sha> "https://github.com/DanteArcese/unitpricer.git#<sha>"` (and the same for `unitpricer-backup` from `#<sha>:deploy/backup`, when that directory exists), then `docker compose pull db cloudflared`. The remote script is passed to `ssh` as an argument, so `docker compose run` cannot swallow it.

### Open search: migrations and backfill

Migrations `catalog` 0008-0010 (open-search schema, fetch heartbeat, curate-next proxy) run automatically on deploy with `migrate`. After the first deploy that includes them, run the backfill once so existing offers get their generic phrase and measure:

```bash
docker compose run --rm -T web python manage.py backfill_generic </dev/null
```

It is safe to re-run. New offers get these fields at ingestion. Raise or remove a source cap with `unlimited` as described under source budgets; budgets already in the database change only in the admin.

**The migrate window.** `deploy.sh` runs `migrate` while the old `worker` is still up. Migration 0008 rewrites the `catalog_offer` table (the stored search vector), so an old-worker tick that writes offers during it can wait on the lock or fail. That is harmless: a failed run is retried on the next tick, and the new worker starts with the stack. To avoid the noise, stop the worker first (`docker compose stop worker` on the droplet) and deploy; the deploy starts it again.

**What the backfill covers.** `backfill_generic` fills the generic measure and phrase on offers already in the database. Two things fill in later, on their own:

- Generic *observed* offers (in-store prices that match no category) did not exist before this release, since the sync dropped them. They appear after the next daily Open Prices sync.
- Open Food Facts labels and category paths on existing offers fill in as their OFF entries refresh (live offers on their next ingestion, at most 12 h; observed offers on the next daily sync).

A worker shutdown during the daily Open Prices sync ends the run as `interrupted` between offers: offers already written stay, nothing is deleted, and the sync runs again on the next start.

## Rollback

`deploy/deploy.sh <older-sha>`. Migrations are not reversed: only roll back across commits with no new migration, or restore the database from a backup first. Rolling back past the public launch (#77) needs the `app.env` changes in `docs/launch.md` (Rollback) as well.

## Logs

```bash
ssh deploy@<ip> 'cd /srv/unitpricer && docker compose logs -f web worker'
```

## One-off commands

```bash
docker compose run --rm -T web python manage.py <command> </dev/null   # drop -T and </dev/null for an interactive shell
```

### Open Prices (in-store shelf prices)

Off by default. To enable, add `UNITPRICER_OPEN_PRICES=1` to `/srv/unitpricer/app.env` (optional: `UNITPRICER_OPEN_PRICES_ASSISTED=0` to drop AI-assisted entries, `UNITPRICER_OPEN_PRICES_HOURS`, `UNITPRICER_OPEN_PRICES_COUNTRY`), then `docker compose up -d web worker`. The worker starts the first sync within one tick (about five minutes) after the restart, then syncs once a day; there is no manual step. The first sync makes about 110 requests and takes a few minutes. Do not also run `open_prices_sync` by hand while the flag is on: the command refuses when an Open Prices run started within the last hour is still RUNNING (override with `--force`), and a pull far smaller than the stored data deletes nothing unless you pass `--allow-deletions`. To pull manually (for example with the flag off):

```bash
docker compose run --rm -T web python manage.py open_prices_sync </dev/null
```

Confirm the `ingest connector=openprices category=all status=succeeded` log line shows `fetched` of about 10,500 (about 5,000 with assisted entries off), and that `/c/dairy/milk/` on the beta host shows "Seen in stores". The ODbL export for a data request: `docker compose run --rm -T web python manage.py export_open_prices --out /tmp/open-prices.jsonl.gz </dev/null` (write to a mounted path to keep the file). The public site has no dump link yet; that waits for the public launch.

#### Sizes from Open Food Facts

Many Open Prices entries carry no size, so their offers have no unit price. After each successful Open Prices sync the worker looks up observed offers' barcodes in the Open Food Facts product API: barcodes with no cached entry first (curated categories before generic offers, newest first), then entries without a quantity that were last checked more than 30 days ago. Each answer (a 404 too) is cached as an `OffEntry`, and the offers on that barcode are normalized again, so size, labels and categories come from OFF. Requests draw from the `off-product` budget: 1 per second, at most 300 a day (`UNITPRICER_BUDGET_OFF_PRODUCT_*`, or the admin). A 429, 5xx or transport error stores nothing, backs the budget off and is retried on a later run (not in the same run); a 404 or another non-retryable answer (400, 403, unreadable) is recorded like "not found" and asked again only after 30 days. A cached entry fills only the fields it has; labels, size and categories it lacks still come from Open Prices. Look for `ingest connector=off-product category=all status=succeeded` in the worker log.

To run it once by hand (for example right after enabling Open Prices, or with the flag off):

```bash
docker compose run --rm -T web python manage.py off_observed_sizes </dev/null
docker compose run --rm -T web python manage.py off_observed_sizes --limit 50 </dev/null   # fewer barcodes
```

It prints `status=succeeded requests=… stored=… not_found=… rejected=… failed=… renormalized=… matched=…`. A manual run counts toward the day's 300; once the cap is reached it stops with `note=budget exhausted` and the rest waits for the next day. Offers whose barcode OFF has no size for stay unresolved; nothing is guessed.

### Purge sample data

Run before enabling a real Walmart key, and on any database that ever held recorded data (dry run first, then `--apply`):

```bash
docker compose run --rm -T web python manage.py purge_sample_data </dev/null
docker compose run --rm -T web python manage.py purge_sample_data --apply </dev/null
```

`refresh --recordings` and `demo_data` refuse to run when `DEBUG` is off.

### Uptime monitoring

Monitor `https://unitpricer.com/healthz` (5 minutes, expects `"status": "ok"`) and the backup heartbeat. Cloudflare Bot Fight Mode and the rate rule are described in `docs/launch.md`; confirm the monitor still gets 200 after enabling them.

## Backups and restore

The `backup` service runs `pg_dump -Fc` at 03:30 UTC (`BACKUP_AT`), checks the dump with `pg_restore --list`, and only then stores it. A failed dump or failed check stores nothing and sends no heartbeat. If `BACKUP_HEARTBEAT_URL` is set it is pinged after each good run, so point an uptime monitor at it. DigitalOcean daily droplet backups (kept 7 days) are the second layer.

**Without R2 (current default).** When any of `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` is empty, the dump stays in the `backups` volume (`/backups`), the newest 7 `unitpricer-*.dump` files are kept, and the log says `R2 not configured; kept dump locally`. This copy lives on the same droplet, so it does not survive losing the droplet; the daily provider backup covers that.

**With R2.** The dump is also uploaded to bucket `unitpricer-backups`, prefix `pg/`; the bucket's lifecycle rule deletes objects after 30 days (the script does no remote retention). The local newest-7 copy is still kept.

### Enabling R2 later

1. In Cloudflare, enable R2, create bucket `unitpricer-backups`, and add a lifecycle rule deleting objects under `pg/` after 30 days.
2. Create an R2 API token with Object Read & Write limited to that bucket.
3. Put `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` in `/srv/unitpricer/.env` (mode 600), then `docker compose up -d backup`.
4. Trigger one run and confirm the object appears: `docker compose exec backup backup.sh once`.

### Restore drill

Restore into a scratch database, never straight over production first.

```bash
cd /srv/unitpricer
docker compose exec db createdb -U unitpricer drill
docker compose exec backup backup.sh restore-latest "postgres://unitpricer:$POSTGRES_PASSWORD@db/drill"
docker compose exec db psql -U unitpricer drill -c 'select count(*) from django_migrations'
docker compose exec db dropdb -U unitpricer drill
```

`restore-latest` takes the newest object under `pg/` when R2 is configured, otherwise the newest local dump, and runs `pg_restore --no-owner --clean --if-exists`. Compare row counts of a few real tables with production, then record the drill:

| Date | Dump used | Restored to | Rows verified | Time taken | Notes |
|---|---|---|---|---|---|
| _TBD_ | | | | | |

## SSH access and the firewall

SSH is key-only, root login is off, only user `deploy` may log in, and `ufw` denies all incoming except OpenSSH. To restrict SSH to your current address (or lift it again):

```bash
sudo ufw delete allow OpenSSH
sudo ufw allow from <your-ip> to any port 22 proto tcp
sudo ufw status numbered

sudo ufw delete <rule-number>     # remove the restriction
sudo ufw allow OpenSSH
```

Keep a second session open while changing rules; the DigitalOcean web console is the fallback.

## Rotating secrets

- **Django `SECRET_KEY`:** put the old key (comma-separated if several) in `DJANGO_SECRET_KEY_FALLBACKS` in `app.env` (read into Django's `SECRET_KEY_FALLBACKS`), set the new `DJANGO_SECRET_KEY`, run `docker compose up -d web worker`, and remove the fallback after sessions and tokens have aged out (waitlist tokens: 7 days).
- **Resend key:** create a new key in Resend, update `DJANGO_EMAIL_HOST_PASSWORD` in `app.env`, `docker compose up -d web worker`, delete the old key.
- **Tunnel token:** rotate in Cloudflare Zero Trust (Networks > Tunnels > `unitpricer-prod`), update `TUNNEL_TOKEN` in `.env`, `docker compose up -d cloudflared`.

## Error reporting (Sentry, optional)

Off unless `SENTRY_DSN` is set in `app.env` (blank in `.env.example`). Set `SENTRY_ENVIRONMENT` (default `production`; `development` when `DJANGO_DEBUG=1`), then `docker compose up -d web worker`; both processes load the settings and report. `config/sentry.py` sends no PII (`send_default_pii` off, no local variables, no request body, no performance tracing) and `scrub_event` strips cookies, all headers except `User-Agent`, query strings, form data, user info, emails and sign-in/signup tokens before sending. In the Sentry project, also enable "Prevent storing of IP addresses". If you change what is sent, change the Sentry line on the privacy page to match.
