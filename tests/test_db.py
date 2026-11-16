from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

import pytest

from librus_fetcher.db import connect, get_entries, known_ids, persist
from librus_fetcher.models import (
    Announcement, AttendanceRecord, AttendanceType, CalendarEvent, CalendarEventCategory, EntryKind,
    Grade, Message, Note, NoteType,
)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db" / "librus.sqlite")
    yield c
    c.close()


def rows(conn, sql="SELECT id, student, kind, superseded_by FROM entries ORDER BY id"):
    return conn.execute(sql).fetchall()


def ann(**kw):
    base = dict(title="Tytuł", author="Alfa Testowa", published_on=date(2026, 1, 15),
                content="Treść", content_md="Treść")
    return Announcement(**(base | kw))


def msg(librus_id, **kw):
    base = dict(librus_id=librus_id, sender="Fikcyjny Delta", sender_role="Nauczyciel", subject="Wycieczka",
                sent_at=datetime(2026, 1, 15, 12, 0, 0), content="Treść", content_md="Treść")
    return Message(**(base | kw))


def grade(librus_id, **kw):
    base = dict(librus_id=librus_id, subject="matematyka", period=1, value="5", category="kartkówka",
                date=date(2026, 10, 2), teacher="X Y", added_by="X Y")
    return Grade(**(base | kw))


def absence(librus_id, **kw):
    base = dict(librus_id=librus_id, date=date(2026, 10, 8), lesson_number=2, subject="fizyka",
                attendance_type=AttendanceType.ABSENCE, type_label="nieobecność", symbol="nb",
                teacher="X Y", added_by="X Y")
    return AttendanceRecord(**(base | kw))


# ------------------------------------------------------------------ deduplikacja

def test_insert_is_idempotent(conn):
    note = Note(content="Pochwała", date=date(2026, 9, 8), teacher="Makieta Zeta", note_type=NoteType.POSITIVE)
    assert persist(conn, "Alfa", [note]).inserted == 1
    assert persist(conn, "Alfa", [note]).unchanged == 1
    # uwagi są osobiste - ta sama treść u innego dziecka to osobny wpis
    assert persist(conn, "Beta", [note]).inserted == 1
    assert [r[1] for r in rows(conn)] == ["Alfa", "Beta"]


def test_announcements_always_shared(conn):
    persist(conn, "Alfa", [ann()])
    persist(conn, "Beta", [ann()])
    assert rows(conn) == [(1, None, "announcement", None)]
    sources = conn.execute("SELECT student FROM entry_sources ORDER BY student").fetchall()
    assert sources == [("Alfa",), ("Beta",)]


def test_row_format(conn):
    persist(conn, "Alfa", [ann()])
    content, created_at = conn.execute("SELECT content, created_at FROM entries").fetchone()
    assert isinstance(content, bytes) and json.loads(content)["title"] == "Tytuł"
    assert datetime.fromisoformat(created_at).tzinfo is not None  # ISO 8601 z offsetem


def test_kind_check_constraint(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO entries (student, kind, content, fingerprint, share_fingerprint, created_at) "
                     "VALUES (NULL, 'bogus', x'00', 'f', 'f', '2026-01-01T00:00:00+00:00')")


# ------------------------------------------------------------------ wykrywanie wpisów wspólnych

def test_repeated_message_becomes_shared(conn):
    """Ta sama wiadomość u trojga dzieci (różne librus_id) -> jeden wiersz, student = NULL."""
    s1 = persist(conn, "Alfa", [msg(1)])
    s2 = persist(conn, "Beta", [msg(2)])
    s3 = persist(conn, "Gamma", [msg(3)])
    assert (s1.inserted, s2.promoted_to_shared, s3.merged_into_shared) == (1, 1, 1)
    assert rows(conn) == [(1, None, "message", None)]
    # każde dziecko zna "swój" identyfikator - wiadomość nie będzie otwierana ponownie
    assert known_ids(conn, EntryKind.MESSAGE, "Beta") == {2}
    assert known_ids(conn, EntryKind.MESSAGE, "Gamma") == {3}
    # ponowny przebieg niczego nie zmienia
    assert persist(conn, "Beta", [msg(2)]).merged_into_shared == 1
    assert len(rows(conn)) == 1


