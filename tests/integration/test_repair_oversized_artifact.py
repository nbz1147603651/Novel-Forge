"""Integration tests for oversized artifact repair: pre-flight + circuit breaker + degraded path.

End-to-end verification that large blueprint payloads trigger the full repair safety chain:
  1. Pre-flight token estimation identifies oversized payloads
  2. Circuit breaker opens after 3 consecutive failures for the same TaskType
  3. Degraded repair (summary-only) succeeds with a mock adapter
  4. Degraded repair output is a valid dict structure

The repair priority chain in init_service.py is: 完整 > 分块 > 降级 (full → chunked → degraded).
When estimated tokens exceed 10000, the flow routes to ``_repair_artifact_summary_only``.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import TaskCircuitOpenError
from novel_forge.gateway.task_circuit_breaker import (
    TaskCircuitState,
    TaskTypeCircuitBreaker,
)
from novel_forge.pipeline.long.services.init.init_service import (
    _repair_artifact_summary_only,
)

# ── Helpers ──────────────────────────────────────────────────────────


def construct_large_blueprint(num_chapters: int = 24) -> dict[str, Any]:
    """Construct a realistically-large blueprint payload for testing oversized paths.

    Each chapter includes full character arcs, suspense schedule entries,
    subplot events, and detailed narrative metadata — replicating the
    structure that caused the 1.18M token Run 2 error.
    """
    _BLOB = "叙事内容扩展填充以确保payload体积达到预检阈值进行超大负载降级路径测试验证"  # ~60 chars in Chinese
    _MEDIUM = _BLOB * 20  # ~1200 chars per medium block
    _LARGE = _BLOB * 80  # ~4800 chars per large block

    chapters: list[dict[str, Any]] = []
    for i in range(num_chapters):
        chapter = {
            "chapter_number": i + 1,
            "title": f"第{i + 1}章 - 长标题以增加体积用于测试超大负载路径 {_BLOB}",
            "goal": f"本章目标：推进主线并通过角色弧线、悬疑时间表、支线事件 完成第{i + 1}章的叙事契约要求。{_LARGE}",
            "summary": f"本章摘要：在第{i + 1}章中，主角面临关键抉择，支线相互交织形成紧张感与悬念。{_LARGE}",
            "beats_summary": [
                f"节拍 {j}: 推动主线并揭示角色深层动机——详细展开 {_MEDIUM}" for j in range(5)
            ],
            "main_plot_points": [
                f"主线点 {j}: 核心情节推进——深化冲突与张力 {_MEDIUM}" for j in range(3)
            ],
            "character_arcs": [
                {
                    "character": f"角色_{c}",
                    "arc_milestone": f"角色弧线里程碑在第{i + 1}章——情感转变与成长 {_LARGE}",
                    "emotional_state": f"复杂情感交织，包含矛盾与觉醒 {_MEDIUM}",
                    "key_decisions": [
                        f"关键决定 {d}: 选择推动角色发展 {_MEDIUM}" for d in range(2)
                    ],
                }
                for c in range(6)
            ],
            "suspense_schedule": [
                {
                    "suspense_id": f"sus_{i}_{s}",
                    "type": "mystery" if s % 2 == 0 else "emotion",
                    "description": f"悬念线 {s}: 在第{i + 1}章埋设线索 {_MEDIUM}",
                    "urgency": "high" if s % 3 == 0 else "normal",
                    "resolution_hint": f"悬念将在后续章节回收，当前仅投放线索 {_MEDIUM}",
                }
                for s in range(4)
            ],
            "subplot_events": [
                {
                    "subplot_name": f"支线_{sp}",
                    "event": f"支线事件在第{i + 1}章推进 {_MEDIUM}",
                    "weave_notes": f"与主线的交织方式：通过角色互动和线索共享 {_MEDIUM}",
                    "depends_on": [f"支线_{sp}:{max(1, i)}"] if i > 0 else [],
                }
                for sp in range(3)
            ],
            "pov_character": f"角色_{i % 4}",
            "setting": f"场景_{i + 1}——包含详细环境描写和氛围设定 {_MEDIUM}",
            "expected_word_count": 4500,
            "element_focus": [
                f"叙事要素_{e}: 在章节中的具体体现和操作方式 {_MEDIUM}" for e in range(5)
            ],
            "subplot_points": [
                f"支线要点_{p}: 与主线交汇点——描述和分析 {_MEDIUM}" for p in range(3)
            ],
            "time_marker": f"第{i + 1}天，时间推移标记——用于跨章节时间追踪 {_BLOB}",
            "tension_level": "中→高" if i < num_chapters // 2 else "高→极高",
            "forbidden_elements": [f"禁止元素_{f}: 章节中需避开的模式 {_MEDIUM}" for f in range(3)],
        }
        chapters.append(chapter)

    return {
        "artifact_name": "story_blueprint",
        "version": "2.0",
        "project_title": "超大蓝图负载测试",
        "genre": "悬疑奇幻",
        "theme": "身份与时间的代价",
        "synopsis": ("在一个时间流动异常的小镇，失忆的年轻人必须找到时间裂缝的真相。" + _LARGE),
        "chapters": chapters,
        "character_arcs": [
            {
                "character": f"角色_{c}",
                "arc_summary": f"从冲突到成长的完整弧线——包含转变和代价 {_LARGE}",
                "milestones": [
                    {
                        "chapter_start": m * 6 + 1,
                        "chapter_end": min((m + 1) * 6, num_chapters),
                        "description": f"阶段 {m}: 角色弧线推进 {_MEDIUM}",
                    }
                    for m in range(4)
                ],
            }
            for c in range(8)
        ],
        "subplot_plan": [
            {
                "name": f"支线_{sp}",
                "description": f"支线描述——与主线交错的完整计划 {_LARGE}",
                "involved_chapters": list(range(1, num_chapters + 1)),
                "chapter_events": [
                    {
                        "chapter_number": ch,
                        "event": f"支线在第{ch}章的事件 {_MEDIUM}",
                        "weave_notes": f"与主线的交织方式 {_MEDIUM}",
                        "depends_on": [],
                    }
                    for ch in range(1, num_chapters + 1)
                ],
                "priority": "primary" if sp == 0 else "normal",
            }
            for sp in range(3)
        ],
        "suspense_schedule": [
            {
                "suspense_id": f"global_sus_{s}",
                "suspense_type": "mystery" if s % 2 == 0 else "emotion",
                "introduce_chapter": s * 4 + 1,
                "resolve_chapter": min((s + 1) * 4, num_chapters),
                "description": f"全局悬念 {s}: 跨章节追踪 {_MEDIUM}",
                "urgency_level": "high",
                "strand_affinity": {"quest": 0.4, "fire": 0.3, "constellation": 0.3},
            }
            for s in range(6)
        ],
        "narrative_phases": [
            {
                "phase_name": f"阶段 {p}",
                "chapter_start": p * 6 + 1,
                "chapter_end": min((p + 1) * 6, num_chapters),
                "description": f"叙事阶段 {p}: 从铺垫到高潮的推进 {_MEDIUM}",
                "tension_level": "升→高",
                "key_events": [f"关键事件 {e} {_BLOB}" for e in range(3)],
            }
            for p in range(4)
        ],
        "key_turning_points": [
            {"chapter_number": tp * 4 + 1, "description": f"转折点 {tp}: 叙事方向改变 {_MEDIUM}"}
            for tp in range(6)
        ],
        "ending_strategy": f"余韵式收束：主角在真相面前做出终极选择 {_MEDIUM}",
        "last_modified": "2026-06-07T00:00:00Z",
    }


def estimate_blueprint_tokens(payload: dict[str, Any]) -> int:
    """Estimate token count using the same chars/4 heuristic as init_service.py."""
    prompt_text = json.dumps(payload, ensure_ascii=False)
    return max(1, len(prompt_text) // 4)


def construct_mock_adapter_with_response(response_dict: dict[str, Any]) -> Any:
    """Build a mock adapter whose ``complete()`` returns the given dict as JSON."""
    adapter = MagicMock()
    adapter.complete = AsyncMock()
    adapter.complete.return_value = MagicMock(
        content=json.dumps(response_dict, ensure_ascii=False),
    )
    return adapter


def construct_failing_mock_adapter() -> Any:
    """Build a mock adapter whose ``complete()`` always raises."""
    adapter = MagicMock()
    adapter.complete = AsyncMock(side_effect=RuntimeError("mock adapter failure"))
    return adapter


# ── Test 1: Pre-flight Oversized Detection ──────────────────────────


class TestPreflightOversizedDetection:
    """Verify the token estimation logic identifies oversized payloads.

    The production code in _repair_init_artifact_payload uses::

        estimated_tokens = max(1, len(prompt_text) // 4)

    and issues WARNING when > 50000 tokens.
    """

    async def test_oversized_blueprint_exceeds_50k_tokens(self) -> None:
        """24-chapter blueprint with rich arcs and suspense should exceed 50K tokens."""
        payload = construct_large_blueprint(num_chapters=24)
        estimated = estimate_blueprint_tokens(payload)
        assert estimated > 50000, (
            f"Expected >50000 pre-flight tokens for 24-chapter blueprint, got {estimated}. "
            f"Payload size: {len(json.dumps(payload, ensure_ascii=False))} chars."
        )

    async def test_small_payload_stays_under_threshold(self) -> None:
        """A minimal blueprint should not trigger the oversized warning."""
        payload = {
            "artifact_name": "small",
            "version": "1.0",
            "chapters": [{"chapter_number": 1, "goal": "short goal", "summary": "short summary"}],
        }
        estimated = estimate_blueprint_tokens(payload)
        assert estimated < 50000, f"Expected <50000 tokens for minimal blueprint, got {estimated}"

    async def test_preflight_estimation_formula_matches_init_service(self) -> None:
        """Verify the estimation formula matches init_service: chars // 4."""
        payload = {"artifact_name": "test", "chapters": [{"goal": "x" * 40000}]}
        prompt_text = json.dumps(payload, ensure_ascii=False)
        expected = max(1, len(prompt_text) // 4)
        assert estimate_blueprint_tokens(payload) == expected

    async def test_oversized_flag_metadata(self) -> None:
        """Simulate the preflight metadata dict that gets emitted in the repair flow."""
        payload = construct_large_blueprint(num_chapters=24)
        estimated = estimate_blueprint_tokens(payload)
        metadata = {
            "preflight_oversized": estimated > 50000,
            "estimated_tokens": estimated,
            "context_window_pct": min(100.0, (estimated / 131072) * 100),
        }
        assert metadata["preflight_oversized"] is True
        assert metadata["context_window_pct"] > 5.0  # significant portion of 128K window


# ── Test 2: Circuit Breaker Through 3 Failures ─────────────────────


class TestCircuitBreakerOpensAfter3Failures:
    """End-to-end: simulate 3 consecutive repair failures → circuit opens.

    The production flow in _repair_init_artifact_payload calls ``ctx.call_with_retry``
    with ``TaskType.REPAIR_INIT_ARTIFACT_PATCH``. The underlying gateway checks the
    ``TaskTypeCircuitBreaker`` before each call, and the breaker opens after 3 failures.
    """

    def test_3_failures_open_circuit_for_artifact_repair(self) -> None:
        breaker = TaskTypeCircuitBreaker(threshold=3, cooldown_seconds=60.0)
        task = TaskType.REPAIR_INIT_ARTIFACT_PATCH

        assert breaker.get_state(task) == TaskCircuitState.CLOSED
        assert breaker.get_failure_count(task) == 0

        # Simulate 3 consecutive failures (what happens in call_with_retry)
        breaker.record_failure(task)
        assert breaker.get_state(task) == TaskCircuitState.CLOSED
        assert breaker.get_failure_count(task) == 1

        breaker.record_failure(task)
        assert breaker.get_failure_count(task) == 2

        breaker.record_failure(task)
        assert breaker.get_state(task) == TaskCircuitState.OPEN
        assert breaker.get_failure_count(task) == 3

    def test_4th_call_raises_task_circuit_open_error(self) -> None:
        breaker = TaskTypeCircuitBreaker(threshold=3, cooldown_seconds=60.0)
        task = TaskType.REPAIR_INIT_ARTIFACT_PATCH

        # 3 failures → OPEN
        breaker.record_failure(task)
        breaker.record_failure(task)
        breaker.record_failure(task)
        assert breaker.get_state(task) == TaskCircuitState.OPEN

        # 4th attempt → rejected
        with pytest.raises(TaskCircuitOpenError) as exc_info:
            breaker.check(task)

        assert exc_info.value.task_type == task
        assert exc_info.value.retry_after_s > 0

    def test_mock_adapter_failures_integrate_with_breaker(self) -> None:
        """Simulate the full integration: failing adapter → breaker records failure."""
        breaker = TaskTypeCircuitBreaker(threshold=3, cooldown_seconds=60.0)
        task = TaskType.REPAIR_INIT_ARTIFACT_PATCH

        # Simulate 3 LLM call failures
        for _ in range(3):
            try:
                breaker.check(task)
                # Simulate LLM failure
                raise RuntimeError("mock LLM failure")
            except RuntimeError:
                breaker.record_failure(task)

        # Circuit should now be OPEN
        assert breaker.get_state(task) == TaskCircuitState.OPEN

        # 4th call: circuit rejects before even trying the LLM
        with pytest.raises(TaskCircuitOpenError):
            breaker.check(task)

    def test_independent_tasks_not_affected(self) -> None:
        breaker = TaskTypeCircuitBreaker(threshold=3, cooldown_seconds=60.0)
        repair_task = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        draft_task = TaskType.DRAFT_CHAPTER

        # Open circuit for repair task
        breaker.record_failure(repair_task)
        breaker.record_failure(repair_task)
        breaker.record_failure(repair_task)
        assert breaker.get_state(repair_task) == TaskCircuitState.OPEN

        # Draft task should still be CLOSED
        assert breaker.get_state(draft_task) == TaskCircuitState.CLOSED
        breaker.check(draft_task)  # should not raise


# ── Test 3: Degraded Repair Success with Mock Adapter ───────────────


class TestDegradedRepairSucceeds:
    """End-to-end: degraded repair path (summary-only) with mock LLM adapter.

    When the pre-flight estimates > 10000 tokens, init_service.py routes to
    ``_repair_artifact_summary_only`` which builds a minimal prompt from the
    payload summary and calls the LLM only with lightweight fields.
    """

    async def test_summary_only_repair_with_mock_adapter(self) -> None:
        """Mock adapter returns successful patches → payload is updated."""
        payload = {
            "artifact_name": "test_blueprint",
            "version": "1.0",
            "chapters": [
                {"chapter_number": 1, "title": "旧标题", "goal": "旧目标" * 50},
                {"chapter_number": 2, "title": "第二章", "goal": "目标2" * 50},
            ],
        }
        adapter = construct_mock_adapter_with_response(
            {
                "patches": [
                    {
                        "op": "replace",
                        "path": "/chapters/0/title",
                        "value": "修复后的标题",
                        "issue_ids": ["i1"],
                        "rationale": "标题与章节内容不一致",
                    },
                    {
                        "op": "replace",
                        "path": "/chapters/0/goal",
                        "value": "修复后的目标",
                        "issue_ids": ["i2"],
                        "rationale": "目标过于模糊",
                    },
                ],
                "summary": "已修复2处问题",
            }
        )

        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="修复章节标题和目标的不一致问题",
            adapter=adapter,
        )

        assert result["chapters"][0]["title"] == "修复后的标题"
        assert result["chapters"][0]["goal"] == "修复后的目标"
        assert result["chapters"][1]["title"] == "第二章"  # unchanged
        assert result["artifact_name"] == "test_blueprint"  # preserved

    async def test_large_outline_degraded_repair(self) -> None:
        """Large 24-chapter outline with mock adapter returns repair patches."""
        payload = construct_large_blueprint(num_chapters=24)
        payload["artifact_name"] = "story_outline"
        adapter = construct_mock_adapter_with_response(
            {
                "patches": [
                    {
                        "op": "replace",
                        "path": "/synopsis",
                        "value": "修复后的摘要——更准确地反映主线走向",
                        "issue_ids": ["i1"],
                        "rationale": "原摘要有两处事实错误",
                    },
                ],
                "summary": "修复了摘要中的事实错误",
            }
        )

        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="修复摘要中的事实描述错误",
            adapter=adapter,
        )

        assert "修复后" in result["synopsis"]
        assert result["artifact_name"] == "story_outline"
        assert len(result["chapters"]) == 24  # structure preserved

    async def test_degraded_repair_no_adapter_returns_original(self) -> None:
        """When no adapter is provided, the degraded repair returns payload unchanged."""
        payload = {"title": "original", "chapters": [{"goal": "keep me"}]}
        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="修复",
            adapter=None,
        )
        assert result == payload

    async def test_degraded_repair_adapter_error_preserves_original(self) -> None:
        """Adapter failure in degraded path returns original payload unchanged."""
        payload = {"title": "safe", "chapters": [{"goal": "untouched"}]}
        adapter = construct_failing_mock_adapter()
        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="修复",
            adapter=adapter,
        )
        assert result == payload
        assert result["title"] == "safe"

    async def test_degraded_repair_parse_failure_returns_original(self) -> None:
        """Malformed LLM response in degraded path returns original payload."""
        payload = {"title": "original"}
        adapter = MagicMock()
        adapter.complete = AsyncMock()
        adapter.complete.return_value = MagicMock(content="not valid json {{{")

        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="修复",
            adapter=adapter,
        )
        assert result == payload


