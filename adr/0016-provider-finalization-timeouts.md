# Provider finalization timeouts

## Status

Accepted

## Decision

Bound every provider call that runs after recording stops. Batch transcription receives a 90 second deadline, and each notes attempt receives a 60 second deadline. A timeout is recorded as provider failure while the checkpointed recording and live transcript continue through finalization.

## Context

The 2026-09-09 live cancel reached `recorder.stopped` with a valid 793597 byte recording, then remained in `recording` because batch transcription had no caller deadline. A provider that kept its HTTP request open could therefore prevent the run from persisting its terminal state.

## Consequences

Provider outages become visible and bounded by the finalization budget. The API can return a partial recording and live transcript with an explicit transcription or notes failure. The finalizer can retry provider work independently when a durable finalization queue is available.
