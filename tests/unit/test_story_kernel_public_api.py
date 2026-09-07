"""Public API checks for the StoryKernel-native architecture."""

from __future__ import annotations

import importlib.util

import novel_forge.story_kernel as sk


class TestStoryKernelPublicApi:
    """StoryKernel should expose native field-pool primitives only."""

    def test_legacy_compat_module_is_not_public_api(self) -> None:
        assert not hasattr(sk, "StoryKernelAdapter")
        assert importlib.util.find_spec("novel_forge.story_kernel.compat") is None

    def test_legacy_projection_module_is_not_public_api(self) -> None:
        assert not hasattr(sk, "generate_character_bible")
        assert importlib.util.find_spec("novel_forge.story_kernel.projections") is None
