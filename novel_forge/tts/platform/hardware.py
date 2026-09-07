"""Conservative hardware discovery for audio model planning."""

from __future__ import annotations

import os
import platform

from novel_forge.tts.platform.schemas import AudioHardwareProfile, AudioMemoryClass


def _memory_gb() -> float | None:
    try:
        if hasattr(os, "sysconf"):
            pages = int(os.sysconf("SC_PHYS_PAGES"))
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            if pages > 0 and page_size > 0:
                return round(pages * page_size / (1024**3), 1)
    except (OSError, TypeError, ValueError):
        return None
    return None


def detect_audio_hardware(*, accelerator_preference: str = "auto") -> AudioHardwareProfile:
    """Return stable planning hints without importing Torch/ONNX runtimes."""

    system = platform.system().lower()
    platform_id = {"darwin": "macos"}.get(system, system or "unknown")
    memory = _memory_gb()
    if memory is None:
        memory_class = AudioMemoryClass.MEDIUM
    elif memory < 12:
        memory_class = AudioMemoryClass.LIGHT
    elif memory < 24:
        memory_class = AudioMemoryClass.MEDIUM
    else:
        memory_class = AudioMemoryClass.HIGH

    accelerator = accelerator_preference.strip().lower() or "auto"
    if accelerator == "auto":
        accelerator = "mps" if platform_id == "macos" and platform.machine() == "arm64" else "cpu"
    return AudioHardwareProfile(
        platform=platform_id,
        accelerator=accelerator,
        memory_class=memory_class,
        memory_gb=memory,
        device_label=f"{platform.machine() or 'unknown'} · {accelerator.upper()}",
    )
