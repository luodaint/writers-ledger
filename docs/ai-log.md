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

## Between phases: GitHub and CI

- **Asked:** a public repo `luodaint/writers-ledger`, CI/CD ("tests, cleanup, etc."), changes only through PRs, issues and wiki on.
- **Done:** CI runs `make lint test`, then the offline demo end to end (`make up seed traffic smoke`). `main` has a ruleset with no bypass: PR, both checks and CodeQL required, squash merge only. Secret scanning with push protection, Dependabot for the action pins, private vulnerability reporting. Ruff added as a dev dependency for "cleanup".
- **Got wrong:** the first `sim/smoke.py` compared `sqlite3.Row` objects to tuples, so it could never pass; caught by testing it against a hand-built ledger before CI ran it. `make traffic` needed `-T` to run without a terminal.
- **Changed by hand:** nothing.

## Phase 2: rules

- **Asked:** the four rules, deduplication, the planted incidents. Owner's decisions: register the storefront as a writer, show Shopify's unattributed stock changes as a grey note, keep app names as Shopify identities. Run `/simplify` when done.
- **Done:** `rules.py` is pure and was written tests first (21 tests). A finding is stored per fingerprint; its count is recomputed by each check and never summed, and a check that no longer finds it resolves it. Checks run after every Odoo poll, on a "Run check now" button and with `make check`. Each check re-reads `writers.yaml`, and a broken edit pauses checks instead of resolving everything. `sim/traffic.py` plants the four incidents, and `make smoke` (in CI) requires exactly those four.
- **Got wrong:** summaries said "1 times". A drift test fed writes oldest-first, so removing the sort went unnoticed; mutation testing caught it. `Referrer-Policy: no-referrer` makes Chrome send `Origin: null` on form posts, so the button was refused in a real browser while the unit tests passed. Tests read the repo's `writers.yaml`, so following the README demo would have broken `make test`; the `/simplify` review caught it. App code is 617 lines against a ~560 estimate.
- **Changed by hand:** nothing.

## Phase 3: triage and evals

- **Asked:** redaction, two-tier triage (`claude-haiku-5-5` rates every finding, `claude-sonnet-5-5` explains the high and critical ones), the cost in the footer, evals. Owner's decisions: about 700 lines of app code, and triage in the background so "Run check now" stays fast.
- **Done:**
  - `redact.py` written tests first.
  - `triage.py` tested against a stub client that records every request: no email or phone pattern may appear in anything sent.
  - The rule's severity is a floor. Email-shaped logins go to the model as `[login]`.
  - Results are cached by the exact input. Triage runs in its own thread. Sonnet's declined requests fall back server-side (`fallbacks: "default"`).
  - `make eval`: 15 cases scoring Claude's own rating. A run costs $0.059; the first full triage of the demo was 7 calls, $0.0143.
- **The evals caught:**
  - **First run, 13/14.** An unknown `temp-import` script changing stock 30 times came back high instead of critical, 1 time in 3; Claude read the name as a harmless one-off.
  - **After a first fix, 13/14 again.** The rubric gained "however harmless its name sounds" and named the models. Stock then passed, but an unknown app creating Shopify orders 7 times fell to high, 3 times in 3. A control run showed the same with a neutral name: the rubric didn't map `shopify:order` to "orders", and the injection text played no part.
  - **Final fix, 14/14.** Naming the six exact `what_changed` values fixed both. The neutral-name control is now a permanent case: 15 in all.
- **Got wrong:**
  - **The API key went into the public `.env.example`, not `.env`.** It was moved before any commit, and a test now fails on any value there. A search printed the key once in this session, so it should be rotated.
  - **The collector's first poll started from its own moment.** Traffic played straight after `make seed` was never recorded, a race present since Phase 1 that an end-to-end run hit. The seed now writes when it ended, and the collector starts there.
  - **A test checked for `[login]` anywhere in the request,** which the system prompt already contained; mutation testing caught it.
  - **App code is 779 lines against the ~705 estimate.** `triage.py` came out about 30 lines longer than planned.
  - **While undoing a deliberate mutation, I ran `git checkout -- app/main.py`.** It threw away the file's uncommitted Phase 3 work. I restored it from the diff saved for the `/simplify` review, re-applied the later edits, and checked the line count matched. Mutations are now undone only from a backup copy.
