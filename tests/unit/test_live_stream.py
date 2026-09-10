import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from twin.transcription import deepgram


class Socket:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def send_media(self, data: bytes) -> None:
        self.events.append("media")

    async def send_keep_alive(self) -> None:
        self.events.append("keepalive")

    async def send_close_stream(self) -> None:
        self.events.append("close")

    async def send_finalize(self) -> None:
        self.events.append("finalize")


@pytest.mark.parametrize("idle", [False, True], ids=["audio", "idle-before-audio"])
async def test_feed_keeps_idle_input_alive_and_closes_at_eof(monkeypatch, idle: bool) -> None:
    monkeypatch.setattr(deepgram, "KEEPALIVE_INTERVAL_S", 0.01, raising=False)
    socket = Socket()

    async def audio():
        if idle:
            await asyncio.sleep(0.035)
        yield b"audio"

    await deepgram._feed(socket, audio())
    assert socket.events[-2:] == ["media", "close"]
    assert ("keepalive" in socket.events) == idle


@pytest.mark.parametrize("failure", ["sender", "receiver", "early-close"])
async def test_stream_failure_cancels_other_tasks(failure: str) -> None:
    stopped = asyncio.Event()

    class FailingSocket(Socket):
        async def send_media(self, data: bytes) -> None:
            if failure == "sender":
                raise ConnectionError("send_failed")

        async def __aiter__(self):
            if failure == "receiver":
                raise ConnectionError("receive_failed")
            if failure == "sender":
                await asyncio.Event().wait()
            return
            yield

    async def audio():
        try:
            yield b"audio"
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def sink(segment):
        pass

    source = audio()
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(deepgram._stream(FailingSocket(), source, sink), 1)
    await source.aclose()
    assert stopped.is_set()


@pytest.mark.parametrize("final_count", [0, 2], ids=["silence", "multiple-final-results"])
async def test_stream_drains_all_results_after_audio_eof(final_count: int) -> None:
    closed = asyncio.Event()
    received = []

    class DrainingSocket(Socket):
        async def send_close_stream(self) -> None:
            closed.set()

        async def __aiter__(self):
            await closed.wait()
            for index in range(final_count):
                yield SimpleNamespace(
                    channel=SimpleNamespace(
                        alternatives=[SimpleNamespace(transcript=str(index), words=[])]
                    ),
                    start=index,
                    duration=1,
                    is_final=True,
                    from_finalize=True,
                )

    async def audio():
        yield b"audio"

    async def sink(segment):
        received.append(segment.text)

    count = await asyncio.wait_for(deepgram._stream(DrainingSocket(), audio(), sink), 1)
    assert count == final_count
    assert received == [str(index) for index in range(final_count)]


@pytest.mark.parametrize("failures", [0, 2, 3], ids=["connect", "retry", "exhausted"])
async def test_connection_retry_is_bounded(monkeypatch, failures: int) -> None:
    monkeypatch.setattr(deepgram, "CONNECT_RETRY_DELAY_S", 0)
    attempts = 0
    released = False

    class Listener:
        @asynccontextmanager
        async def connect(self, **options):
            nonlocal attempts, released
            attempts += 1
            if attempts <= failures:
                raise TimeoutError
            try:
                yield Socket()
            finally:
                released = True

    if failures == 3:
        with pytest.raises(TimeoutError):
            async with deepgram._connect(Listener()):
                pytest.fail("exhausted connection yielded")
    else:
        async with deepgram._connect(Listener()):
            assert not released
        assert released
    assert attempts == min(failures + 1, 3)
