# ADR 0005 — Webhook outbox with ordered sender

Date: 2026-09-08. Status: accepted.

## Context

Webhook delivery ran inline inside transcript ingestion. When a
receiver throttled (429 storm, observed live), every segment waited
through full retry backoffs and ingestion lagged minutes behind the
meeting. Delivery must not share fate with ingestion.

## Decision

Emit writes rows (`webhook_deliveries`: url, payload with `event_id`,
attempts, `next_try_at`); a sender loop in the worker delivers them.
Ordering per bot via a Redis mutex per `bot_id` (atomic SET NX with
TTL — no connection held across HTTP, crash-safe by expiry). Backoff
doubles per attempt, 5 attempts max, then the row is deleted with an
error log; rows older than 30 days are purged by the same loop.
At-least-once stays explicit: receivers deduplicate on `event_id`.

Considered and rejected: drop-on-fail (breaks the retry promise),
unbounded in-memory retry (dies with Job pods by design), Redis
Streams as outbox (same moving parts, uninspectable), raw-SQL
advisory locks (correct but holds transactions across network calls).

## Consequences

One migration, one sender task, no new dependencies. The inline
`WebhookDispatcher` is gone; `DbOutbox` implements `EventSink`.
