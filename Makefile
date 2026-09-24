.PHONY: up down test lint
up:
	docker compose up --build -d
down:
	docker compose down -v
lint:
	uv run ruff check . && uv run ruff format --check . && uv run mypy
test:
	uv run pytest -q
