"""TitlePolicy normalize and clamp unit tests.

Covers T14 (fix verification) + T15 (comprehensive test suite).

Tests the normalize path added in commit 6c6696f9:
- TitlePolicy._normalize_title_policy_payload (schema-level before validator)
- _normalize_title_policy (step-level wrapper)
- Full EditorialContractStep._execute integration
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.editorial.schemas import EditorialContract, TitlePolicy
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.steps.editorial_contract_step import (
    EditorialContractInput,
    EditorialContractStep,
    _normalize_title_policy,
)
from novel_forge.prompts.builder import PromptBuilder

# ---------------------------------------------------------------------------
# T14: Minimal fix verification — _normalize_title_policy + TitlePolicy clamp
# ---------------------------------------------------------------------------

class TestNormalizeTitlePolicyFix:
    """Verify the fix from commit 6c6696f9 is complete and working."""

    def test_normalize_title_policy_clamps_zero_to_one(self) -> None:
        """max_reuse: 0 → clamp to 1."""
        result = _normalize_title_policy({"max_reuse": 0, "naming_strategy": "test"})
        assert result["max_reuse"] == 1

    def test_title_policy_model_validate_clamps_zero(self) -> None:
        """TitlePolicy.model_validate with max_reuse=0 should NOT raise."""
        tp = TitlePolicy.model_validate({"max_reuse": 0, "naming_strategy": "test"})
        assert tp.max_reuse == 1

    def test_editorial_contract_accepts_clamped_title_policy(self) -> None:
        """Full EditorialContract.model_validate with max_reuse=0 in title_policy."""
        payload = _minimal_contract_payload()
        payload["title_policy"] = {"max_reuse": 0, "naming_strategy": "test"}
        contract = EditorialContract.model_validate(payload)
        assert contract.title_policy.max_reuse == 1

    def test_editorial_contract_accepts_clamped_title_policy_via_schema(self) -> None:
        """TitlePolicy.model_validate directly with max_reuse=0."""
        tp = TitlePolicy.model_validate({"max_reuse": 0})
        assert tp.max_reuse == 1


# ---------------------------------------------------------------------------
# T15: Comprehensive TitlePolicy test suite (12+ cases)
# ---------------------------------------------------------------------------

class TestTitlePolicyMaxReuseClamp:
    """Test max_reuse clamping behavior across all boundary values."""

    @pytest.mark.parametrize(
        "input_value,expected",
        [
            pytest.param(0, 1, id="zero_clamped_to_1"),
            pytest.param(1, 1, id="one_stays_1"),
            pytest.param(2, 2, id="two_stays_2"),
            pytest.param(5, 5, id="five_stays_5"),
            pytest.param(10, 10, id="ten_stays_10"),
            pytest.param(11, 10, id="eleven_clamped_to_10"),
            pytest.param(100, 10, id="hundred_clamped_to_10"),
            pytest.param(-1, 1, id="negative_one_clamped_to_1"),
            pytest.param(-999, 1, id="large_negative_clamped_to_1"),
        ],
    )
    def test_max_reuse_integer_clamp(self, input_value: int, expected: int) -> None:
        tp = TitlePolicy.model_validate({"max_reuse": input_value})
        assert tp.max_reuse == expected

    def test_max_reuse_none_uses_default(self) -> None:
        """max_reuse: None → default 2."""
        tp = TitlePolicy.model_validate({"max_reuse": None})
        assert tp.max_reuse == 2

    def test_max_reuse_missing_uses_default(self) -> None:
        """Missing max_reuse → default 2."""
        tp = TitlePolicy.model_validate({})
        assert tp.max_reuse == 2

    def test_max_reuse_string_zero_clamped(self) -> None:
        """max_reuse: "0" (string) → clamp to 1."""
        tp = TitlePolicy.model_validate({"max_reuse": "0"})
        assert tp.max_reuse == 1

    def test_max_reuse_string_number(self) -> None:
        """max_reuse: "5" (string) → parsed to 5."""
        tp = TitlePolicy.model_validate({"max_reuse": "5"})
        assert tp.max_reuse == 5

    def test_max_reuse_semantic_no_reuse(self) -> None:
        """max_reuse: "禁止复用" (semantic) → 1."""
        tp = TitlePolicy.model_validate({"max_reuse": "禁止复用"})
        assert tp.max_reuse == 1

    def test_max_reuse_invalid_string_uses_default(self) -> None:
        """max_reuse: "abc" (invalid) → default 2."""
        tp = TitlePolicy.model_validate({"max_reuse": "abc"})
        assert tp.max_reuse == 2


class TestTitlePolicyBooleanFlags:
    """Test allow_reuse and allow_repeated_titles boolean flags."""

    def test_allow_reuse_false_sets_max_reuse_1(self) -> None:
        """allow_reuse: false → max_reuse=1."""
        tp = TitlePolicy.model_validate({"allow_reuse": False})
        assert tp.max_reuse == 1

    def test_allow_repeated_titles_false_sets_max_reuse_1(self) -> None:
        """allow_repeated_titles: false → max_reuse=1."""
        tp = TitlePolicy.model_validate({"allow_repeated_titles": False})
        assert tp.max_reuse == 1

    def test_allow_reuse_false_overrides_explicit_max_reuse(self) -> None:
        """allow_reuse: false + max_reuse: 5 → max_reuse=1 (flag wins)."""
        tp = TitlePolicy.model_validate({"allow_reuse": False, "max_reuse": 5})
        assert tp.max_reuse == 1


class TestTitlePolicyAliases:
    """Test max_reuse alias resolution."""

    @pytest.mark.parametrize("alias", [
        "reuse_limit", "max_repeat", "max_repeats",
        "maximum_reuse", "maximum_repeats",
    ])
    def test_max_reuse_aliases(self, alias: str) -> None:
        """All aliases should resolve to max_reuse."""
        tp = TitlePolicy.model_validate({alias: 3})
        assert tp.max_reuse == 3


class TestTitlePolicyMissingEntirePolicy:
    """Test when title_policy is entirely missing."""

    def test_missing_title_policy_uses_defaults(self) -> None:
        """Missing entire title_policy → EditorialContract uses defaults."""
        payload = _minimal_contract_payload()
        # Don't include title_policy at all
        payload.pop("title_policy", None)
        contract = EditorialContract.model_validate(payload)
        assert contract.title_policy.max_reuse == 2
        assert contract.title_policy.allowed_repeated_titles == []


class TestTitlePolicyConflictWithNamingStrategy:
    """Test that max_reuse: 0 with conflicting naming_strategy is accepted."""

    def test_max_reuse_zero_with_naming_strategy_upper_limit(self) -> None:
        """max_reuse: 0 + naming_strategy: "上限两对" → should accept, clamp to 1."""
        tp = TitlePolicy.model_validate({
            "max_reuse": 0,
            "naming_strategy": "全120章采用四字格律标题，允许极少数回环标题用于首尾呼应，上限两对",
        })
        # The normalize path clamps 0→1, doesn't reject
        assert tp.max_reuse == 1
        assert "上限两对" in tp.naming_strategy


class TestTitlePolicyNormalizeFunction:
    """Test the step-level _normalize_title_policy function."""

    def test_normalize_returns_only_known_keys(self) -> None:
        """_normalize_title_policy should only return _TITLE_POLICY_KEYS."""
        # TitlePolicy is extra="forbid", so extra fields are rejected at validation.
        # This is correct behavior — the normalize function validates through TitlePolicy.
        result = _normalize_title_policy({
            "max_reuse": 3,
            "naming_strategy": "test",
        })
        assert set(result.keys()) == {"max_reuse", "allowed_repeated_titles", "naming_strategy"}
        assert result["max_reuse"] == 3

    def test_normalize_rejects_extra_fields(self) -> None:
        """_normalize_title_policy rejects extra fields (TitlePolicy is extra='forbid')."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="extra_field"):
            _normalize_title_policy({
                "max_reuse": 3,
                "naming_strategy": "test",
                "extra_field": "should be rejected",
            })

    def test_normalize_empty_dict_uses_defaults(self) -> None:
        """_normalize_title_policy({}) → defaults."""
        result = _normalize_title_policy({})
        assert result["max_reuse"] == 2
        assert result["allowed_repeated_titles"] == []

    def test_normalize_non_dict_returns_defaults(self) -> None:
        """_normalize_title_policy("not a dict") → defaults."""
        result = _normalize_title_policy("not a dict")
        assert result["max_reuse"] == 2


