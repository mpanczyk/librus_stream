"""Pełny cykl fetchera dla trojga dzieci względem mock serwisu."""
from __future__ import annotations

from collections import Counter

from librus_fetcher.__main__ import run_cycle
from librus_fetcher.config import Account, Config, DatabaseSettings
from librus_fetcher.db import connect


def test_full_cycle_three_children(tmp_path, settings):
    cfg = Config(
        accounts=[Account(name="Alfa", login="mock_alfa", password="mock-pass-1"),
                  Account(name="Beta", login="mock_beta", password="mock-pass-2"),
                  Account(name="Gamma", login="mock_gamma", password="mock-pass-3")],
        fetcher=settings,
        database=DatabaseSettings(path=str(tmp_path / "librus.sqlite")),
    )
    conn = connect(cfg.database.path)

    stats, errors = run_cycle(cfg, conn)
    assert errors == []
    by = Counter(conn.execute("SELECT kind, IFNULL(student, '*') FROM entries").fetchall())
    # wszystkie konta widzą te same strony mocka:
    assert by[("announcement", "*")] == 113
    assert by[("message", "*")] == 53          # wspólne - powtarzają się u trojga dzieci
    assert by[("calendar_event", "*")] == 39 + 17
    for child in ("Alfa", "Beta", "Gamma"):    # osobiste - nigdy nie są łączone
        assert by[("grade", child)] == 45
        assert by[("note", child)] == 3
        assert by[("attendance", child)] == 10
    assert not any(student == "*" and kind in ("grade", "note", "attendance") for kind, student in by)

    # drugi cykl: nic nowego, wiadomości nie są otwierane ponownie
    stats2, errors2 = run_cycle(cfg, conn)
    assert errors2 == []
    assert stats2.inserted == 0 and stats2.promoted_to_shared == 0
    assert conn.execute("SELECT COUNT(*) FROM entries WHERE superseded_by IS NOT NULL").fetchone()[0] == 0
    sources = conn.execute("SELECT COUNT(DISTINCT student) FROM entry_sources").fetchone()[0]
    assert sources == 3
    conn.close()
