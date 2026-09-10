import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from redis.asyncio import from_url

from twin.api.routes import router
from twin.core.config import get_settings
from twin.core.errors import TwinError, make_error
from twin.core.logging import configure_logging
from twin.storage.blob import MinioBlobStore
from twin.storage.database import create_engine_and_sessionmaker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(json_logs=settings.env != "dev", log_level=settings.log_level)
    engine, session_factory = create_engine_and_sessionmaker(settings.database_url)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.redis = from_url(settings.redis_url, decode_responses=True)
    app.state.blob = MinioBlobStore(
        settings.blob_endpoint,
        settings.blob_access_key,
        settings.blob_secret_key,
        settings.blob_bucket,
    )
    yield
    await app.state.redis.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Fakery API", version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(TwinError)
    async def twin_error_handler(request: Request, exc: TwinError) -> JSONResponse:
        request_id = request.headers.get("x-request-id", "")
        return JSONResponse(status_code=exc.status, content=exc.to_problem(request_id))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(
            f"{'.'.join(str(loc) for loc in err.get('loc', []))}: {err.get('msg', 'invalid')}"
            for err in exc.errors()
        )
        problem = make_error("validation-failed", detail=detail)
        request_id = request.headers.get("x-request-id", "")
        return JSONResponse(status_code=problem.status, content=problem.to_problem(request_id))

    return app


app = create_app()
