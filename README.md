# Writers Ledger

[![CI](https://github.com/luodaint/writers-ledger/actions/workflows/ci.yml/badge.svg)](https://github.com/luodaint/writers-ledger/actions/workflows/ci.yml)

Every write into Odoo and Shopify lands in a ledger, gets matched against
`writers.yaml`, and anything unknown, out of scope or ownerless becomes a finding,
explained in plain words.

**Cost per check run: about $0.015.** That is the first triage of the demo's four
findings: 7 Claude calls, measured from the app's own logs. A check with nothing
new costs $0, because the rules use no AI and each finding is triaged once. Per
call: about $0.0002 to rate a finding, about $0.004 to explain one.

The case's questions are [answered below](#the-cases-questions).

**Docs:** [what it does](docs/overview.md) · [install and run](docs/install.md) ·
[test data](docs/data.md) · [the register](docs/register.md) ·
[findings and triage](docs/findings.md) · [security](docs/security.md) ·
[what it can't see](docs/limits.md) · [develop](docs/development.md)

## What it looks like

These screenshots come from the offline demo (`make up`, `make seed`, `make traffic`)
with triage on.

**Findings**, most urgent first. The rules decide what is wrong. Claude rates each
finding, and explains the high and critical ones in plain words with a fix to paste.

![Four findings. The admin-key cron, raised to critical by Claude; the sync bot writing product prices; Barcelona stock 3 apart between Odoo and Shopify; and a pricing script nobody owns. Each shows Claude's explanation, a suggested fix and who to ask.](docs/screenshots/findings.png)

**Writers** shows the register and what each writer did. Anyone seen writing who isn't
in the register comes first, in red.

![The writers table. The admin login, not in the register, is in red at the top. Next comes an amber row for Shopify changes without an author. Then come the four registered writers, each with its owner, what it may change, its last activity and its number of writes.](docs/screenshots/writers.png)

**Ledger** shows the latest writes seen in Odoo and Shopify, here filtered to
Shopify. Each filter lists only what the other filters leave, with a count, and
applies as soon as you pick it. Click a system, writer or model in a row to filter
by it.

![The ledger filtered to Shopify. Each row is one write: when, which system, who, what changed and the details, such as SKU, location and quantity. Shopify stock changes, which name no author, are marked amber as unattributed.](docs/screenshots/ledger.png)

## Run it offline

```sh
make up        # Postgres, Odoo 19, a fake Shopify and the app (first boot ~2 min)
make seed      # Odoo bot users with their own API keys, 15 shoes x 6 sizes, 3 shops, stock
make traffic   # three simulated days of writes from every writer, plus four planted incidents
```

Open <http://127.0.0.1:8000> and log in as `reviewer` / `local-only-demo`. The
ledger fills within 10 seconds of the traffic. Odoo is at <http://127.0.0.1:8069>
(`admin` / `local-only-admin`). These defaults only work offline: every port is
bound to `127.0.0.1`. Change them in `.env` (see `.env.example`). Settings, triage
and troubleshooting: [Install and run](docs/install.md).

## The demo: four findings, and the register is the fix

About a minute after `make traffic`, the page shows four findings:

| Severity | Finding | What happened |
|---|---|---|
| High | Someone who is not in the register is writing | A forgotten cron uses the Odoo **admin** key to reset Milan stock every night. |
| High | A writer is changing something it isn't allowed to | `shopify-odoo-sync` started writing product prices in Odoo. |
| Medium | A writer that nobody owns is active | `pmo-pricing-script` updates products, and its owner is empty. |
| High | Odoo and Shopify disagree about stock | One stock push sent Barcelona 3 pairs too many of `AL05-38`. |

Below them, a grey note counts the Shopify stock changes that nobody can be blamed
for: Shopify doesn't say which app changes inventory.

The sync wrote 3 prices but the finding says 18 writes. Odoo keeps a shoe's price on
the shoe, so changing it updates all six of its sizes, and the ledger counts what
Odoo records.

Now fix the register. Open `writers.yaml` and:

1. Give `pmo-pricing-script` an owner: `owner: pmo`.
2. Register the cron, with an owner who will replace the admin key with a bot of its own:

   ```yaml
   - id: milan-stock-reset
     owner: sysadmin
     purpose: Nightly Milan stock reset. Move it off the admin key.
     identity: { odoo_user: admin }
     may_write: [odoo:stock.quant]
   ```

3. Press **Run check now** (or `make check`). Both findings move to *Resolved*.
   No restart: every check re-reads `writers.yaml`. A half-saved, broken file pauses
   the checks and shows the problem on the page; nothing resolves until it's fixed.

The other two are fixed outside the register. Take the product rights away from
the sync bot in Odoo, or, if it should write prices, add `odoo:product.product` to
its `may_write`. Correct the Barcelona stock. To kill the cron instead of
registering it, revoke its key: the finding resolves when its last write is older
than the 7-day window.

## Test it with other data

Odoo in the demo is real Odoo 19; only Shopify is faked. Three ways to go further,
in full in [Test data](docs/data.md):

**1. More, or different, data.** `make traffic` doesn't pass settings through, so
run the container directly:

```sh
docker compose -f docker-compose.fake.yml run --rm -T -e TRAFFIC_DAYS=30 -e TRAFFIC_SEED=42 traffic
```

`TRAFFIC_DAYS` sets the simulated days (default 3), so 30 gives about ten times the
writes, all inside the 7-day window. `TRAFFIC_SEED` picks different SKUs, customers
and orders. Running it again adds to what's there: the same findings come back
with higher counts. To start from zero, run
`docker compose -f docker-compose.fake.yml down --volumes`, then `make up seed traffic`.

**2. Be a writer yourself.** The most convincing test, with no scripted data:

1. Log in to Odoo at <http://127.0.0.1:8069> as `admin` (`ODOO_ADMIN_PASSWORD`, or
   `local-only-admin`).
2. Open a shoe, change its **Sales Price** and save.
3. Within about 10 seconds, a new finding for `admin` on `odoo:product.product`
   appears, with 6 writes, one per size: `unknown_writer`, or `out_of_scope` if you
   already registered `admin` in the demo above. With triage on, Claude rates it.
4. Allow it in `writers.yaml` and press **Run check now**: the finding resolves.
   After the demo, add `odoo:product.product` to the `may_write` of its `admin`
   entry; on a fresh demo, add an entry for `admin`. An identity can belong to one
   writer only, so never add a second one.

**3. A real Shopify dev store, still on your machine.** Not built yet. The real
Shopify API would answer "who made this change" instead of the fake copying it. It
takes a dev store with three apps, a seed for the Shopify catalogue, webhook
registration, and a tunnel such as cloudflared or ngrok, because Shopify only sends
webhooks to a public HTTPS address. Expect it to confirm what the demo shows: the
fake already follows the real API's limits.

## How it works

Odoo is polled with a read-only key, and Shopify sends signed webhooks. Each change
becomes a write in the ledger, without customer data. After every poll, a check
applies four rules to the ledger and `writers.yaml`, with no AI involved:
`unknown_writer`, `out_of_scope`, `ownerless_writer` and `stock_drift`. Repeats
collapse into one finding with a count.

Claude then rates every finding with `claude-haiku-5-5`, and explains the high and
critical ones with `claude-sonnet-5-5`: plain sentences, a fix to paste and who to
ask. The rule's severity is a floor Claude can raise but never lower, and no
customer data reaches it. A first triage of the demo's four findings costs about
**$0.015**.

More: [findings and triage](docs/findings.md), [security](docs/security.md),
including how the Odoo API keys are handled, and
[what the ledger can and can't see](docs/limits.md).

## The case's questions

**Why this problem?**
The case memo named one architectural risk: nobody can list everything that
writes to Odoo and Shopify, and the AI organigram is about to multiply those
writers. The easy build was a chatbot you ask "do we have shoe A in Boston?". It
reads the data and assumes it is right. When a forgotten cron resets the stock, or
a sync bot starts rewriting prices, it answers with confidence, and it is wrong.
The risk is in the writes, so this makes every write visible, attributed and
owned. Then any answer, from a chatbot or a person, can be trusted.

**Why this model for this step?**

- **Rating every finding is classification**: a fixed rubric, four severities, six
  categories and a JSON schema. `claude-haiku-5-5` does it for about $0.0002 a
  call and scores 15 out of 15 on the evals, so it runs on everything.
- **Explaining is writing for the person who has to act**: plain sentences, a fix
  to paste, who to ask. That is worth `claude-sonnet-5-5`, at about $0.004 a call,
  so it runs only on high and critical findings. If it declines a request, the API
  retries it on a fallback model.
- **Nothing here needs a bigger model.** The rules have already done the
  detecting, and each call reads about a thousand tokens.

**What did you not use AI for, and why?**

- **Detection.** The four rules are plain code with unit tests. A finding must be
  reproducible and explainable, the same every time. Claude only adds judgement on
  top, and the rule's severity is a floor it can't lower.
- **Who made a change.** That comes from Odoo's and Shopify's own records, never
  from a model's guess. When Shopify can't say, the write is `unattributed`.
- **The safety checks.** Redaction, the schema check on Claude's answers and the
  check that a suggested fix grants nothing new are code, so no prompt can talk
  them out of it.
- **Applying a fix.** Claude suggests; a person edits the register.
- **The interface.** No chat: one page a non-technical person can read without
  explanation.

**A failure case, and its fix.**
The evals caught Claude underrating an unknown app that created Shopify orders 7
times: it said high where the rubric says critical, in 3 runs out of 3. A control
run with a neutral app name failed the same way, so it wasn't prompt injection.
The rubric said "orders" but never tied the `shopify:order` scope to that word.
Naming the six exact scopes the rubric means fixed it, and the control is now a
permanent case: 15 out of 15. A second failure came from an automated security
review: an app's name could steer Claude's suggested `writers.yaml` entry into
granting more access. Code now checks every scope and identity in the entry before
it is shown. More in [the AI log](docs/ai-log.md).

**To make this 10× cheaper, what changes?**
A check already costs nothing, and each finding is triaged once, so the cost is
per new finding. Over 90% of it is Sonnet's explanations: 3 of the demo's 7 calls
make up $0.014 of its $0.015.

- **Explain with Haiku**, or with the rule's own template for the four known
  rules, and keep Sonnet for findings the rubric can't place. That alone is about
  10×: $0.015 becomes about $0.0015. It needs evals for the explanations first;
  today only the ratings are scored.
- **Rate findings in batches**, so the rubric, most of each Haiku call's input, is
  paid once per batch instead of once per finding.
- **Use the Batch API**, at half price, where a wait is fine, such as a backfill.

**To run autonomously without a human in the loop, what's missing?**

- **Tickets to owners.** Today someone has to look at the page. A finding should
  open a ticket for its writer's owner, with Claude's explanation and fix, and
  close it when the finding resolves.
- **An audit trail instead of polling.** Polling sees only the last writer of a
  record between two polls. OCA `auditlog` or a database trigger in Odoo would
  record every write.
- **A gateway for agent writes.** The ledger finds a bad write after it happened.
  Agents should write through a gateway that checks the register first, so an
  out-of-scope write is refused, not reported.
- **Automatic fixes, with limits.** Revoking a key nobody registered can be
  automatic, behind a kill switch. Granting access should stay a human decision:
  the register is code, so an agent can open the pull request and a person
  merges it.
- **Alarms.** A daily ceiling on triage spend, and an alert when the collector or
  triage stops.

**How you used your AI coding assistant on this project.**
Claude Code built it, phase by phase, under the rules in
[`AGENTS.md`](AGENTS.md). For each phase it proposed a short plan and waited for
my OK. It wrote the core's tests first and opened one pull request, which CI
checked with the whole demo end to end, and I reviewed and merged it. After each
phase, `/simplify` ran four parallel reviews (reuse, simplification, efficiency and
depth of fix), and security reviews ran on the changes. Where it got things wrong
is in [the AI log](docs/ai-log.md), with the fix for each. Examples: a race between
the seed and the collector, a test that passed for the wrong reason, a
`git checkout` that wiped uncommitted work, and a flaky CI run blamed on the wrong
change.

## Develop

```sh
make test    # Python 3.12 venv via uv, then pytest
make lint    # ruff: lint and format check, as in CI
make fmt     # fix what ruff can, then format
make smoke   # after `make up seed traffic`: every writer is in the ledger, no customer data
make eval    # Claude's triage on evals/cases.yaml (needs ANTHROPIC_API_KEY in .env)
make loc     # app code line count
make help    # all targets
```

`main` only changes through pull requests. CI runs lint and tests, then the whole
offline demo end to end (`make up seed traffic smoke`); both checks and CodeQL must
pass before a merge. More: [Develop](docs/development.md). Report vulnerabilities
privately: see [SECURITY.md](SECURITY.md).

Spec: [`docs/brief.md`](docs/brief.md). Rules for coding agents:
[`AGENTS.md`](AGENTS.md). How the AI assistant was used:
[`docs/ai-log.md`](docs/ai-log.md).
