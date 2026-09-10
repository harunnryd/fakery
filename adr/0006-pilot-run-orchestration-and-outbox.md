# ADR 0006: Pilot run orchestration and durable webhook delivery

## Status

Accepted

## Decision

The worker claims runs and supervises bounded tasks independently from webhook delivery. A bot run reserves capacity before a Kubernetes Job is created. Run state and its outbox intent are written in the same PostgreSQL transaction. Each delivery keeps a stable event identity, per-bot sequence, lease, attempt history, and terminal dead state. Delivery is at-least-once; receivers deduplicate by event identity.

## Consequences

Worker restart can reconcile existing Jobs without creating a duplicate participant. Redis outages after a committed create are recovered by a database relay. A failed receiver no longer erases evidence or allows later events to overtake it. The sender can replay or skip a dead event explicitly.
