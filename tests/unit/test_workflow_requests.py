"""Tests for desktop workflow request builders."""

from __future__ import annotations

import pytest

from novel_forge.desktop.pages.chapter_studio.actions import (
    _book_consistency_route_entry,
    _recommended_book_audit_max_tokens,
)
from novel_forge.desktop.workflow_requests import (
    build_book_consistency_request,
    build_init_long_request,
    build_prepare_chapter_request,
    build_reevaluate_chapter_request,
    build_repair_issues_request,
    build_repair_motif_history_request,
    build_resolve_chapter_checkpoint_request,
    build_run_chapter_request,
    build_short_request,
    summarize_job_result,
)
from novel_forge.gateway.profiles import ProfilesConfig, TaskRouteEntry
from novel_forge.workspace.contracts import (
    InitLongRequest,
    PrepareChapterRequest,
    ResolveChapterCheckpointRequest,
    RunChapterRequest,
)


def test_long_chapter_requests_drop_max_edit_rounds() -> None:
    """Long-form WAVE runs once; ``max_edit_rounds`` is no longer a request field."""
    init_req = InitLongRequest(premise="一段足够明确的长篇故事设定")
    run_req = RunChapterRequest(project_id="demo", chapter_number=1)
    prepare_req = PrepareChapterRequest(project_id="demo", chapter_number=1)
    resolve_req = ResolveChapterCheckpointRequest(
        project_id="demo",
        chapter_number=1,
        checkpoint_id="cp1",
        option_id="continue",
    )

    assert "max_edit_rounds" not in type(init_req).model_fields
    assert "max_edit_rounds" not in type(run_req).model_fields
    assert "max_edit_rounds" not in type(prepare_req).model_fields
    assert "max_edit_rounds" not in type(resolve_req).model_fields


def test_build_short_request_requires_theme() -> None:
    with pytest.raises(ValueError, match="故事主题"):
        build_short_request(
            project_id="",
            theme="   ",
            genre="literary",
            tone="warm",
            length_target=3200,
            max_edit_rounds=2,
            segment_trigger_words=5500,
            title="",
            language="zh",
            characters_hint="",
            world_hint="",
            conflict_hint="",
            pov_hint="",
            opening_style="",
            ending_style="",
            extra_instructions="",
        )


def test_recommended_book_audit_max_tokens_tracks_model_limit() -> None:
    assert _recommended_book_audit_max_tokens(None) == 8192
    assert _recommended_book_audit_max_tokens(4096) == 4096
    assert _recommended_book_audit_max_tokens(8192) == 8192
    assert _recommended_book_audit_max_tokens(16384) == 12288
    assert _recommended_book_audit_max_tokens(32768) == 16384
    assert _recommended_book_audit_max_tokens(384000) == 16384


def test_book_consistency_route_entry_accepts_saved_task_key() -> None:
    config = ProfilesConfig(routes={"book_consistency": TaskRouteEntry(profile_id="deepseek:v4")})

    assert _book_consistency_route_entry(config).profile_id == "deepseek:v4"


def test_book_consistency_route_entry_accepts_legacy_enum_name() -> None:
    config = ProfilesConfig(routes={"BOOK_CONSISTENCY": TaskRouteEntry(profile_id="deepseek:v4")})

    assert _book_consistency_route_entry(config).profile_id == "deepseek:v4"


def test_build_short_request_maps_segment_trigger_to_segmented_mode() -> None:
    request = build_short_request(
        project_id="short_demo",
        theme="风暴中的旧港来信",
        genre="mystery",
        tone="dark",
        length_target=6200,
        max_edit_rounds=2,
        segment_trigger_words=5500,
        title="",
        language="zh",
        characters_hint="",
        world_hint="",
        conflict_hint="",
        pov_hint="",
        opening_style="",
        ending_style="",
        extra_instructions="",
        research_enabled=True,
        research_provider="mcp_search",
        research_query_hint="港口汛期档案制度",
    )
    assert request.segmented_mode == "on"
    assert request.writing_mode == "auto"
    assert request.research_enabled is True
    assert request.research_provider == "mcp_search"
    assert request.research_query_hint == "港口汛期档案制度"

    request = build_short_request(
        project_id="short_demo",
        theme="风暴中的旧港来信",
        genre="mystery",
        tone="dark",
        length_target=3200,
        max_edit_rounds=2,
        segment_trigger_words=5500,
        title="",
        language="zh",
        characters_hint="",
        world_hint="",
        conflict_hint="",
        pov_hint="",
        opening_style="",
        ending_style="",
        extra_instructions="",
    )
    assert request.segmented_mode == "off"


