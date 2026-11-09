# UnitPricer

UnitPricer ranks groceries and household goods by **fair price per unit**, filtered by
attributes rather than by exact product. Ask for "any 2% milk" or "any 2-ply toilet paper"
and see the cheapest options across retailers, per millilitre, per egg, or per square metre
of paper.

## Why

- **Compare by spec, not SKU.** Brand is an optional filter, never a requirement.
- **Honest ranking.** Order depends only on unit price and your filters, never on affiliate commission.
- **Never guesses.** Unknown attributes are shown as unknown. Products whose unit price can't be
  computed are listed but unranked, with the reason.
- **No AI anywhere.** No LLMs in the product or the ingestion pipeline; extraction is deterministic rules.
- **Free.** Open source under AGPL-3.0-or-later.

## Status

Early development. The normalizer (M1), catalog and ingestion (M2), and browse and search (M3)
are done. Live retailer data is pending API access, so for now the site runs on recorded
Walmart responses. See the [roadmap](docs/roadmap.md) and the
[design spec](docs/superpowers/specs/2026-11-07-unitpricer-design.md).

## Quick start

Prerequisites: Python 3.13, [uv](https://docs.astral.sh/uv/), and PostgreSQL
(`sudo apt install postgresql` is enough for the tests; for the dev server use your own
database or the Docker Compose `db` service below).

```bash
cp .env.example .env
# In .env set DJANGO_DEBUG=1 (local dev; no DJANGO_SECRET_KEY needed) and DATABASE_URL, e.g.
#   DATABASE_URL=postgres://unitpricer:unitpricer@localhost:5432/unitpricer
uv sync
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py demo_data      # categories + recorded Walmart milk offers, offline
uv run --env-file .env python manage.py runserver
```

Open-ended search needs one extra step on an existing database:
`uv run --env-file .env python manage.py backfill_generic`, run once after migrating to catalog 0010.
`manage.py bench_search` (DEBUG only) is a local p95 check. Set `UNITPRICER_LIVE_OFF=1` to turn on the
Open Food Facts → Open Prices live source (off by default). Budgets live in the admin, and a changed
`UNITPRICER_BUDGET_*` setting applies only to new rows.

Then open <http://127.0.0.1:8000/c/dairy/milk/>. `demo_data` needs no retailer keys and refuses
to run when `DJANGO_DEBUG` is off (so does `refresh --recordings`). Launch steps: `docs/launch.md`. Never commit `.env`.

### Tests

```bash
uv run pytest
```

With `DATABASE_URL` unset, the tests start a throwaway Postgres cluster in `.pytest-pg/` from
your system PostgreSQL binaries. CI uses a `postgres:17` service.

### Docker Compose (optional)

```bash
docker compose up db -d
docker compose run --rm web uv run python manage.py migrate
docker compose run --rm web uv run python manage.py sync_categories
docker compose up
```

This runs Postgres, the web server on port 8000, and a worker (`run_scheduler`) that refreshes
synced categories roughly every 12 hours. The worker also runs a fetch thread
(`UNITPRICER_FETCH_THREADS`, 1 or 2) that takes queued live-search jobs. Every request to a
source draws a token from that source's `SourceBudget` row, which the scheduled refresh shares.
The worker creates missing budget rows from the `UNITPRICER_BUDGET_*` settings at startup and
never overwrites an existing row: to apply a changed setting, edit the row in the admin or
delete it and restart the worker.

## How it works

Retailer connectors fetch raw offers; a pure, deterministic normalizer turns each title and size
into a category, attributes, measures and unit prices; the results are stored in a Postgres
catalog; and a Django + HTMX site serves it from that cache, so no page waits on a retailer API.
Categories are plain YAML files in `categories/`, and each one is proven by fixtures of real titles.

```
connectors  ->  normalizer  ->  catalog (Postgres)  ->  web
(Walmart)       (categories/)   (+ Open Food Facts,      (browse, search)
                                   Open Prices)
```

## Data and attribution

- Some product data comes from [Open Food Facts](https://world.openfoodfacts.org) and Open
  Products Facts, available under the
  [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/). Attribution is
  shown on the site's disclosure page.
- Shelf prices in the "Seen in stores" section come from [Open Prices](https://prices.openfoodfacts.org),
  made available under the ODbL. They are dated observations, not live prices. Our method is this
  source code, and `manage.py export_open_prices --out FILE` writes the observations and their
  normalization as gzip JSONL. A copy is available on request (see `/disclosure`).
- Retailer data is used only as the retailer's program terms allow (for example, cached prices are
  timestamped and kept fresh for at most 24 hours).
- **Affiliate disclosure:** UnitPricer may earn a commission on purchases made through its links.
  Commission never affects ranking or which products are shown. See `/disclosure` on the site.

## Deploying

Production runs on a DigitalOcean droplet with Docker Compose and a Cloudflare Tunnel. See [docs/deploy.md](docs/deploy.md).

## Contributing

The most common contribution is a new category. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[AGPL-3.0-or-later](LICENSE).
