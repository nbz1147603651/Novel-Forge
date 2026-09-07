"""Tests for chapter rewrite strategy context packaging."""

from __future__ import annotations

from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.rewrite_strategy import (
    build_rewrite_strategy_plan,
    notes_with_rewrite_strategy,
)


def test_auto_force_rewrite_uses_compatibility_when_downstream_exists(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("rewrite_strategy_auto"))
    layout.ensure_dirs()
    tmp_storage.save_text(layout.chapter_path(3), "# 第3章\n\n陆云峥推开古董店的门。")
    tmp_storage.save_json(
        layout.creative_report_path(3),
        {"summary": "陆云峥进入古董店，怀表线索继续推进。"},
    )

    plan = build_rewrite_strategy_plan(
        storage=tmp_storage,
        layout=layout,
        chapter_number=2,
        force=True,
        requested_strategy="auto",
    )

    assert plan.effective_strategy == "compatible"
    assert plan.downstream_chapters == (3,)
    assert "作者层后文兼容约束" in plan.prompt_note
    assert "陆云峥推开古董店的门" in plan.prompt_note
    assert plan.metadata["downstream_count"] == 1


def test_auto_force_rewrite_without_downstream_stays_sequential(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("rewrite_strategy_latest"))
    layout.ensure_dirs()

    plan = build_rewrite_strategy_plan(
        storage=tmp_storage,
        layout=layout,
        chapter_number=4,
        force=True,
        requested_strategy="auto",
    )

    assert plan.effective_strategy == "sequential"
    assert plan.prompt_note == ""


def test_explicit_compatible_can_be_preserved_for_plan_regeneration(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("rewrite_strategy_regen"))
    layout.ensure_dirs()
    tmp_storage.save_text(layout.chapter_path(5), "第五章已有正文。")

    plan = build_rewrite_strategy_plan(
        storage=tmp_storage,
        layout=layout,
        chapter_number=4,
        force=False,
        requested_strategy="compatible",
    )

    assert plan.effective_strategy == "compatible"
    assert "作者层后文兼容约束" in plan.prompt_note


def test_notes_with_rewrite_strategy_appends_once(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("rewrite_strategy_notes"))
    layout.ensure_dirs()
    tmp_storage.save_text(layout.chapter_path(3), "第三章已有正文。")
    plan = build_rewrite_strategy_plan(
        storage=tmp_storage,
        layout=layout,
        chapter_number=2,
        force=True,
        requested_strategy="compatible",
    )

    merged = notes_with_rewrite_strategy("加强怀表线索", plan)
    merged_again = notes_with_rewrite_strategy(merged, plan)

    assert "加强怀表线索" in merged
    assert merged.count("作者层后文兼容约束") == 1
    assert merged_again == merged


def test_compatibility_package_scrubs_prompt_artifacts(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("rewrite_strategy_scrub"))
    layout.ensure_dirs()
    tmp_storage.save_text(
        layout.chapter_path(3),
        "完成场景转换至古董店，以环境细节锚定新空间。\n\n陆云峥推门入内。",
    )

    plan = build_rewrite_strategy_plan(
        storage=tmp_storage,
        layout=layout,
        chapter_number=2,
        force=True,
        requested_strategy="compatible",
    )

    assert "完成场景转换" not in plan.prompt_note
    assert "陆云峥推门入内" in plan.prompt_note
