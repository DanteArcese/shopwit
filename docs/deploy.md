# Deploying UnitPricer

First deploy and day-to-day operations. Files: `compose.prod.yaml`, `deploy/cloud-init.yaml`, `deploy/deploy.sh`.

## Topology

One DigitalOcean droplet (Ubuntu 24.04, `s-1vcpu-2gb`, `nyc3`) running Docker Compose: `db` (Postgres 17), `web` (gunicorn), `worker` (`run_scheduler`), `cloudflared` and `backup`. No container publishes a port. Traffic arrives through the Cloudflare tunnel **`unitpricer-prod`** (remotely managed; ingress `unitpricer.com`, `www.unitpricer.com` and `beta.unitpricer.com` all go to `http://web:8000`). The app host `beta.unitpricer.com` sits behind the Cloudflare Access application **"UnitPricer beta"**; its allow-list is managed in Cloudflare Zero Trust (Access > Applications), not in this repo.

Create the droplet with `deploy/cloud-init.yaml` as user data, your SSH key attached, weekly backups and monitoring on.

## Server files

Both live in `/srv/unitpricer`, mode `600`, owner `deploy`. Never commit them.

- `.env` (compose interpolation): `POSTGRES_PASSWORD`, `TUNNEL_TOKEN`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (all optional until R2 is enabled), `BACKUP_HEARTBEAT_URL` (optional), `UNITPRICER_IMAGE_TAG` (written by `deploy.sh`).
- `app.env` (Django, passed to `web` and `worker`): `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS=unitpricer.com,www.unitpricer.com,beta.unitpricer.com`, `UNITPRICER_PUBLIC_HOST`, `UNITPRICER_APP_HOST`, `UNITPRICER_PUBLIC_URL`, `UNITPRICER_SITE_URL`, `UNITPRICER_POSTAL_ADDRESS`, `UNITPRICER_TRUST_CF_IP=1`, `UNITPRICER_DAILY_EMAIL_CAP`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `DJANGO_EMAIL_*` (Resend SMTP, `smtp.resend.com` port 2587, user `resend`, password = Resend API key), `DJANGO_DEFAULT_FROM_EMAIL`. `DATABASE_URL` is set by compose. Names are listed in `.env.example`.

## Deploy

```bash
export UNITPRICER_DEPLOY_HOST=deploy@<droplet-ip>   # optional: UNITPRICER_DEPLOY_KEY (default ~/.ssh/unitpricer_deploy)
deploy/deploy.sh            # origin/main
deploy/deploy.sh <git-ref>
```

The script refuses unless GitHub Actions workflow `CI` has a successful `push` run for that commit (which pushed the `sha-<sha>` image). It copies `compose.prod.yaml`, sets `UNITPRICER_IMAGE_TAG`, pulls, runs `migrate` and `createcachetable`, starts the stack, waits up to 90 s for `web` to be healthy (printing its logs and failing otherwise), prunes images and prints the SHA.

## Rollback

`deploy/deploy.sh <older-sha>`. Migrations are not reversed: only roll back across commits with no new migration, or restore the database from a backup first.

## Logs

```bash
ssh deploy@<ip> 'cd /srv/unitpricer && docker compose logs -f web worker'
```

## One-off commands

```bash
docker compose run --rm web python manage.py <command>
```

## Backups and restore

Nightly `pg_dump -Fc` at 03:30 UTC by the `backup` service to R2 bucket `unitpricer-backups` (prefix `pg/`, 30-day lifecycle rule). Until R2 is enabled, dumps go to the `backups` volume mounted at `/backups`. DigitalOcean weekly backups are the second layer. Restore steps are in the backup image's documentation (Task 9); drill them and record results:

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
