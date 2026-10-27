# What it does

ALOHAS sells shoes through two systems: **Odoo** keeps orders, products and stock,
and **Shopify** runs the online store. Many things write into them: sync bots, cron
jobs, Shopify apps, scripts and people. When a stock count is wrong or a price
changes, the first question is "who did that?", and neither system answers it on
its own. The case memo named the risk:

> Nobody can list everything that writes to Odoo and Shopify today, and the AI
> organigram is about to multiply those writers.

Writers Ledger is the smallest working version of the fix. It records every write
it can see, matches each author against a list of known writers, and turns
anything unknown, out of scope or ownerless into a **finding**, explained in plain
words.

## From a write to a finding

```
Odoo ── polled with a read-only key ──┐
                                      ├─▶ ledger ─▶ check ─▶ findings ─▶ triage ─▶ the page
Shopify ── webhooks, HMAC-signed ─────┘      (rules + writers.yaml)       (Claude)
```

1. **Collect.** The app polls Odoo every few seconds with a read-only API key, and
   Shopify sends it a webhook for every order, product and stock change. Each change
   becomes a **write** in the **ledger**, a SQLite table: when, which system, what
   changed, who did it, and a few allowlisted fields such as SKU, location and
   quantity. Customer data is dropped on arrival.
2. **Check.** After every poll, a **check** applies four **rules** to the ledger and
   the **register**, `writers.yaml`. The rules are plain code, with no AI involved.
3. **Find.** Each rule hit is a **finding**, deduplicated by fingerprint, so 40
   writes by the same rogue script are one finding that says 40.
4. **Triage.** With an Anthropic API key, Claude rates every finding and explains
   the high and critical ones, with a suggested fix and who to ask. The rule's
   severity is a floor that Claude can raise but never lower.
5. **Fix.** Most fixes are a change to the register: add the writer, give it an
   owner, or allow it to write where it does. The rest are fixed in Odoo or
   Shopify. The next check resolves the finding.

How each step works: [Findings and triage](findings.md). What the collectors
can't see: [What it can and can't see](limits.md).

## The page

One page, readable without explanation, in three sections:

- **Findings**, most urgent first, each with Claude's explanation, a fix and the
  writer's owner. A **Run check now** button runs a check at once, for example
  after editing the register.
- **Writers**: the register, with what each writer did. Anyone seen writing who
  isn't in the register comes first, in red.
- **Ledger**: the latest 200 writes. Filter by system, what changed and who; each
  list shows only what the other filters leave, with a count. Click a system,
  writer or model in a row to filter by it.

The page is rendered on the server. Its one small script only applies a filter as
soon as you pick it and shows that a check is running; without it, the page
works the same with a Filter button.

## Words

The code, the tests, the page and these docs use these words, with these meanings:

| Word | Meaning |
|---|---|
| Writer | A script, app, agent or person that writes to Odoo or Shopify. Has an `id`, an `owner`, a `purpose`, identities and `may_write`. |
| Register | `writers.yaml`, the list of writers. The register is the fix. |
| Identity | How a system names the author of a write: an Odoo login, a Shopify app name, or `unattributed` when the system names nobody. |
| Scope | A `system:model` entry, such as `odoo:stock.quant` or `shopify:inventory_level`. |
| Write | One observed change in Odoo or Shopify: a ledger row. |
| Ledger | The append-only record of writes. |
| Check | One run of the rules over the ledger and the register. |
| Rule | `unknown_writer`, `out_of_scope`, `ownerless_writer` and `stock_drift`, plus the informational `unattributed_write`. |
| Finding | A rule hit, deduplicated by fingerprint. Has a severity, a count, first and last seen, and a status, `open` or `resolved`. |
| Drift | Odoo's stock quantity and Shopify's stock level differ for the same SKU and location after the settle window. |
| Triage | The Claude pass: a small model rates every finding, a large one explains the high and critical ones. |

## Where the code is

| Path | What it holds |
|---|---|
| `app/register.py` | Reads and validates `writers.yaml`. |
| `app/rules.py` | The four rules, fingerprints and severities. |
| `app/redact.py` | Removes personal data before anything goes to Claude. |
| `app/ledger.py` | SQLite: writes, findings, triage results and the cost of every Claude call. |
| `app/collect_odoo.py` | Polls Odoo's JSON-2 API with the read-only key. |
| `app/collect_shopify.py` | Receives and verifies Shopify webhooks, then asks Shopify who made the change. |
| `app/triage.py`, `app/triage.yaml` | The Claude calls; the prompts, rubric and answer schemas. |
| `app/main.py` | Wires it all together and serves the page. |
| `app/templates/`, `app/static/` | The page, its stylesheet and its one script. |
| `writers.yaml` | The demo register. |
| `sim/` | The demo: the Odoo seed, the fake Shopify, the traffic and the smoke test. |
| `evals/` | Claude's triage scored on 15 findings. |
| `tests/` | Unit tests. |

The first three files are the **domain core**: plain functions with no I/O, tested
first. The collectors, the ledger and triage are **adapters**, where all I/O lives.
`app/main.py` is the only module that imports both.

## Not in this version

The brief leaves these out of the MVP: an MCP server, a gateway for agent writes,
an approval queue, BigQuery, tracing an order from Shopify to delivery,
stuck pre-orders, more than one user, and a JavaScript frontend.
