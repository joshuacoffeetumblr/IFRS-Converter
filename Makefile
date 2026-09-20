.DEFAULT_GOAL := help
BACKEND := backend
FRONTEND := frontend
PY := $(BACKEND)/.venv/bin

.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup: setup-backend setup-frontend ## Install all dependencies

.PHONY: setup-backend
setup-backend: ## Create the backend virtualenv and install dependencies
	cd $(BACKEND) && uv venv --python 3.12 .venv && uv pip install -e ".[dev]"

.PHONY: setup-frontend
setup-frontend: ## Install frontend dependencies
	cd $(FRONTEND) && npm ci

.PHONY: up
up: ## Start the full stack (db, api, web)
	docker compose up --build

.PHONY: down
down: ## Stop the stack
	docker compose down

.PHONY: up-prod
up-prod: ## Start the production stack (reads .env; every secret is required)
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

.PHONY: migrate
migrate: ## Apply database migrations
	cd $(BACKEND) && .venv/bin/alembic upgrade head

.PHONY: revision
revision: ## Autogenerate a migration: make revision m="add projects"
	cd $(BACKEND) && .venv/bin/alembic revision --autogenerate -m "$(m)"

.PHONY: lint
lint: ## Lint both projects
	cd $(BACKEND) && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd $(FRONTEND) && npm run lint

.PHONY: format
format: ## Auto-format both projects
	cd $(BACKEND) && .venv/bin/ruff check . --fix && .venv/bin/ruff format .

.PHONY: typecheck
typecheck: ## Type-check both projects
	cd $(BACKEND) && .venv/bin/mypy app tests
	cd $(FRONTEND) && npm run typecheck

.PHONY: test
test: ## Run the backend test suite
	cd $(BACKEND) && .venv/bin/pytest

.PHONY: e2e
e2e: ## Run Playwright end-to-end tests (requires the stack to be running)
	cd $(FRONTEND) && npx playwright test

.PHONY: seed
seed: ## Load the account catalog and IFRS 18 rule set into the database
	cd $(BACKEND) && .venv/bin/python -m app.cli seed

.PHONY: validate
validate: ## Run a real statement through every stage: make validate f=statement.xlsx
	cd $(BACKEND) && .venv/bin/python -m app.cli validate "$(abspath $(f))"

.PHONY: demo
demo: ## Run the whole pipeline over the fixture and write an Excel export
	cd $(BACKEND) && .venv/bin/python -m scripts.demo_export ../out

.PHONY: fixture
fixture: ## Write the synthetic Korean statement fixtures to ./fixtures for inspection
	cd $(BACKEND) && .venv/bin/python -m tests.fixtures.dump ../fixtures

.PHONY: check
check: lint typecheck test ## Everything CI runs, locally
