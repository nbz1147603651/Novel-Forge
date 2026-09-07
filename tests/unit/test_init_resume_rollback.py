"""Regression tests for init-long resume rollback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.core.schemas.bible import (
    CharacterBible,
    CharacterKnowledgeBoundary,
    CharacterProfile,
    StoryBible,
)
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.init import init_contract_flow as init_contract_impl
from novel_forge.pipeline.long.services.init import init_orchestrator as init_impl
from novel_forge.pipeline.long.services.init import init_service as init_service_module
from novel_forge.pipeline.long.services.init import init_source_resume as init_resume_impl
from novel_forge.pipeline.long.services.init import init_story_bible as init_story_impl
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _save_outline_batch_checkpoint,
)
from novel_forge.pipeline.long.services.init.init_service import (
    _character_bible_semantic_hash,
    _character_knowledge_boundaries_complete,
    _claim_coverage_checkpoint_path,
    _classify_init_readiness_resume,
    _detect_init_resume_anchor,
    _init_readiness_blocks_only_claim_coverage,
    _init_readiness_blocks_only_contract_coherence,
    _init_readiness_blocks_only_source_artifacts,
    _late_init_resume_base_readiness,
    _load_cached_model_or_rollback,
    _save_claim_coverage_checkpoint,
    _source_artifact_contract_repair_report,
    _source_artifacts_resume_base_readiness,
    claim_coverage_checkpoint_matches,
)
from novel_forge.pipeline.long.services.init.init_v2 import hash_payload


def test_invalid_cached_init_artifact_quarantines_downstream(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("resume_rollback"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        on_step=lambda step, data: events.append((step, data)),
    )

    storage.save_json(layout.bible_path, {"premise": "已完成的世界观"})
    storage.save_json(layout.characters_path, {"characters": "bad-cache"})
    storage.save_json(layout.blueprint_path, {"synopsis": "downstream"})
    storage.save_json(layout.outline_path, {"total_chapters": 2, "chapters": []})
    storage.save_json(layout.narrative_contract_path, {"rules": ["downstream"]})
    storage.save_json(layout.canon_dir / "canon_current.json", {"project_id": "resume_rollback"})

    result = _load_cached_model_or_rollback(
        ctx,  # type: ignore[arg-type]
        "character_bible",
        layout.characters_path,
        CharacterBible.model_validate,
    )

    assert result is None
    assert layout.bible_path.exists()
    assert not layout.characters_path.exists()
    assert not layout.blueprint_path.exists()
    assert not layout.outline_path.exists()
    assert not layout.narrative_contract_path.exists()
    assert not (layout.canon_dir / "canon_current.json").exists()
    assert events and events[0][0] == "init_resume_rollback"

    rollback_dirs = list((layout.states_dir / "init_rollbacks").glob("*_character_bible"))
    assert len(rollback_dirs) == 1
    rollback_root = rollback_dirs[0]
    assert (rollback_root / "character_bible.json").exists()
    assert (rollback_root / "plans" / "narrative_blueprint.json").exists()
    assert (rollback_root / "outline.json").exists()
    assert (rollback_root / "plans" / "narrative_contract.json").exists()
    assert (rollback_root / "canon" / "canon_current.json").exists()


def test_invalid_cached_style_profile_quarantines_blueprint(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("style_resume_rollback"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        on_step=lambda step, data: events.append((step, data)),
    )
    storage.save_json(layout.style_profile_path, {"summary": "bad-cache"})
    storage.save_json(layout.blueprint_path, {"synopsis": "depends on old style"})
    storage.save_json(layout.outline_path, {"total_chapters": 2, "chapters": []})
    storage.save_json(layout.narrative_contract_path, {"rules": ["downstream"]})

    def _reject_style(_payload: object) -> object:
        raise ValueError("invalid style cache")

    result = _load_cached_model_or_rollback(
        ctx,  # type: ignore[arg-type]
        "style_profile",
        layout.style_profile_path,
        _reject_style,
    )

    assert result is None
    assert not layout.style_profile_path.exists()
    assert not layout.blueprint_path.exists()
    assert not layout.outline_path.exists()
    assert not layout.narrative_contract_path.exists()
    assert events and events[0][0] == "init_resume_rollback"
    rollback_dirs = list((layout.states_dir / "init_rollbacks").glob("*_style_profile"))
    assert len(rollback_dirs) == 1
    rollback_root = rollback_dirs[0]
    assert (rollback_root / "style_profile.json").exists()
    assert (rollback_root / "plans" / "narrative_blueprint.json").exists()


def test_init_resume_rollback_refuses_when_chapters_exist(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("resume_rollback_guard"))
    layout.ensure_dirs()
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        coerce_character_bible=CharacterBible.model_validate,
        on_step=lambda _step, _data: None,
    )

    storage.save_json(layout.characters_path, {"characters": "bad-cache"})
    storage.save_text(layout.chapter_path(1), "已经归档的正文")

    with pytest.raises(RuntimeError, match="finalized chapters"):
        _load_cached_model_or_rollback(
            ctx,  # type: ignore[arg-type]
            "character_bible",
            layout.characters_path,
            CharacterBible.model_validate,
        )

    assert layout.characters_path.exists()


def test_init_resume_anchor_ignores_unverified_outline_session_progress(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("resume_outline_gap"))
    layout.ensure_dirs()
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        coerce_character_bible=CharacterBible.model_validate,
        on_step=lambda _step, _data: None,
    )

    storage.save_json(
        layout.outline_session_path,
        {
            "total_chapters": 5,
            "chapters_done": 4,
            "latest_chapter_number": 5,
            "conversation_history": [],
        },
    )
    checkpoint_dir = layout.states_dir / "outline_batches"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    chapters = [
        ChapterOutline(
            chapter_number=number,
            title=f"第{number}章",
            goal="推进主线",
            beats_summary=["主线继续推进。"],
            main_plot_points=["主线继续推进。"],
        ).model_dump(mode="json")
        for number in (1, 2, 4, 5)
    ]
    storage.save_json(
        checkpoint_dir / "batch_001_005.json",
        {
            "schema_version": "1.0",
            "status": "accepted",
            "total_chapters": 5,
            "batch_start": 1,
            "batch_end": 5,
            "accepted_chapters": [1, 2, 4, 5],
            "missing_chapters": [3],
            "chapters": chapters,
        },
    )

    anchor = _detect_init_resume_anchor(ctx, total_chapters=5)  # type: ignore[arg-type]

    assert anchor is None


def test_init_resume_anchor_uses_first_missing_verified_outline_chapter(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("resume_outline_verified_gap"))
    layout.ensure_dirs()
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        on_step=lambda _step, _data: None,
    )
    session_id = "verified-session"
    checkpoint = _save_outline_batch_checkpoint(
        storage,
        layout,
        total_chapters=5,
        batch_start=1,
        batch_end=2,
        chapters=[
            ChapterOutline(
                chapter_number=number,
                title=f"第{number}章",
                goal="推进主线",
                beats_summary=["主线继续推进。"],
                main_plot_points=["主线继续推进。"],
            )
            for number in (1, 2)
        ],
        missing_chapters=[],
        status="accepted",
        session_id=session_id,
    )
    storage.save_json(
        layout.outline_session_path,
        {
            "schema_version": "1.1",
            "status": "recoverable",
            "session_id": session_id,
            "total_chapters": 5,
            "chapters_done": 2,
            "latest_chapter_number": 2,
            "last_safe_chapter": 2,
            "conversation_history": [],
            "accepted_batches": [
                {
                    "batch_start": 1,
                    "batch_end": 2,
                    "accepted_chapters": [1, 2],
                    "checkpoint_file": "batch_001_002.json",
                    "content_hash": checkpoint["content_hash"],
                    "source_hash": checkpoint.get("source_hash", ""),
                    "session_id": session_id,
                }
            ],
        },
    )

    anchor = _detect_init_resume_anchor(ctx, total_chapters=5)  # type: ignore[arg-type]

    assert anchor == {
        "step": "plan_outline",
        "artifact": "outline_session",
        "label": "章节大纲",
        "chapters_done": 2,
        "chapters_total": 5,
        "next_chapter": 3,
        "partial": True,
        "recovered_from_session": True,
    }


def test_init_resume_anchor_prefers_verified_session_over_canonical_outline(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("resume_prefers_session"))
    layout.ensure_dirs()
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        coerce_character_bible=CharacterBible.model_validate,
        on_step=lambda _step, _data: None,
    )
    full_outline = StoryOutline(
        total_chapters=5,
        synopsis="旧大纲",
        chapters=[
            ChapterOutline(
                chapter_number=number,
                title=f"旧第{number}章",
                goal="旧主线",
                beats_summary=["旧主线继续推进。"],
                main_plot_points=["旧主线继续推进。"],
            )
            for number in range(1, 6)
        ],
    )
    storage.save_json(layout.outline_path, full_outline.model_dump(mode="json"))
    session_id = "verified-session"
    checkpoint = _save_outline_batch_checkpoint(
        storage,
        layout,
        total_chapters=5,
        batch_start=1,
        batch_end=2,
        chapters=[
            ChapterOutline(
                chapter_number=number,
                title=f"新第{number}章",
                goal="新主线",
                beats_summary=["新主线继续推进。"],
                main_plot_points=["新主线继续推进。"],
            )
            for number in (1, 2)
        ],
        missing_chapters=[],
        status="accepted",
        session_id=session_id,
    )
    storage.save_json(
        layout.outline_session_path,
        {
            "schema_version": "1.1",
            "status": "running",
            "session_id": session_id,
            "total_chapters": 5,
            "chapters_done": 2,
            "latest_chapter_number": 2,
            "last_safe_chapter": 2,
            "conversation_history": [],
            "accepted_batches": [
                {
                    "batch_start": 1,
                    "batch_end": 2,
                    "accepted_chapters": [1, 2],
                    "checkpoint_file": "batch_001_002.json",
                    "content_hash": checkpoint["content_hash"],
                    "source_hash": checkpoint.get("source_hash", ""),
                    "session_id": session_id,
                }
            ],
        },
    )

    anchor = _detect_init_resume_anchor(ctx, total_chapters=5)  # type: ignore[arg-type]

    assert anchor is not None
    assert anchor["artifact"] == "outline_session"
    assert anchor["chapters_done"] == 2
    assert anchor["next_chapter"] == 3


def test_init_resume_anchor_keeps_chapter_contracts_after_failed_coherence(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("resume_contracts"))
    layout.ensure_dirs()
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        settings=SimpleNamespace(init_coherence_block_min_severity="high"),
        on_step=lambda _step, _data: None,
    )

    outline = StoryOutline(
        total_chapters=2,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=number,
                title=f"第{number}章",
                goal="推进主线",
                beats_summary=["完成本章事件。"],
                main_plot_points=["主线推进。"],
            )
            for number in (1, 2)
        ],
    )
    storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
    storage.save_json(
        layout.plans_dir / "chapter_contracts.json",
        {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "第1章",
                    "required_events": ["完成本章事件。"],
                    "completion_criteria": ["主线推进可被正文观察。"],
                    "source": "plan_chapter_contracts",
                }
            ],
            "coverage": {"local_fallback_accepted": True},
        },
    )
    storage.save_json(
        layout.reports_dir / "contract_coherence.json",
        {
            "artifact": "chapter_contracts",
            "verdict": "needs_repair",
            "blocked": True,
            "summary": "仍需修复。",
            "issues": [{"severity": "high", "description": "测试阻断问题"}],
        },
    )

    anchor = _detect_init_resume_anchor(ctx, total_chapters=2)  # type: ignore[arg-type]

    assert anchor == {
        "step": "plan_chapter_contracts",
        "artifact": "chapter_contracts",
        "label": "章节契约",
    }


def test_init_resume_anchor_keeps_failed_readiness_as_high_water(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("resume_failed_readiness"))
    layout.ensure_dirs()
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        on_step=lambda _step, _data: None,
    )

    outline = StoryOutline(
        total_chapters=2,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=number,
                title=f"第{number}章",
                goal="推进主线",
                beats_summary=["完成本章事件。"],
                main_plot_points=["主线推进。"],
            )
            for number in (1, 2)
        ],
    )
    storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
    storage.save_json(
        layout.reports_dir / "init_readiness.json",
        {
            "allowed": False,
            "summary": "初始化准入未通过，需修复。",
            "stages": {
                "outline_inheritance": {
                    "verdict": "needs_repair",
                    "blocked": True,
                    "issue_count": 1,
                }
            },
        },
    )

    anchor = _detect_init_resume_anchor(ctx, total_chapters=2)  # type: ignore[arg-type]

    assert anchor == {
        "step": "init_readiness",
        "artifact": "init_readiness",
        "label": "初始化准入未通过",
        "allowed": False,
        "blocked": True,
        "summary": "初始化准入未通过，需修复。",
    }


def test_init_resume_anchor_uses_source_artifacts_repair_when_only_source_blocked(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("resume_source_artifacts"))
    layout.ensure_dirs()
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        on_step=lambda _step, _data: None,
    )
    outline = StoryOutline(
        total_chapters=2,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=number,
                title=f"第{number}章",
                goal="推进主线",
                beats_summary=["完成本章事件。"],
                main_plot_points=["主线推进。"],
            )
            for number in (1, 2)
        ],
    )
    storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
    storage.save_json(
        layout.reports_dir / "init_readiness.json",
        {
            "allowed": False,
            "summary": "source_artifacts 阻断。",
            "stages": {
                "blueprint_coherence": {"verdict": "accept", "blocked": False},
                "outline_inheritance": {"verdict": "accept", "blocked": False},
                "contract_coherence": {"verdict": "accept", "blocked": False},
                "claim_contract_coverage": {"verdict": "accept", "blocked": False},
                "source_artifacts": {"verdict": "needs_repair", "blocked": True},
            },
            "remaining_issues": [
                {"stage": "source_artifacts", "type": "unresolved_contract_entity"}
            ],
        },
    )
    monkeypatch.setattr(
        init_story_impl,
        "_source_artifacts_resume_prereqs_exist",
        lambda _ctx, *, outline: True,
    )

    anchor = _detect_init_resume_anchor(ctx, total_chapters=2)  # type: ignore[arg-type]

    assert anchor == {
        "step": "init_source_artifacts",
        "artifact": "source_artifacts",
        "label": "源头准入修复",
        "allowed": False,
        "blocked": True,
        "resumable": True,
        "summary": "source_artifacts 阻断。",
    }


def test_source_artifacts_resume_requires_only_source_stage_blocked() -> None:
    assert _init_readiness_blocks_only_source_artifacts(
        {
            "allowed": False,
            "stages": {
                "contract_coherence": {"blocked": False},
                "source_artifacts": {"blocked": True},
            },
            "remaining_issues": [{"stage": "source_artifacts"}],
        }
    )
    assert not _init_readiness_blocks_only_source_artifacts(
        {
            "allowed": False,
            "stages": {
                "contract_coherence": {"blocked": True},
                "source_artifacts": {"blocked": True},
            },
            "remaining_issues": [
                {"stage": "contract_coherence"},
                {"stage": "source_artifacts"},
            ],
        }
    )


def test_init_resume_classification_distinguishes_source_artifacts_only() -> None:
    classification = _classify_init_readiness_resume(
        {
            "allowed": False,
            "stages": {
                "contract_coherence": {"blocked": False},
                "source_artifacts": {"blocked": True},
            },
            "remaining_issues": [{"stage": "source_artifacts"}],
        }
    )

    assert classification.mode == "source_artifacts_only"
    assert classification.step == "init_source_artifacts"
    assert classification.source_artifacts_only is True
    assert classification.reason == "source_artifacts_only_blocked"


def test_init_resume_classification_distinguishes_claim_coverage_only() -> None:
    readiness = {
        "allowed": False,
        "stages": {
            "contract_coherence": {"blocked": False},
            "claim_contract_coverage": {"blocked": True},
            "source_artifacts": {"blocked": False},
        },
        "remaining_issues": [{"stage": "claim_contract_coverage"}],
    }

    assert _init_readiness_blocks_only_claim_coverage(readiness)
    classification = _classify_init_readiness_resume(readiness)
    assert classification.mode == "claim_coverage"
    assert classification.step == "init_claim_contract_coverage"
    assert classification.reason == "claim_contract_coverage_blocked"

    clean = _late_init_resume_base_readiness(
        readiness,
        blocked_stage="claim_contract_coverage",
    )
    assert clean["allowed"] is True
    assert "claim_contract_coverage" not in clean["stages"]
    assert clean["remaining_issues"] == []


def test_contract_coherence_resume_requires_only_contract_stage_blocked() -> None:
    readiness = {
        "allowed": False,
        "stages": {
            "blueprint_coherence": {"blocked": False},
            "outline_inheritance": {"blocked": False},
            "contract_coherence": {"blocked": True},
            "source_artifacts": {"blocked": False},
        },
        "remaining_issues": [{"stage": "contract_coherence"}],
    }

    assert _init_readiness_blocks_only_contract_coherence(readiness)
    classification = _classify_init_readiness_resume(readiness)
    assert classification.mode == "contract_coherence"
    assert classification.step == "adjudicate_contract_coherence"
    assert classification.reason == "contract_coherence_blocked"

    readiness["stages"]["source_artifacts"]["blocked"] = True
    assert not _init_readiness_blocks_only_contract_coherence(readiness)


def test_claim_coverage_checkpoint_is_hash_bound_to_current_artifacts(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("coverage_checkpoint"))
    layout.ensure_dirs()
    ctx = SimpleNamespace(storage=storage, layout=layout)
    artifacts = {
        "blueprint": {"title": "v1"},
        "outline": {"chapters": [{"chapter_number": 1}]},
        "narrative_contract": {"plot_threads": []},
        "chapter_contracts": {"chapter_contracts": [{"chapter_number": 1}]},
    }

    _save_claim_coverage_checkpoint(
        ctx,  # type: ignore[arg-type]
        status="needs_repair",
        artifacts=artifacts,
        report={
            "verdict": "needs_repair",
            "blocked": True,
            "items": [
                {"claim_id": "claim_1", "coverage_status": "uncovered"},
            ],
        },
        repair_round=1,
        strategy="deterministic_upsert",
    )

    assert storage.exists(_claim_coverage_checkpoint_path(layout))
    assert claim_coverage_checkpoint_matches(ctx, artifacts=artifacts)  # type: ignore[arg-type]
    assert not claim_coverage_checkpoint_matches(
        ctx,  # type: ignore[arg-type]
        artifacts={**artifacts, "outline": {"chapters": [{"chapter_number": 2}]}},
    )


def test_claim_coverage_checkpoint_accepts_legacy_progressive_frontier_hash(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("coverage_checkpoint_legacy_progressive"))
    layout.ensure_dirs()
    ctx = SimpleNamespace(storage=storage, layout=layout)
    source_artifacts = {
        "blueprint": {"title": "v1"},
        "outline": {
            "total_chapters": 8,
            "hard_through_chapter": 3,
            "planned_through_chapter": 5,
            "chapters": [{"chapter_number": number} for number in range(1, 6)],
        },
        "narrative_contract": {"plot_threads": []},
        "chapter_contracts": {
            "chapter_contracts": [{"chapter_number": number} for number in range(1, 4)]
        },
    }
    legacy_artifacts = {
        **source_artifacts,
        "outline": {
            **source_artifacts["outline"],
            "total_chapters": 3,
            "hard_through_chapter": 3,
            "planned_through_chapter": 3,
            "chapters": [{"chapter_number": number} for number in range(1, 4)],
        },
    }

    _save_claim_coverage_checkpoint(
        ctx,  # type: ignore[arg-type]
        status="needs_repair",
        artifacts=legacy_artifacts,
        report={"items": []},
        repair_round=1,
        strategy="audit",
    )

    assert claim_coverage_checkpoint_matches(
        ctx, artifacts=source_artifacts  # type: ignore[arg-type]
    )
    assert not claim_coverage_checkpoint_matches(
        ctx,
        artifacts={**source_artifacts, "blueprint": {"title": "changed"}},
    )


def test_source_artifacts_resume_base_readiness_removes_old_source_block() -> None:
    readiness = {
        "allowed": False,
        "blocked": True,
        "verdict": "needs_repair",
        "summary": "source_artifacts 阻断。",
        "stages": {
            "contract_coherence": {"blocked": False},
            "source_artifacts": {"blocked": True},
        },
        "remaining_issues": [{"stage": "source_artifacts", "type": "unresolved_contract_entity"}],
    }

    clean = _source_artifacts_resume_base_readiness(readiness)

    assert clean["allowed"] is True
    assert clean["blocked"] is False
    assert clean["verdict"] == "accept"
    assert "source_artifacts" not in clean["stages"]
    assert clean["remaining_issues"] == []
    assert "source_artifacts_resume_from" in clean


def test_source_artifact_repair_report_allows_structured_new_candidates() -> None:
    readiness_artifact = SimpleNamespace(
        blocking_issues=[
            {
                "code": "unresolved_contract_entity",
                "message": "第 2 章契约引用未登记角色/实体：朝堂众臣",
                "severity": "critical",
                "source": "chapter_contract_index",
                "path": "by_chapter.2",
            }
        ]
    )
    report = _source_artifact_contract_repair_report(
        readiness_artifact,
        chapter_contracts={
            "chapter_contracts": [
                {
                    "chapter_number": 2,
                    "knowledge_ops": ["朝堂众臣得知魂玉异动。"],
                    "required_events": ["朝堂众臣施压。"],
                }
            ]
        },
        entity_catalog={"allowed_entities": [], "policy": "测试"},
    )

    assert report is not None
    scope_fields = report["repair_scope"][0]["fields"]
    assert "new_group_candidates" in scope_fields
    assert "new_concept_candidates" in scope_fields
    assert "new_group_candidates" in report["change_intent"]


async def test_init_long_project_source_artifacts_resume_skips_upstream_generation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("source_fast_path"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        config=SimpleNamespace(
            volume_auto_chapter_threshold=9999,
            volume_auto_word_threshold=999999,
            default_chapters_per_volume=10,
        ),
        settings=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
        trace=SimpleNamespace(summary=lambda: {}),
    )
    outline = StoryOutline(
        total_chapters=2,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=number,
                title=f"第{number}章",
                goal="推进主线",
                beats_summary=["完成本章事件。"],
                main_plot_points=["主线推进。"],
            )
            for number in (1, 2)
        ],
    )
    bundle = init_service_module.SourceArtifactsResumeBundle(
        spec=StorySpec(theme="测试故事", genre="xuanhuan", tone="serious", length_target=6000),
        story_bible=StoryBible(premise="测试故事"),
        character_bible=CharacterBible(characters=[CharacterProfile(name="沈珩")]),
        character_system={"characters": []},
        entity_registry=SimpleNamespace(entities=[]),  # type: ignore[arg-type]
        entity_graph={"entities": []},
        style_profile=None,
        creative_packet={"direction": "稳态"},
        blueprint=SimpleNamespace(model_dump=lambda mode="json": {}),  # type: ignore[arg-type]
        outline=outline,
        narrative_contract={"llm_contract": {}},
        chapter_contracts={"chapter_contracts": []},
        readiness_report={"allowed": True},
        coherence_reports={"source_artifacts": None},
        outline_ctx={},
        total_chapters=2,
    )
    calls: list[str] = []

    async def fail_spec_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("SpecStep.run should not be called on source-artifacts resume")

    async def fake_persist(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append("persist_source_artifacts")
        return kwargs["chapter_contracts"]  # type: ignore[return-value]

    async def fake_finish(*_args: object, **kwargs: object) -> SimpleNamespace:
        calls.append("finish_tail")
        return SimpleNamespace(project_id=kwargs["project_id"])

    monkeypatch.setattr(init_impl, "build_init_context", lambda *_args: ctx)
    monkeypatch.setattr(
        init_impl,
        "_ensure_long_init_request_fresh",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        init_impl,
        "_detect_init_resume_anchor",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        init_impl,
        "_load_source_artifacts_resume_bundle",
        lambda *_args, **_kwargs: bundle,
    )
    monkeypatch.setattr(init_impl, "_persist_source_artifacts_from_init", fake_persist)
    monkeypatch.setattr(init_impl, "_finish_init_after_source_artifacts", fake_finish)
    monkeypatch.setattr(init_impl.SpecStep, "run", fail_spec_run)

    result = await init_impl.init_long_project(
        SimpleNamespace(),
        "测试故事",
        project_id="source_fast_path",
        total_chapters=2,
    )

    assert result.project_id == "source_fast_path"
    assert calls == ["persist_source_artifacts", "finish_tail"]
    assert [step for step, _payload in events] == ["init_source_artifacts_resume"]


async def test_init_long_project_claim_coverage_resume_skips_upstream_generation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("coverage_fast_path"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        config=SimpleNamespace(
            volume_auto_chapter_threshold=9999,
            volume_auto_word_threshold=999999,
            default_chapters_per_volume=10,
        ),
        settings=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
        trace=SimpleNamespace(summary=lambda: {}),
    )
    outline = StoryOutline(
        total_chapters=2,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=number,
                title=f"第{number}章",
                goal="推进主线",
                beats_summary=["完成本章事件。"],
                main_plot_points=["主线推进。"],
            )
            for number in (1, 2)
        ],
    )
    bundle = init_service_module.SourceArtifactsResumeBundle(
        spec=StorySpec(theme="测试故事", genre="xuanhuan", tone="serious", length_target=6000),
        story_bible=StoryBible(premise="测试故事"),
        character_bible=CharacterBible(characters=[CharacterProfile(name="沈珩")]),
        character_system={"characters": []},
        entity_registry=SimpleNamespace(entities=[]),  # type: ignore[arg-type]
        entity_graph={"entities": []},
        style_profile=None,
        creative_packet={"direction": "稳态"},
        blueprint=SimpleNamespace(model_dump=lambda mode="json": {}),  # type: ignore[arg-type]
        outline=outline,
        narrative_contract={"plot_threads": []},
        chapter_contracts={"chapter_contracts": []},
        readiness_report={"allowed": True},
        coherence_reports={
            "contract_coherence": {"blocked": False},
            "claim_contract_coverage": {"blocked": True},
            "source_artifacts": None,
        },
        outline_ctx={},
        total_chapters=2,
        resume_mode="claim_coverage",
    )
    calls: list[str] = []

    async def fail_spec_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("SpecStep.run should not be called on claim-coverage resume")

    async def fake_repair_coverage(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append("repair_claim_coverage")
        kwargs["init_coherence_reports"]["claim_contract_coverage"] = {  # type: ignore[index]
            "verdict": "accept",
            "blocked": False,
            "issues": [],
        }
        return kwargs["chapter_contracts"]  # type: ignore[return-value]

    async def fake_persist(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append("persist_source_artifacts")
        return kwargs["chapter_contracts"]  # type: ignore[return-value]

    async def fake_finish(*_args: object, **kwargs: object) -> SimpleNamespace:
        calls.append("finish_tail")
        return SimpleNamespace(project_id=kwargs["project_id"])

    monkeypatch.setattr(init_impl, "build_init_context", lambda *_args: ctx)
    monkeypatch.setattr(
        init_impl,
        "_ensure_long_init_request_fresh",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        init_impl,
        "_detect_init_resume_anchor",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        init_impl,
        "_load_source_artifacts_resume_bundle",
        lambda *_args, **_kwargs: bundle,
    )
    monkeypatch.setattr(
        init_impl,
        "_source_artifact_resume_rebuild_chapters",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        init_impl,
        "repair_claim_contract_coverage",
        fake_repair_coverage,
    )
    monkeypatch.setattr(
        init_impl,
        "_save_init_readiness",
        lambda *_args, **_kwargs: {"allowed": True},
    )
    monkeypatch.setattr(init_impl, "_persist_source_artifacts_from_init", fake_persist)
    monkeypatch.setattr(init_impl, "_finish_init_after_source_artifacts", fake_finish)
    monkeypatch.setattr(init_impl.SpecStep, "run", fail_spec_run)

    result = await init_impl.init_long_project(
        SimpleNamespace(),
        "测试故事",
        project_id="coverage_fast_path",
        total_chapters=2,
    )

    assert result.project_id == "coverage_fast_path"
    assert calls == ["repair_claim_coverage", "persist_source_artifacts", "finish_tail"]
    assert [step for step, _payload in events] == [
        "init_claim_contract_coverage_resume",
        "init_claim_contract_coverage_resumed",
    ]


async def test_init_long_project_contract_coherence_resume_reuses_upstream_artifacts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("contract_fast_path"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        config=SimpleNamespace(
            volume_auto_chapter_threshold=9999,
            volume_auto_word_threshold=999999,
            default_chapters_per_volume=10,
        ),
        settings=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
        trace=SimpleNamespace(summary=lambda: {}),
    )
    outline = StoryOutline(
        total_chapters=2,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=number,
                title=f"第{number}章",
                goal="推进主线",
                beats_summary=["完成本章事件。"],
                main_plot_points=["主线推进。"],
            )
            for number in (1, 2)
        ],
    )
    chapter_contracts = {
        "chapter_contracts": [
            {"chapter_number": 1},
            {"chapter_number": 2},
        ]
    }
    bundle = init_service_module.SourceArtifactsResumeBundle(
        spec=StorySpec(theme="测试故事", genre="xuanhuan", tone="serious", length_target=6000),
        story_bible=StoryBible(premise="测试故事"),
        character_bible=CharacterBible(characters=[CharacterProfile(name="沈珩")]),
        character_system={"characters": []},
        entity_registry=SimpleNamespace(entities=[]),  # type: ignore[arg-type]
        entity_graph={"entities": []},
        style_profile=None,
        creative_packet={"direction": "稳态"},
        blueprint=SimpleNamespace(model_dump=lambda mode="json": {}),  # type: ignore[arg-type]
        outline=outline,
        narrative_contract={"plot_threads": []},
        chapter_contracts=chapter_contracts,
        readiness_report={"allowed": True},
        coherence_reports={
            "contract_coherence": {"blocked": True, "focus_chapters": [2]},
            "claim_contract_coverage": {"blocked": False},
            "source_artifacts": None,
        },
        outline_ctx={},
        total_chapters=2,
        resume_mode="contract_coherence",
    )
    calls: list[object] = []

    async def fail_spec_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("SpecStep.run should not be called on contract-coherence resume")

    async def fake_reaudit(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append(("reaudit_contract_coherence", kwargs["focus_chapters"]))
        kwargs["init_coherence_reports"]["contract_coherence"] = {  # type: ignore[index]
            "verdict": "accept",
            "blocked": False,
            "issues": [],
        }
        return kwargs["chapter_contracts"]  # type: ignore[return-value]

    async def fake_persist(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append("persist_source_artifacts")
        return kwargs["chapter_contracts"]  # type: ignore[return-value]

    async def fake_finish(*_args: object, **kwargs: object) -> SimpleNamespace:
        calls.append("finish_tail")
        return SimpleNamespace(project_id=kwargs["project_id"])

    monkeypatch.setattr(init_impl, "build_init_context", lambda *_args: ctx)
    monkeypatch.setattr(
        init_impl,
        "_ensure_long_init_request_fresh",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        init_impl,
        "_detect_init_resume_anchor",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        init_impl,
        "_load_source_artifacts_resume_bundle",
        lambda *_args, **_kwargs: bundle,
    )
    monkeypatch.setattr(
        init_impl,
        "_source_artifact_resume_rebuild_chapters",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(init_impl, "reaudit_changed_chapter_contracts", fake_reaudit)
    monkeypatch.setattr(
        init_impl,
        "_save_init_readiness",
        lambda *_args, **_kwargs: {"allowed": True},
    )
    monkeypatch.setattr(init_impl, "_persist_source_artifacts_from_init", fake_persist)
    monkeypatch.setattr(init_impl, "_finish_init_after_source_artifacts", fake_finish)
    monkeypatch.setattr(init_impl.SpecStep, "run", fail_spec_run)

    result = await init_impl.init_long_project(
        SimpleNamespace(),
        "测试故事",
        project_id="contract_fast_path",
        total_chapters=2,
    )

    assert result.project_id == "contract_fast_path"
    assert calls == [
        ("reaudit_contract_coherence", [2]),
        "persist_source_artifacts",
        "finish_tail",
    ]
    assert [step for step, _payload in events] == [
        "init_contract_coherence_resume",
        "init_contract_coherence_resumed",
        "chapter_contract_cast_plan_synced",
    ]


async def test_init_long_project_source_artifacts_resume_rebuilds_scoped_contracts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("source_scoped_rebuild"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        config=SimpleNamespace(
            volume_auto_chapter_threshold=9999,
            volume_auto_word_threshold=999999,
            default_chapters_per_volume=10,
        ),
        settings=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
        trace=SimpleNamespace(summary=lambda: {}),
    )
    outline = StoryOutline(
        total_chapters=2,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=number,
                title=f"第{number}章",
                goal="推进主线",
                beats_summary=["完成本章事件。"],
                main_plot_points=["主线推进。"],
            )
            for number in (1, 2)
        ],
    )
    bundle = init_service_module.SourceArtifactsResumeBundle(
        spec=StorySpec(theme="测试故事", genre="xuanhuan", tone="serious", length_target=6000),
        story_bible=StoryBible(premise="测试故事"),
        character_bible=CharacterBible(characters=[CharacterProfile(name="沈珩")]),
        character_system={"characters": []},
        entity_registry=SimpleNamespace(entities=[]),  # type: ignore[arg-type]
        entity_graph={"entities": []},
        style_profile=None,
        creative_packet={"direction": "稳态"},
        blueprint=SimpleNamespace(model_dump=lambda mode="json": {}),  # type: ignore[arg-type]
        outline=outline,
        narrative_contract={"plot_threads": []},
        chapter_contracts={
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "title": "保留章",
                    "required_events": ["保留事件"],
                    "source": "plan_chapter_contracts",
                },
                {
                    "chapter_number": 2,
                    "title": "坏章",
                    "required_events": ["未知实体出场"],
                    "source": "plan_chapter_contracts",
                },
            ]
        },
        readiness_report={"allowed": True},
        coherence_reports={"source_artifacts": None},
        outline_ctx={},
        total_chapters=2,
    )
    calls: list[str] = []
    focus_calls: list[list[int]] = []
    reaudit_focus_calls: list[list[int]] = []
    persisted_contracts: dict[str, object] = {}

    async def fake_rebuild(
        *_args: object, **kwargs: object
    ) -> tuple[dict[str, object], dict[str, object]]:
        focus_calls.append(list(kwargs["focus_chapters"]))  # type: ignore[arg-type]
        return (
            {
                "chapter_contracts": [
                    {
                        "chapter_number": 2,
                        "title": "修复章",
                        "required_events": ["规范实体出场"],
                        "source": "plan_chapter_contracts",
                    }
                ]
            },
            {"complete": True},
        )

    def fake_persist_runtime(*_args: object, **_kwargs: object) -> SimpleNamespace:
        calls.append("persist_runtime_contracts")
        return SimpleNamespace(milestones=[1, 2])

    async def fake_reaudit(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append("reaudit_contracts")
        reaudit_focus_calls.append(list(kwargs["focus_chapters"]))  # type: ignore[arg-type]
        return kwargs["chapter_contracts"]  # type: ignore[return-value]

    async def fake_persist_source(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append("persist_source_artifacts")
        assert callable(kwargs["post_repair_reaudit"])
        persisted_contracts.update(kwargs["chapter_contracts"])  # type: ignore[arg-type]
        return kwargs["chapter_contracts"]  # type: ignore[return-value]

    async def fake_finish(*_args: object, **kwargs: object) -> SimpleNamespace:
        calls.append("finish_tail")
        return SimpleNamespace(project_id=kwargs["project_id"])

    monkeypatch.setattr(init_impl, "build_init_context", lambda *_args: ctx)
    monkeypatch.setattr(
        init_impl,
        "_ensure_long_init_request_fresh",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        init_impl,
        "_detect_init_resume_anchor",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        init_impl,
        "_load_source_artifacts_resume_bundle",
        lambda *_args, **_kwargs: bundle,
    )
    monkeypatch.setattr(
        init_impl,
        "_source_artifact_resume_rebuild_chapters",
        lambda *_args, **_kwargs: [2],
    )
    monkeypatch.setattr(init_impl, "_batched_generate_chapter_contracts", fake_rebuild)
    monkeypatch.setattr(
        init_impl,
        "_persist_chapter_contract_runtime_artifacts",
        fake_persist_runtime,
    )
    monkeypatch.setattr(
        init_impl,
        "reaudit_changed_chapter_contracts",
        fake_reaudit,
    )
    monkeypatch.setattr(
        init_impl, "_persist_source_artifacts_from_init", fake_persist_source
    )
    monkeypatch.setattr(init_impl, "_finish_init_after_source_artifacts", fake_finish)

    result = await init_impl.init_long_project(
        SimpleNamespace(),
        "测试故事",
        project_id="source_scoped_rebuild",
        total_chapters=2,
    )

    assert result.project_id == "source_scoped_rebuild"
    assert focus_calls == [[2]]
    assert reaudit_focus_calls == [[2]]
    assert calls == [
        "reaudit_contracts",
        "persist_runtime_contracts",
        "persist_source_artifacts",
        "finish_tail",
    ]
    assert [item["title"] for item in persisted_contracts["chapter_contracts"]] == [  # type: ignore[union-attr]
        "保留章",
        "修复章",
    ]
    assert [step for step, _payload in events] == [
        "init_source_artifacts_resume",
        "init_source_artifacts_contracts_rebuilt",
    ]


async def test_reaudit_changed_contracts_runs_coherence_then_claim_coverage(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("contract_reaudit"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        settings=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
    )
    outline = StoryOutline(
        total_chapters=2,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=number,
                title=f"第{number}章",
                goal="推进主线",
                beats_summary=["完成本章事件。"],
                main_plot_points=["主线推进。"],
            )
            for number in (1, 2)
        ],
    )
    contracts = {
        "chapter_contracts": [
            {"chapter_number": 1, "required_events": ["事件1"]},
            {"chapter_number": 2, "required_events": ["修复后事件"]},
        ]
    }
    reports: dict[str, dict[str, object] | None] = {
        "contract_coherence": None,
        "claim_contract_coverage": None,
        "source_artifacts": None,
    }
    gate_focus: list[list[int]] = []
    coverage_calls: list[str] = []

    async def fake_gate(*_args: object, **kwargs: object) -> dict[str, object]:
        gate_focus.append(list(kwargs["focus_chapters"]))  # type: ignore[arg-type]
        return {
            "verdict": "accept",
            "blocked": False,
            "issues": [],
            "summary": "定向契约复审通过",
        }

    async def fake_coverage(*_args: object, **kwargs: object) -> dict[str, object]:
        coverage_calls.append("claim_contract_coverage")
        kwargs["init_coherence_reports"]["claim_contract_coverage"] = {  # type: ignore[index]
            "verdict": "accept",
            "blocked": False,
            "issues": [],
            "summary": "Claims 覆盖通过",
        }
        return kwargs["chapter_contracts"]  # type: ignore[return-value]

    monkeypatch.setattr(init_contract_impl, "run_init_coherence_v2_gate", fake_gate)
    monkeypatch.setattr(init_contract_impl, "repair_claim_contract_coverage", fake_coverage)
    monkeypatch.setattr(
        init_contract_impl,
        "_save_init_readiness",
        lambda *_args, **_kwargs: {"allowed": False},
    )

    result = await init_service_module.reaudit_changed_chapter_contracts(
        ctx=ctx,  # type: ignore[arg-type]
        blueprint=SimpleNamespace(model_dump=lambda mode="json": {}),  # type: ignore[arg-type]
        outline=outline,
        llm_contract={"plot_threads": []},
        chapter_contracts=contracts,
        project_id="contract_reaudit",
        init_entity_catalog={"allowed_entities": []},
        outline_ctx={},
        total_chapters=2,
        narrative_complexity="medium",
        init_coherence_reports=reports,  # type: ignore[arg-type]
        init_repairs=[],
        focus_chapters=[2],
        init_coherence_profile={"summary": "profile"},
    )

    assert result is contracts
    assert gate_focus == [[2]]
    assert coverage_calls == ["claim_contract_coverage"]
    assert [step for step, _payload in events] == [
        "init_repair_reaudit_started",
        "init_repair_reaudit_stage",
        "init_repair_reaudit_stage",
        "init_repair_reaudit_passed",
    ]


async def test_reaudit_reconciles_claims_refreshed_after_coverage_repair(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("contract_reaudit_ledger_refresh"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        settings=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
    )
    outline = StoryOutline(
        total_chapters=1,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=1,
                title="第一章",
                goal="推进主线",
                beats_summary=["完成本章事件。"],
                main_plot_points=["主线推进。"],
            )
        ],
    )
    contracts = {
        "chapter_contracts": [{"chapter_number": 1, "required_events": ["原始事件"]}]
    }
    first_repaired = {
        "chapter_contracts": [
            {
                "chapter_number": 1,
                "required_events": ["原始事件"],
                "cognitive_constraints": [
                    {"claim_id": "claim_1", "cognitive_object": "别名版对象"}
                ],
            }
        ]
    }
    refreshed_ledger_repaired = {
        "chapter_contracts": [
            {
                "chapter_number": 1,
                "required_events": ["原始事件"],
                "cognitive_constraints": [
                    {"claim_id": "claim_1", "cognitive_object": "规范实体名称"}
                ],
            }
        ]
    }
    reports: dict[str, dict[str, object] | None] = {
        "contract_coherence": None,
        "claim_contract_coverage": None,
        "source_artifacts": None,
    }
    gate_focus: list[list[int]] = []
    coverage_inputs: list[dict[str, object]] = []

    async def fake_gate(*_args: object, **kwargs: object) -> dict[str, object]:
        gate_focus.append(list(kwargs["focus_chapters"]))  # type: ignore[arg-type]
        return {
            "verdict": "accept",
            "blocked": False,
            "issues": [],
            "summary": "定向契约复审通过",
        }

    async def fake_coverage(*_args: object, **kwargs: object) -> dict[str, object]:
        coverage_inputs.append(kwargs["chapter_contracts"])  # type: ignore[arg-type]
        kwargs["init_coherence_reports"]["claim_contract_coverage"] = {  # type: ignore[index]
            "verdict": "accept",
            "blocked": False,
            "issues": [],
            "summary": "Claims 覆盖通过",
        }
        return first_repaired if len(coverage_inputs) == 1 else refreshed_ledger_repaired

    def fake_final_audit(*_args: object, **kwargs: object) -> dict[str, object]:
        assert kwargs["chapter_contracts"] is refreshed_ledger_repaired
        return {
            "verdict": "accept",
            "blocked": False,
            "issues": [],
            "summary": "刷新账本后覆盖通过",
        }

    monkeypatch.setattr(init_contract_impl, "run_init_coherence_v2_gate", fake_gate)
    monkeypatch.setattr(init_contract_impl, "repair_claim_contract_coverage", fake_coverage)
    monkeypatch.setattr(
        init_contract_impl,
        "run_init_claim_contract_coverage_audit",
        fake_final_audit,
    )
    monkeypatch.setattr(
        init_contract_impl,
        "_save_init_readiness",
        lambda *_args, **_kwargs: {"allowed": True},
    )

    result = await init_service_module.reaudit_changed_chapter_contracts(
        ctx=ctx,  # type: ignore[arg-type]
        blueprint=SimpleNamespace(model_dump=lambda mode="json": {}),  # type: ignore[arg-type]
        outline=outline,
        llm_contract={"plot_threads": []},
        chapter_contracts=contracts,
        project_id="contract_reaudit_ledger_refresh",
        init_entity_catalog={"allowed_entities": []},
        outline_ctx={},
        total_chapters=1,
        narrative_complexity="medium",
        init_coherence_reports=reports,  # type: ignore[arg-type]
        init_repairs=[],
        focus_chapters=[1],
        init_coherence_profile={"summary": "profile"},
    )

    assert result is refreshed_ledger_repaired
    assert coverage_inputs == [contracts, first_repaired]
    assert gate_focus == [[1], [1]]
    assert any(
        step == "init_repair_reaudit_stage"
        and isinstance(payload, dict)
        and payload.get("phase") == "after_claim_ledger_refresh"
        for step, payload in events
    )


async def test_reaudit_changed_contracts_blocks_before_claim_coverage(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("contract_reaudit_blocked"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        settings=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
    )
    outline = StoryOutline(
        total_chapters=1,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=1,
                title="第一章",
                goal="推进主线",
                beats_summary=["事件。"],
                main_plot_points=["主线。"],
            )
        ],
    )
    coverage_called = False

    async def blocked_gate(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "verdict": "needs_repair",
            "blocked": True,
            "issues": [{"severity": "high", "message": "新契约冲突"}],
            "summary": "定向契约复审失败",
        }

    async def should_not_run_coverage(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal coverage_called
        coverage_called = True
        return {}

    monkeypatch.setattr(init_contract_impl, "run_init_coherence_v2_gate", blocked_gate)
    monkeypatch.setattr(
        init_contract_impl,
        "repair_claim_contract_coverage",
        should_not_run_coverage,
    )
    monkeypatch.setattr(
        init_contract_impl,
        "_save_init_readiness",
        lambda *_args, **_kwargs: {"allowed": False},
    )

    with pytest.raises(init_service_module.InitCoherenceError):
        await init_service_module.reaudit_changed_chapter_contracts(
            ctx=ctx,  # type: ignore[arg-type]
            blueprint=SimpleNamespace(model_dump=lambda mode="json": {}),  # type: ignore[arg-type]
            outline=outline,
            llm_contract={},
            chapter_contracts={"chapter_contracts": [{"chapter_number": 1}]},
            project_id="contract_reaudit_blocked",
            init_entity_catalog={"allowed_entities": []},
            outline_ctx={},
            total_chapters=1,
            narrative_complexity="medium",
            init_coherence_reports={"contract_coherence": None},
            init_repairs=[],
            focus_chapters=[1],
            init_coherence_profile={"summary": "profile"},
        )

    assert coverage_called is False
    assert any(step == "init_repair_reaudit_failed" for step, _payload in events)


async def test_finish_init_blocks_story_kernel_failure_when_required(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("kernel_required"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        settings=SimpleNamespace(narrative_state_required=True, story_kernel_db_path=""),
        on_step=lambda step, data: events.append((step, data)),
        trace=SimpleNamespace(summary=lambda: {}),
    )
    outline = StoryOutline(
        total_chapters=1,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=1,
                title="第一章",
                goal="建立冲突",
                beats_summary=["冲突出现。"],
                main_plot_points=["主线启动。"],
            )
        ],
    )

    def fail_build_kernel_from_init(**_kwargs: object) -> object:
        raise RuntimeError("kernel db unavailable")

    monkeypatch.setattr(
        init_resume_impl,
        "build_kernel_from_init",
        fail_build_kernel_from_init,
    )

    with pytest.raises(init_service_module.InitCoherenceError, match="故事内核初始化失败"):
        await init_service_module._finish_init_after_source_artifacts(
            ctx,  # type: ignore[arg-type]
            project_id="kernel_required",
            story_bible=StoryBible(premise="测试故事"),
            character_bible=CharacterBible(characters=[CharacterProfile(name="沈珩")]),
            outline=outline,
            entity_registry=SimpleNamespace(entities=[]),  # type: ignore[arg-type]
            entity_graph=SimpleNamespace(entities=[]),
        )

    failed_events = [payload for step, payload in events if step == "story_kernel_build_failed"]
    assert failed_events
    assert failed_events[0]["narrative_state_required"] is True  # type: ignore[index]


async def test_finish_init_blocks_failed_story_kernel_truth_gate(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("kernel_truth_gate_blocked"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        settings=SimpleNamespace(narrative_state_required=False, story_kernel_db_path=""),
        on_step=lambda step, data: events.append((step, data)),
        trace=SimpleNamespace(summary=lambda: {}),
    )
    outline = StoryOutline(
        total_chapters=1,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=1,
                title="第一章",
                goal="建立冲突",
                beats_summary=["冲突出现。"],
                main_plot_points=["主线启动。"],
            )
        ],
    )
    monkeypatch.setattr(
        init_resume_impl,
        "build_kernel_from_init",
        lambda **_kwargs: SimpleNamespace(knowledge_ledger=[]),
    )
    monkeypatch.setattr(
        init_resume_impl,
        "InitTruthGate",
        SimpleNamespace(
            validate=lambda _kernel: SimpleNamespace(
                passed=False,
                violations=["实体引用不完整"],
                warnings=[],
            )
        ),
    )

    with pytest.raises(init_service_module.InitCoherenceError, match="Truth Gate 未通过"):
        await init_service_module._finish_init_after_source_artifacts(
            ctx,  # type: ignore[arg-type]
            project_id="kernel_truth_gate_blocked",
            story_bible=StoryBible(premise="测试故事"),
            character_bible=CharacterBible(characters=[CharacterProfile(name="沈珩")]),
            outline=outline,
            entity_registry=SimpleNamespace(entities=[]),  # type: ignore[arg-type]
            entity_graph=SimpleNamespace(entities=[]),
        )

    blocked_events = [payload for step, payload in events if step == "story_kernel_gate_blocked"]
    assert blocked_events
    assert blocked_events[0]["action"] == "block_init_completion"  # type: ignore[index]
    assert not any(step == "story_kernel_saved" for step, _payload in events)
    assert not (layout.canon_dir / "canon_current.json").exists()


async def test_finish_init_allows_story_kernel_failure_when_optional(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("kernel_optional"))
    layout.ensure_dirs()
    events: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        settings=SimpleNamespace(narrative_state_required=False, story_kernel_db_path=""),
        on_step=lambda step, data: events.append((step, data)),
        trace=SimpleNamespace(summary=lambda: {"ok": True}),
    )
    outline = StoryOutline(
        total_chapters=1,
        synopsis="测试故事",
        chapters=[
            ChapterOutline(
                chapter_number=1,
                title="第一章",
                goal="建立冲突",
                beats_summary=["冲突出现。"],
                main_plot_points=["主线启动。"],
            )
        ],
    )

    def fail_build_kernel_from_init(**_kwargs: object) -> object:
        raise RuntimeError("kernel db unavailable")

    monkeypatch.setattr(
        init_resume_impl,
        "build_kernel_from_init",
        fail_build_kernel_from_init,
    )

    result = await init_service_module._finish_init_after_source_artifacts(
        ctx,  # type: ignore[arg-type]
        project_id="kernel_optional",
        story_bible=StoryBible(premise="测试故事"),
        character_bible=CharacterBible(characters=[CharacterProfile(name="沈珩")]),
        outline=outline,
        entity_registry=SimpleNamespace(entities=[]),  # type: ignore[arg-type]
        entity_graph=SimpleNamespace(entities=[]),
    )

    assert result.project_id == "kernel_optional"
    failed_events = [payload for step, payload in events if step == "story_kernel_build_failed"]
    assert failed_events
    assert failed_events[0]["narrative_state_required"] is False  # type: ignore[index]


def test_character_bible_semantic_hash_ignores_knowledge_boundaries() -> None:
    base = CharacterBible(
        characters=[
            CharacterProfile(name="沈知微", role="protagonist"),
            CharacterProfile(name="陈默", role="supporting"),
        ]
    )
    with_boundaries = CharacterBible.model_validate(base.model_dump(mode="json"))
    with_boundaries.characters[0].knowledge_boundaries = CharacterKnowledgeBoundary(
        known_facts=["她知道玉佩来自旧宅。"],
        sensory_access_rules=["无法听见密室外的耳语。"],
    )
    with_boundaries.characters[1].knowledge_boundaries = CharacterKnowledgeBoundary(
        suspected=["他怀疑账本被调包。"],
    )

    assert hash_payload(base) != hash_payload(with_boundaries)
    assert _character_bible_semantic_hash(base) == _character_bible_semantic_hash(with_boundaries)
    assert not _character_knowledge_boundaries_complete(base)
    assert _character_knowledge_boundaries_complete(with_boundaries)