class TestTitlePolicyFullStepFlow:
    """Integration test: simulate LLM output through EditorialContractStep._execute."""

    def test_full_step_with_max_reuse_zero(self) -> None:
        """Simulate LLM output with max_reuse=0走完 EditorialContractStep._execute successfully."""
        router = _TitlePolicyZeroRouter()
        builder = _RecordingBuilder()
        step = EditorialContractStep(
            cast(ModelRouter, router),
            cast(PromptBuilder, builder),
            settings=_settings(),
        )

        contract = asyncio.run(
            step.run(
                EditorialContractInput(
                    title="测试长篇",
                    total_chapters=120,
                    story_bible={"synopsis": "核心梗概"},
                    character_bible={"characters": [{"name": "主角甲"}]},
                    blueprint={"synopsis": "蓝图", "key_turning_points": [{"chapter": 88}]},
                    blueprint_elements={},
                )
            )
        )

        assert isinstance(contract, EditorialContract)
        assert contract.title_policy.max_reuse == 1
        assert contract.title_policy.naming_strategy == (
            "全120章采用四字格律标题，允许极少数回环标题用于首尾呼应，上限两对"
        )


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _minimal_contract_payload() -> dict[str, Any]:
    """Minimal valid EditorialContract payload for testing."""
    return {
        "project_title": "测试",
        "character_voices": [
            {
                "character": "主角甲",
                "sentence_profile": "短句。",
                "explanation_bias": "少解释。",
                "emotion_syntax": "情绪升高时更短。",
                "signature_moves": ["先看事实"],
                "taboo_patterns": ["长篇独白"],
                "sample_lines": ["先看证据。"],
            }
        ],
        "climax_markers": [
            {
                "chapter_number": 40,
                "climax_type": "main",
                "description": "主线摊牌",
                "expected_aftermath_chapters": 6,
            }
        ],
        "denouement_budget": {
            "expected_chapters": 6,
            "max_confirmation_scenes": 2,
            "required_new_functions": ["余波后果"],
            "forbidden_repeats": [],
        },
        "theme_policies": ["主题通过选择呈现"],
        "symbol_policies": [
            {
                "symbol": "怀表",
                "narrative_function": "时间",
                "explanation_policy": "explain_once",
                "escalation_rule": "每次出现改变语境。",
                "max_explicit_explanations": 1,
            }
        ],
        "scene_resistance_rules": [
            {
                "scene_type": "对峙",
                "required_resistance": "必须有阻力。",
                "examples": ["门被挡住"],
            }
        ],
        "expression_channel_budget": {
            "somatic_reaction": 1,
            "action_tag": 2,
            "dialogue_tag": 2,
            "sensory_anchor": 3,
        },
        "expression_channel_profiles": [],
        "body_signal_budget_per_high_emotion_scene": 1,
        "forbidden_confirmation_phrases": [],
        "revision_priorities": [],
        "revelation_ladder": [],
        "editorial_element_directives": [],
        "time_bridge_policies": [],
        "title_policy": {
            "max_reuse": 2,
            "allowed_repeated_titles": [],
            "naming_strategy": "默认策略",
        },
    }


