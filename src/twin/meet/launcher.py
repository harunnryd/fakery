"""Browser engine abstraction + the validated join knowledge.

Call sites depend on MeetBrowser, never on a concrete browser library
— the engine is swappable (stealth browsers get licensed and delisted;
the join approach must survive that).

Join knowledge validated live (2026-09-06, ledger in research/): stock
headless is fingerprint-blocked; a headed stealth engine with a
persistent signed-in profile reaches prejoin unattended; the guest
tier (no Google account) joins too — but only with the humanization
layer (locale-coherent context, mouse telemetry, per-keystroke
typing). A robotic knock is silently discarded ("System info will be
sent to confirm you're not a bot"); a human-paced knock is offered to
the host and admitted. Selectors stay out of code until wiring —
recipes are versioned data re-derived by live probe, never hand-encoded.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class MeetBrowser(Protocol):
    async def open(self, meeting_url: str) -> Any:
        """Return a live page sitting in the meeting (post-admission)."""
        ...


@dataclass(slots=True)
class EngineConfig:
    profile_dir: Path
    headed: bool = True


class PatchrightBrowser:
    """Tier-2 stealth engine: Patchright with a persistent signed-in profile."""

    def __init__(self, config: EngineConfig) -> None:
        self._config = config

    async def open(self, meeting_url: str) -> Any:
        # Lazy import: patchright is an optional extra and its browser
        # binaries are installed out-of-band (uv sync --extra browser).
        from patchright.async_api import async_playwright

        self._config.profile_dir.expanduser().mkdir(parents=True, exist_ok=True)
        playwright = await async_playwright().start()
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._config.profile_dir),
            headless=not self._config.headed,
            no_viewport=True,
        )
        page = await context.new_page()
        return page
