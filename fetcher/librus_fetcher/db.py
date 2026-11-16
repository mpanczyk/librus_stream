"""Baza SQLite z wpisami.

Tabela `entries` - jeden wiersz na wersję wpisu:
  id                - klucz techniczny
  student           - nazwa dziecka (accounts[].name) albo NULL dla wpisów wspólnych
  kind              - EntryKind (tekst po angielsku)
  content           - JSON wpisu zapisany jako BLOB (UTF-8)
  fingerprint       - SHA-256 całej treści; (kind, student, fingerprint) jest unikalne
  share_fingerprint - SHA-256 treści bez pól zależnych od konta (wykrywanie wpisów wspólnych)
  created_at        - moment dodania wpisu do bazy, ISO 8601 z offsetem (UTC)
  superseded_by     - id nowszej wersji tego samego elementu; NULL => wersja aktualna

Tabela pomocnicza `entry_sources` - które dziecko widziało dany wpis i pod jakim identyfikatorem
Librusa. Pozwala (1) nie otwierać ponownie znanych wiadomości, także tych zapisanych jako wspólne,
(2) wiedzieć, komu faktycznie pokazano wpis wspólny, (3) wykrywać nowsze wersje w obrębie dziecka.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Entry, EntryKind, StoredEntry

log = logging.getLogger(__name__)

_KINDS_SQL = ", ".join(f"'{k.value}'" for k in EntryKind)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS entries (
    id                INTEGER PRIMARY KEY,
    student           TEXT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ({_KINDS_SQL})),
    content           BLOB NOT NULL,
    fingerprint       TEXT NOT NULL,
    share_fingerprint TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    superseded_by     INTEGER NULL REFERENCES entries (id)
);
-- NULL w zwykłym UNIQUE nie koliduje z NULL, więc normalizujemy go do ''.
CREATE UNIQUE INDEX IF NOT EXISTS ux_entries_identity
    ON entries (kind, IFNULL(student, ''), fingerprint);
CREATE INDEX IF NOT EXISTS ix_entries_share ON entries (kind, share_fingerprint);
CREATE INDEX IF NOT EXISTS ix_entries_student_kind ON entries (student, kind);
CREATE INDEX IF NOT EXISTS ix_entries_created_at ON entries (created_at);

CREATE TABLE IF NOT EXISTS entry_sources (
    entry_id      INTEGER NOT NULL REFERENCES entries (id) ON DELETE CASCADE,
    student       TEXT NOT NULL,
    librus_id     INTEGER NULL,
    identity_key  TEXT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (entry_id, student)
);
CREATE INDEX IF NOT EXISTS ix_sources_identity ON entry_sources (student, identity_key);
CREATE INDEX IF NOT EXISTS ix_sources_librus_id ON entry_sources (student, librus_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")  # API czyta równolegle z zapisem fetchera
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


@dataclass
class PersistStats:
    inserted: int = 0
    unchanged: int = 0
    promoted_to_shared: int = 0   # wpis dziecka stał się wspólny, bo powtórzył się u innego
    merged_into_shared: int = 0   # wpis pasował do istniejącego wpisu wspólnego/innego dziecka
    superseded: int = 0           # starsze wersje oznaczone superseded_by

    def __iadd__(self, other: "PersistStats") -> "PersistStats":
        for f in self.__dataclass_fields__:
            setattr(self, f, getattr(self, f) + getattr(other, f))
        return self


def persist(conn: sqlite3.Connection, student: str, entries: Iterable[Entry]) -> PersistStats:
    """Zapisuje wpisy zebrane w jednym przebiegu dla jednego dziecka (jedna transakcja).

    Algorytm dla każdego wpisu:
      1. ta sama treść już jest (u tego dziecka albo jako wspólna)    -> tylko odnotuj źródło,
      2. typ `shareable` i ta sama treść (bez pól zależnych od konta)
         jest u innego dziecka                                        -> wpis staje się wspólny
                                                                         (student = NULL), bez duplikatu,
      3. w przeciwnym razie                                           -> nowy wiersz; jeżeli dziecko
         widziało wcześniej element o tym samym identity_key, którego
         treści nie ma już w bieżącym przebiegu, i dopasowanie jest
         jednoznaczne                                                 -> stary.superseded_by = nowy.id
    """
    entries = list(entries)
    stats = PersistStats()
    now = now_iso()
    # Bieżący stan dziecka - do weryfikacji, czy stara wersja zniknęła ze strony.
    current_fps = {(e.kind, e.fingerprint()) for e in entries}
    identity_counts = Counter((e.kind, e.identity_key()) for e in entries if e.identity_key())

    with conn:
        for e in entries:
            fp, share_fp, ident = e.fingerprint(), e.share_fingerprint(), e.identity_key()
            librus_id = getattr(e, "librus_id", None)
            own = None if e.shared else student

            # 1. identyczny wpis już zapisany
            row = conn.execute(
                "SELECT id FROM entries WHERE kind = ? AND fingerprint = ? "
                "AND (student IS NULL OR student IS ?) ORDER BY student IS NULL DESC LIMIT 1",
                (e.kind.value, fp, own)).fetchone()
            if row:
                _add_source(conn, row[0], student, librus_id, ident, now)
                stats.unchanged += 1
                continue

            # 2. ten sam wpis u innego dziecka -> wspólny
            if e.shareable:
                row = conn.execute(
                    "SELECT id, student FROM entries WHERE kind = ? AND share_fingerprint = ? "
                    "AND superseded_by IS NULL AND (student IS NULL OR student IS NOT ?) "
                    "ORDER BY student IS NULL DESC, id LIMIT 1",
                    (e.kind.value, share_fp, student)).fetchone()
                if row:
                    entry_id, owner = row
                    if owner is not None:
                        conn.execute("UPDATE entries SET student = NULL WHERE id = ?", (entry_id,))
                        stats.promoted_to_shared += 1
                        log.info("wpis %s (%s) powtarza się u %s i %s -> wspólny",
                                 entry_id, e.kind.value, owner, student)
                    else:
                        stats.merged_into_shared += 1
                    _add_source(conn, entry_id, student, librus_id, ident, now)
                    continue

            # 3. nowy wpis (ew. nowa wersja istniejącego)
            cur = conn.execute(
                "INSERT INTO entries (student, kind, content, fingerprint, share_fingerprint, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (own, e.kind.value, e.model_dump_json().encode("utf-8"), fp, share_fp, now))
            new_id = cur.lastrowid
            _add_source(conn, new_id, student, librus_id, ident, now)
            stats.inserted += 1

            if ident and identity_counts[(e.kind, ident)] == 1:
                stats.superseded += _supersede(conn, e.kind, student, ident, new_id, current_fps)
    return stats


def _supersede(conn: sqlite3.Connection, kind: EntryKind, student: str, ident: str, new_id: int,
               current_fps: set[tuple[EntryKind, str]]) -> int:
    candidates = conn.execute(
        "SELECT e.id, e.fingerprint FROM entries e JOIN entry_sources s ON s.entry_id = e.id "
        "WHERE s.student = ? AND e.kind = ? AND s.identity_key = ? "
        "AND e.superseded_by IS NULL AND e.id != ?",
        (student, kind.value, ident, new_id)).fetchall()
    gone = [cid for cid, cfp in candidates if (kind, cfp) not in current_fps]
    if len(gone) != 1:
        if len(gone) > 1:
            log.warning("niejednoznaczna nowa wersja %s (%s): kandydaci %s - pomijam", new_id, kind, gone)
        return 0
    conn.execute("UPDATE entries SET superseded_by = ? WHERE id = ?", (new_id, gone[0]))
    log.info("wpis %s (%s) zastąpiony przez %s", gone[0], kind.value, new_id)
    return 1


def _add_source(conn: sqlite3.Connection, entry_id: int, student: str, librus_id: int | None,
                ident: str | None, now: str) -> None:
    conn.execute(
        "INSERT INTO entry_sources (entry_id, student, librus_id, identity_key, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (entry_id, student) DO UPDATE SET last_seen_at = excluded.last_seen_at",
        (entry_id, student, librus_id, ident, now, now))


def known_ids(conn: sqlite3.Connection, kind: EntryKind, student: str) -> set[int]:
    """Identyfikatory Librusa wpisów, które dziecko już widziało (w tym zapisanych jako wspólne)."""
    rows = conn.execute(
        "SELECT DISTINCT s.librus_id FROM entry_sources s JOIN entries e ON e.id = s.entry_id "
        "WHERE s.student = ? AND e.kind = ? AND s.librus_id IS NOT NULL",
        (student, kind.value))
    return {r[0] for r in rows}


def get_entries(conn: sqlite3.Connection, *, current_only: bool = False) -> list[StoredEntry]:
    sql = "SELECT id, student, kind, content, created_at, superseded_by FROM entries"
    if current_only:
        sql += " WHERE superseded_by IS NULL"
    return [StoredEntry.from_row(r) for r in conn.execute(sql + " ORDER BY id")]
