"""Tests for HumanizePatternHit source field Literal expansion."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.core.schemas.humanize import (
    _LIBRARY_SOURCE_VALUES,
    HumanizePatternHit,
)

_BASE_KWARGS = dict(
    pattern_id="test",
    pattern_name="x",
    category="y",
    severity="high",
    evidence_quote="",
    paragraph_index=0,
    suggestion="",
    confidence=0.9,
    actionable=True,
)


class TestLibrarySourceValues:
    """_LIBRARY_SOURCE_VALUES module-level constant."""

    def test_constant_exists(self) -> None:
        assert _LIBRARY_SOURCE_VALUES == frozenset({"library", "library_user"})

    def test_constant_is_frozenset(self) -> None:
        assert isinstance(_LIBRARY_SOURCE_VALUES, frozenset)


class TestSourceLiteralNewValues:
    """New source values are accepted."""

    def test_source_library(self) -> None:
        hit = HumanizePatternHit(source="library", **_BASE_KWARGS)
        assert hit.source == "library"

    def test_source_library_user(self) -> None:
        hit = HumanizePatternHit(source="library_user", **_BASE_KWARGS)
        assert hit.source == "library_user"


class TestSourceLiteralBackwardCompat:
    """Existing source values still work."""

    def test_source_local(self) -> None:
        hit = HumanizePatternHit(source="local", **_BASE_KWARGS)
        assert hit.source == "local"

    def test_source_llm(self) -> None:
        hit = HumanizePatternHit(source="llm", **_BASE_KWARGS)
        assert hit.source == "llm"

    def test_source_merged(self) -> None:
        hit = HumanizePatternHit(source="merged", **_BASE_KWARGS)
        assert hit.source == "merged"

    def test_source_default_is_llm(self) -> None:
        hit = HumanizePatternHit(**_BASE_KWARGS)
        assert hit.source == "llm"


class TestSourceLiteralRejectsInvalid:
    """Invalid source values are rejected."""

    def test_source_garbage(self) -> None:
        with pytest.raises(ValidationError):
            HumanizePatternHit(source="garbage", **_BASE_KWARGS)

    def test_source_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            HumanizePatternHit(source="", **_BASE_KWARGS)

    def test_source_numeric(self) -> None:
        with pytest.raises(ValidationError):
            HumanizePatternHit(source=123, **_BASE_KWARGS)  # type: ignore[arg-type]
