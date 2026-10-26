# Writers Ledger

Every write into Odoo and Shopify lands in a ledger, gets matched against
`writers.yaml`, and anything unknown, out of scope or ownerless becomes a finding,
explained in plain words.

> Work in progress (Phase 0 of 4: skeleton). The full README, with the cost per
> check run and the case questions answered, comes in Phase 4.

## Run it offline

```sh
docker compose -f docker-compose.fake.yml up    # or: make up
```

Open <http://127.0.0.1:8000> and log in as `reviewer` / `local-only-demo`.
Odoo is at <http://127.0.0.1:8069> (`admin` / `local-only-admin`). These defaults
only work offline: every port is bound to `127.0.0.1`. Change them in `.env`
(see `.env.example`).

## Develop

```sh
make test    # Python 3.12 venv via uv, then pytest
make loc     # app code line count (budget: 500)
make help    # all targets
```

Spec: [`docs/brief.md`](docs/brief.md). Rules for coding agents:
[`AGENTS.md`](AGENTS.md). How the AI assistant was used:
[`docs/ai-log.md`](docs/ai-log.md).
