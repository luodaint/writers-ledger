# Install and run

The demo runs on your machine, in Docker: real Odoo 19, a fake Shopify and the app.
No accounts are needed, except an optional Anthropic API key for triage.

## What you need

- Docker with Compose v2: Docker Desktop, OrbStack or Docker Engine.
- `git` and `make`.
- About two minutes for the first boot, while Odoo creates its database.
- Optional, for triage: an Anthropic API key.
- Only for development: [uv](https://docs.astral.sh/uv/), which installs
  Python 3.12 for the tests. See [Develop](development.md).

## Run the demo

```sh
git clone https://github.com/luodaint/writers-ledger.git
cd writers-ledger
make up seed traffic
```

- `make up` builds the app and starts Postgres, Odoo, the fake Shopify and the app.
- `make seed` creates the Odoo bot users with their API keys, 15 shoes in 6 sizes,
  3 shops and their stock.
- `make traffic` plays three simulated days of writes from every writer, plus four
  planted incidents.

What each one creates: [Test data](data.md).

| What | Where | Login |
|---|---|---|
| The app | <http://127.0.0.1:8000> | `reviewer` / `local-only-demo` |
| Odoo | <http://127.0.0.1:8069> | `admin` / `local-only-admin` |

The ledger fills within 10 seconds of the traffic. The findings follow about a
minute later, because drift waits for a one-minute settle window. Every port is
bound to `127.0.0.1`, so the default passwords never reach another machine.

## Turn on triage

```sh
cp .env.example .env
chmod 600 .env
```

Put your key after `ANTHROPIC_API_KEY=` in `.env`, then run `make up` again to
recreate the app with it. Without a key, the page says triage is off and
everything else works. A first triage of the demo's four findings costs about
$0.015; the footer shows the cost of the last triage run.

`.env` is git-ignored. Never put a value in `.env.example`: it is public.

## Settings

All settings come from `.env`. The demo needs none of them.

| Setting | Default in the demo | What it does |
|---|---|---|
| `APP_USER`, `APP_PASSWORD` | `reviewer`, `local-only-demo` | The login for the page. |
| `POSTGRES_PASSWORD`, `ODOO_ADMIN_PASSWORD` | `local-only-postgres`, `local-only-admin` | Set when the database is created; changing them later needs a start from zero. |
| `ODOO_DB` | `writers` | The Odoo database name. |
| `POLL_SECONDS` | `10` | How often the app reads Odoo. |
| `DRIFT_SETTLE_MINUTES` | `1` | How long Odoo and Shopify stock may differ before it counts as drift. |
| `ANTHROPIC_API_KEY` | empty | Turns triage on. |
| `TRIAGE_MODEL_SMALL`, `TRIAGE_MODEL_LARGE` | `claude-haiku-5-5`, `claude-sonnet-5-5` | The models that rate and explain findings. |
| `SHOPIFY_*` | the fake Shopify's | Only for a real Shopify store, which the demo doesn't use. |

## Stop, start again, start from zero

```sh
make down     # stop; the ledger, Odoo and the fake Shopify keep their data
make up       # start again where you left off
make logs     # follow the logs
make check    # run a check now, like the button
```

To wipe everything and see the demo's starting state again:

```sh
docker compose -f docker-compose.fake.yml down --volumes
make up seed traffic
```

`make seed` on its own, without the wipe, rotates the Odoo keys and keeps the
ledger: see [Security](security.md#the-odoo-api-keys).

## When something goes wrong

- **The page says "Not authenticated" and never asks for a login.** Some browsers
  built into editors don't show the login prompt. Open the page in a normal browser.
- **The ledger stays empty.** The logs (`make logs`) say "No Odoo key from a
  current seed for the collector yet". Run `make seed`.
- **Pulling an image fails** (`Bad Gateway`, a timeout). Docker Hub had a hiccup;
  run `make up` again.
- **Port 8000 or 8069 is taken.** Stop whatever uses it, or change the left-hand
  port in `docker-compose.fake.yml`.
- **The page says "writers.yaml has a problem".** Checks pause until the file is
  fixed; the message names the problem. See [The register](register.md).
- **On Linux, an edit to `writers.yaml` doesn't show up.** The file is mounted on
  its own, and editors that save by replacing it (vim does) leave the container on
  the old copy. Run `docker compose -f docker-compose.fake.yml restart app`.
- **`make smoke` checks the whole demo**: every writer seen, no customer data, the
  four planted incidents found and nothing else. It is what CI runs.

## The other compose file

`docker-compose.yml` is a server version: Odoo, Postgres and the app, without the
fake Shopify. It publishes no ports, because it expects a reverse proxy with HTTPS
in front, and it refuses to start without `APP_USER`, `APP_PASSWORD` (at least 12
characters), `POSTGRES_PASSWORD` and `ODOO_ADMIN_PASSWORD`. The demo doesn't use it.
`make up COMPOSE="docker compose"` selects it.
