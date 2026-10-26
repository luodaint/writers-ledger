# AI log

How I used my AI coding assistant (Claude Code), phase by phase: what I asked,
where it got it wrong, what I changed by hand.

## Phase 0: skeleton

- **Asked:** propose a Phase 0 plan from `docs/brief.md`, then build it with DDD and TDD; write `AGENTS.md`; tell me what to set up on Hetzner, Odoo and Shopify.
- **Caught before coding:** the brief's `SHOPIFY_TOKEN` no longer fits Shopify (no new admin-created custom apps since Jan 2026; Dev Dashboard apps get ~24 h tokens via client credentials), and live Shopify can't attribute inventory writes, so those are `unattributed` offline too. Odoo 19 API keys need an expiry (max 90 days without admin).
- **Got wrong:** the "every route needs login" test assumed a flat `app.routes`; FastAPI 0.143 wraps included routers in a private class, so the test saw no protected routes. Fixed the test to list the routes of the app and of the protected `pages` router, without reaching into private API. My first Odoo idea (`-i` on boot) would have created the database with `admin/admin`; switched to `odoo db init --password`.
- **Changed by hand:** nothing yet.
