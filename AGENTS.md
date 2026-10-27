# Writers Ledger: agent instructions

## Project context

- Purpose: every write into Odoo and Shopify lands in a ledger, gets matched
  against `writers.yaml`, and anything unknown, out of scope or ownerless becomes
  a finding, explained in plain words by Claude. ALOHAS CTO case, part two.
- Spec: `docs/brief.md` is the source of truth. Read it before starting a phase.
- Runtime: Python 3.12 (`python:3.12-slim` in Docker; locally via `uv`, the host
  Python is newer), FastAPI + Jinja2 server-rendered pages, SQLite from the
  standard library, Odoo 19 Community (External JSON-2 API), Shopify Admin API +
  webhooks, Anthropic SDK.
- Dependencies: runtime `fastapi`, `uvicorn`, `jinja2`, `httpx`, `pyyaml`,
  `anthropic`; dev `pytest`, `ruff`. Ask the owner before adding anything else. Pin exact
  versions.
- Size budget: about 700 lines of app code in `app/`, raised from the brief's 500
  with the owner. Templates, `app/triage.yaml`, `sim/`, `evals/` and `tests/` don't
  count. `make loc` reports it; report the count at the end of each phase.

## Domain language (DDD)

Use these words, with these meanings, in code, tests, UI copy and docs. Don't
introduce synonyms.

| Term | Meaning |
|---|---|
| Writer | A script, app, agent or person that writes to Odoo or Shopify. Has `id`, `owner`, `purpose`, identities, `may_write`. |
| Register | `writers.yaml`: the list of writers. The register is the fix. |
| Identity | How a system names the author of a write: Odoo login, Shopify app handle, or `unattributed`. |
| Scope | A `system:model` entry, e.g. `odoo:stock.quant`, `shopify:inventory_level`. |
| Write | One observed change in Odoo or Shopify: a ledger row. |
| Ledger | The append-only record of writes. |
| Check | One run of the rules over the ledger and the register. |
| Rule | `unknown_writer`, `out_of_scope`, `ownerless_writer`, `stock_drift`, plus the informational `unattributed_write`. |
| Finding | A rule hit, deduplicated by fingerprint (rule + system + model + identity; for drift, rule + SKU + location). Has severity, count, first/last seen, status `open`/`resolved`. |
| Drift | Odoo `stock.quant` quantity differs from the Shopify inventory level for the same SKU and location after the settle window. |
| Triage | The LLM pass: small model rates every finding, large model explains high/critical ones. |

Module boundaries:

- **Domain core** (pure functions, no I/O, never imports FastAPI, httpx, sqlite3
  or anthropic): `register.py`, `rules.py`, `redact.py`.
- **Adapters** (all I/O lives here): `ledger.py` (SQLite), `collect_odoo.py`,
  `collect_shopify.py`, `triage.py` (Anthropic).
- **Composition**: `main.py` wires the core and adapters together. It is the only
  module that imports both.
- Dependencies point inward: adapters may import the core, the core imports
  nothing from the project except other core modules.
- Domain values are plain dicts or frozen dataclasses named with the terms above.
  No repositories, service layers, aggregate classes, base classes with one child
  or plugin systems: the brief forbids speculative abstractions. DDD here means
  shared language, a pure core and clear boundaries.

## TDD

- Red, green, refactor. Write the failing test, run it and see it fail for the
  right reason, then write the minimum code to pass, then clean up.
- Test-first is mandatory for the domain core (register validation, the four
  rules, fingerprint deduplication, redaction), webhook HMAC verification and
  basic auth.
- Adapters are tested against fakes (`sim/fake_shopify.py` payloads, recorded
  Odoo JSON-2 responses). No network calls in unit tests; stub the Anthropic
  client.
- Every bug fix starts with a failing regression test.
- LLM behaviour is measured by evals (`make eval`), not unit tests. Run evals
  before and after every prompt change; record regressions in `docs/ai-log.md`.

## Security rules

- No customer personal data reaches the LLM. Triage sends an allowlisted summary
  (rule, system, model, identity, counts, field names), never raw payloads;
  `redact.py` is the second layer.
- No customer personal data in the ledger: drop customer, address, email and
  phone fields at collection time.
- Secrets come from env only. Never commit `.env`, never log keys or tokens,
  never echo them in output. `.env.example` lists names, not values.
- Basic auth on every route except `/webhooks/shopify` (HMAC) and `/healthz`.
  Compare credentials with `secrets.compare_digest`; refuse to start without
  them.
- Webhooks: verify HMAC-SHA256 over the raw body before parsing, compare in
  constant time, deduplicate on `X-Shopify-Webhook-Id`, respond within 5 seconds.
- SQL uses parameters only. YAML uses `safe_load` only.
- Templates autoescape; never apply `|safe` to payload data or LLM output. LLM
  output is untrusted text: validate it against the schema, display it, never
  execute it. The rule-based severity is a floor the LLM cannot lower.
- The Odoo collector uses a read-only bot user, never the admin key. Odoo and
  Postgres are never published publicly in the live compose file.
- Containers: pinned image tags, non-root user, dropped capabilities.

## Commands

- Tests: `make test`. Lint: `make lint` (ruff, configured in `pyproject.toml`).
- Offline stack: `docker compose -f docker-compose.fake.yml up`
- End to end: `make up seed traffic smoke`, exactly what CI runs.
- Other targets (`check`, `eval`, `reset`) are added in the phase that needs
  them; `make help` lists what exists.

## Workflow and git

- Work in the phases of `docs/brief.md`. Propose a short plan before each phase
  and wait for the owner's OK.
- Every change is a branch and a pull request into `main`, one PR per phase.
  Nobody pushes to `main`: a ruleset requires the PR, the `Lint and tests` and
  `Offline demo, end to end` checks, and CodeQL. The owner reviews and
  squash-merges; agents never merge their own PRs.
- After each phase, append 3–5 lines to `docs/ai-log.md`: what was asked, where
  the agent got it wrong, what was changed by hand.
- Check the current Odoo 19, Shopify and Anthropic docs before coding against
  them; note anything that differs from the brief.

## Shared workflows

The shared toolkit is mounted at `.agents/toolkit`. Use
`.agents/toolkit/skills/security-audit/SKILL.md` for security reviews,
`.agents/toolkit/skills/code-review/SKILL.md` for code reviews and
`.agents/toolkit/skills/simplify/SKILL.md` for requested cleanup. A review alone
does not authorize edits.
