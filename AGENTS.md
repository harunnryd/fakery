# Repository Instructions

> Thin by design. Every rule below must prevent a specific mistake.

## Product

Fakery — AI twin meeting assistant for Google Meet, delivered as an
API (assistant mode + disclosed stand-in mode). No first-party UI
exists or is planned; any proposal requiring a shipped screen is
rejected outright.

## Commands

Use the justfile only — do not invent commands:

```bash
just install    # uv sync (dev + browser extras)
just dev        # uvicorn --reload on :8000
just test       # pytest
just lint       # ruff check .
just format     # ruff format .
just migrate    # alembic upgrade head
```

## Architecture Rules

1. Browser engine is behind `twin.meet.launcher.MeetBrowser`; never
   import patchright/playwright at call sites (lazy import lives only
   in the engine implementation).
2. Every external capability (STT, LLM, blob store) sits behind a
   Protocol in its own package; no provider SDK imports above that layer.
3. Join flows are versioned recipes (steps + selectors) re-derived
   against live Meet — never encoded from memory — and the
   humanization layer (locale-coherent context, mouse telemetry,
   per-keystroke typing) is part of the flow, not an optional garnish:
   live A/B showed robotic knocks are silently discarded.
4. Structural choices require a short ADR under `adr/` before code.

## Conventions

- Type hints on public functions; `async` for I/O boundaries.
- Self-documenting code: names carry the WHAT; functions short and
  single-purpose; no magic numbers — named constants instead; short
  parameter lists, related data grouped into objects. Comments and
  docstrings carry only WHY a name cannot express (non-obvious reason,
  workaround with removal condition, external spec citation) — never a
  restatement of the code, no commented-out code.
- Tests are table-driven via `pytest.mark.parametrize` with descriptive
  case IDs; one test function per behavior.
- No secrets, tokens, cookies, or meeting URLs in the repo. Logs redact
  sensitive values to length only (`core/logging.py`).
- English for all documents and identifiers. Conventional Commits.

## Design Principles

- SOLID: each module/class has one reason to change; new providers join
  by implementing a Protocol (`MeetBrowser`, `Transcriber`, `Summarizer`,
  `BlobStore`, `BotRepository`) — factories may `match` on kind, call
  sites must never branch on concrete types; depend on the Protocol,
  never the implementation.
- DRY: one authoritative source per piece of knowledge — statuses and
  transition rules in `bots/state.py`, error slugs in `core/errors.py`,
  shared time in `core/time.py`. Identical logic in two places
  consolidates immediately; only coincidental similarity waits for the
  third occurrence (Rule of Three).

## Testing

- `just test` and `just lint` must pass before any handoff.
- Contract tests run against the FastAPI app with dependency overrides;
  they must not require live Postgres/Redis.
