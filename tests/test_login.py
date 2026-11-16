"""Logowanie i nawigacja Playwright względem mock serwisu."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from librus_fetcher.browser import LoginError, logged_in
from librus_fetcher.config import Account

JS_MESSAGES_PAGE_2 = ("() => { const e = document.getElementById('numer_strony105'); "
                      "e.value = 1; e.form.submit(); }")
JS_NEXT_MONTH = """() => { const m = document.querySelector('select[name=miesiac]');
    const r = document.querySelector('select[name=rok]');
    let i = m.selectedIndex + 1; if (i >= m.options.length) { i = 0; r.selectedIndex += 1; }
    m.selectedIndex = i; m.form.submit(); }"""


def text(soup: BeautifulSoup) -> str:
    return " ".join(soup.get_text(" ").replace("\xa0", " ").split())


def test_login_lands_on_parent_index(browser, settings, mock_account):
    with logged_in(browser, settings, mock_account) as s:
        assert s.page.url == f"{settings.base_url}/rodzic/index"


def test_wrong_password_raises(browser, settings):
    bad = Account(name="X", login="mock_alfa", password="zle-haslo")
    with pytest.raises(LoginError, match="Nieprawidłowy login"):
        with logged_in(browser, settings, bad):
            pass


def test_main_pages_are_served(browser, settings, mock_account):
    with logged_in(browser, settings, mock_account) as s:
        soup = BeautifulSoup(s.get("/ogloszenia"), "lxml")
        assert len(soup.select("table.decorated.big")) > 10
        soup = BeautifulSoup(s.get("/przegladaj_oceny/uczen"), "lxml")
        assert soup.select("span.grade-box a.ocena")
        assert "Uczeń Testowy" in text(soup)


def test_message_pagination_and_detail(browser, settings, mock_account):
    with logged_in(browser, settings, mock_account) as s:
        first = BeautifulSoup(s.get("/wiadomosci"), "lxml")
        assert "Strona 1 z 2" in text(first)
        second = BeautifulSoup(s.submit(JS_MESSAGES_PAGE_2), "lxml")
        assert "Strona 2 z 2" in text(second)
        # treść wiadomości: pierwsza z listy, dla której istnieje fixture
        from mock_librus.app import fixture_set
        routes = fixture_set("account0").routes
        links = [a["href"] for a in first.select("a[href^='/wiadomosci/1/5/']")]
        link = next(h for h in links if f"GET {h}" in routes)
        detail = BeautifulSoup(s.get(link), "lxml")
        assert detail.select_one("div.container-message-content")


def test_calendar_next_month(browser, settings, mock_account):
    with logged_in(browser, settings, mock_account) as s:
        cur = BeautifulSoup(s.get("/terminarz"), "lxml")
        nxt = BeautifulSoup(s.submit(JS_NEXT_MONTH), "lxml")
        m_cur = cur.select_one("select[name=miesiac] option[selected]")["value"]
        m_nxt = nxt.select_one("select[name=miesiac] option[selected]")["value"]
        assert int(m_nxt) == int(m_cur) % 12 + 1
        # miesiąc bez fixtures -> pusty kalendarz
        empty = BeautifulSoup(s.submit(JS_NEXT_MONTH), "lxml")
        assert not empty.select("div.kalendarz-dzien td")


def test_session_required(browser, settings):
    from librus_fetcher.browser import LibrusSession
    s = LibrusSession(browser, settings)
    try:
        with pytest.raises(LoginError):
            s.get("/ogloszenia")  # bez logowania -> przekierowanie na portal
    finally:
        s.close()
