"""MVVM base infrastructure for Desktop pages (Phase 10).

Provides a lightweight ViewModel base class that bridges pure-Python state
mutations to Qt signal notifications, enabling testable separation of UI
logic from business state.

Usage::

    from novel_forge.desktop.mvvm import ViewModel, observable

    class MyViewModel(ViewModel):
        def __init__(self):
            super().__init__()
            self._count = 0

        @observable
        def count(self) -> int:
            return self._count

        @count.setter
        def count(self, value: int) -> None:
            self._count = value

        def increment(self) -> None:
            self.count = self._count + 1  # triggers notify("count")

    # In the View (QWidget):
    vm = MyViewModel()
    vm.changed.connect(lambda field: label.setText(str(vm.count)))
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from PySide6.QtCore import QObject, Signal

T = TypeVar("T")


class ViewModel(QObject):
    """Base ViewModel with Qt change-notification signal.

    Subclasses hold pure business state and expose commands.  The ``changed``
    signal emits the property name whenever an observable field is mutated,
    allowing Views to bind selectively.

    Thread-safety: ViewModels are owned by the main (GUI) thread.  Async
    commands should post results back via Qt signals or ``QMetaObject.invokeMethod``.
    """

    # Emitted when any observable property changes.  The argument is the
    # property name (str).  Views connect to this and filter by field name.
    changed = Signal(str)

    # Emitted when an error occurs during a command.  Arguments: (field, message).
    error = Signal(str, str)

    def notify(self, field: str) -> None:
        """Manually emit a change notification for *field*."""
        self.changed.emit(field)

    def notify_many(self, *fields: str) -> None:
        """Emit change notifications for multiple fields."""
        for f in fields:
            self.changed.emit(f)

    def set_error(self, field: str, message: str) -> None:
        """Emit an error signal for *field*."""
        self.error.emit(field, message)


def observable(prop: property) -> property:
    """Decorator that auto-emits ``changed`` when the property setter fires.

    Usage::

        class VM(ViewModel):
            def __init__(self):
                super().__init__()
                self._name = ""

            @observable
            def name(self) -> str:
                return self._name

            @name.setter
            def name(self, value: str) -> None:
                self._name = value

    After ``vm.name = "x"``, the ``changed("name")`` signal is emitted
    automatically.
    """
    field_name = prop.fget.__name__ if prop.fget else ""
    original_setter = prop.fset

    def _auto_notify_setter(self: ViewModel, value: Any) -> None:
        if original_setter is not None:
            original_setter(self, value)
        self.changed.emit(field_name)

    return property(prop.fget, _auto_notify_setter, prop.fdel, prop.__doc__)


class Command:
    """Encapsulates an async or sync action with can_execute gating.

    Usage::

        class VM(ViewModel):
            def __init__(self):
                super().__init__()
                self.generate = Command(self._do_generate, self._can_generate)

            def _can_generate(self) -> bool:
                return not self.is_generating

            async def _do_generate(self, params: GenParams) -> None:
                ...

        # In the View:
        btn.clicked.connect(lambda: vm.generate.execute(params))
        vm.changed.connect(lambda _: btn.setEnabled(vm.generate.can_execute()))
    """

    def __init__(
        self,
        execute_fn: Callable[..., Any],
        can_execute_fn: Callable[[], bool] | None = None,
    ) -> None:
        self._execute_fn = execute_fn
        self._can_execute_fn = can_execute_fn

    def can_execute(self) -> bool:
        if self._can_execute_fn is None:
            return True
        return self._can_execute_fn()

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        if not self.can_execute():
            return None
        return self._execute_fn(*args, **kwargs)


__all__ = ["ViewModel", "observable", "Command"]
