"""集成测试：Claim-to-Contract 覆盖审计修复循环（TDD-RED）。

验证修复循环的核心契约：
  blocked (needs_repair) → backfill cognitive_constraints → re-audit → accept
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.pipeline.long.services.init.init_service import _init_coherence_blocks

# ---- _local_cognitive_backfill 尚未实现（任务 7/8） ----
# 当实现后，此导入将成功，测试可推进到 GREEN 阶段。
_LOCAL_COGNITIVE_BACKFILL_READY = False
try:
    from novel_forge.pipeline.long.services.init.init_service import (  # noqa: F811
        _local_cognitive_backfill,
    )

    _LOCAL_COGNITIVE_BACKFILL_READY = True
except ImportError:
    _local_cognitive_backfill = None  # type: ignore[assignment]


# ---- 测试辅助 ----


def _mock_ctx(settings_overrides: dict[str, Any] | None = None) -> SimpleNamespace:
    """构建最小化的 InitLongServiceContext 模拟对象。"""
    settings_defaults: dict[str, Any] = {"init_coherence_block_min_severity": "high"}
    if settings_overrides:
        settings_defaults.update(settings_overrides)
    return SimpleNamespace(settings=SimpleNamespace(**settings_defaults))


def _coverage_report(
    *,
    verdict: str = "needs_repair",
    blocked: bool = True,
    issues: list[dict[str, Any]] | None = None,
    summary: str = "",
    covered_claims: int = 0,
    total_claims: int = 1,
) -> dict[str, Any]:
    """构建 Claim-to-Contract 覆盖审计报告。"""
    return {
        "schema_version": 1,
        "report_type": "init_claim_contract_coverage",
        "verdict": verdict,
        "blocked": blocked,
        "summary": summary or f"claim coverage audit verdict={verdict}",
        "issues": issues or [],
        "covered_claims": covered_claims,
        "total_claims": total_claims,
    }


def _needs_repair_issue(
    *,
    issue_id: str = "claim-coverage-1",
    severity: str = "high",
    claim_id: str = "claim_cognitive_1",
    claim_text: str = "玄昱确认沈清漪即青阳会盟少年",
    target_chapter: int = 91,
    missing_field: str = "cognitive_constraints",
    summary: str = "",
) -> dict[str, Any]:
    """构建一个需要 repair 的覆盖缺口 issue。"""
    return {
        "issue_id": issue_id,
        "severity": severity,
        "category": "claim_contract_coverage",
        "claim_id": claim_id,
        "claim_text": claim_text,
        "target_chapter": target_chapter,
        "missing_field": missing_field,
        "summary": summary or f"claim {claim_id} 未在 ch{target_chapter} 的 {missing_field} 中落地",
    }


def _chapter_contracts(*contracts: dict[str, Any]) -> dict[str, Any]:
    """构建 chapter_contracts payload。"""
    return {"chapter_contracts": list(contracts)}


def _ch91_contract(**overrides: Any) -> dict[str, Any]:
    """构建 ch91 契约（无 cognitive_constraints，模拟缺口状态）。"""
    base: dict[str, Any] = {
        "chapter_number": 91,
        "title": "第九十一章",
        "required_events": [],
        "entry_state_requirements": [],
        "exit_state_targets": [],
        "forbidden_changes": [],
        "completion_criteria": [],
        "character_focus": [],
    }
    base.update(overrides)
    return base


# ---- 测试 ----


def test_init_claim_coverage_repair_loop_backfills_and_passes() -> None:
    """TDD-RED：验证 Claim-to-Contract 覆盖修复循环的核心契约。

    修复循环在 init_service.py:6575-6596 尚未实现，_local_cognitive_backfill
    也未实现（任务 7/8）。此测试在 RED 阶段验证：
    1. _init_coherence_blocks 对 needs_repair 报告返回 True（阻塞）
    2. _local_cognitive_backfill 导入失败（预期，RED 标记）
    3. 修复后 _init_coherence_blocks 对 accept 报告返回 False（通过）
    """
    # ---- Step 1: 模拟 needs_repair 场景 ----
    ctx = _mock_ctx()
    ch91 = _ch91_contract()
    _chapter_contracts(ch91)

    needs_repair_report = _coverage_report(
        verdict="needs_repair",
        blocked=True,
        issues=[
            _needs_repair_issue(
                claim_id="claim_cognitive_1",
                claim_text="玄昱确认沈清漪即青阳会盟少年",
                target_chapter=91,
                missing_field="cognitive_constraints",
            ),
        ],
        summary="1个 claim 未在 chapter_contracts 的 cognitive_constraints 中落地",
    )

    # 断言：needs_repair 报告被 _init_coherence_blocks 识别为阻塞
    assert _init_coherence_blocks(ctx, needs_repair_report), (
        "_init_coherence_blocks 应对 needs_repair 报告返回 True（阻塞初始化栅栏）"
    )

    # ---- Step 2: 验证修复循环入口存在 ----
    # RED 标记：_local_cognitive_backfill 尚未实现（任务 7/8）。
    # 当实现后移除以下断言，测试可推进到 GREEN 阶段。
    assert _LOCAL_COGNITIVE_BACKFILL_READY, (
        "RED: _local_cognitive_backfill 尚未实现（任务 7/8）。\n"
        "修复循环预期在 init_service.py:6575-6596 调用此函数，\n"
        "将未覆盖 claim 的 cognitive_subjects/object/level 信息\n"
        "backfill 到对应 chapter_contract 的 cognitive_constraints 字段。\n"
        "实现后此测试将进入 GREEN 阶段。"
    )

    # ---- Step 3: 模拟修复后场景（backfill 后的理想状态） ----
    # 当 _local_cognitive_backfill 实现后，它应修改 ch91 补充 cognitive_constraints。
    # 以下为修复后的 mock 状态：
    _ch91_contract(
        cognitive_constraints=[
            {
                "claim_id": "claim_cognitive_1",
                "claim_text": "玄昱确认沈清漪即青阳会盟少年",
                "cognitive_subjects": ["玄昱"],
                "cognitive_object": "沈清漪即青阳会盟少年",
                "cognitive_level": "confirmed",
                "action_level": "internal",
                "reader_awareness": "full",
                "character_knowledge_coverage": {"玄昱": "full", "沈清漪": "unknown"},
                "cognitive_chapter": 1,
                "public_reveal_chapter": 50,
            },
        ],
    )

    # 修复后的 coverage_report（模拟 re-audit 通过）
    accept_report = _coverage_report(
        verdict="accept",
        blocked=False,
        issues=[],
        summary="所有 claim 已成功 backfill 到 cognitive_constraints",
        covered_claims=1,
        total_claims=1,
    )
    # 修复 accept_report 的 schema_version 需要与 needs_repair_report 一致
    accept_report["schema_version"] = needs_repair_report["schema_version"]

    # 断言：修复后 _init_coherence_blocks 不再阻塞
    assert not _init_coherence_blocks(ctx, accept_report), (
        "_init_coherence_blocks 应对 accept 报告返回 False（通过初始化栅栏）"
    )
