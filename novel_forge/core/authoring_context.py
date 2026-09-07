"""Transport-independent cancellation/authority hook for model dispatch."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


class AuthoringAuthorityError(ValueError):
    """Authority/budget waits must never become provider retry/failover errors."""


_authority_check: ContextVar[Callable[[], None] | None] = ContextVar(
    "authority_check", default=None
)
_reserve: ContextVar[Callable[[str, float | None, dict[str, Any]], None] | None] = ContextVar(
    "authoring_reserve", default=None
)
_settle: ContextVar[Callable[[str, float | None], None] | None] = ContextVar(
    "authoring_settle", default=None
)


def begin_authoring_call(call_id: str, estimate: float | None, metadata: dict[str, Any]) -> None:
    check_authoring_authority()
    reserve = _reserve.get()
    if reserve is not None:
        reserve(call_id, estimate, metadata)


def has_authoring_budget() -> bool:
    return _reserve.get() is not None


def settle_authoring_call(call_id: str, cost: float | None) -> None:
    # An already sent request can cost money after pause; never require fresh
    # authority merely to record what already happened.
    settle = _settle.get()
    if settle is not None:
        settle(call_id, cost)


def check_authoring_authority() -> None:
    check = _authority_check.get()
    if check is not None:
        check()


@contextmanager
def authoring_dispatch_guard(
    check: Callable[[], None],
    *,
    reserve: Callable[[str, float | None, dict[str, Any]], None] | None = None,
    settle: Callable[[str, float | None], None] | None = None,
) -> Iterator[None]:
    token = _authority_check.set(check)
    reserve_token = _reserve.set(reserve)
    settle_token = _settle.set(settle)
    try:
        yield
    finally:
        _authority_check.reset(token)
        _reserve.reset(reserve_token)
        _settle.reset(settle_token)
