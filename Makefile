# LaneReach developer commands. Requires uv and Docker.
.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help up down logs migrate migrate-local seed seed-local purge run web-install web-dev web-test web-types lint format typecheck test check check-docker coverage audit eval

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

up: ## Start the Compose stack (postgres, redis, migrate, api, worker, beat)
	docker compose up -d --build

down: ## Stop the Compose stack
	docker compose down

logs: ## Tail Compose logs
	docker compose logs -f api worker beat

migrate: ## Create DB roles and run Alembic migrations inside Compose
	docker compose run --rm migrate

migrate-local: ## Same as migrate, against DATABASE_* URLs from .env
	$(UV) run lanereach-admin bootstrap-db
	$(UV) run lanereach-admin migrate

seed: ## Load demo tenants, offices, contacts, templates and workflows (idempotent)
	docker compose run --rm migrate lanereach-admin seed

seed-local: ## Same as seed, against DATABASE_OWNER_URL from .env
	$(UV) run lanereach-admin seed

purge: ## Run the data-retention purge now (it also runs daily on beat)
	docker compose run --rm migrate lanereach-admin purge-expired-data

run: ## Run the API locally with reload
	$(UV) run uvicorn lanereach.main:create_app --factory --reload --port 8080

lint: ## Ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Apply ruff formatting and safe fixes
	$(UV) run ruff format .
	$(UV) run ruff check . --fix

typecheck: ## mypy (strict)
	$(UV) run mypy

test: ## Full pytest suite (mock LLM, testcontainers Postgres/Redis)
	$(UV) run pytest

check: lint typecheck test ## ruff + mypy + pytest. Must pass before every commit.

# The same checks in Linux (docker/Dockerfile.dev) for hosts that cannot run the Python
# environment, e.g. when Windows Application Control blocks compiled extensions.
DEV_RUN = docker run --rm -v "$(CURDIR):/work" -v /var/run/docker.sock:/var/run/docker.sock 	-e PYTHONPATH=/work/src -e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal 	-e TESTCONTAINERS_RYUK_DISABLED=true lanereach-dev

check-docker: ## ruff + mypy + pytest inside the dev image (builds it first)
	docker build -q -f docker/Dockerfile.dev -t lanereach-dev .
	$(DEV_RUN) sh -c "ruff check . && ruff format --check . && mypy && pytest"

coverage: ## pytest with coverage report
	$(UV) run pytest --cov --cov-report=term-missing

audit: ## Scan dependencies for known vulnerabilities
	$(UV) export --frozen --no-dev --no-hashes --no-emit-project --format requirements-txt > .audit-requirements.txt
	$(UV) run pip-audit --strict -r .audit-requirements.txt
	@rm -f .audit-requirements.txt

eval: ## AI evaluation set against the configured LLM (set LLM_PROVIDER, LLM_MODEL and a key)
	$(UV) run python evals/run_evals.py

web-install: ## Install the console's npm dependencies
	cd web && npm ci

web-dev: ## Run the console with hot reload on :5173 (proxies /api to :8080)
	cd web && npm run dev

web-test: ## Type-check, unit-test and build the console
	cd web && npm run typecheck && npm test && npm run build

web-types: ## Regenerate the console's API types from the backend's OpenAPI document
	$(UV) run python -c "import json; from lanereach.main import create_app; print(json.dumps(create_app().openapi(), indent=1))" > web/openapi.json
	cd web && npm run api-types
