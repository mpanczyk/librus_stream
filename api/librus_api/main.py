"""Serwis API (FastAPI) udostępniający zapisane wpisy - na razie szkielet."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI

DB_PATH = os.environ.get("DATABASE_PATH", "/data/librus.sqlite")

app = FastAPI(title="Librus Stream API")


@app.get("/health")
def health() -> dict:
    db = Path(DB_PATH)
    status = {"status": "ok", "database": "missing"}
    if db.exists():
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entries'").fetchone()
        status["database"] = "ready" if has_table else "empty"
    return status
