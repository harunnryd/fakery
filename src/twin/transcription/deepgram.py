import asyncio
from collections.abc import AsyncIterator
from typing import Any

from twin.transcription.transcriber import Segment, SegmentSink, live_segment, prerecorded_segments

STREAM_MODEL = "nova-3"
STREAM_LANGUAGE = "id"
STREAM_ENCODING = "linear16"
STREAM_SAMPLE_RATE = 16000
STREAM_CHANNELS = 1


class DeepgramTranscriber:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def transcribe_stream(self, audio: AsyncIterator[bytes], sink: SegmentSink) -> int:
        from deepgram import AsyncDeepgramClient

        client = AsyncDeepgramClient(api_key=self._api_key)
        finals = 0
        async with client.listen.v1.connect(
            model=STREAM_MODEL,
            language=STREAM_LANGUAGE,
            encoding=STREAM_ENCODING,
            sample_rate=STREAM_SAMPLE_RATE,
            channels=STREAM_CHANNELS,
            interim_results=True,
            smart_format=True,
            punctuate=True,
        ) as socket:
            feed = asyncio.create_task(_feed(socket, audio))
            try:
                async for message in socket:
                    segment = live_segment(message)
                    if segment is not None and segment.final:
                        finals += 1
                        await sink(segment)
                    if getattr(message, "from_finalize", False):
                        break
            finally:
                await feed
            await socket.send_close_stream()
        return finals

    async def transcribe_recording(self, audio: bytes) -> list[Segment]:
        from deepgram import AsyncDeepgramClient

        client = AsyncDeepgramClient(api_key=self._api_key)
        response = await client.listen.v1.media.transcribe_file(
            request=audio,
            model=STREAM_MODEL,
            language=STREAM_LANGUAGE,
            smart_format=True,
            punctuate=True,
        )
        return prerecorded_segments(response)


async def _feed(socket: Any, audio: AsyncIterator[bytes]) -> None:
    async for chunk in audio:
        await socket.send_media(chunk)
    await socket.send_finalize()
