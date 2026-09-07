"""Tests for humanize_library_dashboard table model (I-4).

The dashboard previously used ``QTableWidget`` which allocates a widget per row
and degrades significantly at 1000+ entries. The refactor switches to
``QTableView`` + a custom ``QAbstractTableModel`` so only visible rows are
materialized (delegates paint badges / actions on demand).

These tests guard the structural contract — they fail if anyone reintroduces
``QTableWidget`` into the module or removes the required model methods.
"""
from __future__ import annotations

import inspect

import pytest
from PySide6.QtCore import Qt

pytestmark = pytest.mark.desktop


def test_humanize_dashboard_uses_model_not_widget() -> None:
    """humanize_library_dashboard should use QTableView + QAbstractTableModel, not QTableWidget."""
    from novel_forge.desktop.pages.standalone import humanize_library_dashboard as hld

    source = inspect.getsource(hld)
    assert "QTableWidget" not in source, (
        "humanize_library_dashboard should not use QTableWidget; "
        "use QTableView + QAbstractTableModel instead (I-4)."
    )
    assert "QAbstractTableModel" in source, (
        "humanize_library_dashboard should define a QAbstractTableModel subclass."
    )
    assert "QTableView" in source, (
        "humanize_library_dashboard should use QTableView (not QTableWidget)."
    )


def test_humanize_model_has_required_methods() -> None:
    """The new model class should implement rowCount, columnCount, data, headerData."""
    from novel_forge.desktop.pages.standalone import humanize_library_dashboard as hld

    model_cls = None
    for name in dir(hld):
        obj = getattr(hld, name)
        if (
            inspect.isclass(obj)
            and "Model" in name
            and obj.__module__ == hld.__name__
            # Must subclass QAbstractTableModel (not unrelated "Model" classes)
            and any(
                base.__name__ == "QAbstractTableModel" for base in obj.__mro__
            )
        ):
            model_cls = obj
            break

    assert model_cls is not None, (
        "No QAbstractTableModel subclass found in humanize_library_dashboard."
    )
    for method in ("rowCount", "columnCount", "data", "headerData"):
        assert hasattr(model_cls, method), (
            f"Model class {model_cls.__name__} missing required method {method!r}"
        )
        assert callable(getattr(model_cls, method)), (
            f"Model class {model_cls.__name__}.{method} must be callable"
        )


def _entry(pattern_id: str, *, hit_count: int):
    from novel_forge.core.schemas.humanize_library import HumanizeLibraryEntry

    return HumanizeLibraryEntry(
        pattern_id=pattern_id,
        pattern_name=pattern_id,
        category="测试",
        severity="medium",
        detection_method="regex",
        source="user",
        hit_count=hit_count,
    )


def test_humanize_model_sort_updates_visible_row_order() -> None:
    from novel_forge.desktop.pages.standalone import humanize_library_dashboard as hld

    first = _entry("lib_user_00000001", hit_count=1)
    second = _entry("lib_user_00000002", hit_count=9)
    model = hld._HumanizeLibraryModel([first, second])

    model.sort(5, Qt.SortOrder.DescendingOrder)

    assert model.entry_at(0) is second
    assert model.data(model.index(0, 5), Qt.ItemDataRole.DisplayRole) == "9"
