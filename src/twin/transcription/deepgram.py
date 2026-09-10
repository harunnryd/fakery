import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import structlog

from twin.transcription.transcriber import (
    Segment,
    SegmentSink,
    live_segment,
    prerecorded_segments,
)

STREAM_MODEL = "nova-3"
STREAM_ENCODING = "linear16"
STREAM_SAMPLE_RATE = 16000
STREAM_CHANNELS = 1
DIARIZE_MODEL = "latest"
KEEPALIVE_INTERVAL_S = 3
STREAM_DRAIN_TIMEOUT_S = 30
CONNECT_ATTEMPTS = 3
CONNECT_RETRY_DELAY_S = 1
logger = structlog.get_logger()


class DeepgramTranscriber:
    def __init__(self, api_key: str, diarize: bool = True, language: str = "en") -> None:
        self._api_key = api_key
        self._diarize = diarize
        self._language = language

    async def transcribe_stream(self, audio: AsyncIterator[bytes], sink: SegmentSink) -> int:
        from deepgram import AsyncDeepgramClient

        client = AsyncDeepgramClient(api_key=self._api_key)
        async with _connect(
            client.listen.v1,
            model=STREAM_MODEL,
            language=self._language,
            encoding=STREAM_ENCODING,
            sample_rate=STREAM_SAMPLE_RATE,
            channels=STREAM_CHANNELS,
            interim_results=True,
            smart_format=True,
            punctuate=True,
        ) as socket:
            return await _stream(socket, audio, sink)

    async def transcribe_recording(self, audio: bytes) -> list[Segment]:
        from deepgram import AsyncDeepgramClient

        client = AsyncDeepgramClient(api_key=self._api_key)
        options = {
            "model": STREAM_MODEL,
            "language": self._language,
            "smart_format": True,
            "punctuate": True,
        }
        if self._diarize:
            options["diarize_model"] = DIARIZE_MODEL
        response = await client.listen.v1.media.transcribe_file(request=audio, **options)
        return prerecorded_segments(response)


@asynccontextmanager
async def _connect(listener: Any, **options: Any) -> AsyncIterator[Any]:
    async with AsyncExitStack() as stack:
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            try:
                socket = await stack.enter_async_context(listener.connect(**options))
                break
            except (TimeoutError, OSError) as error:
                logger.warning(
                    "transcribe.connect_failed", attempt=attempt, error=type(error).__name__
                )
                if attempt == CONNECT_ATTEMPTS:
                    raise
                await asyncio.sleep(CONNECT_RETRY_DELAY_S * attempt)
        logger.info("transcribe.connected")
        yield socket


async def _feed(socket: Any, audio: AsyncIterator[bytes]) -> None:
    pending = asyncio.create_task(anext(audio))
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=KEEPALIVE_INTERVAL_S)
            if not done:
                await socket.send_keep_alive()
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                await socket.send_close_stream()
                return
            await socket.send_media(chunk)
            pending = asyncio.create_task(anext(audio))
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def _receive(socket: Any, sink: SegmentSink) -> int:
    finals = 0
    async for message in socket:
        segment = live_segment(message)
        if segment is not None and segment.final:
            await sink(segment)
            finals += 1
    return finals


async def _stream(socket: Any, audio: AsyncIterator[bytes], sink: SegmentSink) -> int:
    feed = asyncio.create_task(_feed(socket, audio))
    receive = asyncio.create_task(_receive(socket, sink))
    try:
        done, _ = await asyncio.wait({feed, receive}, return_when=asyncio.FIRST_COMPLETED)
        if receive in done:
            result = receive.result()
            if not feed.done():
                raise ConnectionError("stream_closed_before_audio_eof")
            await feed
            return result
        await feed
        return await asyncio.wait_for(receive, STREAM_DRAIN_TIMEOUT_S)
    finally:
        feed.cancel()
        receive.cancel()
        await asyncio.gather(feed, receive, return_exceptions=True)
