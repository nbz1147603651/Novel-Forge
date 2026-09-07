from __future__ import annotations

from novel_forge.core.schemas.spec import StorySpec
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.short.stages.draft import _ensure_style_profile
from novel_forge.pipeline.short_runner import ShortStoryRunner
from novel_forge.pipeline.steps.spec_step import SpecStep


def test_spec_step_ignores_enriched_legacy_writing_style() -> None:
    merged = SpecStep._merge_spec(
        {
            "title": "",
            "genre": "mystery",
            "theme": "退役法医在雪港收到妹妹寄来的死亡预告",
            "tone": "dark",
            "length_target": 3200,
            "language": "zh",
            "characters_hint": "",
            "world_hint": "",
            "conflict_hint": "",
            "pov_hint": "",
            "opening_style": "",
            "ending_style": "",
            "extra_instructions": "",
            "style_tags": [],
            "writing_style": "default",
            "narrative_complexity": "standard",
        },
        {
            "title": "雪港来信",
            "writing_style": "文学",
        },
    )

    spec = StorySpec.model_validate(merged)

    assert spec.title == "雪港来信"
    assert spec.writing_style == "default"


def test_spec_step_makes_ambiguous_pov_hint_explicit() -> None:
    merged = SpecStep._merge_spec(
        {
            "title": "",
            "genre": "romance",
            "theme": "重逢",
            "tone": "warm",
            "length_target": 3000,
            "language": "zh",
            "characters_hint": "",
            "world_hint": "",
            "conflict_hint": "",
            "pov_hint": "女主主视角为主，少量穿插男主视角",
            "opening_style": "",
            "ending_style": "",
            "extra_instructions": "",
            "style_tags": [],
            "writing_style": "default",
            "narrative_complexity": "standard",
        },
        {},
    )

    spec = StorySpec.model_validate(merged)

    assert "第三人称" in spec.pov_hint
    assert "非对话正文禁止使用“我/我们/咱们”" in spec.pov_hint
    assert "女主主视角为主" in spec.pov_hint


def test_spec_step_preserves_explicit_first_person_pov_hint() -> None:
    original = "第一人称，林晚自述"

    assert SpecStep._merge_pov_hint(original, "") == original


def test_spec_step_preserves_existing_pov_hard_rule() -> None:
    original = "叙事人称硬约束：第三人称限知；非对话正文禁止使用我。女主主视角。"

    assert SpecStep._merge_pov_hint(original, "") == original


async def test_spec_step_normalizes_enriched_text_for_plain_zh(
    monkeypatch,
    router,
    builder,
    runtime_settings,
) -> None:
    step = SpecStep(router, builder, settings=runtime_settings)

    async def fake_call_with_retry(*args, **kwargs):
        return {
            "title": "陸雲崢",
            "characters_hint": "沈念卿會重逢",
            "writing_style": "default",
        }

    monkeypatch.setattr(step, "_call_with_retry", fake_call_with_retry)

    spec = await step.run(
        {
            "title": "",
            "genre": "fantasy",
            "theme": "旧梦重逢",
            "tone": "warm",
            "length_target": 3000,
            "language": "zh",
        }
    )

    assert spec.title == "陆云峥"
    assert spec.characters_hint == "沈念卿会重逢"


async def test_short_style_profile_uses_short_synopsis_for_inference(
    monkeypatch,
    router,
    builder,
    tmp_storage,
    runtime_settings,
) -> None:
    captured: dict[str, str] = {}

    class _FakeProfile:
        def model_dump(self, *, mode: str = "json") -> dict[str, object]:
            return {
                "modules": [],
                "source_elements": ["synopsis"],
                "summary": "冷峻悬疑，心理压迫感强。",
                "global_style": {
                    "dialogue_ratio": "medium",
                    "pace_mode": "fast",
                    "emotional_style": "subtle",
                    "environment_ratio": "high",
                    "info_density": "medium",
                    "banned_phrases": [],
                },
            }

    async def fake_run(self, input_data):
        captured["synopsis"] = input_data.synopsis
        captured["premise"] = input_data.premise
        captured["writing_style_mode"] = input_data.writing_style_mode
        captured["narrative_complexity"] = input_data.narrative_complexity
        return _FakeProfile()

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.profile_style_step.ProfileStyleStep.run",
        fake_run,
    )

    runner = ShortStoryRunner(
        router,
        builder,
        tmp_storage,
        settings=runtime_settings,
        max_edit_rounds=1,
    )
    layout = ProjectLayout(tmp_storage.ensure_project_dir("short_style_profile"))
    layout.ensure_dirs()
    spec = StorySpec(
        title="雪港来信",
        genre="mystery",
        theme="退役法医在雪港收到妹妹寄来的死亡预告",
        tone="dark",
        length_target=3200,
        language="zh",
        characters_hint="沈闻（男，退役法医，冷静克制）；沈枝（女，失踪记者，留下延时寄出的信）",
        world_hint="终年暴雪的海港小城，旧码头和冷库构成主要场景",
        conflict_hint="外部：预告中的死者陆续出现；内在：主角怀疑自己当年篡改过尸检结论",
    )

    payload = await _ensure_style_profile(runner, layout, spec, blueprint=None)

    assert payload is not None
    assert "退役法医在雪港收到妹妹寄来的死亡预告" in captured["synopsis"]
    assert "预告中的死者陆续出现" in captured["synopsis"]
    assert "终年暴雪的海港小城" in captured["premise"]
    assert captured["writing_style_mode"] == ""
    assert captured["narrative_complexity"] == "standard"
