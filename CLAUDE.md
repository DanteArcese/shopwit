# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Project

UnitPricer: open-source web app ranking groceries and household goods by fair price per unit, filtered by attributes (e.g. any 2% milk) rather than UPC. Django + HTMX + Postgres. No AI/LLMs anywhere. Design spec: `docs/superpowers/specs/2026-11-07-unitpricer-design.md`.

## Commands

```bash
uv sync              # Install dependencies
uv add <package>     # Add a dependency
uv run pytest        # Run tests
uv run --env-file .env <command>   # Run with local secrets
uv run --env-file .env python manage.py runserver   # Run the app (needs DJANGO_SECRET_KEY, DATABASE_URL)
docker compose up    # Or run db + web + worker in containers
UNITPRICER_DEPLOY_HOST=deploy@<ip> deploy/deploy.sh [ref]   # Deploy a CI-green commit (see docs/deploy.md)
```

First run (empty database): `cp .env.example .env`, then in `.env` set `DJANGO_DEBUG=1` (local dev, so no `DJANGO_SECRET_KEY` is needed) and `DATABASE_URL` (entries in the file, not shell prefixes). Then `uv run --env-file .env python manage.py migrate`, `uv run --env-file .env python manage.py createcachetable` (rate limits use the database cache) and `uv run --env-file .env python manage.py sync_categories`, before starting the worker (`run_scheduler`) or `runserver`. With no categories loaded the scheduler has nothing to refresh. `uv run --env-file .env python manage.py refresh --connector walmart --recordings tests/connectors/recordings/walmart` runs offline; categories without recordings are reported `failed`.

Open Prices: `uv run --env-file .env python manage.py open_prices_sync [--recordings tests/connectors/recordings/open_prices]` pulls the window now (the scheduler does it daily when `UNITPRICER_OPEN_PRICES=1`); `manage.py export_open_prices --out FILE.jsonl.gz` writes the ODbL export.

Open-ended search: after migrating (through catalog 0010), run `uv run --env-file .env python manage.py backfill_generic` once to give existing offers their generic phrase and measure. `manage.py bench_search` (DEBUG only) is a local p95 check of the search path. `UNITPRICER_LIVE_OFF=1` turns on the OFF → Open Prices live source (off by default). Budgets live in the admin; a changed `UNITPRICER_BUDGET_*` setting applies only to new rows. The worker also runs the fetch thread(s) (`UNITPRICER_FETCH_THREADS`).

Try it locally (needs `DJANGO_DEBUG=1`, migrations applied): `uv run --env-file .env python manage.py demo_data` loads categories and recorded Walmart milk offers offline (no `WALMART_*` vars), then `uv run --env-file .env python manage.py runserver` and open `/c/dairy/milk/` on it. `demo_data` refuses to run when `DEBUG` is off.

Tests need real Postgres. With `DATABASE_URL` unset, `tests/postgres_plugin.py` starts a throwaway cluster in `.pytest-pg/` from system binaries (one-time `sudo apt install postgresql` required). CI uses a postgres:17 service.

## Standards

- Python with `uv` only. No `pip install` or `python -m venv`.
- Track work in GitHub issues. Reference the issue in commits and PRs.
- Add or update tests with every feature. Keep the suite passing.
- Secrets live in `.env` (gitignored). Document variable names in `.env.example`. Do not read `.env`.
