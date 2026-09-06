import pytest

from twin.bots.state import BotStatus, can_transition, require_transition
from twin.core.errors import TwinError


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        (BotStatus.QUEUED, BotStatus.JOINING, True),
        (BotStatus.QUEUED, BotStatus.FAILED, True),
        (BotStatus.JOINING, BotStatus.JOINED, True),
        (BotStatus.JOINING, BotStatus.FAILED, True),
        (BotStatus.JOINED, BotStatus.RECORDING, True),
        (BotStatus.RECORDING, BotStatus.PROCESSING, True),
        (BotStatus.PROCESSING, BotStatus.COMPLETED, True),
        (BotStatus.PROCESSING, BotStatus.FAILED, True),
        (BotStatus.QUEUED, BotStatus.RECORDING, False),
        (BotStatus.QUEUED, BotStatus.COMPLETED, False),
        (BotStatus.JOINED, BotStatus.JOINING, False),
        (BotStatus.COMPLETED, BotStatus.FAILED, False),
        (BotStatus.FAILED, BotStatus.QUEUED, False),
        (BotStatus.COMPLETED, BotStatus.PROCESSING, False),
    ],
    ids=[
        "queued_to_joining",
        "queued_to_failed",
        "joining_to_joined",
        "joining_to_failed",
        "joined_to_recording",
        "recording_to_processing",
        "processing_to_completed",
        "any_active_to_failed",
        "queued_cannot_skip_to_recording",
        "queued_cannot_skip_to_completed",
        "joined_cannot_go_back",
        "terminal_completed_is_frozen",
        "terminal_failed_is_frozen",
        "completed_cannot_reenter_pipeline",
    ],
)
def test_can_transition_enforces_pipeline_order(
    current: BotStatus, target: BotStatus, expected: bool
) -> None:
    assert can_transition(current, target) is expected


def test_require_transition_rejects_illegal_move_as_conflict() -> None:
    with pytest.raises(TwinError) as exc_info:
        require_transition(BotStatus.QUEUED, BotStatus.COMPLETED)
    assert exc_info.value.slug == "conflict"
    assert "queued -> completed" in exc_info.value.detail
