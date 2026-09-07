# ADR 0001 — CloakBrowser as the default Meet engine

Date: 2026-09-06. Status: accepted.

## Context

The guest tier joined and was admitted once on Patchright (Tier-2),
then the same meeting code gated the second knock with
"tidak dapat bergabung" while the host watched. Reaching prejoin plus
knock in both rounds points at per-code velocity gating rather than a
fingerprint block, but the stealth front already reserves a Tier-1
commercial engine behind the launcher interface as the fallback, and
AWS's LMA runs CloakBrowser as its meeting-browser in production.

## Decision

Add CloakBrowser 0.5.10 (pinned) as the `MeetBrowser` implementation in
`twin.meet.launcher`, replacing Patchright outright. No engine switch
survives: one engine means no dead fallback branch and no dual-browser
image. Rollback, if ever needed, is a revert to the pre-ADR tree.

Adopted LMA-proven launch facts: persistent context per tier profile
dir, deterministic fingerprint seed per profile, Xvfb-matched window
and fingerprint screen flags, fake media args, GPU off, and both
WebRTC host-candidate flags (Cloak's IP-leak patch otherwise kills
in-page media). The license key travels only via the
`CLOAKBROWSER_LICENSE_KEY` env var, never the repo.

## Consequences

The image bakes the ~200 MB Cloak binary at build time
(`python -m cloakbrowser install`, auto-update off). Free tier allows
one concurrent session, which matches one bot per worker today; scale
needs a paid key. Meet admit rate stays the acceptance metric —
generic detection scores do not transfer.
