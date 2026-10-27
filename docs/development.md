# Develop

The rules for coding agents, which people follow too, are in
[AGENTS.md](../AGENTS.md). This page is the short version.

## Commands

```sh
make test    # a Python 3.12 venv via uv, then pytest
make lint    # ruff: lint and format check, the same as CI
make fmt     # fix what ruff can, then format the code
make smoke   # after `make up seed traffic`: the whole demo, end to end
make eval    # Claude's triage on evals/cases.yaml (needs ANTHROPIC_API_KEY in .env; costs cents)
make loc     # app code line count
make help    # all targets
```

`make test` and `make lint` create `.venv` on first use with
[uv](https://docs.astral.sh/uv/), installing the pinned packages with
`--require-hashes`. The tests need no Docker and no network.

## How the code is tested

- **Tests first.** Red, green, refactor: write the failing test, see it fail for
  the right reason, write the least code that passes, then clean up. This is
  mandatory for the domain core (the register, the rules, fingerprints,
  redaction), the webhook signature check and the login.
- **Every bug fix starts with a failing test** that reproduces it.
- **No network in unit tests.** The collectors are tested against recorded Odoo
  responses and the fake Shopify's payloads, the Claude client is a stub, and the
  traffic's Odoo calls go to a fake.
- **Mutation checks.** After a change, break the code on purpose (drop a condition,
  flip a comparison) and confirm a test fails, then restore it from a copy.
- **Claude's behaviour is measured by evals, not unit tests.** Run `make eval`
  before and after any change to `app/triage.yaml`, and note regressions in
  [the AI log](ai-log.md). See [Findings and triage](findings.md#evals).
- **The page is checked in a browser** too: the console must show no CSP errors.

## Rules for the code

- The domain core (`app/register.py`, `app/rules.py`, `app/redact.py`) is plain
  functions with no I/O; it never imports FastAPI, httpx, sqlite3 or anthropic. All
  I/O lives in the adapters, and `app/main.py` is the only module that imports both.
- Domain values are plain dicts or frozen dataclasses. No repositories, service
  layers, base classes with one child or plugin systems.
- **Style is ruff's**: lines up to 175 columns, formatted by `ruff format`. Run
  `make fmt` before you push.
- Use the words in [What it does](overview.md#words), with those meanings, in code,
  tests, the page and docs.
- **Size budget:** about 700 lines of app code in `app/`, counted by `make loc`.
  Templates, `app/triage.yaml`, `sim/`, `evals/` and `tests/` don't count.

## Dependencies

Runtime: `fastapi`, `uvicorn`, `jinja2`, `httpx`, `pyyaml`, `anthropic`. Dev:
`pytest`, `ruff`. Ask the owner before adding anything. Versions are pinned with
hashes:

```sh
uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes \
  --exclude-newer <date> -o requirements.txt
```

`requirements-dev.in` builds on `requirements.txt` the same way.

## Pull requests and CI

Every change is a branch and a pull request into `main`. Nobody pushes to `main`,
and agents never merge their own pull requests: the owner reviews and
squash-merges.

CI ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) runs on every pull
request and every push to `main`:

1. **Lint and tests**: `make lint` (ruff's lint and format check; `make fmt` fixes
   a failure), `make test`, the line count in the job summary,
   and a check that `docker-compose.yml` is valid.
2. **Offline demo, end to end**: `make up seed traffic smoke` on a fresh runner,
   with the logs dumped if it fails.

Both, and CodeQL, must pass before a merge. Dependabot proposes updates to the
GitHub Actions weekly, after a 14-day cooldown.

After each phase of [the brief](brief.md), [the AI log](ai-log.md) gets a few
lines: what was asked, where the agent got it wrong, what was changed by hand.
