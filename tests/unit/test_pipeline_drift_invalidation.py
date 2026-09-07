"""Regression tests for stale pipeline artifact invalidation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.init.init_cache import (
    _build_long_init_request_payload,
    _ensure_long_init_request_fresh,
    _long_init_request_fingerprint,
    _reset_project_for_reinit,
    reset_project_for_reinit,
)
from novel_forge.pipeline.short_runner import ShortStoryRunner
from novel_forge.prompts.builder import PromptBuilder


def _short_runner(tmp_path: Path, runtime_settings) -> ShortStoryRunner:
    return ShortStoryRunner(
        ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock"),
        PromptBuilder(),
        FileSystemStorage(tmp_path),
        settings=runtime_settings,
        max_edit_rounds=1,
    )


def _long_request_payload(
    *,
    premise: str = "武周政权晚期，崔令仪以代兄承爵的身份踏入朝堂。",
    total_chapters: int = 82,
    words_per_chapter: int = 5000,
    volume_mode: str = "on",
    chapters_per_volume: int = 27,
    polish_hint: str = "",
) -> dict[str, object]:
    return _build_long_init_request_payload(
        premise=premise,
        genre="romance",
        tone="suspenseful",
        title="朱批录",
        language="zh",
        characters_hint="崔令仪，沈照夜。",
        world_hint="武周晚期，长安权力中枢。",
        conflict_hint="朝堂博弈与情感撕裂并行。",
        pov_hint="崔令仪限知视角。",
        opening_style="从户部查账切入。",
        ending_style="在政变与告别中收束。",
        extra_instructions="强化权谋与情感的双线推进。",
        total_chapters=total_chapters,
        words_per_chapter=words_per_chapter,
        volume_mode=volume_mode,
        chapters_per_volume=chapters_per_volume,
        effective_volume_mode=volume_mode == "on",
        effective_chapters_per_volume=chapters_per_volume if chapters_per_volume > 0 else 27,
        blueprint_element_preferences={},
        polish_hint=polish_hint,
    )


def _long_ctx(
    tmp_storage: FileSystemStorage, project_id: str
) -> tuple[SimpleNamespace, ProjectLayout, list[tuple[str, object]]]:
    layout = ProjectLayout(tmp_storage.project_dir(project_id))
    layout.ensure_dirs()
    steps: list[tuple[str, object]] = []
    ctx = SimpleNamespace(
        storage=tmp_storage,
        layout=layout,
        on_step=lambda step, data: steps.append((step, data)),
    )
    return ctx, layout, steps


def test_short_checkpoint_drift_invalidates_derived_artifacts(
    tmp_path: Path,
    runtime_settings,
) -> None:
    runner = _short_runner(tmp_path, runtime_settings)
    layout = ProjectLayout(runner._storage.ensure_project_dir("short_demo"))
    layout.ensure_dirs()

    runner._storage.save_json(
        layout.spec_path,
        {"theme": "旧主题", "genre": "fantasy", "tone": "warm", "title": "旧标题"},
    )
    runner._storage.save_json(layout.short_blueprint_path(), {"title": "旧蓝图"})
    runner._storage.save_json(layout.short_beats_path(), {"beats": []})
    runner._storage.save_text(layout.short_draft_path(0), "旧草稿")
    runner._storage.save_text(layout.chapters_dir / "short_story.md", "旧成稿")

    spec_input = {
        "theme": "新主题",
        "genre": "fantasy",
        "tone": "warm",
        "title": "新标题",
        "language": "zh",
    }
    payload = runner._build_checkpoint_payload(
        spec_input,
        segmented_mode=None,
        segment_target_words=None,
        segment_max_count=None,
        blueprint_element_preferences=None,
    )
    runner._ensure_short_checkpoint_fresh(
        layout,
        request_payload=payload,
        request_fingerprint=runner._checkpoint_fingerprint(payload),
        spec_input=spec_input,
        segmented_mode=None,
        segment_target_words=None,
        segment_max_count=None,
        blueprint_element_preferences=None,
    )

    assert not layout.spec_path.exists()
    assert not layout.short_blueprint_path().exists()
    assert not layout.short_beats_path().exists()
    assert not layout.short_draft_path(0).exists()
    assert not (layout.chapters_dir / "short_story.md").exists()
    assert runner._short_meta_path(layout).exists()


def test_init_reproject_reset_removes_previous_project_contents(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "long_demo")
    layout.ensure_dirs()

    for path in (
        layout.spec_path,
        layout.bible_path,
        layout.characters_path,
        layout.blueprint_path,
        layout.outline_path,
        layout.outline_session_path,
        layout.outline_tracker_path,
        layout.narrative_contract_path,
        layout.element_progress_path,
        layout.memory_dir / "outline_episodic.json",
        layout.canon_dir / "canon_current.json",
        layout.reports_dir / "chapter_001_eval.json",
        layout.drafts_dir / "chapter_001" / "v0_draft.md",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    layout.chapter_path(1).write_text("既有章节正文", encoding="utf-8")
    log_path = layout.logs_dir / "previous_run" / "summary.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("{}", encoding="utf-8")

    removed = _reset_project_for_reinit(layout)

    assert str(layout.outline_path) in removed
    assert str(layout.memory_dir) in removed
    assert str(layout.canon_dir) in removed
    assert str(layout.chapters_dir) in removed
    assert str(layout.drafts_dir) in removed
    assert str(layout.reports_dir) in removed
    assert not layout.outline_path.exists()
    assert not (layout.memory_dir / "outline_episodic.json").exists()
    assert not (layout.canon_dir / "canon_current.json").exists()
    assert not layout.chapter_path(1).exists()
    assert not (layout.drafts_dir / "chapter_001" / "v0_draft.md").exists()
    assert not (layout.reports_dir / "chapter_001_eval.json").exists()
    assert layout.memory_dir.exists()
    assert layout.canon_dir.exists()
    assert log_path.exists()


def test_init_reproject_reset_can_preserve_chapter_markdown(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "long_demo_keep_chapters")
    layout.ensure_dirs()

    layout.style_profile_path.write_text("{}", encoding="utf-8")
    layout.narrative_contract_path.parent.mkdir(parents=True, exist_ok=True)
    layout.narrative_contract_path.write_text("{}", encoding="utf-8")
    layout.chapter_path(1).write_text("既有章节正文", encoding="utf-8")
    log_path = layout.logs_dir / "previous_run" / "summary.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("{}", encoding="utf-8")

    removed = reset_project_for_reinit(layout, preserve_chapters=True)

    assert str(layout.chapters_dir) not in removed
    assert layout.chapter_path(1).exists()
    assert not layout.style_profile_path.exists()
    assert not layout.narrative_contract_path.exists()
    assert log_path.exists()


def test_long_resume_legacy_project_keeps_outline_when_only_premise_vs_theme_differs(
    tmp_storage: FileSystemStorage,
) -> None:
    ctx, layout, steps = _long_ctx(tmp_storage, "long_resume_legacy")

    request_payload = _long_request_payload(
        premise="武周政权晚期，崔令仪以代兄承爵的身份踏入朝堂。",
    )
    tmp_storage.save_json(
        layout.spec_path,
        {
            "theme": "长安三年，崔令仪在紫宸殿与户部账册之间艰难求生。",
            "genre": "romance",
            "tone": "suspenseful",
            "title": "朱批录",
            "language": "zh",
        },
    )
    tmp_storage.save_json(
        layout.outline_path,
        {
            "total_chapters": 82,
            "volume_mode": True,
            "volumes": [{"start_chapter": 1, "end_chapter": 27}],
            "chapters": [{"expected_word_count": 5000}],
        },
    )
    tmp_storage.save_json(
        layout.outline_session_path,
        {"chapters_done": 30, "latest_chapter_number": 30},
    )
    tmp_storage.save_json(
        layout.init_request_meta_path,
        {
            "schema_version": 1,
            "request_fingerprint": _long_init_request_fingerprint(request_payload),
            "request": request_payload,
        },
    )

    _ensure_long_init_request_fresh(
        ctx,
        project_id="long_resume_legacy",
        request_payload=request_payload,
    )

    assert layout.spec_path.exists()
    assert layout.outline_path.exists()
    assert layout.outline_session_path.exists()
    assert layout.init_request_meta_path.exists()
    assert steps == []


def test_long_resume_saved_request_drift_resets_outline_chain(
    tmp_storage: FileSystemStorage,
) -> None:
    ctx, layout, steps = _long_ctx(tmp_storage, "long_resume_reset")

    for path in (layout.spec_path, layout.outline_path, layout.outline_session_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp_storage.save_json(layout.spec_path, {"genre": "romance", "tone": "suspenseful"})
    tmp_storage.save_json(layout.outline_path, {"total_chapters": 82, "chapters": []})
    tmp_storage.save_json(layout.outline_session_path, {"chapters_done": 30})

    previous_payload = _long_request_payload(total_chapters=82)
    tmp_storage.save_json(
        layout.init_request_meta_path,
        {
            "schema_version": 1,
            "request_fingerprint": _long_init_request_fingerprint(previous_payload),
            "request": previous_payload,
        },
    )

    current_payload = _long_request_payload(total_chapters=90)
    _ensure_long_init_request_fresh(
        ctx,
        project_id="long_resume_reset",
        request_payload=current_payload,
    )

    assert not layout.spec_path.exists()
    assert not layout.outline_path.exists()
    assert not layout.outline_session_path.exists()
    assert layout.init_request_meta_path.exists()
    assert [step for step, _ in steps] == ["init_param_drift", "init_project_reset"]
    saved_meta = tmp_storage.load_json(layout.init_request_meta_path)
    assert saved_meta["request_fingerprint"] == _long_init_request_fingerprint(current_payload)


def test_confirmed_long_project_drift_requires_decision_before_reset(
    tmp_storage: FileSystemStorage,
) -> None:
    ctx, layout, steps = _long_ctx(tmp_storage, "long_resume_confirmed")
    previous_payload = _long_request_payload(total_chapters=82)
    current_payload = _long_request_payload(total_chapters=90)

    tmp_storage.save_json(layout.spec_path, {"title": "朱批录", "genre": "romance"})
    tmp_storage.save_json(layout.outline_path, {"total_chapters": 82, "chapters": []})
    tmp_storage.save_json(layout.reports_dir / "init_readiness.json", {"ready": True})
    tmp_storage.save_json(
        layout.init_request_meta_path,
        {
            "schema_version": 1,
            "request_fingerprint": _long_init_request_fingerprint(previous_payload),
            "request": previous_payload,
        },
    )

    freshness = _ensure_long_init_request_fresh(
        ctx,
        project_id="long_resume_confirmed",
        request_payload=current_payload,
    )

    assert freshness.requires_confirmation is True
    assert freshness.reset_performed is False
    assert layout.spec_path.exists()
    assert layout.outline_path.exists()
    saved_meta = tmp_storage.load_json(layout.init_request_meta_path)
    assert saved_meta["request_fingerprint"] == _long_init_request_fingerprint(previous_payload)
    assert [step for step, _ in steps] == ["init_param_drift_requires_confirmation"]

    confirmed = _ensure_long_init_request_fresh(
        ctx,
        project_id="long_resume_confirmed",
        request_payload=current_payload,
        allow_confirmed_reset=True,
    )

    assert confirmed.requires_confirmation is False
    assert confirmed.reset_performed is True
    assert not layout.spec_path.exists()
    assert not layout.outline_path.exists()
    saved_meta = tmp_storage.load_json(layout.init_request_meta_path)
    assert saved_meta["request_fingerprint"] == _long_init_request_fingerprint(current_payload)


def test_long_resume_polish_hint_drift_resets_outline_chain(
    tmp_storage: FileSystemStorage,
) -> None:
    ctx, layout, steps = _long_ctx(tmp_storage, "long_resume_polish_reset")

    for path in (layout.spec_path, layout.outline_path, layout.outline_session_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp_storage.save_json(layout.spec_path, {"genre": "romance", "tone": "suspenseful"})
    tmp_storage.save_json(layout.outline_path, {"total_chapters": 82, "chapters": []})
    tmp_storage.save_json(layout.outline_session_path, {"chapters_done": 30})

    previous_payload = _long_request_payload(polish_hint="")
    tmp_storage.save_json(
        layout.init_request_meta_path,
        {
            "schema_version": 1,
            "request_fingerprint": _long_init_request_fingerprint(previous_payload),
            "request": previous_payload,
        },
    )

    current_payload = _long_request_payload(polish_hint="强化前五章的悬念递进")
    _ensure_long_init_request_fresh(
        ctx,
        project_id="long_resume_polish_reset",
        request_payload=current_payload,
    )

    assert not layout.spec_path.exists()
    assert not layout.outline_path.exists()
    assert not layout.outline_session_path.exists()
    assert [step for step, _ in steps] == ["init_param_drift", "init_project_reset"]


def test_long_resume_missing_polish_hint_metadata_keeps_empty_default(
    tmp_storage: FileSystemStorage,
) -> None:
    ctx, layout, steps = _long_ctx(tmp_storage, "long_resume_old_meta")

    tmp_storage.save_json(layout.spec_path, {"genre": "romance", "tone": "suspenseful"})
    tmp_storage.save_json(layout.outline_path, {"total_chapters": 82, "chapters": []})

    previous_payload = _long_request_payload()
    previous_payload["generation_options"].pop("polish_hint", None)  # type: ignore[index]
    tmp_storage.save_json(
        layout.init_request_meta_path,
        {
            "schema_version": 1,
            "request_fingerprint": _long_init_request_fingerprint(previous_payload),
            "request": previous_payload,
        },
    )

    current_payload = _long_request_payload(polish_hint="")
    _ensure_long_init_request_fresh(
        ctx,
        project_id="long_resume_old_meta",
        request_payload=current_payload,
    )

    assert layout.spec_path.exists()
    assert layout.outline_path.exists()
    assert steps == []


def test_long_resume_legacy_outline_shape_change_still_triggers_reset(
    tmp_storage: FileSystemStorage,
) -> None:
    ctx, layout, steps = _long_ctx(tmp_storage, "long_resume_legacy_reset")

    tmp_storage.save_json(
        layout.spec_path,
        {
            "theme": "旧主题",
            "genre": "romance",
            "tone": "suspenseful",
            "title": "朱批录",
            "language": "zh",
        },
    )
    tmp_storage.save_json(
        layout.outline_path,
        {
            "total_chapters": 40,
            "volume_mode": False,
            "volumes": [],
            "chapters": [{"expected_word_count": 3000}],
        },
    )

    _ensure_long_init_request_fresh(
        ctx,
        project_id="long_resume_legacy_reset",
        request_payload=_long_request_payload(
            total_chapters=82,
            words_per_chapter=5000,
            volume_mode="on",
            chapters_per_volume=27,
        ),
    )

    assert not layout.spec_path.exists()
    assert not layout.outline_path.exists()
    assert layout.init_request_meta_path.exists()
    assert [step for step, _ in steps] == ["init_param_drift", "init_project_reset"]
