# Cloud Run image. uv for reproducible installs from uv.lock.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /uvx /bin/

WORKDIR /app

# Dependencies first, so a source-only change does not reinstall the world.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY anbu_care ./anbu_care
RUN uv sync --frozen --no-dev

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "anbu_care.server:app", "--host", "0.0.0.0", "--port", "8080"]
