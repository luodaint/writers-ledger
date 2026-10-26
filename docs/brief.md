# Build brief: Writers Ledger (ALOHAS CTO case, Part two)

You are building a small, working MVP with me. Read this whole brief before writing code. Start by proposing a plan for Phase 0 and wait for my OK.

## Why this exists

In the memo for this case, I named one architectural risk:

> Nobody can list everything that writes to Odoo and Shopify today, and the AI organigram is about to multiply those writers.

This project is the smallest working version of the fix:

1. **A register** (`writers.yaml`): who is allowed to write, to what, and who owns them.
2. **A ledger**: every write we can observe in Odoo and Shopify, with the identity that made it.
3. **A check**: compares ledger and register and produces findings.
4. **Triage with an LLM**: rates each finding and explains it in plain words.

One sentence: *every write into Odoo and Shopify lands in a ledger, gets matched against `writers.yaml`, and anything unknown, out of scope or ownerless becomes a finding, explained in plain words.*

## Hard constraints from the case

- **It runs.** A live URL (my Dokploy on Hetzner) AND `git clone` + one command (`docker compose up`) offline.
- **A non-technical person can use it.** One web page, readable without explanation.
- **The LLM is used non-trivially.** Structured outputs, two model tiers, an eval set.
- **Small enough to read once.** Target ≤ 500 lines of app code (excluding seed, simulator, tests, templates). Report the count at the end of each phase.
- **No customer personal data goes to the LLM.** Redact names, emails, addresses and phone numbers before any model call. This is the spirit of the case's one hard line (never expose someone else's data).

## How we work

- **Plan before each phase.** Short plan, then wait for my OK.
- **Tests first for the rules.** The four checks in `rules.py` are pure functions; write their tests before the code.
- **Ask before adding a dependency.** Prefer the standard library.
- **One commit per phase**, with a clear message.
- **Keep `docs/ai-log.md`.** After each phase, add 3–5 lines: what I asked, where you got it wrong, what I changed by hand. The case explicitly asks how I used my AI assistant.
- **No speculative abstractions.** No plugin systems, no hexagonal architecture, no base classes with one child. Plain functions and modules.

## Stack

- Python 3.12, FastAPI, Jinja2 templates (server-rendered, no SPA, no JS build step), SQLite via the standard library.
- `httpx` for HTTP calls, `pyyaml` for the register, `anthropic` SDK for the LLM.
- Odoo 19 Community in Docker with Postgres. Use the **External JSON-2 API** (`POST /json/2/<model>/<method>`, bearer API key). Check the current Odoo 19 docs before coding against it.
- Shopify: a free **dev store** with a custom app (Admin API token + webhooks). Check current Shopify docs for webhook topics, HMAC verification and what attribution fields exist.
- Models are configured by env vars:
  - `TRIAGE_MODEL_SMALL` (default `claude-haiku-5-5`)
  - `TRIAGE_MODEL_LARGE` (default `claude-sonnet-5-5`)

## Repo layout

```
writers-ledger/
  app/
    main.py          # FastAPI: page, webhook endpoint, scheduler kick
    register.py      # load + validate writers.yaml
    ledger.py        # SQLite schema + insert/query helpers
    collect_odoo.py  # poll Odoo JSON-2
    collect_shopify.py # webhook handler (+ fake source for offline mode)
    rules.py         # the four checks, pure functions
    triage.py        # LLM triage: small model on all, large model on high severity
    redact.py        # strip personal data before LLM calls
    templates/index.html
  sim/
    seed_odoo.py     # creates API users, products, locations, stock
    traffic.py       # plays out days of writes from each writer, incl. planted incidents
    fake_shopify.py  # emits Shopify-shaped events offline
  evals/
    cases.yaml       # planted findings + expected severity/category
    run_evals.py
  tests/
  writers.yaml
  docker-compose.yml        # live: odoo + postgres + app
  docker-compose.fake.yml   # offline: odoo + postgres + app + fake shopify
  Makefile                  # make up / seed / traffic / check / eval
  README.md
  docs/ai-log.md
```

## The register: `writers.yaml`

```yaml
- id: shopify-odoo-sync
  owner: backend
  purpose: Creates sale orders in Odoo from Shopify orders
  identity: { odoo_user: sync@alohas.test, shopify_app: order-sync }
  may_write: [odoo:sale.order, odoo:res.partner]

- id: stock-cron
  owner: sysadmin
  purpose: Pushes Odoo stock levels to Shopify locations
  identity: { odoo_user: stock@alohas.test, shopify_app: stock-sync }
  may_write: [odoo:stock.quant, shopify:inventory_level]

- id: pmo-pricing-script
  owner: ""            # deliberately ownerless for the demo
  purpose: Bulk-updates product metafields for drops
  identity: { shopify_app: pricing-tool }
  may_write: [shopify:product]
```

