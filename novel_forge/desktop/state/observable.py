"""Observable per-page state containers for the desktop application.

Provides a base class that emits change signals when fields are updated,
enabling reactive UI patterns without a global singleton bottleneck.

Usage::

    class MyPageState(ObservablePageState):
        title: str = ""
        count: int = 0

    state = MyPageState()
    state.changed.connect(lambda field, value: print(f"{field} -> {value}"))
    state.batch_update(title="Hello", count=42)
"""

from __future__ import annotations

import warnings
from typing import ClassVar

from PySide6.QtCore import QObject, Signal


class ObservablePageState(QObject):
    """Base class for per-page observable state containers.

    Subclasses should define their fields as regular instance attributes
    (set in ``__init__`` or as class-level defaults).  Use
    :meth:`batch_update` for atomic multi-field changes, or override
    individual setters to emit fine-grained signals.

    The ``_declared_fields`` class variable is auto-populated via
    ``__init_subclass__`` by collecting annotated attribute names from
    the subclass hierarchy.  :meth:`batch_update` emits a
    ``UserWarning`` when an unknown field name is passed.

    Signals
    -------
    changed(str, object)
        Emitted when any field changes — args are ``(field_name, new_value)``.
        For batch updates the field_name is ``"batch"`` and value is a dict.
    """

    changed = Signal(str, object)

    # Auto-collected annotated field names (populated by __init_subclass__).
    _declared_fields: ClassVar[frozenset[str]] = frozenset()

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Collect annotated field names from the entire MRO.
        fields: set[str] = set()
        for klass in cls.__mro__:
            if klass is QObject or klass is object:
                continue
            hints = getattr(klass, "__annotations__", None)
            if hints:
                for name in hints:
                    if not name.startswith("_"):
                        fields.add(name)
        cls._declared_fields = frozenset(fields)

    def batch_update(self, **kwargs: object) -> None:
        """Atomically update multiple fields and emit a single change event.

        Only updates attributes that already exist on the instance.
        Emits a ``UserWarning`` for field names not found in
        :attr:`_declared_fields` (compile-time safety net).
        """
        updated: dict[str, object] = {}
        for name, value in kwargs.items():
            if self._declared_fields and name not in self._declared_fields:
                warnings.warn(
                    f"{type(self).__name__}.batch_update: "
                    f"unknown field {name!r} (not in _declared_fields)",
                    stacklevel=2,
                )
            if hasattr(self, name):
                setattr(self, name, value)
                updated[name] = value
        if updated:
            self.changed.emit("batch", updated)

    def _emit_change(self, field_name: str, value: object) -> None:
        """Emit a change signal for a single field.

        Subclasses can call this from custom property setters.
        """
        self.changed.emit(field_name, value)
