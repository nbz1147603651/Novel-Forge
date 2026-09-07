"""Compatibility imports for the Engine-owned Ollama runtime.

Desktop code still imports this module during the dual-client convergence
window, but ownership and native HTTP access live in ``app_service``.
"""

from __future__ import annotations

from novel_forge.app_service.ollama_control import (
    OllamaSidecarResult,
    OllamaSidecarService,
)
from novel_forge.app_service.ollama_control import (
    host_value as _host_value,
)
from novel_forge.app_service.ollama_control import (
    native_base_url as _native_base_url,
)

__all__ = ["OllamaSidecarResult", "OllamaSidecarService", "_host_value", "_native_base_url"]