Validation in `register.py`: unique `id`, at least one identity, `may_write` entries in the form `system:model`. A broken register fails loudly at startup.

## The ledger

One SQLite table `writes`:

| column | meaning |
|---|---|
| id | autoincrement |
| seen_at | when we observed it |
| written_at | when the system says it happened |
| system | `odoo` or `shopify` |
| model | e.g. `stock.quant`, `inventory_level` |
| record_ref | record id / gid |
| identity | odoo login, shopify app handle, or `unattributed` |
| action | `create` / `write` |
| payload | small JSON with changed values (redacted) |

A second table `findings`: `id, fingerprint, rule, severity, category, writer_id, identity, summary, explanation, suggested_fix, first_seen, last_seen, count, status (open/resolved)`. The fingerprint (rule + identity + model) deduplicates, so 40 rogue writes make one finding with `count=40`, not 40 findings.

## Collectors

**Odoo (`collect_odoo.py`)**
- Poll every N seconds (env `POLL_SECONDS`, default 30) the models `sale.order`, `stock.quant`, `stock.picking`, `product.product`.
- Use `search_read` with `write_date > last_cursor`, fields `id, write_uid, write_date, create_date` plus a few business fields per model.
- Resolve `write_uid` to login once and cache it.
- Store the cursor per model in SQLite so restarts don't replay everything.
- **Known limitation, keep it in the README:** polling sees only the last writer per record. The production answer is an audit trail (OCA `auditlog` if available for 19, or a database trigger).

