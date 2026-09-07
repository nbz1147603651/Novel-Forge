"""Batch 3 unit tests: film platform depth-adaptation layer.

Covers:
- platform profile loading, deep-merge semantics and mtime hot reload;
- one intermediate prompt projected onto three platforms within each
  platform's budget / structure / capability contract;
- 方舟 real-face compliance branch (trusted asset vs virtual human);
- capability-driven routing over catalog features;
- deterministic cost estimation integrated with the job ledger.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import novel_forge.film.platform_profiles as profiles_mod
from novel_forge.film.jobs import FilmJobManager
from novel_forge.film.platform_adapter import (
    apply_ark_face_compliance,
    detect_real_face_dependency,
    estimate_generation_cost,
    project_prompt_for_platform,
    select_platform_for_shot,
)
from novel_forge.film.platform_profiles import (
    _deep_merge,
    _normalize_provider,
    invalidate_platform_profile_cache,
    load_platform_profile,
)
from novel_forge.film.schemas import (
    FilmJobKind,
    FilmStudioState,
    FilmTimeline,
    ProductionBible,
    Screenplay,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_platform_profile_cache()
    yield
    invalidate_platform_profile_cache()


# ─── Profiles: loading / merge / hot reload ──────────────────────────────────


class TestPlatformProfiles:
    def test_normalize_aliases(self) -> None:
        assert _normalize_provider("dashscope") == "bailian"
        assert _normalize_provider("ark") == "volcengine_ark"
        assert _normalize_provider("doubao") == "volcengine_ark"
        assert _normalize_provider("hailuo") == "minimax"
        assert _normalize_provider("") == ""

    def test_default_profile_loads(self) -> None:
        profile = load_platform_profile("")
        assert profile["provider_id"] == "default"
        assert profile["char_budget"]["prompt_zh"] == 800
        assert profile["capability_switches"]["multi_shot"] is False

    def test_three_platforms_declare_distinct_contracts(self) -> None:
        bailian = load_platform_profile("bailian")
        ark = load_platform_profile("volcengine_ark")
        minimax = load_platform_profile("minimax")
        # 方舟中文 ≤500 字硬预算
        assert ark["char_budget"]["prompt_zh"] == 500
        # 百炼多镜头叙事，MiniMax H3 时间线结构 + 运镜指令 + 原生音频
        assert bailian["capability_switches"]["multi_shot"] is True
        assert minimax["capability_switches"]["camera_command"] is True
        assert minimax["capability_switches"]["multi_shot"] is True
        assert minimax["capability_switches"]["native_audio"] is True
        assert minimax["capability_switches"]["multi_image_reference"] is True
        assert minimax["structure"]["kind"] == "h3_timeline"
        # 方舟受信演员资产
        assert ark["capability_switches"]["trusted_actor_asset"] is True

    def test_deep_merge_retains_unspecified_default_keys(self) -> None:
        bailian = load_platform_profile("bailian")
        # 百炼只覆盖 4 个运镜词条，其余保留 default 词条
        assert bailian["camera_vocabulary"]["slow_push"] == "镜头缓慢向前推进"
        assert bailian["camera_vocabulary"]["pan"] == "水平摇镜"
        # default 的结构节 section_order 未被平台文件覆盖
        assert bailian["structure"]["section_order"] == [
            "subject",
            "action",
            "camera",
            "lighting",
            "style",
        ]

    def test_deep_merge_list_replaces(self) -> None:
        merged = _deep_merge({"rules": ["a"], "cfg": {"x": 1}}, {"rules": ["b"]})
        assert merged["rules"] == ["b"]
        assert merged["cfg"]["x"] == 1

    def test_mtime_hot_reload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        profile_file = tmp_path / "testplat.json"
        default_file = tmp_path / "default.json"
        default_file.write_text(
            json.dumps({"provider_id": "default", "char_budget": {"prompt_zh": 800}}),
            encoding="utf-8",
        )
        original = {
            "provider_id": "testplat",
            "char_budget": {"prompt_zh": 123},
        }
        profile_file.write_text(json.dumps(original), encoding="utf-8")
        monkeypatch.setattr(profiles_mod, "_PROFILES_DIR", tmp_path)
        invalidate_platform_profile_cache()

        first = load_platform_profile("testplat")
        assert first["char_budget"]["prompt_zh"] == 123

        modified = dict(original)
        modified["char_budget"] = {"prompt_zh": 456}
        profile_file.write_text(json.dumps(modified), encoding="utf-8")
        os.utime(profile_file, ns=(0, int(time.time() * 1e9) + 1_000_000_000))

        second = load_platform_profile("testplat")
        assert second["char_budget"]["prompt_zh"] == 456


# ─── Prompt projection: one rewrite, many platforms ──────────────────────────

_LONG_PROMPT = (
    "。".join(f"第{i}段画面描述：主角在雨夜街角回望，霓虹倒映在积水中" for i in range(40)) + "。"
)


class TestPromptProjection:
    def test_same_intermediate_projects_within_each_platform_budget(self) -> None:
        intermediate = {
            "prompt": _LONG_PROMPT,
            "negative_prompt": "画面过曝",
            "camera_motion": "slow_push",
        }
        for platform in ("bailian", "volcengine_ark", "minimax"):
            profile = load_platform_profile(platform)
            budget = profile["char_budget"]["prompt_zh"]
            projection = project_prompt_for_platform(intermediate, platform)
            assert len(projection.prompt) <= budget, platform
            assert projection.provider_id == platform
            # 负面提示合入平台必需项
            assert projection.negative_prompt
            for term in profile["negative_policy"]["required_terms"]:
                assert term in projection.negative_prompt, platform

    def test_ark_projection_truncates_at_sentence_boundary(self) -> None:
        projection = project_prompt_for_platform({"prompt": _LONG_PROMPT}, "volcengine_ark")
        assert projection.truncated is True
        assert len(projection.prompt) <= 500
        # 句子边界截断：以句号收尾，不出现半句
        assert projection.prompt.endswith("。")

    def test_minimax_projects_camera_commands_in_english(self) -> None:
        projection = project_prompt_for_platform(
            {"prompt": "主角在雨夜街角回望。", "camera_motion": "slow_push"},
            "minimax",
        )
        assert "the camera pushes in with small amplitude at slow speed" in projection.prompt
        assert "[Shot 1]" in projection.prompt

    def test_bailian_projects_camera_vocabulary_in_chinese(self) -> None:
        projection = project_prompt_for_platform(
            {"prompt": "主角在雨夜街角回望。", "camera_motion": "slow_push"},
            "bailian",
        )
        assert "镜头缓慢向前推进" in projection.prompt

    def test_capability_gate_flags_unsupported_demand(self) -> None:
        projection = project_prompt_for_platform(
            {"prompt": "多镜头场次。", "requires_multi_shot": True},
            "volcengine_ark",
        )
        assert projection.capability_gates["multi_shot"] is False
        assert any("multi_shot" in note for note in projection.notes)

    def test_capability_gate_passes_supported_demand(self) -> None:
        projection = project_prompt_for_platform(
            {"prompt": "多镜头场次。", "requires_multi_shot": True},
            "bailian",
        )
        assert projection.capability_gates["multi_shot"] is True
        h3 = project_prompt_for_platform(
            {"prompt": "多镜头场次。", "requires_multi_shot": True},
            "minimax",
        )
        assert h3.capability_gates["multi_shot"] is True


# ─── MiniMax H3 timeline projection (Context-IR grammar) ─────────────────


class TestH3TimelineProjection:
    def test_t2va_renders_three_core_sections(self) -> None:
        projection = project_prompt_for_platform(
            {
                "prompt": "雨夜街角，主角回望。",
                "camera_motion": "static",
                "sound_design": "雨点敲击地面，远处车流声。",
                "alignment_mode": "t2va",
                "duration_s": 6,
            },
            "minimax",
        )
        assert projection.prompt.startswith("integrated_multimodal_description: [Shot 1]")
        assert "\n\noverall_soundscape: 雨点敲击地面，远处车流声。" in projection.prompt
        assert "\n\nnon_diegetic_music: N/A" in projection.prompt
        assert "the camera holds a static shot" in projection.prompt
        # 结构化提示词必须关闭供应商 optimizer，避免结构被改写
        assert projection.flags.get("disable_prompt_optimizer") is True

    def test_i2va_starts_with_alignment_instruction(self) -> None:
        projection = project_prompt_for_platform(
            {"prompt": "女子在雨窗旁。", "alignment_mode": "i2va", "duration_s": 8},
            "minimax",
        )
        first_line = projection.prompt.splitlines()[0]
        assert "<Picture 1> (from [Shot 1]) is fully referenced" in first_line
        assert projection.prompt.split("\n\n")[1].startswith("integrated_multimodal_description:")

    def test_fl2va_formats_duration_to_two_decimals(self) -> None:
        projection = project_prompt_for_platform(
            {"prompt": "骑行者撑伞。", "alignment_mode": "fl2va", "duration_s": 8},
            "minimax",
        )
        assert "aligns with the 8.00-second mark of the target video" in projection.prompt

    def test_l2va_aligns_last_frame_only(self) -> None:
        projection = project_prompt_for_platform(
            {"prompt": "玻璃杯碎裂。", "alignment_mode": "l2va", "duration_s": 6},
            "minimax",
        )
        assert "aligns with the 6.00-second mark" in projection.prompt

    def test_ref2va_omits_alignment_header(self) -> None:
        projection = project_prompt_for_platform(
            {"prompt": "参考角色形象。", "alignment_mode": "ref2va", "duration_s": 5},
            "minimax",
        )
        assert projection.prompt.startswith("integrated_multimodal_description:")

    def test_dialogue_gets_stable_speaker_id_and_language_tag(self) -> None:
        projection = project_prompt_for_platform(
            {
                "prompt": "车厢内。",
                "dialogues": [{"speaker": "林远", "text": "我下一站下车。", "offscreen": False}],
                "alignment_mode": "t2va",
                "duration_s": 6,
            },
            "minimax",
        )
        assert "林远 (S1) says: <d>[Chinese] 我下一站下车。</d>" in projection.prompt

    def test_offscreen_voiceover_states_lips_closed(self) -> None:
        projection = project_prompt_for_platform(
            {
                "prompt": "空镜。",
                "dialogues": [{"speaker": "旁白", "text": "那条路我还记得。", "offscreen": True}],
                "alignment_mode": "t2va",
                "duration_s": 6,
            },
            "minimax",
        )
        assert (
            "says in an off-screen voiceover: <d>[Chinese] 那条路我还记得。</d>"
            in projection.prompt
        )
        assert "lips remain completely closed" in projection.prompt

    def test_music_section_accepts_score_description(self) -> None:
        projection = project_prompt_for_platform(
            {
                "prompt": "雨夜街角。",
                "music": "Sparse piano notes at a slow tempo.",
                "alignment_mode": "t2va",
                "duration_s": 6,
            },
            "minimax",
        )
        assert "non_diegetic_music: Sparse piano notes at a slow tempo." in projection.prompt

    def test_long_body_truncates_within_budget_keeping_sections(self) -> None:
        profile = load_platform_profile("minimax")
        budget = profile["char_budget"]["prompt_zh"]
        projection = project_prompt_for_platform(
            {"prompt": _LONG_PROMPT + _LONG_PROMPT, "alignment_mode": "fl2va", "duration_s": 10},
            "minimax",
        )
        assert len(projection.prompt) <= budget
        assert projection.truncated is True
        assert "overall_soundscape:" in projection.prompt
        assert "non_diegetic_music:" in projection.prompt

    def test_flat_platforms_keep_vendor_neutral_projection(self) -> None:
        projection = project_prompt_for_platform(
            {"prompt": "主角在雨夜街角回望。", "camera_motion": "slow_push"},
            "volcengine_ark",
        )
        assert "integrated_multimodal_description" not in projection.prompt
        assert "缓慢推近" in projection.prompt

    def test_unknown_structure_kind_falls_back_to_flat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        default_file = tmp_path / "default.json"
        default_file.write_text(
            json.dumps({"provider_id": "default", "char_budget": {"prompt_zh": 800}}),
            encoding="utf-8",
        )
        weird_file = tmp_path / "weirdplat.json"
        weird_file.write_text(
            json.dumps({"provider_id": "weirdplat", "structure": {"kind": "unknown_kind"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(profiles_mod, "_PROFILES_DIR", tmp_path)
        invalidate_platform_profile_cache()

        projection = project_prompt_for_platform({"prompt": "降级。"}, "weirdplat")
        assert projection.prompt == "降级。"
        assert any("unknown_kind" in note and "flat" in note for note in projection.notes)


# ─── 方舟 real-face compliance branch ─────────────────────────────────────────


class TestFaceCompliance:
    def test_no_dependency_passes_through(self) -> None:
        decision = apply_ark_face_compliance("雨夜街角空镜。", platform_id="volcengine_ark")
        assert decision.required is False
        assert decision.path == ""

    def test_real_face_keyword_detected(self) -> None:
        assert detect_real_face_dependency("需要真人演员出镜的特写")
        assert detect_real_face_dependency("close-up of a real person")
        assert not detect_real_face_dependency("动画风格角色特写")

    def test_ark_trusted_asset_path(self) -> None:
        decision = apply_ark_face_compliance(
            "真人演员特写镜头。",
            platform_id="volcengine_ark",
            reference_urls=["asset://trusted-actor/lin-yuan"],
        )
        assert decision.required is True
        assert decision.path == "trusted_actor_asset"
        assert decision.run_plan_note

    def test_ark_without_trusted_asset_switches_to_virtual_human(self) -> None:
        decision = apply_ark_face_compliance("真人演员特写镜头。", platform_id="volcengine_ark")
        assert decision.path == "virtual_human"

    def test_other_platform_always_virtual_human(self) -> None:
        decision = apply_ark_face_compliance(
            "真人演员特写镜头。",
            platform_id="bailian",
            reference_urls=["asset://trusted-actor/lin-yuan"],
        )
        assert decision.path == "virtual_human"


# ─── Capability-driven routing ────────────────────────────────────────────────


class TestCapabilityRouting:
    def test_multi_shot_routes_to_bailian(self) -> None:
        selected, scores = select_platform_for_shot({"requires_multi_shot": True})
        assert selected == "bailian"
        # MiniMax-H3 的时间线结构同样原生支持多镜头，平分是可接受语义；
        # 平分下按目录顺序优先百炼。
        assert scores["bailian"] >= scores["minimax"]

    def test_camera_command_routes_to_minimax(self) -> None:
        selected, _ = select_platform_for_shot({"requires_camera_command": True})
        assert selected == "minimax"

    def test_many_references_route_to_minimax_h3(self) -> None:
        selected, _ = select_platform_for_shot({"reference_image_count": 4})
        assert selected == "minimax"

    def test_trusted_actor_routes_to_ark(self) -> None:
        selected, _ = select_platform_for_shot({"requires_trusted_actor": True})
        assert selected == "volcengine_ark"

    def test_preferred_provider_loyalty_on_tie(self) -> None:
        selected, _ = select_platform_for_shot({}, preferred_provider="minimax")
        assert selected == "minimax"


# ─── Cost estimation + job ledger integration ────────────────────────────────


class TestCostEstimation:
    def test_video_cost_scales_with_duration(self) -> None:
        short = estimate_generation_cost("bailian", media_kind="video", duration_s=2)
        long = estimate_generation_cost("bailian", media_kind="video", duration_s=10)
        assert long > short > 0

    def test_image_cost_scales_with_count(self) -> None:
        one = estimate_generation_cost("minimax", media_kind="image", image_count=1)
        four = estimate_generation_cost("minimax", media_kind="image", image_count=4)
        assert pytest.approx(four, rel=1e-6) == one * 4

    def test_resolution_multiplier(self) -> None:
        base = estimate_generation_cost("volcengine_ark", media_kind="video", duration_s=5)
        hd = estimate_generation_cost(
            "volcengine_ark", media_kind="video", duration_s=5, resolution="1080P"
        )
        assert hd > base

    def test_h3_uses_official_per_second_tier(self) -> None:
        # 768P $0.1287/s: 10s clip ≈ $1.287 (ComfyUI price-badge parity).
        cost = estimate_generation_cost(
            "minimax", media_kind="video", duration_s=10, model_id="MiniMax-H3"
        )
        assert pytest.approx(cost, rel=1e-6) == round(0.1287 * 10, 4)

    def test_h3_2k_tier_and_reference_surcharge(self) -> None:
        # 2K $0.1859/s × 6s + 2 extra refs beyond the free five.
        cost = estimate_generation_cost(
            "minimax",
            media_kind="video",
            duration_s=6,
            resolution="2K",
            model_id="MiniMax-H3",
            reference_image_count=7,
        )
        assert pytest.approx(cost, rel=1e-6) == round(0.1859 * 6 + 2 * 0.0572, 4)

    def test_h3_unknown_resolution_falls_back_to_768p_rate(self) -> None:
        # H3 does not support 1080P; the estimator must not apply the
        # generic 1.25× high-res multiplier on top of an invalid tier.
        cost = estimate_generation_cost(
            "minimax",
            media_kind="video",
            duration_s=5,
            resolution="1080P",
            model_id="MiniMax-H3",
        )
        assert pytest.approx(cost, rel=1e-6) == round(0.1287 * 5, 4)

    def test_h3_image_cost_keeps_flat_table(self) -> None:
        cost = estimate_generation_cost(
            "minimax", media_kind="image", image_count=2, model_id="MiniMax-H3"
        )
        assert pytest.approx(cost, rel=1e-6) == round(0.03 * 2, 4)

    def test_unknown_provider_falls_back_to_default(self) -> None:
        cost = estimate_generation_cost("unknown_platform", media_kind="video", duration_s=5)
        assert cost > 0

    def test_job_ledger_records_estimated_cost(self) -> None:
        state = FilmStudioState(
            project_id="p1",
            project_title="测试",
            production_bible=ProductionBible(project_id="p1", title="测试"),
            screenplay=Screenplay(title="测试"),
            timeline=FilmTimeline(name="主时间线"),
        )
        manager = FilmJobManager()
        state, job, created = manager.acquire(
            state,
            kind=FilmJobKind.GENERATE_SHOT,
            target_id="sh-01",
            provider_id="bailian",
            estimated_cost_usd=estimate_generation_cost(
                "bailian", media_kind="video", duration_s=6
            ),
        )
        assert created is True
        assert job.estimated_cost_usd > 0
        assert state.jobs[0].estimated_cost_usd == job.estimated_cost_usd
