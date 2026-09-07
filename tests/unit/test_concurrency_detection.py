"""Tests for explicit book-audit repair concurrency settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.core.config import Settings


def test_repair_concurrency_defaults_to_serial() -> None:
    settings = Settings(_env_file=None)

    assert settings.long_book_audit_repair_concurrency == 1


def test_repair_concurrency_accepts_upper_bound() -> None:
    settings = Settings(_env_file=None, long_book_audit_repair_concurrency=8)

    assert settings.long_book_audit_repair_concurrency == 8


def test_repair_concurrency_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, long_book_audit_repair_concurrency=0)
