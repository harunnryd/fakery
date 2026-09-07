import asyncio
import random
from typing import Any

import structlog

from twin.meet.humanize import first_visible, human_click, human_type, wander

logger = structlog.get_logger(__name__)

GOTO_TIMEOUT_S = 60
PREJOIN_TIMEOUT_S = 25
LANDING_DWELL_S = (4.0, 7.0)
TOOLTIP_SETTLE_S = (0.5, 1.2)
KNOCK_SETTLE_S = (1.5, 3.5)
ADMISSION_TIMEOUT_S = 120
ADMISSION_POLL_S = 2
END_POLL_S = 5

NAME_SELECTORS = 'input[aria-label*="nama" i], input[aria-label*="name" i]'
END_CANCELLED = "cancelled"
DEFAULT_LANG = "en"
WARMUP_URLS = ("https://www.google.com/", "https://meet.google.com/")
WARMUP_DWELL_S = (1.5, 3.0)

VOCABULARY = {
    "id": {
        "join": ("Minta bergabung", "Gabung sekarang"),
        "tooltip": ("Mengerti",),
        "dismiss": ("Jangan sekarang",),
        "gate": ("tidak dapat bergabung", "tidak bisa bergabung"),
        "leave": ("tutup panggilan", "tinggalkan", "keluar", "akhiri"),
        "end": ("telah berakhir", "menakhiri", "kamu dikeluarkan"),
    },
    "en": {
        "join": ("Ask to join", "Join now"),
        "tooltip": ("Got it",),
        "dismiss": ("Not now",),
        "gate": ("can't join",),
        "leave": ("leave", "end call"),
        "end": ("meeting ended", "you were removed", "call ended"),
    },
}


class JoinError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


async def join_meeting(
    page: Any,
    meeting_url: str,
    display_name: str,
    guest: bool = True,
    warmup: bool = False,
    pre_knock: Any | None = None,
) -> None:
    if warmup:
        await _warmup_navigation(page)
    await _open_prejoin(page, meeting_url, guest)
    if guest:
        await _fill_name(page, display_name)
    else:
        await _settle_signed_in(page)
    if pre_knock is not None:
        await pre_knock(page)
    await _knock(page)


async def wait_admitted(page: Any, timeout_s: int = ADMISSION_TIMEOUT_S) -> str:
    deadline = asyncio.get_event_loop().time() + timeout_s
    probed = False
    while asyncio.get_event_loop().time() < deadline:
        if await _leave_button(page) is not None:
            return "admitted"
        gate = await _gate_visible(page)
        if gate:
            raise JoinError("join-gated", f"visible gate text: {gate}")
        if not probed and asyncio.get_event_loop().time() > deadline - timeout_s + 10:
            probed = True
            await _log_button_labels(page)
        await asyncio.sleep(ADMISSION_POLL_S)
    raise JoinError("join-timeout", f"not admitted within {timeout_s}s")


async def wait_meeting_ended(
    page: Any,
    max_seconds: int,
    should_stop: Any | None = None,
) -> str:
    deadline = asyncio.get_event_loop().time() + max_seconds
    leave_misses = 0
    while asyncio.get_event_loop().time() < deadline:
        if should_stop is not None and await should_stop():
            return END_CANCELLED
        vocabulary = await _vocabulary(page)
        for end in vocabulary["end"]:
            if await _text_visible(page, end):
                return "meeting-ended"
        if await _leave_button(page) is None:
            leave_misses += 1
            if leave_misses == 1:
                await _log_button_labels(page)
            if leave_misses >= 2:
                return "meeting-ended"
        else:
            leave_misses = 0
        await asyncio.sleep(END_POLL_S)
    raise JoinError("meeting-timeout", f"meeting still running after {max_seconds}s")


async def leave_meeting(page: Any) -> None:
    leave = await _leave_button(page)
    if leave is None:
        logger.warning("leave.button-not-found")
        return
    await human_click(page, leave)


