.PHONY: up down test lint seed dashboard-dev dashboard-test dashboard-build eval eval-baselines eval-report eval-validate eval-gate
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
