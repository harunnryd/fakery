import pytest

from twin.bots.runtime import SHUTDOWN_EVENT, _on_sigterm, _should_stop, handle_sigterm


class FakeRedis:
    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    async def get(self, name: str) -> str | None:
        return "1" if self._cancelled else None


class FakeContext:
    def __init__(self, redis: FakeRedis | None) -> None:
        self.redis = redis


@pytest.mark.parametrize(
    ("cancelled", "shutdown", "expected"),
    [
        (False, False, False),
        (True, False, True),
        (False, True, True),
        (True, True, True),
    ],
    ids=["running", "cancelled", "shutdown", "both"],
)
async def test_should_stop(cancelled: bool, shutdown: bool, expected: bool) -> None:
    if shutdown:
        SHUTDOWN_EVENT.set()
    try:
        check = _should_stop(FakeContext(FakeRedis(cancelled)), "bot_x")  # type: ignore[arg-type]
        assert await check() is expected
    finally:
        SHUTDOWN_EVENT.clear()


async def test_handle_sigterm_sets_shutdown() -> None:
    try:
        _on_sigterm()
        assert SHUTDOWN_EVENT.is_set()
        handle_sigterm()
        assert SHUTDOWN_EVENT.is_set()
    finally:
        SHUTDOWN_EVENT.clear()
