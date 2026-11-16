"""Przejście przez podstrony dziennika jednego zalogowanego konta."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from . import parsers as P
from .browser import LibrusSession
from .config import FetcherSettings
from .models import Entry

log = logging.getLogger(__name__)

# numer_strony105 jest liczony od zera
JS_MESSAGES_PAGE = ("(page) => { const e = document.getElementById('numer_strony105'); "
                    "e.value = page; e.form.submit(); }")
JS_NEXT_MONTH = """() => { const m = document.querySelector('select[name=miesiac]');
    const r = document.querySelector('select[name=rok]');
    let i = m.selectedIndex + 1;
    if (i >= m.options.length) { i = 0; r.selectedIndex += 1; }
    m.selectedIndex = i; m.form.submit(); }"""


@dataclass
class ScrapeResult:
    entries: list[Entry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def scrape_account(session: LibrusSession, settings: FetcherSettings,
                   known_message_ids: set[int]) -> ScrapeResult:
    """Zbiera wpisy ze wszystkich sekcji. Błąd jednej sekcji nie przerywa pozostałych."""
    result = ScrapeResult()

    def section(name: str, fn: Callable[[], list[Entry]]) -> None:
        try:
            items = fn()
            log.info("  %s: %d", name, len(items))
            result.entries.extend(items)
        except Exception as e:  # noqa: BLE001 - logujemy i idziemy dalej
            log.exception("  %s: błąd", name)
            result.errors.append(f"{name}: {e}")

    section("ogłoszenia", lambda: P.parse_announcements(session.get("/ogloszenia")))
    section("oceny", lambda: P.parse_grades(session.get("/przegladaj_oceny/uczen")))
    section("uwagi", lambda: P.parse_notes(session.get("/uwagi")))
    section("frekwencja", lambda: P.parse_attendance(session.get("/przegladaj_nb/uczen")))
    section("terminarz", lambda: _calendar(session, settings.calendar_months_ahead))
    section("wiadomości", lambda: _messages(session, known_message_ids, result))
    return result


def _calendar(session: LibrusSession, months_ahead: int) -> list[Entry]:
    html = session.get("/terminarz")
    events: list[Entry] = list(P.parse_calendar(html))
    for _ in range(months_ahead):
        events.extend(P.parse_calendar(session.submit(JS_NEXT_MONTH)))
    return events


def _messages(session: LibrusSession, known_ids: set[int], result: ScrapeResult) -> list[Entry]:
    listing = P.parse_message_list(session.get("/wiadomosci"))
    items = list(listing.items)
    for page in range(2, listing.pages + 1):
        items.extend(P.parse_message_list(session.submit(JS_MESSAGES_PAGE, page - 1)).items)

    new = [i for i in items if i.librus_id not in known_ids]
    log.info("  wiadomości: %d na liście, %d nowych", len(items), len(new))
    out: list[Entry] = []
    for item in new:
        try:
            out.append(P.parse_message(session.get(item.path), item.librus_id))
        except Exception as e:  # noqa: BLE001
            log.warning("  wiadomość %s: %s", item.librus_id, e)
            result.errors.append(f"wiadomość {item.librus_id}: {e}")
    return out
