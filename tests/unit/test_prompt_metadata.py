"""Tests for prompt catalog metadata and maintainability linting."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import OutputKind
from novel_forge.prompts.metadata import collect_prompt_templates, lint_prompt_catalog


def test_prompt_catalog_contains_registered_task_metadata() -> None:
    catalog = collect_prompt_templates()
    by_template = {record.template: record for record in catalog}

    draft = by_template["writing/draft_chapter.j2"]

    assert draft.task_types == (TaskType.DRAFT_CHAPTER,)
    assert draft.output_kinds == (OutputKind.TEXT,)
    assert draft.has_remark_layer is True
    assert draft.has_guidance_layer is True
    assert draft.has_format_layer is True
    assert "PlanCard" in draft.purpose


def test_prompt_catalog_keeps_shared_macros_separate_from_task_templates() -> None:
    catalog = collect_prompt_templates()
    shared_templates = [record for record in catalog if record.partial and not record.registered]

    assert shared_templates
    assert all(record.category == "shared" for record in shared_templates)


def test_registered_prompt_templates_pass_layer_lint() -> None:
    issues = lint_prompt_catalog()
    assert not issues


def test_prompt_catalog_exposes_per_step_format_profile() -> None:
    catalog = collect_prompt_templates()
    by_template = {record.template: record for record in catalog}

    patch = by_template["writing/patch_chapter.j2"]
    subplot = by_template["planning/subplot_polish.j2"]

    assert patch.format_profile == "strict-json"
    assert patch.required_top_level_keys == ("patches",)
    assert patch.missing_required_keys == ()

    assert subplot.format_profile == "strict-json"
    assert subplot.required_top_level_keys == ("subplots",)
    assert subplot.missing_required_keys == ()


def test_prompt_scaffold_renders_three_layer_json_skeleton() -> None:
    script_path = Path("scripts/scaffold_prompt.py").resolve()
    spec = importlib.util.spec_from_file_location("scaffold_prompt", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    rendered = module.render_prompt_skeleton(
        task_type="demo_task",
        category="planning",
        filename="demo_task.j2",
        output_kind=OutputKind.JSON,
        required_keys=("result",),
        purpose="生成演示输出",
    )

    assert "【备注】" in rendered
    assert "指导层" in rendered
    assert "业务字段说明" in rendered
    assert "standard_json_output" not in rendered
    assert "输出契约由 `TaskFormatContract` 和 `PromptBuilder` 统一注入" in rendered
    assert "`result`" in rendered
