"""Sesja przeglądarki (Playwright, headless) zalogowana na jedno konto Librus Synergia."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import Browser, Frame, Page, Playwright, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .config import Account, FetcherSettings

log = logging.getLogger(__name__)

LOGIN_TIMEOUT_MS = 30_000


class LoginError(RuntimeError):
    pass


class LibrusSession:
    """Zalogowana karta przeglądarki. Każde konto dostaje własny, czysty kontekst (ciasteczka)."""

    def __init__(self, browser: Browser, settings: FetcherSettings):
        self.settings = settings
        self._ctx = browser.new_context(locale="pl-PL", user_agent=settings.user_agent)
        self.page: Page = self._ctx.new_page()
        self._last_request = 0.0

    # ------------------------------------------------------------------ logowanie
    def login(self, account: Account) -> None:
        page = self.page
        s = self.settings
        log.info("logowanie: %s", account.name)
        page.goto(s.portal_url, wait_until="networkidle")
        try:  # baner zgody na ciasteczka (nie zawsze się pojawia)
            page.click("button:has-text('Akceptuję')", timeout=5_000)
        except PlaywrightTimeout:
            pass
        page.click("button:has-text('LIBRUS Synergia')")
        page.click("a[href='/rodzina/synergia/loguj']")
        page.wait_for_load_state("networkidle")

        frame = self._find_login_frame()
        frame.fill("#Login", account.login)
        frame.fill("#Pass", account.password.get_secret_value())
        frame.click("#LoginBtn")
        try:
            page.wait_for_url(f"{s.base_url}/rodzic/**", timeout=LOGIN_TIMEOUT_MS)
        except PlaywrightTimeout as e:
            raise LoginError(f"logowanie nie powiodło się dla konta {account.name!r}: "
                             f"{self._login_error_text(frame)}") from e
        page.wait_for_load_state("networkidle")
        self._last_request = time.monotonic()

    def _find_login_frame(self) -> Frame:
        deadline = time.monotonic() + LOGIN_TIMEOUT_MS / 1000
        while time.monotonic() < deadline:
            for frame in self.page.frames:
                try:
                    if frame.query_selector("#Login"):
                        frame.wait_for_selector("#Login", state="visible", timeout=5_000)
                        return frame
                except Exception:  # ramka mogła zostać odłączona w trakcie ładowania
                    continue
            self.page.wait_for_timeout(250)
        raise LoginError("nie znaleziono formularza logowania (pole #Login)")

    @staticmethod
    def _login_error_text(frame: Frame) -> str:
        try:
            el = frame.query_selector(".alert, .error, #LoginError, [role=alert]")
            if el:
                return el.inner_text().strip() or "brak komunikatu"
        except Exception:
            pass
        return "przekroczono czas oczekiwania na przekierowanie do dziennika"

    # ------------------------------------------------------------------ nawigacja
    def _throttle(self) -> None:
        wait = self.settings.request_delay_seconds - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get(self, path: str) -> str:
        """Otwiera podstronę dziennika i zwraca jej HTML."""
        self._throttle()
        # strony dziennika są renderowane po stronie serwera - "load" wystarcza
        resp = self.page.goto(f"{self.settings.base_url}{path}", wait_until="load")
        if resp is not None and resp.status >= 400:
            raise RuntimeError(f"GET {path}: HTTP {resp.status}")
        url = self.page.url
        if url.startswith(self.settings.portal_url) or not url.startswith(self.settings.base_url):
            raise LoginError(f"sesja wygasła (przekierowanie na {url})")
        return self.page.content()

    def submit(self, script: str, arg=None) -> str:
        """Wysyła formularz bieżącej strony skryptem JS (paginacja, zmiana miesiąca); zwraca HTML."""
        self._throttle()
        with self.page.expect_navigation(wait_until="load"):
            self.page.evaluate(script, arg)
        return self.page.content()

    def logout(self) -> None:
        try:
            self._throttle()
            self.page.goto(f"{self.settings.base_url}/wyloguj", wait_until="networkidle")
        except Exception:  # noqa: BLE001 - wylogowanie jest tylko uprzejmością
            log.debug("wylogowanie nie powiodło się", exc_info=True)

    def close(self) -> None:
        self._ctx.close()


@contextmanager
def launch(settings: FetcherSettings) -> Iterator[Browser]:
    with sync_playwright() as p:  # type: Playwright
        browser = p.chromium.launch(headless=settings.headless)
        try:
            yield browser
        finally:
            browser.close()


@contextmanager
def logged_in(browser: Browser, settings: FetcherSettings, account: Account) -> Iterator[LibrusSession]:
    session = LibrusSession(browser, settings)
    try:
        session.login(account)
        yield session
        session.logout()
    finally:
        session.close()
