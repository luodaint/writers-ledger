# Test data

Odoo in the demo is real Odoo 19: the real JSON-2 API, a separate API key for each
writer, real stock moves. Only Shopify is faked, by `sim/fake_shopify.py`, which
answers the same Admin API requests and sends the same signed webhooks as a real
store.

## What `make seed` creates

`sim/seed_odoo.py` runs inside Odoo's shell and creates:

- **Bot users, each with its own API key**: the read-only collector, the sync,
  the stock cron and a PMO user, plus a key for `admin` that the "forgotten cron"
  incident uses. Who may do what: [Security](security.md#the-odoo-api-keys).
- **The catalogue** from `sim/catalog.py`: 15 shoes in sizes 36 to 41, so 90
  variants with SKUs `AL01-36` to `AL15-41`.
- **Three shops**, as Odoo stock locations: Barcelona, Milan and Online warehouse.
- **Starting stock** for every SKU in every shop. The fake Shopify starts with the
  same numbers, so nothing drifts at the start.

The seed also records when it finished. The collector's first poll starts there,
so the seed's own writes never reach the ledger.

## What `make traffic` plays

`sim/traffic.py` plays three simulated days, squeezed into about a minute. Every
day:

| Writer | What it does | Where |
|---|---|---|
| Customers, on the Online Store | 4 orders | Shopify |
| `shopify-odoo-sync` | a customer and a sale order for each of them | Odoo |
| `stock-cron` | counts the stock of a SKU in a shop, 3 times, and pushes each new number | Odoo, then Shopify |
| `pmo-pricing-script` (the `pricing-tool` app) | sets a drop label on one product | Shopify |

And the four planted incidents the check must find:

1. **The forgotten cron.** A script with the `admin` key resets 4 Milan SKUs every
   night; the stock cron then pushes them to Shopify.
2. **Scope creep.** `shopify-odoo-sync` writes a product price in Odoo, which its
   register entry doesn't allow.
3. **Ownerless.** `pmo-pricing-script` writes, and its owner is empty.
4. **Drift.** At the end, one push sends Barcelona 3 more pairs of `AL05-38` than
   Odoo has.

The customers' names and emails are made up (`example.com`). They are there so
the redaction has something to remove: the ledger drops them on arrival.

## 1. More, or different, data

`make traffic` doesn't pass settings through, so run the container directly:

```sh
docker compose -f docker-compose.fake.yml run --rm -T -e TRAFFIC_DAYS=30 -e TRAFFIC_SEED=42 traffic
```

- **`TRAFFIC_DAYS`** sets how many simulated days to play (default 3), so 30 gives
  about ten times the writes. Days are squeezed into seconds, so they all fall
  inside the 7-day window and the finding counts grow to match.
- **`TRAFFIC_SEED`** picks different SKUs, customers and orders (default 7).
- **Running it again** adds to what's there. Findings stay deduplicated: the same
  fingerprints come back with higher counts. Drift disappears for about a minute
  after each run, while the settle window waits for the stock to stop moving, and
  then comes back.
- **To start from zero:** `docker compose -f docker-compose.fake.yml down --volumes`,
  then `make up seed traffic`. This wipes the ledger, Odoo and the fake Shopify.

## 2. Be a writer yourself

This is the most convincing test, because it uses no scripted data at all: the
ledger catches a change a person made by hand.

1. Open Odoo at <http://127.0.0.1:8069> and log in as `admin`. The password is
   `ODOO_ADMIN_PASSWORD` from your `.env`, or `local-only-admin` if you never set it.
2. Open a shoe (Inventory, Products), change its **Sales Price** and save.
3. Within about 10 seconds, the page shows a new `unknown_writer` finding for
   `admin` on `odoo:product.product`, with 6 writes: Odoo keeps the price on the
   shoe and stamps all six of its sizes. With triage on, Claude rates it a few
   seconds later.
4. To see it resolve, register yourself. Add this to `writers.yaml`, save, and
   press **Run check now**:

   ```yaml
   - id: catalogue-admin
     owner: ecommerce
     purpose: Sets product prices by hand in Odoo
     identity: { odoo_user: admin }
     may_write: [odoo:product.product]
   ```

   Both of admin's `unknown_writer` findings resolve, the price change and the
   forgotten cron. The cron's stock writes come back as `out_of_scope`: admin is a
   known writer now, but not one allowed to write stock. Remove the entry to put
   things back.

Two things change the result:

- **If you already did the README demo's fix**, `admin` belongs to
  `milan-stock-reset`, allowed `odoo:stock.quant`, so the price change shows as
  `out_of_scope` straight away. Don't add the entry above: an identity can belong
  to one writer only, and a second one is an error that pauses the checks. Add
  `odoo:product.product` to that entry's `may_write` instead, and the price finding
  resolves.
- Counting stock as `admin` (Odoo's Physical Inventory) adds to the forgotten
  cron's finding instead of making a new one: it is the same rule, model and
  identity, so the same fingerprint.

Any other Odoo login that isn't in the register behaves the same way.

## 3. A real Shopify dev store, still on your machine (not built yet)

**What it adds:** the real Shopify API answers "who made this change" for orders,
products and stock, instead of the fake copying that behaviour.

**What it takes:**

- **In Shopify:**
  - a dev store;
  - three apps in Shopify's Dev Dashboard: the `writers-ledger` app that receives
    webhooks, `stock-sync` and `pricing-tool`;
  - access to protected customer data for the `writers-ledger` app, so it can
    receive order webhooks.
- **Code to add:**
  - a `sim/seed_shopify.py` that creates the 90 variants and 3 locations with the
    same starting stock as Odoo;
  - a `traffic` service in `docker-compose.yml`;
  - code to register the webhooks with Shopify.
- **A tunnel** such as cloudflared or ngrok, because Shopify only sends webhooks to
  a public HTTPS address. That puts your local app on the internet while the tunnel
  is up. Basic auth covers every route except the webhook, which checks Shopify's
  HMAC signature, and `/healthz`. Free tunnel addresses change each time, so the
  webhooks have to be registered again before each demo.
- **Orders:** the fake shop's checkout has no API equivalent on a real store.
  Orders would have to be test orders placed in the storefront with Shopify's
  Bogus Gateway, its fake test payment option, or be left out.

**What to expect.** The fake Shopify already follows the real API's limits: it
reports stock changes as `unattributed`, and only credits an order or product
change to an author whose event falls between 30 seconds before and 5 seconds after
it. So a real store should mostly confirm what the demo shows, not reveal new
findings. Option 2 tests something different: scripted traffic shows that the
planted incidents are caught; a change by hand shows that something nobody
planned for is caught too.
