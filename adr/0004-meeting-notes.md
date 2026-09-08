# ADR 0004 — Meeting notes on OpenAI mini

Date: 2026-09-07. Status: accepted.

## Context

M3 needs summary, key points, and action items with owner and due
date after every meeting, plus authenticated API access. The job is
extractive over our own transcript (speaker numbers included), not
open-ended generation — model quality matters less than prompt
discipline and output validation.

## Decision

OpenAI `gpt-4o-mini` behind a `Summarizer` protocol in `twin/notes/`,
called through LangChain (`ChatOpenAI` with structured output onto a
Pydantic schema, converted to our dataclasses at the boundary) —
provider and model switchable via env without code changes.
Placement: inside `attend()`, after transcription and annotation,
before completion — so `notes.completed` emits on the same event path
as everything else.

Failure policy: 3 inline attempts, then the run still completes with
transcript and recording intact; the miss is an error log, not a
failed run — notes are best-effort, attendance is the promise. Auth
is one global `X-API-Key` compared in constant time; per-client keys
wait for the second client.

## Consequences

One `LLM_API_KEY` secret, one `openai` dependency. Owner attribution
is only as good as the speaker labels feeding it — NULL speakers
yield ownerless action items, honestly.
