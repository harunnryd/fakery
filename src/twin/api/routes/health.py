from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from twin.core.metrics import render

router = APIRouter(tags=["health"])
EXPECTED_SCHEMA_REVISION = "0009"


@router.get("/healthz")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readiness(request: Request) -> Response:
    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
            revision = await session.execute(text("SELECT version_num FROM alembic_version"))
            if revision.scalar_one_or_none() != EXPECTED_SCHEMA_REVISION:
                return Response(
                    content='{"status":"not-ready"}',
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    media_type="application/json",
                )
        await request.app.state.redis.ping()
    except Exception:
        return Response(
            content='{"status":"not-ready"}',
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
    return Response(content='{"status":"ready"}', media_type="application/json")


@router.get("/metrics")
async def metrics() -> Response:
    return Response(content=render(), media_type="text/plain; version=0.0.4")
