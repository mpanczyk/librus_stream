"""Parsery stron Librus Synergia -> modele wpisów.

Każdy parser przyjmuje HTML całej strony. Parsery są czyste (bez sieci), testowane na fixtures.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time

from bs4 import BeautifulSoup, Tag

from .models import (
    Announcement, AttendanceRecord, AttendanceType, CalendarEvent, CalendarEventCategory, Grade,
    GradeType, Message, MessageAttachment, Note, NoteType,
)
from .text import clean, html_to_md, html_to_text

log = logging.getLogger(__name__)

_BR_SPLIT = re.compile(r"<br\s*/?>", re.I)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _date(s: str) -> date:
    m = _DATE.search(s or "")
    if not m:
        raise ValueError(f"brak daty w {s!r}")
    return date.fromisoformat(m.group(0))


def _tooltip(title: str) -> dict[str, str]:
    """'Klucz: wartość<br>Klucz2: wartość2' -> słownik (tekst bez znaczników)."""
    out: dict[str, str] = {}
    for part in _BR_SPLIT.split(title or ""):
        key, sep, value = part.partition(":")
        if sep:
            out[clean(key)] = clean(BeautifulSoup(value, "lxml").get_text())
    return out


def _yes_no(s: str | None) -> bool | None:
    if s is None:
        return None
    s = s.strip().lower()
    return True if s == "tak" else False if s == "nie" else None


def _int(s: str | None) -> int | None:
    m = re.search(r"-?\d+", s or "")
    return int(m.group(0)) if m else None


# --------------------------------------------------------------------------- ogłoszenia

def parse_announcements(html: str) -> list[Announcement]:
    out = []
    for table in _soup(html).select("table.decorated.big"):
        rows = {clean(th.get_text()): th.find_next_sibling("td") for th in table.select("tbody th")}
        if not {"Dodał", "Data publikacji", "Treść"} <= rows.keys():
            continue
        head = table.select_one("thead td")
        out.append(Announcement(
            title=clean(head.get_text()) if head else "",
            author=clean(rows["Dodał"].get_text()),
            published_on=_date(rows["Data publikacji"].get_text()),
            content=html_to_text(rows["Treść"]),
            content_md=html_to_md(rows["Treść"]),
        ))
    return out


# --------------------------------------------------------------------------- wiadomości

_SENDER = re.compile(r"^(?P<name>.*?)\s*(?:\((?P<paren>[^)]*)\))?\s*(?:\[(?P<role>[^\]]*)\])?$")


def _split_sender(raw: str) -> tuple[str, str | None]:
    """'Testowa Alfa (Testowa Alfa) [Nauczyciel]' -> ('Testowa Alfa', 'Nauczyciel')."""
    m = _SENDER.match(clean(raw))
    name = clean(m.group("name"))
    role = m.group("role")
    if role is None and m.group("paren") and clean(m.group("paren")) != name:
        role = m.group("paren")  # np. "(Przedstawiciel Szkolnej Rady Rodziców)"
    return name, clean(role) if role else None


@dataclass(frozen=True)
class MessageListItem:
    librus_id: int
    path: str
    sender: str
    subject: str
    sent_at: datetime


@dataclass(frozen=True)
class MessageListPage:
    items: list[MessageListItem]
    page: int
    pages: int


def parse_message_list(html: str) -> MessageListPage:
    soup = _soup(html)
    items = []
    for cb in soup.select("input.wiadomoscLista"):
        tr = cb.find_parent("tr")
        tds = tr.find_all("td", recursive=False)
        link = tr.select_one("a[href^='/wiadomosci/']")
        items.append(MessageListItem(
            librus_id=int(cb["value"]),
            path=link["href"],
            sender=_split_sender(tds[2].get_text())[0],
            subject=clean(tds[3].get_text()),
            sent_at=datetime.fromisoformat(clean(tds[4].get_text())),
        ))
    page, pages = 1, 1
    pag = soup.select_one("div.pagination span")
    if pag:
        m = re.search(r"(\d+)\s+z\s+(\d+)", clean(pag.get_text()))
        if m:
            page, pages = int(m.group(1)), int(m.group(2))
    return MessageListPage(items=items, page=page, pages=pages)


def parse_message(html: str, librus_id: int) -> Message:
    soup = _soup(html)
    fields: dict[str, Tag] = {}
    for b in soup.select("td.medium.left > b, td.medium > b"):
        cell = b.find_parent("td").find_next_sibling("td")
        if cell is not None:
            fields[clean(b.get_text())] = cell
    content = soup.select_one("div.container-message-content")
    if content is None or "Nadawca" not in fields:
        raise ValueError(f"wiadomość {librus_id}: nieoczekiwana struktura strony")
    sender, role = _split_sender(fields["Nadawca"].get_text())

    attachments = []
    for img in soup.select("img[onclick*='pobierz_zalacznik']"):
        m = re.search(r"""["'](\\?/wiadomosci\\?/pobierz_zalacznik[^"']+)["']""", img["onclick"])
        row = img.find_parent("tr")
        name_td = row.find("td") if row else None
        if m and name_td is not None:
            attachments.append(MessageAttachment(name=clean(name_td.get_text()),
                                                 download_path=m.group(1).replace("\\/", "/")))
    return Message(
        librus_id=librus_id,
        sender=sender,
        sender_role=role,
        subject=clean(fields["Temat"].get_text()) if "Temat" in fields else "",
        sent_at=datetime.fromisoformat(clean(fields["Wysłano"].get_text())),
        content=html_to_text(content),
        content_md=html_to_md(content),
        attachments=tuple(attachments),
    )


# --------------------------------------------------------------------------- oceny

# Nagłówki kolumn tabeli ocen (drugi wiersz thead) -> (okres, typ oceny); None = pomiń.
_GRADE_COLUMNS: dict[str, tuple[int, GradeType] | None] = {
    "(I)": (1, GradeType.PERIOD_PROPOSED),
    "I": (1, GradeType.PERIOD),
    "(II)": (2, GradeType.PERIOD_PROPOSED),
    "II": (2, GradeType.PERIOD),
    "(R)": (2, GradeType.FINAL_PROPOSED),
    "R": (2, GradeType.FINAL),
}


def _grade_columns(table: Tag) -> list[tuple[int, GradeType] | None]:
    """Mapowanie kolumn z ocenami (od trzeciej) na (okres, typ)."""
    rows = table.select("thead > tr")
    cols: list[tuple[int, GradeType] | None] = []
    regular_seen = 0
    for td in rows[1].find_all("td", recursive=False):
        label = clean(td.get_text())
        if label == "Oceny bieżące":
            regular_seen += 1
            cols.append((regular_seen, GradeType.REGULAR))
        else:
            cols.append(_GRADE_COLUMNS.get(label))
    return cols


def parse_grades(html: str) -> list[Grade]:
    soup = _soup(html)
    table = next((t for t in soup.select("table.decorated.stretch")
                  if t.select_one("thead") and "Przedmiot" in t.select_one("thead").get_text()), None)
    if table is None:
        raise ValueError("nie znaleziono tabeli ocen")
    cols = _grade_columns(table)
    out = []
    for tr in table.find("tbody", recursive=False).find_all("tr", recursive=False):
        if tr.get("id"):  # rozwinięte szczegóły przedmiotu - duplikat danych
            continue
        tds = tr.find_all("td", recursive=False)
        if len(tds) != len(cols) + 2:
            if tr.select("span.grade-box"):
                log.warning("pominięto wiersz ocen o nieznanym układzie: %s", clean(tr.get_text())[:80])
            continue
        subject = clean(tds[1].get_text())
        for td, col in zip(tds[2:], cols):
            if col is None:
                continue
            period, grade_type = col
            for a in td.select("span.grade-box a"):
                out.append(_grade_from_link(a, subject, period, grade_type))
    return out


def _grade_from_link(a: Tag, subject: str, period: int, grade_type: GradeType) -> Grade:
    librus_id = _int(a.get("href", "").rsplit("/", 1)[-1])
    t = _tooltip(a.get("title", ""))
    return Grade(
        librus_id=librus_id,
        subject=subject,
        period=period,
        grade_type=grade_type,
        value=clean(a.get_text()),
        category=t.get("Kategoria", ""),
        date=_date(t.get("Data", "")),
        teacher=t.get("Nauczyciel", ""),
        added_by=t.get("Dodał", ""),
        counts_to_average=_yes_no(t.get("Licz do średniej")),
        weight=_int(t.get("Waga")),
        comment=t.get("Komentarz") or None,
        improvement_of=t.get("Poprawa oceny") or None,
    )


# --------------------------------------------------------------------------- uwagi

_NOTE_TYPES = {"pozytywna": NoteType.POSITIVE, "negatywna": NoteType.NEGATIVE}


def parse_notes(html: str) -> list[Note]:
    out = []
    for table in _soup(html).select("table.decorated"):
        heads = [clean(td.get_text()) for td in table.select("thead td")]
        if heads[:1] != ["Uwaga"]:
            continue
        idx = {h: i for i, h in enumerate(heads)}
        for tr in table.select("tbody > tr"):
            tds = tr.find_all("td", recursive=False)
            if len(tds) < len(heads):
                continue  # np. wiersz "Brak uwag"

            def col(name: str) -> str:
                return clean(tds[idx[name]].get_text()) if name in idx else ""

            out.append(Note(
                content=html_to_text(tds[idx["Uwaga"]]),
                date=_date(col("Data")),
                teacher=col("Kto dodał"),
                note_type=_NOTE_TYPES.get(col("Rodzaj uwagi").lower(), NoteType.NEUTRAL),
                category=col("Kategoria") or None,
            ))
    return out


# --------------------------------------------------------------------------- terminarz

_DETAIL_ID = re.compile(r"/terminarz/szczegoly(?:_wolne)?/(\d+)")
_SUBST = re.compile(r"^(Zastępstwo z|Odwołane zajęcia)\s+(.+?)\s+na lekcji nr:\s*(\d+)\s*\((.+)\)$")
_TIME_RANGE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
_TIME = re.compile(r"(\d{1,2}:\d{2})")


def _time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def calendar_month(html: str) -> tuple[int, int]:
    """(rok, miesiąc) wybrany na stronie terminarza."""
    soup = _soup(html)
    m = soup.select_one("select[name=miesiac] option[selected]")
    r = soup.select_one("select[name=rok] option[selected]")
    if not (m and r):
        raise ValueError("nie rozpoznano miesiąca terminarza")
    return int(r["value"]), int(m["value"])


def parse_calendar(html: str) -> list[CalendarEvent]:
    year, month = calendar_month(html)
    out = []
    for day in _soup(html).select("div.kalendarz-dzien"):
        num = day.select_one("div.kalendarz-numer-dnia")
        if num is None or not clean(num.get_text()).isdigit():
            continue
        d = date(year, month, int(clean(num.get_text())))
        for td in day.select("td"):
            ev = _calendar_cell(td, d)
            if ev is not None:
                out.append(ev)
    return out


def _calendar_cell(td: Tag, d: date) -> CalendarEvent | None:
    lines = [clean(BeautifulSoup(x, "lxml").get_text()) for x in _BR_SPLIT.split(td.decode_contents())]
    lines = [x for x in lines if x]
    if not lines:
        return None
    one_line = " ".join(lines)
    m_id = _DETAIL_ID.search(td.get("onclick", ""))
    librus_id = int(m_id.group(1)) if m_id else None

    # dzień wolny: "Wszystkich Świętych: SP1"
    if "szczegoly_wolne" in td.get("onclick", ""):
        title, _, school = one_line.rpartition(":")
        return CalendarEvent(librus_id=librus_id, date=d, category=CalendarEventCategory.DAY_OFF,
                             title=clean(title) or one_line, class_name=clean(school) or None)

    # zastępstwo / odwołane zajęcia
    m = _SUBST.match(one_line)
    if m:
        category = (CalendarEventCategory.SUBSTITUTION if m.group(1).startswith("Zastępstwo")
                    else CalendarEventCategory.CANCELLED_LESSON)
        return CalendarEvent(librus_id=librus_id, date=d, category=category, teacher=m.group(2),
                             lesson_number=int(m.group(3)), subject=clean(m.group(4)))

    # wpis z podpowiedzią: wydarzenie albo wywiadówka
    tip = td.get("title", "")
    tip_fields = _tooltip(tip)
    ev: dict = dict(librus_id=librus_id, date=d, category=CalendarEventCategory.EVENT,
                    teacher=tip_fields.get("Nauczyciel") or None)
    if "Data dodania" in tip_fields:
        ev["added_at"] = datetime.fromisoformat(tip_fields["Data dodania"])
    desc_html = _tooltip_description(tip)
    if desc_html is not None:
        ev["description"] = html_to_text(desc_html) or None
        ev["description_md"] = html_to_md(desc_html) or None

    subject_span = td.select_one("span.przedmiot")
    rest = []
    for line in lines:
        if line.startswith("Nr lekcji:"):
            ev["lesson_number"] = _int(line)
        elif line.startswith("Czas:") and (tr := _TIME_RANGE.search(line)):
            ev["time_from"], ev["time_to"] = _time(tr.group(1)), _time(tr.group(2))
        elif line.startswith("godz.:") and (t1 := _TIME.search(line)):
            ev["time_from"] = _time(t1.group(1))
        elif line.startswith("Sala:"):
            ev["room"] = clean(line.partition(":")[2]) or None
        elif line.startswith("Wywiadówka:"):
            ev["category"] = CalendarEventCategory.PARENT_MEETING
            ev["event_type"] = "Wywiadówka"
            ev["title"] = clean(line.partition(":")[2]) or None
        elif subject_span is not None and line.startswith(clean(subject_span.get_text())) and "subject" not in ev:
            ev["subject"] = clean(subject_span.get_text())
            ev["event_type"] = clean(line[len(ev["subject"]):].lstrip(" ,")) or None
        else:
            rest.append(line)
    # pozostałe linie: rodzaj wpisu bez przedmiotu (np. "Konsultacje"), potem klasa ("8a SP1")
    if rest and "event_type" not in ev:
        ev["event_type"] = rest.pop(0)
    if rest:
        ev["class_name"] = rest.pop(0)
    if rest:
        log.debug("terminarz %s: nierozpoznane linie %s", d, rest)
    return CalendarEvent(**ev)


def _tooltip_description(title: str) -> str | None:
    """Fragment HTML po 'Opis:' aż do 'Data dodania:' (opis bywa wielolinijkowy)."""
    m = re.search(r"Opis:\s*(.*?)(?:<br\s*/?>\s*Data dodania:|$)", title or "", re.S | re.I)
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------------- frekwencja

def _attendance_type(label: str) -> AttendanceType:
    s = label.lower()
    if "uspr" in s:
        return AttendanceType.EXCUSED_ABSENCE
    if s.startswith("nieobecn"):
        return AttendanceType.ABSENCE
    if s.startswith("spóźn"):
        return AttendanceType.LATE
    if s.startswith("zwoln"):
        return AttendanceType.RELEASED
    if s.startswith("obecn"):
        return AttendanceType.PRESENCE
    return AttendanceType.OTHER


def parse_attendance(html: str) -> list[AttendanceRecord]:
    out = []
    for a in _soup(html).select("table.center.big.decorated p.box a.ocena"):
        m = re.search(r"/przegladaj_nb/szczegoly/(\d+)", a.get("onclick", ""))
        if not m:
            continue
        t = _tooltip(a.get("title", ""))
        label = t.get("Rodzaj", "")
        out.append(AttendanceRecord(
            librus_id=int(m.group(1)),
            date=_date(t.get("Data", "")),
            lesson_number=_int(t.get("Godzina lekcyjna")) or 0,
            subject=t.get("Lekcja", ""),
            attendance_type=_attendance_type(label),
            type_label=label,
            symbol=clean(a.get_text()),
            category=t.get("Kategoria") or None,
            teacher=t.get("Nauczyciel", ""),
            added_by=t.get("Dodał", ""),
            is_trip=_yes_no(t.get("Czy wycieczka")),
        ))
    return out