def test_build_short_request_carries_writing_mode() -> None:
    request = build_short_request(
        project_id="short_demo",
        theme="风暴中的旧港来信",
        genre="mystery",
        tone="dark",
        length_target=3200,
        max_edit_rounds=2,
        segment_trigger_words=5500,
        writing_mode="scene_level",
        title="",
        language="zh",
        characters_hint="",
        world_hint="",
        conflict_hint="",
        pov_hint="",
        opening_style="",
        ending_style="",
        extra_instructions="",
    )

    assert request.writing_mode == "scene_level"


def test_build_short_request_carries_blueprint_preferences() -> None:
    request = build_short_request(
        project_id="short_demo",
        theme="风暴中的旧港来信",
        genre="mystery",
        tone="dark",
        length_target=3200,
        max_edit_rounds=2,
        segment_trigger_words=5500,
        title="",
        language="zh",
        characters_hint="",
        world_hint="",
        conflict_hint="",
        pov_hint="",
        opening_style="",
        ending_style="",
        extra_instructions="",
        blueprint_element_preferences={
            "preset_id": "mystery",
            "manual_override": False,
            "items": [{"element_id": "mystery_clue_ledger", "enabled": True, "weight": 95}],
        },
    )
    assert request.blueprint_element_preferences["preset_id"] == "mystery"


def test_build_init_long_request_carries_blueprint_preferences() -> None:
    request = build_init_long_request(
        project_id="long_demo",
        premise="失忆调查员追查一段被删除的城市历史",
        genre="scifi",
        tone="suspenseful",
        total_chapters=20,
        words_per_chapter=4200,
        volume_mode="auto",
        chapters_per_volume=0,
        title="",
        language="zh",
        characters_hint="",
        world_hint="",
        conflict_hint="",
        pov_hint="",
        opening_style="",
        ending_style="",
        extra_instructions="",
        polish_hint="强化前五章的悬念递进",
        blueprint_element_preferences={
            "preset_id": "scifi",
            "manual_override": True,
            "items": [{"element_id": "scifi_rule_reveal", "enabled": True, "weight": 98}],
        },
    )
    assert request.blueprint_element_preferences["manual_override"] is True
    assert request.polish_hint == "强化前五章的悬念递进"


def test_build_run_chapter_request_requires_project_id() -> None:
    with pytest.raises(ValueError, match="项目 ID"):
        build_run_chapter_request(
            project_id=" ",
            chapter_number=2,
            force=False,
        )


def test_chapter_request_builders_preserve_repair_control_mode() -> None:
    run_request = build_run_chapter_request(
        project_id="long_demo",
        chapter_number=2,
        force=False,
        repair_control_mode="ai_auto",
    )
    prepare_request = build_prepare_chapter_request(
        project_id="long_demo",
        chapter_number=2,
        force=False,
        notes="",
        repair_control_mode="ai_assisted",
    )
    resolve_request = build_resolve_chapter_checkpoint_request(
        project_id="long_demo",
        chapter_number=2,
        checkpoint_id="cp-1",
        option_id="write_now",
        notes="",
        repair_control_mode="manual",
    )
    repair_request = build_repair_issues_request(
        project_id="long_demo",
        chapter_number=2,
        continuity_issue_indices=[0],
        causal_issue_indices=[],
        repair_control_mode="ai_auto",
    )

    assert run_request.repair_control_mode == "ai_auto"
    assert prepare_request.repair_control_mode == "ai_assisted"
    assert resolve_request.repair_control_mode == "manual"
    assert repair_request.repair_control_mode == "ai_auto"


