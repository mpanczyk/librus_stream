"""Punkt wejścia serwisu fetcher.

Co `interval_minutes` wykonuje cykl: dla każdego konta z konfiguracji logowanie, przejście
przez podstrony, parsowanie i zapis nowych wpisów. `--once` - jeden cykl i koniec.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sqlite3
import sys
import threading

from .browser import launch, logged_in
from .config import Config, load_config
from .db import PersistStats, connect, known_ids, persist
from .models import EntryKind
from .scrape import scrape_account

log = logging.getLogger("librus_fetcher")


def run_cycle(cfg: Config, conn: sqlite3.Connection) -> tuple[PersistStats, list[str]]:
    total = PersistStats()
    errors: list[str] = []
    with launch(cfg.fetcher) as browser:
        for account in cfg.accounts:
            log.info("konto %s", account.name)
            try:
                with logged_in(browser, cfg.fetcher, account) as session:
                    result = scrape_account(session, cfg.fetcher,
                                            known_ids(conn, EntryKind.MESSAGE, account.name))
                stats = persist(conn, account.name, result.entries)
                log.info("konto %s: %s", account.name, stats)
                total += stats
                errors += [f"{account.name}: {e}" for e in result.errors]
            except Exception as e:  # noqa: BLE001 - jedno konto nie blokuje pozostałych
                log.exception("konto %s: błąd", account.name)
                errors.append(f"{account.name}: {e}")
    return total, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="librus_fetcher")
    parser.add_argument("--once", action="store_true", help="wykonaj jeden cykl i zakończ")
    parser.add_argument("--config", help="ścieżka do config.yaml (domyślnie $CONFIG_PATH)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    conn = connect(cfg.database.path)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    try:
        while True:
            stats, errors = run_cycle(cfg, conn)
            log.info("cykl zakończony: %s; błędy: %d", stats, len(errors))
            if args.once:
                return 1 if errors else 0
            if stop.wait(cfg.fetcher.interval_minutes * 60):
                return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
