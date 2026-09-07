"""Compatibility module for Engine-owned workflow preset persistence."""

from __future__ import annotations

import sys

from novel_forge.app_service import preset_manager as _shared

sys.modules[__name__] = _shared
