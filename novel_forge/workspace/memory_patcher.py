"""Memory patcher — patches chapter files based on consistency/causal repair results.

Extracted from chapter_studio_data.py to separate business logic from UI layer.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


class MemoryPatcher:
    """Patches generated chapter files with repair results and fallback content."""

    @staticmethod
    def patch_memory_file(
        memory_path: Path,
        patch_data: dict[str, Any],
    ) -> bool:
        """Apply a single dict of patch data to a JSON memory file.

        Args:
            memory_path: Path to the JSON memory file.
            patch_data: Dict of key-value pairs to patch.

        Returns:
            True if file was modified, False if no changes needed.
        """
        if not memory_path.exists():
            return False

        with open(memory_path, encoding="utf-8") as f:
            memory = json.load(f)

        changed = False
        for key, value in patch_data.items():
            if memory.get(key) != value:
                memory[key] = value
                changed = True

        if changed:
            tmp = memory_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(memory, f, ensure_ascii=False, indent=2)
            shutil.copymode(memory_path, tmp)
            tmp.replace(memory_path)

        return changed

    @staticmethod
    def ensure_file_with_fallback(
        file_path: Path,
        fallback_content: str,
    ) -> bool:
        """Ensure a file exists, writing fallback content if not.

        Args:
            file_path: Path to the file.
            fallback_content: Content to write if file doesn't exist.

        Returns:
            True if fallback was written, False if file already existed.
        """
        if file_path.exists():
            return False

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(fallback_content, encoding="utf-8")
        return True
