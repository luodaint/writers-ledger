# What it can and can't see

Know these before you trust a count.

## Odoo

- **Odoo is polled**, every `POLL_SECONDS` (10 in the demo), so the ledger sees only
  the last writer of each record between two polls. A count is a floor. The
  production answer is an audit trail: OCA `auditlog` or a database trigger.
- **It observes from the moment `make seed` finished**, which the seed writes into
  the collector's key file, and ignores everything older, the seed's own writes
  included.
- **The collector is hard to blind.** Each poll reads every page since the cursor,
  so a bulk write that stamps thousands of records with the same second can't stall
  it. It re-reads a 5-minute overlap, because Odoo stamps `write_date` when a
  transaction starts and a long one (a scheduled job, say) commits later; the
  ledger drops what it already has. A writer or product it isn't allowed to read is
  recorded by id (`res.users#13`, so an unknown writer) instead of stopping the
  poll, and one model failing doesn't stop the others.
- **A record seen twice in the same second keeps its latest state.** A stock count
  is two requests, set then apply, often within one second. A poll between them sees
  the old quantity; the re-read after the apply replaces it, so the ledger never
  keeps a quantity Odoo has already moved past. Only Odoo rows are updated this way:
  one collector reads Odoo in order, so a later read is a newer state.
- **A price change counts six times.** Odoo keeps a shoe's price on the shoe, so
  changing it updates all six of its sizes, and the ledger counts what Odoo records.

## Shopify

- **Shopify can't say who changed inventory.** Stock level changes are stored as
  `unattributed` and counted in a grey note, never a card.
- **Orders and products are attributed with care.** A new order is attributed to
  the app that created it. Order and product updates are attributed through the
  record's event log: only an event from 30 seconds before to 5 seconds after the
  change counts, and if two different authors fit, the write is `unattributed`
  rather than a guess. The fake Shopify behaves the same way, so the demo is no
  more generous than the real thing.
- **Shopify identities are app names**, because product events expose an app's
  name but not its id. Installing an app is limited to store staff, but a staff
  member could install one named like a registered writer.
- **Two stock updates in the same second are one write.** The ledger tells writes
  apart by record, second and identity, and Shopify stock is always `unattributed`,
  so two Shopify updates of the same SKU and location within a second become one
  write, holding the first one received. A later delivery never rewrites it,
  because webhooks carry no sub-second order and a signed body can be replayed. The
  ledger can then hold the older level and report drift that isn't there; the fix
  is to read the current level back from Shopify.

## The ledger and the check

- **`unattributed` is reserved.** A writer actually named that is stored in quotes
  (`"unattributed"`), so it shows in red with the unknown writers.
- **No customer data is stored.** Each collector keeps an allowlist of fields per
  model: order numbers, totals, SKUs, stock quantities. Names, emails, addresses
  and phone numbers are dropped on arrival.
- **Each check reads every stock level the ledger ever stored**, because drift needs
  the newest one, however old. That takes about 20 ms for a few thousand writes.
  After months of webhooks, keep the newest level per SKU and location in a table
  of its own.
- **The page shows the latest 200 writes** for the current filters. The counts in
  the filter lists cover the whole ledger.

## The demo

- **The fake Shopify keeps its stock, orders and events on a volume**, so rebuilding
  doesn't reset Shopify while Odoo keeps its stock. Start from zero with
  `down --volumes`: see [Install and run](install.md#stop-start-again-start-from-zero).
- **Odoo deletes a stock record that reaches zero**, in its daily scheduler, which
  first runs a few minutes after the database is created. The traffic counts stock
  there again by creating a new one.
