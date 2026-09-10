# CPU isolation and probe budget

## Decision

Pilot bot Jobs receive a two CPU limit, while the worker receives a two CPU limit and the API receives one CPU. The API, sender, and finalizer have explicit memory and CPU requests. API liveness and readiness probes allow five seconds for a response and six consecutive failures before replacing the process.

## Evidence

On 2026-09-09 a single headed Chromium Job saturated the kind node. The API and Postgres probes timed out, the API restarted, and the local port-forward failed while the bot remained in `joining`. The bot later failed with `join-prejoin-missing` after the control plane had recovered. The failure was caused by node CPU starvation, so only increasing probe timeouts would hide the failure.

## Consequences

The control plane retains scheduling headroom during browser capture. A bot that needs more than two CPUs is throttled and can fail its own acceptance target instead of taking down API, database, or reconciliation loops. Production sizing must provide at least three bot CPU limits plus control-plane headroom before the three-meeting target is attempted.
