# UnitPricer Roadmap

Work order from first commit to public launch and beyond. The design lives in
[`docs/superpowers/specs/2026-11-07-unitpricer-design.md`](superpowers/specs/2026-11-07-unitpricer-design.md),
which this roadmap references by section (§).

- Each **task** becomes a GitHub issue and is done in its own worktree on an `issue-<n>-<slug>` branch, with a PR that closes it.
- Each coding **milestone** gets a detailed implementation plan in `docs/superpowers/plans/` when work on it starts.
- ⛳ marks a **decision checkpoint**: stop, review what was learned, and decide before continuing.

```
M0 Access & foundations ──┐
                          ├─► M2 Catalog & ingestion ─► M3 Browse & search ─► ⛳ dogfood, name, deploy ─► M4 Lists & accounts
M1 Normalizer core ───────┘                                                                                      │
                                                                                                                 ▼
            M7 Nutrition per $ ◄─ M6 Launch ◄─ ⛳ launch readiness ◄─ M5 Coverage & quality
```

M0 and M1 run in parallel: M1 needs no API keys.

---

## M0: Access and foundations

**Goal:** Learn which official data sources are actually usable before building connectors (§11).

| # | Task | Done when |
|---|---|---|
| 0.1 | Amazon Associates / Creators API eligibility | ✅ Known: Creators API needs 10 qualifying sales in 30 days; Associates signup moves to Checkpoint B (needs the live URL) |
| 0.2 | Walmart developer API access (walmart.io) | ⏳ Application created; public-key upload fails with a 503 — retry or contact walmart.io support |
| 0.3 | Sovrn Commerce spike + program terms review | ✅ [`research/2026-11-07-sovrn-api.md`](research/2026-11-07-sovrn-api.md): link router only, not a data source |
| 0.4 | Open Food Facts evaluation | ✅ [`research/2026-11-07-open-food-facts.md`](research/2026-11-07-open-food-facts.md): dump + daily deltas, Open Products Facts for household |
| 0.5 | Repo hygiene: LICENSE (AGPL-3.0-or-later, done), README, CONTRIBUTING (including "how to add a category" and the fixture format), `.env.example` entries (done) | Files merged |

⛳ **Checkpoint A: connector order. Decided 2026-11-07:** M2's first connector is the **Walmart Affiliate API**, built and tested against sample responses from Walmart's documentation, switching to live data once walmart.io access works. Sovrn is used as the affiliate-link router, not a data source. Amazon follows once Creators API access is possible.

---

## M1: Normalizer core (no network, no Django)

**Goal:** Turn raw retailer titles and sizes into categories, attributes, measures and unit prices, proven by fixtures (§5.2, §6, §7).
**Plan:** [`plans/2026-11-07-m1-normalizer-core.md`](superpowers/plans/2026-11-07-m1-normalizer-core.md)

| # | Task |
|---|---|
| 1.1 | Core types and the category file loader, with validation |
| 1.2 | Quantity parsing into canonical measures (volume, mass, count, paper goods) |
| 1.3 | Attribute extraction (API fields → Open Food Facts labels → patterns → synonyms; ambiguous values never guessed) |
| 1.4 | Category matching, plus brand tier (store brand vs. name brand) |
| 1.5 | Comparison methods and the `normalize()` pipeline |
| 1.6 | First categories with fixtures: milk, eggs, coffee |
| 1.7 | Paper-goods categories with fixtures: toilet paper, paper towels |

**Exit:** `uv run pytest` passes on 5 categories with at least 8 real-title fixtures each.

---

## M2: Catalog and ingestion

**Goal:** A local Postgres catalog filled on a schedule from at least one source (§5.1, §5.3, §13).
**Plan:** [`plans/2026-11-07-m2-catalog-ingestion.md`](superpowers/plans/2026-11-07-m2-catalog-ingestion.md)

