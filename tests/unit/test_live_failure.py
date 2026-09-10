import asyncio
from unittest.mock import AsyncMock

import pytest

from twin.bots import runtime


@pytest.mark.parametrize("failure", [TimeoutError, ConnectionError])
async def test_live_failure_remains_visible_to_finalization(monkeypatch, failure) -> None:
    status = AsyncMock()
    monkeypatch.setattr(runtime, "_set_artifact_status", status)
    provider = AsyncMock()
    provider.transcribe_stream.side_effect = failure()

    async def chunks():
        yield b"unused"

    result = await runtime._transcribe_live("bot_test", None, provider, chunks())
    assert result.error == "live-failed"
    assert result.finals == 0
    status.assert_awaited_once_with(
        None, "bot_test", "transcription_status", "provider-failed", "live-failed"
    )


async def test_batch_failure_times_out_without_blocking_finalization(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "BATCH_TRANSCRIBE_TIMEOUT_S", 0.01)
    provider = AsyncMock()

    async def hang(_: bytes) -> list:
        await asyncio.sleep(1)
        return []

    provider.transcribe_recording.side_effect = hang
    result = await runtime._batch_segments("bot_test", None, provider, b"audio")
    assert result is None
