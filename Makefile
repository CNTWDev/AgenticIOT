.PHONY: install api admin db-up migrate check format contracts test-integration

install:
	uv sync --frozen
	npm ci

api:
	uv run uvicorn agenticiot.main:app --host 127.0.0.1 --port 8000 --reload

admin:
	npm run dev

db-up:
	docker compose up -d postgres

migrate:
	uv run alembic upgrade head

check:
	bash -n scripts/deploy.sh
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest -m 'not integration'
	uv run python scripts/contracts.py --check
	npm run lint
	npm run format:check
	npm run build

format:
	uv run ruff check --fix .
	uv run ruff format .
	npm run format

contracts:
	uv run python scripts/contracts.py

test-integration:
	uv run pytest -m integration
