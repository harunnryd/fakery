from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import select

from twin.bots.models import BotRun
from twin.core.time import utcnow
from twin.storage.database import session_scope

STREAM = "bots:runs"
GROUP = "workers"
ORPHAN_IDLE_MS = 300_000
CLAIM_BLOCK_MS = 5_000
CANCEL_KEY = "bots:cancel:{bot_id}"
CANCEL_TTL_S = 3_600
LEASE_TTL_S = 3_600
HEARTBEAT_TTL_S = 90
DISPATCH_RELAY_BATCH = 50


async def request_cancel(redis: Redis, bot_id: str) -> None:
    await redis.set(CANCEL_KEY.format(bot_id=bot_id), "1", ex=CANCEL_TTL_S)


async def is_cancelled(redis: Redis, bot_id: str) -> bool:
    return bool(await redis.get(CANCEL_KEY.format(bot_id=bot_id)))


def lease_key(tenant: str, platform: str, tier: str) -> str:
    return f"profiles:lease:{tenant}:{platform}:{tier}"


async def acquire_lease(redis: Redis, name: str, owner: str, ttl_s: int = LEASE_TTL_S) -> bool:
    return bool(await redis.set(name, owner, ex=ttl_s, nx=True))


async def release_lease(redis: Redis, name: str, owner: str) -> None:
    if await redis.get(name) == owner:
        await redis.delete(name)


def heartbeat_key(bot_id: str) -> str:
    return f"bots:hb:{bot_id}"


async def beat(redis: Redis, bot_id: str, ttl_s: int = HEARTBEAT_TTL_S) -> None:
    await redis.set(heartbeat_key(bot_id), "1", ex=ttl_s)


async def clear_beat(redis: Redis, bot_id: str) -> None:
    await redis.delete(heartbeat_key(bot_id))


async def ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except Exception as err:
        if "BUSYGROUP" not in str(err):
            raise


async def enqueue_run(redis: Redis, bot_id: str) -> None:
    await ensure_group(redis)
    await redis.xadd(STREAM, {"bot_id": bot_id})


async def relay_queued(redis: Redis, session_factory: Any) -> int:
    await ensure_group(redis)
    relayed = 0
    async with session_scope(session_factory) as session:
        rows = await session.execute(
            select(BotRun)
            .where(BotRun.status == "queued", BotRun.dispatch_status == "pending")
            .order_by(BotRun.created_at, BotRun.id)
            .limit(DISPATCH_RELAY_BATCH)
            .with_for_update(skip_locked=True)
        )
        for run in list(rows.scalars()):
            run.dispatch_attempts += 1
            try:
                await redis.xadd(STREAM, {"bot_id": run.id})
            except Exception as err:
                run.dispatch_last_error = type(err).__name__
                continue
            run.dispatch_status = "enqueued"
            run.dispatch_enqueued_at = utcnow()
            relayed += 1
    return relayed


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
    return _first_entry([("", entries)])


async def ack_run(redis: Redis, entry_id: str) -> None:
    await redis.xack(STREAM, GROUP, entry_id)


def _first_entry(rows: list[Any]) -> tuple[str, str] | None:
    for _stream, entries in rows:
        for entry_id, fields in entries:
            bot_id = fields.get("bot_id")
            if bot_id:
                return str(entry_id), str(bot_id)
    return None
