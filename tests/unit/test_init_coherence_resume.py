"""Regression tests for init coherence report resume."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.schemas.init_coherence import CoherenceClaim
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.init.init_coherence import (
    BLUEPRINT_ARTIFACT,
    init_coherence_issue_id,
)
from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
    CLAIM_LEDGER_JSON,
    CLAIM_LEDGER_STAGE_SCHEMA_VERSION,
    _persist_claims,
    init_coherence_artifact_hashes,
    load_reusable_init_coherence_profile,
    run_init_coherence_v2_gate,
    semantic_compiler_fingerprint,
    semantic_compiler_runtime_fingerprint,
)
from novel_forge.pipeline.long.services.init.init_service import (
    _init_coherence_repair_round_start,
    _load_reusable_init_coherence_report,
    _save_init_readiness,
)


def _coherence_claim(**overrides: Any) -> dict[str, Any]:
    return {
        "cognitive_subjects": ["主角A"],
        "cognitive_object": "关键线索",
        "cognitive_level": "confirmed",
        "action_level": "internal",
        "reader_awareness": "partial",
        "character_knowledge_coverage": {"主角A": "partial"},
        "cognitive_chapter": 1,
        "public_reveal_chapter": None,
        "foreshadow_chapters": [1],
        **overrides,
    }


def _coherence_claim_model_validate(payload: dict[str, Any]) -> CoherenceClaim:
    return CoherenceClaim.model_validate(_coherence_claim(**payload))


def _ctx(tmp_path: Any) -> tuple[SimpleNamespace, ProjectLayout, list[tuple[str, Any]]]:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("init_resume"))
    layout.ensure_dirs()
    events: list[tuple[str, Any]] = []
    ctx = SimpleNamespace(
        storage=storage,
        layout=layout,
        settings=SimpleNamespace(init_coherence_block_min_severity="high"),
        on_step=lambda step, data: events.append((step, data)),
    )
    return ctx, layout, events


def _complete_claim_coverage() -> dict[str, Any]:
    return {
        "status": "complete",
        "complete": True,
        "batch_count": 1,
        "records": [],
        "unprocessed_source_refs": [],
    }


@pytest.mark.asyncio
async def test_reuses_stage_claim_ledger_when_artifacts_match(tmp_path) -> None:
    ctx, layout, events = _ctx(tmp_path)
    ctx.settings.init_coherence_use_memory = False
    ctx.settings.init_coherence_semantic_top_k = 0
    ctx.router = SimpleNamespace()

    async def fail_call_with_retry(*_args, **_kwargs):
        raise AssertionError("claims should be reused from ledger")

    ctx.call_with_retry = fail_call_with_retry
    artifacts = {BLUEPRINT_ARTIFACT: {"synopsis": "银杏叶牵出旧约。"}}
    ctx._init_coherence_compiler_fingerprint = semantic_compiler_fingerprint(
        ctx,
        profile={"summary": "same"},
        artifacts=artifacts,
    )
    ctx._init_coherence_compiler_runtime_fingerprint = (
        semantic_compiler_runtime_fingerprint(ctx)
    )
    ctx._init_coherence_claim_coverage = {
        "blueprint_coherence": [
            {
                "coverage_status": "complete",
                "unprocessed_source_refs": [],
            }
        ]
    }
    claim = _coherence_claim_model_validate(
        {
            "claim_id": "blueprint_claim_001",
            "artifact": BLUEPRINT_ARTIFACT,
            "source_path": "/synopsis",
            "source_field": "synopsis",
            "claim_text": "银杏叶牵出旧约。",
            "evidence": "银杏叶牵出旧约。",
        }
    )
    _persist_claims(
        ctx,
        [claim],
        stage="blueprint_coherence",
        artifacts=artifacts,
        focus_chapters=None,
    )

    report = await run_init_coherence_v2_gate(
        ctx,
        stage="blueprint_coherence",
        repair_artifact=BLUEPRINT_ARTIFACT,
        profile={"summary": "same"},
        artifacts=artifacts,
    )

    assert report["claims_count"] == 1
    assert report["verdict"] == "accept"
    assert any(
        step == "extract_init_coherence_claims" and payload.get("source") == "claim_ledger"
        for step, payload in events
    )
    assert (layout.memory_dir / "init_coherence_claims.jsonl").exists()


@pytest.mark.asyncio
async def test_reextracts_legacy_stage_claim_ledger_when_artifact_hash_mismatches(
    tmp_path,
) -> None:
    ctx, layout, events = _ctx(tmp_path)
    ctx.settings.init_coherence_use_memory = False
    ctx.settings.init_coherence_semantic_top_k = 0
    ctx.router = SimpleNamespace()

    async def call_with_retry(*_args, **_kwargs):
        return {
            "claims": [
                _coherence_claim(
                    claim_id="blueprint_claim_new_001",
                    artifact=BLUEPRINT_ARTIFACT,
                    source_path="/synopsis",
                    source_field="synopsis",
                    claim_text="银杏叶牵出旧约。",
                    evidence="银杏叶牵出旧约。",
                    chapter_numbers=[1],
                )
            ],
            "coverage_status": "complete",
            "unprocessed_source_refs": [],
        }

    ctx.call_with_retry = call_with_retry
    blueprint_payload = {"synopsis": "银杏叶牵出旧约。", "created_at": "2026-05-24T01:00:00Z"}
    ctx.storage.save_json(layout.blueprint_path, blueprint_payload)
    legacy_hash = "legacy-timestamp-sensitive-hash"
    claim = _coherence_claim_model_validate(
        {
            "claim_id": "blueprint_claim_legacy_001",
            "artifact": BLUEPRINT_ARTIFACT,
            "source_path": "/synopsis",
            "source_field": "synopsis",
            "claim_text": "银杏叶牵出旧约。",
            "evidence": "银杏叶牵出旧约。",
        }
    )
    claim_entry = {
        **claim.model_dump(mode="json"),
        "status": "active",
        "stage": "blueprint_coherence",
        "stream_finalized_stage": "blueprint_coherence",
        "artifact_hash": legacy_hash,
    }
    ctx.storage.save_json(
        layout.memory_dir / CLAIM_LEDGER_JSON,
        {
            "stages": {
                "blueprint_coherence": {
                    "schema_version": CLAIM_LEDGER_STAGE_SCHEMA_VERSION,
                    "claim_count": 1,
                    "claim_ids": ["blueprint_claim_legacy_001"],
                    "artifact_hashes": {BLUEPRINT_ARTIFACT: legacy_hash},
                    "focus_chapters": [],
                }
            },
            "active_claim_ids": ["blueprint_claim_legacy_001"],
            "claims_by_id": {"blueprint_claim_legacy_001": claim_entry},
        },
    )

    report = await run_init_coherence_v2_gate(
        ctx,
        stage="blueprint_coherence",
        repair_artifact=BLUEPRINT_ARTIFACT,
        profile={"summary": "same"},
        artifacts={BLUEPRINT_ARTIFACT: blueprint_payload},
    )

    rewritten = ctx.storage.load_json(layout.memory_dir / CLAIM_LEDGER_JSON)
    expected_hash = init_coherence_artifact_hashes({BLUEPRINT_ARTIFACT: blueprint_payload})[
        BLUEPRINT_ARTIFACT
    ]

    assert report["claims_count"] == 1
    assert report["verdict"] == "accept"
    assert rewritten["stages"]["blueprint_coherence"]["artifact_hashes"] == {
        BLUEPRINT_ARTIFACT: expected_hash
    }
    stage_claim_ids = rewritten["stages"]["blueprint_coherence"]["claim_ids"]
    assert "blueprint_claim_legacy_001" not in stage_claim_ids
    assert any(claim_id.endswith("blueprint_claim_new_001") for claim_id in stage_claim_ids)
    assert rewritten["claims_by_id"]["blueprint_claim_legacy_001"]["status"] == "invalidated"
    assert all(
        rewritten["claims_by_id"][claim_id]["artifact_hash"] == expected_hash
        for claim_id in stage_claim_ids
    )
    assert not any(
        step == "extract_init_coherence_claims" and payload.get("source") == "claim_ledger"
        for step, payload in events
    )


def _save_accepted_blueprint_report(
    ctx: SimpleNamespace,
    layout: ProjectLayout,
    *,
    blueprint_payload: dict[str, Any],
    focus_chapters: list[int] | None = None,
) -> None:
    compiler_runtime_fingerprint = semantic_compiler_runtime_fingerprint(ctx)
    ctx.storage.save_json(
        layout.reports_dir / "blueprint_coherence.json",
        {
            "schema_version": "audit_v2",
            "stage": "blueprint_coherence",
            "artifact": BLUEPRINT_ARTIFACT,
            "verdict": "accept",
            "issues": [],
            "blocked": False,
            "summary": "已通过。",
            "claims_count": 3,
            "extracted_claims_count": 3,
            "compiler_runtime_fingerprint": compiler_runtime_fingerprint,
        },
    )
    ctx.storage.save_json(
        layout.memory_dir / CLAIM_LEDGER_JSON,
        {
            "stages": {
                "blueprint_coherence": {
                    "schema_version": CLAIM_LEDGER_STAGE_SCHEMA_VERSION,
                    "claim_count": 3,
                    "artifact_hashes": init_coherence_artifact_hashes(
                        {BLUEPRINT_ARTIFACT: blueprint_payload}
                    ),
                    "focus_chapters": focus_chapters or [],
                    "compiler_runtime_fingerprint": compiler_runtime_fingerprint,
                    "claim_coverage": _complete_claim_coverage(),
                }
            }
        },
    )


def test_semantic_compiler_fingerprint_binds_sources_profile_and_runtime(tmp_path) -> None:
    ctx, _layout, _events = _ctx(tmp_path)
    ctx.router = SimpleNamespace()
    artifacts = {BLUEPRINT_ARTIFACT: {"synopsis": "银杏叶牵出旧约。"}}
    baseline = semantic_compiler_fingerprint(
        ctx,
        profile={"summary": "原始画像"},
        artifacts=artifacts,
    )

    changed_source = semantic_compiler_fingerprint(
        ctx,
        profile={"summary": "原始画像"},
        artifacts={BLUEPRINT_ARTIFACT: {"synopsis": "来源已变更。"}},
    )
    changed_profile = semantic_compiler_fingerprint(
        ctx,
        profile={"summary": "画像已变更"},
        artifacts=artifacts,
    )
    ctx.settings.temp_adjudicate_init_conflict_candidates = 0.2
    changed_runtime = semantic_compiler_fingerprint(
        ctx,
        profile={"summary": "原始画像"},
        artifacts=artifacts,
    )

    assert len({baseline, changed_source, changed_profile, changed_runtime}) == 4


def test_reuses_accepted_blueprint_coherence_report_when_artifact_hash_matches(tmp_path) -> None:
    ctx, layout, events = _ctx(tmp_path)
    blueprint_payload = {"synopsis": "银杏叶牵出旧约。"}
    _save_accepted_blueprint_report(ctx, layout, blueprint_payload=blueprint_payload)

    report = _load_reusable_init_coherence_report(
        ctx,
        stage="blueprint_coherence",
        artifact=BLUEPRINT_ARTIFACT,
        artifacts={BLUEPRINT_ARTIFACT: blueprint_payload},
    )

    assert report is not None
    assert report["verdict"] == "accept"
    assert report["artifact_hashes"] == init_coherence_artifact_hashes(
        {BLUEPRINT_ARTIFACT: blueprint_payload}
    )
    assert events == [
        (
            "init_coherence_report_resumed",
            {
                "verdict": "accept",
                "summary": "已通过。",
                "blocked": False,
                "issue_count": 0,
                "high_or_critical": 0,
                "stage": "blueprint_coherence",
                "artifact": BLUEPRINT_ARTIFACT,
                "source": "reports/blueprint_coherence.json",
            },
        )
    ]


def test_rejects_reusable_report_when_claim_ledger_stage_missing(tmp_path) -> None:
    ctx, layout, events = _ctx(tmp_path)
    blueprint_payload = {"synopsis": "银杏叶牵出旧约。"}
    expected_hashes = init_coherence_artifact_hashes({BLUEPRINT_ARTIFACT: blueprint_payload})
    ctx.storage.save_json(
        layout.reports_dir / "blueprint_coherence.json",
        {
            "schema_version": "audit_v2",
            "stage": "blueprint_coherence",
            "artifact": BLUEPRINT_ARTIFACT,
            "verdict": "accept",
            "issues": [],
            "blocked": False,
            "summary": "已通过。",
            "claims_count": 3,
            "extracted_claims_count": 3,
            "artifact_hashes": expected_hashes,
        },
    )

    report = _load_reusable_init_coherence_report(
        ctx,
        stage="blueprint_coherence",
        artifact=BLUEPRINT_ARTIFACT,
        artifacts={BLUEPRINT_ARTIFACT: blueprint_payload},
    )

    assert report is None
    assert events == []
    decisions = ctx.storage.load_json(layout.reports_dir / "init_resume_decisions.json")
    assert decisions["decisions"][-1]["action"] == "cache_rejected"
    assert decisions["decisions"][-1]["reason"] == "missing_claim_ledger_stage"


def test_init_readiness_links_resume_decisions_report(tmp_path) -> None:
    ctx, layout, _events = _ctx(tmp_path)
    ctx.storage.save_json(
        layout.reports_dir / "init_resume_decisions.json",
        {
            "schema_version": 1,
            "decisions": [
                {
                    "stage": "contract_coherence",
                    "artifact": "chapter_contracts",
                    "action": "cache_rejected",
                    "reason": "missing_claim_ledger_stage",
                    "path": "",
                    "timestamp": "2026-06-14T00:00:00+00:00",
                }
            ],
        },
    )

    readiness = _save_init_readiness(ctx, reports={}, repairs=[])

    assert readiness["resume_decisions_path"] == "reports/init_resume_decisions.json"
    persisted = ctx.storage.load_json(layout.reports_dir / "init_readiness.json")
    assert persisted["resume_decisions_path"] == "reports/init_resume_decisions.json"


def test_reuses_blocked_blueprint_coherence_report_when_artifact_hash_matches(tmp_path) -> None:
    ctx, layout, events = _ctx(tmp_path)
    blueprint_payload = {"synopsis": "银杏叶牵出旧约。"}
    compiler_runtime_fingerprint = semantic_compiler_runtime_fingerprint(ctx)
    ctx.storage.save_json(
        layout.reports_dir / "blueprint_coherence.json",
        {
            "schema_version": "audit_v2",
            "stage": "blueprint_coherence",
            "artifact": BLUEPRINT_ARTIFACT,
            "verdict": "needs_repair",
            "issues": [
                {
                    "severity": "high",
                    "description": "关键线索兑现章节早于铺垫。",
                }
            ],
            "blocked": True,
            "summary": "仍需修复。",
            "claims_count": 3,
            "extracted_claims_count": 3,
            "compiler_runtime_fingerprint": compiler_runtime_fingerprint,
        },
    )
    ctx.storage.save_json(
        layout.memory_dir / CLAIM_LEDGER_JSON,
        {
            "stages": {
                "blueprint_coherence": {
                    "schema_version": CLAIM_LEDGER_STAGE_SCHEMA_VERSION,
                    "claim_count": 3,
                    "artifact_hashes": init_coherence_artifact_hashes(
                        {BLUEPRINT_ARTIFACT: blueprint_payload}
                    ),
                    "focus_chapters": [],
                    "compiler_runtime_fingerprint": compiler_runtime_fingerprint,
                    "claim_coverage": _complete_claim_coverage(),
                }
            }
        },
    )

    report = _load_reusable_init_coherence_report(
        ctx,
        stage="blueprint_coherence",
        artifact=BLUEPRINT_ARTIFACT,
        artifacts={BLUEPRINT_ARTIFACT: blueprint_payload},
    )

    assert report is not None
    assert report["verdict"] == "needs_repair"
    assert report["blocked"] is True
    assert events == [
        (
            "init_coherence_report_resumed",
            {
                "verdict": "needs_repair",
                "summary": "仍需修复。",
                "blocked": True,
                "issue_count": 1,
                "high_or_critical": 1,
                "stage": "blueprint_coherence",
                "artifact": BLUEPRINT_ARTIFACT,
                "source": "reports/blueprint_coherence.json",
            },
        )
    ]


def test_init_coherence_artifact_hash_ignores_schema_metadata() -> None:
    left = {
        "synopsis": "同一份蓝图。",
        "schema_version": "2.0",
        "created_at": "2026-05-24T01:00:00Z",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "起点",
                "schema_version": "2.0",
                "created_at": "2026-05-24T01:00:01Z",
            }
        ],
    }
    right = {
        "synopsis": "同一份蓝图。",
        "schema_version": "2.1",
        "created_at": "2026-05-25T01:00:00Z",
        "chapters": [
            {
                "chapter_number": 1,
                "title": "起点",
                "schema_version": "2.1",
                "created_at": "2026-05-25T01:00:01Z",
            }
        ],
    }

    assert init_coherence_artifact_hashes({BLUEPRINT_ARTIFACT: left}) == (
        init_coherence_artifact_hashes({BLUEPRINT_ARTIFACT: right})
    )


def test_repair_round_resume_counts_only_matching_current_issues() -> None:
    settings = SimpleNamespace(init_coherence_block_min_severity="high")
    current_issue = {
        "severity": "high",
        "description": "关键线索兑现章节早于铺垫。",
        "artifact": BLUEPRINT_ARTIFACT,
    }
    other_issue = {
        "severity": "high",
        "description": "另一个无关阻断。",
        "artifact": BLUEPRINT_ARTIFACT,
    }
    report = {
        "artifact": BLUEPRINT_ARTIFACT,
        "issues": [current_issue],
        "blocked": True,
        "verdict": "needs_repair",
    }
    repairs = [
        {
            "artifact": BLUEPRINT_ARTIFACT,
            "round": 2,
            "source_issue_ids": [init_coherence_issue_id(current_issue)],
        },
        {
            "artifact": BLUEPRINT_ARTIFACT,
            "round": 3,
            "source_issue_ids": [init_coherence_issue_id(other_issue)],
        },
    ]

    assert (
        _init_coherence_repair_round_start(
            settings,
            repairs,
            artifact=BLUEPRINT_ARTIFACT,
            report=report,
        )
        == 2
    )


def test_rejects_pre_audit_v2_hash_report_even_when_report_is_newer(tmp_path) -> None:
    ctx, layout, events = _ctx(tmp_path)
    blueprint_payload = {"synopsis": "当前蓝图。", "created_at": "2026-05-24T01:00:00Z"}
    expected_hashes = init_coherence_artifact_hashes({BLUEPRINT_ARTIFACT: blueprint_payload})
    ctx.storage.save_json(layout.blueprint_path, blueprint_payload)
    ctx.storage.save_json(
        layout.reports_dir / "blueprint_coherence.json",
        {
            "stage": "blueprint_coherence",
            "artifact": BLUEPRINT_ARTIFACT,
            "verdict": "accept",
            "issues": [],
            "blocked": False,
            "summary": "旧版 hash 报告已通过。",
            "claims_count": 3,
            "extracted_claims_count": 3,
            "artifact_hashes": expected_hashes,
        },
    )
    ctx.storage.save_json(
        layout.memory_dir / CLAIM_LEDGER_JSON,
        {
            "stages": {
                "blueprint_coherence": {
                    "claim_count": 3,
                    "artifact_hashes": expected_hashes,
                    "focus_chapters": [],
                }
            }
        },
    )

    report = _load_reusable_init_coherence_report(
        ctx,
        stage="blueprint_coherence",
        artifact=BLUEPRINT_ARTIFACT,
        artifacts={BLUEPRINT_ARTIFACT: blueprint_payload},
    )

    assert report is None
    assert events == []
    decisions = ctx.storage.load_json(layout.reports_dir / "init_resume_decisions.json")
    assert decisions["decisions"][-1]["reason"] == "stale_pre_audit_v2_report"


def test_ignores_accepted_blueprint_coherence_report_when_artifact_hash_changed(tmp_path) -> None:
    ctx, layout, events = _ctx(tmp_path)
    _save_accepted_blueprint_report(
        ctx,
        layout,
        blueprint_payload={"synopsis": "旧蓝图。"},
    )

    report = _load_reusable_init_coherence_report(
        ctx,
        stage="blueprint_coherence",
        artifact=BLUEPRINT_ARTIFACT,
        artifacts={BLUEPRINT_ARTIFACT: {"synopsis": "新蓝图。"}},
    )

    assert report is None
    assert events == []


def test_ignores_accepted_blueprint_coherence_report_from_focused_recheck(tmp_path) -> None:
    ctx, layout, events = _ctx(tmp_path)
    blueprint_payload = {"synopsis": "局部重检后的蓝图。"}
    _save_accepted_blueprint_report(
        ctx,
        layout,
        blueprint_payload=blueprint_payload,
        focus_chapters=[12, 13],
    )

    report = _load_reusable_init_coherence_report(
        ctx,
        stage="blueprint_coherence",
        artifact=BLUEPRINT_ARTIFACT,
        artifacts={BLUEPRINT_ARTIFACT: blueprint_payload},
    )

    assert report is None
    assert events == []


def test_reuses_current_accepted_report_when_artifact_is_not_newer(tmp_path) -> None:
    ctx, layout, events = _ctx(tmp_path)
    blueprint_payload = {"synopsis": "当前蓝图。"}
    ctx.storage.save_json(layout.blueprint_path, blueprint_payload)
    _save_accepted_blueprint_report(
        ctx,
        layout,
        blueprint_payload=blueprint_payload,
    )

    report = _load_reusable_init_coherence_report(
        ctx,
        stage="blueprint_coherence",
        artifact=BLUEPRINT_ARTIFACT,
        artifacts={BLUEPRINT_ARTIFACT: blueprint_payload},
    )

    assert report is not None
    assert report["verdict"] == "accept"
    assert events[-1][0] == "init_coherence_report_resumed"


def test_reuses_profile_with_object_marker_lists(tmp_path) -> None:
    ctx, layout, events = _ctx(tmp_path)
    ctx.storage.save_json(
        layout.reports_dir / "init_coherence_profile.json",
        {
            "genre_tags": ["现代言情"],
            "project_ontology": {
                "payoff_types": [
                    {"type": "information", "trigger": "日记曝光"},
                    {"type": "relationship"},
                ],
                "irreversible_event_markers": [
                    {"marker": "公开", "trigger": "主动坦白"},
                ],
                "temporal_markers": [
                    {"marker": "回忆", "usage": "物证链"},
                ],
            },
            "extraction_guidance": ["抽取状态变化"],
            "summary": "可续用画像。",
            "refined_from_blueprint": True,
        },
    )

    profile = load_reusable_init_coherence_profile(ctx, require_refined=True)

    assert profile is not None
    assert profile["project_ontology"]["payoff_types"] == ["information", "relationship"]
    assert profile["project_ontology"]["irreversible_event_markers"] == ["公开"]
    assert profile["project_ontology"]["temporal_markers"] == ["回忆"]
    assert profile["refined_from_blueprint"] is True
    assert events[-1][0] == "init_coherence_profile_resumed"


def test_rejects_profile_that_serialized_rule_objects_into_strings(tmp_path) -> None:
    ctx, layout, _events = _ctx(tmp_path)
    ctx.storage.save_json(
        layout.reports_dir / "init_coherence_profile.json",
        {
            "genre_tags": ["悬疑"],
            "project_ontology": {"state_axes": ["trust"]},
            "conflict_lens": [
                '{"conflict_type":"信任危机","protagonist_side":{"character":"旧草稿名"}}'
            ],
            "extraction_guidance": ["抽取状态变化"],
            "summary": "旧画像。",
            "refined_from_blueprint": True,
        },
    )

    assert load_reusable_init_coherence_profile(ctx, require_refined=True) is None
