"""Konfiguracja z pliku YAML (patrz config.example.yaml)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator

DEFAULT_CONFIG_PATH = "/config/config.yaml"


class Account(BaseModel):
    name: str = Field(min_length=1, description="Etykieta dziecka, trafia do kolumny `student`.")
    login: str = Field(min_length=1)
    password: SecretStr


class FetcherSettings(BaseModel):
    # Strona portalu, od której zaczyna się logowanie (przycisk "LIBRUS Synergia" -> "Zaloguj").
    portal_url: str = "https://portal.librus.pl/rodzina"
    # Bazowy adres dziennika (podstrony /ogloszenia, /wiadomosci, ...).
    base_url: str = "https://synergia.librus.pl"
    interval_minutes: int = Field(default=60, ge=1)
    headless: bool = True
    # Pauza między kolejnymi stronami - nie obciążamy infrastruktury dziennika.
    request_delay_seconds: float = Field(default=2.0, ge=0)
    # Liczba kolejnych miesięcy terminarza (poza bieżącym) do sprawdzenia.
    calendar_months_ahead: int = Field(default=1, ge=0)
    # Zwykły User-Agent Chrome; domyślny "HeadlessChrome" jest blokowany przez WAF Librusa.
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    @field_validator("portal_url", "base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")


class DatabaseSettings(BaseModel):
    path: str = "/data/librus.sqlite"


class Config(BaseModel):
    accounts: list[Account] = Field(min_length=1)
    fetcher: FetcherSettings = FetcherSettings()
    database: DatabaseSettings = DatabaseSettings()

    @field_validator("accounts")
    @classmethod
    def _unique_names(cls, v: list[Account]) -> list[Account]:
        names = [a.name for a in v]
        if len(names) != len(set(names)):
            raise ValueError("nazwy kont (accounts[].name) muszą być unikalne")
        return v


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG_PATH))
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Puste sekcje w YAML (same komentarze) dają None - traktujemy jak brak sekcji.
    data = {k: v for k, v in data.items() if v is not None}
    return Config.model_validate(data)