async def log_participant_signals(page: Any) -> None:
    try:
        signals = await page.eval_on_selector_all(
            PARTICIPANT_PROBE_SELECTOR,
            "els => els.map(e => (e.getAttribute('aria-label') || e.textContent || '')"
            ".trim().slice(0, 60)).filter(Boolean)",
        )
        lang = await page.evaluate("() => document.documentElement.lang || ''")
        logger.info("probe.participants", lang=lang, signals=signals)
    except Exception as err:
        logger.warning("probe.participants_failed", error=str(err))


PARTICIPANT_PROBE_SELECTOR = (
    '[aria-label*="peserta" i], [aria-label*="participant" i], '
    '[aria-label*="orang" i], [data-participant-token]'
)


async def _warmup_navigation(page: Any) -> None:
    for url in WARMUP_URLS:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_S * 1000)
            await asyncio.sleep(random.uniform(*WARMUP_DWELL_S))
        except Exception:
            continue


async def _open_prejoin(page: Any, meeting_url: str, guest: bool) -> None:
    await page.goto(meeting_url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_S * 1000)
    await asyncio.sleep(random.uniform(*LANDING_DWELL_S))
    await _dismiss_overlays(page)
    await wander(page)

    if guest:
        name_input = page.locator(NAME_SELECTORS).first
        try:
            await name_input.wait_for(state="visible", timeout=PREJOIN_TIMEOUT_S * 1000)
        except Exception as err:
            gate = await _gate_visible(page)
            if gate:
                raise JoinError("join-gated", f"gate before prejoin: {gate}") from err
            raise JoinError("join-prejoin-missing", "prejoin name field never appeared") from err


async def _fill_name(page: Any, display_name: str) -> None:
    name_input = page.locator(NAME_SELECTORS).first
    await human_type(page, name_input, display_name)
    await asyncio.sleep(random.uniform(*KNOCK_SETTLE_S))
    await wander(page, rounds=random.randint(1, 2))


async def _settle_signed_in(page: Any) -> None:
    await asyncio.sleep(random.uniform(*KNOCK_SETTLE_S))
    await _dismiss_overlays(page)
    await wander(page, rounds=random.randint(1, 2))


async def _knock(page: Any) -> None:
    vocabulary = await _vocabulary(page)
    candidates = [page.get_by_role("button", name=label).first for label in vocabulary["join"]]
    knock = await first_visible(page, candidates)
    if knock is None:
        raise JoinError("join-no-knock-button", "no knock/join button rendered")
    await human_click(page, knock)
    logger.info("join.knocked")


async def _leave_button(page: Any) -> Any | None:
    vocabulary = await _vocabulary(page)
    hints = [f'[aria-label*="{hint}" i]' for hint in vocabulary["leave"]]
    return await first_visible(page, [page.locator(hint).first for hint in hints])


async def _text_visible(page: Any, text: str) -> bool:
    locator = page.get_by_text(text).first
    try:
        return await locator.is_visible()
    except Exception:
        return False


async def _gate_visible(page: Any) -> str:
    vocabulary = await _vocabulary(page)
    for gate in vocabulary["gate"]:
        if await _text_visible(page, gate):
            return gate
    return ""


async def _log_button_labels(page: Any) -> None:
    try:
        labels = await page.eval_on_selector_all(
            '[role="button"], button',
            "els => els.map(e => (e.getAttribute('aria-label') || e.textContent || '').trim())"
            ".filter(Boolean).slice(0, 40)",
        )
        logger.info("probe.buttons", labels=labels)
    except Exception:
        pass


async def _dismiss_overlays(page: Any) -> None:
    vocabulary = await _vocabulary(page)
    labels = (*vocabulary["tooltip"], *vocabulary["dismiss"])
    candidates = [page.get_by_role("button", name=label).first for label in labels]
    overlay = await first_visible(page, candidates)
    if overlay is None:
        return
    await human_click(page, overlay)
    await asyncio.sleep(random.uniform(*TOOLTIP_SETTLE_S))


async def _vocabulary(page: Any) -> dict:
    lang = ""
    try:
        lang = str(await page.evaluate("() => document.documentElement.lang || ''"))
    except Exception:
        pass
    key = lang.split("-")[0].lower()
    return VOCABULARY.get(key, VOCABULARY[DEFAULT_LANG])
