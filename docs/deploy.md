# Deploying UnitPricer

First deploy and day-to-day operations. Files: `compose.prod.yaml`, `deploy/cloud-init.yaml`, `deploy/deploy.sh`.

## Topology

One DigitalOcean droplet (Ubuntu 24.04, `s-1vcpu-2gb`, `nyc3`) running Docker Compose: `db` (Postgres 17), `web` (gunicorn), `worker` (`run_scheduler`), `cloudflared` and `backup`. No container publishes a port. Traffic arrives through the Cloudflare tunnel **`unitpricer-prod`** (remotely managed; ingress `unitpricer.com`, `www.unitpricer.com` and `beta.unitpricer.com` all go to `http://web:8000`). The app host `beta.unitpricer.com` sits behind the Cloudflare Access application **"UnitPricer beta"**; its allow-list is managed in Cloudflare Zero Trust (Access > Applications), not in this repo.

Create the droplet with `deploy/cloud-init.yaml` as user data, your SSH key attached, weekly backups and monitoring on.

## Server files

Both live in `/srv/unitpricer`, mode `600`, owner `deploy`. Never commit them.

- `.env` (compose interpolation): `POSTGRES_PASSWORD`, `TUNNEL_TOKEN`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (all optional until R2 is enabled), `BACKUP_HEARTBEAT_URL` (optional), `UNITPRICER_IMAGE_TAG` (written by `deploy.sh`).
- `app.env` (Django, passed to `web` and `worker`): `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS=unitpricer.com,www.unitpricer.com,beta.unitpricer.com`, `UNITPRICER_PUBLIC_HOST`, `UNITPRICER_APP_HOST`, `UNITPRICER_PUBLIC_URL`, `UNITPRICER_SITE_URL`, `UNITPRICER_POSTAL_ADDRESS`, `UNITPRICER_TRUST_CF_IP=1`, `UNITPRICER_DAILY_EMAIL_CAP`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `DJANGO_EMAIL_*` (Resend SMTP, `smtp.resend.com` port 2587, user `resend`, password = Resend API key), `DJANGO_DEFAULT_FROM_EMAIL`. `DATABASE_URL` is set by compose. Names are listed in `.env.example`.

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

The script runs `git fetch origin`, then refuses unless GitHub Actions workflow `CI` has a successful `push` run on `main` for that commit (which pushed the `sha-<sha>` image). It installs that commit's own `compose.prod.yaml` (so rollbacks get the old file), sets `UNITPRICER_IMAGE_TAG`, pulls, runs `migrate` and `createcachetable`, starts the stack, waits up to 90 s for `web` to be healthy (printing its logs and failing otherwise), prunes images and prints the SHA.

### Build-on-server mode

`UNITPRICER_BUILD_ON_SERVER=1 deploy/deploy.sh [ref]` skips pulling the app images. The green-CI check still applies. On the droplet it runs `docker build -t ghcr.io/dantearcese/unitpricer:sha-<sha> "https://github.com/DanteArcese/unitpricer.git#<sha>"` (and the same for `unitpricer-backup` from `#<sha>:deploy/backup`, when that directory exists), then `docker compose pull db cloudflared`. The remote script is passed to `ssh` as an argument, so `docker compose run` cannot swallow it.

## Rollback

`deploy/deploy.sh <older-sha>`. Migrations are not reversed: only roll back across commits with no new migration, or restore the database from a backup first.

## Logs

```bash
ssh deploy@<ip> 'cd /srv/unitpricer && docker compose logs -f web worker'
```

## One-off commands

```bash
docker compose run --rm -T web python manage.py <command> </dev/null   # drop -T and </dev/null for an interactive shell
```

## Backups and restore

The `backup` service runs `pg_dump -Fc` at 03:30 UTC (`BACKUP_AT`), checks the dump with `pg_restore --list`, and only then stores it. A failed dump or failed check stores nothing and sends no heartbeat. If `BACKUP_HEARTBEAT_URL` is set it is pinged after each good run, so point an uptime monitor at it. DigitalOcean weekly droplet backups are the second layer.

**Without R2 (current default).** When any of `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` is empty, the dump stays in the `backups` volume (`/backups`), the newest 7 `unitpricer-*.dump` files are kept, and the log says `R2 not configured; kept dump locally`. This copy lives on the same droplet, so it does not survive losing the droplet; the weekly provider backup covers that.

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

- **Django `SECRET_KEY`:** (settings do not yet read `DJANGO_SECRET_KEY_FALLBACKS`; wire it to Django's `SECRET_KEY_FALLBACKS` before the first rotation) put the old key in `DJANGO_SECRET_KEY_FALLBACKS` in `app.env`, set the new `DJANGO_SECRET_KEY`, run `docker compose up -d web worker`, and remove the fallback after sessions and tokens have aged out (waitlist tokens: 7 days).
- **Resend key:** create a new key in Resend, update `DJANGO_EMAIL_HOST_PASSWORD` in `app.env`, `docker compose up -d web worker`, delete the old key.
- **Tunnel token:** rotate in Cloudflare Zero Trust (Networks > Tunnels > `unitpricer-prod`), update `TUNNEL_TOKEN` in `.env`, `docker compose up -d cloudflared`.
