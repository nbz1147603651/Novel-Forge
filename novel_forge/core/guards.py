"""Runtime guards for catching common programming errors."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any


def assert_not_coroutine(value: Any, context: str) -> None:
    """Assert that a value is NOT an unawaited coroutine.

    This guard catches two common mistakes:
    1. Passing a coroutine object where the result was expected
       (asyncio.iscoroutine detects this)
    2. Passing a coroutine function (async def) where the function's
       return value was expected (inspect.iscoroutinefunction detects this)

    Args:
        value: The value to check.
        context: Description of where this check occurs (e.g. variable name,
            function name, location). Included in error message.

    Raises:
        RuntimeError: If value is a coroutine object or coroutine function.
    """
    if asyncio.iscoroutine(value):
        raise RuntimeError(
            f"assert_not_coroutine failed: value at {context} is an unawaited coroutine object. "
            f"Use 'await' to retrieve the result. "
            f"Type: {type(value).__name__}"
        )

    if inspect.iscoroutinefunction(value):
        raise RuntimeError(
            f"assert_not_coroutine failed: value at {context} is a coroutine function (async def). "
            f"Did you forget to call it, or forgot to await its return value? "
            f"Type: {type(value).__name__}"
        )