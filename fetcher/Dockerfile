# Kontekst budowania: korzeń repozytorium (uv.lock jest wspólny dla workspace'u).
# Obraz z Playwright i przeglądarkami - wersja musi odpowiadać playwright w fetcher/pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.65.0-noble

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=python3.12 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# 1) same zależności (warstwa cache'owana, dopóki nie zmieni się lockfile)
COPY pyproject.toml uv.lock ./
COPY fetcher/pyproject.toml fetcher/
COPY api/pyproject.toml api/
COPY mock_librus/pyproject.toml mock_librus/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package librus-fetcher --no-install-workspace

# 2) kod serwisu
COPY fetcher/ fetcher/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package librus-fetcher --no-editable

ENV PATH=/opt/venv/bin:$PATH \
    CONFIG_PATH=/config/config.yaml \
    PYTHONUNBUFFERED=1

CMD ["python", "-m", "librus_fetcher"]
