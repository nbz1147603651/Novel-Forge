"""Regression tests for local Engine revision guarding."""

from __future__ import annotations

import pytest

from novel_forge.common import runtime_identity


def test_revision_guard_rejects_new_work_after_source_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_identity, "_boot_revision", "src-before")
    monkeypatch.setattr(runtime_identity, "current_engine_revision", lambda: "src-after")

    with pytest.raises(runtime_identity.EngineRestartRequiredError) as error:
        runtime_identity.require_engine_revision_current()

    assert error.value.boot_revision == "src-before"
    assert error.value.current_revision == "src-after"


def test_revision_guard_keeps_current_runtime_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_identity, "_boot_revision", "src-current")
    monkeypatch.setattr(runtime_identity, "current_engine_revision", lambda: "src-current")

    runtime_identity.require_engine_revision_current()
