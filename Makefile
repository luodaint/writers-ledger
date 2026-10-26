# Offline stack by default. For the live file: make up COMPOSE="docker compose"
COMPOSE ?= docker compose -f docker-compose.fake.yml

.PHONY: help up down logs test loc seed traffic

help: ## List the targets
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-6s %s\n", $$1, $$2}'

up: ## Start Odoo, Postgres and the app (http://127.0.0.1:8000)
	$(COMPOSE) up -d --build --wait

down: ## Stop everything (data is kept)
	$(COMPOSE) down

logs: ## Follow the logs
	$(COMPOSE) logs -f

test: .venv ## Run the tests
	.venv/bin/pytest -q

loc: ## Count app code lines (non-blank, non-comment Python in app/)
	@cat app/*.py | grep -cvE '^[[:space:]]*(#|$$)'

.venv: requirements.txt requirements-dev.txt
	uv venv --python 3.12 --allow-existing .venv
	uv pip install --python .venv --require-hashes -r requirements.txt -r requirements-dev.txt
	@touch .venv

seed: ## Create Odoo users with their API keys, products, locations and stock
	$(COMPOSE) run --rm -T seed < sim/seed_odoo.py

traffic: ## Play a few simulated days of normal writes from every writer
	$(COMPOSE) run --rm traffic
