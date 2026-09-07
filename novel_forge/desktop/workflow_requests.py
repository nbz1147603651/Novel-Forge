"""Compatibility module for Engine-owned workflow request builders."""

from __future__ import annotations

import sys

from novel_forge.app_service import workflow_requests as _shared

sys.modules[__name__] = _shared
