"""Compatibility aliases for the former desktop settings persistence module.

Settings persistence belongs to the Engine application service.  The aliases
remain for the PySide stable fallback so existing UI imports do not create a
second implementation.
"""

from __future__ import annotations

from novel_forge.app_service.settings_store import SettingsSaveResult, SettingsStore

DesktopSettingsStore = SettingsStore

__all__ = ["DesktopSettingsStore", "SettingsSaveResult"]