def test_different_messages_stay_personal(conn):
    persist(conn, "Alfa", [msg(1, content="Dla Alfy")])
    persist(conn, "Beta", [msg(2, content="Dla Bety")])
    assert [r[1] for r in rows(conn)] == ["Alfa", "Beta"]


def test_calendar_shared_but_grades_never(conn):
    day_off = dict(date=date(2026, 11, 11), category=CalendarEventCategory.DAY_OFF, title="Święto")
    persist(conn, "Alfa", [CalendarEvent(librus_id=10, **day_off), grade(1)])
    persist(conn, "Beta", [CalendarEvent(librus_id=20, **day_off), grade(1)])
    assert [(r[1], r[2]) for r in rows(conn)] == [(None, "calendar_event"), ("Alfa", "grade"), ("Beta", "grade")]


# ------------------------------------------------------------------ wersjonowanie (superseded_by)

def test_changed_grade_supersedes_old_version(conn):
    persist(conn, "Alfa", [grade(1, value="4"), grade(2)])
    stats = persist(conn, "Alfa", [grade(1, value="5"), grade(2)])
    assert (stats.inserted, stats.superseded, stats.unchanged) == (1, 1, 1)
    assert rows(conn) == [(1, "Alfa", "grade", 3), (2, "Alfa", "grade", None), (3, "Alfa", "grade", None)]
    current = get_entries(conn, current_only=True)
    assert sorted((e.content.librus_id, e.content.value) for e in current) == [(1, "5"), (2, "5")]


def test_excused_absence_is_new_version(conn):
    persist(conn, "Alfa", [absence(7)])
    persist(conn, "Alfa", [absence(7, attendance_type=AttendanceType.EXCUSED_ABSENCE,
                                     type_label="nieobecność uspr.", symbol="u")])
    assert rows(conn) == [(1, "Alfa", "attendance", 2), (2, "Alfa", "attendance", None)]


def test_same_grade_id_other_child_does_not_supersede(conn):
    persist(conn, "Alfa", [grade(1, value="4")])
    persist(conn, "Beta", [grade(1, value="5")])
    assert [r[3] for r in rows(conn)] == [None, None]


def test_edited_shared_announcement(conn):
    persist(conn, "Alfa", [ann()])
    persist(conn, "Beta", [ann()])
    persist(conn, "Alfa", [ann(content="Treść poprawiona", content_md="Treść poprawiona")])
    persist(conn, "Beta", [ann(content="Treść poprawiona", content_md="Treść poprawiona")])
    assert rows(conn) == [(1, None, "announcement", 2), (2, None, "announcement", None)]


def test_still_present_entry_is_not_superseded(conn):
    """Druga uwaga tego samego dnia od tego samego nauczyciela nie zastępuje pierwszej."""
    n1 = Note(content="Pierwsza", date=date(2026, 9, 8), teacher="T", note_type=NoteType.POSITIVE)
    n2 = Note(content="Druga", date=date(2026, 9, 8), teacher="T", note_type=NoteType.POSITIVE)
    persist(conn, "Alfa", [n1])
    persist(conn, "Alfa", [n1, n2])
    assert [r[3] for r in rows(conn)] == [None, None]


def test_edited_note_supersedes(conn):
    n1 = Note(content="Pierwsza", date=date(2026, 9, 8), teacher="T", note_type=NoteType.POSITIVE)
    persist(conn, "Alfa", [n1])
    persist(conn, "Alfa", [n1.model_copy(update={"content": "Pierwsza (poprawiona)"})])
    assert [r[3] for r in rows(conn)] == [2, None]


def test_stored_entry_model(conn):
    persist(conn, "Alfa", [grade(1, value="4")])
    persist(conn, "Alfa", [grade(1, value="5")])
    old, new = get_entries(conn)
    assert old.superseded_by == new.id and new.superseded_by is None
    assert isinstance(new.content, Grade) and new.kind is EntryKind.GRADE
    assert new.model_dump(mode="json")["content"]["value"] == "5"
