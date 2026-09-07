# ADR 0002 — BotRuntime isolation ladder

Date: 2026-09-07. Status: accepted.

## Context

One claimed run already equals one fresh browser, but the browser
lives inside the long-lived worker pod (process-per-bot). PLAN targets
microVM-per-bot in production, with Job-per-bot as the k8s-native
middle tier and Kata (`runtimeClassName`) as the microVM mechanism —
all without application-code changes per tier.

## Decision

Introduce a `BotRuntime` protocol in `twin.bots.runtime` with a single
method, `spawn`, covering the attended run (browser open through
leave and completion). Supervisor concerns — profile lease, profile
ship, heartbeat, cancel watch, orphan sweep — stay in the worker and
`execute_run`, because they outlive any single runtime.

Tiers, in order: `process` (current behavior, extracted unchanged),
`job` (worker creates one k8s Job per run; needs RBAC + manifests),
`kata` (the same Job with a Kata `runtimeClassName` on KVM nodes).
Selected via `Settings.bot_runtime`. kind/Docker Desktop cannot
validate `kata` (no nested KVM); it waits for a KVM host.

Dropped 2026-09-07: no DIY-VMM track. Raw Firecracker / Cloud
Hypervisor / QEMU supervisors, VMM pluggability, and any
cloud-provider APIs are out of scope — k8s (Job controller, CNI,
RBAC, RuntimeClass) already provides every supervisor mechanism, on
any KVM host, with no cloud dependency. Kata stays as a one-line
option, not a project.

## Consequences

`execute_run` keeps orchestration; runtimes own attendance. Adding a
tier means implementing the protocol plus its manifests — no call-site
branches. Refactors of the hot path re-verify with one live join.