- **`/simplify` (four parallel reviews) changed:**
  - **The cache key.** It included the exact drift quantities, so a stock-sync outage would have paid for a triage on every sale, up to about $11 an hour. It also left out the prompts and models, so a prompt change never re-triaged anything. It is now fingerprint, a count bucket (below or at 5), the register entry, the prompts and the models.
  - **Refused requests.** A request Claude rejects (400 or 404) is final for that input; before, it was retried and paid for on every poll.
  - **One severity floor.** Claude's rating is stored as given, and the floor is applied once, on the page.
  - **`make eval`** scores only the rating, so a run costs $0.0027 instead of $0.059.
  - **The collector** requires the seed's end mark.
  - **Skipped:** the efficiency, reuse and altitude reviews' remaining ideas are listed in the PR. App code is 776 lines.
- **Automated security review after the push** (valid):
  - **The finding:** a Shopify app name or Odoo login is chosen by whoever writes, so prompt injection could steer Claude's "ready-to-paste" `writers.yaml` entry into granting more access.
  - **The fix:** before an entry is shown, code checks that every scope in it is the one changed or one the writer already has, and every identity is the writer's own. Otherwise the card falls back to the rule's advice. The card now labels Claude's text, so the fix is checked before pasting.
  - **Cost:** 13 lines, which takes app code to 789.
- **Changed by hand:** nothing.

## Between phases: ledger filters and docs

- **Asked:**
  - a System column in the ledger, and filters that react;
  - a sign that **Run check now** is running;
  - three ways to test with other data in the README;
  - documentation in several pages.
- **Done:**
  - each filter lists only what the other filters leave, with counts, and applies on change; a click on a row's system, writer or model filters by it;
  - the button says "Checking…" while it runs. One same-origin script does both, and the page still works without it;
  - eight pages in `docs/`. The README's long reference sections moved there.
- **Got wrong:**
  - **The testing options I first gave in chat said a second `make traffic` adds to what's there. It crashed.** Odoo's scheduler deletes a stock quant that reaches zero, and the traffic assumed every quant still existed. Fixed test-first, and checked by playing the traffic three times on one stack.
  - **I expected a price edit in Odoo's product form to stay invisible**, because the collector watches variants, not templates. Trying it in the real UI showed Odoo stamps all six sizes, so the option was kept as written.
  - **The first browser check of the busy button read the page after its reload**, so it reported a failure that wasn't there.
  - **CI's end-to-end run failed twice on drift that wasn't there, and I blamed the wrong change.**
    - **What I blamed:** a `/simplify` idea I had applied, counting stock with Odoo 19's inventory-mode `create` upsert. Odoo's source shows it sets the count by assignment, which the ORM skips when the value equals the cached reset 0, so I reverted it to an explicit write.
    - **The real cause:** a race the ledger had since Phase 1, which Phase 3's pull request had also hit once. A stock count is two requests, set then apply, within one second on a fast runner. A poll between them stored the old quantity, and the re-read after the apply was dropped as a duplicate of the same record, second and writer. `make traffic` restarts the app, so a poll often lands inside the 2-second burst.
    - **The fix:** the ledger now keeps the newest state of an Odoo record seen again in the same second. A test reproduces the race. The automated security review then pointed out that a later delivery proves nothing for Shopify, whose webhooks can arrive out of order or be replayed, so Shopify rows are never rewritten. The write stays too: the upsert's zero case is a real risk in Odoo's code, even though CI didn't hit it.
- **`/simplify` changed:**
  - a dead class, two duplicate tests, and a check time that had lost its date.
  - **Skipped:** building the row links in Python and two new SQLite indexes. Each costs app code lines, for gains only on a ledger far larger than the demo. App code is 801 lines.
- **Changed by hand:** nothing.

## Between phases: ruff format

- **Asked:** ruff as the formatter too, at 175 columns, checked in CI, with an automatic fix.
- **Done:**
  - `make fmt` fixes what ruff can and formats the code;
  - `make lint`, which CI runs, now fails on unformatted code too.
  - CI checks but doesn't commit fixes, because the workflow token stays read-only.
- **Got wrong:**
  - **The formatter made app code longer, not shorter: 804 to 856 lines.** It lays out a call or dict that doesn't fit on one line with one item per line, where the code had packed several.
  - **It moved one comment off its line** (Sonnet's fallback price) and let another run past 175 columns. Both were fixed by hand.
- **Changed by hand:** nothing.
