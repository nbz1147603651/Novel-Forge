"""Runtime/model compatibility matrix independent from UI and providers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from novel_forge.tts.model_center.runtimes import audio_runtime_catalog
from novel_forge.tts.model_center.schemas import (
    AudioModelDescriptor,
    AudioRuntimeState,
    RuntimeInstallState,
)


@dataclass(frozen=True)
class CompatibilityResult:
    compatible: bool
    reason: str = ""


def check_runtime_compatibility(
    model: AudioModelDescriptor,
    runtime: AudioRuntimeState | None,
) -> CompatibilityResult:
    if not model.runtime_id:
        return CompatibilityResult(True)
    descriptors = {item.runtime_id: item for item in audio_runtime_catalog()}
    descriptor = descriptors.get(model.runtime_id)
    if descriptor is None:
        return CompatibilityResult(False, f"未登记运行时 {model.runtime_id}。")
    if model.plugin_id not in descriptor.supported_plugins:
        return CompatibilityResult(False, "运行时未声明支持该模型插件。")
    if runtime is None or not runtime.version:
        return CompatibilityResult(False, f"需要安装 {descriptor.display_name}。")
    if runtime.state in {
        RuntimeInstallState.NOT_INSTALLED,
        RuntimeInstallState.FAILED,
        RuntimeInstallState.INCOMPATIBLE,
    }:
        return CompatibilityResult(False, f"{descriptor.display_name} 当前不可用。")
    current = _version_tuple(runtime.version)
    if model.minimum_runtime_version and current < _version_tuple(model.minimum_runtime_version):
        return CompatibilityResult(False, f"运行时需要 >= {model.minimum_runtime_version}。")
    if model.maximum_runtime_version and current > _version_tuple(model.maximum_runtime_version):
        return CompatibilityResult(False, f"运行时需要 <= {model.maximum_runtime_version}。")
    return CompatibilityResult(True)


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(item) for item in numbers) or (0,)
