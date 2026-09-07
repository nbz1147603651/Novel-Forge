"""Application-level local audio model inventory and lifecycle management.

Public exports stay lazy so an isolated sidecar can import one lightweight
model-center helper without importing every cloud and sound provider.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "ApplicationAudioModelManager",
    "AudioModelCenterService",
    "AudioRuntimeManager",
    "ManagedPythonRuntime",
    "ModelRepositoryMigrator",
    "PersistentDownloadManager",
    "audio_model_catalog",
]

_EXPORTS = {
    "ApplicationAudioModelManager": (
        "novel_forge.tts.model_center.manager",
        "ApplicationAudioModelManager",
    ),
    "AudioModelCenterService": (
        "novel_forge.tts.model_center.service",
        "AudioModelCenterService",
    ),
    "AudioRuntimeManager": (
        "novel_forge.tts.model_center.runtimes",
        "AudioRuntimeManager",
    ),
    "ManagedPythonRuntime": (
        "novel_forge.tts.model_center.python_runtime",
        "ManagedPythonRuntime",
    ),
    "ModelRepositoryMigrator": (
        "novel_forge.tts.model_center.migration",
        "ModelRepositoryMigrator",
    ),
    "PersistentDownloadManager": (
        "novel_forge.tts.model_center.downloads",
        "PersistentDownloadManager",
    ),
    "audio_model_catalog": (
        "novel_forge.tts.model_center.catalog",
        "audio_model_catalog",
    ),
}


def __getattr__(name: str) -> Any:
    """Resolve public model-center APIs without eager optional imports."""

    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
