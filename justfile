# justfile — the only sanctioned command surface.

default:
    @just --list

install:
    uv sync --extra dev --extra browser

dev:
    uv run uvicorn twin.main:app --reload --port 8000

test:
    uv run --frozen pytest

lint:
    uv run --frozen ruff check .

format:
    uv run --frozen ruff format .

compose-up:
    docker compose -f deploy/compose/docker-compose.yml up -d

compose-down:
    docker compose -f deploy/compose/docker-compose.yml down

docker-build:
    docker build -t fakery:dev .

k8s-apply:
    kubectl apply -k deploy/k8s/

k8s-migrate:
    kubectl -n fakery delete job twin-migrate --ignore-not-found
    kubectl apply -f deploy/k8s/migrate-job.yaml
    kubectl -n fakery wait --for=condition=complete job/twin-migrate --timeout=120s

migrate:
    uv run alembic upgrade head
