"""Tests for narrative element library maintenance validation."""

from __future__ import annotations

from novel_forge.pipeline.steps.blueprint_element_select.validation import validate_element_library


def test_blueprint_element_library_validation_passes() -> None:
    report = validate_element_library()

    assert report.ok, [issue.message for issue in report.errors]
    assert report.total_elements >= 50
    assert report.required_count == 6
    assert report.builtin_count >= 50
