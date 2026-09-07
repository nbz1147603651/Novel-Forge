"""BeatsStep — generates StoryBeats from a StorySpec."""

from __future__ import annotations

from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.beats import StoryBeats
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.step_registry import register_step


@register_step("beats")
class BeatsStep(PipelineStep[StorySpec, StoryBeats]):
    """Spec → model → StoryBeats."""

    def __init__(
        self, *args: Any, extra_context: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._extra_context = extra_context or {}

    @property
    def step_name(self) -> str:
        return "beats"

    @staticmethod
    def _normalize_beats_payload(data: Any) -> Any:
        """Coerce known container-type mismatches before model_validate."""
        if not isinstance(data, dict):
            return data
        result = dict(data)
        # beats field: dict-of-dicts → list-of-dicts
        beats_raw = result.get("beats", [])
        if isinstance(beats_raw, dict):
            beats_raw = list(beats_raw.values())
        # Within each beat, characters_involved may come as dict or str
        if isinstance(beats_raw, list):
            normalized: list[Any] = []
            for beat in beats_raw:
                if isinstance(beat, dict):
                    chars = beat.get("characters_involved", [])
                    if isinstance(chars, dict):
                        chars = [v for v in chars.values() if v] or list(chars.keys())
                    elif isinstance(chars, str):
                        chars = [chars.strip()] if chars.strip() else []
                    beat = {**beat, "characters_involved": chars}
                normalized.append(beat)
            if normalized:
                # Enforce a complete short-story arc even when the model omits beat_type.
                peak_idx = 0
                peak_tension = -1
                for idx, beat in enumerate(normalized):
                    if not isinstance(beat, dict):
                        continue
                    try:
                        tension = int(beat.get("tension_level", 0))
                    except Exception:
                        tension = 0
                    if tension >= peak_tension:
                        peak_tension = tension
                        peak_idx = idx
                last_idx = len(normalized) - 1
                for idx, beat in enumerate(normalized):
                    if not isinstance(beat, dict):
                        continue
                    if idx == 0:
                        beat_type = "opening"
                    elif idx == last_idx:
                        beat_type = "resolution"
                    elif idx == peak_idx:
                        beat_type = "climax"
                    elif idx < peak_idx:
                        beat_type = "rising"
                    else:
                        beat_type = "falling"
                    normalized[idx] = {**beat, "beat_type": beat_type}
            result["beats"] = normalized
        return result

    async def _execute(self, input_data: StorySpec) -> StoryBeats:
        # 动态计算 max_tokens：根据目标字数估算节拍数量和描述需求
        # 节拍数 ≈ length_target / 1000, 每个节拍描述 ≈ 100-150 tokens
        length_target = input_data.length_target or 3000
        estimated_beats = max(3, length_target // 1000)  # 最少 3 个节拍
        estimated_output_chars = estimated_beats * 450 + 1200
        max_tokens = self._dynamic_max_tokens(
            TaskType.BEATS,
            estimated_output_chars,
            prompt_overhead=1800,
            min_tokens=2048,
        )

        data = await self._call_with_retry(
            TaskType.BEATS,
            {"spec": input_data, **self._extra_context},
            max_tokens=max_tokens,
            temperature=self.settings.temp_beats,
        )
        return StoryBeats.model_validate(self._normalize_beats_payload(data))
