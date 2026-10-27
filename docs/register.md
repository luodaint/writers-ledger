# The register

`writers.yaml` lists every writer allowed to write into Odoo or Shopify: who it is
in each system, what it may change and who owns it. It is the fix for most
findings: register a writer, give it an owner, or allow it a scope, and the next
check resolves the finding.

## An entry

```yaml
- id: stock-cron
  owner: sysadmin
  purpose: Pushes Odoo stock levels to Shopify locations
  identity: { odoo_user: stock@alohas.test, shopify_app: stock-sync }
  may_write: [odoo:stock.quant, shopify:inventory_level]
```

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | A unique name for the writer. |
| `owner` | yes, may be `""` | The team or person to ask about it. `""` means nobody owns it, which is itself a finding. |
| `purpose` | no | One line on what it is for. The page shows it, and Claude reads it. |
| `identity` | yes, at least one | How each system names this writer: `odoo_user` is the Odoo login, `shopify_app` the Shopify app's name. An identity belongs to one writer only. |
| `may_write` | no | The scopes it may change, as `system:model`. An empty list means it may change nothing. |

A writer that works in both systems has both identities in one entry, like the
stock cron above: one writer, one owner, one list of scopes.

## Which scopes the ledger sees

| Scope | What changes |
|---|---|
| `odoo:sale.order` | Sale orders: created, confirmed, totals. |
| `odoo:stock.quant` | Stock quantities, in the shops and warehouses. |
| `odoo:stock.picking` | Transfers: receipts, deliveries, moves between shops. |
| `odoo:product.product` | Products: price, SKU, archived or not. |
| `shopify:order` | Orders, created and updated. |
| `shopify:product` | Products. |
| `shopify:inventory_level` | Stock levels. Shopify never says who changed one, so these are always `unattributed`. |

Any other `system:model` is valid in the register, such as `odoo:res.partner` in
the demo, but the ledger never sees writes to it. Customers are personal data, and
the collectors don't read them.

## Edit it while the app runs

Every check re-reads the file, so there is no restart. Save it and press **Run
check now**, or wait for the next check. A file that doesn't parse or validate
pauses the checks: the page shows the problem and keeps the last version that
worked, and nothing resolves until the file is fixed. The messages say what is
wrong, for example:

- `stock-cron: missing 'owner' (write owner: "" if nobody owns it)`
- `my-bot: needs at least one identity (odoo_user or shopify_app)`
- `identity 'admin' is claimed by milan-stock-reset and catalogue-admin`
- `my-bot: bad may_write entry 'stock' (expected system:model, e.g. odoo:stock.quant)`
- `duplicate id 'stock-cron'`

On Linux, see the note on editors in [Install and run](install.md#when-something-goes-wrong).

## Which finding, which fix

| Finding | Fix it in the register | Or fix it at the source |
|---|---|---|
| `unknown_writer`: someone not in the register is writing | Add an entry with that identity, an owner and its scopes. | Revoke its key in Odoo or uninstall its Shopify app. The finding resolves when its last write is older than the 7-day window. |
| `out_of_scope`: a writer changed something it may not | Add the scope to its `may_write`, if it should write there. | Take the right away from it in Odoo or Shopify. |
| `ownerless_writer`: a writer nobody owns is active | Set its `owner`. | Stop the writer. |
| `stock_drift`: Odoo and Shopify disagree on a SKU's stock in a shop | Nothing to register. | Correct the stock on the wrong side. |

A writer can break two rules at once: an ownerless writer that also writes outside
its scopes has both findings.

Registering a writer is a decision, not a formality. Registering the forgotten
cron, as the README demo does, says "we know about it, sysadmin owns it". If
nobody should be using the admin key, revoke the key instead.

Claude's suggested entry on a finding's card can't grant more than the finding is
about: code checks it before it is shown. Read it before you paste it. How:
[Findings and triage](findings.md#a-fix-claude-writes-cant-grant-more-than-the-finding-is-about).
