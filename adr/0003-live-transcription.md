# ADR 0003 — Live transcription on Deepgram + webhook emit

Date: 2026-09-07. Status: accepted.

## Context

M2 needs segments queryable during the meeting plus webhooks for
status changes. Two facts force the shape: MediaRecorder timeslice
blobs are webm fragments most STT APIs cannot ingest, so the worker
must decode to PCM16 mono first; and every bot-status transition
already funnels through `BotService.advance`, which is the single
honest place to emit `bot.status_changed`.

## Decision

Deepgram nova-3 over websocket as the streaming provider
(`STT_API_KEY`, one token, no GCP project): interim results drive
live reads, final results persist. Audio path per run: drained
Opus/webm chunks → ffmpeg subprocess (PCM16 mono) → socket →
segments. If the live socket fails, the finished MinIO recording is
transcribed in batch post-meeting (late but complete).

`Transcriber` protocol lives in `twin/transcription/`; the Deepgram
SDK stays below it. `BotService` gains `ingest_segment` (persist +
emit `transcript.segment`) and an optional `EventSink` (default off,
so existing call sites and tests do not change) that signs
(HMAC, existing `sign()`) and POSTs with 3-attempt backoff, logging
failures. `notes.completed` stays M3.

## Consequences

The image gains ffmpeg. Subscription rows live in Postgres
(`webhook_subscriptions` + API CRUD). Delivery is at-least-once per
attempt, best-effort overall — a durable outbox is deferred until a
miss is observed, not built on speculation.