**Shopify (`collect_shopify.py`)**
- `POST /webhooks/shopify`: verify the HMAC header with the app secret. Reject and log on failure.
- Topics: orders create/update, inventory levels update, products update.
- Identity: use whatever attribution the payload or a follow-up Admin API call gives (e.g. the order's source app). If none, store `unattributed`. "Unattributed" is itself a finding category, not a bug.
- In offline mode, `sim/fake_shopify.py` POSTs signed, Shopify-shaped payloads to the same endpoint, so the handler code path is identical.

## Rules (`rules.py`)

Pure functions, input = writes + register, output = findings. No AI here.

1. **unknown_writer**: identity not in any register entry (excluding `unattributed`).
2. **out_of_scope**: the identity belongs to a writer, but `system:model` is not in its `may_write`.
3. **ownerless_writer**: a registered writer with an empty `owner` that has written in the window.
4. **stock_drift**: for each SKU and location, Odoo `stock.quant` quantity ≠ Shopify inventory level after a settle window (env `DRIFT_SETTLE_MINUTES`, default 10).

Plus one informational category: **unattributed_write** (Shopify could not tell us who).

Tests: one test per rule for a positive and a negative case, plus deduplication by fingerprint.

## Triage (`triage.py`)

- **Small model on every new or changed finding.** Structured output (tool/JSON schema) with: `severity` (low/medium/high/critical), `category`, `likely_cause` (one sentence), `confidence` (0–1).
- **Large model only for high/critical.** Structured output: `explanation` (2–3 plain sentences for a non-technical reader), `suggested_fix` (often a ready-to-paste `writers.yaml` entry), `who_to_ask` (the owner, or "no owner, assign one").
- Everything passes through `redact.py` first.
- Cache by fingerprint + count bucket so we don't re-triage the same finding every poll.
- Log tokens and cost per call to SQLite. The page footer shows **cost per check run** as one number. The README cover needs that number.
- If the API key is missing, findings still show with rule-based severity and a note "triage off". The app never breaks because the LLM is down.

## Evals (`evals/`)

- `cases.yaml`: about 10 finding snapshots (the planted incidents plus a few benign ones) with expected `severity` and `category`.
- `make eval` runs triage on them and prints pass/fail per case and an overall score.
- Run it before and after every prompt change. If a change makes a case regress, note it in `docs/ai-log.md`; that's a story for the README ("a regression the eval caught").

## Simulation

**`sim/seed_odoo.py`**
- Creates API users with their own keys: `sync@`, `stock@`, `pmo@`, and uses the **admin** key for the rogue writer.
- Creates about 15 shoe products with sizes 36–41 as variants, and stock locations Barcelona, Milan, Online warehouse.

**`sim/traffic.py`** plays out several simulated days of normal traffic from each writer, plus these planted incidents:
1. **The forgotten cron:** a script using the admin key adjusts `stock.quant` in Milan repeatedly overnight → `unknown_writer`, should triage as critical.
2. **Scope creep:** `shopify-odoo-sync` writes to `product.product` → `out_of_scope`, high.
3. **Ownerless:** `pmo-pricing-script` updates products → `ownerless_writer`, medium.
4. **Drift:** stock for one SKU in Barcelona differs between Odoo and Shopify → `stock_drift`, high.
5. **Benign noise:** normal writes from registered writers within scope → no findings (this catches false positives).

## The page (`templates/index.html`)

One page, three sections, readable by someone non-technical:

1. **Findings** first: red/amber cards with severity, the plain explanation, the suggested fix, owner, count, first/last seen.
2. **Writers:** the register as a table, with last activity and number of writes per writer. Writers seen in the ledger but not in the register appear at the top in red.
3. **Ledger:** the latest 200 writes, filterable by system, model and identity with simple query-string filters.

Footer: last check time, cost of the last check run, LOC count of `app/`.

Basic auth on the whole app (env `APP_USER`/`APP_PASSWORD`), except the webhook endpoint (which uses HMAC).

## The demo story (the README walks through this)

1. `make up && make seed && make traffic`
2. Open the page: four findings, each explained.
3. Edit `writers.yaml`: register the forgotten cron with an owner (or decide to kill it), give `pmo-pricing-script` an owner.
4. Re-run the check: those findings resolve. **The register is the fix.**

## Deployment

- **Live (Dokploy on Hetzner):** `docker-compose.yml` deployed as a Compose app. Odoo + Postgres + app. A domain with HTTPS for the app (Shopify webhooks need it). Secrets via Dokploy env: `ODOO_URL`, `ODOO_DB`, `ODOO_API_KEY_*`, `SHOPIFY_STORE`, `SHOPIFY_TOKEN`, `SHOPIFY_WEBHOOK_SECRET`, `ANTHROPIC_API_KEY`, `APP_USER`, `APP_PASSWORD`.
- **Offline:** `docker compose -f docker-compose.fake.yml up` runs everything with the fake Shopify source. No accounts needed except an optional LLM key.
- A `make reset` that wipes the ledger and reseeds, so reviewers always see the intended starting state.

## README (one page, clearer than the average open-source project)

Cover line: **cost per check run: €X** (from the logged numbers).

Then answer the case's questions directly:
- Why this problem? (link to memo point 2)
- Why this model for this step? (small for triage on everything, large only for explanations)
- What did you not use AI for, and why? (the rules: detection must be deterministic and testable)
- A failure case and its fix (from the evals or the ai-log)
- To make this 10× cheaper, what changes? (e.g. rules-only severity for known patterns, batch triage, cache)
- To run autonomously, what's missing? (auto-opening tickets to owners, a real audit trail instead of polling, a gateway for agent writes)
- How I used my AI coding assistant (summary of `docs/ai-log.md`)
- What I'd add with another four hours: order path tracing (Shopify order → Odoo sale order → production → delivery) and stuck pre-order detection.

## Out of scope for the MVP

MCP server, agent gateway, approval queue, BigQuery, order path tracing, stuck pre-orders, multi-user auth, a JS frontend.

## Phases

**Phase 0: skeleton.** Repo layout, compose files, Odoo boots, app boots with an empty page, `writers.yaml` loads and validates.
*Done when:* `docker compose -f docker-compose.fake.yml up` shows the empty page with the register table.

**Phase 1: ledger.** Odoo collector + Shopify webhook handler + fake Shopify source + seed + traffic (no incidents yet).
*Done when:* the Ledger section fills with attributed writes from both systems.

**Phase 2: rules.** Tests first, then the four rules + deduplication + the planted incidents in `traffic.py`.
*Done when:* all tests pass and the four planted incidents show as findings with rule-based severity, and benign traffic produces none.

**Phase 3: triage + evals.** Redaction, two-tier triage, cost logging, `evals/cases.yaml`, `make eval`.
*Done when:* findings show explanations and suggested fixes, `make eval` passes, and the footer shows cost per run.

**Phase 4: live + README.** Dokploy deploy against real Odoo and my Shopify dev store, `make reset`, README, final ai-log entry, LOC count.
*Done when:* the live URL works end to end and the README answers every question above.
