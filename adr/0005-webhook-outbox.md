# ADR 0005 — Webhook outbox with ordered sender

Date: 2026-09-08. Status: superseded by ADR 0006.

## Context

Webhook delivery ran inline inside transcript ingestion. When a
receiver throttled (429 storm, observed live), every segment waited
through full retry backoffs and ingestion lagged minutes behind the
meeting. Delivery must not share fate with ingestion.

## Decision

Emit writes rows (`webhook_deliveries`: url, payload with `event_id`,
attempts, `next_try_at`); a sender loop in the worker delivers them.
Ordering per bot and subscription now uses a Redis lease plus database
sequence and delivery status. Backoff doubles with ten attempts maximum;
exhausted rows remain as dead evidence and later events block until replay or
skip. Rows older than 30 days are purged by the dedicated sender.
At-least-once stays explicit: receivers deduplicate on `event_id`.

Considered and rejected: drop-on-fail (breaks the retry promise),
unbounded in-memory retry (dies with Job pods by design), Redis
Streams as outbox (same moving parts, uninspectable), raw-SQL
advisory locks (correct but holds transactions across network calls).

## Consequences

The sender is a separate Deployment. `DbOutbox` implements `EventSink` and
shares the state transaction when bound to its session.
