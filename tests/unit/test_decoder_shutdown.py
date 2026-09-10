import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from twin.transcription import decode


@pytest.mark.parametrize("terminate_works", [True, False], ids=["terminate", "kill-fallback"])
async def test_decoder_close_reaps_process_without_waiting_for_audio(
    monkeypatch, terminate_works: bool
) -> None:
    exited = asyncio.Event()
    proc = SimpleNamespace(
        stdin=SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock()),
        stdout=SimpleNamespace(read=AsyncMock(return_value=b"pcm")),
        returncode=None,
    )

    def finish() -> None:
        proc.returncode = -9
        exited.set()

    async def wait() -> int:
        await exited.wait()
        return proc.returncode

    proc.wait = wait
    proc.terminate = Mock(side_effect=finish if terminate_works else None)
    proc.kill = Mock(side_effect=finish)
    monkeypatch.setattr(decode.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(decode, "DECODER_STOP_TIMEOUT_S", 0.01)

    async def audio():
        await asyncio.Event().wait()
        yield b"audio"

    decoded = decode.decode_webm(audio())
    assert await anext(decoded) == b"pcm"
    await asyncio.wait_for(decoded.aclose(), 1)
    assert proc.returncode is not None
    assert proc.kill.call_count == (not terminate_works)
