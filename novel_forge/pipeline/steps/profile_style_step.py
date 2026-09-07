"""ProfileStyleStep — 根据项目要素推导项目专属写作风格规范。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.style_profile import (
    CoolPointConfig,
    GlobalStyleConfig,
    HookConfig,
    MicroPayoffConfig,
    ProjectStyleProfile,
    StrandConfig,
    StyleModule,
    validate_hook_strength,
    validate_hook_type,
)
from novel_forge.pipeline.steps.base import PipelineStep


@dataclass
class ProfileStyleInput:
    """Input for style profiling task — project elements, not chapter text."""

    title: str
    genre: str
    tone: str
    writing_style_mode: str = ""
    narrative_complexity: str = "standard"
    story_bible: dict[str, Any] = field(default_factory=dict)
    character_bible: dict[str, Any] = field(default_factory=dict)
    blueprint_elements: dict[str, Any] = field(default_factory=dict)
    extra_instructions: str = ""
    synopsis: str = ""
    premise: str = ""
    kernel_context: dict[str, Any] | None = None  # StoryKernel field slices (from ContextComposer)


class ProfileStyleStep(PipelineStep[ProfileStyleInput, ProjectStyleProfile]):
    """根据项目设定要素生成项目专属写作风格规范。"""

    @property
    def step_name(self) -> str:
        return "profile_style"

    @staticmethod
    def _bounded_text(value: Any, max_length: int) -> str:
        """Deterministically fit descriptive text into its persisted schema field."""

        text = str(value or "").strip()
        if len(text) <= max_length:
            return text
        content_limit = max(1, max_length - 1)
        prefix = text[:content_limit]
        cut = max(prefix.rfind(mark) for mark in "。！？；.!?")
        if cut >= max_length // 2:
            prefix = prefix[: cut + 1]
        return f"{prefix.rstrip()}…"[:max_length]

    @classmethod
    def _parse_modules(cls, modules_raw: Any) -> list[StyleModule]:
        if not isinstance(modules_raw, list):
            return []
        modules: list[StyleModule] = []
        for raw_module in modules_raw:
            if not isinstance(raw_module, dict):
                continue
            raw_rules = raw_module.get("rules") or []
            rules = (
                [cls._bounded_text(rule, 50) for rule in raw_rules]
                if isinstance(raw_rules, list)
                else []
            )
            modules.append(
                StyleModule(
                    name=cls._bounded_text(raw_module.get("name", ""), 12),
                    rules=rules,
                    positive_example=cls._bounded_text(
                        raw_module.get("positive_example", ""),
                        60,
                    ),
                    negative_example=cls._bounded_text(
                        raw_module.get("negative_example", ""),
                        60,
                    ),
                )
            )
        return modules

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> ProjectStyleProfile:
        """Parse LLM response into ProjectStyleProfile with genre parameter fields.

        Args:
            data: Raw LLM response dict containing modules, source_elements, summary,
                  and optional hook_config, strand_config, micro_payoff_config.

        Returns:
            ProjectStyleProfile with all fields parsed and validated.
        """
        modules = ProfileStyleStep._parse_modules(data.get("modules", []))

        hook_config = ProfileStyleStep._parse_hook_config(data.get("hook_config", {}))
        strand_config = ProfileStyleStep._parse_strand_config(data.get("strand_config", {}))
        micro_payoff_config = ProfileStyleStep._parse_micro_payoff_config(
            data.get("micro_payoff_config", {})
        )
        cool_point_config = ProfileStyleStep._parse_cool_point_config(
            data.get("cool_point_config", {})
        )
        global_style = ProfileStyleStep._parse_global_style_config(data.get("global_style", {}))

        return ProjectStyleProfile(
            modules=modules,
            source_elements=data.get("source_elements", ["story_bible", "character_bible"]),
            summary=ProfileStyleStep._bounded_text(data.get("summary", ""), 80),
            hook_config=hook_config,
            strand_config=strand_config,
            micro_payoff_config=micro_payoff_config,
            cool_point_config=cool_point_config,
            global_style=global_style,
        )

    @staticmethod
    def _parse_hook_config(data: dict[str, Any]) -> HookConfig:
        if not isinstance(data, dict):
            return HookConfig()

        preferred_types_raw = data.get("preferred_types", [])
        preferred_types: list[str] = []
        if isinstance(preferred_types_raw, list):
            for t in preferred_types_raw:
                validated = validate_hook_type(str(t))
                if validated != "none":
                    preferred_types.append(validated)
        if not preferred_types:
            preferred_types = ["crisis", "mystery"]

        strength_baseline = validate_hook_strength(str(data.get("strength_baseline", "medium")))
        chapter_end_required = bool(data.get("chapter_end_required", True))

        return HookConfig(
            preferred_types=preferred_types,
            strength_baseline=strength_baseline,
            chapter_end_required=chapter_end_required,
        )

    @staticmethod
    def _parse_strand_config(data: dict[str, Any]) -> StrandConfig:
        if not isinstance(data, dict):
            return StrandConfig()

        def clamp_int(value: Any, default: int, min_val: int = 1) -> int:
            if isinstance(value, int) and value >= min_val:
                return value
            return default

        return StrandConfig(
            quest_max_consecutive=clamp_int(data.get("quest_max_consecutive"), 5),
            fire_max_absent=clamp_int(data.get("fire_max_absent"), 10),
            constellation_max_absent=clamp_int(data.get("constellation_max_absent"), 15),
            stagnation_threshold=clamp_int(data.get("stagnation_threshold"), 3),
        )

    @staticmethod
    def _parse_micro_payoff_config(data: dict[str, Any]) -> MicroPayoffConfig:
        if not isinstance(data, dict):
            return MicroPayoffConfig()

        valid_payoff_types = {
            "information",
            "relationship",
            "ability",
            "resource",
            "recognition",
            "emotion",
            "clue",
        }
        preferred_types_raw = data.get("preferred_types", [])
        preferred_types: list[str] = []
        if isinstance(preferred_types_raw, list):
            for t in preferred_types_raw:
                normalized = str(t).lower().strip()
                if normalized in valid_payoff_types:
                    preferred_types.append(normalized)
        if not preferred_types:
            preferred_types = ["information", "clue"]

        min_per_chapter = data.get("min_per_chapter", 1)
        if not isinstance(min_per_chapter, int) or min_per_chapter < 0:
            min_per_chapter = 1

        return MicroPayoffConfig(
            preferred_types=preferred_types,
            min_per_chapter=min_per_chapter,
        )

    @staticmethod
    def _parse_cool_point_config(data: dict[str, Any]) -> CoolPointConfig:
        if not isinstance(data, dict):
            return CoolPointConfig()

        valid_patterns = {
            "装逼打脸",
            "越级反杀",
            "扮猪吃虎",
            "顿悟突破",
            "血脉觉醒",
            "打脸逆袭",
            "暗中布局",
            "一鸣惊人",
            "真相揭露",
            "巧妙推理",
            "身份反转",
            "幕后黑手",
            "告白成功",
            "误会解除",
            "暧昧升温",
            "破镜重圆",
            "科技突破",
            "人机融合",
            "信息差降维打击",
        }
        preferred_patterns_raw = data.get("preferred_patterns", [])
        preferred_patterns: list[str] = []
        if isinstance(preferred_patterns_raw, list):
            for p in preferred_patterns_raw:
                normalized = str(p)[:50]
                if normalized in valid_patterns or len(preferred_patterns) < 10:
                    if normalized not in preferred_patterns:
                        preferred_patterns.append(normalized)

        density_raw = str(data.get("density_per_chapter", "medium")).lower()
        if density_raw not in ("high", "medium", "low"):
            density_raw = "medium"

        return CoolPointConfig(
            preferred_patterns=preferred_patterns,
            density_per_chapter=density_raw,
        )

    @staticmethod
    def _parse_global_style_config(data: dict[str, Any]) -> GlobalStyleConfig:
        if not isinstance(data, dict):
            return GlobalStyleConfig()

        banned_raw = data.get("banned_phrases") or []
        banned_phrases = (
            [str(p)[:50] for p in banned_raw[:30]] if isinstance(banned_raw, list) else []
        )

        return GlobalStyleConfig(
            dialogue_ratio=str(data.get("dialogue_ratio", "medium")),
            pace_mode=str(data.get("pace_mode", "moderate")),
            emotional_style=str(data.get("emotional_style", "balanced")),
            environment_ratio=str(data.get("environment_ratio", "medium")),
            info_density=str(data.get("info_density", "medium")),
            banned_phrases=banned_phrases,
        )

    @staticmethod
    def _merge_results(
        style_data: dict[str, Any], structure_data: dict[str, Any]
    ) -> ProjectStyleProfile:
        """合并两个 LLM 的推导结果。

        Args:
            style_data: PROFILE_STYLE 调用返回的 style 信息
                (modules, summary, global_style)。
            structure_data: PROFILE_STRUCTURE 调用返回的 structure 信息
                (hook_config, strand_config, micro_payoff_config, cool_point_config)。

        Returns:
            合并后的 ProjectStyleProfile。
        """
        # 解析 style 部分
        modules = ProfileStyleStep._parse_modules(style_data.get("modules", []))

        summary = ProfileStyleStep._bounded_text(style_data.get("summary", ""), 80)
        global_style_data = style_data.get("global_style", {})

        # 解析 structure 部分
        hook_config = ProfileStyleStep._parse_hook_config(structure_data.get("hook_config", {}))
        strand_config = ProfileStyleStep._parse_strand_config(
            structure_data.get("strand_config", {})
        )
        micro_payoff_config = ProfileStyleStep._parse_micro_payoff_config(
            structure_data.get("micro_payoff_config", {})
        )
        cool_point_config = ProfileStyleStep._parse_cool_point_config(
            structure_data.get("cool_point_config", {})
        )

        source_elements_raw = style_data.get("source_elements", [])
        source_elements: list[str] = []
        if isinstance(source_elements_raw, list):
            source_elements = [str(item) for item in source_elements_raw if str(item or "").strip()]
        if not source_elements:
            source_elements = ["story_bible", "character_bible"]

        global_style = ProfileStyleStep._parse_global_style_config(global_style_data)

        return ProjectStyleProfile(
            modules=modules,
            source_elements=source_elements,
            summary=summary,
            hook_config=hook_config,
            strand_config=strand_config,
            micro_payoff_config=micro_payoff_config,
            cool_point_config=cool_point_config,
            global_style=global_style,
        )

    @staticmethod
    def _merge_kernel_context(
        context: dict[str, Any],
        kernel_context: dict[str, Any] | None,
    ) -> None:
        """Merge StoryKernel field slices into a template context.

        Kernel fields are prefixed with ``kernel_`` to avoid collision with
        existing context keys.  Explicit context fields always take precedence.
        """
        if not kernel_context:
            return
        for key, value in kernel_context.items():
            prefixed = f"kernel_{key}"
            if prefixed not in context:
                context[prefixed] = value

    async def _execute(self, input_data: ProfileStyleInput) -> ProjectStyleProfile:
        banned_intent_rules = input_data.story_bible.get("banned_intent_rules", [])

        style_context: dict[str, Any] = {
            "title": input_data.title,
            "genre": input_data.genre,
            "tone": input_data.tone,
            "writing_style_mode": input_data.writing_style_mode,
            "story_bible": input_data.story_bible,
            "character_bible": input_data.character_bible,
            "blueprint_elements": input_data.blueprint_elements,
            "extra_instructions": input_data.extra_instructions,
            "banned_intent_rules": banned_intent_rules,
            "synopsis": input_data.synopsis,
            "premise": input_data.premise,
        }

        structure_context: dict[str, Any] = {
            "genre": input_data.genre,
            "tone": input_data.tone,
            "narrative_complexity": input_data.narrative_complexity,
            "synopsis": input_data.synopsis,
            "premise": input_data.premise,
            "blueprint_elements": input_data.blueprint_elements,
        }

        self._merge_kernel_context(style_context, input_data.kernel_context)
        self._merge_kernel_context(structure_context, input_data.kernel_context)
        style_temp = self._settings.temp_profile_style
        structure_temp = getattr(self._settings, "temp_profile_structure", style_temp)

        style_result, structure_result = cast(
            tuple[dict[str, Any] | BaseException, dict[str, Any] | BaseException],
            await asyncio.gather(
                self._call_with_retry(
                    TaskType.PROFILE_STYLE,
                    style_context,
                    max_tokens=self._dynamic_max_tokens(
                        TaskType.PROFILE_STYLE,
                        2800,
                        prompt_overhead=2600,
                        min_tokens=4096,
                    ),
                    temperature=style_temp,
                    required_keys=("modules", "source_elements", "summary", "global_style"),
                ),
                self._call_with_retry(
                    TaskType.PROFILE_STRUCTURE,
                    structure_context,
                    max_tokens=self._dynamic_max_tokens(
                        TaskType.PROFILE_STRUCTURE,
                        1800,
                        prompt_overhead=2200,
                        min_tokens=2048,
                    ),
                    temperature=structure_temp,
                    required_keys=(
                        "hook_config",
                        "strand_config",
                        "micro_payoff_config",
                        "cool_point_config",
                    ),
                ),
                return_exceptions=True,
            ),
        )

        if isinstance(style_result, BaseException):
            # style 分支是核心语料来源，失败时仍沿用原有失败语义。
            raise style_result

        style_data = style_result
        structure_data: dict[str, Any] = {}
        if isinstance(structure_result, BaseException):
            # 并发结构分支失败时，降级为 schema 默认结构参数，
            # 保证 style_profile.json 仍可产出并被后续流程消费。
            self._logger.warning(
                "profile_structure_branch_failed | fallback=default_structure | error=%s",
                structure_result,
            )
        else:
            structure_data = structure_result

        return self._merge_results(style_data, structure_data)
