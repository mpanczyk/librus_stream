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
    uv sync --frozen --no-dev --package librus-mock --no-install-workspace

COPY mock_librus/ mock_librus/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package librus-mock --no-editable

# Fixtures są montowane jako volumen (patrz docker-compose.mock.yml).
ENV PATH=/opt/venv/bin:$PATH \
    MOCK_FIXTURES_DIR=/fixtures \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "mock_librus.app:app", "--host", "0.0.0.0", "--port", "8000"]
