# syntax=docker/dockerfile:1.7
# LaneReach API / worker / beat image. One image, three commands (see docker-compose.yml).

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first so they are cached independently of source changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
RUN groupadd --system lanereach && useradd --system --gid lanereach --home /app lanereach \
    && mkdir -p /data/documents && chown lanereach:lanereach /data/documents
WORKDIR /app
COPY --from=builder --chown=lanereach:lanereach /app/.venv ./.venv
COPY --from=builder --chown=lanereach:lanereach /app/src ./src
COPY --chown=lanereach:lanereach alembic.ini ./
COPY --chown=lanereach:lanereach alembic ./alembic
USER lanereach
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live').status == 200 else 1)"
CMD ["uvicorn", "lanereach.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
