from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn

from librus_fetcher.config import Account, FetcherSettings
from librus_fetcher.browser import launch


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mock_url() -> str:
    """Mock Librusa uruchomiony w wątku na wolnym porcie."""
    from mock_librus.app import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("mock nie wystartował")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def settings(mock_url: str) -> FetcherSettings:
    return FetcherSettings(portal_url=f"{mock_url}/rodzina", base_url=mock_url, request_delay_seconds=0)


@pytest.fixture(scope="session")
def browser(settings: FetcherSettings):
    with launch(settings) as b:
        yield b


@pytest.fixture
def mock_account() -> Account:
    return Account(name="Alfa", login="mock_alfa", password="mock-pass-1")
