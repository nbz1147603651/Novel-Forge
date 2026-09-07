"""Compatibility module for the Engine-owned creative configuration service."""

from __future__ import annotations

import sys

from novel_forge.app_service import ai_generate as _shared

sys.modules[__name__] = _shared