| # | Task |
|---|---|
| 2.1 | Django project, Docker Compose (web, worker, Postgres), settings loaded from env |
| 2.2 | Models: Category, AttributeDef, Product, Offer, PriceHistory, ReviewItem; sync categories from YAML (files are the source of truth) |
| 2.3 | Connector interface and the recorded-response test harness (no live calls in CI) |
| 2.4 | First connector: Walmart Affiliate API against documented sample responses (live once walmart.io access works) |
| 2.5 | Open Food Facts enrichment client with caching |
| 2.6 | Ingestion job: connector → normalizer → upsert products, offers and price history; log run stats per connector and category |
| 2.7 | Scheduler and worker: refresh about every 12 h; freshness cutoff 24 h (never above a source's terms limit); Walmart offers never written to price history |
| 2.8 | Admin review queue; manual corrections that survive re-ingestion |

**Exit:** One command fills the local catalog for 5 categories. Re-running it is idempotent, and the review queue shows what didn't resolve.

---

## Rename: shopwit → UnitPricer (between M2 and M3) ✅ Done

Final name decided 2026-11-07: **UnitPricer**. `unitpricer.com` is registered on Cloudflare, and `hello@unitpricer.com` forwards to the maintainer. Done (#26) right after M2 merges, before M3 adds templates and copy ([#26](https://github.com/DanteArcese/unitpricer/issues/26)): GitHub repo, Python metadata, User-Agent strings, docs, CLAUDE.md.

---

## M3: Browse and search, usable for you

**Goal:** The core experience: "2% milk", ranked by unit price (§4 items 1–4, §5.4, §7, §10).
**Plan:** [`plans/2026-11-07-m3-browse-search.md`](superpowers/plans/2026-11-07-m3-browse-search.md)

| # | Task |
|---|---|
| 3.1 | Category page: attribute filters with URL state and HTMX partial updates |
| 3.2 | Ranking by unit price; switching comparison units; labels for unknown attributes and price age; unranked list with reasons |
| 3.3 | Search resolution: query → category + filters via synonyms; full-text fallback |
| 3.4 | Store and membership selector (session-based) |
| 3.5 | Metric/imperial setting with a locale-based default; display conversion |
| 3.6 | Direct affiliate links built at render time (no redirect or shortener), click counting via `ping`/`sendBeacon` without personal data, "price as of" timestamps, Amazon disclaimer and disclosure text |
| 3.7 | Responsive layout pass (phone in the aisle, desktop at home) |

⛳ **Checkpoint B: dogfood, name and first deploy.**
1. **Dogfood.** Use it for real shopping for two weeks. Log every wrong attribute, missing product and confusing screen as an issue, and fix the worst before M4.
2. **Final name and domain.** ✅ Done early: **UnitPricer**, `unitpricer.com` (Cloudflare Registrar). Remaining: DNSSEC and a DMARC record in the Cloudflare dashboard.
3. **Production hosting.** Compare a VPS (Docker Compose), a PaaS (Fly.io, Railway, Render) and serverless (Vercel). This needs a long-running worker and Postgres, which serverless suits poorly. Decide on cost, operations effort and backups.
4. **First deploy** on the chosen host (TLS, backups, error monitoring), with the affiliate disclosure page. Then register the domain with Sovrn and the other programs so approval runs in parallel with M4–M5.
5. **Sovrn merchant audit.** With UnitPricer's own site key, script the Approved Merchants API to list every grocery and household merchant that is actually monetizable, marked by whether UnitPricer has a data source for it. Feeds Checkpoint D.

---

## M4: Lists and accounts

**Goal:** Lists by spec, plus optional accounts (§4 items 5–6, §8, §9).

| # | Task |
|---|---|
| 4.1 | List and ListItem models (spec = category + filters + target quantity, optional pinned product) |
| 4.2 | List resolution: cheapest qualifying fresh offer per item at the selected stores |
| 4.3 | Totals per store; cheapest single store vs. cheapest split; quantity fit (e.g. 2 × 64 oz vs. 1 × 128 oz) |
| 4.4 | Anonymous lists stored in the session |
| 4.5 | Accounts: email and password plus magic link; move session data into the account on sign-in |
| 4.6 | Saved settings (unit system, stores, default comparison units) |

---

## M5: Coverage and quality

**Goal:** Enough breadth and reliability for strangers to trust it.

| # | Task |
|---|---|
| 5.1 | Second and third connectors (Walmart, Amazon or Sovrn, whichever weren't done in M2) |
| 5.2 | Expand to about 25 categories (§6 list), each with fixtures. **Plan:** [`plans/2026-11-08-category-expansion.md`](superpowers/plans/2026-11-08-category-expansion.md) |
| 5.3 | Parse-rate monitoring: alert when a connector's or category's resolved rate drops |
| 5.4 | "Report a wrong attribute" link, feeding the review queue |
| 5.5 | Performance pass: query plans for filtered category pages; indexes on JSONB attributes |

⛳ **Checkpoint C: launch readiness.** Affiliate programs approved for the live domain; coverage and parse rates good enough for strangers; legal pages reviewed.

---

## M6: Launch

| # | Task |
|---|---|
| 6.1 | ✅ Done after M2: rename shopwit → UnitPricer (#26) |
| 6.2 | Production hardening: uptime checks, alerting, restore drill for backups |
| 6.3 | Legal pages: privacy policy, terms, affiliate disclosure (FTC and each program's required wording) |
| 6.4 | SEO: indexable category landing pages ("cheapest 2-ply toilet paper"), sitemap, structured data, canonical URLs for filter combinations |
| 6.5 | Polish the open-source repo: README screenshots, contributing guide, "good first category" issues |
| 6.6 | Soft launch to frugal and personal-finance communities; collect feedback as issues |

---

## M7: Nutrition per dollar (first post-core feature)

| # | Task |
|---|---|
| 7.1 | Nutrition fields from Open Food Facts (protein, calories, etc.) stored per product |
| 7.2 | Comparison methods: cost per 10 g protein, cost per 100 kcal |
| 7.3 | Protein-powder and other high-protein categories; a "fitness" landing page |

---

## Later (ordered by expected value; re-plan after launch)

1. Price history, a "good price?" indicator, and email alerts on a spec (excluding Walmart data, per its terms)
2. Effective unit price: Subscribe & Save, shipping thresholds, membership costs
3. ⛳ **Checkpoint D: in-store chains** (Costco, Shaw's, Stop & Shop, Target): unofficial APIs or scraping, form-based community shelf prices, or a paid data vendor. Weigh the legal risk of each, given the public repo.
4. Ads, with a paid ad-free tier
5. PWA (install to home screen, offline lists), then a browser extension
6. ZIP-level pricing; international (currency and locale data)
