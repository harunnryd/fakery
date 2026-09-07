# Fakery

**An AI Twin Meeting Assistant for Google Meet.** Open-source,
API-only, self-hostable: you run the server, clients order a twin over
the API, and it shows up in the meeting — as an assistant that
listens, transcribes, and takes notes, or as a disclosed stand-in
that delivers your update when you cannot attend.

## The honest fakery

The name is the product. Fakery fakes your presence — out loud, on
purpose: the twin carries your name, announces that it is an AI
attending on your behalf, then does the job properly. It joins on
time, listens, takes notes, delivers your standup update, answers
when woken — and never invents your progress.

## Two modes

| Mode | What the twin does |
|---|---|
| `assistant` | joins, listens, returns transcript and notes; answers only when woken |
| `stand_in` | replaces you at standup / weekly-report-style ceremonies: delivers your submitted brief, takes basic follow-ups, relays the rest to you |

## What it looks like

Assistant flow (works today):

```bash
curl -X POST localhost:8000/v1/bots \
  -H "Content-Type: application/json" \
  -d '{"meeting_url": "https://meet.google.com/abc-defg-hij"}'
# 202 Accepted → {"id": "bot_…", "status": "queued", …}

curl localhost:8000/v1/bots/<bot_id>/transcript   # segments as they land
curl localhost:8000/v1/bots/<bot_id>/notes        # planned (M3)
```

Stand-in flow (target shape — see [`PLAN.md`](PLAN.md), M6):

```bash
curl -X POST localhost:8000/v1/personas \
  -d '{"name": "Harun", "role": "Backend",
       "formats": {"standup": ["yesterday", "today", "blockers"]}}'

curl -X POST localhost:8000/v1/personas/<id>/updates \
  -d '{"standup": {"yesterday": "…", "today": "…", "blockers": "…"}}'

curl -X POST localhost:8000/v1/bots \
  -d '{"meeting_url": "…", "mode": "stand_in", "persona_id": "<id>"}'
```

## Stack

- Python 3.12, FastAPI (async), SQLAlchemy 2 + asyncpg, Alembic
- structlog for logging (JSON in prod, pretty in dev)
- CloakBrowser (stealth Chromium) for Meet join, behind an engine interface
- Postgres + Redis + S3-compatible blob store (see `deploy/compose/`)

## Structure

```
src/twin/
  main.py            FastAPI app factory, error handlers, lifespan
  core/              config, structlog setup, RFC 9457 errors
  api/               HTTP layer: routes, request/response schemas, deps
  bots/              bot lifecycle: state machine, ORM models, service
  meet/              browser engine interface (Chromium ships in the image)
  storage/           database engine/session
  webhooks/          HMAC webhook signing (delivery lands in M2)
tests/
  unit/              pure logic, table-driven
  contract/          API behavior against the FastAPI app
db/migrations/       Alembic migrations (async)
deploy/k8s/          Kubernetes manifests (kind works today)
deploy/compose/      local Postgres + Redis + MinIO
```

## Quickstart

```bash
just install          # uv sync (includes browser + dev extras)
just compose-up       # start Postgres, Redis, MinIO
cp .env.example .env  # fill in what you need
just migrate          # alembic upgrade head
just dev              # uvicorn on :8000

curl localhost:8000/healthz
```

Run the checks:

```bash
just test
just lint
just format
```

## API (v1)

| Method | Path | Behavior |
|---|---|---|
| GET | `/healthz` | liveness |
| POST | `/v1/bots` | queue a bot for a meeting URL → 202 with resource |
| GET | `/v1/bots/{id}` | bot status (`queued → joining → joined → recording → processing → completed/failed`) |
| GET | `/v1/bots/{id}/transcript` | ordered transcript segments |
| DELETE | `/v1/bots/{id}` *(planned)* | cancel a queued or live bot |
| GET | `/v1/bots/{id}/notes` *(planned)* | summary, key points, action items |
| POST | `/v1/bots/{id}/wake` *(planned)* | wake the twin with an optional prompt |
| POST/GET | `/v1/personas` *(planned)* | stand-in identity + briefs |

Errors use RFC 9457 problem details with stable slugs
(`not-found`, `validation-failed`, `conflict`, ...).

## OSS characteristics

- Apache-2.0. API-only: no first-party UI — bring your own client or
  generate one from the OpenAPI spec; integrators own every screen.
- Self-hostable: one compose file brings up Postgres, Redis, MinIO;
  the worker runs as its own container.
- Guest tier: bots join meetings without any Google account — the
  humanized join flow passes Meet's bot check — so self-hosters
  provision nothing. Signed-in profiles remain the premium path.
- Integration surface: OpenAPI + HMAC-signed webhooks
  (`X-Webhook-Signature: sha256=…`).

## Status

Scaffold ready for development. The build map — modules and features
in dependency order, with milestones M1–M6 — lives in
[`PLAN.md`](PLAN.md). Wired: bot lifecycle state machine + API.
Contracts defined but not yet implemented: Meet join flow (selectors
need live re-derivation), STT providers, LLM summarizer, TTS, blob
store, webhook delivery, bot worker.
