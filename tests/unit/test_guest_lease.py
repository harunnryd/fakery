from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from twin.bots import runner
from twin.core.config import Settings


@pytest.mark.parametrize("tier", ["guest", "signed"])
async def test_guest_spawn_ignores_shared_profile_lease(monkeypatch, tier: str) -> None:
    @asynccontextmanager
    async def session_scope(factory):
        yield None

    spawn = AsyncMock()
    failed = AsyncMock()
    lease = AsyncMock(return_value=False)
    monkeypatch.setattr(runner, "session_scope", session_scope)
    monkeypatch.setattr(runner, "_tier", lambda settings: tier)
    monkeypatch.setattr(runner, "is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(runner, "reserve_capacity", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "release_capacity", AsyncMock())
    monkeypatch.setattr(runner, "acquire_lease", lease)
    monkeypatch.setattr(runner, "release_lease", AsyncMock())
    monkeypatch.setattr(runner, "fail_run", failed)
    monkeypatch.setattr(runner, "launch_runtime", lambda *a, **kw: SimpleNamespace(spawn=spawn))
    monkeypatch.setattr(
        runner,
        "_service",
        lambda *a: SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status="queued"))),
    )
    context = SimpleNamespace(
        settings=Settings(), redis=object(), session_factory=None, owner="test"
    )
    await runner.execute_run("bot_test", context)
    assert spawn.await_count == (tier == "guest")
    assert lease.await_count == (tier == "signed")
    assert failed.await_count == (tier == "signed")
