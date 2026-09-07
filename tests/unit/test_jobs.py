from datetime import UTC, datetime
from pathlib import Path

import pytest

from twin.bots.jobs import (
    bot_job_name,
    build_bot_job,
    resolve_job_outcome,
)
from twin.bots.models import BotRun
from twin.bots.runtime import JobRuntime, PreparedLaunch, RunContext
from twin.bots.state import BotStatus
from twin.core.config import Settings
from twin.meet.launcher import EngineConfig


class FakeJobs:
    def __init__(self, result: str = "completed") -> None:
        self.result = result
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.closed = False

    async def create_job(self, manifest: dict) -> None:
        self.created.append(manifest)

    async def wait_terminal(self, namespace: str, name: str, timeout_s: int) -> str:
        return self.result

    async def delete_job(self, namespace: str, name: str) -> None:
        self.deleted.append(name)

    async def aclose(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, run: BotRun) -> None:
        self._run = run
        self.commits = 0

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, stmt: object) -> None:
        raise AssertionError("no queries expected")

    async def get(self, model: object, bot_id: str) -> BotRun:
        return self._run

    def add(self, run: BotRun) -> None:
        self._run = run

    async def commit(self) -> None:
        self.commits += 1


def _context(run: BotRun) -> RunContext:
    session = FakeSession(run)
    return RunContext(
        session_factory=lambda: session,  # type: ignore[return-value]
        settings=Settings(),
        blob=None,
        redis=None,
    )


def _run(status: str) -> BotRun:
    moment = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    return BotRun(
        id="bot_abc123",
        meeting_url="https://meet.google.com/abc-defg-hij",
        status=status,
        created_at=moment,
        updated_at=moment,
    )


@pytest.mark.parametrize(
    ("bot_id", "expected"),
    [
        ("bot_abc123", "botrun-bot-abc123"),
        ("bot_A83F9C", "botrun-bot-a83f9c"),
        ("bot_x", "botrun-bot-x"),
    ],
    ids=["lowercase", "uppercased", "short"],
)
def test_bot_job_name_is_dns_safe(bot_id: str, expected: str) -> None:
    assert bot_job_name(bot_id) == expected


@pytest.mark.parametrize(
    ("minutes", "expected_deadline"),
    [(45, 3300), (10, 1200)],
    ids=["default-meeting", "short-meeting"],
)
def test_build_bot_job_carries_run_identity(minutes: int, expected_deadline: int) -> None:
    manifest = build_bot_job(
        "bot_abc123", image="fakery:dev", namespace="fakery", meeting_max_minutes=minutes
    )
    assert manifest["metadata"]["name"] == "botrun-bot-abc123"
    assert manifest["metadata"]["labels"]["twin.bot/id"] == "bot_abc123"
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "fakery:dev"
    assert "python -m twin.bot_run bot_abc123" in container["command"][2]
    assert manifest["spec"]["backoffLimit"] == 0
    assert manifest["spec"]["ttlSecondsAfterFinished"] == 300
    assert manifest["spec"]["activeDeadlineSeconds"] == expected_deadline
    assert manifest["spec"]["template"]["spec"]["restartPolicy"] == "Never"


@pytest.mark.parametrize(
    ("job_result", "db_terminal", "expected"),
    [
        ("completed", True, None),
        ("completed", False, "job-failed"),
        ("failed", True, None),
        ("failed", False, "job-failed"),
        ("timeout", True, None),
        ("timeout", False, "job-timeout"),
    ],
    ids=[
        "clean-finish",
        "finished-without-status",
        "infra-dead-after-terminal",
        "infra-dead-mid-run",
        "timeout-after-finish",
        "timeout-mid-run",
    ],
)
def test_resolve_job_outcome(job_result: str, db_terminal: bool, expected: str | None) -> None:
    assert resolve_job_outcome(job_result, db_terminal) == expected


@pytest.mark.parametrize(
    ("job_result", "db_status", "expect_code", "expect_delete"),
    [
        ("completed", "completed", None, False),
        ("failed", "recording", "job-failed", False),
        ("timeout", "recording", "job-timeout", True),
    ],
    ids=["clean-finish", "infra-failure", "watch-timeout"],
)
async def test_job_spawn_resolves_supervisor_outcome(
    job_result: str, db_status: str, expect_code: str | None, expect_delete: bool
) -> None:
    jobs = FakeJobs(result=job_result)
    run = _run(db_status)
    context = _context(run)
    runtime = JobRuntime(jobs)
    launch = PreparedLaunch(config=EngineConfig(profile_dir=Path("/tmp/x")), warmup=False)
    await runtime.spawn("bot_abc123", "https://meet.google.com/abc-defg-hij", "G", context, launch)
    assert len(jobs.created) == 1
    assert jobs.created[0]["metadata"]["name"] == "botrun-bot-abc123"
    assert jobs.closed is True
    assert (len(jobs.deleted) > 0) is expect_delete
    if expect_code is None:
        assert BotStatus(run.status) is BotStatus(db_status)
    else:
        assert run.status == BotStatus.FAILED.value
        assert run.error_code == expect_code
