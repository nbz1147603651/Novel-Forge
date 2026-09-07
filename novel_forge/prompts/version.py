"""Template version management module.

This module provides version tracking and compatibility checking for templates.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_VERSION_FILE = Path(__file__).parent / "prompts" / "_version.json"


@dataclass
class TemplateVersion:
    """Version information for a template."""

    name: str
    version: str  # format: "major.minor.patch"
    status: str  # "stable", "beta", "deprecated"
    last_updated: str
    changelog: Optional[str] = None

    def is_compatible(self, other: "TemplateVersion") -> bool:
        """Check if this version is compatible with another version."""
        self_parts = [int(x) for x in self.version.split(".")]
        other_parts = [int(x) for x in other.version.split(".")]
        return self_parts[0] == other_parts[0]


@dataclass
class StyleVersion:
    """Version information for a writing style."""

    name: str
    identifier: str
    version: str
    status: str

    def is_stable(self) -> bool:
        return self.status == "stable"


class TemplateVersionManager:
    """Manages template versions and compatibility."""

    CURRENT_VERSION = "2.1.3"
    MIN_COMPATIBLE_VERSION = "1.0.0"

    WRITING_STYLES: dict[
        str, StyleVersion
    ] = {}  # Deprecated: style_profile.json now handles styles

    CATEGORY_VERSIONS = {
        "writing": TemplateVersion("写作类", "2.1.3", "stable", "2026-05-14"),
        "planning": TemplateVersion("规划类", "2.1.3", "stable", "2026-05-14"),
        "checking": TemplateVersion("检查类", "2.1.3", "stable", "2026-05-14"),
        "initialization": TemplateVersion("初始化类", "2.1.3", "stable", "2026-05-14"),
        "summary": TemplateVersion("总结类", "2.0.0", "stable", "2026-03-30"),
        "canon": TemplateVersion("规范类", "2.1.3", "stable", "2026-05-14"),
        "compression": TemplateVersion("压缩类", "2.0.0", "stable", "2026-03-30"),
        "beats": TemplateVersion("节拍类", "2.1.3", "stable", "2026-05-14"),
        "_base": TemplateVersion("基础模块", "2.1.0", "stable", "2026-04-21"),
        "_styles": TemplateVersion("风格模块", "2.0.0", "stable", "2026-03-30"),
    }

    def __init__(self, version_file: Path | None = None) -> None:
        self._version_file = version_file or _VERSION_FILE

    def get_current_version(self) -> str:
        """Get the current system version."""
        return self.CURRENT_VERSION

    def get_category_version(self, category: str) -> Optional[TemplateVersion]:
        """Get version info for a template category."""
        return self.CATEGORY_VERSIONS.get(category)

    def get_style_version(self, style_identifier: str) -> Optional[StyleVersion]:
        """Get version info for a writing style (deprecated)."""
        return self.WRITING_STYLES.get(style_identifier)

    def is_compatible(self, version: str) -> bool:
        """Check if a version is compatible with the current system."""
        current_parts = [int(x) for x in self.CURRENT_VERSION.split(".")]
        version_parts = [int(x) for x in version.split(".")]
        return current_parts[0] == version_parts[0]

    def get_version_info(self) -> dict[str, Any]:
        """Get complete version information."""
        return {
            "system_version": self.CURRENT_VERSION,
            "min_compatible_version": self.MIN_COMPATIBLE_VERSION,
            "categories": {k: asdict(v) for k, v in self.CATEGORY_VERSIONS.items()},
            "styles": {k: asdict(v) for k, v in self.WRITING_STYLES.items()},
            "last_updated": datetime.now().isoformat(),
        }

    def export_version_file(self) -> None:
        """Export version info to JSON file."""
        self._version_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._version_file, "w", encoding="utf-8") as f:
            json.dump(self.get_version_info(), f, ensure_ascii=False, indent=2)

    @classmethod
    def from_version_file(cls, version_file: Path | None = None) -> "TemplateVersionManager":
        """Load version info from JSON file."""
        file_path = version_file or _VERSION_FILE
        manager = cls(file_path)
        if file_path.exists():
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
                manager.CURRENT_VERSION = data.get("system_version", "2.0.0")
        return manager


_version_manager: Optional[TemplateVersionManager] = None


def get_version_manager() -> TemplateVersionManager:
    """Get the singleton version manager instance."""
    global _version_manager
    if _version_manager is None:
        _version_manager = TemplateVersionManager()
    return _version_manager
