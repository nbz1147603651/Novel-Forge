"""Artifact helpers for workflow-related desktop pages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.core.utils.version_diff import draft_stem_display_label
from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.document_presenter import smart_artifact_content
from novel_forge.desktop.jobs import DesktopJobRecord
from novel_forge.desktop.pages.document_renderers import smart_render_document
from novel_forge.desktop.pages.standalone.renderer_html import (
    connect_deep_links,
    parse_deep_link,
)
from novel_forge.desktop.progress import (
    compute_job_progress,
    ui_parallel_pairs_for,
    ui_steps_for_kind,
)
from novel_forge.desktop.progress import (
    display_step_name as _shared_step_display,
)
from novel_forge.desktop.progress import (
    resolve_step_key as _shared_resolve_step_key,
)
from novel_forge.desktop.theme.documents import render_document_css
from novel_forge.desktop.ui_density import configure_tab_widget_density
from novel_forge.desktop.widgets import PipelineStep, show_info_message


def _compute_progress(job: DesktopJobRecord) -> int:
    return compute_job_progress(job)


def _step_display(raw_key: str) -> str:
    return _shared_step_display(raw_key)


def _resolve_step_key(kind: str, raw_key: str) -> str:
    return _shared_resolve_step_key(kind, raw_key)


def _steps_for_kind(kind: str) -> list[PipelineStep]:
    return ui_steps_for_kind(kind)


def _parallel_pairs_for_kind(kind: str) -> list[tuple[str, str]]:
    return ui_parallel_pairs_for(kind)


_CHAPTER_MEMORY_ARTIFACTS: list[tuple[str, str]] = [
    ("章节记忆诊断", "reports/chapter_{ch}_memory_diagnostics.json"),
    ("项目记忆索引", "memory/project_memory.json"),
    ("zvec 向量集合", "memory/zvec_vectors"),
    ("叙事状态索引", "memory/narrative_state_index.json"),
    ("情节要素进度", "plans/element_progress.json"),
]

_MOTIF_MEMORY_ARTIFACTS: list[tuple[str, str]] = [
    ("项目记忆索引", "memory/project_memory.json"),
    ("zvec 向量集合", "memory/zvec_vectors"),
    ("叙事状态索引", "memory/narrative_state_index.json"),
]

_STORY_BIBLE_FRAGMENT_ARTIFACTS: list[tuple[str, str]] = [
    ("世界观分片：核心前提", "initialization/fragments/story_bible/story_core.checkpoint.json"),
    (
        "世界观分片：世界规则与主题",
        "initialization/fragments/story_bible/story_bible_independent_fragments.checkpoint.json",
    ),
    (
        "世界观分片：连续性规则",
        "initialization/fragments/story_bible/story_bible_continuity.checkpoint.json",
    ),
]

_CHAPTER_CORE_REVIEW_ARTIFACTS: list[tuple[str, str]] = [
    ("大纲对齐报告", "reports/chapter_{ch}_alignment.json"),
    ("连贯性报告", "reports/chapter_{ch}_continuity.json"),
    ("因果链报告", "reports/chapter_{ch}_causal.json"),
    ("追读力报告", "reports/chapter_{ch}_reading_power.json"),
    ("知识边界审计", "reports/chapter_{ch}_knowledge_boundary_verification.json"),
    ("质量评估", "reports/chapter_{ch}_eval.json"),
]

_CHAPTER_GUARD_CONTROL_ARTIFACTS: list[tuple[str, str]] = [
    ("护栏报告", "reports/chapter_{ch}_guard.json"),
    ("质量门禁", "reports/chapter_{ch}_quality_gate.json"),
    ("阶段控制", "reports/chapter_{ch}_stage_visibility.json"),
    ("表达通道", "reports/chapter_{ch}_expression_repetition.json"),
]

_CHAPTER_DRAFT_ARTIFACTS: list[tuple[str, str]] = [
    ("DRAFT 原稿", "drafts/chapter_{ch}/v0_draft.md"),
    ("WAVE 编织稿", "drafts/chapter_{ch}/v1_wave.md"),
    ("最终章节", "chapters/chapter_{ch}.md"),
]

_CHAPTER_REPAIR_FOCUS_ARTIFACTS: list[tuple[str, str]] = [
    ("最终章节", "chapters/chapter_{ch}.md"),
    *_CHAPTER_CORE_REVIEW_ARTIFACTS,
    *_CHAPTER_GUARD_CONTROL_ARTIFACTS,
]


_CHAPTER_DECISION_ARTIFACTS: list[tuple[str, str]] = [
    ("质量评估", "reports/chapter_{ch}_eval.json"),
    ("大纲对齐报告", "reports/chapter_{ch}_alignment.json"),
    ("知识边界审计", "reports/chapter_{ch}_knowledge_boundary_verification.json"),
]


_CHAPTER_REPAIR_FOCUS_ARTIFACTS: list[tuple[str, str]] = [
    ("修复后稿件", "drafts/chapter_{ch}/v_final_review.md"),
    ("修复方案", "reports/chapter_{ch}_repair_plan.json"),
    ("连贯性报告", "reports/chapter_{ch}_continuity.json"),
    ("因果链报告", "reports/chapter_{ch}_causal.json"),
    ("追读力报告", "reports/chapter_{ch}_reading_power.json"),
    ("护栏报告", "reports/chapter_{ch}_guard.json"),
    ("阶段控制", "reports/chapter_{ch}_stage_visibility.json"),
    ("表达通道", "reports/chapter_{ch}_expression_repetition.json"),
]


_CHAPTER_STATE_EXTRACTION_ARTIFACTS: list[tuple[str, str]] = [
    ("最终章节", "chapters/chapter_{ch}.md"),
    ("创作报告", "reports/chapter_{ch}_creative.json"),
    ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
    ("状态裁判副本", "narrative_state/chapter_{ch}_state_adjudication.json"),
    ("状态裁判索引", "narrative_state/adjudication_report_index.json"),
    ("章节证据快照", "narrative_state/evidence/chapter_{ch}_evidence.json"),
    ("叙事状态账本", "narrative_state/state_ledger.jsonl"),
    ("叙事状态投影", "narrative_state/story_state_projection.json"),
    ("待定叙事队列", "narrative_state/pending_queue.json"),
    ("叙事记忆索引", "memory/narrative_state_index.json"),
]


_CHAPTER_PERSIST_ARTIFACTS: list[tuple[str, str]] = [
    ("最终章节", "chapters/chapter_{ch}.md"),
    ("创作报告", "reports/chapter_{ch}_creative.json"),
    ("规范状态", "canon/canon_current.json"),
]


_SHORT_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "spec": [("故事规格", "spec.json")],
    "chapter_research": [
        ("章节证据包", "reports/short_research_evidence.json"),
        ("章节研究报告", "reports/short_research_report.json"),
        ("章节研究摘要", "reports/short_research_dossier.json"),
    ],
    "short_blueprint_elements": [("叙事要素选择", "plans/blueprint_elements_selection.json")],
    "short_blueprint": [("叙事蓝图", "plans/short_blueprint.json")],
    "short_profile_style": [("风格规范", "style_profile.json")],
    "beats": [("节拍结构", "beats.json")],
    "short_execution_plan": [
        ("分段执行方案", "plans/short_segment_plan.json"),
    ],
    "draft": [("初稿", "drafts/v0_draft.md")],
    "edit_": [],
    "short_completeness_check": [
        ("完整性检查", "reports/short_quality_gate.json"),
        ("当前正文", "drafts/v0_draft.md"),
    ],
    "evaluate": [
        ("最终文本", "chapters/short_story.md"),
        ("评估报告", "reports/eval_report.json"),
    ],
    "creative_summary": [
        ("创作分析", "reports/short_creative_summary.json"),
    ],
}

_INIT_LONG_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "spec": [("故事规格", "spec.json")],
    "init_web_research": [
        ("资料检索报告", "reports/init_web_research.json"),
        ("资料分析报告", "reports/init_research_dossier.json"),
        ("大纲资料校准", "reports/outline_research_grounding.json"),
    ],
    "init_story_bible": [
        ("世界观设定", "story_bible.json"),
        ("上游健康门", "reports/init_upstream_health.json"),
    ],
    "init_character_bible": [("角色设定", "character_bible.json")],
    "init_character_system": [("角色系统审计", "states/init_v2/character_system.json")],
    "plan_blueprint_elements": [("叙事要素选择", "plans/blueprint_elements_selection.json")],
    "profile_style": [("风格规范", "style_profile.json")],
    "init_entity_registry": [("实体注册表", "narrative_state/entity_registry.json")],
    "init_entity_graph": [("实体图谱", "narrative_state/entity_graph.json")],
    "creative_director_packet": [("创作导演包", "plans/creative_director_packet.json")],
    "derive_editorial_contract": [
        ("编辑契约", "plans/editorial_contract.json"),
        ("编辑契约准入", "reports/init_editorial_readiness.json"),
    ],
    "init_narrative_contract": [
        ("叙事契约", "plans/narrative_contract.json"),
        ("叙事状态投影", "narrative_state/story_state_projection.json"),
    ],
    "plan_blueprint": [("叙事蓝图", "plans/narrative_blueprint.json")],
    "plan_blueprint_fragments": [("叙事蓝图分块", "plans/narrative_blueprint_fragments.json")],
    "plan_blueprint_validated": [("验证后叙事蓝图", "plans/narrative_blueprint.json")],
    "plan_blueprint_repaired": [("修复后叙事蓝图", "plans/narrative_blueprint.json")],
    "derive_init_coherence_profile": [
        ("一致性画像", "reports/init_coherence_profile.json"),
    ],
    "refine_init_coherence_profile": [
        ("精炼后一致性画像", "reports/init_coherence_profile.json"),
    ],
    "build_init_coherence_profile": [
        ("一致性画像", "reports/init_coherence_profile.json"),
    ],
    "extract_init_coherence_claims": [
        ("初始化一致性 Claims", "memory/init_coherence_claims.jsonl"),
        ("初始化一致性 Claims 账本", "memory/init_coherence_claim_ledger.json"),
        ("初始化一致性索引", "memory/init_coherence_index.json"),
    ],
    "retrieve_init_conflict_candidates": [
        ("冲突候选报告", "reports/init_conflict_candidates.json"),
    ],
    "adjudicate_init_conflict_candidates": [
        ("冲突候选裁判", "reports/init_conflict_adjudication.json"),
    ],
    "adjudicate_blueprint_coherence": [("蓝图裁判报告", "reports/blueprint_coherence.json")],
    "repair_init_artifact_patch": [("初始化修复报告", "reports/init_artifact_repair.json")],
    "plan_blueprint_subplot_matrix": [("支线执行矩阵", "plans/subplot_execution_matrix.json")],
    "plan_blueprint_subplot_weave_validation": [
        ("支线闭环校验", "reports/subplot_weave_validation.json")
    ],
    "plan_chapter_design_matrix": [
        ("章节设计矩阵（结构约束）", "plans/chapter_design_matrix.json"),
    ],
    "plan_outline": [
        ("章节大纲", "outline.json"),
        ("揭示边界产物清单", "states/artifact_manifest.json"),
        ("大纲断点", "states/outline_session.json"),
    ],
    "adjudicate_outline_inheritance": [("大纲继承裁判", "reports/outline_inheritance.json")],
    "plan_chapter_contracts": [
        ("章节契约", "plans/chapter_contracts.json"),
        ("揭示边界产物清单", "states/artifact_manifest.json"),
    ],
    "adjudicate_contract_coherence": [("契约裁判报告", "reports/contract_coherence.json")],
    "init_claim_contract_coverage": [
        ("一致性 Claims 契约覆盖", "reports/init_claim_contract_coverage.json")
    ],
    "init_repair_reaudit_stage": [
        ("修复后契约复审", "reports/contract_coherence.json"),
        ("修复后 Claims 覆盖复审", "reports/init_claim_contract_coverage.json"),
        ("初始化准入报告", "reports/init_readiness.json"),
    ],
    "init_repair_reaudit_passed": [
        ("修复后契约复审", "reports/contract_coherence.json"),
        ("修复后 Claims 覆盖复审", "reports/init_claim_contract_coverage.json"),
        ("初始化准入报告", "reports/init_readiness.json"),
    ],
    "init_source_artifacts": [
        ("源头准入报告", "source_artifacts/init_readiness.json"),
        ("源头 artifact", "source_artifacts/*.json"),
    ],
    "init_readiness": [
        ("初始化准入报告", "reports/init_readiness.json"),
        ("效率指标", "reports/init_efficiency_metrics.json"),
        ("上游健康门", "reports/init_upstream_health.json"),
    ],
    "canon_state": [("规范状态", "canon/canon_current.json")],
}

_CHAPTER_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "state_packet": [("章节上下文", "states/chapter_{ch}_state_packet.json")],
    "chapter_research": [
        ("章节证据包", "reports/chapter_{ch}_research_evidence.json"),
        ("章节研究报告", "reports/chapter_{ch}_research_report.json"),
        ("章节研究摘要", "reports/chapter_{ch}_research_dossier.json"),
    ],
    "bridge": [
        ("章节桥接", "plans/chapter_{ch}_bridge.json"),
        ("章节计划", "plans/chapter_{ch}_plan.json"),
        ("场景计划", "plans/chapter_{ch}_scene_plan.json"),
        ("场景计划验证", "reports/chapter_{ch}_scene_plan_validation.json"),
    ],
    "plan": [
        ("章节计划", "plans/chapter_{ch}_plan.json"),
        ("场景计划", "plans/chapter_{ch}_scene_plan.json"),
        ("场景计划验证", "reports/chapter_{ch}_scene_plan_validation.json"),
    ],
    "draft": _CHAPTER_DRAFT_ARTIFACTS,
    "wave": _CHAPTER_DRAFT_ARTIFACTS,
    "opening_guard": [
        ("最终章节", "chapters/chapter_{ch}.md"),
        *_CHAPTER_GUARD_CONTROL_ARTIFACTS,
    ],
    "alignment": [
        *_CHAPTER_CORE_REVIEW_ARTIFACTS,
        *_CHAPTER_GUARD_CONTROL_ARTIFACTS,
    ],
    "continuity_repair": [
        ("最终章节", "chapters/chapter_{ch}.md"),
        ("连贯性报告", "reports/chapter_{ch}_continuity.json"),
        ("连续性修复记录", "reports/revisions/chapter_{ch}_continuity_repair.json"),
    ],
    "alignment_repair": _CHAPTER_REPAIR_FOCUS_ARTIFACTS,
    "guard_review": [
        ("最终章节", "chapters/chapter_{ch}.md"),
        *_CHAPTER_GUARD_CONTROL_ARTIFACTS,
    ],
    "causal_repair": [
        ("最终章节", "chapters/chapter_{ch}.md"),
        ("因果链报告", "reports/chapter_{ch}_causal.json"),
        ("因果修复记录", "reports/revisions/chapter_{ch}_causal_repair.json"),
    ],
    "reading_power_repair": [
        ("最终章节", "chapters/chapter_{ch}.md"),
        ("追读力报告", "reports/chapter_{ch}_reading_power.json"),
        ("追读力修复记录", "reports/revisions/chapter_{ch}_reading_power_repair.json"),
    ],
    "polish": [
        ("润色后章节", "chapters/chapter_{ch}.md"),
        ("文学精修对比", "reports/revisions/chapter_{ch}_polish_chapter.json"),
        ("质量评估", "reports/chapter_{ch}_eval.json"),
    ],
    "humanize": [
        ("拟人化扫描", "reports/chapter_{ch}_humanize.json"),
        ("拟人化修订对比", "reports/revisions/chapter_{ch}_humanize_layer.json"),
        ("最终章节", "chapters/chapter_{ch}.md"),
    ],
    "extract_canon": [
        ("最终章节", "chapters/chapter_{ch}.md"),
        ("创作报告", "reports/chapter_{ch}_creative.json"),
        ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
        ("状态裁判副本", "narrative_state/chapter_{ch}_state_adjudication.json"),
        ("状态裁判索引", "narrative_state/adjudication_report_index.json"),
        ("章节证据快照", "narrative_state/evidence/chapter_{ch}_evidence.json"),
        ("叙事状态账本", "narrative_state/state_ledger.jsonl"),
        ("叙事状态投影", "narrative_state/story_state_projection.json"),
        ("待定叙事队列", "narrative_state/pending_queue.json"),
        ("叙事记忆索引", "memory/narrative_state_index.json"),
        *_CHAPTER_CORE_REVIEW_ARTIFACTS,
        *_CHAPTER_GUARD_CONTROL_ARTIFACTS,
    ],
    "persist": [
        ("最终章节", "chapters/chapter_{ch}.md"),
        ("创作报告", "reports/chapter_{ch}_creative.json"),
        ("质量评估", "reports/chapter_{ch}_eval.json"),
    ],
    "memory_updated": _CHAPTER_MEMORY_ARTIFACTS,
    "candidate_state_deltas": [
        ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
        ("章节证据快照", "narrative_state/evidence/chapter_{ch}_evidence.json"),
    ],
    "state_delta_adjudication": [
        ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
    ],
    "final_state_adjudication": [
        ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
    ],
    "repair_adjudicated_issue": [
        ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
        ("最终章节", "chapters/chapter_{ch}.md"),
    ],
    "state_ledger_prepared": [
        ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
    ],
    "state_ledger_written": [
        ("叙事状态账本", "narrative_state/state_ledger.jsonl"),
        ("叙事状态投影", "narrative_state/story_state_projection.json"),
        ("待定叙事队列", "narrative_state/pending_queue.json"),
        ("状态裁判索引", "narrative_state/adjudication_report_index.json"),
        ("叙事记忆索引", "memory/narrative_state_index.json"),
    ],
    "humanize_scan": [
        ("拟人化扫描", "reports/chapter_{ch}_humanize.json"),
        ("拟人化修订对比", "reports/revisions/chapter_{ch}_humanize_layer.json"),
    ],
    "humanize_revision_diff": [
        ("拟人化修订对比", "reports/revisions/chapter_{ch}_humanize_layer.json"),
    ],
    "knowledge_boundary_verification": [
        ("知识边界审计", "reports/chapter_{ch}_knowledge_boundary_verification.json"),
    ],
}

_PREPARE_CHAPTER_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "state_packet": [("章节上下文", "states/chapter_{ch}_state_packet.json")],
    # NIMO's workflow node is named ``plan`` while the preparation run
    # persists the concrete chapter/scene plan artifacts.  Retain the same
    # alias here as the API resolver for desktop safety parity.
    "plan": [
        ("章节计划", "plans/chapter_{ch}_plan.json"),
        ("场景计划", "plans/chapter_{ch}_scene_plan.json"),
        ("场景计划验证", "reports/chapter_{ch}_scene_plan_validation.json"),
    ],
    "bridge": [
        ("章节桥接", "plans/chapter_{ch}_bridge.json"),
        ("章节计划", "plans/chapter_{ch}_plan.json"),
        ("场景计划", "plans/chapter_{ch}_scene_plan.json"),
        ("场景计划验证", "reports/chapter_{ch}_scene_plan_validation.json"),
    ],
    "scene_plan_validation": [
        ("场景计划", "plans/chapter_{ch}_scene_plan.json"),
        ("场景计划验证", "reports/chapter_{ch}_scene_plan_validation.json"),
    ],
    "plan_checkpoint": [
        ("章节上下文", "states/chapter_{ch}_state_packet.json"),
        ("章节桥接", "plans/chapter_{ch}_bridge.json"),
        ("章节计划", "plans/chapter_{ch}_plan.json"),
        ("场景计划", "plans/chapter_{ch}_scene_plan.json"),
        ("场景计划验证", "reports/chapter_{ch}_scene_plan_validation.json"),
    ],
}

_RESOLVE_CHECKPOINT_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "plan_checkpoint": [
        ("章节上下文", "states/chapter_{ch}_state_packet.json"),
        ("章节桥接", "plans/chapter_{ch}_bridge.json"),
        ("章节计划", "plans/chapter_{ch}_plan.json"),
        ("场景计划", "plans/chapter_{ch}_scene_plan.json"),
        ("场景计划验证", "reports/chapter_{ch}_scene_plan_validation.json"),
    ],
    "draft": [
        ("初稿成章", "drafts/chapter_{ch}/v1_wave.md"),
        ("DRAFT 原稿", "drafts/chapter_{ch}/v0_draft.md"),
        ("场景拼接稿", "drafts/chapter_{ch}_scene_stitched.md"),
        ("场景拼接报告", "reports/chapter_{ch}_scene_stitch_report.json"),
    ],
    "draft_scene": [
        ("场景拼接稿", "drafts/chapter_{ch}_scene_stitched.md"),
        ("场景拼接报告", "reports/chapter_{ch}_scene_stitch_report.json"),
    ],
    "scene_stitch": [
        ("场景拼接稿", "drafts/chapter_{ch}_scene_stitched.md"),
        ("场景拼接报告", "reports/chapter_{ch}_scene_stitch_report.json"),
    ],
    "pre_alignment": [
        *_CHAPTER_CORE_REVIEW_ARTIFACTS,
    ],
    "alignment": [
        *_CHAPTER_CORE_REVIEW_ARTIFACTS,
        *_CHAPTER_GUARD_CONTROL_ARTIFACTS,
    ],
    "continuity_repair": [
        *_CHAPTER_CORE_REVIEW_ARTIFACTS,
        *_CHAPTER_REPAIR_FOCUS_ARTIFACTS,
    ],
    "alignment_repair": [
        *_CHAPTER_CORE_REVIEW_ARTIFACTS,
        *_CHAPTER_REPAIR_FOCUS_ARTIFACTS,
    ],
    "guard_checkpoint": [
        *_CHAPTER_DECISION_ARTIFACTS,
    ],
    "post_alignment": [
        *_CHAPTER_CORE_REVIEW_ARTIFACTS,
        *_CHAPTER_GUARD_CONTROL_ARTIFACTS,
    ],
    "post_guard_repair": [
        *_CHAPTER_REPAIR_FOCUS_ARTIFACTS,
    ],
    "polish_reextract_canon": [
        *_CHAPTER_STATE_EXTRACTION_ARTIFACTS,
    ],
    "persist": [
        *_CHAPTER_PERSIST_ARTIFACTS,
    ],
    "candidate_state_deltas": [
        ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
        ("章节证据快照", "narrative_state/evidence/chapter_{ch}_evidence.json"),
    ],
    "state_delta_adjudication": [
        ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
    ],
    "final_state_adjudication": [
        ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
    ],
    "repair_adjudicated_issue": [
        ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
        ("最终章节", "chapters/chapter_{ch}.md"),
    ],
    "state_ledger_prepared": [
        ("状态裁判报告", "reports/chapter_{ch}_state_adjudication.json"),
    ],
    "state_ledger_written": [
        ("叙事状态账本", "narrative_state/state_ledger.jsonl"),
        ("叙事状态投影", "narrative_state/story_state_projection.json"),
        ("待定叙事队列", "narrative_state/pending_queue.json"),
        ("状态裁判索引", "narrative_state/adjudication_report_index.json"),
        ("叙事记忆索引", "memory/narrative_state_index.json"),
    ],
    "evaluate": [
        ("质量评估", "reports/chapter_{ch}_eval.json"),
    ],
    "humanize_scan": [
        ("拟人化扫描", "reports/chapter_{ch}_humanize.json"),
        ("拟人化修订对比", "reports/revisions/chapter_{ch}_humanize_layer.json"),
    ],
    "humanize_revision_diff": [
        ("拟人化修订对比", "reports/revisions/chapter_{ch}_humanize_layer.json"),
    ],
    "knowledge_boundary_verification": [
        ("知识边界审计", "reports/chapter_{ch}_knowledge_boundary_verification.json"),
    ],
    "volume_audit": [
        ("卷末审计报告", "reports/volume_{ch}_audit.json"),
    ],
    "memory_updated": _CHAPTER_MEMORY_ARTIFACTS,
}

_POLISH_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "polish_start": [
        ("当前章节", "chapters/chapter_{ch}.md"),
        ("章节草稿", "drafts/chapter_{ch}/v0_draft.md"),
        ("质量评估", "reports/chapter_{ch}_eval.json"),
    ],
    "polish": [
        ("润色后章节", "chapters/chapter_{ch}.md"),
        ("润色修订对比", "reports/revisions/chapter_{ch}_polish_chapter.json"),
        ("质量评估", "reports/chapter_{ch}_eval.json"),
    ],
    "polish_revision_diff": [
        ("润色修订对比", "reports/revisions/chapter_{ch}_polish_chapter.json"),
    ],
}

_REPAIR_CONTINUITY_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "repair_continuity_start": [
        ("修复前章节", "chapters/chapter_{ch}.md"),
        ("连贯性报告", "reports/chapter_{ch}_continuity.json"),
    ],
    "repair_continuity": [
        ("修复后章节", "chapters/chapter_{ch}.md"),
        ("连贯性报告", "reports/chapter_{ch}_continuity.json"),
    ],
    "continuity_eval_after_repair": [
        ("修复后章节", "chapters/chapter_{ch}.md"),
        ("连贯性报告", "reports/chapter_{ch}_continuity.json"),
    ],
}

_REPAIR_CAUSAL_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "repair_causal_start": [
        ("修复前章节", "chapters/chapter_{ch}.md"),
        ("因果链报告", "reports/chapter_{ch}_causal.json"),
    ],
    "repair_causal": [
        ("修复后章节", "chapters/chapter_{ch}.md"),
        ("因果链报告", "reports/chapter_{ch}_causal.json"),
    ],
    "causal_eval_after_repair": [
        ("修复后章节", "chapters/chapter_{ch}.md"),
        ("因果链报告", "reports/chapter_{ch}_causal.json"),
    ],
}

_REPAIR_ISSUES_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "repair_continuity_start": [
        ("修复前章节", "chapters/chapter_{ch}.md"),
        ("连贯性报告", "reports/chapter_{ch}_continuity.json"),
    ],
    "repair_causal_start": [
        ("章节正文", "chapters/chapter_{ch}.md"),
        ("因果链报告", "reports/chapter_{ch}_causal.json"),
    ],
    "repair_issues": [
        ("修复后章节", "chapters/chapter_{ch}.md"),
        ("连贯性报告", "reports/chapter_{ch}_continuity.json"),
        ("因果链报告", "reports/chapter_{ch}_causal.json"),
        *_CHAPTER_MEMORY_ARTIFACTS,
    ],
}

_REEVALUATE_CHAPTER_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "reevaluate_start": [
        ("章节正文", "chapters/chapter_{ch}.md"),
        *_CHAPTER_CORE_REVIEW_ARTIFACTS,
        *_CHAPTER_GUARD_CONTROL_ARTIFACTS,
    ],
    "evaluate": [
        ("章节正文", "chapters/chapter_{ch}.md"),
        ("质量评估", "reports/chapter_{ch}_eval.json"),
    ],
    "continuity_eval_after_repair": [
        ("章节正文", "chapters/chapter_{ch}.md"),
        ("连贯性报告", "reports/chapter_{ch}_continuity.json"),
    ],
    "causal_eval_after_repair": [
        ("章节正文", "chapters/chapter_{ch}.md"),
        ("因果链报告", "reports/chapter_{ch}_causal.json"),
    ],
    "reading_power_eval": [
        ("章节正文", "chapters/chapter_{ch}.md"),
        ("追读力报告", "reports/chapter_{ch}_reading_power.json"),
    ],
    "reevaluate_chapter": [
        *_CHAPTER_CORE_REVIEW_ARTIFACTS,
        *_CHAPTER_GUARD_CONTROL_ARTIFACTS,
    ],
}

_BOOK_CONSISTENCY_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "book_consistency_start": [
        ("全书一致性审计", "reports/book_consistency_audit.json"),
    ],
    "book_consistency_issue_pool_ready": [
        ("全书一致性审计", "reports/book_consistency_audit.json"),
    ],
    "book_consistency": [
        ("全书一致性审计", "reports/book_consistency_audit.json"),
    ],
    "book_consistency_audit_saved": [
        ("全书一致性审计", "reports/book_consistency_audit.json"),
    ],
    "book_consistency_verify_start": [
        ("全书一致性审计", "reports/book_consistency_audit.json"),
    ],
    "book_consistency_verify_": [
        ("全书一致性审计", "reports/book_consistency_audit.json"),
    ],
    "book_consistency_verify_progress": [
        ("全书一致性审计", "reports/book_consistency_audit.json"),
    ],
    "book_consistency_verify_done": [
        ("全书一致性审计", "reports/book_consistency_audit.json"),
    ],
    "book_consistency_repair_start": [
        ("全书一致性审计", "reports/book_consistency_audit.json"),
    ],
    "book_consistency_repair_": [
        ("全书修复报告", "reports/book_consistency_repair_report.json"),
        ("全书一致性审计", "reports/book_consistency_audit.json"),
        ("验证断点", "states/book_consistency_verify_checkpoint.json"),
        ("修复断点", "states/book_consistency_repair_checkpoint.json"),
    ],
    "book_consistency_repair_progress": [
        ("全书一致性审计", "reports/book_consistency_audit.json"),
        ("全书修复报告", "reports/book_consistency_repair_report.json"),
    ],
    "book_consistency_repair_review": [
        ("全书修复报告", "reports/book_consistency_repair_report.json"),
        ("全书一致性审计", "reports/book_consistency_audit.json"),
    ],
    "book_consistency_report_written": [
        ("全书一致性审计", "reports/book_consistency_audit.json"),
        ("全书修复报告", "reports/book_consistency_repair_report.json"),
    ],
    "book_consistency_repair_report_written": [
        ("全书修复报告", "reports/book_consistency_repair_report.json"),
        ("全书一致性审计", "reports/book_consistency_audit.json"),
    ],
}

_REEXTRACT_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "reextract_start": [
        ("当前章节", "chapters/chapter_{ch}.md"),
        ("创作报告", "reports/chapter_{ch}_creative.json"),
    ],
    "reextract_chapter": [
        ("当前章节", "chapters/chapter_{ch}.md"),
        ("创作报告", "reports/chapter_{ch}_creative.json"),
    ],
    "reextract_done": [
        ("创作报告", "reports/chapter_{ch}_creative.json"),
        ("叙事状态账本", "narrative_state/state_ledger.jsonl"),
        ("叙事状态投影", "narrative_state/story_state_projection.json"),
    ],
}

_EXPORT_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "export": [
        ("默认导出目录", "exports"),
    ],
}

_TTS_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "tts_prepare": [
        ("旁白声音画像", "tts/narrator_profile.json"),
        ("配音团队", "tts/voice_team.json"),
        ("音频执行方案", "tts/audio_execution_plan.json"),
        ("声音创作圣经", "tts/audio_creative_bible.json"),
    ],
    "tts_script": [
        ("配音脚本", "tts/scripts/chapter_{ch}_script.json"),
        ("章节 TTS 元数据", "reports/chapter_{ch}_tts_metadata.json"),
    ],
    "tts_synthesis": [
        ("可续跑合成进度", "states/tts_progress/chapter_{ch}.json"),
    ],
    "tts_post": [
        ("语音时间线", "tts/timelines/chapter_{ch}.json"),
        ("混音计划", "tts/mix_plans/chapter_{ch}.json"),
        ("声音素材解析", "tts/sound_resolutions/chapter_{ch}.json"),
        ("声音生成报告", "tts/sound_generation/chapter_{ch}.json"),
        ("混音渲染报告", "tts/render_reports/chapter_{ch}.json"),
    ],
    "tts_delivery": [
        ("自动配音运行记录", "tts/results/chapter_{ch}_auto_run.json"),
        ("章节音频结果", "tts/results/chapter_{ch}_audio.json"),
        ("音频质量报告", "tts/quality/chapter_{ch}.json"),
        ("章节字幕", "tts/audio/chapter_{ch}/chapter.srt"),
        ("章节成品音频", "tts/audio/chapter_{ch}/chapter_full.mp3"),
    ],
}

_REPAIR_MOTIF_HISTORY_ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "motif_history_repair_start": _MOTIF_MEMORY_ARTIFACTS,
    "motif_repair_layer1_start": _MOTIF_MEMORY_ARTIFACTS,
    "motif_repair_layer1_done": _MOTIF_MEMORY_ARTIFACTS,
    "motif_repair_layer2_start": _MOTIF_MEMORY_ARTIFACTS,
    "motif_repair_layer2_scanning": _MOTIF_MEMORY_ARTIFACTS,
    "motif_repair_layer2_progress": _MOTIF_MEMORY_ARTIFACTS,
    "motif_repair_layer2_done": _MOTIF_MEMORY_ARTIFACTS,
    "motif_history_repair_done": _MOTIF_MEMORY_ARTIFACTS,
    "motif_history_repair_error": _MOTIF_MEMORY_ARTIFACTS,
}


def _artifact_mapping_for(kind: str) -> dict[str, list[tuple[str, str]]]:
    return {
        "run_short": _SHORT_ARTIFACTS,
        "init_long": _INIT_LONG_ARTIFACTS,
        "run_chapter": _CHAPTER_ARTIFACTS,
        "prepare_chapter": _PREPARE_CHAPTER_ARTIFACTS,
        "resolve_chapter_checkpoint": _RESOLVE_CHECKPOINT_ARTIFACTS,
        "resolve_chapter_checkpoint_finalize": _RESOLVE_CHECKPOINT_ARTIFACTS,
        "polish_chapter": _POLISH_ARTIFACTS,
        "repair_continuity": _REPAIR_CONTINUITY_ARTIFACTS,
        "repair_causal": _REPAIR_CAUSAL_ARTIFACTS,
        "repair_issues": _REPAIR_ISSUES_ARTIFACTS,
        "reevaluate_chapter": _REEVALUATE_CHAPTER_ARTIFACTS,
        "book_consistency": _BOOK_CONSISTENCY_ARTIFACTS,
        "export_book": _EXPORT_ARTIFACTS,
        "reextract_relationships": _REEXTRACT_ARTIFACTS,
        "repair_motif_history": _REPAIR_MOTIF_HISTORY_ARTIFACTS,
        "tts_synthesize": _TTS_ARTIFACTS,
        "tts_full_pipeline": _TTS_ARTIFACTS,
        "tts_post_archive": _TTS_ARTIFACTS,
    }.get(kind, {})


def _entries_for_step(
    mapping: dict[str, list[tuple[str, str]]],
    step_key: str,
) -> list[tuple[str, str]]:
    if step_key in mapping:
        return list(mapping[step_key])
    for key, entries in mapping.items():
        if key.endswith("_") and step_key.startswith(key):
            return list(entries)
    return []


def _append_artifact(
    result: list[tuple[str, Path]],
    label: str,
    path: Path,
) -> None:
    if not path.is_file():
        return
    if any(existing == path for _, existing in result):
        return
    result.append((label, path))


def _chapter_draft_rank(path: Path) -> tuple[int, int, str]:
    stem = path.stem
    if stem == "v_final_review":
        return (3, 0, stem)
    if stem.startswith("v") and "_edited" in stem:
        try:
            return (2, int(stem.split("_", 1)[0][1:]), stem)
        except (IndexError, ValueError):
            return (1, 0, stem)
    if stem == "v1_wave":
        return (1, 1, stem)
    if stem == "v0_draft":
        return (0, 0, stem)
    return (1, 0, stem)


def _append_chapter_draft_versions(
    result: list[tuple[str, Path]],
    project_dir: Path,
    chapter_token: str,
) -> None:
    draft_dir = project_dir / "drafts" / f"chapter_{chapter_token}"
    if not draft_dir.is_dir():
        return
    paths = sorted(draft_dir.glob("v*.md"), key=_chapter_draft_rank, reverse=True)
    for path in paths:
        label = draft_stem_display_label(path.stem)
        if path.stem == "v0_draft":
            _append_artifact(result, "章节草稿（DRAFT 原稿）", path)
        elif path.stem == "v1_wave":
            _append_artifact(result, "章节草稿（初稿成章）", path)
        else:
            _append_artifact(result, f"章节草稿（{label}）", path)


def _artifact_candidates(
    kind: str,
    step_key: str,
    project_dir: Path,
    chapter_number: int = 0,
) -> list[tuple[str, Path]]:
    mapping = _artifact_mapping_for(kind)
    ch = f"{chapter_number:03d}"
    candidates = [
        (label, project_dir / rel.replace("{ch}", ch))
        for label, rel in _entries_for_step(mapping, step_key)
    ]

    if kind == "init_long" and step_key == "init_story_bible":
        candidates.extend(
            (label, project_dir / rel) for label, rel in _STORY_BIBLE_FRAGMENT_ARTIFACTS
        )
    if kind == "run_short" and step_key.startswith("edit_"):
        candidates.append(("编辑稿", project_dir / "drafts" / "v*_edited.md"))
        candidates.append(("初稿", project_dir / "drafts" / "v0_draft.md"))
    if kind == "run_short" and step_key in {"draft", "short_execution_plan"}:
        candidates.append(("分段草稿", project_dir / "drafts" / "segments" / "segment_*.md"))
        candidates.append(
            ("分段桥接", project_dir / "plans" / "short_segments" / "segment_*_bridge.json")
        )
    if kind == "run_chapter" and step_key in {"bridge", "draft", "wave"}:
        candidates.append(("稿件版本", project_dir / "drafts" / f"chapter_{ch}" / "v*.md"))
    if kind in {
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
    } and step_key in {"draft", "guard_checkpoint"}:
        candidates.append(("章节稿件版本", project_dir / "drafts" / f"chapter_{ch}" / "v*.md"))
    if kind == "export_book" and step_key == "export":
        candidates.append(("默认导出文件", project_dir / "exports" / "*"))
    if kind == "book_consistency":
        candidates.append(
            ("带时间戳审计报告", project_dir / "reports" / "book_consistency_audit_*.json")
        )
    return candidates


def _resolve_artifacts(
    kind: str,
    step_key: str,
    project_dir: Path,
    chapter_number: int = 0,
) -> list[tuple[str, Path]]:
    mapping = _artifact_mapping_for(kind)
    entries = _entries_for_step(mapping, step_key)
    ch = f"{chapter_number:03d}"
    result: list[tuple[str, Path]] = []

    if kind in {
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
    } and step_key in {"draft", "guard_checkpoint"}:
        _append_chapter_draft_versions(result, project_dir, ch)

    for label, rel in entries:
        rel_filled = rel.replace("{ch}", ch)
        path = project_dir / rel_filled
        _append_artifact(result, label, path)

    if kind == "init_long" and step_key == "init_story_bible" and not result:
        for label, rel in _STORY_BIBLE_FRAGMENT_ARTIFACTS:
            _append_artifact(result, label, project_dir / rel)

    if kind == "run_short" and step_key.startswith("edit_"):
        drafts_dir = project_dir / "drafts"
        if drafts_dir.is_dir():
            for path in sorted(drafts_dir.glob("v*_edited.md")):
                _append_artifact(result, f"编辑稿 ({path.stem})", path)
        if not result:
            draft_path = project_dir / "drafts" / "v0_draft.md"
            _append_artifact(result, "初稿", draft_path)

    if kind == "run_short" and step_key in {"draft", "short_execution_plan"}:
        for base, label_prefix, pattern in (
            (project_dir / "drafts" / "segments", "分段草稿", "segment_*.md"),
            (project_dir / "plans" / "short_segments", "分段桥接", "segment_*_bridge.json"),
        ):
            if base.is_dir():
                for path in sorted(base.glob(pattern)):
                    _append_artifact(result, f"{label_prefix} ({path.stem})", path)

    if kind == "run_chapter" and step_key in {"bridge", "draft", "wave"}:
        _append_chapter_draft_versions(result, project_dir, ch)

    if (
        kind in {"resolve_chapter_checkpoint", "resolve_chapter_checkpoint_finalize"}
        and step_key == "volume_audit"
    ):
        # Volume audit files follow pattern: volume_NNN_audit.json
        reports_dir = project_dir / "reports"
        if reports_dir.is_dir():
            audit_files = sorted(reports_dir.glob("volume_*_audit.json"))
            if audit_files:
                _append_artifact(result, "卷末审计报告", audit_files[-1])

    if kind == "book_consistency":
        reports_dir = project_dir / "reports"
        if reports_dir.is_dir():
            for pattern, label in (
                ("book_consistency_audit_*.json", "带时间戳审计报告"),
                ("book_consistency_repair_report_*.json", "带时间戳修复报告"),
            ):
                paths = sorted(reports_dir.glob(pattern), key=lambda item: item.stat().st_mtime)
                if paths:
                    _append_artifact(result, label, paths[-1])

    if kind == "export_book" and step_key == "export":
        export_dir = project_dir / "exports"
        if export_dir.is_dir():
            export_files = [path for path in export_dir.iterdir() if path.is_file()]
            export_files.sort(key=lambda item: (item.stat().st_mtime, item.name), reverse=True)
            for path in export_files[:20]:
                _append_artifact(result, "导出文件", path)

    return result


def step_has_artifacts(
    *,
    kind: str,
    step_key: str,
    project_dir: Path | None,
    chapter_number: int = 0,
) -> bool:
    """Return whether a completed task-flow step has a concrete file to show."""
    if project_dir is None:
        return False
    return bool(_resolve_artifacts(kind, step_key, project_dir, chapter_number))


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    """Best-effort parse of JSONL records."""
    rows: list[dict[str, Any]] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _truncate_message(message: str, limit: int = 180) -> str:
    text = " ".join(str(message or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _latest_init_long_log_dir(project_dir: Path) -> Path | None:
    logs_dir = project_dir / "logs"
    if not logs_dir.is_dir():
        return None
    candidates = [p for p in logs_dir.iterdir() if p.is_dir() and "_desktop-init-long_" in p.name]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0]


def _extract_profile_style_failure_reason(project_dir: Path) -> str | None:
    """Extract a human-readable reason when style_profile.json is missing."""
    history_path = project_dir / "states" / "task_flow_history.json"
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            history = []
        if isinstance(history, list):
            for job in reversed(history):
                if not isinstance(job, dict) or job.get("kind") != "init_long":
                    continue
                events = job.get("events", [])
                if not isinstance(events, list):
                    continue
                for event in reversed(events):
                    if not isinstance(event, dict):
                        continue
                    step = str(event.get("step", "")).strip()
                    payload = event.get("payload")
                    if step == "profile_style_skipped":
                        return "该步骤被配置跳过（未生成 style_profile.json）。"
                    if step == "profile_style_failed" and isinstance(payload, dict):
                        err = str(payload.get("error") or payload.get("reason") or "").strip()
                        if err:
                            return f"风格规范生成失败：{_truncate_message(err)}"
                        return "风格规范生成失败，流程按容错策略继续执行。"

    log_dir = _latest_init_long_log_dir(project_dir)
    if log_dir is None:
        return None

    errors_path = log_dir / "errors.jsonl"
    if errors_path.exists():
        last_style_error: str | None = None
        last_structure_error: str | None = None
        for row in _iter_jsonl(errors_path):
            if row.get("event") != "api_call_error":
                continue
            data = row.get("data")
            if not isinstance(data, dict):
                continue
            task_name = str(data.get("task") or "").strip()
            if task_name not in {"profile_style", "profile_structure"}:
                continue
            error = data.get("error")
            if not isinstance(error, dict):
                continue
            provider_msg = str(
                error.get("provider_error_message") or error.get("message") or ""
            ).strip()
            if provider_msg:
                if task_name == "profile_style":
                    last_style_error = provider_msg
                elif task_name == "profile_structure":
                    last_structure_error = provider_msg
        if last_style_error and last_structure_error:
            return (
                "模型调用失败："
                f"style={_truncate_message(last_style_error, 100)}；"
                f"structure={_truncate_message(last_structure_error, 100)}"
            )
        if last_structure_error:
            return f"模型调用失败（结构分支）：{_truncate_message(last_structure_error)}"
        if last_style_error:
            return f"模型调用失败（风格分支）：{_truncate_message(last_style_error)}"

    pylog_path = log_dir / "python.log"
    if pylog_path.exists():
        try:
            for line in reversed(pylog_path.read_text(encoding="utf-8").splitlines()):
                marker = "风格规范生成失败（不影响后续流程）："
                if marker in line:
                    detail = line.split(marker, 1)[-1].strip()
                    if detail:
                        return f"风格规范生成失败：{_truncate_message(detail)}"
                    return "风格规范生成失败，流程按容错策略继续执行。"
        except OSError:
            pass
    return None


def _build_empty_artifact_hint(kind: str, step_key: str, project_dir: Path) -> str | None:
    key = (step_key or "").strip()
    if kind == "init_long" and key.startswith("profile_style"):
        reason = _extract_profile_style_failure_reason(project_dir)
        if reason:
            return (
                "未找到 `style_profile.json`。\n"
                f"{reason}\n"
                "建议修复模型路由/权限后，重新执行立项（或仅重跑风格规范步骤）。"
            )
        return (
            "未找到 `style_profile.json`。\n该步骤可能被跳过，或在生成失败后按容错策略继续了流程。"
        )
    return None


def _relative_artifact_path(path: Path, project_dir: Path | None) -> str:
    if project_dir is not None:
        try:
            return path.relative_to(project_dir).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _format_candidate_summary(
    candidates: list[tuple[str, Path]] | None,
    project_dir: Path | None,
    *,
    limit: int = 12,
) -> str:
    if not candidates:
        return ""
    unique: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, path in candidates:
        rel = _relative_artifact_path(path, project_dir)
        key = (label, rel)
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, rel))
    rows = [f"- {label} · {rel}" for label, rel in unique[:limit]]
    remaining = max(0, len(unique) - limit)
    if remaining > 0:
        rows.append(f"- 另有 {remaining} 个候选路径")
    return "\n".join(rows)


class StepArtifactDialog(QDialog):
    """Modal dialog to view the content of files produced by a pipeline step."""

    _DIALOG_QSS = """
    QLabel#artifactDialogTitle {
        color: {{text.heading}};
        font-family: "Songti SC", "STSong", "Georgia";
        font-size: 17px;
        font-weight: 700;
    }
    QLabel#artifactDialogSubtitle {
        color: {{text.muted}};
        font-size: 12px;
    }
    QTextBrowser#artifactEmptyMessage {
        background: rgba({{bg.input}}, 0.72);
        border: 1px solid rgba({{border.default}}, 0.16);
        border-radius: 10px;
        color: {{text.muted}};
        font-size: 13px;
        padding: 18px;
    }
    QLabel#artifactFileLabel {
        color: {{text.body.alt}};
        font-size: 11px;
        font-weight: 600;
        padding: 2px 0;
    }
    QTextEdit#artifactContent {
        background: rgba({{bg.input}}, 0.92);
        border: 1px solid rgba({{border.default}}, 0.18);
        border-radius: 8px;
        color: {{text.primary}};
        font-family: "SF Mono", "Menlo", "Consolas", "Courier New";
        font-size: 12px;
        padding: 12px;
        selection-background-color: rgba({{accent.primary}}, 0.18);
    }
    QTabWidget::pane {
        background: transparent;
        border: none;
        padding-top: 4px;
    }
    QTabBar::tab {
        background: rgba({{bg.control.hover}}, 0.4);
        border: 1px solid rgba({{border.default}}, 0.15);
        border-bottom: none;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        color: {{text.body.alt}};
        font-size: 12px;
        font-weight: 600;
        min-width: 80px;
        padding: 6px 14px;
    }
    QTabBar::tab:selected {
        background: rgba({{bg.input}}, 0.92);
        border-color: rgba({{border.default}}, 0.22);
        color: {{text.heading}};
    }
    QTabBar::tab:hover:!selected {
        background: rgba({{bg.control.hover}}, 0.6);
    }
    QTabWidget#artifactTabs {
        background: transparent;
        border: none;
    }
    QTabWidget#artifactTabs::pane {
        background: transparent;
        border: none;
        padding-top: 4px;
    }
    QTabWidget#artifactTabs::tab-bar {
        background: transparent;
        left: 4px;
    }
    QTabWidget#artifactTabs QTabBar {
        background: transparent;
        border: none;
    }
    QTabWidget#artifactTabs QTabBar::tab {
        font-size: 11px;
        margin-right: 1px;
        min-width: 0px;
        padding: 5px 9px;
    }
    QTabWidget#artifactTabs > QStackedWidget {
        background: transparent;
        border: none;
    }
    QFrame#artifactSeparator {
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:0,
            stop:0 transparent,
            stop:0.15 rgba({{border.warm}}, 0.28),
            stop:0.85 rgba({{border.warm}}, 0.28),
            stop:1 transparent
        );
        border: none;
    }
    """

    def __init__(
        self,
        step_label: str,
        artifacts: list[tuple[str, Path]],
        *,
        candidate_paths: list[tuple[str, Path]] | None = None,
        empty_hint: str | None = None,
        project_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("appDialog")
        self.setWindowTitle(f"{step_label} · 产出文件")
        self.setMinimumSize(980, 560)
        self.resize(*smart_dialog_size(self, 1240, 720))
        self.setModal(True)
        self.setStyleSheet(render_document_css(self._DIALOG_QSS))
        self._deep_link_target: str | None = None
        self._project_dir = project_dir

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(10)

        title = QLabel("产出文件")
        title.setObjectName("artifactDialogTitle")
        layout.addWidget(title)

        sep = QFrame()
        sep.setObjectName("artifactSeparator")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        artifact_count = len(artifacts)
        subtitle_text = (
            f"{step_label} · 暂无可查看文件"
            if artifact_count == 0
            else f"{step_label} · 共 {artifact_count} 个产出文件"
        )
        if artifact_count > 1:
            subtitle_text += " · 点击标签切换查看"
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("artifactDialogSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        if not artifacts:
            message = "该步骤尚无可查看的产出文件。\n任务完成后此处将展示相关内容。"
            if empty_hint:
                message = f"{message}\n\n{empty_hint}"
            candidate_summary = _format_candidate_summary(candidate_paths, project_dir)
            if candidate_summary:
                message = f"{message}\n\n已检查候选产物：\n{candidate_summary}"
            empty = QTextBrowser()
            empty.setObjectName("artifactEmptyMessage")
            empty.setReadOnly(True)
            empty.setOpenExternalLinks(False)
            empty.setPlainText(message)
            layout.addWidget(empty, 1)
        elif len(artifacts) == 1:
            label_text, file_path = artifacts[0]
            layout.addWidget(self._make_artifact_panel(label_text, file_path), 1)
        else:
            tabs = QTabWidget()
            tabs.setObjectName("artifactTabs")
            tabs.setDocumentMode(True)
            configure_tab_widget_density(tabs, "content")
            tabs.setAutoFillBackground(False)
            tabs.tabBar().setAutoFillBackground(False)
            for label_text, file_path in artifacts:
                panel = self._make_artifact_panel(label_text, file_path)
                index = tabs.addTab(panel, label_text)
                tabs.setTabToolTip(index, _relative_artifact_path(file_path, self._project_dir))
            layout.addWidget(tabs, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("actionButton")
        close_btn.setProperty("variant", "secondary")
        close_btn.setProperty("compact", True)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def refresh_theme_colors(self) -> None:
        """Re-resolve this dialog's local QSS after a desktop theme switch."""
        self.setStyleSheet(render_document_css(self._DIALOG_QSS))

    def _make_artifact_panel(self, label_text: str, file_path: Path) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(8)

        rel = _relative_artifact_path(file_path, self._project_dir)
        file_info = QLabel(f"文件：{rel}")
        file_info.setObjectName("artifactFileLabel")
        file_info.setWordWrap(True)
        file_info.setToolTip(label_text)
        panel_layout.addWidget(file_info)

        widget = smart_render_document(file_path)
        if widget is not None:
            self._wire_deep_links(widget)
            panel_layout.addWidget(widget, 1)
        else:
            panel_layout.addWidget(self._make_content_view(file_path), 1)
        return panel

    def _wire_deep_links(self, widget: QWidget | None) -> None:
        """If *widget* is a QTextBrowser with deep links, connect its signal."""
        if (
            widget is not None
            and isinstance(widget, QTextBrowser)
            and widget.property("has_deep_links")
        ):
            connect_deep_links(widget, self._handle_deep_link)

    def _handle_deep_link(self, url: object) -> None:
        """Handle a deep-link anchor click: store target and close dialog."""
        url_str = url.toString() if hasattr(url, "toString") else str(url)
        info = parse_deep_link(url_str)
        if info is None:
            return
        if info.get("type") == "chapter":
            project_id = str(info["project_id"])
            chapter_number = int(info["chapter_number"])
            self._deep_link_target = f"chapter_studio:{project_id}:{chapter_number}"
            self.accept()

    @staticmethod
    def _make_content_view(file_path: Path) -> QTextEdit:
        view = QTextEdit()
        view.setObjectName("artifactContent")
        view.setReadOnly(True)
        view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        try:
            raw = smart_artifact_content(file_path)
            view.setPlainText(raw)
        except (OSError, UnicodeDecodeError):
            view.setPlainText(f"无法以文本方式读取文件：{file_path}")
        return view

    @classmethod
    def show_for_step(
        cls,
        *,
        kind: str,
        step_key: str,
        step_label: str,
        project_dir: Path | None,
        chapter_number: int = 0,
        parent: QWidget | None = None,
    ) -> str | None:
        """Show the artifact dialog. Returns a navigation target if a deep link was clicked."""
        if project_dir is None or not project_dir.is_dir():
            show_info_message(parent, "暂无数据", "项目目录尚不可用，请在任务完成后查看。")
            return None
        artifacts = _resolve_artifacts(kind, step_key, project_dir, chapter_number)
        candidate_paths = _artifact_candidates(kind, step_key, project_dir, chapter_number)
        empty_hint = (
            _build_empty_artifact_hint(kind, step_key, project_dir) if not artifacts else None
        )
        dialog = cls(
            step_label,
            artifacts,
            candidate_paths=candidate_paths,
            empty_hint=empty_hint,
            project_dir=project_dir,
            parent=parent,
        )
        dialog.exec()
        return dialog._deep_link_target
