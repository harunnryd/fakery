from enum import StrEnum

from twin.core.errors import make_error


class BotStatus(StrEnum):
    QUEUED = "queued"
    JOINING = "joining"
    JOINED = "joined"
    RECORDING = "recording"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


_ALLOWED: dict[BotStatus, frozenset[BotStatus]] = {
    BotStatus.QUEUED: frozenset({BotStatus.JOINING, BotStatus.FAILED}),
    BotStatus.JOINING: frozenset({BotStatus.JOINED, BotStatus.FAILED}),
    BotStatus.JOINED: frozenset({BotStatus.RECORDING, BotStatus.FAILED}),
    BotStatus.RECORDING: frozenset({BotStatus.PROCESSING, BotStatus.FAILED}),
    BotStatus.PROCESSING: frozenset({BotStatus.COMPLETED, BotStatus.FAILED}),
    BotStatus.COMPLETED: frozenset(),
    BotStatus.FAILED: frozenset(),
}


def can_transition(current: BotStatus, target: BotStatus) -> bool:
    return target in _ALLOWED[current]


def require_transition(current: BotStatus, target: BotStatus) -> BotStatus:
    if not can_transition(current, target):
        raise make_error(
            "conflict",
            detail=f"illegal bot transition {current.value} -> {target.value}",
        )
    return target
