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
```

## Standards

- Python with `uv` only. No `pip install` or `python -m venv`.
- Track work in GitHub issues. Reference the issue in commits and PRs.
- Add or update tests with every feature. Keep the suite passing.
- Secrets live in `.env` (gitignored). Document variable names in `.env.example`. Do not read `.env`.
