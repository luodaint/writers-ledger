# Security

What the app protects, and how. To report a vulnerability, use GitHub's private
reporting: see [SECURITY.md](../SECURITY.md).

## The login

- **HTTP basic auth on every route** except `/webhooks/shopify`, which proves itself
  with Shopify's signature, and `/healthz`, which says only `ok`.
- **Credentials come from `APP_USER` and `APP_PASSWORD`**, compared in constant time
  with `secrets.compare_digest`. The app refuses to start without them, or with a
  password shorter than 12 characters.
- **The API docs pages are off** (`/docs`, `/redoc`, `/openapi.json`).
- **Run check now only accepts a post from the page itself.** Browsers send basic
  auth along with forged cross-site form posts too, so the app checks the
  `Sec-Fetch-Site` and `Origin` headers against its own host.

## The page

- **Templates autoescape**, and nothing from a payload or from Claude is marked safe.
- **Response headers**, on every response:

  | Header | Value |
  |---|---|
  | `Content-Security-Policy` | `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` |
  | `X-Content-Type-Options` | `nosniff` |
  | `X-Frame-Options` | `DENY` |
  | `Referrer-Policy` | `same-origin` (with `no-referrer`, Chrome sends `Origin: null` on the page's own posts, and the check button would refuse them) |
  | `Strict-Transport-Security` | `max-age=31536000` |
  | `Cache-Control` | `no-store` |

- **One script, from the app itself.** `script-src 'self'` allows only
  `/page.js`, served behind the login. There is no inline code, and the script
  never touches ledger data: it submits the filter form and marks the check button
  busy.

## Shopify webhooks

- **The body is capped** at 512 KB, then **verified before it is parsed**:
  HMAC-SHA256 over the raw bytes with the app's secret, compared in constant time.
  A bad signature is refused (401) and logged without the body.
- **Each delivery is recorded once.** Shopify redelivers; the app deduplicates on
  `X-Shopify-Webhook-Id`.
- **The answer comes first.** Shopify gives up after 5 seconds, so the app answers
  at once and asks Shopify who made the change afterwards.

## The Odoo API keys

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

The app, the only container meant to face users, never sees a key that can write.
Both files are mode `0640`, owned by the `odoo` user and group (gid 101); the
reading containers join that group with `group_add: ["101"]`. The keys are never
printed, logged, put in environment variables or committed to git.

**Expiry and rotation.** Odoo caps keys for internal users at 90 days; the seed
sets 89, and 7 for the admin key. Run `make seed` again to rotate: it revokes the
old keys, issues new ones and rewrites the files. The app re-reads its file on every
poll, so no restart is needed, and an old key is refused (HTTP 401) right away. Set
a reminder at about 80 days, or run `make seed` from a monthly cron. To cut a bot
off immediately, delete its key in Odoo (Settings, Users, the bot, API Keys) or
re-run `make seed`.

## Customer data

- **Not in the ledger.** Each collector keeps an allowlist of fields per model:
  order numbers, totals, SKUs, stock quantities. Names, emails, addresses and phone
  numbers are dropped on arrival. Odoo customers (`res.partner`) aren't read at all.
- **Not sent to Claude.** Triage sends an allowlisted summary, never a payload, and
  `app/redact.py` is a second layer that removes personal fields and masks anything
  shaped like an email address or a phone number. A login that is an email address
  goes as `[login]`. Details: [Findings and triage](findings.md#what-claude-sees).

## Claude's answers

Claude's output is untrusted text. It is checked against a JSON schema, capped in
length, shown as plain text and never run. The rule's severity is a floor Claude
can't lower. A suggested `writers.yaml` entry is shown only if it grants nothing
beyond the finding: [how](findings.md#a-fix-claude-writes-cant-grant-more-than-the-finding-is-about).

## Secrets

Secrets come from the environment only, through `.env`, which is git-ignored.
`.env.example` lists names, never secret values, and a test fails if it holds
anything but names and a few harmless defaults. Nothing logs a key, a token or a password. Each Claude call is logged with its
tokens and cost, never its prompt or answer.

## Code and containers

- **SQL uses parameters only**; column names come from a fixed list. YAML is read
  with `safe_load` only.
- **Pinned images**: `python:3.12.15-slim-bookworm`, `odoo:19.0-20261015`,
  `postgres:17.11`. Python packages install with `--require-hashes`.
- **The app container** runs as a non-root user (uid 10001), on a read-only file
  system with a `tmpfs` for `/tmp`, with every Linux capability dropped and
  `no-new-privileges`. The traffic container is locked down the same way.
- **Odoo and Postgres publish no ports** in `docker-compose.yml`. In the demo every
  port is bound to `127.0.0.1`.

## The repository

- `main` changes only through pull requests: a ruleset with no bypass requires the
  lint and tests job, the offline end-to-end demo and CodeQL, a linear history and
  squash merges.
- Secret scanning with push protection, Dependabot alerts and version updates for
  the GitHub Actions, and private vulnerability reporting are on.
- CI actions are pinned to commit SHAs, the default token is read-only, and pull
  requests from forks need approval to run.
