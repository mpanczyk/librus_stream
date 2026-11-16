"""Modele wpisów z dziennika Librus Synergia.

Każdy typ wpisu ma własny model. Do bazy trafia `model.model_dump_json()` (kolumna `content`)
oraz trzy skróty:
  fingerprint()        - cała treść; identyczna treść => ten sam wpis (nie zapisujemy ponownie),
  share_fingerprint()  - treść bez pól zależnych od konta (np. librus_id); ten sam skrót u dwojga
                         dzieci => wpis wspólny (student = NULL), o ile typ jest `shareable`,
  identity_key()       - "który to element" niezależnie od treści (np. librus_id oceny);
                         nowa treść o tym samym kluczu => nowa wersja, stara dostaje superseded_by.

Pola opisane jako "surowa etykieta" zawierają tekst dokładnie tak, jak pokazuje go Librus
(po polsku) - zbiór możliwych wartości nie jest zamknięty i zależy od konfiguracji szkoły.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny


def _sha256(prefix: str, data: Any) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{prefix}:{canonical}".encode()).hexdigest()


class EntryKind(StrEnum):
    """Rodzaj wpisu - wartość kolumny `kind` w bazie."""

    ANNOUNCEMENT = "announcement"      # ogłoszenie (wspólne, student = NULL)
    MESSAGE = "message"                # wiadomość
    GRADE = "grade"                    # ocena
    NOTE = "note"                      # uwaga o dziecku
    CALENDAR_EVENT = "calendar_event"  # wydarzenie w terminarzu
    ATTENDANCE = "attendance"          # wpis o frekwencji (nieobecność, spóźnienie, ...)


class Entry(BaseModel):
    """Wspólna baza wszystkich wpisów."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ClassVar[EntryKind]
    # True -> wpis zawsze dotyczy wszystkich dzieci (student = NULL), np. ogłoszenia.
    shared: ClassVar[bool] = False
    # True -> wpis powtarzający się u kilkorga dzieci staje się wspólny (student = NULL).
    # Wyłączone dla typów z natury osobistych (oceny, frekwencja, uwagi).
    shareable: ClassVar[bool] = False
    # Pola zależne od konta, pomijane przy porównywaniu wpisów między dziećmi.
    account_specific: ClassVar[dict[str, Any]] = {}
    # Pola identyfikujące element przy wykrywaniu nowszych wersji.
    identity_fields: ClassVar[tuple[str, ...]] = ()

    def fingerprint(self) -> str:
        """SHA-256 kanonicznego JSON-a całej treści."""
        return _sha256(self.kind, self.model_dump(mode="json"))

    def share_fingerprint(self) -> str:
        """SHA-256 treści bez pól zależnych od konta."""
        return _sha256(self.kind, self.model_dump(mode="json", exclude=self.account_specific or None))

    def identity_key(self) -> str | None:
        """Skrót pól identyfikujących element; None, gdy typ nie wspiera wersjonowania."""
        if not self.identity_fields:
            return None
        return _sha256(self.kind, {f: self.model_dump(mode="json")[f] for f in self.identity_fields})


# --------------------------------------------------------------------------- ogłoszenia

class Announcement(Entry):
    """Ogłoszenie szkolne (/ogloszenia). Librus nie udostępnia identyfikatora ogłoszenia."""

    kind: ClassVar[EntryKind] = EntryKind.ANNOUNCEMENT
    shared: ClassVar[bool] = True

    identity_fields: ClassVar[tuple[str, ...]] = ("title", "author", "published_on")

    title: str
    author: str                         # "Dodał", np. "Alfa Testowa"
    published_on: date                  # "Data publikacji"
    content: str                        # treść jako tekst (<br> -> "\n")
    content_md: str                     # treść HTML przekształcona do Markdown


# --------------------------------------------------------------------------- wiadomości

class MessageAttachment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str                           # nazwa pliku
    download_path: str                  # np. "/wiadomosci/pobierz_zalacznik/<msg_id>/<file_id>"


