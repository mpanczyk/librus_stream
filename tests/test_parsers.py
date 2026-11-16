"""Parsery na zanonimizowanych fixtures."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time
from pathlib import Path

import pytest

from librus_fetcher import parsers as P
from librus_fetcher.models import AttendanceType, CalendarEventCategory as Cat, GradeType, NoteType
from librus_fetcher.text import html_to_md, html_to_text

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "synergia" / "account0"


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_announcements():
    items = P.parse_announcements(read("ogloszenia.html"))
    assert len(items) == 113
    a = items[0]
    assert a.published_on == date(2026, 11, 13)
    assert a.author and a.title.startswith("Ogłoszenie testowe")
    assert "\n" in a.content and "<br" not in a.content
    assert "  \n" in a.content_md  # <br> -> twarde złamanie linii w Markdown


def test_message_list_and_pagination():
    p1 = P.parse_message_list(read("wiadomosci.html"))
    p2 = P.parse_message_list(read("wiadomosci_page2.html"))
    assert (p1.page, p1.pages, len(p1.items)) == (1, 2, 50)
    assert (p2.page, p2.pages) == (2, 2) and p2.items
    assert not {i.librus_id for i in p1.items} & {i.librus_id for i in p2.items}
    first = p1.items[0]
    assert first.path == f"/wiadomosci/1/5/{first.librus_id}/f0"
    # fixtures: data i godzina oryginalne, minuty i sekundy zanonimizowane
    assert first.sent_at.replace(minute=0, second=0) == datetime(2026, 11, 12, 18)


def test_all_message_details_parse():
    files = sorted(FIX.glob("wiadomosci_1_5_*_f0.html"))
    assert len(files) == 15
    msgs = [P.parse_message(f.read_text(encoding="utf-8"), int(f.name.split("_")[3])) for f in files]
    assert all(m.sender and m.subject and m.content and m.content_md for m in msgs)
    with_att = [m for m in msgs if m.attachments]
    assert with_att
    att = with_att[0].attachments[0]
    assert att.download_path.startswith(f"/wiadomosci/pobierz_zalacznik/{with_att[0].librus_id}/")
    assert att.name.startswith("zalacznik_")
    roles = Counter(m.sender_role for m in msgs)
    assert roles["Nauczyciel"] or roles["Dyrektor Szkoły"]


def test_sender_split():
    assert P._split_sender("Testowa Alfa (Testowa Alfa) [Nauczyciel]") == ("Testowa Alfa", "Nauczyciel")
    assert P._split_sender("Nowak Jan (Przedstawiciel Szkolnej Rady Rodziców)") == \
        ("Nowak Jan", "Przedstawiciel Szkolnej Rady Rodziców")
    assert P._split_sender("Nowak Jan") == ("Nowak Jan", None)


def test_grades():
    grades = P.parse_grades(read("przegladaj_oceny_uczen.html"))
    assert len(grades) == 45  # bez ukrytej oceny testowej Librusa (id 000000)
    assert all(g.period == 1 and g.grade_type is GradeType.REGULAR for g in grades)
    assert len({g.librus_id for g in grades}) == 45
    g = next(g for g in grades if g.subject == "biologia")
    assert (g.value, g.category, g.counts_to_average, g.weight) == ("6", "kartkówka", True, 1)
    assert g.date == date(2026, 10, 2) and g.comment
    plus = next(g for g in grades if g.improvement_of)
    assert plus.improvement_of == ". (odpowiedź ustna)" and plus.counts_to_average is None
    values = Counter(g.value for g in grades)
    assert values["+"] and values["87"] and values["."]


def test_period_grade_columns(monkeypatch):
    """Oceny śródroczne/roczne trafiają do właściwych kolumn (syntetyczny wiersz)."""
    html = read("przegladaj_oceny_uczen.html")
    link = ('<span class="grade-box"><a class="ocena" href="/przegladaj_oceny/szczegoly/999" '
            'title="Kategoria: śródroczna&lt;br&gt;Data: 2027-01-20 (śr.)&lt;br&gt;Nauczyciel: X Y'
            '&lt;br&gt;Dodał: X Y">5</a></span>')
    # komórka "I" pierwszego przedmiotu: po średniej (img) następuje <td class="center">-</td>
    marker = '<td class="center">\n  -\n </td>'
    import re
    html2, n = re.subn(r'(<td class="center">)\s*-\s*(</td>)', rf"\g<1>{link}\g<2>", html, count=1)
    assert n == 1
    grades = P.parse_grades(html2)
    special = [g for g in grades if g.librus_id == 999]
    assert len(special) == 1 and special[0].grade_type is GradeType.PERIOD and special[0].period == 1


def test_notes():
    notes = P.parse_notes(read("uwagi.html"))
    assert len(notes) == 3
    assert all(n.note_type is NoteType.POSITIVE and n.teacher for n in notes)
    assert notes[0].date == date(2026, 9, 8)


def test_calendar_current_month():
    events = P.parse_calendar(read("terminarz.html"))
    assert len(events) == 39
    cats = Counter(e.category for e in events)
    assert cats == {Cat.EVENT: 18, Cat.SUBSTITUTION: 11, Cat.CANCELLED_LESSON: 7, Cat.DAY_OFF: 2,
                    Cat.PARENT_MEETING: 1}
    assert all(e.date.month == 11 and e.date.year == 2026 for e in events)

    day_off = next(e for e in events if e.category is Cat.DAY_OFF)
    assert (day_off.date, day_off.title, day_off.class_name) == (date(2026, 11, 1), "Wszystkich Świętych", "SP1")

    sub = next(e for e in events if e.category is Cat.SUBSTITUTION)
    assert sub.lesson_number == 5 and sub.subject == "język angielski" and sub.teacher

    test = next(e for e in events if e.event_type == "Kartkówka")
    assert test.subject == "chemia" and test.lesson_number == 7 and test.class_name == "8a SP1"
    assert test.librus_id and test.description
    assert test.added_at.replace(minute=0, second=0) == datetime(2026, 10, 27, 9)

    consult = next(e for e in events if e.event_type == "Konsultacje")
    assert (consult.time_from, consult.time_to, consult.subject) == (time(16, 0), time(18, 45), None)

    meeting = next(e for e in events if e.category is Cat.PARENT_MEETING)
    assert meeting.title == "Zebranie klasowe" and meeting.time_from == time(17, 0)
    assert meeting.room == "12" and meeting.class_name == "8a SP1"

    with_room = [e for e in events if e.room]
    assert with_room


def test_calendar_next_month():
    events = P.parse_calendar(read("terminarz_next_month.html"))
    assert len(events) == 17
    assert all(e.date.month == 12 for e in events)


def test_calendar_multiline_description():
    title = ("Nauczyciel: A B<br />Opis: Sprawdzian<br /><br />Kryteria:<br />1. Pierwsze<br />"
             "2. <b>Drugie</b><br />Data dodania: 2026-01-15 12:00:00")
    desc = P._tooltip_description(title)
    assert html_to_text(desc) == "Sprawdzian\n\nKryteria:\n1. Pierwsze\n2. Drugie"
    assert "**Drugie**" in html_to_md(desc)


def test_attendance():
    recs = P.parse_attendance(read("przegladaj_nb_uczen.html"))
    assert len(recs) == 10
    assert Counter(r.symbol for r in recs) == {"zw": 6, "ks": 4}
    assert all(r.attendance_type is AttendanceType.RELEASED for r in recs)
    ks = next(r for r in recs if r.symbol == "ks")
    assert ks.category == "Konkurs szkolny" and ks.is_trip is False and ks.lesson_number == 2


@pytest.mark.parametrize("label,expected", [
    ("nieobecność", AttendanceType.ABSENCE),
    ("nieobecność uspr.", AttendanceType.EXCUSED_ABSENCE),
    ("spóźnienie", AttendanceType.LATE),
    ("zwolnienie", AttendanceType.RELEASED),
    ("obecność", AttendanceType.PRESENCE),
    ("coś nowego", AttendanceType.OTHER),
])
def test_attendance_type_mapping(label, expected):
    assert P._attendance_type(label) is expected


def test_markdown_conversion():
    html = 'Dzień dobry,<br/>link: <a href="https://example.com/x">strona</a><br/><b>ważne</b>'
    assert html_to_text(html) == "Dzień dobry,\nlink: strona\nważne"
    md = html_to_md(html)
    assert "[strona](https://example.com/x)" in md and "**ważne**" in md
