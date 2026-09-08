from datetime import UTC, datetime, timedelta

import pytest

from twin.bots.models import BotRun
from twin.bots.runner import is_orphan, sweep_orphans
from twin.bots.runtime import RunContext, _complete
from twin.bots.state import BotStatus
from twin.core.config import Settings


class FakeRedis:
    def __init__(self, beats: set[str] | None = None) -> None:
        self.beats: set[str] = set(beats or [])

    async def get(self, name: str) -> str | None:
        return "1" if name in self.beats else None

    async def delete(self, name: str) -> None:
        self.beats.discard(name)


class FakeResult:
    def __init__(self, rows: list[BotRun]) -> None:
        self._rows = rows

    def scalars(self) -> list[BotRun]:
        return self._rows


class FakeSession:
    def __init__(self, rows: list[BotRun] | None = None, run: BotRun | None = None) -> None:
        self._rows = rows or []
        self._run = run
        self.commits = 0

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, stmt: object) -> FakeResult:
        return FakeResult(self._rows)

    async def get(self, model: object, bot_id: str) -> BotRun | None:
        return self._run

    def add(self, run: BotRun) -> None:
        self._run = run

    async def commit(self) -> None:
        self.commits += 1


def _run_at(status: str, updated_at: datetime, bot_id: str = "bot_x") -> BotRun:
    return BotRun(
        id=bot_id,
        meeting_url="https://meet.google.com/abc-defg-hij",
        status=status,
        created_at=updated_at,
        updated_at=updated_at,
    )


NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "age_s", "heartbeat", "expected"),
    [
        ("joining", 301, False, True),
        ("recording", 900, False, True),
        ("joined", 299, False, False),
        ("processing", 600, True, False),
        ("completed", 9999, False, False),
        ("failed", 9999, False, False),
        ("queued", 9999, False, False),
    ],
    ids=[
        "stale-joining",
        "stale-recording",
        "fresh-joined",
        "heartbeat-alive",
        "terminal-completed",
        "terminal-failed",
        "unclaimed-queued",
    ],
)
def test_is_orphan_flags_only_stale_live_runs_without_beat(
    status: str, age_s: int, heartbeat: bool, expected: bool
) -> None:
    updated = NOW - timedelta(seconds=age_s)
    assert is_orphan(BotStatus(status), updated, heartbeat, NOW, 300) is expected


async def test_sweep_reaps_only_stale_runs_without_beat() -> None:
    stale = NOW - timedelta(seconds=900)
    fresh = NOW - timedelta(seconds=60)
    rows = [
        _run_at("recording", stale, "bot_reap"),
        _run_at("joined", stale, "bot_alive"),
        _run_at("recording", fresh, "bot_fresh"),
        _run_at("completed", stale, "bot_done"),
    ]
    session = FakeSession(rows=rows)
    redis = FakeRedis(beats={"bots:hb:bot_alive"})
    reaped = await sweep_orphans(lambda: session, redis, 300, NOW)  # type: ignore[arg-type]
    assert reaped == 1
    assert rows[0].status == "failed"
    assert rows[0].error_code == "orphaned"
    assert rows[0].left_at == NOW
    assert rows[1].status == "joined"
    assert rows[2].status == "recording"
    assert session.commits == 1


@pytest.mark.parametrize(
    ("cancelled", "expected_code"),
    [(True, "cancelled"), (False, None)],
    ids=["cancelled-run", "natural-end"],
)
async def test_complete_marks_cancelled_runs(cancelled: bool, expected_code: str | None) -> None:
    moment = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    run = _run_at("processing", moment, "bot_x")
    session = FakeSession(run=run)
    context = RunContext(
        session_factory=lambda: session,  # type: ignore[return-value]
        settings=Settings(),
        blob=None,
        redis=None,
    )
    await _complete("bot_x", context, b"", cancelled)
    assert run.status == "completed"
    assert run.error_code == expected_code
