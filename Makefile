# Offline stack by default. For the live file: make up COMPOSE="docker compose"
COMPOSE ?= docker compose -f docker-compose.fake.yml

.PHONY: help up down logs test lint loc seed traffic check smoke eval

help: ## List the targets
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

up: ## Start Odoo, Postgres and the app (http://127.0.0.1:8000)
	$(COMPOSE) up -d --build --wait

down: ## Stop everything (data is kept)
	$(COMPOSE) down

logs: ## Follow the logs
	$(COMPOSE) logs -f

test: .venv ## Run the tests
	.venv/bin/pytest -q

lint: .venv ## Check the code with ruff (the same check as CI)
	.venv/bin/ruff check .

loc: ## Count app code lines (non-blank, non-comment Python in app/)
	@cat app/*.py | grep -cvE '^[[:space:]]*(#|$$)'

.venv: requirements.txt requirements-dev.txt
	uv venv --python 3.12 --allow-existing .venv
	uv pip install --python .venv --require-hashes -r requirements.txt -r requirements-dev.txt
	@touch .venv

seed: ## Create Odoo users with their API keys, products, locations and stock
	$(COMPOSE) run --rm -T seed < sim/seed_odoo.py

traffic: ## Play simulated days of writes from every writer, plus the four planted incidents
	$(COMPOSE) run --rm -T traffic

check: ## Run a check now: the rules over the ledger and writers.yaml
	$(COMPOSE) exec -T app python -c 'from app.main import run_check; run_check()'

smoke: ## Check the running offline demo end to end (what CI runs after seed and traffic)
	$(COMPOSE) exec -T app python -m sim.smoke

eval: .venv ## Score Claude's triage on evals/cases.yaml (reads ANTHROPIC_API_KEY from .env; costs cents)
	@set -a; [ ! -f .env ] || . ./.env; set +a; .venv/bin/python -m evals.run