def test_summarize_job_result_includes_tokens_only() -> None:
    text = summarize_job_result(
        {"word_count": 3200, "overall_score": 8.6, "tokens_used": 1234, "cost_usd": 0.0123}
    )
    assert "3,200 字" in text
    assert "质量评分 8.6" in text
    assert "Token 消耗 1234" in text
    assert "成本" not in text


def test_summarize_job_result_accepts_total_tokens_alias() -> None:
    text = summarize_job_result({"word_count": 1800, "overall_score": 7.9, "total_tokens": 987})
    assert "1,800 字" in text
    assert "质量评分 7.9" in text
    assert "Token 消耗 987" in text


def test_summarize_job_result_includes_causal_score_and_warning_count() -> None:
    text = summarize_job_result(
        {
            "word_count": 2600,
            "overall_score": 8.1,
            "metadata": {
                "causal_score": 9.2,
                "warnings": ["因果链校验发现 2 个问题，其中高优先级 1 个，建议人工复核。"],
            },
        }
    )

    assert "2,600 字" in text
    assert "质量评分 8.1" in text
    assert "因果 9.2" in text
    assert "1 条提醒" in text


def test_build_prepare_chapter_request_requires_project_id() -> None:
    with pytest.raises(ValueError, match="项目 ID"):
        build_prepare_chapter_request(
            project_id=" ",
            chapter_number=2,
            force=False,
            notes="",
        )


def test_build_resolve_chapter_checkpoint_request_requires_checkpoint() -> None:
    with pytest.raises(ValueError, match="章节决策"):
        build_resolve_chapter_checkpoint_request(
            project_id="long_demo",
            chapter_number=2,
            checkpoint_id=" ",
            option_id="write_now",
            notes="",
        )


def test_build_repair_motif_history_request_requires_project_id() -> None:
    with pytest.raises(ValueError, match="项目 ID"):
        build_repair_motif_history_request(
            project_id=" ",
            chapter_number=2,
        )


def test_build_reevaluate_chapter_request_requires_project_id() -> None:
    with pytest.raises(ValueError, match="项目 ID"):
        build_reevaluate_chapter_request(
            project_id=" ",
            chapter_number=2,
        )


def test_build_book_consistency_request_forces_audit_only_boundary() -> None:
    request = build_book_consistency_request(
        project_id="long_demo",
        chapter_range=[1, 2, 3],
        analysis_mode="full_text",
        prompt_hint="优先检查人物称谓。",
        location_strictness="strict",
        max_tokens=9000,
        temperature=0.25,
        repair_mode="targeted",
        audit_max_chapters_per_batch=8,
        audit_max_issues_per_chunk=9,
        audit_issue_pool_max_items=120,
        repair_min_severity="warning",
        repair_max_chapters=6,
        allow_exhausted_retry=True,
        use_issue_panel_pool=True,
        repair_concurrency=2,
        generate_repair_report=True,
        post_repair_targeted_audit=True,
        repair_guard_enabled=True,
        repair_guard_max_delta_ratio=0.1,
        repair_guard_max_added_chars=500,
        two_phase_enabled=True,
        two_phase_threshold=0.65,
        two_phase_max_target_chapters=18,
    )

    assert request.project_id == "long_demo"
    assert request.chapter_range == [1, 2, 3]
    assert request.analysis_mode == "full_text"
    assert request.location_strictness == "strict"
    assert request.repair_mode == "off"
    assert request.audit_max_chapters_per_batch == 8
    assert request.audit_max_issues_per_chunk == 9
    assert request.audit_issue_pool_max_items == 120
    assert request.use_issue_panel_pool is True
    assert request.repair_concurrency == 2
    assert request.generate_repair_report is True
    assert request.post_repair_targeted_audit is True
    assert request.repair_guard_enabled is True
    assert request.repair_guard_max_delta_ratio == 0.1
    assert request.repair_guard_max_added_chars == 500
    assert request.two_phase_enabled is True
    assert request.two_phase_threshold == 0.65
    assert request.two_phase_max_target_chapters == 18
