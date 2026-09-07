# Single image for both roles: the API (default CMD) and the worker
# (different command). Ships CloakBrowser stealth Chromium + Xvfb so a bot
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
    CLOAKBROWSER_CACHE_DIR=/opt/cloakbrowser \
    CLOAKBROWSER_AUTO_UPDATE=false \
    PATH="/app/.venv/bin:$PATH"
# Xvfb: headed Chromium without a display; dumb-init: reap zombies from
# browser child processes; fonts: Meet renders text, not tofu; lib set:
# system deps for the CloakBrowser patched Chromium binary.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb dumb-init ca-certificates fonts-liberation fonts-noto-color-emoji \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdbus-1-3 libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 \
        libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
        libcairo2 libasound2 libx11-xcb1 libfontconfig1 libxcb1 \
        libxext6 libxshmfence1 libglib2.0-0 libgtk-3-0 \
        libpangocairo-1.0-0 libcairo-gobject2 libgdk-pixbuf-2.0-0 \
        libxss1 libxtst6 libgl1-mesa-dri libegl-mesa0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY . /app
RUN uv sync --frozen --extra browser \
    && uv run --frozen python -m cloakbrowser install \
    && uv run --frozen python -m cloakbrowser info --quick
EXPOSE 8000
ENTRYPOINT ["dumb-init", "--"]
CMD ["uvicorn", "twin.main:app", "--host", "0.0.0.0", "--port", "8000"]
