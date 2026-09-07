"""Unit tests for Bailian (DashScope) voice catalog metadata enrichment.

Verifies that:
- Voice catalog entries include personality/voice_description/trait/role fields
- _infer_bailian_personality() correctly infers personality from metadata
"""

from __future__ import annotations

from novel_forge.tts.gateway.adapters.dashscope_adapter import (
    _infer_bailian_personality,
)


class TestInferBailianPersonality:
    """Test the personality inference function."""

    def test_warm_trait(self):
        voice = {"trait": "知心温暖音", "tags": ["温暖", "共情"], "name": "龙安聆心"}
        result = _infer_bailian_personality(voice)
        assert "温暖" in result

    def test_sunny_trait(self):
        voice = {"trait": "阳光大男孩", "tags": ["阳光", "活力"], "name": "龙安洋"}
        result = _infer_bailian_personality(voice)
        assert "阳光" in result

    def test_calm_trait(self):
        voice = {"trait": "沉稳质感男", "tags": ["沉稳", "质感"], "name": "龙三叔"}
        result = _infer_bailian_personality(voice)
        assert "沉稳" in result

    def test_lively_trait(self):
        voice = {"trait": "欢脱元气女", "tags": ["活泼", "元气"], "name": "龙安欢"}
        result = _infer_bailian_personality(voice)
        assert "活泼" in result

    def test_childlike_trait(self):
        voice = {"trait": "天真烂漫女童", "tags": ["天真", "烂漫"], "name": "龙呼呼"}
        result = _infer_bailian_personality(voice)
        assert "天真" in result

    def test_magnetic_trait(self):
        voice = {"trait": "温润磁性音", "tags": ["磁性", "温润"], "name": "龙松林望"}
        result = _infer_bailian_personality(voice)
        # Should match 磁性 or 温暖 (温润 contains 温)
        assert result != ""

    def test_empty_voice(self):
        voice = {"voice_id": "test", "name": "test"}
        result = _infer_bailian_personality(voice)
        # No trait/tags to match, might be empty
        assert isinstance(result, str)

    def test_max_three_labels(self):
        """Personality should return at most 3 labels."""
        voice = {
            "trait": "温暖活泼阳光甜美",
            "tags": ["温暖", "活泼", "阳光", "甜美", "天真"],
            "name": "龙甜甜",
        }
        result = _infer_bailian_personality(voice)
        labels = result.split("，")
        assert len(labels) <= 3

    def test_tags_only(self):
        """Inference works from tags alone when trait is empty."""
        voice = {"trait": "", "tags": ["沉稳", "冷静", "质感"], "name": "龙某某"}
        result = _infer_bailian_personality(voice)
        assert "沉稳" in result

    def test_name_contributes(self):
        """Name field also contributes to matching."""
        voice = {"trait": "", "tags": [], "name": "温暖小太阳"}
        result = _infer_bailian_personality(voice)
        assert "温暖" in result


class TestVoiceCatalogMetadata:
    """Verify that hardcoded catalog entries have enriched metadata."""

    def _get_all_catalog_voices(self):
        """Collect all voice entries from the dashscope adapter catalogs."""
        from novel_forge.tts.gateway.adapters import dashscope_adapter

        voices = []
        for attr_name in dir(dashscope_adapter):
            if attr_name.startswith("_") and "VOICES" in attr_name:
                catalog = getattr(dashscope_adapter, attr_name)
                if isinstance(catalog, (list, tuple)):
                    voices.extend(catalog)
        return voices

    def test_catalogs_have_trait_field(self):
        """At least some catalog voices should have a trait field."""
        voices = self._get_all_catalog_voices()
        assert len(voices) > 0, "No voice catalogs found"
        voices_with_trait = [v for v in voices if v.get("trait")]
        # We enriched 4 catalogs with trait fields
        assert len(voices_with_trait) >= 10, (
            f"Expected >=10 voices with trait, got {len(voices_with_trait)}"
        )

    def test_catalogs_have_personality_field(self):
        """Enriched voices should have personality field."""
        voices = self._get_all_catalog_voices()
        voices_with_personality = [v for v in voices if v.get("personality")]
        assert len(voices_with_personality) >= 10

    def test_catalogs_have_voice_description_field(self):
        """Enriched voices should have voice_description field."""
        voices = self._get_all_catalog_voices()
        voices_with_desc = [v for v in voices if v.get("voice_description")]
        assert len(voices_with_desc) >= 10

    def test_catalogs_have_role_field(self):
        """Enriched voices should have role field."""
        voices = self._get_all_catalog_voices()
        voices_with_role = [v for v in voices if v.get("role")]
        assert len(voices_with_role) >= 10

    def test_enriched_voice_sample(self):
        """Spot-check a known enriched voice entry."""

        # Find 龙安聆心 (longanlingxin) in catalogs
        voices = self._get_all_catalog_voices()
        target = next((v for v in voices if v.get("voice_id") == "longanlingxin"), None)
        assert target is not None, "longanlingxin not found in any catalog"
        assert target.get("trait") != ""
        assert target.get("personality") != ""
        assert target.get("voice_description") != ""
        assert target.get("role") != ""
