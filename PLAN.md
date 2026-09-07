# Plan — Fakery Build Map

> Module and feature map, in build order. Contracts already exist in
> `src/twin/`; this plan fills them with real implementations.
> Status legend: `[x]` done · `[~]` contract only · `[ ]` to build.
>
> Product modes (2026-09-06): **(1) meeting assistant** — listens,
> transcribes, takes notes, answers when woken; **(2) digital
> stand-in** — replaces a person at internal ceremonies (standup,
> weekly report) when they cannot attend, delivering their update and
> answering basic follow-ups on their behalf.

## Current state (2026-09-06, live-verified tonight)

- `[x]` Scaffold: FastAPI app, bot state machine + API (create/get/
  transcript), core (config/logging/errors), migrations, deployment
  (Dockerfile bot-ready + k8s manifests + compose), CI.
- `[x]` M1 loop closed live: Redis-Streams worker claims runs, CloakBrowser
  joins real meetings (knock-and-admit ≤2 s on fresh links), DOM-channel
  audio recorded as Opus/webm, hashed + stored in MinIO
  (37 KB verified), status visible via API. 80 tests green.
- `[x]` Profile store + lease: tier profiles (`signed`/`guest`) ship as
  `profiles/{tenant}/meet/{tier}/profile.tar.gz` in blob (Fernet-sealed
  when `PROFILE_ENCRYPTION_KEY` is set), exclusive Redis lease per
  profile, heartbeat + orphan sweep (stale live runs fail as `orphaned`).
- `[~]` Cancel semantics (corrected from the pre-live draft): cancel
  before join fails the run (`cancelled`); cancel mid-run completes it
  with the partial recording and marks `error_code=cancelled` — a user
  stop is not a failure, and the soak harness counts it as success.
- `[x]` M2 live transcript + webhooks (verified 2026-09-07): Deepgram
  nova-3 streaming over worker-decoded PCM16, 18 segments queryable
  mid-meeting, batch fallback quiet when live delivers;
  `bot.status_changed` + `transcript.segment` webhooks HMAC-signed
  with retry (one transient failure auto-recovered live).

## Modules to build (dependency order)

### 1. `bots/runner` — the worker (shipped, live)
The process that turns a queued bot run into an attended meeting.
- `[x]` Redis Streams consumer: claim queued runs, run lifecycle,
  release on shutdown.
- `[x]` Drive loop: engine → join → capture → transcribe → notes,
  updating bot status at each transition (state machine guards it).
