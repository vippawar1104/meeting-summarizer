.PHONY: up down test lint seed e2e-mock e2e dashboard-dev dashboard-test dashboard-build eval eval-baselines eval-report eval-validate eval-gate
up:
	docker compose up --build -d
down:
	docker compose down -v
lint:
	uv run ruff check . && uv run ruff format --check . && uv run mypy
test:
	uv run pytest -q

MODEL ?= gemini-2.5-flash
PROMPT ?= v1
eval-validate:
	uv run python -m eval.validate
eval-baselines:
	uv run python -m eval.run --model baseline:null --model baseline:regex --mode replay
eval:
	uv run python -m eval.run --model $(MODEL) --prompt $(PROMPT)
eval-report:
	uv run python -m eval.report --update-readme
eval-gate:
	uv run python -m eval.gate --model baseline:regex --prompt v1

seed:  # local demo data for the dashboard (dev only)
	REVIEWLY_ENV=dev uv run python -m scripts.seed_demo
dashboard-dev:
	cd dashboard && npm run dev
dashboard-test:
	cd dashboard && npm run typecheck && npm test
dashboard-build:
	cd dashboard && npm ci && npm run build

# Whole system locally with a fake GitHub and your real LLM key (see docker-compose.e2e.yml):
#   make e2e-mock   (terminal 1)   then   make e2e   (terminal 2, after the stack is up with the e2e override)
e2e-mock:
	uv run python -m scripts.mock_github
e2e:
	uv run python -m scripts.e2e_local
