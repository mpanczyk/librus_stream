from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from librus_fetcher.config import load_config
from librus_fetcher.models import ENTRY_MODELS, EntryKind, Grade, GradeType

ROOT = Path(__file__).resolve().parent.parent


def _grade(**kw):
    base = dict(librus_id=1, subject="matematyka", period=1, value="5", category="kartkówka",
                date=date(2026, 1, 15), teacher="Testowa Alfa", added_by="Testowa Alfa",
                counts_to_average=True, weight=1, comment="Komentarz testowy")
    return Grade(**(base | kw))


def test_every_kind_has_model():
    assert set(ENTRY_MODELS) == set(EntryKind)


def test_fingerprints():
    assert _grade().fingerprint() == _grade().fingerprint()
    assert _grade().fingerprint() != _grade(value="4").fingerprint()
    # ta sama ocena po zmianie -> ten sam element (identity), inna treść
    assert _grade().identity_key() == _grade(value="4").identity_key()
    assert _grade().identity_key() != _grade(librus_id=2).identity_key()
    assert _grade().grade_type is GradeType.REGULAR


def test_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        _grade(unknown="x")


def test_example_and_mock_configs_load():
    cfg = load_config(ROOT / "config.example.yaml")
    assert len(cfg.accounts) == 3
    assert cfg.fetcher.base_url == "https://synergia.librus.pl"
    mock = load_config(ROOT / "config.mock.yaml")
    assert mock.fetcher.base_url == "http://mock-librus:8000"
    assert mock.accounts[0].password.get_secret_value() == "mock-pass-1"
