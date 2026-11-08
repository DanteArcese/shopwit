# Production hosting options for Checkpoint B

Research for issue #36. Date of all pricing checks: **2026-11-07**. This document recommends; the maintainer decides (roadmap Checkpoint B item 3, spec §12).

## What has to be hosted

From `compose.yaml`, `Dockerfile`, `config/settings.py`:

- **web**: Django 5.2 + HTMX, server-rendered. Compose runs `runserver`; production needs gunicorn (or similar) and static serving (`STATIC_ROOT` is set; WhiteNoise or a CDN is not yet wired, so that is pre-deploy work on any host).
- **worker**: `manage.py run_scheduler`, a long-running process (refresh ~12 h, Open Food Facts daily delta ~45 MB/day). Must not be a request-bound or time-limited function.
- **Postgres 17**, small (tens of thousands of rows). Config is `DATABASE_URL`, secrets are env vars (`DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, affiliate IDs), which fits every host below.
- Requirements: env-var secrets, daily DB backup with a **tested restore**, TLS, logs, uptime monitoring. Domain is on Cloudflare (Registrar + DNS, Email Routing in use, so keep MX/TXT records untouched).
- Constraint: solo maintainer, hobby budget, low ops. The maintainer's WSL machine has no Docker daemon today, so "build and run locally" is not a prerequisite for any option that builds remotely (PaaS, or CI building images).

## Comparison at a glance

| | VPS + Compose | Fly.io | Railway | Render | Vercel |
|---|---|---|---|---|---|
| Realistic cost/mo (web+worker+DB+backups) | ~$13-16 | ~$6-10 self-run DB; ~$45+ managed | ~$5-12 (usage) | ~$20-21 | $20+ and DB extra; poor fit |
| Postgres | self-managed container | MPG from $38, or self-run | template service on volume | managed, from $6 | none native (Marketplace) |
| Worker | Compose service | process group / 2nd machine | 2nd service | background worker | Daemons (beta) |
| Backups | you script them | MPG: included; self-run: you | you (volume backups: unverified) | PITR 3 d (Hobby) | depends on DB vendor |
| Ops burden | highest | medium | low | lowest | n/a |
| Lock-in | none | low | low | low | medium-high |

## 1. Small VPS (Hetzner / DigitalOcean / Lightsail) + Docker Compose

- **Cost.** Hetzner Regular Performance, EU, ex-VAT: 1 vCPU/1 GB €6.49, 1 vCPU/2 GB €11.99, 2 vCPU/4 GB €19.99, 20 TB traffic, bill capped at the monthly price; backups add a percentage of the instance price (page says "from €1.198", daily, 7 slots); snapshots €0.0143/GB ([Hetzner](https://www.hetzner.com/cloud/regular-performance)). The page does not list CX22-class plans, so the old CX22 name is unverified; US locations are pricier (CPX11 €17.99). DigitalOcean Basic Droplet 1 GiB $6, 2 GiB $12; backups 20% weekly or 30% daily ([DO](https://www.digitalocean.com/pricing/droplets)). A 2 GB box runs web + worker + Postgres comfortably at this scale: **~$13-16/mo** including provider backups. Lightsail not fetched (unverified).
- **Worker/DB.** Same `compose.yaml` shape plus gunicorn and a `restart: unless-stopped` policy. Postgres is a container with a volume: you own upgrades, tuning and disk-full risk.
- **Backups.** Provider snapshots are crash-consistent only, so also run a nightly `pg_dump` (cron container) to off-box storage (e.g. Cloudflare R2, free-tier limits unverified) and rehearse a restore into a scratch DB. This is the largest piece of work in this option.
- **Deploy.** GitHub Actions builds the image to GHCR, SSH (or a webhook) runs `docker compose pull && up -d`. Not `git push`-native; ~1 evening to set up.
- **TLS/Cloudflare.** Two good patterns: (a) **Cloudflare Tunnel** (`cloudflared` container, free): no open inbound ports, orange-cloud proxied, TLS terminates at Cloudflare, no Caddy needed; (b) Caddy with orange-cloud and a Cloudflare Origin Certificate or DNS-01 challenge, or DNS-only with Caddy's automatic Let's Encrypt. Tunnel is the lowest-maintenance and hides the origin IP.
- **Logs/monitoring.** `docker logs` (set log rotation), free uptime monitor (UptimeRobot / Better Stack free tiers, unverified limits), Sentry free tier for errors.
- **Lock-in.** None; the Compose file moves anywhere.
- **Gotchas.** You patch the OS (unattended-upgrades), firewall, SSH keys; a single box is a single point of failure; no preview environments.

## 2. Fly.io

- **Cost.** shared-cpu-1x: 256 MB $2.19, 512 MB $3.69, 1 GB $6.70/mo; volumes $0.15/GB; snapshots $0.08/GB (first 10 GB/mo free); no free tier for new orgs, no platform fee ([Fly pricing](https://docs.fly.io/about/pricing)). **Managed Postgres** is Basic $38/mo (shared-2x, 1 GB) plus $0.28/GB, HA, backups and pooling included ([Fly MPG](https://docs.fly.io/mpg)); retention periods not stated on that page. Cheaper path: web 512 MB + worker 512 MB + a self-run Postgres machine with a volume, roughly **$8-12/mo**, but then backups and upgrades are yours (Fly's older "unmanaged Postgres" app is, to my knowledge, deprecated; unverified here). With MPG: **~$45+/mo**.
- **Worker.** A second process group in `fly.toml` (`worker = "uv run python manage.py run_scheduler"`) with `auto_stop_machines = false`; web can auto-stop to save money.
- **Deploy.** `fly deploy` from CI (GitHub Action) using a Dockerfile; builds remotely, so no local Docker daemon needed.
- **TLS/Cloudflare.** Fly issues certs per custom domain (HTTP-01 or DNS challenge). Works DNS-only; with orange-cloud use SSL mode Full (strict) and expect cert-issuance quirks until DNS is set up (behavior from experience, unverified).
- **Logs/monitoring.** `fly logs`, built-in Prometheus metrics; add external uptime + Sentry.
- **Lock-in.** Low (Dockerfile) but `fly.toml`, private networking and volumes are Fly-specific.
- **Gotchas.** Volumes are single-host, pinned to a region; MPG price is out of proportion to this app; machine-level billing is easy to misjudge.

## 3. Railway

- **Cost.** Hobby $5/mo including $5 usage credit; usage rates about $20/vCPU-mo, $10/GB-RAM-mo, $0.15/GB-mo volume, $0.05/GB egress; Free plan is $1/mo credit, no custom domains; Hobby max volume 5 GB, 7-day logs, 2 custom domains ([Railway](https://railway.com/pricing)). An idle-ish Django web + worker + Postgres at ~0.25 GB each is likely **$5-12/mo** total (my estimate from the rates; usage-based, so it floats).
- **Worker.** A separate service from the same repo with a different start command.
- **Postgres.** One-click Postgres is a service on a volume, not a fully managed DB. Scheduled backups exist as a volume feature, but plan availability and restore semantics are **unverified**; assume you still need a `pg_dump` cron to off-site storage.
- **Deploy.** GitHub push auto-deploys (Dockerfile or Nixpacks); the nicest flow of the five.
- **TLS/Cloudflare.** Custom domain with a CNAME; works DNS-only; proxied needs Full (strict).
- **Logs.** In dashboard (7 days on Hobby); uptime/Sentry external.
- **Gotchas.** Usage billing is unpredictable; Hobby caps at 2 custom domains; DB is less "managed" than the marketing suggests.

## 4. Render

- **Cost.** Workspace Hobby $0 (Pro $25/mo); web service from $7/mo (0.5 CPU/512 MB), Postgres from $6/mo (256 MB) or $19/mo (1 GB), storage $0.30/GB; PITR 3 days on Hobby, 7 on Pro ([Render pricing](https://render.com/pricing.md)). Background worker price was not shown on the fetched page (likely $7 Starter, **unverified**). Estimate: web $7 + worker $7 + Postgres $6-19 = **~$20-33/mo**. Free web services sleep and free Postgres expires after 30 days (secondary sources, unverified), so neither fits.
- **Worker.** First-class "Background Worker" service type.
- **Postgres.** Truly managed with daily backups and PITR; restore is a dashboard action. Best backup story for the money, though the 256 MB tier is tight with an OFF delta import (check RAM).
- **Deploy.** Git push auto-deploy, `render.yaml` blueprint, Dockerfile supported.
- **TLS/Cloudflare.** Automatic certs; documented support for Cloudflare DNS (DNS-only works cleanly; proxied needs Full strict).
- **Logs.** Dashboard log streaming; log drains need a paid workspace (unverified).
- **Lock-in.** Low. **Gotchas.** Highest per-month cost among the realistic options; shared-CPU Starter is slow for the daily sync.

## 5. Vercel (honest fit)

- **Web.** Vercel's runtime is serverless functions; Django runs via the Python runtime as functions (stateless, 300 s max on Hobby, up to 800 s on Pro; [Hobby limits](https://vercel.com/docs/plans/hobby)). Server-rendered Django works but cold starts, no persistent connections and WSGI-on-functions packaging are friction.
- **Worker.** Cron/functions cannot host `run_scheduler`. **Vercel Daemons** (container workers) exist but are **beta**, with a Hobby quota of 5 CPU-hours and 420 GB-hours/mo, stopping when exceeded ([Daemons](https://vercel.com/kb/guide/vercel-daemons)); production-only, you manage SIGTERM and retries. A 24/7 worker would need Pro (rates not published on that page).
- **Postgres.** No native managed Postgres confirmed; use a Marketplace vendor (Neon, Supabase), which adds a second bill and a network hop.
- **Licensing.** Hobby is "non-commercial, personal use only"; an affiliate-revenue site is commercial, so Pro ($20/user/mo, plus usage) would be required.
- **Verdict.** Poor fit: three vendors, a beta worker, Pro plan, and a serverless model that fights Django. Not recommended.

## 6. Cloudflare-native (Containers/Workers)

Containers need Workers Paid ($5/mo) and are billed per active 10 ms; included 25 GiB-hours RAM, 375 vCPU-minutes; instance types from 256 MiB to 12 GiB ([pricing](https://developers.cloudflare.com/containers/pricing/)). Each container is fronted by a Worker + Durable Object, sleeps when idle, and has ephemeral disk. That is a request-driven model; a Django app plus an always-on scheduler plus Postgres (Postgres only via Hyperdrive to an external DB; D1 is SQLite) is a poor fit. GA status not stated on the page. **Skip.** Cloudflare is still the right place for DNS, Tunnel, Email Routing, and optionally R2 for backups.

## Recommendation (recommend, not decide)

| Goal | Option | Why |
|---|---|---|
| **Best overall** | Hetzner (or DO) 2 GB VPS + Docker Compose + Cloudflare Tunnel | ~$13-16/mo flat, same Compose file as dev, no lock-in, full control of worker and DB, Tunnel removes TLS and firewall work. Cost: you own backups and patching. |
| **Cheapest acceptable** | Fly.io, 2 small machines + self-run Postgres volume (~$8-12) or Railway Hobby (~$5-12) | Lower, but you still script backups, and usage billing varies. VPS is only ~$5 more and simpler. |
| **Lowest ops** | Render (web $7 + worker + managed Postgres) | Managed DB with PITR, git-push deploys, ~$20-33/mo. Pick this if "never SSH into a box" outweighs ~$10/mo. |

Not recommended: Vercel, Cloudflare Containers, Fly MPG ($38 floor).

## Setup steps for the recommended option

1. Add production pieces to the repo: gunicorn, WhiteNoise, `CSRF_TRUSTED_ORIGINS`, secure-cookie/HSTS settings, `compose.prod.yaml` (no `runserver`, no published DB port, restart policies, log rotation).
2. Create a Hetzner project and 2 GB server (EU or US region per user base); SSH keys only, disable password login, enable `ufw` and unattended upgrades, install Docker.
3. Create a Cloudflare Tunnel (Zero Trust dashboard); add the `cloudflared` service to Compose; route `unitpricer.com` and `www` to `http://web:8000`. Leave MX/TXT (Email Routing) records alone.
4. Put secrets in a server-side `.env` (never committed) referenced via `env_file`; keep `.env.example` current.
5. GitHub Actions: build image, push to GHCR, SSH deploy `docker compose pull && up -d`; run `migrate` and `sync_categories` once per the compose.yaml note.
6. Nightly `pg_dump` container to Cloudflare R2 (or Hetzner Storage Box) with 14-30 day retention; enable provider backups as a second layer.
7. **Restore drill**: restore the latest dump into a scratch Postgres 17 container and run the app against it; record the steps in `docs/` and repeat quarterly.
8. Uptime monitor on `/` (and a health URL) with email alert; Sentry free tier for Django errors; check that the scheduler's last-refresh time is surfaced somewhere so a dead worker is noticed.
9. Cloudflare: enable DNSSEC, add DMARC (already open in Checkpoint B), SSL mode Full (strict), cache static paths.
10. Publish the affiliate disclosure page, then register the live domain with Sovrn and the other programs.

## Open questions for the maintainer

- Is ~$13-16/mo flat acceptable, or is the target closer to $5-10 (favoring Fly/Railway)?
- Comfortable owning OS patching and a restore drill, or is managed Postgres worth ~$10-15/mo more (Render)?
- Primary audience region (US vs EU)? Hetzner's US locations cost noticeably more; DO/Lightsail are US-native.
- Acceptable RPO/RTO: is losing up to 24 h of data fine? Product data is rebuildable from sources; user accounts/lists (M4) are not. This may change the backup design after M4.
- Preference for `git push` deploys vs. a CI-driven SSH deploy?
- Will SEO pages be cached behind Cloudflare (reducing origin load) or rendered dynamically?
- Unverified items to confirm on the vendor sites before purchase: Render worker price, Railway DB backups, Fly MPG backup retention, Hetzner plan names and US pricing, free-tier limits of uptime/Sentry/R2.