class Message(Entry):
    """Wiadomość odebrana (/wiadomosci + /wiadomosci/1/5/<id>/f0).

    Uwaga: otwarcie treści wiadomości oznacza ją w Librusie jako przeczytaną.
    """

    kind: ClassVar[EntryKind] = EntryKind.MESSAGE
    shareable: ClassVar[bool] = True
    account_specific: ClassVar[dict[str, Any]] = {"librus_id": True,
                                                  "attachments": {"__all__": {"download_path"}}}
    identity_fields: ClassVar[tuple[str, ...]] = ("librus_id",)

    librus_id: int
    sender: str                         # np. "Testowa Alfa"
    sender_role: str | None = None      # surowa etykieta w [], np. "Nauczyciel", "Dyrektor Szkoły"
    subject: str
    sent_at: datetime                   # "Wysłano" (czas lokalny szkoły, bez strefy)
    content: str                        # treść jako tekst (<br> -> "\n")
    content_md: str                     # treść HTML przekształcona do Markdown
    attachments: tuple[MessageAttachment, ...] = ()


# --------------------------------------------------------------------------- oceny

class GradeType(StrEnum):
    REGULAR = "regular"                       # ocena bieżąca
    PERIOD_PROPOSED = "period_proposed"       # przewidywana śródroczna
    PERIOD = "period"                         # śródroczna (I / II)
    FINAL_PROPOSED = "final_proposed"         # przewidywana roczna
    FINAL = "final"                           # roczna (R)


class Grade(Entry):
    """Ocena (/przegladaj_oceny/uczen). Wszystkie dane są na liście - bez podstron."""

    kind: ClassVar[EntryKind] = EntryKind.GRADE
    identity_fields: ClassVar[tuple[str, ...]] = ("librus_id",)

    librus_id: int
    subject: str                        # przedmiot, np. "matematyka"
    period: int = Field(ge=1)           # okres (semestr): 1 lub 2
    grade_type: GradeType = GradeType.REGULAR
    value: str                          # surowa wartość: "5", "5+", "6=", "+", "-", ".", "87", "np"
    category: str                       # surowa etykieta, np. "kartkówka", "sprawdzian", "aktywność"
    date: date
    teacher: str
    added_by: str
    counts_to_average: bool | None = None   # "Licz do średniej" (brak dla ocen typu "+")
    weight: int | None = None
    comment: str | None = None
    improvement_of: str | None = None   # "Poprawa oceny", np. ". (odpowiedź ustna)"


# --------------------------------------------------------------------------- uwagi

class NoteType(StrEnum):
    POSITIVE = "positive"               # pozytywna
    NEGATIVE = "negative"               # negatywna
    NEUTRAL = "neutral"                 # neutralna / inna


class Note(Entry):
    """Uwaga o dziecku (/uwagi). Librus nie udostępnia identyfikatora uwagi."""

    kind: ClassVar[EntryKind] = EntryKind.NOTE
    # Brak identyfikatora - kilka uwag z tego samego dnia od tego samego nauczyciela
    # daje niejednoznaczny klucz i wtedy wersjonowanie jest pomijane (patrz store.py).
    identity_fields: ClassVar[tuple[str, ...]] = ("date", "teacher", "note_type", "category")

    content: str
    date: date
    teacher: str                        # "Kto dodał"
    note_type: NoteType                 # "Rodzaj uwagi"
    category: str | None = None         # surowa etykieta "Kategoria" (często pusta)


# --------------------------------------------------------------------------- terminarz

class CalendarEventCategory(StrEnum):
    EVENT = "event"                     # wpis nauczyciela: sprawdzian, kartkówka, wycieczka, ...
    DAY_OFF = "day_off"                 # dzień wolny / święto
    SUBSTITUTION = "substitution"       # zastępstwo
    CANCELLED_LESSON = "cancelled_lesson"   # odwołane zajęcia
    PARENT_MEETING = "parent_meeting"   # wywiadówka / zebranie z rodzicami