class _RecordingBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[TaskType, dict[str, Any], int, float]] = []

    def build(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float | None = None,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
    ) -> ModelRequest:
        self.calls.append((task_type, context, max_tokens, temperature))
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": task_type.value}],
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p if top_p is not None else 1.0,
        )


class _TitlePolicyZeroRouter:
    """Router that returns max_reuse: 0 to test the normalize path."""

    def __init__(self) -> None:
        self.tasks: list[TaskType] = []
        self.output_limits = {
            TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES: 32768,
            TaskType.DERIVE_EDITORIAL_STRUCTURE: 65536,
            TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS: 65536,
            TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES: 32768,
        }

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        return self.output_limits.get(task_type, 8192)

    def resolve_model_id_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str:
        return "mock-model"

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.tasks.append(request.task_type)
        payload: dict[str, Any]
        if request.task_type == TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES:
            payload = {
                "character_voices": [
                    {
                        "character": "主角甲",
                        "sentence_profile": "短句。",
                        "explanation_bias": "少解释。",
                        "emotion_syntax": "情绪升高时更短。",
                        "signature_moves": ["先看事实"],
                        "taboo_patterns": ["长篇独白"],
                        "sample_lines": ["先看证据。"],
                    }
                ]
            }
        elif request.task_type == TaskType.DERIVE_EDITORIAL_STRUCTURE:
            payload = {
                "climax_markers": [
                    {
                        "chapter_number": 88,
                        "climax_type": "main",
                        "description": "主线摊牌",
                        "expected_aftermath_chapters": 6,
                    }
                ],
                "denouement_budget": {
                    "expected_chapters": 6,
                    "max_confirmation_scenes": 2,
                    "required_new_functions": ["余波后果"],
                    "forbidden_repeats": [],
                },
                "revelation_ladder": [],
                "time_bridge_policies": [],
                "title_policy": {
                    "max_reuse": 0,
                    "allowed_repeated_titles": [],
                    "naming_strategy": "全120章采用四字格律标题，允许极少数回环标题用于首尾呼应，上限两对",
                },
            }
        elif request.task_type == TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS:
            payload = {
                "theme_policies": ["主题通过选择呈现"],
                "symbol_policies": [
                    {
                        "symbol": "怀表",
                        "narrative_function": "时间",
                        "explanation_policy": "explain_once",
                        "escalation_rule": "每次出现改变语境。",
                        "max_explicit_explanations": 1,
                    }
                ],
                "scene_resistance_rules": [
                    {
                        "scene_type": "对峙",
                        "required_resistance": "必须有阻力。",
                        "examples": ["门被挡住"],
                    }
                ],
                "expression_channel_budget": {"somatic_reaction": 1, "action_tag": 2, "dialogue_tag": 2, "sensory_anchor": 3},
                "expression_channel_profiles": [],
                "body_signal_budget_per_high_emotion_scene": 1,
                "forbidden_confirmation_phrases": [],
                "revision_priorities": [],
            }
        else:
            payload = {"editorial_element_directives": []}

        return ModelResponse(
            content=json.dumps(payload, ensure_ascii=False),
            model_id="mock-model",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=1.0,
            cost_usd=0.0,
        )


def _settings() -> Settings:
    return Settings.model_validate({"temp_plan_outline": 0.7})
