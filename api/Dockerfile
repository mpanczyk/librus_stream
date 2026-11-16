# Kontekst budowania: korzeń repozytorium (uv.lock jest wspólny dla workspace'u).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY fetcher/pyproject.toml fetcher/
COPY api/pyproject.toml api/
COPY mock_librus/pyproject.toml mock_librus/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package librus-api --no-install-workspace

COPY api/ api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package librus-api --no-editable

ENV PATH=/opt/venv/bin:$PATH \
    DATABASE_PATH=/data/librus.sqlite \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "librus_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
