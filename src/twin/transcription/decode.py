import asyncio
from collections.abc import AsyncIterator

PCM_SAMPLE_RATE = 16000
PCM_CHUNK_BYTES = 8192
DECODER_STOP_TIMEOUT_S = 5


def ffmpeg_argv() -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-f",
        "s16le",
        "-ac",
        "1",
        "-ar",
        str(PCM_SAMPLE_RATE),
        "pipe:1",
    ]


async def decode_webm(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_argv(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    feed = asyncio.create_task(_feed(proc, chunks))
    try:
        while True:
            data = await proc.stdout.read(PCM_CHUNK_BYTES)
            if not data:
                break
            yield data
        await feed
        if await proc.wait() != 0:
            raise RuntimeError("audio_decode_failed")
    finally:
        feed.cancel()
        await asyncio.gather(feed, return_exceptions=True)
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), DECODER_STOP_TIMEOUT_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()


async def _feed(proc: asyncio.subprocess.Process, chunks: AsyncIterator[bytes]) -> None:
    assert proc.stdin is not None
    try:
        async for chunk in chunks:
            proc.stdin.write(chunk)
            await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
