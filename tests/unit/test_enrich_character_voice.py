"""Tests for ENRICH_CHARACTER response containing voice + relationships."""

from __future__ import annotations

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import get_task_format_contract
from novel_forge.core.schemas.bible import CharacterProfile


class TestEnrichCharacterContract:
    """ENRICH_CHARACTER format contract includes voice and relationships."""

    def test_contract_exists(self) -> None:
        contract = get_task_format_contract(TaskType.ENRICH_CHARACTER)
        assert contract is not None

    def test_voice_in_allowed_keys(self) -> None:
        contract = get_task_format_contract(TaskType.ENRICH_CHARACTER)
        assert "voice" in contract.allowed_top_level_keys

    def test_relationships_in_allowed_keys(self) -> None:
        contract = get_task_format_contract(TaskType.ENRICH_CHARACTER)
        assert "relationships" in contract.allowed_top_level_keys

    def test_required_keys_do_not_include_voice(self) -> None:
        """voice is optional in ENRICH_CHARACTER — not required."""
        contract = get_task_format_contract(TaskType.ENRICH_CHARACTER)
        assert "voice" not in contract.required_top_level_keys

    def test_required_keys_include_core_fields(self) -> None:
        contract = get_task_format_contract(TaskType.ENRICH_CHARACTER)
        for key in ("appearance", "personality", "backstory", "arc"):
            assert key in contract.required_top_level_keys


class TestEnrichCharacterResponseWithVoice:
    """CharacterProfile accepts ENRICH_CHARACTER-style payloads with voice."""

    def test_profile_from_enrich_response_with_voice(self) -> None:
        """Simulate an ENRICH_CHARACTER LLM response with voice field."""
        enrich_data = {
            "appearance": "身材高瘦，常穿深色长衫",
            "personality": "沉默寡言，心思缜密",
            "backstory": "幼年丧父，独自求学",
            "arc": "从孤独求生到信任他人",
            "voice": "短句为主，喜欢用反问句，紧张时会下意识咬唇。",
            "relationships": {"苏晴": "青梅竹马，互相信任"},
        }
        profile = CharacterProfile.model_validate(
            {**enrich_data, "name": "林远"}
        )
        assert profile.voice == "短句为主，喜欢用反问句，紧张时会下意识咬唇。"
        assert "苏晴" in profile.relationships

    def test_profile_from_enrich_response_without_voice(self) -> None:
        """ENRICH_CHARACTER response without voice gets empty default."""
        enrich_data = {
            "appearance": "身材高瘦",
            "personality": "沉默寡言",
            "backstory": "幼年丧父",
            "arc": "从孤独到信任",
        }
        profile = CharacterProfile.model_validate(
            {**enrich_data, "name": "林远"}
        )
        assert profile.voice == ""

    def test_profile_from_enrich_response_with_relationships(self) -> None:
        """Relationships from enrich response are preserved."""
        enrich_data = {
            "appearance": "普通",
            "personality": "普通",
            "backstory": "普通",
            "arc": "普通",
            "relationships": {
                "苏晴": "青梅竹马",
                "赵云": "战友",
                "老王": "师父",
            },
        }
        profile = CharacterProfile.model_validate(
            {**enrich_data, "name": "林远"}
        )
        assert len(profile.relationships) == 3
        assert profile.relationships["苏晴"] == "青梅竹马"

    def test_enrich_response_json_round_trip(self) -> None:
        """Full enrich response survives JSON round-trip."""
        enrich_data = {
            "name": "林远",
            "appearance": "身材高瘦",
            "personality": "沉默寡言",
            "backstory": "幼年丧父",
            "arc": "从孤独到信任",
            "voice": "低沉沙哑，语速偏慢",
            "relationships": {"苏晴": "青梅竹马"},
        }
        profile = CharacterProfile.model_validate(enrich_data)
        json_str = profile.model_dump_json()
        restored = CharacterProfile.model_validate_json(json_str)

        assert restored.voice == "低沉沙哑，语速偏慢"
        assert restored.relationships == {"苏晴": "青梅竹马"}
        assert restored.appearance == "身材高瘦"

    def test_enrich_response_with_empty_relationships(self) -> None:
        """Empty relationships dict is valid."""
        enrich_data = {
            "appearance": "普通",
            "personality": "普通",
            "backstory": "普通",
            "arc": "普通",
            "relationships": {},
        }
        profile = CharacterProfile.model_validate(
            {**enrich_data, "name": "林远"}
        )
        assert profile.relationships == {}

    def test_enrich_response_voice_with_structured_value(self) -> None:
        """Voice as a structured list from LLM gets coerced to string."""
        enrich_data = {
            "appearance": "普通",
            "personality": "普通",
            "backstory": "普通",
            "arc": "普通",
            "voice": ["短句为主", "喜欢反问", "紧张时咬唇"],
        }
        profile = CharacterProfile.model_validate(
            {**enrich_data, "name": "林远"}
        )
        assert isinstance(profile.voice, str)
        assert len(profile.voice) > 0
