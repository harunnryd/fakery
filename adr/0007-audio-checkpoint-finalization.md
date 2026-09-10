# ADR 0007: Checkpointed recording and durable finalization

## Status

Accepted

## Decision

Recording capture is independent from transcription and notes. Audio is emitted as ordered, checksummed chunks to a checkpoint manifest no less often than every ten seconds. A checkpoint is durable only after its blob and manifest are stored. Finalization reconstructs the longest valid prefix, verifies decoding and hash, and records partial or degraded status without deleting a usable prefix.

## Consequences

Normal shutdown flushes browser data before the browser exits. A hard kill or node loss can lose at most the current checkpoint window when storage is healthy. Storage failures remain visible as degraded recording state and do not prevent transcript or notes status from being reported separately.
