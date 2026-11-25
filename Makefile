.PHONY: install db migrate seed ingest serve demo test test-integration lint eval eval-offline docker

PY ?= .venv/bin/python
NICE ?= nice -n 10

install:
	python3.11 -m venv .venv && $(PY) -m pip install -U pip && $(PY) -m pip install -e ".[dev,evals]"

db:            ## start Postgres with pgvector
	docker compose up -d db

migrate:
	.venv/bin/alembic upgrade head

seed:
	.venv/bin/harbor seed

ingest:
	.venv/bin/harbor ingest knowledge

serve:
	.venv/bin/harbor serve --reload

demo:          ## offline mode, no keys or database
	.venv/bin/harbor demo

test:
	$(NICE) $(PY) -m pytest tests/unit -q

test-integration:
	DATABASE_URL=postgresql+psycopg://harbor:harbor@localhost:5432/harbor $(NICE) $(PY) -m pytest tests/integration -q -m integration

lint:
	.venv/bin/ruff check harbor tests && .venv/bin/ruff format --check harbor tests

eval:
	.venv/bin/harbor eval --ragas

eval-offline:
	.venv/bin/harbor eval --offline

docker:
	docker compose up --build
