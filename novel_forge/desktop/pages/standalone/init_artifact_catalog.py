"""Shared catalog for long-initialization artifacts shown in document views."""

from __future__ import annotations

from typing import Final

INIT_ARTIFACT_TITLES: Final[dict[str, str]] = {
    "character_system.json": "角色系统",
    "character_relationship_matrix.json": "角色关系矩阵",
    "entity_graph.json": "实体图谱",
    "entity_registry.json": "实体注册表",
    "narrative_contract.json": "叙事契约",
    "chapter_contracts.json": "章节契约",
    "creative_director_packet.json": "创作导演包",
    "chapter_design_matrix.json": "章节设计矩阵",
    "narrative_blueprint_fragments.json": "叙事蓝图分块",
    "subplot_execution_matrix.json": "支线执行矩阵",
    "subplot_weave_validation.json": "支线闭环校验",
    "init_editorial_readiness.json": "编辑契约准入",
    "init_artifact_repair.json": "初始化修复报告",
    "init_creative_refinement.json": "初始化创意精炼",
    "init_readiness.json": "初始化准入",
    "story_state_projection.json": "叙事状态投影",
    "artifact_manifest.json": "产物清单",
    "outline_session.json": "章节大纲断点",
}

CHARACTER_THEME_ARTIFACTS: Final[tuple[tuple[str, str], ...]] = (
    ("角色声纹", "plans/editorial_contract.json"),
    ("编辑契约准入", "reports/init_editorial_readiness.json"),
    ("角色系统", "states/init_v2/character_system.json"),
    ("角色关系矩阵", "states/init_v2/character_relationship_matrix.json"),
    ("实体图谱", "narrative_state/entity_graph.json"),
    ("实体注册表", "narrative_state/entity_registry.json"),
)

BLUEPRINT_THEME_ARTIFACTS: Final[tuple[tuple[str, str], ...]] = (
    ("叙事契约", "plans/narrative_contract.json"),
    ("章节契约", "plans/chapter_contracts.json"),
    ("创作导演包", "plans/creative_director_packet.json"),
    ("蓝图分块", "plans/narrative_blueprint_fragments.json"),
    ("章节设计矩阵", "plans/chapter_design_matrix.json"),
    ("支线闭环校验", "reports/subplot_weave_validation.json"),
    ("叙事状态", "narrative_state/story_state_projection.json"),
)

INIT_COHERENCE_THEME_ARTIFACTS: Final[tuple[tuple[str, str], ...]] = (
    ("一致性画像", "reports/init_coherence_profile.json"),
    ("一致性 Claims", "memory/init_coherence_claims.jsonl"),
    ("一致性 Claims 账本", "memory/init_coherence_claim_ledger.json"),
    ("一致性索引", "memory/init_coherence_index.json"),
    ("冲突候选", "reports/init_conflict_candidates.json"),
    ("冲突裁决", "reports/init_conflict_adjudication.json"),
    ("一致性 Claims 契约覆盖", "reports/init_claim_contract_coverage.json"),
    ("初始化修复", "reports/init_artifact_repair.json"),
    ("初始化准入", "reports/init_readiness.json"),
)