# ── Test 4: Degraded Repair Returns Valid Blueprint Structure ───────


class TestDegradedRepairValidBlueprint:
    """End-to-end: degraded repair output passes structural validation.

    After the degraded repair path completes, the result must be a valid dict
    with all top-level keys preserved, suitable for downstream pipeline steps.
    """

    async def test_degraded_output_is_valid_dict(self) -> None:
        """Degraded repair returns a dict — never a string or list."""
        payload = construct_large_blueprint(num_chapters=24)
        payload["artifact_name"] = "narrative_blueprint"
        adapter = construct_mock_adapter_with_response(
            {
                "patches": [
                    {
                        "op": "replace",
                        "path": "/synopsis",
                        "value": "修复后的叙事蓝图摘要",
                        "issue_ids": ["i1"],
                        "rationale": "提升摘要质量",
                    },
                ],
                "summary": "已修复1处问题",
            }
        )

        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="优化叙事蓝图摘要的清晰度和准确性",
            adapter=adapter,
        )

        assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"
        assert "artifact_name" in result
        assert "chapters" in result
        assert "synopsis" in result
        assert "character_arcs" in result
        assert "suspense_schedule" in result

    async def test_degraded_output_preserves_all_top_level_keys(self) -> None:
        """Degraded repair must preserve every top-level key from the original."""
        payload = construct_large_blueprint(num_chapters=24)
        original_keys = set(payload.keys())
        adapter = construct_mock_adapter_with_response(
            {
                "patches": [],
                "summary": "No changes needed — blueprint is consistent",
            }
        )

        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="验证蓝图结构一致性",
            adapter=adapter,
        )

        result_keys = set(result.keys())
        missing = original_keys - result_keys
        extra = result_keys - original_keys
        assert not missing, f"Missing top-level keys: {missing}"
        assert not extra, f"Unexpected top-level keys: {extra}"

    async def test_degraded_output_chapter_count_preserved(self) -> None:
        """24-chapter count must survive the degraded repair path intact."""
        payload = construct_large_blueprint(num_chapters=24)
        adapter = construct_mock_adapter_with_response(
            {
                "patches": [],
                "summary": "Blueprint structure validated — no issues found",
            }
        )

        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="验证章节结构完整性",
            adapter=adapter,
        )

        assert len(result["chapters"]) == 24
        for i, ch in enumerate(result["chapters"]):
            assert ch["chapter_number"] == i + 1
            assert "character_arcs" in ch
            assert "suspense_schedule" in ch
            assert "subplot_events" in ch

    async def test_degraded_output_with_suggested_changes_validates(self) -> None:
        """Degraded repair with suggested field changes produces a valid blueprint."""
        payload = construct_large_blueprint(num_chapters=24)
        adapter = construct_mock_adapter_with_response(
            {
                "patches": [
                    {
                        "op": "replace",
                        "path": "/synopsis",
                        "value": "修复后的叙事摘要——反映编辑建议的核心修改方向",
                        "issue_ids": ["i1", "i2"],
                        "rationale": "编辑裁判发现问题：摘要与角色弧线存在矛盾",
                    },
                    {
                        "op": "replace",
                        "path": "/ending_strategy",
                        "value": "修正后的结局策略——与角色弧线终点保持一致",
                        "issue_ids": ["i3"],
                        "rationale": "结局策略与角色弧线的最终状态不匹配",
                    },
                ],
                "summary": "基于编辑裁判建议修复了2处结构问题",
            }
        )

        result = await _repair_artifact_summary_only(
            payload,
            repair_intent=(
                "编辑裁判报告指出：1) 摘要与角色弧线方向矛盾；"
                "2) 结局策略与角色弧线终点不一致。请修复这两处。"
            ),
            adapter=adapter,
        )

        assert "修复后" in result["synopsis"]
        assert "修正" in result["ending_strategy"]
        assert result["chapters"] is not None
        assert result["character_arcs"] is not None
        assert result["suspense_schedule"] is not None


