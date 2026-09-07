"""Helpers for closing provider SDK clients safely."""

from __future__ import annotations

import inspect
from typing import Any


async def close_async_client(client: Any) -> None:
    """Best-effort close for async SDK clients.

    Supports both ``aclose()`` and ``close()`` methods, and gracefully handles
    methods that may be sync or async depending on SDK implementation.
    """
    if client is None:
        return

    for method_name in ("aclose", "close"):
        method = getattr(client, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
            if inspect.isawaitable(result):
                await result
        except Exception:
            # Exit cleanup should be best-effort and never raise.
            pass
        return
