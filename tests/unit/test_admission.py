from datetime import UTC, datetime

import pytest

from twin.bots.models import BotRun
from twin.bots.runner import _wait_for_capacity, count_live_runs
from twin.bots.runtime import RunContext
from twin.core.config import Settings


class FakeResult:
    def __init__(self, rows: list[BotRun]) -> None:
        self._rows = rows

    def scalars(self) -> list[BotRun]:
        return self._rows


class FakeSession:
    def __init__(self, rows: list[BotRun]) -> None:
        self._rows = rows

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, stmt: object) -> FakeResult:
        live = {"joining", "joined", "recording", "processing"}
        return FakeResult([run for run in self._rows if run.status in live])


def _run(status: str) -> BotRun:
    moment = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    return BotRun(
        id=f"bot_{status}",
        meeting_url="https://meet.google.com/abc-defg-hij",
        status=status,
        created_at=moment,
        updated_at=moment,
    )


def _context(rows: list[BotRun], cap: int) -> RunContext:
    session = FakeSession(rows)
    settings = Settings(max_concurrent_runs=cap)
    return RunContext(
        session_factory=lambda: session,  # type: ignore[return-value]
        settings=settings,
        blob=None,
        redis=None,
    )


async def test_count_live_runs_ignores_terminal() -> None:
    context = _context([_run("recording"), _run("joining"), _run("completed"), _run("failed")], 3)
    assert await count_live_runs(context) == 2


@pytest.mark.parametrize(
    ("rows", "cap", "expected"),
    [
        ([], 1, True),
        ([_run("recording")], 2, True),
        ([_run("recording"), _run("joined")], 1, False),
    ],
    ids=["empty", "headroom", "capped"],
)
async def test_wait_for_capacity(
    monkeypatch: pytest.MonkeyPatch, rows: list[BotRun], cap: int, expected: bool
) -> None:
    monkeypatch.setattr("twin.bots.runner.ADMIT_POLL_S", 0)
    monkeypatch.setattr("twin.bots.runner.ADMIT_WAIT_S", 0)
    assert await _wait_for_capacity(_context(rows, cap), "bot_x") is expected