- `[ ]` Recurring schedules: stored rules ("standup every Monday
  09:00") spawn bot runs without a client call — required by the
  stand-in mode (M6).
- `[ ]` Runtime isolation tiers behind one `BotRuntime` protocol:
  `process` (one browser per run in the worker pod — live) →
  **`job` (one k8s Job per run — live since 2026-09-07**, own Xvfb +
  limits per bot, TTL cleanup, supervisor watches). Kata stays a
  one-line `runtimeClassName` option for KVM hosts; no DIY-VMM track
  (decided 2026-09-07: k8s already provides every supervisor
  mechanism, cloud-independent). Density per tier is config;
  anti-affinity (never two bots in one meeting) is a scheduler rule.
- `[x]` Per-session lifecycle: one claimed run = one fresh browser in
  its own isolation unit — spawned at claim time, the leased profile
  mounted from MinIO, the unit destroyed on completion (nothing
  survives between sessions; state lives only in Postgres + blob).
  Live tier today is job-per-bot (one k8s Job per run); Kata stays a
  one-line option, no DIY-VMM track.
- `[x]` Heartbeat + orphan recovery: the runner beats
  (`bots:hb:{id}`, 90 s TTL) during a run; the worker sweeps live-state
  runs with no fresh beat older than 5 min and fails them as `orphaned`.
  Stream-level reclaim (`reclaim_stale`) covers worker death mid-claim.
- `[x]` Cancellation: `DELETE /v1/bots/{id}` → cooperative stop →
  `failed` (`cancelled`) before join, `completed` + partial recording
  with `error_code=cancelled` mid-run.

### 2. `meet/` — live Google Meet layer (shipped, live)
- `[x]` Re-derive join selectors against live Meet (prejoin fields,
  admit/knock button, leave button, "meeting ended" signal) — never
  copied from memory.
- `[x]` Wire `CloakBrowser`: two identity tiers — persistent
  signed-in profile (primary) and the humanized guest tier (no
  account); headed mode; config via `Settings.browser_profile_dir`.
- `[x]` Admission handling: instant-join links and knock-and-wait;
  timeout → fail with honest code.
- `[x]` Mid-meeting events: kicked, ended, network drop → graceful
  leave + status update.
- `[ ]` Chat channel: watch in-meeting chat for wake cues (exact-match
  replies — proven live channel) and mirror answers as chat messages
  when configured (M5).

### 3. `audio/` — capture (listen-only, shipped live)
- `[x]` Capture over the browser page: an RTC hook armed as an init
  script (before page JS runs) records Opus/webm chunks into the DOM;
  the poll loop drains them. Verified: 76 chunks / 37 KB in one run.
- `[x]` Recording: webm chunks → bytes; hash + upload
  to blob on meeting end.
- `[ ]` `AudioSink` playback arrives with the voice module (M5);
  listen-only ships first.

### 4. `transcription/` — first STT provider (shipped, live)
- `[x]` Streaming provider behind `Transcriber` (Deepgram nova-3,
  Indonesian; worker decodes Opus/webm chunks to PCM16 mono via
  ffmpeg first — sliced webm is not directly ingestible).
- `[x]` Persist `transcript_segments` as they arrive
  (`service.ingest_segment`, live-readable via API).
- `[x]` Batch fallback: post-meeting transcription from the recording
  when live streaming yields nothing.

### 5. `notes/` — LLM summarizer
- `[ ]` Implement `Summarizer`: transcript → `MeetingNotes`
  (summary, key points, action items with owner/due).
- `[ ]` Store `meeting_notes`; run after meeting end, inside the runner.

### 6. `personas/` — digital stand-in identity
Stand-in mode shares the bot pipeline; the run's mode
(`assistant` | `stand_in`) decides behavior.
- `[ ]` `persona` model: the person's identity (name, role, team),
  speaking-style notes, and per-ceremony formats (standup:
  yesterday/today/blockers; weekly: shipped/next/risks).
- `[ ]` Brief ingestion via API: the owner submits the update content
  for a specific meeting; the twin delivers it verbatim-in-spirit — it
  never invents progress. Missing required fields → ask the owner via
  API/webhook; on timeout, an honest "no update today".
- `[ ]` Disclosure rule: a stand-in announces it is an AI attending on
  behalf of the named person; display name carries that person's name,
  never a fake identity.
- `[ ]` Answer policy in stand-in mode: wake-triggered Q&A answers
  only from the submitted brief + recent meeting context; anything
  beyond it → "I'll relay that and get back to you" (relayed to the
  owner via API).

### 7. `voice/` — wake-triggered speaking (in scope)
Answer mode decided 2026-09-06: the twin answers only when woken and
never speaks autonomously — silence is the default state.
- `[ ]` Wake triggers behind one interface: in-meeting chat cue
  (exact-match channel, proven live), transcript wake-phrase, and the
  API wake endpoint.
- `[ ]` Answer loop: woken prompt + recent transcript context → LLM
  answer → TTS → `AudioSink` playback.
- `[ ]` Barge-in: `interrupt()` halts playback mid-sentence (validated
  approach: cross-process halt ~0 ms); the twin never talks over a
  speaking human.
- `[ ]` TTS provider behind a contract; Indonesian voice quality is
  the acceptance bar.
- `[ ]` Stand-in delivery mode: speak the persona's brief at the right
  meeting moment (schedule- or client-triggered), then fall back to
  wake-triggered Q&A.

### 8. `storage/` — blob implementation (shipped)
- `[x]` S3-compatible `BlobStore` (MinIO in dev, any S3 API in prod).
- `[x]` **Chrome profile store** (LMA-proven pattern): key
  `profiles/{tenant}/{platform}/profile.tar`, lifecycle
  download → launch persistent context → upload back, Fernet-sealed
  when `PROFILE_ENCRYPTION_KEY` is set (profiles are live Google
  session cookies — treat as secrets).
- `[x]` **Exclusive lease per profile (Redis, TTL)**: the same profile
  can never run on two bots at once, making the same-account
  double-join trap impossible by construction.
- `[ ]` Retention rule: raw audio 30 days, then purge (M4 job).

### 9. `webhooks/` — real delivery (M2: status + segments)
- `[x]` `webhook_subscriptions` table + API CRUD (URL per client).
- `[x]` Emit `bot.status_changed` on every transition,
  `transcript.segment` on new segments (`notes.completed` waits M3).
- `[x]` Delivery retry policy + failure logging.

### 10. `api/` — complete the surface
- `[ ]` API-key auth (`X-API-Key`, keys seeded via env/config).
- `[ ]` `GET /v1/bots` (cursor pagination), `DELETE /v1/bots/{id}`,
  `GET /v1/bots/{id}/notes`.
- `[ ]` Persona CRUD (`/v1/personas`) + brief submission
  (`/v1/personas/{id}/updates`); bot creation accepts `mode`
  (`assistant` | `stand_in`) and `persona_id`.
- `[ ]` `POST /v1/bots/{id}/wake` — client-driven wake with an optional
  prompt; queues a speech turn.
- `[ ]` `request_id` middleware; bind `bot_id` to all log lines.

### 11. `deploy/` — ship the loop
- `[x]` Dockerfile: one image for API and worker — Python 3.12 +
   Chromium (CloakBrowser) + Xvfb + fonts; a bot can run headed under
  Xvfb inside the container.
- `[x]` Kubernetes manifests (`deploy/k8s/`): namespace, Postgres,
  Redis, MinIO (all with PVCs), migrate Job, API Deployment + Service
  — deployable today on any k8s (Docker Desktop to real clusters).
- `[x]` Worker Deployment live with module 1 (same image, worker
  command under Xvfb; per-bot isolation tiers attach here);
  prod-grade ingress, HPA and multi-node scheduling later.

## The deep stack — human-likeness (M7 and beyond)

M1–M6 make the twin present and useful. The layers below make it
behave like the person — knowing what they know, answering at human
speed. This is the moat; build it only on top of a working pipeline.

### 12. `memory/` — the second brain
- `[ ]` Episodic memory: every meeting's transcript, notes, decisions,
  and commitments embedded into pgvector — "what did we decide about X
  six months ago" is one vector search away.
- `[ ]` Semantic memory: durable facts (projects, colleagues,
  preferences) with provenance back to meeting evidence, never
  silently widened.
- `[ ]` Retrieval behind a protocol; feeds wake-answers, stand-in
  briefs, and the pre-meeting briefing packet.
- `[ ]` Decay + purge policy aligned with the retention rules.

### 13. `voice/loop` — the realtime conversation engine
- `[ ]` Latency budget, stated up front: first audible syllable ≤ 1s
  after the human stops talking (natural turn gap is ~300 ms — this
  is the battle, everything is budgeted against it).
- `[ ]` Streaming cascade as the default engine: end-of-turn detection
  → parallel retrieval (memory + briefing packet) → small fast LLM
  streaming → sentence-level TTS. The slow deliberation LLM runs only
  for depth, off the critical path.
- `[ ]` Speech-to-Speech provider behind the same `VoiceEngine`
  protocol as the cascade — lower latency, weaker grounding,
  Indonesian quality unproven. Decide by measurement, not hype.
- `[ ]` Tools/MCP mid-conversation: acknowledge out loud ("bentar,
  aku cek…") while the call runs; humans do exactly this, and silence
  reads as death. Pre-brief anticipated answers before the meeting so
  most turns never need a live tool call.

### 14. Avatar — research spike before any build
- `[ ]` Spike: virtual-camera video track fed by a streaming avatar
  provider or an open-source renderer, behind a `VideoSink` protocol.
- Enter the roadmap only after the voice loop beats the latency
  budget — a beautiful avatar that answers late is worse than a
  silent tile with a great voice.

### 15. `meet/agent` — the AI DOM layer
Automates the stealth front's "recipes are data" loop: an AI agent
keeps the join and in-meeting flows alive when Google ships UI
changes. Deterministic-first, AI-fallback:
- `[ ]` Escalation ladder behind one `DOMAgent` protocol: (1) versioned
  recipe locators (deterministic, ms-latency, free) → (2) semantic
  a11y-tree matching by role/label (cheap, no vision) → (3) VLM agent
  on screenshot + DOM tree (slow, expensive, but handles pixel-only
  targets — the blue mic pill trap — and novel states like Google's
  A/B tests and unexpected dialogs).
- `[ ]` Crystallization loop: when the AI layer completes a flow, its
  trajectory is captured and proposed as the next recipe version for
  review — so AI usage decays as recipes absorb what it learned,
  instead of paying per-click forever.
- `[ ]` Mid-meeting recovery: agent handles popup/new-state events
  (permission prompts, removed-from-meeting, layout changes) that
  recipes cannot enumerate.
- Scope rule: the AI DOM layer drives flow control only. It never
  sits in the realtime voice path — conversation latency belongs to
  the voice loop (M8); the DOM agent's seconds are fine at join time
  and on rare events.
- Evidence: AWS's LMA ships an equivalent (`ai-dom-resolver.ts`,
  sources `primary`/`cache`/`ai` + dialog taxonomy) — the pattern is
  production-proven outside Google-scale targets.

## The stealth front — Meet's automation detection (continuous, from M1)

The full-browser participant is the only API-drivable join path Meet
offers (Add-ons SDK is client-side only, PSTN is audio-only, Drive
API only reads host-started recordings), so automation detection is a
permanent engineering front, not a one-time unlock. Even AWS's LMA
sample skips the headless Meet bot entirely and routes Meet through an
in-user-browser capture extension (evidence:
`research/lma-virtual-participant.md`). Validated so far:
stock headless is fingerprint-blocked; CloakBrowser headed + signed-in
profile reaches prejoin unattended; a robotic guest knock is silently
discarded while a locale-matched humanized guest knock is offered to
the host and admitted (live A/B, 2026-09-06); knock-and-admit works.
Layered posture:

- **Identity** — two tiers. Signed-in (primary): a real Google account
  profile — account reputation is an asset: age it, respect
  per-account rate limits, never double-join the same account
  (takeover trap). Guest (OSS tier): no Google account at all — viable
  with the humanization layer (live-validated) and removes the
  account-provisioning barrier for self-hosters.
- **Fingerprint** — stealth engine (CloakBrowser, source-level
  Chromium patches, LMA-proven in production),
  persistent context with realistic history, headed mode under Xvfb,
  desktop-Chrome UA. Never bare headless.
- **Behavior** — humanized pacing in the join flow is MANDATORY, not
  cosmetic: live A/B evidence (`research/anonymous-join-live-test.md`)
  shows the identical guest context gets its knock silently discarded
  when driven robotically, and offered to the host (→ admitted) when
  driven with locale match, mouse telemetry, and per-keystroke typing.
  Consistent IP identity, media/tone fixtures outliving the run.
- **Coherence** — the bot's locale story is config, not chassis: the
  pod can live anywhere, but the browser context's locale,
  timezone, geolocation, Accept-Language, AND the egress IP's
  geography must all tell the same story as the persona (live A/B:
  `en-US` robotic vs `id-ID` humanized on the same machine flipped
  the outcome). A US datacenter IP claiming Jakarta is itself a
  signal — the IP pool is selected per persona geography.
- **Structural consent** — knock-and-admit is treated as a feature:
  the human host admitting the twin is the consent gate. We never try
  to defeat human gates; only fingerprint gates need engineering.
- **Density & egress** — co-location is measured, not guessed: ~3
  concurrent talk-mode bots per 2vCPU/4GB (memory binds first;
  listen-only fits more). Hardware density is not the constraint —
  IP and fingerprint homogeneity are: same-tenant bots may share a VM
  and IP (reads as one office), cross-tenant bots need distinct
  egress IPs, and two bots never join the same meeting. Runtime
  isolation is tiered (`BotRuntime` protocol, module 1) with
  **job-per-bot as the production target** (one k8s Job per run,
  profiles shipped from object storage; Kata is a one-line
  `runtimeClassName` option, never a DIY-VMM project);
  the detection ledger arbitrates when to move up a tier.
- **Telemetry** — every join logs engine version + outcome; blocked
  joins go into a detection ledger (signals observed). A rising block
  rate is a build-stopping alarm; the fallback ladder is Tier-1
  engine → fresh accounts/IPs → re-derived recipes.
- **Recipes are data** — the join flow is versioned steps + selectors,
  re-derived by live probe; never hand-encoded from memory. The AI DOM
  layer (module 15) automates that re-derivation.

## Milestones

- **M1 — Attends & records (listen-only):** modules 1–3 + 8.
  Exit: bot joins real meetings with zero manual steps and zero
  automation blocks across the first 20 live joins, recording
  hashed and stored, status visible via API.
- **M2 — Live transcript:** modules 4 + 9 (partial). Exit: segments
  queryable during the meeting; webhook on status changes.
- **M3 — Automatic notes:** modules 5 + 9 (rest) + 10. Exit: external
  client integrates end-to-end: create bot → webhooks → transcript +
  notes, authenticated.
- **M4 — Operational hardening:** cancellation, orphan recovery,
  retention job, pagination edge cases, load check (K density per box).
- **M5 — Speaks when woken (decided in scope):** module 7 (voice) +
  meet chat channel + sink playback. Exit: the twin answers a woken
  question audibly in the meeting, stays fully silent otherwise, and
  barge-in cuts playback mid-sentence.
- **M6 — Digital stand-in:** module 6 (personas) + recurring
  schedules + voice delivery mode. Exit: the twin attends a standup on
  behalf of an absent owner, delivers the submitted update in their
  format, answers basic follow-ups when woken, and disclosed itself as
  an AI at the start.
- **M7 — Remembers everything:** module 12 (memory). Exit: asked about
  a decision from months ago, the twin answers from retrieved meeting
  evidence and can cite the meeting it came from.
- **M8 — Realtime conversation:** module 13 (voice loop). Exit: p50
  first-syllable latency ≤ 1s in live meetings; a tool-backed question
  keeps the human engaged (acknowledgment inside 1s, real answer
  delivered after).
- **M9 — Avatar:** spike first (module 14); build only if M8 holds and
  the avatar adds perceived presence without breaking the budget.
- **M10 — Self-healing join:** module 15 (AI DOM layer). Exit: a
  deliberately varied Meet UI is still joined without a human
  rewriting selectors, and the agent's trajectory lands as a reviewed
  recipe version.

## Decisions (owner, 2026-09-06)

1. Speaking model: the twin answers only when woken — via in-meeting
   chat cue, transcript wake-phrase, or the API wake endpoint. Never
   autonomous; speaking work is core scope (module 7), not optional.
2. STT: quality streaming tier. Multilingual quality is the acceptance
   bar (Indonesian accents, code-switching); the cheap tier is a
   measured downgrade decision, never the starting point.
3. Worker topology: separate process/container from the API, same
   image; Redis Streams is the only boundary between them. The API
   never spawns browser processes.
4. Product scope: the twin is both a meeting assistant and a digital
   stand-in that replaces a person at internal ceremonies (standup,
   weekly report) when they cannot attend. The stand-in delivers
   owner-provided content — never invents progress — and always
   discloses that it is an AI standing in.
5. Join identity tiers: signed-in profiles are the primary path; a
   humanized guest tier ships for OSS adoption (self-hosters provision
   no Google account). The locale story is persona config, coherent
   across locale, timezone, geolocation, Accept-Language, and egress
   IP geography.
6. Runtime isolation: job-per-bot is the production target
   (profiles shipped from object storage, exclusive lease per profile);
   process-per-bot for dev. No DIY-VMM track: k8s provides every
   supervisor mechanism, cloud-independent.