class CalendarEvent(Entry):
    """Wpis w terminarzu (/terminarz, miesiąc bieżący + kolejne).

    Dane pochodzą z kafelka dnia i jego podpowiedzi (title) - podstrony szczegółów
    nie wnoszą nic ponad to.
    """

    kind: ClassVar[EntryKind] = EntryKind.CALENDAR_EVENT
    shareable: ClassVar[bool] = True
    account_specific: ClassVar[dict[str, Any]] = {"librus_id": True}

    librus_id: int | None = None        # tylko wpisy z linkiem do szczegółów
    date: date
    category: CalendarEventCategory
    title: str | None = None            # np. nazwa święta, "Zebranie klasowe"
    event_type: str | None = None       # surowa etykieta, np. "Sprawdzian", "Kartkówka", "Wycieczka"
    subject: str | None = None
    lesson_number: int | None = None    # "Nr lekcji"
    time_from: time | None = None       # "Czas: 16:00 - 18:45" / "godz.: 17:00"
    time_to: time | None = None
    teacher: str | None = None          # autor wpisu albo nauczyciel zastępowany / odwołany
    description: str | None = None      # "Opis" jako tekst
    description_md: str | None = None   # "Opis" przekształcony do Markdown
    room: str | None = None             # "Sala"
    class_name: str | None = None       # np. "8a SP1"
    added_at: datetime | None = None    # "Data dodania"

    def identity_key(self) -> str | None:
        if self.librus_id is not None:
            return _sha256(self.kind, {"librus_id": self.librus_id})
        return _sha256(self.kind, self.model_dump(
            mode="json", include={"date", "category", "lesson_number", "subject", "title", "class_name"}))


# --------------------------------------------------------------------------- frekwencja

class AttendanceType(StrEnum):
    ABSENCE = "absence"                     # nieobecność (nb)
    EXCUSED_ABSENCE = "excused_absence"     # nieobecność usprawiedliwiona (u)
    LATE = "late"                           # spóźnienie (sp)
    RELEASED = "released"                   # zwolnienie (zw, a także np. "ks" - konkurs szkolny)
    PRESENCE = "presence"                   # obecność (zwykle niewidoczna na liście)
    OTHER = "other"                         # inna, nieznana etykieta - patrz type_label


class AttendanceRecord(Entry):
    """Wpis frekwencji (/przegladaj_nb/uczen). Librus pokazuje tylko wpisy inne niż obecność."""

    kind: ClassVar[EntryKind] = EntryKind.ATTENDANCE
    identity_fields: ClassVar[tuple[str, ...]] = ("librus_id",)

    librus_id: int
    date: date
    lesson_number: int                  # "Godzina lekcyjna"
    subject: str                        # "Lekcja"
    attendance_type: AttendanceType
    type_label: str                     # surowa etykieta "Rodzaj", np. "zwolnienie"
    symbol: str                         # skrót w tabeli: "nb", "u", "sp", "zw", "ks", ...
    category: str | None = None         # surowa etykieta "Kategoria", np. "Konkurs szkolny"
    teacher: str
    added_by: str
    is_trip: bool | None = None         # "Czy wycieczka"


ENTRY_MODELS: dict[EntryKind, type[Entry]] = {
    m.kind: m for m in (Announcement, Message, Grade, Note, CalendarEvent, AttendanceRecord)
}


# --------------------------------------------------------------------------- wiersz bazy

class StoredEntry(BaseModel):
    """Wiersz tabeli `entries`: wpis wraz z metadanymi zapisu."""

    model_config = ConfigDict(frozen=True)

    id: int
    student: str | None                 # None => wpis wspólny dla wszystkich dzieci
    kind: EntryKind
    content: SerializeAsAny[Entry]   # konkretny model wg kind (Announcement, Grade, ...)
    created_at: datetime                # moment dodania do bazy (ISO 8601, UTC)
    superseded_by: int | None = None    # id nowszej wersji; None => wpis aktualny

    @classmethod
    def from_row(cls, row: tuple) -> "StoredEntry":
        id_, student, kind, content, created_at, superseded_by = row
        kind = EntryKind(kind)
        entry = ENTRY_MODELS[kind].model_validate_json(content)
        return cls(id=id_, student=student, kind=kind, content=entry,
                   created_at=datetime.fromisoformat(created_at), superseded_by=superseded_by)
