"""Shared whole-book audit dimension contracts."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from novel_forge.common.constants import TaskType


@dataclass(frozen=True)
class GlobalAuditDimensionSpec:
    """Execution contract for one whole-book audit dimension."""

    name: str
    task_type: TaskType
    prompt_hint: str
    required_context_keys: tuple[str, ...]
    slice_kinds: tuple[str, ...]
    semantic_query_templates: tuple[str, ...]
    output_focus: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    parallel_group: str = "base"
    max_context_items: dict[str, int] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class DimensionAuditResult:
    """Normalized output from a dimension/slice audit lane."""

    dimension: str
    slice_id: str
    dimension_summary: str
    claims: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    handoff_notes: str = ""
    coverage: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


GLOBAL_AUDIT_DIMENSION_SPECS: tuple[GlobalAuditDimensionSpec, ...] = (
    GlobalAuditDimensionSpec(
        name="timeline_arc",
        task_type=TaskType.BOOK_CONSISTENCY_TIMELINE,
        prompt_hint="请只审计全局时间弧：事件顺序、时间跨度、卷/阶段边界与关键转折因果。",
        required_context_keys=(
            "chapter_summaries",
            "chapter_texts",
            "audit_slices",
            "arc_summary",
            "outline_summary",
            "chapter_exit_states",
            "shared_evidence_anchor",
        ),
        slice_kinds=("volume", "blueprint_phase", "turning_point", "whole_book"),
        semantic_query_templates=("时间标记 事件顺序 转折 因果 第{chapter}章",),
        output_focus=("event_order", "time_gap", "turning_point", "boundary_transition"),
    ),
    GlobalAuditDimensionSpec(
        name="character_arc",
        task_type=TaskType.BOOK_CONSISTENCY_CHARACTER_STATE,
        prompt_hint="请只审计角色弧：身份、关系、能力、位置、记忆、动机与状态演进。",
        required_context_keys=(
            "chapter_summaries",
            "chapter_texts",
            "character_profiles_compact",
            "canon_characters",
            "canon_relationships",
            "chapter_exit_states",
            "shared_evidence_anchor",
        ),
        slice_kinds=("volume", "blueprint_phase", "turning_point", "whole_book"),
        semantic_query_templates=("角色 状态 关系 能力 动机 第{chapter}章",),
        output_focus=("state_change", "relationship_change", "motivation_shift"),
    ),
    GlobalAuditDimensionSpec(
        name="world_rule_integrity",
        task_type=TaskType.BOOK_CONSISTENCY_WORLD_RULE,
        prompt_hint="请只审计世界规则完整性：规则、限制、代价、例外、组织制度是否自洽。",
        required_context_keys=(
            "chapter_summaries",
            "chapter_texts",
            "world_rules",
            "world_setting",
            "world_hint",
            "shared_evidence_anchor",
        ),
        slice_kinds=("volume", "blueprint_phase", "turning_point", "whole_book"),
        semantic_query_templates=("世界规则 限制 代价 例外 制度 第{chapter}章",),
        output_focus=("rule_claim", "exception", "cost", "violation"),
    ),
    GlobalAuditDimensionSpec(
        name="motif_distribution",
        task_type=TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT,
        prompt_hint="请只审计母题分布：意象、象征物、主题动作的复现、过密、消失与语义漂移。",
        required_context_keys=(
            "chapter_summaries",
            "chapter_texts",
            "motif_protocols",
            "story_theme",
            "memory_enhancement_context",
            "shared_evidence_anchor",
        ),
        slice_kinds=("motif", "volume", "blueprint_phase", "whole_book"),
        semantic_query_templates=("母题 意象 象征 复现 漂移 第{chapter}章",),
        output_focus=("motif_occurrence", "semantic_drift", "overuse", "dropout"),
    ),
    GlobalAuditDimensionSpec(
        name="promise_payoff",
        task_type=TaskType.BOOK_CONSISTENCY_PLOT_THREAD,
        prompt_hint="请只审计承诺与伏笔兑现：伏笔、誓言、任务、悬念是否兑现或合理延迟。",
        required_context_keys=(
            "chapter_summaries",
            "chapter_texts",
            "promise_ledger",
            "arc_summary",
            "dimension_ledger_context",
            "shared_evidence_anchor",
        ),
        slice_kinds=("promise_thread", "volume", "blueprint_phase", "whole_book"),
        semantic_query_templates=("伏笔 承诺 誓言 任务 兑现 延迟 第{chapter}章",),
        output_focus=("planted_promise", "payoff", "delay_reason", "orphaned_promise"),
        dependencies=("timeline_arc", "character_arc"),
        parallel_group="dependent",
    ),
    GlobalAuditDimensionSpec(
        name="plot_thread_liveness",
        task_type=TaskType.BOOK_CONSISTENCY_PLOT_THREAD,
        prompt_hint="请只审计情节线生命力：主线、支线、任务链是否断线、重复、无故消失或被错误收束。",
        required_context_keys=(
            "chapter_summaries",
            "chapter_texts",
            "plot_threads",
            "chapter_issue_pool",
            "dimension_ledger_context",
            "shared_evidence_anchor",
        ),
        slice_kinds=("plot_thread", "promise_thread", "volume", "whole_book"),
        semantic_query_templates=("主线 支线 情节线 任务链 悬念 断线 第{chapter}章",),
        output_focus=("thread_status", "open_loop", "duplicate_thread", "stale_thread"),
        dependencies=("timeline_arc", "promise_payoff"),
        parallel_group="dependent",
    ),
    GlobalAuditDimensionSpec(
        name="tension_curve",
        task_type=TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT,
        prompt_hint="请只审计张力曲线：阶段边界、高潮前后、缓冲段、冲突密度与节奏坍塌。",
        required_context_keys=(
            "chapter_summaries",
            "chapter_texts",
            "arc_summary",
            "story_theme",
            "conflict_hint",
            "dimension_ledger_context",
            "shared_evidence_anchor",
        ),
        slice_kinds=("volume", "blueprint_phase", "turning_point", "plot_thread", "whole_book"),
        semantic_query_templates=("张力 节奏 高潮 缓冲 冲突密度 第{chapter}章",),
        output_focus=("tension_rise", "climax", "cooldown", "density_problem"),
        dependencies=("timeline_arc", "plot_thread_liveness", "promise_payoff"),
        parallel_group="dependent",
    ),
)


def _validate_global_audit_dimension_specs(
    specs: tuple[GlobalAuditDimensionSpec, ...],
) -> None:
    """Fail fast if global-audit dimension dependencies are invalid."""
    names: set[str] = set()
    duplicates: set[str] = set()
    for spec in specs:
        if spec.name in names:
            duplicates.add(spec.name)
        names.add(spec.name)
    if duplicates:
        raise ValueError(
            "Duplicate global audit dimension spec names: " + ", ".join(sorted(duplicates))
        )

    missing = {
        dependency
        for spec in specs
        for dependency in spec.dependencies
        if dependency not in names
    }
    if missing:
        raise ValueError(
            "Unknown global audit dimension dependencies: " + ", ".join(sorted(missing))
        )

    visiting: set[str] = set()
    visited: set[str] = set()
    by_name = {spec.name: spec for spec in specs}

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise ValueError(f"Global audit dimension dependency cycle includes: {name}")
        visiting.add(name)
        for dependency in by_name[name].dependencies:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for spec in specs:
        visit(spec.name)


_validate_global_audit_dimension_specs(GLOBAL_AUDIT_DIMENSION_SPECS)

GLOBAL_AUDIT_DIMENSIONS: tuple[str, ...] = tuple(
    spec.name for spec in GLOBAL_AUDIT_DIMENSION_SPECS
)
GLOBAL_AUDIT_DIMENSION_SPECS_BY_NAME: dict[str, GlobalAuditDimensionSpec] = {
    spec.name: spec for spec in GLOBAL_AUDIT_DIMENSION_SPECS
}
