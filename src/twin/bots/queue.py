from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

STREAM = "bots:runs"
GROUP = "workers"
ORPHAN_IDLE_MS = 300_000
CLAIM_BLOCK_MS = 5_000
CANCEL_KEY = "bots:cancel:{bot_id}"
CANCEL_TTL_S = 3_600


async def request_cancel(redis: Redis, bot_id: str) -> None:
    await redis.set(CANCEL_KEY.format(bot_id=bot_id), "1", ex=CANCEL_TTL_S)


async def is_cancelled(redis: Redis, bot_id: str) -> bool:
    return bool(await redis.get(CANCEL_KEY.format(bot_id=bot_id)))


async def ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except Exception as err:
        if "BUSYGROUP" not in str(err):
            raise


async def enqueue_run(redis: Redis, bot_id: str) -> None:
    await ensure_group(redis)
    await redis.xadd(STREAM, {"bot_id": bot_id})


async def claim_run(
    redis: Redis, consumer: str, block_ms: int = CLAIM_BLOCK_MS
) -> tuple[str, str] | None:
    try:
        rows = await _read_group(redis, consumer, block_ms)
    except RedisTimeoutError:
        return None
    except ResponseError as err:
        if "NOGROUP" not in str(err):
            raise
        await ensure_group(redis)
        rows = await _read_group(redis, consumer, block_ms)
    return _first_entry(rows)


async def _read_group(redis: Redis, consumer: str, block_ms: int) -> list:
    return await redis.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=1, block=block_ms)


async def reclaim_stale(redis: Redis, consumer: str) -> tuple[str, str] | None:
    await ensure_group(redis)
    cursor, entries, _ = await redis.xautoclaim(
        STREAM, GROUP, consumer, min_idle_time=ORPHAN_IDLE_MS, start_id="0", count=1
    )
    if not entries:
        return None
    return _first_entry([("", entries)])  # type: ignore[list-item]


async def ack_run(redis: Redis, entry_id: str) -> None:
    await redis.xack(STREAM, GROUP, entry_id)


def _first_entry(rows: list[Any]) -> tuple[str, str] | None:
    for _stream, entries in rows:
        for entry_id, fields in entries:
            bot_id = fields.get("bot_id")
            if bot_id:
                return str(entry_id), str(bot_id)
    return None
