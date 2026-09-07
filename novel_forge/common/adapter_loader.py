"""Shared adapter-loading helper for LLM and TTS gateways.

Both ``gateway/factory.py`` and ``tts/gateway/factory.py`` resolve an adapter
class from a ``(module_path, class_name)`` registry entry via ``importlib``.
The lookup logic is identical; only the error policy differs (the LLM factory
swallows load failures and returns ``None`` for optional profile adapters,
while the TTS factory raises ``ValueError`` because an unknown TTS provider is
a hard configuration error).

This module factors out the shared lookup so the two factories stop
duplicating the ``importlib.import_module`` + ``getattr`` dance. It loads
classes only - instantiation, kwarg filtering, and fault-tolerance wrapping
stay in each factory, where the provider-specific concerns belong.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, cast


def load_adapter_class(
    module_path: str,
    class_name: str,
    *,
    on_error: Callable[[str, str, BaseException], None] | None = None,
) -> type[Any] | None:
    """Import and return an adapter class by ``(module_path, class_name)``.

    Parameters
    ----------
    module_path:
        Dotted module path to ``importlib.import_module``.
    class_name:
        Attribute name to ``getattr`` from the imported module.
    on_error:
        Optional callback invoked when import or attribute lookup fails.
        Receives ``(module_path, class_name, exc)``. When omitted (default),
        any failure is re-raised as a ``ValueError`` so callers that treat an
        unknown/broken adapter as a hard error get the TTS-style behaviour
        for free; callers that want the LLM-style "swallow and warn" policy
        pass an ``on_error`` that logs and let this function return ``None``.

    Returns
    -------
    The resolved class object, or ``None`` when ``on_error`` swallowed a
    failure.
    """
    try:
        module = importlib.import_module(module_path)
        return cast("type[Any]", getattr(module, class_name))
    except (ImportError, AttributeError) as exc:
        if on_error is not None:
            on_error(module_path, class_name, exc)
            return None
        raise ValueError(
            f"Failed to load adapter class {class_name!r} from {module_path!r}: {exc}"
        ) from exc