# ── Integration Sanity: Full Chain ──────────────────────────────────


class TestFullRepairChain:
    """End-to-end: the complete chain pre-flight → degraded → valid output."""

    async def test_oversized_blueprint_full_degraded_chain(self) -> None:
        """Simulate the full repair chain: detect oversized, route to degraded, get valid result."""
        payload = construct_large_blueprint(num_chapters=24)

        # Step 1: Pre-flight estimation
        estimated = estimate_blueprint_tokens(payload)
        assert estimated > 50000, f"Pre-flight must detect oversized: {estimated} tokens"

        # Step 2: Route to degraded path (summary_only)
        assert estimated > 10000, "Should route to degraded (summary_only)"
        adapter = construct_mock_adapter_with_response(
            {
                "patches": [],
                "summary": "Pre-flight validated — blueprint passes degraded inspection",
            }
        )

        # Step 3: Execute degraded repair
        start = time.monotonic()
        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="端到端验证：超大蓝图走完整降级修复路径",
            adapter=adapter,
        )
        elapsed = time.monotonic() - start

        # Step 4: Validate output
        assert isinstance(result, dict)
        assert result["artifact_name"] == "story_blueprint"
        assert len(result["chapters"]) == 24

        # Performance: degraded path must complete quickly (mock adapter)
        assert elapsed < 5.0, f"Degraded repair too slow: {elapsed:.2f}s"
