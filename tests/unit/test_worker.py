import pytest

from twin.worker import GuestPacer


@pytest.mark.parametrize(
    ("now", "expected_wait", "expected_slot"),
    [(100.0, 240.0, 340.0), (500.0, 0.0, 500.0)],
    ids=["second-slot", "after-gap"],
)
def test_guest_pacer_reserves_slots(now: float, expected_wait: float, expected_slot: float) -> None:
    pacer = GuestPacer(240)
    first_wait, first_slot = pacer.reserve(100.0)
    assert first_wait == 0.0
    assert first_slot == 100.0
    wait, slot = pacer.reserve(now)
    assert wait == expected_wait
    assert slot == expected_slot


def test_guest_pacer_releases_cancelled_latest_slot() -> None:
    pacer = GuestPacer(240)
    _, slot = pacer.reserve(100.0)
    pacer.release(slot)
    wait, next_slot = pacer.reserve(100.0)
    assert wait == 0.0
    assert next_slot == 100.0


def test_guest_pacer_keeps_later_reservation_when_older_slot_is_released() -> None:
    pacer = GuestPacer(240)
    _, first_slot = pacer.reserve(100.0)
    _, second_slot = pacer.reserve(100.0)
    pacer.release(first_slot)
    wait, next_slot = pacer.reserve(100.0)
    assert wait == 480.0
    assert next_slot == 580.0
    assert second_slot == 340.0
