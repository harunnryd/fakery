# Single image for both roles: the API (default CMD) and, once module 1
# lands, the worker (different command). Ships Chromium + Xvfb so a bot
# can run headed-under-Xvfb inside the container.

FROM python:3.12-slim-bookworm AS deps
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra browser

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PATH="/app/.venv/bin:$PATH"
# Xvfb: headed Chromium without a display; dumb-init: reap zombies from
# browser child processes; fonts: Meet renders text, not tofu.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb dumb-init ca-certificates fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY . /app
RUN uv sync --frozen --extra browser \
    && uv run --frozen patchright install --with-deps chromium
EXPOSE 8000
ENTRYPOINT ["dumb-init", "--"]
CMD ["uvicorn", "twin.main:app", "--host", "0.0.0.0", "--port", "8000"]
