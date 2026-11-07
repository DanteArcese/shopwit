# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Project

shopwit (working codename): open-source web app ranking groceries and household goods by fair price per unit, filtered by attributes (e.g. any 2% milk) rather than UPC. Django + HTMX + Postgres. No AI/LLMs anywhere. Design spec: `docs/superpowers/specs/2026-11-07-shopwit-design.md`.

## Commands

```bash
uv sync              # Install dependencies
uv add <package>     # Add a dependency
uv run pytest        # Run tests
uv run --env-file .env <command>   # Run with local secrets
uv run --env-file .env python manage.py runserver   # Run the app (needs DJANGO_SECRET_KEY, DATABASE_URL)
docker compose up    # Or run db + web + worker in containers
```

First run (empty database): `uv run python manage.py migrate`, then `uv run python manage.py sync_categories`, before starting the worker (`run_scheduler`). With no categories loaded the scheduler has nothing to refresh. `refresh --connector walmart --recordings tests/connectors/recordings/walmart` runs offline; categories without recordings are reported `failed`.

Tests need real Postgres. With `DATABASE_URL` unset, `tests/pgserver_plugin.py` starts a throwaway cluster in `.pytest-pg/` from system binaries (one-time `sudo apt install postgresql`). CI uses a postgres:17 service.

## Standards

- Python with `uv` only. No `pip install` or `python -m venv`.
- Track work in GitHub issues. Reference the issue in commits and PRs.
- Add or update tests with every feature. Keep the suite passing.
- Secrets live in `.env` (gitignored). Document variable names in `.env.example`. Do not read `.env`.
