# Imagen única de la plataforma: API, workers, despachador, ingesta y stub de identidad.
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.8.0 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY stubs ./stubs
COPY alembic.ini ./
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:${PATH}"
RUN useradd --system --uid 10001 social && chown -R social /app
USER social

EXPOSE 8000
CMD ["social-listener", "api"]
