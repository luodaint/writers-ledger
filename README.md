# Writers Ledger

[![CI](https://github.com/luodaint/writers-ledger/actions/workflows/ci.yml/badge.svg)](https://github.com/luodaint/writers-ledger/actions/workflows/ci.yml)

Every write into Odoo and Shopify lands in a ledger, gets matched against
`writers.yaml`, and anything unknown, out of scope or ownerless becomes a finding,
explained in plain words.

> Work in progress (Phase 2 of 4: the rules). The full README, with the cost per
> check run and the case questions answered, comes in Phase 4.

## Run it offline

```sh
make up        # Postgres, Odoo 19, a fake Shopify and the app (first boot ~2 min)
make seed      # Odoo bot users with their own API keys, 15 shoes x 6 sizes, 3 shops, stock
make traffic   # three simulated days of writes from every writer, plus four planted incidents
```

Open <http://127.0.0.1:8000> and log in as `reviewer` / `local-only-demo`. The
ledger fills within 10 seconds of the traffic. Odoo is at <http://127.0.0.1:8069>
(`admin` / `local-only-admin`). These defaults only work offline: every port is
bound to `127.0.0.1`. Change them in `.env` (see `.env.example`).

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

## How a check works

The check runs after every Odoo poll, on the **Run check now** button, and from
`make check`. It applies four rules to the ledger and the register, in
[`app/rules.py`](app/rules.py), with no AI involved:

- **unknown_writer** (high): the identity isn't in the register.
- **out_of_scope** (high): the writer changed a `system:model` missing from its `may_write`.
- **ownerless_writer** (medium): a writer with an empty owner wrote something.
- **stock_drift** (high): for a SKU and location, the newest Odoo quantity and the
  newest Shopify level differ, and neither changed in the last
  `DRIFT_SETTLE_MINUTES` (10 live, 1 offline). A sync in flight isn't drift.

Writes Shopify can't attribute are counted apart, as `unattributed_write`, and
never become a card. Repeats collapse into one finding per rule, system, model and
identity, so 40 rogue writes are one finding that says 40. The count is what the
ledger observed in the last 7 days. Polling sees only the last writer of a record
between two polls, so the real number can be higher. The button only accepts a
post from the page itself, because browsers send basic auth along with forged
cross-site posts too.

## How the Odoo API keys are handled

Each writer signs in to Odoo with its own API key, which is what lets the ledger
say *who* made a change.

**Who gets a key.** `make seed` creates one per bot user:

| Odoo login | Used by | Allowed by Odoo to |
|---|---|---|
| `ledger@alohas.test` | the app's collector | **read only**: a custom group, "Writers Ledger: read-only" |
| `sync@alohas.test` | the simulated `shopify-odoo-sync` | create customers and sale orders, and edit products (the scope-creep incident) |
| `stock@alohas.test` | the simulated `stock-cron` | count and adjust stock |
| `pmo@alohas.test` | not used yet | read |
| `admin` | the "forgotten cron" incident; this key lasts 7 days | everything |

The collector checks its own key on every poll and **refuses to poll if the key
can write** to any watched model.

**How they are made.** Odoo 19 only lets an existing key create a new key, and only
for its own user. So the seed runs inside `odoo shell`, Odoo's admin console, which
can issue a key for each bot. The bots have no password: the key is the only way in.

**Where they live.** Two files on two Docker volumes, so each container holds only
the keys it needs:

| Volume | File | Holds | Mounted by |
|---|---|---|---|
| `odoo-collector-key` | `odoo-key.json` | the read-only collector key | `seed` (writes), `app` (read-only) |
| `odoo-writer-keys` | `odoo-keys.json` | the writers' keys and the admin key | `seed` (writes), `traffic` (read-only) |

The app is the only container exposed to the internet, and it never sees a key
that can write. Both files are mode `0640`, owned by the `odoo` user and group
(gid 101); the reading containers join that group with `group_add: ["101"]`. The
keys are never printed, logged, put in environment variables or committed to git.

**Expiry and rotation.** Odoo caps keys for internal users at 90 days; the seed
sets 89, and 7 for the admin key. Run `make seed` again to rotate: it revokes the old keys, issues new
ones and rewrites the file. The app re-reads the file on every poll, so no restart
is needed, and an old key is refused (HTTP 401) right away. Set a reminder at about
80 days, or run `make seed` from a monthly cron. To cut a bot off immediately,
delete its key in Odoo (Settings, Users, the bot, API Keys) or re-run `make seed`.

**Live (Dokploy).** The same mechanism, run once from a shell on the server, in the
folder where Dokploy checked out the repo:
`docker compose -p <dokploy-app-name> run --rm -T seed < sim/seed_odoo.py`.

## What the ledger can and can't see

- **Odoo is polled** every `POLL_SECONDS` (30 live, 10 offline), so it sees only
  the last writer of each record between two polls. The production answer is an
  audit trail: OCA `auditlog` or a database trigger. On first start the collector
  observes from that moment on and ignores existing data.
- **Shopify can't say who changed inventory.** A new order is attributed to the app
  that created it. Order and product updates are attributed through the record's
  event log: only an event from 30 seconds before to 5 seconds after the change
  counts, and if two different authors fit, the write is `unattributed` rather than
  a guess. Inventory level changes are stored as `unattributed`. The fake Shopify behaves the same way, so the
  offline demo is no more generous than the real thing.
- **Shopify identities are app names**, because product events expose an app's
  name but not its id. Installing an app is limited to store staff, but a staff
  member could install one named like a registered writer.
- **The collector is hard to blind.** Each poll reads every page since the cursor,
  so a bulk write that stamps thousands of records with the same second can't stall
  it. It re-reads a 5-minute overlap, because Odoo stamps `write_date` when a
  transaction starts and a long one (a scheduled job, say) commits later; the
  ledger drops what it already has. A writer or product it isn't allowed to read is
  recorded by id (`res.users#13`, so an unknown writer) instead of stopping the poll,
  and one model failing doesn't stop the others.
- **`unattributed` is reserved.** A writer actually named that is stored in quotes
  (`"unattributed"`), so it shows in red with the unknown writers.
- **No customer data is stored.** Each collector keeps an allowlist of fields per
  model: order numbers, totals, SKUs, stock quantities. Names, emails, addresses
  and phone numbers are dropped on arrival.
- The fake Shopify keeps its stock, orders and events on a volume, so rebuilding
  doesn't reset Shopify while Odoo keeps its stock.
- Offline, `writers.yaml` is mounted into the app as a single file. On Docker
  Desktop and OrbStack (macOS) every save shows up. On Linux, an editor that saves
  by replacing the file (vim does by default) leaves the container on the old
  copy: run `docker compose -f docker-compose.fake.yml restart app` after saving.
- Two Shopify stock updates for the same SKU and location within the same second
  are stored once, because the ledger tells writes apart by record, second and
  identity, and Shopify stock is always `unattributed`. The ledger can then keep
  the older level and report drift that isn't there. Webhooks also carry no
  sub-second order, so the fix is to read the current level back from Shopify.
- Each check reads every stock level the ledger ever stored, because drift needs the
  newest one however old it is. That takes about 20 ms for a few thousand writes.
  After months of live webhooks, keep the newest level per SKU and location in a
  table of its own.
- Coming from Phase 1? Run `make seed` once more: the sync bot needs its product
  rights, and the keys rotate.

## Develop

```sh
make test    # Python 3.12 venv via uv, then pytest
make lint    # ruff
make smoke   # after `make up seed traffic`: every writer is in the ledger, no customer data
make loc     # app code line count
make help    # all targets
```

`main` only changes through pull requests. CI runs lint and tests, then the whole
offline demo end to end (`make up seed traffic smoke`); both checks and CodeQL must
pass before a merge. Report vulnerabilities privately: see [SECURITY.md](SECURITY.md).

Spec: [`docs/brief.md`](docs/brief.md). Rules for coding agents:
[`AGENTS.md`](AGENTS.md). How the AI assistant was used:
[`docs/ai-log.md`](docs/ai-log.md).
