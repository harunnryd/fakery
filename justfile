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

docker-build-release tag="fakery:release":
    docker build --pull -t {{tag}} .

docker-image-digest tag="fakery:release":
    docker image inspect {{tag}} | uv run python -c 'import json, sys; print(json.load(sys.stdin)[0]["Id"])'

kind-load tag="fakery:release":
    kind load docker-image {{tag}} --name fakery
    uv run python scripts/register_kind_digest.py '{{tag}}'

kind-images:
    docker exec fakery-control-plane ctr -n k8s.io images list

kind-image-alias source target:
    docker exec fakery-control-plane ctr -n k8s.io images tag {{source}} {{target}}

kind-create image="kindest/node:v1.35.0":
    kind create cluster --name fakery --image {{image}}

integration-test:
    uv run --frozen pytest tests/integration

live-acceptance:
    uv run --frozen pytest tests/live

k8s-apply mode="local":
    just k8s-apply-{{mode}}

[private]
k8s-apply-local:
    kubectl -n fakery delete job twin-migrate --ignore-not-found
    kubectl apply -k deploy/k8s/
    just k8s-secrets local
    just k8s-restart

[private]
k8s-apply-production:
    just k8s-secrets production
    kubectl -n fakery delete job twin-migrate --ignore-not-found
    kubectl apply -k deploy/k8s/production

k8s-secrets mode="local":
    uv run python scripts/sync_k8s_secrets.py --mode {{mode}}

k8s-sync-secrets:
    just k8s-secrets local

k8s-migrate:
    kubectl -n fakery delete job twin-migrate --ignore-not-found
    kubectl apply -f deploy/k8s/base/migrate-job.yaml
    kubectl -n fakery wait --for=condition=complete job/twin-migrate --timeout=120s

migrate:
    uv run alembic upgrade head

k8s-status:
    kubectl -n fakery get pods -o wide

k8s-rollout:
    kubectl -n fakery rollout status deployment/api --timeout=120s
    kubectl -n fakery rollout status deployment/worker --timeout=120s
    kubectl -n fakery get pods -o 'custom-columns=NAME:.metadata.name,IMAGE:.status.containerStatuses[*].imageID'

k8s-restart:
    kubectl -n fakery rollout restart deployment/api deployment/worker deployment/sender deployment/finalizer

k8s-logs target:
    kubectl -n fakery logs {{target}} --tail=100

k8s-api-forward:
    kubectl -n fakery port-forward service/api 8000:8000

k8s-postgres-forward:
    kubectl -n fakery port-forward service/postgres 5432:5432

live-api action value="":
    uv run python scripts/live_api.py {{action}} '{{value}}'

live-speech:
    say -v Daniel -r 155 'This is an AI generated test voice for SigmaWave AI. For the pilot, we will keep PostgreSQL as the source of truth. Audio checkpoints must be durable every ten seconds. The engineering team will test cancellation before approving the release. No production rollout is approved today.'

recording-probe path image="fakery:stt-guest-fix":
    docker run --rm -i --entrypoint ffmpeg {{image}} -hide_banner -i pipe:0 -af volumedetect -f null - < '{{path}}'
