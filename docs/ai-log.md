# AI log

How I used my AI coding assistant (Claude Code), phase by phase: what I asked,
where it got it wrong, what I changed by hand.

## Phase 0: skeleton

- **Asked:** propose a Phase 0 plan from `docs/brief.md`, then build it with DDD and TDD; write `AGENTS.md`; tell me what to set up on Hetzner, Odoo and Shopify.
- **Caught before coding:** the brief's `SHOPIFY_TOKEN` no longer fits Shopify (no new admin-created custom apps since Jan 2026; Dev Dashboard apps get ~24 h tokens via client credentials), and live Shopify can't attribute inventory writes, so those are `unattributed` offline too. Odoo 19 API keys need an expiry (max 90 days without admin).
- **Got wrong:** the "every route needs login" test assumed a flat `app.routes`; FastAPI 0.143 wraps included routers in a private class, so the test saw no protected routes. Fixed the test to list the routes of the app and of the protected `pages` router, without reaching into private API. My first Odoo idea (`-i` on boot) would have created the database with `admin/admin`; switched to `odoo db init --password`.
- **Changed by hand:** nothing yet.

## Phase 1: ledger

- **Asked:** the Odoo collector, the Shopify webhook handler, a fake Shopify, seed and normal traffic. API keys on a volume, explained in the README; around 500–600 lines is fine.
- **Caught by probing the real systems first:** Odoo's standard Sales and Inventory groups grant write access, so the collector got its own read-only group plus a self-check that refuses a key that can write. Each stock adjustment also writes a mirror quant in a virtual location, so the collector watches only real locations. Shopify's `2026-04`+ API requires `@idempotent` and `changeFromQuantity` on inventory writes; the fake enforces both so the traffic code stays valid against a real store.
- **Got wrong:** the first-poll cursor equalled the newest historic write, so the next `>=` poll re-recorded the last seed write as new. On a fresh install that would have been a false unknown-writer finding; a test caught it and the cursor now starts one second later. The seed changed bot groups from `odoo shell` without telling the running server, which kept serving stale permissions until `env.registry.signal_changes()` was added. The ledger table first overflowed the page; restructured to five columns.
- **Found in review after the first commit:** a second-opinion review and an automated security review caught five more, all fixed with tests first.
  - The ledger's unique key lacked the identity, so a rogue write in the same second as a legitimate one would have vanished.
  - An empty webhook id would have made later deliveries look like duplicates.
  - The fake Shopify forgot its stock on every rebuild, which would have produced false drift.
  - The internet-facing app could read the admin key; it now gets only the read-only key.
  - A bulk write of more than 500 records in one second could stall the collector.
  - Also tightened: a product event only counts if it is within two minutes of the change.
- **Second automated security pass:** three more fixes, tests first.
  - A long Odoo transaction committing after the poll was missed; the collector now re-reads a 5-minute overlap.
  - One unreadable record stalled the collector forever; it is now recorded by id.
  - A writer named `unattributed` could hide behind the sentinel.
  - Running it end to end exposed a latent seed bug: re-seeding rewrote every SKU and would have looked like 90 writes by `__system__`.
  - Not changed: Shopify identity by app name. Product events expose no app id; the owner decides.
- **Third automated security pass:** order updates were blamed on the app that created the order, and a later event by another app could take the blame for a change. Updates are now attributed through the record's events in a 30-second-before to 5-second-after window, and two authors in that window mean `unattributed`.
- **Open for Phase 2:** customers buying on the Online Store show as a writer missing from the register.
- **Changed by hand:** nothing yet.
